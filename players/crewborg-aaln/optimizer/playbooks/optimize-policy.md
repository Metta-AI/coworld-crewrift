<!--
Mirror of the Devin org playbook "Run an Agent Optimization Loop for a Coworld Policy".
Macro: !optimize_policy | playbook-1555bb32137d42129c1731e6f5311a1e
Canonical source: https://app.devin.ai/settings/playbooks/1555bb32137d42129c1731e6f5311a1e
Keep this file in sync with the live playbook; the live version takes precedence if they drift.
-->

# Run an Agent Optimization Loop for a Coworld Policy

**Macro:** `!optimize_policy`

## Overview
Run one evidence-first optimization loop to improve a Coworld game policy for a given player and league: collect live context, diagnose the current policy with hosted XP evals, form a falsifiable hypothesis, make one scoped change, verify it against the champion and broad guardrails, and either promote or reject with a recorded verdict. The loop is the standard cycle: observe → evaluate → reconstruct → analyze → hypothesize → change → verify → promote/reject → record.

## What's Needed From User
- Game name (here: `crewrift`) and, if known, the `league_id`. If league is unknown, discover it with `coworld leagues --json`.
- Player identity: `player_id` (or player name to select/create). The loop acts as exactly this player.
- Objective: climb leaderboard / beat top-N / maximize expected league score / improve a specific role without regressing others.
- Budget: max episodes (XP) the loop may spend, and whether promotion/submission is permitted. Size this from game variance — see Eval Sizing below; high-variance games (e.g. crewrift) need a much larger episode budget than low-variance ones (e.g. cue-n-woo).
- Auth: `SOFTMAX_USER_API_TOKEN` set in env (verify with `coworld status`). Bedrock access via `--use-bedrock` for LLM policies.

## Procedure
1. Collect live context (do not trust memory): run `coworld player use <player_id>`, then read `coworld leagues <league_id> --json`, `coworld memberships --league <league_id> --active-only --json`, and `coworld submissions --league <league_id> --mine --json`. Record current champion policy ref + version id, leaderboard rank/score, and the top target policies.
2. Locate the policy source: the policy lives at `players/crewborg-aaln/` (vendored `players/crewrift/crewborg/`). Identify the current champion's source/version so you have a rollback baseline.
3. Diagnose the current policy with a smoke + diagnostic XP eval (see Eval Sizing and the Eval Ladder in Advice). Size the diagnostic to the game's variance, not a fixed number. Download replays, player artifacts, and hosted stdout/stderr for the player slot. Inspect logs FIRST — a traceback, malformed action, missing fallback, or `run`-attribute/timeout is a policy/runtime bug to fix before any strategic analysis.
4. Reconstruct behavior: join replay rows (what happened, score per row, owner/answerer, seat) with artifact rows (what the policy saw, parsed features, decision path, fallback/LLM use). Aggregate mean ± stderr, win/tie/loss, and seat-/role-/opponent-conditioned breakdowns. Never reason from a single scalar score.
5. Write one falsifiable hypothesis using the template in Advice (observation, causal guess, evidence, change, expected metric movement, eval plan, overfit risk, rollback condition).
6. Make ONE scoped change implementing the hypothesis. Keep the diff small so the eval result is attributable. Add artifact logging first if the hypothesis needs row-level evidence you don't yet capture.
7. Build the policy Docker image and upload it: `coworld upload-policy <image> --name <policy_name> --run <argv0> --run <argv1> [--use-bedrock --bedrock-model <id>]`. Each argv token needs its own `--run` flag. Then verify the `run` attribute exists on the new version (missing `run` → -100 timeout penalty, the most common silent "0 score" failure).
8. Verify with variance: run candidate XP evals vs (a) the target opponent/pattern, (b) the previous champion, and (c) a broad top-N/random guardrail. Compute the required completed-episode count from Eval Sizing FIRST, then issue as many XP requests as it takes to reach it (the API is fast and effectively unlimited). Split into small batches and keep in-flight concurrency modest; treat whole-episode -100 sweeps as infra contention to retry, not policy scores.
9. Classify the verdict: `promote` (clears objective + guardrails, no important regressions, failure rate not increased), `reject` (regression or not worth broad loss), `weak_evidence` (too noisy/small — e.g. below the game's required episode count), or `needs_data` (joins can't answer). Only promote when the rollback ref is known.
10. If promoting, submit: `coworld submit <policy_name:vN> --league <league_id> --auto-champion always --no-open-browser`, then confirm via `coworld submissions`/`memberships` that the active player, version id, and champion state are correct.
11. Record the loop so future agents need no chat history: persist the run record (base/candidate refs, source diff, hypothesis, eval request ids, artifact/log dirs, stdout/stderr findings, summary, verdict, rollback ref) plus per-eval artifacts.

## Eval Sizing (calculate episodes from variance, not habit)
Episode count is a calculation, not a fixed ladder. Two factors set how many completed episodes — and therefore how many XP requests — you need before a mean is trustworthy:
1. **Players per episode + role/seat asymmetry.** More players per game, and more dependence on a randomized role or seat, means higher per-game variance, so more games are needed to pin the mean.
2. **Single-episode score spread.** Games whose one-episode score can swing from large negative penalties to large win bonuses need far more samples than games with a tight score range.

