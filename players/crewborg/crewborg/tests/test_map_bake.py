"""Static-map bake tests against the vendored croatoan.resources (design §6)."""

from __future__ import annotations

from crewborg.map import (
    DEFAULT_MAP_HEIGHT,
    DEFAULT_MAP_WIDTH,
    bake_map,
    load_croatoan_map,
    walkability_matches,
)
from crewborg.map.parser import load_resource_rects

CROATOAN = """
/* vents */
width: 714px;
height: 470px;
left: 222px;
top: 108px;

/* vent4 */
width: 14px;
height: 14px;
left: 733px;
top: 334px;
background: rgba(255, 0, 0, 0.4);

/* vent4 */
width: 14px;
height: 14px;
left: 922px;
top: 479px;
background: rgba(255, 0, 0, 0.4);

/* task */
width: 20px;
height: 20px;
left: 100px;
top: 100px;
background: #00ff00;

/* task */
width: 20px;
height: 20px;
left: 900px;
top: 400px;
background: #00ff00;

/* Bridge */
width: 100px;
height: 80px;
left: 120px;
top: 300px;
background: #102030;
"""


def test_parser_drops_blocks_without_a_color() -> None:
    rects = load_resource_rects(CROATOAN)
    # The "vents" container has no background color, so it is dropped.
    assert all(r.name != "vents" for r in rects)
    assert sorted(r.name for r in rects) == ["Bridge", "task", "task", "vent4", "vent4"]


def test_bake_classifies_tasks_vents_rooms_and_button() -> None:
    data = bake_map(load_resource_rects(CROATOAN))

    # Tasks preserve file order (= the stream's 3000/7000 + idx index).
    assert len(data.tasks) == 2
    assert (data.tasks[0].x, data.tasks[0].y) == (100, 100)
    assert (data.tasks[1].x, data.tasks[1].y) == (900, 400)

    # Two vents share group "4" with serial group indices.
    assert len(data.vents) == 2
    assert {v.group for v in data.vents} == {"4"}
    assert sorted(v.group_index for v in data.vents) == [1, 2]

    assert [r.name for r in data.rooms] == ["Bridge"]

    # Button is the 28x34 rect centered on the Bridge room center (170, 340).
    assert (data.button.w, data.button.h) == (28, 34)
    assert data.button.center.x == 170 and data.button.center.y == 340


def test_croatoan_bake_is_self_consistent() -> None:
    data = load_croatoan_map()
    assert (data.width, data.height) == (DEFAULT_MAP_WIDTH, DEFAULT_MAP_HEIGHT)
    assert len(data.tasks) > 0 and len(data.vents) > 0 and len(data.rooms) > 0
    # The emergency button is derived from the Bridge room and clamped to the map.
    assert any(r.name.lower() == "bridge" for r in data.rooms)
    assert 0 <= data.button.x <= DEFAULT_MAP_WIDTH - data.button.w
    assert 0 <= data.button.y <= DEFAULT_MAP_HEIGHT - data.button.h


def test_walkability_matches_checks_map_dimensions() -> None:
    data = load_croatoan_map()
    assert walkability_matches(data, DEFAULT_MAP_WIDTH, DEFAULT_MAP_HEIGHT)
    assert not walkability_matches(data, DEFAULT_MAP_WIDTH, DEFAULT_MAP_HEIGHT + 1)
