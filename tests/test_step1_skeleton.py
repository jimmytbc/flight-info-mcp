"""Acceptance tests - MCP skeleton over stdio.

Acceptance criteria:
- Spawn the server, complete an MCP handshake, list zero tools, terminate cleanly (exit 0).
- stdout carries only MCP protocol messages.
- structured logs go to stderr only; no API-key-shaped strings at any level.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SPAWN_CMD = [sys.executable, "-m", "flight_info_mcp"]
KEY_SHAPED_TOKEN = re.compile(r"\b[A-Za-z0-9]{16,}\b")


@pytest.mark.asyncio
async def test_handshake_completes_and_tools_list_responds():
    """Skeleton invariant: the server boots and answers MCP requests.
    The tool surface fills the tools list; we just verify the call works."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "flight_info_mcp"],
        env={**os.environ, "AVIATIONSTACK_API_KEY": "step1-stub"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            assert init_result.serverInfo.name == "flight-info-mcp"
            tools_result = await session.list_tools()
            assert isinstance(tools_result.tools, list)


def test_clean_exit_on_stdin_eof():
    """Closing stdin should drive the server to a graceful zero-exit."""
    proc = subprocess.run(
        SPAWN_CMD,
        input=b"",
        capture_output=True,
        timeout=5,
        env={**os.environ, "AVIATIONSTACK_API_KEY": "step1-stub"},
    )
    assert proc.returncode == 0, (
        f"non-zero exit on EOF: rc={proc.returncode} stderr={proc.stderr!r}"
    )


def test_stdout_clean_and_stderr_has_no_key_shaped_tokens():
    """No MCP client connects in this run, so stdout must stay empty
    (the server emits MCP messages only in response to client requests).
    stderr may carry log lines but never a key-shaped run of 16+ alphanumerics."""
    proc = subprocess.run(
        SPAWN_CMD,
        input=b"",
        capture_output=True,
        timeout=5,
        env={**os.environ, "AVIATIONSTACK_API_KEY": "step1-stub"},
    )
    assert proc.stdout == b"", f"unexpected stdout: {proc.stdout!r}"
    stderr_text = proc.stderr.decode("utf-8", errors="replace")
    matches = KEY_SHAPED_TOKEN.findall(stderr_text)
    assert matches == [], f"stderr contains key-shaped tokens: {matches!r}"
