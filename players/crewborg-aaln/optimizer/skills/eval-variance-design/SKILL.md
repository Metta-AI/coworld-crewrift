---
name: eval-variance-design
description: Design an eval set whose variance configuration is chosen for a specific optimization goal — opponent field, seat/role rotation, episode count, seeds — and state the trust bar (large enough N, taint filtering, identical config on both arms) before reading results. Use when deciding how many episodes, which opponents to eval against, or how to make an eval actually measure the goal.
---

# Eval-Variance Design (variance config for the goal)

"Run evals" is underspecified. The **same change can look like a win or a loss**
depending on the opponent field, the seat assignment, and the episode count. This
skill picks the eval configuration *for the stated goal* and sets the bar for
trusting the result. It is the front half of every loop iteration; the back half
(turning episodes into a verdict) is `eval-aggregation`.

**Precondition:** the optimization goal is pinned (one sentence, measurable
target, comparison set) per `base-optimizer-framework`. Every choice below is
derived from it.

## Step 1 — Derive the opponent field from the goal (the key decision)

The opponent set *is* the goal made concrete. Do not default to "one strong
opponent."

| Goal | Opponent field | Score objective |
|---|---|---|
| Win a league | the **current leaderboard field**, weighted by how often each policy is encountered | maximize total expected points across the field |
| Beat one named policy | that policy in every lobby, seats rotated | head-to-head win rate |
| Robustness | a **spread** (weak + median + strong) | win-rate floor across the spread |
| Regression guard | the previous champion on the old eval set | no drop vs baseline |

**The points/encounter asymmetry (state it explicitly).** Beating a *weak,
frequent* policy can yield more total points than beating the *single strongest,
rare* policy. Before locking the field, decide whether the goal rewards marginal
wins against common-weak opponents (optimize to crush them) or hard wins against
the best (optimize the tail). This changes which episodes you weight in
aggregation. Never silently collapse a multi-policy field to one representative
opponent.

## Step 2 — Control seating and roles

If the game assigns roles/seats from a seed (no fixed color→role link):

- **Rotate seats** across episodes so a version is not always one role or always
  in one map region. Putting multiple slots of each arm in the same lobby with
  rotating seats is a clean head-to-head shape.
- Plan to report **per-role** (e.g. attacker vs defender win rate), never only the
  aggregate — a change often helps one role and hurts the other, and the aggregate
  hides it. (Aggregation enforces this; design for it here by ensuring both roles
  are sampled enough.)

## Step 3 — Size N for the effect you expect

Win rate is a noisy binomial; a small move on a small set is inside the noise. Use
an explicit ladder and **state the expected effect size up front** — if the
planned N cannot resolve it, say so before running:

| Tier | Question | Rough N |
|---|---|---|
| **Smoke** | Does it run, no traceback, telemetry present? | 1–3 episodes |
| **Directional** | Which way does it move? | ~50 episodes/role-field |
| **Gate** | Ship or not? | ~100 clean episodes/matchup, often two matchups (the named target *and* the broad field) |

Never promote on smoke or directional-only evidence (see `promotion-gate`).

## Step 4 — Run the eval (identical config across arms)

Use the hosted XP/experience-request flow for the real comparison field
(`hosted-xp-evals`, `coworld-operations`): build/upload/submit the candidate, then
pull episodes (replays + per-slot artifacts + roster). For local head-to-head
iteration, use the game's local episode runner.

- **Pin every build** to a policy-version id and record it for the baseline and
  each variant — strong versioning lets you A/B several versions at once and is
  what the run record keys on.
- Run the baseline and the changed policy at the **identical** field / seating /
  N / trace level, or the comparison is confounded.

If a large request is unstable at the size boundary, split into smaller batches
and aggregate them under one logical eval set.

## Step 5 — State the trust bar before reading results

These are preconditions for believing the number; `eval-aggregation` enforces them
on the data:

1. **N is large enough** for the predicted effect (Step 3).
2. **Taint is filterable.** Disconnect / no-show episodes (e.g. a slot scoring a
   floor value from an image-pull timeout) must be excludable and **counted** —
   they pollute means and fabricate regressions. Decide the taint rule now.
3. **Same config both arms** (Step 4).
4. **History-aware.** Know how the baseline did *in this exact situation* before —
   a "regression" may be a known high-variance matchup.

## Quick reference

| Goal shape | Field | N | Report |
|---|---|---|---|
| League points | full current field, encounter-weighted | ~100/matchup | total points + per-role |
| Head-to-head | named opponent, rotated seats | 50–100 | win rate + per-role + score |
| Robustness | weak/median/strong spread | 50+/arm | win-rate floor |

## Output

The eval plan: goal, opponent field + weighting, seat/role rotation, N per tier,
pinned baseline + variant version ids, trace level, and the stated trust bar. Hand
it to `hosted-xp-evals` to run and to `eval-aggregation` to evaluate.

## Integration

- **Consumes:** `base-optimizer-framework` (the pinned objective).
- **Feeds:** `hosted-xp-evals` / `coworld-operations` (run it), `eval-aggregation`
  (the trust bar it enforces), `promotion-gate` (gate N).
- **Game-specific grounding:** the field, encounter weights, and taint floor are
  game/league specific — see `games/<game>/skills/...`.
