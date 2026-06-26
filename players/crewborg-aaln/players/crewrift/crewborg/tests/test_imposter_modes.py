"""Hunt / Search / Pretend / Evade imposter mode tests (design §7.2)."""

from __future__ import annotations

import numpy as np

from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation, Vent
from players.crewrift.crewborg.agent_tracking import OccupancySnapshot, update_agent_tracking
from players.crewrift.crewborg.modes import EvadeMode, HuntMode, PretendMode, SearchMode
from players.crewrift.crewborg.nav import build_nav_graph
from players.crewrift.crewborg.types import ActionState, Belief, BodyEntry, PlayerRecord


def _visible(belief: Belief, object_id: int, xy: tuple[int, int], color: str = "red", tick: int | None = None) -> None:
    belief.roster[color] = PlayerRecord(
        object_id=object_id, color=color, facing="left", world_x=xy[0], world_y=xy[1],
        last_seen_tick=belief.last_tick if tick is None else tick, life_status="alive",
    )


# --------------------------------------------------------------------------- #
# Hunt — drives off the shared kill-opportunity helper                        #
# --------------------------------------------------------------------------- #


def test_hunt_strikes_a_victim_in_range_and_unwitnessed() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=True)
    _visible(belief, 1004, (108, 100), color="green")  # 8px away (<KillRange), alone
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "kill" and intent.target_color == "green"


def test_hunt_shadows_in_range_until_the_cooldown_clears() -> None:
    # In range + unwitnessed but NOT yet kill-ready (entered Hunt in the lead window):
    # lie in wait, don't fire, so the strike lands the instant the cooldown clears.
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=False)
    _visible(belief, 1004, (108, 100), color="green")
    assert HuntMode().decide(belief, ActionState()).kind == "navigate_to"


def test_hunt_stalks_a_distant_victim() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    _visible(belief, 1004, (300, 100), color="green")  # far ⇒ close in, don't kill
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to" and intent.point[0] > 100  # heading toward the victim


def test_hunt_ignores_a_recently_seen_victim() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    _visible(belief, 1004, (120, 100), tick=1)  # Search may track this; Hunt requires visibility
    assert HuntMode().decide(belief, ActionState()).kind == "idle"


def test_hunt_idles_with_only_too_stale_victims() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=500)
    _visible(belief, 1004, (120, 100), tick=1)
    assert HuntMode().decide(belief, ActionState()).kind == "idle"


def test_hunt_skips_teammates() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=True)
    belief.teammate_colors = {"red"}
    _visible(belief, 1004, (108, 100), color="red")  # teammate — never a victim
    assert HuntMode().decide(belief, ActionState()).kind == "idle"

    _visible(belief, 1007, (108, 100), color="green")  # an in-range crewmate is killable
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "kill" and intent.target_color == "green"


def test_hunt_lies_in_wait_when_a_witness_is_near() -> None:
    # Victim in range but a witness beside it (zero urgency) ⇒ shadow, don't fire.
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=True)
    _visible(belief, 1004, (108, 100), color="green")
    _visible(belief, 1005, (110, 100), color="blue")  # witness next to the victim
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"  # lying in wait, not killing


def test_hunt_strikes_a_witnessed_victim_under_full_urgency() -> None:
    belief = Belief(
        self_world_x=100, self_world_y=100, last_tick=300, self_kill_ready=True, kill_ready_since_tick=0,
    )
    _visible(belief, 1004, (108, 100), color="green")
    _visible(belief, 1005, (110, 100), color="blue")  # witness ignored at full urgency
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "kill" and intent.target_color == "green"


def test_hunt_commits_to_one_victim_across_ticks() -> None:
    # Once committed, Hunt keeps the same victim even as another comes closer.
    mode = HuntMode()
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    _visible(belief, 1004, (300, 100), color="green")
    mode.decide(belief, ActionState())  # commits to 1004
    assert mode._victim_color == "green"
    _visible(belief, 1009, (140, 100), color="white")  # a nearer crewmate appears
    mode.decide(belief, ActionState())
    assert mode._victim_color == "green"  # still committed to the first victim


def test_hunt_prefers_reachable_victim() -> None:
    mask = np.ones((24, 120), dtype=bool)
    mask[:, 56:64] = False  # wall splits the map; right side is unreachable from the left
    belief = Belief(self_world_x=8, self_world_y=12, last_tick=5)
    belief.nav = build_nav_graph(mask, cell_size=8)
    _visible(belief, 1001, (110, 12), color="green")  # right side: UNREACHABLE
    _visible(belief, 1002, (10, 12), color="blue")  # left: reachable
    mode = HuntMode()
    mode.decide(belief, ActionState())
    assert mode._victim_color == "blue"  # committed to the reachable one, not 1001


# --------------------------------------------------------------------------- #
# Search — find and follow targets during the kill lead window                #
# --------------------------------------------------------------------------- #


def test_search_follows_a_visible_target() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    _visible(belief, 1004, (130, 100), color="green")
    intent = SearchMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (130, 100)
    assert intent.reason == "search: following visible target"


