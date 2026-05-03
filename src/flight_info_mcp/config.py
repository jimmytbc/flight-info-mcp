"""Process-start configuration loader.

Reads provider credentials from the environment exactly once. Missing required
keys cause a fail-fast ``ConfigError``. The startup entry point in
``__main__`` translates that to a non-zero exit and a stderr line that names
the missing variable but never echoes a value.

Loaded credentials are registered with :mod:`obs` so the redaction filter
masks them out of any future log record.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from . import obs

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Raised when required configuration cannot be loaded."""


@dataclass(frozen=True)
class Config:
    aviationstack_api_key: str
    opensky_username: str | None
    opensky_password: str | None

    @property
    def opensky_authenticated(self) -> bool:
        return bool(self.opensky_username) and bool(self.opensky_password)


_REQUIRED_VARS = ("AVIATIONSTACK_API_KEY",)


def load(env: dict[str, str] | None = None) -> Config:
    src = env if env is not None else os.environ

    for name in _REQUIRED_VARS:
        if not (src.get(name) or "").strip():
            raise ConfigError(f"missing required environment variable: {name}")

    av_key = src["AVIATIONSTACK_API_KEY"].strip()
    os_user = (src.get("OPENSKY_USERNAME") or "").strip() or None
    os_pass = (src.get("OPENSKY_PASSWORD") or "").strip() or None

    obs.register_secret(av_key)
    obs.register_secret(os_user)
    obs.register_secret(os_pass)

    paired = bool(os_user) and bool(os_pass)
    if (os_user is None) ^ (os_pass is None):
        log.warning(
            "OpenSky credentials incomplete (exactly one of "
            "OPENSKY_USERNAME / OPENSKY_PASSWORD set); operating in "
            "unauthenticated mode"
        )

    return Config(
        aviationstack_api_key=av_key,
        opensky_username=os_user if paired else None,
        opensky_password=os_pass if paired else None,
    )
