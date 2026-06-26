"""Per-player observation event log tests (design §5.2)."""

from __future__ import annotations

from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation, Vent
from players.crewrift.crewborg.strategy.event_log import EVENT_MERGE_GRACE_TICKS, update_event_log
from players.crewrift.crewborg.types import Belief, BodyEntry, PlayerRecord


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


def test_leaving_and_returning_splits_into_two_intervals() -> None:
    belief = Belief(map=_map())
    _see(belief, "red", (110, 110), 1)  # in task
    _see(belief, "red", (300, 300), 2)  # left
    _see(belief, "red", (110, 110), 3)  # back in task
    tasks = [e for e in belief.roster["red"].events if e.kind == "task"]
    assert len(tasks) == 2 and all(e.duration_ticks == 1 for e in tasks)


def test_a_brief_unobserved_gap_is_bridged() -> None:
    belief = Belief(map=_map())
    _see(belief, "red", (110, 110), 1)
    # A gap within the grace window (we lost sight, didn't see them elsewhere).
    _see(belief, "red", (110, 110), 1 + EVENT_MERGE_GRACE_TICKS)
    tasks = [e for e in belief.roster["red"].events if e.kind == "task"]
    assert len(tasks) == 1 and tasks[0].end_tick == 1 + EVENT_MERGE_GRACE_TICKS


def test_a_long_unobserved_gap_splits_the_interval() -> None:
    belief = Belief(map=_map())
    _see(belief, "red", (110, 110), 1)
    _see(belief, "red", (110, 110), 2 + EVENT_MERGE_GRACE_TICKS)  # gap exceeds grace
    tasks = [e for e in belief.roster["red"].events if e.kind == "task"]
    assert len(tasks) == 2


def test_room_and_vent_dwell_are_logged() -> None:
    belief = Belief(map=_map())
    _see(belief, "blue", (205, 205), 1)  # inside the vent rect (and not in the room)
    _see(belief, "green", (50, 50), 1)  # inside the Cafeteria room
    assert any(e.kind == "vent" and e.region_index == 0 for e in belief.roster["blue"].events)
    assert any(e.kind == "room" and e.region_index == 0 for e in belief.roster["green"].events)


def test_near_body_logs_target_color_and_closest_approach() -> None:
    belief = Belief(map=_map())
    belief.bodies[2001] = BodyEntry(object_id=2001, color="yellow", world_x=300, world_y=300, first_seen_tick=1)
    _see(belief, "orange", (330, 300), 1)  # 30px from the body
    _see(belief, "orange", (310, 300), 2)  # closes to 10px
    near = [e for e in belief.roster["orange"].events if e.kind == "near_body"]
    assert len(near) == 1
    assert near[0].target_color == "yellow" and near[0].min_dist == 10  # closest approach kept


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


def test_following_then_death_is_a_query_over_the_log() -> None:
    # The compound "orange followed yellow, who then died" is not its own event —
    # it's a proximity event toward yellow plus yellow's life status.
    belief = Belief(map=_map())
    belief.roster["orange"] = PlayerRecord(color="orange", world_x=300, world_y=300, last_seen_tick=1, life_status="alive")
    belief.roster["yellow"] = PlayerRecord(color="yellow", world_x=308, world_y=300, last_seen_tick=1, life_status="alive")
    belief.last_tick = 1
    update_event_log(belief)
    belief.roster["yellow"].life_status = "dead"

    followed = [
        e for e in belief.roster["orange"].events
        if e.kind == "proximity" and e.target_color == "yellow"
    ]
    assert followed and belief.roster["yellow"].life_status == "dead"


def test_dead_players_are_not_logged() -> None:
    belief = Belief(map=_map())
    belief.roster["red"] = PlayerRecord(color="red", world_x=110, world_y=110, last_seen_tick=5, life_status="dead")
    belief.last_tick = 5
    update_event_log(belief)
    assert belief.roster["red"].events == []