def test_search_follows_a_recently_seen_committed_target() -> None:
    mode = SearchMode()
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    _visible(belief, 1004, (130, 100), color="green")
    mode.decide(belief, ActionState())

    belief.last_tick = 10
    belief.roster["green"].last_seen_tick = 5
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (130, 100)
    assert intent.reason == "search: following last-seen target"


def test_search_fakes_a_nearby_task_instead_of_camping_a_target_in_deep_cooldown() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    # Standing right next to a tasking crewmate near room A's station (80, 50),
    # with the kill cooldown far from ready: fake the nearby station, don't camp.
    belief = _belief(map_data, nav, (95, 80), tick=50)
    belief.kill_cooldown_start_tick = 40
    belief.kill_cooldown_estimate = 900  # ~890 ticks to ready >> SEARCH_LEAD_TICKS
    _see(belief, 1001, (95, 75))  # the target, 5 px away, ~29 px off room A's station

    intent = SearchMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.reason == "search: faking a task near target"
    assert 70 <= intent.point[0] < 90  # room A's station, within the target's sight


def test_search_keeps_following_when_the_kill_window_is_near() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (95, 60), tick=50)
    belief.kill_cooldown_start_tick = 0
    belief.kill_cooldown_estimate = 100  # ready in 50 ticks <= SEARCH_LEAD_TICKS
    _see(belief, 1001, (90, 55))

    intent = SearchMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (90, 55)
    assert intent.reason == "search: following visible target"


def test_search_patrols_task_stations_when_occupancy_is_empty() -> None:
    # No occupancy substrate/snapshot at all: Search must keep moving (patrol
    # the real task stations), never idle out a kill cooldown standing still.
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    mode = SearchMode()
    belief = _belief(map_data, nav, (10, 10), tick=5)

    first = mode.decide(belief, ActionState())
    assert first.kind == "navigate_to"
    assert first.reason == "search: patrolling task stations"

    # Arriving at the patrol point advances to the next station, not idle.
    belief.self_world_x, belief.self_world_y = first.point
    second = mode.decide(belief, ActionState())
    assert second.kind == "navigate_to"
    assert second.point != first.point


def test_search_moves_through_ranked_occupancy_points() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (10, 10), tick=5)
    update_agent_tracking(belief)
    substrate = belief.agent_tracking.substrate
    assert substrate is not None
    cell_a = next(cell for cell in substrate.cells.values() if cell.label == "A")
    cell_b = next(cell for cell in substrate.cells.values() if cell.label == "B")
    belief.agent_tracking.snapshot = OccupancySnapshot(
        tick=5,
        expected_by_cell={cell_a.index: 2.0, cell_b.index: 1.0},
        top_cell=cell_a.index,
        top_point=cell_a.center,
        top_expected=2.0,
        tracked_count=1,
        support_cell_count=2,
    )

    mode = SearchMode()
    first = mode.decide(belief, ActionState())
    assert first.kind == "navigate_to"
    assert first.point == cell_a.center

    belief.self_world_x, belief.self_world_y = first.point
    second = mode.decide(belief, ActionState())
    assert second.kind == "navigate_to"
    assert second.point == cell_b.center


# --------------------------------------------------------------------------- #
# Evade — leave the body after a kill                                         #
# --------------------------------------------------------------------------- #


def test_evade_vents_when_a_vent_exists() -> None:
    map_data = MapData(
        width=1000, height=1000, tasks=(),
        vents=(Vent(x=300, y=300, w=14, h=14, group="1", group_index=1),),
        rooms=(), button=MapRect(x=0, y=0, w=28, h=34), home=MapPoint(x=0, y=0),
    )
    belief = Belief(map=map_data, self_world_x=100, self_world_y=100)
    intent = EvadeMode().decide(belief, ActionState())
    assert intent.kind == "vent"


def test_evade_moves_away_from_body_when_no_vents() -> None:
    map_data = MapData(
        width=1000, height=1000, tasks=(), vents=(), rooms=(),
        button=MapRect(x=0, y=0, w=28, h=34), home=MapPoint(x=0, y=0),
    )
    belief = Belief(map=map_data, self_world_x=100, self_world_y=100)
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=110, world_y=100, first_seen_tick=1)
    intent = EvadeMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (90, 100)


# --------------------------------------------------------------------------- #
# Pretend — fake tasks at real stations in occupancy-selected rooms           #
# --------------------------------------------------------------------------- #


def _shadow_map() -> MapData:
    # A dedicated starting room (holds home) plus two task rooms with one station each.
    return MapData(
        width=200, height=120,
        tasks=(
            TaskStation(name="a", x=70, y=40, w=20, h=20),  # in room A, center (80, 50)
            TaskStation(name="b", x=150, y=40, w=20, h=20),  # in room B, center (160, 50)
        ),
        vents=(),
        rooms=(
            Room(name="Start", x=0, y=0, w=40, h=120),
            Room(name="A", x=40, y=0, w=80, h=120),
            Room(name="B", x=120, y=0, w=80, h=120),
        ),
        button=MapRect(x=0, y=100, w=10, h=10), home=MapPoint(x=10, y=10),
    )


