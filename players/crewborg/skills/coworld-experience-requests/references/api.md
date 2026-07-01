# Experience-request API reference

This is the field-level reference for the `coworld-experience-requests` skill (see
`SKILL.md` for the workflow). An **experience request** is a hosted batch of
episodes you define and the server runs: you pick a **target** (which game / league
/ division), a **roster** (which policies play, in which seats and roles) and a
**count**, POST it, and get back an `xreq_…` handle you poll for progress and then
pull artifacts from (replays, logs, results — via the `coworld-episode-artifacts`
skill).

It explains the **API** and its options so you can compose whatever request you
need — it is not a fixed recipe book; mix the building blocks below as the question
demands. `SKILL.md` has the end-to-end loop (resolve → compose → create → stream →
analyze) this fits into.

The API is **game-agnostic** (works for any Coworld); only `game_config_overrides`
(e.g. Crewrift's per-slot roles) is game-specific.

> **Always check the live schema first — the API drifts.** Print
> `components.schemas.V2CreateExperienceRequestRequest` from
> `<api-server>/observatory/openapi.json` before composing a body, and treat that
> as the source of truth over this doc. The field list below was **re-verified live on
> 2026-06-27** (incl. `included_players` / `excluded_players` and per-episode pool resolution); the
> roster schema dates to the overhaul in metta PR #15572 — if you see older examples with
> `requester` / `opponents` / `rotate_seats`, they are from the **removed** pre-#15572 schema (see
> "Removed fields" below).

## The endpoint

`POST /v2/experience-requests` on the Observatory gateway
(`softmax.auth.get_api_server() + "/observatory"`, today
`https://softmax.com/api/observatory`; auth header `X-Auth-Token`). Three ways to
call it:

```sh
# CLI — body is a JSON file (or '-' for stdin)
uv run coworld xp-request create body.json
uv run coworld xp-request list --mine
uv run coworld xp-request get xreq_... --json
uv run coworld xp-request episodes xreq_...
```

```python
# Python client (handles auth + base URL)
from coworld.api_client import CoworldApiClient
with CoworldApiClient.from_login(server_url="https://softmax.com/api") as client:
    detail = client.create_experience_request(payload)   # -> ExperienceRequestDetail
    detail = client.get_experience_request(detail.id)    # readback / poll
    episodes = client.list_experience_request_episodes(detail.id)
```

```python
# Raw httpx (when you want full control / to follow drift)
import httpx, softmax.auth as auth
api = auth.get_api_server(); base = api.rstrip("/") + "/observatory"
tok = auth.load_current_token(server=api)          # NB: not load_current_cogames_token (removed)
r = httpx.post(base + "/v2/experience-requests", headers={"X-Auth-Token": tok},
               json=payload, timeout=120, follow_redirects=True)
```

## The request body — every field

`V2CreateExperienceRequestRequest` has **`additionalProperties: false`**, so an
unknown key is rejected — send only the fields below. Group them by what they
decide:

### Target — *which game*

Pick one of these (a league/division resolves its canonical Coworld for you):

| field | meaning |
| --- | --- |
| `target.division_id` / `target.division_name` | target division; resolves its league + Coworld. `division_name` must be scoped by `league_id`/`league_name` |
| `target.league_id` / `target.league_name` | target league; resolves its canonical Coworld (ambiguous names must use the id) |
| `target.coworld_id` (+ `target.variant_id`) | a direct Coworld, for ad-hoc runs off any league |
| top-level `coworld_id` / `variant_id` | shorthand for a direct Coworld target |

### Roster — *who plays, in which seat*

