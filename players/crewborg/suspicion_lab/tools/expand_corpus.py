#!/usr/bin/env python3
"""Expand corpus replays into cached JSONL event streams.

Stage B of the suspicion-learning pipeline (../README.md §4).
Runs the version-matched `expand_replay` binary (`--format jsonl --snapshot-every N`)
over every corpus episode and writes `expanded/<episode_dir>.jsonl.gz`. Idempotent:
existing outputs are skipped. Hash-fails are recorded in `expanded/_manifest.json`
(the signal a round was recorded by a different game version — build that version's
binary and re-run with --ref).

    uv run python suspicion_lab/tools/expand_corpus.py            # default ref 42fed21
    uv run python suspicion_lab/tools/expand_corpus.py --ref <sha> --workers 8
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# suspicion_lab/tools/ -> players/crewborg (the player root)
PLAYER_ROOT = Path(__file__).resolve().parents[2]
SUSPICION_LAB = PLAYER_ROOT / "suspicion_lab"
DEFAULT_REF = "42fed21"   # first ref with JSONL + visibility output (coworld-crewrift #57)
DEFAULT_SNAPSHOT_EVERY = 24


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def expander_path(ref: str) -> Path:
    path = PLAYER_ROOT / "tools" / "bin" / f"expand_replay-{ref}"  # built by tools/build_expand_replay.sh
    if not path.exists():
        sys.exit(
            f"No expander binary for ref {ref} at {path}.\n"
            f"Build it: tools/build_expand_replay.sh --ref {ref}"
        )
    return path


def expand_one(binary: Path, replay: Path, out_path: Path, snapshot_every: int) -> str:
    """Expand one replay; returns 'ok' | 'hash_failed' | 'error'."""
    proc = subprocess.run(
        [str(binary), "--format", "jsonl", f"--snapshot-every={snapshot_every}", str(replay)],
        capture_output=True,
        text=True,
    )
    # On hash failure the expander still prints rows up to the fail tick, exits 1,
    # and the last rows are trace_warning/trace_complete{complete:false}.
    out = proc.stdout
    if not out.strip():
        return "error"
    status = "ok" if proc.returncode == 0 else "hash_failed"
    tmp = out_path.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as fh:
        fh.write(out)
    tmp.rename(out_path)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expand corpus replays to JSONL.")
    parser.add_argument("--corpus", type=Path, default=SUSPICION_LAB / "corpus")
    parser.add_argument("--out", type=Path, default=SUSPICION_LAB / "expanded")
    parser.add_argument("--ref", default=DEFAULT_REF, help="Game ref of the expander binary to use.")
    parser.add_argument("--snapshot-every", type=int, default=DEFAULT_SNAPSHOT_EVERY)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=None, help="Expand at most N new episodes.")
    args = parser.parse_args(argv)

    binary = expander_path(args.ref)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "_manifest.json"
    manifest: dict = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    todo: list[tuple[str, Path, Path]] = []
    for ep_dir in sorted(args.corpus.iterdir()):
        replay = ep_dir / "replay.json"
        if not ep_dir.is_dir() or not replay.exists():
            continue
        out_path = args.out / f"{ep_dir.name}.jsonl.gz"
        prior = manifest.get(ep_dir.name)
        if out_path.exists() and prior and prior.get("status") == "ok":
            continue
        # hash_failed episodes are retried only when run with a different ref
        if prior and prior.get("status") == "hash_failed" and prior.get("ref") == args.ref:
            continue
        todo.append((ep_dir.name, replay, out_path))
    if args.limit is not None:
        todo = todo[: args.limit]
    log(f"{len(todo)} episodes to expand (ref {args.ref}, workers {args.workers}).")

    counts = {"ok": 0, "hash_failed": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(expand_one, binary, replay, out_path, args.snapshot_every): name
            for name, replay, out_path in todo
        }
        done = 0
        for future in as_completed(futures):
            name = futures[future]
            try:
                status = future.result()
            except Exception as exc:  # noqa: BLE001 - record and continue
                log(f"  {name}: {exc}")
                status = "error"
            counts[status] += 1
            manifest[name] = {"status": status, "ref": args.ref, "snapshot_every": args.snapshot_every}
            done += 1
            if done % 50 == 0:
                manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
                log(f"  …{done}/{len(todo)} ({counts})")
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    log(f"Done: {counts}. Manifest: {manifest_path}")
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
