# Crewrift Event Warehouse Design

Status: prototype
Workspace: `/Users/jamesboggs/coding/role_repos/reporter_lab/crewrift-event-warehouse`
Builds on: `crewrift-event-reporter` (per-episode event extraction)
Design date: 2026-06-24

## Goal

Turn an arbitrarily large batch of Crewrift episodes into a single **queryable
behavioral database**, indexed by policy rather than by episode slot.

The existing `crewrift-event-reporter` is the right per-episode primitive, but it
has a structural ceiling for cross-episode analysis:

- It requires **exactly one** episode per request.
- Its only player axis is `player` = **slot** (0–7). A slot is meaningless across
  episodes — slot 3 is a different policy every game — so the single-episode
  output cannot answer "how does *this policy* behave across 500 games."

This warehouse sits on top of the event reporter. It runs the same extraction
across every episode in a batch, **re-keys each event from slot to policy
identity and role**, and collates everything into a partitioned Parquet dataset
that DuckDB / polars / pandas read directly with no load step.

This is an **AI-optimizer-facing data build**, not a human-facing report. There
is no narrative, no HTML, no LLM, and no Column post. The output is a dataset you
query however you want.

## Non-Goals

- No WebSocket service. This is an offline CLI / library.
- No narrative, HTML, leaderboard, or Column posting (that is the sportscaster's
  job, which is explicitly out of scope here).
- No new event extraction logic. Extraction is reused verbatim from the event
  reporter; the warehouse only fans out, enriches, and collates.
- No pre-baked reports. The dataset *is* the deliverable; specific analyses are
  queries run against it later.

## The Core Problem: Slot → Policy Re-Keying

Every event row produced by the event reporter is `{ts, player, key, value}`,
where `player` is a slot. To make events comparable across episodes we resolve,
**within each episode**, each slot to a stable identity:

| Dimension | Source | Why it lives there |
| --- | --- | --- |
| `policy_version` | `PlayerIdentity.player_id` (request) | Stable cross-episode primary key (policy version UUID). |
| `policy_name` | `PlayerIdentity.display_name` (request) | Groups versions of one policy (`Paz-Bot-9000:v97`). |
| `role` | `results.json` `crew[]` / `imposter[]` (per episode) | Role is assigned per game, not per policy — the *same* policy is imposter in some episodes, crew in others. |

`role` is the subtle one: it is **not** in `PlayerIdentity`. It comes from the
episode's `results.json`, where `crew[slot]==1` means crew and `imposter[slot]==1`
means imposter. "Analysis by role" therefore means grouping by policy × the role
it held *in that episode*.

Slot is retained as a within-episode locator (needed to resolve the *other*
slots embedded in interaction events — see below), but `policy_version` is the
cross-episode join key.

## Output: Star Schema

Two tables. Interaction events embed *other* slots inside their `value` JSON
(`proximity_interval` has `player_a`/`player_b`; `following_interval` has
`follower`/`target`; `near_body_interval` has `victim_slot`). Those references
are still raw slots. A two-table star schema lets any embedded slot resolve to a
policy/role with a one-line join, instead of pre-baking a fixed set of companion
columns into every event (which would need schema surgery each time a new
interaction key is added).

### `events` (fact table)

The enriched event stream — one row per event, every episode concatenated.

| column | type | meaning |
| --- | --- | --- |
| `ts` | int64 | replay tick |
| `episode_id` | string | episode request id (`ereq_…`) |
| `slot` | int32 | actor slot, or `-1` for global events |
| `policy_version` | string | actor's policy version id, null for global rows |
| `policy_name` | string | actor's policy display name, null for global rows |
| `role` | string | `crew` / `imposter` / `unknown`, null for global rows |
| `key` | string | event key (`entered_room`, `following_interval`, …) |
| `value` | string | the original event payload as a compact JSON string |

`value` stays a JSON string for exactly the reason the event reporter keeps it
that way: payloads are heterogeneous, and a fixed top-level schema lets every
consumer read the same columns regardless of event type. Embedded slots stay in
`value`; you resolve them by joining `episode_players` on `(episode_id, slot)`.

### `episode_players` (dimension table)

One row per `(episode_id, slot)`. Resolves any slot — the actor's or an embedded
one — to identity, role, and per-episode outcome.

| column | type | meaning |
| --- | --- | --- |
| `episode_id` | string | episode request id |
| `slot` | int32 | player slot |
| `policy_version` | string | policy version id |
| `policy_name` | string | policy display name |
| `role` | string | `crew` / `imposter` / `unknown` |
| `score` | float64 | final score |
| `win` | bool | won the episode |
| `tasks` | int32 | tasks completed |
| `kills` | int32 | kills |
| `identity_source` | string | how identity was resolved (audit) |

`identity_source` follows the event reporter / sportscaster fallback chain:
`request.player_id` → `request.display_name` → `results.names` → `slot:N`.

### Example queries

Every question on the original list is a `GROUP BY`, some with one join:

```sql
-- How often does each policy enter each room? Most common room per policy.
SELECT policy_name, json_extract_string(value, '$.room') AS room, count(*) AS visits
FROM events WHERE key = 'entered_room' AND slot >= 0
GROUP BY policy_name, room ORDER BY policy_name, visits DESC;

-- How closely does a policy follow others, and what role were the followed in?
SELECT f.policy_name AS follower, tgt.policy_name AS followed, tgt.role AS followed_role,
       avg(json_extract(e.value, '$.alignment_ratio')::double) AS mean_alignment
FROM events e
JOIN episode_players f   ON f.episode_id = e.episode_id AND f.slot = e.slot
JOIN episode_players tgt ON tgt.episode_id = e.episode_id
                        AND tgt.slot = json_extract(e.value, '$.target')::int
WHERE e.key = 'following_interval'
GROUP BY follower, followed, followed_role;
```

