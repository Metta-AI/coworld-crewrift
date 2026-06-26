---
name: hosted-xp-evals
description: Create, monitor, and archive hosted experience request evaluations for policy-vs-policy testing. Use when running XP requests, pairwise evals, top-N/random evals, or downloading hosted episode replays and artifacts.
---

# Hosted XP Evals

Use hosted experience requests when the policy must be tested against submitted
league policies or when local execution cannot reproduce the real hosted setup.

## Inputs

Collect:

- League or coworld target id.
- Our policy ref (name:version format, e.g. `my-policy:v5`) or policy version UUID.
- Opponent selector:
  - explicit policy refs or policy version UUIDs,
  - `top_n` (integer),
  - random league selection.
- Number of episodes.
- Slot assignment: use `-1` for any-seat (system assigns), or `0`/`1` for fixed.
- Notes string with purpose, champion/candidate refs, and target refs.

## Request Shapes

The v2 experience-requests API is **roster-based**: every participant goes through
`roster[].player`, which takes exactly one of `{ "policy_ref": "<name:vN>" }` or
`{ "top_n": <int> }`; `slot: -1` = any seat. Top-level `requester` / `opponents` /
`rotate_seats` / `top_n` / `player_selection` and `policy_version_id` inside a
roster player all produce **422**. The full pairwise and broad-top-N JSON bodies
live in `coworld-operations`; Crewrift's 8-seat roster shapes (leaderboard-variance
and targeted-pairwise) are in `crewrift-eval-design`.

## Pre-Flight Checks (before creating XP requests)

1. **Verify the `run` attribute exists** on the policy version, or the container
   can't start and scores −100 (see `coworld-operations` for the curl + the
   `--run` upload rule).
2. **Start with a 1-episode smoke test** before larger batches — catches startup
   crashes cheaply.
3. **Confirm the active player** owns the policy being tested.

## Eval Ladder

Follow this progression to avoid wasting episodes:

1. **Smoke** (1 episode): Confirm policy starts, connects, completes without
   crash. Check for -100 score (timeout = container didn't run).
2. **Diagnostic** (6 episodes): Pairwise vs 1-2 specific opponents. Look for
   pattern signals.
3. **Candidate** (24-40 episodes): Matched distribution vs champion targets.
4. **Guardrail** (40 episodes): Broad top-N/random to catch overfit.
5. **Promotion**: Only after candidate + guardrail both pass.

## Monitoring

Poll until terminal or timeout:

- pending/submitted/running/completed/failed counts.
- child episode ids and replay URLs.
- failed episode errors.
- hosted stdout/stderr logs for failed episodes and representative completed
  episodes.

If a large request fails at the backend boundary, split into smaller batches and
aggregate them as one eval set.

## Artifact Capture

For each eval, persist:

- request body,
- request detail,
- child episode list,
- replay JSON for every completed episode,
- player artifact zip/database when available,
- hosted stdout/stderr logs or saved log triage summary for our player,
- analysis summary,
- hypothesis/verdict records.

Artifacts are preferred for learning because logs may reset while artifacts
persist. Logs are still mandatory for crash diagnosis because a policy can fail
before artifacts are written.

## Hosted Log Triage

For each episode request, inspect this optimizer player's stdout/stderr and record
the standard triage block (traceback / exception / `file:line` / malformed action
/ provider failure / artifact-emitted) — schema in `coworld-operations`. An eval
is tainted when logs show an unresolved policy crash, malformed action, or missing
fallback; fix that before interpreting score.

## Aggregation

Aggregate across all completed episodes per role and per version — mean ± stderr,
win/tie/loss, seat-conditioned mean, failure/crash count, plus the
hypothesis-relevant behavioral rate. Never decide from a single scalar without
variance and role breakdowns. The variance-aware verdict (taint filter, effect
size with uncertainty, anomaly gate) is `eval-aggregation`.
