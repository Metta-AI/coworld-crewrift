"""Bayesian suspicion: posterior P(imposter) per player (design §10.1).

→ Canonical reference: ``docs/suspicion.md`` — the living home for the
model, each evidence type's log-LR function (form + parameters + shape), the offline
fitting workflow, and the provenance log. Update that doc whenever a function or its
constants change.

For every player we could vote as an imposter we maintain `belief.suspicion[color]` =
the posterior **probability they are an imposter**, updated from a combinatorial prior
by the evidence we have observed. The score is a real probability, so thresholds
(e.g. the flee bar) are interpretable — no magic numbers.

Maintained for both live roles. As a **crewmate** it scores every other player and is
a genuine belief. As an **imposter** it scores only **non-teammates** (the crewmates
it could deflect onto) — mechanically the same number, but read as "how suspicious
this crewmate *looks* on the shared evidence," to pick the most-citable deflection
target at a meeting (design §10.4). A ghost holds no suspicion.

**Never ourselves.** Our own player sprite is the camera centre and leaks into the
roster as if it were another player; ``_recompute`` skips ``belief.self_color`` (and
``event_log`` skips logging ``tailing_self`` for it — we are trivially always at our
own spot), so we never tail, suspect, accuse, or vote *ourself*. ``top_suspect`` /
``active_tail_suspect`` hard-guard the same color regardless, and Attend Meeting refuses
a self-targeted ballot — defense-in-depth around a bug that otherwise self-ejects us.

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

- Near-certain (`WITNESSED_LOG_LR` ⇒ P ≈ 1): detected from frame-to-frame transitions
  on the tape (§5.1) — *witnessed kill* (lone kill-range neighbour of a just-killed
  victim) and *witnessed vent* (emergence / submersion, line-of-sight via the `shadow`
  mask) — and recorded as `kill` / `vent_use` point events on the perpetrator's log, so
  every signal lives in one place (there is no separate "confirmed" set).
- Graded functions over the event log (§5.2): **vent dwell** (weak, ~flat past a
  pass-through), **body proximity** (log-LR *decreases* with dwell — brief is the
  only window on a fleeing killer), **follow-to-death** (log-LR *increases* with how
  long the shadowing lasted), and **being tailed** (`tailing_self`, a logistic in how
  long someone shadowed *us* — needs no death; saturates at a *moderate* P ≈ 0.72, a
  strong reason to call a meeting and accuse but not on its own near-certain).

`believed_imposters` is every alive player with `P ≥ FLEE_PROBABILITY` — the
near-certain set (crewmate-only; an imposter knows the truth, a ghost has no use for it).
It is a derived readout — traced and serialized into the meeting LLM context — not a mode gate.

**Two scoring paths.** When the vendored fitted weights load
(``data/suspicion_weights.json``, trained by ``suspicion_lab`` — see
``suspicion_lab/README.md``), the posterior is the FITTED model:
``logit = intercept + Σ w·x`` over instance-summed, exposure-aware features, with
negative weights as exculpatory evidence and the vote bar at calibrated
near-certainty (no clear-leader rule). The hand-written ``_*_log_lr`` functions
below are the fallback when no weights are available
(``CREWBORG_SUSPICION_WEIGHTS=0`` forces them). A witnessed kill/vent is a
definitional near-certainty floor on both paths.

Legacy-path simplifications (documented for later): naive-Bayes independence between
evidence types; positive-evidence-only (the prior is the baseline — no exculpatory
terms); and a static `K / (P − 1)` prior without redistributing the imposter budget
as players are caught/die (a proper joint model is a refinement).

Collaborators
-------------
Relies on:
  - ``strategy.occupancy`` — ``neighbors_within`` / ``players_in_rect`` / ``rect_visible``
    for the frame-to-frame witnessed-kill and witnessed-vent transition detectors.
  - ``strategy.event_log`` (upstream) — the durative ``PlayerRecord.events`` this reads;
    ``strategy.social_evidence`` (upstream) — the public/social counters the fitted
    feature vector reads. Both must run *before* ``update_suspicion`` each tick.
  - ``action.KILL_RANGE_SQ`` (the witnessed-kill neighbour radius); ``types`` (``Belief``,
    ``PlayerRecord``, ``PerceptionFrame``, ``PlayerEvent``); the vendored fitted weights
    ``data/suspicion_weights.json`` (optional — falls back to the hand model).
Used by:
  - ``strategy.rule_based`` (``active_tail_suspect`` → Accuse) and ``modes.accuse``.
  - ``modes.attend_meeting`` (``top_suspect`` → the vote) and ``strategy.meeting.*``.
  - ``events.py`` (tracing: ``witnessed_imposters`` + the suspicion snapshots/gauges).
Emits / touches: writes ``belief.suspicion`` (per-color posterior) and
  ``belief.believed_imposters`` every tick, and appends latched ``kill`` / ``vent_use``
  point events onto perpetrators' ``PlayerRecord.events`` (the witnessed detectors).

Modifying this file: keep code and ``docs/suspicion.md`` in sync — every log-LR
form/constant change is logged in that doc's provenance table. The fitted and hand
paths must BOTH treat a witnessed kill/vent as a definitional near-certainty floor (it
is not a fitted quantity). Never score ``belief.self_color`` or a teammate. Run order
matters: this is the last knowledge-layer step, after event-log + social-evidence.
"""

