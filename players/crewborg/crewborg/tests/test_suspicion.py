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
    CHAT_SUSPECT_MIN_P,
    FLEE_PROBABILITY,
    FOLLOW_FULL_TICKS,
    VENT_CROSS_TICKS,
    WEIGHTS_VOTE_PROBABILITY,
    active_tail_suspect,
    chat_suspect,
    top_suspect,
    update_suspicion,
    witnessed_imposters,
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


# --- witnessed kill ---------------------------------------------------------


def test_lone_neighbor_of_a_just_killed_victim_is_confirmed() -> None:
    prev = _frame(4, players={"red": (100, 100), "green": (110, 100)})  # together, alive
    curr = _frame(5, players={"green": (110, 100)}, bodies={"red": (100, 100)})  # red now a body
    belief = _belief(prev, curr)
    update_suspicion(belief)
    assert "green" in belief.believed_imposters


def test_kill_is_not_attributed_when_two_players_were_in_range() -> None:
    prev = _frame(4, players={"red": (100, 100), "green": (110, 100), "blue": (115, 100)})
    curr = _frame(5, players={"green": (110, 100), "blue": (115, 100)}, bodies={"red": (100, 100)})
    belief = _belief(prev, curr)
    update_suspicion(belief)
    assert not belief.believed_imposters  # ambiguous → no accusation


def test_kill_with_no_visible_neighbor_implicates_no_one() -> None:
    prev = _frame(4, players={"red": (100, 100), "green": (200, 100)})  # green far (>kill range)
    curr = _frame(5, players={"green": (200, 100)}, bodies={"red": (100, 100)})
    belief = _belief(prev, curr)
    update_suspicion(belief)
    assert not belief.believed_imposters


def test_non_consecutive_frames_are_not_read_as_a_kill() -> None:
    prev = _frame(2, players={"red": (100, 100), "green": (110, 100)})  # a meeting-sized gap
    curr = _frame(5, players={"green": (110, 100)}, bodies={"red": (100, 100)})
    belief = _belief(prev, curr)
    update_suspicion(belief)
    assert not belief.believed_imposters


def test_imposter_scores_deflection_suspicion_on_non_teammates() -> None:
    # An imposter now computes suspicion over non-teammates (its deflection candidates):
    # green looks like the killer of red, so the imposter could cite that at a meeting.
    prev = _frame(4, players={"red": (100, 100), "green": (110, 100)})
    curr = _frame(5, players={"green": (110, 100)}, bodies={"red": (100, 100)})
    belief = _belief(prev, curr, self_role="imposter")
    update_suspicion(belief)
    assert belief.suspicion.get("green", 0.0) > 0.99 and "green" in belief.believed_imposters


def test_imposter_never_scores_a_teammate() -> None:
    # The same scene, but green is a known teammate ⇒ excluded entirely (we never
    # deflect onto our own).
    prev = _frame(4, players={"red": (100, 100), "green": (110, 100)})
    curr = _frame(5, players={"green": (110, 100)}, bodies={"red": (100, 100)})
    belief = _belief(prev, curr, self_role="imposter")
    belief.teammate_colors = {"green"}
    update_suspicion(belief)
    assert "green" not in belief.suspicion and not belief.believed_imposters


# --- witnessed vent: emergence (a) ------------------------------------------


def test_player_emerging_into_a_watched_clear_vent_is_confirmed() -> None:
    prev = _frame(4, players={}, camera=(0, 0))  # vent + margin in view, no one near it
    curr = _frame(5, players={"red": (53, 53)}, camera=(0, 0))  # now inside the vent rect
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert "red" in belief.believed_imposters


def test_emergence_requires_the_vent_to_have_been_watched() -> None:
    prev = _frame(4, players={}, camera=(400, 400))  # vent off-screen last frame
    curr = _frame(5, players={"red": (53, 53)}, camera=(0, 0))
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert not belief.believed_imposters  # we weren't watching → can't conclude emergence


def test_a_player_near_the_vent_last_frame_blocks_an_emergence_call() -> None:
    prev = _frame(4, players={"red": (48, 53)}, camera=(0, 0))  # within the walk margin of the vent
    curr = _frame(5, players={"red": (53, 53)}, camera=(0, 0))  # could have walked in
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert not belief.believed_imposters


