#!/usr/bin/env python3
"""Decision-level evaluation: replay held-out meetings through a vote policy.

Stage D's shipping gate (design §6): a model ships only if its vote policy beats
**always-skip** on net parity cost. Uses the out-of-fold posteriors written by
fit.py, so every decision is evaluated on a game the model never trained on.

For each (meeting, observer) the policy picks: vote the top suspect if
P >= threshold AND P - runner_up >= margin, else skip. Each cast vote is scored
by the suspect's true role: an imposter-hit is parity-positive, a crew-hit is a
parity gift (the league evidence: crew ejections decide losses). Reported per
policy point: votes/meeting, hit rate, and net = (imposter hits - crew hits) per
100 decisions — always-skip is 0 by definition.

    uv run python suspicion_lab/tools/eval.py --model models/<tag>
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

LAB_ROOT = Path(__file__).resolve().parents[2]
SUSPICION_LAB = LAB_ROOT / "suspicion_lab"

POLICY_GRID = [
    # (threshold, margin)
    (0.5, 0.2),    # the CURRENT hand model's clear-leader rule, on fitted P
    (0.6, 0.2),
    (0.7, 0.2),
    (0.8, 0.0),    # the current VOTE_PROBABILITY bar
    (0.8, 0.2),
    (0.9, 0.0),
    (0.95, 0.0),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate vote policies on out-of-fold posteriors.")
    parser.add_argument("--model", type=Path, required=True, help="models/<tag> dir from fit.py")
    args = parser.parse_args(argv)

    oof = pd.read_parquet(args.model / "oof_predictions.parquet")
    decisions = oof.groupby(["episode", "meeting_idx", "observer_slot"])
    n_decisions = decisions.ngroups
    print(f"{n_decisions} (meeting, observer) decisions across {oof.episode.nunique()} games\n")

    print(f"{'policy':<22} {'votes':>6} {'votes/dec':>10} {'imp hits':>9} {'crew hits':>10} "
          f"{'hit rate':>9} {'net/100':>8}")
    for threshold, margin in POLICY_GRID:
        votes = imp_hits = crew_hits = 0
        for _, group in decisions:
            ranked = group.sort_values("p_imposter", ascending=False)
            top = ranked.iloc[0]
            runner_p = ranked.iloc[1].p_imposter if len(ranked) > 1 else 0.0
            if top.p_imposter >= threshold and (top.p_imposter - runner_p) >= margin:
                votes += 1
                if top.label_imposter:
                    imp_hits += 1
                else:
                    crew_hits += 1
        net = 100 * (imp_hits - crew_hits) / max(n_decisions, 1)
        hit_rate = imp_hits / votes if votes else float("nan")
        print(f"P>={threshold:<4} lead>={margin:<6} {votes:>6} {votes / n_decisions:>10.3f} "
              f"{imp_hits:>9} {crew_hits:>10} {hit_rate:>9.2f} {net:>+8.2f}")
    print("\nalways-skip baseline: net/100 = 0.00 by definition. Ship a policy only if its")
    print("net is clearly positive (and prefer fewer crew hits at similar net — design §6).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
