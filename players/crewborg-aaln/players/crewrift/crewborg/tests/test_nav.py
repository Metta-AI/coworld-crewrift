"""Nav graph + A* route planning + destination anchors (design §6, §9)."""

from __future__ import annotations

import numpy as np

from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect, TaskStation, Vent
from players.crewrift.crewborg.nav import (
    CLEARANCE_RADIUS,
    _segment_clear,
    build_nav_graph,
    plan_route,
    plan_route_via_vents,
)


def test_partially_blocked_cell_is_still_a_node() -> None:
    # With 1x1 point collision, one blocked pixel must NOT discard the whole cell
    # (the old conservative rule did exactly that). The cell stays routable.
    mask = np.ones((16, 16), dtype=bool)
    mask[0, 0] = False
    graph = build_nav_graph(mask, cell_size=8)
    assert (0, 0) in graph.node_point  # cell survives despite the blocked pixel


def test_fully_blocked_cell_is_not_a_node() -> None:
    mask = np.ones((16, 16), dtype=bool)
    mask[0:8, 0:8] = False  # top-left cell has no walkable pixel
    graph = build_nav_graph(mask, cell_size=8)
    assert (0, 0) not in graph.node_point
    assert (1, 1) in graph.node_point


def test_node_points_keep_clearance_from_walls() -> None:
    # A wall on the left third; the node in the adjacent cell sits CLEARANCE_RADIUS
    # off the wall (corridor-centered), not flush against it.
    mask = np.ones((24, 24), dtype=bool)
    mask[:, 0:8] = False
    graph = build_nav_graph(mask, cell_size=8)
    pt = graph.node_point.get((1, 1))  # cell x8..15, y8..15 — adjacent to the wall
    assert pt is not None
    assert bool(graph.clearance[pt[1], pt[0]])  # the node point keeps clearance
    assert pt[0] >= 8 + CLEARANCE_RADIUS  # off the wall edge at x=8


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


def test_clear_shot_collapses_to_a_single_waypoint() -> None:
    mask = np.ones((64, 64), dtype=bool)
    graph = build_nav_graph(mask, cell_size=8)
    assert plan_route(graph, (4, 4), (60, 60)) == [(60, 60)]


def test_smoothed_route_segments_never_cross_a_wall() -> None:
    mask = np.ones((24, 80), dtype=bool)
    mask[8:, 32:40] = False
    graph = build_nav_graph(mask, cell_size=8)

    start = (8, 16)
    route = plan_route(graph, start, (72, 16))
    assert route and route[-1] == (72, 16)
    # Every leg up to (but excluding) the final exact-goal hop is occlusion-free.
    legs = [start] + route
    for a, b in zip(legs[:-2], legs[1:-1]):
        assert _segment_clear(graph.walkability, a, b), f"leg {a}->{b} crosses the wall"


def test_line_of_sight_blocks_a_diagonal_corner_squeeze() -> None:
    mask = np.ones((16, 16), dtype=bool)
    mask[0:8, 8:16] = False  # top-right pixels blocked
    mask[8:16, 0:8] = False  # bottom-left pixels blocked
    graph = build_nav_graph(mask, cell_size=8)
    # (4,4) -> (12,12) grazes the shared corner of the two blocked quadrants.
    assert not _segment_clear(graph.walkability, (4, 4), (12, 12))


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


def test_unreachable_task_has_no_anchor_and_is_reported() -> None:
    mask = np.ones((24, 48), dtype=bool)
    mask[:, 24:32] = False  # wall splits the map; home is on the left
    task = TaskStation(name="far", x=40, y=10, w=4, h=4)  # right of the wall
    graph = build_nav_graph(mask, map_data=_map(tasks=[task], home=MapPoint(x=4, y=12)))

    assert graph.task_anchor(0) is None
    assert any("task[0]" in w for w in graph.unreachable)


def test_vent_anchor_lands_within_reach_of_the_vent_center() -> None:
    mask = np.ones((24, 48), dtype=bool)
    vent = Vent(x=18, y=8, w=8, h=8, group="1", group_index=1)  # center (22, 12)
    graph = build_nav_graph(mask, map_data=_map(vents=[vent]))

    anchor = graph.vent_anchor(0)
    assert anchor is not None
    (ax, ay), (cx, cy) = anchor, (22, 12)
    assert (ax - cx) ** 2 + (ay - cy) ** 2 <= 16**2  # within VentRange of the center


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


def test_a_solitary_vent_has_no_teleport_edge() -> None:
    mask = np.ones((48, 48), dtype=bool)
    vent = Vent(x=8, y=8, w=8, h=8, group="lonely", group_index=1)
    graph = build_nav_graph(mask, map_data=_map(vents=[vent]))
    assert graph.vent_edges == {}  # nowhere to teleport to


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


def test_plain_plan_route_never_teleports() -> None:
    # The vent-free planner must detour up through the gap, not jump the wall.
    mask, map_data = _detour_map_with_linking_vents()
    graph = build_nav_graph(mask, map_data=map_data)
    route = plan_route(graph, (10, 110), (190, 110))
    assert route and route[-1] == (190, 110)
    assert any(y < 20 for _, y in route)  # routed up through the top gap
