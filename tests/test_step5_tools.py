"""Acceptance tests - tool surface wiring.

Acceptance criteria:
1. Each tool with valid input → expected response shape with both UTC and
   airport-local time fields populated where computable.
2. Each tool with an unrecognised input field → schema rejection (not silently
   accepted).
3. ``get_current_location`` for a flight known to Aviationstack but absent
   from OpenSky's current state vectors → DATA_UNAVAILABLE (not NO_MATCH,
   not a fabricated coordinate).
"""
from __future__ import annotations

import os
import sys

import pytest
import respx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from flight_info_mcp.clients.aviationstack import AviationstackClient
from flight_info_mcp.clients.opensky import OpenSkyClient
from flight_info_mcp.tools import (
    _FLIGHT_IATA_INPUT_SCHEMA,
    call_get_arrival_info,
    call_get_current_location,
    call_get_departure_info,
)


# pytest-asyncio is configured to ``asyncio_mode = "auto"`` in pyproject.toml,
# so ``async def`` tests run without an explicit mark.


AVI_URL = "https://api.aviationstack.com/v1/flights"
OS_URL = "https://opensky-network.org/api/states/all"


def _av_record(*, icao24: str | None = "abcd12") -> dict:
    return {
        "flight_date": "2026-05-03",
        "flight_status": "active",
        "departure": {
            "airport": "Heathrow",
            "iata": "LHR",
            "icao": "EGLL",
            "timezone": "Europe/London",
            "scheduled": "2026-05-03T07:00:00+00:00",
            "estimated": "2026-05-03T07:15:00+00:00",
            "actual": "2026-05-03T07:18:00+00:00",
        },
        "arrival": {
            "airport": "JFK",
            "iata": "JFK",
            "icao": "KJFK",
            "timezone": "America/New_York",
            "scheduled": "2026-05-03T14:00:00-04:00",
            "estimated": "2026-05-03T14:10:00-04:00",
            "actual": None,
        },
        "airline": {"iata": "BA", "name": "British Airways"},
        "flight": {"iata": "BA117", "icao": "BAW117", "number": "117"},
        "aircraft": ({"icao24": icao24} if icao24 else {}),
    }


def _airborne_state(icao24: str = "abcd12") -> list:
    return [
        icao24, "BAW117  ", "United Kingdom", 1685529600, 1685529600,
        -0.1276, 51.5074, 11000.0, False, 250.0, 90.0, 0.0,
        None, 11500.0, None, False, 0,
    ]


# --- 1: valid input → UTC + airport-local time fields ----------------------


@respx.mock
async def test_departure_info_returns_utc_and_local_times():
    respx.get(AVI_URL).respond(200, json={"data": [_av_record()]})

    async with AviationstackClient(api_key="k") as av:
        out = await call_get_departure_info({"flight_iata": "BA117"}, aviationstack=av)

    assert "flights" in out
    assert len(out["flights"]) == 1
    flight = out["flights"][0]
    sched = flight["departure_times"]["scheduled"]
    # Wire 07:00 with Europe/London (BST = UTC+1 in May): components 07:00
    # anchor to BST (BUG-03 re-anchor: wire offset is advisory only).
    assert sched == {
        "utc": "2026-05-03T06:00:00Z",
        "local": "2026-05-03T07:00:00+01:00",
    }
    # Pass-through preserved (no editing of upstream content).
    assert flight["departure"] == _av_record()["departure"]
    assert out["user_localised"] is False


@respx.mock
async def test_arrival_info_returns_utc_and_local_times():
    respx.get(AVI_URL).respond(200, json={"data": [_av_record()]})

    async with AviationstackClient(api_key="k") as av:
        out = await call_get_arrival_info({"flight_iata": "BA117"}, aviationstack=av)

    flight = out["flights"][0]
    sched = flight["arrival_times"]["scheduled"]
    # 14:00 EDT (-04:00) == 18:00 UTC; wire offset agrees with the IANA tz.
    assert sched == {
        "utc": "2026-05-03T18:00:00Z",
        "local": "2026-05-03T14:00:00-04:00",
    }
    # `actual: None` propagates as None enrichment
    assert flight["arrival_times"]["actual"] is None
    assert out["user_localised"] is False


