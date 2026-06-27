#!/usr/bin/env python3
"""Bake crewborg's nav graph + occupancy substrate offline (run when the map changes).

There is one static map in Crewrift (croatoan). crewborg's nav graph and occupancy
substrate are pure functions of that map's walkability mask, but building them is a
heavy pure-Python pass — ~14s on the first tick under the hosted 250m-CPU cap, which
freezes the agent at spawn (see crewborg/navbake.py / WORKING_CONTEXT). This tool
bakes them ONCE into the vendored asset `crewborg/map/croatoan_navbake.pkl.gz`,
which crewborg then loads instead of computing (validating the streamed mask still
matches; else it falls back to the live build).

Run with the player dir on PYTHONPATH (so `import crewborg` works), e.g.
`PYTHONPATH=players/crewborg uv run python players/crewborg/tools/build/nav_bake.py …`.

Two steps, re-run only when the league redeploys a changed map:

1. CAPTURE the authoritative walkability mask crewborg actually sees. Run a local
   Gate-1 episode with CREWBORG_CAPTURE_WALKABILITY=1 (the bridge prints one
   bit-packed JSON line to its stderr / policy log), then extract it:

       tools/build/nav_bake.py extract-walkability \
           /tmp/coworld_smoke/logs/policy_agent_0.log -o /tmp/croatoan_walkability.npy

2. BAKE the asset from that mask:

       tools/build/nav_bake.py bake /tmp/croatoan_walkability.npy
       # -> writes crewborg/map/croatoan_navbake.pkl.gz (+ prints per-stage timing)

Then rebuild the image (tools/build/build_player.sh) so the asset ships, and the
first-tick build drops from seconds to a fast load. Run this under the same throttle
as production (`docker run --cpus=0.25 ...`) if you want the timing to reflect hosted.

HOW TO EDIT: the bake logic is crewborg-internal (it imports crewborg.nav / .navbake /
.agent_tracking). If those modules change, update the imports below; the asset path is
derived from this file's location (parents[2] = the player root).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import numpy as np

# crewborg must be importable as the top-level `crewborg` package (see the PYTHONPATH note above).
from crewborg.agent_tracking import build_occupancy_substrate
from crewborg.map import load_croatoan_map
from crewborg import nav as nav_mod
from crewborg.navbake import NAVBAKE_PACKAGE, NAVBAKE_RESOURCE, serialize_navbake


def _navbake_asset_path() -> Path:
    # tools/build/nav_bake.py -> players/crewborg (parents[2]) -> crewborg/map/<asset>
    return Path(__file__).resolve().parents[2] / "crewborg" / "map" / NAVBAKE_RESOURCE


def extract_walkability(log_path: Path, out_path: Path) -> None:
    """Pull the `walkability_capture` line out of a player log into a `.npy` mask."""

    capture: dict | None = None
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line or "walkability_capture" not in line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") == "walkability_capture":
            capture = record
            break
    if capture is None:
        sys.exit(
            f"no 'walkability_capture' line in {log_path}. Re-run the local episode with "
            "CREWBORG_CAPTURE_WALKABILITY=1 set on the crewborg player."
        )
    shape = tuple(capture["shape"])
    packed = np.frombuffer(base64.b64decode(capture["packbits_b64"]), dtype=np.uint8)
    mask = np.unpackbits(packed, count=int(np.prod(shape))).astype(bool).reshape(shape)
    np.save(out_path, mask)
    walkable = int(mask.sum())
    print(f"wrote {out_path}  shape={shape}  walkable_px={walkable} ({100*walkable/mask.size:.1f}%)")


def bake(walkability_path: Path, out_path: Path) -> None:
    """Build the nav graph + occupancy substrate from a captured mask and serialize."""

    walkability = np.load(walkability_path)
    if walkability.dtype != bool:
        walkability = walkability.astype(bool)
    print(f"walkability: shape={walkability.shape} walkable_px={int(walkability.sum())}")

    map_data = load_croatoan_map()

    t0 = time.perf_counter()
    nav = nav_mod.build_nav_graph(walkability, map_data=map_data)
    t1 = time.perf_counter()
    substrate = build_occupancy_substrate(nav, map_data)
    t2 = time.perf_counter()

    print(f"  build_nav_graph:          {t1 - t0:8.2f}s  ({len(nav.node_point)} nodes, "
          f"{sum(len(v) for v in nav.adjacency.values()) // 2} edges)")
    print(f"  build_occupancy_substrate:{t2 - t1:8.2f}s  ({len(substrate.anchors)} anchors, "
          f"{len(substrate.polylines)} polylines)")
    print(f"  TOTAL bake:               {t2 - t0:8.2f}s   (this is the per-run first-tick cost we remove)")
    if nav.unreachable:
        print(f"  WARNING: {len(nav.unreachable)} unreachable destination(s): {', '.join(nav.unreachable)}")

    blob = serialize_navbake(nav, substrate)
    out_path.write_bytes(blob)
    print(f"\nwrote {out_path}  ({len(blob) / 1024:.0f} KiB gzipped)")
    print(f"Asset loads as `{NAVBAKE_PACKAGE}:{NAVBAKE_RESOURCE}`. Rebuild the image to ship it.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("extract-walkability", help="Pull the captured mask out of a player log into a .npy")
    ex.add_argument("log", type=Path, help="policy_agent_N.log from a CREWBORG_CAPTURE_WALKABILITY=1 run")
    ex.add_argument("-o", "--out", type=Path, default=Path("/tmp/croatoan_walkability.npy"))

    bk = sub.add_parser("bake", help="Build + serialize the nav/substrate asset from a captured mask")
    bk.add_argument("walkability", type=Path, help="a .npy walkability mask (from extract-walkability)")
    bk.add_argument("-o", "--out", type=Path, default=None, help="output asset path (default: the vendored asset)")

    args = parser.parse_args(argv)
    if args.command == "extract-walkability":
        extract_walkability(args.log, args.out)
    elif args.command == "bake":
        bake(args.walkability, args.out or _navbake_asset_path())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
