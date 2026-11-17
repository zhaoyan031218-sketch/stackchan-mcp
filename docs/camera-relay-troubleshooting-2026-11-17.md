# Snapshot succeeds on the robot but MCP returns 503

## Observed evidence

The user's CoreS3 supports face commands, motion and MiniMax speech through
Railway, but `stackchan_see` repeatedly returns HTTP 503. The relevant serial
output from a failed attempt was:

```text
[CAM] GC0308 ready (QVGA 320x240)
E (177606) gdma: gdma_disconnect(299): no peripheral is connected to the channel
[CAM] Deinitialized
[TOUCH] Internal I2C resumed (sensor=ready)
[CAM] Captured JPEG: 7747 bytes
[HTTP] GET /snapshot -> 7747 bytes JPEG
```

Microphone recording continued afterward without a reboot message in the
provided excerpt. The preceding recording diagnostic reported about 116 KB
free heap and 7.8 MB free PSRAM; these are not allocation measurements at the
instant the photo response is prepared.

## Investigation and limits

The camera initialized and produced a JPEG. The `gdma` message appears during
deinitialization, followed by successful capture and bus recovery; it alone
does not establish the cause of the cloud failure or prove that all camera
cleanup is harmless.

In outbound mode, `CommandContext.send_P` copies JPEG data into a bounded PSRAM
response frame for later transmission by the network task. The `[HTTP]` log
is printed after that call but before the network task sends it, even if the
copy failed. It is **not proof that Railway received the image**. Copy failure
can return 503; oversize images return 413 (this 7,747-byte image is below the
128 KiB limit). A normal `captureJpeg` failure returns 500.

The relay can also return 503 when the device connection closes or when reply
metadata/framing fails validation. These failures were swallowed by a generic
exception handler. The client discarded the response's error body. In addition,
`snapshot()` first performed an ignored warmup request and then a second
capture, so a disconnected first request could be masked by the second
request's offline error. Firmware already discards a stale camera frame inside
each capture. Relay HTTP callers wait at least 22 seconds for the relay's
20-second command deadline, so the direct-mode 5-second warmup timeout is not
the active relay timeout.

**The original upload failure remains unconfirmed.** Current evidence points
to reply preparation, transport or validation after capture. No device access
or original photo bytes were available in this environment.

## Diagnostic correction

- Relay snapshots now make one capture request (`preview=1`). Direct LAN
  behavior is unchanged. A first failure is surfaced without a hidden retry.
- Controlled protocol failures retain distinct `error_code` values. Unknown
  exception text, WebSocket close reasons and raw provider/device bodies are
  not included in diagnostics.
- Interrupted requests log only the known route, reply stage, fixed code and
  announced binary size. `awaiting_reply` means no valid binary metadata was
  accepted; `awaiting_binary` means valid metadata arrived but the payload did
  not complete. It does not imply anything about physical camera health.
- The client displays fixed descriptions for known codes and exact known
  firmware failures, including response-buffer allocation failure. Other
  response bodies stay hidden.

Deploying these changes updates Railway only. Existing device firmware and
credentials remain compatible. They improve diagnosis and remove a redundant
capture; they do not by themselves prove physical photo upload is fixed.

## Verification and next live check

```sh
uv run --locked pytest tests/test_outbound_relay.py tests/test_relay_client.py tests/test_railway.py -q
uv run --locked ruff check .
uv run --locked pyright
uv run --locked pytest --cov=mcp_server --cov=scripts --cov-report=term-missing
make test-mcp
```

Focused tests passed (130 tests). Tests cover a synthetic 7,747-byte image
through real Uvicorn/WebSocket/MCP servers, exact returned image bytes, one
capture per tool invocation, first-failure preservation, invalid metadata,
truncated frames, disconnection before/after metadata and content redaction.
The image fixture tests transport and serialization, not JPEG decoding or a
physical camera.

The full Python suite passed (627 tests; 9 deprecation warnings), as did
`make test-mcp` (123 tests), ruff, pyright and the repository security audit.
The Railway image built successfully. Its non-root container passed the same
7,747-byte MCP image round-trip and first-failure test, as well as existing
authentication, device reconnection and graceful-shutdown smoke checks.

