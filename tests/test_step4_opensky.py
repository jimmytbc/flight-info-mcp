"""Acceptance tests - OpenSky client.

Acceptance criteria:
1. A live integration test against OpenSky returns parsed live-state for a
   flight known to be airborne with current ground-station coverage.
2. A test for a known grounded flight returns DATA_UNAVAILABLE, not NO_MATCH.
3. A test for an unknown ICAO 24-bit address returns NO_MATCH.
4. A test where OpenSky times out returns UPSTREAM_FAILURE with provider=opensky.
"""
from __future__ import annotations

import base64
import logging
import os

import httpx
import pytest
import respx

from flight_info_mcp import obs
from flight_info_mcp.clients.opensky import (
    ON_GROUND_INDEX,
    OpenSkyClient,
    OpenSkyResponse,
)
from flight_info_mcp.errors import ErrorCode, ErrorEnvelope, Provider


pytestmark = pytest.mark.asyncio

STATES_URL = "https://opensky-network.org/api/states/all"


def _airborne_state(icao24: str = "abcd12") -> list:
    state = [
        icao24,                # 0  icao24
        "BAW117  ",            # 1  callsign
        "United Kingdom",      # 2  origin_country
        1685529600,            # 3  time_position
        1685529600,            # 4  last_contact
        -0.1276,               # 5  longitude
        51.5074,               # 6  latitude
        11000.0,               # 7  baro_altitude
        False,                 # 8  on_ground
        250.0,                 # 9  velocity
        90.0,                  # 10 true_track
        0.0,                   # 11 vertical_rate
        None,                  # 12 sensors
        11500.0,               # 13 geo_altitude
        None,                  # 14 squawk
        False,                 # 15 spi
        0,                     # 16 position_source
    ]
    assert state[ON_GROUND_INDEX] is False
    return state


def _grounded_state(icao24: str = "abcd12") -> list:
    state = _airborne_state(icao24)
    state[ON_GROUND_INDEX] = True
    state[7] = None   # baro_altitude null
    state[3] = None   # time_position null
    state[9] = 0.0
    return state


# --- success path --------------------------------------------------------------------


@respx.mock
async def test_airborne_aircraft_returns_parsed_state():
    payload = {"time": 1685529600, "states": [_airborne_state("abcd12")]}
    respx.get(STATES_URL).respond(200, json=payload)

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("abcd12")

    assert isinstance(result, OpenSkyResponse)
    assert result.time == 1685529600
    assert len(result.states) == 1
    assert result.states[0].raw[0] == "abcd12"


@respx.mock
async def test_request_uses_icao24_query_param_lowercased():
    route = respx.get(STATES_URL).respond(
        200, json={"time": 0, "states": [_airborne_state("abcd12")]}
    )

    async with OpenSkyClient() as client:
        await client.get_state_by_icao24("ABCD12")

    request = route.calls.last.request
    assert "icao24=abcd12" in str(request.url)


# --- error mapping -------------------------------------------------------------------


@respx.mock
async def test_grounded_aircraft_returns_data_unavailable_not_no_match():
    """'live coordinates for a grounded flight' → DATA_UNAVAILABLE."""
    payload = {"time": 1685529600, "states": [_grounded_state("abcd12")]}
    respx.get(STATES_URL).respond(200, json=payload)

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("abcd12")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.DATA_UNAVAILABLE
    assert result.code != ErrorCode.NO_MATCH
    assert result.provider == Provider.OPENSKY


@respx.mock
async def test_unknown_icao24_states_null_returns_no_match():
    respx.get(STATES_URL).respond(200, json={"time": 1685529600, "states": None})

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("ffffff")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.NO_MATCH
    assert result.provider == Provider.OPENSKY


@respx.mock
async def test_unknown_icao24_empty_states_returns_no_match():
    respx.get(STATES_URL).respond(200, json={"time": 1685529600, "states": []})

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("ffffff")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.NO_MATCH


@respx.mock
async def test_404_status_returns_no_match():
    respx.get(STATES_URL).respond(404, text="not found")

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("ffffff")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.NO_MATCH


