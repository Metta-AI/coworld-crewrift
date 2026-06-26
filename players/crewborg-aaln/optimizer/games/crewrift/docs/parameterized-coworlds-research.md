# Parameterizing the Crew Rift Co-world for Curriculum / Scenario-Based Training

**Status:** Research report (no source changes)
**Scope:** How to expose a variant/scenario tree of small controlled drills (voting, hunting, communication, deception, coordination, strategic decisions) that optimizers can request as cheap, targeted experience — while the real league/tournament stays the final validation.

> **Guiding principle (from the user):** Scenarios are *training infrastructure*, not the tournament. The co-world developer owns the WHOLE system (game, optimizer, player templates, scenario tree, XP-request support, diagnostics, replay). If a needed parameterization is missing, the right move is to ADD it to the game/co-world, not declare it unsupported.

---

## 0. TL;DR

- **The cheap win is real and large.** The hosted `xp-request create` API *already* accepts a `variant_id`, and `run-episode`/`play`/`scrimmage` *already* accept `--variant VARIANT_ID` (Coworld `COOKBOOK.md` lines 44, 51). The episode job spec also accepts a free-form `game_config` override (`episode_request_schema.json`, `Coworld Episode Job Spec.properties.game_config`, `additionalProperties: true`). So **config-only drills cost zero engine changes and zero upstream-schema changes** — they are just more entries in `coworld_manifest.json:variants[]`.
- **The engine already supports more than the manifest exposes.** Per-slot **role** and **color** forcing exists today (`src/crewrift/sim.nim:2592-2597`; manifest `coworld_manifest.json:52-94`); deterministic **seed** control exists (`sim.nim:1450-1453`, `:4108`); the `certification.game_config` block (`coworld_manifest.json:527-557`) is already a working "drill" shape (1 task, 300 ticks, 0 reveal/wait).
- **The genuinely missing capabilities require Nim engine changes**: starting a *live* game mid-history / from a saved state, forcing spawn **positions**, preloading **meeting/vote/body** state, and **scripted NPC behavior/utterances**. The good news: the hard primitive (a complete, flatty-serializable `SimServer` with keyframe save/restore + seek) already exists — but is wired only for *read-only replay playback* (`src/crewrift/replays.nim:91-97, 152-170, 321-343`), not for spawning a live episode.
- **The upstream manifest schema gates the rich design.** `CoworldVariant` is `{id, name, game_config, description}` with `additionalProperties: false` (verified from `Metta-AI/coworld` `src/coworld/coworld_manifest_schema.json`). A scenario object with `skill_tag`/`category`/`grading`/`cost_tier` therefore **cannot** live in the canonical manifest until the upstream Pydantic model (`types.py`) is changed and the schema regenerated. Until then, scenario metadata must live in a **side-car file in this repo** (e.g. `games/crewrift/scenarios/*.json`) referenced by the optimizer skills, with only the `game_config` half flowing into manifest variants.
- **The optimizer methodology has no "scenario/drill/curriculum" object today** and one skill (`replay-variance-miner`) explicitly warns that a naive competence-curriculum optimizes a gameable proxy. Drills must target *score-variance-explaining* behaviors and must pass through the existing role-disaggregation + anti-overfit + champion-regression guards unchanged.

---

## 1. Current state — what parameterization exists today

### 1.1 The co-world manifest (`coworld_manifest.json`)

- Declares the upstream schema it conforms to at the top: `"$schema": "https://raw.githubusercontent.com/Metta-AI/coworld/main/src/coworld/coworld_manifest_schema.json"` (`coworld_manifest.json:2`).
- `game.config_schema` (`coworld_manifest.json:14-247`) is the full legal knob surface. Notable drill-relevant knobs:
  - `seed` (`:100-105`, `-1` = time-based)
  - `minPlayers` (`:106-112`), `imposterCount` / `autoImposterCount` (`:113-124`)
  - `tasksPerPlayer` (`:125-130`), `buttonCalls` (`:131-136`)
  - every phase tick budget: `startWaitTicks` (`:137-142`), `voteTimerTicks` (`:143-148`), `voteResultTicks` (`:149-154`), `killCooldownTicks` (`:167-172`), `roleRevealTicks` (`:173-178`), `gameOverTicks` (`:191-196`), `maxTicks` (`:197-202`), `maxGames` (`:203-208`)
  - interaction ranges `killRange`/`ventRange`/`reportRange` (`:209-226`)
  - `mapPath` (`:242-246`)
  - **`slots[]`** — a per-slot array (`:52-94`) that can fix `role` (`crew`/`imposter`, `:64-70`), `color` (16 colors, `:71-90`), and `token`; plus `closedRoster` (`:95-98`).
