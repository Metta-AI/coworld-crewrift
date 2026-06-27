"""Victim-selection + witness logic tests (design §7.2, §10)."""

from __future__ import annotations

from crewborg.strategy.opportunity import (
    URGENCY_FULL_TICKS,
    kill_urgency_ticks,
    select_victim,
    unwitnessed,
)
from crewborg.types import Belief, PlayerRecord


def _crew(belief: Belief, object_id: int, xy: tuple[int, int], color: str, tick: int) -> None:
    belief.roster[color] = PlayerRecord(
        object_id=object_id,
        color=color,
        facing="left",
        world_x=xy[0],
        world_y=xy[1],
        last_seen_tick=tick,
        life_status="alive",
    )


# --- urgency ----------------------------------------------------------------


def test_kill_urgency_is_zero_until_kill_ready() -> None:
    assert kill_urgency_ticks(Belief(last_tick=100)) == 0
    assert kill_urgency_ticks(Belief(last_tick=100, self_kill_ready=True)) == 0  # since-tick unknown
    assert kill_urgency_ticks(Belief(last_tick=100, self_kill_ready=True, kill_ready_since_tick=70)) == 30


# --- select_victim ----------------------------------------------------------


def test_select_victim_prefers_the_isolated_straggler() -> None:
    # Two clustered crewmates and one straggler far from everyone ⇒ pick the straggler
    # (easiest to finish off unwitnessed), even though it's farther from us.
    belief = Belief(self_world_x=0, self_world_y=0, last_tick=5)
    _crew(belief, 1, (40, 0), "green", 5)  # clustered pair...
    _crew(belief, 2, (50, 0), "blue", 5)  # ...10px apart
    _crew(belief, 3, (300, 0), "white", 5)  # the straggler, far from the others
    v = select_victim(belief)
    assert v is not None and v.object_id == 3


def test_select_victim_prefers_unclaimed_target_when_teammate_is_closer() -> None:
    belief = Belief(self_world_x=0, self_world_y=0, last_tick=5, teammate_colors={"pink"})
    _crew(belief, 1, (100, 0), "green", 5)
    _crew(belief, 2, (0, 100), "blue", 5)
    _crew(belief, 3, (96, 0), "pink", 5)  # teammate is already closer to green

    v = select_victim(belief)
    assert v is not None and v.color == "blue"


def test_select_victim_still_takes_claimed_target_if_it_is_the_only_option() -> None:
    belief = Belief(self_world_x=0, self_world_y=0, last_tick=5, teammate_colors={"pink"})
    _crew(belief, 1, (100, 0), "green", 5)
    _crew(belief, 2, (96, 0), "pink", 5)  # teammate is closer, but there is no other victim

    v = select_victim(belief)
    assert v is not None and v.color == "green"


# --- unwitnessed ------------------------------------------------------------


def test_unwitnessed_true_for_a_lone_target() -> None:
    belief = Belief(self_world_x=0, self_world_y=0, last_tick=5)
    _crew(belief, 1, (50, 50), "green", 5)
    assert unwitnessed(belief, belief.roster["green"])


def test_unwitnessed_false_with_a_recent_nearby_witness() -> None:
    belief = Belief(self_world_x=0, self_world_y=0, last_tick=5)
    _crew(belief, 1, (50, 50), "green", 5)
    _crew(belief, 2, (60, 50), "blue", 5)  # 10px away, seen now ⇒ witness
    assert not unwitnessed(belief, belief.roster["green"])


def test_full_urgency_strikes_through_a_witness() -> None:
    belief = Belief(
        self_world_x=0, self_world_y=0, last_tick=URGENCY_FULL_TICKS,
        self_kill_ready=True, kill_ready_since_tick=0,
    )
    _crew(belief, 1, (50, 50), "green", URGENCY_FULL_TICKS)
    _crew(belief, 2, (60, 50), "blue", URGENCY_FULL_TICKS)  # witness ignored at full urgency
    assert unwitnessed(belief, belief.roster["green"])
