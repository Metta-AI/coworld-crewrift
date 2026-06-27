"""The meeting LLM stays inert unless a Bedrock backend is configured.

With no flag/backend signals the client must report disabled with the no-backend
reason, so meetings fall back to deterministic in-pod rather than silently
attempting an LLM call. See docs/issues/2026-06-26-bedrock-disabled-crewrift-prime-xp.md.
"""

from __future__ import annotations

from crewborg.strategy.meeting.llm import build_meeting_llm_client_from_env


def test_no_signals_still_reports_no_backend():
    client = build_meeting_llm_client_from_env({"CREWBORG_LLM_MEETINGS": "1"})
    assert client.enabled is False
    assert client.disabled_reason == "no LLM backend configured"
