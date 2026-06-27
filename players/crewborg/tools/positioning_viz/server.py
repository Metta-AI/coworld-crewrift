"""Kill-ready positioning viewer — local web app.

WHAT THIS IS
------------
The interactive front end of the positioning toolkit. Point it at one or more
Crewrift event warehouses (per-tick, ``--snapshot-every 1``; see
``extract_positions.py``) and browse, per replay, the spatial picture at every
imposter kill-ready moment: where the imposter has been (past P ticks), where
everyone else is, and where the imposter goes next (future F ticks, or until the
next kill). Sliders for P/F; dropdowns for replay + event. The same picture
``render_event.py`` writes to PNG, but live in the browser.

HOW TO USE IT
-------------
    # from this directory (players/crewborg/tools/positioning_viz/):
    uv run --with duckdb --with flask --with pandas python server.py /tmp/wh [more_warehouses...] [--port 8809]
    # then open http://localhost:8809

Build a warehouse first (per-tick is the warehouse default), e.g. via the
one-shot ``players/crewborg/skills/crewrift-event-warehouse/scripts/build_warehouse.py``
with a version-matched ``--expand-replay`` binary — see ``extract_positions.py``
and ``docs/reference/crewrift-replays.md`` §B.

HOW TO EDIT IT
--------------
- This is a thin Flask shell: it serves ``index.html`` and two JSON endpoints
  (``/api/replays`` -> the dropdown list; ``/api/replay/<episode_id>`` -> the full
  positioning payload). All data shaping lives in ``extract_positions.py``; all
  drawing lives in ``index.html``. To change *what is shown*, edit those, not this.
- Multiple warehouses are merged by episode id (``_EP_INDEX``); the dropdown spans
  all of them. ``--us-policy`` sets which policy is highlighted (passed through to
  ``extract_positions``).
"""

from __future__ import annotations

import argparse
import os

from flask import Flask, jsonify, send_from_directory

import extract_positions as ex

app = Flask(__name__)
WAREHOUSES: list[str] = []
_EP_INDEX: dict[str, str] = {}  # episode_id -> warehouse dir


@app.after_request
def _no_cache(resp):
    # This is an iterative dev tool — never let the browser serve stale API/HTML.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


def _build_index() -> list[dict]:
    """Scan every warehouse, build the episode->warehouse map, return the replay list."""
    rows = []
    _EP_INDEX.clear()
    for wh in WAREHOUSES:
        if not os.path.isdir(wh):
            continue
        for e in ex.list_episodes(wh):
            _EP_INDEX[e["episode_id"]] = wh
            rows.append(e)
    return rows


@app.route("/")
def index():
    """Serve the canvas viewer."""
    return send_from_directory(os.path.dirname(__file__), "index.html")


@app.route("/api/replays")
def replays():
    """The replay dropdown: every episode across all warehouses, with imposter policies."""
    return jsonify(_build_index())


@app.route("/api/replay/<episode_id>")
def replay(episode_id: str):
    """The full positioning payload for one episode (map, tracks, kills, ready_events)."""
    wh = _EP_INDEX.get(episode_id) or (WAREHOUSES[0] if WAREHOUSES else None)
    if not wh:
        return jsonify({"error": "no warehouse"}), 404
    return jsonify(ex.extract_replay(wh, episode_id))


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve the Crewrift kill-ready positioning viewer.")
    ap.add_argument("warehouses", nargs="+", help="Warehouse dir(s) (per-tick / --snapshot-every 1).")
    ap.add_argument("--port", type=int, default=8809, help="HTTP port (default 8809).")
    ap.add_argument("--us-policy", default=ex.US_POLICY, help=f"Policy to treat as 'us' (default: {ex.US_POLICY}).")
    a = ap.parse_args()

    ex.US_POLICY = a.us_policy
    global WAREHOUSES
    WAREHOUSES = [os.path.abspath(w) for w in a.warehouses]
    print(f"warehouses: {WAREHOUSES}")
    print(f"open http://localhost:{a.port}")
    app.run(port=a.port, debug=False)


if __name__ == "__main__":
    main()
