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
from crewborg.types import ActionState, Belief, PlayerRecord


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


def _belief(self_xy=(150, 40), tick=10) -> Belief:
    m = _map()
    nav = build_nav_graph(np.ones((m.height, m.width), dtype=bool), map_data=m)
    return Belief(map=m, nav=nav, self_role="imposter", self_world_x=self_xy[0], self_world_y=self_xy[1], last_tick=tick)


def _crew(belief: Belief, color: str, xy, tick=None) -> PlayerRecord:
    rec = PlayerRecord(color=color, world_x=xy[0], world_y=xy[1],
                       last_seen_tick=belief.last_tick if tick is None else tick, life_status="alive")
    belief.roster[color] = rec
    return rec


def _seed_occluded_follow(mode: SearchMode, belief: Belief, color: str = "green") -> PlayerRecord:
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


def test_watches_when_crew_are_in_the_target_room() -> None:
    mode = SearchMode()
    mode._state = "go_to_room"
    mode._target_room = "Left"
    mode._goto_point = (40, 40)
    belief = _belief(self_xy=(40, 40))  # we've arrived in Left
    _crew(belief, "green", (60, 40))     # a crewmate is in Left
    mode.decide(belief, ActionState())
    assert mode._state == "watch"
    assert "green" in mode._room_crew


def test_empty_room_repicks() -> None:
    mode = SearchMode()
    mode._state = "go_to_room"
    mode._target_room = "Left"
    mode._goto_point = (40, 40)
    belief = _belief(self_xy=(40, 40))  # arrived, but no crew here
    mode.decide(belief, ActionState())
    assert mode._state in {"pick_room", "go_to_room"}  # re-dispatched to another room
    assert mode._prev_room == "Left"


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
