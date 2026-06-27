# Submit & monitor — CLI + API + qualification model

Exact behaviour of the `coworld` commands, Observatory routes, and the **qualification model**
this skill uses. **Re-verified live 2026-06-27** (`coworld <cmd> --help`, `/observatory/openapi.json`,
and metta source `Metta-AI/metta`). Re-check with `--help`; the CLI ships ahead of the metta
checkout. Auth: `softmax login` (`load_current_token`); CLI sends `Authorization: Bearer`, raw API
probing uses `X-Auth-Token`. The metta backend lives at repo-root `app_backend/src/metta/app_backend`.

> **Uploading a version is the separate `build & upload` skill** (`coworld upload-policy …` →
> `POST /stats/policies/docker-img/complete`, returns `<name>:vN`, inert). This reference covers
> only **submit → qualify → champion**, which assumes the version already exists.

## submit — `coworld submit <POLICY> --league/-l LEAGUE_ID [--auto-champion always|never|lineage] [--open-browser/--no-open-browser] [--server]`

- `<POLICY>` = `NAME` or `NAME:vN` (bare name → latest owned version). Resolves via
  `GET /stats/policy-versions?mine=true&name_exact=<name>[&version=N]` — you can only submit versions
  **you own**.
- `--league` (required); **there is no `--division`** — placement is **server-side** (below).
- **`--auto-champion`** (default `always`): champion promotion mode **after the policy qualifies** —
  `always` (promote whenever it qualifies) · `never` (place, never auto-champion) · `lineage` (only
  supersede *your own* prior champion). Backend gate: `should_auto_promote_membership_to_champion`
  (`v2/policy_membership_events.py`).
- Does: **`POST /v2/league-submissions`** `{league_id, policy_version_id}` (route
  `v2/routes/leagues.py:create_league_submission`). The schema `V2CreateLeagueSubmissionRequest` also
  allows optional `player_id` / `preferences` / `notes`, but the **CLI exposes none** — `player_id`
  (submit under a different owned player) is API-only.

### Server-side placement — you do NOT pick a division (this changed; old mental model was wrong)

The client sends **no division**. A submission-processor loop (`v2/pipeline.py:run_submission_processor_once`
→ `_process_submission`) resolves the target division (`v2/division_selectors.py`), in order:

1. **`select_qualifier_division`** → the **staging** division named by the league's
   `commissioner_config.qualifiers_division_name`, *if set and that division exists* → membership
   starts **`qualifying`**.
2. else **`select_competition_entry_division`** → the named Competition division, else the
   **lowest-level** Competition division. For Crewrift's **container-commissioner** league this
   placement **also starts `qualifying`** (`_submission_entry_status`, `v2/league_policy_memberships.py`).
3. else → `rejected`.

> **The old "you must place it into a qualifier division" is wrong** and is now self-healing:
> `pipeline.py:~704` falls back to Competition **even when `qualifiers_division_name` is stale**
> (points at an archived/missing staging division), instead of hard-rejecting. So
> `"league has no submission division"` is now **rare = genuine league misconfig** (no qualifier AND
> no competition division resolves) → escalate to the league owner / commissioner; it is **not** a
> division you choose.

### Submission status + rejection reasons

`LeagueSubmissionStatus` (`models.py`): **`pending → processing → placed`** | `rejected` | `withdrawn`.
A `rejected` submission carries a **`notes`** reason — the monitor surfaces it. Observed reasons:
- `"policy version … already has an active membership in this league"` — **dedup**; retire the old
  membership (below) or submit a *new* version (a new submission of the same pv supersedes the prior).
- `"league has no divisions"`.
- `"league has no submission division"` — rare league misconfig (see above).

## The qualification model — what "qualified" means

Qualification is a **commissioner-issued state transition**, not a counter the backend maintains.
`PolicyMembershipStatus` (`models.py`): **`submitted → qualifying → competing`** | `disqualified`
(+ `substatus` ∈ `active`/`benched`/`crash`/`inactive`). **Champion is not a status** — it is
`status == competing AND is_champion == True`.

