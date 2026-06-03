# Crewrift Reporters

A **reporter** is a Coworld supporting runnable that turns one finished episode
into a human- and machine-readable report — narrative summaries, highlights,
statistics, event logs. Reporters are post-episode and on-demand: they are
**not** run by the episode runner. An episode produces its artifacts whether or
not any reporter ever runs against them.

See the upstream artifact docs in the `coworld` package:

- Role: `packages/coworld/src/coworld/docs/roles/REPORTER.md`
- Replay artifact: `packages/coworld/src/coworld/docs/artifacts/REPLAY.md`
- Output report: `packages/coworld/src/coworld/docs/artifacts/REPORT.md`

This directory holds Crewrift's own reporter(s). It mirrors the layout of
`players/`: each reporter lives in its own folder with an entry-point module and
a same-named submodule folder for its parts.

## scribe

`scribe` is the Crewrift reporter. It is being built bottom-up: first decode the
episode replay, then re-simulate that replay through the real Crewrift simulator
to recover a tick-aligned event timeline.

```
reporters/scribe/
  scribe.nim            local developer CLI: print one replay's timeline
  service.nim           persistent websocket reporter service
  Dockerfile            container image for the service entry point
  scribe/
    report.nim          core: replay -> decoded EpisodeReport (config + replay)
    driver.nim          replay re-simulation driver with hash validation
    identity.nim        stable player identity table
    events.nim          timeline event model
    probes.nim          sim-backed attribution probes
    detect.nim          per-tick event detection
    timeline.nim        extraction orchestration and text rendering
    event_log.nim       EpisodeTimeline -> event-log rows
    csv.nim             CSV escaping/rendering
    parquet.nim         fixed-schema Coworld event-log Parquet writer
    protocol.nim        websocket request/response JSON envelopes
    uri_io.nim          file:// and https:// replay reads
```

### What it does today

1. Reads a replay artifact from a `file://` or `https://` URI.
2. Decodes the replay bytes with Crewrift's own codec
   (`src/crewrift/replays.nim`) into a `ReplayData` — joins, leaves, inputs,
   and tick hashes — plus the `GameConfig` the episode ran with, recovered from
   the replay header.
3. Re-simulates the decoded replay tick by tick with `initSimServer` and
   `step`, validating every recorded `gameHash`.
4. Builds an in-memory `EpisodeTimeline` with stable player identities for game
   start, playing start, kills, task completions, meetings, votes, ejections,
   voting chat messages, stuck penalties, vents, game over, and replay leaves.
5. Exposes a persistent websocket service at `/report` that accepts
   `file://` or `https://` replay URIs and returns a binary Parquet event log
   using the Coworld event-log columns: `ts,player,key,value`. CSV remains
   available by requesting `format: "csv"`.

The replay format is **game-owned**, so the reporter reuses the game's codec
instead of reimplementing the byte layout. That is the only way to stay correct
as the format evolves; the codec validates the `CREWRIFT` magic and format
version on the way in.

The event extractor also reuses game logic rather than duplicating rules. Most
events are state diffs between the pre-step and post-step sim snapshots; body
report attribution probes a cloned post-step `SimServer` with the exported
`tryReport` proc and the real pre-step body limit. Kill attribution uses the
simulator's persisted reward-account kill counters and appended body order.

### Service interface

`service.nim` is a long-running service rather than the short-lived report
writer process described by the current upstream reporter role docs. It listens
on port `8080` by default and accepts websocket upgrades on `/report`.

Request text frame:

```json
{
  "type": "report.generate",
  "request_id": "req-1",
  "replay_uri": "file:///path/to/replay.bitreplay",
  "format": "parquet"
}
```

If `format` is omitted, the service defaults to `parquet`. Explicit `format`
values are `parquet` and `csv`.

Response frames:

1. Text `report.accepted`.
2. Text `report.parquet` metadata with
   `content_type: "application/vnd.apache.parquet"`,
   `filename: "events.parquet"`,
   `schema: "coworld.event_log.parquet.v1"`, and
   `columns: ["ts", "player", "key", "value"]`.
3. Binary Parquet payload.
4. Text `report.done`.

For `format: "csv"`, step 2 uses `report.csv` metadata,
`content_type: "text/csv"`, `filename: "events.csv"`, and
`schema: "coworld.event_log.csv.v1"`, followed by a binary CSV payload.

Errors are sent as text `report.error` messages with `request_id`, `code`, and
`message`.

The service allows two concurrent report jobs by default. Additional requests
receive a `busy` error. Configure with `--max-concurrency`, or
`SCRIBE_MAX_CONCURRENCY`.

### What is deliberately not built yet

- **Canonical Coworld report zip output.** This service returns raw event-log
  payloads over websocket, not a `manifest.json` report zip.
- **Multi-game segmentation** beyond stop-after-first and any narrative/stats
  layered on top of the timeline.

## Build and run

Dependencies are resolved exactly like the game and the bots — install Nim and
sync the lock file with [nimby](https://github.com/treeform/nimby), then build
from the repo root:

```sh
nimby use 2.2.10
nimby sync -g nimby.lock

nim c reporters/scribe/scribe.nim
reporters/scribe/scribe.out path/to/replay.bitreplay
```

Build and run the persistent service with:

```sh
nim c reporters/scribe/service.nim
reporters/scribe/service --port:8080 --max-concurrency:2
```

Build the service image with:

```sh
docker build -f reporters/scribe/Dockerfile -t crewrift-scribe .
docker run --rm -p 8080:8080 crewrift-scribe
```

Run the focused reporter tests with:

```sh
nim c -r reporters/scribe/test_timeline.nim
nim c -r reporters/scribe/test_service.nim
```