- `results_schema` (`:249-374`) — per-slot arrays: `scores`, `win`, `tasks`, `kills`, `imposter`, `crew`, `vote_players`, `vote_skip`, `vote_timeout`, `connect_timeout`, `disconnect_timeout`. These are the only structured outputs a grader/optimizer gets per episode today.
- Role declarations: `commissioner` (`:432-441`, the shared social-deduction commissioner), `reporter` (`:442-451`, the event-log reporter that expands a replay into `{ts, player, key, value}` rows), `grader` (`:452-461`, the reference grader scoring from wins/score-spread/tasks/kills/voting). There is **no** `diagnoser` or `optimizer` runnable declared (both are optional in the upstream schema).
- `variants` (`:462-499`) — **exactly one variant, `"default"`** (`:464`), an 8-player balanced tournament `game_config`.
- `certification` (`:500-558`) — a smoke fixture: 8× `notsus` players (`:501-526`) and a *shrunk* `game_config` (`:527-557`): `tasksPerPlayer: 1`, `maxTicks: 300`, `startWaitTicks: 0`, `roleRevealTicks: 0`, `gameOverTicks: 1`, `voteTimerTicks: 120`. **This block is already a config-only "drill."**

### 1.2 What the upstream schema allows (verified against Metta-AI/coworld)

From `Metta-AI/coworld` `src/coworld/coworld_manifest_schema.json` (fetched live) and `src/coworld/docs/COWORLD_MANIFEST.md`:

- `CoworldVariant` has **exactly four properties** — `id`, `name`, `game_config` (free-form, `additionalProperties: true`), `description` — and the variant object itself is `additionalProperties: false`. All four are `required`.
- `variants` is a top-level required arr=y, `minItems: 1`. `game_config` here is "token-free game config that validates against `game.config_schema`."
- The manifest is `additionalProperties: false` at top level, with roles `game`, `player` (required), and optional `commissioner`, `reporter`, `grader`, **`diagnoser`**, **`optimizer`** (the last two marked `x-coworld-future-required: true`).
- **The schema is generated, not hand-authored.** `COWORLD_MANIFEST.md` (§"Source Of Truth", §"Validation And Regeneration"): the Pydantic models in `src/coworld/types.py` own the field contracts; the JSON schema is regenerated via `scripts/generate_coworld_schemas.py`; "Do not hand-edit `coworld_manifest_schema.json`." Schema authority therefore lives **upstream in Metta-AI/coworld**, not in this repo.

**Implication:** today's `variants` can only carry a *named game_config*. There is no manifest-native place for scenario metadata (skill tag, category, per-scenario grading, cost tier). That is the central schema gap (see §3.1, §6).

### 1.3 The episode-run path (where a game_config actually reaches a policy)

- Local: `coworld run-episode MANIFEST [player]`, `coworld play MANIFEST`, with `--variant VARIANT_ID` to pick a non-certification variant (`COOKBOOK.md:33-44`). A full `episode_request.json` matching `coworld/runner/episode_request_schema.json` can be passed instead (`COOKBOOK.md:443-448`).
- The runner-facing `Coworld Episode Job Spec` (`episode_request_schema.json`) requires `{manifest, game_config, players}` and optionally `episode_tags`; its `game_config` is free-form (`additionalProperties: true`). So a raw scenario `game_config` is fully expressible at the job level.
- The game container reads its config from `COGAME_CONFIG_URI` (crewborg `docs/crewrift-replays.md:104`). Boot path: `src/crewrift.nim:29-64` → `defaultGameConfig()` then `config.update(runtimeConfig.config)` → `resolveRandomSeed()` → `runServerLoop` → `initSimServer(config)` (`src/crewrift/server.nim:766-829`; seed wired at `sim.nim:4108`).
- The **policy container is scenario-agnostic** — it only reads `COGAMES_ENGINE_WS_URL` and reacts to the binary scene stream; it never sees `game_config` (`players/crewborg-aaln/players/crewrift/crewborg/coworld/policy_player.py:99-159`; `coworld/entrypoint.sh:6`). A policy "learns" the scenario only by playing it.

### 1.4 The experience-request (XP / "Generate Experience") contract today

