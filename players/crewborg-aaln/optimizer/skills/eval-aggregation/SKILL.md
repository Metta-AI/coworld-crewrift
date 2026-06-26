---
name: eval-aggregation
description: Aggregate an eval set of episodes (player artifacts + replays) into a trustworthy verdict on the goal metric — filter taint first, aggregate per role and per version, report effect size with uncertainty, weight by the scoring objective, and run the anomaly trust gate before calling a result real. Use to decide whether a policy change is statistically better, not just numerically different.
---

# Eval Aggregation & Anomaly Verification

Turn a set of episodes into a **trustworthy** verdict on the goal metric. This
skill guards against the two ways aggregation lies: aggregating *wrong* (mixing
roles, counting tainted episodes) and aggregating *over-confidently* (calling
noise a win). To trust an eval you need **large N and awareness of how the policy
did in this exact situation before.** It is the back half of every loop
iteration; `eval-variance-design` is the front half.

## Step 1 — Build per-episode records

For each episode, load every per-slot player artifact (`replay-artifact-analysis`)
and, when needed, the ground-truth replay events + role/seat census. One record
per episode with: version(s) present, slots, per-version score, outcome,
roles/seats, and sampled behavioral metrics relevant to the hypothesis.

## Step 2 — Filter taint FIRST (before any mean)

Drop polluted episodes, and **count them** — the taint rate is itself a metric:

- a floor score (e.g. a large negative) on any slot usually means a disconnect /
  no-show (often infrastructure, e.g. a cold-node image-pull timeout), not
  gameplay;
- an artifact showing 0 ticks / no telemetry is a non-participating slot;
- including either craters the mean and **fabricates a regression**.

Keep only episodes that resolved to a real outcome with all arms participating.
**Always report the taint/disconnect rate** (clean vs total) — a high rate is a
deploy bug, not a policy result, and means "fix and re-run," not "compare."

## Step 3 — Aggregate per role AND per version (never just the aggregate)

Attribute each slot to its role/seat (via the seat→role/color census) and tally
wins separately:

- report **per-version, per-role** win rate plus mean score;
- a change that lifts the aggregate by helping one role while quietly tanking the
  other is a **false win**. A real example: a candidate looked fine on the broad
  field but **regressed head-to-head**, visible only once aggregation was split
  per-matchup and per-role.

Never declare a win/tie from a single aggregate scalar.

## Step 4 — Weight by the scoring objective

If the goal rewards beating frequent-weak opponents over rare-strong ones
(`eval-variance-design` Step 1), weight episodes by encounter frequency / point
yield, not uniformly. A flat win rate answers the wrong question when the league
pays per point. **State the weighting you used.**

## Step 5 — Effect size WITH uncertainty (not "better")

A rate is binomial: standard error ≈ `sqrt(p(1−p)/n)`. Report the delta and a
band, e.g. "role win rate 24% vs 39% (n=44 vs 41, ≈±7pp each) — outside the noise
band, real regression." If the delta is inside ~2 SE, the verdict is **"no
detectable change at this N"** — not a win, not a loss. Lead with the number and
its uncertainty.

## Step 6 — Anomaly checks (the trust gate)

Before accepting the verdict, run these. A failure here **invalidates** the
result:

1. **N sufficient?** Could this N even resolve the predicted effect? If not, the
   read is directional only.
2. **Taint rate within historical background?** A spike means infra contaminated
   the arm — fix and re-run, don't compare.
3. **History-aware: how did this policy do in this exact situation before?** Pull
   prior evals of the baseline against the *same* field/role. A "regression" may
   be a known high-variance matchup, or the baseline's own number may have been a
   lucky draw. Don't trust a single eval against a single historical point.
4. **Outcome reconciliation:** do the outcome counts (win/loss/draw) reconcile
   with the clean episode count? Unexplained "unknown" outcomes mean missing
   artifacts — fix collection before trusting behavioral metrics.
5. **Behavioral corroboration:** does the win/loss have a *mechanism* in the
   behavioral data (the rate/event the hypothesis predicted)? A score move with no
   behavioral correlate is suspect — likely variance or taint.

## Output

A short verdict block:

```text
clean N (and excluded/taint count)
per-version per-role win rate + mean score, each with uncertainty
goal-metric delta vs baseline, with its band
weighting used
anomaly checks: which passed / which failed
decision: signal | no signal | regression | re-run needed
```

Feed it straight into the loop's decide step and the run record, and to
`promotion-gate` for the ship/no-ship call.

## Integration

- **Consumes:** `replay-artifact-analysis` (per-episode records), `eval-variance-design`
  (the trust bar it enforces), `spatial-temporal-analysis` (behavioral mechanism).
- **Feeds:** `policy-hypothesis-loop` (verdict on the hypothesis), `promotion-gate`.
- **Game-specific grounding:** the taint floor, role/seat census, and which
  behavioral metrics matter are game-specific — see `games/<game>/skills/...`.
