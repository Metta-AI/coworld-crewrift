# crewborg-aaln/optimizer

Self-contained optimizer workspace for **crewborg-aaln** — everything an agent
needs to optimize this policy for the **Crewrift** tournament/league
(`league_605ff338-0a2e-4e62-aeda-559df9a9198f`), without loading the upstream
`optimizer-skills` library.

**Entry point:** read **[`AGENTS.md`](AGENTS.md)** first — it orients you and
lays out the `setup → understand → eval → mine → hypothesize → edit → eval →
gate → submit` workflow. Then read **[`guide/SKILL.md`](guide/SKILL.md)** for the
crewborg-aaln specifics, then follow
**[`playbooks/optimize-policy.md`](playbooks/optimize-policy.md)**.

## Layout

| Path | What |
|---|---|
| `AGENTS.md` | Orientation + the optimize workflow + Crewrift non-negotiables. **Read first.** |
| `guide/SKILL.md` | The policy-specific guide: cognitive-stack architecture, env/variant flags, league + Observatory IDs (**centralized here**), build/run/test, the file-to-edit map, trace format. **Authored for this policy.** |
| `CREWBORG_INSIGHTS.md` | Hard-won tournament knowledge: measured opponent tells, eval traps, variant-flag tradeoffs, the tunable surface, tooling. |
| `playbooks/optimize-policy.md` | One evidence-first optimization loop (`!optimize_policy`). |
| `playbooks/defend-leaderboard.md` | Scheduled #1-defense monitor (`!defend_leaderboard`). |
| `games/crewrift/` | Crewrift-specific skills (`crewrift-optimization`, `crewrift-eval-design`) + `MANIFEST.md`. **Canonical game grounding.** |
| `skills/crewborg-optimization/` | Policy-specific knob: `crewborg-suspicion-tuning` (fit the suspicion model from replays). |
| `skills/` | Game-agnostic optimizer methodology (16 skills), pulled per loop step. |

## Skills (copied from `../../../../../optimizer-skills`, self-contained)

**Game-specific (highest priority):** `games/crewrift/skills/crewrift-optimization`
(scoring, role census, `trace.db` schema + `server_tick` join, `.bitreplay`
reconstruction, fetch/build/submit, trace flags, the −100 taint, map/nav facts)
and `crewrift-eval-design` (40–80 game floor, roster shapes, role/seat/penalty
breakdowns).

**Core process (almost every loop):** `base-optimizer-framework`,
`continuous-optimizer`, `policy-hypothesis-loop`, `hosted-xp-evals`,
`eval-variance-design`, `eval-aggregation`, `promotion-gate`,
`replay-artifact-analysis`, `opponent-strategy-mining`, `coworld-operations`.

**Supporting:** `data-collection-design`, `player-artifacts`,
`scripted-policy-techniques`, `replay-variance-miner`.

**Specialty (map + momentum nav):** `spatial-temporal-analysis`, `map-navigation`.

**Policy-specific:** `crewborg-optimization/crewborg-suspicion-tuning`.

**Deliberately not copied** (upstream if needed): `seed-a-new-policy`,
`cogtext-session-memory`, the offline `harness/`, `templates/`, and the other
games.

## Provenance

Skills consolidated from `Metta-AI/optimizer-skills`. The Crewrift game skills
and the optimize/defend playbooks were copied largely intact; `guide/SKILL.md`
and the entry-point `AGENTS.md`/`README.md` were authored for crewborg-aaln.
Excludes `.git`, `__pycache__`, episode data, and caches.