from __future__ import annotations

import importlib.resources
import json
import math
import os
from pathlib import Path

from crewborg.action import KILL_RANGE_SQ
from crewborg.strategy.occupancy import (
    neighbors_within,
    players_in_rect,
    rect_visible,
)
from crewborg.types import Belief, PerceptionFrame, PlayerEvent, PlayerEventKind, PlayerRecord

# Each evidence type contributes a log-likelihood-ratio, log(P(e|imp)/P(e|crew)), to
# the posterior. Witnessed kill/vent are definitional near-certainties (a constant).
# The graded event-log cues use simple, hand-written **per-event functions** of the
# event's features (duration, distance) — `_*_log_lr` below — because the
# relationship is not flat: a skilled imposter *flees* rather than dwelling, so e.g.
# body-proximity is MORE suspicious when brief. The function form + its constants ARE
# the parameterization (no learning machinery yet); docs/suspicion.md §3
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

# being tailed (``tailing_self``) — live evidence: a player shadowing *us* over time is
# a likely imposter lining up its target, and (unlike third-party follow) it needs no
# death. A **logistic in duration**: a brief brush ⇒ ~nothing, the ramp leaves zero
# around ~15 ticks, crosses the midpoint at ~30 ticks, and **saturates around P ≈ 0.72**
# (deliberately *moderate*, not near-certain — being tailed is a strong reason to call a
# meeting and accuse, but lots of crew move together, so it must not on its own clear the
# flee/near-certain-vote bars). Saturated LR ≈ log(6.5) against the combinatorial prior.
TAIL_SELF_LOG_LR_MAX = math.log(6.5)
TAIL_SELF_MIDPOINT_TICKS = 30  # logistic centre (P ≈ 0.5 here at a typical prior)
TAIL_SELF_STEEPNESS = 0.2  # 50 ticks ⇒ ~0.98 of max; 15 ticks ⇒ ~0.05 of max
# Once an *active* tail pushes our suspicion of the tailer to this, we are "sketched
# out" enough to stop and call a meeting (Accuse mode, ~34 ticks of sustained tailing).
ACCUSE_THRESHOLD = 0.6
# A tailing_self interval counts as *active* (they're tailing us right now) if it was
# extended within this many ticks — robust to a brief occlusion mid-tail.
ACCUSE_TAIL_RECENCY_TICKS = 6

