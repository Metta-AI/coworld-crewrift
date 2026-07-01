# Suspicion — the Bayesian P(imposter) model

crewborg keeps, for every player it could vote out, a posterior probability that
they are an imposter. This document is the deep reference for that model: the
prior, the log-odds update, the per-event and social evidence, the two scoring
paths (the fitted production posterior and the legacy hand model), the outputs and
thresholds, and how the fitted weights are learned.

It complements the orientation in [`README.md`](../README.md), the structural spec
in [`design.md`](../design.md) §10.1, and the docstrings in
`strategy/suspicion.py`. The behaviours that *consume* the posterior live in their
own references: the crewmate vote in [`crewmate-play.md`](./crewmate-play.md), and
imposter deflection in [`imposter-play.md`](./imposter-play.md) and
[`meetings.md`](./meetings.md). Evidence collection upstream is covered by
[`agent-tracking.md`](./agent-tracking.md) and
[`perception-and-belief.md`](./perception-and-belief.md); tracing output by
[`trace-logs.md`](./trace-logs.md).

---

## 1. What the model computes

For each player, `strategy/suspicion.py:update_suspicion` writes a posterior into
the belief state:

```
belief.suspicion[color] = P(imposter | everything this agent has observed)  ∈ [0, 1]
```

It is a real probability with units, so each threshold means something concrete —
"call a meeting on a tail once we are at least 60% sure" — rather than an arbitrary
score.

The posterior is maintained for **both live roles**, over the players the agent
could vote as imposters:

- As a **crewmate**, it scores every other player and is a genuine belief.
- As an **imposter**, it scores only **non-teammates** — the same number, read as
  "how suspicious this crewmate *looks* on the shared evidence," used to pick the
  most-citable deflection target at a meeting.
- A **ghost** (`not belief.self_alive`) holds no suspicion: `belief.suspicion`
  and `belief.believed_imposters` are cleared.

### Outputs

| Output | Produced by | Meaning |
|---|---|---|
| `belief.suspicion[color]` | `_recompute` | per-player posterior P(imposter) |
| `belief.believed_imposters` | `_recompute` | the set of **alive** colors at or above `FLEE_PROBABILITY` (0.9) — the near-certain set, exposed as belief state (e.g. it seeds the meeting vote) |
| `top_suspect(belief)` | `strategy/suspicion.py:top_suspect` | the live player to vote out at a meeting, or `None` to skip a flat field |
| `active_tail_suspect(belief)` | `strategy/suspicion.py:active_tail_suspect` | the most-suspicious player *currently tailing us* over `ACCUSE_THRESHOLD`, which gates Accuse mode |
| `witnessed_imposters(belief)` | `strategy/suspicion.py:witnessed_imposters` | colors directly caught killing or venting; for tracing/forensics (already drive P ≈ 1) |

### Self-exclusion — never suspect, accuse, or vote self

The agent's own sprite is the camera centre and leaks into the roster as if it were
another player. The model excludes it at four layers (defense-in-depth against a bug
that otherwise self-ejects the agent):

- `_recompute` skips `belief.self_color` and any `belief.teammate_colors`.
- `strategy/event_log.py:update_event_log` never logs a `tailing_self` event against
  `self_color` (the agent is trivially always at its own spot).
- `top_suspect` and `active_tail_suspect` hard-guard `self_color` regardless of how
  suspicion was computed.
- Attend Meeting refuses a self-targeted ballot.

---

## 2. The prior — combinatorics

With `P` players total and `K` imposters, a crewmate knows all `K` imposters are
among the other `P − 1` players. By symmetry each other player's marginal prior is:

```
prior = K / (P − 1)
```

- `P` = `belief.total_player_count`.
- `K` = `strategy/suspicion.py:_imposter_count`: `belief.imposter_count` if set, else
  the game's auto-imposter formula `(P − 3) // 2` (0 below 5 players), clamped to
  `[0, P − 1]`.
