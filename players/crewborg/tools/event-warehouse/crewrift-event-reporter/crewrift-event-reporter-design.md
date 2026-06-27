# Crewrift Event Reporter Design

Status: prototype implemented; aligned with merged CrewRift `expand_replay` PR #57
Workspace: `/Users/jamesboggs/coding/role_repos/reporter_lab/crewrift-event-reporter`
Research date: 2026-06-12

## Goal

Build a separate episode-level reporter that extracts a high-value event stream
from one Crewrift episode bundle. This is not part of the sportscaster service
and does not render HTML. It is a data producer.

The reporter should:

- use the same WebSocket lifecycle and message contract as the existing
  reporter shape
- accept exactly one episode bundle per request
- expand the replay with the current CrewRift code
- enrich the raw replay facts with spatial and temporal derived events
- write a zip containing exactly one file, `events.parquet`
- send the normal report-finished message over `/reporter`

## Non-Goals

- No round aggregation.
- No leaderboard.
- No prose narrative.
- No LLM.
- No static HTML/CSS/JS.
- No Python replay parser.
- No separate JSON sidecars in the output zip.

## Placement

This should be its own project under the reporter lab:

```text
reporter_lab/
  crewrift-sportscaster/
  crewrift-event-reporter/
```

The two reporters can share design patterns, but should not be one service. If
shared code becomes worthwhile, extract it later into a small local package
instead of importing from the sportscaster app directly.

## WebSocket Contract

