"""Role-specialized meeting system prompt assembly + client wiring."""

from __future__ import annotations

from time import monotonic

import pytest

from players.crewrift.crewborg.native import NativeSession, PlayerRegistration
from players.crewrift.crewborg.strategy.meeting import build_system_prompt
from players.crewrift.crewborg.strategy.meeting.llm import (
    MeetingParams,
    NativeMeetingClient,
)
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


@pytest.mark.asyncio
async def test_client_selects_exact_native_prompt_from_context_role(tmp_path):
    for role in ("imposter", "crewmate"):
        owner = NativeSession(
            PlayerRegistration(requested_slot=None),
            tmp_path / f"{role}.jsonl",
            lambda generation: None,
        )
        client = NativeMeetingClient(MeetingParams(), owner)
        task = client.decide(
            {"self": {"role": role}, "meeting": {"tick": 0}}, trigger="meeting_start"
        )
        generation = next(iter(owner.generations.values()))
        assert generation.request.messages[0]["content"] == build_system_prompt(role)
        assert (_IMPOSTER_TELL in generation.request.messages[0]["content"]) == (
            role == "imposter"
        )
        assert generation.platform_call_id is None
        assert await owner.stop(monotonic() + 2)
        assert task.cancelled()
