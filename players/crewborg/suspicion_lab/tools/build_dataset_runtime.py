#!/usr/bin/env python3
"""Build the suspicion dataset from crewborg's OWN runtime-traced features.

The runtime-feature counterpart to `build_dataset.py` — the train->serve-gap rework
(design suspicion-learning.md §7.2). Rather than reconstructing features offline from
expanded replays, this reads the *exact* feature vectors crewborg computed at serve
time — `domain.suspicion_snapshot.ranking[].features`, emitted per meeting under
`CREWBORG_TRACE_SUSPICION_FEATURES=1` in the policy artifact — and labels each
(crewborg-observer, suspect, meeting) row with the suspect's ground-truth role from the
expanded replay's `player_manifest`. It emits the SAME parquet schema as
`build_dataset.py`, so `fit.py --features runtime` consumes it unchanged.

Inputs:
  --expanded   dir of `<ep>.jsonl.gz` from expand_corpus.py (for labels + meeting ticks)
  --artifacts  fetch_artifacts layout (episode dirs w/ episode.json + artifacts/*.zip)
Episodes are matched between the two by their `ereq_<id>` key.

    uv run python suspicion_lab/tools/build_dataset_runtime.py \
        --expanded /tmp/v76_expanded --artifacts /tmp/v76_arts \
        --policy crewborg --version 76 --out /tmp/runtime_dataset.parquet
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURE_NAMES  # noqa: E402
from replay_parse import parse_game  # noqa: E402

EREQ = re.compile(r"ereq_[0-9a-f]+")
# crewborg's traced snapshot fires at meeting start; require the matched meeting's
# call_tick to be within this many ticks of the snapshot (else the snapshot doesn't
# correspond to a real meeting we parsed — dropped and counted).
MATCH_TOL_TICKS = 400


def ereq_key(name: str) -> str | None:
    m = EREQ.search(name)
    return m.group(0) if m else None


def iter_snapshots(zpath: Path):
    """Yield (tick, data) for each domain.suspicion_snapshot in a policy artifact."""
    with zipfile.ZipFile(zpath) as zf:
        if "telemetry.jsonl" not in zf.namelist():
            return
        with zf.open("telemetry.jsonl") as f:
            for line in io.TextIOWrapper(f):
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (e.get("event") or e.get("name")) == "domain.suspicion_snapshot":
                    yield e.get("tick"), (e.get("data") or {})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--expanded", type=Path, required=True)
    ap.add_argument("--artifacts", type=Path, required=True)
    ap.add_argument("--policy", default="crewborg")
    ap.add_argument("--version", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    art_by_ereq: dict[str, Path] = {}
    for d in sorted(args.artifacts.glob("*/")):
        k = ereq_key(d.name)
        if k:
            art_by_ereq[k] = d

    rows: list[dict] = []
    st: collections.Counter = collections.Counter()
    match_dists: list[int] = []

    for exp in sorted(args.expanded.glob("*.jsonl.gz")):
        k = ereq_key(exp.name)
        if not k or k not in art_by_ereq:
            st["no_artifact_dir"] += 1
            continue
        artdir = art_by_ereq[k]
        try:
            ep = json.loads((artdir / "episode.json").read_text())
        except OSError:
            st["no_episode_json"] += 1
            continue
        cb_slot = next(
            (p.get("position") for p in (ep.get("participants") or [])
             if p.get("policy_name") == args.policy and p.get("version") == args.version),
            None,
        )
        if cb_slot is None:
            st["no_subject_seat"] += 1
            continue
        zpath = artdir / "artifacts" / f"policy_artifact_{cb_slot}.zip"
        if not zpath.exists():
            st["no_artifact_zip"] += 1
            continue
        try:
            game = parse_game(exp)
        except Exception as exc:  # noqa: BLE001 - skip corrupt games
            st["parse_fail"] += 1
            continue
        if not game.players or not game.meetings or cb_slot not in game.players:
            st["no_meetings_or_slot"] += 1
            continue
        # Crew-POV only: this is the crew suspicion model.
        if game.players[cb_slot].role != "crew":
            st["subject_imposter"] += 1
            continue

        color2slot = {p.color: s for s, p in game.players.items()}
        meetings = [(mi, m.call_tick) for mi, m in enumerate(game.meetings)]

        for tick, data in iter_snapshots(zpath):
            ranking = data.get("ranking") or []
            if not ranking or tick is None:
                continue
            mi, ct = min(meetings, key=lambda mc: abs(tick - mc[1]))
            dist = abs(tick - ct)
            if dist > MATCH_TOL_TICKS:
                st["snapshot_no_meeting"] += 1
                continue
            match_dists.append(dist)
            for entry in ranking:
                feats = entry.get("features")
                if not isinstance(feats, dict):
                    continue
                sslot = color2slot.get(entry.get("color"))
                if sslot is None or sslot == cb_slot:
                    st["suspect_unresolved"] += (sslot is None)
                    continue
                sus = game.players[sslot]
                row = {
                    "episode": game.episode,
                    "meeting_idx": mi,
                    "decision_tick": ct,
                    "observer_slot": cb_slot,
                    "observer_name": game.players[cb_slot].name,
                    "suspect_slot": sslot,
                    "suspect_name": sus.name,
                    "label_imposter": int(sus.role == "imposter"),
                    "snapshot_tick": tick,
                    "runtime_p": entry.get("p"),
                }
                # All FEATURE_NAMES columns (fit.py may reference either set); the traced
                # dict carries exactly the RUNTIME_FEATURES, offline-only names stay 0.
                for fn in FEATURE_NAMES:
                    row[fn] = float(feats.get(fn, 0.0))
                rows.append(row)
            st["snapshots"] += 1
        st["episodes_used"] += 1

    df = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    log(f"Wrote {len(df)} rows from {st['episodes_used']} crew-POV episodes "
        f"({st['snapshots']} meeting-snapshots) -> {args.out}")
    log(f"stats: {dict(st)}")
    if match_dists:
        md = sorted(match_dists)
        log(f"snapshot->meeting tick gap: median {md[len(md)//2]}, max {md[-1]} (tol {MATCH_TOL_TICKS})")
    if len(df):
        log(f"base rate P(imposter) = {df.label_imposter.mean():.3f} "
            f"({int(df.label_imposter.sum())} imp / {int((1 - df.label_imposter).sum())} crew rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
