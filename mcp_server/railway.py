"""Single-replica cloud entrypoint: public MCP/WSS, loopback-only relay controls."""

import asyncio
import hmac
import os
import signal
import socket
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from functools import wraps
from urllib.parse import urlsplit

import uvicorn
from mcp.server.fastmcp import FastMCP, Image
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute

from .mcp_tools import register_tools
from .outbound_relay import HEADER_SIZE, MAX_AUDIO_REPLY, RelaySettings
from .outbound_relay import create_app as create_relay_app
from .server import BearerAuthMiddleware, ensure_http_auth_configured
from .stackchan_client import StackchanClient
from .stackchan_config import load_config


class ManagedServer(uvicorn.Server):
    def capture_signals(self):
        # Both listeners share one loop and one explicit shutdown handler.
        return nullcontext()


class ThreadedTools:
    """Keep blocking device/TTS calls off the relay's event loop.

    Preserve serial tool execution while allowing heartbeats and the private
    HTTP server to progress. The pinned FastMCP runs sync functions inline.
    """

    def __init__(self, mcp: FastMCP):
        self.mcp = mcp
        self.lock = asyncio.Lock()

    def tool(self, **options):
        def register(function):
            @wraps(function)
            async def threaded(*args, **kwargs):
                async with self.lock:
                    return await run_in_threadpool(function, *args, **kwargs)

            return self.mcp.tool(**options)(threaded)
        return register


def transport_security() -> TransportSecuritySettings:
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    for raw in (os.environ.get("RAILWAY_PUBLIC_DOMAIN", ""),
                os.environ.get("STACKCHAN_PUBLIC_HOST", "")):
        domain = raw.strip().lower()
        if not domain:
            continue
        parsed = urlsplit(f"https://{domain}")
        if (parsed.hostname != domain or parsed.path or parsed.query or parsed.fragment
                or parsed.username or parsed.password or "*" in domain
                or any(c.isspace() for c in domain)):
            raise ValueError("Public host must be a hostname without scheme, port, or path")
        hosts.extend([domain, f"{domain}:443"])
        origins.append(f"https://{domain}")
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)


async def serve() -> None:
    config = load_config()
    if config.transport != "relay":
        raise ValueError("Railway requires STACKCHAN_TRANSPORT=relay")
    ensure_http_auth_configured(config)
    settings = RelaySettings.from_env()
    token = config.mcp_auth_token
    if (len(token) < 24 or not token.isascii() or any(c.isspace() for c in token)
            or any(hmac.compare_digest(token, other)
                   for other in (settings.device_token, settings.control_token))):
        raise ValueError("MCP token must be at least 24 ASCII characters and distinct from relay tokens")
    if not hmac.compare_digest(config.relay_token, settings.control_token):
        raise ValueError("MCP and relay control token configuration must match")
    port = int(os.environ.get("PORT", "8080"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")

    # Reserve a private port; callers and the relay always use the same socket.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        relay_port = listener.getsockname()[1]
        config = replace(config, relay_url=f"http://127.0.0.1:{relay_port}")
        private_app = create_relay_app(settings)
        relay = private_app.state.relay
        private = ManagedServer(uvicorn.Config(
            private_app, host="127.0.0.1", port=relay_port, ws="none",
            access_log=False, proxy_headers=False, timeout_graceful_shutdown=5,
        ))
        mcp = FastMCP("stackchan", transport_security=transport_security())
        register_tools(ThreadedTools(mcp), StackchanClient(config), config, Image)
        mcp_app = mcp.streamable_http_app()

        @asynccontextmanager
        async def lifespan(app: Starlette):
            # Mounted ASGI apps do not automatically run their own lifespan.
            async with mcp.session_manager.run():
                yield

        async def health(request: Request) -> JSONResponse:
            # Device offline is normal before provisioning, not a restart reason.
            ready = private.started and not private.should_exit and not private_task.done()
            return JSONResponse({"ok": ready}, status_code=200 if ready else 503)

        public_app = Starlette(lifespan=lifespan, routes=[
            Route("/healthz", health, methods=["GET"]),
            WebSocketRoute("/stackchan/ws", relay.device),
            Route("/mcp", BearerAuthMiddleware(mcp_app, token=token)),
        ])
        public_app.router.redirect_slashes = False
        public = ManagedServer(uvicorn.Config(
            public_app, host="0.0.0.0", port=port, workers=1,  # noqa: S104 - Railway ingress.
            ws="websockets", ws_max_size=MAX_AUDIO_REPLY + HEADER_SIZE,
            ws_max_queue=2, ws_per_message_deflate=False, access_log=False,
            proxy_headers=False, timeout_graceful_shutdown=5,
        ))
        stopping = False

        def stop() -> None:
            nonlocal stopping
            stopping = True
            public.should_exit = private.should_exit = True

        loop = asyncio.get_running_loop()
        signals = (signal.SIGINT, signal.SIGTERM)
        for sig in signals:
            loop.add_signal_handler(sig, stop)
        private_task = asyncio.create_task(private.serve(sockets=[listener]))
        public_task = asyncio.create_task(public.serve())
        tasks = (private_task, public_task)
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            public.should_exit = private.should_exit = True
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                for sig in signals:
                    loop.remove_signal_handler(sig)
        if not stopping:
            raise RuntimeError("A required server stopped unexpectedly")


if __name__ == "__main__":
    asyncio.run(serve())
