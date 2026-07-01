"""Hunt / Search / Pretend / Evade imposter mode tests (design §7.2).

NOTE: the Pretend/Search *seeking* tests below are skipped — that logic was retired
2026-06-24 (cold-stored at ``modes/_deprecated/``) pending the new group-follow →
peel-off approach. They are kept as a record of the old contract; they will be
replaced by tests for the new modes, not revived. Hunt/Evade tests stay live.
"""

from __future__ import annotations

import numpy as np
import pytest

from crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation, Vent
from crewborg.agent_tracking import OccupancySnapshot, update_agent_tracking
from crewborg.modes import EvadeMode, HuntMode, SearchMode
from crewborg.modes._deprecated.pretend import PretendMode  # retired; only the skipped tests use it
from crewborg.nav import build_nav_graph
from crewborg.types import ActionState, Belief, BodyEntry, CommanderPriorities, PlayerRecord
from players.player_sdk import EventEmitter, ListTraceSink

# Applied to tests that pin the retired occupancy-seeking Pretend/Search behavior.
deprecated_seeking = pytest.mark.skip(
    reason="Pretend/Search occupancy-seeking retired 2026-06-24 (modes/_deprecated/); "
    "new group-follow→peel-off approach pending. See design.md."
)


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


def test_hunt_strikes_a_witnessed_victim_after_the_first_kill() -> None:
    # After our FIRST kill (last_kill_tick set), the unwitnessed requirement is dropped:
    # getting the SECOND kill is the imposter's core job, worth more than stealth. Same
    # witness setup as the lie-in-wait test, but with a kill already banked ⇒ strike.
    belief = Belief(
        self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=True, last_kill_tick=3,
    )
    _visible(belief, 1004, (108, 100), color="green")
    _visible(belief, 1005, (110, 100), color="blue")  # witness ignored once we've killed once
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "kill" and intent.target_color == "green"


def test_hunt_strikes_a_witnessed_victim_under_full_urgency() -> None:
    belief = Belief(
        self_world_x=100, self_world_y=100, last_tick=300, self_kill_ready=True, kill_ready_since_tick=0,
    )
    _visible(belief, 1004, (108, 100), color="green")
    _visible(belief, 1005, (110, 100), color="blue")  # witness ignored at full urgency
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "kill" and intent.target_color == "green"


def test_hunt_commander_allows_witnessed_kill_with_danger_reason() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=True)
    belief.commander = CommanderPriorities(
        allow_witnessed_kill=True,
        danger_reason="last chance before meeting",
        as_of_tick=belief.last_tick,
    )
    _visible(belief, 1004, (108, 100), color="green")
    _visible(belief, 1005, (110, 100), color="blue")  # witness next to the victim
    mode = HuntMode()
    trace = ListTraceSink()
    mode.emit = EventEmitter(trace, tick=belief.last_tick)
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "kill" and intent.target_color == "green"
    [event] = [event for event in trace.events if event.name == "domain.commander_danger"]
    assert event.data["lever"] == "allow_witnessed_kill"
    assert event.data["danger_reason"] == "last chance before meeting"
    assert event.data["target_color"] == "green"


def test_hunt_stale_commander_does_not_allow_witnessed_kill() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=500, self_kill_ready=True)
    belief.commander = CommanderPriorities(
        allow_witnessed_kill=True,
        danger_reason="stale risk",
        as_of_tick=0,
    )
    _visible(belief, 1004, (108, 100), color="green")
    _visible(belief, 1005, (110, 100), color="blue")
    assert HuntMode().decide(belief, ActionState()).kind == "navigate_to"


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


def test_hunt_default_select_victim_pick_stands_without_commander_target() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    _visible(belief, 1004, (120, 100), color="green")
    _visible(belief, 1005, (300, 100), color="blue")  # more isolated ⇒ default target
    _visible(belief, 1006, (122, 100), color="white")
    mode = HuntMode()
    mode.decide(belief, ActionState())
    assert mode._victim_color == "blue"


