# Crewrift Prime — advanced-skill commissioner

A custom Coworld commissioner for the **Crewrift Prime** league that replaces the
stock score-only Qualifiers→Competition gate with a **strict three-skill gate**,
plus first-class **decision observability** (per-skill scores, verdicts, and a
human-readable reason for every promotion decision).

## Why a custom image

The stock config-driven `ruleset_strategy` commissioner's transition vocabulary
(`TransitionCriteriaConfig`, `extra="forbid"`) only allows `completed_episodes_*`
/ `score_*`. It discards every other field of the per-slot `results_schema` that
the platform delivers in `EpisodeResult.game_results`. Gating on advanced skills
requires reading `game_results`, so a code change (this image) is unavoidable.
Everything else — `crash_check`, division migration, ranking, scheduling — is
reused unchanged from the vendored stock base (`vendor/commissioners`).

## The gate — ONE game measures everything ("one game and we're in")

Each Qualifiers entrant plays **exactly ONE 8-seat self-play game** of the
`scn_qualifier` variant. Self-play fills all 8 seats with the entrant, so that
single game exercises both roles (the entrant plays the imposter(s) AND crew),
and all three signals come from its per-slot `game_results`:

| Skill | Metric | Threshold (default, env-overridable) | Computed from the one game |
|---|---|---|---|
| voting | `meeting_participation` | pass if it votes / no-meeting (`CREWRIFT_PRIME_MEETING_PARTICIPATION_MIN=0.0`) | meeting-aware participation: pass if the entrant cast a vote/skip (or spoke) when a meeting occurred; no penalty if no meeting occurred; fail only if a meeting happened yet it never voted (`vote_players`/`vote_skip`/`vote_timeout`). |
| hunting | `imposter_kills` | `>= 0.5` (`CREWRIFT_PRIME_HUNT_KILLS_MIN`) → ≥1 kill | total `kills` landed by the imposter seat(s) (`imposter`==1) in the game |
| tasks | `crew_tasks_mean` | `>= 1.0` (`CREWRIFT_PRIME_TASK_TASKS_MIN`) | mean `tasks` across the crew seats (`crew`==1) in the game |

**Promote** (→ Competition, `status=competing` / `substatus=champion`) iff ALL
three pass. **Hold** in Qualifiers (`status=qualifying` / `substatus=skill_gate`)
and re-run the single game next round otherwise.

**Crash safety is folded into the one game** (no separate crash_check stage):
- a completed game with results is, by definition, not a crash → evaluate the 3 skills;
- a genuine non-completion (no results, not infra) → **DQ** ("Failed to complete the qualifier game");
- an infra/dispatch failure (HTTP 4xx/5xx from `/jobs/batch`, job never created) → **hold & retry**, never DQ.

Single game ⇒ single-game variance; thresholds are deliberately low/easy. The
`scn_qualifier` variant tuning (natural `imposterCount: 2`, `killCooldownTicks: 90`
so the imposter kills early → body → report → meeting, `tasksPerPlayer: 8` +
`maxTicks: 6000` so crew complete tasks without instant-winning, and a generous
`connectTimeoutTicks: 3600` so all 8 self-play agents connect — see Part 1 below)
makes one game reliably surface all three signals.

