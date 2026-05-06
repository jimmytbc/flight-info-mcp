"""Acceptance tests - Aviationstack client.

Acceptance criteria:
1. A live integration test against Aviationstack with a valid key returns a
   parsed result for a known active flight; the API key does not appear in
   the test transcript.
2. A test with a deliberately invalid key returns ``UPSTREAM_FAILURE`` with
   provider=aviationstack; no key value appears in the error envelope.
3. A 429-class response is mapped to ``QUOTA_LIMIT``.
4. Non-JSON or schema-shape mismatch is mapped to ``UPSTREAM_FAILURE``; the
   malformed payload is not echoed in the detail field.
"""
from __future__ import annotations

import logging
import os

import httpx
import pytest
import respx

from flight_info_mcp import obs
from flight_info_mcp.clients.aviationstack import (
    AviationstackClient,
    AviationstackResponse,
)
from flight_info_mcp.errors import ErrorCode, ErrorEnvelope, Provider


pytestmark = pytest.mark.asyncio


FLIGHTS_URL = "https://api.aviationstack.com/v1/flights"


# --- success path ---------------------------------------------------------------------------


@respx.mock
async def test_success_returns_pass_through_data():
    payload = {
        "pagination": {"limit": 100, "offset": 0, "count": 1, "total": 1},
        "data": [
            {"flight": {"iata": "BA117", "icao": "BAW117", "number": "117"}}
        ],
    }
    respx.get(FLIGHTS_URL).respond(200, json=payload)

    async with AviationstackClient(api_key="test-key") as client:
        result = await client.query_flights(flight_iata="BA117")

    assert isinstance(result, AviationstackResponse)
    assert result.flights == payload["data"]


@respx.mock
async def test_request_uses_access_key_query_param_not_header():
    route = respx.get(FLIGHTS_URL).respond(200, json={"data": []})

    async with AviationstackClient(api_key="my-test-key-xyz") as client:
        await client.query_flights(flight_iata="BA117")

    assert route.called
    request = route.calls.last.request
    assert "access_key=my-test-key-xyz" in str(request.url), (
        f"key should be on URL query: {request.url}"
    )
    header_names = {k.lower() for k in request.headers.keys()}
    assert "authorization" not in header_names
    assert "x-api-key" not in header_names


# --- error mappings -------------------------------------------------------------------------


@respx.mock
async def test_invalid_key_in_body_maps_to_upstream_failure():
    """Aviationstack returns 200 with an in-body error envelope when the key is bad."""
    respx.get(FLIGHTS_URL).respond(
        200,
        json={
            "error": {
                "code": "invalid_access_key",
                "message": "You have not supplied a valid API Access Key.",
            }
        },
    )

    async with AviationstackClient(api_key="bogus-key") as client:
        result = await client.query_flights()

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE
    assert result.provider == Provider.AVIATIONSTACK
    assert "bogus-key" not in result.detail


@respx.mock
async def test_429_status_maps_to_quota_limit():
    respx.get(FLIGHTS_URL).respond(429, text="rate limited")

    async with AviationstackClient(api_key="k") as client:
        result = await client.query_flights()

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.QUOTA_LIMIT
    assert result.provider == Provider.AVIATIONSTACK


@respx.mock
async def test_in_body_quota_error_maps_to_quota_limit():
    respx.get(FLIGHTS_URL).respond(
        200,
        json={
            "error": {
                "code": "usage_limit_reached",
                "message": "Your monthly usage limit has been reached.",
            }
        },
    )

    async with AviationstackClient(api_key="k") as client:
        result = await client.query_flights()

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.QUOTA_LIMIT


@respx.mock
async def test_5xx_status_maps_to_upstream_failure():
    respx.get(FLIGHTS_URL).respond(503, text="service unavailable")

    async with AviationstackClient(api_key="k") as client:
        result = await client.query_flights()

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE


