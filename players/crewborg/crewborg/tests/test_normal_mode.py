"""Normal mode: task selection + completion detection (design §7.1)."""

from __future__ import annotations

import numpy as np

from crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation
from crewborg.modes import NormalMode
from crewborg.nav import build_nav_graph
from crewborg.types import ActionState, Belief, CommanderPriorities, PlayerRecord


def _map_with_tasks() -> MapData:
    return MapData(
        width=1000,
        height=1000,
        tasks=(
            TaskStation(name="near", x=100, y=100, w=20, h=20),  # center (110, 110)
            TaskStation(name="far", x=500, y=500, w=20, h=20),  # center (510, 510)
        ),
        vents=(),
        rooms=(),
        button=MapRect(x=0, y=0, w=28, h=34),
        home=MapPoint(x=0, y=0),
    )


def _roomed_map() -> MapData:
    return MapData(
        width=300,
        height=100,
        tasks=(
            TaskStation(name="left", x=40, y=40, w=8, h=8),
            TaskStation(name="mid", x=140, y=40, w=8, h=8),
            TaskStation(name="right", x=240, y=40, w=8, h=8),
        ),
        vents=(),
        rooms=(
            Room(name="Left", x=0, y=0, w=100, h=100),
            Room(name="Mid", x=100, y=0, w=100, h=100),
            Room(name="Right", x=200, y=0, w=100, h=100),
        ),
        button=MapRect(x=0, y=0, w=8, h=8),
        home=MapPoint(x=0, y=0),
    )


def _crew(belief: Belief, color: str, xy: tuple[int, int]) -> None:
    belief.roster[color] = PlayerRecord(
        color=color,
        world_x=xy[0],
        world_y=xy[1],
        last_seen_tick=belief.last_tick,
        life_status="alive",
    )


def test_picks_nearest_incomplete_assigned_task() -> None:
    belief = Belief(
        map=_map_with_tasks(),
        assigned_task_indices={0, 1},
        visible_task_indices={0, 1},
        self_world_x=490,
        self_world_y=490,  # nearest to task 1's center (510, 510)
    )
    intent = NormalMode().decide(belief, ActionState())
    assert intent.kind == "complete_task" and intent.task_index == 1


def test_commander_target_room_bias_picks_reachable_task_in_that_room() -> None:
    belief = Belief(
        map=_roomed_map(),
        assigned_task_indices={0, 1, 2},
        visible_task_indices={0, 1, 2},
        self_world_x=145,
        self_world_y=44,
        last_tick=10,
        commander=CommanderPriorities(target_room="Right", as_of_tick=10),
    )

    intent = NormalMode().decide(belief, ActionState())

    assert intent.kind == "complete_task" and intent.task_index == 2


def test_hard_commander_target_room_completes_candidate_task_in_that_room() -> None:
    belief = Belief(
        map=_roomed_map(),
        assigned_task_indices={0, 1, 2},
        visible_task_indices={0, 1, 2},
        self_world_x=145,
        self_world_y=44,
        last_tick=10,
        commander=CommanderPriorities(target_room="Right", strength="hard", as_of_tick=10),
    )

    intent = NormalMode().decide(belief, ActionState())

    assert intent.kind == "complete_task" and intent.task_index == 2


def test_hard_commander_target_room_without_candidate_task_positions_in_room() -> None:
    map_data = _roomed_map()
    nav = build_nav_graph(np.ones((map_data.height, map_data.width), dtype=bool), map_data=map_data)
    belief = Belief(
        map=map_data,
        nav=nav,
        assigned_task_indices={0, 1},
        visible_task_indices={0, 1},
        self_world_x=145,
        self_world_y=44,
        last_tick=10,
        commander=CommanderPriorities(target_room="Right", strength="hard", as_of_tick=10),
    )
    room_center = (250, 50)
    expected_cell = nav.nearest_reachable_node(*room_center)
    assert expected_cell is not None

    intent = NormalMode().decide(belief, ActionState())

    assert intent.kind == "navigate_to"
    assert intent.point == nav.node_point[expected_cell]
    assert intent.reason == "commander: positioning in Right"


def test_commander_target_task_wins_when_signalled_and_reachable() -> None:
    belief = Belief(
        map=_roomed_map(),
        assigned_task_indices={0, 1, 2},
        visible_task_indices={0, 1, 2},
        self_world_x=42,
        self_world_y=44,
        last_tick=10,
        commander=CommanderPriorities(target_room="Left", target_task=2, as_of_tick=10),
    )

    intent = NormalMode().decide(belief, ActionState())

    assert intent.kind == "complete_task" and intent.task_index == 2


