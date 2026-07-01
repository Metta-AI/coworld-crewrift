# Observatory episode-artifact endpoint map

The authoritative live route list is always `<base>/openapi.json`. Read it when a
route 4xxs — **the server moves faster than the published `coworld` client**, and
this skew is the single most common cause of artifact-download breakage here.

`<base>` defaults to the official gateway derived from your `softmax login`:
`<api-server>/observatory` (today `https://softmax.com/api/observatory`). The same
routes are also served directly at `https://api.observatory.softmax-research.net`
(root, no `/observatory` segment); pass `--server` to switch.

Auth: `X-Auth-Token: <token>` header. In Python use
`softmax.auth.load_current_token(server=softmax.auth.get_api_server())` — **not**
the removed `load_current_cogames_token(api_server=...)` (see "Drift" below).

## The two episode populations

| | League / tournament episodes | Experience-request episodes |
| --- | --- | --- |
| What | League round games (e.g. a Crewrift league) | Ad-hoc / commissioner-created hosted episodes |
| Listed by | `/stats/policy-versions` → `/episodes?policy_version_id=` | `/v2/episode-requests` (by container) or `/v2/experience-requests/{xreq}/episodes` |
| Id form | bare uuid (`/episodes/{uuid}`) | `ereq_...` |
| Metadata record | `/episodes/{id}` → `replay_url`, `policy_results`, `game_stats`, `tags` | the row itself → `participants`, `scores`, `status`, `replay_url`, `game_config` |
| `job_id` | in `tags.job_id` | top-level `job_id` field |

They are **disjoint populations**: a league episode's `pool_id` returns **0** rows
from `/v2/episode-requests?pool_id=`, and `coworld episodes --policy <league-player>`
is empty. Don't try to cross them — discover each in its own world.

## The universal artifact key: `job_id`

Every episode in **both** worlds carries a `job_id`, and the job is the universal
artifact handle. All artifacts come from these job routes (verified live 2026-06-08,
for both a Crewrift league episode and an amongthem experience-request episode):

| Route | Returns |
| --- | --- |
| `GET /jobs/{job_id}/artifacts/results` | `results.json` (scores / metrics / win / per-agent) |
| `GET /jobs/{job_id}/artifacts/replay` | replay bytes (zlib-compressed; magic `0x78`) |
| `GET /jobs/{job_id}/artifacts/error_info` | `error_info.json` — **404 when absent** (valid type; present only on failure) |
| `GET /jobs/{job_id}/policy-logs` | JSON list of filenames `["policy_agent_0.log", ...]` |
| `GET /jobs/{job_id}/policy-logs/{agent_idx}` | one agent's stderr trace |
| `GET /jobs/{job_id}/policy-artifact` | JSON list of **filenames** (`["policy_artifact_0.zip", ...]`), one per slot that uploaded — *not* bare slot ints; parse the index out |
| `GET /jobs/{job_id}/policy-artifact/{agent_idx}` | one slot's `policy_artifact_{idx}.zip` (player-uploaded telemetry/debug bundle; **policy-scoped** — only slots you own) |

The replay decompresses (zlib) to the game's binary replay (e.g. magic
`CREWRIFT...`) — the directly-loadable form. Keep the raw `.z` too.

### Dead ends (do not use)
- `GET /v2/episode-requests/{ereq}/artifacts/{type}` — `results`/`replay`/`game_logs`/`stats`
  all return **400 "Unknown artifact type"**; only the `/jobs/{job_id}/...` routes serve these.
- `GET /v2/experience-request-episodes...` — **gone** (renamed away ~2026-06; an
  older `fetch_episodes.py` keyed logs off this and now fails here).
- `coworld_id` / `job_id` / `episode_id` as query params on `/v2/episode-requests`
  are **silently ignored** (not real filters). Only `pool_id`, `round_id`,
  `division_id`, `player_id` filter server-side. A **bare** pool uuid 422s — the
  value must carry the `pool_` prefix.

## Discovery routes

```
GET /stats/policy-versions?name_exact=<name>&limit=100      -> [{id, version, policy_id, ...}]
GET /episodes?policy_version_id=<pv>&limit&offset           -> [episode record, ...]
GET /episodes/{episode_id}                                  -> single league episode record
GET /v2/episode-requests?pool_id=pool_<uuid>|round_id|division_id|player_id&limit&offset
                                                           -> {entries, total_count, limit, offset}
GET /v2/episode-requests/{ereq_id}                          -> single experience-request episode row
GET /v2/experience-requests/{xreq_id}/episodes             -> [experience-request episode row, ...]
GET /v2/experience-requests?mine&limit&offset              -> {entries, ...} (the xreq batteries, not episodes)
```

`/episodes` and `/stats/policy-versions` return bare lists (the latter may also be
`{entries:[...]}`). `/v2/episode-requests` is paginated as
`{entries, total_count, limit, offset}` — `total_count` is the whole table
(hundreds of thousands), so always filter by a container; never page it blindly.

## Official `coworld` CLI equivalents (for interactive use)

These work today against the live server (they hit `/v2/episode-requests` +
`/jobs/{job_id}/...` under the hood) but only cover the experience-request world:

```bash
uv run coworld episodes --pool pool_... --json       # list ereq episodes
uv run coworld replays  --round round_... --download-dir replays/
uv run coworld episode-results ereq_... --output results.json
uv run coworld episode-logs ereq_... --download-dir logs/
uv run coworld replay <coworld_id> <replay_file>     # open a replay in the viewer
```

For league episodes by policy, and for bundling everything per episode in one
pass, use this skill's `fetch_artifacts.py` instead.

## Drift log (why this file exists)

- **2026-06-27**: re-verified the discovery split live — `coworld episodes --policy crewborg`
  returns `[]` (champion league player), while `/stats/policy-versions` → `/episodes` lists its
  league games (the `fetch_artifacts.py --policy` path downloaded a current league episode). The
  two-population model + the `job_id` artifact routes still hold.
- **2026-06-10**: added the per-player artifact routes
  (`/jobs/{job_id}/policy-artifact[/{agent_idx}]`) — players may upload one
  telemetry/debug zip per slot to a runner-provided
  `COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` (metta #15290; player-side support in the
  players SDK's `TraceOutputs`). **Verified live 2026-06-10** against crewborg v18
  hosted episodes (after metta #15409 fixed the runner-image build so the upload
  actually ships). Note the listing returns **filenames**, not slot ints.

- **2026-06**: `/v2/episode-requests*` ↔ `/v2/experience-request*` churn; the
  `/v2/experience-request-episodes` route was removed.
- **~2026-06 (auth)**: `softmax.auth.load_current_cogames_token(api_server=...)` →
  `softmax.auth.load_current_token(server=...)`. The old name is gone; tools that
  still call it (e.g. an older `crewrift/crewborg/scripts/fetch_episodes.py`) fail at auth
  until updated.

When you hit drift: diff `<base>/openapi.json` against the routes above, fix the
path, and add a dated line here.
