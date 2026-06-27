"""Serialize belief into the compact gameplay state the commander LLM reasons over.

The READ-from-belief / WRITE-to-prompt boundary: turns the rich live ``Belief`` into a
small JSON object the LLM sees as ``context``. The ``legal_rooms`` / ``legal_players``
allow-lists serve double duty — they are sent to the LLM as the only valid targets it
may name, and the same sets are re-derived by ``schema.sanitize_priorities`` to reject
anything the LLM returns outside them.

Collaborators
-------------
Relies on:
  - ``types.Belief`` — phase, self pose/role, roster, bodies, map rooms.
  - ``modes.imposter_common.room_containing`` — resolve an (x, y) to its room name.
  - ``strategy.opportunity.ticks_until_kill_ready`` — kill-cooldown countdown for ``self``.
Used by:
  - ``strategy.CommanderStrategy.decide`` — builds the context each tick and publishes
    it to the worker's snapshot buffer.
  - ``llm.AnthropicCommanderClient.decide`` — the dict becomes the ``context`` field of
    the user message; ``context["self"]["role"]`` selects the system prompt.

Modifying this file: ``legal_rooms`` / ``legal_players`` are the LLM's allow-list AND
the sanitizer's validity set — keep them consistent with ``schema.py`` or the LLM will
be told one set of legal targets while a different set is enforced. Output must stay
JSON-serializable (it is sent over the wire and traced).
"""

from __future__ import annotations

from typing import Any

from crewborg.modes import imposter_common as ic
from crewborg.strategy.opportunity import ticks_until_kill_ready
from crewborg.types import Belief


def legal_rooms(belief: Belief) -> list[str]:
    """Names of every room on the baked map (the only room values the LLM may name).

    Empty list when the map is not yet baked."""
    if belief.map is None:
        return []
    return [room.name for room in belief.map.rooms]


def legal_players(belief: Belief) -> list[str]:
    """Colors the LLM may legally target: live roster members that are neither us nor a teammate.

    Excludes dead players, ``self``, and known imposter teammates — i.e. exactly the
    crewmates that are valid kill/steer targets."""
    return [
        record.color
        for record in belief.roster.values()
        if record.life_status != "dead"
        and record.color != belief.self_color
        and record.color not in belief.teammate_colors
    ]


def serialize_commander_context(belief: Belief, *, active_mode: str | None = None) -> dict[str, Any]:
    """Return the compact, JSON-serializable state the gameplay commander needs.

    Bundles phase, self pose/role/kill-readiness, the room and player allow-lists, the
    full roster (alive flag, position, room, last-seen tick), and any known bodies.
    ``active_mode`` is the deterministic mode currently selected this tick (passed
    through for the LLM's situational awareness; it does not constrain output). The
    dict is sent verbatim as the LLM ``context`` and also drives prompt-role selection
    via ``self.role``."""

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
    """Room name containing (x, y), or ``None`` if either coordinate is missing or no room matches."""
    if x is None or y is None:
        return None
    room = ic.room_containing(belief, (x, y))
    return room.name if room is not None else None
