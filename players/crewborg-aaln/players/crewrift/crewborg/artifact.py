"""Episode debug artifact: record traces/metrics into SQLite and surface it at episode end.

Instead of streaming every trace event to stderr, crewborg records the full
unfiltered event/metric stream into an in-memory SQLite database and, at episode
end, makes it retrievable two ways:

1. **Binary artifact (forward path).** If a per-slot upload URL is set
   (``COWORLD_PLAYER_ARTIFACT_UPLOAD_URL``), upload one ``.zip`` (``trace.db`` +
   ``summary.json``) — exactly one object per slot, max 200 MB, ``PUT`` with
   ``Content-Type: application/zip``, presigned so no auth header. Works locally
   (``file://`` via ``coworld run-episode``) and is forward-compatible if/when the
   platform injects a per-player upload URL.

2. **Captured-log metadata (works on TODAY's hosted platform).** The deployed
   coworld runner (verified against the installed coworld 0.1.20:
   ``runner/kubernetes_runner.py`` injects only ``COWORLD_PLAYER_WS_URL`` +
   ``COGAMES_ENGINE_WS_URL`` into the player pod, and there is no
   ``COWORLD_PLAYER_ARTIFACT_UPLOAD_URL`` anywhere in the package) provides **no
   per-player binary upload channel** — the only per-player output it collects is
   the container's captured ``stdout``/``stderr`` (uploaded as
   ``policy_agent_{slot}.log`` via the runner-side ``POLICY_LOG_URLS`` and embedded
   in ``DEBUG_URI``'s debug archive; both are runner env, not player env). So we
   ALWAYS emit the ``summary.json`` metadata as a clearly-marked block to stderr,
   guaranteeing the artifact's *value* (row counts, tick range, top events, zip
   size) lands in the captured policy-log even when no binary channel exists.

SQLite over parquet because it is stdlib (the crewborg image installs only the
base ``players`` deps — no ``pyarrow``/``requests``), serializes to bytes without
touching disk (:meth:`sqlite3.Connection.serialize`), and stays queryable with
any sqlite client. The upload uses :mod:`urllib` for the same no-new-deps reason.

A missing env var or failed upload is never fatal: the artifact is best-effort
debug data and a missing artifact never fails an otherwise successful episode.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from players.player_sdk.trace import MetricSample, TraceEvent

ARTIFACT_URL_ENV = "COWORLD_PLAYER_ARTIFACT_UPLOAD_URL"

# Env vars the Coworld runner injects into the player pod (verified against the
# installed coworld 0.1.20 ``runner/kubernetes_runner.py``: the player container
# gets ``COWORLD_PLAYER_WS_URL`` + ``COGAMES_ENGINE_WS_URL`` — both set to the same
# ``ws://<svc>:8080/player?slot=<N>&token=<T>`` — plus any policy-submission env).
# Both carry the per-slot WS URL; we read either to recover the slot. The token
# rides in the same query string and is a SECRET — it is parsed out and discarded,
# never stored or logged.
WS_URL_ENV_CANDIDATES: tuple[str, ...] = ("COWORLD_PLAYER_WS_URL", "COGAMES_ENGINE_WS_URL")

# Bumped when summary.json's shape changes so downstream readers can branch.
# v2: added the per-tick ``positions`` table + its row counts in the summary.
SCHEMA_VERSION = 2

# Ordered candidate env vars for a per-player artifact upload destination, most
# preferred first. ``COWORLD_PLAYER_ARTIFACT_UPLOAD_URL`` is the forward contract
# (PLAYER_ARTIFACT.md). The deployed runner injects none of these into the player
# pod today (verified coworld 0.1.20), so this normally resolves to None and the
# always-emitted stderr summary block (below) is the actual deliverable; the list
# stays extensible for if/when the platform exposes a real per-player upload URL.
# NOTE: deliberately excludes the runner-side DEBUG_URI / POLICY_LOG_URLS — those
# are the runner's own collection targets (a whole-episode debug archive and a
# per-slot *text* log), never injected into the player pod, and writing crewborg's
# zip there would clobber the runner's own uploads.
ARTIFACT_URL_ENV_CANDIDATES: tuple[str, ...] = (ARTIFACT_URL_ENV,)

MAX_ARTIFACT_BYTES = 200 * 1024 * 1024  # Platform rejects larger uploads.
UPLOAD_TIMEOUT_SECONDS = 60.0

# Bound in-memory growth on pathological runs (e.g. CREWBORG_TRACE=viewer for a
# long episode). Rows past the cap are counted as dropped in summary.json.
MAX_ROWS_PER_TABLE = 1_500_000

_SCHEMA = """
CREATE TABLE traces (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    wall_time REAL NOT NULL,
    tick INTEGER NOT NULL,
    event TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX traces_event ON traces(event);
CREATE INDEX traces_tick ON traces(tick);
CREATE TABLE metrics (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    wall_time REAL NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    tags TEXT NOT NULL
);
CREATE INDEX metrics_name ON metrics(name);
CREATE TABLE positions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tick INTEGER NOT NULL,
    server_tick INTEGER,
    self_x INTEGER,
    self_y INTEGER,
    room_id INTEGER,
    mode TEXT,
    intent_kind TEXT,
    held_mask INTEGER,
    phase TEXT,
    visible TEXT NOT NULL
);
CREATE INDEX positions_tick ON positions(tick);
"""


ARTIFACT_README = """\
# crewborg episode debug artifact

This `.zip` is **crewborg's per-episode debug capture** — the full, unfiltered
trace/metric stream from one Crewrift (Coworld) episode, recorded by crewborg's
`SqliteEpisodeRecorder`. On the hosted platform it is uploaded as the per-slot
Coworld player artifact when an upload URL is available; the same metadata
(`summary.json`) is *always* echoed to the player's captured stderr log
(`policy_agent_{slot}.log`) so the artifact's value survives even when no binary
upload channel exists. You are reading this because you unzipped it cold — here
is everything you need to make sense of it.

