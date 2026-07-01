"""Extract per-replay positional data for the kill-ready positioning viewer.

Reads a crewrift event warehouse (ideally built with ``--snapshot-every 1`` for smooth
per-tick paths) and, for one episode, produces a compact JSON the web UI loads:

  - map geometry (rooms / vents / tasks / button) for the background,
  - every player's per-tick (x, y, alive) track,
  - kills (tick + location + killer/victim),
  - kill-ready events: each tick an imposter's kill_cooldown transitions to 0 (they
    *can* kill again) — the centre of each positioning graphic — with the tick of
    their next kill (so the UI can draw the path forward "until next kill").

The UI then draws, per ready-event: the focal imposter's path P ticks back → ready →
F ticks forward (or to next kill), and everyone else's path P ticks back, on the map.

CLI:  python extract_positions.py <warehouse_dir> [--episode EPISODE_ID] [--list]
"""

from __future__ import annotations

import argparse
import glob
import json
import os

import duckdb
import pandas as pd

# policy_name -> the human we think of it as (for labels/colour grouping)
PLAYER_LABEL = {"crewborg": "Us (crewborg)", "crewborg-aaln": "Aaron", "truecrew": "Andre"}


def _int(v):
    """int() that tolerates pandas NA / None -> None."""
    return None if v is None or pd.isna(v) else int(v)


def _ev(wh: str, key: str) -> str:
    return f"read_parquet('{wh}/events/key={key}/*.parquet')"


def list_episodes(wh: str) -> list[dict]:
    """Episodes in the warehouse, with a one-line summary (imposter policies + #ready)."""
    con = duckdb.connect()
    players = con.execute(
        f"SELECT episode_id, slot, role, policy_name FROM read_parquet('{wh}/episode_players.parquet')"
    ).df()
    out = {}
    for r in players.itertuples():
        e = out.setdefault(r.episode_id, {"episode_id": r.episode_id, "imposters": []})
        if r.role == "imposter":
            e["imposters"].append(PLAYER_LABEL.get(r.policy_name, r.policy_name))
    return sorted(out.values(), key=lambda e: e["episode_id"])


