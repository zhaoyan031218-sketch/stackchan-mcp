# Railway deployment validation — 2026-11-17

## Blocking synchronous MCP tools

The first combined MCP/relay process could initialize and list tools, but a
simulated `stackchan_face` request timed out even with a connected WebSocket.
The relay and tool logs showed the command handler running without the private
HTTP server progressing. The pinned MCP SDK's
`FuncMetadata.call_fn_with_arg_validation` calls synchronous functions inline.
Since the tool uses blocking `requests`, sharing that event loop with its
HTTP destination deadlocked until the request timeout.

The Railway entrypoint now registers async wrappers around the existing tools,
preserving their signatures and serial execution with an async lock. Work runs
through Starlette's thread pool; the event loop remains available for relay
HTTP, WebSockets and health probes. The existing local entrypoint and robot
protocol were not changed. Tests check a real MCP tool call through the
loopback control API to a simulated WebSocket body, plus health while the
command awaits its response and a second connection after reconnect.

## Non-root container permissions

The image built successfully but exited with `PermissionError` reading
`/app/mcp_server/__init__.py`. This cloud checkout carries restrictive file
modes that Docker COPY preserved. The image now grants read/traverse permission
to the allowlisted application files after installation, retaining root
ownership and running the service as UID 10001. No writable application code
directory or root runtime is required.

## Build proxy trust

The managed development machine's proxy CA was absent from the base image,
causing uv to report `UnknownIssuer` when downloading locked Python wheels.
For local validation only, a temporary Dockerfile mounted the machine's combined
CA bundle as a BuildKit secret and used `SSL_CERT_FILE` for the two uv install
steps. TLS verification and lockfile hashes remained enabled. No session CA was
committed or retained in image layers. Railway uses the committed Dockerfile
with the standard certificate store; its remote build still needs validation
after account access is available.

## Verification

- `uv run --locked ruff check .`: passed.
- `uv run --locked pyright`: passed, zero errors or warnings.
- `make lint-firmware`: passed, cppcheck reported no high-severity defects.
- `uv run --locked python scripts/ci_security_audit.py`: passed.
- `uv run --locked pytest --cov=mcp_server --cov=scripts --cov-report=term-missing`:
  538 passed, eight dependency deprecation warnings. The entrypoint subprocess
  is tested behaviorally; it is not instrumented by the parent coverage run.
- Container build with the session-only CA mount: passed.
- Actual container: `/healthz`, MCP initialize/ping/tools-list, role-separated
  authentication, hidden control routes, simulated face commands and reconnect,
  responsive health during a pending command, ffmpeg availability, UID 10001,
  no copied `.env`/firmware configuration, and SIGTERM exit code 0 all passed.
- No real robot actions or paid TTS requests were performed.

See [Railway deployment](railway-deployment.md) for configuration and operational
limits, and [outbound channel](outbound-channel.md) for the existing wire protocol.
