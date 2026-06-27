"""Nav graph + A* route planning + destination anchors (design §6, §9)."""

from __future__ import annotations

import numpy as np

from crewborg.map.types import MapData, MapPoint, MapRect, TaskStation, Vent
from crewborg.nav import (
    build_nav_graph,
    plan_route,
    plan_route_via_vents,
)


def test_route_keeps_clearance_around_a_wall() -> None:
    # A bar hanging from the top; the only crossing is the gap along the bottom.
    # The route must dip into the gap but should keep clearance from the bar, not
    # graze it (which is what wedged the agent before).
    mask = np.ones((24, 60), dtype=bool)
    mask[0:16, 28:32] = False
    graph = build_nav_graph(mask, cell_size=8)
    route = plan_route(graph, (8, 20), (50, 20))
    assert route and route[-1] == (50, 20)
    for x, y in route:
        assert bool(graph.clearance[y, x]), f"waypoint ({x},{y}) hugs a wall (no clearance)"


def test_route_goes_around_a_wall() -> None:
    mask = np.ones((24, 80), dtype=bool)
    mask[8:, 32:40] = False  # vertical wall on the lower rows; gap stays open up top
    graph = build_nav_graph(mask, cell_size=8)

    route = plan_route(graph, (8, 16), (72, 16))
    assert route, "expected a route around the wall"
    assert route[-1] == (72, 16)  # ends exactly on the goal
    assert any(y < 8 for _, y in route)  # detours up through the gap


def test_unreachable_goal_returns_empty() -> None:
    mask = np.ones((24, 48), dtype=bool)
    mask[:, 24:32] = False  # full-height wall splits the map in two
    graph = build_nav_graph(mask, cell_size=8)
    assert plan_route(graph, (8, 12), (40, 12)) == []


# --------------------------------------------------------------------------- #
# Destination anchors                                                         #
# --------------------------------------------------------------------------- #


def _map(tasks=(), vents=(), button=MapRect(x=0, y=0, w=4, h=4), home=MapPoint(x=4, y=4)) -> MapData:
    return MapData(width=48, height=24, tasks=tuple(tasks), vents=tuple(vents), rooms=(), button=button, home=home)


def test_task_anchor_is_a_reachable_walkable_pixel_in_the_rect() -> None:
    # A task rect that straddles a wall: its geometric center sits in the wall, but
    # the anchor must be a walkable, reachable pixel inside the rect.
    mask = np.ones((24, 48), dtype=bool)
    mask[:, 10:14] = False  # wall band through the task rect's center column
    task = TaskStation(name="edge", x=8, y=8, w=8, h=8)  # center (12, 12) — in the wall
    graph = build_nav_graph(mask, map_data=_map(tasks=[task]))

    anchor = graph.task_anchor(0)
    assert anchor is not None
    ax, ay = anchor
    assert 8 <= ax < 16 and 8 <= ay < 16  # inside the rect
    assert mask[ay, ax]  # on a walkable pixel
    assert not (10 <= ax < 14)  # not in the wall band


# --------------------------------------------------------------------------- #
# Vent teleport edges + vent-aware routing (imposter flee)                    #
# --------------------------------------------------------------------------- #


def test_vent_edges_connect_same_group_reachable_vents() -> None:
    mask = np.ones((48, 48), dtype=bool)
    vents = [
        Vent(x=8, y=8, w=8, h=8, group="g", group_index=1),  # center (12, 12)
        Vent(x=32, y=32, w=8, h=8, group="g", group_index=2),  # center (36, 36)
    ]
    graph = build_nav_graph(mask, map_data=_map(vents=vents))
    pairs = {(e.from_vent, e.to_vent) for edges in graph.vent_edges.values() for e in edges}
    assert pairs == {(0, 1), (1, 0)}  # a teleport edge each way


def _detour_map_with_linking_vents() -> tuple[np.ndarray, MapData]:
    # A wall splits left from right except for a narrow gap along the top rows, so
    # walking across is a long detour — but two same-group vents link the sides.
    mask = np.ones((120, 200), dtype=bool)
    mask[20:120, 98:102] = False  # wall from row 20 down; gap is rows 0..20
    vents = [
        Vent(x=86, y=56, w=8, h=8, group="g", group_index=1),  # center (90, 60), left of wall
        Vent(x=106, y=56, w=8, h=8, group="g", group_index=2),  # center (110, 60), right of wall
    ]
    map_data = MapData(
        width=200, height=120, tasks=(), vents=tuple(vents), rooms=(),
        button=MapRect(x=0, y=0, w=4, h=4), home=MapPoint(x=10, y=10),
    )
    return mask, map_data


def test_plan_route_via_vents_takes_the_teleport_shortcut() -> None:
    mask, map_data = _detour_map_with_linking_vents()
    graph = build_nav_graph(mask, map_data=map_data)

    waypoints, teleports = plan_route_via_vents(graph, (10, 110), (190, 110))
    assert waypoints, "expected a route to the far side"
    assert teleports, "expected the route to use a vent teleport"
    # The teleport target waypoint lands within VentRange of a vent center.
    centers = [(90, 60), (110, 60)]
    for index in teleports:
        wx, wy = waypoints[index]
        assert any((wx - cx) ** 2 + (wy - cy) ** 2 <= 16**2 for cx, cy in centers)
