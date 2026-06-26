# Suspicion — Bayesian P(imposter)

**Status:** living document. This is the canonical, durable home for crewborg's
suspicion model and especially its **per-event log-LR functions** (§3) — the place
where we record, justify, and improve the evidence weights as we learn them from
games.

- **Code:** [`strategy/suspicion.py`](../../strategy/suspicion.py) — the `_*_log_lr`
  functions + `WITNESSED_LOG_LR`; the update is `update_suspicion(belief)`.
- **Spec summary:** [`design.md` §10.1](../../design.md).
- **Inputs:** the perception tape (§5.1) and per-player event log (§5.2), both in
  `design.md`.

If a value here and in the code ever disagree, **the code is what runs** — but **a
change should land in both**, with the rationale recorded here.

---

## 1. What we compute

For a **crewmate** observer, for every *other* player, the posterior probability
they are an imposter:

```
belief.suspicion[color] = P(imposter | everything we have observed)   ∈ [0, 1]
```

`believed_imposters` (which gates the Flee mode) is every **alive** player whose
posterior is at or above `FLEE_PROBABILITY` (0.9). Suspicion is **crewmate-only**:
an imposter already knows who its teammates are (it accrues no suspicion and never
flees a crewmate), and a ghost does not flee.

This is a real probability with units, so the threshold means something concrete —
"flee only when ≥90% sure" — rather than an arbitrary score.

---

## 2. The model

### 2.1 Prior — combinatorics, with budget redistribution

With `P` players total and `K` imposters, a crewmate knows all `K` imposters are
among the other `P − 1` players. The prior **redistributes the imposter budget**
as the game reveals information: every confirmed imposter (alive or dead) is
attributed budget, and dead players are no longer candidates. An *unconfirmed*
player's marginal prior is the hidden budget over the remaining candidates:

```
K_hidden     = max(0, K − |confirmed_imposters|)
n_candidates = max(1, (P − 1) − dead_others − confirmed_alive)
prior        = K_hidden / n_candidates
```

So with `K = 2`, catching one imposter roughly halves everyone else's prior
instead of leaving it stale; with both caught, everyone else drops to the floor.

- `P` = `belief.total_player_count` (estimated early from distinct colors seen;
  authoritative once the meeting census arrives, §4.3).
- `K` = `belief.imposter_count` if set, else **derived** from the player count via
  Crewrift's own auto-imposter formula `(P − 3) // 2` (`sim.nim` `ratioImposterCount`
  / `effectiveImposterCount`; default `autoImposterCount = true`). Override
  `belief.imposter_count` if a game is known to use a fixed count.

The prior is clamped to `[PRIOR_MIN, PRIOR_MAX]` = `[1e-3, 0.99]` so its log-odds
stays finite.

### 2.2 Update — log-odds Bayes

Each piece of evidence is incorporated by a **likelihood ratio**

```
LR_e = P(observe e | player is imposter) / P(observe e | player is crewmate)
```

In log-odds form, evidence is additive (this is just Bayes' rule for independent
evidence):

```
logit(P) = logit(prior) + Σ_e log(LR_e)
P        = sigmoid(logit(P))
```

where `logit(p) = ln(p / (1 − p))` and `sigmoid(x) = 1 / (1 + e^−x)`.

- `logLR > 0` ⇒ evidence raises suspicion; `= 0` ⇒ neutral; `< 0` ⇒ lowers it (we
  have no `< 0` evidence yet — see §5, positive-evidence-only).
