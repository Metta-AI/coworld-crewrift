# Crewrift Prime Qualification — Platform Wiring Handoff

## Context

The Crewrift Prime commissioner was reworked from a "Qualifiers staging division"
model into an **event-driven qualification flow**. On a new submission the
commissioner now: (1) runs a self-play *experience-request* game for the policy,
(2) re-simulates the resulting `.bitreplay` into skill metrics, (3) runs a strict
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

Blockers 1 and 2 are resolved; blocker 3 (the optional LLM interview gate) is now
implemented on the platform side (Gap 3 — see below). This section is kept as the record
of what blocked qualification and how each was cleared.

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

### 3. Interview-mode container launch + address — RESOLVED (platform side, Gap 3)

**Status: implemented in `../metta` (2026-06-25).** Documented here as the now-cleared
final blocker.

The platform now launches a candidate player container in *interview mode* and surfaces
its address to the commissioner:

- **Launcher.** `InterviewContainer`
  (`src/metta/app_backend/v2/container_commissioner/interview_container.py`) mirrors
  `CommissionerContainer`: it creates a k8s `Job` + ClusterIP `Service` running the
  **candidate's** image (resolved from its `PolicyVersion.container_image_id`) with the
  command overridden to the interview entrypoint and the interview port (default **8770**)
  exposed. It waits for the Service to have a ready endpoint, exposes the in-cluster DNS
  address (`<svc>.<namespace>.svc.cluster.local:8770`), and tears the Job (+owned Service)
  down afterward.
- **Run command / port** are NOT carried in any platform-stored manifest (a player's
  `coplayer_manifest.json` is documentary and ignored by the backend), so they come from
  `JobDispatchConfig` (`COWORLD_INTERVIEW_RUN` / `COWORLD_INTERVIEW_PORT` /
  `COWORLD_INTERVIEW_PORT_ENV`), defaulting to the crewbot3000 contract
  (`python -m players.crewrift.crewbot3000.coworld.interview_server`, port 8770,
  `CREWRIFT_INTERVIEW_PORT`).
- **Secret.** The interview LLM key (`ANTHROPIC_API_KEY` /
  `CREWRIFT_PRIME_INTERVIEW_API_KEY`) is injected into the player interview container via
  the same secretKeyRef mechanism (dep B), allowlisted by
  `COWORLD_INTERVIEW_SECRET_ENV_KEYS` against the shared
  `COWORLD_COMMISSIONER_SECRET_NAME` Secret.
- **Address surfacing.** The per-submission qualify trigger
  (`_run_container_commissioner_qualification` → `_qualify_league_with_optional_interview`,
  `src/metta/app_backend/v2/pipeline.py`) processes **one candidate per commissioner boot**
  when the platform gate `COWORLD_INTERVIEW_ENABLED=1`: for each eligible membership it
  launches that candidate's interview container and injects its address as
  `CREWRIFT_PRIME_INTERVIEW_ADDR` on the commissioner container, then runs the ungated
  migrate body and tears both down.

**Multi-candidate note / limitation.** The commissioner consumes a *single*
`CREWRIFT_PRIME_INTERVIEW_ADDR` and interviews every pending membership in one
`migrate_league` pass. To keep that single address correct, the platform qualifies one
candidate per commissioner boot (the address always points at the candidate being
qualified). If a league has multiple pending candidates, they are processed across
separate boots in the same tick. A future optimization could mux multiple interview
servers behind one address or extend the protocol to carry per-candidate addresses.

**To turn it on:** set the platform `COWORLD_INTERVIEW_ENABLED=1`, add
`COWORLD_INTERVIEW_SECRET_ENV_KEYS` (e.g. `ANTHROPIC_API_KEY` or
`CREWRIFT_PRIME_INTERVIEW_API_KEY`), ensure the shared Secret carries that key, and keep
the commissioner manifest `CREWRIFT_PRIME_INTERVIEW_ENABLED=1` (already flipped).

---

## Enabling dependencies

### A. Bundle the Nim replay expander into the commissioner image

The replay re-simulation step shells out to a Nim expander
(`tools/expand_replay.nim`, invoked via `CREWRIFT_PRIME_EXPAND_REPLAY_CMD` run in
`CREWRIFT_PRIME_GAME_DIR`). That binary is **not present** in the commissioner
image — `crewrift-prime/commissioner/Dockerfile` installs only the vendored
`commissioners` package plus the Crewrift Prime overlay. Without the expander,
every qualifier becomes an **infra hold** (replay can't be expanded → metrics
can't be derived).

**Fix.** Add a build stage that compiles a `crewrift-expand-replay` binary from the
game repo's `tools/expand_replay.nim`, copy it into the image, and set
`CREWRIFT_PRIME_EXPAND_REPLAY_CMD` / `CREWRIFT_PRIME_GAME_DIR` accordingly.

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
| `CREWRIFT_PRIME_INTERVIEW_ENABLED` | Master switch for interview gate (commissioner side) | **`1`** (Gap 3 landed) |
| `CREWRIFT_PRIME_INTERVIEW_MIN` | Interview pass threshold | |
| `CREWRIFT_PRIME_INTERVIEW_ADDR` | Interview websocket address | **set by the platform per-candidate** (Gap 3); leave unset in the manifest |
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
4. **Interview gate (Gap 3, landed):** the platform launcher + per-candidate address
   surface are implemented and the commissioner manifest is flipped to
   `CREWRIFT_PRIME_INTERVIEW_ENABLED=1`. To activate end-to-end, also set the platform
   `COWORLD_INTERVIEW_ENABLED=1`, add `COWORLD_INTERVIEW_SECRET_ENV_KEYS` (the interview
   LLM key, e.g. `ANTHROPIC_API_KEY` or `CREWRIFT_PRIME_INTERVIEW_API_KEY`) and ensure the
   shared `COWORLD_COMMISSIONER_SECRET_NAME` Secret carries that key. With
   `COWORLD_INTERVIEW_ENABLED=0` (default) the commissioner sees no address and
   infra-holds each candidate for retry — base qualification is unaffected because the
   interview only HOLDS (never DQs) on a missing address.