- `_prior_imposter_p` clamps the result to `[PRIOR_MIN, PRIOR_MAX] = [1e-3, 0.99]`
  so its log-odds stays finite.

The prior is the base of the legacy hand model and the anchor for the witnessed
floor (§5). The **fitted model replaces it with a learned intercept** (§4) — the
combinatorial prior is not added on the fitted path except through the witnessed
floor.

---

## 3. The log-odds update

Evidence is combined in log-odds, where independent evidence is additive (Bayes'
rule):

```
logit(P) = base + Σ_e logLR(e)
P        = sigmoid(logit(P))
```

with `logit(p) = ln(p / (1 − p))` and `sigmoid(x) = 1 / (1 + e^−x)` (both clamped to
`±700` to keep `exp` finite). A `logLR > 0` raises suspicion, `= 0` is neutral,
`< 0` lowers it below the base.

Because a player's role is a **fixed latent variable**, evidence does not decay over
wall-clock time: observing someone vent at minute 1 is permanent evidence about
their unchanging role. There is no time-decay term. (This is distinct from the
*within-event* shape of a cue — e.g. body-proximity strength varying with dwell
duration.)

`_recompute` runs this for each live, non-teammate, non-self player, choosing the
base + evidence terms by scoring path:

```
                       ┌─ fitted weights loaded? ──┐
   prior K/(P−1) ──────┤                           │
                       │  yes → logit = intercept + Σ w·x   (§4)
                       │        (witnessed → floored to prior + WITNESSED_LOG_LR)
                       │
                       │  no  → logit = logit(prior) + Σ max-per-type logLR   (§5)
                       └───────────────────────────┘
                                  │
                            P = sigmoid(logit)
```

---

## 4. The fitted model (production posterior)

When the vendored weights file `data/suspicion_weights.json` loads, the posterior is
the **fitted model**: `strategy/suspicion.py:_fitted_log_odds` computes
`logit = intercept + Σ w·x` over the runtime feature vector. This is the production
path. The hand model of §5 is the fallback.

### 4.1 Loading and the ops toggle

`_load_weights` loads the file at import (`_WEIGHTS`), validating
`schema == "crewborg-suspicion-weights/v1"` and the presence of `coefficients`. It
never raises — a missing asset or bad JSON yields `None`, which falls back to the
hand model.

| Env var | Effect |
|---|---|
| `CREWBORG_SUSPICION_WEIGHTS=0` | force the legacy hand model (§5) |
| `CREWBORG_SUSPICION_WEIGHTS=<path>` | load weights from `<path>` instead of the vendored asset |
| `CREWBORG_WEIGHTS_VOTE_P=<float>` | override the fitted crewmate vote bar `WEIGHTS_VOTE_PROBABILITY` (default 0.9) |

`set_weights(weights)` is a test/ops hook to pin the scoring path.

### 4.2 The feature vector

`_fitted_features(belief, record)` extracts one feature dict per suspect from that
player's `PlayerRecord.events` (the event log, §6) and its social counters (§6.1).
Durative features are expressed in **samples** — runtime tick durations divided by
`sample_unit_ticks` (24) so they land in the same unit as the offline expander's
snapshots. **Instance summing with per-context dedup** is built into the extractor:
bodies dedupe by body color, follows sum across a victim's qualifying intervals,
vent visits count per dwell interval.

