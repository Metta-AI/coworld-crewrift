# crewrift-event-warehouse

Offline batch tool that turns an arbitrarily large set of Crewrift episodes into
a **policy-indexed, queryable Parquet dataset** plus a local HTML query
dashboard. It runs the per-episode event extraction from
[`crewrift-event-reporter`](../crewrift-event-reporter) **in-process** across a
whole batch, re-keys every event from episode *slot* to **policy version /
policy name / role**, and collates the result into a DuckDB-friendly star
schema (an `events` fact table + an `episode_players` dimension table).

This is an AI-optimizer-facing data build, not a human report: no narrative, no
LLM, no Column posting. It is a **CLI**, not a WebSocket service. It reuses the
event reporter's `expand_replay` integration, so it carries the same
sim-version-coupling caveat (see [version coupling](#expand_replay-version-coupling)).
See [`crewrift-event-warehouse-design.md`](./crewrift-event-warehouse-design.md)
for the full rationale.

## Quick reference

| | |
| --- | --- |
| **Input** | one or more `report_request.json` files (or directories scanned for them); episodes de-duplicated by `episode_request_id` |
| **Output** | a partitioned dataset directory: `events/key=<k>/<ereq>.parquet` (fact), `episode_players.parquet` (dimension), `manifest.json` (batch summary) |
| **LLM** | none |
| **Replay helper** | CrewRift `expand_replay` binary (used in-process via the event reporter). Must match the arena sim version — see [version coupling](#expand_replay-version-coupling) |
| **Entry points** | console script `crewrift-event-warehouse build` and `crewrift-event-warehouse serve`. No WS service |
| **Run locally** | `uv run crewrift-event-warehouse build --input round-1959/report_request.json --out warehouse/` then `uv run crewrift-event-warehouse serve --out warehouse/` |

## Why

The event reporter is per-episode and its only player axis is `slot` (0–7),
which is meaningless across games. The warehouse resolves each slot to a stable
policy identity (and the role it held *that* episode) so you can ask
cross-episode, by-policy, by-role questions over many rounds at once.

## How to call it

The warehouse has two subcommands. `duckdb` is a runtime dependency, so the
`serve` dashboard and direct DuckDB queries work without any extra install.

### `build` — construct the dataset

Build the input with the existing round fetcher (it emits `report_request.json`):

```sh
python ../tmp/round-loop/fetch_round.py <round_id> round-1959/
```

Then build the warehouse over one or more rounds:

```sh
uv run crewrift-event-warehouse build \
  --input round-1959/report_request.json \
  --input round-1958/ \
  --out warehouse/ \
  --workers 8
```

`build` flags:

| Flag | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--input` / `-i` | Yes | — | A `report_request.json` file, or a directory scanned recursively for them. **Repeatable.** |
| `--out` / `-o` | Yes | — | Output dataset directory. |
| `--workers` / `-w` | No | CPU count | Parallel worker processes. |
| `--snapshot-every` | No | `1` | Replay snapshot cadence passed through to `expand_replay` (sets `CREWRIFT_EVENT_SNAPSHOT_EVERY` for the build). |

`--input` accepts a file or a directory (scanned recursively for
`report_request.json`); episodes are de-duplicated by `episode_request_id`. The
process must have `CREWRIFT_EXPAND_REPLAY` pointing at a compatible helper
binary (see [version coupling](#expand_replay-version-coupling)).

#### Incremental builds

Repeated `build` runs against the same `--out` are **incremental**: episodes
already recorded `ok` (and not trace-warned) in the existing `manifest.json`
are skipped — no replay re-expansion — while `failed` / `trace_warning`
episodes are re-attempted (their old event shards are deleted first).
`manifest.json` and `episode_players.parquet` are **merged** with the prior
build rather than overwritten, so a build over a growing episode set only pays
for the new episodes. The manifest's `episodes_cached` counts this run's cache
hits. Delete the `--out` directory for a from-scratch rebuild (e.g. after
fixing a version-skewed `CREWRIFT_EXPAND_REPLAY`, since `ok` episodes are
trusted and never re-checked).

### `serve` — query dashboard

```sh
uv run crewrift-event-warehouse serve --out warehouse/
# -> http://127.0.0.1:8765
```

`serve` flags:

| Flag | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--out` / `-o` | Yes | — | Warehouse dataset directory to query. |
| `--host` | No | `127.0.0.1` | Bind host. |
| `--port` / `-p` | No | `8765` | Bind port. |

A single-page UI with a free-form SQL editor (⌘/Ctrl+Enter to run), one-click
preset queries for the common questions (room frequency, routes, follow distance
with the followed player's role, per-role win rates), and a schema/manifest
sidebar. It runs a small stdlib HTTP server backed by native DuckDB, so it
scales to large datasets. In your SQL, write `{events}` and `{players}` — they
expand to the dataset's `read_parquet(...)` expressions.

### `suss` — label who-susses-who (LLM)

```sh
uv run crewrift-event-warehouse suss --out warehouse/
```

Extends a **built** warehouse with an `events/key=chat_suss` partition: each meeting
`chat` message is labelled with the player it accuses (its "sus" target) via an LLM
(Bedrock **Haiku 4.5**), then resolved to that target's slot / role / policy *per
episode*. Rows are keyed by the **speaker** (like other attributed events); the `value`
JSON adds `suss_target_color`, `suss_target_slot`, `suss_target_role`,
`suss_target_policy`, `is_suss`, and `target_is_imposter`.

This is what makes **suss-rate** queryable — e.g. "when policy X (as crew) accuses
someone, how often is the target actually an imposter":

```sql
SELECT policy_name, role,
  COUNT(*) FILTER (WHERE json_extract_string(value,'$.is_suss')='true') AS susses,
  100.0*COUNT(*) FILTER (WHERE json_extract_string(value,'$.target_is_imposter')='true')
    / NULLIF(COUNT(*) FILTER (WHERE json_extract_string(value,'$.is_suss')='true'),0) AS suss_accuracy_pct
FROM {events} WHERE key='chat_suss' GROUP BY 1,2;
```

Details: the target color depends only on the message text, so distinct texts are
classified once and cached in `chat_suss_cache.json` (re-runs are cheap; `--refresh`
forces re-classification). Needs **AWS creds + Bedrock access** (the dep `boto3` is
bundled; uses `us.anthropic.claude-haiku-4-5-...` in `us-east-1`). Idempotent —
re-running overwrites the partition.

`suss` flags:

| Flag | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--out` / `-o` | Yes | — | Built warehouse dataset directory to extend. |
| `--refresh` | No | off | Re-classify all chat texts, ignoring the cache. |

## Input contract

`load_batch` flattens each `report_request.json` and reads its top-level
`episodes` list — exactly the shape the metta reporter-runner backend sends and
the shape `tmp/round-loop/fetch_round.py` emits. Each episode is a
`ReporterEpisodeInput` (the event reporter's protocol type): `episode_request_id`,
`status`, a bundle-style `manifest`, presigned/`file://` per-token `artifacts`
(the warehouse reads `results` and the zlib `replay`), and a `players` list of
`{slot, player_id, display_name}` for stable cross-seat identity.

Per-episode handling is isolated — one bad episode never sinks the batch:

- `status != "success"` → episode **skipped** (recorded in the manifest, no rows).
- Any extraction exception (missing artifact, decode failure, helper error with
  no output) → episode **failed** (recorded in the manifest, no rows).
- Otherwise → **ok**, rows written.

## Output contract

A partitioned dataset directory:

```text
<out>/
  events/key=<event_key>/<ereq>.parquet   # fact table, hive-partitioned by key
  episode_players.parquet                 # dimension table (slot -> policy/role/outcome)
  manifest.json                           # batch summary + per-episode status
```

`events` fact table columns:

| column | type | meaning |
| --- | --- | --- |
| `ts` | `int64` | replay tick |
| `episode_id` | `string` | source episode (`episode_request_id`) |
| `slot` | `int32` | actor slot, or `-1` for global rows (null identity) |
| `policy_version` | `string` | stable policy id (request `player_id`); null when only a name was available |
| `policy_name` | `string` | resolved display/policy name |
| `role` | `string` | role this slot held *this* episode |
| `key` | `string` | event key (also the partition value) |
| `value` | `string` | original event payload as a JSON string |

Slots embedded *inside* `value` (e.g. `following_interval.target`) resolve via a
join to `episode_players` on `(episode_id, slot)`.

`episode_players` dimension table columns: `episode_id, slot, policy_version,
policy_name, role, score, win, tasks, kills, identity_source`. `identity_source`
records how the slot was resolved (`request.player_id` → `request.display_name`
→ `results.names` → `slot`); `policy_version` is non-null only for the first.

`manifest.json` fields: `schema_version` (`crewrift-event-warehouse/v1`),
`episodes_total`, `episodes_ok`, `episodes_skipped`, `episodes_failed`,
`episodes_cached` (this run's incremental-build cache hits), `events_written`,
`distinct_policies`, `event_keys` (sorted), and `episodes[]` (sorted by
`episode_id`) with per-episode `episode_id`, `status`, `event_count`,
**`trace_warning`** (bool; the version-skew signal — see below), and `message`.
Summary counts describe the **merged warehouse**, not just the latest call
(see "Incremental builds" above).

Load it with DuckDB:

```python
import duckdb
con = duckdb.connect()
con.execute("CREATE VIEW events AS SELECT * FROM read_parquet('warehouse/events/**/*.parquet', hive_partitioning=true)")
con.execute("CREATE VIEW episode_players AS SELECT * FROM read_parquet('warehouse/episode_players.parquet')")

# Most common room per policy
con.sql("""
  SELECT policy_name, json_extract_string(value,'$.room') AS room, count(*) AS visits
  FROM events WHERE key='entered_room' AND slot>=0
  GROUP BY policy_name, room ORDER BY policy_name, visits DESC
""")
```

## Configuration / environment variables

The warehouse passes these through to the event reporter's extraction code; the
same defaults apply as for `crewrift-event-reporter`.

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `CREWRIFT_EXPAND_REPLAY` | Effectively yes | `crewrift-expand-replay` (on `PATH`) | Path to the compiled CrewRift `expand_replay` helper. Must match the arena sim version (see below). |
| `CREWRIFT_EVENT_SNAPSHOT_EVERY` | No | `1` | Replay snapshot cadence. The `--snapshot-every` build flag sets this. |
| `CREWRIFT_EVENT_NEAR_DISTANCE` | No | `32` | Proximity threshold in map pixels. |
| `CREWRIFT_EVENT_BODY_DISTANCE` | No | `36` | Body proximity threshold in map pixels. |
| `CREWRIFT_EVENT_GROUP_DISTANCE` | No | `44` | Third-player proximity threshold for isolation/group context. |
| `CREWRIFT_EVENT_MIN_INTERVAL_TICKS` | No | `24` | Minimum duration for interval events. |

## Runtime / Docker

The warehouse reuses the event reporter's extraction code **in-process** — it
imports `extract_episode_rows` and runs it across a process pool — and that code
shells out to the compiled CrewRift `expand_replay` binary. So the binary must
be present wherever the warehouse runs.

It is **not** recompiled for the warehouse. The warehouse image builds `FROM` the
event-reporter image, inheriting the already-bundled helper
(`/usr/local/bin/crewrift-expand-replay`), the `/workspace/crewrift` tree, and the
installed `crewrift_event_reporter` package:

```sh
# 1. Build the base reporter image (compiles the helper once):
docker build --platform linux/amd64 \
  --build-context crewrift=/Users/jamesboggs/coding/coworlds/coworld-crewrift \
  -t crewrift-event-reporter:local ../crewrift-event-reporter

# 2. Build the warehouse on top (no recompile):
docker build -t crewrift-event-warehouse:local .
```

The image is self-contained: no host Nim toolchain is needed. For local
(non-Docker) dev, point `CREWRIFT_EXPAND_REPLAY` at a helper binary you built
separately. Parsing inputs and querying the output need no helper at all.

## Local development

```sh
uv run --python 3.12 python -m pytest
```

Tests use a fake `expand_replay` helper, so the suite runs without the Nim build.

## Troubleshooting / common failures

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| **Thin / sparse events; many episodes built ok but row counts are tiny** | Replay hash failure / sim version skew — the #1 silent failure. Episodes whose replay aborted carry `trace_warning: true` in `manifest.json` and a `trace_warning` row in `events/key=trace_warning/`. | Count `trace_warning: true` episodes in `manifest.json` **first**. If many, rebuild `CREWRIFT_EXPAND_REPLAY` from the arena's exact commit — see [version coupling](#expand_replay-version-coupling). The warehouse keeps partial episodes; it does not drop them. |
| High `episodes_skipped` | Those episodes had `status != "success"` in the input. | Expected — failed games carry no replay to analyze. |
| High `episodes_failed` | Extraction raised (missing `replay`/`results` ref, decode error, or `expand_replay` exited nonzero with no output at all). | Inspect the per-episode `message` in `manifest.json`; verify artifact refs resolve and the helper binary is valid. |
| `crewrift-event-warehouse: command not found` | Package not installed into the active env. | Run via `uv run crewrift-event-warehouse ...` from the project dir, or install the package. |
| `FileNotFoundError: no report_request.json found under ...` | `--input` pointed at a path with no request files. | Point at a `report_request.json` file or a directory containing them. |
| Empty `policy_version` column / grouping mixes policies | Slots resolved by display name or results only (no request `player_id`). Check `identity_source` in `episode_players`. | Ensure the input `players` list carries `player_id`. Group by `policy_name` if version ids are unavailable. |

### expand_replay version coupling

The CrewRift `expand_replay` helper RE-STEPS the sim and hash-checks every tick.
On a sim-version mismatch it ABORTS mid-replay, emitting a `trace_warning`
(`message:"hash failed"`, `fail_tick:N`) plus `trace_complete:false`, yielding
only PARTIAL events (early `player_state` / `entered_room`; no kills, bodies,
votes, or following). There is NO flag to disable the check (only `--format`,
`--snapshot-every`). The warehouse KEEPS partial/hash-failed episodes (it does
not drop them) and surfaces the per-episode `trace_warning` flag in
`manifest.json` plus a `trace_warning` event partition — so a version-skew run
looks like sparse output, not an error.

CONSEQUENCE: the expander binary (`CREWRIFT_EXPAND_REPLAY`) must be built from
the EXACT crewrift commit the arena deployment ran when it recorded the replays
— not local `master`, which drifts ahead and hash-fails.

How to find the right version:

- `coworld episodes --round <id> --json` → each episode's `coworld_version`
  (e.g. `crewrift:0.1.54`) — a published image version, NOT a git tag.
- `coworld download <cow_id> -o <dir>` pulls the runtime image; its
  `/bin/crewrift` mtime pins the build time. Build the expander from the
  crewrift master commit at/just-before that time and VERIFY (exit 0 +
  `trace_complete:true` on a real replay from that round).
- As of 2026-06-24, arena `crewrift:0.1.54` ⇒ commit `42fed21`. Current master
  hash-FAILS. Build:
  `nim c -d:release -d:useMalloc --opt:speed --out:/tmp/expand-42fed21 tools/expand_replay.nim`
  (after `nimby --global sync nimby.lock`; nim+nimby on PATH via `~/.local/bin`).
