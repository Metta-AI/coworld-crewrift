"""Extract per-replay positional data for the kill-ready positioning viewer.

WHAT THIS IS
------------
The data layer of the positioning viewer. Given a built Crewrift **event
warehouse** and one episode, it produces the compact dict the UI draws:

  - map geometry (rooms / vents / tasks / button) for the background,
  - every player's per-tick (x, y, alive, playing) track,
  - kills (tick + location + killer/victim),
  - **kill-ready events**: each tick an imposter's ``kill_cooldown`` transitions
    to 0 (it *can* kill again) — the centre of each positioning graphic — with the
    tick of the kill that converted it (so the UI can draw the path "until next
    kill"). The window is meeting-aware (see ``extract_replay``).

The viewer then draws, per ready-event: the focal imposter's path P ticks back →
ready → forward to its next kill (or F ticks), plus everyone else's recent path,
on the map. The point: *see where an imposter sits the moment it can kill again*,
and how long/far it hunts before converting.

HOW TO USE IT (end-to-end flow across the 5 files)
--------------------------------------------------
1. Build a **per-tick** event warehouse (the viewer needs ``player_state``
   position snapshots every tick; the warehouse defaults to ``--snapshot-every 1``,
   so per-tick is the default). The one-shot builder, from the repo root:

       B=players/crewborg/skills/crewrift-event-warehouse/scripts/build_warehouse.py
       uv run python "$B" --xreq <xreq_id> --out /tmp/wh --expand-replay /tmp/expand-<commit>

   ``--expand-replay`` must be a version-matched ``expand_replay`` binary (built
   from this repo's ``tools/expand_replay.nim`` at the arena's deployed commit —
   ``CREWRIFT_REF`` in ``tools/build/versions.env``). See
   ``docs/reference/crewrift-replays.md`` §B and the
   ``crewrift-event-warehouse`` skill for the version-coupling recipe.

2. ``extract_positions.py`` (this file) reads that warehouse for one episode.
   List episodes, or dump one as a sanity check:

       python extract_positions.py /tmp/wh --list
       python extract_positions.py /tmp/wh --episode <episode_id>

3. ``render_event.py`` renders a ready-event (or a montage) to PNG for headless /
   agent viewing. ``server.py`` + ``index.html`` serve the same picture as an
   interactive browser viewer. Both import this module.

HOW TO EDIT IT
--------------
- **What counts as "us" (the highlighted policy)** is ``US_POLICY`` below; it can
  be overridden at runtime (``server.py`` / ``render_event.py`` both take
  ``--us-policy``). It is echoed into the payload as ``us_policy`` so the browser
  can highlight without a hardcoded name.
- **Where data comes from**: every field is pulled from the warehouse ``events``
  table by ``key`` (``player_state``, ``kill``, ``map_geometry``) plus the
  ``episode_players`` dimension. The exact ``value`` JSON fields per key are
  documented in
  ``players/crewborg/skills/crewrift-event-warehouse/references/event-catalog.md``
  — change a SELECT here only against that catalog.
- **What a ready event is / how its window ends** lives entirely in
  ``extract_replay`` (the ``ready_events`` loop). The meeting-aware window rule is
  load-bearing (a meeting resets the cooldown, so a kill *after* a meeting is not a
  conversion of this ready moment) — see ``docs/best_practices.md`` ("Meeting ticks
  are NOT idle time").
- **To add a field the UI draws** (e.g. velocity, room): add it to the
  ``player_state`` SELECT, append it to the per-tick track sample, and read it in
  ``render_event.py`` / ``index.html``.
"""

from __future__ import annotations

import argparse
import json

import duckdb
import pandas as pd

# The policy we treat as "us" — highlighted in every view and starred in lists.
# Overridable at runtime via the --us-policy flag in server.py / render_event.py.
US_POLICY = "crewborg"


def _int(v):
    """int() that tolerates pandas NA / None -> None."""
    return None if v is None or pd.isna(v) else int(v)


def _events(warehouse: str, key: str) -> str:
    """A read_parquet(...) SQL expression for one event ``key`` partition."""
    return f"read_parquet('{warehouse}/events/key={key}/*.parquet')"


def list_episodes(warehouse: str) -> list[dict]:
    """All episodes in the warehouse, each with its imposter policy names.

    Cheap (reads only the ``episode_players`` dimension), used to populate the
    replay dropdown and to scan for a policy's ready events.
    """
    con = duckdb.connect()
    players = con.execute(
        f"SELECT episode_id, slot, role, policy_name "
        f"FROM read_parquet('{warehouse}/episode_players.parquet')"
    ).df()
    out: dict[str, dict] = {}
    for r in players.itertuples():
        e = out.setdefault(r.episode_id, {"episode_id": r.episode_id, "imposters": []})
        if r.role == "imposter":
            e["imposters"].append(r.policy_name)
    return sorted(out.values(), key=lambda e: e["episode_id"])