| Feature | Source event / counter | Sign | Meaning |
|---|---|---|---|
| `witnessed_kills` | count of `kill` / `vent_use` point events | **+ (floor)** | a direct catch; also forced to the witnessed floor (§4.4), so it is never down-weighted |
| `near_body_bodies` | distinct body colors the suspect was logged near | + | proximity to corpses |
| `follow_death_samples` | `proximity` ticks where the target later died within `FOLLOW_DEATH_WINDOW_TICKS` (72) | + | shadowing a victim up to their death — the strongest graded incriminating cue |
| `tail_obs_samples` | total `tailing_self` ticks | mixed | how long the suspect shadowed *us* (cumulative) |
| `tail_obs_max_run` | longest single `tailing_self` interval | − | longest unbroken tail (weak on the current fit) |
| `vent_visits` | `vent` dwells longer than `VENT_CROSS_TICKS` (3) | + | standing on a vent past a pass-through |
| `copresence_killrange_samples` | `tailing_self` ticks within 28 px (`COPRESENCE_DIST_SQ` = 28²) | + | sustained close co-presence with the agent |
| `task_site_dwell_samples` | `task` dwell ticks | mixed | short dwell mildly exculpatory; long bare dwell reads Pretend-like (positive) |
| `observed_samples` | `record.seen_ticks` (exposure) | + | how long the agent watched the suspect — the **exposure** denominator weighing evidence against opportunity |
| `tasks_completed_watched` | watched real-task completion (`social_evidence`) | **−− (large)** | the strongest exculpation; imposters cannot complete tasks |
| `accusations_made` | chat accusations the suspect made | + | accusing a lot leans guilty on this field |
| `times_accused` | chat accusations against the suspect | − | being accused leans innocent on this field |
| `times_defended` | chat defenses of the suspect | ~0 | currently carries no weight |
| `votes_cast` / `votes_skipped` | attributed meeting votes | − | voting/skipping behaviour |
| `voted_against_observer` | the suspect voted against the agent | + | |
| `vote_agreement_with_observer` | the suspect voted with the agent | + | |
| `reported_bodies` | the suspect called a body-report meeting | − | reporters lean innocent |
| `button_calls_made` | the suspect pressed the emergency button | − | callers lean innocent |

The exact coefficients live in `data/suspicion_weights.json` and are refit
periodically; the file is the source of truth for the current numbers. The signs
and relative magnitudes above describe the production fit's shape, not fixed
constants.

### 4.3 Binning and clipping

`_fitted_log_odds` transforms features two ways, mirroring the offline fit:

- **Binned features** (those listed in the file's `bin_spec`: the duration/exposure
  features `follow_death_samples`, `tail_obs_samples`, `tail_obs_max_run`,
  `copresence_killrange_samples`, `task_site_dwell_samples`, `observed_samples`) are
  bucketed by the bin edges into an indicator, and that bucket's coefficient
  (`<name>__<lo>to<hi>` / `<name>__gt<lo>`) is added. This recovers a piecewise
  *shape* over duration/distance — e.g. body or task dwell that is exculpatory when
  short and incriminating when long — inside one linear model.
- **Linear features** (the rest) contribute `coefficient · min(value, linear_clip)`,
  where `linear_clip` (default 5) caps the count so one runaway feature can't
  dominate.

The `intercept` absorbs `logit(prior)` — the fitted model carries its own base rate
rather than the combinatorial prior.

### 4.4 The witnessed floor (definitional, both paths)

A witnessed kill or vent is a definitional near-certainty (the agent *saw* it), not
a fitted quantity. On the fitted path, after computing `intercept + Σ w·x`,
`_recompute` floors any suspect with a `kill`/`vent_use` event to at least
`prior_logit + WITNESSED_LOG_LR` (`WITNESSED_LOG_LR = ln(1e6)`, P ≈ 1). The hand
model adds the same `WITNESSED_LOG_LR` directly. So a catch is overwhelming on
either path regardless of the field.

---

## 5. The legacy hand model (fallback)

When no weights load (or `CREWBORG_SUSPICION_WEIGHTS=0`), the posterior is the
hand-written model: `logit = logit(prior) + _evidence_log_lr(belief, record)`. It is
**positive-evidence-only** (the prior is the floor — no exculpation) and aggregates
**`max` per evidence type** (`_evidence_log_lr`), so a player's single most-suspicious
instance of each type counts and an unbounded event log can't inflate the posterior.

### 5.1 The per-event evidence table

Each graded cue is a closed-form `_*_log_lr` function of one event's features, not a
flat ratio — because the relationship between a cue's features and guilt is not flat
(a skilled imposter *flees* a kill rather than dwelling). The constants are module
globals in `strategy/suspicion.py`.

