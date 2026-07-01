"""Kill-ready positioning viewer — local web app.

Point it at one or more crewrift event warehouses (ideally per-tick, --snapshot-every 1)
and browse, per replay, the spatial picture at every imposter kill-ready moment: where the
imposter has been (past P ticks), where everyone else is, and where the imposter goes next
(future F ticks or until the next kill). Sliders for P/F; dropdowns for replay + event.

Usage:
  uv run --with duckdb --with flask --with pandas python server.py /tmp/v50_pertick [more_warehouses...] [--port 8809]
Then open http://localhost:8809
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
    return send_from_directory(os.path.dirname(__file__), "index.html")


@app.route("/api/replays")
def replays():
    return jsonify(_build_index())


@app.route("/api/replay/<episode_id>")
def replay(episode_id: str):
    wh = _EP_INDEX.get(episode_id) or (WAREHOUSES[0] if WAREHOUSES else None)
    if not wh:
        return jsonify({"error": "no warehouse"}), 404
    return jsonify(ex.extract_replay(wh, episode_id))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("warehouses", nargs="+", help="warehouse dir(s) (per-tick preferred)")
    ap.add_argument("--port", type=int, default=8809)
    a = ap.parse_args()
    WAREHOUSES = [os.path.abspath(w) for w in a.warehouses]
    print(f"warehouses: {WAREHOUSES}")
    print(f"open http://localhost:{a.port}")
    app.run(port=a.port, debug=False)
