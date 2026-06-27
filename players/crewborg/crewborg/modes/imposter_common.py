"""Shared geometry + crew helpers for the imposter modes (design §7.2).

The imposter stances — Search (the always-on seeking FSM), Hunt, Recon, and Evade —
lean on the same primitives: self position, squared distance, locating the room a
point sits in, the starting/spawn room, task-station anchors, snapping a point to a
reachable nav node, and the set of crewmates visible this tick. They live here so each
mode stays focused on its own behavior rather than re-deriving map/roster geometry.

(Historical note: these helpers originally backed a single "Pretend" FSM that was
retired 2026-06-24 and split into Search/Recon/Evade/Hunt — see ``modes/_deprecated/``.
The helpers survived the split unchanged; only the callers changed.)

Collaborators
-------------
Relies on:
  - ``map.types.Room`` — the rect type for room containment tests.
  - ``types.Belief`` — map / nav graph / roster / tick fields read here.
  - ``belief.nav`` — ``nearest_reachable_node`` / ``node_point`` / ``task_anchor``
    (reachability snapping and baked task anchors).
Used by: ``modes.search`` / ``modes.hunt`` / ``modes.recon`` / ``modes.evade`` (imported
  as ``ic``).
Emits: nothing — pure functions, no intents and no side effects.

Modifying this file: these are read-only belief queries. Keep them pure (no mutation of
belief, no intents); behavioral decisions belong in the mode files, action in
``action.py``.
"""

from __future__ import annotations

from crewborg.map.types import Room
from crewborg.types import Belief, PlayerRecord

Point = tuple[int, int]


def self_xy(belief: Belief) -> Point | None:
    """Our own world position, or ``None`` until the first self-position signal."""
    if belief.self_world_x is None or belief.self_world_y is None:
        return None
    return belief.self_world_x, belief.self_world_y


def dist2(a: Point, b: Point) -> int:
    """Squared Euclidean distance in world px (cheap; avoids the sqrt for comparisons)."""
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def in_rect(point: Point, rect: Room) -> bool:
    """Whether ``point`` lies in ``rect`` (half-open: left/top inclusive, right/bottom exclusive)."""
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
