# flight-info-mcp

A stateless [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server
that serves today-flight data to AI agents and LLM clients via three read-only tools.
The server queries two third-party providers and surfaces upstream results without
cleaning or reconciling them. Errors come back as a small typed envelope.

## Tools

| Tool | Provider | Returns |
|---|---|---|
| `get_departure_info(flight_iata, dep_iata?)` | Aviationstack | Today's departure record(s), enriched with `departure_times.{scheduled,estimated,actual}.{utc,local}` |
| `get_arrival_info(flight_iata, arr_iata?)` | Aviationstack | Today's arrival record(s), enriched with `arrival_times.{scheduled,estimated,actual}.{utc,local}` |
| `get_current_location(flight_iata)` | OpenSky (with Aviationstack as ICAO-24 directory) | Live state vector: lat, lon, altitude, velocity, heading, on-ground flag, `as_of.utc` |

When upstream cannot fulfil a query, every tool returns a typed error envelope
with a code from the AD-5 taxonomy (`NO_MATCH`, `DATA_UNAVAILABLE`, `QUOTA_LIMIT`,
`UPSTREAM_FAILURE`) and the responsible provider.

## Hard invariants

The server never:

- fabricates flight data when upstream is silent
- cleans, normalises or reconciles upstream payloads
- echoes API keys in tool output, error envelopes, log lines or test fixtures
- caches results, retries silently or holds per-request state across calls

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- An [Aviationstack](https://aviationstack.com/) API key (free tier works for
  smoke-testing; coverage is partial)
- Optional [OpenSky Network](https://opensky-network.org/) credentials for
  raised rate limits (anonymous works for low-volume use)

## Setup

```bash
uv sync
cp .env.example .env
$EDITOR .env   # paste AVIATIONSTACK_API_KEY and any optional values
```

The server reads `.env` at startup via `python-dotenv`. In production an
orchestrator's injected env vars take precedence over `.env` values.

## Run

```bash
uv run python -m flight_info_mcp
```

The server speaks MCP over stdio. Wire it into your agent harness with the
appropriate `StdioServerParameters` (or equivalent) per the MCP client SDK.

## Test

```bash
uv run pytest                              # full suite (mocked upstreams)
uv run pytest tests/test_step8_smoke.py    # smoke tests
```

Live-upstream tests in `test_step8_smoke.py` skip automatically unless
`AVIATIONSTACK_API_KEY` is set to a real (non-stub) value.

## Layout

```
src/flight_info_mcp/
  __main__.py     entry point: dotenv -> config -> obs -> run
  server.py       MCP Server wiring + stdio transport
  config.py       env-var loader; fail-fast on missing required vars
  obs.py          stderr-only logging with credential redaction
  errors.py       AD-5 envelope + sole make_error / envelope_to_dict
  schemas.py      structural validators for upstream responses
  time_utils.py   UTC and airport-local time enrichment
  tools.py        three MCP tools registered with the server
  clients/
    aviationstack.py   async HTTP client; access_key query-param auth
    opensky.py         async HTTP client; optional Basic Auth
tests/            pytest suite mirrored to the Build Brief steps
```

## License

Copyright 2026 Jimmy Tong. Licensed under the Apache License, Version 2.0
(the "License"); you may not use this software except in compliance with the
License. See [`LICENSE`](./LICENSE) or
[apache.org/licenses/LICENSE-2.0](http://www.apache.org/licenses/LICENSE-2.0).
