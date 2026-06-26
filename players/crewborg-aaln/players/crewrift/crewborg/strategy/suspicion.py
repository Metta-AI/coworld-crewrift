"""Bayesian suspicion: posterior P(imposter) per player (design §10.1).

→ Canonical reference: ``docs/designs/suspicion.md`` — the living home for the
model, each evidence type's log-LR function (form + parameters + shape), the offline
fitting workflow, and the provenance log. Update that doc whenever a function or its
constants change.

Crewmate POV. For every other player we maintain `belief.suspicion[color]` = the
posterior **probability they are an imposter**, updated from a combinatorial prior
by the evidence we have observed. The score is a real probability, so thresholds
(e.g. the flee bar) are interpretable — no magic numbers.

**Prior.** With `P` players and `K` imposters, a crewmate knows the `K` imposters
are among the other `P − 1`; by symmetry each other player's marginal prior is
`K / (P − 1)`. `K` is derived from the player count via the game's auto formula
(`(P − 3) // 2`), overridable by `belief.imposter_count`.

**Update.** Work in log-odds: `logit(P) = logit(prior) + Σ_e logLR(e)` over observed
evidence `e`. `P = sigmoid(logit)`. The log-LR of each graded cue is a simple,
hand-written **function of the event's features** (`_*_log_lr` below), not a flat
constant — because the relationship isn't flat (a skilled imposter flees rather than
dwelling). The function forms and their constants are the **parameterization** (and
the learnable surface — there is no learning machinery yet).

**Evidence**, by type, contributes its most-suspicious instance (we aggregate with
`max` per type), so an unbounded event log can't inflate the posterior and there's
no double-counting; and because role is a fixed latent, evidence **persists** (no
time decay):

- Near-certain (`WITNESSED_LOG_LR` ⇒ P ≈ 1), from frame-to-frame transitions on the
  tape (§5.1): *witnessed kill* (lone kill-range neighbour of a just-killed victim)
  and *witnessed vent* (emergence / submersion, line-of-sight via the `shadow` mask).
- Graded functions over the event log (§5.2): **vent dwell** (weak, ~flat past a
  pass-through), **body proximity** (log-LR *decreases* with dwell — brief is the
  only window on a fleeing killer), **follow-to-death** (log-LR *increases* with how
  long the shadowing lasted).
- Social cues over the episode accusation graph + meeting vote history
  (``_social_log_lr``): defended-by / accused-by a confirmed imposter (their speech
  is inverted evidence), crowd accusation by independent speakers (evidence-backed
  lines only), the plain-sus disinfo tell (a bare "<color> sus" exculpates the
  named color and incriminates the speaker — 2026-06-11 truecrew eval), and having
  voted to eject a confirmed imposter (crew-like).

`believed_imposters` (which gates Flee) is every alive player with `P ≥
FLEE_PROBABILITY`. Crewmate-only — an imposter knows the truth, a ghost doesn't flee.

v1 simplifications (documented for later): naive-Bayes independence between evidence
types. The prior now redistributes the imposter budget as players are confirmed or
die (`_prior_imposter_p`); the social cues add the first (weak) exculpatory terms.
A proper joint model over the full K-imposter assignment remains a refinement.

TODO(meeting-call attribution): the meeting-call interstitial (upstream
2026-06-10) now exposes WHO opened every meeting and how
(``belief.meeting_called_by`` / ``meeting_trigger`` / ``MeetingRecord.called_by``).
Candidate cues to fit per docs/designs/suspicion.md §6 before wiring in:
self-report patterns (reporter of a body they were last seen near), serial
reporters across meetings, and button calls correlated with imposter cooldown
resets. Raw reporter identity alone is ~LR 1 (innocent crew report bodies too),
so no log-LR term is added until the conditional cues are fitted offline.
"""

from __future__ import annotations

import math

from players.crewrift.crewborg.action import KILL_RANGE_SQ
from players.crewrift.crewborg.strategy.occupancy import (
    neighbors_within,
    players_in_rect,
    rect_visible,
)
from players.crewrift.crewborg.types import Belief, PerceptionFrame, PlayerEvent, PlayerRecord

