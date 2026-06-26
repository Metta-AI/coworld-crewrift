"""Who-sus'd-who tests: chat stance parsing + social belief folding (design §5 social)."""

from __future__ import annotations

from players.crewrift.crewborg.perception.entities import (
    ChatLine,
    ResolvedScene,
    VoteCandidate,
    VoteDot,
    VotingState,
)
from players.crewrift.crewborg.strategy.meeting.social import has_evidence_context, parse_stances
from players.crewrift.crewborg.types import Belief, Percept, update_belief


# --- parser ------------------------------------------------------------------


def test_parse_simple_accusation() -> None:
    assert parse_stances("blue", "red is sus") == [("red", "accuse")]


def test_parse_defense_keywords() -> None:
    assert parse_stances("blue", "green was with me the whole time") == [("green", "defend")]
    assert parse_stances("blue", "red is clear") == [("red", "defend")]


def test_parse_compound_clauses_split_stances() -> None:
    pairs = parse_stances("yellow", "red is sus, blue is clear")
    assert ("red", "accuse") in pairs
    assert ("blue", "defend") in pairs


def test_parse_multiword_colors_do_not_double_count() -> None:
    # "light blue" must match the two-word color, not also count as "blue".
    assert parse_stances("red", "saw light blue vent in electrical") == [("light blue", "accuse")]


def test_parse_excludes_speaker_self_reference() -> None:
    # A self-alibi is not an accusation of another player.
    assert parse_stances("red", "red was in nav, vote green") == [("green", "accuse")]


def test_parse_bare_mention_reads_as_accusation() -> None:
    assert parse_stances("blue", "where was orange?") == [("orange", "accuse")]


def test_parse_no_colors_yields_nothing() -> None:
    assert parse_stances("blue", "i was doing tasks") == []


def test_parse_deduplicates_within_a_line() -> None:
    assert parse_stances("blue", "red red red! vote red") == [("red", "accuse")]


# --- evidence-format classification -------------------------------------------


def test_bare_sus_lines_carry_no_evidence() -> None:
    # The truecrew disinfo format: 0/185 of these named a real imposter.
    assert not has_evidence_context("red sus")
    assert not has_evidence_context("light blue sus")
    assert not has_evidence_context("vote red")
    assert not has_evidence_context("red?")


def test_evidence_wording_is_recognized() -> None:
    # The truecrew body-report format (84% accurate) and witness phrasings.
    assert has_evidence_context("body in storage deck sus red")
    assert has_evidence_context("saw blue vent in electrical")
    assert has_evidence_context("RED was following yellow")
    assert has_evidence_context("i seen green kill")


def test_folded_accusations_carry_the_evidence_flag() -> None:
    belief = Belief()
    _fold(
        belief,
        10,
        voting=_meeting_voting(),
        chat_lines=(
            ChatLine(speaker_color="red", text="green sus"),
            ChatLine(speaker_color="green", text="body in med bay sus red"),
        ),
    )
    by_speaker = {a.speaker_color: a for a in belief.accusations}
    assert by_speaker["red"].has_evidence is False
    assert by_speaker["green"].has_evidence is True


# --- belief folding ----------------------------------------------------------


def _fold(belief: Belief, tick: int, **resolved_fields) -> None:
    resolved = ResolvedScene(tick=tick, camera_ready=True, camera_x=0, camera_y=0, **resolved_fields)
    update_belief(belief, Percept(tick=tick, messages_applied=tick, resolved=resolved))


def _meeting_voting(dots: tuple[VoteDot, ...] = ()) -> VotingState:
    return VotingState(
        timer_present=True,
        self_marker_color="blue",
        candidates=(
            VoteCandidate(slot=0, color="red", alive=True),
            VoteCandidate(slot=1, color="blue", alive=True),
            VoteCandidate(slot=2, color="green", alive=True),
        ),
        dots=dots,
    )


def test_accusations_accumulate_across_meetings() -> None:
    belief = Belief()
    _fold(belief, 10, voting=_meeting_voting(), chat_lines=(ChatLine(speaker_color="red", text="green is sus"),))
    assert len(belief.accusations) == 1
    first = belief.accusations[0]
    assert (first.speaker_color, first.target_color, first.stance) == ("red", "green", "accuse")
    assert first.meeting_id == 10

    # Meeting closes (back to Playing), then a second meeting opens: chat_log is
    # cleared but accusations persist and keep accumulating.
    _fold(belief, 20, crew_tasks_remaining=5)
    _fold(belief, 30, voting=_meeting_voting(), chat_lines=(ChatLine(speaker_color="green", text="red is clear"),))
    assert belief.chat_log[-1].text == "red is clear"
    assert len(belief.accusations) == 2
    second = belief.accusations[1]
    assert (second.speaker_color, second.target_color, second.stance) == ("green", "red", "defend")
    assert second.meeting_id == 30


def test_duplicate_chat_lines_record_one_accusation() -> None:
    belief = Belief()
    line = ChatLine(speaker_color="red", text="green vented")
    _fold(belief, 10, voting=_meeting_voting(), chat_lines=(line,))
    _fold(belief, 11, voting=_meeting_voting(), chat_lines=(line,))  # re-rendered chat
    assert len(belief.accusations) == 1


def test_meeting_record_freezes_final_tally_and_ejection() -> None:
    belief = Belief()
    # Votes trickle in across the meeting; the record tracks the latest dots.
    _fold(belief, 10, voting=_meeting_voting(dots=(VoteDot(voter=0, target=2),)))
    _fold(belief, 11, voting=_meeting_voting(dots=(VoteDot(voter=0, target=2), VoteDot(voter=1, target=-2))))
    assert len(belief.meeting_history) == 1
    record = belief.meeting_history[0]
    assert record.meeting_id == 10
    assert record.votes == {"red": "green", "blue": "skip"}

    # The vote-result interstitial names the ejectee; the record closes out.
    _fold(belief, 12, phase_texts=frozenset({"WAS KILLED"}), ejected_color="green")
    assert record.ejected_color == "green"
    assert belief.roster["green"].life_status == "dead"

    # A later meeting opens its own record.
    _fold(belief, 20, crew_tasks_remaining=5)
    _fold(belief, 30, voting=_meeting_voting(dots=(VoteDot(voter=0, target=-2),)))
    assert len(belief.meeting_history) == 2
    assert belief.meeting_history[1].meeting_id == 30
    assert belief.meeting_history[1].votes == {"red": "skip"}
    assert belief.meeting_history[1].ejected_color is None