> **Why connectTimeoutTicks: 3600** (Part-1 fix). Episode requests correctly
> carry 8 `policy_version_ids`, but a slow-starting policy can leave some of its 8
> self-play agents un-connected within the timeout — they show as `connect_timeout`
> (e.g. truecrew's crash-check ran with only 2 of 8 connected). Raising the connect
> window gives all 8 agents time to join. This is a runtime/connection issue, not a
> seat-expansion bug.

## Competition division — score = WINNING PLAYERS (1 point per winning seat, by role)

Once promoted, a policy competes in the **Competition** division, where the round
score counts **every winning player (seat)**: **1 point for each seat that won as
imposter, plus 1 point for each seat that won as crew** (score =
`imposter_wins + crew_wins`). A seat scores if its per-slot `game_results.win` is
True; the role of that winning seat (imposter vs crew) comes from the per-slot
`imposter`/`crew` arrays. An entrant occupying several winning seats in one game
scores once **per winning seat** (not once per winning episode).

- `_complete_competition_round` (subclass override) sets each entrant's per-round
  score = winning players that round, with `imposter_wins`/`crew_wins` in
  `result_metadata` and a `competition_wins` breakdown in `round_display`. A
  `COMMISSIONER_DECISION {"decision":"COMPETITION_WINS", ...}` line is logged per
  entrant.
- `rank_division` (subclass override) ranks the Competition leaderboard by
  **cumulative points** — the SUM of per-round winning-player counts (no ewma decay) — so the
  leaderboard is a running win total. Other divisions keep the stock ranking.

### Threshold rationale (lowered 2026-06-24 — "easier for now")

Thresholds were lowered so a modest policy can clear the gate while each drill
still measures real skill (none are trivially 0). Observed `crewborg-aaln` drill
metrics historically: voting ≈ 0.0, hunting ≈ 0.75 kills, tasks ≈ 10.5.

- `meeting_participation >= 0.5` (voting is a PARTICIPATION ASSURANCE, redesigned
  2026-06-24): it answers "does the policy know how to **vote** and **talk** —
  i.e. can it take part in a meeting?", NOT "does it vote correctly". An entrant
  passes when it makes a deliberate vote action (votes for a player or explicitly
  skips) — and, when measurable, speaks — in at least half the drill's meetings.
  A policy that only times out (never votes/talks) fails for the right reason
  ("doesn't vote/talk"). See "Voting = meeting participation" below for the
  vote/talk signal details and the deferred per-slot chat field.
- `kills_as_imposter_rate >= 0.5`: at least one kill every other episode on
  average — proves the policy can execute as imposter, not just survive.
- `tasks_completed >= 1.0`: at least one completed task/seat under task pressure —
  proves real routing throughput without demanding a near-clear.

**UI sync:** these three numbers are mirrored in the web app at
`web/softmax.com/src/app/(observatory)/observatory/v2/skillGate.ts`
(`SKILL_SPEC_BY_VARIANT[].threshold`), which the **episode viewer** reads. The
round view is event-sourced (reads the threshold the commissioner recorded), but
the episode viewer uses these constants, so they MUST be kept in sync with
`decision.py`.

Tune via env (no rebuild needed if set on the runnable): set them in the coworld
manifest commissioner runnable `env`, or as constants in `decision.py`:
`CREWRIFT_PRIME_MEETING_PARTICIPATION_MIN`, `CREWRIFT_PRIME_HUNT_KILLS_MIN`,
`CREWRIFT_PRIME_TASK_TASKS_MIN`, `CREWRIFT_PRIME_EPISODES_PER_DRILL`.

### Voting = meeting participation (vote + talk)

The voting drill is an **assurance that the policy can participate in a meeting**,
deliberately split into two capabilities:

- **"Knows how to vote" — measurable today.** Per drill episode (self-play, so all
  8 seats are the entrant) the entrant participated if `sum(vote_players) +
  sum(vote_skip) > 0` — it either voted for a player or explicitly skipped. Pure
  `vote_timeout` (never acting) is not participation. `meeting_participation` is
  the fraction of episodes with participation.
- **"Knows how to talk" — NOT measurable today; forward-compatible + deferred.**
  The crewrift `results_schema` exposes no chat/talk field. The engine *does* track
  chat (`addVotingChat`, per-player `lastChatTick`, `sim.chatMessages`) but never
  emits a per-slot count. The commissioner already reads a per-slot talk count from
  `game_results` under any of `chat_messages` / `spoke` / `messages_sent` **if
  present** (counts speaking as participation); until the game emits it, talk is
  simply absent and is **never fabricated**.

  **Deferred engine plan (path b) — to activate "talk":** this needs a crewrift
  GAME image rebuild + coworld game re-upload/re-cert, which the commissioner-only
  `patch-commissioner` deploy cannot ship. Concretely:
  1. `src/crewrift/sim.nim`: add `chatMessages*: int` to `RewardAccount` (next to
     `votePlayers`/`voteSkip`/`voteTimeout`); in `addVotingChat`, after a message
     is accepted, `let i = sim.rewardAccountForPlayer(playerIndex); if i >= 0: inc
     sim.rewardAccounts[i].chatMessages` (mirrors `recordVotePlayers`); in the
     results builder add `chatMessages` to the per-slot init/read and emit
     `results["chat_messages"] = chatMessagesList`.
  2. `coworld_manifest.crewrift_prime.json` → `results_schema.properties`: add an
     optional `chat_messages` integer array (the schema is `additionalProperties:
     false`, so the field must exist in the schema *and* be emitted by the rebuilt
     binary — both ship together).
  3. Rebuild the crewrift game image, `upload-coworld` the new game, re-certify.
     No commissioner change is needed — talk auto-activates.

## Skill-gate stage detection (regression fix)

The platform schedules per-entrant **parallel-qualifier** rounds whose
`round_config.stages` is `null`, so the skill-gate stage CANNOT be detected by a
stage label at scheduling time. The commissioner instead detects the stage from
the **entrant membership's substatus** (the authoritative stage signal the
platform uses):

