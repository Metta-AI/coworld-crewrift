from __future__ import annotations

from crewborg.map.types import MapData, MapPoint, MapRect, Room
from crewborg.strategy.commander.bias import commander_of, filter_or_fallback, room_crew_count
from crewborg.types import Belief, CommanderPriorities, PlayerRecord


def test_filter_or_fallback_keeps_matches() -> None:
    assert filter_or_fallback([1, 2, 3, 4], lambda value: value % 2 == 0) == [2, 4]


def test_filter_or_fallback_falls_back_when_empty() -> None:
    assert filter_or_fallback([1, 3, 5], lambda value: value % 2 == 0) == [1, 3, 5]


def test_commander_of_fresh() -> None:
    belief = Belief(last_tick=100, commander=CommanderPriorities(target_room="electrical", as_of_tick=50))

    commander = commander_of(belief)

    assert commander is not None
    assert commander.target_room == "electrical"


def test_commander_of_stale_returns_none() -> None:
    belief = Belief(last_tick=10_000, commander=CommanderPriorities(target_room="electrical", as_of_tick=50))

    assert commander_of(belief) is None


def test_commander_of_none() -> None:
    assert commander_of(Belief()) is None


def test_room_crew_count_counts_visible_live_non_teammates_in_room() -> None:
    belief = Belief(
        last_tick=20,
        map=MapData(
            width=200,
            height=200,
            tasks=(),
            vents=(),
            rooms=(Room(name="electrical", x=0, y=0, w=100, h=100),),
            button=MapRect(x=0, y=0, w=8, h=8),
            home=MapPoint(x=0, y=0),
        ),
        teammate_colors={"purple"},
    )
    belief.roster["red"] = PlayerRecord(color="red", world_x=10, world_y=10, last_seen_tick=20, life_status="alive")
    belief.roster["blue"] = PlayerRecord(color="blue", world_x=20, world_y=20, last_seen_tick=19, life_status="alive")
    belief.roster["green"] = PlayerRecord(color="green", world_x=30, world_y=30, last_seen_tick=20, life_status="dead")
    belief.roster["purple"] = PlayerRecord(
        color="purple", world_x=40, world_y=40, last_seen_tick=20, life_status="alive"
    )

    assert room_crew_count(belief, "electrical") == 1
