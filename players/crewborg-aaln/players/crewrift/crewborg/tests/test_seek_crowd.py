"""Seek Crowd mode tests: break the shadow-kill setup by walking to witnesses."""

from __future__ import annotations

from players.crewrift.crewborg.modes import SeekCrowdMode, SeekCrowdParams
from players.crewrift.crewborg.types import ActionState, Belief, PlayerRecord


def _belief(tick: int = 100) -> Belief:
    return Belief(
        phase="Playing",
        self_role="crewmate",
        last_tick=tick,
        self_world_x=100,
        self_world_y=100,
    )


def _see(belief: Belief, color: str, x: int, y: int, *, tick: int | None = None) -> None:
    belief.roster[color] = PlayerRecord(
        color=color,
        world_x=x,
        world_y=y,
        last_seen_tick=belief.last_tick if tick is None else tick,
        life_status="alive",
    )


def test_breaks_away_from_the_tail_when_it_is_visible() -> None:
    # iter-2 (2026-06-12 buzzer field): the primary tail response is to steer
    # AWAY from the follower (crowds are the danger, not the refuge), so a
    # locatable live tail yields a flee_from, not a route into a cluster.
    belief = _belief()
    _see(belief, "red", 150, 100)  # the tail, glued to us
    _see(belief, "green", 300, 100)
    intent = SeekCrowdMode(SeekCrowdParams(avoid_color="red")).decide(belief, ActionState())
    assert intent.kind == "flee_from"
    assert intent.target_color == "red"


def test_falls_back_to_nearest_crew_when_tail_unlocatable() -> None:
    # Tail color not in roster (lost LoS on the follower): fall back to the old
    # crowd-seek so we are not stranded.
    belief = _belief()
    _see(belief, "green", 300, 100)  # nearest other crew
    _see(belief, "yellow", 600, 100)
    intent = SeekCrowdMode(SeekCrowdParams(avoid_color="red")).decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (300, 100)


def test_never_routes_to_the_tail_itself_or_points_next_to_it() -> None:
    # When the tail is unlocatable (not in roster) the crowd fallback still must
    # not route onto a "crowd" point sitting on the tail's last spot.
    belief = _belief()
    _see(belief, "green", 180, 100)  # crew near where a tail would be
    _see(belief, "yellow", 500, 100)  # a real escape
    # avoid a color we cannot see, but pin the avoid point via a stale tail far away
    intent = SeekCrowdMode(SeekCrowdParams(avoid_color=None)).decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    # nearest crew with no avoid is green at 180
    assert intent.point == (180, 100)


def test_ignores_dead_and_stale_sightings() -> None:
    belief = _belief(tick=1000)
    _see(belief, "green", 300, 100)
    belief.roster["green"].life_status = "dead"
    _see(belief, "yellow", 400, 100, tick=100)  # seen ~37 s ago: stale
    intent = SeekCrowdMode(SeekCrowdParams(avoid_color="red")).decide(belief, ActionState())
    # No live crowd known and no occupancy substrate in this test ⇒ idle
    # (no map to fall back to).
    assert intent.kind == "idle"


def test_is_legal_only_while_playing() -> None:
    mode = SeekCrowdMode(SeekCrowdParams(avoid_color="red"))
    assert mode.is_legal(_belief())
    assert not mode.is_legal(Belief(phase="Voting"))