## Files

- `trace.db` — a SQLite database holding every trace event and metric sample.
- `summary.json` — episode/player metadata + row counts + per-event counts
  (see below). The same JSON is echoed to the captured stderr policy-log.
- `report.html` — a self-contained, player-specific visual summary of THIS game
  (this player's stats, a position heatmap, a mode/event timeline, the per-game
  event profile, suspicion, and meetings/votes). Built from `trace.db` +
  `summary.json` and opens in any browser with a double-click (no server, no
  build — Chart.js loads from a CDN).
- `README.md` — this file.

## `trace.db` schema

Three tables; `data`, `tags`, and `visible` are JSON text (parse with
`json_extract` / `json.loads`):

```sql
CREATE TABLE traces (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    wall_time REAL    NOT NULL,  -- unix epoch seconds when recorded
    tick      INTEGER NOT NULL,  -- game tick (24 ticks/sec)
    event     TEXT    NOT NULL,  -- event name, e.g. "domain.vote_cast"
    data      TEXT    NOT NULL   -- JSON object payload
);
CREATE TABLE metrics (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    wall_time REAL    NOT NULL,
    kind      TEXT    NOT NULL,  -- "counter" | "histogram" | "gauge"
    name      TEXT    NOT NULL,  -- e.g. "cyborg.step.latency_ms"
    value     REAL    NOT NULL,
    tags      TEXT    NOT NULL   -- JSON object
);
CREATE TABLE positions (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    tick        INTEGER NOT NULL,  -- crewborg's own tick (bridge frame count)
    server_tick INTEGER,           -- the server's tick from the "tick <N>" marker
                                   -- sprite: joins this trace to the .bitreplay
    self_x      INTEGER,           -- our world position (NULL during meetings,
    self_y      INTEGER,           --   when the camera is down)
    room_id     INTEGER,           -- baked-map room index containing self
    mode        TEXT,              -- active mode (normal / hunt / flee / ...)
    intent_kind TEXT,              -- the tick's symbolic intent
    held_mask   INTEGER,           -- the wire button bitmask sent
    phase       TEXT,              -- game phase (Playing / MeetingCall / Voting ...)
    visible     TEXT    NOT NULL   -- JSON [{"c": color, "x": x, "y": y}, ...]:
                                   --   players seen in LOS this tick
);
```

Indexes exist on `traces(event)`, `traces(tick)`, `metrics(name)`, and
`positions(tick)`.

Event names follow crewborg's conventions: framework events are unprefixed
(`perception`, `mode_entered`, `belief_updated`, ...) while game/domain events
are prefixed `domain.` (`domain.phase_change`, `domain.role_resolved`,
`domain.vote_cast`, `domain.kill_landed`, `domain.meeting_called`,
`domain.game_over`, `domain.body_sighted`, `domain.task_completed`, ...).
Every `domain.*` payload also carries the spatial annotation `self_x` /
`self_y` / `room_id` (last known self fix at the time of the event).

`positions` is the full 24 Hz movement/visibility track (one row per tick,
~2 MB per 10k ticks): use it for heatmaps, encounter analysis, and joining
crewborg's view against the server replay via `server_tick`.

## How to query it

Open with any SQLite client — `sqlite3 trace.db`, Python's stdlib
`import sqlite3`, or a GUI like DB Browser for SQLite. Examples:

```sh
# Count events by name, most frequent first:
sqlite3 trace.db "SELECT event, COUNT(*) FROM traces GROUP BY event ORDER BY 2 DESC;"

# Pull every vote cast and its target (data is JSON):
sqlite3 trace.db "SELECT tick, json_extract(data,'$.target') AS target
                  FROM traces WHERE event='domain.vote_cast' ORDER BY tick;"

# A gauge over time:
sqlite3 trace.db "SELECT tick, value FROM metrics
                  WHERE name='cyborg.directive.age_ticks' ORDER BY seq;"

# The movement track with who was visible:
sqlite3 trace.db "SELECT tick, server_tick, self_x, self_y, room_id, mode, visible
                  FROM positions WHERE visible != '[]' ORDER BY tick LIMIT 20;"
```

## `summary.json`