- Hosted requests go through `POST /v2/experience-requests` (`xreq_...`), created via `coworld xp-request create` (`COOKBOOK.md:776-786`; `players/crewborg-aaln/optimizer/skills/coworld-operations/SKILL.md:111-123`).
- The **verified v2 body is roster-based** and rejects legacy top-level `requester`/`opponents`/`rotate_seats` (422) — `players/crewborg-aaln/optimizer/skills/hosted-xp-evals/SKILL.md:25-29, 33-74`. It carries: `target.league_id`/`division_id` (or direct `coworld_id` + **`variant_id`**), `roster[].player` (`policy_ref` | `top_n` | `random`), `roster[].slot` (`-1` any / `0`,`1` fixed), `num_episodes`, `notes`.
- **Crucially, the cookbook example body already includes `variant_id`:** `{"coworld_id": "cow_...", "variant_id": "variant_...", "roster": [...], "num_episodes": 5}` (`COOKBOOK.md:51`). And "the body is passed through to the backend unchanged" (`COOKBOOK.md:784`).
- Today the optimizer chooses only WHO plays (policies/slots/league/count). The WHAT (`game_config`) is resolved server-side from the league's configured variant and merely echoed read-only into each child episode's `episode_request.json` (live example: `players/crewbot3000/diagnosis_data/ep00_5afdad60/episode_request.json:6-49`).
- Per-episode artifacts land as `episode.json`, `episode_request.json` (with `game_config`), `replay.json[.z]`, and per-slot `logs/` (`players/crewborg-aaln/players/crewrift/crewborg/scripts/fetch_episodes.py:341-395`). The rich per-tick `trace.db` is the **player's own artifact**, uploaded via `COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` (`coworld/policy_player.py:12-23, 150-154`); schema (`traces`/`metrics`/`positions`, `server_tick` join key) at `crewrift-optimization/SKILL.md:57-98`.

### 1.5 Limits of today's parameterization (explicit)

Today's variants can only tweak **global game config**. They **cannot**:
- seed a **mid-game** state (start in `Voting`/`MeetingCall`, preload a partial event history, jump to a tick);
- force **spawn positions** (all players co-locate at `home` via `arrangeHomePositions()`, `sim.nim:2583`);
- inject a **scripted NPC** that emits a fixed utterance or a fixed vote;
- preload **bodies / votes / accusations**;
- attach **per-scenario grading** (the grader sees only the standard `results_schema`).

What they *can* already do (today, no engine change): fix per-slot **role** & **color**, fix a **closed roster**, set a deterministic **seed**, and shrink **ticks/tasks/timers** to make short, cheap, role-controlled episodes — i.e. the `certification` shape generalized.

---

## 2. The game engine — what state can be configured/seeded

The full Nim engine source is in this repo under `src/crewrift/` (not just vendored references): `sim.nim` (simulator, ~4215 lines), `server.nim` (ws server / loop), `replays.nim` (record/playback/seek), `tasks.nim`, `global.nim`, `texts.nim`, plus `tools/expand_replay.nim`.

### 2.1 Game phases and config surface

`GamePhase` enum (`src/crewrift/sim.nim:191-199`): `Lobby`, `Playing`, `Voting`, `VoteResult`, `GameOver`, `RoleReveal`, `GameInfo`, `MeetingCall`. (The active gameplay phase is named `Playing`.)

`GameConfig` (`sim.nim:294-329`) — ~35 fields, all seedable from JSON: physics (`motionScale`, `accel`, friction, `maxSpeed`), `seed`, `speed`, ranges, every phase tick budget, `minPlayers`, `imposterCount`/`autoImposterCount`, `maxTicks`/`maxGames`, `tasksPerPlayer`, `buttonCalls`, `mapPath`, `closedRoster`, and `slots*: seq[PlayerSlotConfig]`.

`PlayerSlotConfig` (`sim.nim:286-292`): per-slot `name`, `token`, `role`, `color`, `hasRole`, `hasColor`. Config ingestion is `update()` (`sim.nim:1383-1442`); seed resolution `resolveRandomSeed()` (`sim.nim:1450-1453`); config round-trips into the replay header via `configJson()` (`sim.nim:1471+`).

### 2.2 How a game starts (and what is RNG-driven)

`initSimServer` resolves the seed and seeds the RNG: `result.rng = initRand(resolvedConfig.seed)` (`sim.nim:4104-4108`) — so seed deterministically drives role shuffle and task assignment. The sim always begins in `Lobby` and only calls `startGame()` once `players.len >= requiredLobbyPlayers` and the `startWaitTicks` countdown elapses (`sim.nim:4261-4290`).

`startGame` (`sim.nim:2577-2651`, read directly): `arrangeHomePositions()` (`:2583`) co-locates everyone at `home`; **fixed roles are honored first** via `slotConfig(joinOrder).hasRole` (`:2592-2597`), then the remaining imposter budget is filled by an RNG shuffle of the unfixed candidates (`:2600-2608`); tasks are assigned via `assignTaskDetails(..., sim.rng)` (`:2624-2636`).

