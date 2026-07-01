"""Validate raw commander LLM JSON against the current legal game state."""

from __future__ import annotations

from typing import Any

from crewborg.types import CommanderPriorities

_VALID_POSTURES = {"stick", "isolate", "neutral"}
_VALID_STRENGTHS = {"soft", "hard"}


def sanitize_priorities(
    raw: dict[str, Any],
    legal_rooms: set[str],
    legal_players: set[str],
    *,
    as_of_tick: int,
) -> CommanderPriorities:
    """Return a safe commander payload, dropping invalid or stale-by-construction fields."""

    danger_reason = raw.get("danger_reason")
    has_danger_reason = isinstance(danger_reason, str) and bool(danger_reason.strip())
    allow_witnessed_kill = bool(raw.get("allow_witnessed_kill")) and has_danger_reason
    skip_evade = bool(raw.get("skip_evade")) and has_danger_reason

    target_task = raw.get("target_task")
    if type(target_task) is not int:
        target_task = None

    posture = raw.get("posture")
    if posture not in _VALID_POSTURES:
        posture = "neutral"
    strength = raw.get("strength")
    if strength not in _VALID_STRENGTHS:
        strength = "soft"

    return CommanderPriorities(
        target_room=_legal_string(raw.get("target_room"), legal_rooms),
        target_task=target_task,
        posture=posture,
        strength=strength,
        hunt_room=_legal_string(raw.get("hunt_room"), legal_rooms),
        target_player=_legal_string(raw.get("target_player"), legal_players),
        avoid_room=_legal_string(raw.get("avoid_room"), legal_rooms),
        allow_witnessed_kill=allow_witnessed_kill,
        skip_evade=skip_evade,
        danger_reason=danger_reason.strip() if (allow_witnessed_kill or skip_evade) else None,
        reason=raw.get("reason") if isinstance(raw.get("reason"), str) else None,
        as_of_tick=as_of_tick,
    )


def _legal_string(value: Any, legal_values: set[str]) -> str | None:
    return value if isinstance(value, str) and value in legal_values else None
