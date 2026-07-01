from __future__ import annotations

from crewborg.map.types import MapData, MapPoint, MapRect, Room
from crewborg.strategy.commander.context import (
    legal_players,
    legal_rooms,
    serialize_commander_context,
)
from crewborg.strategy.commander.schema import sanitize_priorities
from crewborg.types import Belief, BodyEntry, PlayerRecord

LEGAL_ROOMS = {"electrical", "medbay"}
LEGAL_PLAYERS = {"red", "blue"}


def test_unknown_room_dropped() -> None:
    priorities = sanitize_priorities({"target_room": "atlantis"}, LEGAL_ROOMS, LEGAL_PLAYERS, as_of_tick=5)

    assert priorities.target_room is None
    assert priorities.as_of_tick == 5


def test_known_room_kept() -> None:
    priorities = sanitize_priorities({"hunt_room": "medbay"}, LEGAL_ROOMS, LEGAL_PLAYERS, as_of_tick=5)

    assert priorities.hunt_room == "medbay"


def test_unknown_player_dropped() -> None:
    priorities = sanitize_priorities({"target_player": "purple"}, LEGAL_ROOMS, LEGAL_PLAYERS, as_of_tick=5)

    assert priorities.target_player is None


def test_invalid_posture_returns_neutral() -> None:
    priorities = sanitize_priorities({"posture": "panic"}, LEGAL_ROOMS, LEGAL_PLAYERS, as_of_tick=5)

    assert priorities.posture == "neutral"


def test_target_task_accepts_int_but_not_bool() -> None:
    kept = sanitize_priorities({"target_task": 4}, LEGAL_ROOMS, LEGAL_PLAYERS, as_of_tick=5)
    dropped = sanitize_priorities({"target_task": True}, LEGAL_ROOMS, LEGAL_PLAYERS, as_of_tick=5)

    assert kept.target_task == 4
    assert dropped.target_task is None


def test_danger_without_reason_dropped() -> None:
    priorities = sanitize_priorities({"allow_witnessed_kill": True}, LEGAL_ROOMS, LEGAL_PLAYERS, as_of_tick=5)

    assert priorities.allow_witnessed_kill is False
    assert priorities.danger_reason is None


def test_danger_with_reason_kept() -> None:
    priorities = sanitize_priorities(
        {"skip_evade": True, "danger_reason": "last imposter, must chain kills"},
        LEGAL_ROOMS,
        LEGAL_PLAYERS,
        as_of_tick=5,
    )

    assert priorities.skip_evade is True
    assert priorities.danger_reason == "last imposter, must chain kills"


def test_context_serializes_legal_state_and_active_mode() -> None:
    belief = Belief(
        phase="Playing",
        last_tick=80,
        self_role="imposter",
        self_color="black",
        self_kill_ready=False,
        self_world_x=20,
        self_world_y=20,
        kill_cooldown_start_tick=60,
        kill_cooldown_estimate=50,
        teammate_colors={"purple"},
        map=MapData(
            width=200,
            height=200,
            tasks=(),
            vents=(),
            rooms=(
                Room(name="electrical", x=0, y=0, w=100, h=100),
                Room(name="medbay", x=100, y=0, w=100, h=100),
            ),
            button=MapRect(x=0, y=0, w=8, h=8),
            home=MapPoint(x=0, y=0),
        ),
    )
    belief.roster["red"] = PlayerRecord(color="red", world_x=10, world_y=10, last_seen_tick=80, life_status="alive")
    belief.roster["blue"] = PlayerRecord(color="blue", world_x=120, world_y=20, last_seen_tick=70, life_status="dead")
    belief.roster["purple"] = PlayerRecord(
        color="purple", world_x=30, world_y=30, last_seen_tick=80, life_status="alive"
    )
    belief.bodies[2004] = BodyEntry(object_id=2004, color="blue", world_x=120, world_y=20, first_seen_tick=75)

    context = serialize_commander_context(belief, active_mode="search")

    assert legal_rooms(belief) == ["electrical", "medbay"]
    assert legal_players(belief) == ["red"]
    assert context["phase"] == "Playing"
    assert context["self"]["room"] == "electrical"
    assert context["self"]["ticks_until_kill_ready"] == 30
    assert context["legal_rooms"] == ["electrical", "medbay"]
    assert context["legal_players"] == ["red"]
    assert context["roster"]["red"]["room"] == "electrical"
    assert context["roster"]["blue"]["alive"] is False
    assert context["bodies"] == [{"color": "blue", "room": "medbay", "x": 120, "y": 20}]
    assert context["active_mode"] == "search"
