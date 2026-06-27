#!/usr/bin/env python3
"""Live UI to watch path predictions evolve over a replay.

Loads one episode from a built crewrift-event-warehouse, runs the
:class:`PathPredictor` over every tick for a selected target (fed ONLY what
crewborg actually saw — masked by ``player_visible_interval``), and serves a
self-contained page: a top-down map with all players, the selected target's
candidate nav paths drawn weighted by probability, and a timeline scrubber/play so
you can watch the distribution sharpen as the target moves and persist when it goes
out of crewborg's view.

Run:
    uv run python crewborg/tools/path_prediction_ui.py \\
        --warehouse /tmp/xp_imp_warehouse --episode ereq_...   # then open :8810

With no --episode it lists episodes in the warehouse and exits.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import replay_frames as rf  # noqa: E402

from crewborg import navbake  # noqa: E402
from crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation, Vent  # noqa: E402
from crewborg.nav import build_nav_graph  # noqa: E402
from crewborg.strategy.path_prediction import PathPredictor  # noqa: E402

DASHBOARD_HTML = HERE / "path_prediction_ui.html"


def map_from_geometry(g: dict) -> MapData:
    """Build a ``MapData`` from the warehouse ``map_geometry`` event dict."""

    return MapData(
        width=int(g["width"]), height=int(g["height"]),
        tasks=tuple(TaskStation(name=t["name"], x=int(t["x"]), y=int(t["y"]), w=int(t["w"]), h=int(t["h"]))
                    for t in g.get("tasks", [])),
        vents=tuple(Vent(x=int(v["x"]), y=int(v["y"]), w=int(v["w"]), h=int(v["h"]),
                         group=str(v["group"]), group_index=int(v["group_index"]))
                    for v in g.get("vents", [])),
        rooms=tuple(Room(name=r["name"], x=int(r["x"]), y=int(r["y"]), w=int(r["w"]), h=int(r["h"]))
                    for r in g.get("rooms", [])),
        button=MapRect(x=int(g["button"]["x"]), y=int(g["button"]["y"]), w=int(g["button"]["w"]), h=int(g["button"]["h"])),
        home=MapPoint(x=int(g["home"]["x"]), y=int(g["home"]["y"])),
    )


def build_nav(frames: rf.ReplayFrames):
    """Nav graph + map for this episode. Prefer crewborg's *real* baked croatoan nav
    (the exact walkability the live agent uses, with corridors); fall back to a
    room-rect-union approximation only if the bake can't load."""

    map_data = map_from_geometry(frames.map)
    payload = navbake._read_payload()
    if payload is not None and payload.get("nav") is not None:
        nav = payload["nav"]
        if getattr(nav, "walkability", None) is not None:
            return nav, map_data
    # Fallback: union of room rects (corridors missing → some rooms unreachable).
    g = frames.map
    w, h = int(g["width"]), int(g["height"])
    walk = np.zeros((h, w), dtype=bool)
    for room in g.get("rooms", []):
        x, y, rw, rh = int(room["x"]), int(room["y"]), int(room["w"]), int(room["h"])
        walk[max(0, y):min(h, y + rh), max(0, x):min(w, x + rw)] = True
    return build_nav_graph(walk, map_data=map_data), map_data


def run_predictions(frames: rf.ReplayFrames, nav, map_data, target_slot: int) -> list[dict]:
    """Run the predictor over the whole episode for one target; return per-frame
    rendering data (true positions + the target's weighted candidate paths)."""

    predictor = PathPredictor(nav=nav, map=map_data)
    seen_ticks = frames.visible.get(target_slot, set())
    out: list[dict] = []
    for tick in frames.ticks:
        pos = frames.positions.get(tick, {})
        tgt = pos.get(target_slot)
        visible = tick in seen_ticks and tgt is not None and tgt[2]  # alive + seen
        observed = (tgt[0], tgt[1]) if (visible and tgt is not None) else None
        predictor.observe(tick, observed)

        cands = [
            {
                "label": c.dest_label,
                "prob": round(c.prob, 3),
                "path": c.path,
                "pred": list(c.pred_pos),
            }
            for c in predictor.ranked()[:8]
        ]
        out.append({
            "tick": tick,
            "visible": bool(visible),
            "players": {
                str(s): {"x": p[0], "y": p[1], "alive": p[3] if len(p) > 3 else p[2]}
                for s, p in pos.items()
            },
            "candidates": cands,
        })
    return out


class Handler(http.server.BaseHTTPRequestHandler):
    frames: rf.ReplayFrames = None
    nav = None
    map_data = None
    cache: dict[int, list[dict]] = {}

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path in ("", "/index.html"):
            self._file(DASHBOARD_HTML, "text/html")
        elif path == "/meta":
            self._json({
                "episode_id": self.frames.episode_id,
                "map": self.frames.map,
                "players": {str(s): v for s, v in self.frames.players.items()},
                "tick_min": self.frames.ticks[0],
                "tick_max": self.frames.ticks[-1],
            })
        elif path == "/predict":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            slot = int(q.get("slot", ["1"])[0])
            if slot not in self.cache:
                self.cache[slot] = run_predictions(self.frames, self.nav, self.map_data, slot)
            self._json({"slot": slot, "frames": self.cache[slot]})
        else:
            self.send_error(404)

    def _file(self, p: Path, ctype: str):
        try:
            body = p.read_bytes()
        except OSError:
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--episode")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PRED_UI_PORT", "8810")))
    args = ap.parse_args(argv)

    if not args.episode:
        import duckdb
        con = duckdb.connect()
        rows = con.execute(
            "SELECT episode_id, count(*) FROM "
            f"read_parquet('{args.warehouse}/episode_players.parquet') GROUP BY 1 LIMIT 40"
        ).fetchall()
        print("episodes in warehouse (pass one with --episode):")
        for eid, n in rows:
            print(f"  {eid}")
        return 0

    frames = rf.load(args.warehouse, args.episode)
    nav, map_data = build_nav(frames)
    Handler.frames, Handler.nav, Handler.map_data = frames, nav, map_data
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        print(f"Path-prediction UI: http://localhost:{args.port}  (episode {args.episode}; Ctrl-C to stop)", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbye", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
