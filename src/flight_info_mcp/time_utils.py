"""Time-field enrichment.

UTC is the default. Where the upstream record carries a timezone-aware ISO
string, we add a sibling UTC form without touching the original - the
original IS the airport-local form (offset is the airport's tz).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def enrich_iso_time(value: Any) -> dict[str, Any] | None:
    """Convert an ISO-8601 string with optional offset into ``{"utc", "local"}``.

    Returns ``None`` if the value is missing or unparseable. ``local`` is the
    original string (which carries the airport-local time + offset); ``utc``
    is the same instant rendered with a ``Z`` suffix. If the upstream string
    is naive (no offset), it is treated as UTC and ``local`` is None.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return {"utc": _utc_iso(dt.replace(tzinfo=timezone.utc)), "local": None}
    return {"utc": _utc_iso(dt.astimezone(timezone.utc)), "local": value}


def epoch_to_utc(epoch: int | float) -> str:
    return _utc_iso(datetime.fromtimestamp(epoch, tz=timezone.utc))


def _utc_iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")