# The near-certainty bar: a player at or above this P(imposter) enters `believed_imposters`
# — a real probability, so the bar is interpretable (only near-certainty clears it).
FLEE_PROBABILITY = 0.9
# Vote a player out once P(imposter) reaches this on its own — near-certainty (a
# witnessed catch, a saturated tail) clears the bar regardless of the field. A touch
# below the near-certainty bar: a vote is a deliberate, one-shot meeting decision.
VOTE_PROBABILITY = 0.8
# A vote also fires on a *clear leading suspect* short of near-certainty: the top
# posterior is over VOTE_LEAD_MIN_P (real evidence — more likely than not an imposter)
# AND leads the runner-up by at least VOTE_LEAD_MARGIN (it stands out, not a flat field).
# This is the "vote on a clear leader, skip when the posterior is flat" rule — ejecting
# an innocent helps the imposters, so a flat or low field skips.
VOTE_LEAD_MIN_P = 0.5
VOTE_LEAD_MARGIN = 0.2
# Clamp the prior away from 0/1 so its log-odds stays finite.
PRIOR_MIN, PRIOR_MAX = 1e-3, 0.99

# Max distance a player can walk in one tick (MaxSpeed/MotionScale = 704/256 ≈ 2.75,
# rounded up): a player materialising inside a vent from beyond this vented.
VENT_WALK_MARGIN = 3


# --- fitted weights (the learned model; suspicion_lab/README.md) ----
#
# When the vendored ``data/suspicion_weights.json`` loads, the posterior comes from
# the FITTED model: logit = intercept + Σ w·x over the runtime feature vector below
# (evidence *instances* summed — with per-context dedup — and exculpatory negative
# weights, per design §1). The hand-written ``_*_log_lr`` functions above remain the
# FALLBACK when no weights are available (and the witnessed kill/vent near-certainty
# stays a definitional floor in both paths — it is not a fitted quantity).
#
# Ops override: CREWBORG_SUSPICION_WEIGHTS=0 forces the legacy hand model; a path
# loads that file instead of the vendored asset.
WEIGHTS_PACKAGE = "crewborg.data"
WEIGHTS_RESOURCE = "suspicion_weights.json"
WEIGHTS_SCHEMA = "crewborg-suspicion-weights/v1"
# Vote bar under the fitted model — from the held-out decision simulator
# (suspicion_lab eval.py): at 0.9 the fitted posterior's votes hit imposters with
# ~100% precision; there is NO clear-leader rule on this path (it was the mis-vote
# engine — votes fire on calibrated near-certainty only).
#
# Tunable via CREWBORG_WEIGHTS_VOTE_P (a float in (0,1)) for threshold sweeps: the
# 0.9 bar maximises vote PRECISION, but a 2026-06-23 eval found v31 crew loses the
# parity race (54% of games 8/8-tasks-but-lost) by sitting passive — it wins when it
# votes (1.11 player-votes/g in wins vs 0.23 in losses). Lowering this trades
# precision for more ejections; the sweep finds the crew-win-maximising point.
def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


WEIGHTS_VOTE_PROBABILITY = _env_float("CREWBORG_WEIGHTS_VOTE_P", 0.9)
# Offline features count expander samples (one per `snapshot-every` ticks); runtime
# durations divide by this to land in the same unit. Read from the weights file.
DEFAULT_SAMPLE_UNIT_TICKS = 24
# The offline copresence gate (kill_range + 8 px), mirrored on tailing_self min_dist.
COPRESENCE_DIST_SQ = 28**2


def _load_weights() -> dict | None:
    """The fitted weights, or None (→ legacy hand model). Never raises."""

    override = os.environ.get("CREWBORG_SUSPICION_WEIGHTS", "").strip()
    if override == "0":
        return None
    try:
        if override:
            data = json.loads(Path(override).read_text())
        else:
            resource = importlib.resources.files(WEIGHTS_PACKAGE).joinpath(WEIGHTS_RESOURCE)
            data = json.loads(resource.read_text())
        if data.get("schema") != WEIGHTS_SCHEMA or "coefficients" not in data:
            return None
        return data
    except Exception:  # missing asset / bad JSON ⇒ hand model, never a crash
        return None


_WEIGHTS: dict | None = _load_weights()


def set_weights(weights: dict | None) -> None:
    """Test/ops hook: pin the scoring path (None ⇒ legacy hand model)."""

    global _WEIGHTS
    _WEIGHTS = weights


