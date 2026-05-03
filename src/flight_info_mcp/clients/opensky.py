"""OpenSky provider client.

Endpoint: ``GET https://opensky-network.org/api/states/all``

Auth: HTTP Basic Auth with ``OPENSKY_USERNAME`` / ``OPENSKY_PASSWORD`` when
both are set; anonymous (lower rate limit) otherwise. Credentials are
registered with :mod:`obs` so any accidental log occurrence gets redacted.

Mapping rules:
- airborne aircraft (state present, ``on_ground == False``) → parsed result
- grounded aircraft (state present, ``on_ground == True``)  → DATA_UNAVAILABLE
- unknown ICAO 24-bit address                               → NO_MATCH
- timeout / network / 5xx / non-JSON / shape mismatch       → UPSTREAM_FAILURE
- 429-class                                                 → QUOTA_LIMIT
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .. import obs
from ..errors import ErrorCode, ErrorEnvelope, Provider, make_error
from ..schemas import validate_opensky_payload

log = logging.getLogger(__name__)

ON_GROUND_INDEX = 8


@dataclass(frozen=True)
class OpenSkyState:
    """Pass-through OpenSky state vector. Hard invariant 2: not transformed."""

    raw: list[Any]


@dataclass(frozen=True)
class OpenSkyResponse:
    time: int
    states: list[OpenSkyState]


OpenSkyResult = OpenSkyResponse | ErrorEnvelope


class OpenSkyClient:
    BASE_URL = "https://opensky-network.org/api"
    HOST = "opensky-network.org"

    def __init__(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        http: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        if username:
            obs.register_secret(username)
        if password:
            obs.register_secret(password)
        self._auth: httpx.BasicAuth | None = (
            httpx.BasicAuth(username, password) if username and password else None
        )
        self._http = http if http is not None else httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "OpenSkyClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def get_state_by_icao24(self, icao24: str) -> OpenSkyResult:
        """Look up the live state vector for one aircraft by 24-bit ICAO address."""
        path = "/states/all"
        params = {"icao24": icao24.lower().strip()}
        log.info(
            "opensky request method=GET host=%s path=%s authenticated=%s filter_keys=%s",
            self.HOST,
            path,
            self._auth is not None,
            sorted(params.keys()),
        )

        try:
            response = await self._http.get(
                f"{self.BASE_URL}{path}", params=params, auth=self._auth
            )
        except httpx.TimeoutException:
            log.info("opensky timeout host=%s path=%s", self.HOST, path)
            return make_error(
                ErrorCode.UPSTREAM_FAILURE, Provider.OPENSKY, "request timed out"
            )
        except httpx.HTTPError as exc:
            log.info(
                "opensky network-error host=%s path=%s type=%s",
                self.HOST,
                path,
                type(exc).__name__,
            )
            return make_error(
                ErrorCode.UPSTREAM_FAILURE, Provider.OPENSKY, "network error"
            )

        if response.status_code == 404:
            return make_error(
                ErrorCode.NO_MATCH, Provider.OPENSKY, "no matching aircraft state"
            )
        if response.status_code == 429:
            return make_error(
                ErrorCode.QUOTA_LIMIT, Provider.OPENSKY, "rate or quota limit hit"
            )
        if 500 <= response.status_code < 600:
            return make_error(
                ErrorCode.UPSTREAM_FAILURE,
                Provider.OPENSKY,
                f"upstream {response.status_code}",
            )
        if response.status_code != 200:
            return make_error(
                ErrorCode.UPSTREAM_FAILURE,
                Provider.OPENSKY,
                f"upstream {response.status_code}",
            )

        try:
            payload = response.json()
        except ValueError:
            return make_error(
                ErrorCode.UPSTREAM_FAILURE, Provider.OPENSKY, "non-JSON response"
            )

        validated = validate_opensky_payload(payload)
        if isinstance(validated, ErrorEnvelope):
            return validated

        states = validated.get("states")
        if states is None or states == []:
            return make_error(
                ErrorCode.NO_MATCH, Provider.OPENSKY, "no matching aircraft state"
            )

        if all(bool(state[ON_GROUND_INDEX]) for state in states):
            return make_error(
                ErrorCode.DATA_UNAVAILABLE,
                Provider.OPENSKY,
                "aircraft is on the ground; live coordinates not available",
            )

        return OpenSkyResponse(
            time=int(validated["time"]),
            states=[OpenSkyState(raw=list(state)) for state in states],
        )
