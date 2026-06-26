#!/usr/bin/env python3
"""Expand a Crewrift episode replay and correlate player vs opponent outcomes.

Workflow
--------
1. Download episodes with ``fetch_episodes.py`` (includes ``replay.json`` and
   ``episode_request.json`` with the opponent roster + scores).
2. Run this script on one episode directory (or pass ``--crewrift-root`` so it
   can call ``tools/expand_replay.nim`` in the local ``coworld-crewrift`` checkout).

The replay is Crewrift's binary ``.bitreplay`` format (per-tick input masks).
There is no Python decoder — we re-simulate via Nim::

    data = loadReplay(path)
    sim = initSimServer(data.replayGameConfig())
    replay = initReplayPlayer(data)
    while replay.playing:
        replay.stepReplay(sim)

``expand_replay.nim --json`` wraps that loop and emits structured events
(kills, votes, tasks, phase changes). Join crewborg's per-tick trace to the
replay timeline via ``positions.server_tick`` in ``trace.db`` (same counter as
the server's ``tick <N>`` sprite marker).

Usage::

    uv run python players/crewrift/crewborg/scripts/replay_analysis.py \\
        episode_data/20260610_abc12345

    uv run python .../replay_analysis.py episode_data/20260610_abc12345 \\
        --trace-db logs/ereq_.../trace.db --slot 2
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


@dataclass
class Participant:
    slot: int
    policy_name: str
    policy_version_id: str | None
    score: float | None = None
    display_name: str | None = None


@dataclass
class SlotStats:
    slot: int
    policy_name: str
    policy_version_id: str | None
    score: float | None
    kills_made: int = 0
    kills_received: int = 0
    tasks_completed: int = 0
    votes_cast: int = 0
    vote_targets: Counter[int] = field(default_factory=Counter)
    vote_received: int = 0
    chat_messages: int = 0


def find_crewrift_root(explicit: Path | None) -> Path:
    if explicit is not None:
        if not (explicit / "tools" / "expand_replay.nim").is_file():
            raise SystemExit(f"Not a coworld-crewrift checkout: {explicit}")
        return explicit
    env = os.environ.get("CREWRIFT_ROOT")
    if env:
        root = Path(env)
        if (root / "tools" / "expand_replay.nim").is_file():
            return root
    candidates = [
        Path("/Users/aaln/experiments/softmax/coworld-crewrift"),
        Path.home() / "coding" / "games" / "coworld-crewrift",
    ]
    for candidate in candidates:
        if (candidate / "tools" / "expand_replay.nim").is_file():
            return candidate
    raise SystemExit(
        "Set CREWRIFT_ROOT to your coworld-crewrift checkout "
        "(needs tools/expand_replay.nim and Nim 2.2.10)."
    )


def expand_replay_json(replay_path: Path, crewrift_root: Path) -> dict[str, Any]:
    nim_bin = os.environ.get("NIM_BIN", "nim")
    cmd = [
        nim_bin,
        "r",
        "tools/expand_replay.nim",
        "--json",
        str(replay_path.resolve()),
    ]
    log(f"Running: {' '.join(cmd)} (cwd={crewrift_root})")
    proc = subprocess.run(
        cmd,
        cwd=crewrift_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout)[-2000:]
        raise SystemExit(f"expand_replay failed ({proc.returncode}):\n{tail}")
    # Nim hints may precede JSON on stderr; JSON is the last line starting with '{'
    for line in reversed((proc.stdout + proc.stderr).splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise SystemExit("expand_replay produced no JSON output")


def load_participants(episode_dir: Path) -> list[Participant]:
    req_path = episode_dir / "episode_request.json"
    if not req_path.is_file():
        raise SystemExit(f"Missing {req_path}")
    data = json.loads(req_path.read_text())
    participants: list[Participant] = []
    for row in data.get("participants") or []:
        slot = row.get("position")
        if slot is None:
            continue
        participants.append(
            Participant(
                slot=int(slot),
                policy_name=str(row.get("policy_name") or row.get("name") or "?"),
                policy_version_id=row.get("policy_version_id"),
                score=_as_float(row.get("score")),
                display_name=row.get("name"),
            )
        )
    participants.sort(key=lambda p: p.slot)
    return participants


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def aggregate_slot_stats(
    participants: list[Participant],
    events: list[dict[str, Any]],
) -> dict[int, SlotStats]:
    by_slot = {
        p.slot: SlotStats(
            slot=p.slot,
            policy_name=p.policy_name,
            policy_version_id=p.policy_version_id,
            score=p.score,
        )
        for p in participants
    }
    for event in events:
        slot = event.get("player")
        if not isinstance(slot, int) or slot < 0:
            continue
        stats = by_slot.get(slot)
        if stats is None:
            continue
        key = event.get("key")
        value = event.get("value") or {}
        if key == "kill":
            stats.kills_made += 1
            victim = value.get("victim_slot")
            if isinstance(victim, int) and victim in by_slot:
                by_slot[victim].kills_received += 1
        elif key == "completed_task":
            stats.tasks_completed += 1
        elif key == "vote_cast":
            stats.votes_cast += 1
            target = value.get("target_slot")
            if isinstance(target, int):
                stats.vote_targets[target] += 1
                if target in by_slot:
                    by_slot[target].vote_received += 1
        elif key == "chat":
            stats.chat_messages += 1
    return by_slot


def opponent_matrix(
    participants: list[Participant],
    slot_stats: dict[int, SlotStats],
    focus_slot: int | None,
) -> list[dict[str, Any]]:
    """Pairwise summary: for each crewborg slot, who else was in the game."""
    rows: list[dict[str, Any]] = []
    focus_policies = {p.slot for p in participants if p.policy_name == "crewborg"}
    if focus_slot is not None:
        focus_policies = {focus_slot}

    for slot in sorted(focus_policies):
        me = slot_stats.get(slot)
        if me is None:
            continue
        opponents = []
        for other in participants:
            if other.slot == slot:
                continue
            ost = slot_stats.get(other.slot)
            opponents.append(
                {
                    "slot": other.slot,
                    "policy_name": other.policy_name,
                    "policy_version_id": other.policy_version_id,
                    "score": other.score,
                    "kills_made": ost.kills_made if ost else 0,
                    "tasks_completed": ost.tasks_completed if ost else 0,
                    "votes_against_me": me.vote_targets.get(other.slot, 0),
                    "my_votes_against": ost.vote_received if ost else 0,
                }
            )
        rows.append(
            {
                "slot": slot,
                "policy_name": me.policy_name,
                "policy_version_id": me.policy_version_id,
                "score": me.score,
                "kills_made": me.kills_made,
                "tasks_completed": me.tasks_completed,
                "votes_cast": me.votes_cast,
                "opponents": opponents,
            }
        )
    return rows


def load_trace_server_ticks(trace_db: Path, slot: int | None) -> dict[int, dict[str, Any]]:
    """Load per-server-tick rows from crewborg trace.db (if positions table exists)."""
    if not trace_db.is_file():
        return {}
    conn = sqlite3.connect(trace_db)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "positions" not in tables:
            log(f"trace.db has no positions table (older artifact?): {trace_db}")
            return {}
        rows = conn.execute(
            "SELECT server_tick, tick, mode, intent_kind, phase "
            "FROM positions WHERE server_tick IS NOT NULL ORDER BY server_tick"
        ).fetchall()
    finally:
        conn.close()
    return {
        int(server_tick): {
            "agent_tick": tick,
            "mode": mode,
            "intent_kind": intent_kind,
            "phase": phase,
        }
        for server_tick, tick, mode, intent_kind, phase in rows
    }


def correlate_trace_to_events(
    events: list[dict[str, Any]],
    trace_by_server_tick: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join replay events to crewborg trace rows on server tick (= event ts)."""
    joins: list[dict[str, Any]] = []
    for event in events:
        ts = event.get("ts")
        if not isinstance(ts, int):
            continue
        trace = trace_by_server_tick.get(ts)
        if trace is None:
            continue
        joins.append(
            {
                "server_tick": ts,
                "event_key": event.get("key"),
                "agent_tick": trace["agent_tick"],
                "mode": trace["mode"],
                "intent_kind": trace["intent_kind"],
                "phase": trace["phase"],
            }
        )
    return joins