| Cue | Function | Form (log-LR) | Sign vs. dwell | Meaning |
|---|---|---|---|---|
| witnessed kill / vent | `_evidence_log_lr` | `WITNESSED_LOG_LR = ln(1e6)` if any `kill`/`vent_use` event | constant | definitional catch — P ≈ 1, the certainty **floor** |
| vent dwell | `_vent_dwell_log_lr` | `VENT_DWELL_LOG_LR (ln 8)` if `duration > VENT_CROSS_TICKS (3)`, else 0 | ~flat | weak: a real venter teleports (owned by the transition detector); merely standing on a vent past crossing it |
| body proximity | `_body_proximity_log_lr` | 0 if `min_dist > BODY_NEAR_DIST (16 px)`, else `BODY_NEAR_LOG_LR (ln 3) · max(0, 1 − duration/BODY_FADE_TICKS (48))` | **decreasing** | brief presence is the only window on a fleeing killer; a long camp is innocent reporter behaviour, fading to 0 by ~2 s |
| follow-to-death | `_follow_log_lr` | 0 unless target now dead and `\|death_seen − end\| ≤ FOLLOW_DEATH_WINDOW_TICKS (72)`, else `FOLLOW_LOG_LR (ln 6) · min(1, duration/FOLLOW_FULL_TICKS (48))` | **increasing** | sustained shadowing of a now-dead victim, saturating at full by ~2 s |
| being tailed | `_tailing_self_log_lr` | logistic: `TAIL_SELF_LOG_LR_MAX (ln 6.5) / (1 + exp(−TAIL_SELF_STEEPNESS (0.2)·(duration − TAIL_SELF_MIDPOINT_TICKS (30))))` | **increasing** | someone shadowing *us* over time; a brief brush ≈ 0, half at 30 ticks, saturates at a **moderate** P ≈ 0.72. Needs **no death**. Crossing `ACCUSE_THRESHOLD` (~34 ticks) triggers Accuse |

The being-tailed cue is read off the agent's own position (which it knows perfectly)
and needs no death, so it is a high-quality live signal — but it is deliberately
capped *below* near-certainty because crew routinely move together.

### 5.2 Deliberately neutral on this path

Brief proximity to a *living* player, distant near-body, and bare `task` dwell all
map to a `0` log-LR in the hand model (crew constantly pass within kill range; fakers
also dwell at task sites). These events are still recorded in the event log and are
available to the fitted model and the opt-in meeting LLM.

---

## 6. What feeds the model each tick

The inner loop in `__init__.py:build_runtime` runs four knowledge-layer steps in a
fixed order; suspicion is **last** and consumes the rest:

```
update_belief → update_event_log → update_social_evidence → update_suspicion
  (perception)   (durative cues)    (public counters)        (posterior)
```

### 6.1 The event log (`strategy/event_log.py`)

`update_event_log` folds each tick's observation of every visible player into
**durative intervals** on `PlayerRecord.events`. A predicate true across consecutive
observed ticks extends one interval (bridging gaps up to `EVENT_MERGE_GRACE_TICKS`);
a break opens a fresh one, so a duration is honestly "observed for ≥ this long." It
records these `PlayerEvent` kinds:

- `room` / `task` / `vent` — which room, task-site, or vent rect the player sat in.
- `near_body` — within `NEAR_BODY_RADIUS_SQ` (48 px) of a discovered body (with the
  body color and min distance).
- `tailing_self` — sustained within `TAIL_SELF_RADIUS_SQ` (64 px, wider than kill
  range so a forming tail is caught) of the agent; never logged for `self_color`.
- `proximity` — within kill range of another player (`KILL_RANGE_SQ` = 400, i.e. 20 px).

It also maintains `record.seen_ticks` (the exposure feature).

### 6.2 Social evidence (`strategy/social_evidence.py`)

`update_social_evidence` maintains the cumulative public counters the fitted model's
non-perceptual features read (the hand model ignores them). Counters live on
`PlayerRecord`, are banked exactly once per real event, and never reset
mid-episode:

