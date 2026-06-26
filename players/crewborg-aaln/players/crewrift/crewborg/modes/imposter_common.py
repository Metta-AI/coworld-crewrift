"""Shared geometry + crew helpers for the imposter Pretend FSM (design §7.2).

Pretend's follow / recover / wander / fake-task states lean on the same primitives:
locating the room a point sits in, the starting room, task-station anchors, snapping
to a reachable nav node, and the set of crewmates visible this tick. They live here
so the mode stays focused on its state machine.
"""

from __future__ import annotations

from players.crewrift.crewborg.map.types import Room
from players.crewrift.crewborg.types import Belief, PlayerRecord

Point = tuple[int, int]


def self_xy(belief: Belief) -> Point | None:
    if belief.self_world_x is None or belief.self_world_y is None:
        return None
    return belief.self_world_x, belief.self_world_y


def dist2(a: Point, b: Point) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def in_rect(point: Point, rect: Room) -> bool:
    return rect.x <= point[0] < rect.x + rect.w and rect.y <= point[1] < rect.y + rect.h


def room_containing(belief: Belief, point: Point) -> Room | None:
    """The room whose rect strictly contains ``point``, or ``None`` (e.g. a hallway)."""

    rooms = belief.map.rooms if belief.map is not None else ()
    for room in rooms:
        if in_rect(point, room):
            return room
    return None


def starting_room(belief: Belief) -> Room | None:
    """The room containing the spawn (``home``); never a fake-task location."""

    if belief.map is None:
        return None
    home = (belief.map.home.x, belief.map.home.y)
    return room_containing(belief, home)


def reachable_point(belief: Belief, point: Point) -> Point:
    """Snap ``point`` to the nearest reachable nav node (a stable, walkable goal)."""

    if belief.nav is None:
        return point
    cell = belief.nav.nearest_reachable_node(*point)
    if cell is None:
        return point
    return belief.nav.node_point[cell]


def task_point(belief: Belief, index: int) -> Point:
    """A task station's baked reachable anchor, or its centre before the graph exists."""

    if belief.nav is not None:
        anchor = belief.nav.task_anchor(index)
        if anchor is not None:
            return anchor
    task = belief.map.tasks[index]
    return task.center.x, task.center.y


def visible_crew(belief: Belief) -> list[PlayerRecord]:
    """Live non-teammate players seen this very tick (the candidates to follow)."""

    return [
        e
        for e in belief.roster.values()
        if e.last_seen_tick == belief.last_tick
        and e.color not in belief.teammate_colors
        and e.life_status != "dead"
    ]
