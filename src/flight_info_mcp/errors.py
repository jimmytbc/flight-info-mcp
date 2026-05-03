"""Error taxonomy and central envelope constructor.

Every non-success response in the codebase passes through ``make_error``
and nowhere else, so provider clients always emit correctly-shaped envelopes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import obs


class ErrorCode(str, Enum):
    NO_MATCH = "NO_MATCH"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    QUOTA_LIMIT = "QUOTA_LIMIT"
    UPSTREAM_FAILURE = "UPSTREAM_FAILURE"


class Provider(str, Enum):
    AVIATIONSTACK = "aviationstack"
    OPENSKY = "opensky"
    NONE = "none"


@dataclass(frozen=True)
class ErrorEnvelope:
    code: ErrorCode
    provider: Provider
    detail: str


def make_error(code: ErrorCode, provider: Provider, detail: str = "") -> ErrorEnvelope:
    """Sole constructor of error envelopes.

    ``detail`` is passed through :func:`obs.redact` so that any registered
    secret value appearing in the detail string gets masked before the
    envelope leaves the function. Callers should still avoid putting raw
    upstream payload bytes into ``detail`` (no editing of upstream content).
    """
    return ErrorEnvelope(code=code, provider=provider, detail=obs.redact(detail))


def envelope_to_dict(env: ErrorEnvelope) -> dict[str, dict[str, str]]:
    """Wire-shape serialization of an ``ErrorEnvelope`` for tool outputs.

    Centralised here (alongside ``make_error``) so the project never has a
    second place that builds an ``{"error": {...}}`` dict - that would
    defeat the "single error-mapping module" invariant.
    """
    return {
        "error": {
            "code": env.code.value,
            "provider": env.provider.value,
            "detail": env.detail,
        }
    }
