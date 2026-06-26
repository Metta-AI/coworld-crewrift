---
name: replay-artifact-analysis
description: Reconstruct game behavior from hosted replays and persistent player artifacts. Use to aggregate XP evals, identify role leaks, inspect prompts/answers/actions, and build debugging datasets.
---

# Replay Artifact Analysis

Turn raw hosted eval data into a structured behavior dataset.

## Required Files

For each eval set, gather:

- `xp_request` detail or equivalent request metadata.
- child episode rows with participants and scores.
- replay JSON for each completed episode.
- player artifact archives/databases for our policy when available.
- any policy-generated reasoning logs inside the artifact.
- hosted stdout/stderr logs for our policy's episode request/agent slot.

## Reconstruction

For each episode:

1. Map participant slot to policy version, policy label, player, and seat.
2. Load final scores by policy version id.
3. Inspect hosted stdout/stderr for this optimizer player's slot. If a
   traceback, malformed action, provider failure, timeout, or crash appears,
   record it before interpreting score.
4. Iterate the ground-truth replay events (kills, votes, task completions, phase
   flips), each with its tick.
5. Attribute each event to slot, role, seat, the action taken, and the
   points/outcome for each side.
6. Join policy artifact records by episode id and the shared clock
   (tick/round/phase, plus `seat/slot`). For Crewrift the join key is
   `server_tick` — see `crewrift-optimization`.

## Metrics

Compute (always per role and per version):

- policy mean score and standard error,
- win/tie/loss rate,
- seat-conditioned scores,
- role-conditioned mean points,
- the rate/event the current hypothesis predicts (e.g. vote accuracy, kills/ep,
  missed-vote count, stuck ticks),
- decision-path distribution (scripted / fallback / LLM) and LLM failure count,
- stdout/stderr traceback count, exception types, source `file:line`, malformed
  action count, and provider/runtime failure count.

## Pattern Extraction

Look for: a recurring failure mode that diverges between versions, an opponent
behavior change across versions, a decision path that scores worse than another
(fallback vs LLM), deterministic ties, and anomalies/outliers. Require repeated
observations before labeling a pattern; treat a version change as a new
hypothesis.

For our policy's own artifact emission contract and schema, see `player-artifacts`
(crewborg emits `trace.db`, documented in `crewrift-optimization`).

## Output

Produce a concise analysis bundle:

- `summary`: numeric metrics.
- `profiles`: per-opponent behavior profile.
- `failures`: top policy failure modes.
- `examples`: representative episode/row ids.
- `log_triage`: stdout/stderr findings by episode request id.
- `hypotheses`: candidate improvements to test next.
