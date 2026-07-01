#!/usr/bin/env python3
"""Live dashboard for one or more in-flight experience requests.

Game-agnostic. Point it at `xreq_…` ids and open the browser: it polls the
Observatory API, pulls each *completed* episode's per-seat `results.json` exactly
once, and serves a self-contained page that updates as completions roll in:

  - completion progress + throughput/ETA per request and overall
  - a leaderboard of per-player win-rate and mean score
  - a player x player win-rate heatmap (row = focal player, col = role they held;
    plus a same-episode head-to-head grid)
  - per-player score distributions as points along one horizontal line per player

It only READS (episode lists + results artifacts), so it is safe to run alongside
the requests and alongside `fetch_artifacts.py`. Stats are attributed by SEAT from
each episode's `participants` (position -> player/policy/version) and
`game_config.slots` (position -> role), so they never depend on the deduped inline
`scores` field.

Run:
    uv run python xp_dashboard.py xreq_abc... xreq_def...
    uv run python xp_dashboard.py --port 8808 xreq_abc...
    XP_DASH_PORT=8808 uv run python xp_dashboard.py xreq_abc...

then open http://localhost:8808 . Labels: a player is shown as its `player_name`;
a seat is additionally tagged with `policy:vN` so multiple versions of the same
player (e.g. a threshold sweep) stay distinct.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Reuse the artifact downloader's authenticated client + auth helpers (same skill family).
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coworld-episode-artifacts" / "scripts"))
import fetch_artifacts as FA  # noqa: E402

HERE = Path(__file__).resolve().parent
DASHBOARD_HTML = HERE / "xp_dashboard.html"
POLL_SECONDS = 15          # how often the background poller hits the API
RATE_WINDOW_SECONDS = 180  # throughput measured over the last N seconds


def seat_label(participant: dict) -> str:
    """A stable display label for a seat's occupant: 'Player Name (policy:vN)'."""
    player = participant.get("player_name") or "?"
    policy = participant.get("policy_name") or "?"
    ver = participant.get("version")
    return f"{player} ({policy}:v{ver})" if ver is not None else f"{player} ({policy})"


