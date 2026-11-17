import hashlib
import json
import struct
import zipfile
from pathlib import Path

import pytest

from scripts.package_firmware import package_firmware


def make_build(tmp_path: Path) -> tuple[Path, Path, Path]:
    build = tmp_path / "m5stack-cores3-public"
    build.mkdir()
    image = bytearray(512)
    image[0] = 0xE9
    struct.pack_into("<H", image, 12, 9)
    (build / "firmware.bin").write_bytes(image)
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
    return build, boot_app0, tmp_path / "out" / "firmware.zip"


def test_package_verifies_every_component_and_labels_public_build(tmp_path: Path) -> None:
    build, boot_app0, output = make_build(tmp_path)
    result = package_firmware(build, boot_app0, output, "verification-only")
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result["bytes"] == output.stat().st_size
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["profile"] == "verification-only"
        assert manifest["hardware_validated"] is False
        assert manifest["cloud_validated"] is False
        assert len(archive.namelist()) == 7
        for component in manifest["components"]:
            actual = archive.read(component["name"])
            assert len(actual) == component["bytes"]
            assert hashlib.sha256(actual).hexdigest() == component["sha256"]
        for line in archive.read("SHA256SUMS.txt").decode().splitlines():
            digest, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        assert "不能用它恢复 Railway" in archive.read("README-zh.txt").decode()
    assert not list(output.parent.glob("*.tmp"))


@pytest.mark.parametrize("bad_part", ["firmware.bin", "bootloader.bin", "partitions.bin", "boot_app0.bin"])
def test_missing_image_never_replaces_previous_complete_package(tmp_path: Path, bad_part: str) -> None:
    build, boot_app0, output = make_build(tmp_path)
    output.parent.mkdir()
    output.write_bytes(b"previous validated package")
    (boot_app0 if bad_part == "boot_app0.bin" else build / bad_part).unlink()
    with pytest.raises(OSError):
        package_firmware(build, boot_app0, output, "verification-only")
    assert output.read_bytes() == b"previous validated package"


def test_wrong_chip_is_rejected_before_publishing(tmp_path: Path) -> None:
    build, boot_app0, output = make_build(tmp_path)
    image = bytearray((build / "firmware.bin").read_bytes())
    struct.pack_into("<H", image, 12, 0)
    (build / "firmware.bin").write_bytes(image)
    with pytest.raises(ValueError, match="Not an ESP32-S3"):
        package_firmware(build, boot_app0, output, "verification-only")
    assert not output.exists()


def test_partition_overlap_with_nvs_is_rejected(tmp_path: Path) -> None:
    build, boot_app0, output = make_build(tmp_path)
    (build / "bootloader.bin").write_bytes((build / "bootloader.bin").read_bytes() + bytes(0x9000))
    with pytest.raises(ValueError, match="overlaps"):
        package_firmware(build, boot_app0, output, "verification-only")
    assert not output.exists()


def test_public_build_cannot_be_labelled_provisioned(tmp_path: Path) -> None:
    build, boot_app0, output = make_build(tmp_path)
    with pytest.raises(ValueError, match="cannot be labelled provisioned"):
        package_firmware(build, boot_app0, output, "provisioned-unverified")


def test_failed_zip_verification_keeps_old_release_and_removes_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build, boot_app0, output = make_build(tmp_path)
    output.parent.mkdir()
    output.write_bytes(b"previous validated package")
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda _self: "firmware.bin")
    with pytest.raises(ValueError, match="ZIP integrity"):
        package_firmware(build, boot_app0, output, "verification-only")
    assert output.read_bytes() == b"previous validated package"
    assert list(output.parent.iterdir()) == [output]