def resolve_replay_path(episode_dir: Path) -> Path:
    for name in ("replay.json", "replay.bitreplay"):
        path = episode_dir / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    raise SystemExit(f"No replay file in {episode_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode_dir", type=Path, help="Episode directory from fetch_episodes.py")
    parser.add_argument(
        "--crewrift-root",
        type=Path,
        default=None,
        help="Path to coworld-crewrift checkout (or set CREWRIFT_ROOT)",
    )
    parser.add_argument(
        "--trace-db",
        type=Path,
        default=None,
        help="Optional crewborg trace.db to join on server_tick",
    )
    parser.add_argument(
        "--slot",
        type=int,
        default=None,
        help="Focus correlation on one player slot (default: all crewborg slots)",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="Write JSON report to this path (default: stdout)",
    )
    args = parser.parse_args()

    episode_dir = args.episode_dir.resolve()
    if not episode_dir.is_dir():
        raise SystemExit(f"Not a directory: {episode_dir}")

    crewrift_root = find_crewrift_root(args.crewrift_root)
    replay_path = resolve_replay_path(episode_dir)
    participants = load_participants(episode_dir)
    timeline = expand_replay_json(replay_path, crewrift_root)
    events: list[dict[str, Any]] = timeline.get("events") or []
    slot_stats = aggregate_slot_stats(participants, events)
    matrix = opponent_matrix(participants, slot_stats, args.slot)

    trace_joins: list[dict[str, Any]] = []
    if args.trace_db is not None:
        trace_rows = load_trace_server_ticks(args.trace_db.resolve(), args.slot)
        trace_joins = correlate_trace_to_events(events, trace_rows)

    report = {
        "episode_dir": str(episode_dir),
        "replay_path": str(replay_path),
        "tick_count": timeline.get("tick_count"),
        "hash_failed": timeline.get("hash_failed"),
        "fail_tick": timeline.get("fail_tick"),
        "participants": [
            {
                "slot": p.slot,
                "policy_name": p.policy_name,
                "policy_version_id": p.policy_version_id,
                "score": p.score,
            }
            for p in participants
        ],
        "slot_stats": {
            str(slot): {
                "policy_name": s.policy_name,
                "policy_version_id": s.policy_version_id,
                "score": s.score,
                "kills_made": s.kills_made,
                "kills_received": s.kills_received,
                "tasks_completed": s.tasks_completed,
                "votes_cast": s.votes_cast,
                "vote_targets": dict(s.vote_targets),
                "chat_messages": s.chat_messages,
            }
            for slot, s in sorted(slot_stats.items())
        },
        "crewborg_opponent_correlation": matrix,
        "trace_joins": trace_joins[:200],  # cap for readability
        "trace_join_count": len(trace_joins),
    }

    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n")
        log(f"Wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
