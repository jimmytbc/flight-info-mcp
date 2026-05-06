"""Unit tests for time-field enrichment.

Covers BUG-02 + BUG-03 regression cases. The wire offset is treated as a
placeholder when an IANA airport_tz is supplied (Aviationstack writes
airport-local timestamps with +00:00 tags; we cannot trust the wire offset).
"""
from __future__ import annotations

from flight_info_mcp.time_utils import enrich_iso_time, epoch_to_utc


# --- naive value branch -------------------------------------------------------


def test_naive_value_without_airport_tz_treated_as_utc():
    """Without an airport_tz to anchor against, a naive wire value is
    treated as UTC and local is None (no airport context to express it in)."""
    assert enrich_iso_time("2026-05-03T07:00:00") == {
        "utc": "2026-05-03T07:00:00Z",
        "local": None,
    }


def test_naive_value_with_airport_tz_anchored_as_local():
    """With an airport_tz, a naive wire value is anchored to that tz."""
    assert enrich_iso_time(
        "2026-05-03T07:00:00", airport_tz="Asia/Singapore"
    ) == {
        "utc": "2026-05-02T23:00:00Z",
        "local": "2026-05-03T07:00:00+08:00",
    }


# --- airport_tz branch (BUG-03 re-anchor) ------------------------------------


def test_singapore_arrival_with_plus_zero_wire_anchored_to_sgt():
    """BUG-03: Aviationstack writes SIN-local time tagged +00:00. The wire
    components 14:00 anchor to Asia/Singapore -> 14:00 SGT = 06:00 UTC."""
    result = enrich_iso_time(
        "2026-05-03T14:00:00+00:00", airport_tz="Asia/Singapore"
    )
    assert result == {
        "utc": "2026-05-03T06:00:00Z",
        "local": "2026-05-03T14:00:00+08:00",
    }


def test_london_departure_with_plus_zero_wire_anchored_to_bst():
    """May falls in BST (UTC+1); wire components 07:00 anchor to BST."""
    result = enrich_iso_time(
        "2026-05-03T07:00:00+00:00", airport_tz="Europe/London"
    )
    assert result == {
        "utc": "2026-05-03T06:00:00Z",
        "local": "2026-05-03T07:00:00+01:00",
    }


def test_new_york_arrival_anchored_to_edt_in_summer():
    """May = EDT = UTC-4. Wire components 18:00 anchor to EDT."""
    result = enrich_iso_time(
        "2026-05-03T18:00:00+00:00", airport_tz="America/New_York"
    )
    assert result == {
        "utc": "2026-05-03T22:00:00Z",
        "local": "2026-05-03T18:00:00-04:00",
    }


def test_new_york_arrival_anchored_to_est_in_winter():
    """January = EST = UTC-5. Verifies DST-aware anchor across the year."""
    result = enrich_iso_time(
        "2026-01-15T18:00:00+00:00", airport_tz="America/New_York"
    )
    assert result == {
        "utc": "2026-01-15T23:00:00Z",
        "local": "2026-01-15T18:00:00-05:00",
    }


def test_wire_offset_is_ignored_when_airport_tz_is_supplied():
    """The wire offset is advisory only - the IANA tz is authoritative.
    A wire of 16:00+02:00 with airport_tz=Asia/Singapore anchors the
    components 16:00 to SGT, ignoring the wire's +02:00."""
    result = enrich_iso_time(
        "2026-05-03T16:00:00+02:00", airport_tz="Asia/Singapore"
    )
    assert result == {
        "utc": "2026-05-03T08:00:00Z",
        "local": "2026-05-03T16:00:00+08:00",
    }


# --- fallback branches --------------------------------------------------------


def test_invalid_iana_name_falls_back_to_wire_value():
    """Bad tz string from upstream must not crash; fall back to original."""
    result = enrich_iso_time(
        "2026-05-03T07:00:00+00:00", airport_tz="Mars/Olympus"
    )
    assert result == {
        "utc": "2026-05-03T07:00:00Z",
        "local": "2026-05-03T07:00:00+00:00",
    }


def test_no_airport_tz_provided_falls_back_to_wire_value():
    """Original behaviour preserved when caller omits airport_tz."""
    result = enrich_iso_time("2026-05-03T07:00:00+00:00")
    assert result == {
        "utc": "2026-05-03T07:00:00Z",
        "local": "2026-05-03T07:00:00+00:00",
    }


def test_blank_airport_tz_treated_as_missing():
    """Whitespace or empty string is not a valid IANA name; falls back."""
    result = enrich_iso_time(
        "2026-05-03T07:00:00+00:00", airport_tz=""
    )
    assert result == {
        "utc": "2026-05-03T07:00:00Z",
        "local": "2026-05-03T07:00:00+00:00",
    }


# --- input validation ---------------------------------------------------------


def test_missing_value_returns_none():
    assert enrich_iso_time(None) is None
    assert enrich_iso_time("") is None
    assert enrich_iso_time(None, airport_tz="Asia/Singapore") is None


def test_non_string_value_returns_none():
    assert enrich_iso_time(1234567890) is None
    assert enrich_iso_time(["2026-05-03"]) is None


def test_unparseable_string_returns_none():
    assert enrich_iso_time("not an ISO string") is None
    assert enrich_iso_time("2026-13-99") is None


# --- epoch_to_utc unchanged ---------------------------------------------------


def test_epoch_to_utc_returns_z_suffix():
    assert epoch_to_utc(1685529600) == "2023-05-31T10:40:00Z"
