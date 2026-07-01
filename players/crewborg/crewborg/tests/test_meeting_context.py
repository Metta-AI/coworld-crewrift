"""Meeting LLM context and decision schema tests."""

from __future__ import annotations

import pytest

from crewborg.perception.entities import VoteCandidate, VoteDot, VotingState
from crewborg.strategy.meeting import (
    VOTE_SKIP,
    MeetingDecision,
    MeetingDecisionValidationError,
    sanitize_chat,
    serialize_meeting_context,
    validate_meeting_decision,
    valid_vote_targets,
)
from crewborg.types import Belief, ChatEvent, PlayerRecord


def _belief() -> Belief:
    belief = Belief(phase="Voting", phase_start_tick=10, last_tick=34, total_player_count=3)
    belief.voting = VotingState(
        timer_present=True,
        self_marker_color="blue",
        candidates=(
            VoteCandidate(slot=0, color="red", alive=True),
            VoteCandidate(slot=1, color="blue", alive=True),
            VoteCandidate(slot=2, color="green", alive=False),
        ),
        dots=(VoteDot(voter=0, target=2), VoteDot(voter=1, target=-2)),
        cursor_slot=0,
    )
    belief.roster["red"] = PlayerRecord(color="red", life_status="alive", last_seen_tick=20)
    belief.roster["blue"] = PlayerRecord(color="blue", life_status="alive", last_seen_tick=20)
    belief.roster["green"] = PlayerRecord(color="green", life_status="dead", death_seen_tick=30)
    belief.chat_log = [ChatEvent(tick=25, speaker_color="red", text="blue sus")]
    belief.suspicion = {"red": 0.91, "green": 0.2}
    return belief


def test_valid_vote_targets_excludes_self_and_dead_candidates() -> None:
    assert valid_vote_targets(_belief()) == {"red"}


def test_meeting_context_serializes_timer_chat_votes_and_suspicion() -> None:
    context = serialize_meeting_context(
        _belief(),
        trigger="meeting_start",
        tentative_vote="red",
        sent_chat_texts={"hello"},
        last_chat_tick=10,
    )

    assert context["meeting"]["estimated_remaining_ticks"] == 216
    assert context["constraints"]["valid_vote_targets"] == ["red", VOTE_SKIP]
    assert context["constraints"]["chat_cooldown_ready"] is False
    assert context["state"]["tentative_vote"] == "red"
    assert context["chat"]["messages"][0]["text"] == "blue sus"
    assert context["voting"]["tally"] == {VOTE_SKIP: 1, "green": 1}
    assert context["players"][0]["color"] == "blue"
    assert context["suspicion"]["would_vote"] == "red"


def test_chat_sanitizer_keeps_printable_ascii_and_truncates() -> None:
    assert sanitize_chat("  héllo\nthere  ") == "hllothere"
    assert len(sanitize_chat("x" * 500)) == 160


def test_meeting_decision_validation_rejects_dead_or_unknown_vote_target() -> None:
    with pytest.raises(MeetingDecisionValidationError):
        validate_meeting_decision(
            MeetingDecision(action="submit_vote", vote_target="green"),
            alive_vote_targets={"red"},
            fallback_vote=VOTE_SKIP,
        )


def test_submit_without_target_uses_tentative_then_fallback() -> None:
    decision = validate_meeting_decision(
        MeetingDecision(action="submit_vote"),
        alive_vote_targets={"red"},
        current_tentative="red",
        fallback_vote=VOTE_SKIP,
    )
    assert decision.vote_target == "red"

    decision = validate_meeting_decision(
        MeetingDecision(action="submit_vote"),
        alive_vote_targets={"red"},
        fallback_vote=VOTE_SKIP,
    )
    assert decision.vote_target == VOTE_SKIP
