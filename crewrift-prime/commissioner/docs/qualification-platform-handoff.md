# Crewrift Prime Qualification — Platform Wiring Handoff

## Context

The Crewrift Prime commissioner was reworked from a "Qualifiers staging division"
model into an **event-driven qualification flow**. On a new submission the
commissioner now: (1) runs a self-play *experience-request* game for the policy,
(2) reads the episode's per-slot **results JSON** artifact for skill metrics, (3) runs a strict
three-skill gate plus an optional out-of-band **LLM interview** hard gate, and
(4) promotes a passing policy directly into the **Competition** division. There is
no longer a Qualifiers staging division.

The commissioner code is **complete and tested** (see
`crewrift-prime/commissioner/`). This document covers the **platform-side**
changes still required — all in the sibling repo `../metta`, under
`app_backend` — to make the reworked flow actually fire end-to-end.

> All `file ~lines` references below are into `../metta/app_backend` unless the
> path is explicitly under `crewrift-prime/`.

---

## Hard blockers

These three items block qualification from working at all (blocker 3 only blocks
the optional LLM interview gate, not base qualification).

### 1. Submission seam — `migrate_league` is one-shot and never re-fires per submission

**Problem.** The platform runs the commissioner's `migrate_league` only **once**,
gated on `league.commissioner_migration_version` — a hash of
`commissioner_config + runtime`:

- `_apply_container_commissioner_migration`
  (`src/metta/app_backend/v2/pipeline.py` ~909–932) — applies the migration and
  stamps the version; skips when the stored version already matches.
- scheduled from `_schedule_and_execute_container_commissioner_rounds`
  (`pipeline.py` ~1102–1111).

Because the migrate body only runs when the version hash changes, the
commissioner's event-driven gate — which lives **inside** its `migrate_league`
and qualifies every `submitted` / `qualifying` membership — **never executes per
submission**. Submissions land but are never evaluated.

Submission ingress for reference:

- `POST /v2/league-submissions` → `create_league_submission`
  (`src/metta/app_backend/v2/routes/leagues.py` ~506–571) inserts the submission.
- drained by `_process_submission` (`pipeline.py` ~466–541), which calls
  `place_league_submission_membership`.

**Fix.** Add a **per-submission commissioner qualify trigger** — a new function
parallel to `run_submission_processor_once`, invoked from `run_round_runner_once`
(`pipeline.py` ~1312–1319). It must:

- run the commissioner `migrate_league` body **without** the
  `commissioner_migration_version` gate;
- be scoped to **container-commissioner** leagues that have `submitted` /
  `qualifying` memberships;
- apply a **re-qualify cadence** so held entrants (e.g. infra-held) don't spawn a
  fresh xp-request game on every poll.

### 2. xp-request payload shape — RESOLVED (commissioner side)

**Status: already fixed on the commissioner side — no platform change required.**
Documented here so the reader knows it *was* a blocker that has been cleared.

The commissioner's `xp_request_client.py` now builds the roster-based
`V2CreateExperienceRequestRequest` shape: a `roster` of 8 self-play participants
`{"player": {"policy_ref": <policy_version_id>}, "slot": -1}`, dropping the legacy
`requester` / `opponents` / `backfill` fields.

This matches the platform contract:

- endpoint `POST /v2/experience-requests`
  (`src/metta/app_backend/v2/routes/experience_requests.py` ~642–696);
- schema `V2CreateExperienceRequestRequest`
  (`src/metta/app_backend/v2/api_types.py` ~131–161).

**No platform change needed.**

### 3. Interview-mode container launch + address — blocks only the LLM interview gate

**Problem.** There is **no platform capability** to launch a candidate player
container in *interview mode*, nor to surface that container's address back to the
commissioner. The commissioner reaches the player's interview websocket server
(`coworld.interview.v1`, port **8770**) through an injectable transport provider
that defaults to the env var `CREWRIFT_PRIME_INTERVIEW_ADDR`.