# --- witnessed vent: submersion (b) -----------------------------------------


def test_player_vanishing_from_a_visible_vent_is_confirmed() -> None:
    prev = _frame(4, players={"red": (53, 53)}, camera=(0, 0))  # standing in the vent rect
    curr = _frame(5, players={}, camera=(0, 0))  # gone, but the vent is still in view
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert "red" in belief.believed_imposters


def test_submersion_requires_the_vent_to_still_be_in_view() -> None:
    prev = _frame(4, players={"red": (53, 53)}, camera=(0, 0))
    curr = _frame(5, players={}, camera=(400, 400))  # vent off-screen now → maybe just walked off
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert not belief.believed_imposters


def test_a_player_standing_on_a_vent_is_not_a_venter() -> None:
    prev = _frame(4, players={"red": (53, 53)}, camera=(0, 0))
    curr = _frame(5, players={"red": (54, 53)}, camera=(0, 0))  # still visible on the vent
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert not belief.believed_imposters


# --- line-of-sight gating (the decoded shadow mask) -------------------------


def test_emergence_is_suppressed_when_the_vent_is_occluded() -> None:
    occluded = np.ones((128, 128), dtype=bool)
    occluded[47:61, 47:61] = False  # vent + walk margin out of line of sight
    prev = _frame(4, players={}, camera=(0, 0), mask=occluded)  # "clear" only because occluded
    curr = _frame(5, players={"red": (53, 53)}, camera=(0, 0), mask=np.ones((128, 128), dtype=bool))
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert not belief.believed_imposters  # couldn't actually see the vent was clear


def test_emergence_fires_when_the_vent_is_truly_in_sight() -> None:
    lit = np.ones((128, 128), dtype=bool)
    prev = _frame(4, players={}, camera=(0, 0), mask=lit)
    curr = _frame(5, players={"red": (53, 53)}, camera=(0, 0), mask=lit)
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert "red" in belief.believed_imposters


def test_submersion_is_suppressed_when_the_vent_is_occluded_now() -> None:
    prev = _frame(4, players={"red": (53, 53)}, camera=(0, 0), mask=np.ones((128, 128), dtype=bool))
    occluded = np.ones((128, 128), dtype=bool)
    occluded[50:58, 50:58] = False  # the vent is no longer in sight this frame
    curr = _frame(5, players={}, camera=(0, 0), mask=occluded)
    belief = _belief(prev, curr, map=_vent_map())
    update_suspicion(belief)
    assert not belief.believed_imposters  # player gone, but maybe they just walked behind a wall


# --- Bayesian posterior: prior + per-event graded log-LRs (tier 2) ----------


def _vent_dwell(start: int = 1, dur: int = 10) -> PlayerEvent:
    return PlayerEvent(kind="vent", start_tick=start, end_tick=start + dur, region_index=0)


def _near_body(start: int = 50, dur: int = 1, dist: int = 8) -> PlayerEvent:
    return PlayerEvent(
        kind="near_body", start_tick=start, end_tick=start + dur, target_color="blue", min_dist=dist
    )


