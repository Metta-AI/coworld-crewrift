#!/usr/bin/env python3
"""Compare policies' per-game BEHAVIOUR head-to-head from an event warehouse.

Given a built `crewrift-event-warehouse` (see ../.claude/skills/crewrift-event-warehouse), this
diffs any set of policies — in a chosen ROLE — on the behavioural dimensions that drive
imposter (or crew) success: proximity to the opposite team, isolation (kill setups),
following/chasing, room circulation, where time is spent, and ejection rate. Where
`compare.py` (the crewrift-ab skill) diffs *outcome* metrics from results.json, this diffs
*behaviour* from the replay events — the "why" behind the numbers.

It is **policy-agnostic**: pass any policy names (or let it use every policy in the
warehouse). It is not specific to any opponent.

USAGE
    # compare crewborg against an opponent, as imposter (the default role):
    uv run --with duckdb python tools/behavior_compare.py /tmp/wh --policies crewborg <opponent>
    # crew behaviour across every policy in the warehouse:
    uv run --with duckdb python tools/behavior_compare.py /tmp/wh --role crew

HOW TO EDIT (add a dimension)
    Each dimension is one small function that runs a DuckDB query over the warehouse and
    returns a per-policy DataFrame, plus a print. To add one: write a `dim_*` query against
    the relevant event `key` (the catalog is
    ../.claude/skills/crewrift-event-warehouse/references/event-catalog.md — note the slot-join
    rules and that proximity/isolation are GLOBAL rows with `player_a`/`player_b`), then call
    it in `main`. Per-game rates divide by `games[policy]` (the ops-filtered role-game count).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

OPPOSITE = {"imposter": "crew", "crew": "imposter"}


class Warehouse:
    """Thin DuckDB wrapper over a warehouse dir + the role-restricted player set."""

    def __init__(self, wh: Path, policies: list[str] | None, role: str):
        self.con = duckdb.connect()
        self.wh, self.role, self.opp = wh, role, OPPOSITE[role]
        # actor (episode, slot) for each policy in `role`, ops-filtered (score<0 == a crash).
        where_pol = "" if not policies else "AND policy_name IN (" + ",".join(f"'{p}'" for p in policies) + ")"
        self.actors = self.con.execute(
            f"SELECT policy_name, episode_id, slot, win FROM {self.ep()} "
            f"WHERE role='{role}' AND COALESCE(score,0) >= 0 {where_pol}").df()
        self.con.register("actors", self.actors)
        self.games = self.actors.groupby("policy_name")["episode_id"].nunique().to_dict()

    def ev(self, key: str) -> str:
        return f"read_parquet('{self.wh}/events/key={key}/*.parquet')"

    def ep(self) -> str:
        return f"read_parquet('{self.wh}/episode_players.parquet')"

    def per_game(self, df, value_col: str) -> dict[str, float]:
        """policy -> value_col summed and divided by that policy's role-game count."""
        return {r.policy_name: (getattr(r, value_col) or 0) / self.games.get(r.policy_name, 1)
                for r in df.itertuples()}


def dim_pair_interval(w: Warehouse, key: str) -> "object":
    """Intervals where the actor was near / isolated-with an OPPOSITE-role player.

    `proximity_interval` and `isolation_interval` are GLOBAL rows (slot=-1) carrying
    `player_a`/`player_b`; we keep those where one endpoint is the actor and the other is an
    opposite-role player in that episode.
    """
    return w.con.execute(f"""
      WITH p AS (
        SELECT episode_id,
               json_extract(value,'$.player_a')::INT a, json_extract(value,'$.player_b')::INT b,
               json_extract(value,'$.duration_ticks')::DOUBLE dur
        FROM {w.ev(key)} WHERE json_extract_string(value,'$.phase')='Playing')
      SELECT act.policy_name, COUNT(*) n, SUM(p.dur) ticks
      FROM p JOIN actors act ON p.episode_id=act.episode_id AND (p.a=act.slot OR p.b=act.slot)
      JOIN {w.ep()} o ON o.episode_id=p.episode_id
           AND o.slot = CASE WHEN p.a=act.slot THEN p.b ELSE p.a END AND o.role='{w.opp}'
      GROUP BY 1""").df()