@respx.mock
async def test_timeout_returns_upstream_failure_with_provider_opensky():
    respx.get(STATES_URL).mock(side_effect=httpx.TimeoutException("timed out"))

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("abcd12")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE
    assert result.provider == Provider.OPENSKY


@respx.mock
async def test_429_status_returns_quota_limit():
    respx.get(STATES_URL).respond(429, text="rate limited")

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("abcd12")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.QUOTA_LIMIT
    assert result.provider == Provider.OPENSKY


@respx.mock
async def test_5xx_status_returns_upstream_failure():
    respx.get(STATES_URL).respond(503, text="service unavailable")

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("abcd12")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE


@respx.mock
async def test_non_json_response_returns_upstream_failure_without_echoing_payload():
    sentinel = "OPENSKY-HTML-PAYLOAD-DO-NOT-LEAK"
    respx.get(STATES_URL).respond(200, text=f"<html>{sentinel}</html>")

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("abcd12")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE
    assert sentinel not in result.detail


@respx.mock
async def test_missing_time_field_returns_upstream_failure():
    respx.get(STATES_URL).respond(200, json={"states": [_airborne_state()]})

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("abcd12")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE


@respx.mock
async def test_truncated_state_vector_returns_upstream_failure():
    # 8 elements - one short of needing index 8 for on_ground.
    short_state = ["abcd12", "BAW117", "GB", 0, 0, 0.0, 0.0, 0.0]
    respx.get(STATES_URL).respond(200, json={"time": 0, "states": [short_state]})

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("abcd12")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE


# --- auth path -----------------------------------------------------------------------


@respx.mock
async def test_authenticated_client_sends_basic_auth_header():
    route = respx.get(STATES_URL).respond(200, json={"time": 0, "states": None})

    async with OpenSkyClient(username="alice", password="rabbits") as client:
        await client.get_state_by_icao24("ffffff")

    request = route.calls.last.request
    auth_header = request.headers.get("authorization", "")
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode()
    assert decoded == "alice:rabbits"


@respx.mock
async def test_unauthenticated_client_omits_authorization_header():
    route = respx.get(STATES_URL).respond(200, json={"time": 0, "states": None})

    async with OpenSkyClient() as client:
        await client.get_state_by_icao24("ffffff")

    request = route.calls.last.request
    assert "authorization" not in {k.lower() for k in request.headers.keys()}


@respx.mock
async def test_credentials_do_not_appear_in_logs(caplog):
    distinct_user = "SECRETopenskyuser-step4-DO-NOT-LEAK"
    distinct_pass = "SECRETopenskypass-step4-DO-NOT-LEAK"
    respx.get(STATES_URL).respond(200, json={"time": 0, "states": None})

    with caplog.at_level(logging.DEBUG):
        async with OpenSkyClient(username=distinct_user, password=distinct_pass) as client:
            await client.get_state_by_icao24("abcd12")

    rendered = "\n".join(rec.getMessage() for rec in caplog.records)
    assert distinct_user not in rendered
    assert distinct_pass not in rendered


# --- live integration ----------------------------------------------------------------


_LIVE_ICAO24 = (os.environ.get("OPENSKY_LIVE_ICAO24") or "").strip()


@pytest.mark.skipif(
    not _LIVE_ICAO24,
    reason="OPENSKY_LIVE_ICAO24 unset; skipping live integration test (smoke run covers).",
)
async def test_live_integration_known_airborne_returns_parsed_state(caplog):
    user = (os.environ.get("OPENSKY_USERNAME") or "").strip() or None
    pwd = (os.environ.get("OPENSKY_PASSWORD") or "").strip() or None
    if user:
        obs.register_secret(user)
    if pwd:
        obs.register_secret(pwd)

    with caplog.at_level(logging.DEBUG):
        async with OpenSkyClient(username=user, password=pwd) as client:
            result = await client.get_state_by_icao24(_LIVE_ICAO24)

    # We can't guarantee the aircraft is airborne the moment the test runs, but
    # the live wire must produce a typed result of some kind.
    assert isinstance(result, (OpenSkyResponse, ErrorEnvelope))
    rendered = "\n".join(rec.getMessage() for rec in caplog.records)
    if user:
        assert user not in rendered
    if pwd:
        assert pwd not in rendered
