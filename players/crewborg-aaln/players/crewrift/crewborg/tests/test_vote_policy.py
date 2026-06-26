"""Game-theory vote policy tests (design §10.2)."""

from __future__ import annotations

from players.crewrift.crewborg.perception.entities import VoteCandidate, VoteDot, VotingState
from players.crewrift.crewborg.strategy.meeting.schema import VOTE_SKIP
from players.crewrift.crewborg.strategy.meeting.vote_policy import (
    ANNOUNCE_MIN_PROBABILITY,
    COMFORTABLE_VOTE_PROBABILITY,
    DEADLINE_VOTE_MIN_PROBABILITY,
    SKIP_PILEON_MIN_PROBABILITY,
    TIGHT_VOTE_PROBABILITY,
    anti_split_swap,
    corroborated_accusation_target,
    crewmate_fallback_vote,
    deadline_posterior_gate,
    fallback_vote,
    imposter_fallback_vote,
    imposters_remaining,
    must_eject,
    plurality_target,
    should_announce,
    skip_pileon_swap,
    vote_bar,
)
from players.crewrift.crewborg.types import Accusation, Belief, PlayerRecord

COLORS = ("red", "blue", "green", "yellow", "pink", "lime", "orange", "white")


def _belief(*, alive: int = 8, self_color: str = "blue", dots: tuple[VoteDot, ...] = ()) -> Belief:
    """An 8-player, 2-imposter meeting belief with the first ``alive`` colors alive."""

    belief = Belief(phase="Voting", total_player_count=8, imposter_count=2)
    for index, color in enumerate(COLORS):
        status = "alive" if index < alive else "dead"
        belief.roster[color] = PlayerRecord(color=color, life_status=status, last_seen_tick=1)
    belief.voting = VotingState(
        timer_present=True,
        self_marker_color=self_color,
        candidates=tuple(
            VoteCandidate(slot=index, color=color, alive=index < alive)
            for index, color in enumerate(COLORS)
        ),
        dots=dots,
    )
    return belief


# --- K-tracking ----------------------------------------------------------------


def test_imposters_remaining_shrinks_when_a_confirmed_imposter_dies() -> None:
    belief = _belief(alive=7)
    assert imposters_remaining(belief) == 2

    belief.confirmed_imposters = {"white"}  # white (index 7) is dead
    assert imposters_remaining(belief) == 1

    # A confirmed imposter still alive does not shrink the at-large budget.
    belief.confirmed_imposters = {"red"}
    assert imposters_remaining(belief) == 2


# --- endgame bar -----------------------------------------------------------------


def test_vote_bar_tightens_as_the_margin_shrinks() -> None:
    # 8 alive, 2 imps: crew 6, margin 4 -> comfortable.
    assert vote_bar(_belief(alive=8)) == COMFORTABLE_VOTE_PROBABILITY
    # 7 alive: crew 5, margin 3 -> the classic 0.8 bar.
    assert vote_bar(_belief(alive=7)) == 0.8
    # 6 alive: crew 4, margin 2 -> tight.
    assert vote_bar(_belief(alive=6)) == TIGHT_VOTE_PROBABILITY


def test_must_eject_zeroes_the_bar() -> None:
    # 5 alive, 2 imps: crew 3, margin 1 -> a skip loses on the next kill.
    belief = _belief(alive=5)
    assert must_eject(belief)
    assert vote_bar(belief) == 0.0
    assert not must_eject(_belief(alive=6))


def test_crewmate_votes_best_read_in_must_eject_even_below_the_bar() -> None:
    belief = _belief(alive=5)
    belief.suspicion = {"red": 0.45, "green": 0.3}  # nobody over any normal bar
    assert crewmate_fallback_vote(belief) == "red"


def test_crewmate_skips_below_the_bar_with_margin() -> None:
    belief = _belief(alive=8)
    belief.suspicion = {"red": 0.45, "green": 0.3}
    assert crewmate_fallback_vote(belief) == VOTE_SKIP


