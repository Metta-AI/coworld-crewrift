# Crewrift Event-Log Reporter

This reporter is a minimal Coworld reporter service for completed Crewrift
episodes. It accepts a platform WebSocket `report_request`, fetches the episode
replay from `context.replay_uri` or `REPORTER_REPLAY_URI`, expands the replay
through `tools/expand_replay.nim`, and returns structured categorical events as
JSON.

The reporter does not reimplement game rules. Replay interpretation stays in
`tools/expand_replay.nim`, which re-runs the real simulator.

## Runtime

- `GET /healthz` returns `200 healthy`.
- `WEBSOCKET /report` receives platform messages and sends reporter messages.
- The service listens on `REPORTER_HOST` and `REPORTER_PORT`, defaulting to
  `0.0.0.0:8080`.

## Protocol

Inbound messages:

```json
{
  "type": "report_request",
  "request_id": "req_123",
  "target": {"kind": "episode", "id": "episode_123"},
  "reason": "episode_completed",
  "context": {
    "replay_uri": "file:///workspace/crewrift/tests/replays/notsus.bitreplay"
  }
}
```

```json
{"type": "drain", "reason": "shutdown"}
```

Outbound success:

```json
{
  "type": "report_output",
  "request_id": "req_123",
  "target": {"kind": "episode", "id": "episode_123"},
  "mime": "application/json",
  "encoding": "json",
  "payload": [
    {
      "ts": 1,
      "player": 0,
      "key": "player_joined",
      "value": {"label": "red(notsus1)"}
    }
  ]
}
```

The reporter may send `report_accepted` before processing. Any fetch, decode,
hash-validation, or unsupported-target failure returns:

```json
{
  "type": "report_failed",
  "request_id": "req_123",
  "target": {"kind": "episode", "id": "episode_123"},
  "error": "clear error text"
}
```

Only `target.kind == "episode"` is supported.

## Replay URI Resolution

The reporter reads replay bytes from:

1. `report_request.context.replay_uri`
2. `REPORTER_REPLAY_URI`

The URI must be readable by `bitworld/runtime.readCogameUri`, currently
`file://`, `http://`, or `https://`. Hosted zlib-compressed replay payloads are
decompressed before decoding. A valid replay with no events returns an empty
JSON array.

## Event Rows

The payload is a JSON array of rows:

```json
{
  "ts": 123,
  "player": 4,
  "key": "completed_task",
  "value": {"task": 6, "while_dead": false}
}
```

Schema:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "array",
  "items": {
    "type": "object",
    "additionalProperties": false,
    "required": ["ts", "player", "key", "value"],
    "properties": {
      "ts": {"type": "integer"},
      "player": {"type": "integer"},
      "key": {
        "type": "string",
        "enum": [
          "player_joined",
          "entered_room",
          "left_room",
          "phase",
          "vote_called_body",
          "vote_called_button",
          "kill",
          "body",
          "died",
          "revived",
          "started_task",
          "completed_task",
          "vote_cast",
          "chat",
          "score"
        ]
      },
      "value": {"type": "object"}
    }
  }
}
```

## Local Run

From the repo root:

```sh
nim c --out:/tmp/crewrift-eventlog-reporter reporters/eventlog/service.nim
REPORTER_HOST=127.0.0.1 \
REPORTER_PORT=18080 \
REPORTER_REPLAY_URI=file://$PWD/tests/replays/notsus.bitreplay \
  /tmp/crewrift-eventlog-reporter
```

Run the tests:

```sh
nim r reporters/eventlog/test_eventlog.nim
```

Build the container:

```sh
docker build -f reporters/eventlog/Dockerfile -t crewrift-eventlog-reporter .
```

## Manifest Entry

This reporter is wired into `coworld_manifest.json` under `reporter[]`. The
committed entry uses only the fields the manifest's declared `$schema` (metta
`main`) currently accepts:

```json
{
  "id": "crewrift-eventlog-reporter",
  "name": "Crewrift Event-Log Reporter",
  "type": "reporter",
  "description": "Expands a completed Crewrift episode replay into a structured categorical event log: {ts, player, key, value} JSON rows served over the reporter WebSocket contract.",
  "source_url": "https://github.com/Metta-AI/coworld-crewrift/tree/master/reporters/eventlog",
  "image": "ghcr.io/metta-ai/reporters-crewrift-eventlog:latest"
}
```

The image is published at `ghcr.io/metta-ai/reporters-crewrift-eventlog:latest`
(`linux/amd64`).

### Future upgrade: typed reporter fields

The reporter role contract adds two reporter-specific fields — `purpose` and
`output_format`. That typed manifest spec was reverted on metta `main`
(`#14986` → `#15013`), so those fields are not yet valid against the declared
schema and are intentionally omitted from the committed entry. Once the typed
spec re-lands on `main`, upgrade the entry to declare them:

```json
{
  "purpose": "categorical_events",
  "output_format": {
    "mime": "application/json",
    "schema": "<use the EventLogSchema from reporters/eventlog/report.nim>"
  }
}
```
