"""Exercise the cloud entrypoint against a simulated body, never real hardware."""

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
MCP_TOKEN = "test-mcp-0123456789abcdefghijklmnop"
DEVICE_TOKEN = "test-device-0123456789abcdefghijklmnop"
CONTROL_TOKEN = "test-control-0123456789abcdefghijklmnop"
AUTH = {"Authorization": f"Bearer {MCP_TOKEN}"}


@pytest.fixture(scope="module")
def firmware_ws_encoder(tmp_path_factory):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("firmware wire-format integration test requires g++")
    executable = tmp_path_factory.mktemp("ws-encoder") / "encoder"
    subprocess.run(  # noqa: S603 - compile a fixed repository fixture without a shell.
        [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "firmware/src"),
         str(ROOT / "tests/fixtures/outbound_ws_writer.cpp"), "-o", str(executable)],
        check=True, capture_output=True, timeout=30,
    )
    return executable


@pytest.fixture
def cloud_config(tmp_path):
    # The project loader reads .env relative to its package. A clean copy avoids
    # reading a developer's local .env, including when running the full suite.
    shutil.copytree(ROOT / "mcp_server", tmp_path / "mcp_server",
                    ignore=shutil.ignore_patterns("__pycache__"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {
        "PATH": os.environ["PATH"], "PYTHONPATH": str(tmp_path),
        "PORT": str(port), "STACKCHAN_TRANSPORT": "relay",
        "STACKCHAN_MCP_AUTH_TOKEN": MCP_TOKEN,
        "STACKCHAN_RELAY_DEVICE_TOKEN": DEVICE_TOKEN,
        "STACKCHAN_RELAY_TOKEN": CONTROL_TOKEN,
        "RAILWAY_PUBLIC_DOMAIN": "robot.example.com",
        "STACKCHAN_AUDIO_DIR": str(tmp_path / "audio"),
        "STACKCHAN_OTEL_LOG": str(tmp_path / "otel.jsonl"),
        "NO_PROXY": "127.0.0.1,localhost",
    }
    return tmp_path, port, env


@contextmanager
def running_cloud(cloud_config):
    directory, port, env = cloud_config
    base = f"http://127.0.0.1:{port}"
    with (directory / "server.log").open("w+") as log:
        process = subprocess.Popen([sys.executable, "-m", "mcp_server.railway"],
                                   cwd=directory, env=env, stdout=log, stderr=log)
        try:
            with httpx.Client(trust_env=False, timeout=1) as client:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        pytest.fail(log.read())
                    try:
                        if client.get(base + "/healthz").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(.02)
                else:
                    pytest.fail("Cloud entrypoint did not become ready")
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
                pytest.fail("Cloud entrypoint did not shut down gracefully")
            log.seek(0)
            output = log.read()
            assert process.returncode == 0, output
            for token in (MCP_TOKEN, DEVICE_TOKEN, CONTROL_TOKEN):
                assert token not in output


def test_public_routes_and_auth_roles(cloud_config):
    with running_cloud(cloud_config) as base, httpx.Client(base_url=base, trust_env=False) as http:
        assert http.get("/healthz").json() == {"ok": True}
        for token in ("", DEVICE_TOKEN, CONTROL_TOKEN):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            assert http.post("/mcp", headers=headers).status_code == 401
        for path in ("/device/face", "/relay/status", "/audio", "/", "/mcp/"):
            assert http.get(path, headers=AUTH).status_code == 404
        for token in (MCP_TOKEN, CONTROL_TOKEN, "wrong"):
            with pytest.raises(InvalidStatus) as exc, connect(
                base.replace("http:", "ws:") + "/stackchan/ws",
                additional_headers={"Authorization": f"Bearer {token}"},
            ):
                pass
            assert exc.value.response.status_code == 403
        headers = {**AUTH, "Content-Type": "application/json"}
        assert http.post("/mcp", headers={**headers, "Host": "evil.example"}, json={}).status_code == 421
        assert http.post("/mcp", headers={**headers, "Origin": "https://evil.example"}, json={}).status_code == 403
        # A configured Railway domain reaches MCP protocol validation, not 421.
        response = http.post("/mcp", headers={**headers, "Host": "robot.example.com"}, json={})
        assert response.status_code == 406  # MCP requires an Accept header.


def test_mcp_to_public_websocket_and_device_reconnect(cloud_config):
    async def exercise(base):
        async with (
            streamablehttp_client(base + "/mcp", headers=AUTH) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            assert (await session.initialize()).serverInfo.name == "stackchan"
            await session.send_ping()
            assert "stackchan_face" in {tool.name for tool in (await session.list_tools()).tools}
            offline = await session.call_tool("stackchan_face", {"expression": "happy"})
            assert "Error" in offline.content[0].text
            for _ in range(2):
                with connect(base.replace("http:", "ws:") + "/stackchan/ws",
                             additional_headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}) as ws:
                    ws.send(json.dumps({"type": "hello", "v": 1}))
                    # Ping/pong ensures the preceding hello was processed.
                    assert ws.ping().wait(timeout=2)
                    pending = asyncio.create_task(session.call_tool("stackchan_face", {"expression": "happy"}))
                    command = json.loads(await asyncio.to_thread(ws.recv, timeout=3))
                    assert command["path"] == "/face"
                    assert command["body"] == {"face": "happy"}
                    # A pending synchronous tool must not starve health or WSS.
                    async with httpx.AsyncClient(trust_env=False, timeout=1) as http:
                        assert (await http.get(base + "/healthz")).json() == {"ok": True}
                    ws.send(json.dumps({"type": "heartbeat"}))
                    ws.send(json.dumps({"id": command["id"], "status": 200,
                                        "body": {"success": True}}))
                    result = await asyncio.wait_for(pending, timeout=3)
                    assert "Face: happy" in result.content[0].text
                    assert not result.isError
    with running_cloud(cloud_config) as base:
        asyncio.run(exercise(base))


def test_mcp_camera_image_and_first_failure_survive_public_websocket(cloud_config):
    async def exercise(base):
        async with (
            streamablehttp_client(base + "/mcp", headers=AUTH) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            with connect(base.replace("http:", "ws:") + "/stackchan/ws",
                         additional_headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}) as ws:
                ws.send(json.dumps({"type": "hello", "v": 1}))
                assert ws.ping().wait(timeout=2)
                # Same byte count as the real device's captured JPEG. Synthetic
                # bytes exercise binary transport/MCP serialization, not a codec.
                jpeg = b"\xff\xd8" + bytes(i % 256 for i in range(7743)) + b"\xff\xd9"
                for valid in (True, False):
                    pending = asyncio.create_task(session.call_tool("stackchan_see", {}))
                    command = json.loads(await asyncio.to_thread(ws.recv, timeout=3))
                    assert command["path"] == "/snapshot"
                    assert command["query"] == {"preview": "1"}
                    ws.send(json.dumps({"id": command["id"], "status": 200,
                                        "binary_size": len(jpeg),
                                        "content_type": "image/jpeg" if valid else "private-metadata"}))
                    if valid:
                        ws.send(b"SCB1" + command["id"].encode() + jpeg)
                    # A hidden warmup/second capture would wait for another reply
                    # and fail this deadline instead of returning this first result.
                    result = await asyncio.wait_for(pending, timeout=3)
                    if valid:
                        image = result.content[0]
                        assert image.type == "image"
                        assert image.mimeType == "image/jpeg"
                        assert base64.b64decode(image.data) == jpeg
                        assert "Photo captured (7747 bytes)" in result.content[1].text
                    else:
                        message = result.content[0].text
                        assert "Relay HTTP 503" in message
                        assert "content type did not match" in message
                        assert "private-metadata" not in message
    with running_cloud(cloud_config) as base:
        asyncio.run(exercise(base))


@pytest.mark.parametrize("image_size", [7747, 128 * 1024])
def test_firmware_fragmented_image_reaches_mcp(cloud_config, firmware_ws_encoder, image_size):
    async def exercise(base):
        async with (
            streamablehttp_client(base + "/mcp", headers=AUTH) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            with connect(base.replace("http:", "ws:") + "/stackchan/ws",
                         additional_headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}) as ws:
                ws.send(json.dumps({"type": "hello", "v": 1}))
                assert ws.ping().wait(timeout=2)
                pending = asyncio.create_task(session.call_tool("stackchan_see", {}))
                command = json.loads(await asyncio.to_thread(ws.recv, timeout=3))
                assert command["path"] == "/snapshot"
                jpeg = b"\xff\xd8" + bytes(i % 256 for i in range(image_size - 4)) + b"\xff\xd9"
                wire = subprocess.run(  # noqa: S603 - fixed, locally compiled firmware fixture.
                    [str(firmware_ws_encoder)], input=b"SCB1" + command["id"].encode() + jpeg,
                    capture_output=True, check=True, timeout=5,
                ).stdout
                ws.send(json.dumps({"id": command["id"], "status": 200,
                                    "binary_size": len(jpeg), "content_type": "image/jpeg"}))
                # Already-framed production C++ output: bypass the Python sender's
                # framing so the real server independently validates/reassembles it.
                ws.socket.sendall(wire)
                result = await asyncio.wait_for(pending, timeout=3)
                assert result.content[0].type == "image"
                assert base64.b64decode(result.content[0].data) == jpeg
                # FIN/continuation correctness must leave the connection reusable.
                pending = asyncio.create_task(session.call_tool("stackchan_face", {"expression": "happy"}))
                command = json.loads(await asyncio.to_thread(ws.recv, timeout=3))
                assert command["path"] == "/face"
                ws.send(json.dumps({"id": command["id"], "status": 200, "body": {"success": True}}))
                assert "Face: happy" in (await asyncio.wait_for(pending, timeout=3)).content[0].text
    with running_cloud(cloud_config) as base:
        asyncio.run(exercise(base))


@pytest.mark.parametrize(("name", "value"), [
    ("STACKCHAN_MCP_AUTH_TOKEN", ""),
    ("STACKCHAN_MCP_AUTH_TOKEN", DEVICE_TOKEN),
    ("STACKCHAN_MCP_AUTH_TOKEN", "short"),
    ("STACKCHAN_RELAY_DEVICE_TOKEN", CONTROL_TOKEN),
    ("STACKCHAN_RELAY_TOKEN", ""),
    ("STACKCHAN_TRANSPORT", "direct"),
    ("PORT", "0"),
    ("STACKCHAN_PUBLIC_HOST", "https://example.com/path"),
])
def test_invalid_cloud_configuration_fails_closed(cloud_config, name, value):
    directory, _, env = cloud_config
    result = subprocess.run([sys.executable, "-m", "mcp_server.railway"],
                            cwd=directory, env={**env, name: value},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "Uvicorn running" not in result.stderr
    for token in (MCP_TOKEN, DEVICE_TOKEN, CONTROL_TOKEN):
        assert token not in result.stderr