@respx.mock
async def test_arrival_local_uses_destination_iana_tz_when_wire_is_utc():
    """BUG-02 regression: when the upstream wire timestamp carries +00:00
    but the destination is in another timezone, ``arrival_times.local`` must
    reflect the destination IANA tz, not the wire offset."""
    sin_record = {
        "flight_date": "2026-05-03",
        "flight_status": "active",
        "departure": {
            "airport": "Xiamen", "iata": "XMN", "timezone": "Asia/Shanghai",
            "scheduled": "2026-05-03T10:00:00+00:00",
        },
        "arrival": {
            "airport": "Singapore Changi", "iata": "SIN",
            "timezone": "Asia/Singapore",
            "scheduled": "2026-05-03T14:00:00+00:00",
            "estimated": "2026-05-03T14:00:00+00:00",
            "actual": None,
        },
        "airline": {"iata": "MF"},
        "flight": {"iata": "MF8675"},
        "aircraft": {"icao24": "abcd12"},
    }
    respx.get(AVI_URL).respond(200, json={"data": [sin_record]})

    async with AviationstackClient(api_key="k") as av:
        out = await call_get_arrival_info({"flight_iata": "MF8675"}, aviationstack=av)

    sched = out["flights"][0]["arrival_times"]["scheduled"]
    # BUG-03 re-anchor: wire components 14:00 anchored to SGT.
    assert sched["utc"] == "2026-05-03T06:00:00Z"
    assert sched["local"] == "2026-05-03T14:00:00+08:00"
    # Pass-through: the raw upstream string is unchanged.
    assert out["flights"][0]["arrival"]["scheduled"] == "2026-05-03T14:00:00+00:00"


@respx.mock
async def test_flight_iata_with_internal_space_is_normalised():
    """BUG-01 regression: ``TR 983`` must reach upstream as ``TR983``."""
    route = respx.get(AVI_URL).respond(200, json={"data": []})

    async with AviationstackClient(api_key="k") as av:
        await call_get_departure_info({"flight_iata": "TR 983"}, aviationstack=av)

    assert route.called
    request = route.calls.last.request
    assert "flight_iata=TR983" in str(request.url)
    assert "flight_iata=TR%20983" not in str(request.url)


@respx.mock
async def test_flight_iata_with_tab_is_normalised():
    """BUG-01: tab and other whitespace characters also collapsed."""
    route = respx.get(AVI_URL).respond(200, json={"data": []})

    async with AviationstackClient(api_key="k") as av:
        await call_get_departure_info({"flight_iata": "ZH\t227"}, aviationstack=av)

    request = route.calls.last.request
    assert "flight_iata=ZH227" in str(request.url)


@respx.mock
async def test_dep_iata_with_internal_space_is_normalised():
    """BUG-01: same normalisation applied to dep_iata."""
    route = respx.get(AVI_URL).respond(200, json={"data": []})

    async with AviationstackClient(api_key="k") as av:
        await call_get_departure_info(
            {"flight_iata": "BA117", "dep_iata": "L HR"}, aviationstack=av
        )

    request = route.calls.last.request
    assert "dep_iata=LHR" in str(request.url)


@respx.mock
async def test_arr_iata_with_internal_space_is_normalised():
    """BUG-01: same normalisation applied to arr_iata."""
    route = respx.get(AVI_URL).respond(200, json={"data": []})

    async with AviationstackClient(api_key="k") as av:
        await call_get_arrival_info(
            {"flight_iata": "BA117", "arr_iata": "J FK"}, aviationstack=av
        )

    request = route.calls.last.request
    assert "arr_iata=JFK" in str(request.url)


@respx.mock
async def test_outer_whitespace_still_stripped():
    """Pre-existing behaviour kept: leading/trailing whitespace collapsed too."""
    route = respx.get(AVI_URL).respond(200, json={"data": []})

    async with AviationstackClient(api_key="k") as av:
        await call_get_departure_info({"flight_iata": "  BA117  "}, aviationstack=av)

    request = route.calls.last.request
    assert "flight_iata=BA117" in str(request.url)


@respx.mock
async def test_enrichment_falls_back_when_record_lacks_timezone():
    """Defensive: if upstream record has no ``timezone`` field, enrichment
    falls back to the wire value (no crash, no fabricated tz)."""
    record = {
        "departure": {
            "iata": "LHR",
            "scheduled": "2026-05-03T07:00:00+00:00",
        },
        "arrival": {
            "iata": "JFK",
            "scheduled": "2026-05-03T14:00:00-04:00",
        },
        "flight": {"iata": "BA117"},
    }
    respx.get(AVI_URL).respond(200, json={"data": [record]})

    async with AviationstackClient(api_key="k") as av:
        out = await call_get_departure_info({"flight_iata": "BA117"}, aviationstack=av)

    sched = out["flights"][0]["departure_times"]["scheduled"]
    assert sched["utc"] == "2026-05-03T07:00:00Z"
    assert sched["local"] == "2026-05-03T07:00:00+00:00"


