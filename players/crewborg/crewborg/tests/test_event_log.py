"""Per-player observation event log tests (design §5.2)."""

from __future__ import annotations

from crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation, Vent
from crewborg.strategy.event_log import update_event_log
from crewborg.types import Belief, PlayerRecord


def _map() -> MapData:
    return MapData(
        width=400, height=400,
        tasks=(TaskStation(name="t0", x=100, y=100, w=20, h=20),),  # rect [100,120)
        vents=(Vent(x=200, y=200, w=8, h=8, group="g", group_index=1),),  # rect [200,208)
        rooms=(Room(name="Cafeteria", x=0, y=0, w=150, h=150),),  # rect [0,150)
        button=MapRect(x=0, y=0, w=4, h=4), home=MapPoint(x=10, y=10),
    )


def _see(belief: Belief, color: str, xy: tuple[int, int], tick: int) -> None:
    """Simulate observing ``color`` at ``xy`` on ``tick``, then log the frame."""

    rec = belief.roster.get(color)
    if rec is None:
        rec = belief.roster[color] = PlayerRecord(color=color)
    rec.world_x, rec.world_y, rec.last_seen_tick, rec.life_status = xy[0], xy[1], tick, "alive"
    belief.last_tick = tick
    update_event_log(belief)


def test_task_dwell_accumulates_one_interval_while_visible() -> None:
    belief = Belief(map=_map())
    for tick in (1, 2, 3):
        _see(belief, "red", (110, 110), tick)  # inside the task rect each tick
    events = [e for e in belief.roster["red"].events if e.kind == "task"]
    assert len(events) == 1
    assert events[0].region_index == 0
    assert events[0].duration_ticks == 3 and (events[0].start_tick, events[0].end_tick) == (1, 3)


def test_room_and_vent_dwell_are_logged() -> None:
    belief = Belief(map=_map())
    _see(belief, "blue", (205, 205), 1)  # inside the vent rect (and not in the room)
    _see(belief, "green", (50, 50), 1)  # inside the Cafeteria room
    assert any(e.kind == "vent" and e.region_index == 0 for e in belief.roster["blue"].events)
    assert any(e.kind == "room" and e.region_index == 0 for e in belief.roster["green"].events)


def test_proximity_is_logged_for_both_players() -> None:
    belief = Belief(map=_map())
    # Two players 10px apart (within the 20px kill range) over two ticks.
    belief.roster["orange"] = PlayerRecord(color="orange")
    belief.roster["yellow"] = PlayerRecord(color="yellow")
    for tick in (1, 2):
        belief.roster["orange"].world_x, belief.roster["orange"].world_y = 300, 300
        belief.roster["orange"].last_seen_tick, belief.roster["orange"].life_status = tick, "alive"
        belief.roster["yellow"].world_x, belief.roster["yellow"].world_y = 310, 300
        belief.roster["yellow"].last_seen_tick, belief.roster["yellow"].life_status = tick, "alive"
        belief.last_tick = tick
        update_event_log(belief)

    o = [e for e in belief.roster["orange"].events if e.kind == "proximity"]
    y = [e for e in belief.roster["yellow"].events if e.kind == "proximity"]
    assert len(o) == 1 and o[0].target_color == "yellow" and o[0].duration_ticks == 2
    assert len(y) == 1 and y[0].target_color == "orange"


def test_tailing_self_accumulates_while_a_player_shadows_us() -> None:
    belief = Belief(map=_map())
    belief.self_world_x, belief.self_world_y = 300, 300
    for tick in (1, 2, 3):
        _see(belief, "red", (340, 300), tick)  # 40px from us — inside the tail radius (64px)
    tail = [e for e in belief.roster["red"].events if e.kind == "tailing_self"]
    assert len(tail) == 1
    assert tail[0].target_color is None  # None target = me
    assert tail[0].duration_ticks == 3 and (tail[0].start_tick, tail[0].end_tick) == (1, 3)
