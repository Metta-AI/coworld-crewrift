"""Suspicion tests: witnessed catches, the legacy hand model, the fitted model.

The graded-shape tests below assert the LEGACY hand-written functions (the fallback
when no weights load), so an autouse fixture pins that path; the fitted path —
production default once ``data/suspicion_weights.json`` vendored — has its own
relational tests at the bottom (``TestFittedModel``).
"""

from __future__ import annotations

import numpy as np
import pytest

from crewborg.map.types import MapData, MapPoint, MapRect, Vent
from crewborg.strategy import suspicion as suspicion_module
from crewborg.strategy.suspicion import (
    BODY_FADE_TICKS,
    active_tail_suspect,
    top_suspect,
    update_suspicion,
)
from crewborg.types import Belief, PerceptionFrame, PlayerEvent, PlayerRecord


@pytest.fixture(autouse=True)
def _legacy_hand_model():
    """Pin the legacy path: these tests assert the hand-written log-LR shapes."""

    saved = suspicion_module._WEIGHTS
    suspicion_module.set_weights(None)
    yield
    suspicion_module.set_weights(saved)


def _frame(tick: int, players=None, bodies=None, camera=(0, 0), mask=None) -> PerceptionFrame:
    return PerceptionFrame(
        tick=tick, camera_x=camera[0], camera_y=camera[1],
        players=dict(players or {}), bodies=dict(bodies or {}), visible_mask=mask,
    )


def _belief(prev: PerceptionFrame, curr: PerceptionFrame, **kwargs) -> Belief:
    kwargs.setdefault("self_role", "crewmate")
    return Belief(last_tick=curr.tick, recent_frames=[prev, curr], **kwargs)


def _vent_map() -> MapData:
    return MapData(
        width=200, height=200, tasks=(),
        vents=(Vent(x=50, y=50, w=8, h=8, group="g", group_index=1),),  # rect [50,58)x[50,58)
        rooms=(), button=MapRect(x=0, y=0, w=4, h=4), home=MapPoint(x=10, y=10),
    )


# --- witnessed kill / vent: the definitional floor --------------------------


def test_lone_neighbor_of_a_just_killed_victim_is_confirmed() -> None:
    prev = _frame(4, players={"red": (100, 100), "green": (110, 100)})  # together, alive
    curr = _frame(5, players={"green": (110, 100)}, bodies={"red": (100, 100)})  # red now a body
    belief = _belief(prev, curr)
    update_suspicion(belief)
    assert "green" in belief.believed_imposters


def test_player_emerging_into_a_watched_clear_vent_is_confirmed() -> None:
    prev = _frame(4, players={}, camera=(0, 0))  # vent + margin in view, no one near it
    curr = _frame(5, players={"red": (53, 53)}, camera=(0, 0))  # now inside the vent rect
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert "red" in belief.believed_imposters


def test_player_vanishing_from_a_visible_vent_is_confirmed() -> None:
    prev = _frame(4, players={"red": (53, 53)}, camera=(0, 0))  # standing in the vent rect
    curr = _frame(5, players={}, camera=(0, 0))  # gone, but the vent is still in view
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert "red" in belief.believed_imposters


def test_emergence_is_suppressed_when_the_vent_is_occluded() -> None:
    # A false confirmation through a wall is a catastrophic wrong-vote regression.
    occluded = np.ones((128, 128), dtype=bool)
    occluded[47:61, 47:61] = False  # vent + walk margin out of line of sight
    prev = _frame(4, players={}, camera=(0, 0), mask=occluded)  # "clear" only because occluded
    curr = _frame(5, players={"red": (53, 53)}, camera=(0, 0), mask=np.ones((128, 128), dtype=bool))
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert not belief.believed_imposters  # couldn't actually see the vent was clear


# --- Bayesian posterior: prior + per-event graded log-LRs (tier 2) ----------


def _vent_dwell(start: int = 1, dur: int = 10) -> PlayerEvent:
    return PlayerEvent(kind="vent", start_tick=start, end_tick=start + dur, region_index=0)


def _near_body(start: int = 50, dur: int = 1, dist: int = 8) -> PlayerEvent:
    return PlayerEvent(
        kind="near_body", start_tick=start, end_tick=start + dur, target_color="blue", min_dist=dist
    )


def _crew_belief(total_players: int = 8) -> Belief:
    # A neutral crewmate scene with a known player count, so the prior is meaningful.
    return Belief(self_role="crewmate", last_tick=200, total_player_count=total_players)


def _add(belief: Belief, color: str, events=()) -> None:
    belief.roster[color] = PlayerRecord(color=color, life_status="alive", events=list(events))


def test_no_evidence_player_sits_at_the_combinatorial_prior() -> None:
    belief = _crew_belief(total_players=8)  # 8 players ⇒ 2 imposters among the other 7
    _add(belief, "red")
    update_suspicion(belief)
    assert belief.suspicion["red"] == pytest.approx(2 / 7)
    assert "red" not in belief.believed_imposters


