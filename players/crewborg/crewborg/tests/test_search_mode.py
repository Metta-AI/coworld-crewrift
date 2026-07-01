"""New Search mode FSM tests (modes/search.py).

Search is the imposter's always-on seeking stance: pick a nearby room, watch it,
and when a crewmate leaves follow them to their next room (using path prediction
once they're out of view). A small open-floor map keeps the nav routing trivial.
"""

from __future__ import annotations

import numpy as np

from crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation
from crewborg.modes.search import SearchMode
from crewborg.nav import build_nav_graph
from crewborg.types import ActionState, Belief, CommanderPriorities, PlayerRecord


def _map() -> MapData:
    # Three rooms left→right with a task station in each; spawn (home) in the middle.
    return MapData(
        width=300, height=80,
        tasks=(
            TaskStation(name="L", x=40, y=40, w=8, h=8),
            TaskStation(name="M", x=150, y=40, w=8, h=8),
            TaskStation(name="R", x=250, y=40, w=8, h=8),
        ),
        vents=(),
        rooms=(
            Room(name="Left", x=0, y=0, w=100, h=80),
            Room(name="Mid", x=100, y=0, w=100, h=80),
            Room(name="Right", x=200, y=0, w=100, h=80),
        ),
        button=MapRect(x=4, y=4, w=8, h=8),
        home=MapPoint(x=150, y=40),  # spawn in Mid → Mid is the start room (never watched)
    )


def _distant_map() -> MapData:
    rooms = tuple(
        Room(name=name, x=index * 100, y=0, w=100, h=80)
        for index, name in enumerate(("Start", "Near1", "Near2", "Near3", "Near4", "Far"))
    )
    tasks = tuple(
        TaskStation(name=name, x=room.x + 40, y=40, w=8, h=8)
        for name, room in zip(("start", "n1", "n2", "n3", "n4", "far"), rooms, strict=True)
    )
    return MapData(
        width=600,
        height=80,
        tasks=tasks,
        vents=(),
        rooms=rooms,
        button=MapRect(x=4, y=4, w=8, h=8),
        home=MapPoint(x=50, y=40),
    )


def _belief(self_xy=(150, 40), tick=10) -> Belief:
    m = _map()
    nav = build_nav_graph(np.ones((m.height, m.width), dtype=bool), map_data=m)
    return Belief(map=m, nav=nav, self_role="imposter", self_world_x=self_xy[0], self_world_y=self_xy[1], last_tick=tick)


def _distant_belief() -> Belief:
    m = _distant_map()
    nav = build_nav_graph(np.ones((m.height, m.width), dtype=bool), map_data=m)
    return Belief(map=m, nav=nav, self_role="imposter", self_world_x=50, self_world_y=40, last_tick=10)


def _crew(belief: Belief, color: str, xy, tick=None) -> PlayerRecord:
    rec = PlayerRecord(color=color, world_x=xy[0], world_y=xy[1],
                       last_seen_tick=belief.last_tick if tick is None else tick, life_status="alive")
    belief.roster[color] = rec
    return rec


def _seed_occluded_follow(mode: SearchMode, belief: Belief, color: str = "green") -> PlayerRecord:
    # Keep self in Left while the target moves through Mid so the FOLLOW same-room hand-off
    # does not fire during seeding (this test map has no corridors; a real map does).
    belief.self_world_x, belief.self_world_y = 10, 40
    target = _crew(belief, color, (130, 40))
    mode._begin_follow(belief, target)
    for tick in range(11, 20):
        belief.last_tick = tick
        target.record(tick, 130 + (tick - 10) * 4, 40, "right", 1001)
        mode.decide(belief, ActionState())
    return target


def test_pick_room_then_navigate_to_a_task_room() -> None:
    mode = SearchMode()
    intent = mode.decide(_belief(), ActionState())
    assert intent.kind == "navigate_to"
    assert mode._state == "go_to_room"
    assert mode._target_room in {"Left", "Right"}  # a nearby task room, not the start room (Mid)


