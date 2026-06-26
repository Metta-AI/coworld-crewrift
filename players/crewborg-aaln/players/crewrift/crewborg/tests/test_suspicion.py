"""Near-certain suspicion tests: witnessed kill + witnessed vent (design §10.1)."""

from __future__ import annotations

import numpy as np
import pytest

from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect, Vent
from players.crewrift.crewborg.strategy.suspicion import (
    BODY_FADE_TICKS,
    FLEE_PROBABILITY,
    FOLLOW_FULL_TICKS,
    VENT_CROSS_TICKS,
    VOTE_PROBABILITY,
    top_suspect,
    update_suspicion,
)
from players.crewrift.crewborg.types import Belief, PerceptionFrame, PlayerEvent, PlayerRecord


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


def test_imposter_observer_accrues_no_suspicion() -> None:
    prev = _frame(4, players={"red": (100, 100), "green": (110, 100)})
    curr = _frame(5, players={"green": (110, 100)}, bodies={"red": (100, 100)})
    belief = _belief(prev, curr, self_role="imposter")
    update_suspicion(belief)
    assert not belief.suspicion and not belief.believed_imposters


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
    update_suspicion(belief)  # emergence ⇒ confirmed
    assert "red" in belief.confirmed_imposters and "red" in belief.believed_imposters
    assert belief.suspicion["red"] > 0.99  # overwhelming likelihood ratio


# --- prior redistribution (remaining-K) --------------------------------------


def test_prior_shrinks_once_a_confirmed_imposter_is_dead() -> None:
    # 8 players, 2 imposters: baseline prior 2/7. With one confirmed imposter
    # ejected, the hidden budget is 1 over the 6 remaining candidates.
    belief = _crew_belief(total_players=8)
    _add(belief, "red")
    belief.roster["white"] = PlayerRecord(color="white", life_status="dead")
    belief.confirmed_imposters = {"white"}
    update_suspicion(belief)
    assert belief.suspicion["red"] == pytest.approx(1 / 6)


def test_prior_excludes_a_confirmed_alive_imposter_from_the_candidates() -> None:
    # A confirmed-but-alive imposter holds one budget slot and is not a hidden
    # candidate: the other players' prior is 1 hidden imposter over 6 candidates.
    belief = _crew_belief(total_players=8)
    _add(belief, "red")
    _add(belief, "white")
    belief.confirmed_imposters = {"white"}
    update_suspicion(belief)
    assert belief.suspicion["red"] == pytest.approx(1 / 6)
    assert belief.suspicion["white"] > 0.99  # the catch itself is overwhelming


# --- social (who-sus'd-who) evidence ------------------------------------------


def _accusation(speaker: str, target: str, stance: str = "accuse", meeting_id: int = 10, has_evidence: bool = True):
    from players.crewrift.crewborg.types import Accusation

    return Accusation(
        meeting_id=meeting_id, tick=meeting_id, speaker_color=speaker,
        target_color=target, stance=stance, text="…", has_evidence=has_evidence,
    )


def test_defense_by_a_confirmed_imposter_raises_suspicion() -> None:
    belief = _crew_belief()
    _add(belief, "red")
    _add(belief, "blue")  # baseline
    _add(belief, "white")
    belief.confirmed_imposters = {"white"}
    belief.accusations = [_accusation("white", "red", stance="defend")]
    update_suspicion(belief)
    assert belief.suspicion["red"] > belief.suspicion["blue"]


def test_accusation_by_a_confirmed_imposter_lowers_suspicion() -> None:
    belief = _crew_belief()
    _add(belief, "red")
    _add(belief, "blue")  # baseline
    _add(belief, "white")
    belief.confirmed_imposters = {"white"}
    belief.accusations = [_accusation("white", "red")]
    update_suspicion(belief)
    assert belief.suspicion["red"] < belief.suspicion["blue"]


def test_crowd_accusation_needs_two_independent_speakers() -> None:
    one = _crew_belief()
    _add(one, "red")
    _add(one, "blue")
    one.accusations = [_accusation("green", "red")]
    update_suspicion(one)
    assert one.suspicion["red"] == pytest.approx(one.suspicion["blue"])  # one accuser: neutral

    two = _crew_belief()
    _add(two, "red")
    _add(two, "blue")
    two.accusations = [_accusation("green", "red"), _accusation("yellow", "red")]
    update_suspicion(two)
    assert two.suspicion["red"] > two.suspicion["blue"]


# --- the plain-sus disinfo tell (2026-06-11 truecrew eval) -------------------


def test_plain_sus_exculpates_the_named_target() -> None:
    # A bare "<color> sus" with no evidence wording marks the named color as
    # likely innocent (0/185 named a real imposter vs truecrew:v14).
    belief = _crew_belief()
    _add(belief, "red")
    _add(belief, "blue")  # baseline
    belief.accusations = [_accusation("green", "red", has_evidence=False)]
    update_suspicion(belief)
    assert belief.suspicion["red"] < belief.suspicion["blue"]


def test_plain_sus_raises_suspicion_of_the_speaker() -> None:
    # …and marks the speaker as a likely imposter steering the meeting.
    belief = _crew_belief()
    _add(belief, "red")
    _add(belief, "blue")  # baseline
    _add(belief, "green")
    belief.accusations = [_accusation("green", "red", has_evidence=False)]
    update_suspicion(belief)
    assert belief.suspicion["green"] > belief.suspicion["blue"]


def test_evidence_backed_accusation_beats_the_plain_sus_exculpation() -> None:
    # Real evidence against the same color cancels the format-level tell.
    belief = _crew_belief()
    _add(belief, "red")
    _add(belief, "blue")  # baseline
    belief.accusations = [
        _accusation("green", "red", has_evidence=False),
        _accusation("yellow", "red", has_evidence=True),
    ]
    update_suspicion(belief)
    assert belief.suspicion["red"] >= belief.suspicion["blue"]


def test_bare_accusations_never_count_as_crowd_corroboration() -> None:
    # Two bare-sus speakers are a disinfo chorus, not independent corroboration.
    belief = _crew_belief()
    _add(belief, "red")
    _add(belief, "blue")  # baseline
    belief.accusations = [
        _accusation("green", "red", has_evidence=False),
        _accusation("yellow", "red", has_evidence=False),
    ]
    update_suspicion(belief)
    assert belief.suspicion["red"] < belief.suspicion["blue"]


def test_voting_for_a_confirmed_imposter_is_crew_like() -> None:
    from players.crewrift.crewborg.types import MeetingRecord

    belief = _crew_belief()
    _add(belief, "red")
    _add(belief, "blue")  # baseline
    belief.roster["white"] = PlayerRecord(color="white", life_status="dead")
    belief.confirmed_imposters = {"white"}
    belief.meeting_history = [MeetingRecord(meeting_id=10, votes={"red": "white", "blue": "skip"}, ejected_color="white")]
    update_suspicion(belief)
    assert belief.suspicion["red"] < belief.suspicion["blue"]


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


def test_top_suspect_picks_the_highest_over_the_vote_bar() -> None:
    belief = Belief(self_role="crewmate")
    belief.suspicion = {"red": VOTE_PROBABILITY + 0.05, "blue": 0.99}
    assert top_suspect(belief) == "blue"  # the most suspicious clears the bar


def test_top_suspect_returns_none_below_the_vote_bar() -> None:
    belief = Belief(self_role="crewmate")
    belief.suspicion = {"red": VOTE_PROBABILITY - 0.05, "blue": 0.1}
    assert top_suspect(belief) is None  # nobody confident enough ⇒ skip
