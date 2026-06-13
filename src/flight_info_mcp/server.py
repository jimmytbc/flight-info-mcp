"""MCP server entry point.

Supports stdio (default) and SSE transport; selected via MCP_TRANSPORT env var.
The clients share a single ``httpx.AsyncClient`` whose lifetime spans ``run()``.
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
    server = Server(SERVER_NAME)
    register_tools(server, aviationstack=aviationstack, opensky=opensky)
    return server


async def _run_sse(server: Server, init_options: InitializationOptions) -> None:
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Mount, Route

    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8002"))

    sse = SseServerTransport("/messages/")

    async def handle_sse(scope, receive, send):
        async with sse.connect_sse(scope, receive, send) as streams:
            await server.run(streams[0], streams[1], init_options)
        return Response()

    async def sse_endpoint(request: Request) -> Response:
        return await handle_sse(request.scope, request.receive, request._send)

    app = Starlette(routes=[
        Route("/sse", endpoint=sse_endpoint, methods=["GET"]),
        Mount("/messages/", app=sse.handle_post_message),
    ])

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    uv_server = uvicorn.Server(config)
    log.info("flight-info-mcp SSE server listening on %s:%d", host, port)
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
        if transport == "sse":
            await _run_sse(server, init_options)
        else:
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, init_options)
    log.info("flight-info-mcp stopped")