### 2.3 Map data

Only `mapPath` is configurable (`sim.nim:327`; manifest `:242-246`). Tasks/vents/rooms/button all come from the resource file (`loadCrewriftMap`, `sim.nim:4132-4135`); individual placements are not config-overridable — you'd swap the whole `.resources` file. Per the crewborg design, only `croatoan` exists today. (The crewborg `map/bake.py`/`map/parser.py`/`map/types.py` are a *client-side re-implementation* for the bot's belief, not the engine's bake.)

### 2.4 Replays = the latent save/restore primitive

Replays are **per-tick input masks re-simulated from the seed**, not stored frames (`replays.nim:40-54`; magic `CREWRIFT`, format v3, embeds the config JSON). But the engine *can* serialize/restore complete game state:
- `serializeReplaySim`/`deserializeReplaySim` flatty-(de)serialize a **complete `SimServer`** (`replays.nim:91-97`).
- `buildReplayKeyframes` snapshots full sim state every `ReplayKeyframeTicks = 100` ticks (`replays.nim:301-319`, read directly).
- `seekReplay`/`applyReplaySeek` restore the nearest keyframe (`restoreReplayKeyframe`, `:152-170`) then step forward to any tick (`replays.nim:321-343`, read directly).

This is the key building block: **a full, restorable `SimServer` snapshot already exists** — but it is wired only into read-only replay viewing (`COGAME_LOAD_REPLAY_URI` at boot), never into spawning a live, interactive episode from that state.

### 2.5 NPC behavior / utterances

There are **no in-engine bot players** — every player is an external websocket client (`server.nim:442-469`). Chat enters only via real player sockets (`server.nim:1057` → `addVotingChat`, `sim.nim:2935`) or replay chat records. The `src/crewrift/ais/*.nim` `talkToAI` procs are **dead code** (never called by `sim.nim`/`server.nim`/`global.nim`). So there is no mechanism to inject a scripted action/utterance for an NPC into a live game.

### 2.6 Capability verdict table

| Capability | Status | Evidence |
|---|---|---|
| Deterministic seed | **EXISTS** | `sim.nim:301, 1450-1453, 4108` |
| Force per-slot **role** | **EXISTS** | `PlayerSlotConfig.role` `sim.nim:289`; `startGame` `:2592-2597`; manifest `:64-70` |
| Force per-slot **color** | **EXISTS** | `sim.nim:1205-1213`; manifest `:71-90` |
| Fixed/closed roster + tokens | **EXISTS** | `closedRoster`/`slots`/`tokens` `sim.nim:1264-1297`; manifest `:52-98` |
| Shrink ticks/tasks/timers (drill shape) | **EXISTS** | `certification.game_config` `coworld_manifest.json:527-557` |
| Choose map (whole file only) | **EXISTS** | `mapPath` `sim.nim:327`; manifest `:242-246` |
| Per-task/vent/room placement via config | **ABSENT** | from resource file; `loadCrewriftMap` `sim.nim:4132` |
| Full game-state serialize/restore (mechanism) | **EXISTS (replay only)** | `replays.nim:91-97, 152-170, 321-343` |
| Start a **live** game mid-history / from saved state | **ABSENT** → needs Nim change | always `Lobby`→`startGame` `sim.nim:4261-4290` |
| Force spawn **positions** | **ABSENT** → needs Nim change | `arrangeHomePositions()` `sim.nim:2583` |
| Preload **meeting/vote/body** state | **ABSENT** → needs Nim change | `VoteState` reachable only via in-game transitions (`sim.nim:206-216`) |
| Scripted/deterministic **NPC** behavior/utterances | **ABSENT** → needs Nim change | players are ws clients; `ais/*` unused |

---

## 3. Gap analysis per scenario type

For each example drill, what the game/co-world needs that it lacks today:

### 3.1 Voting ("load history up to a meeting, only decide votes")
- **Want:** start the episode already in `Voting`/`MeetingCall` with a pre-seeded meeting context (who's dead, who's been accused, recent chat), let the policy decide only the vote, then terminate + grade on the vote.
- **Have:** can shrink to a tiny task phase + low `voteTimerTicks` and force roles/seed so a meeting happens fast (config-only). Can fix the imposter's seat so "correct vote" is known.
- **Gap:** no way to **start in `Voting`** or **preload meeting/body/accusation state** (`sim.nim:206-216`, `:4261-4290`). The latent primitive is `restoreReplayKeyframe` (`replays.nim:152-170`) — restore a keyframe captured at a meeting, then hand control to live players. Needs a Nim "live-start-from-keyframe" path + an early-terminate-after-vote hook.

