"""Consumption helpers for gameplay-commander priorities.

The modes use these helpers so staleness and "bias, don't force" fallback rules
stay in one place as commander priorities are wired into behavior.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from crewborg.types import Belief, CommanderPriorities

T = TypeVar("T")

# About 10 seconds at 24 Hz. A stalled worker should degrade to default behavior,
# not keep steering from an old phase.
COMMANDER_TTL_TICKS = 240


def commander_of(belief: Belief) -> CommanderPriorities | None:
    commander = belief.commander
    if commander is None:
        return None
    if belief.last_tick - commander.as_of_tick > COMMANDER_TTL_TICKS:
        return None
    return commander


def filter_or_fallback(candidates: list[T], predicate: Callable[[T], bool]) -> list[T]:
    filtered = [candidate for candidate in candidates if predicate(candidate)]
    return filtered if filtered else candidates


def room_crew_count(belief: Belief, room_name: str) -> int:
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
