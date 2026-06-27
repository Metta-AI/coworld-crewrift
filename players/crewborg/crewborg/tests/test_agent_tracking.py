"""Agent location tracking tests (docs/agent-tracking.md).

Covers the occupancy estimator (the reachability-disc location belief under
negative line-of-sight) and the densest-crew readouts consumed by the live Evade
mode (``best_seek_point`` / ``best_pretend_room_target``).
"""

from __future__ import annotations

import numpy as np

from crewborg.agent_tracking import (
    OccupancySnapshot,
    best_pretend_room_target,
    best_seek_point,
    update_agent_tracking,
)
from crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation
from crewborg.nav import build_nav_graph
from crewborg.types import Belief, PerceptionFrame, PlayerRecord


def _map() -> MapData:
    return MapData(
        width=128,
        height=64,
        tasks=(
            TaskStation(name="left", x=16, y=16, w=8, h=8),
            TaskStation(name="right", x=96, y=16, w=8, h=8),
        ),
        vents=(),
        rooms=(
            Room(name="Left", x=0, y=0, w=64, h=64),
            Room(name="Right", x=64, y=0, w=64, h=64),
        ),
        button=MapRect(x=4, y=48, w=8, h=8),
        home=MapPoint(x=8, y=8),
    )


def _belief() -> Belief:
    map_data = _map()
    nav = build_nav_graph(np.ones((map_data.height, map_data.width), dtype=bool), map_data=map_data)
    return Belief(map=map_data, nav=nav, self_role="imposter", self_world_x=8, self_world_y=8)


def test_tracker_collapses_visible_agents_then_sweeps_visible_empty_cells() -> None:
    belief = _belief()
    belief.last_tick = 10
    belief.roster["green"] = PlayerRecord(color="green", world_x=16, world_y=16, last_seen_tick=10, life_status="alive")
    belief.recent_frames.append(
        PerceptionFrame(tick=10, camera_x=0, camera_y=0, players={"green": (16, 16)})
    )
    update_agent_tracking(belief)

    observed = belief.agent_tracking.estimates["green"]
    assert observed.observed_this_tick is True
    assert observed.support_cell_count == 1

    # Next tick, the left half of the map is in line of sight and green is absent.
    # Negative LoS removes that swept area, so the belief moves to the unseen right.
    belief.last_tick = 11
    belief.recent_frames.append(
        PerceptionFrame(tick=11, camera_x=0, camera_y=0, visible_mask=np.ones((64, 64), dtype=bool))
    )
    update_agent_tracking(belief)

    estimate = belief.agent_tracking.estimates["green"]
    substrate = belief.agent_tracking.substrate
    assert substrate is not None
    assert estimate.observed_this_tick is False
    assert estimate.mass_by_cell
    assert all(substrate.cells[cell_id].center[0] >= 64 for cell_id in estimate.mass_by_cell)


def test_best_seek_point_reads_the_hottest_reachable_cell() -> None:
    belief = _belief()
    update_agent_tracking(belief)
    substrate = belief.agent_tracking.substrate
    assert substrate is not None
    right_cell = next(cell for cell in substrate.cells.values() if cell.center[0] >= 64)
    belief.agent_tracking.snapshot = OccupancySnapshot(
        tick=1,
        expected_by_cell={right_cell.index: 1.0},
        top_cell=right_cell.index,
        top_point=right_cell.center,
        top_expected=1.0,
        tracked_count=1,
        support_cell_count=1,
    )

    assert best_seek_point(belief, (8, 8)) == right_cell.center


def test_best_pretend_room_target_aggregates_room_density() -> None:
    belief = _belief()
    update_agent_tracking(belief)
    substrate = belief.agent_tracking.substrate
    assert substrate is not None
    right_cells = [cell for cell in substrate.cells.values() if cell.label == "Right"]
    belief.agent_tracking.snapshot = OccupancySnapshot(
        tick=1,
        expected_by_cell={cell.index: 0.25 for cell in right_cells},
        top_cell=right_cells[0].index,
        top_point=right_cells[0].center,
        top_expected=0.25,
        tracked_count=1,
        support_cell_count=len(right_cells),
    )

    target = best_pretend_room_target(belief, (8, 8))
    assert target is not None
    assert target.room_name == "Right"
    assert target.expected == 1.0