def test_commander_hunt_room_picks_valid_task_room() -> None:
    mode = SearchMode()
    belief = _belief()
    belief.commander = CommanderPriorities(hunt_room="Right", as_of_tick=belief.last_tick)
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert mode._target_room == "Right"


def test_hard_commander_hunt_room_picks_distant_task_room() -> None:
    mode = SearchMode()
    belief = _distant_belief()
    belief.commander = CommanderPriorities(hunt_room="Far", strength="hard", as_of_tick=belief.last_tick)
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert mode._target_room == "Far"


def test_soft_commander_hunt_room_is_a_nudge_not_a_force() -> None:
    # A SOFT hunt-room only ADDS a bonus to the score; a strong recency penalty on it wins.
    mode = SearchMode()
    belief = _belief()  # self in Mid; candidates Left / Right
    belief.commander = CommanderPriorities(hunt_room="Right", as_of_tick=belief.last_tick)
    mode._last_visit_tick = {"Right": belief.last_tick}  # Right JUST visited -> big recency penalty
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert mode._target_room == "Left"  # recency penalty overrides the soft hunt nudge


def test_pickroom_prefers_high_occupancy_room(monkeypatch) -> None:
    import crewborg.modes.search as search_mod
    # Occupancy is the strongest signal: crew expected in Right -> pick Right over empty Left.
    monkeypatch.setattr(search_mod, "room_occupancy", lambda belief: {"Left": (0.0, 0.0), "Right": (1.0, 0.0)})
    mode = SearchMode()
    intent = mode.decide(_belief(), ActionState())  # self in Mid; candidates Left / Right
    assert intent.kind == "navigate_to"
    assert mode._target_room == "Right"


def test_pickroom_penalizes_recently_visited() -> None:
    # Anti-ping-pong: a room we just visited is penalized, so we pick the other.
    mode = SearchMode()
    belief = _belief()
    mode._last_visit_tick = {"Left": belief.last_tick}  # Left just visited
    intent = mode.decide(belief, ActionState())
    assert mode._target_room == "Right"


def test_pickroom_prefers_long_unvisited_room() -> None:
    # Unvisitedness grows over time so peripheral rooms get swept: long-stale Left beats fresh Right.
    mode = SearchMode()
    belief = _belief()
    belief.last_tick = 2000
    mode._last_visit_tick = {"Left": 900, "Right": 1980}  # Left stale (age 1100), Right just seen (age 20)
    intent = mode.decide(belief, ActionState())
    assert mode._target_room == "Left"


def test_pickroom_always_moves_never_idles() -> None:
    mode = SearchMode()
    intent = mode.decide(_belief(), ActionState())
    assert intent.kind == "navigate_to"  # PICK_ROOM must never emit idle


def test_commander_unknown_hunt_room_falls_back_to_random_pick() -> None:
    mode = SearchMode()
    belief = _belief()
    belief.commander = CommanderPriorities(hunt_room="Unknown", as_of_tick=belief.last_tick)
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert mode._target_room in {"Left", "Right"}


def test_commander_avoid_room_excludes_candidate_task_room() -> None:
    mode = SearchMode()
    belief = _belief()
    belief.commander = CommanderPriorities(avoid_room="Left", as_of_tick=belief.last_tick)
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert mode._target_room == "Right"


def test_commander_avoid_room_falls_back_when_filter_empties_candidates() -> None:
    mode = SearchMode()
    belief = _belief(self_xy=(10, 40))  # Left is current; only Right survives current/start exclusions.
    belief.commander = CommanderPriorities(avoid_room="Right", as_of_tick=belief.last_tick)
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert mode._target_room == "Right"


def test_go_to_room_follows_any_visible_crewmate() -> None:
    # NEW: seeing ANY crewmate (room or hallway) while in transit -> FOLLOW it.
    mode = SearchMode()
    mode._state = "go_to_room"
    mode._target_room = "Left"
    mode._goto_point = (40, 40)
    belief = _belief(self_xy=(120, 40))    # still en route (in Mid)
    _crew(belief, "green", (118, 40))       # a crewmate right next to us
    intent = mode.decide(belief, ActionState())
    assert mode._state == "follow"
    assert mode._follow_color == "green"
    assert intent.kind == "navigate_to"


