"""Normal mode: task selection + completion detection (design §7.1)."""

from __future__ import annotations

import numpy as np

from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation
from players.crewrift.crewborg.modes import CrewmateGhostMode, NormalMode
from players.crewrift.crewborg.nav import build_nav_graph
from players.crewrift.crewborg.types import ActionState, Belief, PlayerRecord


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


def test_no_signal_redispatches_to_remembered_assigned_tasks() -> None:
    # Signals empty but assigned tasks not known complete ⇒ walk to the nearest
    # remembered station instead of idling at spawn (the v4 Bridge-blob fix).
    # Standing at a station that shows no signal proves it done (in-view
    # incomplete assigned tasks always signal), so task 0 — under our feet,
    # signal-less — is marked completed and task 1 becomes the goal.
    belief = Belief(
        map=_map_with_tasks(), assigned_task_indices={0, 1}, visible_task_indices=set(),
        self_world_x=110, self_world_y=110, crew_tasks_remaining=5,
    )
    intent = NormalMode().decide(belief, ActionState())
    assert 0 in belief.completed_task_indices
    assert intent.kind == "navigate_to" and intent.task_index == 1
    assert intent.point == (510, 510)

    # Both stations checked and signal-less ⇒ all done; with no crew tracked,
    # head home.
    belief.self_world_x, belief.self_world_y = 510, 510
    done = NormalMode().decide(belief, ActionState())
    assert 1 in belief.completed_task_indices
    assert done.kind == "navigate_to" and done.point == (0, 0)  # home


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


def test_tasks_done_drifts_to_known_crew_instead_of_spawn() -> None:
    # Survival posture (the v4 Bridge-blob fix): with tasks done and crew
    # tracked, stand near witnesses rather than idling alone at spawn.
    belief = Belief(
        map=_map_with_tasks(),
        assigned_task_indices={0, 1},
        completed_task_indices={0, 1},
        self_world_x=110,
        self_world_y=110,
        last_tick=100,
    )
    belief.roster["green"] = PlayerRecord(
        color="green", world_x=400, world_y=400, last_seen_tick=90, life_status="alive"
    )
    intent = NormalMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to" and intent.point == (400, 400)
    assert intent.reason == "tasks done: staying near crew"

    # A ghost can't be killed: it still heads home.
    ghost_intent = CrewmateGhostMode().decide(belief, ActionState())
    assert ghost_intent.point == (0, 0)


def _map_with_danger_room() -> MapData:
    return MapData(
        width=1000,
        height=1000,
        tasks=(
            TaskStation(name="hub", x=100, y=100, w=20, h=20),  # center (110, 110), in Storage Deck
            TaskStation(name="safe", x=140, y=100, w=20, h=20),  # center (150, 110), outside
        ),
        vents=(),
        rooms=(Room(name="Storage Deck", x=80, y=80, w=60, h=60),),
        button=MapRect(x=0, y=0, w=28, h=34),
        home=MapPoint(x=0, y=0),
    )


def test_danger_room_station_is_deprioritized_only_while_shadowed() -> None:
    # The Storage-Deck survival tie-break: with a known-alive non-teammate near
    # us, the slightly-nearer danger-room station loses the ordering; alone, the
    # normal nearest-first routing is untouched (routing stays best-in-class).
    def belief() -> Belief:
        return Belief(
            map=_map_with_danger_room(),
            assigned_task_indices={0, 1},
            visible_task_indices={0, 1},
            self_world_x=115,
            self_world_y=160,  # hub station marginally nearer than safe
            last_tick=100,
        )

    alone = belief()
    assert NormalMode().decide(alone, ActionState()).task_index == 0  # nearest wins

    shadowed = belief()
    shadowed.roster["red"] = PlayerRecord(
        color="red", world_x=140, world_y=160, last_seen_tick=100, life_status="alive"
    )
    intent = NormalMode().decide(shadowed, ActionState())
    assert intent.task_index == 1  # the danger-room station yields the tie-break


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


