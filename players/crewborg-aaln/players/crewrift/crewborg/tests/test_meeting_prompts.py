"""Role-specialized meeting system prompt assembly + client wiring."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from players.crewrift.crewborg.strategy.meeting import build_system_prompt
from players.crewrift.crewborg.strategy.meeting.llm import AnthropicMeetingClient, MeetingLLMConfig
from players.crewrift.crewborg.strategy.meeting.prompts import (
    IMPOSTER_STRATEGY,
    SHARED_BOILERPLATE,
    resolve_role,
)

# An imposter-only phrase that must never leak into a non-imposter prompt.
_IMPOSTER_TELL = "Blend in"


def test_shared_contract_is_present_for_every_role() -> None:
    for role in ("crewmate", "imposter", None, "dead", "spectator"):
        assert SHARED_BOILERPLATE in build_system_prompt(role)


def test_crewmate_prompt_has_crewmate_strategy_and_no_imposter_tactics() -> None:
    prompt = build_system_prompt("crewmate")
    assert "You are a crewmate." in prompt
    assert "state.fallback_vote" in prompt  # crewmate strategy tier
    assert _IMPOSTER_TELL not in prompt
    assert "fellow imposters" not in prompt


def test_imposter_prompt_has_imposter_goals_and_strategy() -> None:
    prompt = build_system_prompt("imposter")
    assert "You are an imposter." in prompt
    assert _IMPOSTER_TELL in prompt
    # The teammate-protection rule is the whole point of the imposter prompt.
    assert "self.teammates" in prompt
    assert IMPOSTER_STRATEGY in prompt


def test_unknown_and_ghost_roles_default_to_crewmate() -> None:
    for role in (None, "dead", "unknown", "spectator", ""):
        assert resolve_role(role) == "crewmate"
        # Safe default: never disclose imposter tactics to a non-imposter.
        assert _IMPOSTER_TELL not in build_system_prompt(role)
    assert resolve_role("imposter") == "imposter"
    assert resolve_role("crewmate") == "crewmate"


class _RecordingAnthropic:
    """Minimal Anthropic stand-in that records the system prompt it is given."""

    def __init__(self, decision_json: str) -> None:
        self.captured: dict[str, Any] = {}
        self.messages = self
        self._decision_json = decision_json

    def create(self, **kwargs: Any) -> Any:
        self.captured = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=self._decision_json)], usage=None)


def _context_with_role(role: str | None) -> dict[str, Any]:
    return {"self": {"role": role}, "meeting": {"tick": 0}}


def test_client_selects_prompt_from_context_role() -> None:
    fake = _RecordingAnthropic('{"schema_version":1,"action":"wait"}')
    client = AnthropicMeetingClient(MeetingLLMConfig(), client=fake)

    client.decide(_context_with_role("imposter"), trigger="meeting_start")
    assert fake.captured["system"] == build_system_prompt("imposter")
    assert _IMPOSTER_TELL in fake.captured["system"]

    client.decide(_context_with_role("crewmate"), trigger="meeting_start")
    assert fake.captured["system"] == build_system_prompt("crewmate")
    assert _IMPOSTER_TELL not in fake.captured["system"]
