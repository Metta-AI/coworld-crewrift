<!--
Mirror of the Devin org playbook "Monitor a Coworld Leaderboard and Defend/Advance Our Policy Rank".
Macro: !defend_leaderboard | playbook-6117fb018ee64b56a8bdeece565a2a43
Canonical source: https://app.devin.ai/settings/playbooks/6117fb018ee64b56a8bdeece565a2a43
Designed to be attached to a recurring schedule (one schedule per game).
Keep this file in sync with the live playbook; the live version takes precedence if they drift.
-->

# Monitor a Coworld Leaderboard and Defend/Advance Our Policy Rank

**Macro:** `!defend_leaderboard`

## Overview
Run one **leaderboard-defense check** for a Coworld game: read the live leaderboard, detect policies that are beating—or are on track to beat—our player's policy, and, only when a real threat is found, launch an evidence-first optimization loop to overtake (or proactively stay ahead of) them. This playbook is **game-agnostic**: it is parameterized by game / league / player and is meant to be attached to a recurring schedule (one schedule per game). Each run is cheap when there is no threat and escalates to a full optimization loop when there is.

The cycle is: snapshot → detect threats → decide → (optimize if threatened) → record → (update baseline snapshot).

## What's Needed From User / Schedule Prompt
These come from the attached schedule's prompt (or, if missing, are discovered/asked):
- `game` — here: `crewrift`.
- `league_id` — target league. If unknown, discover with `coworld leagues --json` and match on game name.
- `player_id` (or player name) — the player we are defending. The loop acts as exactly this player.
- `policy_name` — our policy's name (`crewborg-aaln`). If unknown, derive from our active submissions.
- Objective — default: **be and stay #1 by expected league score**. Override allowed (e.g. top-3 is fine).
- Thresholds (defaults in Specifications) — gap margin that counts as a threat, riser delta, episode budget per check.
- Permissions — whether this run may upload/submit a new policy, or only diagnose + report. Default: may run the full loop and submit if the promotion gate passes.
- Auth: `SOFTMAX_USER_API_TOKEN` in env (verify `coworld status`); Bedrock via `--use-bedrock` for LLM policies.