def test_commander_unknown_or_empty_target_room_falls_back_to_nearest_task() -> None:
    belief = Belief(
        map=_roomed_map(),
        assigned_task_indices={0, 1},
        visible_task_indices={0, 1},
        self_world_x=142,
        self_world_y=44,
        last_tick=10,
        commander=CommanderPriorities(target_room="Right", as_of_tick=10),
    )

    intent = NormalMode().decide(belief, ActionState())

    assert intent.kind == "complete_task" and intent.task_index == 1


def test_hard_commander_unknown_target_room_falls_back_to_nearest_task() -> None:
    belief = Belief(
        map=_roomed_map(),
        assigned_task_indices={0, 1},
        visible_task_indices={0, 1},
        self_world_x=142,
        self_world_y=44,
        last_tick=10,
        commander=CommanderPriorities(target_room="Unknown", strength="hard", as_of_tick=10),
    )

    intent = NormalMode().decide(belief, ActionState())

    assert intent.kind == "complete_task" and intent.task_index == 1


def test_commander_target_task_unreachable_falls_back_to_nearest_reachable_task() -> None:
    mask = np.ones((100, 300), dtype=bool)
    mask[:, 100:200] = False
    belief = Belief(
        map=_roomed_map(),
        assigned_task_indices={0, 2},
        visible_task_indices={0, 2},
        self_world_x=44,
        self_world_y=44,
        last_tick=10,
        commander=CommanderPriorities(target_task=2, as_of_tick=10),
    )
    belief.nav = build_nav_graph(mask, map_data=belief.map, cell_size=8)

    intent = NormalMode().decide(belief, ActionState())

    assert intent.kind == "complete_task" and intent.task_index == 0


def test_commander_posture_stick_breaks_task_ties_toward_crowded_room() -> None:
    belief = Belief(
        map=_roomed_map(),
        assigned_task_indices={0, 2},
        visible_task_indices={0, 2},
        self_world_x=144,
        self_world_y=44,
        last_tick=10,
        commander=CommanderPriorities(posture="stick", as_of_tick=10),
    )
    _crew(belief, "green", (220, 44))
    _crew(belief, "blue", (230, 44))

    intent = NormalMode().decide(belief, ActionState())

    assert intent.kind == "complete_task" and intent.task_index == 2


def test_commander_posture_isolate_breaks_task_ties_toward_empty_room() -> None:
    belief = Belief(
        map=_roomed_map(),
        assigned_task_indices={0, 2},
        visible_task_indices={0, 2},
        self_world_x=144,
        self_world_y=44,
        last_tick=10,
        commander=CommanderPriorities(posture="isolate", as_of_tick=10),
    )
    _crew(belief, "green", (220, 44))
    _crew(belief, "blue", (230, 44))

    intent = NormalMode().decide(belief, ActionState())

    assert intent.kind == "complete_task" and intent.task_index == 0


def test_commander_posture_neutral_matches_default_task_tie() -> None:
    common = dict(
        map=_roomed_map(),
        assigned_task_indices={0, 2},
        visible_task_indices={0, 2},
        self_world_x=144,
        self_world_y=44,
        last_tick=10,
    )
    default_intent = NormalMode().decide(Belief(**common), ActionState())
    neutral_intent = NormalMode().decide(
        Belief(**common, commander=CommanderPriorities(posture="neutral", as_of_tick=10)),
        ActionState(),
    )

    assert neutral_intent.kind == "complete_task"
    assert neutral_intent.task_index == default_intent.task_index


def test_advances_to_next_task_after_completion() -> None:
    belief = Belief(
        map=_map_with_tasks(),
        assigned_task_indices={0, 1},
        visible_task_indices={0, 1},
        self_world_x=110,
        self_world_y=110,  # standing on task 0
    )
    mode = NormalMode()

    first = mode.decide(belief, ActionState())
    assert first.task_index == 0  # nearest is task 0

    # Task 0 completes: progress hits 100 and its bubble leaves the visible set.
    belief.active_task_progress_pct = 100
    belief.visible_task_indices = {1}
    second = mode.decide(belief, ActionState())
    assert 0 in belief.completed_task_indices
    assert second.kind == "complete_task" and second.task_index == 1


