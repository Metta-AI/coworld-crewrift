"""Serialize belief into explicit gameplay state for the commander LLM."""

from __future__ import annotations

from typing import Any

from crewborg.modes import imposter_common as ic
from crewborg.strategy.opportunity import ticks_until_kill_ready
from crewborg.types import Belief


def legal_rooms(belief: Belief) -> list[str]:
    if belief.map is None:
        return []
    return [room.name for room in belief.map.rooms]


def legal_players(belief: Belief) -> list[str]:
    return [
        record.color
        for record in belief.roster.values()
        if record.life_status != "dead"
        and record.color != belief.self_color
        and record.color not in belief.teammate_colors
    ]


def serialize_commander_context(belief: Belief, *, active_mode: str | None = None) -> dict[str, Any]:
    """Return the compact, JSON-serializable state the gameplay commander needs."""

    return {
        "phase": belief.phase,
        "self": {
            "role": belief.self_role,
            "color": belief.self_color,
            "x": belief.self_world_x,
            "y": belief.self_world_y,
            "room": _room_name(belief, belief.self_world_x, belief.self_world_y),
            "kill_ready": belief.self_kill_ready,
            "ticks_until_kill_ready": ticks_until_kill_ready(belief),
        },
        "legal_rooms": legal_rooms(belief),
        "legal_players": legal_players(belief),
        "roster": {
            record.color: {
                "alive": record.life_status != "dead",
                "x": record.world_x,
                "y": record.world_y,
                "room": _room_name(belief, record.world_x, record.world_y),
                "last_seen_tick": record.last_seen_tick,
            }
            for record in belief.roster.values()
        },
        "bodies": [
            {
                "color": body.color,
                "x": body.world_x,
                "y": body.world_y,
                "room": _room_name(belief, body.world_x, body.world_y),
            }
            for body in belief.bodies.values()
        ],
        "active_mode": active_mode,
    }


def _room_name(belief: Belief, x: int | None, y: int | None) -> str | None:
    if x is None or y is None:
        return None
    room = ic.room_containing(belief, (x, y))
    return room.name if room is not None else None