- **Chat stances** — `_count_chat_stances` reduces each meeting chat line to
  `(speaker, stance, target)` via templated-chat regexes (`ACCUSE_HINT` /
  `DEFEND_HINT`), bumping `accusations_made` on the speaker and
  `times_accused` / `times_defended` on the target. Unparseable lines are dropped.
- **Vote tallies** — `_track_meeting_votes` stages the voting UI's attributed dots
  during a meeting and commits them once at meeting end into `votes_cast`,
  `votes_skipped`, `voted_against_me`, and `vote_agreed_with_me`.
- **Meeting caller** — `_bank_meeting_caller` credits the MeetingCall interstitial's
  caller into `reported_bodies` (body report) or `button_calls_made` (button).
- **Watched real-task completion** — `_detect_watched_completions` credits
  `tasks_completed_watched` (the strongest exculpation) only when the global
  `crew_tasks_remaining` HUD counter drops by exactly one while **exactly one**
  visible living player is finishing a near-full task dwell (≥ `WATCHED_DWELL_MIN_TICKS`,
  56). A fake task hold never decrements the counter, so it cannot trigger this.

### 6.3 The witnessed-catch detectors

`update_suspicion` runs two frame-to-frame transition detectors before recomputing,
each latching a point event on the perpetrator's log via `_log_witnessed`:

- `_detect_witnessed_kill` — a body present this frame whose owner was alive last
  frame, with **exactly one** non-teammate inside `KILL_RANGE_SQ` of that owner a
  frame ago, is an unambiguous `kill`. Zero or multiple neighbours ⇒ no attribution.
  A `kill` is deduped per victim so a persisting body can't re-log.
- `_detect_witnessed_vent` — per vent rect, either **emergence** (the vent + walk
  margin was in line of sight and empty last frame, occupied now) or **submersion**
  (a player was in the vent last frame, the vent is still in sight, the player has
  vanished) latches a `vent_use`. Line of sight is checked with `rect_visible` so an
  off-screen vent never produces a false catch.

These catches flow through the one posterior (the witnessed floor, §4.4) rather than
a separate "confirmed" set.

---

## 7. Thresholds and consumers

| Knob | Value | Effect |
|---|---|---|
| `WEIGHTS_VOTE_PROBABILITY` | 0.9 (env `CREWBORG_WEIGHTS_VOTE_P`) | the **fitted crewmate** vote bar: vote only at calibrated near-certainty; **no clear-leader rule** |
| `VOTE_PROBABILITY` | 0.8 | self-standing vote bar on the **legacy / imposter** path — near-certainty regardless of the field |
| `VOTE_LEAD_MIN_P` / `VOTE_LEAD_MARGIN` | 0.5 / 0.2 | the *clear leading suspect* rule (legacy / imposter path): vote the top suspect when P ≥ 0.5 and it leads the runner-up by ≥ 0.2; a flat field names no one |
| `ACCUSE_THRESHOLD` | 0.6 | an **active tail** at/above this triggers Accuse (drop tasks, go call a meeting) |
| `ACCUSE_TAIL_RECENCY_TICKS` | 6 | how recently a `tailing_self` interval must have been extended to count as *active* (robust to brief occlusion) |
| `FLEE_PROBABILITY` | 0.9 | the near-certain bar defining `believed_imposters` |
| `PRIOR_MIN` / `PRIOR_MAX` | 1e-3 / 0.99 | clamp the prior so log-odds is finite |
| `WITNESSED_LOG_LR` | ln(1e6) | the definitional catch strength / floor |

### `top_suspect` — the vote target

`top_suspect` ranks live non-self players by posterior and returns one to vote, or
`None` to skip:

- **Fitted model, crewmate** (`_WEIGHTS` loaded and `self_role != "imposter"`):
  return the leader only if `P ≥ WEIGHTS_VOTE_PROBABILITY` (0.9). The clear-leader
  rule is deliberately absent — on league data it was the mis-vote engine, and every
  crew ejection is a parity gift, so crewborg votes only at calibrated near-certainty.
