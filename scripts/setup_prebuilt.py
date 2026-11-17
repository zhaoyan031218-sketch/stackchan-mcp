"""Flash and configure a precompiled CoreS3 over USB; no compiler is required."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import importlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import configure_robot  # noqa: E402 - supports direct script execution.

OFFSETS = {"bootloader.bin": 0x0000, "partitions.bin": 0x8000,
           "boot_app0.bin": 0xE000, "firmware.bin": 0x10000}
MARKER = b"STACKCHAN_RUNTIME_SETUP_V1"
MAX_LINE_BYTES = 12288
MAX_RESULT_BYTES = 2048


class SetupError(RuntimeError):
    """A fixed, safe message that can be shown without exposing serial data."""


def verify_package(root: Path) -> dict[str, Any]:
    """Do not pass paths or offsets from an unvalidated manifest to esptool."""
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 65536:
        raise ValueError("Invalid package manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in {
        "format": 1, "board": "m5stack-cores3", "chip": "esp32s3",
        "flash_bytes": 16777216, "profile": "runtime-configurable", "setup_protocol": 1,
    }.items()):
        raise ValueError("Use the runtime-configurable CoreS3 precompiled package")
    components = manifest.get("components")
    if not isinstance(components, list) or len(components) != len(OFFSETS):
        raise ValueError("The package must contain exactly four firmware components")
    seen: set[str] = set()
    limits = {"bootloader.bin": 0x8000, "partitions.bin": 0x1000,
              "boot_app0.bin": 0x2000, "firmware.bin": 0x640000}
    for component in components:
        if not isinstance(component, dict):
            raise ValueError("Invalid firmware component")
        name = component.get("name")
        if name not in OFFSETS or name in seen:
            raise ValueError("Unexpected or duplicate firmware component")
        seen.add(name)
        if component.get("offset") != f"0x{OFFSETS[name]:04x}":
            raise ValueError("Unsafe flash offset in manifest")
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing firmware component")
        size = path.stat().st_size
        if not 0 < size <= limits[name] or size != component.get("bytes"):
            raise ValueError("Firmware component size mismatch; download/extract the complete ZIP")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != component.get("sha256"):
            raise ValueError("Firmware hash mismatch; download/extract the complete ZIP")
        if name == "firmware.bin" and MARKER not in data:
            raise ValueError("Firmware lacks the USB runtime setup marker")
    # Check supplied support files as well. Names are constrained to this package.
    allowed = {*OFFSETS, "manifest.json", "README-zh.txt", "START-SETUP.cmd",
               "setup-requirements.txt", "scripts/setup_prebuilt.py",
               "scripts/configure_robot.py", "scripts/windows_tls_root.ps1"}
    checksum_path = root / "SHA256SUMS.txt"
    if checksum_path.is_symlink() or checksum_path.stat().st_size > 16384:
        raise ValueError("Invalid package checksums")
    checksum_names: set[str] = set()
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        parts = line.split("  ", 1)
        if len(parts) != 2:
            raise ValueError("Malformed package checksum")
        digest, name = parts
        if name not in allowed or name in checksum_names or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Unexpected package checksum entry")
        checksum_names.add(name)
        path = root / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Package checksum mismatch; extract the original complete ZIP again")
    if checksum_names != allowed:
        raise ValueError("Incomplete precompiled package checksums")
    return manifest


def encode_configuration(host: str, token: str, ssid: str, password: str, ca: str) -> bytes:
    host = configure_robot.validate_host(host)
    configure_robot.validate_credentials(ssid, password, token)
    if len(ca.encode("utf-8")) > 4096:
        raise ValueError("The verified root certificate exceeds the device's size limit")
    # verified_windows_root has already validated the server and PEM trust anchor.
    payload = json.dumps({"type": "stackchan.configure", "v": 1, "host": host,
                          "token": token, "ssid": ssid, "password": password, "ca_pem": ca},
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > min(MAX_LINE_BYTES, 6144):
        raise ValueError("Configuration exceeds the device's USB setup size limit")
    return payload + b"\n"


def flash(root: Path, port: str) -> None:
    # Revalidate immediately before the write, including the embedded setup marker.
    verify_package(root)
    command = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port,
               "--baud", "460800", "--after", "hard_reset", "write_flash",
               "--flash_mode", "keep", "--flash_freq", "keep", "--flash_size", "16MB"]
    for name, offset in OFFSETS.items():
        command.extend([f"0x{offset:04x}", str(root / name)])
    subprocess.run(command, cwd=root, check=True, timeout=180)  # noqa: S603 - fixed esptool argv, no shell or credentials.


def reset_for_configuration(port: str) -> None:
    command = [sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port,
               "--after", "hard_reset", "run"]
    try:
        subprocess.run(command, check=True, timeout=30, capture_output=True)  # noqa: S603 - no flash/erase, no secrets in argv.
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        print("Automatic reset did not finish. Press the CoreS3 Reset button now; setup will wait 45 seconds.")


def open_serial(port: str, serial_module: Any, deadline: float) -> Any:
    while time.monotonic() < deadline:
        connection = None
        try:
            # Set control lines before opening; do not deliberately enter the ROM bootloader.
            connection = serial_module.Serial(port=None, baudrate=115200, timeout=0.25, write_timeout=5)
            connection.dtr = False
            connection.rts = False
            connection.port = port
            connection.open()
            return connection
        except (OSError, serial_module.SerialException):
            if connection is not None:
                connection.close()
            time.sleep(0.25)
    raise SetupError("USB port did not return. Check Device Manager for a changed COM number, close other serial monitors, then retry your command with --port COM_NUMBER.")


class ProtocolReader:
    def __init__(self) -> None:
        self.pending = bytearray()
        self.discarding = False

    def read_line(self, connection: Any) -> bytes | None:
        raw = connection.read_until(b"\n", MAX_RESULT_BYTES + 1)
        if not self.discarding:
            self.pending.extend(raw)
            if len(self.pending) > MAX_RESULT_BYTES:
                self.pending.clear()
                self.discarding = True
        if not raw.endswith(b"\n"):
            return None
        if self.discarding:
            self.discarding = False
            return None
        complete = bytes(self.pending)
        self.pending.clear()
        return complete

    def read(self, connection: Any) -> dict[str, Any] | None:
        complete = self.read_line(connection)
        if complete is None:
            return None
        try:
            message = json.loads(complete)
        except (ValueError, UnicodeDecodeError):
            return None
        return message if isinstance(message, dict) else None


def provision(port: str, payload: bytes, serial_module: Any) -> None:
    """Send once after readiness; never print firmware output or credential data."""
    ready_deadline = time.monotonic() + 45
    connection = open_serial(port, serial_module, ready_deadline)
    reader = ProtocolReader()
    try:
        while time.monotonic() < ready_deadline:
            try:
                message = reader.read(connection)
            except (OSError, serial_module.SerialException):
                connection.close()
                connection = open_serial(port, serial_module, ready_deadline)
                reader = ProtocolReader()
                continue
            if message and message.get("type") == "stackchan.setup.ready" and message.get("v") == 1:
                break
        else:
            raise SetupError("No setup-ready response in 45 seconds. Run --configure-only with the current COM port and press Reset when prompted. No configuration was sent.")
        write_deadline = time.monotonic() + 4
        for offset in range(0, len(payload), 128):
            remaining = write_deadline - time.monotonic()
            if remaining <= 0:
                raise SetupError("USB configuration write timed out; no automatic resend was attempted. Retry --configure-only after Reset.")
            connection.write_timeout = min(0.5, remaining)
            chunk = payload[offset:offset + 128]
            if connection.write(chunk) != len(chunk):
                raise SetupError("USB configuration write was incomplete; retry --configure-only after Reset")
            # Hardware USB CDC ignores the nominal baud rate; pace writes to avoid RX overflow.
            time.sleep(0.005)
        drain_deadline = time.monotonic() + 5
        while connection.out_waiting:
            if time.monotonic() >= drain_deadline:
                raise SetupError("USB output did not drain in 5 seconds. No automatic resend was attempted; check the cable and retry --configure-only.")
            time.sleep(0.01)
        result_deadline = time.monotonic() + 20
        while time.monotonic() < result_deadline:
            message = reader.read(connection)
            if not message or message.get("type") != "stackchan.setup.result":
                continue
            if message.get("ok") is True:
                return
            # Do not echo even an apparent error code from untrusted serial output.
            raise SetupError("Device rejected configuration. Check Wi-Fi name/password, device token, hostname and available NVS storage, then retry --configure-only.")
        raise SetupError("No save confirmation within 20 seconds. The device may have restarted; this helper will not resend secrets automatically. Retry --configure-only if needed.")
    finally:
        connection.close()


def available_ports() -> list[Any]:
    return list(importlib.import_module("serial.tools.list_ports").comports())


def choose_port(requested: str | None) -> str:
    if requested:
        port = requested.upper()
    else:
        ports = available_ports()
        print("Available serial ports:")
        for item in ports:
            print(f"  {item.device}: {item.description}")
        port = input("CoreS3 COM port (for example COM5): ").strip().upper()
    if not re.fullmatch(r"COM[1-9][0-9]*", port):
        raise ValueError("Select the actual Windows COM port, for example COM5")
    return port


def diagnostic_line(raw: bytes) -> str | None:
    """Allow selected diagnostics, excluding configuration and network identifiers."""
    try:
        line = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not line or len(line) > MAX_RESULT_BYTES or any(ord(c) < 32 for c in line):
        return None
    if any(c in line for c in "{}") or re.search(
        r"(?i)(?:https?|wss?)://|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,63}\b"
        r"|\b(?:token|password|ssid|authorization|bearer|ca_pem)\s*[:=]", line
    ):
        return None
    if re.match(r"^\[(?:BOOT|OUTBOUND|PCM|ENV)\] ", line):
        # Session names may come from a remote caller; diagnostics only need sequence/size.
        line = re.sub(r"\b(?:session|session_id)=[^ ]+", "session=<redacted>", line)
        line = re.sub(r"\bcurrent=(?![0-9]+(?: |$))[^ ]+", "current=<redacted>", line)
        line = re.sub(r"(?<=\[PCM\] Session complete: ).+", "<redacted>", line)
        if re.search(r"[A-Za-z0-9_+/=-]{40,}", line):
            return None
        return line
    patterns = (
        r"ESP-ROM:esp32s3-[0-9]{8}",
        r"Build:[A-Z][a-z]{2} +[0-9]{1,2} +[0-9]{4}",
        r"rst:0x[0-9a-fA-F]+ \([A-Z0-9_]+\),boot:0x[0-9a-fA-F]+ \([A-Z0-9_]+\)",
        r"Saved PC:0x[0-9a-fA-F]+",
        r"Guru Meditation Error: Core +[0-9]+ panic'ed \((?:LoadProhibited|StoreProhibited|"
        r"InstrFetchProhibited|IllegalInstruction|LoadStoreError|LoadStoreAlignment|"
        r"Unhandled debug exception|Interrupt wdt timeout on CPU[0-9]+|"
        r"Cache disabled but cached memory region accessed|IntegerDivideByZero|DoubleException)\)\.?(?: Exception was unhandled\.)?",
        r"Debug exception reason: Stack canary watchpoint triggered \([A-Za-z0-9_-]{1,24}\)",
        r"Core +[0-9]+ register dump:",
        r"(?:(?:PC|PS|A[0-9]{1,2}|SAR|EXCCAUSE|EXCVADDR|LBEG|LEND|LCOUNT)\s*:\s*0x[0-9a-fA-F]+\s*)+",
        r"Backtrace: *(?:0x[0-9a-fA-F]+:0x[0-9a-fA-F]+ *)+(?:\|<-CORRUPTED)?",
        r"ELF file SHA256: [0-9a-fA-F]+",
        r"abort\(\) was called at PC 0x[0-9a-fA-F]+ on core [0-9]+",
        r"CORRUPT HEAP: Bad (?:head|tail) at 0x[0-9a-fA-F]+\. Expected 0x[0-9a-fA-F]+ got 0x[0-9a-fA-F]+",
        r"Rebooting\.\.\.",
        r"Brownout detector was triggered",
    )
    return line if any(re.fullmatch(pattern, line) for pattern in patterns) else None


def monitor(port: str, serial_module: Any, seconds: int = 90) -> None:
    if not 1 <= seconds <= 300:
        raise SetupError("Monitor duration must be between 1 and 300 seconds")
    deadline = time.monotonic() + seconds
    connection = open_serial(port, serial_module, deadline)
    reader = ProtocolReader()
    shown = 0
    print(f"Read-only diagnostic monitor: {seconds} seconds. No reset or configuration commands will be sent.")
    print("Trigger one short speech in Kelivo while this runs. Press the device Reset button only if you need boot diagnostics.")
    try:
        while time.monotonic() < deadline:
            try:
                raw = reader.read_line(connection)
            except (OSError, serial_module.SerialException):
                connection.close()
                print("USB disconnected; waiting for the same COM port within the remaining monitor time...")
                connection = open_serial(port, serial_module, deadline)
                reader = ProtocolReader()
                continue
            if raw is not None and (safe := diagnostic_line(raw)) is not None:
                print(safe, flush=True)
                shown += 1
    finally:
        connection.close()
    print(f"Monitor finished: {shown} permitted diagnostic lines shown; no log file was written.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="gojobody.up.railway.app")
    parser.add_argument("--port", help="Current CoreS3 COM port, for example COM5")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--configure-only", action="store_true", help="Reset and update USB configuration without flashing")
    mode.add_argument("--flash-only", action="store_true", help="Flash verified firmware and preserve existing device configuration")
    mode.add_argument("--monitor", action="store_true", help="Read selected diagnostics for up to 90 seconds; no reset or writes")
    mode.add_argument("--list-ports", action="store_true")
    parser.add_argument("--monitor-seconds", type=int, default=90, help="Monitor duration, 1 to 300 seconds (default: 90)")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.version_info < (3, 11):
        parser.error("Run this helper with Python 3.11 or newer on the Windows computer connected to CoreS3")
    try:
        serial_module = importlib.import_module("serial")
        if args.list_ports:
            for item in available_ports():
                print(f"{item.device}: {item.description}")
            return 0
        if args.monitor:
            monitor(choose_port(args.port), serial_module, args.monitor_seconds)
            return 0
        verify_package(ROOT)
        port = choose_port(args.port)
        if args.flash_only:
            print("Flashing verified firmware only; existing device Wi-Fi and Railway settings are preserved.")
            flash(ROOT, port)
            print("Flash finished. Compatible existing runtime configuration will be reused; an unconfigured device waits for USB setup.")
            print("To collect diagnostics, run this helper with --monitor --port and the current COM number.")
            return 0
        if not sys.stdin.isatty():
            raise SetupError("Run in an interactive Windows terminal so passwords can be entered without echo")
        host = configure_robot.validate_host(args.host)
        print(f"Verifying {host}:443 with Windows certificate trust...")
        ca = configure_robot.verified_windows_root(host)
        ssid = getpass.getpass("2.4 GHz Wi-Fi name / SSID (hidden): ")
        password = getpass.getpass("Wi-Fi password (hidden; empty for open Wi-Fi): ")
        token = getpass.getpass("Railway STACKCHAN_RELAY_DEVICE_TOKEN (hidden): ").strip()
        payload = encode_configuration(host, token, ssid, password, ca)
        print("Settings are held in memory and sent only over the selected USB serial port.")
        if args.configure_only:
            print("Entering USB setup. If needed, press the CoreS3 Reset button while waiting.")
            reset_for_configuration(port)
        else:
            print("Flashing the verified precompiled firmware; no PlatformIO or compiler download is needed...")
            flash(ROOT, port)
        print("Waiting for USB setup readiness (up to 45 seconds)...")
        provision(port, payload, serial_module)
    except ModuleNotFoundError:
        print("Missing USB tools. Run START-SETUP.cmd to install the pinned setup requirements.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError:
        print("Flashing failed; configuration was not sent. Check USB cable/COM port and retry the same complete package.", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("A setup step timed out and was stopped. Check network/USB connection, then retry.", file=sys.stderr)
        return 1
    except SetupError as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError):
        # Validation helpers use fixed messages but serial/OS exceptions may contain payloads.
        print("Setup did not complete. Check the complete package, Windows time/network, the selected COM port and entered settings. If flashing finished, retry with --configure-only; no full erase is needed.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    print("Device confirmed settings saved and is restarting. Credentials were not written to computer files.")
    print("Next verify device online, one camera picture, a short spoken phrase and microphone recording in Kelivo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
