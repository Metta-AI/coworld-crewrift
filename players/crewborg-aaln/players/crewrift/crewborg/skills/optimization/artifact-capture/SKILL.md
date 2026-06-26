---
name: artifact-capture
description: "Use to save, download, and read crewborg player artifacts (per-episode trace.db + summary.json) for the optimizer loop. Trigger on 'save player artifacts', 'get crewborg's logging', 'where is the trace.db', 'download artifacts', or 'read the episode artifact'."
---

# Player Artifact Capture

## Why artifacts are the unlock

"As soon as artifacts was unlocked, I was off to the races." The artifact is the
**single most useful** data source in the loop. Key properties:

- **All logging lives in the artifact, not stderr logs.** Hosted logs get reset
  and are capped; artifacts persist and are downloadable. Default trace/metric
  logging targets the artifact, not the stream.
- **Pre-structured + indexed.** The artifact ships a `trace.db` whose tables are
  already structured and indexed, so when a 100-episode eval comes in you just
  **join the tables — no reformatting**, the structure is known ahead of time.
- **Big.** A run is on the order of MBs (player location, what it saw, every
  domain event). Joining many gives a huge dataset.
- **Self-describing.** Every artifact carries a `README.md` guide to what the
  data means, including a summary of all events.

Code: `artifact.py` (`SqliteEpisodeRecorder`, `upload_episode_artifact`).

**Announce at start:** "Capturing crewborg artifacts: I'll confirm the upload
channel (or use the always-on stderr summary), then download + open the indexed
`trace.db` for the loop."

## The artifact contents

A single `.zip` per slot: `trace.db` + `summary.json` + `README.md`.

`trace.db` has three indexed tables (full DDL in `artifact.py` `_SCHEMA` and the
embedded README):

| Table | One row per | Key columns |
|---|---|---|
| `traces` | trace event | `tick`, `event` (e.g. `domain.vote_cast`), `data` (JSON) |
| `metrics` | metric sample | `name`, `kind` (counter/histogram/gauge), `value`, `tags` |
| `positions` | **tick (24 Hz)** | `tick`, **`server_tick`** (replay join key), `self_x/y`, `room_id`, `mode`, `intent_kind`, `held_mask`, `phase`, `visible` (JSON LoS players) |

`positions.server_tick` comes from the game's invisible `tick <N>` sprite marker
and is **the join key to the replay timeline** (see `replay-reconstruction`).
Domain events are `domain.*`; framework events are unprefixed.

`summary.json` holds row counts, dropped counts, `first/last_tick`,
`event_counts`, and a non-secret `episode` block (slot, role, color, outcome when
resolved). The same JSON is **always echoed to stderr** as a greppable block, so
the artifact's value survives even with no binary upload channel.

## Saving / capturing artifacts

Two delivery paths, both best-effort and never fatal (an episode never fails for
a missing artifact):

1. **Binary upload (forward path).** If `COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` is
   set (presigned `https://` PUT, or `file://` on local runs), the bridge uploads
   the `.zip` at episode end. ≤ 200 MB, one object per slot.
2. **Captured-log metadata (works today).** When no upload URL exists, the
   `summary.json` block is emitted to the slot's stderr policy-log
   (`policy_agent_{slot}.log`), which the hosted runner captures.

To enrich what lands in the artifact, raise the trace level *before* the episode
(more rows = more to mine later, bounded by `MAX_ROWS_PER_TABLE`):

- `CREWBORG_METRICS=1` — counters/gauges without full debug.
- `CREWBORG_TRACE=debug` — full framework stream + metrics + `decision_snapshot`
  + per-tick `suspicion_tick`/`kill_state`/`occupancy_snapshot`.
- `CREWBORG_TRACE=viewer` — adds `viewer_*` frames for the replay viewer.
- Targeted: `CREWBORG_TRACE_GROUPS=voting,occupancy`,
  `CREWBORG_TRACE_INCLUDE=meeting_*,vote_cast`.

`data-collection-design` decides *which* of these to turn on for a given
hypothesis (more is not free — capped logs, bigger zips).

## Downloading hosted artifacts

For episodes crewborg played in the league:

```sh
players/crewrift/crewborg/scripts/fetch_episodes.sh -n 100   # or fetch_episodes.py
```

This writes `episode_data/<timestamp>_<id8>/` per episode with `episode.json`,
`episode_request.json` (roster: slot → policy/version + scores), `replay.json`
(the `.bitreplay`), and `logs/crewborg_slot{N}_v{V}.log` (the stderr trace, which
contains the `summary.json` block). The binary `trace.db` is uploaded only when an
upload URL was present; otherwise reconstruct row counts/events from the captured
`summary.json` block in the log.

## Reading an artifact (the join-the-tables pattern)

Open `trace.db` with stdlib `sqlite3`. The canonical reader extracts events by
name and the position track (mirror `eval_2026-06-11_v3_vs_v8/analyze.py`):

```python
import json, sqlite3, zipfile, tempfile
from pathlib import Path

def read_artifact(zip_path):
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extract("trace.db", td)
            summary = json.loads(zf.read("summary.json"))
        db = sqlite3.connect(str(Path(td) / "trace.db"))
        evs = lambda name: [
            (t, json.loads(d))
            for t, d in db.execute(
                "select tick, data from traces where event=? order by tick", (name,)
            )
        ]
        out = {
            "summary": summary,
            "game_over": evs("domain.game_over"),
            "kills": evs("domain.kill_landed"),
            "votes": evs("domain.meeting_vote_selected"),
            "tasks": len(evs("domain.task_completed")),
            "positions": db.execute(
                "select server_tick, self_x, self_y, mode, intent_kind, phase, visible "
                "from positions where server_tick is not null order by server_tick"
            ).fetchall(),
        }
        db.close()
        return out
```

For a whole eval set, run this over every per-slot zip and hand the records to
`eval-aggregation`.

## Integration

- **Feeds:** `replay-reconstruction` (the `server_tick` join), `eval-aggregation`
  (per-episode records), `pattern-toolkit` (positions/events to shape).
- **Configured by:** `data-collection-design` (which trace level to enable).
- **Grounded in:** `artifact.py`, `scripts/fetch_episodes.py`,
  `episode_data/eval_2026-06-11_v3_vs_v8/analyze.py`, `design.md` §11.
