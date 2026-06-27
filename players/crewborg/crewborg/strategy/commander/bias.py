"""Consumption helpers the modes use to READ gameplay-commander priorities.

The READ side of the commander seam (the WRITE side is ``strategy.py`` /
``worker.py``). Modes call these instead of touching ``belief.commander`` directly,
so the staleness gate and the "bias, don't force" fallback rules live in one place.
``commander_of`` is the single accessor — if it returns ``None`` (feature off,
unset, or stale), the mode falls back to pure deterministic behavior, which is what
keeps "commander off = inert".

Collaborators
-------------
Relies on:
  - ``types.Belief`` / ``types.CommanderPriorities`` — the priority payload and the
    ``last_tick`` / ``as_of_tick`` clock used for the TTL.
  - ``belief.map`` rooms + ``belief.roster`` / ``belief.teammate_colors`` for the
    occupancy count.
Used by (the modes that consult priorities):
  - ``modes.hunt`` / ``modes.recon`` — ``commander_of`` (target_player, danger levers).
  - ``modes.search`` — ``commander_of`` + ``filter_or_fallback`` (hunt_room/avoid_room).
  - ``modes.normal`` — ``commander_of`` + ``room_crew_count`` (target_room/task/posture).
  - ``strategy.rule_based`` — ``commander_of`` (the ``skip_evade`` danger lever).

Modifying this file: these are pure readers — they never mutate belief and never
force an impossible choice. ``commander_of``'s TTL and ``filter_or_fallback``'s
"keep all when the filter empties" are the two guarantees the modes rely on; keep
them or every consuming mode's fallback assumption breaks.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from crewborg.types import Belief, CommanderPriorities

T = TypeVar("T")

# Staleness horizon for a commander payload, in ticks. About 10 seconds at 24 Hz.
# A stalled/slow worker should degrade to default behavior rather than keep steering
# from an old game phase, so priorities older than this are treated as absent.
COMMANDER_TTL_TICKS = 240


def commander_of(belief: Belief) -> CommanderPriorities | None:
    """Return the currently-applicable commander priorities, or ``None``.

    ``None`` is returned when the feature is off / nothing has been published yet
    (``belief.commander is None``) OR the installed payload is older than
    ``COMMANDER_TTL_TICKS`` (``last_tick - as_of_tick`` exceeds the horizon). The
    single gate every mode goes through: a ``None`` here means "behave exactly as
    the deterministic agent would". Pure read; never mutates belief."""
    commander = belief.commander
    if commander is None:
        return None
    if belief.last_tick - commander.as_of_tick > COMMANDER_TTL_TICKS:
        return None
    return commander


def filter_or_fallback(candidates: list[T], predicate: Callable[[T], bool]) -> list[T]:
    """Keep candidates matching ``predicate``, but fall back to the full list if none match.

    Encodes "bias, don't force": a commander preference (e.g. avoid_room) narrows the
    candidate set when it can, but never empties it — so the caller always has at least
    the original options to choose from. Preserves input order."""
    filtered = [candidate for candidate in candidates if predicate(candidate)]
    return filtered if filtered else candidates


def room_crew_count(belief: Belief, room_name: str) -> int:
    """Count visible, live, non-teammate crew currently inside the named room.

    Used by posture biasing (stick toward crowded rooms / isolate toward empty ones).
    Counts only roster entries seen this tick (``last_seen_tick == last_tick``), so it
    reflects believed-present crew, not historical positions. Returns 0 if the map is
    unbaked or the room name is unknown (an absent/unknown room is treated as empty)."""
    if belief.map is None:
        return 0
    room = next((candidate for candidate in belief.map.rooms if candidate.name == room_name), None)
    if room is None:
        return 0
    return sum(
        1
        for crew in belief.roster.values()
        if crew.last_seen_tick == belief.last_tick
        and crew.color not in belief.teammate_colors
        and crew.life_status != "dead"
        and room.x <= crew.world_x < room.x + room.w
        and room.y <= crew.world_y < room.y + room.h
    )
