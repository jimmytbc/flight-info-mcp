"""Unit tests for time-field enrichment.

Covers BUG-02 regression cases: airport-local representation must reflect
the airport's IANA timezone, not the wire offset.
"""
from __future__ import annotations

from flight_info_mcp.time_utils import enrich_iso_time, epoch_to_utc


# --- naive value branch -------------------------------------------------------


def test_naive_value_returns_local_null_regardless_of_tz():
    """Wire value with no offset is treated as UTC; local is None even when an
    airport_tz is supplied. Existing behaviour kept (BUG-02 acceptance)."""
    assert enrich_iso_time("2026-05-03T07:00:00") == {
        "utc": "2026-05-03T07:00:00Z",
        "local": None,
    }
    assert enrich_iso_time("2026-05-03T07:00:00", airport_tz="Asia/Singapore") == {
        "utc": "2026-05-03T07:00:00Z",
        "local": None,
    }


# --- airport_tz branch (BUG-02 fix) ------------------------------------------


def test_utc_wire_value_converted_to_singapore_local():
    """Aviationstack returns 14:00 UTC for a SIN arrival; local must reflect
    Asia/Singapore (UTC+8 fixed, no DST)."""
    result = enrich_iso_time(
        "2026-05-03T14:00:00+00:00", airport_tz="Asia/Singapore"
    )
    assert result == {
        "utc": "2026-05-03T14:00:00Z",
        "local": "2026-05-03T22:00:00+08:00",
    }


def test_utc_wire_value_converted_to_london_bst():
    """May falls in BST (UTC+1). The wire offset of +00:00 disagrees with
    the airport's actual May offset; the IANA tz wins."""
    result = enrich_iso_time(
        "2026-05-03T07:00:00+00:00", airport_tz="Europe/London"
    )
    assert result == {
        "utc": "2026-05-03T07:00:00Z",
        "local": "2026-05-03T08:00:00+01:00",
    }


def test_utc_wire_value_converted_to_new_york_edt():
    """May = EDT = UTC-4."""
    result = enrich_iso_time(
        "2026-05-03T18:00:00+00:00", airport_tz="America/New_York"
    )
    assert result == {
        "utc": "2026-05-03T18:00:00Z",
        "local": "2026-05-03T14:00:00-04:00",
    }


def test_utc_wire_value_converted_to_new_york_est_in_winter():
    """January = EST = UTC-5. Verifies DST-aware conversion across the year."""
    result = enrich_iso_time(
        "2026-01-15T18:00:00+00:00", airport_tz="America/New_York"
    )
    assert result == {
        "utc": "2026-01-15T18:00:00Z",
        "local": "2026-01-15T13:00:00-05:00",
    }


def test_wire_offset_disagreeing_with_airport_tz_is_overridden():
    """Even if the wire value carries +02:00, the destination IANA wins."""
    result = enrich_iso_time(
        "2026-05-03T16:00:00+02:00", airport_tz="Asia/Singapore"
    )
    # Wire instant is 14:00:00 UTC; SIN is +08:00.
    assert result == {
        "utc": "2026-05-03T14:00:00Z",
        "local": "2026-05-03T22:00:00+08:00",
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
