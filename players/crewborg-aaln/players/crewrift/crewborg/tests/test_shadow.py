"""Follower ("tail") detection tests (strategy.shadow)."""

from __future__ import annotations

from players.crewrift.crewborg.strategy.shadow import (
    TAIL_GAP_GRACE_TICKS,
    TAIL_MIN_TICKS,
    TAIL_RADIUS,
    TAIL_STALE_TICKS,
    active_tail,
    update_tail_tracking,
)
from players.crewrift.crewborg.types import Belief, PlayerRecord


def _belief(tick: int = 0) -> Belief:
    return Belief(
        phase="Playing",
        self_role="crewmate",
        last_tick=tick,
        self_world_x=100,
        self_world_y=100,
    )


def _see(belief: Belief, color: str, x: int, y: int, *, tick: int | None = None) -> None:
    tick = belief.last_tick if tick is None else tick
    record = belief.roster.get(color)
    if record is None:
        record = belief.roster[color] = PlayerRecord(color=color)
    record.record(tick, x, y, "right", 1000)


def _shadow_for(belief: Belief, color: str, ticks: int, *, start: int = 0, dist: int = 50) -> None:
    """Simulate ``color`` observed within tail range for ``ticks`` consecutive ticks."""

    for t in range(start, start + ticks):
        belief.last_tick = t
        _see(belief, color, 100 + dist, 100)
        update_tail_tracking(belief)


def test_sustained_shadowing_becomes_an_active_tail() -> None:
    belief = _belief()
    _shadow_for(belief, "red", TAIL_MIN_TICKS + 2)
    assert active_tail(belief) == "red"


def test_brief_proximity_is_not_a_tail() -> None:
    belief = _belief()
    _shadow_for(belief, "red", TAIL_MIN_TICKS // 2)
    assert active_tail(belief) is None


def test_a_player_outside_tail_range_never_accrues_a_streak() -> None:
    belief = _belief()
    _shadow_for(belief, "red", TAIL_MIN_TICKS + 2, dist=TAIL_RADIUS + 40)
    assert active_tail(belief) is None


def test_los_flicker_within_grace_keeps_the_streak() -> None:
    belief = _belief()
    _shadow_for(belief, "red", TAIL_MIN_TICKS // 2)
    # A short unobserved gap (exactly the grace), then the follower re-appears.
    resume = TAIL_MIN_TICKS // 2 - 1 + TAIL_GAP_GRACE_TICKS
    _shadow_for(belief, "red", TAIL_MIN_TICKS, start=resume)
    assert active_tail(belief) == "red"


def test_a_long_gap_resets_the_streak() -> None:
    belief = _belief()
    _shadow_for(belief, "red", TAIL_MIN_TICKS - 10)
    resume = TAIL_MIN_TICKS + TAIL_STALE_TICKS + 50
    _shadow_for(belief, "red", TAIL_MIN_TICKS - 10, start=resume)
    assert active_tail(belief) is None  # neither run reached the bar on its own


def test_tail_response_ends_once_the_follower_breaks_off() -> None:
    belief = _belief()
    _shadow_for(belief, "red", TAIL_MIN_TICKS + 2)
    assert active_tail(belief) == "red"
    belief.last_tick += TAIL_STALE_TICKS + 1  # not seen in range since
    update_tail_tracking(belief)
    assert active_tail(belief) is None


def test_a_nearby_witness_suppresses_the_response() -> None:
    belief = _belief()
    _shadow_for(belief, "red", TAIL_MIN_TICKS + 2)
    _see(belief, "green", 120, 100)  # another live player right next to us
    assert active_tail(belief) is None


def test_meetings_and_other_roles_clear_the_streaks() -> None:
    belief = _belief()
    _shadow_for(belief, "red", TAIL_MIN_TICKS + 2)
    belief.phase = "Voting"
    update_tail_tracking(belief)
    assert belief.tail_streaks == {}

    imposter = _belief()
    imposter.self_role = "imposter"
    _see(imposter, "red", 150, 100)
    update_tail_tracking(imposter)
    assert imposter.tail_streaks == {}
    assert active_tail(imposter) is None


def test_a_dead_tail_is_dropped() -> None:
    belief = _belief()
    _shadow_for(belief, "red", TAIL_MIN_TICKS + 2)
    belief.roster["red"].life_status = "dead"
    update_tail_tracking(belief)
    assert active_tail(belief) is None