- **Legacy model, or any imposter**: return the leader if `P ≥ VOTE_PROBABILITY`
  (0.8), else if it is a clear leader (`P ≥ VOTE_LEAD_MIN_P` and ahead of the
  runner-up by `VOTE_LEAD_MARGIN`). An imposter keeps the clear-leader logic on
  purpose: engineering a plausible mis-ejection is its job, not a risk.

How the vote is then cast at a meeting is in [`crewmate-play.md`](./crewmate-play.md)
and [`meetings.md`](./meetings.md); imposter deflection that reuses this posterior is
in [`imposter-play.md`](./imposter-play.md).

### `active_tail_suspect` — the Accuse trigger

`active_tail_suspect` returns the most-suspicious live player with P ≥
`ACCUSE_THRESHOLD` whose most recent `tailing_self` interval is still live (extended
within `ACCUSE_TAIL_RECENCY_TICKS`). It gates Accuse mode (`modes/accuse.py`): stop
tasking, walk to the emergency button, call a meeting, and accuse. Crewmate-only by
construction (suspicion is empty for other live roles).

---

## 8. How the weights are learned

The agent never learns at runtime — scoring is a fixed dot product over the loaded
weights. The weights are fit **offline** and vendored into the image as
`data/suspicion_weights.json`. The runtime's only job is to load that file; the
fitting pipeline lives in a separate optimizer toolkit.

### Method

The model is **L1-regularized logistic regression** over the per-(observer, suspect)
evidence features. This is exactly "learn the evidence weights": the coefficients
*are* the additive log-LRs, the intercept absorbs `logit(prior)`, and the in-game
architecture (sum log-odds → sigmoid) is unchanged — the agent simply loads
different numbers. L1 prunes the candidate feature catalogue down to evidence that
actually improves held-out prediction; a feature with no signal (e.g.
`times_defended` at 0.0) drops out.

### Why it can be fit from replays

A league replay records every player's true role, and the replay expander computes
**exact rendered-view visibility** for every (observer, target) pair every tick. So
"what did this crewmate actually see" is *computed*, not modelled: an observer's
evidence is the global event stream clipped to their visibility intervals — the same
clipping the runtime perception performs. Offline and runtime features are therefore
the **same quantities**, which is what lets the fitted coefficients drop straight
into the runtime.

### Feature design

- **Unit of analysis**: one (observer, suspect, decision-point) row, labelled with
  the suspect's ground-truth role, snapshotted at each meeting's vote moment and at
  mid-play ticks (the Accuse decision).
- **Instance summing with per-context dedup** (§4.2) rather than the hand model's
  per-type `max` — independent observations multiply LRs (add in log-odds), and
  per-context dedup guards the dependence failure of one long behaviour re-logged as
  many intervals.
- **Exculpatory evidence** falls out as negative coefficients (e.g.
  `tasks_completed_watched`, `reported_bodies`, `button_calls_made`), so
  innocent-looking players sink below the base rate and the field separates honestly
  — unlike the positive-only hand model.
- **Shapes without nonlinearity**: graded duration/distance features are binned into
  a few buckets, one coefficient each (the file's `bin_spec`), recovering the
  per-feature shape (e.g. decreasing body-proximity, exculpatory-then-incriminating
  task dwell) inside one linear model.
- **Exposure** (`observed_samples`) is a feature, weighing evidence against
  opportunity — a cue seen over a long watch means more than the same cue glimpsed
  briefly.
- **Calibration is the point**, not just ranking, because the vote thresholds are
  probabilities — the fit is validated with game-grouped cross-validation (never
  split by row, to avoid leakage) and decision-level simulation of held-out
  meetings, not AUC alone.

Each candidate feature must be computable from runtime perception too — that parity
constraint is part of what makes a feature admissible, and is what keeps the offline
extractor and `_fitted_features` in lockstep.