### 3.2 Emergency-Meeting Response (a bot calls a meeting and instructs the policy to say/do something)
- **Want:** a scripted NPC that calls a meeting and emits a fixed utterance/instruction; pass = the policy actually responds (votes / speaks / moves as instructed).
- **Have:** nothing scripted; meetings only arise from real player button calls.
- **Gap:** **scripted NPC** (engine-side bot or a deterministic scripted player container) that can call a meeting and `addVotingChat` a fixed line (`sim.nim:2935`). Needs Nim NPC support OR a "scripted-player" runnable that replays a fixed input/chat track. Plus a grader that checks "did the policy respond."

### 3.3 Hunting / Imposter (isolate chase/juke/kill/avoid-detection)
- **Want:** a short imposter-only mechanical drill with a known target/seed.
- **Have:** **mostly today.** Force the policy's slot `role: imposter` (`sim.nim:2592-2597`), set seed, shrink `maxTicks`, lower `killCooldownTicks`, and fill other seats with `notsus` crew. This is a config-only drill **now**.
- **Gap:** richer fidelity (fixed spawn positions for a deterministic chase geometry) needs the spawn-position engine change (`arrangeHomePositions()`, `sim.nim:2583`); deterministic prey behavior needs a scripted-player.

### 3.4 Communication (one player gives info, policy must interpret and act)
- **Want:** a scripted teammate emits a fact ("red vented"); policy must up/down-weight and act.
- **Have:** nothing — relies on scripted utterances.
- **Gap:** same **scripted NPC / scripted-player** requirement as 3.2, plus a grader checking the policy's downstream action (vote target / suspicion update). For crewborg specifically, the tell is fittable (`crewborg-suspicion-tuning/SKILL.md:55-66` `PLAIN_SUS_*_LOG_LR`), so a Tier-A local fixture can cover much of this offline (§5).

### 3.5 Strategic situations (preconfigured histories; right move depends on context)
- **Want:** late-game voting, ambiguous suspicion, split groups, conflicting testimony — i.e. an arbitrary preloaded mid-game state.
- **Have:** nothing for true mid-game state; only seed+role+timing shaping.
- **Gap:** the **mid-game live-start-from-saved-state** capability (the §2.4 primitive promoted to a live path) is the general solution that subsumes 3.1/3.5. This is the single highest-leverage engine change.

**Summary:** Hunting (3.3) and a coarse Voting/Strategic drill (via seed+role+timing shaping) are achievable **config-only today**. Emergency-meeting (3.2) and Communication (3.4) require **scripted NPC/player**. True mid-game Voting/Strategic (3.1/3.5) require **live-start-from-saved-state** + **early-terminate/grading hooks**. Forced spawn positions are a smaller, independent engine add that improves determinism across all of them.

---

## 4. Proposed design

### 4.1 Scenario tree — where it lives

Because upstream `CoworldVariant` is `additionalProperties: false` with only `{id, name, game_config, description}`, scenario metadata **cannot** be put into manifest variants today. Two-part proposal:

**(a) Game-config half → manifest `variants[]` (no upstream change).** Each scenario whose realization is config-only (or, later, config + scenario-block once the engine supports it) gets a normal variant entry. Use a naming convention so the variant id encodes the scenario, e.g. `scn_vote_basic`, `scn_hunt_isolated`, `drill_meeting_response`.

**(b) Scenario metadata half → side-car file in this repo:** `players/crewborg-aaln/optimizer/games/crewrift/scenarios/<id>.json` (or a single `scenarios.json`), referenced by the optimizer skills. Proposed object:

```json
{
  "id": "scn_vote_basic",
  "name": "Basic Vote — single clear imposter",
  "skill_tags": ["voting", "deduction"],
  "category": "voting",
  "variant_id": "scn_vote_basic",
  "cost_tier": "cheap",
  "realization": {
    "kind": "config_only",
    "game_config_ref": "variants/scn_vote_basic",
    "seed_sweep": [1001, 1002, 1003, 1004]
  },
  "grading": {
    "type": "per_scenario",
    "pass_criteria": [
      { "metric": "vote_cast", "op": ">=", "value": 1, "desc": "policy actually cast a vote" },
      { "metric": "vote_correct_rate", "op": ">=", "value": 0.7 },
      { "metric": "vote_timeout", "op": "==", "value": 0 }
    ]
  },
  "regression_baseline": "champion"
}
```

