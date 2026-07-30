"""MCP server entry point.

Supports stdio (default) and streamable HTTP transport; selected via
MCP_TRANSPORT env var. The clients share a single ``httpx.AsyncClient`` whose
lifetime spans ``run()``.
"""
from __future__ import annotations

import logging
import os

import httpx
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

from . import SERVER_NAME, SERVER_VERSION
from .clients.aviationstack import AviationstackClient
from .clients.opensky import OpenSkyClient
from .config import Config
from .tools import register_tools

log = logging.getLogger(__name__)


def build_server(
    *,
    aviationstack: AviationstackClient,
    opensky: OpenSkyClient,
) -> Server:
    server = Server(SERVER_NAME, version=SERVER_VERSION)
    register_tools(server, aviationstack=aviationstack, opensky=opensky)
    return server


async def _run_streamable_http(server: Server) -> None:
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8002"))

    # stateless=True: fresh transport per request, no session tracking —
    # required by this server's no-session invariant (CLAUDE.md #4).
    session_manager = StreamableHTTPSessionManager(
        app=server,
        event_store=None,
        json_response=False,
        stateless=True,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    app = Starlette(
        routes=[Mount("/mcp", app=session_manager.handle_request)],
        lifespan=lifespan,
    )

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    uv_server = uvicorn.Server(config)
    log.info(
        "flight-info-mcp streamable HTTP server listening on %s:%d at /mcp",
        host,
        port,
    )
    await uv_server.serve()


async def run(config: Config) -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    log.info(
        "flight-info-mcp starting transport=%s tools=3 opensky_auth=%s",
        transport,
        config.opensky_authenticated,
    )
    async with httpx.AsyncClient(timeout=10.0) as http:
        aviationstack = AviationstackClient(api_key=config.aviationstack_api_key, http=http)
        opensky = OpenSkyClient(
            username=config.opensky_username,
            password=config.opensky_password,
            http=http,
        )
        server = build_server(aviationstack=aviationstack, opensky=opensky)
        init_options = InitializationOptions(
            server_name=SERVER_NAME,
            server_version=SERVER_VERSION,
            capabilities=server.get_capabilities(
                notification_options=NotificationOptions(),
                experimental_capabilities={},
            ),
        )
        if transport == "streamable-http":
            await _run_streamable_http(server)
        elif transport == "stdio":
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, init_options)
        else:
            raise ValueError(
                f"Unsupported MCP_TRANSPORT={transport!r}: use 'stdio' or "
                "'streamable-http' (legacy 'sse' transport removed)"
            )
    log.info("flight-info-mcp stopped")
