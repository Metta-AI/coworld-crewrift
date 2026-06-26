# Crewrift Prime Qualification — Platform Wiring Handoff

## Context

The Crewrift Prime commissioner was reworked from a "Qualifiers staging division"
model into an **event-driven qualification flow**. On a new submission the
commissioner now: (1) runs a self-play *experience-request* game for the policy,
(2) re-simulates the resulting `.bitreplay` into skill metrics (including a
per-slot **chat-message count** read straight from the replay), (3) runs a strict
three-skill gate whose voting check includes a **hard in-replay talk gate** ("a
meeting occurred but the policy never talked → not LLM-enabled → fail") **plus an
LLM content grade** of the meeting speech ("the policy talked, but an LLM judges
the speech gibberish/canned → fail"), and
(4) promotes a passing policy directly into the **Competition** division. There is
no longer a Qualifiers staging division, and **no out-of-band LLM interviewer** —
the "can it talk genuinely?" check is computed offline from the replay the
commissioner already parses (presence count + an LLM grade of the chat text).

The commissioner code is **complete and tested** (see
`crewrift-prime/commissioner/`). This document covers the **platform-side**
changes still required — all in the sibling repo `../metta`, under
`app_backend` — to make the reworked flow actually fire end-to-end.

> All `file ~lines` references below are into `../metta/app_backend` unless the
> path is explicitly under `crewrift-prime/`.

---

## Hard blockers

Blockers 1 and 2 are resolved; the former blocker 3 (LLM-enabled / "can it talk?"
verification) is now handled **in-replay** by the commissioner — no platform launch
seam is required. This section is kept as the record of what blocked qualification
and how each was cleared.

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

### 3. "Is the policy LLM-enabled / can it talk?" — RESOLVED in-replay (no platform launch)

**Status: handled entirely by the commissioner (2026-06-26).** Previously this was a
hard blocker that proposed an out-of-band interview container (a separate websocket
server per candidate); that whole stack has been **deleted** in both repos and replaced
with a lightweight in-replay signal.

The Crewrift `.bitreplay` already records meeting chat, and the bundled Nim expander
(`tools/expand_replay.nim`) already emits a per-slot `chat` event per message. The
commissioner now folds those into a per-slot `chat_messages` COUNT plus a per-slot
`chat_texts` TEXT array (`crewrift-prime/commissioner/replay_parser.py`). The voting
verdict (`decision.py`'s `_voting_verdict`) applies a **two-layer talk gate**:

1. **Presence (cheap):** when a meeting occurred AND the chat signal is available AND
   the policy's talk count is 0, voting FAILS ("meeting occurred but policy never
   talked — not LLM-enabled").
2. **Content (LLM-graded):** the commissioner's I/O layer (`qualify_submission`)
   concatenates the candidate's meeting speech from `chat_texts` (self-play → all
   seats) and asks an LLM (`crewrift-prime/commissioner/chat_grader.py`, a stdlib
   `urllib` Anthropic client recovered from the deleted interviewer — NO websocket /
   riddle / container) whether it is genuine Crewrift conversation. A meeting with
   chat present but graded not-genuine (gibberish/canned) FAILS too. This closes the
   loophole where a policy emits a canned `print("gg")` and passes a count-only gate.
   `decision.py` stays PURE — the commissioner computes the boolean and feeds it in.

The grade is **resilient**: disabled (`CREWRIFT_PRIME_GRADE_CHAT_CONTENT=0`), no API
key, or an LLM error → the commissioner passes `None` and the gate degrades to the
presence check (talked → pass) — a grader hiccup NEVER blocks qualification. Talking
genuinely also counts as meeting participation.

The `scn_qualifier` variant is tuned (config-only) to make a meeting near-certain
(`killCooldownTicks: 1`, `buttonCalls: 8`, `buttonResetsKillCooldowns: true`, long
`maxTicks`). Crewrift has **no config knob that deterministically forces a meeting**
(see `GameConfig` in `src/crewrift/sim.nim`), so a fully-guaranteed meeting would need
an engine/harness change (a forced emergency on a tick) — intentionally not done.

**No platform support required**, **no game re-cert** (the chat is already in the
replay and already emitted by the expander; only the commissioner parser/decision
changed). All `COWORLD_INTERVIEW_*` platform settings, the `interview_container.py`
launcher, the generic interviewer image/service, and the per-candidate interview
isolation in the qualify pass have been **removed**.

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
safe way to pass secrets — `SOFTMAX_API_TOKEN`, `ANTHROPIC_API_KEY` — through the
plaintext manifest env.

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
| `CREWRIFT_PRIME_QUALIFIER_EPISODES` | Episodes per qualifier xp-request | |
| `CREWRIFT_PRIME_MEETING_PARTICIPATION_MIN` | (optional) voting skill threshold | |
| `CREWRIFT_PRIME_HUNT_KILLS_MIN` | (optional) hunting skill threshold | |
| `CREWRIFT_PRIME_TASK_TASKS_MIN` | (optional) tasks skill threshold | |
| `CREWRIFT_PRIME_GRADE_CHAT_CONTENT` | Enable the LLM **content** grade of meeting speech (default ON; `0` = presence-only) | non-secret; set in manifest |
| `CREWRIFT_PRIME_INTERVIEW_MODEL` | Anthropic model for the chat content grader | non-secret; set in manifest |
| `CREWRIFT_PRIME_INTERVIEW_API_KEY` / `ANTHROPIC_API_KEY` | Chat-grader LLM key | **secret** — injected via the k8s `crewrift-prime-commissioner-secrets` (dep B); NEVER in the manifest |
| `CREWRIFT_PRIME_GRADE_CHAT_AUTOPASS_ON_LLM_FAIL` | (optional) on LLM error, degrade to presence-only quietly (default ON) | non-secret |

> **Resiliency:** content grading NEVER disqualifies on an LLM problem. With
> `CREWRIFT_PRIME_GRADE_CHAT_CONTENT=1` (default) the commissioner LLM-grades the
> candidate's meeting speech; if the key is absent, the grader errors, or grading
> is disabled, the talk gate **degrades to the presence check** (talked → pass).
> Only a meeting with NO chat, or chat the LLM affirmatively judges not-genuine
> (gibberish/canned), fails the voting check.

> **Removed:** the out-of-band interviewer transport (`CREWRIFT_PRIME_INTERVIEW_ADDR`,
> the websocket riddle Q&A) and the platform's `COWORLD_INTERVIEW_*` settings — the
> "can it talk?" check is the in-replay talk gate (presence + LLM content grade)
> inside the voting skill. The grader REUSES the secret env names
> `CREWRIFT_PRIME_INTERVIEW_API_KEY` / `ANTHROPIC_API_KEY` / `CREWRIFT_PRIME_INTERVIEW_MODEL`
> for the offline replay-speech grade — no live player connection.

---

## Minimal path to first live qualification

1. **Crewrift Prime seed config without `qualifiers_division_name`** (new
   `social_deduction` template branch); drain any existing Qualifiers memberships
   so the migration can archive the old division.
2. **Commissioner env + Nim expander + secret injection:** bundle the
   `crewrift-expand-replay` binary (dep A), resolve k8s-Secret injection (dep B),
   set the env table above.
3. **Add the per-submission migrate trigger** (blocker 1) so submissions actually
   get evaluated.
4. **Talk gate (in-replay, landed):** no platform work — the voting skill already
   fails a policy that never talks in the qualifier's forced meeting. The
   `scn_qualifier` variant is tuned to make a meeting near-certain; a
   fully-guaranteed meeting would need an engine/harness change (a forced emergency
   on a tick), which is intentionally not done. No interviewer image, container, or
   `COWORLD_INTERVIEW_*` / `CREWRIFT_PRIME_INTERVIEW_*` config is needed.
