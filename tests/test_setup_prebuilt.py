import hashlib
import json
import struct
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import package_firmware, setup_prebuilt


def make_package(tmp_path: Path) -> Path:
    build = tmp_path / "m5stack-cores3-runtime"
    build.mkdir()
    image = bytearray(512)
    image[0] = 0xE9
    struct.pack_into("<H", image, 12, 9)
    (build / "firmware.bin").write_bytes(image + setup_prebuilt.MARKER)
    (build / "bootloader.bin").write_bytes(image)
    table = bytearray()
    for kind, subtype, offset, size, name in [
        (1, 2, 0x9000, 0x5000, b"nvs"),
        (1, 0, 0xE000, 0x2000, b"otadata"),
        (0, 0x10, 0x10000, 0x640000, b"app0"),
    ]:
        table.extend(struct.pack("<HBBII16sI", 0x50AA, kind, subtype, offset, size, name, 0))
    (build / "partitions.bin").write_bytes(table)
    boot_app0 = tmp_path / "boot_app0.bin"
    boot_app0.write_bytes(b"\xff" * 0x2000)
    target = tmp_path / "package.zip"
    package_firmware.package_firmware(build, boot_app0, target, "runtime-configurable")
    root = tmp_path / "unpacked"
    with zipfile.ZipFile(target) as archive:
        archive.extractall(root)
    return root


def test_runtime_package_has_verified_helper_hashes_and_marker(tmp_path: Path) -> None:
    root = make_package(tmp_path)
    manifest = setup_prebuilt.verify_package(root)
    assert manifest["profile"] == "runtime-configurable"
    assert manifest["setup_protocol"] == 1
    assert "不用下载 PlatformIO" in (root / "README-zh.txt").read_text()
    assert "esptool==4.11.0" in (root / "setup-requirements.txt").read_text()


@pytest.mark.parametrize("filename", ["firmware.bin", "scripts/setup_prebuilt.py"])
def test_corruption_blocks_flash_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    root = make_package(tmp_path)
    with (root / filename).open("ab") as file:
        file.write(b"corrupted")
    calls = []
    monkeypatch.setattr(setup_prebuilt.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError):
        setup_prebuilt.flash(root, "COM5")
    assert calls == []


