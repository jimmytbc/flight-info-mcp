"""Acceptance tests - error taxonomy enforcement.

Acceptance criteria:
1. A static-analysis or unit test enumerates every error-construction call site
   in the codebase and verifies each uses the central error-mapping module.
2. A test confirms the error envelope's ``detail`` field never contains the
   literal value of any environment variable known to hold a credential.
3. A test confirms the error envelope's ``provider`` field is one of
   ``aviationstack``, ``opensky`` or ``none``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from flight_info_mcp import obs
from flight_info_mcp.errors import (
    ErrorCode,
    ErrorEnvelope,
    Provider,
    envelope_to_dict,
    make_error,
)


SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "flight_info_mcp"
ERRORS_PY = SRC_ROOT / "errors.py"


# --- 1: AST audit - only errors.py constructs ErrorEnvelope ----------------


def test_error_envelope_constructed_only_inside_errors_py():
    """Every direct ``ErrorEnvelope(...)`` call must live in ``errors.py``.
    All other production code paths must build envelopes via ``make_error``."""
    offending: list[tuple[str, int]] = []
    for py_file in SRC_ROOT.rglob("*.py"):
        if py_file == ERRORS_PY:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_envelope_call = (
                (isinstance(func, ast.Name) and func.id == "ErrorEnvelope")
                or (isinstance(func, ast.Attribute) and func.attr == "ErrorEnvelope")
            )
            if is_envelope_call:
                offending.append(
                    (str(py_file.relative_to(SRC_ROOT.parent.parent)), node.lineno)
                )
    assert offending == [], (
        "Direct ErrorEnvelope(...) construction found outside errors.py - "
        f"all envelopes must go through make_error(): {offending}"
    )


def test_error_response_dict_constructed_only_inside_errors_py():
    """Every literal ``{"error": {...}}`` dict must be inside ``errors.py``
    (the ``envelope_to_dict`` helper is the only legitimate site).
    Catches ad-hoc error-shape building outside the central module."""
    offending: list[tuple[str, int]] = []
    for py_file in SRC_ROOT.rglob("*.py"):
        if py_file == ERRORS_PY:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key in node.keys:
                if (
                    isinstance(key, ast.Constant)
                   and key.value == "error"
                ):
                    offending.append(
                        (str(py_file.relative_to(SRC_ROOT.parent.parent)), node.lineno)
                    )
                    break
    assert offending == [], (
        "Literal {'error': ...} dicts found outside errors.py - "
        f"use envelope_to_dict(): {offending}"
    )


# --- 2: detail never contains literal credential values --------------------


def test_make_error_redacts_registered_credentials_from_detail():
    """obs.register_secret + make_error must mask credentials before
    the envelope is constructed."""
    sentinel = "FAKE-CREDENTIAL-SHOULD-NEVER-LEAK-INTO-DETAIL-987"
    obs.register_secret(sentinel)

    env = make_error(
        ErrorCode.UPSTREAM_FAILURE,
        Provider.AVIATIONSTACK,
        f"upstream complaint mentioning {sentinel}",
    )
    assert sentinel not in env.detail
    assert "***REDACTED***" in env.detail


def test_envelope_to_dict_inherits_redacted_detail():
    """Serializing to wire-shape doesn't reintroduce a leak - detail field
    of the dict is exactly the envelope's already-redacted detail."""
    sentinel = "FAKE-CREDENTIAL-SHOULD-NEVER-LEAK-INTO-DICT-654"
    obs.register_secret(sentinel)

    env = make_error(
        ErrorCode.QUOTA_LIMIT,
        Provider.OPENSKY,
        f"quota tripped with creds {sentinel}",
    )
    wire = envelope_to_dict(env)
    assert sentinel not in wire["error"]["detail"]


def test_detail_never_contains_any_known_credential_env_var(monkeypatch):
    """Apply the live ``config.load`` flow with concrete fake credentials,
    then exercise an error path through ``make_error`` and confirm none of
    the credential values reach the envelope's detail field."""
    fake_av_key = "FAKE-AVKEY-CONFIG-LOAD-TEST-1111"
    fake_user = "FAKE-OSUSER-CONFIG-LOAD-TEST-2222"
    fake_pass = "FAKE-OSPASS-CONFIG-LOAD-TEST-3333"

    from flight_info_mcp.config import load
    cfg = load(env={
        "AVIATIONSTACK_API_KEY": fake_av_key,
        "OPENSKY_USERNAME": fake_user,
        "OPENSKY_PASSWORD": fake_pass,
    })
    assert cfg.aviationstack_api_key == fake_av_key  # sanity

    env = make_error(
        ErrorCode.UPSTREAM_FAILURE,
        Provider.AVIATIONSTACK,
        f"failure context: key={fake_av_key} user={fake_user} pass={fake_pass}",
    )
    for credential in (fake_av_key, fake_user, fake_pass):
        assert credential not in env.detail, (
            f"{credential!r} leaked into envelope.detail: {env.detail!r}"
        )


# --- 3: provider field is one of {aviationstack, opensky, none} ------------


def test_provider_enum_has_only_three_documented_values():
    assert {p.value for p in Provider} == {"aviationstack", "opensky", "none"}


def test_error_code_enum_has_only_ad5_codes():
    assert {c.value for c in ErrorCode} == {
        "NO_MATCH",
        "DATA_UNAVAILABLE",
        "QUOTA_LIMIT",
        "UPSTREAM_FAILURE",
    }


@pytest.mark.parametrize("provider", list(Provider))
@pytest.mark.parametrize("code", list(ErrorCode))
def test_make_error_emits_envelope_with_documented_field_values(provider, code):
    """Cartesian sanity check - every (code, provider) pair produces a
    well-formed envelope. The dataclass typing already enforces this; the
    test pins it as a regression guard."""
    env = make_error(code, provider, "synthetic")
    assert env.code == code
    assert env.provider == provider
    assert env.code.value in {"NO_MATCH", "DATA_UNAVAILABLE", "QUOTA_LIMIT", "UPSTREAM_FAILURE"}
    assert env.provider.value in {"aviationstack", "opensky", "none"}


def test_envelope_to_dict_provider_field_is_documented():
    """Wire-shape contract - the dict's provider key is exactly one of three values."""
    for provider in Provider:
        env = make_error(ErrorCode.UPSTREAM_FAILURE, provider, "x")
        wire = envelope_to_dict(env)
        assert wire["error"]["provider"] in {"aviationstack", "opensky", "none"}