def extract_replay(wh: str, episode_id: str) -> dict:
    con = duckdb.connect()

    # --- map background (one geometry row per episode) ---
    mg = con.execute(
        f"SELECT value FROM {_ev(wh, 'map_geometry')} WHERE episode_id = ? LIMIT 1", [episode_id]
    ).fetchone()
    gmap = json.loads(mg[0]) if mg else {}
    game_map = {
        "width": gmap.get("width"),
        "height": gmap.get("height"),
        "rooms": gmap.get("rooms", []),
        "vents": gmap.get("vents", []),
        "tasks": gmap.get("tasks", []),
        "button": gmap.get("button"),
        "home": gmap.get("home"),
    }

    # --- players (slot -> role/policy/label) ---
    pl = con.execute(
        "SELECT slot, role, policy_name FROM read_parquet(?) WHERE episode_id = ? ORDER BY slot",
        [f"{wh}/episode_players.parquet", episode_id],
    ).df()
    players = [
        {"slot": int(r.slot), "role": r.role, "policy": r.policy_name,
         "label": PLAYER_LABEL.get(r.policy_name, r.policy_name)}
        for r in pl.itertuples()
    ]

    # --- per-tick state: x, y, alive, kill_cooldown, phase ---
    ps = con.execute(
        f"""SELECT slot,
              json_extract(value,'$.x')::INT x, json_extract(value,'$.y')::INT y,
              json_extract_string(value,'$.alive') alive,
              json_extract(value,'$.kill_cooldown')::INT cd,
              json_extract_string(value,'$.phase') phase, ts
            FROM {_ev(wh, 'player_state')} WHERE episode_id = ? ORDER BY slot, ts""",
        [episode_id],
    ).df()

    tracks: dict[int, list] = {p["slot"]: [] for p in players}
    cd_series: dict[int, list] = {p["slot"]: [] for p in players}
    for r in ps.itertuples():
        s = int(r.slot)
        playing = r.phase == "Playing"
        # track sample: [tick, x, y, alive, playing] — the playing flag lets the UI drop
        # meeting ticks (during a meeting all players teleport home, which otherwise draws a
        # bogus jump-to-Bridge in the path).
        tracks.setdefault(s, []).append([int(r.ts), r.x, r.y, r.alive == "true", playing])
        cd_series.setdefault(s, []).append((int(r.ts), r.cd, r.phase))

    # --- kills ---
    kraw = con.execute(
        f"""SELECT slot, ts, json_extract(value,'$.victim_slot')::INT victim,
              json_extract(value,'$.x')::INT x, json_extract(value,'$.y')::INT y
            FROM {_ev(wh, 'kill')} WHERE episode_id = ? ORDER BY ts""",
        [episode_id],
    ).df()
    def _pos_at(slot, t):
        """Killer's (x, y) at tick t — kills carry no coords, so the kill happened where
        the killer was standing. Nearest sample wins (per-tick warehouse => exact)."""
        tr = tracks.get(slot) or []
        best, bd = None, 1e9
        for p in tr:
            d = abs(p[0] - t)
            if d < bd:
                bd, best = d, p
        return (best[1], best[2]) if best else (None, None)

    kills = []
    for r in kraw.itertuples():
        kx, ky = _int(r.x), _int(r.y)
        if kx is None:
            kx, ky = _pos_at(int(r.slot), int(r.ts))
        kills.append({"tick": int(r.ts), "killer": int(r.slot), "victim": _int(r.victim), "x": kx, "y": ky})
    kills_by_killer: dict[int, list[int]] = {}
    for k in kills:
        kills_by_killer.setdefault(k["killer"], []).append(k["tick"])

    # --- kill-ready events: imposter kill_cooldown transitions >0 -> 0, while Playing & alive ---
    role_of = {p["slot"]: p["role"] for p in players}
    label_of = {p["slot"]: p["label"] for p in players}
    policy_of = {p["slot"]: p["policy"] for p in players}
    # A ready event = an imposter's kill_cooldown hitting 0 while Playing. Its window is
    # MEETING-AWARE: it ends at the imposter's next kill OR the next meeting (whichever first),
    # because a meeting both stops the hunt and RESETS the cooldown — so a kill after a meeting
    # is NOT a conversion of this ready moment. idle_ready_ticks counts only Playing+ready ticks.
    ready_events = []
    for s, series in cd_series.items():
        if role_of.get(s) != "imposter":
            continue
        kill_ticks = sorted(kills_by_killer.get(s, []))
        prev_cd = None
        for ts, cd, phase in series:
            if cd == 0 and (prev_cd is None or prev_cd > 0) and phase == "Playing":
                meeting_start = next((t2 for t2, c2, p2 in series if t2 > ts and p2 != "Playing"), None)
                next_kill = next((k for k in kill_ticks if k >= ts), None)
                if next_kill is not None and (meeting_start is None or next_kill <= meeting_start):
                    converted, kill_tick, ended_by, window_end = True, next_kill, "kill", next_kill
                elif meeting_start is not None:
                    converted, kill_tick, ended_by, window_end = False, None, "meeting", meeting_start
                else:
                    converted, kill_tick, ended_by, window_end = False, None, "game_end", series[-1][0]
                idle = sum(1 for t2, c2, p2 in series if ts <= t2 <= window_end and c2 == 0 and p2 == "Playing")
                ready_events.append({
                    "tick": ts, "slot": s, "policy": policy_of[s], "label": label_of[s],
                    "converted": converted, "kill_tick": kill_tick, "ended_by": ended_by,
                    "window_end": window_end, "idle_ready_ticks": idle,
                    "next_kill_tick": kill_tick,  # only the converting kill (None if a meeting interrupted)
                })
            prev_cd = cd
    ready_events.sort(key=lambda e: e["tick"])

    return {
        "episode_id": episode_id,
        "map": game_map,
        "players": players,
        "tracks": tracks,          # slot -> [[tick,x,y,alive], ...]
        "kills": kills,
        "ready_events": ready_events,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("warehouse")
    ap.add_argument("--episode")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list or not a.episode:
        eps = list_episodes(a.warehouse)
        print(f"{len(eps)} episodes:")
        for e in eps[:40]:
            print(f"  {e['episode_id']}  imposters={e['imposters']}")
    else:
        d = extract_replay(a.warehouse, a.episode)
        print(json.dumps({k: (v if k != "tracks" else {s: len(t) for s, t in v.items()}) for k, v in d.items()},
                         indent=1, default=str)[:1500])
        print(f"\nready_events: {len(d['ready_events'])} | kills: {len(d['kills'])}")
