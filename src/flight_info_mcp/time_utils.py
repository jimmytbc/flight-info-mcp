"""Time-field enrichment.

UTC is the default. The airport-local representation is computed against the
airport's IANA timezone when one is supplied, so it stays correct even when
the upstream wire value carries an offset that disagrees with the airport
(e.g. Aviationstack returning a UTC-zoned string for a SIN arrival).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def enrich_iso_time(
    value: Any, *, airport_tz: str | None = None
) -> dict[str, Any] | None:
    """Convert an ISO-8601 string with optional offset into ``{"utc", "local"}``.

    Returns ``None`` if ``value`` is missing or unparseable.

    Behaviour by case:
    - **Wire value carries an offset and ``airport_tz`` is a valid IANA name:**
      ``local`` is the same instant expressed in the airport's tz (correct
      even if the wire offset disagrees).
    - **Wire value carries an offset and ``airport_tz`` is missing or
      unrecognised:** ``local`` falls back to the original wire string.
    - **Wire value is naive (no offset):** treated as UTC; ``local`` is
      ``None`` regardless of ``airport_tz``.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None

    if dt.tzinfo is None:
        return {"utc": _utc_iso(dt.replace(tzinfo=timezone.utc)), "local": None}

    utc_dt = dt.astimezone(timezone.utc)
    utc_iso = _utc_iso(utc_dt)

    if airport_tz:
        try:
            tz = ZoneInfo(airport_tz)
        except (ZoneInfoNotFoundError, ValueError):
            return {"utc": utc_iso, "local": value}
        return {"utc": utc_iso, "local": utc_dt.astimezone(tz).isoformat()}

    return {"utc": utc_iso, "local": value}


def epoch_to_utc(epoch: int | float) -> str:
    return _utc_iso(datetime.fromtimestamp(epoch, tz=timezone.utc))


def _utc_iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")