def dim_attributed(w: Warehouse, key: str, slot_field: str):
    """Per-game count + ticks of an interval attributed to the actor (follow/chase)."""
    return w.con.execute(f"""
      SELECT act.policy_name, COUNT(*) n, SUM(json_extract(t.value,'$.duration_ticks')::DOUBLE) ticks
      FROM {w.ev(key)} t JOIN actors act
        ON t.episode_id=act.episode_id AND json_extract(t.value,'${slot_field}')::INT=act.slot
      WHERE json_extract_string(t.value,'$.phase')='Playing' GROUP BY 1""").df()


def dim_room_entries(w: Warehouse):
    return w.con.execute(f"""
      SELECT act.policy_name, COUNT(*) n FROM {w.ev('entered_room')} t
      JOIN actors act ON t.episode_id=act.episode_id AND t.slot=act.slot
      WHERE json_extract_string(t.value,'$.phase')='Playing' GROUP BY 1""").df()


def dim_room_presence(w: Warehouse):
    return w.con.execute(f"""
      SELECT act.policy_name, json_extract_string(t.value,'$.room') room, COUNT(*) c
      FROM {w.ev('player_state')} t JOIN actors act ON t.episode_id=act.episode_id AND t.slot=act.slot
      WHERE json_extract_string(t.value,'$.phase')='Playing'
        AND json_extract_string(t.value,'$.alive')='true' GROUP BY 1,2""").df()


def dim_ejection(w: Warehouse):
    """Actor ended the game dead (== voted out / killed), per role-game."""
    return w.con.execute(f"""
      WITH last AS (
        SELECT act.policy_name, t.episode_id,
               ARG_MAX(json_extract_string(t.value,'$.alive'), t.ts) AS alive_end
        FROM {w.ev('player_state')} t JOIN actors act ON t.episode_id=act.episode_id AND t.slot=act.slot
        GROUP BY 1,2)
      SELECT policy_name, COUNT(*) g, SUM(CASE WHEN alive_end='false' THEN 1 ELSE 0 END) ended_dead
      FROM last GROUP BY 1""").df()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("warehouse", type=Path, help="A built event-warehouse directory.")
    ap.add_argument("--policies", nargs="+", help="Policy names to compare (default: all in the warehouse).")
    ap.add_argument("--role", choices=["imposter", "crew"], default="imposter", help="Which role's behaviour (default imposter).")
    args = ap.parse_args(argv)

    w = Warehouse(args.warehouse, args.policies, args.role)
    pols = sorted(w.games, key=lambda p: -w.games[p])
    if not pols:
        raise SystemExit(f"no {args.role} games for {args.policies or 'any policy'} in {args.warehouse}")
    print(f"warehouse: {args.warehouse}  ·  role: {args.role}")
    print("  ".join(f"{p}={w.games[p]}g" for p in pols) + "  (ops-filtered)\n")

    near = w.per_game(dim_pair_interval(w, "proximity_interval"), "ticks")
    iso_n = {r.policy_name: r.n / w.games.get(r.policy_name, 1) for r in dim_pair_interval(w, "isolation_interval").itertuples()}
    fol = dim_attributed(w, "following_interval", ".follower"); cha = dim_attributed(w, "chase_interval", ".chaser")
    foln = {r.policy_name: r.n / w.games.get(r.policy_name, 1) for r in fol.itertuples()}
    chan = {r.policy_name: r.n / w.games.get(r.policy_name, 1) for r in cha.itertuples()}
    entries = {r.policy_name: r.n / w.games.get(r.policy_name, 1) for r in dim_room_entries(w).itertuples()}
    ej = {r.policy_name: (r.ended_dead, r.g) for r in dim_ejection(w).itertuples()}
    rp = dim_room_presence(w)

    print(f"{'policy':18} near-opp  isol/g  follow/g  chase/g  rooms/g  ended-dead")
    for p in pols:
        ed, g = ej.get(p, (0, w.games[p]))
        print(f"  {p:16} {near.get(p,0):7.0f}t {iso_n.get(p,0):6.2f} {foln.get(p,0):8.2f} "
              f"{chan.get(p,0):7.2f} {entries.get(p,0):7.1f}  {100*ed/max(g,1):4.0f}%")
    print(f"\n(near-opp = {w.opp}-proximity ticks/game · isol = isolated with one {w.opp} · "
          f"follow/chase attributed to the actor)\n")

    print(f"=== top rooms by alive-Playing time ({args.role}) ===")
    for p in pols:
        d = rp[rp.policy_name == p]; tot = d.c.sum()
        if not tot:
            continue
        top = d.sort_values("c", ascending=False).head(4)
        print(f"  {p:16} " + "  ".join(f"{x.room}:{100*x.c/tot:.0f}%" for x in top.itertuples()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
