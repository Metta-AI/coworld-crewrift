"""Game-theory vote policy (design §10.2): who to vote for, given the game state.

The suspicion model (``strategy.suspicion``) answers *who is most likely an
imposter*; this module answers *what vote maximizes our win chance given that
posterior and the live game state*. Crewrift's vote game has structure worth
exploiting:

- **The imposter budget is known** (K = 2 by default): once a confirmed imposter
  is dead, the remaining budget shrinks — ``imposters_remaining`` tracks it.
- **Wrong ejections help the imposters** (the crew thins toward parity), so the
  evidence bar for voting a player out should *rise* as the margin shrinks…
- …**except in a must-eject state**: when the alive crew is within one of the
  imposters, a skipped vote hands the imposters the game on the next kill, so the
  crew must vote its best read regardless of confidence (``vote_bar`` → 0).
- **Ties and splits eject no one**, which favors the imposters. Near the deadline
  a trailing vote is wasted; ``anti_split_swap`` joins the forming plurality when
  it lands on a plausibly guilty player.
- **An imposter's safest vote is the crowd's**: ``imposter_fallback_vote`` joins
  the crew plurality on a non-teammate instead of skipping, so the imposter's
  vote record looks like everyone else's and pushes a crewmate out.
"""

from __future__ import annotations

from collections import Counter

from players.crewrift.crewborg.perception.entities import SKIP_VOTE_TARGET
from players.crewrift.crewborg.strategy.meeting.schema import VOTE_SKIP
from players.crewrift.crewborg.strategy.suspicion import VOTE_PROBABILITY, _imposter_count
from players.crewrift.crewborg.types import Accusation, Belief

# State-dependent evidence bars (posterior P(imposter)) for the crewmate vote.
# margin = alive crew − imposters remaining: how many wrong ejections / kills the
# crew can absorb before parity.
COMFORTABLE_VOTE_PROBABILITY = 0.75  # margin ≥ 4: a mistake is recoverable
TIGHT_VOTE_PROBABILITY = 0.9  # margin ≤ 2: one wrong eject puts us at must-eject

# Anti-split: within this many ticks of the deadline, a trailing vote is wasted —
# join the plurality if its target is at least this plausibly an imposter.
ANTI_SPLIT_REMAINING_TICKS = 96
ANTI_SPLIT_MIN_PROBABILITY = 0.3

# Skip pile-on: a skipping crewmate near the deadline joins a *corroborated*
# accusation — some other voter both voted the target AND chat-accused it this
# meeting (a confident witness leading a charge), the accuser is not themselves a
# believed/confirmed imposter, and we hold no exculpatory read on the target
# (posterior at least this; the uninformed 8p/2imp prior is ~0.28). Without this,
# a lone witness's correct vote always loses to the crew's collective skip — the
# 2026-06-10 eval saw 84% skips and zero ejections in 20 episodes.
SKIP_PILEON_MIN_PROBABILITY = 0.2

# Accuser credibility (2026-06-11 evals): vote-with-chat corroboration alone is
# not enough — against the champion field the pile-on ran 21% accurate because
# it followed bare chat assertions, while against truecrew it ran 89% because
# the followed accusations were body-report-grounded. A pile-on therefore also
# requires the accusation to be *credible*: tied to evidence (evidence wording
# in the line, or the accuser opened this meeting by reporting a body), or
# consistent with our own independent read on the target (posterior at least
# ``PILEON_OWN_READ_PROBABILITY``). Bare unsupported "<color> sus" assertions
# never qualify — vs truecrew 0/185 of them named a real imposter.
PILEON_OWN_READ_PROBABILITY = 0.5

# Deadline posterior gate (2026-06-11, v8 0.1.52 field eval / FINDINGS_v4
# Addendum B): the deadline auto-submit fires far more in meeting-heavy metas
# and votes on weaker reads than the deliberate tally-wait path. A crewmate's
# deadline vote on a player must clear this posterior or fall back to skip —
# except in must-eject (any read beats a skip there) and for confirmed
# imposters. Imposters are exempt: their deadline plurality-join is the
# free-parity ejection channel, deliberately posterior-free.
DEADLINE_VOTE_MIN_PROBABILITY = 0.5