def test_ghost_picks_nearer_task_through_walls() -> None:
    mask = np.ones((24, 48), dtype=bool)
    mask[:, 24:28] = False
    belief = Belief(
        map=MapData(
            width=48,
            height=24,
            tasks=(
                TaskStation(name="L", x=6, y=10, w=4, h=4),  # center (8, 12), reachable
                TaskStation(name="R", x=30, y=10, w=4, h=4),  # center (32, 12), across wall but nearer
            ),
            vents=(),
            rooms=(),
            button=MapRect(x=0, y=0, w=4, h=4),
            home=MapPoint(x=0, y=0),
        ),
        assigned_task_indices={0, 1},
        visible_task_indices={0, 1},
        self_world_x=22,
        self_world_y=12,
    )
    belief.nav = build_nav_graph(mask, map_data=belief.map, cell_size=8)
    intent = CrewmateGhostMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to_noclip"
    assert intent.task_index == 1
    assert intent.point == (32, 12)


def test_targets_route_near_task_over_euclidean_near_task_across_a_wall() -> None:
    # The station just across the wall is euclidean-nearest but a long walk
    # around; route-aware ordering picks the genuinely closer station.
    mask = np.ones((24, 64), dtype=bool)
    mask[0:20, 24:28] = False  # wall with a gap at the bottom (y >= 20)
    belief = Belief(
        map=MapData(
            width=64,
            height=24,
            tasks=(
                TaskStation(name="across", x=28, y=2, w=4, h=4),  # center (30, 4): 10px away but around the wall
                TaskStation(name="same-side", x=2, y=2, w=4, h=4),  # center (4, 4): 16px away, straight shot
            ),
            vents=(),
            rooms=(),
            button=MapRect(x=0, y=20, w=4, h=4),
            home=MapPoint(x=2, y=22),
        ),
        assigned_task_indices={0, 1},
        visible_task_indices={0, 1},
        self_world_x=20,
        self_world_y=4,
    )
    belief.nav = build_nav_graph(mask, map_data=belief.map, cell_size=4)
    intent = NormalMode().decide(belief, ActionState())
    assert intent.kind == "complete_task" and intent.task_index == 1


def test_targets_the_chain_endpoint_over_the_middle_station() -> None:
    # Stations on a line with us just left of the middle one: starting at the
    # left endpoint finishes the chain with no backtrack; the pure
    # nearest-station pick (the middle) would walk the leftmost twice.
    belief = Belief(
        map=MapData(
            width=1000,
            height=1000,
            tasks=(
                TaskStation(name="mid", x=190, y=100, w=20, h=20),  # center (200, 110)
                TaskStation(name="left", x=90, y=100, w=20, h=20),  # center (100, 110)
                TaskStation(name="right", x=490, y=100, w=20, h=20),  # center (500, 110)
            ),
            vents=(),
            rooms=(),
            button=MapRect(x=0, y=0, w=28, h=34),
            home=MapPoint(x=0, y=0),
        ),
        assigned_task_indices={0, 1, 2},
        visible_task_indices={0, 1, 2},
        self_world_x=170,  # 30px from mid, 70px from left
        self_world_y=110,
    )
    intent = NormalMode().decide(belief, ActionState())
    # Scores: left first = 70 + (left->mid 100) + (mid->right 300) = 470;
    # mid first = 30 + (mid->left 100) + (left->right 400) = 530. Left wins.
    assert intent.kind == "complete_task" and intent.task_index == 1


def test_ghost_completes_task_once_inside_station() -> None:
    belief = Belief(
        map=_map_with_tasks(),
        assigned_task_indices={0},
        visible_task_indices={0},
        self_world_x=105,
        self_world_y=105,
    )
    intent = CrewmateGhostMode().decide(belief, ActionState())
    assert intent.kind == "complete_task"
    assert intent.task_index == 0