def extract_replay(warehouse: str, episode_id: str) -> dict:
    """Build the full positioning payload for one episode.

    Returns a dict with: ``episode_id``, ``us_policy``, ``map`` (geometry),
    ``players`` (slot/role/policy/label), ``tracks`` (slot -> per-tick samples),
    ``kills``, and ``ready_events``. This is exactly what ``server.py`` JSON-encodes
    for the browser and what ``render_event.py`` draws.
    """
    con = duckdb.connect()

    # --- map background (one geometry row per episode) ---
    mg = con.execute(
        f"SELECT value FROM {_events(warehouse, 'map_geometry')} WHERE episode_id = ? LIMIT 1",
        [episode_id],
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

    # --- players (slot -> role / policy / label) ---
    pl = con.execute(
        "SELECT slot, role, policy_name FROM read_parquet(?) WHERE episode_id = ? ORDER BY slot",
        [f"{warehouse}/episode_players.parquet", episode_id],
    ).df()
    players = [
        {"slot": int(r.slot), "role": r.role, "policy": r.policy_name, "label": r.policy_name}
        for r in pl.itertuples()
    ]

    # --- per-tick state: x, y, alive, kill_cooldown, phase ---
    # player_state value fields are catalogued in references/event-catalog.md.
    ps = con.execute(
        f"""SELECT slot,
              json_extract(value,'$.x')::INT x, json_extract(value,'$.y')::INT y,
              json_extract_string(value,'$.alive') alive,
              json_extract(value,'$.kill_cooldown')::INT cd,
              json_extract_string(value,'$.phase') phase, ts
            FROM {_events(warehouse, 'player_state')} WHERE episode_id = ? ORDER BY slot, ts""",
        [episode_id],
    ).df()

    # tracks[slot] = [[tick, x, y, alive, playing], ...]; the `playing` flag lets the
    # UI drop meeting ticks (during a meeting all players teleport home, which would
    # otherwise draw a bogus jump-to-Bridge in the path).
    tracks: dict[int, list] = {p["slot"]: [] for p in players}
    cd_series: dict[int, list] = {p["slot"]: [] for p in players}
    for r in ps.itertuples():
        s = int(r.slot)
        playing = r.phase == "Playing"
        tracks.setdefault(s, []).append([int(r.ts), r.x, r.y, r.alive == "true", playing])
        cd_series.setdefault(s, []).append((int(r.ts), r.cd, r.phase))

    # --- kills ---
    kraw = con.execute(
        f"""SELECT slot, ts, json_extract(value,'$.victim_slot')::INT victim,
              json_extract(value,'$.x')::INT x, json_extract(value,'$.y')::INT y
            FROM {_events(warehouse, 'kill')} WHERE episode_id = ? ORDER BY ts""",
        [episode_id],
    ).df()

    def _pos_at(slot: int, t: int):
        """Killer's (x, y) at tick ``t`` from its track (nearest sample wins).

        The ``kill`` event carries no coordinates (see the event catalog), so the
        kill happened where the killer was standing — its sampled position at the
        kill tick. With a per-tick warehouse the nearest sample is exact.
        """
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

    # --- kill-ready events: imposter kill_cooldown transitions >0 -> 0, while Playing ---
    role_of = {p["slot"]: p["role"] for p in players}
    label_of = {p["slot"]: p["label"] for p in players}
    policy_of = {p["slot"]: p["policy"] for p in players}
    # A ready event = an imposter's kill_cooldown hitting 0 while Playing. Its window is
    # MEETING-AWARE: it ends at the imposter's next kill OR the next meeting (whichever
    # comes first), because a meeting both stops the hunt and RESETS the cooldown — so a
    # kill after a meeting is NOT a conversion of this ready moment. idle_ready_ticks
    # counts only Playing+ready ticks inside the window.
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
        "us_policy": US_POLICY,
        "map": game_map,
        "players": players,
        "tracks": tracks,          # slot -> [[tick, x, y, alive, playing], ...]
        "kills": kills,
        "ready_events": ready_events,
    }


def main() -> None:
    """CLI: list episodes, or dump one episode's payload summary for a sanity check."""
    global US_POLICY
    ap = argparse.ArgumentParser(description="Extract positioning data from a Crewrift event warehouse.")
    ap.add_argument("warehouse", help="Built event warehouse dir (per-tick / --snapshot-every 1).")
    ap.add_argument("--episode", help="Episode id to extract (omit to list episodes).")
    ap.add_argument("--list", action="store_true", help="List episodes and exit.")
    ap.add_argument("--us-policy", default=US_POLICY, help=f"Policy to treat as 'us' (default: {US_POLICY}).")
    a = ap.parse_args()
    US_POLICY = a.us_policy

    if a.list or not a.episode:
        eps = list_episodes(a.warehouse)
        print(f"{len(eps)} episodes:")
        for e in eps[:40]:
            print(f"  {e['episode_id']}  imposters={e['imposters']}")
        return

    d = extract_replay(a.warehouse, a.episode)
    summary = {k: (v if k != "tracks" else {s: len(t) for s, t in v.items()}) for k, v in d.items()}
    print(json.dumps(summary, indent=1, default=str)[:1500])
    print(f"\nready_events: {len(d['ready_events'])} | kills: {len(d['kills'])}")


if __name__ == "__main__":
    main()
