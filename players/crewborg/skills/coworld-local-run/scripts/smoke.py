#!/usr/bin/env python3
"""Run your own built policy in a local Coworld episode — a Gate-1 smoke test.

"Did my change take, and does the player connect → play → exit cleanly?" — *not* a
competitive test (you can't run other users' policies locally). This orchestrates the
three `coworld` CLI steps and guards the two footguns a fresh agent hits:

  1. the image must be **linux/amd64** (the runner hard-fails otherwise), and
  2. your policy image must be the **positional argument** to `run-episode` — omit it
     and the runner silently runs the manifest's *reference* player instead, so your
     change isn't under test even though the run "passes".

Flow: ensure the game manifest+image are local (`coworld download` if needed) → run an
episode with YOUR image in every slot → verify exit 0 + results.json + replay, and
print the command to watch the replay.

Usage (auth via `softmax login` for first-time `download`; run inside `uv run`):

    uv run python smoke.py --coworld <cow_id|name> --image crewborg:dev
    uv run python smoke.py --coworld cow_... --image my:dev --run python --run -m --run my_player
    uv run python smoke.py --coworld cow_... --image my:dev --timeout 180 --out /tmp/smoke

Gate-1 passes when this exits 0: the CLI exited cleanly (so no game/player container
crashed), `results.json` validated, and a `replay` was written. NOTE: the default run
uses the package's **certification** config, which is deliberately degenerate — a
score of 0 there is NOT a failure; this checks *liveness/correctness*, not gameplay.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("  $ " + " ".join(cmd), file=sys.stderr, flush=True)
    return subprocess.run(cmd, **kw)


def coworld(*args: str, **kw) -> subprocess.CompletedProcess:
    # `python -m coworld` is robust regardless of console-script PATH.
    return run([sys.executable, "-m", "coworld", *args], **kw)


def assert_amd64(image: str) -> None:
    """The runner rejects non-amd64 images; catch it here with the fix."""
    p = run(
        ["docker", "image", "inspect", image, "--format", "{{.Os}}/{{.Architecture}}"],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        sys.exit(f"Image {image!r} is not a local Docker image (docker inspect failed). "
                 f"Build it first, e.g. `docker build --platform linux/amd64 -t {image} .`")
    plat = p.stdout.strip()
    if plat != "linux/amd64":
        sys.exit(f"Image {image!r} is {plat}, but Coworld requires linux/amd64. "
                 f"Rebuild with: docker build --platform linux/amd64 -t {image} .")
    print(f"  image {image} is linux/amd64 ✓", file=sys.stderr)


def ensure_manifest(coworld_ref: str) -> Path:
    """Download the game (idempotent) and return the local manifest path."""
    # download is a no-op if already cached; resolves a name to its cow_id dir.
    p = coworld("download", coworld_ref, capture_output=True, text=True)
    sys.stderr.write(p.stdout + p.stderr)
    if p.returncode != 0:
        sys.exit(f"`coworld download {coworld_ref}` failed (auth/network/docker?). See output above.")
    # Parse the manifest path `download` prints (`Manifest: <path>`) — it names the
    # game we asked for, fresh or cached. Do NOT glob coworld/ by newest mtime: the
    # download dir is shared across labs, so a cached no-op keeps its old mtime and
    # the glob would pick whichever game some other lab downloaded most recently
    # (this silently ran Crewrift smokes against a Cue-n-Woo game for a week).
    for line in (p.stdout + p.stderr).splitlines():
        if line.startswith("Manifest:"):
            manifest = Path(line.split(":", 1)[1].strip())
            if manifest.is_file():
                return manifest
            sys.exit(f"`coworld download` reported manifest {manifest} but it does not exist.")
    sys.exit("could not find a `Manifest:` line in `coworld download` output; "
             "pass --manifest <path> explicitly.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local Gate-1 smoke test for your built policy.")
    ap.add_argument("--coworld", required=True, help="Coworld id (cow_...) or name to run the episode in.")
    ap.add_argument("--image", required=True, help="Your built policy image tag (local, linux/amd64).")
    ap.add_argument("--run", action="append", default=[], help="One argv token for the image (repeatable).")
    ap.add_argument("--timeout", type=float, default=120.0, help="run-episode --timeout-seconds.")
    ap.add_argument("--manifest", help="Use this manifest path instead of downloading the coworld.")
    ap.add_argument("--out", default="/tmp/coworld_smoke", help="Output dir for episode artifacts.")
    args = ap.parse_args(argv)

    assert_amd64(args.image)
    manifest = Path(args.manifest) if args.manifest else ensure_manifest(args.coworld)
    print(f"  manifest: {manifest}", file=sys.stderr)

    out = Path(args.out)
    cmd = ["run-episode", str(manifest), args.image, "--output-dir", str(out),
           "--timeout-seconds", str(args.timeout)]
    for tok in args.run:
        cmd += ["--run", tok]
    proc = coworld(*cmd)

    # Verdict
    results = out / "results.json"
    # run-episode names the replay file `replay` (no extension).
    replay = out / "replay"
    logs = sorted((out / "logs").glob("policy_agent_*.log")) if (out / "logs").exists() else []
    ok = proc.returncode == 0 and results.exists() and replay.exists()
    print("\n=== Gate-1 verdict ===", file=sys.stderr)
    print(f"  exit code:   {proc.returncode}  ({'clean' if proc.returncode == 0 else 'CRASH — a game/player container exited non-zero'})")
    print(f"  results.json:{'present' if results.exists() else 'MISSING'}")
    print(f"  replay:      {'present' if replay.exists() else 'MISSING'}  ({replay})")
    print(f"  player logs: {len(logs)} ({', '.join(p.name for p in logs)})")
    if results.exists():
        try:
            scores = json.loads(results.read_text()).get("scores")
            print(f"  scores:      {scores}   (degenerate cert fixture → a 0 is NOT failure)")
        except Exception:
            pass
    if ok:
        print(f"\nPASS. Watch it: uv run coworld replay {manifest} {replay}")
    else:
        print("\nFAIL — the player did not connect/play/exit cleanly. Read "
              f"{out}/logs/policy_agent_0.log and {out}/logs/game.stderr.log.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
