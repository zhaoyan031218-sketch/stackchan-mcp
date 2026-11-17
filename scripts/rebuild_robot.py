"""Build a provisioned CoreS3 Railway image in a fresh Windows project folder."""

import argparse
import os
import re
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = "m5stack-cores3-railway"


def run(command: list[str], root: Path, env: dict[str, str]) -> None:
    # argv contains only tool paths, options and a hostname/port, never credentials.
    subprocess.run(command, cwd=root, env=env, check=True)  # noqa: S603 - no shell; local tools with explicit argv.


def prepare_config(root: Path, host: str | None, env: dict[str, str]) -> None:
    config = root / "firmware/src/config.h"
    if config.is_symlink():
        raise ValueError("Private config.h must be a regular local file")
    if config.exists():
        if host:
            raise ValueError(
                "Private config.h already exists. Omit --host to reuse it, or use a new "
                "project folder to configure another host. Existing settings were preserved."
            )
        print("Using existing private firmware/src/config.h (contents are not displayed).")
        return
    if not host:
        raise ValueError("No private config.h. Run again with --host YOUR_DOMAIN.up.railway.app")
    run([sys.executable, str(root / "scripts/configure_robot.py"), "--host", host], root, env)
    if not config.is_file():
        raise RuntimeError("Configuration did not create private config.h; build stopped")


def prepare_tools(root: Path, env: dict[str, str]) -> Path:
    directory = root / "firmware/.venv"
    python = directory / "Scripts/python.exe"
    if not python.is_file():
        print("Creating the isolated firmware tool environment...")
        venv.EnvBuilder(with_pip=True).create(directory)
    run([str(python), "-m", "pip", "install", "platformio==6.1.18"], root, env)
    return python


def build_and_upload(root: Path, python: Path, env: dict[str, str],
                     upload_port: str | None = None) -> Path:
    command = [str(python), "-m", "platformio", "run", "-d", "firmware", "-e", TARGET]
    # A failed build raises before upload, even if an old binary remains on disk.
    run(command, root, env)
    binary = root / f"firmware/.pio/build/{TARGET}/firmware.bin"
    if not binary.is_file():
        raise RuntimeError("Build did not produce firmware.bin; upload stopped")
    print(f"Private firmware ready: {binary}")
    print("This image contains device credentials. Keep it private.")
    if upload_port:
        run([*command, "-t", "upload", "--upload-port", upload_port], root, env)
        print("Upload finished. Use --monitor --port with the current COM port to check startup.")
    else:
        print("Built only. To flash, run this helper with --upload --port COM_NUMBER.")
    return binary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="Only for first-time configuration; hostname without https://")
    parser.add_argument("--port", help="Current Windows serial port, for example COM7")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--upload", action="store_true", help="Build successfully, then flash the selected port")
    mode.add_argument("--list-ports", action="store_true", help="List serial ports without flashing")
    mode.add_argument("--monitor", action="store_true", help="Open the serial monitor without building or flashing")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info < (3, 11):
        parser.error("Use Python 3.11 or newer on the Windows computer connected to the CoreS3")
    if ((args.upload or args.monitor)
            and (not args.port or not re.fullmatch(r"COM[1-9][0-9]*", args.port.upper()))):
        parser.error("--upload and --monitor require the current --port COM_NUMBER")
    if args.port and not (args.upload or args.monitor):
        parser.error("--port is used only with --upload or --monitor")
    if args.host and (args.list_ports or args.monitor):
        parser.error("--host is used only when creating a new configuration for a build")

    # Keep the tool cache and temporary files inside this fresh project, not in
    # an old project or the user's global PlatformIO directory.
    temporary = ROOT / "work/tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "PLATFORMIO_CORE_DIR": str(ROOT / "work/platformio"),
        "PLATFORMIO_SETTING_ENABLE_TELEMETRY": "no",
        "TEMP": str(temporary), "TMP": str(temporary),
    })
    try:
        if not args.list_ports and not args.monitor:
            prepare_config(ROOT, args.host, env)
        python = prepare_tools(ROOT, env)
        if args.list_ports:
            run([str(python), "-m", "platformio", "device", "list"], ROOT, env)
        elif args.monitor:
            run([str(python), "-m", "platformio", "device", "monitor",
                 "--port", args.port.upper(), "--baud", "115200"], ROOT, env)
        else:
            build_and_upload(ROOT, python, env, args.port.upper() if args.upload else None)
    except subprocess.CalledProcessError:
        print("The previous step failed; remaining steps were not run. Keep the error above.", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
