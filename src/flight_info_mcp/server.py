"""MCP server entry point.

Stdio transport with three tools registered via :func:`register_tools`. The clients share
a single ``httpx.AsyncClient`` whose lifetime spans ``run()``.
"""
from __future__ import annotations

import logging

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


async def run(config: Config) -> None:
    log.info(
        "flight-info-mcp starting transport=stdio tools=3 opensky_auth=%s",
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
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)
    log.info("flight-info-mcp stopped")