- The league's **commissioner** (a per-league container image; round runner polls ~every **10 min**,
  `pipeline.py`) schedules qualifier episodes and, as they complete, emits transitions applied by
  `v2/policy_membership_events.py:apply_policy_membership_event`.
- The verdict rule is per-league config (`RulesetStrategyCommissionerConfig`): for each `qualifying`
  membership it builds a `PolicyTransitionObservation` (`completed_episodes`, `scheduled_episodes`,
  `score`, `failed_episodes`) and picks the first `TransitionCriteria` that matches —
  `completed_episodes_gt` (the games gate) + `score_gt` (the score bar) → target `competing`
  (**qualified**), or `score_lte`/`completed_episodes_lte` → `disqualified`.
- **There is no games-played field on the membership.** Running progress lives in
  `GET /v2/policy-membership-events` → `evidence[].metadata.observed.{completed_episodes,
  scheduled_episodes, score}`. The verdict lives in `membership.status`.

### Disqualification — causes + two monitor caveats

- **`substatus=crash`** — the policy crashed / failed episodes (reported as `EpisodeFailed`, scored a
  loss). **#1 operational cause = TIMEOUTS** (LLM latency especially; a fast/no-LLM player qualifies
  clean). Pull the qualifier episodes (`coworld-episode-artifacts`) and read the logs.
- **`substatus=inactive`** — evicted by the **player-per-user limit** (default 2,
  `_enforce_active_player_limit`) or **retired**. Not a quality failure.
- ⚠️ **A failed *round* is NOT a disqualified *policy*.** `InfrastructureEpisodeError` (5xx / OOM /
  dead pod) aborts a round **without** changing membership status — so trust
  `membership.status == disqualified`, never round-failure counts.
- ⚠️ **Poll without `active_only`.** A `disqualified` membership drops out of `active_only` /
  leaderboard queries, so a monitor filtering on active would silently *lose* the row instead of
  seeing the DQ. The `monitor` script polls unfiltered for this reason.

## monitor — the routes (there is no `coworld standings`)

| Question | Route | Fields |
| --- | --- | --- |
| Placed? | `GET /v2/league-submissions?policy_version_id=<pv>` (or `--mine`) | `status` (`placed`/`rejected`), `notes`, `league_policy_membership_id` |
| **Qualified?** (verdict) | `GET /v2/league-policy-memberships?policy_version_id=<pv>` (or `--mine`) | **`status`**, `substatus`, **`is_champion`**, `division` |
| Qualifying progress | `GET /v2/policy-membership-events?league_policy_membership_id=<lpm>` | `evidence[].metadata.observed.{completed_episodes, scheduled_episodes, score}`, `to_division`, `created_at` |
| Standings / rank | `GET /v2/divisions/{div_id}/leaderboard?include_recent_rounds=N` | `rank`, `player_id`, `score`, **`rounds_played`**, `recent_rounds[]`. **Ranked per player** — match your row by `player_id`. |
| Resolve rotating ids | `coworld leagues [id]` / `coworld divisions [id] -l <league>` | current league/division ids |

CLI equivalents (interactive): `coworld memberships --mine [--policy NAME] [--active-only]
[--champions-only]`, `coworld submissions --mine`, `coworld results <div_id|league_id|round_id>`.
The `monitor` script joins these and centers the qualification verdict; `--watch` polls until terminal.

## Reversibility

- **`coworld retire-membership <lpm_id> [--reason …]`** → `POST /v2/league-policy-memberships/{id}/retire`
  → membership `disqualified` / substatus `inactive`; the public submission record persists. Treat
  `submit` as irreversible — retiring is cleanup, not undo.
- **Versioning** (listing uploads, the version → change log) lives in the **`build & upload`** skill,
  not here — this skill submits a version that already exists.

## Gotchas

- **Rotating ids** — re-resolve `league_id` / `div_id` live each session.
- **Per-player leaderboard** — match standings by `player_id` (from your membership), not name.
- **Auth** — 401/403 → `uv run softmax login`.
