---
name: replay-variance-miner
description: Turn a corpus of real scored episodes into a RANKED list of testable, VP-quantified hypotheses by finding the behaviors that explain a policy's own score variance. Use after any multi-episode eval to decide what to change next, instead of eyeballing replays or guessing. Distinguishes load-bearing finishing moves from the invariant engine.
---

# Replay-Variance Miner

Find, from a corpus of real episodes, the behaviors that separate a policy's
**winning games from its losing games**, rank them by the victory points they
could recover, and emit each as a hypothesis. This is the bridge from "we ran an
eval and got scores" to "here is the single highest-leverage change, with its
evidence and expected VP."

## The core idea (why this beats a skill-tree / curriculum)

The behaviors a policy does in *every* game — its invariant engine — are what let
it beat other policies, but they are **useless for explaining its own variance**
because they are identical in its best and worst games. The score spread is driven
by high-variance, load-bearing moves (in Crewrift: imposter kill tempo, a clean
crewmate vote, a missed-vote penalty, a stuck-idle wedge). A curriculum that tests
invariant competence learns nothing; it optimizes a proxy that does not move the
true objective (and is gameable).

So: **demote invariant behaviors** (small spread across score buckets) and **rank
the load-bearing ones** (large spread AND score correlation). A behavior is only a
hypothesis if it actually explains variance.

## When to use

- After any eval with ≳8 scored episodes (more is better; the spread is the signal).
- When deciding what to change next and you don't want to hand-eyeball replays.
- When scalar means hide the real story (high variance — exactly Crewrift).
- To mine a champion's OWN high-vs-low games for self-improvement targets, or to
  mine candidate-vs-champion gaps.

## How it works

1. **Featurize** — map each episode to a flat `dict[str,float]` of behavioral
   features (event timings, presence flags, scoring-category outcomes, counts)
   plus the seat's `score`. For Crewrift, derive features from the joined
   replay + `trace.db` (`crewrift-optimization`): time-to-first-kill, vote
   accuracy, missed-vote count, stuck ticks, fake-task dwell, etc. — and split by
   role, since imposter and crewmate variance are different stories.
2. **Associate** — for every feature, split episodes into top/bottom score
   buckets (25th/75th pct) and compute coverage, Pearson `r` with score,
   high-vs-low spread, and a **load-bearing VP swing** =
   `|r| × normalized_spread × bucket_score_gap`. This rewards features that BOTH
   correlate with score AND vary across buckets; flag each as load-bearing or
   invariant.
3. **Rank & emit** — drop invariant features, sort by VP swing, emit the top-N as
   hypotheses in the standard format (Observation / Causal guess / Evidence /
   Missing data / Change / Expected / Eval plan / Overfit risk / Rollback), each
   with a concrete VP estimate and a `Change` hint mapped to a knob
   (`guide/SKILL.md` "Where to edit").

## Feature design rules (so the miner finds real signal)

- Include the invariant engine features on purpose — they get demoted and
  reported as "invariant," which is itself evidence (what's table-stakes vs. what
  wins).
- Make "never happened" a real worst-case value (e.g. `last_tick+1` for a timing),
  not a missing key, so non-occurrence is comparable.
- Prefer features tied to scoring categories and enabling events; avoid raw
  per-tick noise.

## When a result is NOT trustworthy

- The corpus is < ~8 episodes.
- The score spread is pure noise (no feature clears the load-bearing bar).
- The "failure" is an infra crash / −100 taint visible in stdout/stderr — fix
  that first (`crewrift-optimization` taint rule), don't mine it.

## Integration with the optimizer loop

```
eval -> scores -> [replay-variance-miner] -> ranked hypotheses -> pick #1 ->
  one scoped change -> verify with variance -> promotion gate -> record -> repeat
```

The miner sits at the analyze → hypothesize step. It does not replace the
promotion gate (still statistical, `eval-aggregation` / `promotion-gate`) or the
per-decision drill-down (`replay-artifact-analysis` / `spatial-temporal-analysis`
to confirm the top hypothesis's "Missing data" line). It replaces *guessing what
to change*.