- entrant substatus `""`/`None` ⇒ **crash_check** stage,
- entrant substatus `skill_gate` (or legacy `skill_gate_held`) ⇒ **skill_gate**
  stage ⇒ schedule the three scenario drills.

A v4 regression toggled a held entrant's substatus to the non-stage value
`skill_gate_held`; the platform then could not map it back into the skill_gate
stage, producing empty rounds that raised `pool must have at least one primary
entry`. The fix keeps the hold substatus stable at `skill_gate`.

## Crash-check robustness

`crash_check` is stock self-play on the full 8-seat game. Two defects are fixed
here:

1. **8-seat self-play dispatch.** `RoundStartView.variant()` falls back to
   `len(entries)` (= 1 for a single entrant), which would emit a 1-seat episode
   that the platform's `/jobs/batch` rejects with `400 Bad Request` (player count
   ≠ manifest count) and looks like a crash. The commissioner resolves the seat
   count from the variant's declared player count and **floors it at `NUM_SEATS`
   (8)** so every crash-check episode carries 8 `policy_version_ids`. The seat
   count is commissioner-controlled — `/jobs/batch` builds the episode from the
   list we send — so this fix is entirely commissioner-side. (Andre's
   `truecrew:v25` 1-seat failure was dispatched by an older commissioner build.)
2. **Infra/dispatch failures are NOT disqualifications.** A crash-check round
   where no episode completed AND the failures look like dispatch errors
   (`/jobs/batch`, HTTP 4xx/5xx, job never created) is reclassified from the
   stock `completed_episodes_lte: 0` "Failed crash test" DQ into a **non-DQ hold**
   (`status=qualifying`, `substatus=None` ⇒ retry crash_check), with an accurate
   `reason` ("Crash-check dispatch failed (infrastructure error, not a policy
   crash)") and a `crewrift_prime_dispatch_failure` evidence blob. Genuine policy
   crashes (a job ran and the container failed/timed out) still disqualify.

## Observability — where decisions surface

For every entrant the commissioner builds a `DecisionRecord` (see `decision.py`)
and emits it through **three protocol-supported channels**:

1. **Structured stdout** (hosted commissioner log tab). One greppable JSON line
   per entrant per round, tagged `COMMISSIONER_DECISION`:

   ```
   COMMISSIONER_DECISION {"decision":"PROMOTED","passed":true,"reason":"PROMOTED: cast votes in 4/4 meetings ✓, kills_as_imposter_rate 1.50>=0.5 ✓, tasks_completed 4.19>=1 ✓","short_reason":"...","entrant_policy_version_id":"...","round_id":"...","round_number":4,"skills":{"voting":{"metric_name":"meeting_participation","metric_value":1.0,"threshold":0.5,"comparator":">=","episodes_counted":4,"passed":true,"detail":"cast votes in 4/4 meetings","raw_inputs":{"participated_episodes":4,"votes_for_players_per_episode":[...],"vote_skips_per_episode":[...],"vote_timeouts_per_episode":[...],"chat_messages_per_episode":[null,...],"talk_signal_available":false},"variant_id":"scn_vote_basic"},"hunting":{...},"tasks":{...}}}
   ```

   Grep the hosted logs with `COMMISSIONER_DECISION` to see every decision.

