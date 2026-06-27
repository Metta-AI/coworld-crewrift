"""Imposter meeting tactics: deflection, bandwagoning, and safe fabrication (§10.4)."""

from __future__ import annotations

from crewborg.modes import AttendMeetingMode
from crewborg.perception.entities import VoteCandidate, VoteDot, VotingState
from crewborg.strategy.meeting.accusation import build_accusation, fabricate_accusation
from crewborg.strategy.meeting.imposter import bandwagon_target
from crewborg.types import ActionState, Belief, PlayerEvent, PlayerRecord


def _voting(self_color="orange", **kwargs) -> VotingState:
    return VotingState(
        timer_present=True,
        self_marker_color=self_color,
        candidates=(
            VoteCandidate(slot=0, color="red", alive=True),
            VoteCandidate(slot=1, color="blue", alive=True),
            VoteCandidate(slot=2, color="yellow", alive=True),
        ),
        **kwargs,
    )


# --- bandwagon target -------------------------------------------------------


def test_bandwagon_never_targets_a_teammate() -> None:
    belief = Belief(self_role="imposter", teammate_colors={"blue"})
    belief.voting = _voting(dots=(VoteDot(voter=2, target=1),))  # yellow -> blue (our teammate)
    assert bandwagon_target(belief) is None


# --- safe fabrication -------------------------------------------------------


def test_fabricated_and_real_accusations_share_the_format() -> None:
    # Identical surface format is the anti-tell — both "<color> sus: a, b".
    real_belief = Belief(self_role="crewmate")
    real_belief.roster["red"] = PlayerRecord(
        color="red", life_status="alive",
        events=[PlayerEvent(kind="vent", start_tick=1, end_tick=20, region_index=0)],
    )
    real = build_accusation(real_belief, "red")
    fake = fabricate_accusation(Belief(self_role="imposter"), "red")
    assert real == "red sus: lurking on a vent"
    assert fake.startswith("red sus: ") and real.startswith("red sus: ")


# --- imposter meeting flow --------------------------------------------------


def test_imposter_proactively_accuses_a_sus_crewmate_with_real_evidence() -> None:
    mode = AttendMeetingMode()
    belief = Belief(phase="Voting", self_role="imposter", teammate_colors={"green"})
    belief.roster["red"] = PlayerRecord(
        color="red", life_status="alive",
        events=[PlayerEvent(kind="vent", start_tick=1, end_tick=20, region_index=0)],
    )
    belief.suspicion = {"red": 0.85}  # red a clear leading non-teammate suspect

    chat = mode.decide(belief, ActionState())
    assert chat.kind == "chat" and chat.text == "red sus: lurking on a vent"  # real evidence
    vote = mode.decide(belief, ActionState())
    assert vote.kind == "vote" and vote.target_color == "red"


def test_imposter_bandwagons_with_fabrication_when_it_has_no_real_lead() -> None:
    mode = AttendMeetingMode()
    belief = Belief(phase="Voting", self_role="imposter", teammate_colors={"green"})
    belief.suspicion = {"red": 0.3, "blue": 0.3}  # flat — no real deflection
    belief.voting = _voting(dots=(VoteDot(voter=2, target=1),))  # yellow voted blue
    belief.roster["blue"] = PlayerRecord(color="blue", life_status="alive")

    chat = mode.decide(belief, ActionState())
    assert chat.kind == "chat" and chat.text.startswith("blue sus:")  # fabricated, same format
    vote = mode.decide(belief, ActionState())
    assert vote.kind == "vote" and vote.target_color == "blue"


def test_imposter_stays_quiet_when_only_a_teammate_takes_heat() -> None:
    mode = AttendMeetingMode()
    belief = Belief(phase="Voting", self_role="imposter", teammate_colors={"blue"}, last_tick=0)
    belief.suspicion = {"red": 0.3}
    belief.voting = _voting(dots=(VoteDot(voter=2, target=1),))  # yellow voted blue (teammate)

    assert mode.decide(belief, ActionState()).kind == "idle"  # don't help eject our own
