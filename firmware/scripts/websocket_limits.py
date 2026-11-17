"""Bound and instrument the pinned WebSockets copy without logging wire data."""

import json
from pathlib import Path

Import("env")  # noqa: F821 - SCons injects Import and the requested environment.


LIMIT_DECLARATION = "#define WEBSOCKETS_MAX_DATA_SIZE (15 * 1024)"
LIMIT_OVERRIDE = (
    "#ifndef WEBSOCKETS_MAX_DATA_SIZE\n"
    + LIMIT_DECLARATION
    + "\n#endif // Stackchan bounded frame override"
)


def replace_once(text: str, original: str, replacement: str, label: str) -> str:
    """Accept the original or our exact patch, never a partially changed anchor."""
    if text.count(replacement) == 1:
        if text.count(original) != replacement.count(original):
            raise RuntimeError(f"WebSockets {label} has duplicate anchors")
        return text
    if text.count(original) != 1:
        raise RuntimeError(f"WebSockets {label} changed")
    return text.replace(original, replacement, 1)


def patched_header(text: str) -> str:
    # WebSockets 2.7.2 repeats this default in six platform branches.
    if text.count(LIMIT_DECLARATION) != 6:
        raise RuntimeError("WebSockets frame-limit declarations changed")
    patched_count = text.count(LIMIT_OVERRIDE)
    if patched_count == 6:
        return text
    if patched_count:
        raise RuntimeError("WebSockets frame-limit patch is incomplete")
    return text.replace(LIMIT_DECLARATION, LIMIT_OVERRIDE)


def patched_read_callback(text: str) -> str:
    """Patch the synchronous pinned read callback; connect/write stay unchanged."""
    original = "#else\n    unsigned long t = millis();\n    ssize_t len;"
    text = replace_once(text, original, (
        "#else\n    const size_t stackchanRequested = n;\n"
        "    unsigned long t = millis();\n    ssize_t len;"
    ), "receive progress counter")

    for message, event in (
        ('[readCb] tcp is null!\\n', "rx_no_tcp"),
        ('[readCb] not connected!\\n', "rx_disconnected"),
        ('[readCb] receive TIMEOUT! %lu\\n', "rx_timeout"),
    ):
        arguments = ', (millis() - t)' if event == "rx_timeout" else ""
        original = f'            DEBUG_WEBSOCKETS("{message}"{arguments});'
        text = replace_once(text, original, (
            f'            stackchanWebSocketDiagnostic("{event}", 1002, stackchanRequested, stackchanRequested - n);\n'
        ) + original, f"{event} diagnostic")

    original = "    while(n > 0) {\n        if(client->tcp == NULL) {"
    text = replace_once(text, original, (
        "    while(n > 0) {\n"
        "        if(!stackchanWebSocketReadCurrent()) {\n"
        '            stackchanWebSocketDiagnostic("rx_deadline", 1002, stackchanRequested, stackchanRequested - n);\n'
        "            if(cb) cb(client, false);\n"
        "            return false;\n"
        "        }\n"
        "        if(client->tcp == NULL) {"
    ), "receive deadline before read")

    text = replace_once(text,
        "        if((millis() - t) > WEBSOCKETS_TCP_TIMEOUT) {",
        "        if(stackchanWebSocketReadIdleExpired(t)) {",
        "receive idle timeout")

    original = "            n -= len;\n            // DEBUG_WEBSOCKETS(\"Receive %d left %d!\\n\", len, n);"
    text = replace_once(text, original, original.replace(
        "            n -= len;", "            n -= len;\n"
        "            stackchanWebSocketReadProgress(static_cast<size_t>(len));"
    ), "receive progress notification")

    original = "        if(n > 0) {\n            WEBSOCKETS_YIELD();"
    text = replace_once(text, original, (
        "        // A read can return after the deadline due to task preemption.\n"
        "        if(!stackchanWebSocketReadCurrent()) {\n"
        '            stackchanWebSocketDiagnostic("rx_deadline", 1002, stackchanRequested, stackchanRequested - n);\n'
        "            if(cb) cb(client, false);\n"
        "            return false;\n"
        "        }\n"
    ) + original, "receive deadline after read")
    return text


def read_callback_span(text: str) -> tuple[int, int]:
    signature = "bool WebSockets::readCb(WSclient_t * client, uint8_t * out, size_t n, WSreadWaitCb cb) {"
    if text.count(signature) != 1:
        raise RuntimeError("WebSockets receive callback changed")
    start = text.index(signature)
    end = text.find("\n}\n", start)
    if end < 0:
        raise RuntimeError("WebSockets receive callback ending changed")
    return start, end + 3