def _long_follow(start: int = 40, target: str = "yellow") -> PlayerEvent:
    return PlayerEvent(
        kind="proximity", start_tick=start, end_tick=start + FOLLOW_FULL_TICKS, target_color=target, min_dist=10
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


def test_corroborating_graded_signals_cross_the_flee_bar() -> None:
    belief = _crew_belief()
    _add(belief, "red", [_vent_dwell(), _long_follow()])
    belief.roster["yellow"] = PlayerRecord(color="yellow", life_status="dead", death_seen_tick=40 + FOLLOW_FULL_TICKS)
    update_suspicion(belief)
    assert belief.suspicion["red"] >= FLEE_PROBABILITY and "red" in belief.believed_imposters


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


def test_pass_through_and_distant_cues_are_neutral() -> None:
    belief = _crew_belief()
    _add(belief, "red", [
        _vent_dwell(dur=VENT_CROSS_TICKS - 1),  # duration = VENT_CROSS_TICKS ⇒ just crossing the tile
        _near_body(dur=1, dist=40),  # too far from the body
        PlayerEvent(kind="proximity", start_tick=20, end_tick=24, target_color="green", min_dist=5),  # victim alive
    ])
    _add(belief, "blue")  # baseline
    update_suspicion(belief)
    assert belief.suspicion["red"] == pytest.approx(belief.suspicion["blue"])  # nothing moved the prior


def test_following_a_victim_to_death_only_updates_when_they_died() -> None:
    alive = _crew_belief()
    _add(alive, "orange", [_long_follow()])
    _add(alive, "yellow")  # victim still alive ⇒ no evidence
    update_suspicion(alive)
    assert alive.suspicion["orange"] == pytest.approx(alive.suspicion["yellow"])  # both at the prior

    dead = _crew_belief()
    _add(dead, "orange", [_long_follow()])
    dead.roster["yellow"] = PlayerRecord(color="yellow", life_status="dead", death_seen_tick=40 + FOLLOW_FULL_TICKS)
    update_suspicion(dead)
    assert dead.suspicion["orange"] > 2 / 7  # following the victim to death raised it above prior


def test_dead_subjects_drop_out_of_the_posterior() -> None:
    belief = _crew_belief()
    _add(belief, "red", [_vent_dwell()])
    belief.roster["red"].life_status = "dead"
    update_suspicion(belief)
    assert "red" not in belief.suspicion  # the dead are no threat


def test_a_confirmation_drives_the_posterior_to_near_one() -> None:
    belief = _belief(_frame(4, players={}), _frame(5, players={"red": (53, 53)}), map=_vent_map())
    belief.total_player_count = 8
    update_suspicion(belief)  # emergence ⇒ a witnessed vent_use event on red
    assert "red" in witnessed_imposters(belief) and "red" in belief.believed_imposters
    assert belief.suspicion["red"] > 0.99  # overwhelming likelihood ratio


# --- witnessed catches as event-log entries ---------------------------------


def test_a_kill_event_on_the_log_drives_the_posterior_to_near_one() -> None:
    belief = _crew_belief()
    _add(belief, "red", [PlayerEvent(kind="kill", start_tick=50, end_tick=50, target_color="green")])
    _add(belief, "blue")  # baseline
    update_suspicion(belief)
    assert belief.suspicion["red"] > 0.99 and "red" in belief.believed_imposters
    assert "red" in witnessed_imposters(belief) and "blue" not in witnessed_imposters(belief)


def test_a_vent_use_event_on_the_log_is_a_witnessed_catch() -> None:
    belief = _crew_belief()
    _add(belief, "red", [PlayerEvent(kind="vent_use", start_tick=50, end_tick=50)])
    update_suspicion(belief)
    assert belief.suspicion["red"] > 0.99 and "red" in witnessed_imposters(belief)


# --- being tailed (tailing_self) — logistic in duration ---------------------


def _tail(dur: int, start: int = 1) -> PlayerEvent:
    return PlayerEvent(kind="tailing_self", start_tick=start, end_tick=start + dur, target_color=None)


def test_a_brief_tail_barely_moves_the_posterior() -> None:
    belief = _crew_belief()
    _add(belief, "red", [_tail(dur=5)])  # a brief brush ⇒ ~nothing
    _add(belief, "blue")  # baseline
    update_suspicion(belief)
    assert belief.suspicion["red"] == pytest.approx(belief.suspicion["blue"], abs=0.05)
    assert "red" not in belief.believed_imposters


def test_a_longer_tail_is_more_suspicious_than_a_shorter_one() -> None:
    belief = _crew_belief()
    _add(belief, "red", [_tail(dur=15)])
    _add(belief, "green", [_tail(dur=30)])
    _add(belief, "blue", [_tail(dur=50)])
    update_suspicion(belief)
    assert belief.suspicion["red"] < belief.suspicion["green"] < belief.suspicion["blue"]


def test_a_sustained_tail_saturates_at_a_moderate_suspicion_below_the_flee_bar() -> None:
    # Being tailed is deliberately *moderate* — a strong reason to call a meeting and
    # accuse, but it must not on its own cross the flee / near-certain bars.
    belief = _crew_belief()
    _add(belief, "red", [_tail(dur=50)])  # well past the ramp
    update_suspicion(belief)
    assert 0.65 <= belief.suspicion["red"] <= 0.78  # saturates around ~0.72 at this prior
    assert "red" not in belief.believed_imposters  # below FLEE_PROBABILITY


def test_active_tail_suspect_fires_only_for_a_live_tail_over_the_bar() -> None:
    belief = _crew_belief()  # last_tick = 200
    _add(belief, "red", [_tail(dur=50, start=150)])  # ends at 200 — a live, sustained tail
    _add(belief, "blue", [_tail(dur=50, start=1)])  # ends at 51 — same strength, but lapsed
    update_suspicion(belief)
    assert active_tail_suspect(belief) == "red"  # only the live tail triggers Accuse


def test_active_tail_suspect_is_none_below_the_accuse_threshold() -> None:
    belief = _crew_belief()
    _add(belief, "red", [_tail(dur=8, start=190)])  # live (ends at 198) but brief ⇒ P < bar
    update_suspicion(belief)
    assert active_tail_suspect(belief) is None


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


def test_chat_suspect_voices_a_soft_lead_below_the_vote_bar() -> None:
    # A soft lead (>= CHAT_SUSPECT_MIN_P, short of the vote bar) is worth voicing a read on.
    belief = Belief(self_role="crewmate", self_color="red")
    belief.suspicion = {"blue": 0.5, "green": 0.2}
    assert 0.5 >= CHAT_SUSPECT_MIN_P > 0.2
    assert chat_suspect(belief) == "blue"


def test_chat_suspect_skips_a_flat_field_and_never_returns_self() -> None:
    flat = Belief(self_role="crewmate", self_color="red")
    flat.suspicion = {"blue": 0.3, "green": 0.25}  # both below the chat floor ⇒ stay quiet
    assert chat_suspect(flat) is None
    self_high = Belief(self_role="crewmate", self_color="red")
    self_high.suspicion = {"red": 0.9, "blue": 0.3}  # self excluded; blue below the floor
    assert chat_suspect(self_high) is None


def test_active_tail_suspect_never_returns_self() -> None:
    belief = _crew_belief()
    belief.self_color = "red"
    _add(belief, "red", [_tail(dur=50, start=150)])  # a live, saturated "self-tail"
    belief.suspicion = {"red": 0.99}  # force self in
    assert active_tail_suspect(belief) is None  # never accuse ourselves


# --- believed-imposters maintenance -----------------------------------------


def test_a_confirmed_imposter_is_cleared_once_dead() -> None:
    prev = _frame(4, players={"red": (53, 53)}, camera=(0, 0))
    curr = _frame(5, players={}, camera=(0, 0))
    belief = _belief(prev, curr, map=_vent_map())
    belief.roster["red"] = PlayerRecord(color="red", life_status="alive")
    update_suspicion(belief)
    assert "red" in belief.believed_imposters

    belief.roster["red"].life_status = "dead"
    update_suspicion(belief)
    assert "red" not in belief.believed_imposters


# --- top_suspect (voting target) --------------------------------------------


def test_top_suspect_picks_a_near_certain_suspect_regardless_of_the_field() -> None:
    belief = Belief(self_role="crewmate")
    belief.suspicion = {"red": 0.95, "blue": 0.99}  # both near-certain (e.g. two catches)
    assert top_suspect(belief) == "blue"  # the most suspicious clears the absolute bar


def test_top_suspect_fires_on_a_clear_leader_below_near_certainty() -> None:
    belief = Belief(self_role="crewmate")
    belief.suspicion = {"red": 0.7, "blue": 0.3}  # red short of 0.8 but a clear lead
    assert top_suspect(belief) == "red"  # vote the clear leading suspect


def test_top_suspect_skips_a_flat_field() -> None:
    belief = Belief(self_role="crewmate")
    belief.suspicion = {"red": 0.6, "blue": 0.55, "green": 0.5}  # no one stands out
    assert top_suspect(belief) is None  # flat posterior ⇒ skip rather than eject at random


def test_top_suspect_skips_a_low_lone_lead() -> None:
    belief = Belief(self_role="crewmate")
    belief.suspicion = {"red": 0.45, "blue": 0.1}  # a lead, but the leader is below the floor
    assert top_suspect(belief) is None  # not even more-likely-than-not ⇒ skip


def test_top_suspect_returns_none_with_no_suspicion() -> None:
    belief = Belief(self_role="crewmate")
    assert top_suspect(belief) is None  # e.g. imposter/ghost (cleared suspicion) ⇒ skip


# --- the fitted model (data/suspicion_weights.json; suspicion-learning.md) ----
#
# Relational assertions only — they must survive a re-fit on more games, so they
# test structure (summing, exculpation, restraint), never specific weight values.


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


def _task_dwell(duration: int, start: int = 10) -> PlayerEvent:
    return PlayerEvent(kind="task", start_tick=start, end_tick=start + duration, region_index=0)


def _near_body_event(body_color: str, start: int = 10) -> PlayerEvent:
    return PlayerEvent(kind="near_body", start_tick=start, end_tick=start + 4, target_color=body_color, min_dist=8)


class TestFittedModel:
    def test_vendored_weights_load_and_declare_the_schema(self, _fitted_model) -> None:
        assert _fitted_model["schema"] == "crewborg-suspicion-weights/v1"
        assert _fitted_model["coefficients"]

    def test_no_evidence_sits_at_the_fitted_baseline_below_the_vote_bar(self, _fitted_model) -> None:
        belief = _crew_belief()
        _add(belief, "red")
        p = _p(belief, "red")
        assert p == pytest.approx(1 / (1 + np.exp(-_fitted_model["intercept"])), abs=1e-6)
        assert p < WEIGHTS_VOTE_PROBABILITY

    def test_witnessed_kill_is_a_definitional_near_certainty_and_votable(self, _fitted_model) -> None:
        belief = _crew_belief()
        _add(belief, "red", [PlayerEvent(kind="kill", start_tick=50, end_tick=50, target_color="cyan")])
        _add(belief, "blue")
        assert _p(belief, "red") > 0.99
        assert top_suspect(belief) == "red"

    def test_watched_task_completion_is_exculpatory(self, _fitted_model) -> None:
        # The stable exculpatory invariant: a WATCHED real-task completion (imposters
        # cannot produce one). Bare long dwell is deliberately not asserted — once
        # completions carry the exculpation, dwell-without-completion reads as a
        # Pretend-style fake and may be ~neutral or worse.
        belief = _crew_belief()
        _add(belief, "red", [_task_dwell(duration=120)])
        belief.roster["red"].tasks_completed_watched = 2
        _add(belief, "blue")
        update_suspicion(belief)
        assert belief.suspicion["red"] < belief.suspicion["blue"]

    def test_evidence_instances_sum_monotonically(self, _fitted_model) -> None:
        one = _crew_belief()
        _add(one, "red", [_near_body_event("cyan")])
        two = _crew_belief()
        _add(two, "red", [_near_body_event("cyan"), _near_body_event("purple", start=120)])
        assert _p(two, "red") >= _p(one, "red")

    def test_repeat_sightings_of_the_same_body_do_not_double_count(self, _fitted_model) -> None:
        once = _crew_belief()
        _add(once, "red", [_near_body_event("cyan")])
        thrice = _crew_belief()
        _add(thrice, "red", [_near_body_event("cyan"), _near_body_event("cyan", start=120), _near_body_event("cyan", start=300)])
        assert _p(thrice, "red") == pytest.approx(_p(once, "red"))

    def test_a_sustained_tail_alone_stays_below_the_vote_bar(self, _fitted_model) -> None:
        belief = _crew_belief()
        _add(belief, "red", [PlayerEvent(kind="tailing_self", start_tick=10, end_tick=110, min_dist=40)])
        _add(belief, "blue")
        assert _p(belief, "red") < WEIGHTS_VOTE_PROBABILITY
        assert top_suspect(belief) is None

    def test_no_clear_leader_rule_a_moderate_lead_does_not_vote(self, _fitted_model) -> None:
        belief = _crew_belief()
        # follow evidence well above the field, but short of the calibrated bar
        _add(belief, "red", [_long_follow()])
        _add(belief, "yellow")
        belief.roster["yellow"].life_status = "dead"
        belief.roster["yellow"].death_seen_tick = 40 + FOLLOW_FULL_TICKS
        _add(belief, "blue")
        update_suspicion(belief)
        if belief.suspicion["red"] < WEIGHTS_VOTE_PROBABILITY:
            assert top_suspect(belief) is None

    def test_env_zero_forces_the_legacy_hand_model(self, monkeypatch) -> None:
        monkeypatch.setenv("CREWBORG_SUSPICION_WEIGHTS", "0")
        assert suspicion_module._load_weights() is None
