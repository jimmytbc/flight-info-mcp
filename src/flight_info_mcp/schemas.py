"""Schema validation of upstream responses.

These validators type-check the structural shape of each upstream response.
They emit ``UPSTREAM_FAILURE`` envelopes on malformed structure and never
anything else. Per-provider semantic mappings (NO_MATCH for empty result
sets, DATA_UNAVAILABLE for grounded aircraft, QUOTA_LIMIT for in-body
rate-limit envelopes, etc.) stay in the provider clients - those are
*interpretation*, not *validation*.

The validators never edit, truncate or repair the payload. Free-text fields
of arbitrary size pass through unchanged (verified by tests).
"""
from __future__ import annotations

from typing import Any

from .errors import ErrorCode, ErrorEnvelope, Provider, make_error

OPENSKY_MIN_STATE_VECTOR_LENGTH = 9


def validate_aviationstack_payload(payload: Any) -> dict[str, Any] | ErrorEnvelope:
    """Validate the structural shape of an Aviationstack ``/v1/flights``
    response. Returns the original payload on success (so the client can
    inspect both the success-shape ``{"data": [...]}`` and the in-body
    error-shape ``{"error": {...}}``) or an ``UPSTREAM_FAILURE`` envelope.
    """
    if not isinstance(payload, dict):
        return _aviationstack_failure("expected JSON object at top level")

    # In-body error envelope is structurally valid; the client maps it to
    # (QUOTA_LIMIT vs UPSTREAM_FAILURE based on err.code).
    err = payload.get("error")
    if isinstance(err, dict):
        return payload

    if "data" not in payload:
        return _aviationstack_failure("missing required field: data")
    if not isinstance(payload["data"], list):
        return _aviationstack_failure("field 'data' must be a list")

    return payload


def validate_opensky_payload(payload: Any) -> dict[str, Any] | ErrorEnvelope:
    """Validate the structural shape of an OpenSky ``/api/states/all``
    response. Returns the original payload on success or an
    ``UPSTREAM_FAILURE`` envelope. ``states: null`` is structurally valid
    (the client maps it to ``NO_MATCH``)."""
    if not isinstance(payload, dict):
        return _opensky_failure("expected JSON object at top level")

    if "time" not in payload:
        return _opensky_failure("missing required field: time")
    if not isinstance(payload["time"], (int, float)) or isinstance(payload["time"], bool):
        return _opensky_failure("field 'time' must be a number")

    states = payload.get("states")
    if states is None:
        return payload
    if not isinstance(states, list):
        return _opensky_failure("field 'states' must be a list or null")

    for state in states:
        if not isinstance(state, list):
            return _opensky_failure("each state must be a list")
        if len(state) < OPENSKY_MIN_STATE_VECTOR_LENGTH:
            return _opensky_failure(
                f"state vector too short (need >= {OPENSKY_MIN_STATE_VECTOR_LENGTH} elements)"
            )

    return payload


def _aviationstack_failure(detail: str) -> ErrorEnvelope:
    return make_error(ErrorCode.UPSTREAM_FAILURE, Provider.AVIATIONSTACK, detail)


def _opensky_failure(detail: str) -> ErrorEnvelope:
    return make_error(ErrorCode.UPSTREAM_FAILURE, Provider.OPENSKY, detail)
