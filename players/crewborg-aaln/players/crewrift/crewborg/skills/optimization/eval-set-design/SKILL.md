---
name: eval-set-design
description: "Use to design and run a crewborg eval set whose variance config is chosen for a specific optimization goal: opponent field, seat rotation, episode count, seeds, and the trust requirements (large N, taint filtering). Trigger on 'run an eval set', 'how many episodes', 'which opponents to eval against', or 'design the eval for <goal>'."
---

# Eval-Set Design (variance config for the goal)

## Why this exists

"Run evals" is underspecified. The **variance config must be tested against the
optimization goal** — the same change can look like a win or a loss depending on
the opponent field, seat assignment, and episode count. This skill picks that
config and the bar for trusting the result.

**Announce at start:** "Designing the eval set for the goal: I'll pick the
opponent field, seat rotation, episode count, and seeds so the result actually
measures the goal, then state the trust bar (N + taint filtering)."

## Step 1 — Derive the field from the goal (the key decision)

The opponent set *is* the goal made concrete. Do not default to "one strong
opponent."

| Goal | Opponent field | Score objective |
|---|---|---|
| Win the league | the **current leaderboard field** (all competing policies, weighted by how often each is encountered) | maximize total points across the field |
| Beat one named policy | that policy in every lobby, seats rotated | head-to-head win rate |
| Robustness | a *spread* (weak + median + strong) | win rate floor across the spread |

**The points asymmetry (state it explicitly).** Beating a *weak, frequent* policy
can yield more total points than beating the *single strongest, rare* policy.
Before locking the field, decide whether the goal rewards marginal wins against
weak opponents (optimize to crush them) or hard wins against the best (optimize
the tail). This changes which episodes you weight in aggregation. See
`t.policy-regression`: never silently collapse the field to one representative
policy.

## Step 2 — Control the seating and roles

Crewrift assigns roles from a **time-based seed** (no color→role correlation), so:

- **Rotate seats** across episodes so a version is not always crewmate or always
  in one map region. The `v3_vs_v8` eval put 4 slots each in the same lobby with
  rotating seats — copy that shape for head-to-head.
- Report **per-role** (imposter win rate vs crewmate win rate), never only the
  aggregate. A change often helps one role and hurts the other; the aggregate
  hides it.

## Step 3 — Size N for the effect you expect

Win rate is a noisy binomial. A 5-point move on 50 episodes is inside the noise.
Rule of thumb:

- **Smoke** (does it run, no traceback, telemetry present): 1–3 episodes.
- **Directional read**: 50 episodes/role-field.
- **Gate decision** (ship or not): ~100 clean episodes per matchup, often two
  matchups (e.g. truecrew head-to-head *and* top-ranked field), as in
  `FINDINGS_v4.md`.

State the expected effect size up front; if the planned N can't resolve it, say
so before running.

## Step 4 — Run the eval

Hosted league eval (the real comparison field) — upload the build, submit to the
league/pool, then pull episodes:

```sh
# build + upload + submit (records the pinned policy-version id — keep it!)
players/crewrift/crewborg/build.sh
coworld upload-policy crewborg-aaln:latest --name crewborg-aaln   # [+ --use-bedrock]
coworld submit crewborg-aaln:<tag> --league <crewrift-league-id>

# pull the episodes crewborg just played (replays + per-slot traces + roster)
players/crewrift/crewborg/scripts/fetch_episodes.sh -n 100
```

Local head-to-head iteration uses `coworld run-episode` (see the AGENTS.md
workarounds: re-download the manifest + delete `slots.items.properties.name`,
pass `--run .../coworld/entrypoint.sh`, patch `certification.game_config`).

**Pin every build.** Record the `pv` (policy-version id) for the baseline and
each variant — strong versioning lets you A/B 4–5 versions at once and is what
the findings doc keys on (`FINDINGS_v4.md` §0 version chain).

## Step 5 — State the trust bar before reading results

These are preconditions for believing the number; `eval-aggregation` enforces
them on the data:

1. **N is large enough** for the effect (Step 3).
2. **Taint filtered.** Disconnect / no-show episodes score the whole lobby −100
   (a cold-node image-pull timeout, see `FINDINGS_v4.md` §2). Exclude episodes
   with any slot at −100 or an artifact showing 0 ticks — and **report the
   disconnect rate** itself, because −100s pollute leaderboard means.
3. **Same config both arms.** Baseline and changed policy run the *identical*
   field/seating/N, or the comparison is confounded.
4. **History-aware.** Know how the baseline did *in this exact situation* before
   (`eval-aggregation` anomaly check) — a "regression" may be a known noisy
   matchup.

## Quick reference

| Goal shape | Field | N | Report |
|---|---|---|---|
| League points | full current field, encounter-weighted | ~100/matchup | total points + per-role |
| Head-to-head | named opponent, rotated seats | 50–100 | win rate + per-role + score |
| Robustness | weak/median/strong spread | 50+/arm | win-rate floor |

## Integration

- **Feeds:** `eval-aggregation` (aggregation + anomaly checks).
- **Pairs with:** `t.policy-regression`, `tr.research-partner`.
- **Grounded in:** `scripts/fetch_episodes.py`, `episode_data/FINDINGS_v4.md`,
  `episode_data/eval_2026-06-11_v3_vs_v8/analyze.py`.
