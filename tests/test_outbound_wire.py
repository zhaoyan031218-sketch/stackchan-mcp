"""Use a real loopback WebSocket, never hardware or a production listener."""

import json
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect

from mcp_server import audio_processing, stackchan_config
from mcp_server.outbound_relay import MAX_AUDIO_REPLY, RelaySettings, create_app
from mcp_server.stackchan_client import StackchanClient, post_pcm_stream

DEVICE_TOKEN = "wire-device-0123456789abcdef"
CONTROL_TOKEN = "wire-control-0123456789abcdef"
CONTROL_HEADERS = {"Authorization": f"Bearer {CONTROL_TOKEN}"}


@pytest.fixture(scope="module")
def firmware_pcm_receiver(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    compiler = shutil.which("g++")
    json_headers = root / "firmware/.pio/libdeps/native/ArduinoJson/src"
    if not compiler or not (json_headers / "ArduinoJson.h").is_file():
        pytest.skip("firmware request contract test requires g++ and native PlatformIO dependencies")
    executable = tmp_path_factory.mktemp("pcm-receiver") / "receiver"
    subprocess.run(  # noqa: S603 - fixed repository C++ fixture and parser, no shell.
        [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
         "-I", str(root / "firmware/src"), "-I", str(json_headers),
         str(root / "tests/fixtures/outbound_pcm_receiver.cpp"),
         str(root / "firmware/src/outbound_protocol.cpp"), "-o", str(executable)],
        check=True, capture_output=True, timeout=30,
    )
    return executable


@pytest.fixture
def wire_server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    settings = RelaySettings(DEVICE_TOKEN, CONTROL_TOKEN, command_timeout=2)
    config = uvicorn.Config(create_app(settings), ws="websockets", ws_max_queue=2,
                            ws_max_size=MAX_AUDIO_REPLY + 36, ws_per_message_deflate=False,
                            log_level="error", proxy_headers=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    try:
        assert server.started
        yield f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}/stackchan/ws"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


def test_real_websocket_pcm_snapshot_and_reconnect(wire_server):
    base, url = wire_server
    for cycle in range(2):
        with (
            connect(url, additional_headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}) as ws,
            httpx.Client(base_url=base, headers=CONTROL_HEADERS, timeout=4) as http,
            ThreadPoolExecutor() as pool,
        ):
            ws.send(json.dumps({"type": "hello", "v": 1}))
            deadline = time.monotonic() + 2
            while not http.get("/relay/status").json()["online"]:
                assert time.monotonic() < deadline
                time.sleep(0.005)
            pcm = b"\x00\x01" * 24576
            future = pool.submit(http.post, "/device/play/pcm",
                                 params={"session": f"test-{cycle}", "seq": 0, "final": 1},
                                 content=pcm, headers={"Content-Type": "audio/x-raw"})
            request = json.loads(ws.recv(timeout=2))
            assert request["path"] == "/play/pcm"
            assert ws.recv(timeout=2) == b"SCB1" + request["id"].encode() + pcm
            ws.send(json.dumps({"id": request["id"], "status": 200, "body": {"ok": True}}))
            assert future.result(timeout=3).json()["ok"]
            future = pool.submit(http.get, "/device/snapshot")
            request = json.loads(ws.recv(timeout=2))
            jpeg = b"\xff\xd8" + bytes(range(256)) * 60 + b"\xff\xd9"
            ws.send(json.dumps({"id": request["id"], "status": 200,
                                "binary_size": len(jpeg), "content_type": "image/jpeg"}))
            ws.send(b"SCB1" + request["id"].encode() + jpeg)
            response = future.result(timeout=3)
            assert response.content == jpeg
            assert response.headers["content-type"] == "image/jpeg"
            future = pool.submit(http.get, "/device/audio")
            request = json.loads(ws.recv(timeout=2))
            wav = b"RIFF" + bytes(256040)
            ws.send(json.dumps({"id": request["id"], "status": 200,
                                "binary_size": len(wav), "content_type": "audio/wav"}))
            ws.send(b"SCB1" + request["id"].encode() + wav)
            response = future.result(timeout=3)
            assert response.content == wav
            assert response.headers["content-type"] == "audio/wav"