@respx.mock
async def test_current_location_returns_position_with_utc_as_of():
    respx.get(AVI_URL).respond(200, json={"data": [_av_record(icao24="abcd12")]})
    respx.get(OS_URL).respond(
        200, json={"time": 1685529600, "states": [_airborne_state()]}
    )

    async with AviationstackClient(api_key="k") as av, OpenSkyClient() as os_client:
        out = await call_get_current_location(
            {"flight_iata": "BA117"}, aviationstack=av, opensky=os_client
        )

    assert out["icao24"] == "abcd12"
    assert out["latitude"] == 51.5074
    assert out["longitude"] == -0.1276
    assert out["on_ground"] is False
    assert out["callsign"] == "BAW117"
    assert out["as_of"]["utc"].endswith("Z")
    # In-flight: no airport context, so airport-local is intentionally None
    assert out["as_of"]["local"] is None
    assert out["user_localised"] is False


# --- 2: unrecognised field rejection ---------------------------------------


def test_input_schema_advertises_no_additional_properties():
    """All three tools share the same schema; verify it forbids extras."""
    assert _FLIGHT_IATA_INPUT_SCHEMA["additionalProperties"] is False
    assert _FLIGHT_IATA_INPUT_SCHEMA["required"] == ["flight_iata"]


@pytest.mark.parametrize(
    "tool_name",
    ["get_departure_info", "get_arrival_info", "get_current_location"],
)
async def test_tool_rejects_unrecognised_input_field_at_mcp_wire(tool_name):
    """SDK validates inputSchema before dispatch; extras → CallToolResult.isError=True."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "flight_info_mcp"],
        env={**os.environ, "AVIATIONSTACK_API_KEY": "step5-stub"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                tool_name, {"flight_iata": "BA117", "rogue_field": "x"}
            )
            assert result.isError is True, (
                f"expected isError=True for unrecognised field on {tool_name}, "
                f"got isError={result.isError}, content={result.content}"
            )


# --- 3: known to Aviationstack, absent in OpenSky → DATA_UNAVAILABLE -------


@respx.mock
async def test_current_location_known_to_aviationstack_absent_in_opensky():
    respx.get(AVI_URL).respond(200, json={"data": [_av_record(icao24="abcd12")]})
    # OpenSky has no live state for this aircraft
    respx.get(OS_URL).respond(200, json={"time": 1685529600, "states": None})

    async with AviationstackClient(api_key="k") as av, OpenSkyClient() as os_client:
        out = await call_get_current_location(
            {"flight_iata": "BA117"}, aviationstack=av, opensky=os_client
        )

    assert "error" in out
    assert out["error"]["code"] == "DATA_UNAVAILABLE"
    assert out["error"]["provider"] == "opensky"
    # Coordinates were not fabricated
    assert "latitude" not in out
    assert "longitude" not in out


@respx.mock
async def test_current_location_aviationstack_no_match_returns_no_match():
    respx.get(AVI_URL).respond(200, json={"data": []})

    async with AviationstackClient(api_key="k") as av, OpenSkyClient() as os_client:
        out = await call_get_current_location(
            {"flight_iata": "BA999"}, aviationstack=av, opensky=os_client
        )

    assert out["error"]["code"] == "NO_MATCH"
    assert out["error"]["provider"] == "aviationstack"


@respx.mock
async def test_current_location_grounded_passes_through_data_unavailable():
    respx.get(AVI_URL).respond(200, json={"data": [_av_record(icao24="abcd12")]})
    grounded = _airborne_state()
    grounded[8] = True
    respx.get(OS_URL).respond(200, json={"time": 1685529600, "states": [grounded]})

    async with AviationstackClient(api_key="k") as av, OpenSkyClient() as os_client:
        out = await call_get_current_location(
            {"flight_iata": "BA117"}, aviationstack=av, opensky=os_client
        )

    assert out["error"]["code"] == "DATA_UNAVAILABLE"
    assert out["error"]["provider"] == "opensky"


@respx.mock
async def test_current_location_aviationstack_record_lacks_icao24():
    """Aviationstack found the flight but its record has no aircraft.icao24
    (free-tier coverage gap). Should return DATA_UNAVAILABLE, not NO_MATCH."""
    respx.get(AVI_URL).respond(200, json={"data": [_av_record(icao24=None)]})

    async with AviationstackClient(api_key="k") as av, OpenSkyClient() as os_client:
        out = await call_get_current_location(
            {"flight_iata": "BA117"}, aviationstack=av, opensky=os_client
        )

    assert out["error"]["code"] == "DATA_UNAVAILABLE"
    assert out["error"]["provider"] == "opensky"


# --- additional: tools advertised correctly via MCP handshake --------------


async def test_three_tools_registered_via_handshake():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "flight_info_mcp"],
        env={**os.environ, "AVIATIONSTACK_API_KEY": "step5-stub"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()

    names = {tool.name for tool in tools.tools}
    assert names == {"get_departure_info", "get_arrival_info", "get_current_location"}
    for tool in tools.tools:
        assert tool.inputSchema.get("additionalProperties") is False
        assert tool.inputSchema.get("required") == ["flight_iata"]
