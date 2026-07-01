"""The meeting LLM gates Bedrock on the sidecar endpoint, like the commander.

Sidecar mode strips USE_BEDROCK and injects AWS_ENDPOINT_URL_BEDROCK_RUNTIME, so the
meeting client must treat that endpoint as a Bedrock signal too (else meetings silently
fall back to deterministic in-pod). See docs/reference/coworld-platform.md.
"""

from __future__ import annotations

from crewborg.strategy.meeting.llm import _sidecar_bedrock, build_meeting_llm_client_from_env


def test_sidecar_endpoint_is_a_bedrock_signal():
    assert _sidecar_bedrock({"AWS_ENDPOINT_URL_BEDROCK_RUNTIME": "http://localhost:4000"}) is True
    assert _sidecar_bedrock({}) is False
    assert _sidecar_bedrock({"AWS_ENDPOINT_URL_BEDROCK_RUNTIME": "   "}) is False


def test_sidecar_endpoint_alone_passes_the_backend_gate():
    client = build_meeting_llm_client_from_env(
        {"CREWBORG_LLM_MEETINGS": "1", "AWS_ENDPOINT_URL_BEDROCK_RUNTIME": "http://localhost:4000"}
    )
    assert client.disabled_reason != "no LLM backend configured"


def test_no_signals_still_reports_no_backend():
    client = build_meeting_llm_client_from_env({"CREWBORG_LLM_MEETINGS": "1"})
    assert client.enabled is False
    assert client.disabled_reason == "no LLM backend configured"
