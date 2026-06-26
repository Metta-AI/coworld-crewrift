# Optimizing crewborg-aaln for the Crewrift Tournament

**You are optimizing `crewborg-aaln`** — Aaron Landy's policy in the **Crewrift**
league (`league_605ff338-0a2e-4e62-aeda-559df9a9198f`), player Aaron
(`ply_630a768f-d623-44b2-80fa-36968d6fa75a`). This folder is everything an agent
needs to take the policy from its current state to the top of the leaderboard
through an evidence-first loop. Start here.

## Start here (read in order)

1. **`guide/SKILL.md`** — the crewborg-aaln particulars: cognitive-stack
   architecture, the variant/experiment env flags, the league/Observatory IDs
   (centralized here), build/run/test commands, the file-to-edit map, the trace
   format. **Read this first.**
2. **`CREWBORG_INSIGHTS.md`** — what crewborg already *knows*: measured opponent
   tells, the eval traps it has hit (v4 false-win, −100 taint), variant-flag
   tradeoffs, the tunable surface. **The hard-won knowledge.**
3. **`playbooks/optimize-policy.md`** — the one-loop procedure to follow.
4. **`games/crewrift/skills/`** — `crewrift-optimization` (commands, `trace.db`
   schema, `server_tick` replay join, `−100` taint rule, map facts) and
   `crewrift-eval-design` (opponent field, role/seat census, 40–80 game floor).
   **The canonical game grounding.**
5. **`skills/`** — the game-agnostic optimizer methodology + the policy-specific
   `crewborg-optimization/crewborg-suspicion-tuning`; pull a focused skill per
   loop step (routing below).

## The workflow

```
setup ─▶ understand policy ─▶ run hosted evals ─▶ mine variance/opponents
      ─▶ form hypotheses ─▶ edit ─▶ eval ─▶ promotion gate ─▶ submit to league
                                   ▲                                  │
                                   └────────── record + repeat ───────┘
```

| Step | Do | Skill / file |
|---|---|---|
| **Setup** | `coworld player use ply_630a768f-…`; read live league standings, memberships, submissions. Verify active player = Aaron. Never trust leaderboard state from memory. | `skills/coworld-operations`, `guide/SKILL.md` (IDs) |
| **Understand policy** | Architecture, env flags, where behavior lives. | `guide/SKILL.md`, `players/crewrift/crewborg/design.md` |
| **Run hosted evals** | Build → upload → submit; create XP requests (40–80 completed games, small sequential batches). Persist request bodies, episodes, replays, artifacts, stdout/stderr. Triage logs FIRST. | `skills/hosted-xp-evals`, `skills/eval-variance-design`, `games/crewrift/skills/crewrift-eval-design` |
| **Mine variance / opponents** | Fetch episodes; join replay × artifact on `server_tick`; reconstruct behavior; profile opponents; rank VP-weighted hypotheses. | `skills/replay-artifact-analysis`, `skills/opponent-strategy-mining`, `skills/replay-variance-miner`, `skills/spatial-temporal-analysis` |
| **Form hypotheses** | One falsifiable hypothesis per candidate; instrument if artifacts can't decide it. The crewmate suspicion model is the prime fittable lever. | `skills/policy-hypothesis-loop`, `skills/data-collection-design`, `skills/player-artifacts`, `skills/crewborg-optimization/crewborg-suspicion-tuning` |
| **Edit** | One scoped change (or flag flip). Map it to the smallest file. | `guide/SKILL.md` "Where to edit" table |
| **Eval** | Re-run vs target + previous champion + broad guardrail. Aggregate per role/seat with stderr; taint-filter `−100`. | `skills/eval-aggregation`, `games/crewrift/skills/*` |
| **Promotion gate** | Promote only on improved expected league score + passed guardrails + no role regression + known rollback. | `skills/promotion-gate` |
| **Submit + record** | `coworld submit … --auto-champion always`; record the run so the next agent needs no chat history. | `playbooks/optimize-policy.md` |

To **keep crewborg-aaln at #1 automatically**, attach
`playbooks/defend-leaderboard.md` to a recurring schedule (cheap when there's no
threat; escalates to the optimize loop only when threatened) and drive it with
`skills/continuous-optimizer`.

## Crewrift non-negotiables (why this game is special)

The detail lives in the game skills + `CREWBORG_INSIGHTS.md`; the rules you must
never violate:

- **High variance, role-asymmetric, 8 seats.** Never promote/reject on <40
  completed games; disaggregate by role and seat. (`crewrift-eval-design`)
- **`−100` lobby taint.** Disconnect/no-show scores the whole lobby `−100`
  (usually infra). Exclude tainted episodes, report the rate, keep batches small
  and sequential. (`crewrift-optimization` taint rule)
- **Scripted-first, never crash.** The deterministic vote/fallback path must
  always cast a legal action; LLM meetings sit on top behind a circuit breaker.
  (`scripted-policy-techniques`)
- **Flags are the first experiment surface.** `BE_DUMB`,
  `CREWBORG_LLM_MEETINGS`, `CREWBORG_DICK_MODE` flips are distinct candidates —
  pin and match the flag set across eval arms. (`guide/SKILL.md`,
  `CREWBORG_INSIGHTS.md` §4)

## Layout

See `README.md` for the folder tree and skill-copy rationale. The skills here are
self-contained **copies**; the full upstream library lives at
`../../../../../optimizer-skills` if you need one not copied here (e.g.
`seed-a-new-policy`, `cogtext-session-memory`, the offline `harness/`).
