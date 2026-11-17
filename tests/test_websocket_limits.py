"""Test build patching with a small pinned-source fixture, without installed libraries."""

import json
import runpy
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "firmware/scripts/websocket_limits.py"
FIXTURES = Path(__file__).with_name("fixtures")
READ_CALLBACK = (FIXTURES / "websockets_2_7_2_readcb.cpp").read_text()
LIMIT = "#define WEBSOCKETS_MAX_DATA_SIZE (15 * 1024)"
HEADER = "\n".join([LIMIT] * 6) + "\n"
SOURCE = r'''#include "WebSockets.h"
void WebSockets::clientDisconnect(WSclient_t * client, uint16_t code, char * reason, size_t reasonLen) {
    DEBUG_WEBSOCKETS("[WS][%d][handleWebsocket] clientDisconnect code: %u\n", client->num, code);
}
void WebSockets::handleWebsocketCb(WSclient_t * client) {
    if(header->payloadLen > WEBSOCKETS_MAX_DATA_SIZE) {
        clientDisconnect(client, 1009);
        return;
    }
    if(header->payloadLen > 0) {
        payload = (uint8_t *)malloc(header->payloadLen + 1);
        if(!payload) {
            DEBUG_WEBSOCKETS("[WS][%d][handleWebsocket] to less memory to handle payload %d!\n", client->num, header->payloadLen);
            clientDisconnect(client, 1011);
            return;
        }
    }
}
void WebSockets::handleWebsocketPayloadCb(WSclient_t * client, bool ok, uint8_t * payload) {
    WSMessageHeader_t * header = &client->cWsHeaderDecode;
    if(ok) {
        switch(header->opCode) {
            case WSop_close: {
#ifndef NODEBUG_WEBSOCKETS
                uint16_t reasonCode = 1000;
#endif
            } break;
        }
    }
}
''' + READ_CALLBACK


@pytest.fixture(scope="module")
def patcher():
    registered = []
    environment = SimpleNamespace(AddBuildMiddleware=lambda *args: registered.append(args))
    namespace = runpy.run_path(str(SCRIPT), init_globals={"Import": lambda _: None, "env": environment})
    assert len(registered) == 1
    assert registered[0][1] == "*/WebSockets/src/WebSockets.cpp"
    return SimpleNamespace(**namespace)


def make_dependency(tmp_path: Path, source_text: str = SOURCE) -> tuple[Path, Path, Path]:
    root = tmp_path / "libdeps"
    source = root / "WebSockets/src/WebSockets.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(source_text)
    header = source.with_name("WebSockets.h")
    header.write_text(HEADER)
    (source.parent.parent / "library.json").write_text(json.dumps({"version": "2.7.2"}))
    return root, source, header


def test_patch_is_idempotent_and_keeps_numeric_diagnostics_when_debug_disabled(tmp_path, patcher):
    root, source, header = make_dependency(tmp_path)
    patcher.patch_dependency(source, root)
    once_source, once_header = source.read_text(), header.read_text()
    patcher.patch_dependency(source, root)
    assert source.read_text() == once_source
    assert header.read_text() == once_header
    assert once_header.count("#ifndef WEBSOCKETS_MAX_DATA_SIZE") == 6
    assert once_source.count("ps_malloc(header->payloadLen + 1)") == 1
    assert once_source.count("stackchanWebSocketDiagnostic(") == 11
    assert '#undef NODEBUG_WEBSOCKETS' not in once_source
    assert once_source.index('stackchanWebSocketDiagnostic("peer_close"') < once_source.index("#ifndef NODEBUG_WEBSOCKETS")
    assert 'stackchanWebSocketDiagnostic("library_close", code, 0, 0)' in once_source
    assert 'stackchanWebSocketDiagnostic("peer_close", stackchanPeerCode, 0, 0)' in once_source
    for event in ("rx_no_tcp", "rx_disconnected", "rx_timeout"):
        assert f'stackchanWebSocketDiagnostic("{event}", 1002, stackchanRequested, stackchanRequested - n)' in once_source
    assert once_source.count("header->opCode == WSop_binary || header->opCode == WSop_continuation ||") == 2


def test_existing_frame_limit_and_psram_patch_can_be_upgraded(tmp_path, patcher):
    root, source, header = make_dependency(tmp_path)
    header.write_text(patcher.patched_header(HEADER))
    allocation = "payload = (uint8_t *)malloc(header->payloadLen + 1);"
    old_psram_patch = (
        "#if defined(ESP32)\n"
        "        payload = (uint8_t *)ps_malloc(header->payloadLen + 1);\n"
        "#else\n        " + allocation + "\n"
        "#endif // Stackchan PSRAM receive buffer"
    )
    source.write_text(SOURCE.replace(allocation, old_psram_patch))
    patcher.patch_dependency(source, root)
    assert source.read_text().count("stackchanWebSocketDiagnostic(") == 11
    assert source.read_text().count("ps_malloc(header->payloadLen + 1)") == 1