def test_fixed_offsets_no_erase_and_no_credentials_in_flash_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_package(tmp_path)
    calls = []
    monkeypatch.setattr(setup_prebuilt.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    setup_prebuilt.flash(root, "COM5")
    command = calls[0][0][0]
    assert command[-8:] == ["0x0000", str(root / "bootloader.bin"), "0x8000", str(root / "partitions.bin"),
                            "0xe000", str(root / "boot_app0.bin"), "0x10000", str(root / "firmware.bin")]
    assert "erase_flash" not in command
    assert "--erase-all" not in command
    assert calls[0][1]["timeout"] == 180


def test_unsafe_manifest_offsets_are_rejected_even_with_new_checksum(tmp_path: Path) -> None:
    root = make_package(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["components"][0]["offset"] = "0x9000"
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Unsafe flash offset"):
        setup_prebuilt.verify_package(root)


def test_runtime_marker_is_required_even_if_firmware_hash_matches(tmp_path: Path) -> None:
    root = make_package(tmp_path)
    image = (root / "firmware.bin").read_bytes().replace(setup_prebuilt.MARKER, b"x" * len(setup_prebuilt.MARKER))
    (root / "firmware.bin").write_bytes(image)
    manifest = json.loads((root / "manifest.json").read_text())
    for component in manifest["components"]:
        if component["name"] == "firmware.bin":
            component["sha256"] = hashlib.sha256(image).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="setup marker"):
        setup_prebuilt.verify_package(root)


class FakeSerial:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = list(lines)
        self.writes: list[bytes] = []
        self.closed = False
        self.out_waiting = 0

    def read_until(self, *_args) -> bytes:
        return self.lines.pop(0) if self.lines else b""

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


def fake_connection(monkeypatch: pytest.MonkeyPatch, lines: list[bytes]) -> FakeSerial:
    connection = FakeSerial(lines)
    monkeypatch.setattr(setup_prebuilt, "open_serial", lambda *_args: connection)
    return connection


SERIAL_MODULE = SimpleNamespace(SerialException=OSError)
READY = b'{"type":"stackchan.setup.ready","v":1}\n'
SUCCESS = b'{"type":"stackchan.setup.result","ok":true}\n'


def test_fragmented_ready_and_ack_are_preserved_and_payload_sent_once(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = fake_connection(monkeypatch, [b"Boot log\n", READY[:15], b"", READY[15:],
                                              SUCCESS[:20], b"", SUCCESS[20:]])
    payload = b'{"token":"secret"}\n'
    setup_prebuilt.provision("COM5", payload, SERIAL_MODULE)
    assert connection.writes == [payload]
    assert connection.closed


def test_device_rejection_never_echoes_error_or_replays_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = fake_connection(monkeypatch, [READY, b'{"type":"stackchan.setup.result","ok":false,"error":"SECRET_TOKEN"}\n'])
    with pytest.raises(setup_prebuilt.SetupError, match="Device rejected") as error:
        setup_prebuilt.provision("COM5", b"private config\n", SERIAL_MODULE)
    assert "SECRET_TOKEN" not in str(error.value)
    assert len(connection.writes) == 1
    assert connection.closed


def test_ack_timeout_is_bounded_and_does_not_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = fake_connection(monkeypatch, [READY])
    tick = iter(range(0, 1000))
    monkeypatch.setattr(setup_prebuilt.time, "monotonic", lambda: next(tick))
    with pytest.raises(setup_prebuilt.SetupError, match="No save confirmation"):
        setup_prebuilt.provision("COM5", b"private config\n", SERIAL_MODULE)
    assert len(connection.writes) == 1


def test_output_drain_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = fake_connection(monkeypatch, [READY])
    connection.out_waiting = 1
    tick = iter(range(0, 1000))
    monkeypatch.setattr(setup_prebuilt.time, "monotonic", lambda: next(tick))
    monkeypatch.setattr(setup_prebuilt.time, "sleep", lambda _: None)
    with pytest.raises(setup_prebuilt.SetupError, match="did not drain"):
        setup_prebuilt.provision("COM5", b"private config\n", SERIAL_MODULE)
    assert len(connection.writes) == 1


def test_oversized_serial_line_is_discarded_before_next_valid_message() -> None:
    reader = setup_prebuilt.ProtocolReader()
    connection = FakeSerial([b"x" * 2049, b"rest\n", READY])
    assert reader.read(connection) is None
    assert reader.read(connection) is None
    assert reader.read(connection) == {"type": "stackchan.setup.ready", "v": 1}


def test_large_configuration_is_paced_in_bounded_chunks_without_retransmission(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = fake_connection(monkeypatch, [READY, SUCCESS])
    sleeps = []
    monkeypatch.setattr(setup_prebuilt.time, "sleep", sleeps.append)
    payload = b"x" * 3000 + b"\n"
    setup_prebuilt.provision("COM5", payload, SERIAL_MODULE)
    assert b"".join(connection.writes) == payload
    assert all(len(chunk) <= 128 for chunk in connection.writes)
    assert sleeps == [0.005] * len(connection.writes)
    assert connection.write_timeout <= 0.5


def test_configuration_line_round_trips_unicode_and_bounds_root_size() -> None:
    line = setup_prebuilt.encode_configuration("gojobody.up.railway.app", "t" * 24, "家里 WiFi", "password123", "root")
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    decoded = json.loads(line)
    assert decoded["ssid"] == "家里 WiFi"
    assert decoded["type"] == "stackchan.configure"
    with pytest.raises(ValueError, match="root certificate"):
        setup_prebuilt.encode_configuration("gojobody.up.railway.app", "t" * 24, "ssid", "password123", "x" * 4097)


def test_configure_only_reset_command_has_no_flash_or_erase(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(setup_prebuilt.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    setup_prebuilt.reset_for_configuration("COM5")
    command = calls[0][0][0]
    assert command[-3:] == ["--after", "hard_reset", "run"]
    assert "write_flash" not in command and "erase_flash" not in command
    assert calls[0][1]["timeout"] == 30
