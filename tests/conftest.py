"""Pytest hook: apply the same third-party logger discipline that
``obs.configure()`` enforces in production, so leakage tests verify the same
posture the running server has.

We do NOT call ``obs.configure()`` itself here - that wipes the root
handler list and breaks pytest's ``caplog`` capture. We replicate just the
quieting step.

We also load ``.env`` so live-smoke skipif gates see the operator's real
credentials when running locally. ``load_dotenv`` does not override existing
env vars, so a CI environment with injected secrets is unaffected.
"""
from __future__ import annotations

from dotenv import load_dotenv

from flight_info_mcp import obs

load_dotenv()


def pytest_configure(config):
    obs.quiet_third_party_loggers()
