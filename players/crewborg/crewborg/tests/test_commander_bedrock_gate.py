"""The commander stays inert unless a Bedrock backend is configured.

With the flag on but no backend signal the factory must return a disabled client
with the no-backend reason, so the agent plays its deterministic rule-based line
rather than silently attempting an LLM call. See
docs/issues/2026-06-26-bedrock-disabled-crewrift-prime-xp.md.
"""

from __future__ import annotations

from crewborg.strategy.commander.llm import build_commander_client_from_env


def test_no_signals_still_reports_no_backend():
    client = build_commander_client_from_env({"CREWBORG_LLM_COMMANDER": "1"})
    assert client.enabled is False
    assert client.disabled_reason == "no LLM backend configured"