@respx.mock
async def test_non_json_body_maps_to_upstream_failure_without_echoing_payload():
    sentinel = "BIG_HTML_PAYLOAD_THAT_SHOULD_NOT_LEAK_INTO_DETAIL"
    respx.get(FLIGHTS_URL).respond(200, text=f"<html>{sentinel}</html>")

    async with AviationstackClient(api_key="k") as client:
        result = await client.query_flights()

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE
    assert sentinel not in result.detail


@respx.mock
async def test_unexpected_shape_maps_to_upstream_failure_without_echoing_payload():
    leaky_value = "shape-mismatch-payload-DO-NOT-ECHO"
    respx.get(FLIGHTS_URL).respond(200, json={"unexpected": leaky_value})

    async with AviationstackClient(api_key="k") as client:
        result = await client.query_flights()

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE
    assert leaky_value not in result.detail


@respx.mock
async def test_timeout_maps_to_upstream_failure():
    respx.get(FLIGHTS_URL).mock(side_effect=httpx.TimeoutException("timed out"))

    async with AviationstackClient(api_key="k") as client:
        result = await client.query_flights()

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE


@respx.mock
async def test_network_error_maps_to_upstream_failure():
    respx.get(FLIGHTS_URL).mock(side_effect=httpx.ConnectError("refused"))

    async with AviationstackClient(api_key="k") as client:
        result = await client.query_flights()

    assert isinstance(result, ErrorEnvelope)
    assert result.code == ErrorCode.UPSTREAM_FAILURE


# --- secret leakage discipline ---------------------------------------------------------------


@respx.mock
async def test_key_value_does_not_appear_in_logs(caplog):
    distinct_key = "SECRETkey-aviationstack-step3-SHOULDNOTLEAK-1"
    respx.get(FLIGHTS_URL).respond(200, json={"data": []})

    with caplog.at_level(logging.DEBUG):
        async with AviationstackClient(api_key=distinct_key) as client:
            await client.query_flights(flight_iata="BA117")

    rendered = "\n".join(rec.getMessage() for rec in caplog.records)
    assert distinct_key not in rendered, f"key leaked into logs: {rendered!r}"


@respx.mock
async def test_invalid_key_envelope_does_not_contain_key_value():
    distinct_key = "SECRETkey-aviationstack-step3-SHOULDNOTLEAK-2"
    respx.get(FLIGHTS_URL).respond(
        200,
        json={
            "error": {
                "code": "invalid_access_key",
                "message": f"Bad key: {distinct_key}",
            }
        },
    )

    async with AviationstackClient(api_key=distinct_key) as client:
        result = await client.query_flights()

    assert isinstance(result, ErrorEnvelope)
    assert distinct_key not in result.detail


# --- live integration ----------------------------------------------------------------------


_LIVE_KEY = (os.environ.get("AVIATIONSTACK_API_KEY") or "").strip()
_LIVE_KEY_IS_REAL = _LIVE_KEY and _LIVE_KEY not in {"step1-stub", "stub-aviationstack", "stub"}


@pytest.mark.skipif(
    not _LIVE_KEY_IS_REAL,
    reason="AVIATIONSTACK_API_KEY is unset or a stub; skipping live integration test.",
)
async def test_live_integration_with_valid_key_returns_parsed_result(caplog):
    flight_iata = os.environ.get("AVIATIONSTACK_LIVE_FLIGHT", "BA117")
    obs.register_secret(_LIVE_KEY)

    with caplog.at_level(logging.DEBUG):
        async with AviationstackClient(api_key=_LIVE_KEY) as client:
            result = await client.query_flights(flight_iata=flight_iata)

    if isinstance(result, ErrorEnvelope) and result.code == ErrorCode.QUOTA_LIMIT:
        pytest.skip(f"Aviationstack quota exhausted; rerun later: {result!r}")
    assert isinstance(result, AviationstackResponse), (
        f"expected parsed result with valid key, got {result!r}"
    )
    rendered = "\n".join(rec.getMessage() for rec in caplog.records)
    assert _LIVE_KEY not in rendered, "live API key leaked into log records"