## Procedure
1. **Collect live context (never trust memory).** `coworld player use <player_id>`, then read `coworld leagues <league_id> --json`, `coworld memberships --league <league_id> --active-only --json`, and `coworld submissions --league <league_id> --mine --json`. Record: our active policy ref + version id, our current rank and score, and the full ranked standings.
2. **Load the previous snapshot.** Read the last saved leaderboard snapshot for this league from the persisted monitor log (knowledge note "Leaderboard Monitor State — <game>", or the run-record store). If none exists, treat this as the first run (baseline only).
3. **Detect threats.** Compare live standings vs. snapshot and flag any of:
   - **Ahead of us:** any policy ranked above us (we are not #1) — always a threat unless objective explicitly allows.
   - **Closing the gap:** any policy whose score is within `gap_margin` of ours (default 5%), even if ranked below.
   - **Recent riser:** any policy that gained more than `riser_delta` rank/score since the last snapshot.
   - **New / updated version:** any policy version submitted since the last snapshot (new `policy_version_id` for an existing or new competitor).
   Build a ranked threat list (most dangerous first) with: policy ref, version id, owner, current score, delta vs. us, delta vs. last snapshot.
4. **Decide.** If the threat list is empty (and we hold the objective, e.g. #1), record a short "no action" note, update the snapshot, and STOP — this is the cheap common path. Otherwise pick the single highest-priority threat (or a small target set) and proceed to optimize.
5. **Optimize against the threat(s).** Run the standard optimization loop using playbook **`!optimize_policy`** (`Run an Agent Optimization Loop for a Coworld Policy`), passing the threat policies as the eval targets, our current champion as the rollback baseline, and the per-check episode budget. Follow that playbook's eval ladder, hypothesis, variance, and promotion gate exactly. Keep XP eval batches small (1–3 episodes per request, run sequentially) to avoid shared-Bedrock contention that causes -100 timeout penalties under high concurrency.
6. **Promote or reject** per `!optimize_policy`'s promotion gate. Only submit (`coworld submit ... --auto-champion always --no-open-browser`) when the candidate clears the gate (beats target + broad guardrail, no important regression, fallback proven, rollback ref known). Never submit on a single pairwise win or a within-variance result.
7. **Record the monitoring run** so future runs and agents need no chat history: timestamp, league, our rank/score, the threat list, action taken (no-op / optimized / promoted / rejected), candidate ref + verdict, and eval request ids. Persist it to the run-record store and update the "Leaderboard Monitor State — <game>" note with the new snapshot (standings + version ids + timestamp).
8. **Notify** with a concise summary: our rank, top threats, what was done, and links (policy page, XP requests). If a new policy was promoted, state the before/after expected-score evidence and the rollback ref.

## Specifications
- **Default thresholds:** `gap_margin = 5%` of our score; `riser_delta` = climbed ≥1 rank OR +5% score since last snapshot; treat any version newer than the last snapshot as a candidate threat. Objective default: hold rank #1 by expected league score.
- **Episode budget per check:** smoke (1) for liveness, then diagnostic (≤6, run as small sequential batches) only when a threat warrants a full loop. Do not spend candidate/guardrail-sized batches on a no-threat run.
- **Snapshot record (persist each run):** `{ checked_at, league_id, our_policy_ref, our_version_id, our_rank, our_score, standings: [{policy_ref, version_id, owner, rank, score}], threats: [...], action, candidate_ref, verdict, xp_request_ids }`.
- **Idempotency:** if an optimization loop for the same threat was already run very recently with a recorded verdict and the threat is unchanged (same version id), do not re-run — reference the prior verdict instead.
- **Concurrency safety:** keep XP batches at 1–3 episodes and prefer sequential requests; large concurrent batches overload shared Bedrock quota and yield -100 timeouts that look like policy failures but are infrastructure contention.

## Advice and Pointers
- This is a **defense/advance monitor**, not a per-run rewrite. Most runs should end at step 4 with "no action." Spending a full optimization loop every run wastes budget and risks churning a healthy champion.
- "Beating us" is about **expected league score**, not just head-to-head. A weaker but frequently-scored policy can hurt our rank more than the single strongest opponent — match eval targets to the objective (see optimizer AGENTS.md and the base-optimizer-framework skill).
- A threat that is a **new version of an existing competitor** is high-signal: strategies often change between versions. Re-profile it rather than assuming the old behavior.
- Always read live standings before acting; never assume yesterday's leaderboard. Verify the active player before any upload/submit — acting as the wrong player corrupts the league state.
- The actual policy change, hypothesis, eval shapes, and promotion gate all live in `!optimize_policy` and the game-specific strategy skill (`games/crewrift/skills/`). This playbook only decides **whether** and **against whom** to optimize.
- **Onboarding a new game:** create a new recurring schedule that attaches this playbook and fills in `game`, `league_id`, `player_id`, and `policy_name` for that game. No playbook change is needed per game — the parameters live in the schedule prompt.

## Forbidden Actions
- Do not run a full optimization loop (or submit a policy) when no threat crosses the thresholds — record "no action" and stop.
- Do not promote/submit on a single pairwise win, a tiny eval, or a scalar score without variance and seat/role breakdowns.
- Do not run large concurrent XP batches; keep them small and sequential to avoid -100 timeout penalties from Bedrock contention.
- Do not upload a policy without the correct `--run` argv (and `--use-bedrock` for LLM policies), and do not skip verifying the `run` attribute afterward.
- Do not act as the wrong player, and do not change behavior before reading the live leaderboard.
- Do not discard failed/anomalous episodes — inspect player stdout/stderr first; a timeout/traceback is a runtime bug or infra-contention signal, not strategy noise.
