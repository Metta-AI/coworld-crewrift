# Optimization skills — the crewborg optimizer & eval loop

This folder formalizes the **policy optimization loop** as a set of composable
agent skills. The optimizer is **not a model** — it is a collection of tools wired
into a loop that keeps going:

```
                ┌─────────────────────────────────────────────────────────────┐
   goal ───────▶│  run eval set → collect player data + match to replays →     │
                │  find patterns → generate hypothesis → instrument + change → │
                │  re-run eval set → verify signal (+ anomaly checks) → repeat │
                └─────────────────────────────────────────────────────────────┘
```

Start with **`optimizer-loop`** — it is the orchestrator and routes each phase to
a focused sub-skill below. Every decision in the loop is derived from a pinned
**optimization goal** (e.g. "win the league" vs "beat truecrew head-to-head"),
because the goal determines the eval field, the variance config, the metric, and
what counts as "better."

## Skills

| Skill | Loop phase | What it owns |
|---|---|---|
| `optimizer-loop` | all | orchestrator: pin the goal, sequence the phases, decide + loop |
| `eval-set-design` | run evals (steps 1, 5) | variance config **for the goal** (field, seat rotation, N, seeds), trust bar |
| `artifact-capture` | collect data (2) | save/download player artifacts; the indexed `trace.db` schema; the "join the tables" reader |
| `replay-reconstruction` | collect data (2) | re-simulate the `.bitreplay` to ground truth; join to the artifact on `server_tick` |
| `eval-aggregation` | verify (5, 6) | aggregate per role/version, effect size **with uncertainty**, taint + anomaly checks |
| `pattern-toolkit` | find patterns (3) | cheesy patterns, heuristics, after-the-fact visualizations; the spatial-temporal gap |
| `hypothesis-generation` | hypothesize (3) | pattern → falsifiable, pre-registered hypothesis with a predicted metric move |
| `data-collection-design` | instrument + change (4) | decide what to log to validate the hypothesis; wire it at the right seam |

## How this maps to the workflow notes

- **What data we need from the player** → `data-collection-design` (Step 1).
- **Reconstruct game info from replay, matched to the player data structure** →
  `replay-reconstruction` (the `server_tick` join).
- **Saving player artifacts** → `artifact-capture`.
- **Large eval set with goal-tuned variance** (incl. the points asymmetry:
  beating the weak/frequent policy can pay more than beating the rare/strong one)
  → `eval-set-design`.
- **Correctly aggregating the eval set of players + replays** → `eval-aggregation`.
- **Toolkit of pattern/heuristic skills to shape data so a pattern shows** →
  `pattern-toolkit`.
- **Generate hypotheses** → `hypothesis-generation`.
- **Hypothesis-first data collection; visualizations written after the fact** →
  baked into `hypothesis-generation` + `data-collection-design` + `pattern-toolkit`.
- **Verify the change is statistically better + find anomalies (need large N,
  awareness of past performance in specific situations)** → `eval-aggregation`.
- **Open gap: spatial/temporal analysis isn't rigorous; an adaptive approach would
  help** → flagged in `pattern-toolkit` § "Spatial-temporal gap".

## Grounding

These skills are not generic — they encode the real crewborg toolchain:

- `artifact.py` — the SQLite episode artifact (`trace.db` schema, `server_tick`).
- `scripts/fetch_episodes.py` / `fetch_episodes.sh` — pull hosted episodes.
- `scripts/replay_analysis.py` + `docs/replay-analysis.md` — expand `.bitreplay`,
  join on `server_tick`.
- `episode_data/eval_2026-06-11_v3_vs_v8/analyze.py` — the aggregation pattern.
- `episode_data/FINDINGS_v4.md` — a full loop iteration (incl. a no-show
  course-correction and a failed deployment gate).
- `docs/designs/suspicion.md` §6 — offline LR fitting, a worked hypothesis loop.
- `design.md` §7/§10/§12 — the policy's tunable surface.

## Pairs with (repo-wide skills)

`tr.research-partner` (hypothesis/evidence/uncertainty framing),
`t.policy-regression` (don't collapse the eval field to one policy).
