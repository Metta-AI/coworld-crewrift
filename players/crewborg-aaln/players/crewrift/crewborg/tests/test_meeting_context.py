"""Meeting LLM context and decision schema tests."""

from __future__ import annotations

import pytest

from players.crewrift.crewborg.perception.entities import VoteCandidate, VoteDot, VotingState
from players.crewrift.crewborg.strategy.meeting import (
    VOTE_SKIP,
    MeetingDecision,
    MeetingDecisionValidationError,
    sanitize_chat,
    serialize_meeting_context,
    validate_meeting_decision,
    valid_vote_targets,
)
from players.crewrift.crewborg.types import Accusation, Belief, ChatEvent, MeetingRecord, PlayerRecord


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


def test_meeting_context_uses_learned_vote_timer_and_call_attribution() -> None:
    belief = _belief()
    belief.vote_timer_ticks = 1200  # learned from the GAME INFO interstitial
    belief.meeting_called_by = "green"
    belief.meeting_trigger = "report"
    belief.meeting_reported_body_color = "blue"

    context = serialize_meeting_context(belief, trigger="meeting_start")
    meeting = context["meeting"]
    assert meeting["vote_timer_ticks"] == 1200
    # _belief() opens the meeting at tick 20 with last_tick 44 → 24 elapsed.
    assert meeting["estimated_remaining_ticks"] == 1200 - (belief.last_tick - belief.phase_start_tick)
    assert meeting["called_by"] == "green"
    assert meeting["call_trigger"] == "report"
    assert meeting["reported_body_color"] == "blue"


def test_meeting_context_falls_back_to_conservative_timer_and_null_attribution() -> None:
    context = serialize_meeting_context(_belief(), trigger="meeting_start")
    meeting = context["meeting"]
    assert meeting["vote_timer_ticks"] == 240  # safe fallback for older servers
    assert meeting["called_by"] is None
    assert meeting["call_trigger"] is None
    assert meeting["reported_body_color"] is None


def test_meeting_context_serializes_social_record() -> None:
    belief = _belief()
    belief.accusations = [
        Accusation(
            meeting_id=5, tick=6, speaker_color="red", target_color="blue",
            stance="accuse", text="blue sus",
        )
    ]
    belief.meeting_history = [
        MeetingRecord(meeting_id=5, votes={"red": "green", "blue": "skip"}, ejected_color="green"),
    ]
    belief.confirmed_imposters = {"green"}

    context = serialize_meeting_context(belief, trigger="meeting_start")

    [accusation] = context["social"]["accusations"]
    assert accusation["speaker_color"] == "red"
    assert accusation["target_color"] == "blue"
    assert accusation["stance"] == "accuse"
    [meeting] = context["social"]["meetings"]
    assert meeting["votes"] == {"blue": "skip", "red": "green"}
    assert meeting["ejected_color"] == "green"
    assert meeting["ejected_was_confirmed_imposter"] is True


def test_meeting_context_serializes_game_state() -> None:
    belief = _belief()  # 3 players ⇒ 0 imposters by the auto formula
    context = serialize_meeting_context(belief, trigger="meeting_start")

    game_state = context["game_state"]
    assert game_state["imposters_total"] == 0
    assert game_state["imposters_remaining"] == 0
    assert game_state["alive_players"] == 2
    assert game_state["must_eject"] is False
    assert game_state["vote_bar"] == context["suspicion"]["vote_probability_threshold"]
    assert game_state["plurality_target"] is None  # the only voted target is dead


def test_meeting_context_flags_must_eject_endgame() -> None:
    belief = _belief()
    belief.imposter_count = 1  # 2 alive incl. self, 1 imposter at large -> must eject
    context = serialize_meeting_context(belief, trigger="meeting_start")

    game_state = context["game_state"]
    assert game_state["imposters_remaining"] == 1
    assert game_state["must_eject"] is True
    assert game_state["vote_bar"] == 0.0
    # In must-eject the fallback votes the best read even below any normal bar.
    assert context["state"]["fallback_vote"] == "red"


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