def test_go_to_room_arrives_empty_then_scans() -> None:
    # NEW: arriving with no visible crew -> SEARCH_ROOM (sweep for hidden crew), never idle.
    mode = SearchMode()
    mode._state = "go_to_room"
    mode._target_room = "Left"
    mode._goto_point = (40, 40)
    belief = _belief(self_xy=(40, 40))      # arrived in Left, nobody in view
    intent = mode.decide(belief, ActionState())
    assert mode._state == "search_room"
    assert intent.kind == "navigate_to"      # moving to a scan point, not idling


def test_search_room_watches_when_crew_found() -> None:
    mode = SearchMode()
    mode._state = "search_room"
    mode._target_room = "Left"
    mode._scan_points = [(20, 20)]
    mode._scan_idx = 0
    belief = _belief(self_xy=(40, 40))
    _crew(belief, "green", (60, 60))         # crew hidden inside Left, now found
    mode.decide(belief, ActionState())
    assert mode._state == "watch"
    assert "green" in mode._room_crew


def test_search_room_swept_empty_repicks_never_idle() -> None:
    mode = SearchMode()
    mode._state = "search_room"
    mode._target_room = "Left"
    mode._scan_points = []                    # sweep already exhausted
    mode._scan_idx = 0
    belief = _belief(self_xy=(40, 40))        # no crew here
    intent = mode.decide(belief, ActionState())
    assert mode._prev_room == "Left"
    assert intent.kind == "navigate_to"        # re-picked another room; PICK_ROOM never idles


def test_follows_a_crewmate_who_leaves_the_room() -> None:
    mode = SearchMode()
    mode._state = "watch"
    mode._target_room = "Left"
    mode._goto_point = (40, 40)
    belief = _belief(self_xy=(40, 40))
    mode._room_crew = {"green"}
    _crew(belief, "green", (130, 40))  # green is now OUTSIDE Left (moved into Mid)
    intent = mode.decide(belief, ActionState())
    assert mode._state == "follow"
    assert mode._follow_color == "green"
    assert intent.kind == "navigate_to"


def test_commander_target_player_preferred_when_leaving_room() -> None:
    mode = SearchMode()
    belief = _belief(self_xy=(40, 40))
    belief.commander = CommanderPriorities(target_player="blue", as_of_tick=belief.last_tick)
    room = mode._room(belief, "Left")
    assert room is not None
    mode._room_crew = {"green", "blue"}
    _crew(belief, "green", (130, 40))
    _crew(belief, "blue", (135, 40))
    assert mode._a_crewmate_left(belief, room).color == "blue"


def test_commander_target_player_falls_back_when_not_a_valid_leaver() -> None:
    mode = SearchMode()
    belief = _belief(self_xy=(40, 40))
    belief.commander = CommanderPriorities(target_player="blue", as_of_tick=belief.last_tick)
    room = mode._room(belief, "Left")
    assert room is not None
    mode._room_crew = {"green", "blue"}
    green = _crew(belief, "green", (130, 40))
    _crew(belief, "blue", (60, 40))  # still inside the watched room, so not followable.
    assert mode._a_crewmate_left(belief, room) == green


def test_follow_uses_prediction_when_target_is_occluded() -> None:
    mode = SearchMode()
    belief = _belief(self_xy=(120, 40))
    _seed_occluded_follow(mode, belief)
    assert mode._state == "follow"
    # now occlude: green stops being seen this tick
    belief.last_tick = 40
    intent = mode.decide(belief, ActionState())
    assert intent.kind in {"navigate_to", "idle"}
    # the predictor should still hold candidates (chasing the predicted path)
    assert mode._predictor is not None


def test_default_follow_stops_after_lost_window() -> None:
    mode = SearchMode()
    belief = _belief(self_xy=(120, 40))
    _seed_occluded_follow(mode, belief)

    belief.last_tick = 140
    mode.decide(belief, ActionState())

    assert mode._follow_color is None
    assert mode._state != "follow"