def _fitted_features(belief: Belief, record: PlayerRecord) -> dict[str, float]:
    """The runtime feature vector for one suspect (design §5; offline mirror:
    suspicion_lab/tools/features.py — keep names, units, and dedup rules aligned).

    Units: offline "samples" = one expander snapshot per ``sample_unit_ticks``;
    runtime durations divide by that. Instance summing dedupes per context: bodies
    by body color, follows summed across a victim's qualifying intervals, vent
    visits per dwell interval.
    """

    unit = float((_WEIGHTS or {}).get("sample_unit_ticks", DEFAULT_SAMPLE_UNIT_TICKS))
    tail_durations: list[int] = []
    copresence_ticks = 0
    task_ticks = 0
    vent_visits = 0
    follow_ticks = 0
    near_bodies: set[str | None] = set()
    witnessed = 0

    for event in record.events:
        if event.kind in ("kill", "vent_use"):
            witnessed += 1
        elif event.kind == "near_body":
            if event.min_dist is not None:
                near_bodies.add(event.target_color)
        elif event.kind == "tailing_self":
            tail_durations.append(event.duration_ticks)
            if event.min_dist is not None and event.min_dist**2 <= COPRESENCE_DIST_SQ:
                copresence_ticks += event.duration_ticks
        elif event.kind == "task":
            task_ticks += event.duration_ticks
        elif event.kind == "vent":
            if event.duration_ticks > VENT_CROSS_TICKS:
                vent_visits += 1
        elif event.kind == "proximity":
            victim = belief.roster.get(event.target_color)
            if (
                victim is not None
                and victim.life_status == "dead"
                and victim.death_seen_tick is not None
                and abs(victim.death_seen_tick - event.end_tick) <= FOLLOW_DEATH_WINDOW_TICKS
            ):
                follow_ticks += event.duration_ticks

    return {
        "witnessed_kills": float(witnessed),
        "near_body_bodies": float(len(near_bodies)),
        "follow_death_samples": follow_ticks / unit,
        "tail_obs_samples": sum(tail_durations) / unit,
        "tail_obs_max_run": (max(tail_durations) if tail_durations else 0) / unit,
        "vent_visits": float(vent_visits),
        "copresence_killrange_samples": copresence_ticks / unit,
        "task_site_dwell_samples": task_ticks / unit,
        "observed_samples": record.seen_ticks / unit,
        # public / social counters (strategy.social_evidence; offline names differ
        # only in the observer suffix)
        "tasks_completed_watched": float(record.tasks_completed_watched),
        "accusations_made": float(record.accusations_made),
        "times_accused": float(record.times_accused),
        "times_defended": float(record.times_defended),
        "votes_cast": float(record.votes_cast),
        "votes_skipped": float(record.votes_skipped),
        "voted_against_observer": float(record.voted_against_me),
        "vote_agreement_with_observer": float(record.vote_agreed_with_me),
        "reported_bodies": float(record.reported_bodies),
        "button_calls_made": float(record.button_calls_made),
    }


def _fitted_log_odds(belief: Belief, record: PlayerRecord) -> float:
    """intercept + Σ w·x under the loaded weights (the offline transform mirrored:
    binned indicators for ``bin_spec`` features, clipped linear counts otherwise)."""

    assert _WEIGHTS is not None
    coefs: dict[str, float] = _WEIGHTS["coefficients"]
    bin_spec: dict[str, list[float]] = _WEIGHTS.get("bin_spec", {})
    clip = float(_WEIGHTS.get("linear_clip", 5))
    logit = float(_WEIGHTS["intercept"])
    for name, value in _fitted_features(belief, record).items():
        if name in bin_spec:
            edges = [0.0, *bin_spec[name], math.inf]
            for i in range(len(edges) - 1):
                lo, hi = edges[i], edges[i + 1]
                if lo < value <= hi:
                    label = f"{name}__gt{lo:g}" if hi == math.inf else f"{name}__{lo:g}to{hi:g}"
                    logit += coefs.get(label, 0.0)
                    break
        else:
            logit += coefs.get(name, 0.0) * min(value, clip)
    return logit


