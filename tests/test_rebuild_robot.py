import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import rebuild_robot

ROOT = Path(__file__).resolve().parents[1]


def test_build_failure_does_not_flash_stale_binary(tmp_path, monkeypatch):
    binary = tmp_path / f"firmware/.pio/build/{rebuild_robot.TARGET}/firmware.bin"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"old firmware")
    commands = []

    def fail(command, root, env):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(rebuild_robot, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        rebuild_robot.build_and_upload(tmp_path, Path("python"), {}, "COM7")
    assert len(commands) == 1
    assert "upload" not in commands[0]
    assert binary.read_bytes() == b"old firmware"


def test_missing_image_after_success_does_not_flash(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(rebuild_robot, "run", lambda cmd, *_: commands.append(cmd))
    with pytest.raises(RuntimeError, match="did not produce"):
        rebuild_robot.build_and_upload(tmp_path, Path("python"), {}, "COM7")
    assert len(commands) == 1
    assert "upload" not in commands[0]


def test_existing_credentials_not_overwritten_or_printed(tmp_path, monkeypatch, capsys):
    config = tmp_path / "firmware/src/config.h"
    config.parent.mkdir(parents=True)
    private = "private device credentials"
    config.write_text(private)
    commands = []
    monkeypatch.setattr(rebuild_robot, "run", lambda cmd, *_: commands.append(cmd))
    with pytest.raises(ValueError, match="already exists"):
        rebuild_robot.prepare_config(tmp_path, "robot.example.com", {})
    rebuild_robot.prepare_config(tmp_path, None, {})
    assert config.read_text() == private
    assert not commands
    assert private not in capsys.readouterr().out


@pytest.mark.parametrize(("config", "public", "accepted"), [
    (None, False, False), ("", False, False),
    ("enabled", False, True), ("enabled", True, False),
    ("no_host", False, False), ("no_token", False, False),
    ("short_token", False, False), ("no_ca", False, False),
    ("disabled", False, False), ("wrong_port", False, False),
])
def test_railway_target_refuses_unprovisioned_images(tmp_path, config, public, accepted):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("Firmware configuration validation requires g++")
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    for name in ("config_loader.h", "config_defaults.h"):
        shutil.copyfile(ROOT / "firmware/src" / name, source_dir / name)
    shutil.copyfile(ROOT / "firmware/config.h.example", tmp_path / "config.h.example")
    if config is not None:
        values = {
            "STACKCHAN_OUTBOUND_ENABLED": "1",
            "STACKCHAN_OUTBOUND_HOST": '"robot.example.com"',
            "STACKCHAN_OUTBOUND_DEVICE_TOKEN": '"test-device-token-0123456789abcdef"',
            # Runtime parses certificates; this compile check only requires a value.
            "STACKCHAN_OUTBOUND_CA_PEM": '"test certificate placeholder"',
            "STACKCHAN_OUTBOUND_PORT": "443",
        }
        if config == "":
            values.clear()
        for case, key in (("no_host", "STACKCHAN_OUTBOUND_HOST"),
                          ("no_token", "STACKCHAN_OUTBOUND_DEVICE_TOKEN"),
                          ("no_ca", "STACKCHAN_OUTBOUND_CA_PEM")):
            if config == case:
                values[key] = '""'
        if config == "disabled":
            values["STACKCHAN_OUTBOUND_ENABLED"] = "0"
        if config == "wrong_port":
            values["STACKCHAN_OUTBOUND_PORT"] = "80"
        if config == "short_token":
            values["STACKCHAN_OUTBOUND_DEVICE_TOKEN"] = '"too-short"'
        (source_dir / "config.h").write_text("\n".join(
            f"#define {key} {value}" for key, value in values.items()))
    source = source_dir / "check.cpp"
    source.write_text('#include "config_loader.h"\nint main() {}\n')
    command = [compiler, "-std=c++17", "-DSTACKCHAN_REQUIRE_OUTBOUND_CONFIG",
               "-fsyntax-only", str(source)]
    if public:
        command.append("-DSTACKCHAN_USE_PUBLIC_CONFIG")
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603 - fixed local test compiler; no shell.
    assert (result.returncode == 0) == accepted, result.stderr