class Poller:
    """Polls the given experience requests and accumulates per-seat episode stats.

    Thread-safe snapshot via `snapshot()`. Each completed episode's results are
    fetched once (keyed by episode id) and then cached forever.
    """

    def __init__(self, client: FA.Client, xreqs: list[str]) -> None:
        self._client = client
        self._xreqs = xreqs
        self._lock = threading.Lock()
        # episode_id -> {"xreq","roles":[...],"seats":[label,...],"results":{...}|None,"ts":float}
        self._episodes: dict[str, dict] = {}
        # xreq -> {"total":int,"completed":int,"status":str}
        self._req: dict[str, dict] = {x: {"total": 0, "completed": 0, "status": "?"} for x in xreqs}
        self._started = time.time()
        self._last_poll = 0.0
        self._poll_count = 0

    # --- polling -----------------------------------------------------------------
    def run_forever(self) -> None:
        while True:
            try:
                self._poll_once()
            except Exception as exc:  # never let the poll thread die
                print(f"[poll error] {exc}", file=sys.stderr, flush=True)
            time.sleep(POLL_SECONDS)

    def _poll_once(self) -> None:
        # Collect the episodes that still need a results fetch this poll, across all
        # requests, then fetch them concurrently (the result GETs dominate latency;
        # already-captured episodes are skipped inside _ingest).
        pending: list[tuple[str, dict]] = []
        for xreq in self._xreqs:
            rows = self._client.get_json(f"/v2/experience-requests/{xreq}/episodes")
            rows = rows if isinstance(rows, list) else rows.get("entries", [])
            completed = sum(1 for r in rows if r.get("status") == "completed")
            for r in rows:
                pending.append((xreq, r))
            with self._lock:
                self._req[xreq] = {
                    "total": len(rows),
                    "completed": completed,
                    "status": "done" if (rows and completed == len(rows)) else "running",
                }
        with ThreadPoolExecutor(max_workers=8) as pool:
            pool.map(lambda xr: self._ingest(xr[0], xr[1]), pending)
        with self._lock:
            self._last_poll = time.time()
            self._poll_count += 1

    def _ingest(self, xreq: str, row: dict) -> None:
        eid = row.get("id")
        if not eid:
            return
        with self._lock:
            have = self._episodes.get(eid)
        if have and have.get("results") is not None:
            return  # already fully captured
        if row.get("status") != "completed":
            return
        # Per-seat identity, ordered by seat position. Role is derived later in
        # snapshot() from the results artifact's imposter/crew flags (game_config.slots
        # is empty on natural-roles evals).
        parts = row.get("participants") or []
        seats = [seat_label(p) for p in sorted(parts, key=lambda p: p.get("position", 0))]
        results = None
        job = row.get("job_id")
        if job:
            txt = self._client.get_text_or_none(f"/jobs/{job}/artifacts/results")
            if txt:
                try:
                    results = json.loads(txt)
                except json.JSONDecodeError:
                    results = None
        with self._lock:
            self._episodes[eid] = {
                "xreq": xreq, "seats": seats,
                "results": results, "ts": time.time(),
            }

    # --- snapshot / stats --------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            episodes = list(self._episodes.values())
            req = {k: dict(v) for k, v in self._req.items()}
            last_poll = self._last_poll
            poll_count = self._poll_count
            started = self._started

        now = time.time()
        # Only episodes with results contribute to stats.
        scored = [e for e in episodes if e.get("results")]

        # Per-player accumulation. Key = seat label (player + policy:vN).
        # Track overall + per-role; collect score samples; head-to-head wins.
        players: dict[str, dict] = {}

        def P(label: str) -> dict:
            if label not in players:
                players[label] = {
                    "label": label, "n": 0, "wins": 0, "scores": [],
                    "crew_n": 0, "crew_w": 0, "imp_n": 0, "imp_w": 0,
                }
            return players[label]

        ops_filtered = 0
        for e in scored:
            res = e["results"]
            win = res.get("win") or []
            score = res.get("scores") or []
            ct = res.get("connect_timeout") or []
            dt = res.get("disconnect_timeout") or []
            seats = e["seats"]
            n_seats = min(len(seats), len(win), len(score))
            # Per-seat role comes from the results artifact's `imposter`/`crew` flag
            # arrays — authoritative for BOTH natural-roles and role-pinned evals. The
            # old source (`game_config.slots[].role`) is EMPTY on natural-roles runs, so
            # the per-role split never populated. See results.json: imposter=[1,0,...].
            imp_flags = res.get("imposter") or []
            crew_flags = res.get("crew") or []
            # Episode-level ops filter: if ANY seat timed out, the whole episode is
            # corrupt (-100 across the board) — skip it for stats.
            if any((ct[i] if i < len(ct) else 0) or (dt[i] if i < len(dt) else 0)
                   for i in range(n_seats)):
                ops_filtered += 1
                continue
            for i in range(n_seats):
                lab = seats[i]
                w = bool(win[i])
                p = P(lab)
                p["n"] += 1
                p["wins"] += int(w)
                p["scores"].append([score[i], int(w)])
                if i < len(imp_flags) and imp_flags[i]:
                    p["imp_n"] += 1; p["imp_w"] += int(w)
                elif i < len(crew_flags) and crew_flags[i]:
                    p["crew_n"] += 1; p["crew_w"] += int(w)

        def winrate(w: int, n: int) -> float | None:
            return round(100.0 * w / n, 1) if n else None

        leaderboard = []
        for p in players.values():
            leaderboard.append({
                "label": p["label"], "n": p["n"],
                "win": winrate(p["wins"], p["n"]),
                "crew_win": winrate(p["crew_w"], p["crew_n"]), "crew_n": p["crew_n"],
                "imp_win": winrate(p["imp_w"], p["imp_n"]), "imp_n": p["imp_n"],
                "score_mean": round(sum(s for s, _ in p["scores"]) / len(p["scores"]), 1) if p["scores"] else None,
                "scores": p["scores"],   # [[score, win], ...]
            })
        leaderboard.sort(key=lambda r: (r["win"] if r["win"] is not None else -1), reverse=True)

        # Throughput / ETA from completion timestamps in the recent window.
        recent = sum(1 for e in scored if now - e["ts"] <= RATE_WINDOW_SECONDS)
        rate_per_min = recent / (RATE_WINDOW_SECONDS / 60.0)
        total = sum(v["total"] for v in req.values())
        done = sum(v["completed"] for v in req.values())
        pending = max(0, total - done)
        eta = round(pending / rate_per_min * 60.0) if rate_per_min > 0 else None

        return {
            "now": now, "started": started, "last_poll": last_poll, "poll_count": poll_count,
            "requests": [{"xreq": k, **v} for k, v in req.items()],
            "total": total, "done": done, "pending": pending,
            "pct": round(100.0 * done / total, 1) if total else 0.0,
            "scored_episodes": len(scored), "ops_filtered": ops_filtered,
            "rate_per_min": round(rate_per_min, 1), "eta_seconds": eta,
            "rate_window_seconds": RATE_WINDOW_SECONDS,
            "leaderboard": leaderboard,
        }


class Handler(http.server.BaseHTTPRequestHandler):
    poller: Poller = None  # set in main

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/index.html", "/dashboard"):
            self._send_file(DASHBOARD_HTML, "text/html")
        elif self.path.startswith("/status"):
            self._send_json(self.poller.snapshot())
        else:
            self.send_error(404)

    def _send_file(self, path: Path, ctype: str):
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xreqs", nargs="+", help="One or more xreq_… ids to monitor.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("XP_DASH_PORT", "8808")))
    args = ap.parse_args(argv)

    client = FA.Client(FA.default_server(), FA.load_token())
    poller = Poller(client, args.xreqs)
    # Prime + poll in the background so the server binds and serves IMMEDIATELY.
    # Priming many requests x ~100 result fetches can take minutes; the page just
    # shows 0/0 until the first poll lands, then fills in.
    threading.Thread(target=poller.run_forever, daemon=True).start()

    Handler.poller = poller
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as httpd:
        print(f"XP dashboard: http://localhost:{args.port}  ({len(args.xreqs)} request(s); Ctrl-C to stop)", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbye", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