def test_hunt_prefers_commander_target_player_when_visible_and_selectable() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    belief.commander = CommanderPriorities(target_player="green", as_of_tick=belief.last_tick)
    _visible(belief, 1004, (120, 100), color="green")
    _visible(belief, 1005, (300, 100), color="blue")  # default would prefer this isolated victim
    _visible(belief, 1006, (122, 100), color="white")
    mode = HuntMode()
    mode.decide(belief, ActionState())
    assert mode._victim_color == "green"


def test_hunt_target_player_falls_back_when_not_visible() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    belief.commander = CommanderPriorities(target_player="green", as_of_tick=belief.last_tick)
    _visible(belief, 1004, (120, 100), color="green", tick=1)
    _visible(belief, 1005, (300, 100), color="blue")
    _visible(belief, 1006, (122, 100), color="white")
    mode = HuntMode()
    mode.decide(belief, ActionState())
    assert mode._victim_color == "blue"


# --------------------------------------------------------------------------- #
# Search — find and follow targets during the kill lead window                #
# --------------------------------------------------------------------------- #


@deprecated_seeking
def test_search_follows_a_visible_target() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    _visible(belief, 1004, (130, 100), color="green")
    intent = SearchMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (130, 100)
    assert intent.reason == "search: following visible target"


@deprecated_seeking
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


@deprecated_seeking
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


def test_search_seeks_the_hotter_occupancy_room_not_a_random_one() -> None:
    # _pick_room heads toward best_seek_point (the hottest crew-occupancy cell), not a
    # random room (acquisition fix, James 2026-06-30). FLIP the weights so B is hottest:
    # the pick must follow the occupancy (toward B), which the old seeded-random pick would
    # not — this is the regression guard for "seek crew, don't wander".
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
        expected_by_cell={cell_a.index: 1.0, cell_b.index: 2.0},  # B hotter than A now
        top_cell=cell_b.index,
        top_point=cell_b.center,
        top_expected=2.0,
        tracked_count=1,
        support_cell_count=2,
    )
    first = SearchMode().decide(belief, ActionState())
    assert first.kind == "navigate_to"
    # The pick heads toward the hotter cell B's room, not A — occupancy-driven, not the RNG.
    to_b = (first.point[0] - cell_b.center[0]) ** 2 + (first.point[1] - cell_b.center[1]) ** 2
    to_a = (first.point[0] - cell_a.center[0]) ** 2 + (first.point[1] - cell_a.center[1]) ** 2
    assert to_b < to_a


# --------------------------------------------------------------------------- #
# Evade — re-approach the crew after a kill (rewritten 2026-06-26)             #
# Old flee behavior (vent away / walk off the body) is gone: Evade now beelines #
# toward the densest expected-crew area so a victim cluster is nearby when it    #
# hands back to Search/Recon.                                                    #
# --------------------------------------------------------------------------- #


def _evade_belief_with_occupancy(target_room: str) -> Belief:
    """An imposter in the Left room with expected-crew occupancy massed in `target_room`.
    A vent is present specifically to prove Evade no longer uses it."""
    map_data = MapData(
        width=128, height=64,
        tasks=(TaskStation(name="left", x=16, y=16, w=8, h=8),
               TaskStation(name="right", x=96, y=16, w=8, h=8)),
        vents=(Vent(x=8, y=8, w=14, h=14, group="1", group_index=1),),
        rooms=(Room(name="Left", x=0, y=0, w=64, h=64),
               Room(name="Right", x=64, y=0, w=64, h=64)),
        button=MapRect(x=4, y=48, w=8, h=8), home=MapPoint(x=8, y=8),
    )
    nav = build_nav_graph(np.ones((map_data.height, map_data.width), dtype=bool), map_data=map_data)
    belief = Belief(map=map_data, nav=nav, self_role="imposter", self_world_x=8, self_world_y=8)
    update_agent_tracking(belief)
    cells = [c for c in belief.agent_tracking.substrate.cells.values() if c.label == target_room]
    belief.agent_tracking.snapshot = OccupancySnapshot(
        tick=1, expected_by_cell={c.index: 0.5 for c in cells},
        top_cell=cells[0].index, top_point=cells[0].center, top_expected=0.5,
        tracked_count=1, support_cell_count=len(cells),
    )
    return belief