- Each graded cue's `logLR` is a **function of the event's features** (duration,
  distance), not a flat constant — see §3. We aggregate **per evidence type with
  `max`** (a player's most-suspicious instance of that type), so repeated logging of
  the same behaviour can't inflate the posterior and an unbounded event log (§5.2)
  is safe.
- Because a player's role is a **fixed latent variable**, evidence does not decay in
  time: observing someone vent at minute 1 is permanent evidence about their
  (unchanging) role. There is no time-decay term, by design. (Note this is distinct
  from the *body-proximity* function decreasing with *dwell duration* — that's about
  the within-event shape, not about forgetting over wall-clock time.)

### 2.3 Worked example

8 players ⇒ `K = (8 − 3) // 2 = 2`, so `prior = 2 / 7 ≈ 0.286`, `logit ≈ −0.916`.

| Evidence observed | logLR | logit | P(imposter) | Flee (≥0.9)? |
|---|---|---|---|---|
| none (the prior) | — | −0.916 | 0.286 | no |
| brief `body proximity` (LR≈3) | +1.10 | 0.18 | 0.545 | no |
| `vent dwell` (LR 8) | +2.08 | 1.16 | 0.762 | no |
| `vent dwell` + `follow-to-death` (LR 6) | +2.08 +1.79 | 2.96 | 0.950 | **yes** |
| `witnessed vent` (LR 1e6) | +13.8 | 12.9 | 0.99999 | **yes** |

So a single graded cue is suspicious but not flee-worthy; corroboration crosses the
bar; a witnessed catch is effectively certain regardless of the prior.

---

## 3. The evidence catalogue + per-event log-LR functions

This is the load-bearing part of the model. **The functions and their constants are
the learnable surface** — hand-written initial cuts (no games analysed yet), meant
to be (re)fit from replays (§6). Record every change in the provenance log (§7).

### 3.1 Why functions, not flat ratios

A flat LR per evidence type is wrong because the relationship between an event's
*features* and guilt is not flat — and is sometimes **inverted**. A skilled imposter
**flees** a kill instantly; they do not loiter. So:

- A long dwell next to a body is **reporter** behaviour (innocent); a *brief*
  presence is the only window on a fleeing killer. ⇒ body-proximity log-LR should
  **decrease** with dwell.
- Following someone for a sustained stretch right up to their death is stalking. ⇒
  follow log-LR should **increase** with dwell.
- Standing on a vent is weak either way (a real venter *teleports* — caught by the
  near-certain transition detector). ⇒ ~flat past a pure pass-through.

So each graded cue gets a small **`_*_log_lr(event[, belief]) -> float`** function
(`suspicion.py`). The form + its constants are the parameterization; there is no
learning machinery yet (and deliberately nothing neural). A type's contribution is
the **max** over that player's events of that type (§2.2).

### 3.2 Near-certain (definitional, constant)

| Evidence | Source | Detected when | log-LR |
|---|---|---|---|
| witnessed kill | tape transition (§5.1) | victim alive last frame, body now, exactly **one** other player within `KILL_RANGE_SQ` of the victim last frame | `WITNESSED_LOG_LR` = ln 1e6 |
| witnessed vent | tape transition (§5.1) | *emergence* (vent + `VENT_WALK_MARGIN` in line of sight & clear last frame, occupied now) or *submersion* (player in the vent last frame, gone while it stays in sight); LoS via the `shadow` mask (§4.4) | `WITNESSED_LOG_LR` |

These are definitional (we *saw* it) and not learned.

### 3.3 Graded functions (over the event log, §5.2)

| Function | Event | Form (log-LR) | Constants | Shape / rationale |
|---|---|---|---|---|
| `_vent_dwell_log_lr` | `vent` | `VENT_DWELL_LOG_LR` if `duration > VENT_CROSS_TICKS` else `0` | `VENT_CROSS_TICKS=3`, `VENT_DWELL_LOG_LR=ln 8` | ~flat once it's more than crossing the tile; weak (the transition detector owns real venting). |
| `_body_proximity_log_lr` | `near_body` | `0` if `min_dist > BODY_NEAR_DIST`, else `BODY_NEAR_LOG_LR · max(0, 1 − duration/BODY_FADE_TICKS)` | `BODY_NEAR_DIST=16 px`, `BODY_NEAR_LOG_LR=ln 3`, `BODY_FADE_TICKS=48` | **decreasing** in dwell: full at first sight, fades to 0 by ~2 s (a long camp ⇒ reporter ⇒ neutral). |
| `_follow_log_lr` | `proximity` | `0` unless target now dead and `\|death_seen − end\| ≤ FOLLOW_DEATH_WINDOW_TICKS`, else `FOLLOW_LOG_LR · min(1, duration/FOLLOW_FULL_TICKS)` | `FOLLOW_FULL_TICKS=48`, `FOLLOW_DEATH_WINDOW_TICKS=72`, `FOLLOW_LOG_LR=ln 6` | **increasing** (saturating) in dwell: longer shadowing of a now-dead victim ⇒ more. |

`VENT_WALK_MARGIN` (3 px, one tick of walking) is a perception guard for the
vent-emergence detector, not a scoring parameter.

### 3.4 How to parameterize / change a function

Each function is plain Python over the event's fields (`duration_ticks`, `min_dist`,
`target_color`) plus `belief` (for the target's life status). To re-shape a cue:
edit its constants (magnitude `*_LOG_LR`, scale `*_TICKS`, distance gate) or its
closed form. Keep three things aligned: the **function**, this **table**, and the
**provenance log** (§7). Tests assert *relational* behaviour (evidence raises P; one
cue stays below the flee bar; corroboration crosses it; body-proximity brief > long),
so they survive re-tuning unless the qualitative shape changes.

### 3.5 Social cues (who-sus'd-who; `_social_log_lr`)

Beyond the in-world event log, the episode-persistent **accusation graph**
(`belief.accusations`, parsed from meeting chat by
[`strategy/meeting/social.py`](../../strategy/meeting/social.py)) and **meeting
vote history** (`belief.meeting_history`) carry evidence. Each cue is a *boolean
per player* (it contributes at most once — naturally bounded like the
max-aggregated cues):

| Cue | Condition | log-LR | Rationale |
|---|---|---|---|
| defended by confirmed imposter | a `defend` line from a `confirmed_imposters` speaker | `SOCIAL_DEFENDED_BY_CONFIRMED_LOG_LR` = ln 4 | imposter teammates defend each other |
| accused by confirmed imposter | an `accuse` line from a confirmed speaker | `SOCIAL_ACCUSED_BY_CONFIRMED_LOG_LR` = −ln 2 | imposters scapegoat crew — their accusations are inverted evidence |
| crowd accusation | accused **with evidence wording** (`Accusation.has_evidence`) by ≥ `SOCIAL_CROWD_MIN_ACCUSERS` (2) distinct ordinary speakers (self and confirmed imposters excluded) | `SOCIAL_CROWD_ACCUSED_LOG_LR` = ln 1.5 | independent corroboration; weak — crowds bandwagon. Bare assertions never count (see the plain-sus rows) |
| plain-sus **target** | named by an `accuse` line with **no** evidence wording, while no evidence-backed accusation names them | `PLAIN_SUS_TARGET_LOG_LR` = −ln 3 | the plain-sus disinfo tell: vs truecrew:v14, 0/185 bare `"<color> sus"` lines named a real imposter (2026-06-11 eval) — the format marks the *named* color as likely framed crew |
| plain-sus **speaker** | uttered a bare no-evidence `accuse` line about another player | `PLAIN_SUS_SPEAKER_LOG_LR` = ln 2 | the same engine marks the speaker as likely steering the meeting (imposters frame crew this way; 11/16 ejections followed one) |
| voted for a confirmed imposter | any `meeting_history` record where this player's vote landed on a confirmed imposter | `SOCIAL_VOTED_FOR_CONFIRMED_LOG_LR` = −ln 2 | crew-like behaviour (the first exculpatory terms in the model) |

Our own accusations are excluded from the crowd count (they derive from this very
posterior — counting them would be feedback), and a confirmed imposter's
accusations are handled by the inversion row rather than the crowd row.
`has_evidence` is classified per chat line by
`strategy.meeting.social.has_evidence_context` (keyword format check: body /
vent / saw / seen / follow / kill / report …). The plain-sus magnitudes are
deliberately conservative: the 0/185 was measured against one opponent engine
(truecrew:v14), so the tell is treated as a graded cue, not a near-certainty.

### 3.6 Deliberately excluded (too noisy to score)

- **Brief proximity** to a *living* player — crew constantly pass within kill range.
- **Distant near-body** — beyond `BODY_NEAR_DIST` is just passing through.
- **`task` dwell as exculpation** — would lower suspicion for "looking busy", but
  imposters fake tasks (Pretend does exactly this), so it isn't reliable innocence.

These are still in the event log and are serialized for the opt-in meeting LLM;
they just map to a `0` log-LR in the deterministic Bayesian model.

---

## 4. Thresholds & tuning knobs

| Knob | Value | Effect |
|---|---|---|
| `FLEE_PROBABILITY` | 0.9 | posterior at/above which we **flee** a player (reactive). Higher = more conservative. |
| `VOTE_PROBABILITY` | 0.8 | the *baseline* vote bar. The actual meeting bar is **state-dependent** (`vote_policy.vote_bar`): 0.75 with a comfortable margin, 0.8 at margin 3, 0.9 at margin ≤ 2, and **0 in a must-eject endgame** (skipping loses to the next kill, so the crew votes its best read). See `strategy/meeting/vote_policy.py`. |
| `PRIOR_MIN` / `PRIOR_MAX` | 1e-3 / 0.99 | clamp the prior so log-odds is finite. |
| `WITNESSED_LOG_LR` | ln 1e6 | how strong a witnessed kill/vent is (definitional). |
| the per-event log-LR functions + their constants | §3.3 | how much each graded cue moves belief, *and its shape* vs. duration/distance. **The main thing to fit.** |

**Consumers of the posterior.** `believed_imposters` (P ≥ `FLEE_PROBABILITY`) gates
the Flee mode; the rule-based selector then adds spatial hysteresis so Flee does
not flicker on/off while the suspect is near the distance threshold.
`vote_policy.fallback_vote(belief)` is the Attend Meeting vote target (design
§7.1): the highest-P live non-self, non-teammate player over the state-dependent
`vote_bar`, with a near-deadline anti-split swap onto the plurality; the action
layer maps that color → its candidate-grid slot and steps the cursor onto it
(§4.3), falling back to skip (and, past a step budget, a last-resort confirm) if
the target can't be resolved. (`top_suspect` remains the bare flat-bar read.)

---

## 5. Assumptions and their consequences

These are v1 simplifications. Each is sound enough to ship and clearly documented so
we know what to revisit.

1. **Naive Bayes (conditional independence).** We treat evidence types as
   independent given role and sum their `log(LR)`. Correlated evidence (e.g. two
   cues that tend to co-occur) is over-counted → over-confidence. Mitigated for now
   by counting each *type* once and by conservative weights. A joint model is the
   eventual fix.
2. **Mostly positive evidence.** The social cues (§3.5) add the first weak
   exculpatory terms (accused-by-imposter, voted-for-imposter), but absence of
   suspicious behaviour still never lowers a player below the prior. A true model
   would also use absence likelihoods (e.g. "watched them a long time, never
   vented").
3. **Marginal (not joint) budget redistribution.** The prior now redistributes the
   imposter budget as players are confirmed or die (§2.1), but it is still a
   symmetric marginal — a proper joint/sequential model over the full K-imposter
   assignment (where strong suspicion on one player lowers everyone else's) is a
   refinement.
4. **Observer-relative evidence.** Suspicion is built only from what *this* agent
   saw. Two crewmates can hold different posteriors about the same player. That is
   correct (it mirrors real play) but matters for learning (§6): LRs must be
   estimated from an observer's vantage, not from global ground truth of what
   happened.

---

## 6. Fitting the log-LR functions from replays

This is the durable process by which the functions improve. The agent never learns
at runtime — we (offline) fit each graded cue's function from labelled replays and
update §3 + §7.

The quantity each function approximates, **as a function of the event's features**:

```
logLR(e) = ln[ P(e's features | imposter) / P(e's features | crewmate) ]
```

For a feature like dwell duration, estimate the ratio **per bin** and read off the
*shape* (this is exactly how we found body-proximity should *decrease*):

```
                  fraction of imposter near-body events in this duration bin
ratio(bin) ≈  ───────────────────────────────────────────────────────────────
                  fraction of crewmate near-body events in this duration bin
```

Procedure:

1. **Replays give ground truth.** A replay records every player's true role. Load it
   with the viewer recipe in [`docs/crewrift-replays.md`](../crewrift-replays.md).
2. **Reconstruct observations from an observer's POV.** Evidence is what a crewmate
   *saw*, so re-run the event log + tape detectors as if crewborg were a particular
   crewmate in that game — using that player's line-of-sight/visibility, not the
   global state. Do this per (observer, game). Record each event with its features
   (duration, distance, target role) and the subject's true role.
3. **Bin by feature and estimate the ratio per bin** (with an *opportunity*
   denominator — players the observer could have caught, not all players).
4. **Smooth** (Laplace/add-k) so rare bins don't give 0/∞.
5. **Fit a simple closed form** to the binned ratios — keep the family in §3.3 (flat,
   linear-fade, saturating-ramp) unless the data clearly wants another simple shape.
   Update the function's constants (and the form if needed).
6. **Sanity-check independence.** Highly correlated cues are double-counted by naive
   Bayes — prefer merging or down-weighting.
7. **Update §3.3 + the provenance log (§7), then mirror into `suspicion.py`.** Re-run
   the suspicion tests; they assert *relational* properties (evidence raises P, one
   cue stays below the flee bar, corroboration crosses it, body-proximity brief >
   long), so they survive re-tuning unless the qualitative shape changed.

The witnessed-kill/vent log-LR is **definitional** (we saw it happen) and stays at
the near-certainty value; it is not fit.

The replay-analysis tooling itself is not built yet. When it is, this section should
gain the exact command/script and its output format.

---

## 7. Provenance log

One row per value-setting event. Keep this honest — it is how we know whether a
weight is a guess or earned.

| Date | Cue | Peak LR / shape | Source | Games | Notes |
|---|---|---|---|---|---|
| 2026-06-01 | witnessed kill/vent | 1e6, constant | definitional | — | we saw it; not fit |
| 2026-06-01 | `vent_dwell` | 15, flat ≥24 ticks | hand estimate | 0 | initial guess (superseded) |
| 2026-06-01 | `body_linger` | 3, flat ≥24 ticks | hand estimate | 0 | initial guess (superseded — gate inverted the signal) |
| 2026-06-01 | `follow_to_death` | 6, flat ≥48 ticks | hand estimate | 0 | initial guess (superseded) |
| 2026-06-01 | `vent_dwell` | LR 8, flat past 3-tick crossing | hand estimate | 0 | dwell is weak (transition detector owns real venting) |
| 2026-06-01 | `body_proximity` | LR 3 at first sight → 0 by 48 ticks (**decreasing**) | hand estimate | 0 | a skilled imposter flees; long camp ⇒ reporter ⇒ neutral |
| 2026-06-01 | `follow_to_death` | LR 6, ramp to full by 48 ticks (**increasing**) | hand estimate | 0 | sustained shadowing of a now-dead victim |
| 2026-06-09 | `social: defended_by_confirmed` | LR 4, boolean | hand estimate | 0 | imposter teammates defend each other |
| 2026-06-09 | `social: accused_by_confirmed` | LR 1/2, boolean | hand estimate | 0 | imposters scapegoat crew (inverted evidence) |
| 2026-06-09 | `social: crowd_accused` | LR 1.5, boolean ≥2 accusers | hand estimate | 0 | weak — crowds bandwagon |
| 2026-06-09 | `social: voted_for_confirmed` | LR 1/2, boolean | hand estimate | 0 | crew-like vote record (first exculpatory term) |
| 2026-06-11 | `social: crowd_accused` | unchanged LR 1.5, now counts **evidence-backed lines only** | measured (eval vs truecrew:v14) | 88 | bare accusations were 0/185 accurate — a disinfo chorus is not corroboration |
| 2026-06-11 | `social: plain_sus_target` | LR 1/3, boolean (suppressed by any evidence-backed accusation of the same color) | measured + hand-scaled | 88 | 0/185 bare-sus lines named a real imposter; magnitude kept conservative (one-opponent measurement) |
| 2026-06-11 | `social: plain_sus_speaker` | LR 2, boolean | measured + hand-scaled | 88 | the framing engine marks the speaker; 11/16 ejections followed a bare-sus line |

---

## 8. Adding a new evidence type

1. **Make it observable.** If it is a durative interaction, add a `PlayerEvent`
   kind in the event log (§5.2); if it is a frame transition, add a detector on the
   tape (§5.1) that adds to `confirmed_imposters`.
2. **Write its `_<cue>_log_lr(event[, belief]) -> float`** — a small closed-form
   function of the event's features (think about the *shape*: does the cue get more
   or less suspicious with duration/distance?), with named constants.
3. **Aggregate it** in `_graded_log_lr` (`max` over the player's events of that kind).
4. **Document** it in §3.3 + add a provenance entry (§7) — initially a hand estimate,
   flagged for fitting.
5. **Test** the relational behaviour (raises P; alone below the flee bar unless it's
   near-certain; and its feature shape, e.g. brief > long if decreasing).

---

## 9. Roadmap

- ~~Suspicion-aware voting~~ — **done**: Attend Meeting votes the highest-`P` live
  player over the state-dependent `vote_policy.vote_bar`, else skips
  (`fallback_vote`; §4 consumers).
- ~~Dynamic prior~~ — **done** (marginal budget redistribution, §2.1); a proper
  *joint* model over the K-imposter assignment remains (§5.3).
- ~~Chat/vote evidence~~ — **done** (the social cues, §3.5) from the accusation
  graph + meeting vote history; richer cross-meeting patterns (mutual-defense
  pairs, vote-bloc correlation) remain.
- **Exculpatory evidence** (`LR < 1`) beyond the social cues, and an absence model.
- **The offline LR-learning pipeline** (§6).
- ~~LLM meeting consumer~~ — **done**: the opt-in Attend Meeting LLM consumes the
  per-player view (identity + life + events + posterior), chat transcript, and
  vote tally for chat/voting reasoning.
