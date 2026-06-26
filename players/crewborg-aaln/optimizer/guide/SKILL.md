---
name: crewborg-aaln-guide
description: The policy-specific optimizer guide for crewborg-aaln — Aaron's Crewrift league policy. Read this first when optimizing crewborg-aaln. Covers the cognitive-stack architecture, the variant/experiment env flags, the Crewrift league + Observatory IDs, build/run/test commands, the exact files to edit when improving behavior (suspicion, mode selector, vote policy, modes, navigation), and the trace/debug format. Pair with games/crewrift skills for game mechanics and skills/ for the optimizer methodology.
---

# crewborg-aaln — Policy Optimizer Guide

You are optimizing **crewborg-aaln**, Aaron Landy's policy in the **Crewrift**
league (an Among Us–style social-deduction game: crewmates do tasks and vote,
imposters kill/vent/blend). This is the particulars of *this* policy. For game
mechanics and the optimizer loop, see the routing at the bottom.

The policy directory is the parent of this folder:
`players/crewborg-aaln/` — a self-contained coplayer (vendored
`players/crewrift/crewborg/` + `players/player_sdk/`, plus `Dockerfile`,
`coplayer_manifest.json`, `requirements.txt`, `README.md`). Verify any constant
or path against source before relying on it — Crewrift is in active development.

## League & Observatory identity (collect live before acting)

| Field | Value |
|---|---|
| League | Crewrift — `league_605ff338-0a2e-4e62-aeda-559df9a9198f` |
| Policy | `crewborg-aaln` (current uploaded tag `:v2`; vote-deadline fix in `:v3`) |
| Policy ID | `3154885a-3285-4888-8757-92cb053a078d` |
| Policy version ID (v2) | `804c2e83-4daa-4cf0-b538-700b5f542c8f` |
| Container image (v2) | `img_3f2026ab-199f-4528-b03f-5db0f1fe6561` (`docker-img`) |
| Player | Aaron — `ply_630a768f-d623-44b2-80fa-36968d6fa75a` |
| Run argv | `python -m players.crewrift.crewborg.coworld.policy_player` |

**Never trust these from memory.** Read live standings before selecting eval
targets: `coworld player use ply_630a768f-d623-44b2-80fa-36968d6fa75a`, then
`coworld leagues league_605ff338-0a2e-4e62-aeda-559df9a9198f --json`,
`coworld memberships --league <id> --active-only --json`,
`coworld submissions --league <id> --mine --json`. Verify the active player is
Aaron before any upload/submit. (See `skills/coworld-operations`.)

## Architecture — the cognitive stack

crewborg plugs Crewrift-specific code into the Player SDK's two-loop runtime.
Each tick (24 Hz) the runtime runs, under one shared-memory write lock:

```
perceive(obs) → update_belief(belief, percept) → update_event_log → update_suspicion
   → update_agent_tracking → strategy.decide → mode.decide(belief) → resolve_action → wire Command
```

This is the **perception → world state → integration → cognition → action**
stack:

- **Perception** (`perception/`, `types.py:perceive`) — decode the binary
  Sprite-v1 scene tables into labelled entities + world coords. No computer
  vision; the protocol is structured. Raw pixels never enter belief.
- **World state** (`types.py:update_belief`, `Belief`) — the persistent world
  model: self, map/nav, roster-by-color, perception tape, bodies, chat,
  social history, tasks, phase, voting, suspicion. **Belief is the only
  interface the strategy and modes see.**
- **Integration** (fast loop, after belief): `strategy/event_log.py`
  (per-player observation intervals), `strategy/suspicion.py` (Bayesian
  P(imposter)), `agent_tracking.py` (reachability-disc location estimates +
  occupancy grid).
- **Cognition** — two tiers:
  - **Strategy** (`strategy/rule_based.py`) = the *mode selector*: pure rules
    over belief → a `ModeDirective` (which mode is active), run every tick.
  - **Mode** (`modes/`) = a behavioral stance → one symbolic `Intent` per tick.
- **Action** (`action.py:resolve_action`) — stateful executor: `Intent` → wire
  `Command`. All pathing (`nav.py`), momentum, button/cursor timing, and chat
  buffering live here. Modes never touch buttons or routes.

Invariants: modes emit symbolic intents only; the inner loop never blocks on the
strategy (default directive + TTLs keep it live). Full spec:
`players/crewrift/crewborg/design.md` (architecture §1, modes §7, strategy §10,
suspicion §10.1, action §9). Orientation: `crewborg/AGENTS.md`.

## Variant / experiment flags (what the upload bakes in, and your knobs)