**Fix (follow-up).** Build a **player-interview-mode launcher** analogous to
`CommissionerContainer` (`src/metta/app_backend/.../container_lifecycle.py`
~162–218): a k8s `Job` + `Service` running the **player** image with its command
overridden to the manifest's `interview_run` (from
`players/crewbot3000/coplayer_manifest.json`), exposing port **8770**. Then surface
the in-cluster `Service` DNS to the commissioner. Recommended: a new per-candidate
endpoint that returns the address. Alternatives: a static env var, or extend the
xp-request response to carry it.

**Until then:** ship with `CREWRIFT_PRIME_INTERVIEW_ENABLED=0`, which skips the
interview gate (neutral pass). Base qualification (skill gate) is unaffected.

---

## Enabling dependencies

### A. Bundle the Nim replay expander into the commissioner image — RETIRED (no longer required)

> **Status (update):** This dependency is **no longer required.** The qualifier now
> reads the game's own end-of-episode per-slot **results JSON** artifact
> (`GET /jobs/{job_id}/artifacts/results` — the seat-indexed `results_schema` the
> platform stores per job, the same endpoint `coworld episode-results` reads)
> instead of downloading + re-expanding the `.bitreplay`. The Nim expander
> (`tools/expand_replay.nim`) and `replay_parser.py` are no longer on the qualifier
> path and are not bundled into the commissioner image (`Dockerfile` no longer
> copies `replay_parser.py`). No build stage / `CREWRIFT_PRIME_EXPAND_REPLAY_CMD` /
> `CREWRIFT_PRIME_GAME_DIR` is needed.
>
> Verified live (2026-06-27): a completed `crewrift_prime` qualifier episode's
> results artifact contains every per-slot array `decision.py` needs — `scores`,
> `win`, `tasks`, `kills`, `imposter`, `crew`, `vote_players`, `vote_skip`,
> `vote_timeout` (plus `names`, `connect_timeout`, `disconnect_timeout`), 8 entries
> per slot. The old text is preserved below for history only.