def test_bubble_flicker_at_low_progress_does_not_complete() -> None:
    # On a task, only 40% done, then its bubble blinks out (e.g. an imposter overlaps
    # us) ⇒ NOT complete; keep holding the same task rather than abandoning it.
    belief = Belief(
        map=_map_with_tasks(), visible_task_indices={0, 1}, self_world_x=110, self_world_y=110
    )
    mode = NormalMode()
    assert mode.decide(belief, ActionState()).task_index == 0
    belief.active_task_progress_pct = 40  # mid-task
    mode.decide(belief, ActionState())
    belief.visible_task_indices = {1}  # task 0's bubble flickers out
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "complete_task" and intent.task_index == 0  # still holding task 0
    assert 0 not in belief.completed_task_indices


def test_completes_when_bubble_gone_after_high_progress() -> None:
    belief = Belief(
        map=_map_with_tasks(), visible_task_indices={0, 1}, self_world_x=110, self_world_y=110
    )
    mode = NormalMode()
    assert mode.decide(belief, ActionState()).task_index == 0
    belief.active_task_progress_pct = 95  # ≥ COMPLETION_PROGRESS_PCT
    mode.decide(belief, ActionState())
    belief.visible_task_indices = {1}  # bubble gone *after* near-complete progress
    intent = mode.decide(belief, ActionState())
    assert 0 in belief.completed_task_indices
    assert intent.kind == "complete_task" and intent.task_index == 1  # moved on


def test_done_only_when_no_task_signals_remain() -> None:
    # Signals empty ⇒ done, even though our completed set says nothing finished
    # (we trust the live arrows+bubbles, not the bookkeeping).
    belief = Belief(
        map=_map_with_tasks(), assigned_task_indices={0, 1}, visible_task_indices=set(),
        self_world_x=110, self_world_y=110, crew_tasks_remaining=5,
    )
    intent = NormalMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to" and intent.point == (0, 0)  # home


def test_resignalled_task_is_repursued_even_if_marked_completed() -> None:
    # A task we concluded done but whose bubble is still showing must be re-targeted
    # (self-healing against a wrong completion).
    belief = Belief(
        map=_map_with_tasks(), completed_task_indices={0}, visible_task_indices={0},
        self_world_x=110, self_world_y=110,
    )
    intent = NormalMode().decide(belief, ActionState())
    assert intent.kind == "complete_task" and intent.task_index == 0


def test_returns_to_the_start_room_when_all_tasks_are_done() -> None:
    belief = Belief(
        map=_map_with_tasks(),  # home = (0, 0)
        assigned_task_indices={0, 1},
        completed_task_indices={0, 1},
        self_world_x=110,
        self_world_y=110,
    )
    intent = NormalMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to" and intent.point == (0, 0)  # back to spawn, not idle


def test_sweeps_baked_tasks_when_no_signals_arrive() -> None:
    # showTaskArrows disabled: no task signals, so assigned stays empty. Rather
    # than idle forever, sweep toward the nearest baked station to discover tasks.
    belief = Belief(map=_map_with_tasks(), self_world_x=0, self_world_y=0, crew_tasks_remaining=5)
    intent = NormalMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (110, 110)  # nearest station center to (0, 0)


def test_no_sweep_once_crew_tasks_are_done() -> None:
    # crew tasks all done, none assigned to us ⇒ don't sweep stations; head to spawn.
    belief = Belief(map=_map_with_tasks(), self_world_x=0, self_world_y=0, crew_tasks_remaining=0)
    intent = NormalMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to" and intent.point == (0, 0)  # home, not a station sweep


def test_picks_reachable_task_over_nearer_unreachable_one() -> None:
    mask = np.ones((24, 48), dtype=bool)
    mask[:, 24:32] = False  # wall splits the map
    belief = Belief(
        map=MapData(
            width=48,
            height=24,
            tasks=(
                TaskStation(name="L", x=6, y=10, w=4, h=4),  # center (8, 12), left
                TaskStation(name="R", x=38, y=10, w=4, h=4),  # center (40, 12), right
            ),
            vents=(),
            rooms=(),
            button=MapRect(x=0, y=0, w=4, h=4),
            home=MapPoint(x=0, y=0),
        ),
        assigned_task_indices={0, 1},
        visible_task_indices={0, 1},
        self_world_x=8,
        self_world_y=12,  # left of the wall
    )
    belief.nav = build_nav_graph(mask, map_data=belief.map, cell_size=8)
    intent = NormalMode().decide(belief, ActionState())
    assert intent.kind == "complete_task" and intent.task_index == 0  # task 1 is unreachable
