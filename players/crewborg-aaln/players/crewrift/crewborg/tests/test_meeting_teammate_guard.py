"""Imposter teammate-vote prevention guard (enforced, not just prompted)."""

from __future__ import annotations

import pytest

from players.crewrift.crewborg.perception.entities import VoteCandidate, VotingState
from players.crewrift.crewborg.strategy.meeting import (
    VOTE_SKIP,
    MeetingDecision,
    MeetingDecisionValidationError,
    serialize_meeting_context,
    valid_vote_targets,
    validate_meeting_decision,
)
from players.crewrift.crewborg.types import Belief, PlayerRecord

# Roster for every test: self=blue, red=crewmate, green=imposter teammate.
_SELF = "blue"
_CREWMATE = "red"
_TEAMMATE = "green"


def _voting_belief(*, self_role: str | None, teammates: set[str]) -> Belief:
    belief = Belief(phase="Voting", phase_start_tick=0, last_tick=0, total_player_count=3)
    belief.self_role = self_role
    belief.teammate_colors = set(teammates)
    belief.voting = VotingState(
        timer_present=True,
        self_marker_color=_SELF,
        candidates=(
            VoteCandidate(slot=0, color=_CREWMATE, alive=True),
            VoteCandidate(slot=1, color=_TEAMMATE, alive=True),
            VoteCandidate(slot=2, color=_SELF, alive=True),
        ),
        cursor_slot=0,
    )
    for color in (_CREWMATE, _TEAMMATE, _SELF):
        belief.roster[color] = PlayerRecord(color=color, life_status="alive", last_seen_tick=1)
    return belief


def test_imposter_legal_targets_exclude_self_and_teammates() -> None:
    belief = _voting_belief(self_role="imposter", teammates={_TEAMMATE})
    assert valid_vote_targets(belief) == {_CREWMATE}


def test_crewmate_legal_targets_include_all_other_alive_players() -> None:
    # A crewmate has no teammates; everyone else alive is fair game.
    belief = _voting_belief(self_role="crewmate", teammates=set())
    assert valid_vote_targets(belief) == {_CREWMATE, _TEAMMATE}


def test_serialized_context_hides_teammate_from_imposter_vote_menu() -> None:
    belief = _voting_belief(self_role="imposter", teammates={_TEAMMATE})
    context = serialize_meeting_context(belief, trigger="meeting_start")
    legal = context["constraints"]["valid_vote_targets"]
    assert _TEAMMATE not in legal
    assert _CREWMATE in legal
    assert VOTE_SKIP in legal


def test_validator_rejects_imposter_voting_a_teammate() -> None:
    belief = _voting_belief(self_role="imposter", teammates={_TEAMMATE})
    decision = MeetingDecision(action="submit_vote", vote_target=_TEAMMATE)
    with pytest.raises(MeetingDecisionValidationError):
        validate_meeting_decision(
            decision,
            alive_vote_targets=valid_vote_targets(belief),
            fallback_vote=VOTE_SKIP,
        )


def test_validator_allows_same_target_for_a_crewmate() -> None:
    # The very same color is a legal vote when we are not its imposter teammate.
    belief = _voting_belief(self_role="crewmate", teammates=set())
    decision = MeetingDecision(action="submit_vote", vote_target=_TEAMMATE)
    validated = validate_meeting_decision(
        decision,
        alive_vote_targets=valid_vote_targets(belief),
        fallback_vote=VOTE_SKIP,
    )
    assert validated.vote_target == _TEAMMATE


def test_imposter_with_only_teammates_alive_safely_skips() -> None:
    # If the sole other live player is a teammate, there is no legal target and
    # the imposter falls back to skip rather than deadlocking.
    belief = _voting_belief(self_role="imposter", teammates={_TEAMMATE, _CREWMATE})
    assert valid_vote_targets(belief) == set()
    decision = MeetingDecision(action="submit_vote", vote_target=None)
    validated = validate_meeting_decision(
        decision,
        alive_vote_targets=valid_vote_targets(belief),
        fallback_vote=VOTE_SKIP,
    )
    assert validated.vote_target == VOTE_SKIP
