"""Stderr-only structured logging with credential redaction.

This module stands up the sink and the redaction filter. Other modules register
loaded credentials via ``register_secret`` so any record carrying that value
gets masked before it reaches stderr.
"""
from __future__ import annotations

import logging
import sys
import time

_REDACTED = "***REDACTED***"
_secrets: set[str] = set()


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not _secrets:
            return True
        rendered = record.getMessage()
        masked = rendered
        for secret in _secrets:
            if secret and secret in masked:
                masked = masked.replace(secret, _REDACTED)
        if masked != rendered:
            record.msg = masked
            record.args = None
        return True


def register_secret(value: str | None) -> None:
    if value:
        _secrets.add(value)


def redact(text: str) -> str:
    """Return ``text`` with every registered secret replaced by ``***REDACTED***``."""
    if not text or not _secrets:
        return text
    out = text
    for secret in _secrets:
        if secret and secret in out:
            out = out.replace(secret, _REDACTED)
    return out


_THIRD_PARTY_QUIET_LOGGERS = (
    # httpx logs every request URL at INFO, which would leak Aviationstack's
    # access_key query parameter to stderr. Hold these at WARNING so only
    # actual problems surface.
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "hpack",
)


def quiet_third_party_loggers() -> None:
    for name in _THIRD_PARTY_QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def configure(level: int = logging.INFO) -> logging.Logger:
    formatter = logging.Formatter(
        fmt="%(asctime)sZ level=%(levelname)s logger=%(name)s msg=%(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    # Filter at the logger level so every handler (present and future) sees a
    # redacted record, not just the stderr StreamHandler.
    for existing_filter in list(root.filters):
        root.removeFilter(existing_filter)
    root.addFilter(_RedactFilter())
    root.setLevel(level)
    quiet_third_party_loggers()
    return root