def test_hard_commander_target_player_extends_lost_follow_window() -> None:
    mode = SearchMode()
    belief = _belief(self_xy=(120, 40))
    belief.commander = CommanderPriorities(target_player="green", strength="hard", as_of_tick=belief.last_tick)
    _seed_occluded_follow(mode, belief)

    belief.last_tick = 140
    intent = mode.decide(belief, ActionState())

    assert intent.kind == "navigate_to"
    assert mode._follow_color == "green"
    assert mode._state == "follow"


def test_hard_commander_non_target_player_uses_default_lost_follow_window() -> None:
    mode = SearchMode()
    belief = _belief(self_xy=(120, 40))
    belief.commander = CommanderPriorities(target_player="blue", strength="hard", as_of_tick=belief.last_tick)
    _seed_occluded_follow(mode, belief, color="green")

    belief.last_tick = 140
    mode.decide(belief, ActionState())

    assert mode._follow_color is None
    assert mode._state != "follow"


def test_visible_count_respects_line_of_sight() -> None:
    # A wall between the vantage and a crewmate blocks the line of sight.
    m = _map()
    walk = np.ones((m.height, m.width), dtype=bool)
    walk[:, 95:105] = False  # vertical wall splitting Left from Mid
    nav = build_nav_graph(walk, map_data=m)
    belief = Belief(map=m, nav=nav, self_role="imposter", self_world_x=40, self_world_y=40, last_tick=10)
    mode = SearchMode()
    # crew on the same side (Left, clear) vs across the wall (Mid, blocked)
    assert mode._visible_count(belief, (40, 40), [(60, 40)]) == 1     # same room, LOS clear
    assert mode._visible_count(belief, (40, 40), [(150, 40)]) == 0    # across the wall, blocked


def test_watch_multiple_crew_holds_a_vantage() -> None:
    # MULTIPLE crew in the room -> hold the vantage seeing the most (the one deliberate hold).
    mode = SearchMode()
    mode._state = "watch"
    mode._target_room = "Left"
    belief = _belief(self_xy=(10, 10))   # in a corner of Left
    mode._room_crew = {"green", "yellow"}
    _crew(belief, "green", (80, 60))
    _crew(belief, "yellow", (60, 70))    # TWO crew in Left
    mode.decide(belief, ActionState())
    assert mode._state == "watch"
    assert mode._vantage is not None


def test_watch_single_crew_closes_in_not_idle() -> None:
    # NEW: a lone crewmate is CLOSED ON (approach), never watched from afar / idled.
    mode = SearchMode()
    mode._state = "watch"
    mode._target_room = "Left"
    mode._room_crew = {"green"}
    belief = _belief(self_xy=(10, 10))
    _crew(belief, "green", (80, 60))     # ONE crew -> approach
    intent = mode.decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert "closing on the lone crewmate" in (intent.reason or "")


def test_never_follows_a_teammate() -> None:
    mode = SearchMode()
    mode._state = "watch"
    mode._target_room = "Left"
    mode._goto_point = (40, 40)
    belief = _belief(self_xy=(40, 40))
    belief.teammate_colors = {"red"}
    mode._room_crew = {"red"}
    _crew(belief, "red", (130, 40))  # the teammate "leaves" — must NOT be followed
    mode.decide(belief, ActionState())
    assert mode._follow_color != "red"


def test_follow_hands_off_to_search_room_when_caught_in_a_room() -> None:
    # NEW: while following a VISIBLE target, once we're in the SAME room as it (run down),
    # hand off to SEARCH_ROOM -> WATCH (a lone target then gets approached, not walked onto).
    mode = SearchMode()
    belief = _belief(self_xy=(60, 40))       # we're inside Left
    green = _crew(belief, "green", (75, 40))  # target also inside Left — we've caught up
    mode._begin_follow(belief, green)
    intent = mode.decide(belief, ActionState())
    assert mode._state == "watch"             # SEARCH_ROOM found green -> WATCH
    assert intent.kind == "navigate_to"       # single crew -> approach, not idle
