"""MCP tool surface.

Three tools, fixed routing by query type:
- ``get_departure_info``  → Aviationstack
- ``get_arrival_info``    → Aviationstack
- ``get_current_location`` → OpenSky (with Aviationstack used as a directory
  lookup to translate flight IATA → 24-bit ICAO transponder address)

Input validation is delegated to the MCP SDK, which validates each tool call
against ``Tool.inputSchema`` before dispatch (see
``mcp.server.lowlevel.Server.call_tool``). Each schema sets
``additionalProperties: False`` so unrecognised fields are rejected, not
silently accepted.

Time fields: every Aviationstack ``scheduled``/``estimated``/``actual`` ISO
string gets a ``{utc, local}`` enrichment block. The user-localised flag is
always ``False`` at the server boundary.

Note on input-validation error semantics: the structured error envelope
(NO_MATCH / DATA_UNAVAILABLE / QUOTA_LIMIT / UPSTREAM_FAILURE) covers
upstream-related failures. Input validation rejects calls *before* any
upstream interaction, so it surfaces via MCP's ``CallToolResult.isError=true``
channel rather than as a structured error envelope.
"""
from __future__ import annotations

from typing import Any

from mcp.server import Server
from mcp.types import Tool

from .clients.aviationstack import AviationstackClient
from .clients.opensky import OpenSkyClient, OpenSkyResponse
from .errors import (
    ErrorCode,
    ErrorEnvelope,
    Provider,
    envelope_to_dict,
    make_error,
)
from .time_utils import enrich_iso_time, epoch_to_utc

USER_LOCALISED_DEFAULT = False


_FLIGHT_IATA_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "flight_iata": {
            "type": "string",
            "minLength": 1,
            "description": "IATA flight number, e.g. 'BA117'.",
        },
    },
    "required": ["flight_iata"],
    "additionalProperties": False,
}


_DEPARTURE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "flight_iata": {
            "type": "string",
            "minLength": 1,
            "description": "IATA flight number, e.g. 'BA117'.",
        },
        "dep_iata": {
            "type": "string",
            "minLength": 3,
            "maxLength": 3,
            "description": (
                "Optional departure airport IATA code (3 letters). "
                "Narrows results when the flight number alone is ambiguous."
            ),
        },
    },
    "required": ["flight_iata"],
    "additionalProperties": False,
}


_ARRIVAL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "flight_iata": {
            "type": "string",
            "minLength": 1,
            "description": "IATA flight number, e.g. 'BA117'.",
        },
        "arr_iata": {
            "type": "string",
            "minLength": 3,
            "maxLength": 3,
            "description": (
                "Optional arrival airport IATA code (3 letters). "
                "Narrows results when the flight number alone is ambiguous."
            ),
        },
    },
    "required": ["flight_iata"],
    "additionalProperties": False,
}


# --- dispatch ---------------------------------------------------------------


async def call_get_departure_info(
    arguments: dict[str, Any], *, aviationstack: AviationstackClient
) -> dict[str, Any]:
    filters = _flight_filters(arguments, airport_field="dep_iata")
    result = await aviationstack.query_flights(**filters)
    if isinstance(result, ErrorEnvelope):
        return envelope_to_dict(result)
    return {
        "flights": [_enrich_record(rec, leg="departure") for rec in result.flights],
        "user_localised": USER_LOCALISED_DEFAULT,
    }


async def call_get_arrival_info(
    arguments: dict[str, Any], *, aviationstack: AviationstackClient
) -> dict[str, Any]:
    filters = _flight_filters(arguments, airport_field="arr_iata")
    result = await aviationstack.query_flights(**filters)
    if isinstance(result, ErrorEnvelope):
        return envelope_to_dict(result)
    return {
        "flights": [_enrich_record(rec, leg="arrival") for rec in result.flights],
        "user_localised": USER_LOCALISED_DEFAULT,
    }


async def call_get_current_location(
    arguments: dict[str, Any],
    *,
    aviationstack: AviationstackClient,
    opensky: OpenSkyClient,
) -> dict[str, Any]:
    flight_iata = _require_flight_iata(arguments)

    av = await aviationstack.query_flights(flight_iata=flight_iata)
    if isinstance(av, ErrorEnvelope):
        return envelope_to_dict(av)
    if not av.flights:
        return envelope_to_dict(
            make_error(ErrorCode.NO_MATCH, Provider.AVIATIONSTACK, "no flights match")
        )

    icao24 = _first_icao24(av.flights)
    if not icao24:
        return envelope_to_dict(
            make_error(
                ErrorCode.DATA_UNAVAILABLE,
                Provider.OPENSKY,
                "aircraft icao24 not provided by upstream record",
            )
        )

    os_result = await opensky.get_state_by_icao24(icao24)
    if isinstance(os_result, ErrorEnvelope):
        # Provider routing rule:
        # Aviationstack-known + OpenSky-absent → DATA_UNAVAILABLE, not NO_MATCH,
        # and not a fall-back to schedule data.
        if os_result.code == ErrorCode.NO_MATCH:
            return envelope_to_dict(
                make_error(
                    ErrorCode.DATA_UNAVAILABLE,
                    Provider.OPENSKY,
                    "no live state for tracked aircraft",
                )
            )
        return envelope_to_dict(os_result)

    return _build_current_location_payload(os_result)


