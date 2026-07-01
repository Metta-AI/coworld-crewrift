#!/usr/bin/env python3
"""Streaming eval pipeline: xreq(s) -> artifacts -> event warehouse, overlapped.

The serial flow (monitor the whole xreq -> fetch everything -> build the whole
warehouse) wastes wall clock: episodes finish one by one and each finished
episode's artifacts + extraction are independent of the episodes still running.
This orchestrator overlaps all three stages in ONE background-runnable process:

  - spawns `fetch_artifacts.py --watch` per xreq (episodes stream to disk as
    they complete),
  - periodically folds newly complete episode dirs into the warehouse via the
    INCREMENTAL `crewrift-event-warehouse build` (cached episodes are skipped,
    so each batch only pays for the new ones),
  - exits when every watcher has drained and the final build has caught up.

Crash/Ctrl-C safe: rerun the same command; the watchers resume from disk state
and the incremental build skips everything already in the warehouse.

Usage (run from the repo root; auth from `softmax login`):

    uv run python players/crewborg/skills/crewrift-event-warehouse/scripts/stream_eval.py \\
        --xreq xreq_... [--xreq xreq_...] --out /tmp/wh --expand-replay /tmp/expand-<commit>

`--expand-replay` has the same hard version-coupling requirement as
build_warehouse.py (see the SKILL.md); skew is detected and warned after the
FIRST batch, minutes in, instead of after the whole xreq drains.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from build_warehouse import FETCH, WH_DIR, build_request, find_episode_dirs, summarize


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _pump(prefix: str, stream) -> None:
    """Relay one watcher's stderr through our stderr with an xreq prefix."""
    for line in iter(stream.readline, ""):
        log(f"[{prefix}] {line.rstrip()}")


def spawn_watcher(xreq: str, ep_dir: Path, interval: float) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["uv", "run", "python", str(FETCH), "--xreq", xreq, "--watch",
         "--interval", str(interval), "--out", str(ep_dir)],
        stderr=subprocess.PIPE,
        text=True,
    )
    threading.Thread(target=_pump, args=(xreq[:13], proc.stderr), daemon=True).start()
    return proc


def episode_id_of(ep_dir: Path) -> str:
    meta = json.loads((ep_dir / "episode.json").read_text())
    return str(meta.get("id") or ep_dir.name)


def warehouse_episode_ids(out: Path) -> set[str]:
    """Episode ids the warehouse already holds successfully (status ok)."""
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        return set()
    manifest = json.loads(manifest_path.read_text())
    return {e["episode_id"] for e in manifest.get("episodes", []) if e.get("status") == "ok"}


def run_build(ep_dirs: list[Path], out: Path, expand_replay: Path | None, workers: int | None) -> None:
    req = build_request(ep_dirs, out.parent / (out.name + "_input"))
    env = dict(os.environ)
    if expand_replay:
        env["CREWRIFT_EXPAND_REPLAY"] = str(expand_replay)
    cmd = ["uv", "run", "crewrift-event-warehouse", "build", "--input", str(req), "--out", str(out)]
    if workers:
        cmd += ["--workers", str(workers)]
    subprocess.run(cmd, cwd=WH_DIR, env=env, check=True)


def trace_warning_count(out: Path) -> int:
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text())
    return sum(1 for e in manifest.get("episodes", []) if e.get("trace_warning"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xreq", action="append", required=True, metavar="xreq_…",
                    help="Experience request to stream. Repeatable.")
    ap.add_argument("--out", type=Path, required=True, help="Warehouse output directory.")
    ap.add_argument("--expand-replay", type=Path, required=True,
                    help="Version-matched expand_replay binary (CREWRIFT_EXPAND_REPLAY).")
    ap.add_argument("--batch-n", type=int, default=10,
                    help="Build when this many new episodes have landed (default 10).")
    ap.add_argument("--batch-secs", type=float, default=120.0,
                    help="…or when this long has passed since the last build with >=1 new episode.")
    ap.add_argument("--interval", type=float, default=15.0, help="Poll cadence, seconds.")
    ap.add_argument("--workers", type=int, help="Warehouse build workers (default: CPU count).")
    args = ap.parse_args()

    ep_dir = args.out.parent / (args.out.name + "_episodes")
    ep_dir.mkdir(parents=True, exist_ok=True)
    procs = {x: spawn_watcher(x, ep_dir, args.interval) for x in args.xreq}
    log(f"[stream] watching {len(procs)} xreq(s) -> episodes in {ep_dir}, warehouse in {args.out}")

    last_build = time.monotonic()
    first_build_done = False
    while True:
        alive = any(p.poll() is None for p in procs.values())
        in_warehouse = warehouse_episode_ids(args.out)
        ready = find_episode_dirs(ep_dir)
        new = [d for d in ready if episode_id_of(d) not in in_warehouse]
        overdue = (time.monotonic() - last_build) >= args.batch_secs
        if new and (len(new) >= args.batch_n or overdue or not alive):
            log(f"[stream] building warehouse: +{len(new)} new episodes ({len(ready)} fetched total)")
            try:
                run_build(ready, args.out, args.expand_replay, args.workers)
                last_build = time.monotonic()
                if not first_build_done:
                    first_build_done = True
                    warned = trace_warning_count(args.out)
                    if warned:
                        log(f"[stream] ⚠️  {warned} trace_warning episode(s) IN THE FIRST BATCH — "
                            f"the --expand-replay binary is likely version-skewed vs the arena. "
                            f"Kill this run, rebuild the binary from the arena's deployed crewrift "
                            f"commit (see the SKILL.md), and rerun — it will resume from disk.")
            except subprocess.CalledProcessError as exc:
                log(f"[stream] ! warehouse build failed ({exc}); retrying next tick")
        if not alive:
            in_warehouse = warehouse_episode_ids(args.out)
            remaining = [d for d in find_episode_dirs(ep_dir) if episode_id_of(d) not in in_warehouse]
            if not remaining:
                break
        time.sleep(args.interval)

    watcher_rcs = {x: p.returncode for x, p in procs.items()}
    if (args.out / "manifest.json").exists():
        summarize(args.out)
        manifest = json.loads((args.out / "manifest.json").read_text())
        log(f"[stream] done: {manifest.get('episodes_ok', 0)} episodes in warehouse, "
            f"{manifest.get('episodes_failed', 0)} failed extraction; "
            f"{len(find_episode_dirs(ep_dir))} fetched; watcher exits: {watcher_rcs}")
    else:
        log(f"[stream] done with EMPTY warehouse (no complete episodes fetched); "
            f"watcher exits: {watcher_rcs}")
    return 1 if any(rc for rc in watcher_rcs.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