# Announce bar (2026-06-11 evals): lead an accusation in chat only at
# confirmed-witness-level posterior. Our led accusations ran 42% accurate vs
# truecrew (we voted in 12 of its 15 wrong crew ejections) and 2 of our 4
# ejections vs the champion field came in meetings where we announced — the
# lobby turns on the loudest accuser. Below this bar we vote our read silently.
ANNOUNCE_MIN_PROBABILITY = 0.9


# --- game-state counts --------------------------------------------------------


def imposters_remaining(belief: Belief) -> int:
    """The imposter budget still at large: K minus confirmed imposters now dead."""

    confirmed_dead = sum(
        1
        for color in belief.confirmed_imposters
        if (record := belief.roster.get(color)) is not None and record.life_status == "dead"
    )
    return max(0, _imposter_count(belief) - confirmed_dead)


def alive_count(belief: Belief) -> int:
    """Players currently alive, including self (known reliably during meetings)."""

    alive = {color for color, record in belief.roster.items() if record.life_status == "alive"}
    self_color = belief.voting.self_marker_color
    if self_color is not None and belief.self_role != "dead":
        alive.add(self_color)
    return len(alive)


def must_eject(belief: Belief) -> bool:
    """True when skipping loses: the next kill would reach (or pass) parity.

    With ``I`` imposters and ``C`` crew alive, the imposters win at ``I >= C``;
    a skipped vote lets them kill once more, so at ``C - I <= 1`` the crew must
    eject its best read now.
    """

    imps = imposters_remaining(belief)
    if imps <= 0:
        return False
    crew = alive_count(belief) - imps
    return crew - imps <= 1


def vote_bar(belief: Belief) -> float:
    """The evidence bar (posterior P(imposter)) for voting a player out now.

    Replaces the flat ``VOTE_PROBABILITY``: high while the crew can afford a
    mistake-margin squeeze, zero in a must-eject state (any read beats a skip).
    """

    if must_eject(belief):
        return 0.0
    imps = imposters_remaining(belief)
    crew = alive_count(belief) - imps
    margin = crew - imps
    if margin <= 2:
        return TIGHT_VOTE_PROBABILITY
    if margin == 3:
        return VOTE_PROBABILITY
    return COMFORTABLE_VOTE_PROBABILITY


# --- live-tally reads ---------------------------------------------------------


def vote_tally(belief: Belief) -> Counter[str]:
    """Live votes per target color from the meeting's vote dots (skip excluded)."""

    voting = belief.voting
    slot_to_color = {candidate.slot: candidate.color for candidate in voting.candidates}
    tally: Counter[str] = Counter()
    for dot in voting.dots:
        if dot.target == SKIP_VOTE_TARGET:
            continue
        color = slot_to_color.get(dot.target)
        if color is not None:
            tally[color] += 1
    return tally


def plurality_target(belief: Belief) -> str | None:
    """The alive, non-self, non-teammate player currently leading the tally."""

    excluded = _excluded_targets(belief)
    alive = _alive_candidate_colors(belief)
    ranked = [
        (count, color)
        for color, count in vote_tally(belief).items()
        if color not in excluded and color in alive
    ]
    if not ranked:
        return None
    return max(ranked)[1]


# --- fallback votes (the deterministic engine's pick) --------------------------


def fallback_vote(belief: Belief) -> str:
    """The deterministic vote: a player color or ``VOTE_SKIP``, by role."""

    if belief.self_role == "imposter":
        return imposter_fallback_vote(belief)
    return crewmate_fallback_vote(belief)


def crewmate_fallback_vote(belief: Belief) -> str:
    """Top suspect over the state-dependent bar; in must-eject, the best read."""

    excluded = _excluded_targets(belief)
    alive = _alive_candidate_colors(belief)
    candidates = {
        color: p
        for color, p in belief.suspicion.items()
        if color not in excluded and (not alive or color in alive)
    }
    if not candidates:
        return VOTE_SKIP
    color, p = max(candidates.items(), key=lambda kv: kv[1])
    return color if p >= vote_bar(belief) else VOTE_SKIP


