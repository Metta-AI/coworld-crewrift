---
name: optimizer-loop
description: "Use to run the crewborg policy optimization + eval loop: run eval sets, find behavior patterns, generate hypotheses, instrument + change the policy, and re-evaluate against an optimization goal. Trigger on 'optimize the policy', 'improve crewborg', 'run the eval loop', 'why is crewborg losing', or 'iterate on the policy for <goal>'."
---

# Optimizer & Eval Loop (crewborg)

## What this is

The optimizer is **not a model** — it is a loop of tools wired together. The
standard cycle is:

```
run eval set → analyze data → generate hypothesis → instrument + change policy
   → re-run eval set against policies → verify signal → repeat
```

It just keeps going: hypothesis → change → re-run, continuously, finding obvious
problems first and testing fixes in a structured way.

**Announce at start:** "I'm running the crewborg optimizer loop: I'll state the
optimization goal, run an eval set, find patterns, form a hypothesis, instrument
+ change the policy, then re-evaluate for a statistically real change."

This skill is the **orchestrator**. Each numbered phase below hands off to a
focused sub-skill in this folder. Read the sub-skill before doing that phase.

## Step 0 — Pin the optimization goal (do this first, never skip)

Every later decision (which eval, which variance config, which metric, what
counts as "better") is **derived from the goal**. Write it in one sentence with
a measurable target and a comparison set. Examples:

- "Win the tournament league" → maximize leaderboard points across the *current
  field*, beating the *specific* policies on the board.
- "Beat truecrew:v14 head-to-head" → win rate vs one named opponent.
- "Raise crewmate task throughput without regressing imposter win rate."

Then derive the **scoring objective**, because it changes what to optimize:

> Sometimes it is worth optimizing to beat the *weaker* policy more (it appears
> more often / yields more points) than to beat the *single strongest* policy
> (rarer, fewer marginal points). Make this explicit before choosing an eval
> field — see `eval-set-design`.

Record: goal sentence, target metric, comparison set, baseline policy (the
current champion / last shipped build), and the scoring objective. These are the
loop's invariants.

## Step 1 — Run the baseline eval set → `eval-set-design`

Run an eval set for the **current** policy against the goal's comparison field.
The variance config (opponents, seat rotation, episode count, seeds) is chosen
*for the goal*, not generically. Hand off to `eval-set-design` for the recipe
and the trust requirements (large N, seat rotation, disconnect/taint filtering).

## Step 2 — Collect player data + match it to replays → `artifact-capture`, `replay-reconstruction`

Two data sources, joined:

- **Player artifacts** — crewborg's own per-episode `trace.db` + `summary.json`
  (location, what it saw, modes, intents, domain events). This is *the* data
  unlock. See `artifact-capture` for saving/downloading and the indexed schema.
- **Game replays** — the authoritative `.bitreplay`, re-simulated to ground truth
  (kills, votes, tasks, roles by color). See `replay-reconstruction` for
  expanding a replay and joining it to the artifact on `server_tick`.

Collect *all* player data from the eval episodes and match it to the replay
timeline → `eval-aggregation` aggregates it correctly across the set.

## Step 3 — Find patterns / generate a hypothesis → `pattern-toolkit`, `hypothesis-generation`

Identify behavior patterns that the goal cares about. Use the
`pattern-toolkit` (heuristics, cheesy patterns, visualizations — grids/pictures
written *from the data after the fact*, fed back into the LLM) to shape the data
so a pattern is recognizable. Then `hypothesis-generation` turns a pattern into
a falsifiable, pre-registered hypothesis with a predicted metric move.

**Hypothesis-first is the rule.** You need a pre-game hypothesis, *then* modify
the policy to collect the data that matches it. Images/visualizations are written
from the collected data, not during the game.

## Step 4 — Instrument + change the policy → `data-collection-design`

A hypothesis usually needs data crewborg does not yet log. Use
`data-collection-design` to decide *what to log* (which `domain.*` events,
positions, metrics) so the next eval's artifacts can validate the hypothesis,
**then** make the policy change. Instrumentation and behavior change land
together so the very next eval is diagnostic.

Keep the change minimal and gated where possible (an env flag / variant) so 4–5
versions can be A/B'd at once. Use strong versioning (every build → a pinned
`pv` id, recorded in the findings doc).

## Step 5 — Re-run the eval set + verify → `eval-set-design`, `eval-aggregation`

Re-run the *same* eval config (so the comparison is controlled) for the changed
policy. Then `eval-aggregation`:

- aggregates win rate / score / behavioral metrics per version and per role,
- reports the effect size **with uncertainty** (not "better"/"worse"),
- checks whether the change produced a **statistically real** move on the goal
  metric, and
- runs the **anomaly checks**: is N large enough to trust it? Are episodes
  disconnect/taint-polluted? How did this policy do in this exact situation
  historically? A win-rate move inside the noise band is not signal.

## Step 6 — Decide and loop

- **Signal confirmed, goal metric up, no regression** → record in the findings
  doc, promote per the deployment gate, set the new build as baseline, pick the
  next pattern.
- **No signal / regressed** → the hypothesis is the casualty, not the loop.
  Record the negative result and the confound, then return to Step 3.

Always lead with the result and its uncertainty (pairs with
`tr.research-partner`). The loop never "finishes"; it stops when the goal metric
plateaus within noise across several hypotheses.

## The open gap (flag it, don't pretend it's solved)

Spatial/temporal analysis is **not done as rigorously as it could be**. Static
grids/heatmaps lose ordering and timing. When a hypothesis is inherently
spatial-temporal (intercept geometry, who-followed-whom-then-died, room flow over
time), say so and prefer an adaptive/sequential view over a flat picture — see
`pattern-toolkit` § "Spatial-temporal gap".

## Sub-skill map

| Phase | Sub-skill |
|---|---|
| 0, 1, 5 | `eval-set-design` — variance config for the goal; trust requirements |
| 2 | `artifact-capture` — save/download player artifacts; the indexed schema |
| 2 | `replay-reconstruction` — expand `.bitreplay`; join to artifact on `server_tick` |
| 3 | `pattern-toolkit` — heuristics, cheesy patterns, visualizations |
| 3 | `hypothesis-generation` — pattern → falsifiable pre-registered hypothesis |
| 4 | `data-collection-design` — what to log to validate the hypothesis |
| 5, 6 | `eval-aggregation` — aggregate, effect size + uncertainty, anomaly checks |

## Integration

- **Pairs with:** `tr.research-partner` (hypothesis/evidence framing),
  `t.policy-regression` (don't reduce the comparison field to one policy).
- **Grounded in:** `players/crewrift/crewborg/design.md`,
  `docs/replay-analysis.md`, `docs/designs/suspicion.md` §6 (offline LR fitting
  is a worked instance of this loop), `episode_data/FINDINGS_v4.md` (a full loop
  iteration, including a no-show course-correction and a failed gate).
