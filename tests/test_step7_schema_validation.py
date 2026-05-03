"""Acceptance tests - schema validation of upstream responses.

Acceptance criteria:
1. A test with a valid upstream payload passes type-check and returns the
   parsed result.
2. A test with a payload missing a required structural field is mapped to
   UPSTREAM_FAILURE; the partial payload is not silently completed.
3. A test with a payload containing an oversize free-text field passes
   through unmodified (no truncation).
"""
from __future__ import annotations

import pytest
import respx

from flight_info_mcp.clients.aviationstack import (
    AviationstackClient,
    AviationstackResponse,
)
from flight_info_mcp.clients.opensky import OpenSkyClient, OpenSkyResponse
from flight_info_mcp.errors import ErrorCode, ErrorEnvelope, Provider
from flight_info_mcp.schemas import (
    OPENSKY_MIN_STATE_VECTOR_LENGTH,
    validate_aviationstack_payload,
    validate_opensky_payload,
)


# pytest-asyncio runs in ``asyncio_mode = "auto"`` (pyproject.toml), so each
# ``async def`` test is auto-collected without an explicit module-level mark.


AVI_URL = "https://api.aviationstack.com/v1/flights"
OS_URL = "https://opensky-network.org/api/states/all"


# =============================================================================
# Aviationstack validator
# =============================================================================


def test_aviationstack_validator_accepts_valid_payload():
    payload = {"pagination": {}, "data": [{"flight": {"iata": "BA117"}}]}
    result = validate_aviationstack_payload(payload)
    assert result is payload  # pass-through, not a copy


def test_aviationstack_validator_rejects_non_dict_top_level():
    result = validate_aviationstack_payload([{"data": []}])
    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE
    assert result.provider == Provider.AVIATIONSTACK


def test_aviationstack_validator_rejects_missing_data_field():
    """Acceptance #2 - partial payload (no 'data' key) must NOT be silently
    completed; rejection routes to UPSTREAM_FAILURE."""
    payload = {"pagination": {"count": 0}}  # no 'data' field at all
    result = validate_aviationstack_payload(payload)
    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE
    assert "data" in result.detail


def test_aviationstack_validator_does_not_silently_complete_partial_payload():
    """Defensive: a payload with an explicit but wrong-typed 'data' field
    must not get silently coerced. Same UPSTREAM_FAILURE outcome."""
    payload = {"data": "not a list, but truthy"}
    result = validate_aviationstack_payload(payload)
    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE


def test_aviationstack_validator_passes_in_body_error_through_for_client_to_map():
    """Aviationstack's in-body error envelope is structurally valid; the
    validator hands it back so the client can map invalid_access_key vs
    rate_limit etc. (the audit enforces those map through make_error)."""
    payload = {"error": {"code": "invalid_access_key", "message": "bad key"}}
    result = validate_aviationstack_payload(payload)
    assert result is payload


def test_aviationstack_validator_does_not_truncate_oversize_free_text():
    """Acceptance #3 - a 100KB free-text value must pass through unchanged."""
    huge = "A" * 100_000
    payload = {
        "data": [
            {
                "flight": {"iata": "BA117"},
                "departure": {"airport": huge},  # oversize free-text region
                "arrival": {"airport": "JFK"},
            }
        ]
    }
    result = validate_aviationstack_payload(payload)
    assert result is payload
    assert result["data"][0]["departure"]["airport"] == huge
    assert len(result["data"][0]["departure"]["airport"]) == 100_000


@respx.mock
async def test_aviationstack_client_returns_parsed_result_on_valid_payload():
    huge = "B" * 50_000
    respx.get(AVI_URL).respond(
        200,
        json={
            "data": [
                {
                    "flight": {"iata": "BA117"},
                    "departure": {"airport": huge, "iata": "LHR"},
                }
            ]
        },
    )
    async with AviationstackClient(api_key="k") as client:
        result = await client.query_flights(flight_iata="BA117")

    assert isinstance(result, AviationstackResponse)
    assert result.flights[0]["departure"]["airport"] == huge


@respx.mock
async def test_aviationstack_client_propagates_upstream_failure_on_missing_data():
    respx.get(AVI_URL).respond(200, json={"pagination": {"count": 0}})
    async with AviationstackClient(api_key="k") as client:
        result = await client.query_flights(flight_iata="BA117")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE


# =============================================================================
# OpenSky validator
# =============================================================================


def _well_formed_state(icao24: str = "abcd12") -> list:
    return [
        icao24, "BAW117  ", "United Kingdom", 1685529600, 1685529600,
        -0.1276, 51.5074, 11000.0, False, 250.0, 90.0, 0.0,
        None, 11500.0, None, False, 0,
    ]


def test_opensky_validator_accepts_valid_payload():
    payload = {"time": 1685529600, "states": [_well_formed_state()]}
    result = validate_opensky_payload(payload)
    assert result is payload


def test_opensky_validator_accepts_states_null():
    payload = {"time": 1685529600, "states": None}
    result = validate_opensky_payload(payload)
    assert result is payload  # client maps to NO_MATCH


def test_opensky_validator_rejects_non_dict_top_level():
    result = validate_opensky_payload([])
    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE
    assert result.provider == Provider.OPENSKY


def test_opensky_validator_rejects_missing_time_field():
    payload = {"states": [_well_formed_state()]}
    result = validate_opensky_payload(payload)
    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE
    assert "time" in result.detail


def test_opensky_validator_rejects_non_numeric_time():
    payload = {"time": "yesterday", "states": None}
    result = validate_opensky_payload(payload)
    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE


def test_opensky_validator_rejects_bool_time():
    """Defensive: ``True`` is an int subclass in Python - the validator must
    explicitly reject booleans masquerading as numbers."""
    payload = {"time": True, "states": None}
    result = validate_opensky_payload(payload)
    assert isinstance(result, ErrorEnvelope)


def test_opensky_validator_rejects_truncated_state_vector():
    short = ["abcd12", "X", "GB", 0, 0, 0.0, 0.0, 0.0]  # 8 fields, need 9
    assert len(short) < OPENSKY_MIN_STATE_VECTOR_LENGTH
    payload = {"time": 0, "states": [short]}
    result = validate_opensky_payload(payload)
    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE


def test_opensky_validator_does_not_truncate_oversize_callsign():
    """Acceptance #3 - a 100KB callsign string passes through unchanged."""
    huge = "C" * 100_000
    state = _well_formed_state()
    state[1] = huge  # callsign field
    payload = {"time": 1685529600, "states": [state]}
    result = validate_opensky_payload(payload)
    assert result is payload
    assert result["states"][0][1] == huge
    assert len(result["states"][0][1]) == 100_000


@respx.mock
async def test_opensky_client_returns_parsed_result_on_valid_payload():
    huge = "D" * 50_000
    state = _well_formed_state()
    state[2] = huge  # origin_country oversize
    respx.get(OS_URL).respond(200, json={"time": 1685529600, "states": [state]})

    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("abcd12")

    assert isinstance(result, OpenSkyResponse)
    assert result.states[0].raw[2] == huge


@respx.mock
async def test_opensky_client_propagates_upstream_failure_on_missing_time():
    respx.get(OS_URL).respond(200, json={"states": [_well_formed_state()]})
    async with OpenSkyClient() as client:
        result = await client.get_state_by_icao24("abcd12")

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE
