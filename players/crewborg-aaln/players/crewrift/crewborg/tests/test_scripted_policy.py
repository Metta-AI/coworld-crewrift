"""Scripted labels preserve their source and ordinary parser/installation boundary."""

from __future__ import annotations

import json
from time import monotonic

import pytest
from crewborg_native import PolicyGeneration
from pydantic import TypeAdapter, ValidationError

from players.crewrift.crewborg.scripted import policy_profile_from_env
from players.crewrift.crewborg.strategy.meeting.llm import (
    build_meeting_client,
    read_meeting_params_from_env,
    render_meeting_messages,
)
from players.crewrift.crewborg.types import Intent


@pytest.mark.asyncio
async def test_scripted_meeting_is_immediate_private_evidence_not_a_model_call(
    monkeypatch, native_session
):
    monkeypatch.setenv("CREWBORG_POLICY_ORIGIN", "teacher")
    monkeypatch.setenv("CREWBORG_LLM_MEETINGS", "1")
    monkeypatch.delenv("COWORLD_LLM_ENDPOINT", raising=False)
    profile = policy_profile_from_env()
    native_session.registration.assigned_slot = 2
    client = build_meeting_client(
        read_meeting_params_from_env(policy_profile=profile),
        native_session,
        policy_profile=profile,
    )
    context = {
        "self": {"role": "crewmate"},
        "meeting": {"tick": 12},
        "constraints": {"chat_cooldown_ready": True},
    }
    pending = client.decide(context, trigger="meeting_start")
    assert pending.done()
    result = pending.result()
    assert result.generation.origin == "teacher"
    assert result.generation.prompt == render_meeting_messages(
        context, trigger="meeting_start"
    )
    assert result.generation.parsed_action == json.loads(
        result.generation.completion_text
    )
    assert result.generation.player_slot == 2
    client.installed(
        result, result.decision, Intent(kind="chat", text=result.decision.chat_text), 13
    )
    generation = TypeAdapter(PolicyGeneration).validate_json(
        native_session.path.read_text().splitlines()[-1]
    )
    assert (
        generation.origin == "teacher" and len(generation.controller_installations) == 1
    )
    serialized = generation.model_dump(mode="json")
    assert not {
        "request",
        "model",
        "platform_call_id",
        "decoder",
        "raw_response",
    }.intersection(serialized)
    reader = TypeAdapter(PolicyGeneration)
    assert reader.validate_json(generation.model_dump_json()) == generation
    with pytest.raises(ValidationError):
        reader.validate_python({**serialized, "platform_call_id": "invented"})
    client.cancel(monotonic() + 2)
    assert client.joined()
    native_session.begin_stop(monotonic() + 2)
    with pytest.raises(RuntimeError, match="STOP"):
        client.decide(context, trigger="new_chat")