# Each evidence type contributes a log-likelihood-ratio, log(P(e|imp)/P(e|crew)), to
# the posterior. Witnessed kill/vent are definitional near-certainties (a constant).
# The graded event-log cues use simple, hand-written **per-event functions** of the
# event's features (duration, distance) — `_*_log_lr` below — because the
# relationship is not flat: a skilled imposter *flees* rather than dwelling, so e.g.
# body-proximity is MORE suspicious when brief. The function form + its constants ARE
# the parameterization (no learning machinery yet); docs/designs/suspicion.md §3
# documents each shape and §6 how to (re)fit the constants from replays. Keep code
# and doc in sync, and log changes in the provenance table (§7).

# Near-certain catches (we saw it happen): an overwhelming log-LR ⇒ P ≈ 1.
WITNESSED_LOG_LR = math.log(1e6)

# vent dwell — weak: a real venter teleports (caught by the transition detector), so
# merely standing on a vent is a ~flat cue once it is more than a pass-through.
VENT_CROSS_TICKS = 3  # ≤ this many ticks on a vent tile is just crossing it ⇒ neutral
VENT_DWELL_LOG_LR = math.log(8.0)

# body proximity — DECREASING in dwell: brief presence is the only window on a
# fleeing killer; a long camp at a corpse is (innocent) reporter behaviour. Full at
# first sight, fading linearly to 0 by BODY_FADE_TICKS.
BODY_NEAR_DIST = 16  # world px — "right next to it", not passing by
BODY_NEAR_LOG_LR = math.log(3.0)
BODY_FADE_TICKS = 48  # the log-LR fades to 0 over ~2 s of lingering

# follow-to-death — INCREASING in dwell (saturating): sustained shadowing of a player
# who then died is stalking. Gated on the target now being dead and the follow ending
# near the death.
FOLLOW_FULL_TICKS = 48  # the ramp reaches full at ~2 s of sustained proximity
FOLLOW_DEATH_WINDOW_TICKS = 72  # the follow ended ~within 3 s of finding the body
FOLLOW_LOG_LR = math.log(6.0)

# Social (who-sus'd-who) cues from the episode accusation graph + meeting vote
# history (docs/designs/suspicion.md §3.6). Each is a boolean per player, so it
# contributes at most once — naturally bounded like the max-aggregated cues.
# Confirmed imposters defend each other and scapegoat crew, so their speech is
# *inverted* evidence; voting a confirmed imposter out is crew-like behaviour.
SOCIAL_DEFENDED_BY_CONFIRMED_LOG_LR = math.log(4.0)
SOCIAL_ACCUSED_BY_CONFIRMED_LOG_LR = -math.log(2.0)
SOCIAL_VOTED_FOR_CONFIRMED_LOG_LR = -math.log(2.0)
# Independent corroboration: distinct (non-confirmed, non-self) accusers piling
# onto the same player is weak positive evidence — crowds are sometimes right.
# Counts only *evidence-backed* accusations (``Accusation.has_evidence``): bare
# "<color> sus" assertions are a disinfo channel, not corroboration (2026-06-11
# truecrew eval: 0/185 bare-sus lines named a real imposter).
SOCIAL_CROWD_MIN_ACCUSERS = 2
SOCIAL_CROWD_ACCUSED_LOG_LR = math.log(1.5)
# The plain-sus disinfo tell (2026-06-11 truecrew eval, new chat-format cue):
# a bare "<color> sus" with no evidence wording exculpates the *named* color
# (0/185 named a real imposter; 11/16 wrong ejections followed one) and marks
# the *speaker* as likely steering the meeting (imposters frame crew this way).
# Magnitudes deliberately conservative — the tell was measured against one
# opponent engine; both cues are boolean per player (contribute at most once).
# The exculpation only applies while no evidence-backed accusation names the
# same color (real evidence beats format-level disinfo).
PLAIN_SUS_TARGET_LOG_LR = -math.log(3.0)
PLAIN_SUS_SPEAKER_LOG_LR = math.log(2.0)