`realization.kind` ∈ `{config_only, scripted_player, mid_game_seed}` lets the same scenario schema describe all phases of the roadmap. `cost_tier` ∈ `{cheap, medium, full}` maps to episode budgets.

**(c) Upstream path (when richer manifest-native scenarios are desired):** propose extending `CoworldVariant` upstream (in `Metta-AI/coworld` `src/coworld/types.py`) with an optional `scenario` block — or, cleaner, add a new top-level optional `scenarios: [CoworldScenario]` array (`additionalProperties: false`, each referencing a `variant_id`, plus `skill_tags`, `category`, `grading`, `cost_tier`). This is an **upstream PR**, not a local edit, and would also want a `diagnoser`/`grader` runnable that understands per-scenario grading. Until merged, keep (b).

### 4.2 Engine/game-config changes to realize each scenario

Ordered by leverage; all are in `src/crewrift/`:

1. **Live-start-from-saved-state** (subsumes 3.1, 3.5). Add a config knob, e.g. `initialStateUri` (a saved `SimServer` snapshot or a `replay + seekTick`), and a boot path in `runServerLoop`/`initSimServer` that, instead of `resetToLobby`, calls the existing `deserializeReplaySim`/`restoreReplayKeyframe` (`replays.nim:91-97, 152-170`) to load state, then *transfers control to live player sockets*. The serialize/restore code already exists; the work is exposing it as a live entry point and reconciling live player joins with restored player slots.
2. **Initial-phase / early-terminate hooks** (3.1). Config: `startPhase` (e.g. `Voting`) and `terminateAfter` (e.g. `vote_resolved`, `first_kill`, `n_ticks`). On `terminateAfter`, write `results.json` and exit so drills are cheap and crisp.
3. **Forced spawn positions** (improves 3.3/all). Extend `PlayerSlotConfig` (`sim.nim:286-292`) with optional `x`/`y`/`hasPos`, honored in `arrangeHomePositions()` (`sim.nim:2583`).
4. **Scripted NPC / scripted-player** (3.2, 3.4, deterministic opponents). Two options: (i) a new in-engine scripted bot that executes a declared action/chat track; or (ii) — cheaper, no engine change — a **separate scripted-player runnable** (a `player[]` entry like a deterministic `notsus++`) that reads a fixed input/utterance script and connects as a normal ws client. Prefer (ii) for Phases 1-3; (i) only if engine-side determinism is required.
5. **Preloaded meeting/body/vote state** (3.1, 3.5). Subsumed by (1) if the saved snapshot already encodes a meeting; otherwise a targeted `meetingSeed` config block (dead bodies, prior votes). Recommend doing it via (1).

### 4.3 Generate Experience / XP-request exposure

The XP API already accepts `variant_id` (`COOKBOOK.md:51`) and passes the body through unchanged (`:784`). So:

- **Phase-1 (today):** an optimizer requests "voting practice" by submitting `{"coworld_id": "...", "variant_id": "scn_vote_basic", "roster": [...], "num_episodes": N}`. **Zero API change.** A thin local helper (a new optimizer skill/CLI wrapper) maps a scenario id → its `variant_id` (from the side-car file) and builds the body.
- **Richer (proposed) request additions** (upstream `V2CreateExperienceRequestRequest`): an optional `scenario_id` (resolves to a variant + scenario metadata + per-scenario grader) and/or an optional `game_config_override` object (the episode job spec already supports a free-form `game_config`, so this is mostly a backend pass-through to the runner — see `episode_request_schema.json` `game_config: additionalProperties: true`). `game_config_override` lets an optimizer sweep `seed`/`role`/`tasksPerPlayer` without minting a manifest variant per combination.
- Mapping onto a run: `scenario_id`/`variant_id` → backend resolves the variant `game_config` → runner builds the `Coworld Episode Job Spec` (`{manifest, game_config, players}`) → game container boots from `COGAME_CONFIG_URI`. Identical to the existing path; only the *source* of `game_config` is a scenario variant.

### 4.4 Grading / diagnostics — drills vs league

