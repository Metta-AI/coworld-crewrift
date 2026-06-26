---
name: eval-aggregation
description: "Use to correctly aggregate a crewborg eval set of player artifacts + game replays into win rates, effect sizes with uncertainty, and to run anomaly checks before trusting the result. Trigger on 'aggregate the eval', 'is this change statistically better', 'compute win rates', 'did it actually improve', or 'are these eval results trustworthy'."
---

# Eval Aggregation & Anomaly Verification

## Job

Turn a set of episodes (each = player artifacts + a replay) into a **trustworthy**
verdict on the goal metric. Two failure modes this guards against: aggregating
*wrong* (mixing roles, counting tainted episodes) and aggregating *over-confidently*
(calling noise a win). To trust an eval you need **large N and awareness of past
policy performance in specific situations.**

**Announce at start:** "Aggregating the eval set: I'll filter taint, compute the
goal metric per role/version with uncertainty, then run the anomaly checks before
calling it real."

## Step 1 — Build per-episode records

For each episode, load every crewborg per-slot artifact (`artifact-capture`) and,
when needed, the ground-truth replay events + role-by-color census
(`replay-reconstruction`). One record per episode with: version(s) present,
slots, per-version score, outcome, roles-by-color, and sampled behavioral metrics
(tasks, kills, votes-by-target-role). Mirror
`eval_2026-06-11_v3_vs_v8/analyze.py`.

## Step 2 — Filter taint FIRST (before any mean)

Drop polluted episodes, and count them — they are themselves a metric:

```python
def tainted(r):
    s = [v for v in (r["score_v3"], r["score_v8"]) if v is not None]
    return not s or min(s) < 0          # any slot at -100 = disconnect/no-show
clean = [r for r in records if not tainted(r) and r["outcome"] in ("imps_win", "crew_wins")]
```

A −100 means a slot disconnected / never joined — usually a cold-node image-pull
timeout, not gameplay (`FINDINGS_v4.md` §2). Including it craters the mean and
fabricates a regression. **Always report the disconnect rate** (clean vs total);
a high rate is a deploy bug, not a policy result.

## Step 3 — Aggregate per role AND per version (never just the aggregate)

Win = `(role == imposter and outcome == imps_win)` or
`(role == crewmate and outcome == crew_wins)`, attributed via slot → color → role:

```python
for r in clean:
    for tag in versions:
        for pos in r["slots"][tag]:
            role = r["roles_by_color"].get(SLOT_COLORS[pos])
            if role is None: continue
            n[tag][role] += 1
            if won(role, r["outcome"]): w[tag][role] += 1
# report: overall w/n, imp w/n, crew w/n, mean score — PER VERSION
```

Report imposter and crewmate win rate **separately** plus mean score. A change
that lifts the aggregate by helping crewmates while quietly tanking imposters is a
false win — the v4 package looked fine on the top-ranked field (42% vs 34%) but
**regressed** head-to-head (24% vs 39%); only per-matchup/per-role aggregation
surfaced it.

## Step 4 — Weight by the scoring objective

If the goal rewards beating frequent-weak opponents over rare-strong ones
(`eval-set-design` Step 1), weight episodes by encounter frequency / point yield,
not uniformly. A flat win rate answers the wrong question when the league pays
per-point. State the weighting you used.

## Step 5 — Effect size WITH uncertainty (not "better")

Win rate is binomial: standard error ≈ `sqrt(p(1−p)/n)`. Report the delta and a
band, e.g. "imposter win rate 24% vs 39% (n=44 vs 41, ≈±7pp each) — outside the
noise band, real regression." If the delta is inside ~2 SE, the verdict is **"no
detectable change at this N"** — not a win, not a loss. Lead with the number and
its uncertainty (`tr.research-partner`).

## Step 6 — Anomaly checks (the trust gate)

Before accepting the verdict, run these. A failure here invalidates the result:

1. **N sufficient?** Could this N even resolve the predicted effect? If not, the
   read is directional only.
2. **Taint / disconnect rate** within the historical background (v3 ran 5–12%)?
   A spike means an infra problem contaminated the arm — fix and re-run, don't
   compare.
3. **History-aware: how did this policy do in this exact situation before?**
   Pull prior evals of the baseline against the *same* field/role. A "regression"
   may be a known high-variance matchup, or the baseline's own number may have
   been a lucky draw. Don't trust a single eval against a single historical point.
4. **Outcome sanity:** do `imps_win + crew_wins + draws` reconcile with episode
   count after taint removal? Unexplained "unknown" outcomes mean missing
   artifacts — fix collection before trusting behavioral metrics.
5. **Behavioral metric corroboration:** does the win/loss have a *mechanism* in
   the behavioral data (kills/ep, tasks/ep, vote accuracy)? A score move with no
   behavioral correlate is suspect — likely variance or taint.

## Output

A short verdict block: clean N (and excluded count), per-version per-role win
rate + mean score with uncertainty, the goal-metric delta vs baseline with its
band, which anomaly checks passed, and the decision (**signal / no signal /
regression / re-run needed**). Feed this straight into the loop's Step 6 and the
findings doc.

## Integration

- **Consumes:** `artifact-capture`, `replay-reconstruction`, `eval-set-design`
  (the trust bar it enforces).
- **Pairs with:** `tr.research-partner` (uncertainty-first reporting),
  `t.policy-regression` (per-policy-class reporting).
- **Grounded in:** `episode_data/eval_2026-06-11_v3_vs_v8/analyze.py`,
  `episode_data/FINDINGS_v4.md`.