def test_crewmate_never_votes_self_or_the_dead() -> None:
    belief = _belief(alive=5, self_color="blue")
    # Self tops the ranking (others accused us); a dead player ranks second.
    belief.suspicion = {"blue": 0.99, "white": 0.98, "red": 0.4}
    assert crewmate_fallback_vote(belief) == "red"


# --- imposter pile-on -------------------------------------------------------------


def test_imposter_joins_the_crew_plurality_on_a_non_teammate() -> None:
    belief = _belief(self_color="red", dots=(VoteDot(voter=1, target=2), VoteDot(voter=3, target=2)))
    belief.self_role = "imposter"
    belief.teammate_colors = {"yellow"}
    assert imposter_fallback_vote(belief) == "green"  # slot 2
    assert fallback_vote(belief) == "green"


def test_imposter_skips_when_the_plurality_is_a_teammate_or_absent() -> None:
    no_votes = _belief(self_color="red")
    no_votes.self_role = "imposter"
    assert imposter_fallback_vote(no_votes) == VOTE_SKIP

    teammate_pile = _belief(self_color="red", dots=(VoteDot(voter=1, target=3), VoteDot(voter=2, target=3)))
    teammate_pile.self_role = "imposter"
    teammate_pile.teammate_colors = {"yellow"}  # slot 3 is the teammate
    assert imposter_fallback_vote(teammate_pile) == VOTE_SKIP


# --- anti-split -------------------------------------------------------------------


def test_anti_split_swaps_a_trailing_vote_onto_the_plurality() -> None:
    # Two votes on green, none on our pick red; green is plausibly guilty.
    belief = _belief(dots=(VoteDot(voter=3, target=2), VoteDot(voter=4, target=2)))
    belief.suspicion = {"red": 0.85, "green": 0.5}
    assert anti_split_swap(belief, "red", remaining_ticks=50) == "green"


def test_anti_split_holds_far_from_the_deadline() -> None:
    belief = _belief(dots=(VoteDot(voter=3, target=2), VoteDot(voter=4, target=2)))
    belief.suspicion = {"red": 0.85, "green": 0.5}
    assert anti_split_swap(belief, "red", remaining_ticks=200) == "red"


def test_anti_split_holds_when_not_trailing_or_no_read() -> None:
    # Tied with the plurality: hold our own read.
    tied = _belief(dots=(VoteDot(voter=3, target=2), VoteDot(voter=4, target=0)))
    tied.suspicion = {"red": 0.85, "green": 0.5}
    assert anti_split_swap(tied, "red", remaining_ticks=50) == "red"

    # Plurality target we have no real read on: don't pile on.
    no_read = _belief(dots=(VoteDot(voter=3, target=2), VoteDot(voter=4, target=2)))
    no_read.suspicion = {"red": 0.85, "green": 0.1}
    assert anti_split_swap(no_read, "red", remaining_ticks=50) == "red"