## Layout: Partitioned Parquet Dataset

Output directory:

```text
<out>/
  events/
    key=entered_room/<ereq>.parquet
    key=following_interval/<ereq>.parquet
    ...
  episode_players.parquet
  manifest.json
```

`events` is **Hive-partitioned by `key`**. `key` has a bounded cardinality (~30
values), partitions are reasonably balanced, and nearly every analytical query
filters by key first — so partition pruning is a direct win. Each parallel worker
writes its own per-episode shard inside each `key=` partition (`<ereq>.parquet`),
so writes never collide and need no cross-process coordination. Within a shard,
rows are sorted `(ts, slot)` so Parquet row-group stats prune further on `ts`.

`episode_players` is small (episodes × 8) and written as one file.

`manifest.json` records the batch summary: episode count, distinct policies,
schema versions, per-episode extraction status, and any warnings (e.g. episodes
whose replay trace was partial).

DuckDB reads it with zero setup:

```sql
SELECT * FROM read_parquet('<out>/events/**/*.parquet', hive_partitioning = true);
SELECT * FROM read_parquet('<out>/episode_players.parquet');
```

## Pipeline

```text
batch input (one or more report_request.json, or a dir of them)
  -> load + flatten into a list of (episode) inputs
  -> ProcessPoolExecutor: per episode, in parallel:
       extract_episode_rows(episode)          # reused from event reporter
       build episode_players dim rows          # from results.json + PlayerIdentity
       enrich each event row with the actor slot's policy_version/name/role
       write events/key=<k>/<ereq>.parquet shards for this episode
       return (dim_rows, episode_summary)       # small payloads only
  -> concatenate dim rows -> episode_players.parquet
  -> write manifest.json
```

Synchronous and process-parallel. Each episode is an independent, CPU/IO-bound
unit (it shells out to `expand_replay` and does PyArrow work), so a process pool
gives true parallelism with no `async`. Workers **stream their output to disk**
and return only small dim/summary payloads, so peak memory is bounded by one
episode's rows per worker, not the whole batch — this is what lets the batch be
arbitrarily large.

### Reuse and the one refactor

The event reporter's `service.build_and_write_report` couples extraction to
zip/write. We extract a pure function:

```python
def extract_episode_rows(episode: ReporterEpisodeInput) -> list[EventRow]: ...
```

`build_and_write_report` is rewritten to call it (behavior identical; existing
tests stay green). The warehouse imports `crewrift_event_reporter` as a path
dependency and reuses `bundles`, `replay`, `analysis`, and `events` unchanged.

Results parsing (role, score, win, tasks, kills) is a small self-contained model
in this package — it does **not** import the sportscaster, which is out of scope.

The reuse is **in-process**: the warehouse imports and calls `extract_episode_rows`
directly inside its worker processes, not over a service boundary. So extraction
shells out to the same compiled `expand_replay` binary the reporter uses, and that
binary must exist in the warehouse runtime. It is **not** recompiled: the warehouse
Docker image builds `FROM` the event-reporter image, inheriting the already-bundled
helper (`/usr/local/bin/crewrift-expand-replay`), the `/workspace/crewrift` tree, and
the installed reporter package. One sim compile, inherited — no duplication.

## Inputs

The batch comes from one or more `report_request.json` files — exactly the shape
`tmp/round-loop/fetch_round.py` already produces (an `episodes` list with
presigned/`file://` artifact refs and a per-slot `players` identity list). The
CLI accepts:

- one or more `report_request.json` paths, and/or
- directories scanned for `report_request.json` (e.g. one per round),

flattens their `episodes` into a single batch, and de-duplicates by
`episode_request_id` so overlapping rounds don't double-count.

Fetching artifacts for a round stays the job of `fetch_round.py`. The warehouse
consumes what it produces; it does not call the `coworld` CLI itself.

## CLI

```sh
crewrift-event-warehouse build \
  --input round-1959/report_request.json \
  --input round-1958/ \
  --out warehouse/ \
  --workers 8
```

Options: `--input` (repeatable; file or dir), `--out`, `--workers`
(default = CPU count), `--snapshot-every` (passthrough to the helper),
`--partition-by` (default `key`; `policy_version` as an alternative axis).

## Failure Behavior

- A single episode that fails extraction (missing/unreadable artifact, helper
  crash with no parseable rows) is **recorded in the manifest and skipped**, not
  fatal — one bad episode must not sink a 500-episode batch.
- Partial replay traces propagate the event reporter's existing `trace_warning`
  rows into `events`; the episode is flagged in the manifest.
- A failed-status episode (`status != "success"`) is skipped with a manifest
  note, consistent with the event reporter requiring successful episodes.

## Open Questions

1. Should `episode_players` also carry vote stats (`vote_players`, `vote_skip`,
   `vote_timeout`)? Cheap to add; deferred until a query needs them.
2. Worth a second partition axis (`policy_version`) for policy-centric scans, or
   does row-group pruning on the `key`-partitioned set suffice? Start with `key`.
3. `room_occupancy` is in the event reporter's *designed* key list but may not be
   emitted by the current helper. The occupancy question depends on it — verify
   before relying on it; routes/proximity/following are confirmed produced.
```
