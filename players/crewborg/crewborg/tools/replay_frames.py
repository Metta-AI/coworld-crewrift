"""Load one episode from a built crewrift-event-warehouse into replay frames.

Shared by the path-prediction eval harness and the live UI. Given a warehouse
directory and an ``episode_id`` it returns everything needed to drive the
:class:`PathPredictor` off real replay data without a live game:

- ``map``: the static map geometry (rooms/tasks/vents/dims) for rendering and for
  building the nav graph.
- ``positions``: per-tick ground-truth ``{tick: {slot: (x, y, alive, room)}}`` from
  the replay's ``player_state`` events.
- ``visible``: crewborg's (slot 0) visibility of each other slot, as a set of ticks
  per target — so observations can be masked to *only what crewborg actually saw*.
- ``players``: ``{slot: {policy, role, name}}`` for labelling the agent dropdown.

Requires ``duckdb`` (a warehouse runtime dep). Read-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import duckdb


@dataclass
class ReplayFrames:
    episode_id: str
    map: dict
    players: dict[int, dict]
    positions: dict[int, dict[int, tuple[int, int, bool, str, bool]]]  # x,y,alive,room,inside_room
    visible: dict[int, set[int]]  # target_slot -> set of ticks crewborg (slot 0) saw it
    ticks: list[int]


def _con(warehouse: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        "CREATE VIEW events AS SELECT * FROM "
        f"read_parquet('{warehouse}/events/**/*.parquet', hive_partitioning=true)"
    )
    con.execute(
        "CREATE VIEW players AS SELECT * FROM "
        f"read_parquet('{warehouse}/episode_players.parquet')"
    )
    return con


def load(warehouse: str, episode_id: str) -> ReplayFrames:
    con = _con(warehouse)

    geom = con.execute(
        "SELECT value FROM events WHERE key='map_geometry' AND episode_id=? LIMIT 1",
        [episode_id],
    ).fetchone()
    if not geom:
        raise SystemExit(f"no map_geometry for {episode_id} (wrong warehouse/episode?)")
    map_dict = json.loads(geom[0])

    players: dict[int, dict] = {}
    for slot, policy, role, name in con.execute(
        "SELECT slot, policy_name, role, COALESCE(policy_name,'') FROM players WHERE episode_id=?",
        [episode_id],
    ).fetchall():
        players[int(slot)] = {"policy": policy, "role": role, "name": name}

    # Per-tick ground-truth positions from player_state.
    positions: dict[int, dict[int, tuple[int, int, bool, str, bool]]] = {}
    for ts, slot, value in con.execute(
        "SELECT ts, slot, value FROM events WHERE key='player_state' AND episode_id=? AND slot>=0",
        [episode_id],
    ).fetchall():
        v = json.loads(value)
        positions.setdefault(int(ts), {})[int(slot)] = (
            int(v["x"]), int(v["y"]), bool(v.get("alive", True)), v.get("room") or "",
            bool(v.get("inside_room", True)),
        )

    # Crewborg (slot 0) visibility of each target, expanded to a per-tick set.
    visible: dict[int, set[int]] = {}
    for value in con.execute(
        "SELECT value FROM events WHERE key='player_visible_interval' AND episode_id=? "
        "AND CAST(json_extract_string(value,'$.observer_slot') AS INT)=0",
        [episode_id],
    ).fetchall():
        v = json.loads(value[0])
        tslot = v.get("target_slot")
        if tslot is None:
            continue
        t0 = int(v["tick_start"])
        t1 = int(v.get("last_observed_tick", v.get("tick_end", t0)))
        s = visible.setdefault(int(tslot), set())
        s.update(range(t0, t1 + 1))

    ticks = sorted(positions)
    return ReplayFrames(
        episode_id=episode_id, map=map_dict, players=players,
        positions=positions, visible=visible, ticks=ticks,
    )