# Flee a player once P(imposter) reaches this — a real probability, so the bar is
# interpretable (only near-certainty triggers the reactive Flee).
FLEE_PROBABILITY = 0.9
# Vote a player out once P(imposter) reaches this. Ejecting an innocent helps the
# imposters, so the bar is high but a touch below the (reactive) flee bar — a vote is
# a deliberate, one-shot decision made with the meeting's full evidence.
VOTE_PROBABILITY = 0.8
# Clamp the prior away from 0/1 so its log-odds stays finite.
PRIOR_MIN, PRIOR_MAX = 1e-3, 0.99

# Max distance a player can walk in one tick (MaxSpeed/MotionScale = 704/256 ≈ 2.75,
# rounded up): a player materialising inside a vent from beyond this vented.
VENT_WALK_MARGIN = 3


def update_suspicion(belief: Belief) -> None:
    """Recompute `suspicion` (posterior P(imp)) + `believed_imposters` each tick.

    Run after `update_belief`/`update_event_log` so the strategy snapshot is current.
    """

    if belief.self_role in ("imposter", "dead"):
        belief.suspicion = {}
        belief.believed_imposters = set()
        return
    _detect_witnessed_kill(belief)
    _detect_witnessed_vent(belief)
    _recompute(belief)


# --- prior ------------------------------------------------------------------


