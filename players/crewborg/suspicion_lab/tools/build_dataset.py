#!/usr/bin/env python3
"""Build the suspicion training dataset from expanded replays.

Stage C of the suspicion-learning pipeline (design §5): parses every expanded
episode, extracts per-(observer, suspect, meeting) cumulative feature rows with
ground-truth labels, and writes one parquet. Also prints a coverage + per-cue
sanity report (the first honest look at which hand weights are wrong).

    uv run python suspicion_lab/tools/build_dataset.py
    uv run python suspicion_lab/tools/build_dataset.py --limit 100 --out /tmp/ds.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURE_NAMES, extract_rows  # noqa: E402
from replay_parse import parse_game  # noqa: E402

LAB_ROOT = Path(__file__).resolve().parents[2]
SUSPICION_LAB = LAB_ROOT / "suspicion_lab"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the suspicion dataset.")
    parser.add_argument("--expanded", type=Path, default=SUSPICION_LAB / "expanded")
    parser.add_argument("--out", type=Path, default=SUSPICION_LAB / "dataset" / "dataset.parquet")
    parser.add_argument("--limit", type=int, default=None, help="Parse at most N episodes.")
    args = parser.parse_args(argv)

    paths = sorted(args.expanded.glob("*.jsonl.gz"))
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        sys.exit(f"No expanded episodes in {args.expanded}; run expand_corpus.py first.")

    all_rows: list[dict] = []
    skipped = 0
    for i, path in enumerate(paths):
        try:
            game = parse_game(path)
            if not game.complete:
                skipped += 1
                continue
            all_rows.extend(extract_rows(game))
        except Exception as exc:  # noqa: BLE001 - skip corrupt games, keep building
            log(f"  skip {path.name}: {exc}")
            skipped += 1
        if (i + 1) % 100 == 0:
            log(f"  …{i + 1}/{len(paths)} episodes, {len(all_rows)} rows")

    df = pd.DataFrame(all_rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    log(f"Wrote {len(df)} rows from {len(paths) - skipped} games ({skipped} skipped) -> {args.out}")

    # --- per-cue sanity report: mean per class + a crude lift ---------------------
    if len(df):
        imp = df[df.label_imposter == 1]
        crew = df[df.label_imposter == 0]
        log(f"\nbase rate P(imposter) = {df.label_imposter.mean():.3f}  "
            f"(rows: {len(imp)} imposter / {len(crew)} crew)")
        log(f"{'feature':<32} {'mean|imp':>9} {'mean|crew':>10} {'lift':>6}")
        for name in FEATURE_NAMES:
            mi, mc = imp[name].mean(), crew[name].mean()
            lift = (mi + 1e-9) / (mc + 1e-9)
            log(f"{name:<32} {mi:>9.3f} {mc:>10.3f} {lift:>6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
