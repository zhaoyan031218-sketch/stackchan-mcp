import shutil
import ssl
import subprocess
from pathlib import Path

import pytest

from scripts.configure_robot import render_config, validate_credentials, validate_host, write_config

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-device-token-0123456789abcdef"


@pytest.fixture
def root_ca():
    # A real local trust-store certificate; no network or private key needed.
    der = ssl.create_default_context().get_ca_certs(binary_form=True)[0]
    return ssl.DER_cert_to_PEM_cert(der)


def test_generated_config_compiles_with_escaped_credentials(tmp_path, root_ca):
    ssid = 'robot-"wifi"-测试'
    password = 'secret\\with"quotes'
    template = (ROOT / "firmware/config.h.example").read_text(encoding="utf-8")
    content = render_config(template, "robot.example.com", ssid, password, TOKEN, root_ca)
    header = tmp_path / "config.h"
    write_config(header, content)
    assert "#define STACKCHAN_OUTBOUND_ENABLED 1" in content
    assert "#define WIFI_NETWORK_COUNT 1" in content
    assert "#define STACKCHAN_OUTBOUND_HOST \"robot.example.com\"" in content
    assert "pool.ntp.org" in content
    # Compile and check the actual C++ literals, including UTF-8 and PEM newlines.
    source = tmp_path / "check.cpp"
    source.write_text('#include "config.h"\n#include <iostream>\n'
                      'int main() { std::cout << WIFI_SSID_0 << "\\n" << WIFI_PASSWORD_0'
                      ' << "\\n" << STACKCHAN_OUTBOUND_CA_PEM; }\n', encoding="utf-8")
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("C++ literal validation requires g++")
    binary = tmp_path / "check"
    subprocess.run([compiler, "-std=c++17", str(source), "-o", str(binary)], check=True)  # noqa: S603 - fixed test source, no shell.
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True)  # noqa: S603 - freshly compiled test binary.
    assert result.stdout == ssid + "\n" + password + "\n" + root_ca


def test_existing_private_config_is_preserved(tmp_path):
    path = tmp_path / "config.h"
    path.write_text("existing private settings")
    with pytest.raises(FileExistsError):
        write_config(path, "replacement")
    assert path.read_text() == "existing private settings"


@pytest.mark.parametrize("host", ["https://robot.example.com", "robot.example.com/mcp",
                                  "robot.example.com:443", "foo..com", "foo;bar.com"])
def test_reject_invalid_hosts(host):
    with pytest.raises(ValueError):
        validate_host(host)


@pytest.mark.parametrize(("ssid", "password", "token"), [
    ("", "password", TOKEN), ("测" * 11, "password", TOKEN),
    ("wifi", "short", TOKEN), ("wifi", "password", "short"),
    ("wifi", "password", TOKEN + "\n"), ("wifi\x00", "password", TOKEN),
])
def test_reject_invalid_credentials_without_echoing_secrets(ssid, password, token):
    with pytest.raises(ValueError) as exc:
        validate_credentials(ssid, password, token)
    assert TOKEN not in str(exc.value)


def test_bad_ca_or_changed_template_fails_closed(root_ca):
    with pytest.raises(ssl.SSLError):
        render_config("", "robot.example.com", "wifi", "password", TOKEN, "not a CA")
    with pytest.raises(ValueError, match="Expected exactly one"):
        render_config("", "robot.example.com", "wifi", "password", TOKEN, root_ca)