@pytest.mark.parametrize("anchor", [
    '#include "WebSockets.h"',
    'payload = (uint8_t *)malloc(header->payloadLen + 1);',
    '    if(header->payloadLen > WEBSOCKETS_MAX_DATA_SIZE) {',
    '            case WSop_close: {\n#ifndef NODEBUG_WEBSOCKETS',
    '            DEBUG_WEBSOCKETS("[readCb] receive TIMEOUT! %lu\\n", (millis() - t));',
])
def test_missing_or_changed_anchor_never_partially_writes_files(tmp_path, patcher, anchor):
    root, source, header = make_dependency(tmp_path, SOURCE.replace(anchor, "/* changed upstream */"))
    before_source, before_header = source.read_bytes(), header.read_bytes()
    with pytest.raises(RuntimeError, match="changed"):
        patcher.patch_dependency(source, root)
    assert source.read_bytes() == before_source
    assert header.read_bytes() == before_header


def test_changed_library_version_never_writes_files(tmp_path, patcher):
    root, source, header = make_dependency(tmp_path)
    (source.parent.parent / "library.json").write_text(json.dumps({"version": "2.7.3"}))
    with pytest.raises(RuntimeError, match="version"):
        patcher.patch_dependency(source, root)
    assert source.read_text() == SOURCE
    assert header.read_text() == HEADER


def test_partially_applied_header_fails_closed(tmp_path, patcher):
    root, source, header = make_dependency(tmp_path)
    partial = HEADER.replace(LIMIT, patcher.LIMIT_OVERRIDE, 1)
    header.write_text(partial)
    with pytest.raises(RuntimeError, match="incomplete"):
        patcher.patch_dependency(source, root)
    assert source.read_text() == SOURCE
    assert header.read_text() == partial


def test_duplicate_anchor_fails_closed(tmp_path, patcher):
    root, source, header = make_dependency(tmp_path, SOURCE + '#include "WebSockets.h"\n')
    original = source.read_bytes()
    with pytest.raises(RuntimeError, match="changed"):
        patcher.patch_dependency(source, root)
    assert source.read_bytes() == original
    assert header.read_text() == HEADER


def test_altered_diagnostic_patch_is_rejected_without_writes(tmp_path, patcher):
    root, source, header = make_dependency(tmp_path)
    altered = patcher.patched_source(SOURCE).replace('"rx_timeout"', '"unreviewed_event"')
    source.write_text(altered)
    with pytest.raises(RuntimeError, match="inconsistent"):
        patcher.patch_dependency(source, root)
    assert source.read_text() == altered
    assert header.read_text() == HEADER


def test_dependency_outside_project_cache_is_never_modified(tmp_path, patcher):
    root, source, header = make_dependency(tmp_path)
    with pytest.raises(RuntimeError, match="non-local"):
        patcher.patch_dependency(source, root / "different-project")
    assert source.read_text() == SOURCE
    assert header.read_text() == HEADER


def test_receive_policy_patch_does_not_change_connect_or_write_timeout(patcher):
    unchanged_code = "\nvoid unchanged() { if((millis() - t) > WEBSOCKETS_TCP_TIMEOUT) { return; } }\n"
    result = patcher.patched_source(SOURCE + unchanged_code)
    assert result.endswith(unchanged_code)
    assert result.count("if(stackchanWebSocketReadIdleExpired(t))") == 1
    assert result.count('stackchanWebSocketDiagnostic("rx_deadline"') == 2


@pytest.fixture(scope="module")
def receiver_binaries(tmp_path_factory, patcher):
    compiler = shutil.which("g++")
    if not compiler:
        pytest.skip("The pinned WebSockets receive callback regression needs host g++")
    root = tmp_path_factory.mktemp("websocket-read-callback")
    harness = (FIXTURES / "websocket_receive_harness.cpp").read_text()
    binaries = {}
    for label, callback in (("original", READ_CALLBACK), ("patched", patcher.patched_read_callback(READ_CALLBACK))):
        build = root / label
        build.mkdir()
        (build / "read_callback_under_test.inc").write_text(callback)
        (build / "harness.cpp").write_text(harness)
        binary = build / "receive"
        subprocess.run(  # noqa: S603 - local compiler and checked-in test fixtures, no shell.
            [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
             f"-DCALLBACK_PATCHED={int(label == 'patched')}", str(build / "harness.cpp"), "-o", str(binary)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        binaries[label] = binary
    return binaries


def test_original_callback_reproduces_observed_gap_failure(receiver_binaries):
    result = subprocess.run(  # noqa: S603 - executable compiled by this fixture.
        [str(receiver_binaries["original"]), "observed-gap"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    assert "received=13900 success=0" in result.stdout


@pytest.mark.parametrize("scenario", [
    "observed-gap", "drip-deadline", "pcm-idle", "control-idle", "last-read-deadline",
    "shared-idle", "disconnect", "zero-progress",
])
def test_patched_real_callback_receive_boundaries(receiver_binaries, scenario):
    result = subprocess.run(  # noqa: S603 - executable compiled by this fixture.
        [str(receiver_binaries["patched"]), scenario],
        check=True, capture_output=True, text=True, timeout=5,
    )
    assert f"PASS {scenario}" in result.stdout
