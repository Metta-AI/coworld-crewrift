"""Source-owned scripted policy; it sees the same private messages as native inference."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from typing import Literal

from crewborg_native import (
    NativeProfile,
    NativeSession,
    PolicyProfile,
    ScriptedGeneration,
    ScriptedProfile,
)


def policy_profile_from_env() -> PolicyProfile:
    origin = os.environ.get("CREWBORG_POLICY_ORIGIN", "native")
    if origin == "native":
        return NativeProfile(origin="native")
    if origin != "teacher":
        raise ValueError("Unsupported policy origin")
    return ScriptedProfile(
        origin="teacher",
        teacher_authority_sha256=hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
    )


def scripted_generation(
    profile: ScriptedProfile,
    session: NativeSession,
    phase: Literal["meeting", "commander"],
    prompt: list[dict[str, str]],
) -> ScriptedGeneration:
    """An immediate scripted profile, never a model/latency parity assertion."""
    started = monotonic()
    if session.shutdown_deadline is not None:
        raise RuntimeError("Cannot admit scripted decisions after player STOP")
    if (
        profile.teacher_authority_sha256
        != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    ):
        raise ValueError("Scripted authority does not match the executing source")
    slot = session.registration.assigned_slot
    assert slot is not None, "A teacher needs actual engine-assigned slot admission"
    view = json.loads(prompt[-1]["content"])
    context = view["context"]
    if phase == "meeting":
        tick = int(context["meeting"]["tick"])
        deadline = view["trigger"] == "deadline"
        chat_ready = context["constraints"]["chat_cooldown_ready"]
        decision = {
            "schema_version": 1,
            "action": "submit_vote"
            if deadline
            else "send_chat"
            if chat_ready
            else "wait",
            "chat_text": f"Please share visible evidence at tick {tick}."
            if chat_ready and not deadline
            else None,
            "vote_target": "skip" if deadline else None,
            "reason": "Request visible evidence; skip without a supported accusation.",
            "confidence": None,
        }
    else:
        tick = context["observation_tick"]
        decision = {
            "schema_version": 1,
            "target_room": None,
            "target_task": None,
            "posture": "neutral",
            "hunt_room": None,
            "target_player": None,
            "avoid_room": None,
            "allow_witnessed_kill": False,
            "skip_evade": False,
            "danger_reason": None,
            "reason": "Continue the ordinary controller without unsupported priorities.",
        }
    text = json.dumps(decision, sort_keys=True, separators=(",", ":"))
    return ScriptedGeneration(
        origin="teacher",
        teacher_authority_sha256=profile.teacher_authority_sha256,
        phase=phase,
        observation_tick=tick,
        player_slot=slot,
        prompt=prompt,
        completion_text=text,
        parsed_action=json.loads(text),
        duration_ms=int((monotonic() - started) * 1000),
    )