def test_evade_beelines_to_densest_crew_area_not_a_vent() -> None:
    belief = _evade_belief_with_occupancy("Right")  # crew massed across the map in Right
    intent = EvadeMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"  # no longer vents or flees the body
    assert intent.point[0] > 64          # heads INTO the crew-dense Right room


def test_evade_falls_back_to_last_seen_crew_without_occupancy() -> None:
    # Cold start: no occupancy grid yet, but we have seen a crewmate -> close on them.
    map_data = MapData(
        width=1000, height=1000, tasks=(), vents=(), rooms=(),
        button=MapRect(x=0, y=0, w=28, h=34), home=MapPoint(x=0, y=0),
    )
    belief = Belief(map=map_data, self_role="imposter", self_world_x=100, self_world_y=100)
    _visible(belief, 1007, (400, 300), color="green", tick=5)
    intent = EvadeMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (400, 300)


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


@deprecated_seeking
def test_pretend_targets_a_fallback_task_station_not_visible_crew() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (10, 10), tick=5)  # we are in the start room
    _see(belief, 1001, (80, 60))  # a crewmate over in room A
    intent = PretendMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (80, 50)  # room A's task station, not the visible crewmate


@deprecated_seeking
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


@deprecated_seeking
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

    _see(belief, 1001, (90, 50))  # a crewmate is watching ⇒ the fake task is worth holding

    mode = PretendMode()
    mode._state = "goto_room"
    mode._target_room_name = "A"
    mode._goto_point = (80, 50)
    mode._task_station = (80, 50)
    mode._room_chosen_tick = 10

    intent = mode.decide(belief, ActionState())
    assert intent.kind == "idle"
    assert intent.reason == "faking a task (crew watching)"
    assert mode._state == "do_task"
    assert mode._target_room_name == "A"
    assert mode._hold_until == belief.last_tick + 72


@deprecated_seeking
def test_pretend_does_not_fake_a_task_with_no_crewmate_watching() -> None:
    # Change 2: arriving at a station with an empty room ⇒ don't idle a fake task; keep
    # moving so the kill cooldown converts to a real kill sooner.
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (80, 50), tick=20)  # at room A's station, nobody in view
    mode = PretendMode()
    mode._state = "do_task"
    mode._task_station = (80, 50)

    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"  # re-dispatched out, not idling
    assert mode._hold_until is None  # never started the hold


@deprecated_seeking
def test_pretend_abandons_a_fake_task_when_the_last_crewmate_leaves_view() -> None:
    # Change 3: a hold already in progress stops the instant no crewmate is visible.
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (80, 50), tick=20)
    _see(belief, 1001, (90, 50))  # watcher present
    mode = PretendMode()
    mode._state = "do_task"
    mode._task_station = (80, 50)

    watched = mode.decide(belief, ActionState())
    assert watched.kind == "idle" and watched.reason == "faking a task (crew watching)"

    belief.roster["green"].last_seen_tick = 5  # the watcher is no longer visible this tick
    abandoned = mode.decide(belief, ActionState())
    assert abandoned.kind == "navigate_to"  # stopped faking, moving on


@deprecated_seeking
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


@deprecated_seeking
def test_pretend_wanders_rooms_when_no_crew_is_in_sight() -> None:
    map_data = _shadow_map()
    nav = build_nav_graph(np.ones((120, 200), dtype=bool), map_data=map_data)
    belief = _belief(map_data, nav, (10, 10), tick=5)  # nobody known/visible
    intent = PretendMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"  # wandering, never idle
    assert intent.point[0] >= 40  # heading out of the start room toward another room


@deprecated_seeking
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