def test_mcp_client_through_real_relay(wire_server, monkeypatch, tmp_path):
    base, url = wire_server
    monkeypatch.setattr(stackchan_config, "load_dotenv", lambda: None)
    monkeypatch.setenv("STACKCHAN_TRANSPORT", "relay")
    monkeypatch.delenv("STACKCHAN_RELAY_TOKEN_FILE", raising=False)
    config = replace(stackchan_config.load_config(), relay_url=base, relay_token=CONTROL_TOKEN,
                     pcm_first_segment_timeout=0, pcm_gain=1, pcm_limit=1, save_pcm=False,
                     pcm_declick_samples=0, pcm_zero_cross_window=0, pcm_segment_bytes=48 * 1024)
    client = StackchanClient(config)
    with (
        connect(url, additional_headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}) as ws,
        ThreadPoolExecutor() as pool,
    ):
        ws.send(json.dumps({"type": "hello", "v": 1}))
        deadline = time.monotonic() + 2
        while not client.relay_status()["online"]:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        for call, expected in [
            (lambda: client.set_face("happy"), "/face"),
            (lambda: client.gesture("nod"), "/nod"),
            (lambda: client.move(5, -5, 500), "/move"),
            (client.read_env, "/env"),
            (client.playback_status, "/playback/status"),
        ]:
            future = pool.submit(call)
            request = json.loads(ws.recv(timeout=2))
            assert request["path"] == expected
            ws.send(json.dumps({"id": request["id"], "status": 200, "body": {"success": True}}))
            assert future.result(timeout=3)["success"]
        pcm = b"\x00\x00" * 30000
        future = pool.submit(post_pcm_stream, client, iter([pcm]), tmp_path, audio_processing)
        received = bytearray()
        for seq in range(2):
            request = json.loads(ws.recv(timeout=2))
            assert request["query"]["seq"] == str(seq)
            assert request["query"]["final"] == str(seq)
            payload = ws.recv(timeout=2)
            assert isinstance(payload, bytes)
            assert payload[:36] == b"SCB1" + request["id"].encode()
            received.extend(payload[36:])
            ws.send(json.dumps({"id": request["id"], "status": 200,
                                "body": {"success": True, "staged": seq == 0}}))
        assert future.result(timeout=3)["success"]
        assert bytes(received) == pcm


@pytest.mark.parametrize("pcm_size", [2, 48 * 1024, 128 * 1024])
def test_first_staged_segment_matches_firmware_parser(wire_server, firmware_pcm_receiver, pcm_size):
    base, url = wire_server
    with (
        connect(url, additional_headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}) as ws,
        httpx.Client(base_url=base, headers=CONTROL_HEADERS, timeout=4) as http,
        ThreadPoolExecutor() as pool,
    ):
        ws.send(json.dumps({"type": "hello", "v": 1}))
        assert ws.ping().wait(timeout=2)
        future = pool.submit(http.post, "/device/play/pcm",
                             params={"session": "0123456789abcdef0123456789abcdef",
                                     "seq": 0, "final": 0, "mode": "staged"},
                             content=b"\x01\x00" * (pcm_size // 2),
                             headers={"Content-Type": "audio/x-raw"})
        metadata = ws.recv(timeout=2)
        binary = ws.recv(timeout=2)
        assert isinstance(metadata, str)
        assert isinstance(binary, bytes)
        parsed = subprocess.run(  # noqa: S603 - fixed locally compiled firmware parser fixture.
            [str(firmware_pcm_receiver)], input=metadata.encode() + b"\n" + binary,
            capture_output=True, check=True, timeout=1,
        )
        assert parsed.stdout == f"{pcm_size}\n".encode()
        request = json.loads(metadata)
        ws.send(json.dumps({"id": request["id"], "status": 200,
                            "body": {"success": True, "staged": True}}))
        assert future.result(timeout=3).json() == {"success": True, "staged": True}
        assert http.get("/relay/status").json()["online"] is True


def test_real_channel_booth_priority_and_disconnected_waiter(wire_server):
    base, url = wire_server
    with (
        connect(url, additional_headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}) as ws,
        httpx.Client(base_url=base, headers=CONTROL_HEADERS, timeout=4) as http,
        ThreadPoolExecutor() as pool,
    ):
        ws.send(json.dumps({"type": "hello", "v": 1}))
        deadline = time.monotonic() + 2
        while not http.get("/relay/status").json()["online"]:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        first = pool.submit(http.get, "/device/audio/status")
        active = json.loads(ws.recv(timeout=1))
        abandoned = pool.submit(http.post, "/device/booth", json={"enabled": True}, timeout=0.1)
        with pytest.raises(httpx.ReadTimeout):
            abandoned.result(timeout=1)
        deadline = time.monotonic() + 1
        while http.get("/relay/status").json()["booth_priority_reserved"]:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert http.get("/relay/status").json()["pending_path"] == "/audio/status"
        booth = pool.submit(http.post, "/device/booth", json={"enabled": True})
        deadline = time.monotonic() + 1
        while not http.get("/relay/status").json()["booth_priority_reserved"]:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert http.get("/device/audio/status").status_code == 409
        ws.send(json.dumps({"id": active["id"], "status": 200, "body": {"ready": False}}))
        assert first.result(timeout=1).status_code == 200
        command = json.loads(ws.recv(timeout=1))
        assert command["path"] == "/booth"
        assert command["body"] == {"enabled": True}
        ws.send(json.dumps({"id": command["id"], "status": 200, "body": {"success": True}}))
        assert booth.result(timeout=1).status_code == 200
        assert not http.get("/relay/status").json()["busy"]
        with pytest.raises(TimeoutError):
            ws.recv(timeout=0.05)
