---
name: crewborg-suspicion-tuning
description: "Use to tune crewborg's crewmate-side social-deduction surface — the Bayesian P(imposter) suspicion model, its per-event likelihood-ratio functions, the state-dependent vote bar, and the measured opponent chat tells — by fitting weights from labelled replays. Trigger on 'tune suspicion', 'improve voting / meeting decisions', 'fit the LR functions', 'mine opponent chat tells', 'why did crewborg vote wrong', or 'optimize crewmate win rate'."
---

# Crewborg Suspicion & Vote Tuning (policy-specific)

The generic optimizer skills tell you *how to run the loop*. This skill is the
**crewmate-side lever** specific to crewborg: the Bayesian suspicion model is the
single highest-leverage tunable surface for crewmate win rate, and it is
explicitly designed to be **fit from replays** rather than guessed. Pair this with
`CREWBORG_INSIGHTS.md` (§2 opponent tells, §6 the model) and the generic
`policy-hypothesis-loop` / `eval-aggregation` skills.

> Grounding: `players/crewrift/crewborg/docs/designs/suspicion.md` (the canonical
> model + §6 fitting loop + §7 provenance log), `strategy/suspicion.py` (the
> `_*_log_lr` functions), `strategy/meeting/vote_policy.py` (the vote bar),
> `strategy/meeting/social.py` (chat → accusation graph). The code is what runs;
> a change must land in both the code and the suspicion-doc tables.

## When to reach for this

- Crewmate win rate is the lagging role (the v4 false-win showed a change can lift
  the aggregate while tanking imposters — so always read per-role).
- Crewborg votes out crew / skips a catchable imposter / flees the wrong player.
- You have labelled replays (ground-truth roles) and want a *measured* weight
  change, not a hand guess.

## The model in one screen (what you're tuning)

`belief.suspicion[color] = P(imposter)` via log-odds Bayes:

```
logit(P) = logit(prior) + Σ_type max_over_events logLR(event)
```

- **Prior** = remaining imposter budget over remaining candidates (catching one of
  K=2 ~halves everyone else's prior). `K = (P−3)//2` by default.
- **Near-certain** (LR ≈ 1e6, *definitional, not fit*): witnessed kill / vent from
  frame transitions, gated by the `shadow` line-of-sight mask.
- **Graded functions** (the fittable shapes — aggregated `max` per type so an
  unbounded event log can't inflate P):
  - `vent_dwell` — weak, flat past a 3-tick crossing (LR≈8).
  - `body_proximity` — **decreases** with dwell (LR 3 at first sight → 0 by ~48
    ticks): a skilled imposter flees a kill; a long camp = reporter = neutral.
  - `follow_to_death` — **increases** with dwell toward a now-dead target (LR≈6).
- **Social cues** = the measured opponent chat tells (next section).

`believed_imposters` (P ≥ `FLEE_PROBABILITY = 0.9`) gates Flee. The **vote bar** is
state-dependent (`vote_policy.vote_bar`): 0.75 at margin ≥4, 0.8 at margin 3, 0.9
at margin ≤2, **0 in a must-eject endgame**; anti-split swap onto the plurality
near the deadline; always cast something before the timer (a miss is −10).

## The measured opponent tells (re-measure when the field changes)

From `suspicion.md` §7, measured vs **truecrew:v14** over **88 games**:

- Bare `"<color> sus"` chat with **no evidence wording** was **0/185** accurate →
  the **named** color is likely framed crew (`PLAIN_SUS_TARGET_LOG_LR = −ln 3`,
  suppressed by any evidence-backed accusation), and the **speaker** is likely
  steering (`PLAIN_SUS_SPEAKER_LOG_LR = +ln 2`; 11/16 ejections followed one).
- Crowd accusations count **evidence-backed lines only** (body/vent/saw/follow/
  kill/report keywords) — a bare-sus chorus is disinfo, not corroboration.

**These magnitudes are deliberately conservative** (one-opponent measurement). If
your eval field is not truecrew-dominated, **re-measure them** before trusting them.

## The fitting loop (turn replays into a measured weight)

This is the worked instance of the optimizer loop (`suspicion.md` §6). Each weight
is a pre-registered claim validated by re-fitting.

1. **Pre-register the hypothesis.** "Cue X with feature shape Y predicts imposter
   with LR≈Z; changing it moves crewmate vote accuracy by N pp." Write it down
   before touching code (`policy-hypothesis-loop`).
2. **Get ground truth.** Reconstruct labelled replays (`replay-artifact-analysis`
   / `replay_analysis.py` → `expand_replay.nim`); roles-by-color from
   `domain.game_over`.
3. **Reconstruct evidence from an *observer's* POV** — re-run the event-log + tape
   detectors as if crewborg were a particular crewmate, using *that player's*
   line-of-sight, not global state. Record each event's features (duration,
   distance, target role) + the subject's true role. (Observer-relative is
   essential — two crewmates legitimately hold different posteriors.)
4. **Bin by feature, estimate the per-bin likelihood ratio** with an *opportunity*
   denominator (players the observer could have caught, not all players); smooth
   (Laplace) so rare bins aren't 0/∞.
5. **Fit a simple closed form** — keep the family (flat / linear-fade /
   saturating-ramp) unless the data clearly wants another simple shape.
6. **Update all three in lockstep:** the `_*_log_lr` function in `suspicion.py`,
   the §3.3 table, and the §7 provenance log (one row per value-setting event:
   date, cue, peak LR/shape, source, games, note).
7. **Re-run the suspicion tests** — they assert *relational* properties (evidence
   raises P; one cue stays below the flee bar; corroboration crosses it;
   body-proximity brief > long), so they survive re-tuning unless the qualitative
   shape changed.
8. **Verify in an eval** per-role (`eval-aggregation`): crewmate vote accuracy
   (votes landing on a real imposter) and crewmate win rate, vs the regression
   tripwire (imposter win rate must not drop).

## Adding a new evidence type

1. Make it **observable**: a durative `PlayerEvent` kind (event log) or a frame
   transition (tape detector adding to `confirmed_imposters`).
2. Write `_<cue>_log_lr(event[, belief]) -> float` — a small closed form; decide
   whether the cue gets **more or less** suspicious with duration/distance.
3. Aggregate it with `max` in `_graded_log_lr`.
4. Document §3.3 + a provenance row (flagged "hand estimate" until fit).
5. Test the relational behaviour.

## Cheap behavioral metrics that flag a suspicion/vote problem

Run these first (from `pattern-toolkit` / `crewrift-eval-design`):
- **Vote accuracy** — fraction of crewmate non-skip votes landing on a real
  imposter vs a crewmate (`vi/vc`); led-accusation accuracy.
- **Self-ejection / wrong-ejection count.**
- **Vote-timeout penalties** (−10) — a miss is a bug in the cursor/confirm path,
  not strategy. Must be ~0.
- **Flee onset color × ground-truth role** — fleeing real imposters vs crew.

## Integration

- **Pairs with:** `policy-hypothesis-loop` (pre-register), `eval-aggregation`
  (per-role verdict), `replay-artifact-analysis` (observer-POV reconstruction),
  `crewrift-optimization` (game concretes). See `CREWBORG_INSIGHTS.md` §2/§5/§6.
- **Grounded in:** `docs/designs/suspicion.md`, `strategy/suspicion.py`,
  `strategy/meeting/{vote_policy,social,prompts}.py`, `design.md` §10.1/§12.