# --- registration -----------------------------------------------------------


def register_tools(
    server: Server,
    *,
    aviationstack: AviationstackClient,
    opensky: OpenSkyClient,
) -> None:
    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_departure_info",
                description=(
                    "Today's departure info for a flight by IATA flight number, "
                    "optionally narrowed by departure airport (dep_iata). "
                    "Returns the upstream Aviationstack record(s) enriched with "
                    "departure_times.{scheduled,estimated,actual}.{utc,local}."
                ),
                inputSchema=_DEPARTURE_INPUT_SCHEMA,
            ),
            Tool(
                name="get_arrival_info",
                description=(
                    "Today's arrival info for a flight by IATA flight number, "
                    "optionally narrowed by arrival airport (arr_iata). "
                    "Returns the upstream Aviationstack record(s) enriched with "
                    "arrival_times.{scheduled,estimated,actual}.{utc,local}."
                ),
                inputSchema=_ARRIVAL_INPUT_SCHEMA,
            ),
            Tool(
                name="get_current_location",
                description=(
                    "Live current location for a flight by IATA flight number. "
                    "Aviationstack provides the aircraft's 24-bit ICAO address; "
                    "OpenSky provides the live state vector. Returns "
                    "DATA_UNAVAILABLE when the aircraft is grounded or absent "
                    "from OpenSky's current state vectors."
                ),
                inputSchema=_FLIGHT_IATA_INPUT_SCHEMA,
            ),
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_departure_info":
            return await call_get_departure_info(arguments, aviationstack=aviationstack)
        if name == "get_arrival_info":
            return await call_get_arrival_info(arguments, aviationstack=aviationstack)
        if name == "get_current_location":
            return await call_get_current_location(
                arguments, aviationstack=aviationstack, opensky=opensky
            )
        raise ValueError(f"unknown tool: {name}")


# --- helpers ----------------------------------------------------------------


def _require_flight_iata(args: dict[str, Any]) -> str:
    """Defensive - SDK already validated the schema before this function runs."""
    val = args.get("flight_iata")
    if not isinstance(val, str) or not val.strip():
        raise ValueError("flight_iata must be a non-empty string")
    return val.strip()


def _flight_filters(
    args: dict[str, Any], *, airport_field: str | None = None
) -> dict[str, str]:
    """Build the Aviationstack filter dict from validated tool arguments.

    ``airport_field`` is the upstream parameter name (``dep_iata`` for the
    departure tool, ``arr_iata`` for the arrival tool). When the caller
    supplied that field, it's forwarded to Aviationstack as a narrowing filter
    - this is what distinguishes a "with airport context" query from a
    "without airport context" one.
    """
    filters: dict[str, str] = {"flight_iata": _require_flight_iata(args)}
    if airport_field:
        airport_value = args.get(airport_field)
        if isinstance(airport_value, str) and airport_value.strip():
            filters[airport_field] = airport_value.strip().upper()
    return filters


def _first_icao24(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        aircraft = record.get("aircraft")
        if not isinstance(aircraft, dict):
            continue
        for field in ("icao24", "icao_24bit", "icao_24_bit"):
            value = aircraft.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _enrich_record(record: dict[str, Any], *, leg: str) -> dict[str, Any]:
    """Shallow-copy ``record`` and append a ``<leg>_times`` block.

    Hard invariant 2 forbids editing upstream content; this only adds a new
    top-level key alongside the pass-through fields.
    """
    out = dict(record)
    leg_block = record.get(leg)
    if isinstance(leg_block, dict):
        out[f"{leg}_times"] = {
            "scheduled": enrich_iso_time(leg_block.get("scheduled")),
            "estimated": enrich_iso_time(leg_block.get("estimated")),
            "actual": enrich_iso_time(leg_block.get("actual")),
        }
    return out


def _build_current_location_payload(os_resp: OpenSkyResponse) -> dict[str, Any]:
    state = os_resp.states[0].raw
    callsign_raw = state[1]
    callsign = callsign_raw.strip() if isinstance(callsign_raw, str) else None
    return {
        "icao24": state[0],
        "callsign": callsign or None,
        "origin_country": state[2] if len(state) > 2 else None,
        "longitude": state[5] if len(state) > 5 else None,
        "latitude": state[6] if len(state) > 6 else None,
        "baro_altitude_m": state[7] if len(state) > 7 else None,
        "geo_altitude_m": state[13] if len(state) > 13 else None,
        "velocity_m_s": state[9] if len(state) > 9 else None,
        "true_track_deg": state[10] if len(state) > 10 else None,
        "vertical_rate_m_s": state[11] if len(state) > 11 else None,
        "on_ground": bool(state[8]) if len(state) > 8 else None,
        "as_of": {"utc": epoch_to_utc(os_resp.time), "local": None},
        "user_localised": USER_LOCALISED_DEFAULT,
    }