crewborg behavior is configured **only at the strategy boundary**
(`rule_based.py` reads env once at construction); modes are env-free. The
`crewborg-aaln:v2` image bakes the **deterministic dumb baseline**:

| Env flag | v2 value | Effect |
|---|---|---|
| `CREWBORG_POLICY_VARIANT` | `dumb` | scripted baseline, no external LLM required |
| `BE_DUMB` / `CREWBORG_BE_DUMB` | `1` | aggressive imposter: skip Pretend/Evade/body-reports, stay in Search/Hunt |
| `CREWBORG_LLM_MEETINGS` | `0` | deterministic vote policy (no LLM latency in meetings) |
| `CREWBORG_DICK_MODE` | `0` | no timed emergency-button taunt |
| `CREWBORG_HUNTER` | (unset) | hunter/stakeout fork; the hunter variant is the separate `sussybuster-aaln` policy |

Other knobs you can flip per candidate (edit the `Dockerfile` `ENV` block, then
rebuild/upload — see Build below):

- `CREWBORG_LLM_MEETINGS=1` + a provider key, or a Bedrock flag → LLM chat/vote
  in meetings. Three backends (selected by `CREWBORG_LLM_PROVIDER`, else
  inferred): **OpenRouter** (`OPENROUTER_API_KEY`, OpenAI-compatible gateway,
  default model `anthropic/claude-haiku-4.5` — the preferred path), **direct
  Anthropic** (`ANTHROPIC_API_KEY`, default `claude-haiku-4-5-20251001`), or
  **Bedrock** (`USE_BEDROCK=1` / `CREWBORG_USE_BEDROCK=1` /
  `CLAUDE_CODE_USE_BEDROCK=1` + AWS env creds). Override the model with
  `CREWBORG_LLM_MODEL`. On the hosted runner, attach the key as a
  Secrets-Manager secret at upload:
  `coworld upload-policy ... --secret-env OPENROUTER_API_KEY=sk-or-...` (or
  `--use-bedrock`). The deterministic `vote_policy.fallback_vote` is the
  always-on safety net. See `skills/coworld-operations` §Secrets.
- `CREWBORG_DICK_MODE=1` — one-shot crewmate emergency-button call before the
  first kill cooldown clears.

> **Flags are the experiment surface.** The cleanest first experiments are flag
> flips (dumb vs. LLM meetings; aggressive vs. blended imposter), not code
> changes. Each combo is a distinct candidate — pin and record which flags each
> uploaded version baked in (Observatory `attributes.run` / the Dockerfile ENV)
> and match the set across arms. For each flag's *tradeoff and when to test it*,
> see `CREWBORG_INSIGHTS.md` §4.

## Build / run / test

```sh
# Build the league image (self-contained; bakes the v2 ENV flags above)
docker build -t crewborg-aaln:latest players/crewborg-aaln
# or, for the canonical platform + crewborg build helper:
# players/crewborg-aaln/players/crewrift/crewborg/build.sh

# Upload + submit (RECORD the pinned policy-version id for each arm)
coworld upload-policy crewborg-aaln:latest --name crewborg-aaln   # [+ --use-bedrock]
coworld submit crewborg-aaln:<tag> --league league_605ff338-0a2e-4e62-aeda-559df9a9198f \
  --auto-champion always --no-open-browser

# Run locally against a Crewrift server
COWORLD_PLAYER_WS_URL='ws://127.0.0.1:8080/player?slot=0&token=' \
  python -m players.crewrift.crewborg.coworld.policy_player

# Tests (action / modes / strategy / suspicion / meeting / nav / decoder + runtime)
cd players/crewborg-aaln && python -m pytest players/crewrift/crewborg/tests/

# Pull hosted episodes crewborg played (replays + per-slot traces + roster)
players/crewrift/crewborg/scripts/fetch_episodes.sh -n 100   # or fetch_episodes.py
```

Verify the `run` attribute is non-null on the new version after upload — a
missing `run` is the most common silent `-100` timeout failure.

## Where to edit when improving behavior

All paths under `players/crewborg-aaln/players/crewrift/crewborg/`. Map the
hypothesis to the smallest file:

| Want to change… | Edit | Notes |
|---|---|---|
| **Who we suspect / flee / vote** | `strategy/suspicion.py` | Bayesian P(imposter): prior, likelihood-ratio table, social cues. `FLEE_PROBABILITY` gates Flee. Full model: `docs/designs/suspicion.md`. |
| **Which mode is active (role/phase rules)** | `strategy/rule_based.py` | the mode selector + all env-flag gating (BE_DUMB, DICK_MODE, LLM meetings). Crewmate & imposter priority orders live here. |
| **The meeting vote decision** | `strategy/meeting/vote_policy.py` | state-dependent vote bar, must-eject endgame, anti-split swap, per-role fallback vote. Often the highest-leverage crewmate fix (missed/bad votes are big penalties). |
| **Meeting chat / LLM prompts** | `strategy/meeting/prompts.py`, `context.py`, `llm.py`, `schema.py` | role-specialized prompts (the `ROLE_STRATEGY` tier is the tunable knob); `valid_vote_targets` enforces never-vote-teammate. |
| **A specific behavioral stance** | `modes/<mode>.py` | crewmate: `normal`, `attend_meeting`, `report_body`, `flee`, `dick_mode`, `idle`. imposter: `pretend`, `search`, `hunt`, `evade`. The intent each emits + its "done" detection. |
| **Movement / pathing / stuck recovery** | `nav.py` (graph + routing) and `action.py` (follower, momentum, replan) | momentum bang-bang + predictive stop; route re-roots every `REPLAN_INTERVAL`. See `skills/map-navigation`. |
| **Imposter target selection / interception** | `strategy/trajectory.py`, `strategy/opportunity.py`, `modes/hunt.py`, `modes/search.py` | victim pick, trajectory lead, witness/urgency gate. |
| **Occupancy / location estimates** | `agent_tracking.py`, `strategy/occupancy.py` | reachability-disc beliefs + Pretend room scoring. |
| **What gets logged (for a hypothesis)** | `events.py`, `trace.py`, `artifact.py` | add domain events / artifact fields *first* if the eval can't distinguish your hypothesis. See `skills/data-collection-design`, `skills/player-artifacts`. |
| **Tuning constants (no structural change)** | the relevant module's constants; `design.md §12` indexes them | e.g. `EVADE_TICKS`, `SEARCH_LEAD_TICKS`, isolation/witness/urgency, anti-split timing, Pretend room penalty. |

Keep the diff **one hypothesis per candidate** so the eval result is
attributable (`playbooks/optimize-policy.md`).

## Debug / trace format

Stdout = protocol channel; **stderr = logs/traces**. Logging defaults into the
per-episode **artifact** (`artifact.py` → in-memory SQLite `trace.db` +
`summary.json`, zipped and PUT to `COWORLD_PLAYER_ARTIFACT_UPLOAD_URL`). The
`summary.json` block is always echoed to stderr (greppable fallback).

The `trace.db` schema (`traces`/`metrics`/`positions`), the `server_tick` replay
join key, and the `CREWBORG_TRACE` verbosity levels are documented canonically in
`games/crewrift/skills/crewrift-optimization`. Set the level *before* the episode
and match it across arms. The single most useful forensic record is the
per-meeting `domain.suspicion_snapshot` (ranked posteriors + each suspect's event
log + the would-be vote and the bar) — it explains a vote after the fact.

## Crewrift facts that shape every decision

Scoring (task +1, kill +10, win +100, vote-timeout −10, stuck −1), the
time-based-seed role assignment (no color→role correlation), the −100 lobby
taint, and the high-variance / 40–80-game consequence for evals are the
**objective** — the canonical statement is in
`games/crewrift/skills/crewrift-optimization` (game facts) and
`crewrift-eval-design` (eval sizing). The one rule to internalize: +10/kill and
+100/win dominate task points, so league score weights imposter performance and
wins.

## Routing — read in this order

1. **This guide** — the crewborg-aaln particulars (you are here).
2. `../AGENTS.md` — the optimizer entry point + workflow.
3. `../playbooks/optimize-policy.md` — the one-loop procedure to follow.
4. `../games/crewrift/skills/crewrift-optimization/SKILL.md` and
   `crewrift-eval-design/SKILL.md` — Crewrift commands, schema, eval sizing
   (**the most relevant skills**).
5. `../skills/base-optimizer-framework` — the methodology, when you need depth.
6. Then pull focused `../skills/*` per the loop step:
   `hosted-xp-evals` + `eval-variance-design` (run/size evals),
   `replay-artifact-analysis` + `opponent-strategy-mining` +
   `replay-variance-miner` (analyze), `policy-hypothesis-loop` +
   `data-collection-design` (hypothesize/instrument), `eval-aggregation` +
   `promotion-gate` (verdict), `spatial-temporal-analysis` + `map-navigation`
   (Crewrift's spatial traits), `coworld-operations` (CLI), `continuous-optimizer`
   + `../playbooks/defend-leaderboard.md` (keep #1 automatically).