def update_suspicion(belief: Belief) -> None:
    """Recompute `suspicion` (posterior P(imp)) + `believed_imposters` each tick.

    Run after `update_belief`/`update_event_log` so the strategy snapshot is current.

    Computed for **both** live roles, over the players we could vote as imposters —
    every other player for a crewmate, the **non-teammates** for an imposter (it knows
    its teammates, so it never scores them). For a crewmate the score is a genuine
    `P(imposter)`; for an imposter it's "how suspicious this crewmate *looks* on the
    shared evidence" — the same number, used to pick the most-citable deflection target
    (design §10.4). A ghost holds no suspicion.
    """

    if belief.self_role == "dead":
        belief.suspicion = {}
        belief.believed_imposters = set()
        return
    _detect_witnessed_kill(belief)
    _detect_witnessed_vent(belief)
    _recompute(belief)


# --- prior ------------------------------------------------------------------


def _imposter_count(belief: Belief) -> int:
    """Number of imposters `K`: the explicit ``belief.imposter_count`` if known, else the
    game's auto formula ``(P − 3) // 2`` (0 below 5 players), clamped to ``[0, P − 1]``."""
    if belief.imposter_count is not None:
        return belief.imposter_count
    total = belief.total_player_count
    return 0 if total < 5 else max(0, min((total - 3) // 2, total - 1))


def _prior_imposter_p(belief: Belief) -> float:
    """Each other player's marginal prior P(imposter) = ``K / (P − 1)`` by symmetry,
    clamped to ``[PRIOR_MIN, PRIOR_MAX]`` so its log-odds stays finite."""
    n_others = max(1, belief.total_player_count - 1)
    return min(max(_imposter_count(belief) / n_others, PRIOR_MIN), PRIOR_MAX)


# --- tier 1: near-certain transitions → witnessed events on the perpetrator --


def _frame_pair(belief: Belief) -> tuple[PerceptionFrame, PerceptionFrame] | None:
    """The (previous, current) tape frames, only if they are consecutive ticks."""

    frames = belief.recent_frames
    if len(frames) < 2:
        return None
    prev, curr = frames[-2], frames[-1]
    return (prev, curr) if curr.tick == prev.tick + 1 else None


def _log_witnessed(belief: Belief, color: str, kind: PlayerEventKind, *, target_color: str | None = None) -> None:
    """Record a witnessed catch as a point event on the perpetrator's log (latched).

    The detectors fire on a one-tick transition, so this is a point event
    (start == end == now). It carries no LR itself; ``_evidence_log_lr`` maps its
    presence to ``WITNESSED_LOG_LR``. A ``kill`` is deduped per victim so a persisting
    body can't re-log; ``vent_use`` is a genuine repeat each time someone vents.
    """

    # A witnessed catch is the strongest signal we have; never drop it on an ordering
    # gap. In production ``update_belief`` has already rostered any player visible in the
    # tape (and the perpetrator was, a frame ago), so this is a safety net, not the path.
    record = belief.roster.get(color)
    if record is None:
        record = PlayerRecord(color=color)
        belief.roster[color] = record
    if kind == "kill" and any(e.kind == "kill" and e.target_color == target_color for e in record.events):
        return
    record.events.append(
        PlayerEvent(kind=kind, start_tick=belief.last_tick, end_tick=belief.last_tick, target_color=target_color)
    )


def _detect_witnessed_kill(belief: Belief) -> None:
    """Latch a witnessed kill from the last consecutive frame pair: a body present this
    frame whose owner was alive last frame, with **exactly one** non-teammate inside kill
    range of that owner a frame ago ⇒ that single neighbour is the unambiguous killer (a
    ``kill`` event on its log). Ambiguous (0 or >1 neighbours) ⇒ no attribution."""
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
            _log_witnessed(belief, killers[0], "kill", target_color=victim_color)


def _detect_witnessed_vent(belief: Belief) -> None:
    """Latch a witnessed vent use from the last consecutive frame pair, two ways per vent
    rect: (a) **emergence** — the vent (+ walk margin) was in line of sight and empty last
    frame but is occupied now; (b) **submersion** — a player was in the vent last frame, the
    vent is still in sight, and the player has vanished. Each detected venter gets a
    ``vent_use`` event on its log. Needs the LOS mask (``rect_visible``) to be sure the
    absence/presence is real and not just off-screen."""
    pair = _frame_pair(belief)
    if pair is None or belief.map is None:
        return
    prev, curr = pair
    venters: set[str] = set()
    for vent in belief.map.vents:
        x, y, w, h = vent.x, vent.y, vent.w, vent.h
        # (a) Emergence: vent + walk-margin in line of sight and clear last frame, occupied now.
        watched_clear = rect_visible(prev, x, y, w, h, margin=VENT_WALK_MARGIN) and not players_in_rect(
            prev, x, y, w, h, margin=VENT_WALK_MARGIN
        )
        if watched_clear:
            venters.update(players_in_rect(curr, x, y, w, h))
        # (b) Submersion: a player was in the vent last frame; vent still in sight, player gone.
        if rect_visible(curr, x, y, w, h):
            for color in players_in_rect(prev, x, y, w, h):
                if color not in curr.players:
                    venters.add(color)
    for color in venters:
        _log_witnessed(belief, color, "vent_use")


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


def _tailing_self_log_lr(event: PlayerEvent) -> float:
    """Logistic in how long the player shadowed *us*: a brief brush is ~nothing, the
    ramp leaves zero around ~12-15 ticks, crosses half at the midpoint, and saturates
    "very sketchy" by ~50 ticks (see the constants above for the calibration)."""

    x = TAIL_SELF_STEEPNESS * (event.duration_ticks - TAIL_SELF_MIDPOINT_TICKS)
    return TAIL_SELF_LOG_LR_MAX / (1.0 + math.exp(-max(-700.0, min(700.0, x))))


def _evidence_log_lr(belief: Belief, record: PlayerRecord) -> float:
    """A player's total log-LR: the most-suspicious instance per evidence type.

    Aggregating with ``max`` (not a sum over every event) keeps each type's
    contribution bounded and double-count-free even with an unbounded event log.
    A single witnessed catch (``kill``/``vent_use``) latches the near-certain LR;
    everything else is graded over the event log.
    """

    witnessed = WITNESSED_LOG_LR if any(e.kind in ("kill", "vent_use") for e in record.events) else 0.0
    vent = max((_vent_dwell_log_lr(e) for e in record.events if e.kind == "vent"), default=0.0)
    body = max((_body_proximity_log_lr(e) for e in record.events if e.kind == "near_body"), default=0.0)
    follow = max((_follow_log_lr(e, belief) for e in record.events if e.kind == "proximity"), default=0.0)
    tail = max((_tailing_self_log_lr(e) for e in record.events if e.kind == "tailing_self"), default=0.0)
    return witnessed + vent + body + follow + tail


# --- combine into the posterior ---------------------------------------------


def _recompute(belief: Belief) -> None:
    """Rebuild ``belief.suspicion`` and ``belief.believed_imposters`` from the current
    roster. For each live, non-teammate, non-self player: take the fitted log-odds when
    weights are loaded (witnessed catches floored to prior + ``WITNESSED_LOG_LR``), else
    prior + the hand-model evidence sum; ``sigmoid`` to a posterior. ``believed_imposters``
    is every color at P ≥ ``FLEE_PROBABILITY`` (the near-certain set — a derived readout, not a mode gate)."""
    prior_logit = _logit(_prior_imposter_p(belief))
    suspicion: dict[str, float] = {}
    believed: set[str] = set()

    for color, record in belief.roster.items():
        if record.life_status == "dead":
            continue  # the dead are no threat
        if color in belief.teammate_colors or color == belief.self_color:
            continue  # never score a known teammate, or *ourself* (no-op for a crewmate)
        if _WEIGHTS is not None:
            logit = _fitted_log_odds(belief, record)
            # A witnessed kill/vent stays DEFINITIONAL near-certainty (we saw it),
            # never down-weighted by the fitted model (design: witnessed is not fit).
            if any(e.kind in ("kill", "vent_use") for e in record.events):
                logit = max(logit, prior_logit + WITNESSED_LOG_LR)
        else:
            logit = prior_logit + _evidence_log_lr(belief, record)
        p = _sigmoid(logit)
        suspicion[color] = p
        if p >= FLEE_PROBABILITY:
            believed.add(color)

    belief.suspicion = suspicion
    belief.believed_imposters = believed


def witnessed_imposters(belief: Belief) -> set[str]:
    """Colors we directly caught killing or venting (a ``kill``/``vent_use`` event on
    their log). These already drive P ≈ 1 via ``WITNESSED_LOG_LR``; this exposes the
    set for tracing/forensics — there is no separate ``confirmed`` state to maintain."""

    return {
        color
        for color, record in belief.roster.items()
        if any(e.kind in ("kill", "vent_use") for e in record.events)
    }


def active_tail_suspect(belief: Belief) -> str | None:
    """The player currently **tailing us** whom we're suspicious enough to accuse, or
    `None`. The most-suspicious color with an *ongoing* `tailing_self` interval and
    P ≥ `ACCUSE_THRESHOLD`. Drives Accuse mode: stop, go slam the meeting button, then
    accuse them. Crewmate-only by construction (suspicion is empty for other roles)."""

    best: tuple[str, float] | None = None
    for color, p in belief.suspicion.items():
        if p < ACCUSE_THRESHOLD or color == belief.self_color:
            continue
        record = belief.roster.get(color)
        if record is None or record.life_status == "dead":
            continue
        if not _is_actively_tailing(record, belief.last_tick):
            continue
        if best is None or p > best[1]:
            best = (color, p)
    return best[0] if best is not None else None


def _is_actively_tailing(record: PlayerRecord, tick: int) -> bool:
    """True if this player's most recent `tailing_self` interval is still live (extended
    within `ACCUSE_TAIL_RECENCY_TICKS`)."""

    for event in reversed(record.events):
        if event.kind == "tailing_self":
            return tick - event.end_tick <= ACCUSE_TAIL_RECENCY_TICKS
    return False


def top_suspect(belief: Belief) -> str | None:
    """The live player to vote out — the **clear leading suspect**, or `None` (skip)
    when the posterior is flat. Used by Attend Meeting (§7.1).

    Two ways to clear the bar: near-certainty on its own (P ≥ `VOTE_PROBABILITY` — a
    witnessed catch or a saturated tail), or a clear lead short of that (P over
    `VOTE_LEAD_MIN_P` *and* ahead of the runner-up by `VOTE_LEAD_MARGIN`). A flat field
    — everyone near the prior — names no one, so we skip rather than eject at random.

    **We never return our own color** — a hard guard so the agent can never vote itself
    out, independent of how suspicion is computed.
    """

    ranked = [(c, p) for c, p in belief.suspicion.items() if c != belief.self_color]
    if not ranked:
        return None
    ranked.sort(key=lambda kv: kv[1], reverse=True)
    color, p = ranked[0]
    if _WEIGHTS is not None and belief.self_role != "imposter":
        # Fitted model, CREWMATE vote: calibrated near-certainty ONLY. The clear-
        # leader rule is deliberately absent here — on the league data it was the
        # mis-vote engine (58% of player-votes hit crew), and every crew ejection is
        # a parity gift; the held-out decision sim puts this bar at ~100% imposter
        # precision. An IMPOSTER deflecting keeps the legacy clear-leader logic
        # below: engineering plausible mis-ejections is its job, not a risk.
        return color if p >= WEIGHTS_VOTE_PROBABILITY else None
    if p >= VOTE_PROBABILITY:
        return color  # near-certain on its own
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if p >= VOTE_LEAD_MIN_P and (p - runner_up) >= VOTE_LEAD_MARGIN:
        return color  # a clear leader over a non-flat field
    return None


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def _sigmoid(logit: float) -> float:
    logit = max(-700.0, min(700.0, logit))  # keep exp finite
    return 1.0 / (1.0 + math.exp(-logit))