Holds the episode/player metadata, row counts (`trace_rows`, `metric_rows`,
`position_rows`, dropped counts), the tick range (`first_tick`/`last_tick`),
and `event_counts` (per-event totals). When the runner/bridge could resolve it, an `"episode"`
section carries non-secret context such as the player `slot` (parsed from the
WS URL — the auth token is never included). In production this same summary is
also written to the player's captured stderr log (`policy_agent_{slot}.log`).
"""


# How many position points to inline into the report. A full 10k-tick game holds
# ~10k position rows; we downsample to this many evenly-spaced points so the
# self-contained HTML stays small enough to open instantly (the heatmap re-bins
# server-side, so it is unaffected by the cap).
MAX_REPORT_POSITION_POINTS = 2000

# Heatmap grid resolution (cols x rows over the 1235x659 map). Coarse on purpose:
# a per-game occupancy heatmap, not a pixel render.
REPORT_HEATMAP_COLS = 48
REPORT_HEATMAP_ROWS = 26

# The Crewrift map's world extent (sim:25-26); used to normalize the heatmap.
REPORT_MAP_WIDTH = 1235
REPORT_MAP_HEIGHT = 659


def build_report_payload(summary: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    """Reduce one episode's recorded SQLite + summary into the report's JSON blob.

    Pure read: queries the recorder's own ``traces``/``positions`` tables and the
    already-computed ``summary`` (so episode info / event counts are reused, never
    re-derived). Everything the player-specific report charts is precomputed here
    so the inlined HTML carries a compact, ready-to-plot payload (no client-side
    SQL, no replay, no network). Never raises on missing tables/rows — a sparse
    episode simply yields empty sections that the template degrades over.
    """

    episode = summary.get("episode") or {}
    event_counts: dict[str, int] = summary.get("event_counts") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": summary.get("artifact_generated_at"),
        "episode": episode,
        "first_tick": summary.get("first_tick"),
        "last_tick": summary.get("last_tick"),
        "position_rows": summary.get("position_rows", 0),
        "stats": _report_stats(summary, event_counts),
        "domain_event_counts": {
            name: count for name, count in sorted(event_counts.items()) if name.startswith("domain.")
        },
        "heatmap": _report_heatmap(conn),
        "timeline": _report_timeline(conn),
        "markers": _report_markers(conn),
        "suspicion": _report_suspicion(conn),
        "meetings": _report_meetings(conn),
        "votes": _report_votes(conn),
    }


def _report_stats(summary: dict[str, Any], event_counts: dict[str, int]) -> dict[str, Any]:
    """The big-number stat row: game length + this player's per-game tallies."""

    first = summary.get("first_tick")
    last = summary.get("last_tick")
    game_ticks = (last - first) if isinstance(first, int) and isinstance(last, int) else None
    return {
        "game_ticks": game_ticks,
        "tasks_completed": event_counts.get("domain.task_completed", 0),
        "kills_landed": event_counts.get("domain.kill_landed", 0),
        "votes_cast": event_counts.get("domain.vote_cast", 0),
        "meetings": event_counts.get("domain.meeting_called", 0),
        "deaths_seen": event_counts.get("domain.player_died", 0),
    }