~~The replay re-simulation step shells out to a Nim expander
(`tools/expand_replay.nim`, invoked via `CREWRIFT_PRIME_EXPAND_REPLAY_CMD` run in
`CREWRIFT_PRIME_GAME_DIR`). That binary is **not present** in the commissioner
image — `crewrift-prime/commissioner/Dockerfile` installs only the vendored
`commissioners` package plus the Crewrift Prime overlay. Without the expander,
every qualifier becomes an **infra hold** (replay can't be expanded → metrics
can't be derived).~~

~~**Fix.** Add a build stage that compiles a `crewrift-expand-replay` binary from the
game repo's `tools/expand_replay.nim`, copy it into the image, and set
`CREWRIFT_PRIME_EXPAND_REPLAY_CMD` / `CREWRIFT_PRIME_GAME_DIR` accordingly.~~

### B. Secret-injection path for commissioner env

Commissioner containers currently get a sanitized, plaintext-only env: 
`_validated_public_env` (`container_lifecycle.py` ~392–400) strips private keys,
and commissioners run with `automount_service_account_token=False`. There is no
safe way to pass secrets — `SOFTMAX_API_TOKEN`, `ANTHROPIC_API_KEY`,
`CREWRIFT_PRIME_INTERVIEW_API_KEY` — through the plaintext manifest env.

**Fix.** Add a **k8s-Secret injection mechanism** for commissioner containers
(mount/`envFrom` a Secret) so these can be supplied without landing in the
plaintext manifest.

---

## Stop seeding the Qualifiers division (Area 2)

The `social_deduction` seed template injects `qualifiers_division_name` into
seeded leagues' `commissioner_config`:

- `src/metta/app_backend/v2/seed.py` ~217–230;
- constants `QUALIFIERS_DIVISION_NAME` / `QUALIFIERS_DIVISION_LEVEL` /
  `DIVISION_TYPE_STAGING` in `models.py` ~230–232.

Divisions are now created from the **commissioner migration config**
(`_ensure_commissioner_migration_divisions`, `pipeline.py` ~817–874), and the
Crewrift Prime commissioner declares **only Competition** — so a pre-existing
Qualifiers division gets archived.

But submission placement won't fall through to Competition while
`qualifiers_division_name` is still set: `_process_submission` →
`select_qualifier_division` (`pipeline.py` ~524–530;
`division_selectors.py` ~41–58) → submissions are rejected with
**"no submission division"**.

**Fix.** Give Crewrift Prime a **seed config WITHOUT `qualifiers_division_name`**
(a new template branch). Do **NOT** remove it globally — Among Them and others
still rely on it.

**Migration caveat.** A division with **live memberships cannot be archived**
(`pipeline.py` ~1058–1066). Any existing Crewrift Prime league must have its
Qualifiers memberships **drained** (promoted / DQ'd) before the new migration can
succeed.

---

## Commissioner environment variables

These go on `crewrift-prime/coworld_manifest.crewrift_prime.json` →
`commissioner[0].env` (the `among-them-commissioner` runnable).

| Env var | Purpose | Notes |
|---|---|---|
| `CREWRIFT_PRIME_EXPAND_REPLAY_CMD` | Command to expand a `.bitreplay` (Nim expander) | requires bundled binary (dep A) |
| `CREWRIFT_PRIME_GAME_DIR` | Working dir the expander runs in | |
| `SOFTMAX_API_TOKEN` | Platform API auth | **secret** (needs dep B) |
| `ANTHROPIC_API_KEY` | LLM scorer auth | **secret** (needs dep B) |
| `CREWRIFT_PRIME_INTERVIEW_API_KEY` | Interview LLM/scorer auth | **secret** (needs dep B) |
| `CREWRIFT_PRIME_INTERVIEW_MODEL` | LLM model for interview scoring | |
| `CREWRIFT_PRIME_INTERVIEW_ENABLED` | Master switch for interview gate | **`0` for first light** |
| `CREWRIFT_PRIME_INTERVIEW_MIN` | Interview pass threshold | |
| `CREWRIFT_PRIME_INTERVIEW_ADDR` | Default interview websocket address | until launcher exists (blocker 3) |
| `CREWRIFT_PRIME_QUALIFIER_EPISODES` | Episodes per qualifier xp-request | |
| `CREWRIFT_PRIME_INTERVIEW_FALLBACK` | Use fallback question pool on LLM failure | default **on** |
| `CREWRIFT_PRIME_INTERVIEW_AUTOPASS_ON_LLM_FAIL` | Auto-pass a received answer when scorer LLM fails | default **on** |
| `CREWRIFT_PRIME_MEETING_PARTICIPATION_MIN` | (optional) voting skill threshold | |
| `CREWRIFT_PRIME_HUNT_KILLS_MIN` | (optional) hunting skill threshold | |
| `CREWRIFT_PRIME_TASK_TASKS_MIN` | (optional) tasks skill threshold | |

---

## Minimal path to first live qualification

1. **Crewrift Prime seed config without `qualifiers_division_name`** (new
   `social_deduction` template branch); drain any existing Qualifiers memberships
   so the migration can archive the old division.
2. **Commissioner env + Nim expander + secret injection**, interview gate **OFF**
   (`CREWRIFT_PRIME_INTERVIEW_ENABLED=0`): bundle the `crewrift-expand-replay`
   binary (dep A), resolve k8s-Secret injection (dep B), set the env table above.
3. **Add the per-submission migrate trigger** (blocker 1) so submissions actually
   get evaluated.
4. **Follow-up:** build the interview-mode launcher + address surface (blocker 3),
   then flip `CREWRIFT_PRIME_INTERVIEW_ENABLED=1`.