After the new Railway version is active, call only `stackchan_see` once. If it
still fails, collect the complete new tool error plus the corresponding
`Device channel interrupted` log. Use that evidence to decide whether firmware
send diagnostics, frame handling, or allocation changes are necessary. Do not
change pins, disable TLS, increase frame limits, or repeatedly reboot based
solely on the old generic 503.

## Follow-up: metadata accepted, binary message interrupted

After a device reconnect, the new diagnostic returned:

```text
Relay HTTP 503: Device announced binary data, then disconnected before it arrived; command was not retried.
```

This establishes that the server accepted the reply's request ID, status,
binary size and content type, then lost the channel before receiving the
complete WebSocket binary message. It does not distinguish zero bytes from a
partial message; the ASGI receiver only yields complete messages. The serial
excerpt repeated the old 7,747-byte capture, so it is not treated as a fresh
capture timestamp. The new server result is the decisive additional evidence.

Inspection of pinned arduinoWebSockets 2.7.2 `WebSockets::sendFrame` found two
relevant differences between short metadata and larger image replies:

- Only its small internal-buffer path (payload length below 1,400 bytes and
  enough free heap) generates a random masking key. Larger frames use the
  initialized all-zero key. RFC 6455 section 5.3 requires unpredictable keys
  for client-to-server frames; a constant key does not meet that requirement.
- Larger frames write their header separately, then pass the remaining full
  payload to TLS. Arduino's TLS wrapper and the WebSockets write loop do support
  partial writes, so payload size alone does **not** establish a TLS-limit bug.
  The SDK's nominal maximum TLS content length is 16,384 bytes, larger than the
  originally observed image. Do not claim that a 7,747-byte JPEG exceeded it.

The firmware change replaces just the binary reply writer with bounded,
randomly masked RFC 6455 fragments on the same authenticated TLS connection.
It uses a 1,032-byte stack scratch buffer, retains the original data in PSRAM,
checks command validity between writes, handles accepted prefixes without
replay, and closes on incomplete output. No new library version or dependency
cooldown exception is involved. The relay protocol and size limits stay intact.

New serial logs report connection state and binary send progress. `sent` means
the local TLS writer accepted the complete message, not that Kelivo displayed
it. Snapshot logging now distinguishes a prepared reply from response-buffer
failure instead of unconditionally reporting preparation success.

This is a real transport implementation correction and a candidate fix for the
observed interruption. **No capture of the original on-wire traffic or live
device test proves that Railway rejected the fixed mask, or that this change
alone resolves the physical failure.** The next device test is still required.

Validation passed: both CoreS3 build environments (without private credentials),
99 native test cases, 629 Python tests, the 123-test MCP gate, ruff, pyright,
the repository security audit and high-severity firmware checks (no defects). Native tests
cover masking, continuation/FIN, boundary sizes through a 512 KiB recording,
short writes, failure and expiry without replay. A C++ fixture using the exact
production writer feeds real Uvicorn/WebSocket/MCP servers with 7,747-byte and
128 KiB synthetic images; both arrive byte-for-byte intact and the connection
continues to handle a face command. These host tests do not emulate ESP32 TLS,
radio conditions or Railway's proxy.

Firmware verification commands:

```sh
pio test -d firmware -e native
pio run -d firmware -e m5stack-cores3-public
pio check -d firmware -e m5stack-cores3-public --severity=high --fail-on-defect=high
uv run --locked pytest tests/test_railway.py -k firmware_fragmented
```

The public build is for compilation validation only. The user's upload must be
rebuilt locally using `m5stack-cores3` and their existing private `src/config.h`.
The source update consists of `outbound_service.cpp`, `outbound_ws_writer.h`
and `http_server.cpp`; it does not contain or overwrite credentials or face assets.

Reference: [RFC 6455 masking requirements](https://www.rfc-editor.org/rfc/rfc6455#section-5.3).

## Code references

- `firmware/src/camera_service.cpp`: capture, conversion and bus recovery.
- `firmware/src/http_server.cpp`: response copy and the serial success message.
- `firmware/src/outbound_service.cpp`: deferred metadata/binary transmission.
- `mcp_server/outbound_relay.py`: validation, disconnection and safe error codes.
- `mcp_server/stackchan_client.py`: single relay capture and safe error rendering.
