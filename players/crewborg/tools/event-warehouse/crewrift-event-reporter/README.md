# Crewrift Event Reporter

Python WebSocket reporter that turns one Crewrift episode bundle into a zip
containing exactly one file: `events.parquet`. It consumes a single episode's
artifacts (`results.json` + the zlib replay), re-steps the replay through the
CrewRift `expand_replay` helper, and emits a flat event stream — direct replay
events plus derived spatial/social events — as a Parquet table. It is
optimizer-facing data, not a human report: it does **not** render HTML and does
**not** call an LLM. It **does** require the game-owned `expand_replay` helper
binary.

The reporter uses the same `/reporter` lifecycle as the other reporter
prototypes, but it is a separate data-producing service.

## Quick reference

| | |
| --- | --- |
| **Input** | one episode's presigned artifact refs (`results`, `replay`) via a `report_request` over `/reporter` |
| **Output** | a zip containing exactly `events.parquet` (columns `ts, player, key, value`) |
| **LLM** | none |
| **Replay helper** | CrewRift `expand_replay` binary, required. Must match the arena sim version — see [version coupling](#expand_replay-version-coupling) |
| **Entry points** | WS service `WS /reporter` + `GET /healthz`; ASGI app `crewrift_event_reporter.app:app`. No console script — this is a service, not a CLI |
| **Run locally** | `CREWRIFT_EXPAND_REPLAY=/tmp/crewrift-expand-replay uv run uvicorn crewrift_event_reporter.app:app --host 127.0.0.1 --port 8080` |

## How to call it

There is no CLI. The reporter is driven over WebSocket by the metta
reporter-runner backend (or a local client that speaks the same shape).

### WebSocket service

Lifecycle on `WS /reporter`:

1. Reporter sends `{"type":"reporter_ready","protocol_version":"crewrift-event-reporter/v2"}`.
2. Client sends one `report_request` (shape below). It **must** contain exactly
   one episode — the request is rejected otherwise.
3. Reporter sends `{"type":"report_started","request_id":...,"episode_count":1}`.
4. Reporter extracts events, writes the zip to `report_uri`, and sends
   `report_finished`.
5. Reporter closes the connection.

`GET /healthz` returns `{"status":"ok"}`.

A minimal `report_request`:

```json
{
  "type": "report_request",
  "request_id": "ereq-123",
  "report_uri": "https://example.invalid/output/events.zip",
  "episodes": [
    {
      "episode_request_id": "ereq-123",
      "status": "success",
      "manifest": {
        "ereq_id": "ereq-123",
        "status": "success",
        "include": ["results", "replay"],
        "files": {"results": "results.json", "replay": "replay"}
      },
      "artifacts": {
        "results": {"uri": "https://.../results.json", "media_type": "application/json"},
        "replay": {"uri": "https://.../replay.z", "media_type": "application/octet-stream", "encoding": "zlib"}
      },
      "players": [
        {"slot": 0, "player_id": "policy-alpha", "display_name": "Alpha"}
      ]
    }
  ]
}
```

The caller must supply: the `report_uri` to write to, and a single episode with
presigned `results` and `replay` refs. The reporter process must have
`CREWRIFT_EXPAND_REPLAY` pointing at a compatible helper binary (see
[version coupling](#expand_replay-version-coupling)).

## Input contract

This image implements the metta hosted reporter-runner contract (PR #15877): the
backend sends presigned per-artifact GET URLs per episode instead of a relayed
bundle zip. There is no backwards-compat path — deploy it in lockstep with a
backend that speaks this shape. See the metta `REPORTER.md` role doc for the
full `report_request` shape.

The request's `episodes` list must contain exactly one episode. Each episode
carries:

- `episode_request_id` — seeds the `episode_id` stamped on every output row.
- `status` — must be `"success"`; any other status fails the report.
- `manifest` — bundle-style token → file map (`ereq_id`, `status`, `include`,
  `files`).
- `artifacts` — presigned per-token refs. The reporter reads **`results`**
  (for the player count) and **`replay`**. The `replay` ref is
  `encoding:"zlib"` and is inflated on read. Tokens `error_info`, `game_logs`,
  `player_logs`, and `player_artifact` are accepted by the schema but **not
  read** by this reporter.
- `players` — `[{slot, player_id, display_name}]`, emitted verbatim as
  `player_manifest` rows so downstream consumers can map slots to stable policy
  identity.

## Output contract

The output URI receives a deterministic zip containing exactly one entry:

```text
events.parquet
```

Parquet schema (fixed top-level shape; `value` is a per-event JSON object):

| column | type | meaning |
| --- | --- | --- |
| `ts` | `int64` | replay tick |
| `player` | `int32` | actor slot, or `-1` for global events |
| `key` | `string` | event key |
| `value` | `string` | compact JSON object (sorted keys) |

Every `value` carries `schema_version` (`crewrift-events/v1`), `source`,
`confidence`, and `episode_id`. Rows are sorted by `(ts, player, key)`.

A few notable keys:

- `episode_metadata` (player `-1`, ts `0`) — `request_id`, `report_uri`,
  `generated_at`.
- `player_manifest` (ts `0`) — one row per request player with `player_id` /
  `display_name`.
- `trace_warning` (player `-1`) — emitted when the replay expansion did not
  complete cleanly (see [version coupling](#expand_replay-version-coupling) and
  [troubleshooting](#troubleshooting--common-failures)).
- Direct replay events (e.g. `entered_room`, `player_state`, kills, votes,
  bodies) and derived spatial/social interval events.

Load it with three lines:

```python
import zipfile, io, pyarrow.parquet as pq
with zipfile.ZipFile("events.zip") as zf:
    table = pq.read_table(io.BytesIO(zf.read("events.parquet")))
```

Interval events use `tick_start` as the first tick in the interval and
`tick_end` as the measured boundary tick. When `boundary_precision` is `exact`,
`tick_end` is the first observed tick after the interval ended, except for
intervals that run to trace end. When precision is `sampled`, the boundary is
only as exact as the configured snapshot cadence. `last_observed_tick` records
the last tick where the interval condition was seen as true.

## Configuration / environment variables

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `CREWRIFT_EXPAND_REPLAY` | Effectively yes | `crewrift-expand-replay` (on `PATH`) | Path to the compiled CrewRift `expand_replay` helper. Must match the arena sim version (see below). |
| `CREWRIFT_EVENT_SNAPSHOT_EVERY` | No | `1` | Sampled trace cadence in ticks. The helper invokes `--snapshot-every N`. `1` gives exact spatial interval boundaries; larger values shrink output but make boundaries sample-bounded. |
| `CREWRIFT_EVENT_NEAR_DISTANCE` | No | `32` | Proximity threshold in map pixels. |
| `CREWRIFT_EVENT_BODY_DISTANCE` | No | `36` | Body proximity threshold in map pixels. |
| `CREWRIFT_EVENT_GROUP_DISTANCE` | No | `44` | Third-player proximity threshold for isolation/group context. |
| `CREWRIFT_EVENT_MIN_INTERVAL_TICKS` | No | `24` | Minimum duration for interval events. |

The reporter invokes the helper as `--format jsonl --snapshot-every N`. The
helper's standalone default is no sampled snapshots; this reporter defaults to
`N=1` because proximity, following, and near-body intervals need dense state
rows.

## Local development

Compile the current CrewRift helper:

```sh
cd /Users/jamesboggs/coding/coworlds/coworld-crewrift
nim c -d:release --out:/tmp/crewrift-expand-replay tools/expand_replay.nim
```

> Local `master` drifts ahead of the deployed arena sim and will hash-FAIL on
> real replays. To analyze recorded replays, build the helper from the exact
> arena commit — see [version coupling](#expand_replay-version-coupling).

Run tests:

```sh
cd /Users/jamesboggs/coding/role_repos/reporter_lab/crewrift-event-reporter
uv run pytest
```

Run locally:

```sh
CREWRIFT_EXPAND_REPLAY=/tmp/crewrift-expand-replay \
uv run uvicorn crewrift_event_reporter.app:app --host 127.0.0.1 --port 8080
```

Build the Docker image with a named CrewRift build context:

```sh
docker build \
  --platform linux/amd64 \
  --build-context crewrift=/Users/jamesboggs/coding/coworlds/coworld-crewrift \
  -t crewrift-event-reporter:local .
```

The image builds `tools/expand_replay.nim` from that CrewRift context and
includes the same tree at `/workspace/crewrift`, which the helper expects when
reading replay resources.

## Troubleshooting / common failures

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| **Thin / sparse output** — only early `player_state` / `entered_room`, no kills, bodies, votes, or following intervals | Replay hash failure / sim version skew — the #1 silent failure. A `trace_warning` row (`message:"hash failed"`, `fail_tick:N`, `trace_complete:false`) is present. | Rebuild `CREWRIFT_EXPAND_REPLAY` from the arena's exact commit. See [version coupling](#expand_replay-version-coupling). **Check for a `trace_warning` row first.** |
| `report_failed` with `stage:"report"` | All failures use this single stage. The `error` string names the exception (e.g. `RuntimeError: episode ... status='failed'`, `KeyError` for a missing token, `CalledProcessError` if `expand_replay` exits nonzero **with no output at all**). | Read the `error` text; cross-check episode `status`, that `replay`/`results` refs exist, and that the helper binary path is valid and executable. |
| `report_failed` validation error about episode count | The request did not contain exactly one episode. | This reporter is single-episode; batch many episodes with the warehouse. |
| Empty / missing player slots | `results.json` had no `scores`; player count falls back to distinct positive slots seen in the event rows. | Confirm the `results` artifact is the real Crewrift results.json. |

### expand_replay version coupling

The CrewRift `expand_replay` helper RE-STEPS the sim and hash-checks every tick.
On a sim-version mismatch it ABORTS mid-replay, emitting a `trace_warning`
(`message:"hash failed"`, `fail_tick:N`) plus `trace_complete:false`, yielding
only PARTIAL events (early `player_state` / `entered_room`; no kills, bodies,
votes, or following). There is NO flag to disable the check (only `--format`,
`--snapshot-every`). This reporter KEEPS the partial rows and adds its own
`trace_warning` row — so a version-skew run looks like sparse output, not an
error.

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
