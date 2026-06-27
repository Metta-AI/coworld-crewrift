"""Social-evidence counters: chat stances, attributed votes, watched completions.

These feed the fitted suspicion model's public features (strategy/social_evidence.py;
offline mirror: suspicion_lab/tools/features.py).
"""

from __future__ import annotations

from crewborg.perception.entities import VoteCandidate, VoteDot, VotingState
from crewborg.strategy.social_evidence import (
    SKIP_VOTE_TARGET,
    WATCHED_DWELL_MIN_TICKS,
    update_social_evidence,
)
from crewborg.types import Belief, ChatEvent, PlayerEvent, PlayerRecord


def _belief(**kwargs) -> Belief:
    kwargs.setdefault("self_role", "crewmate")
    kwargs.setdefault("self_color", "red")
    belief = Belief(**kwargs)
    for color in ("red", "blue", "green", "yellow"):
        belief.roster[color] = PlayerRecord(color=color, life_status="alive")
    return belief


# --- chat stances ---------------------------------------------------------------


def test_an_accusation_counts_for_speaker_and_target() -> None:
    belief = _belief()
    belief.chat_log.append(ChatEvent(tick=100, speaker_color="blue", text="green sus: lurking on a vent"))
    update_social_evidence(belief)
    assert belief.roster["blue"].accusations_made == 1
    assert belief.roster["green"].times_accused == 1


# --- vote tallies -----------------------------------------------------------------


def _stage_meeting(belief: Belief, dots: list[VoteDot], start_tick: int = 500) -> None:
    belief.phase = "Voting"
    belief.phase_start_tick = start_tick
    belief.voting = VotingState(
        dots=tuple(dots),
        candidates=(
            VoteCandidate(slot=0, color="red", alive=True),
            VoteCandidate(slot=1, color="blue", alive=True),
            VoteCandidate(slot=2, color="green", alive=True),
            VoteCandidate(slot=3, color="yellow", alive=True),
        ),
    )
    update_social_evidence(belief)  # stages
    belief.phase = "Playing"
    belief.voting = VotingState()   # UI gone
    update_social_evidence(belief)  # commits once


def test_votes_commit_once_with_attribution() -> None:
    belief = _belief()
    _stage_meeting(
        belief,
        dots=[
            VoteDot(voter=0, target=2),                 # me (red) votes green
            VoteDot(voter=1, target=2),                 # blue agrees with me
            VoteDot(voter=2, target=0),                 # green votes ME
            VoteDot(voter=3, target=SKIP_VOTE_TARGET),  # yellow skips
        ],
    )
    update_social_evidence(belief)  # extra ticks must not double-commit
    blue, green, yellow = belief.roster["blue"], belief.roster["green"], belief.roster["yellow"]
    assert blue.votes_cast == 1 and blue.vote_agreed_with_me == 1
    assert green.votes_cast == 1 and green.voted_against_me == 1
    assert yellow.votes_skipped == 1 and yellow.votes_cast == 0
    assert belief.roster["red"].votes_cast == 0  # never count ourselves


# --- watched completion -------------------------------------------------------------


def _full_dwell(end: int) -> PlayerEvent:
    return PlayerEvent(
        kind="task", start_tick=end - WATCHED_DWELL_MIN_TICKS - 4, end_tick=end, region_index=0
    )


def test_counter_decrement_with_one_full_dwell_credits_the_watcher() -> None:
    belief = _belief(last_tick=1000)
    belief.roster["green"].last_seen_tick = 1000
    belief.roster["green"].events.append(_full_dwell(end=999))
    belief.social_prev_tasks_remaining = 40
    belief.crew_tasks_remaining = 39
    update_social_evidence(belief)
    assert belief.roster["green"].tasks_completed_watched == 1


def test_no_credit_without_a_decrement_fake_task_hold() -> None:
    belief = _belief(last_tick=1000)
    belief.roster["green"].last_seen_tick = 1000
    belief.roster["green"].events.append(_full_dwell(end=999))   # a Pretend-style hold
    belief.social_prev_tasks_remaining = 40
    belief.crew_tasks_remaining = 40                              # counter never moved
    update_social_evidence(belief)
    assert belief.roster["green"].tasks_completed_watched == 0
