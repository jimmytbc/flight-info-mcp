"""Aviationstack provider client.

Endpoint: ``GET https://api.aviationstack.com/v1/flights``

Auth: ``access_key`` URL query parameter - Aviationstack's only documented
mechanism.

Logging discipline: this module never logs the request URL, the query string,
or the API key. It logs only the host + path + the *names* of filter
parameters (never their values), so even payload-shaped flight numbers stay
out of the log stream. As a defense-in-depth backup, the key is registered
with :func:`obs.register_secret` so any accidental log occurrence gets
redacted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .. import obs
from ..errors import ErrorCode, ErrorEnvelope, Provider, make_error
from ..schemas import validate_aviationstack_payload

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AviationstackResponse:
    """Pass-through of the Aviationstack ``data`` array.

    Upstream records are not edited, truncated, repaired or normalised. They sit in ``flights`` exactly as
    parsed from the JSON body.
    """

    flights: list[dict[str, Any]]


AviationstackResult = AviationstackResponse | ErrorEnvelope


class AviationstackClient:
    BASE_URL = "https://api.aviationstack.com/v1"
    HOST = "api.aviationstack.com"

    def __init__(
        self,
        api_key: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._key = api_key
        obs.register_secret(api_key)
        self._http = http if http is not None else httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "AviationstackClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def query_flights(self, **filters: str) -> AviationstackResult:
        """Fetch flights matching the given upstream filter parameters
        (e.g. ``flight_iata="BA117"``)."""
        path = "/flights"
        params = {"access_key": self._key, **filters}
        log.info(
            "aviationstack request method=GET host=%s path=%s filter_keys=%s",
            self.HOST,
            path,
            sorted(filters.keys()),
        )

        try:
            response = await self._http.get(f"{self.BASE_URL}{path}", params=params)
        except httpx.TimeoutException:
            log.info("aviationstack timeout host=%s path=%s", self.HOST, path)
            return make_error(
                ErrorCode.UPSTREAM_FAILURE, Provider.AVIATIONSTACK, "request timed out"
            )
        except httpx.HTTPError as exc:
            log.info(
                "aviationstack network-error host=%s path=%s type=%s",
                self.HOST,
                path,
                type(exc).__name__,
            )
            return make_error(
                ErrorCode.UPSTREAM_FAILURE, Provider.AVIATIONSTACK, "network error"
            )

        if response.status_code == 429:
            return make_error(
                ErrorCode.QUOTA_LIMIT, Provider.AVIATIONSTACK, "rate or quota limit hit"
            )
        if 500 <= response.status_code < 600:
            return make_error(
                ErrorCode.UPSTREAM_FAILURE,
                Provider.AVIATIONSTACK,
                f"upstream {response.status_code}",
            )

        try:
            payload = response.json()
        except ValueError:
            return make_error(
                ErrorCode.UPSTREAM_FAILURE, Provider.AVIATIONSTACK, "non-JSON response"
            )

        validated = validate_aviationstack_payload(payload)
        if isinstance(validated, ErrorEnvelope):
            return validated

        err = validated.get("error")
        if isinstance(err, dict):
            err_code = (err.get("code") or "").lower()
            if "rate" in err_code or "usage_limit" in err_code or "quota" in err_code:
                return make_error(
                    ErrorCode.QUOTA_LIMIT,
                    Provider.AVIATIONSTACK,
                    "quota or rate limit reached",
                )
            return make_error(
                ErrorCode.UPSTREAM_FAILURE,
                Provider.AVIATIONSTACK,
                "upstream returned error envelope",
            )

        return AviationstackResponse(flights=validated["data"])