> **Migration note (PR #15877):** the request JSON below reflects the original v1
> design. As shipped, the reporter implements the metta reporter-runner contract:
> `report_request` carries a non-empty `episodes` list (this reporter requires
> exactly one) where each episode has `episode_request_id`, `status`, a
> bundle-style `manifest`, and presigned per-token `artifacts` refs
> (`{uri, media_type, encoding}`; replay refs are `encoding:"zlib"`). There is no
> `episode_bundle_uris`. See the README and metta `REPORTER.md` for the current
> shape. `protocol_version` is now `crewrift-event-reporter/v2`.

Use the same `/reporter` lifecycle:

1. Process starts, initializes, and waits on `/reporter`.
2. Runner connects.
3. Reporter sends:

```json
{"type":"reporter_ready","protocol_version":"crewrift-event-reporter/v1"}
```

4. Runner sends the existing `report_request` shape with one episode bundle:

```json
{
  "type": "report_request",
  "request_id": "ereq_123",
  "episode_bundle_uris": ["file:///shared/episode.zip"],
  "report_uri": "file:///shared/events.zip",
  "round": {
    "league": "Crew Rift Daily League",
    "division": "Competition",
    "round_id": "round_123"
  }
}
```

The reporter should also accept the existing `episodes` form when it contains
exactly one episode:

```json
{
  "type": "report_request",
  "request_id": "ereq_123",
  "episodes": [
    {
      "episode_id": "ereq_123",
      "bundle_uri": "file:///shared/episode.zip",
      "players": [
        {"slot": 0, "player_id": "policy-version-id", "display_name": "crewbot:v12"}
      ]
    }
  ],
  "report_uri": "file:///shared/events.zip"
}
```

If the request contains zero or more than one episode bundle, return
`report_failed` with a clear error. Keeping the message contract stable matters
more than adding a new `episode_report_request` type.

Progress and completion messages stay compatible:

```json
{"type":"report_started","request_id":"ereq_123","episode_count":1}
{"type":"report_finished","request_id":"ereq_123","report_uri":"file:///shared/events.zip","episode_count":1,"players":8}
```

## Output Contract

The output URI receives a zip with exactly one file:

```text
events.parquet
```

There is no manifest. The output zip itself is the reporter artifact; the
Parquet schema is the contract.

## Parquet Schema

The Parquet file has the standard event schema:

| column | type | meaning |
| --- | --- | --- |
| `ts` | `int64` | replay tick |
| `player` | `int32` | actor slot, or `-1` for global events |
| `key` | `string` | stable event key |
| `value` | `string` | compact JSON object encoded as UTF-8 |

`value` is intentionally a JSON object string, not a Parquet struct. Event
payloads are heterogeneous, and keeping the top-level schema fixed lets every
consumer read the file with the same four columns.

Every `value` object should include these common fields where possible:

```json
{
  "schema_version": "crewrift-events/v1",
  "source": "replay|derived|reporter",
  "confidence": 1.0,
  "episode_id": "ereq_123",
  "phase": "Playing"
}
```

Direct replay facts should use `confidence: 1.0`. Derived spatial/temporal
events should include a confidence score and machine-readable evidence.

## Event Key Strategy

Keep current `expand_replay` event keys stable:

- `player_joined`
- `entered_room`
- `left_room`
- `phase`
- `vote_called_body`
- `vote_called_button`
- `kill`
- `body`
- `died`
- `revived`
- `started_task`
- `completed_task`
- `vote_cast`
- `chat`
- `score`

Add global metadata/state keys:

- `episode_metadata`
- `map_geometry`
- `player_manifest`
- `trace_warning`
- `trace_complete`

Add sampled state keys:

- `player_state`
- `body_state`
- `room_occupancy`
- `player_visible_interval`
- `body_visible_interval`

Add derived interaction keys:

- `proximity_interval`
- `isolation_interval`
- `group_interval`
- `following_interval`
- `chase_interval`
- `last_seen_near`

Add derived destination/objective keys:

- `headed_to`
- `arrived_at`
- `route_abandoned`
- `task_attempt`
- `task_abandoned`
- `near_body_interval`
- `passed_body`
- `near_button`
- `near_vent`

Add meeting/vote context keys:

- `meeting_context`
- `vote_summary`
- `chat_sequence`

The MVP does not need all derived keys implemented immediately. The important
part is that the event stream format can hold both direct and inferred facts
without another output contract change.

## Current `expand_replay` Contract

CrewRift now exports authoritative replay state snapshots and metadata while it
steps the sim. These rows are part of the existing `--format jsonl` output; there
is no separate reporter-specific output mode.

Reporter CLI:

```sh
tools/expand_replay \
  --format jsonl \
  --snapshot-every 1 \
  replay.bitreplay
```

The helper's standalone `--snapshot-every` default is `0`, so plain JSONL stays
small and direct-event oriented. The reporter opts into sampled state with
`CREWRIFT_EVENT_SNAPSHOT_EVERY`, defaulting to `1` for exact interval
boundaries.

The helper output uses the standard row shape:

```json
{"ts":2400,"player":3,"key":"player_state","value":{...}}
```

### Helper Rows Used By The Reporter

#### `episode_metadata`

One global row at `ts=0`, `player=-1`.

Current value fields include:

- `schema_version`
- `source`
- `coworld_version`, if available
- `config`: speed, max ticks, kill range, report range, vent range, task timing,
  vote timing, imposter count, task count
- `hash_checking`: enabled/disabled

#### `map_geometry`

One global row at `ts=0`, `player=-1`.

Current value fields include:

- map name, width, height
- rooms: name, x, y, w, h
- tasks: id, name, resource name, x, y, w, h, nearest room
- vents: id, resource name, x, y, w, h, group, group index
- emergency button rect
- home/spawn point

Walkability/pathfinding can wait. Geometry is enough for the first useful
spatial report.

#### `player_manifest`

One row per player after joins are known.

Current value fields include:

- slot
- label/address
- color
- role
- home position
- assigned task ids

Roles are acceptable because this is a post-episode reporter.

#### `player_state`

Sampled every `snapshot_every_ticks`, plus forced at direct event ticks.

Current value fields include:

- x, y
- velocity or previous-position delta
- room, plus `inside_room: true|false`
- role
- alive
- connected
- active task
- task progress
- assigned tasks
- kill cooldown
- vent cooldown
- button calls used
- reward

This is the main addition that unlocks proximity, following, heading, and
arrival analysis.

#### `body_state`

Emitted when a body appears and on sampled ticks while unreported.

Current value fields include:

- victim slot/color
- x, y
- room
- age ticks, if known
- reported flag, if known

#### Visibility Intervals

`expand_replay` emits player-centric visibility intervals using CrewRift's
canonical rendered-view visibility checks. These rows describe what a living
player could objectively see, not whether the policy internally noticed it.

Keys:

- `player_visible_interval`
- `body_visible_interval`

Current value fields include:

- `observer_slot`, `observer_label`, `observer_role`
- `target_kind`, `target_id`, `target_slot`, `target_label`
- `target_role` for visible players
- `tick_start`, `tick_end`, `last_observed_tick`, `duration_ticks`
- `visibility_basis`: currently `rendered_view`
- `boundary_precision`, `ended_by`
- last observed `x`, `y`, `room`

#### Enriched Direct Events

For existing direct event rows, add positions and room where cheap:

- actor x/y/room
- target x/y/room for kill/vote/body events when known
- task x/y/room for task events
- body x/y/room for body/report events

This removes a lot of fragile Python lookups.

### What Not To Put In `expand_replay`

Keep high-level inference out of Nim for now:

- following
- chasing
- route intent
- task abandonment
- suspiciousness scores
- interestingness ranking

The helper should export authoritative state. The Python reporter should compute
derived event semantics from that state.

## Reporter Pipeline

```text
episode bundle zip
  -> read manifest/results/replay
  -> call CrewRift expand_replay --format jsonl
  -> parse standard event rows
  -> normalize/enrich with episode/player metadata from request and bundle
  -> compute derived spatial and temporal events
  -> write all direct + derived rows to events.parquet
  -> zip events.parquet
  -> PUT/write report_uri
  -> send report_finished
```

The reporter should use Python, FastAPI, WebSockets, Pydantic, and PyArrow.
There is no browser/runtime UI dependency.

## Derived Event Analysis

### Proximity

From `player_state` rows, compute pairwise intervals:

- near: distance below near threshold for at least N ticks
- kill-range: distance below `kill_range`
- isolated pair: two players near each other with no third player nearby
- group: three or more players close for a sustained interval
- last seen near: pair proximity shortly before a death/body event

Emit `proximity_interval`, `isolation_interval`, `group_interval`, and
`last_seen_near`.

Suggested value fields:

- `player_a`, `player_b`
- `tick_start`, `tick_end`, `last_observed_tick`, `duration_ticks`
- `boundary_precision`: `exact` for per-tick measured boundaries, `sampled`
  when the configured snapshot cadence prevents exact boundaries
- `min_distance`, `median_distance`, `max_distance`
- `rooms`
- `ended_by`: kill/report/meeting/separation/game_over
- `evidence`

### Following And Chasing

Following is trajectory correlation, not just proximity.

Emit `following_interval` when:

- A stays near B for long enough
- A's movement aligns with B's recent displacement
- A traverses B's recent room/path sequence with a positive lag
- both are not merely idle in the same room

Emit `chase_interval` as a stronger subtype when distance generally decreases
and both players are moving.

Suggested value fields:

- follower/chaser slot
- target slot
- lag ticks
- alignment score
- distance trend
- rooms crossed
- confidence
- evidence

### Destination And Arrival

Candidate targets:

- assigned tasks
- active task
- rooms
- bodies
- emergency button
- vents

Emit:

- `headed_to` when distance to target decreases consistently
- `arrived_at` when player enters target radius or target room
- `route_abandoned` when the player reverses, switches target, dies, or phase
  changes before arriving

Suggested value fields:

- target kind/id/name
- start/end distance
- heading confidence
- arrival radius
- interruption reason

### Body And Kill Context

For each body/kill:

- who was nearby when it happened
- who passed near the body before report
- who first entered the body room after death
- report delay
- whether the killer stayed, left, returned, vented, or joined a group

Emit `near_body_interval`, `passed_body`, and contextual updates in event
values. `near_body_interval` follows the same interval boundary convention as
proximity events: `tick_start` is the first observed near-body tick, `tick_end`
is the first measured non-near tick when available, and `last_observed_tick` is
the last tick where the player was still near the body.

### Meeting And Vote Context

For each meeting:

- trigger type
- caller
- related body/victim
- time since last kill/body
- vote summary
- chat sequence
- timeouts/skips

Emit `meeting_context`, `vote_summary`, and `chat_sequence`.

## Parquet Write Details

Use PyArrow with explicit schema:

```python
schema = pa.schema([
    ("ts", pa.int64()),
    ("player", pa.int32()),
    ("key", pa.string()),
    ("value", pa.string()),
])
```

Sort rows by `(ts, player, key)` with stable insertion order as the tiebreaker.
Use deterministic JSON encoding for `value`: compact separators, sorted keys
where practical, UTF-8, no NaN.

Zip writer requirements:

- file name inside zip: `events.parquet`
- no other entries
- deterministic timestamps if possible

## Failure Behavior

- Missing or unreadable bundle: `report_failed`.
- Missing `results.json`: `report_failed`.
- Missing replay: `report_failed`.
- Helper cannot start: `report_failed`.
- Helper hash-fails after partial rows: write partial `events.parquet` if rows
  are parseable, include `trace_warning` and `trace_complete=false`, then finish.
- Helper emits malformed rows: `report_failed`, unless a request option later
  allows best-effort mode.
- Derived detector cannot run because snapshots are absent: still emit direct
  events and a `trace_warning`.

## Data Quality Rules

- Direct replay facts use `source: "replay"` and `confidence: 1.0`.
- Request/bundle metadata uses `source: "reporter"` and `confidence: 1.0`.
- Derived events use `source: "derived"` and `confidence < 1.0` unless the
  inference is mechanically exact.
- False attribution is worse than missing attribution. If a direct event cannot
  identify actor/target confidently, use `player: -1` or explicit unknown fields
  rather than guessing.
- Never encode subjective labels like "suspicious" as direct facts. If useful,
  encode them as derived ranking fields with evidence and confidence.

## Docker Shape

Separate Docker image:

```text
crewrift-event-reporter/
  Dockerfile
  pyproject.toml
  uv.lock
  crewrift_event_reporter/
  tests/
```

Build stages:

1. Debian/Nim stage builds the current CrewRift helper from a named Docker build
   context.
2. Python runtime stage installs the reporter package and PyArrow.
3. Runtime copies the built helper and the same CrewRift tree into
   `/workspace/crewrift`, because the helper resolves replay resources relative
   to that path.
4. Runtime starts Uvicorn on `/reporter`.

The image should not include the sportscaster package.

## Validation Plan

### CrewRift Helper

- Compile `expand_replay` in release mode.
- Add tests for enriched `--format jsonl`.
- Assert standard schema rows only: `ts`, `player`, `key`, `value`.
- Assert metadata, map geometry, direct events, and sampled state exist.
- Compare direct kill/task event totals to `results.json` on fixture replays.

### Reporter

- Unit-test request validation: exactly one episode accepted.
- Unit-test JSONL parser and schema validation.
- Unit-test Parquet writer schema and zip containing only `events.parquet`.
- Unit-test derived proximity/following/destination detectors with synthetic
  traces.
- Smoke-test against one real recent episode bundle.

### Contract Checks

- WebSocket ready/start/finish/fail messages match the existing runner
  expectations.
- Output URI receives a zip.
- Zip contains exactly one Parquet file.
- Parquet loads with PyArrow and has exactly the four required columns.

## Settled Decisions And Open Questions

1. Direct events and snapshots now share one JSONL stream via `--format jsonl`.
2. The helper default is no sampled snapshots. This reporter defaults to every
   tick with `CREWRIFT_EVENT_SNAPSHOT_EVERY=1`; larger cadences trade precision
   for output size.
3. Should `value` be JSON string in Parquet, or should the broader Coworld event
   schema eventually standardize on a Parquet nested type? My recommendation is
   JSON string now because event payloads are heterogeneous.
4. Should direct `kill` rows be omitted when actor attribution is uncertain, or
   emitted as global rows with `killer_slot: null`?
5. Should derived event keys be emitted by the reporter only, or should some
   low-level derived keys like `room_occupancy` come directly from the helper?
6. How much map/pathfinding should be exported now? Geometry is enough for v1;
   walkability/path distance can be a v2 addition.