- **League** uses the standard `results_schema` + reference grader (wins/score-spread/tasks/kills/voting). Keep unchanged.
- **Drills** need **per-scenario pass/fail** computed from signals the standard results don't expose well (e.g. "did the policy cast *any* vote", "did it vote the known imposter", "did it respond to the meeting within K ticks"). Two sources:
  - The **event-log reporter** already expands a replay into `{ts, player, key, value}` rows (`coworld_manifest.json:442-451`) — vote events, kills, chat. A per-scenario grader (a new `grader[]` or `diagnoser[]` runnable) consumes these rows + the known-imposter seat (from the scenario's forced `slots`) to emit pass/fail per the `grading.pass_criteria` block (§4.1).
  - The **player `trace.db`** (`crewrift-optimization/SKILL.md:57-98`) carries `domain.vote_cast`, `domain.kill_landed`, `domain.meeting_vote_selected` — the exact telemetry a drill asserts on, joinable by `server_tick`.
- These pass/fail records feed **regression testing**: a scenario's pass-rate becomes a tripwire ("voting drill pass-rate must not drop vs champion"), evaluated with the existing effect-size-with-uncertainty + behavioral-corroboration gate (`eval-aggregation/SKILL.md:57-82`).

### 4.5 How player templates / skills / optimizers declare they consume scenarios

Following the platform/game/policy split mandated by `optimize-policy.md:73`:

- **Skills (game-specific):** add a "Scenario rosters" section to `crewrift-eval-design/SKILL.md` (parallel to its Roster Shapes at `:40-90`) listing each drill's `{variant_id, metric, tripwire}`; and a "Fitting corpora" list to `crewborg-suspicion-tuning/SKILL.md` (parallel to its fitting loop at `:68-98`). A skill "knows how to use" a scenario by citing it in its body + the `AGENTS.md` routing table (`:36-51`), exactly as it currently cites the 40-80-game floor.
- **Optimizers:** the loop's existing seams already accept a *named validation target* — `policy-hypothesis-loop/SKILL.md:18-19` has a `validation eval set` field (a scenario id is just a reusable value for it); `continuous-optimizer/SKILL.md:36-49` "Analyze"/"Verify" steps select scenarios alongside leaderboard opponents; `replay-variance-miner` emits an "Eval plan" line that becomes "run drill X."
- **Player templates:** the crewborg policy already emits the joinable metrics (`data-collection-design/SKILL.md:19-45`) and is locally runnable with targeted trace groups (`crewrift-optimization/SKILL.md:100-110`). "Knowing how to use scenarios" = organizing its `tests/` fixtures *by skill* (it already has `test_vote_policy.py`, `test_hunter.py`, `test_meeting_social.py`) so each drill maps to a runnable target.

---

## 5. The three integration tiers (how a "training scenario" is realized for a *scripted* policy)

crewborg is a **scripted/rule-based policy with a fittable Bayesian suspicion model — not an RL network** (`AGENTS.md:42-43`; `scripted-policy-techniques/SKILL.md:34-52`; `crewborg-suspicion-tuning/SKILL.md:29-53`). So a "training scenario" attaches at one of three tiers (cheapest first):

- **Tier A — Deterministic local fixtures** (offline, fast). A voting/hunting/comms drill = a fixtured relational assertion in the style of `tests/test_vote_policy.py` / `test_hunter.py` / `test_meeting_social.py`. E.g. voting drill: construct a meeting state with a known imposter → assert `vote_policy.vote_bar` lands on the real imposter and never times out. **No engine or hosting needed.**
- **Tier B — Observer-POV fitting corpus** (this is the policy's real "training" surface). The suspicion model is *fit* from labelled replays via the loop at `crewborg-suspicion-tuning/SKILL.md:68-98`. A "comms/voting scenario" = a curated, role-labelled replay subset ("all meetings with a framing chorus") fed through that fitting loop to re-estimate the cue's likelihood ratio. A scenario here is a *named, saved corpus filter* + its expected fitted shape.
- **Tier C — Targeted hosted eval rosters** (real league fidelity). The drill = a manifest variant (§4.1a) requested via `xp-request` + `variant_id` (§4.3), scored by the per-role behavioral metric that *is* the drill's score, gated by `eval-aggregation`'s uncertainty + behavioral-corroboration checks.

Regardless of tier, a drill cannot bypass three methodology guards: **role disaggregation** (`eval-aggregation/SKILL.md:37-48`), the **anti-overfit / classifier-gate rule** (`opponent-strategy-mining/SKILL.md:71-76`; `promotion-gate/SKILL.md:37`), and **load-bearing-not-invariant** (`replay-variance-miner/SKILL.md:14-26`).

---

## 6. Closing-the-loop plan (phased, minimal-first)

| Phase | Deliverable | Engine source change? | Upstream schema change? |
|---|---|---|---|
| **1** | **Config-only drills today.** Add manifest `variants[]` for hunting (role-forced imposter), coarse voting (seed+role+short timers), task-pressure drills. Side-car `scenarios/*.json` (§4.1b). Optimizer helper maps scenario id → `variant_id` and submits `xp-request`. Tier-A local fixtures (`tests/` by skill) + Tier-B fitting corpora. | **No** | **No** |
| **2** | **Scripted-player runnable** (deterministic `notsus++` reading a fixed input/utterance/vote track) as a new `player[]` entry. Enables emergency-meeting (3.2) and communication (3.4) drills + deterministic opponents. Per-scenario grader (new `grader[]`/`diagnoser[]`) consuming event-log reporter rows. | **No** (scripted *player*, not engine) | Optional (`diagnoser[]` is allowed) |
| **3** | **Engine: forced spawn positions + initial-phase + early-terminate hooks** (`PlayerSlotConfig` pos, `startPhase`, `terminateAfter`). Sharper, cheaper drills. | **Yes** (`sim.nim`) | No |
| **4** | **Engine: live-start-from-saved-state** (promote `restoreReplayKeyframe` to a live boot path; `initialStateUri`). Enables true mid-game Voting/Strategic (3.1/3.5). | **Yes** (`sim.nim`/`server.nim`/`replays.nim`) | No |
| **5** | **Manifest-native scenario tree + XP `scenario_id`.** Upstream PR to `Metta-AI/coworld` `types.py` adding a `scenarios[]` array (or `variant.scenario` block) + `V2CreateExperienceRequestRequest.scenario_id`/`game_config_override`. Curriculum sequencing in `continuous-optimizer`. | No (engine) | **Yes (upstream PR)** |

**Cheap wins achievable today (Phase 1):** hunting drills (forced imposter role + short `maxTicks`), coarse voting/strategic drills (seed + forced imposter seat + low `voteTimerTicks`), task-pressure drills (`tasksPerPlayer` sweep), all requested via the existing `variant_id` XP path, plus all Tier-A fixtures and Tier-B fitting. **Requires game-engine source changes:** forced positions, initial-phase, early-terminate, and the high-leverage live-start-from-saved-state (Phases 3-4). **Requires upstream coordination:** manifest-native scenario metadata and XP `scenario_id` (Phase 5).

---

## 7. Risks / open questions

- **Overfitting to drills.** A drill that lifts a synthetic metric but tanks a role, or wins via an artifact absent from real play, is a false win (`promotion-gate/SKILL.md:37`; `eval-aggregation/SKILL.md:37-48`). Drills must target *score-variance-explaining* behaviors (`replay-variance-miner/SKILL.md:14-26`), and league games remain the final gate.
- **Transfer validation.** Every drill-driven change must still pass the broad guardrail + champion-regression eval before promotion (`base-optimizer-framework/SKILL.md:94`; `promotion-gate/SKILL.md:11-30`). Drill pass-rate is a *diagnostic/tripwire*, never the promotion criterion alone.
- **Schema authority is upstream.** `coworld_manifest_schema.json` is generated from `Metta-AI/coworld` `src/coworld/types.py` and must not be hand-edited (`COWORLD_MANIFEST.md` §Source Of Truth / §Validation). Manifest-native scenarios (Phase 5) therefore need an **upstream PR**; Phases 1-4 deliberately avoid this by using config-only variants + a local side-car file.
- **Determinism of scripted opponents.** A scripted-*player* (Phase 2) is only as deterministic as the seed + its fixed input track; network timing jitter can perturb tick alignment. An engine-side scripted NPC (more work) would be fully deterministic. Validate determinism with `run-episode --episodes N` + replay re-sim before trusting a drill as a regression baseline.
- **Cost.** The XP API is fast and effectively unlimited; sample size is the constraint (`optimize-policy.md:45`). Cheap drills (short `maxTicks`, 1 task) make large N affordable, but per-scenario grading adds a reporter/grader pass per episode — budget the `cost_tier` accordingly.
- **"Variant" is overloaded.** Policy-side variants are baked env flags (`CREWBORG_POLICY_VARIANT`, `BE_DUMB`, `CREWBORG_LLM_MEETINGS`, `CREWBORG_DICK_MODE`; `guide/SKILL.md:75-106`), distinct from game-side `game_config` scenario variants. Any scenario design must keep these orthogonal (match policy flags across drill arms).
- **Open question — meeting-state fidelity.** Whether Phase-1 "coarse voting" drills (seed-induced meetings) are realistic enough to be useful, or whether the meaningful voting signal only emerges from true mid-game state (Phase 4). Recommend measuring Phase-1 drill→league transfer before investing in Phase 4.
- **Open question — grader ownership.** The reference grader/reporter live in separate upstream repos (`Metta-AI/graders`, `Metta-AI/reporters`). A per-scenario grader could be a new bundled `grader[]`/`diagnoser[]` runnable in *this* repo (allowed by schema) rather than upstream — decide ownership early.