def test_one_graded_signal_raises_the_posterior_but_does_not_flee() -> None:
    belief = _crew_belief()
    _add(belief, "red", [_vent_dwell()])
    _add(belief, "blue")  # a no-evidence baseline
    update_suspicion(belief)
    assert belief.suspicion["red"] > belief.suspicion["blue"]  # evidence moved the posterior up
    assert "red" not in belief.believed_imposters  # ...but a single cue isn't near-certain


def test_body_proximity_is_more_suspicious_when_brief_than_when_camped() -> None:
    # A skilled imposter flees; a long camp at a corpse is reporter behaviour. So the
    # body-proximity log-LR DECREASES with dwell — the headline of the per-event shape.
    belief = _crew_belief()
    _add(belief, "red", [_near_body(dur=1)])  # a brief glimpse next to the body
    _add(belief, "green", [_near_body(dur=BODY_FADE_TICKS)])  # camped until the cue fades to 0
    _add(belief, "blue")  # baseline (prior)
    update_suspicion(belief)
    assert belief.suspicion["red"] > belief.suspicion["green"]
    assert belief.suspicion["green"] == pytest.approx(belief.suspicion["blue"])  # long camp ⇒ neutral


# --- being tailed (tailing_self) → active_tail_suspect (Accuse target) -------


def _tail(dur: int, start: int = 1) -> PlayerEvent:
    return PlayerEvent(kind="tailing_self", start_tick=start, end_tick=start + dur, target_color=None)


def test_active_tail_suspect_fires_only_for_a_live_tail_over_the_bar() -> None:
    belief = _crew_belief()  # last_tick = 200
    _add(belief, "red", [_tail(dur=50, start=150)])  # ends at 200 — a live, sustained tail
    _add(belief, "blue", [_tail(dur=50, start=1)])  # ends at 51 — same strength, but lapsed
    update_suspicion(belief)
    assert active_tail_suspect(belief) == "red"  # only the live tail triggers Accuse


# --- never suspect / vote / accuse ourselves --------------------------------


def test_our_own_color_is_never_scored() -> None:
    # The self-sprite leaks into the roster as our own colour; it must never be scored
    # (else we tail/suspect/vote ourselves — the crew-loss bug).
    belief = _crew_belief()
    belief.self_color = "red"
    _add(belief, "red", [_vent_dwell(), _tail(dur=50, start=150)])  # heavy self-evidence
    _add(belief, "blue")
    update_suspicion(belief)
    assert "red" not in belief.suspicion  # ourselves, never scored
    assert "blue" in belief.suspicion


def test_top_suspect_never_returns_self() -> None:
    # Hard guard: even if our colour is somehow in the posterior, we never vote ourself.
    belief = Belief(self_role="crewmate", self_color="red")
    belief.suspicion = {"red": 0.99, "blue": 0.2}  # self forced highest
    assert top_suspect(belief) != "red"
    assert top_suspect(belief) is None  # blue (0.2) doesn't clear the bar ⇒ skip


# --- top_suspect (voting target) --------------------------------------------


def test_top_suspect_picks_a_near_certain_suspect_regardless_of_the_field() -> None:
    belief = Belief(self_role="crewmate")
    belief.suspicion = {"red": 0.95, "blue": 0.99}  # both near-certain (e.g. two catches)
    assert top_suspect(belief) == "blue"  # the most suspicious clears the absolute bar


def test_top_suspect_fires_on_a_clear_leader_below_near_certainty() -> None:
    belief = Belief(self_role="crewmate")
    belief.suspicion = {"red": 0.7, "blue": 0.3}  # red short of 0.8 but a clear lead
    assert top_suspect(belief) == "red"  # vote the clear leading suspect


# --- the fitted model (data/suspicion_weights.json; suspicion-learning.md) ----
#
# Relational assertions only — they must survive a re-fit on more games, so they
# test structure (loading, definitional floor, legacy fallback), never weights.


@pytest.fixture()
def _fitted_model():
    weights = suspicion_module._load_weights()
    assert weights is not None, "vendored data/suspicion_weights.json failed to load"
    suspicion_module.set_weights(weights)
    yield weights
    suspicion_module.set_weights(None)


def _p(belief: Belief, color: str) -> float:
    update_suspicion(belief)
    return belief.suspicion[color]


class TestFittedModel:
    def test_vendored_weights_load_and_declare_the_schema(self, _fitted_model) -> None:
        assert _fitted_model["schema"] == "crewborg-suspicion-weights/v1"
        assert _fitted_model["coefficients"]

    def test_witnessed_kill_is_a_definitional_near_certainty_and_votable(self, _fitted_model) -> None:
        belief = _crew_belief()
        _add(belief, "red", [PlayerEvent(kind="kill", start_tick=50, end_tick=50, target_color="cyan")])
        _add(belief, "blue")
        assert _p(belief, "red") > 0.99
        assert top_suspect(belief) == "red"

    def test_env_zero_forces_the_legacy_hand_model(self, monkeypatch) -> None:
        monkeypatch.setenv("CREWBORG_SUSPICION_WEIGHTS", "0")
        assert suspicion_module._load_weights() is None
