"""The commander gates Bedrock on what the pod actually injects.

Sidecar mode strips USE_BEDROCK and injects COWORLD_LLM_ENDPOINT instead, so
the commander must treat that endpoint as a Bedrock signal (not rely on USE_BEDROCK).
See docs/reference/coworld-platform.md.
"""

from __future__ import annotations

from crewborg.strategy.commander.llm import _sidecar_llm, build_commander_client_from_env
from crewborg.strategy.commander.worker import _env_seen


def test_sidecar_endpoint_is_a_bedrock_signal():
    assert _sidecar_llm({"COWORLD_LLM_ENDPOINT": "http://localhost:4000"}) is True
    assert _sidecar_llm({}) is False
    assert _sidecar_llm({"COWORLD_LLM_ENDPOINT": "   "}) is False


def test_sidecar_endpoint_alone_passes_the_backend_gate():
    # With the sidecar endpoint but NO USE_BEDROCK (the in-pod reality), the factory must
    # get past the "no LLM backend configured" gate. (It may still fail to construct a real
    # client in a credential-less unit env, but NOT with the no-backend reason.)
    client = build_commander_client_from_env(
        {"CREWBORG_LLM_COMMANDER": "1", "COWORLD_LLM_ENDPOINT": "http://localhost:4000"}
    )
    assert client.disabled_reason != "no LLM backend configured"


def test_no_signals_still_reports_no_backend():
    client = build_commander_client_from_env({"CREWBORG_LLM_COMMANDER": "1"})
    assert client.enabled is False
    assert client.disabled_reason == "no LLM backend configured"


def test_env_seen_reports_the_sidecar_endpoint():
    assert "COWORLD_LLM_ENDPOINT" in _env_seen()