def _imposter_count(belief: Belief) -> int:
    if belief.imposter_count is not None:
        return belief.imposter_count
    total = belief.total_player_count
    return 0 if total < 5 else max(0, min((total - 3) // 2, total - 1))


def _prior_imposter_p(belief: Belief) -> float:
    """The marginal prior for an *unconfirmed* player: hidden K over candidates.

    The static ``K / (P − 1)`` ignores what we have learned: every confirmed
    imposter (alive or dead) is attributed budget, and dead players are no longer
    candidates. The remaining hidden budget is spread over the remaining
    unconfirmed, not-known-dead others — so catching one of two imposters roughly
    halves everyone else's prior instead of leaving it stale.
    """

    k_hidden = max(0, _imposter_count(belief) - len(belief.confirmed_imposters))
    dead_others = sum(1 for record in belief.roster.values() if record.life_status == "dead")
    confirmed_alive = sum(
        1
        for color in belief.confirmed_imposters
        if (record := belief.roster.get(color)) is None or record.life_status != "dead"
    )
    n_candidates = max(1, belief.total_player_count - 1 - dead_others - confirmed_alive)
    return min(max(k_hidden / n_candidates, PRIOR_MIN), PRIOR_MAX)


# --- tier 1: near-certain transitions → confirmed_imposters ------------------


def _frame_pair(belief: Belief) -> tuple[PerceptionFrame, PerceptionFrame] | None:
    """The (previous, current) tape frames, only if they are consecutive ticks."""

    frames = belief.recent_frames
    if len(frames) < 2:
        return None
    prev, curr = frames[-2], frames[-1]
    return (prev, curr) if curr.tick == prev.tick + 1 else None


def _detect_witnessed_kill(belief: Belief) -> None:
    pair = _frame_pair(belief)
    if pair is None:
        return
    prev, curr = pair
    for victim_color in curr.bodies:
        victim_pos = prev.players.get(victim_color)  # was this body's owner alive a frame ago?
        if victim_pos is None:
            continue
        killers = [
            color
            for color in neighbors_within(prev, victim_pos, KILL_RANGE_SQ, exclude=victim_color)
            if color not in belief.teammate_colors
        ]
        if len(killers) == 1:  # a single, unambiguous neighbour ⇒ the killer
            belief.confirmed_imposters.add(killers[0])


def _detect_witnessed_vent(belief: Belief) -> None:
    pair = _frame_pair(belief)
    if pair is None or belief.map is None:
        return
    prev, curr = pair
    for vent in belief.map.vents:
        x, y, w, h = vent.x, vent.y, vent.w, vent.h
        # (a) Emergence: vent + walk-margin in line of sight and clear last frame, occupied now.
        watched_clear = rect_visible(prev, x, y, w, h, margin=VENT_WALK_MARGIN) and not players_in_rect(
            prev, x, y, w, h, margin=VENT_WALK_MARGIN
        )
        if watched_clear:
            for color in players_in_rect(curr, x, y, w, h):
                belief.confirmed_imposters.add(color)
        # (b) Submersion: a player was in the vent last frame; vent still in sight, player gone.
        if rect_visible(curr, x, y, w, h):
            for color in players_in_rect(prev, x, y, w, h):
                if color not in curr.players:
                    belief.confirmed_imposters.add(color)


# --- tier 2: graded evidence from the event log -----------------------------


# --- per-event log-LR functions ---------------------------------------------
# Each maps one event's features → its log-likelihood-ratio contribution (0.0 =
# neutral). Simple closed forms; the constants above are the parameters.


def _vent_dwell_log_lr(event: PlayerEvent) -> float:
    return VENT_DWELL_LOG_LR if event.duration_ticks > VENT_CROSS_TICKS else 0.0


def _body_proximity_log_lr(event: PlayerEvent) -> float:
    if event.min_dist is None or event.min_dist > BODY_NEAR_DIST:
        return 0.0
    fade = max(0.0, 1.0 - event.duration_ticks / BODY_FADE_TICKS)  # brief ⇒ more suspicious
    return BODY_NEAR_LOG_LR * fade


def _follow_log_lr(event: PlayerEvent, belief: Belief) -> float:
    victim = belief.roster.get(event.target_color)
    if victim is None or victim.life_status != "dead" or victim.death_seen_tick is None:
        return 0.0
    if abs(victim.death_seen_tick - event.end_tick) > FOLLOW_DEATH_WINDOW_TICKS:
        return 0.0
    ramp = min(1.0, event.duration_ticks / FOLLOW_FULL_TICKS)  # longer shadowing ⇒ more
    return FOLLOW_LOG_LR * ramp


def _graded_log_lr(belief: Belief, record: PlayerRecord) -> float:
    """A player's total graded log-LR: the most-suspicious instance per evidence type.

    Aggregating with ``max`` (not a sum over every event) keeps each type's
    contribution bounded and double-count-free even with an unbounded event log.
    """

    vent = max((_vent_dwell_log_lr(e) for e in record.events if e.kind == "vent"), default=0.0)
    body = max((_body_proximity_log_lr(e) for e in record.events if e.kind == "near_body"), default=0.0)
    follow = max((_follow_log_lr(e, belief) for e in record.events if e.kind == "proximity"), default=0.0)
    return vent + body + follow


def _social_log_lr(belief: Belief, color: str) -> float:
    """Who-sus'd-who evidence for one player, from accusations + vote history.

    Six boolean cues (each contributes once, keeping the total bounded):

    - defended by a confirmed imposter → up (teammates defend each other);
    - accused by a confirmed imposter → down (imposters scapegoat crew);
    - accused with evidence by ≥ ``SOCIAL_CROWD_MIN_ACCUSERS`` distinct ordinary
      speakers → up (our own and confirmed imposters' accusations are excluded —
      ours would feed back into the posterior, theirs is inverted evidence
      handled above; bare no-evidence accusations never count as corroboration);
    - named by a bare "<color> sus" line with no evidence-backed accusation
      against them → down (the plain-sus disinfo tell exculpates the target);
    - spoke a bare "<color> sus" accusation themselves → up (the speaker is
      likely an imposter steering the meeting);
    - ever voted to eject a confirmed imposter → down (crew-like behaviour).
    """

    confirmed = belief.confirmed_imposters
    self_color = belief.voting.self_marker_color
    defended_by_confirmed = False
    accused_by_confirmed = False
    crowd_accusers: set[str] = set()
    plain_sus_named = False  # this player was the target of a bare accusation
    evidence_named = False  # …or of an evidence-backed one (beats the tell)
    plain_sus_spoke = False  # this player uttered a bare accusation
    for accusation in belief.accusations:
        speaker = accusation.speaker_color
        if (
            accusation.stance == "accuse"
            and not accusation.has_evidence
            and speaker == color
            and accusation.target_color != color
        ):
            plain_sus_spoke = True
        if accusation.target_color != color:
            continue
        if speaker == color:
            continue
        if accusation.stance == "defend":
            defended_by_confirmed = defended_by_confirmed or speaker in confirmed
            continue
        if speaker in confirmed:
            accused_by_confirmed = True
            continue
        if not accusation.has_evidence:
            if speaker != self_color:
                plain_sus_named = True
            continue
        evidence_named = True
        if speaker is not None and speaker != self_color:
            crowd_accusers.add(speaker)

    total = 0.0
    if defended_by_confirmed:
        total += SOCIAL_DEFENDED_BY_CONFIRMED_LOG_LR
    if accused_by_confirmed:
        total += SOCIAL_ACCUSED_BY_CONFIRMED_LOG_LR
    if len(crowd_accusers) >= SOCIAL_CROWD_MIN_ACCUSERS:
        total += SOCIAL_CROWD_ACCUSED_LOG_LR
    if plain_sus_named and not evidence_named:
        total += PLAIN_SUS_TARGET_LOG_LR
    if plain_sus_spoke:
        total += PLAIN_SUS_SPEAKER_LOG_LR
    if confirmed and any(record.votes.get(color) in confirmed for record in belief.meeting_history):
        total += SOCIAL_VOTED_FOR_CONFIRMED_LOG_LR
    return total


# --- combine into the posterior ---------------------------------------------


def _recompute(belief: Belief) -> None:
    prior_logit = _logit(_prior_imposter_p(belief))
    suspicion: dict[str, float] = {}
    believed: set[str] = set()

    for color in set(belief.roster) | belief.confirmed_imposters:
        record = belief.roster.get(color)
        if record is not None and record.life_status == "dead":
            continue  # the dead are no threat (the confirmation is kept for the record)
        logit = prior_logit
        if color in belief.confirmed_imposters:
            logit += WITNESSED_LOG_LR  # any near-certain catch — overwhelming
        if record is not None:
            logit += _graded_log_lr(belief, record)
        logit += _social_log_lr(belief, color)
        logit += _hunter_log_lr(belief, color)
        p = _sigmoid(logit)
        suspicion[color] = p
        if p >= FLEE_PROBABILITY:
            believed.add(color)

    belief.suspicion = suspicion
    belief.believed_imposters = believed


def _hunter_log_lr(belief: Belief, color: str) -> float:
    """Hunter-profile behavioral evidence (0.0 unless ``CREWBORG_HUNTER`` is set).

    The early-button-caller fingerprint (``strategy.hunter``): a *button*
    meeting opened within ~250 ticks of a segment start is sussyboi's imposter
    signature (its crew presses at ~500, the cooldown-jam timing). Moderate
    evidence — some crews also rush the button — so it stacks with the graded /
    social terms instead of convicting alone.
    """

    from players.crewrift.crewborg.strategy.hunter import early_button_caller_log_lr, hunter_enabled

    if not hunter_enabled():
        return 0.0
    return early_button_caller_log_lr(belief, color)


def top_suspect(belief: Belief) -> str | None:
    """The live player to vote out — highest posterior P(imp) over `VOTE_PROBABILITY`,
    or `None` (skip) when no one is suspicious enough. Used by Attend Meeting (§7.1)."""

    if not belief.suspicion:
        return None
    color, p = max(belief.suspicion.items(), key=lambda kv: kv[1])
    return color if p >= VOTE_PROBABILITY else None


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _sigmoid(logit: float) -> float:
    logit = max(-700.0, min(700.0, logit))  # keep exp finite
    return 1.0 / (1.0 + math.exp(-logit))