def test_anti_split_never_swaps_a_skip_and_never_targets_teammates() -> None:
    belief = _belief(dots=(VoteDot(voter=3, target=2), VoteDot(voter=4, target=2)))
    assert anti_split_swap(belief, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP

    imposter = _belief(self_color="red", dots=(VoteDot(voter=1, target=2), VoteDot(voter=3, target=2)))
    imposter.self_role = "imposter"
    imposter.teammate_colors = {"green"}  # the plurality is on our teammate
    assert plurality_target(imposter) is None
    assert anti_split_swap(imposter, "yellow", remaining_ticks=50) == "yellow"


# --- skip pile-on -------------------------------------------------------------------


def _accuse(
    belief: Belief, speaker: str, target: str, *, meeting_id: int | None = None, has_evidence: bool = True
) -> None:
    text = f"saw {target}, vote {target}" if has_evidence else f"{target} sus"
    belief.accusations.append(
        Accusation(
            meeting_id=belief.phase_start_tick if meeting_id is None else meeting_id,
            tick=belief.last_tick,
            speaker_color=speaker,
            target_color=target,
            stance="accuse",
            text=text,
            has_evidence=has_evidence,
        )
    )


def _pileon_belief() -> Belief:
    # green (slot 2) voted red (slot 0) and chat-accused red this meeting; we
    # (blue) hold the uninformed ~prior posterior on red.
    belief = _belief(dots=(VoteDot(voter=2, target=0),))
    _accuse(belief, "green", "red")
    belief.suspicion = {"red": 0.28, "green": 0.28}
    return belief


def test_skip_pileon_joins_a_corroborated_accusation_near_deadline() -> None:
    belief = _pileon_belief()
    assert corroborated_accusation_target(belief) == "red"
    assert skip_pileon_swap(belief, VOTE_SKIP, remaining_ticks=50) == "red"


def test_skip_pileon_holds_far_from_the_deadline_and_for_non_skip_votes() -> None:
    belief = _pileon_belief()
    assert skip_pileon_swap(belief, VOTE_SKIP, remaining_ticks=200) == VOTE_SKIP
    assert skip_pileon_swap(belief, "green", remaining_ticks=50) == "green"


def test_skip_pileon_requires_chat_corroboration_of_the_vote() -> None:
    # A bare vote dot with no matching accusation from that voter is not enough.
    belief = _belief(dots=(VoteDot(voter=2, target=0),))
    belief.suspicion = {"red": 0.28}
    assert skip_pileon_swap(belief, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP

    # An accusation without a matching vote dot is not enough either.
    chat_only = _belief()
    _accuse(chat_only, "green", "red")
    chat_only.suspicion = {"red": 0.28}
    assert skip_pileon_swap(chat_only, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP


def test_skip_pileon_ignores_untrusted_accusers_and_cleared_targets() -> None:
    untrusted = _pileon_belief()
    untrusted.believed_imposters = {"green"}  # the accuser is our believed imposter
    assert skip_pileon_swap(untrusted, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP

    cleared = _pileon_belief()
    cleared.suspicion["red"] = SKIP_PILEON_MIN_PROBABILITY - 0.05  # exculpated read
    assert skip_pileon_swap(cleared, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP


def test_skip_pileon_ignores_stale_meetings_self_targets_and_imposter_self() -> None:
    stale = _pileon_belief()
    stale.accusations[0].meeting_id = 999  # a previous meeting's accusation
    assert skip_pileon_swap(stale, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP

    on_self = _belief(self_color="red", dots=(VoteDot(voter=2, target=0),))
    _accuse(on_self, "green", "red")
    on_self.suspicion = {"green": 0.28}
    assert skip_pileon_swap(on_self, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP

    imposter = _pileon_belief()
    imposter.self_role = "imposter"
    assert skip_pileon_swap(imposter, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP


def test_skip_pileon_never_follows_a_bare_unsupported_accusation() -> None:
    # The plain-sus disinfo channel (0/185 named a real imposter vs truecrew):
    # a vote + bare "<color> sus" chat with no evidence never recruits us.
    belief = _belief(dots=(VoteDot(voter=2, target=0),))
    _accuse(belief, "green", "red", has_evidence=False)
    belief.suspicion = {"red": 0.28, "green": 0.28}
    assert corroborated_accusation_target(belief) is None
    assert skip_pileon_swap(belief, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP


def test_skip_pileon_trusts_the_meeting_reporters_accusation() -> None:
    # A bare-format accusation from the player who opened this meeting by
    # reporting a body is grounded in that discovery — credible.
    belief = _belief(dots=(VoteDot(voter=2, target=0),))
    _accuse(belief, "green", "red", has_evidence=False)
    belief.meeting_called_by = "green"
    belief.meeting_trigger = "report"
    belief.suspicion = {"red": 0.28, "green": 0.28}
    assert skip_pileon_swap(belief, VOTE_SKIP, remaining_ticks=50) == "red"

    # A button-opened meeting grants the caller no such credibility.
    button = _belief(dots=(VoteDot(voter=2, target=0),))
    _accuse(button, "green", "red", has_evidence=False)
    button.meeting_called_by = "green"
    button.meeting_trigger = "button"
    button.suspicion = {"red": 0.28, "green": 0.28}
    assert skip_pileon_swap(button, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP


def test_skip_pileon_trusts_an_accusation_consistent_with_our_own_read() -> None:
    # A bare accusation our own suspicion independently supports is credible.
    belief = _belief(dots=(VoteDot(voter=2, target=0),))
    _accuse(belief, "green", "red", has_evidence=False)
    belief.suspicion = {"red": 0.6, "green": 0.28}
    assert skip_pileon_swap(belief, VOTE_SKIP, remaining_ticks=50) == "red"


def test_skip_pileon_never_targets_teammates_or_the_dead() -> None:
    teammate = _pileon_belief()
    teammate.teammate_colors = {"red"}
    assert skip_pileon_swap(teammate, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP

    dead = _pileon_belief()
    dead.roster["red"].life_status = "dead"
    dead.voting = dead.voting.model_copy(
        update={
            "candidates": tuple(
                candidate.model_copy(update={"alive": candidate.color != "red"})
                for candidate in dead.voting.candidates
            )
        }
    )
    assert skip_pileon_swap(dead, VOTE_SKIP, remaining_ticks=50) == VOTE_SKIP


# --- announce bar -------------------------------------------------------------------


def test_announce_only_at_confirmed_witness_level() -> None:
    # A vote-bar read (0.8) is votable but NOT announceable: 2026-06-11 evals
    # showed led accusations ran 42% accurate vs truecrew and announcing
    # preceded 2 of our 4 ejections vs the champion field.
    below = _belief()
    below.suspicion = {"red": ANNOUNCE_MIN_PROBABILITY - 0.05}
    assert not should_announce(below, "red")

    over = _belief()
    over.suspicion = {"red": ANNOUNCE_MIN_PROBABILITY}
    assert should_announce(over, "red")

    confirmed = _belief()
    confirmed.suspicion = {"red": 0.5}
    confirmed.confirmed_imposters = {"red"}
    assert should_announce(confirmed, "red")


def test_announce_never_for_skips_or_imposters() -> None:
    belief = _belief()
    belief.suspicion = {"red": 0.99}
    assert not should_announce(belief, VOTE_SKIP)

    imposter = _belief()
    imposter.self_role = "imposter"
    imposter.suspicion = {"red": 0.99}
    assert not should_announce(imposter, "red")


# --- deadline posterior gate -----------------------------------------------------


def test_deadline_gate_skips_a_low_posterior_crew_vote() -> None:
    belief = _belief(alive=8)
    belief.self_role = "crewmate"
    belief.suspicion = {"red": DEADLINE_VOTE_MIN_PROBABILITY - 0.1}
    assert deadline_posterior_gate(belief, "red") == VOTE_SKIP


def test_deadline_gate_passes_a_strong_read_and_confirmed_imposters() -> None:
    strong = _belief(alive=8)
    strong.self_role = "crewmate"
    strong.suspicion = {"red": DEADLINE_VOTE_MIN_PROBABILITY}
    assert deadline_posterior_gate(strong, "red") == "red"

    confirmed = _belief(alive=8)
    confirmed.self_role = "crewmate"
    confirmed.suspicion = {"red": 0.1}
    confirmed.confirmed_imposters = {"red"}
    assert deadline_posterior_gate(confirmed, "red") == "red"


def test_deadline_gate_exempts_must_eject_imposters_and_skips() -> None:
    # Must-eject: any read beats a skip, even a weak one.
    must = _belief(alive=5)
    must.self_role = "crewmate"
    must.suspicion = {"red": 0.2}
    assert must_eject(must)
    assert deadline_posterior_gate(must, "red") == "red"

    # The imposter plurality-join is deliberately posterior-free.
    imposter = _belief(alive=8)
    imposter.self_role = "imposter"
    imposter.suspicion = {}
    assert deadline_posterior_gate(imposter, "red") == "red"

    # A skip passes through untouched.
    skip = _belief(alive=8)
    skip.self_role = "crewmate"
    assert deadline_posterior_gate(skip, VOTE_SKIP) == VOTE_SKIP
