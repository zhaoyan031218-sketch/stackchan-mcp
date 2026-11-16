"""Create a private CoreS3 configuration on the Windows computer with the robot."""

import argparse
import getpass
import json
import os
import re
import ssl
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_host(host: str) -> str:
    host = host.strip().lower()
    labels = host.split(".")
    if (len(host) > 253 or len(labels) < 2
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in labels)):
        raise ValueError("Enter a DNS hostname only, without https://, a port, or /mcp")
    return host


def validate_credentials(ssid: str, password: str, token: str) -> None:
    if not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise ValueError("Wi-Fi name must be 1 to 32 UTF-8 bytes")
    if any(ord(c) < 32 or ord(c) == 127 for c in ssid + password):
        raise ValueError("Wi-Fi credentials must not contain control characters")
    if (password and not 8 <= len(password.encode("utf-8")) <= 63
            and not re.fullmatch(r"[0-9a-fA-F]{64}", password)):
        raise ValueError("Wi-Fi password must be 8 to 63 UTF-8 bytes, or 64 hex digits")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{24,512}", token):
        raise ValueError("Device token must contain 24 to 512 ASCII bearer-token characters")


def verified_windows_root(host: str) -> str:
    if sys.platform != "win32":
        raise RuntimeError("Run this configuration helper on your Windows computer")
    script = (ROOT / "scripts" / "windows_tls_root.ps1").read_text(encoding="utf-8")
    powershell = Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(  # noqa: S603 - local checked-in script; hostname is passed as data.
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "STACKCHAN_CERT_HOST": host},
        capture_output=True, text=True, timeout=40, check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "Windows could not verify the server TLS certificate. Check Internet access, "
            "the hostname and Windows date/time; no config was written. "
            "Do not disable certificate verification."
        )
    pem = result.stdout.strip() + "\n"
    # Parse the returned trust anchor before putting it in a C++ string literal.
    ssl.create_default_context(cadata=pem)
    return pem


def render_config(template: str, host: str, ssid: str, password: str, token: str, ca: str) -> str:
    host = validate_host(host)
    validate_credentials(ssid, password, token)
    ssl.create_default_context(cadata=ca)
    values = {
        "WIFI_NETWORK_COUNT": "1",
        "WIFI_SSID_0": json.dumps(ssid, ensure_ascii=False),
        "WIFI_PASSWORD_0": json.dumps(password, ensure_ascii=False),
        "WIFI_SSID_1": '""',
        "WIFI_PASSWORD_1": '""',
        "STACKCHAN_OUTBOUND_ENABLED": "1",
        "STACKCHAN_OUTBOUND_HOST": json.dumps(host),
        "STACKCHAN_OUTBOUND_PORT": "443",
        "STACKCHAN_OUTBOUND_DEVICE_TOKEN": json.dumps(token),
        "STACKCHAN_OUTBOUND_CA_PEM": json.dumps(ca),
    }
    for name, value in values.items():
        replacement = f"#define {name} {value}"
        template, count = re.subn(
            rf"^#define {name}\b[^\n]*$", lambda _, replacement=replacement: replacement,
            template, flags=re.MULTILINE,
        )
        if count != 1:
            raise ValueError(f"Expected exactly one {name} definition in config.h.example")
    return template


def write_config(path: Path, content: str) -> None:
    # Exclusive creation protects existing settings, including a dangling symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
        output.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Railway DNS hostname (not a URL)")
    args = parser.parse_args()
    path = ROOT / "firmware" / "src" / "config.h"
    if path.exists() or path.is_symlink():
        parser.error("firmware/src/config.h already exists; preserve it before creating a new configuration")
    try:
        host = validate_host(args.host)
        print(f"Verifying the TLS certificate for {host}:443 using Windows trust...")
        ca = verified_windows_root(host)
        ssid = input("2.4 GHz Wi-Fi name (SSID): ")
        password = getpass.getpass("Wi-Fi password (hidden; empty only for an open network): ")
        token = getpass.getpass("Railway STACKCHAN_RELAY_DEVICE_TOKEN (hidden): ").strip()
        content = render_config((ROOT / "firmware" / "config.h.example").read_text(encoding="utf-8"),
                                host, ssid, password, token, ca)
        write_config(path, content)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Configuration failed: {exc}\n")
    print("Created private firmware/src/config.h. Credentials were not printed.")
    print("Next: build/upload the m5stack-cores3 environment, NOT m5stack-cores3-public.")


if __name__ == "__main__":
    main()