def imposter_fallback_vote(belief: Belief) -> str:
    """Join the crew plurality on a non-teammate; skip when none has formed.

    Voting with the crowd both looks crew-like and pushes a crewmate toward
    ejection. With no consensus forming, a lone accusation vote is a suspicion
    trail — skip instead.
    """

    target = plurality_target(belief)
    return target if target is not None else VOTE_SKIP


def anti_split_swap(belief: Belief, target: str, remaining_ticks: int) -> str:
    """Near the deadline, swap a trailing player vote onto the plurality.

    Ties and splits eject no one (which favors the imposters), so once the
    meeting is in its final stretch a vote that trails the plurality is wasted.
    Swap only when the plurality target is legal for us and — for a crewmate —
    at least plausibly guilty (``ANTI_SPLIT_MIN_PROBABILITY``).
    """

    if remaining_ticks > ANTI_SPLIT_REMAINING_TICKS or target == VOTE_SKIP:
        return target
    plurality = plurality_target(belief)
    if plurality is None or plurality == target:
        return target
    tally = vote_tally(belief)
    if tally[plurality] <= tally[target]:
        return target  # our pick is not trailing: hold the line
    if belief.self_role != "imposter" and belief.suspicion.get(plurality, 0.0) < ANTI_SPLIT_MIN_PROBABILITY:
        return target  # don't pile onto a player we have no read on
    return plurality


def skip_pileon_swap(belief: Belief, target: str, remaining_ticks: int) -> str:
    """Near the deadline, swap a crewmate's *skip* onto a corroborated accusation.

    A correct lone accusation (e.g. a witnessed kill) otherwise loses to the
    crew's collective skip — skip wins the plurality and the imposter walks.
    The bar for following someone else's vote is corroboration, not count: the
    voter must also have chat-accused the same target this meeting (our
    deterministic crew only announce a read at/above the vote bar), the accuser
    must not be a believed/confirmed imposter, and we must hold no exculpatory
    read on the target (``SKIP_PILEON_MIN_PROBABILITY``). Crewmate-only; an
    imposter's crowd-joining is :func:`imposter_fallback_vote`.
    """

    if target != VOTE_SKIP or belief.self_role == "imposter":
        return target
    if remaining_ticks > ANTI_SPLIT_REMAINING_TICKS:
        return target
    corroborated = corroborated_accusation_target(belief)
    return corroborated if corroborated is not None else target


def corroborated_accusation_target(belief: Belief) -> str | None:
    """The leading voted-AND-chat-accused target this meeting, if plausible.

    A target qualifies when at least one voter (not us, not a believed/confirmed
    imposter) both placed a vote dot on it and *credibly* accused it in this
    meeting's chat (:func:`_credible_accusation` — evidence-tied, or consistent
    with our own read), the target is alive / non-self / non-teammate, and our
    own posterior on it is not exculpatory. Bare unsupported "<color> sus"
    assertions never recruit our vote. Ties break toward the higher tally then
    our higher posterior.
    """

    voting = belief.voting
    meeting_id = belief.phase_start_tick
    excluded = _excluded_targets(belief)
    alive = _alive_candidate_colors(belief)
    untrusted = belief.believed_imposters | belief.confirmed_imposters
    self_color = voting.self_marker_color

    accusers_by_target: dict[str, set[str]] = {}
    for accusation in belief.accusations:
        if accusation.meeting_id != meeting_id or accusation.stance != "accuse":
            continue
        speaker = accusation.speaker_color
        if speaker is None or speaker == self_color or speaker in untrusted:
            continue
        if not _credible_accusation(belief, accusation):
            continue
        accusers_by_target.setdefault(accusation.target_color, set()).add(speaker)

    slot_to_color = {candidate.slot: candidate.color for candidate in voting.candidates}
    tally = vote_tally(belief)
    candidates: list[tuple[int, float, str]] = []
    for dot in voting.dots:
        if dot.target == SKIP_VOTE_TARGET:
            continue
        target = slot_to_color.get(dot.target)
        voter = slot_to_color.get(dot.voter)
        if target is None or voter is None:
            continue
        if target in excluded or (alive and target not in alive):
            continue
        if voter not in accusers_by_target.get(target, ()):
            continue  # the vote is not backed by that voter's own accusation
        p = belief.suspicion.get(target, 0.0)
        if p < SKIP_PILEON_MIN_PROBABILITY:
            continue  # we have an exculpatory / cleared read on the target
        candidates.append((tally[target], p, target))
    if not candidates:
        return None
    return max(candidates)[2]