One field: **`roster`**, a list with **exactly one participant per Coworld player
slot** (its length must equal the resolved game's player count — Crewrift: 8).
Each participant is:

```jsonc
{ "player": <selector>, "slot": <int, default -1> }
```

**`player` — who fills the seat.** Exactly one selector:

| selector | meaning |
| --- | --- |
| `"policy_ref": "name:vN"` or a raw policy-version UUID | a **specific policy version**, resolved by label or UUID. Must have a READY, non-deleted container image. **Ownership is NOT enforced** — you can name any runnable policy (yours or an opponent's exact version), which is what makes pinned A/B rosters possible. |
| `"top_n": N` (1–100) | fill this seat from the target league/division's **top-N champion pool** (competing champions, runnable, ranked by recent mean reward). Requires a league/division target. |
| `"random": true` | independently sample a champion from the same pool, rank-weighted. Requires a league/division target. |

**Pool selectors resolve PER EPISODE** (verified 2026-06-27 against
`metta/app_backend/src/metta/app_backend/v2/experience_requests.py`:
`create_experience_request_in_db` loops `for job_index in range(num_episodes)` and calls
`experience_request_episode_policy_version_ids(roster, job_index)` for each). Every episode
independently fills its `top_n`/`random` seats by **rank-weighted sampling without replacement**
from a fresh champion pool that regenerates when exhausted, **and rotates the round-robin seats**
by the episode index. So a **single N-episode request already faces a varied field across
episodes** — you do **not** need to fire multiple requests for opponent variety (older docs that
said "resolve once per request → fire multiple requests" are stale; this was fixed). Within one
episode the sampled seats are distinct; across episodes they re-draw. `top_n`/`random` can also draw
**your own champion** into an opponent seat — attribute stats by `policy_version_id`, never by name.
*(To re-check if it drifts: read that resolver, or fire a small `top_n`/`random` request and confirm
the opponent changes across the first episodes' `participants`.)*

**Restricting the champion pool — `included_players` / `excluded_players`** (optional, top-level
arrays of player ids `ply_…` or names). `included_players` restricts the `top_n`/`random` pool to
**only** those players (players named by explicit `policy_ref` seats are always included;
non-matching entries are ignored). `excluded_players` drops those players **entirely** (applied after
`included_players`; they stay out even after the pool regenerates mid-request). Use `included_players`
to **broaden or hand-pick the opponent field** when the live champion pool is thin or you want a
specific set; `excluded_players` to keep a known-bad/irrelevant policy out.

**`slot` — which seat, and whether it rotates.**

| value | meaning |
| --- | --- |
| `-1` (default — omit it) | **round-robin**: rotates through the open (non-pinned) seats, shifting by one each episode |
| `0..N-1` | **pinned**: holds that exact seat every episode. Pinned slots must be unique and in range |

Because the roster has one entry per seat, the round-robin participants always
exactly fill the open seats. Seating recipes:

| want | how |
| --- | --- |
| everyone rotates (cancels per-seat bias) | omit `slot` everywhere (all `-1`) |
| fixed seating | pin every participant to a `slot` |
| **pin yours, rotate the rest** | pin your participant; leave the others at `-1` |

### Roles & seating interaction

| field | meaning |
| --- | --- |
| `game_config_overrides` | shallow override of the resolved Coworld's game config — each key **replaces** that key in the game config, and the result is validated against the game's own schema. **Crewrift:** `{"slots": [{"role": "imposter"}, {"role": "crew"}, ...]}` forces per-slot roles. `slots` is an **array of objects** (`{"role": "crew"\|"imposter", "color"?, "token"?}`), **not** bare strings; supply the **full** array (the merge replaces the whole key). Full Crewrift schema: [`crewrift-gameplay.md` → Forcing roles](../../../docs/reference/crewrift-gameplay.md) |

Roles are fixed **by seat**, so seating decides roles: **pinning a participant to a
seat pins its role**, and a round-robin participant visits every open seat's role
over the episodes. "My policy always imposter at slot 0, opponents rotating through
the other seats/roles" is now a single request — pin yours at `0`, force
`slots[0].role = "imposter"`, leave the opponents at `-1`. (Under the removed
pre-#15572 schema this took one request per role-configuration; it no longer does.)

### Volume & execution

| field | meaning |
| --- | --- |
| `num_episodes` | how many episodes (1–100, default `1`) |
| `execution_backend` | `k8s` (default) or `antfarm` (400 unless enabled server-side) |
| `notes` | free-text label (max 1000 chars), handy for finding the request later |

### Removed fields (pre-#15572 — reject on sight)

`requester`, `requester_slot`, `opponents`, top-level `top_n`, `player_selection`,
`policy_version_ids`, `assignments`, and `rotate_seats` are **gone**
(`additionalProperties: false` rejects them). Their jobs all moved into `roster`:
requester/opponents/policy_version_ids → `policy_ref` participants; top-level
`top_n` + `player_selection` → per-seat `top_n` / `random` selectors;
`rotate_seats`/`requester_slot`/`assignments` → per-participant `slot`. There are
also **no `player_id`/`player_name` selectors** anymore — resolve a player's
current version to a `policy_ref` first (see below).

## Building blocks — resolving the IDs

The body needs real refs. Resolve them live (don't hardcode — they rotate):

```sh
uv run coworld leagues --json                       # GET /v2/leagues
uv run coworld divisions --league <league_id> --json
uv run coworld results <division_id> --json         # standings (the leaderboard)
uv run coworld memberships --division <division_id> --active-only --json
```

Underlying routes (raw): `GET /v2/leagues`, `GET /v2/divisions`,
`GET /v2/divisions/{id}/leaderboard` (current champions, ranked by recent mean
reward), `GET /v2/league-policy-memberships?division_id=…&active_only=true&limit=1000`
(the active memberships and their policy versions). Resolve a policy
**name → version** with `GET /stats/policy-versions?name_exact=<name>` — though for
`policy_ref` you usually only need the **label** `name:vN`, no UUID. For an
opponent, take the policy name + version from the leaderboard/membership row and
write it as `policy_ref: "name:vN"`; that exact version is then pinned (it won't
drift if they upload a new one mid-experiment).

## Composition — examples to adapt (not a fixed menu)

**Your policy vs the live division's top 7 champions** (auto-select; everyone
rotates; random roles) — 8 seats, so 8 participants:

```json
{
  "target": {"division_id": "div_…"},
  "roster": [
    {"player": {"policy_ref": "crewborg:v24"}},
    {"player": {"top_n": 7}}, {"player": {"top_n": 7}}, {"player": {"top_n": 7}},
    {"player": {"top_n": 7}}, {"player": {"top_n": 7}}, {"player": {"top_n": 7}},
    {"player": {"top_n": 7}}
  ],
  "num_episodes": 100,
  "notes": "crewborg vs the live top-7, random roles, all seats rotating"
}
```

**Vs explicit, pinned opponents** (stable, reproducible roster — the A/B shape):
name every seat's exact version and pin every slot, so both arms are identical
except the subject:

```json
{
  "target": {"division_id": "div_…"},
  "roster": [
    {"player": {"policy_ref": "crewborg:v24"}, "slot": 0},
    {"player": {"policy_ref": "notsus:v3"}, "slot": 1},
    {"player": {"policy_ref": "evidencebot:v7"}, "slot": 2},
    {"player": {"policy_ref": "slava2:v5"}, "slot": 3},
    {"player": {"policy_ref": "…"}, "slot": 4},
    {"player": {"policy_ref": "…"}, "slot": 5},
    {"player": {"policy_ref": "…"}, "slot": 6},
    {"player": {"policy_ref": "…"}, "slot": 7}
  ],
  "num_episodes": 50
}
```

**Force roles (Crewrift) and pin yours while the rest rotate** —
`game_config_overrides.slots` is an **array of objects**; supply the full array.
`role` ∈ `{"crew","imposter"}`. Your participant pinned to slot 0 keeps the
imposter role every episode; the others cycle through seats 1–7 (so each takes the
slot-7 partner-imposter role in some episodes):

```json
{
  "target": {"division_id": "div_…"},
  "roster": [
    {"player": {"policy_ref": "crewborg:v24"}, "slot": 0},
    {"player": {"top_n": 7}}, {"player": {"top_n": 7}}, {"player": {"top_n": 7}},
    {"player": {"top_n": 7}}, {"player": {"top_n": 7}}, {"player": {"top_n": 7}},
    {"player": {"top_n": 7}}
  ],
  "game_config_overrides": {"slots": [
    {"role": "imposter"}, {"role": "crew"}, {"role": "crew"}, {"role": "crew"},
    {"role": "crew"}, {"role": "crew"}, {"role": "crew"}, {"role": "imposter"}
  ]},
  "num_episodes": 50,
  "notes": "crewborg pinned imposter @0; field rotates through crew + partner-imposter"
}
```

`create` validates this `slots` shape against the live game config schema before
POSTing (see the tool's `game_config_overrides` check), so a wrong shape fails locally
with a clear message instead of as an opaque 400.

**Ad-hoc, no league** — use `"target": {"coworld_id": "cow_…"}` and `policy_ref`
selectors only (`top_n`/`random` need a league/division target).

Mix these freely: target × selectors × pinning × roles × count are independent
knobs.

## After you POST: readback, poll, pull

The response is `V2ExperienceRequestDetail`: `id` (`xreq_…`), `status`, and the
counts `episode_count` / `pending_count` / `running_count` / `completed_count` /
`failed_count`, plus `episodes[]`.

- **Dispatch is asynchronous**: the POST returns immediately with every child
  `pending`; a background maintenance loop dispatches them within seconds (and
  redispatches/retries failures within a bounded budget). A `get` right after
  `create` showing all-pending is normal.
- **Verify** the resolution: `episode_count` matches your `num_episodes`, and the
  first episodes' `participants` seat the policies/versions you intended (pinned
  seats where you pinned, the champion spread you expected).
- **Poll**: `GET /v2/experience-requests/{id}` until `completed_count + failed_count
  == episode_count`.
- **Child episodes**: `GET /v2/experience-requests/{id}/episodes` (each is an
  `ereq_…` row with participants, scores, status, `job_id`).
- **Artifacts**: pull replays / logs / results per episode with the
  `coworld-episode-artifacts` skill (key off `job_id`), then analyze the per-episode
  `scores` / `participants` (and replays/logs) however the question needs.

## Gotchas

- **`additionalProperties: false`** — no stray keys, and in particular none of the
  removed pre-#15572 fields (`requester`, `opponents`, `rotate_seats`, …). If a
  reference or old body template mentions them, it's stale.
- **`roster` length is exact.** One participant per seat — for Crewrift that's
  always 8 entries, even when 7 are the same `{"top_n": 7}` selector. Too few/many
  is a 400.
- **`top_n`/`random` sample the live pool — varied per episode (good), but uncontrolled.** The pool
  is the *current* champions, so it drifts as memberships change and can seat **your own league
  entry** as an opponent. For a clean, reproducible **A/B**, pin the full roster with explicit
  `policy_ref`s; to **shape the pool** without full pinning, use `included_players` / `excluded_players`.
- **Ownership is not enforced for `policy_ref`** — you can (and for A/Bs should)
  name opponents' exact versions. The only requirement is a READY, non-deleted
  image.
- **POST-then-404 replica lag.** A freshly created request can 404 on readback for a
  beat. If the POST body contained an `xreq_…` id, retry the GET before assuming
  failure.
- **Auth drift.** Use `softmax.auth.load_current_token(server=…)`; the older
  `load_current_cogames_token(api_server=…)` was removed.
- **Schema drift.** Re-print the live `V2CreateExperienceRequestRequest` before any
  real submission; `coworld`'s create helpers validate payload keys against live
  OpenAPI (use a dry-run/`--check-schema` path when testing drift).
