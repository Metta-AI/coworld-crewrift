# Crewrift Prime — forked coworld + seeded league

A fork of the canonical `crewrift` coworld, uploaded as the new canonical coworld
**`crewrift_prime`** and seeded into the Observatory league **"Crewrift Prime"**, with
Phase-1 (config-only) scenario-parameterization variants from
`../players/crewborg-aaln/optimizer/games/crewrift/docs/parameterized-coworlds-research.md`.

This directory is a FORK. It does NOT modify the repo's existing `../coworld_manifest.json`.

## Files

- `crewrift_prime.patch.json` — JSON merge-patch applied over the live canonical `crewrift`
  manifest at upload time (`coworld upload-coworld --from-coworld crewrift --patch ...`).
  Patches `game.{name,version,description}` and replaces `variants[]`.
- `coworld_manifest.crewrift_prime.json` — the fully-resolved forked manifest (record of what
  was validated/uploaded; image refs are the local download tags from `coworld download crewrift`).
- `scenarios/scenarios.json` — scenario metadata side-car (skill_tags / category / grading /
  cost_tier). Per research §4.1b this CANNOT live in the canonical manifest (upstream
  `CoworldVariant` is `{id,name,game_config,description}`, `additionalProperties:false`), so it
  lives here and references variants by `variant_id`.

## What was created (live Observatory)

| Thing | ID |
|---|---|
| Coworld | `cow_fa681858-ae83-4f08-83cd-56fc4ec9d164` (`crewrift_prime:0.1.0`, canonical) |
| League seed | `lseed_a43fd996-4611-4d0f-b164-20d8f15a56ea` (template `social_deduction`) |
| League | `league_a12f5172-0907-4d04-8bcb-ca02f5360e3a` (display name **"Crewrift Prime"**) |

The derived league display name comes from `game.name` via the backend's
`_coworld_display_name()` (`crewrift_prime` → "Crewrift Prime").

## Scenario variants (Phase-1, config-only, zero engine change)

- `default` — inherited 8-player balanced tournament config.
- `scn_hunt_isolated` — forced imposter seat + short maxTicks + low killCooldownTicks (research §3.3).
- `scn_vote_basic` — forced imposter seat + short task phase + low voteTimerTicks → fast meeting (research §3.1, coarse).
- `scn_task_pressure` — raised tasksPerPlayer under a shortened maxTicks (research §6 Phase-1).

Request a scenario via the existing XP path, e.g.
`{"coworld_id":"cow_fa681858-...","variant_id":"scn_vote_basic","roster":[...],"num_episodes":N}`.

## Qualifier

Qualification is **event-driven** and lives entirely in the commissioner — there is
**no Qualifiers staging division**. When a new policy is submitted to the league, the
`crewrift_prime_skill` commissioner runs the qualification loop itself:

1. **Submit** — a new policy version is submitted to the league.
2. **Qualifier xp request** — the commissioner creates a one-game self-play
   *experience request* for that policy (`POST /v2/experience-requests`), polls it
   until the child episode completes, and downloads the resulting `.bitreplay`.
3. **Replay evaluated** — the commissioner expands the `.bitreplay` into the
   structured event log (re-simulated by the Nim engine via `tools/expand_replay.nim`)
   and folds it into the per-slot skill metrics (kills by imposter seats, mean crew
   tasks, meeting vote/skip participation, and per-slot **chat messages** — the
   in-replay talk signal), then runs the strict three-skill AND gate.
4. **Promote** — pass all three skills (voting includes the talk gate below) and the
   policy is promoted **directly** into the **Competition** division. Fail the
   gate and it does not qualify (held `qualifying` for retry); a genuine
   non-completion is disqualified; an infrastructure/replay-expansion failure
   holds for retry (never a DQ).

### In-replay talk gate ("is the policy LLM-enabled / can it talk?")

The "can this policy actually talk in a meeting?" check is an **in-replay signal**,
not a separate out-of-band interview. The Crewrift `.bitreplay` already records
meeting chat, and the bundled Nim expander already emits a per-slot `chat` event
per message; the commissioner folds these into a per-slot `chat_messages` array
(`commissioner/replay_parser.py`) that the voting verdict consumes
(`decision.py`'s `_voting_verdict`).

The talk gate is a **hard fail** inside the normal voting verdict (no fourth
"interview" skill, no extra container, no game re-cert): when a meeting occurred in
the qualifier game **and** the chat signal is available **and** the policy's talk
count is **0**, voting FAILS with "meeting occurred but policy never talked — not
LLM-enabled". Talking also counts as meeting participation. Because the qualifier
**forces a meeting** (see below), a real qualifier always reaches a meeting, so a
non-talking policy is reliably caught.

The `scn_qualifier` variant is tuned (config-only) to make a meeting near-certain:
`killCooldownTicks: 1` so the two imposter seats kill immediately (bodies appear
fast → body reports), `buttonCalls: 8` so every seat can call an emergency meeting,
`buttonResetsKillCooldowns: true`, and a long `maxTicks`/`connectTimeoutTicks`.
Crewrift has **no config knob that deterministically forces a meeting** (a meeting
only arises from an emergent kill→body→report or an agent pressing the emergency
button — see `GameConfig` in `src/crewrift/sim.nim`), so a *fully-guaranteed*
meeting would require an engine/harness change (a forced emergency on a tick),
which is intentionally **not** done here.

**No game re-cert needed**: the chat is already in the replay and already emitted
by the expander; only the commissioner's parser/decision changed.

Because the stock platform↔commissioner protocol carries **no per-submission
message**, the commissioner reacts on the `migrate_league` seam (the only entrypoint
that sees every membership with its status and can return membership changes). The
platform must therefore invoke `migrate_league` (or an equivalent submission hook)
when a policy is submitted for qualification to fire promptly. The `social_deduction`
league template's `qualifiers_division_name` / staging division is no longer used by
this commissioner; the platform-side league seed/migration should stop creating a
Qualifiers division for Crewrift Prime (the commissioner's `migration_divisions`
already declares only Competition).
