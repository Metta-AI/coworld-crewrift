---
name: base-optimizer-framework
description: Foundational methodology for autonomous policy optimization. Use before running or designing an optimizer loop, creating eval plans, collecting player artifacts, reconstructing replay data, generating hypotheses, testing policy changes, or deciding whether an improvement is real.
---

# Base Optimizer Framework

An optimizer is not a single model. It is a loop of tools, data, hypotheses,
policy changes, evals, and versioned decisions.

The job is to find score-improving behavior changes using evidence, not vibes:

```text
policy + game details -> eval set -> replay/artifact dataset -> pattern mining
-> hypothesis -> instrumentation/change -> eval with variance -> verdict
-> memory/version update -> repeat
```

## Core Loop

1. **Start from objective**: define the scoring objective precisely.
   - Win a league/tournament.
   - Beat top leaderboard policies.
   - Maximize expected points across all opponents.
   - Improve a specific metric without regressing broad score.
2. **Select eval distribution**: choose opponents, seats, seeds, variants, and episode count to match the objective.
3. **Run eval set**: collect enough episodes to expose variance and edge cases.
4. **Persist all evidence**: request body, request result, episodes, replays, artifacts, hosted stdout/stderr logs, summaries, hypotheses, candidate refs, verdicts.
5. **Reconstruct behavior**: join game replay state with player artifact state.
6. **Mine patterns**: identify obvious problems, anomalies, opponent patterns, and high-leverage heuristics.
7. **Generate hypotheses**: explain why the policy wins or loses in a testable way.
8. **Instrument or modify policy**: update artifacts/logging when data is missing; change strategy only when evidence supports it.
9. **Verify with variance**: rerun matched evals; compare mean, stderr, role metrics, and broad guardrails.
10. **Record verdict**: promote, reject, weak evidence, or needs more data.

The loop continues indefinitely. Each iteration should improve either the policy,
the dataset, the analysis tools, or the skill/memory that drives the next loop.

## Data-First Rule

Before changing policy, ask:

- What behavior pattern would explain the score gap?
- What data would confirm or falsify it?
- Does the current replay/artifact data contain that signal?
- If not, what artifact fields must the player log next?

Hypothesis-first data collection beats generic logging. Do not collect everything
blindly; collect the fields needed to answer the current policy question.

## Player Artifact Contract

Player artifacts are the durable source of policy reasoning; hosted stdout/stderr
are crash evidence (policies can fail before artifacts flush) — not a
replacement. Each policy should write artifacts that are persistent,
downloadable, structured, indexed/joinable, documented by a README/schema, and
stable across batches, so a 100-episode eval is analyzable by joining tables, not
scraping logs. Artifact writes must be best-effort (catch failures so
instrumentation can't crash the player). Full contract, schema, and upload helper:
`player-artifacts`. (crewborg already emits `trace.db` — see
`crewrift-optimization`.)

## Hosted Log Triage

Before treating an eval as strategic evidence, inspect the hosted player logs for
this optimizer's slot: tracebacks, exception type + source `file:line`, malformed
action / serialization errors, action-validation failures, provider/LLM
timeout/auth/throttle errors, process crash / no-action loop / failure before
artifact emission. A log traceback is first a policy/runtime bug, not a strategy
signal — record it with the episode request id and fix or quarantine it before
promotion.

## Replay Reconstruction

Replay data explains what happened; artifact data explains what the player saw,
believed, and chose. Reconstruct by joining episode id, slot, policy version id,
tick/round/phase, entity/row id, action/response, and score-event/result row, to
support score/role/seat attribution, opponent-interaction pairs, temporal
sequence, and observation-vs-truth. If a join key is missing, add it to future
artifacts before continuing. Mechanics: `replay-artifact-analysis` (and the
Crewrift `server_tick` join in `crewrift-optimization`).

## Eval Design And Variance

Eval variance is a core gotcha. Every result needs an uncertainty estimate and a
matching eval distribution; sometimes beating a weaker policy by more points
matters more than narrowly beating the strongest — optimize expected leaderboard
score, not status. Minimum aggregation: completed episode count, mean ± stderr
per policy, win/tie/loss, role- and seat-conditioned metrics, failure count. Do
**not** trust one-off wins, tiny sets, unmatched opponent distributions, results
without variance, or changes that only improve the cherry-picked target. Full
front/back halves: `eval-variance-design` and `eval-aggregation`.

## Pattern And Heuristic Toolkit

Convert raw data into pattern-friendly features (spatial, temporal, role/seat,
opponent, observation-gap, decision-path, anomaly, interaction-pair) and
visualize after the eval from stored data when tables hide the pattern (grids,
timelines, heatmaps, histograms, confusion matrices, per-opponent distributions).
For map/temporal games this is `spatial-temporal-analysis`; for ranking the
variance-explaining behaviors, `replay-variance-miner`.

## Hypothesis Generation

Good hypotheses are specific, causal, measurable, and falsifiable.

Template:

```text
Observation:
Causal guess:
Data supporting it:
Data still missing:
Policy or instrumentation change:
Expected metric movement:
Eval plan:
Overfit/regression risk:
Rollback condition:
```

Examples:

- "Our crewmate role loses because the vote bar is too high in a 2-candidate
  endgame, so we skip and the next kill wins it for imposters."
- "Our imposter pathing loses kills late game because artifact positions show it
  stalls at an unreachable anchor instead of re-rooting."
- "We bleed points to vote-timeout penalties because the cursor-confirm path
  misses the deadline under LLM-meeting latency."

## Change Discipline

Prefer one hypothesis per candidate. A candidate may include instrumentation plus
strategy if instrumentation is necessary to validate the same hypothesis.

Valid change types:

- policy heuristic,
- opponent classifier,
- fallback logic,
- LLM routing/prompting,
- artifact logging,
- eval distribution,
- analysis/visualization tool,
- skill/process update.

Avoid broad refactors during optimization unless the hypothesis is specifically
about maintainability blocking future experiments.

## Versioning Discipline

Every candidate needs:

- base policy ref,
- candidate ref,
- source diff or parameter diff,
- hypothesis id,
- eval request ids,
- artifact locations,
- stdout/stderr log findings,
- verdict,
- rollback ref.

When testing 4-5 versions at once, keep variant names tied to their hypothesis.
Do not compare candidates that changed multiple unrelated variables unless the
campaign is explicitly exploratory.

## Trust And Anomaly Checks

Before accepting an eval:

- inspect failed episodes,
- inspect hosted stdout/stderr for our player in failed and representative
  completed episodes,
- inspect top positive and negative outliers,
- check seat balance,
- check opponent mix,
- compare against historical baseline performance,
- verify policy version ids are the intended ones,
- confirm artifacts are from the right player/slot,
- inspect whether the result depends on a backend or provider failure.

Anomalies are not noise until explained. They often reveal the next hypothesis.

## Promotion Standard

Promote only when the candidate improves the optimization objective with enough
evidence and does not create unacceptable regressions.

If the candidate:

- beats one opponent but loses broad expected score: reject or gate behind a
  classifier.
- improves mean but variance overlaps heavily: weak evidence.
- wins only because of an eval artifact not present in league play: reject.
- improves policy and artifact quality but score is neutral: record as a tooling
  improvement, not a champion promotion.

## What To Persist After Each Loop

Persist a compact run record:

```text
objective:
champion:
candidate:
eval distribution:
episodes completed:
metric summary:
patterns found:
hypotheses tested:
policy/artifact changes:
verdict:
next action:
```

The next agent should be able to resume the loop from this record without
reconstructing intent from chat history.