2. **Membership event fields** (Observatory UI / `GET /v2/policy-membership-events`
   + `GET /v2/league-policy-memberships`). On each `PolicyMembershipEventChange`:
   - `reason` — short reason (e.g. "Held in Qualifiers: failed hunting").
   - `notes` — the full reason string with all three metrics vs thresholds.
   - `evidence[0].summary` — the full reason string; `evidence[0].metadata` — the
     entire `DecisionRecord` (per-skill metric/threshold/verdict/raw inputs).

3. **Cross-round state blob** (`RoundComplete.state`, ≤10MB, persisted by the
   platform and returned in the next `round_start.state`). Per-entrant decision
   records are appended under `state["crewrift_prime_skill"]["rounds"]` (bounded
   to the most recent 50 rounds) so the full decision history is auditable.

The hosted path and the local debug path call the **same** pure function
(`decision.evaluate_entrants`), so the records are identical.

## Local debug path (no hosted round-runner needed)

`decision.py` is pure (no I/O). `debug_decision.py` feeds sample or saved
`game_results` through it and prints the decision records plus the exact hosted
`COMMISSIONER_DECISION` log line.

```sh
cd crewrift-prime/commissioner

# built-in synthetic sample (one passing entrant, one failing hunting):
python debug_decision.py

# from a saved JSON file shaped { entrant_id: { variant_id: [game_results, ...] } }:
python debug_decision.py path/to/results.json
cat results.json | python debug_decision.py -
```

To exercise it against the vendored package in a throwaway venv:

```sh
python3 -m venv /tmp/comm_venv
/tmp/comm_venv/bin/pip install ./vendor
PYTHONPATH=. /tmp/comm_venv/bin/python debug_decision.py
```

Each `game_results` dict is the per-slot `results_schema` the platform delivers
in `EpisodeResult.game_results` — seat-indexed arrays: `vote_players`, `kills`,
`tasks`, `imposter`, `scores`, `win`, etc.

## Files

- `decision.py` — pure decision logic: thresholds, metric computation, verdicts,
  `DecisionRecord`/reason strings. Single source of truth.
- `crewrift_prime_skill_commissioner.py` — `CrewriftPrimeSkillCommissioner`
  subclass: schedules the three drills in the `skill_gate` stage, calls
  `decision.evaluate_entrants`, emits the three observability channels.
- `crewrift_prime.yaml` — ruleset config (Qualifiers `crash_check` + `skill_gate`
  stages, Competition division). Loaded via `RULESET_STRATEGY_CONFIG_PATH`.
- `app.py` — ASGI entrypoint; imports the subclass (registers key
  `crewrift_prime_skill`) then builds `commissioner_app()`.
- `debug_decision.py` — local offline debug/decision script.
- `Dockerfile` — installs `vendor/` then overlays the above.
- `vendor/` — vendored upstream `Metta-AI/commissioners` package (see
  `vendor/VENDOR_PROVENANCE.txt`). Not modified.

## Build / wire (recorded for reproducibility)

```sh
docker build --platform=linux/amd64 -t crewrift-prime-commissioner:v7 .

# Team-only mutation: clear any active player session so get-token returns the
# usr_ token (patch-commissioner needs team auth, not a ply_ token).
cd ../../../metta/packages/coworld
uv run python -c "from softmax.auth import clear_active_player_session; clear_active_player_session(server='https://softmax.com/api')"

# Repoint the coworld's commissioner runnable image; this pushes to Observatory's
# registry, rewrites the manifest image to an img_ id, bumps the coworld version,
# and re-certifies (hosted smoke) to canonical.
uv run coworld patch-commissioner crewrift_prime crewrift-prime-commissioner:v7 \
  --runnable-id among-them-commissioner
```

The league adopts the new commissioner image on its next scheduling tick once the
new coworld version is canonical (the platform resolves the commissioner from the
canonical manifest each tick; the `commissioner_runnable_id` is unchanged). No
re-seed is required.

### Unit tests

```sh
python3 -m venv /tmp/comm_venv && /tmp/comm_venv/bin/pip install ./vendor
RULESET_STRATEGY_CONFIG_PATH=$(pwd)/crewrift_prime.yaml PYTHONPATH=. \
  /tmp/comm_venv/bin/python -m unittest test_observability test_skill_gate_metrics
```

Covers: skill-gate detection by substatus, crash-check 8-seat self-play
scheduling, infra/dispatch failure → non-DQ classification, and the decision
observability log line.