def _belief(map_data: MapData, nav, self_xy: tuple[int, int], tick: int) -> Belief:
    return Belief(map=map_data, nav=nav, self_world_x=self_xy[0], self_world_y=self_xy[1], last_tick=tick)


def _see(belief: Belief, object_id: int, xy: tuple[int, int], tick: int | None = None, color: str = "green") -> None:
    belief.roster[color] = PlayerRecord(
        object_id=object_id, color=color, facing="left", world_x=xy[0], world_y=xy[1],
        last_seen_tick=belief.last_tick if tick is None else tick, life_status="alive",
    )


def test_pretend_idles_only_without_a_self_position() -> None:
    # The one unavoidable idle: camera not up yet (no self position).
    belief = Belief(map=_shadow_map(), last_tick=0)
    assert PretendMode().decide(belief, ActionState()).kind == "idle"


def test_pretend_targets_a_fallback_task_station_not_visible_crew() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (10, 10), tick=5)  # we are in the start room
    _see(belief, 1001, (80, 60))  # a crewmate over in room A
    intent = PretendMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (80, 50)  # room A's task station, not the visible crewmate


def test_pretend_fakes_a_task_at_the_occupancy_selected_station() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (110, 60), tick=5)
    update_agent_tracking(belief)
    substrate = belief.agent_tracking.substrate
    assert substrate is not None
    cell_a = next(cell for cell in substrate.cells.values() if cell.label == "A")
    belief.agent_tracking.snapshot = OccupancySnapshot(
        tick=5,
        expected_by_cell={cell_a.index: 1.0},
        top_cell=cell_a.index,
        top_point=cell_a.center,
        top_expected=1.0,
        tracked_count=1,
        support_cell_count=1,
    )
    moving = PretendMode().decide(belief, ActionState())
    assert moving.kind == "navigate_to"
    assert 70 <= moving.point[0] < 90 and 40 <= moving.point[1] < 60  # room A's station rect


def test_pretend_starts_fake_task_on_arrival_before_retargeting() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (80, 50), tick=20)
    update_agent_tracking(belief)
    substrate = belief.agent_tracking.substrate
    assert substrate is not None
    cell_b = next(cell for cell in substrate.cells.values() if cell.label == "B")
    belief.agent_tracking.snapshot = OccupancySnapshot(
        tick=20,
        expected_by_cell={cell_b.index: 2.0},
        top_cell=cell_b.index,
        top_point=cell_b.center,
        top_expected=2.0,
        tracked_count=1,
        support_cell_count=1,
    )

    mode = PretendMode()
    mode._state = "goto_room"
    mode._target_room_name = "A"
    mode._goto_point = (80, 50)
    mode._task_station = (80, 50)
    mode._room_chosen_tick = 10

    intent = mode.decide(belief, ActionState())
    assert intent.kind == "idle"
    assert intent.reason == "faking a task"
    assert mode._state == "do_task"
    assert mode._target_room_name == "A"
    assert mode._hold_until == belief.last_tick + 72


def test_pretend_moves_to_another_room_after_a_fake_task_completes() -> None:
    # After the 72-tick fake-task hold, the re-dispatch must exclude the room we
    # just faked in — re-picking it (we are standing there, so its occupancy is
    # high) camped the imposter on one station for hundreds of ticks.
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (80, 50), tick=100)  # on room A's station
    update_agent_tracking(belief)
    substrate = belief.agent_tracking.substrate
    assert substrate is not None
    cell_a = next(cell for cell in substrate.cells.values() if cell.label == "A")
    belief.agent_tracking.snapshot = OccupancySnapshot(
        tick=100,
        expected_by_cell={cell_a.index: 2.0},  # room A still ranks hottest
        top_cell=cell_a.index,
        top_point=cell_a.center,
        top_expected=2.0,
        tracked_count=1,
        support_cell_count=1,
    )

    mode = PretendMode()
    mode._state = "do_task"
    mode._task_station = (80, 50)
    mode._hold_until = belief.last_tick  # the hold just expired

    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert not (70 <= intent.point[0] < 90)  # NOT room A's station again


def test_pretend_does_not_fake_a_task_in_the_starting_room() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    # In the starting room ⇒ no fake task here; pick a task station elsewhere.
    belief = _belief(map_data, nav, (10, 60), tick=5)
    _see(belief, 1001, (25, 60))
    intent = PretendMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point != (25, 60)
    assert intent.point[0] >= 40


def test_pretend_wanders_rooms_when_no_crew_is_in_sight() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (10, 10), tick=5)  # nobody known/visible
    intent = PretendMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"  # wandering, never idle
    assert intent.point[0] >= 40  # heading out of the start room toward another room


def test_pretend_wandering_does_not_switch_to_follow_on_sighting_a_crewmate() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (10, 10), tick=5)
    _see(belief, 1001, (80, 60))  # a crewmate appears
    mode = PretendMode()
    mode._state, mode._goto_point = "goto_room", (160, 60)  # mid-wander
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (160, 60)  # Search owns target following, not Pretend