def patched_source(text: str) -> str:
    original = "payload = (uint8_t *)malloc(header->payloadLen + 1);"
    text = replace_once(text, original, (
        "#if defined(ESP32)\n"
        "        payload = (uint8_t *)ps_malloc(header->payloadLen + 1);\n"
        "#else\n        " + original + "\n"
        "#endif // Stackchan PSRAM receive buffer"
    ), "receive allocation")

    original = '#include "WebSockets.h"'
    text = replace_once(text, original, original + (
        "\n\n// Stackchan numeric-only diagnostics; never pass frame data or peer text.\n"
        "extern void stackchanWebSocketDiagnostic(const char* event, uint16_t code,\n"
        "                                        size_t expected, size_t received);"
    ), "diagnostic declaration")
    original = "                                        size_t expected, size_t received);"
    text = replace_once(text, original, original + (
        "\nextern bool stackchanWebSocketReadIdleExpired(uint32_t defaultReadLastProgressMs);\n"
        "extern bool stackchanWebSocketReadCurrent();\n"
        "extern void stackchanWebSocketReadProgress(size_t bytes);"
    ), "receive policy declarations")

    original = "void WebSockets::clientDisconnect(WSclient_t * client, uint16_t code, char * reason, size_t reasonLen) {"
    text = replace_once(text, original, original + (
        '\n    stackchanWebSocketDiagnostic("library_close", code, 0, 0);'
    ), "close diagnostic")

    original = "    if(header->payloadLen > WEBSOCKETS_MAX_DATA_SIZE) {"
    text = replace_once(text, original, (
        "    if(header->opCode == WSop_binary || header->opCode == WSop_continuation ||\n"
        "       header->payloadLen > 8192) {\n"
        '        stackchanWebSocketDiagnostic("frame_begin", 0, header->payloadLen, 0);\n'
        "    }\n\n"
    ) + original, "frame begin diagnostic")

    original = (
        "        if(!payload) {\n"
        '            DEBUG_WEBSOCKETS("[WS][%d][handleWebsocket] to less memory to handle payload %d!\\n", client->num, header->payloadLen);'
    )
    text = replace_once(text, original, original.replace(
        "        if(!payload) {", "        if(!payload) {\n"
        '            stackchanWebSocketDiagnostic("rx_alloc_failed", 1011, header->payloadLen, 0);'
    ), "allocation failure diagnostic")

    original = "    WSMessageHeader_t * header = &client->cWsHeaderDecode;\n    if(ok) {"
    text = replace_once(text, original, original + (
        "\n        if(header->opCode == WSop_binary || header->opCode == WSop_continuation ||\n"
        "           header->payloadLen > 8192) {\n"
        '            stackchanWebSocketDiagnostic("frame_complete", 0, header->payloadLen, header->payloadLen);\n'
        "        }"
    ), "frame completion diagnostic")

    original = "            case WSop_close: {\n#ifndef NODEBUG_WEBSOCKETS"
    text = replace_once(text, original, (
        "            case WSop_close: {\n"
        "                const uint16_t stackchanPeerCode = header->payloadLen >= 2\n"
        "                    ? static_cast<uint16_t>((payload[0] << 8) | payload[1]) : 1005;\n"
        '                stackchanWebSocketDiagnostic("peer_close", stackchanPeerCode, 0, 0);\n'
        "#ifndef NODEBUG_WEBSOCKETS"
    ), "peer close diagnostic")

    start, end = read_callback_span(text)
    text = text[:start] + patched_read_callback(text[start:end]) + text[end:]
    if (text.count("stackchanWebSocketDiagnostic(") != 11
            or text.count("ps_malloc(header->payloadLen + 1)") != 1
            or text.count("stackchanWebSocketReadIdleExpired(") != 2
            or text.count("stackchanWebSocketReadCurrent(") != 3
            or text.count("stackchanWebSocketReadProgress(") != 2):
        raise RuntimeError("WebSockets diagnostic or allocation patch is inconsistent")
    return text


def patch_dependency(source: Path, root: Path) -> None:
    source = source.resolve()
    root = root.resolve()
    if root not in source.parents or source.name != "WebSockets.cpp":
        raise RuntimeError(f"Refusing to patch a non-local WebSockets dependency: {source}")
    manifest = source.parent.parent / "library.json"
    if json.loads(manifest.read_text())["version"] != "2.7.2":
        raise RuntimeError("Recheck the WebSockets patch for this version")
    header = source.with_name("WebSockets.h")
    old_header, old_source = header.read_text(), source.read_text()
    # Validate every anchor in both files before changing either local file.
    new_header = patched_header(old_header)
    new_source = patched_source(old_source)
    if new_header != old_header:
        header.write_text(new_header)
    if new_source != old_source:
        source.write_text(new_source)


def patch_websocket_header(env, node):
    patch_dependency(Path(node.srcnode().get_abspath()), Path(env.subst("$PROJECT_LIBDEPS_DIR")))
    return node


env.AddBuildMiddleware(patch_websocket_header, "*/WebSockets/src/WebSockets.cpp")  # noqa: F821 - SCons supplies env.
