---
name: crewrift-eval-design
description: Crewrift-specific XP-request eval sizing and shaping. Use when running any hosted eval for crewrift policies — diagnostics, candidate verification, guardrails, or targeted runs against a specific opponent. Covers the 40-80 game minimum, leaderboard-variance rosters, targeted pairwise runs, and the role/seat/penalty breakdowns required before a verdict.
---

# Crewrift Eval Design

Crewrift is a high-variance, role-asymmetric, 8-seat social-deduction game. Per
game a policy can score from large negatives (vote-timeout `-10` per missed
meeting vote, `-1` per stuck-idle interval) to large positives (`+10` per kill,
`+100` win bonus, `+1` per task). Role (imposter vs crewmate) and seat are
randomized each game, and leaderboard opponents range from very strong to weak.

The consequence: **a handful of games tells you almost nothing.** Small evals are
liveness checks only. A single bad or good game is well within normal variance.

## Episode-Count Rules (non-negotiable)

- **Smoke / liveness:** 1-3 games. Only confirms the policy starts and is not
  crashing/timing-out (no `-100`). Never a promotion or rejection signal.
- **Candidate verification:** **40-80 games.** This is the floor for any
  candidate-vs-field or candidate-vs-champion comparison.
- **Guardrail before promotion:** **40-80 games** against a broad leaderboard
  roster.
- **Targeted pairwise (beat a specific opponent):** **40-80 games** weighted
  toward that opponent, in addition to the broad guardrail.

Do not conclude promote/reject from fewer than 40 completed games. If a result
looks decisive at 3 games, it is variance or a structural bug — diagnose the bug,
do not promote/reject on the score alone.

## Concurrency / Batching

Keep XP-request batches small (1-3 episodes per request) and run them
**sequentially**. Large concurrent batches overload shared Bedrock quota and
produce `-100` timeout penalties that look like policy failures but are infra
contention. To reach 40-80 games, submit many small sequential requests and
aggregate them into one logical eval set.

## Roster Shapes

Crewrift XP requests are roster-based with exactly 8 participants (8 seats). Use
two complementary shapes, both at 40-80 games:

### 1. Leaderboard-variance roster (default — measures expected league score)

Fill the 8 seats with a broad sample of current leaderboard policies so the
estimate reflects expected league score across the field, not one matchup.
Because role/seat are randomized, this also exercises both roles across all
seats. Pull the current standings live (`coworld memberships`/`leagues`) and
include a spread of strong + mid + weaker policies, refreshing the set as
versions change.

```json
{
  "coworld_id": "<coworld_id>",
  "roster": [
    { "player": { "policy_ref": "<our_policy:vN>" }, "slot": -1 },
    { "player": { "top_n": 7 }, "slot": -1 }
  ],
  "num_episodes": 40,
  "notes": "crewrift candidate guardrail: our_policy vs broad leaderboard field"
}
```

(Or enumerate 7 explicit current leaderboard policy refs instead of `top_n` when
you want a controlled field.)

### 2. Targeted pairwise (when the objective is to beat a specific opponent)

When defending/advancing against a specific threat, additionally weight the
roster toward that opponent so head-to-head performance is measured directly —
e.g. several copies of the threat policy alongside our policy, filling remaining
seats with leaderboard policies. Still run the broad guardrail too; never promote
on the targeted run alone.

```json
{
  "coworld_id": "<coworld_id>",
  "roster": [
    { "player": { "policy_ref": "<our_policy:vN>" }, "slot": -1 },
    { "player": { "policy_ref": "<threat_policy:vM>" }, "slot": -1 },
    { "player": { "policy_ref": "<threat_policy:vM>" }, "slot": -1 },
    { "player": { "policy_ref": "<threat_policy:vM>" }, "slot": -1 },
    { "player": { "policy_ref": "<filler_leaderboard_policy>" }, "slot": -1 }
  ],
  "num_episodes": 40,
  "notes": "crewrift targeted: our_policy vs threat_policy head-to-head"
}
```

## Required Breakdowns Before A Verdict

Crewrift score is dominated by role and by penalties, so always disaggregate —
a flat mean hides the real story:

- **Role-conditioned mean ± stderr:** imposter games vs crewmate games separately
  (imposters can win `+100`+kills; crewmates win/lose very differently). A policy
  can be perfect as imposter and broken as crewmate (or vice versa).
- **Seat-conditioned mean** across the 8 seats.
- **Penalty decomposition:** count vote-timeout penalties (`-10` each — means the
  policy failed to vote in a meeting) and stuck-idle penalties (`-1`/interval —
  means the crewmate froze/stopped pathing). A consistently negative score is
  almost always a structural penalty bug (missed votes / stuck), not strategy
  variance — inspect hosted stdout/stderr and replays for the meeting/vote and
  movement paths before interpreting strategy.
- **Win/tie/loss rate** by role.
- **Failure / `-100` count** (infra timeout vs real crash — check logs).

## Verdict Discipline

Promote only when, over 40-80 games, the candidate improves expected league
score (broad roster) with acceptable stderr, does not regress either role, does
not increase vote-timeout/stuck penalties or failure rate, and (if the objective
was a specific opponent) also wins the targeted run. Otherwise record
`reject` / `weak_evidence` / `needs_data`. A large negative mean driven by
vote-timeout or stuck penalties is a `reject` (or a bug to fix first), not noise.
