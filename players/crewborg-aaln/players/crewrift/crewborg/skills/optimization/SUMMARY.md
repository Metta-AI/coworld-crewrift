# Optimizer & Eval Loop — Simple Summary

A plain-language summary of the crewborg policy optimization workflow. The full,
runnable detail lives in the skill files in this folder (start with
[`optimizer-loop`](./optimizer-loop/SKILL.md)).

## The one-line idea

The optimizer is **not a model** — it is a loop of tools. You run evals, look at
the data, guess what's wrong, change the policy, and run the evals again. Repeat
until the score stops moving.

```
pin the goal
   │
   ▼
run eval set ──► collect player data + match to replays ──► find patterns
   ▲                                                              │
   │                                                              ▼
   └── verify the change is really better ◄── change policy ◄── form hypothesis
       (with anomaly checks)                  (+ add logging)
```

## The loop, step by step

0. **Pin the goal first.** Everything downstream is derived from it. "Win the
   league" is a different optimization than "beat one named policy." Write it as
   one sentence with a measurable target and a comparison set.

1. **Run a baseline eval set.** Pick the opponent field, seat rotation, episode
   count, and seeds *for the goal* — not generically.

2. **Collect the data and join it.** Two sources:
   - **Player artifacts** — crewborg's own per-episode `trace.db` (where it was,
     what it saw, what it decided). This is *the* data unlock.
   - **Game replays** — the authoritative `.bitreplay`, re-simulated for ground
     truth. Joined to the artifact on `server_tick`.

3. **Find a pattern, form a hypothesis.** Shape the data (heuristics, cheesy
   patterns, after-the-fact visualizations) until a pattern is visible, then
   write a falsifiable hypothesis with a predicted metric move.

4. **Instrument + change the policy.** The hypothesis usually needs data you
   don't log yet. Add that logging *and* make the change together, so the next
   eval is diagnostic. Keep it gated (env flag / variant) so several versions can
   be A/B'd at once.

5. **Re-run the same eval set and verify.** Aggregate per role and per version,
   report the effect size **with uncertainty**, and run the anomaly checks: is N
   large enough? Are episodes taint-polluted (disconnects score the lobby −100)?
   How did the baseline do in this exact situation before?

6. **Decide and loop.** Signal confirmed → promote, set new baseline, pick the
   next pattern. No signal → record the negative result, return to step 3. The
   hypothesis is the casualty, never the loop.

## Key principles

- **Hypothesis-first.** You need a pre-game hypothesis, *then* modify the policy
  to collect the data that matches it. Visualizations are written from the data
  after the fact, not during the game.
- **The goal sets the scoring objective.** Beating a *weak, frequent* policy can
  be worth more total points than beating the *single strongest, rare* one.
  Decide which before choosing the eval field.
- **Trust requires N + history.** A win-rate move inside the noise band is not
  signal. Large N, taint filtering, identical config on both arms, and awareness
  of past performance in that situation are preconditions for believing a number.
- **Strong versioning.** Pin every build to a policy-version id and record it in
  the findings doc.

## Known open gap

Spatial/temporal analysis is **not done as rigorously as it could be**. Static
grids/heatmaps lose ordering and timing. When a hypothesis is inherently
spatial-temporal (intercept geometry, who-followed-whom-then-died, room flow over
time), an adaptive/sequential view beats a flat picture. Flagged in
[`pattern-toolkit`](./pattern-toolkit/SKILL.md) § "Spatial-temporal gap".

## Where each piece lives

| Workflow need | Skill |
|---|---|
| Orchestrate the whole loop | [`optimizer-loop`](./optimizer-loop/SKILL.md) |
| What data to collect from the player | [`data-collection-design`](./data-collection-design/SKILL.md) |
| Reconstruct game info from replay, matched to player data | [`replay-reconstruction`](./replay-reconstruction/SKILL.md) |
| Save player artifacts | [`artifact-capture`](./artifact-capture/SKILL.md) |
| Large eval set with goal-tuned variance | [`eval-set-design`](./eval-set-design/SKILL.md) |
| Correctly aggregate the eval set | [`eval-aggregation`](./eval-aggregation/SKILL.md) |
| Toolkit of pattern / heuristic skills | [`pattern-toolkit`](./pattern-toolkit/SKILL.md) |
| Generate hypotheses | [`hypothesis-generation`](./hypothesis-generation/SKILL.md) |