def _report_heatmap(conn: sqlite3.Connection) -> dict[str, Any]:
    """Bin non-NULL self positions into a coarse grid: 'where this player spent time'.

    Meeting ticks (camera down) record NULL self_x/self_y and are skipped. Binning
    server-side keeps the payload tiny and independent of game length.
    """

    grid = [[0] * REPORT_HEATMAP_COLS for _ in range(REPORT_HEATMAP_ROWS)]
    peak = 0
    total = 0
    try:
        rows = conn.execute(
            "SELECT self_x, self_y FROM positions WHERE self_x IS NOT NULL AND self_y IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    for self_x, self_y in rows:
        col = min(REPORT_HEATMAP_COLS - 1, max(0, int(self_x * REPORT_HEATMAP_COLS / REPORT_MAP_WIDTH)))
        row = min(REPORT_HEATMAP_ROWS - 1, max(0, int(self_y * REPORT_HEATMAP_ROWS / REPORT_MAP_HEIGHT)))
        grid[row][col] += 1
        total += 1
        if grid[row][col] > peak:
            peak = grid[row][col]
    return {
        "cols": REPORT_HEATMAP_COLS,
        "rows": REPORT_HEATMAP_ROWS,
        "map_width": REPORT_MAP_WIDTH,
        "map_height": REPORT_MAP_HEIGHT,
        "grid": grid,
        "peak": peak,
        "samples": total,
    }


def _report_timeline(conn: sqlite3.Connection) -> dict[str, Any]:
    """Downsampled mode/phase track over tick: 'what was I doing'.

    Returns evenly-spaced sample points (≤ MAX_REPORT_POSITION_POINTS) carrying the
    tick, active mode, phase, and intent — enough to render a stepped band of what
    the player was doing without inlining a full 24 Hz track.
    """

    try:
        rows = conn.execute(
            "SELECT tick, mode, phase, intent_kind FROM positions ORDER BY seq"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    sampled = _downsample(rows, MAX_REPORT_POSITION_POINTS)
    points = [
        {"tick": tick, "mode": mode, "phase": phase, "intent": intent}
        for tick, mode, phase, intent in sampled
    ]
    modes = sorted({p["mode"] for p in points if p["mode"]})
    phases = sorted({p["phase"] for p in points if p["phase"]})
    return {"points": points, "modes": modes, "phases": phases, "total_rows": len(rows)}


# Domain events worth marking on the timeline (what happened, when). Each maps to
# a short label drawn as a vertical marker over the mode/phase band.
_TIMELINE_MARKER_EVENTS: dict[str, str] = {
    "domain.kill_landed": "kill",
    "domain.meeting_called": "meeting",
    "domain.player_died": "death",
    "domain.task_completed": "task",
    "domain.vote_cast": "vote",
    "domain.game_over": "game over",
}


def _report_markers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Salient domain events with their tick, for vertical markers on the timeline."""

    placeholders = ",".join("?" for _ in _TIMELINE_MARKER_EVENTS)
    try:
        rows = conn.execute(
            f"SELECT tick, event, data FROM traces WHERE event IN ({placeholders}) ORDER BY tick, seq",
            tuple(_TIMELINE_MARKER_EVENTS),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    markers: list[dict[str, Any]] = []
    for tick, event, data in rows:
        label = _TIMELINE_MARKER_EVENTS.get(event, event)
        detail = _marker_detail(event, data)
        markers.append({"tick": tick, "event": event, "label": label, "detail": detail})
    return markers


def _marker_detail(event: str, data_json: str) -> str | None:
    """A short human label for a marker, pulled from its payload (best-effort)."""

    try:
        data = json.loads(data_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if event == "domain.kill_landed":
        return data.get("target_color")
    if event == "domain.meeting_called":
        trigger = data.get("trigger")
        by = data.get("by")
        return f"{by} · {trigger}" if by or trigger else None
    if event == "domain.player_died":
        return data.get("color")
    if event == "domain.game_over":
        return data.get("outcome")
    return None


def _report_suspicion(conn: sqlite3.Connection) -> dict[str, Any]:
    """Peak/final P(imposter) per color from domain.suspicion_snapshot (crewmate-only).

    Imposters never emit suspicion snapshots, so this is empty for them and the
    template hides the section. Tracks each color's peak posterior across all
    snapshots and its value at the last snapshot.
    """

    try:
        rows = conn.execute(
            "SELECT tick, data FROM traces WHERE event='domain.suspicion_snapshot' ORDER BY tick, seq"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    peak: dict[str, float] = {}
    final: dict[str, float] = {}
    snapshots = 0
    for _tick, data_json in rows:
        try:
            data = json.loads(data_json)
        except (TypeError, ValueError):
            continue
        ranking = data.get("ranking") if isinstance(data, dict) else None
        if not isinstance(ranking, list):
            continue
        snapshots += 1
        for entry in ranking:
            if not isinstance(entry, dict):
                continue
            color = entry.get("color")
            p = entry.get("p")
            if color is None or not isinstance(p, (int, float)):
                continue
            final[color] = float(p)
            if p > peak.get(color, -1.0):
                peak[color] = float(p)
    colors = sorted(peak, key=lambda c: peak[c], reverse=True)
    return {
        "snapshots": snapshots,
        "colors": colors,
        "peak": [round(peak[c], 4) for c in colors],
        "final": [round(final.get(c, 0.0), 4) for c in colors],
    }


def _report_meetings(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Meetings this player saw: tick, who called it, and the trigger."""

    try:
        rows = conn.execute(
            "SELECT tick, data FROM traces WHERE event='domain.meeting_called' ORDER BY tick, seq"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    meetings: list[dict[str, Any]] = []
    for tick, data_json in rows:
        try:
            data = json.loads(data_json)
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        meetings.append(
            {
                "tick": tick,
                "by": data.get("by"),
                "trigger": data.get("trigger"),
                "body_color": data.get("body_color"),
            }
        )
    return meetings


def _report_votes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """This player's votes: tick, chosen target, and the reason it gave.

    Sourced from domain.meeting_vote_selected (which carries the chosen target +
    reason); domain.vote_cast only marks the confirm edge and has no target.
    """

    try:
        rows = conn.execute(
            "SELECT tick, data FROM traces WHERE event='domain.meeting_vote_selected' ORDER BY tick, seq"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    votes: list[dict[str, Any]] = []
    for tick, data_json in rows:
        try:
            data = json.loads(data_json)
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        votes.append({"tick": tick, "target": data.get("target"), "reason": data.get("reason")})
    return votes


REPORT_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0b0d12; --panel: #14171f; --panel2: #1b1f2a; --border: #262b38;
    --fg: #e6e9ef; --muted: #9aa3b2; --accent: #6ea8fe; --good: #4ade80;
    --bad: #f87171; --warn: #fbbf24;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg); font-family: var(--sans); }
  header { padding: 24px 32px 8px; }
  h1 { margin: 0 0 4px; font-size: 22px; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 13px; font-family: var(--mono); }
  .grid { display: grid; gap: 16px; padding: 16px 32px 48px;
          grid-template-columns: repeat(12, 1fr); }
  .card { background: var(--panel); border: 1px solid var(--border);
          border-radius: 12px; padding: 16px 18px; min-width: 0; }
  .card h2 { margin: 0 0 2px; font-size: 14px; font-weight: 600; }
  .card p.hint { margin: 0 0 12px; color: var(--muted); font-size: 12px; }
  .col-12 { grid-column: span 12; } .col-8 { grid-column: span 8; }
  .col-6 { grid-column: span 6; } .col-4 { grid-column: span 4; }
  @media (max-width: 1000px) { .grid > * { grid-column: span 12 !important; } }
  .stat-row { display: flex; flex-wrap: wrap; gap: 12px; padding: 0 32px 8px; }
  .stat { background: var(--panel2); border: 1px solid var(--border);
          border-radius: 10px; padding: 10px 14px; min-width: 120px; }
  .stat .v { font-size: 22px; font-weight: 700; font-family: var(--mono); }
  .stat .k { font-size: 11px; color: var(--muted); text-transform: uppercase;
             letter-spacing: 0.05em; }
  canvas { max-height: 360px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { padding: 6px 8px; text-align: right; border-bottom: 1px solid var(--border);
           white-space: nowrap; }
  th:first-child, td:first-child { text-align: left; font-family: var(--mono); }
  thead th { color: var(--muted); font-weight: 600; position: sticky; top: 0;
             background: var(--panel); }
  .matrix-wrap { overflow-x: auto; max-height: 320px; }
  .empty { color: var(--muted); font-size: 12px; font-style: italic; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="sub">__SUBTITLE__</div>
</header>
<div class="stat-row" id="stats"></div>
<div class="grid">
  <div class="card col-8">
    <h2>Where this player spent time</h2>
    <p class="hint">Self-position heatmap binned over the map (meeting ticks, when the camera is down, are skipped). Brighter = more ticks spent there.</p>
    <canvas id="heatmap"></canvas>
  </div>
  <div class="card col-4">
    <h2>Per-game activity profile</h2>
    <p class="hint">Counts of this player's domain events for the game.</p>
    <canvas id="events"></canvas>
  </div>

  <div class="card col-12">
    <h2>What was I doing, and when did things happen</h2>
    <p class="hint">Active mode over tick (stepped), with kills / meetings / deaths / votes / game-over marked as vertical lines.</p>
    <canvas id="timeline"></canvas>
  </div>

  <div class="card col-6" id="suspicionCard">
    <h2>Suspicion — P(imposter) per color</h2>
    <p class="hint">Peak vs final posterior across this game's meeting snapshots (crewmate-only; absent for imposters).</p>
    <canvas id="suspicion"></canvas>
  </div>
  <div class="card col-6">
    <h2>Meetings &amp; this player's votes</h2>
    <p class="hint">Meetings seen (who called, trigger) and the vote target + reason this player chose.</p>
    <div class="matrix-wrap"><table id="meetings"></table></div>
    <div class="matrix-wrap" style="margin-top:12px"><table id="votes"></table></div>
  </div>
</div>
<script>const DATA = __DATA__;</script>
<script>__JS__</script>
</body>
</html>
"""


REPORT_JS = r"""
Chart.defaults.color = '#9aa3b2';
Chart.defaults.borderColor = '#262b38';
Chart.defaults.font.family = 'ui-sans-serif, system-ui, sans-serif';
const PALETTE = ['#6ea8fe','#4ade80','#f87171','#fbbf24','#a78bfa','#22d3ee','#f472b6','#facc15'];
const cap = s => s == null ? '—' : String(s);

// Stat row.
const S = DATA.stats || {};
const ep = DATA.episode || {};
const statRow = document.getElementById('stats');
[
  ['role', cap(ep.role)],
  ['outcome', cap(ep.outcome)],
  ['color', cap(ep.color)],
  ['game ticks', S.game_ticks == null ? '—' : S.game_ticks],
  ['tasks', S.tasks_completed || 0],
  ['kills', S.kills_landed || 0],
  ['votes', S.votes_cast || 0],
  ['meetings', S.meetings || 0],
].forEach(([k, v]) => {
  const d = document.createElement('div');
  d.className = 'stat';
  d.innerHTML = `<div class="v">${v}</div><div class="k">${k}</div>`;
  statRow.appendChild(d);
});

// 1. Heatmap (matrix-style scatter on a 2D canvas via a custom draw).
(function () {
  const hm = DATA.heatmap || {grid: [], cols: 0, rows: 0, peak: 0};
  const cv = document.getElementById('heatmap');
  const cols = hm.cols, rows = hm.rows, peak = hm.peak || 1;
  const W = cv.clientWidth || 720, H = Math.round(W * rows / Math.max(1, cols));
  cv.width = W; cv.height = H; cv.style.maxHeight = H + 'px';
  const ctx = cv.getContext('2d');
  ctx.fillStyle = '#0b0d12'; ctx.fillRect(0, 0, W, H);
  if (!hm.samples) {
    ctx.fillStyle = '#9aa3b2'; ctx.font = '13px ui-sans-serif';
    ctx.fillText('no self-position samples (camera was down all game)', 12, 22);
    return;
  }
  const cw = W / cols, ch = H / rows;
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const v = hm.grid[r][c]; if (!v) continue;
    const t = Math.min(1, v / peak);
    const g = Math.round(60 + t * 195), b = Math.round(255 - t * 90);
    ctx.fillStyle = `rgba(${Math.round(60 + t * 50)},${g},${b},${0.25 + 0.75 * t})`;
    ctx.fillRect(c * cw, r * ch, cw + 0.5, ch + 0.5);
  }
})();

// 2. Domain event counts bar.
const ec = DATA.domain_event_counts || {};
const ecKeys = Object.keys(ec).map(k => k.replace('domain.', ''));
new Chart(document.getElementById('events'), {
  type: 'bar',
  data: { labels: ecKeys, datasets: [{ data: Object.values(ec),
    backgroundColor: '#6ea8fecc' }] },
  options: { indexAxis: 'y', plugins: { legend: { display: false } } },
});

// 3. Mode timeline with event markers.
(function () {
  const tl = DATA.timeline || {points: [], modes: []};
  const pts = tl.points || [];
  const modes = tl.modes || [];
  const modeIdx = m => Math.max(0, modes.indexOf(m));
  const markerPlugin = {
    id: 'markers',
    afterDatasetsDraw(chart) {
      const xs = chart.scales.x, ys = chart.scales.y, ctx = chart.ctx;
      (DATA.markers || []).forEach((mk, i) => {
        const x = xs.getPixelForValue(mk.tick);
        if (x < xs.left || x > xs.right) return;
        ctx.save();
        ctx.strokeStyle = PALETTE[i % PALETTE.length]; ctx.globalAlpha = 0.6;
        ctx.beginPath(); ctx.moveTo(x, ys.top); ctx.lineTo(x, ys.bottom); ctx.stroke();
        ctx.globalAlpha = 1; ctx.fillStyle = PALETTE[i % PALETTE.length];
        ctx.font = '9px ui-monospace'; ctx.save();
        ctx.translate(x + 2, ys.top + 4); ctx.rotate(Math.PI / 2);
        ctx.fillText(mk.label + (mk.detail ? ' ' + mk.detail : ''), 0, 0);
        ctx.restore(); ctx.restore();
      });
    },
  };
  new Chart(document.getElementById('timeline'), {
    type: 'line',
    data: { datasets: [{
      label: 'mode',
      data: pts.map(p => ({ x: p.tick, y: modeIdx(p.mode) })),
      stepped: true, borderColor: '#6ea8fe', backgroundColor: '#6ea8fe33',
      pointRadius: 0, fill: false, borderWidth: 1.5,
    }] },
    options: {
      parsing: false, animation: false,
      scales: {
        x: { type: 'linear', title: { display: true, text: 'tick' } },
        y: { min: -0.5, max: Math.max(0.5, modes.length - 0.5),
             ticks: { stepSize: 1, callback: v => modes[v] || '' } },
      },
      plugins: { legend: { display: false } },
    },
    plugins: [markerPlugin],
  });
})();

// 4. Suspicion peak vs final.
(function () {
  const su = DATA.suspicion || {colors: []};
  if (!su.colors.length) {
    document.getElementById('suspicionCard').querySelector('canvas').remove();
    const p = document.createElement('div'); p.className = 'empty';
    p.textContent = 'No suspicion snapshots (imposter or no meetings this game).';
    document.getElementById('suspicionCard').appendChild(p);
    return;
  }
  new Chart(document.getElementById('suspicion'), {
    type: 'bar',
    data: { labels: su.colors, datasets: [
      { label: 'peak', data: su.peak, backgroundColor: '#f87171cc' },
      { label: 'final', data: su.final, backgroundColor: '#6ea8fecc' },
    ] },
    options: { scales: { y: { min: 0, max: 1 } } },
  });
})();

// 5. Meetings + votes tables.
(function () {
  const meetings = DATA.meetings || [];
  let h = '<thead><tr><th>tick</th><th>by</th><th>trigger</th><th>body</th></tr></thead><tbody>';
  if (!meetings.length) h += '<tr><td colspan="4" class="empty">no meetings</td></tr>';
  meetings.forEach(m => {
    h += `<tr><td>${m.tick}</td><td>${cap(m.by)}</td><td>${cap(m.trigger)}</td><td>${cap(m.body_color)}</td></tr>`;
  });
  document.getElementById('meetings').innerHTML = h + '</tbody>';

  const votes = DATA.votes || [];
  let v = '<thead><tr><th>tick</th><th>vote target</th><th>reason</th></tr></thead><tbody>';
  if (!votes.length) v += '<tr><td colspan="3" class="empty">no votes</td></tr>';
  votes.forEach(o => {
    v += `<tr><td>${o.tick}</td><td>${cap(o.target)}</td><td title="${cap(o.reason)}">${cap(o.reason)}</td></tr>`;
  });
  document.getElementById('votes').innerHTML = v + '</tbody>';
})();
"""


def build_report_html(summary: dict[str, Any], conn: sqlite3.Connection) -> str:
    """Render the self-contained, player-specific episode report HTML.

    Builds the JSON payload from the recorder's own SQLite + summary, inlines it
    into a standalone template (Chart.js via CDN, dark theme), and returns a single
    HTML string that opens offline with a double-click. The auth token is never in
    the summary, so nothing secret is inlined.
    """

    payload = build_report_payload(summary, conn)
    episode = summary.get("episode") or {}
    slot = episode.get("slot")
    role = episode.get("role")
    title = "crewborg episode report"
    if slot is not None:
        title += f" — slot {slot}"
    if role:
        title += f" ({role})"
    first = summary.get("first_tick")
    last = summary.get("last_tick")
    subtitle_bits = [f"ticks {first}..{last}", f"{summary.get('position_rows', 0)} position rows"]
    if episode.get("outcome"):
        subtitle_bits.append(f"outcome: {episode['outcome']}")
    subtitle = " · ".join(subtitle_bits)
    return (
        REPORT_HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__SUBTITLE__", subtitle)
        .replace("__DATA__", json.dumps(payload, default=str))
        .replace("__JS__", REPORT_JS)
    )


def _downsample(rows: list[Any], limit: int) -> list[Any]:
    """Evenly-spaced subsample of ``rows`` down to at most ``limit`` items."""

    n = len(rows)
    if limit <= 0 or n <= limit:
        return list(rows)
    step = n / limit
    return [rows[min(n - 1, int(i * step))] for i in range(limit)]


class SqliteEpisodeRecorder:
    """Trace *and* metrics sink that accumulates the episode into in-memory SQLite.

    Satisfies both SDK sink protocols (``record`` / ``counter`` / ``histogram`` /
    ``gauge``). Writes are lock-guarded so the sink stays safe if a strategy
    runner ever records off the inner-loop thread.
    """

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._closed = False
        self._trace_rows = 0
        self._metric_rows = 0
        self._position_rows = 0
        self._dropped_trace_rows = 0
        self._dropped_metric_rows = 0
        self._dropped_position_rows = 0
        self._event_counts: Counter[str] = Counter()
        self._first_tick: int | None = None
        self._last_tick: int | None = None
        # Best-effort episode/player metadata, populated opportunistically by the
        # bridge/event layer (e.g. slot from the WS URL at startup, role/outcome at
        # end). Kept low-coupling: the recorder never reaches into belief — callers
        # push what they have via ``set_episode_info`` and missing data is omitted.
        self._episode_info: dict[str, Any] = {}

    def set_episode_info(self, **fields: Any) -> None:
        """Merge best-effort episode/player metadata into the artifact.

        Only non-secret context belongs here (slot, role, color, outcome, tick
        counts). ``None`` values are ignored so callers can pass "what they have"
        without first checking. Surfaced under ``summary()["episode"]``.
        """

        with self._lock:
            for key, value in fields.items():
                if value is not None:
                    self._episode_info[key] = value

    # -- TraceSink protocol -------------------------------------------------

    def record(self, event: TraceEvent) -> None:
        with self._lock:
            if self._closed:
                return
            self._event_counts[event.name] += 1
            if self._first_tick is None:
                self._first_tick = event.tick
            self._last_tick = event.tick
            if self._trace_rows >= MAX_ROWS_PER_TABLE:
                self._dropped_trace_rows += 1
                return
            self._trace_rows += 1
            self._conn.execute(
                "INSERT INTO traces (wall_time, tick, event, data) VALUES (?, ?, ?, ?)",
                (time.time(), event.tick, event.name, json.dumps(event.data, default=str)),
            )

    # -- MetricsSink protocol -----------------------------------------------

    def counter(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        self._record_metric(MetricSample(kind="counter", name=name, value=value, tags=dict(tags or {})))

    def histogram(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self._record_metric(MetricSample(kind="histogram", name=name, value=value, tags=dict(tags or {})))

    def gauge(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self._record_metric(MetricSample(kind="gauge", name=name, value=value, tags=dict(tags or {})))

    def _record_metric(self, sample: MetricSample) -> None:
        with self._lock:
            if self._closed:
                return
            if self._metric_rows >= MAX_ROWS_PER_TABLE:
                self._dropped_metric_rows += 1
                return
            self._metric_rows += 1
            self._conn.execute(
                "INSERT INTO metrics (wall_time, kind, name, value, tags) VALUES (?, ?, ?, ?, ?)",
                (time.time(), sample.kind, sample.name, sample.value, json.dumps(sample.tags, default=str)),
            )

    # -- Per-tick positions table ---------------------------------------------

    def record_position(
        self,
        *,
        tick: int,
        server_tick: int | None = None,
        self_x: int | None = None,
        self_y: int | None = None,
        room_id: int | None = None,
        mode: str | None = None,
        intent_kind: str | None = None,
        held_mask: int | None = None,
        phase: str | None = None,
        visible: str = "[]",
    ) -> None:
        """Append one per-tick position/visibility row (never fatal, row-capped).

        ``visible`` is compact JSON of the players seen this tick
        (``[{"c": color, "x": x, "y": y}, ...]``). ``server_tick`` is the game's
        authoritative tick from the "tick <N>" marker sprite — the join key
        against the server's ``.bitreplay`` timeline. Full 24 Hz recording is
        ~2 MB per 10k ticks; rows past the cap are counted as dropped.
        """

        with self._lock:
            if self._closed:
                return
            if self._position_rows >= MAX_ROWS_PER_TABLE:
                self._dropped_position_rows += 1
                return
            self._position_rows += 1
            self._conn.execute(
                "INSERT INTO positions (tick, server_tick, self_x, self_y, room_id,"
                " mode, intent_kind, held_mask, phase, visible)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tick, server_tick, self_x, self_y, room_id, mode, intent_kind, held_mask, phase, visible),
            )

    # -- Artifact assembly ----------------------------------------------------

    def database_bytes(self) -> bytes:
        with self._lock:
            self._conn.commit()
            return self._conn.serialize()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            result: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "artifact_generated_at": datetime.now(timezone.utc).isoformat(),
                "trace_rows": self._trace_rows,
                "metric_rows": self._metric_rows,
                "position_rows": self._position_rows,
                "dropped_trace_rows": self._dropped_trace_rows,
                "dropped_metric_rows": self._dropped_metric_rows,
                "dropped_position_rows": self._dropped_position_rows,
                "first_tick": self._first_tick,
                "last_tick": self._last_tick,
                "event_counts": dict(sorted(self._event_counts.items())),
            }
            if self._episode_info:
                result["episode"] = dict(self._episode_info)
            return result

    def zip_bytes(self) -> bytes:
        """Build the single episode ``.zip``: ``trace.db`` + ``summary.json`` + ``README.md`` + ``report.html``.

        The ``report.html`` entry is a self-contained, player-specific visual
        summary of this one game (heatmap, mode timeline, event/suspicion charts).
        Its generation is best-effort and NEVER fatal: if it raises, the failure is
        logged and the zip is still produced with the other three entries — the
        artifact's value (trace.db / summary.json) must always survive.
        """

        summary = self.summary()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            database_bytes = self.database_bytes()
            archive.writestr("trace.db", database_bytes)
            archive.writestr("summary.json", json.dumps(summary, indent=2))
            archive.writestr("README.md", ARTIFACT_README)
            try:
                report = self._report_html(summary, database_bytes)
            except Exception as error:  # noqa: BLE001 — report must never fail the artifact.
                _log(f"failed to build report.html (zip still produced without it): {error!r}")
            else:
                archive.writestr("report.html", report)
        return buffer.getvalue()

    def _report_html(self, summary: dict[str, Any], database_bytes: bytes) -> str:
        """Render report.html from a throwaway read-only copy of the serialized DB.

        Reads from a fresh in-memory connection deserialized from ``database_bytes``
        rather than ``self._conn`` so the report's queries never contend with the
        live recorder lock or its writes.
        """

        conn = sqlite3.connect(":memory:")
        try:
            conn.deserialize(database_bytes)
            return build_report_html(summary, conn)
        finally:
            conn.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()


def upload_episode_artifact(recorder: SqliteEpisodeRecorder) -> bool:
    """Surface the recorded episode at episode end: stderr metadata + optional upload.

    Two delivery paths, both best-effort and never fatal:

    1. **Always** emit the ``summary.json`` metadata as a clearly-marked block to
       stderr (the per-slot ``policy_agent_{slot}.log`` the hosted runner captures),
       so the artifact's value is visible in production logs even though today's
       platform exposes no per-player binary upload channel.
    2. **If** a per-slot upload URL is set (``COWORLD_PLAYER_ARTIFACT_UPLOAD_URL``),
       additionally upload the ``.zip`` (``trace.db`` + ``summary.json``).

    Every outcome (metadata / no-URL / attempt / success / failure / oversize) is
    logged to stderr with a ``crewborg artifact:`` prefix so the captured logs make
    it unambiguous *whether and when* delivery happened, *to where* (with any
    presigned signature redacted), and *what* was produced.

    Returns whether a binary artifact was uploaded/written (the stderr metadata is
    always emitted regardless of the return value).
    """

    summary = recorder.summary()

    # Build the zip once: it is both the upload payload and the source of the
    # reported byte size. Best-effort — assembling it must never fail the episode.
    try:
        payload: bytes | None = recorder.zip_bytes()
    except Exception as error:  # noqa: BLE001 — zip assembly must never fail the episode.
        payload = None
        _log(f"failed to assemble episode zip: {error!r}")
    zip_size = len(payload) if payload is not None else None

    # GUARANTEED capture: emit the metadata to stderr unconditionally so the value
    # lands in the captured policy-log even when there is no binary upload channel.
    _log_summary_block(summary, zip_size)

    url = _resolve_upload_url()
    if not url:
        _log(
            f"no upload URL set ({ARTIFACT_URL_ENV}); episode summary above is the "
            "captured deliverable (the hosted runner provides no per-player binary "
            "upload channel — only the captured stderr policy-log)"
        )
        return False
    if payload is None:
        return False  # Zip assembly already failed and was logged above.

    display = _display_url(url)
    is_file = urlparse(url).scheme == "file"
    if zip_size > MAX_ARTIFACT_BYTES:
        _log(f"upload skipped: zip is {zip_size} bytes (> {MAX_ARTIFACT_BYTES} max) -> {display}")
        return False

    # Announce the destination BEFORE the PUT so a hang/failure still shows intent.
    verb_ing = "writing" if is_file else "uploading"
    _log(f"{verb_ing} -> {display} ({zip_size} bytes)")
    started = time.monotonic()
    try:
        _put_artifact(url, payload)
    except Exception as error:  # noqa: BLE001 — upload failure must never fail the episode.
        elapsed = time.monotonic() - started
        _log(f"upload FAILED -> {display}: {error!r} (after {elapsed:.2f}s)")
        return False
    elapsed = time.monotonic() - started
    verb = "wrote" if is_file else "upload OK"
    _log(f"{verb} -> {display} ({zip_size} bytes in {elapsed:.2f}s)")
    return True


def _resolve_upload_url() -> str | None:
    """First non-blank per-player upload URL among the candidate env vars, else None."""

    for name in ARTIFACT_URL_ENV_CANDIDATES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def episode_info_from_ws_url(url: str | None) -> dict[str, Any]:
    """Best-effort, non-secret episode/player metadata parsed from a WS URL.

    The Coworld runner connects the player to ``ws://<svc>:8080/player?slot=<N>&token=<T>``.
    We parse out the ``slot`` (an int when numeric) and DELIBERATELY DROP the
    ``token`` — it is an auth secret and must never land in summary.json or the
    captured policy-log. Any failure to parse simply yields no field; this never
    raises.
    """

    info: dict[str, Any] = {}
    raw = (url or "").strip()
    if not raw:
        return info
    try:
        slot_values = parse_qs(urlparse(raw).query).get("slot")
        if slot_values:
            slot_text = slot_values[0]
            info["slot"] = int(slot_text) if slot_text.lstrip("-").isdigit() else slot_text
    except Exception:  # noqa: BLE001 — metadata is best-effort, never fatal.
        return {}
    return info


def episode_info_from_env(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Best-effort episode metadata from the runner-injected WS URL env vars.

    The Coworld runner injects the per-slot WS URL as both ``COWORLD_PLAYER_WS_URL``
    and ``COGAMES_ENGINE_WS_URL`` (verified against installed coworld 0.1.20). We
    read either and parse the non-secret ``slot`` via :func:`episode_info_from_ws_url`
    (the token is dropped). Never raises.
    """

    source = os.environ if env is None else env
    for name in WS_URL_ENV_CANDIDATES:
        info = episode_info_from_ws_url(source.get(name))
        if info:
            return info
    return {}


def _log_summary_block(summary: dict[str, Any], zip_size: int | None) -> None:
    """Emit the artifact metadata to stderr as a clearly-marked, greppable block.

    The block is the guaranteed deliverable on the hosted platform: it carries the
    artifact's value (row counts, drops, tick range, top events, zip size) plus a
    single compact ``summary.json`` line that fully reconstructs ``summary.json``
    even when no binary artifact is uploaded.
    """

    _log("===== episode artifact summary (begin) =====")
    for line in _metadata_lines(summary, zip_size):
        _log(line)
    full = dict(summary)
    if zip_size is not None:
        full["zip_bytes"] = zip_size
    _log("summary.json " + json.dumps(full, separators=(",", ":"), default=str))
    _log("===== episode artifact summary (end) =====")


def _display_url(url: str) -> str:
    """Render a destination URL safe to log.

    ``file://`` paths carry no secret, so show the full filesystem path. For
    ``https://``/``http://`` show only ``scheme://host/path`` and replace any
    query string with ``<redacted>`` — presigned-PUT signatures live in the
    query and must never leak into captured policy-logs.
    """

    parsed = urlparse(url)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return f"{base}?<redacted>" if parsed.query else base


def _metadata_lines(summary: dict[str, Any], zip_size: int | None) -> list[str]:
    """One or two compact lines previewing the artifact contents."""

    fields = [
        f"{zip_size} bytes" if zip_size is not None else "zip unavailable",
        f"trace_rows={summary['trace_rows']}",
        f"metric_rows={summary['metric_rows']}",
        f"position_rows={summary['position_rows']}",
    ]
    if summary.get("dropped_trace_rows"):
        fields.append(f"dropped_trace_rows={summary['dropped_trace_rows']}")
    if summary.get("dropped_metric_rows"):
        fields.append(f"dropped_metric_rows={summary['dropped_metric_rows']}")
    if summary.get("dropped_position_rows"):
        fields.append(f"dropped_position_rows={summary['dropped_position_rows']}")
    fields.append(f"ticks={summary['first_tick']}..{summary['last_tick']}")

    lines = [", ".join(fields)]
    event_counts: dict[str, int] = summary.get("event_counts") or {}
    if event_counts:
        top = sorted(event_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        lines.append("top events: " + ", ".join(f"{name}={count}" for name, count in top))
    return lines


def _put_artifact(url: str, payload: bytes) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        # Local run: write the bytes straight into the mounted workspace.
        path = unquote(parsed.path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(payload)
        return

    # Presigned PUT. No auth header: the URL is already authorized.
    request = urllib.request.Request(
        url,
        data=payload,
        method="PUT",
        headers={"Content-Type": "application/zip"},
    )
    with urllib.request.urlopen(request, timeout=UPLOAD_TIMEOUT_SECONDS) as response:
        response.read()


def _log(message: str) -> None:
    print(f"crewborg artifact: {message}", file=sys.stderr, flush=True)