def _credible_accusation(belief: Belief, accusation: Accusation) -> bool:
    """Whether one accusation is credible enough to recruit our pile-on vote.

    Credible means tied to evidence: the line itself carries evidence wording
    (``has_evidence`` — e.g. truecrew's 84%-accurate "body in <room> sus
    <color>" reports), or the accuser opened this meeting by reporting a body
    (their accusation is grounded in that discovery), or our own suspicion
    independently supports the claim (posterior ≥
    ``PILEON_OWN_READ_PROBABILITY``). A bare assertion meeting none of these is
    the plain-sus disinfo channel — never follow it.
    """

    if accusation.has_evidence:
        return True
    if (
        belief.meeting_trigger == "report"
        and belief.meeting_called_by is not None
        and accusation.speaker_color == belief.meeting_called_by
    ):
        return True
    return belief.suspicion.get(accusation.target_color, 0.0) >= PILEON_OWN_READ_PROBABILITY


def deadline_posterior_gate(belief: Belief, target: str) -> str:
    """Gate a crewmate's deadline auto-submit vote on posterior; prefer skip.

    The deadline auto-submit is a backstop, not a read: it fires when the timer
    forces a vote, so its target can come from a weak tentative or a low-bar
    swap. A wrong ejection helps the imposters, so below
    ``DEADLINE_VOTE_MIN_PROBABILITY`` a crewmate's deadline vote becomes a skip
    — unless the state is must-eject (skipping loses outright) or the target is
    a confirmed imposter. Imposters keep their plurality-join unchanged.
    """

    if target == VOTE_SKIP or belief.self_role == "imposter":
        return target
    if target in belief.confirmed_imposters:
        return target
    if must_eject(belief):
        return target
    if belief.suspicion.get(target, 0.0) >= DEADLINE_VOTE_MIN_PROBABILITY:
        return target
    return VOTE_SKIP


def should_announce(belief: Belief, vote: str) -> bool:
    """Whether to lead the accusation in chat, or vote the read silently.

    Announce only at confirmed-witness level: the target is a witnessed-caught
    confirmed imposter, or our posterior clears ``ANNOUNCE_MIN_PROBABILITY``.
    Announce-then-die is real — see the bar's comment above.
    """

    if belief.self_role == "imposter" or vote == VOTE_SKIP:
        return False
    if vote in belief.confirmed_imposters:
        return True
    return belief.suspicion.get(vote, 0.0) >= ANNOUNCE_MIN_PROBABILITY


# --- helpers -------------------------------------------------------------------


def _excluded_targets(belief: Belief) -> set[str]:
    excluded = set(belief.teammate_colors)
    if belief.voting.self_marker_color is not None:
        excluded.add(belief.voting.self_marker_color)
    return excluded


def _alive_candidate_colors(belief: Belief) -> set[str]:
    """Colors legal to vote for by aliveness — candidate grid first, roster fallback.

    Empty only before any alive information exists; callers treat empty as
    "no constraint" so the policy still works in belief-only unit tests.
    """

    from_grid = {candidate.color for candidate in belief.voting.candidates if candidate.alive}
    if from_grid:
        return from_grid
    return {color for color, record in belief.roster.items() if record.life_status == "alive"}
