"""Acceptance tests - process-start configuration loader.

Acceptance criteria:
1. Spawn with AVIATIONSTACK_API_KEY unset → exit code is non-zero within
   one second of spawn; stderr names the missing variable; stdout is empty.
2. Spawn with AVIATIONSTACK_API_KEY set to a non-empty value → server
   completes startup; the value never appears in stderr at any log level.
3. Spawn with OPENSKY_USERNAME set and OPENSKY_PASSWORD unset → server
   completes startup with a stderr warning that OpenSky will operate in
   unauthenticated mode (no credential value in the warning).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from flight_info_mcp.config import Config, ConfigError, load


SPAWN_CMD = [sys.executable, "-m", "flight_info_mcp"]


def _baseline_env() -> dict[str, str]:
    """A minimal env that strips any inherited credentials so each test starts clean."""
    blocked = {
        "AVIATIONSTACK_API_KEY",
        "OPENSKY_USERNAME",
        "OPENSKY_PASSWORD",
    }
    return {k: v for k, v in os.environ.items() if k not in blocked}


# --- unit tests for the load() function ---------------------------------------------------------


def test_load_raises_when_required_var_missing():
    with pytest.raises(ConfigError) as exc:
        load(env={})
    assert "AVIATIONSTACK_API_KEY" in str(exc.value)


def test_load_raises_when_required_var_blank():
    with pytest.raises(ConfigError):
        load(env={"AVIATIONSTACK_API_KEY": "   "})


def test_load_returns_config_with_only_required_var():
    cfg = load(env={"AVIATIONSTACK_API_KEY": "abc"})
    assert isinstance(cfg, Config)
    assert cfg.aviationstack_api_key == "abc"
    assert cfg.opensky_username is None
    assert cfg.opensky_password is None
    assert cfg.opensky_authenticated is False


def test_load_returns_authenticated_when_both_opensky_vars_set():
    cfg = load(
        env={
            "AVIATIONSTACK_API_KEY": "abc",
            "OPENSKY_USERNAME": "u",
            "OPENSKY_PASSWORD": "p",
        }
    )
    assert cfg.opensky_authenticated is True


def test_load_drops_partial_opensky_pair():
    cfg = load(
        env={
            "AVIATIONSTACK_API_KEY": "abc",
            "OPENSKY_USERNAME": "u",
            # no password
        }
    )
    assert cfg.opensky_authenticated is False
    assert cfg.opensky_username is None  # discarded when not paired


# --- integration tests against the spawned process -------------------------------------------


def test_missing_required_key_exits_nonzero_within_one_second_and_names_the_var(tmp_path):
    """Spawn from a clean cwd so the server's ``load_dotenv()`` can't pick up
    the operator's local ``.env`` and silently succeed."""
    env = _baseline_env()
    started = time.monotonic()
    proc = subprocess.run(
        SPAWN_CMD,
        input=b"",
        capture_output=True,
        timeout=5,
        env=env,
        cwd=str(tmp_path),
    )
    elapsed = time.monotonic() - started

    assert proc.returncode != 0, f"expected non-zero exit, got {proc.returncode}"
    assert elapsed < 1.0, f"fail-fast exceeded 1s: elapsed={elapsed:.3f}s"
    assert proc.stdout == b"", f"stdout must be empty on fail-fast: {proc.stdout!r}"
    stderr_text = proc.stderr.decode("utf-8", errors="replace")
    assert "AVIATIONSTACK_API_KEY" in stderr_text, (
        f"stderr must name the missing variable: {stderr_text!r}"
    )


@pytest.mark.asyncio
async def test_required_key_value_never_appears_in_stderr():
    distinct_key = "SECRETkey-aviationstack-do-not-leak-1234"
    env = _baseline_env()
    env["AVIATIONSTACK_API_KEY"] = distinct_key

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "flight_info_mcp"], env=env
        )
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init_result = await session.initialize()
                assert init_result.serverInfo.name == "flight-info-mcp"

        errlog.seek(0)
        captured_stderr = errlog.read()

    assert distinct_key not in captured_stderr, (
        f"AVIATIONSTACK_API_KEY value leaked to stderr: {captured_stderr!r}"
    )


@pytest.mark.asyncio
async def test_partial_opensky_credentials_warn_without_echoing_value():
    distinct_username = "SECRETopenskyuser-do-not-leak-7890"
    env = _baseline_env()
    env["AVIATIONSTACK_API_KEY"] = "stub-aviationstack"
    env["OPENSKY_USERNAME"] = distinct_username
    # OPENSKY_PASSWORD intentionally unset

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "flight_info_mcp"], env=env
        )
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

        errlog.seek(0)
        captured_stderr = errlog.read()

    assert "unauthenticated" in captured_stderr.lower(), (
        f"expected 'unauthenticated' warning in stderr: {captured_stderr!r}"
    )
    assert distinct_username not in captured_stderr, (
        f"OPENSKY_USERNAME value leaked to stderr: {captured_stderr!r}"
    )
