"""Time-field enrichment.

The IANA ``timezone`` field of an upstream record is the single source of
truth for the airport's offset. The wire timestamp's offset is treated as
a placeholder and stripped: the calendar/clock components are re-anchored
to the airport's IANA tz when one is supplied.

Why: Aviationstack consistently writes airport-local timestamps into fields
tagged with ``+00:00``. Trusting the wire offset would produce results
shifted by the airport's UTC offset (e.g. a SIN arrival at 20:15 SGT shown
as 04:15 SGT next day). The airport's IANA tz is reliably correct; the wire
offset is not.

When no IANA tz is supplied (caller didn't have one to pass), the wire
offset is the only signal available and we trust it. Naive wire values are
treated as UTC under the same fallback path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def enrich_iso_time(
    value: Any, *, airport_tz: str | None = None
) -> dict[str, Any] | None:
    """Convert an ISO-8601 string into ``{"utc", "local"}``.

    Returns ``None`` if ``value`` is missing or unparseable.

    Behaviour by case:
    - **``airport_tz`` is a valid IANA name:** the wire offset is ignored.
      The timestamp's calendar/clock components are anchored to the
      airport's tz, and both ``utc`` and ``local`` are derived from there.
    - **``airport_tz`` is missing or unrecognised, wire has an offset:**
      trust the wire offset; ``local`` is the original wire string.
    - **``airport_tz`` is missing or unrecognised, wire is naive:** treat
      as UTC; ``local`` is ``None``.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None

    if airport_tz:
        try:
            tz = ZoneInfo(airport_tz)
        except (ZoneInfoNotFoundError, ValueError):
            tz = None
        if tz is not None:
            anchored = dt.replace(tzinfo=None).replace(tzinfo=tz)
            return {
                "utc": _utc_iso(anchored.astimezone(timezone.utc)),
                "local": anchored.isoformat(),
            }

    if dt.tzinfo is None:
        return {"utc": _utc_iso(dt.replace(tzinfo=timezone.utc)), "local": None}
    return {"utc": _utc_iso(dt.astimezone(timezone.utc)), "local": value}


def epoch_to_utc(epoch: int | float) -> str:
    return _utc_iso(datetime.fromtimestamp(epoch, tz=timezone.utc))


def _utc_iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")