Size by game shape:
- **2-player, low-variance games (e.g. cue-n-woo):** a handful of episodes suffices — ~6 ep diagnostic, 24-40 ep gate, only a few XP requests total.
- **8-player, role-asymmetric, high-variance games (e.g. crewrift and other social-deduction / hidden-role games):** each episode randomizes role (imposter vs crewmate) and seat and can score anywhere from large negatives to +100. Use **40-80+ completed games** per candidate/guardrail eval, spread across MANY XP requests, and disaggregate by role/seat. Never conclude promote/reject from <40 completed games — a decisive-looking small result is variance or a structural bug.
- **In between:** scale roughly with players-per-episode and observed score stdev; if the stderr of the mean is still large relative to the margin you care about, run more.

**The XP-request API is fast and effectively unlimited — treat sample size, not request count, as the constraint.** Fire as many requests as the needed sample size requires; do not under-size an eval out of caution about issuing "too many" requests.

**Concurrency caveat (observed):** the shared-Bedrock LLM quota, not the API, is the real bottleneck for LLM policies. Firing many high-concurrency requests at once can produce mass -100 timeouts where most/all players in an episode score -100. That is infra contention, NOT policy failure: do not count those episodes in the policy mean — retry them and reduce in-flight concurrency (small batches, a few in flight, or sequential) until episodes return clean. Distinguish the two failure shapes: only-our-slot at -100 with others normal = our policy/run-attribute bug; a whole-episode -100 sweep across the roster = contention.

## Specifications
- Deliverable: a recorded optimization run with base_policy_ref, candidate_policy_ref, source_diff, hypothesis, eval_requests, artifact_dirs, log_dirs, stdout_stderr_findings, summary, verdict, and rollback_policy_ref.
- Per eval, persist: `request-body.json`, `xp_request.json`, `episodes.json`, `replays/<episode_id>.json`, `artifacts/<episode_id>-<agent>.zip`, `logs/<ereq>-<agent>.stdout.txt`, `logs/<ereq>-<agent>.stderr.txt`, `summary.json`, `hypotheses.json`, `verdict.md`.
- Validation: the candidate's mean improves the target objective by the configured margin with acceptable stderr AND passes broad guardrails with no increase in failure rate; hosted logs show no unresolved policy crash. The eval must meet the game's required completed-episode count (Eval Sizing) before a promote/reject verdict; otherwise record `weak_evidence`. If any fails, do not promote — record `reject`/`weak_evidence`/`needs_data`.
- The uploaded policy version has a non-null `run` attribute and belongs to the intended active player.

## Advice and Pointers
- Eval ladder (climb only as far as needed, never promote from the bottom rungs): smoke (1 ep, confirm it starts and isn't -100) → diagnostic (pairwise, sized to variance) → candidate (vs champion/targets, sized to variance) → guardrail (broad top-N/random, sized to variance) → promotion. The per-rung episode counts come from Eval Sizing — e.g. diagnostic ~6 ep for cue-n-woo but tens of games for crewrift.
- XP request body is roster-based and the API rejects top-level `requester`/`opponents`/`rotate_seats`/`top_n` (422). Each `roster[].player` takes exactly one of `{ "policy_ref": "<name:vN>" }` or `{ "top_n": <int> }`; `slot: -1` means any seat.
- Hypothesis template:
  ```
  Observation:
  Causal guess:
  Evidence:
  Missing data:
  Change:
  Expected metric movement:
  Eval plan:
  Overfit risk:
  Rollback condition:
  ```
- Artifacts are the durable learning dataset; logs are crash truth. Always download with `--artifact` and always inspect the player's own stdout/stderr.
- A robust policy never crashes: it must return a legal fallback action on any LLM/provider/timeout failure. A crashing or timing-out policy scores 0 / -100 regardless of strategy.
- Objective realism: sometimes optimizing against a weaker but frequently-scored policy raises league score more than beating the single strongest opponent. Match the eval distribution to the objective.
- Game strategy belongs in the game-specific skill (`games/crewrift/skills/crewrift-eval-design`, `crewrift-optimization`); this playbook covers the platform loop.

## Forbidden Actions
- Do not promote/submit on a single pairwise win, a tiny eval, or a scalar score without variance and seat/role breakdowns.
- Do not conclude promote/reject below the game's required completed-episode count (Eval Sizing) — for high-variance games like crewrift that floor is ~40 games.
- Do not count whole-episode -100 contention sweeps as policy performance, and do not discard them silently — retry and record them as infra failures.
- Do not upload a policy without the correct `--run` argv, and do not skip verifying the `run` attribute afterward.
- Do not change policy behavior before reading live leaderboard/standings — never assume state from memory.
- Do not make broad refactors mid-loop; keep one scoped change per candidate so results are attributable.
- Do not act as the wrong player. Verify the active player before any upload or submit.
- Do not discard failed/anomalous episodes — they often reveal the real bug.
