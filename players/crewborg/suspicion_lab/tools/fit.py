#!/usr/bin/env python3
"""Fit the suspicion model: L1 logistic regression over the evidence features.

Stage D of the suspicion-learning pipeline (design §6). Coefficients are additive
log-LRs (the runtime stays a dot product + sigmoid); the intercept absorbs the
prior. Instance counts enter linearly (= instance summing); negative coefficients
are exculpatory evidence. Duration-like features are binned into indicator columns
so the model can learn each cue's *shape* (design §6 "shapes without nonlinearity").

Group-aware CV by episode (rows within a game are correlated). Emits
`models/<tag>/suspicion_weights.json` + a metrics report.

    uv run python suspicion_lab/tools/fit.py
    uv run python suspicion_lab/tools/fit.py --dataset /tmp/ds.parquet --tag trial
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import FEATURE_NAMES  # noqa: E402

LAB_ROOT = Path(__file__).resolve().parents[2]
SUSPICION_LAB = LAB_ROOT / "suspicion_lab"

# Duration/sample-count features get binned indicators (shape learning); plain
# count features stay linear (instance summing). THIS SPEC IS THE CONTRACT with the
# runtime scorer — it ships inside suspicion_weights.json and the runtime applies
# the same transform.
BIN_SPEC: dict[str, list[float]] = {
    # bin edges: value v falls in bin i where edges[i-1] < v <= edges[i]; a final
    # open bin catches everything above the last edge. v == 0 -> no indicator.
    "follow_death_samples": [2, 6],
    "tail_obs_samples": [5, 20],
    "tail_obs_max_run": [3, 8],
    "copresence_killrange_samples": [5, 20],
    "task_site_dwell_samples": [5, 20],
    "observed_samples": [10, 40],
}
LINEAR_CLIP = 5

# Features crewborg's CURRENT event log + meeting machinery can compute (no new
# perception detectors): the existing durative cues, witnessed point events, and
# chat-accusation counts. tasks_completed_watched / reported_bodies / vote-history
# features wait on new runtime observers (design §7).
RUNTIME_FEATURES = [
    "witnessed_kills",
    "near_body_bodies",
    "follow_death_samples",
    "tail_obs_samples",
    "tail_obs_max_run",
    "vent_visits",
    "copresence_killrange_samples",
    "task_site_dwell_samples",
    "observed_samples",
    # v2 runtime detectors (strategy/social_evidence.py): watched completions via
    # the crew_tasks_remaining decrement + dwell gate; chat stances; attributed
    # vote dots. Only reported_bodies / button_calls_made remain offline-only —
    # observable in principle since game 4b9297d (the MeetingCall interstitial
    # shows the caller's icon in the player view) but not yet parsed by
    # crewborg's perception.
    "tasks_completed_watched",
    "accusations_made",
    "times_accused",
    "times_defended",
    "votes_cast",
    "votes_skipped",
    "voted_against_observer",
    "vote_agreement_with_observer",
    # meeting caller, parsed from the MeetingCall interstitial (game 4b9297d)
    "reported_bodies",
    "button_calls_made",
]  # linear count features are clipped here (one weight per instance, bounded)


def transform(df: pd.DataFrame, feature_set: list[str] | None = None) -> tuple[np.ndarray, list[str]]:
    """Dataset features -> design matrix. Mirrored by the runtime scorer."""
    cols: list[np.ndarray] = []
    names: list[str] = []
    for name in feature_set or FEATURE_NAMES:
        values = df[name].to_numpy(dtype=float)
        if name in BIN_SPEC:
            edges = [0.0, *BIN_SPEC[name], np.inf]
            for i in range(len(edges) - 1):
                lo, hi = edges[i], edges[i + 1]
                indicator = ((values > lo) & (values <= hi)).astype(float)
                label = f"{name}__gt{lo:g}" if hi == np.inf else f"{name}__{lo:g}to{hi:g}"
                cols.append(indicator)
                names.append(label)
        else:
            cols.append(np.clip(values, 0, LINEAR_CLIP))
            names.append(name)
    return np.column_stack(cols), names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fit suspicion weights.")
    parser.add_argument("--dataset", type=Path, default=SUSPICION_LAB / "dataset" / "dataset.parquet")
    parser.add_argument("--tag", default=str(date.today()))
    parser.add_argument("--c-grid", type=float, nargs="*", default=[0.01, 0.03, 0.1, 0.3, 1.0])
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--features", choices=["full", "runtime"], default="full",
                        help="runtime = only features crewborg's existing event log can compute today")
    args = parser.parse_args(argv)

    df = pd.read_parquet(args.dataset)
    feature_set = FEATURE_NAMES if args.features == "full" else RUNTIME_FEATURES
    X, col_names = transform(df, feature_set)
    y = df.label_imposter.to_numpy()
    groups = df.episode.to_numpy()
    print(f"{len(df)} rows, {df.episode.nunique()} games, base rate {y.mean():.3f}", file=sys.stderr)

    # --- choose C by grouped CV ---------------------------------------------------
    cv = GroupKFold(n_splits=args.folds)
    results = []
    for c in args.c_grid:
        aucs, losses = [], []
        for train_idx, test_idx in cv.split(X, y, groups):
            model = LogisticRegression(penalty="l1", solver="liblinear", C=c, max_iter=2000)
            model.fit(X[train_idx], y[train_idx])
            p = model.predict_proba(X[test_idx])[:, 1]
            aucs.append(roc_auc_score(y[test_idx], p))
            losses.append(log_loss(y[test_idx], p))
        results.append((float(np.mean(losses)), float(np.mean(aucs)), c))
        print(f"  C={c:<5} cv log-loss {np.mean(losses):.4f}  AUC {np.mean(aucs):.3f}", file=sys.stderr)
    results.sort()
    best_c = results[0][2]

    # --- calibration check on grouped held-out predictions -------------------------
    oof = np.zeros(len(y))
    for train_idx, test_idx in cv.split(X, y, groups):
        model = LogisticRegression(penalty="l1", solver="liblinear", C=best_c, max_iter=2000)
        model.fit(X[train_idx], y[train_idx])
        oof[test_idx] = model.predict_proba(X[test_idx])[:, 1]
    print("\ncalibration (out-of-fold):", file=sys.stderr)
    print(f"{'pred bucket':<14} {'n':>7} {'mean pred':>10} {'actual':>8}", file=sys.stderr)
    for lo in np.arange(0, 1, 0.1):
        mask = (oof >= lo) & (oof < lo + 0.1)
        if mask.sum():
            print(f"{lo:.1f}–{lo + 0.1:.1f}     {mask.sum():>7} {oof[mask].mean():>10.3f} {y[mask].mean():>8.3f}",
                  file=sys.stderr)

    # --- final fit on everything ----------------------------------------------------
    model = LogisticRegression(penalty="l1", solver="liblinear", C=best_c, max_iter=2000)
    model.fit(X, y)
    coefs = dict(zip(col_names, model.coef_[0].tolist()))
    nonzero = {k: round(v, 4) for k, v in coefs.items() if abs(v) > 1e-6}
    print("\nfitted weights (log-LR per unit / per bin):", file=sys.stderr)
    for k, v in sorted(nonzero.items(), key=lambda kv: -abs(kv[1])):
        print(f"  {k:<40} {v:+.3f}", file=sys.stderr)
    print(f"  intercept (logit prior): {model.intercept_[0]:+.3f} "
          f"(empirical prior logit {np.log(y.mean() / (1 - y.mean())):+.3f})", file=sys.stderr)

    out_dir = SUSPICION_LAB / "models" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    weights = {
        "schema": "crewborg-suspicion-weights/v1",
        "feature_set": args.features,
        "trained": str(date.today()),
        "dataset": str(args.dataset),
        "games": int(df.episode.nunique()),
        "rows": int(len(df)),
        "C": best_c,
        "cv_auc": results[0][1],
        "cv_log_loss": results[0][0],
        "intercept": float(model.intercept_[0]),
        "linear_clip": LINEAR_CLIP,
        "sample_unit_ticks": 24,
        "bin_spec": BIN_SPEC,
        "coefficients": coefs,
    }
    (out_dir / "suspicion_weights.json").write_text(json.dumps(weights, indent=1))
    # out-of-fold posteriors, for the decision simulator
    oof_df = df[["episode", "meeting_idx", "decision_tick", "observer_slot", "suspect_slot",
                 "suspect_name", "label_imposter"]].copy()
    oof_df["p_imposter"] = oof
    oof_df.to_parquet(out_dir / "oof_predictions.parquet", index=False)
    print(f"\nWrote {out_dir}/suspicion_weights.json (+ oof_predictions.parquet)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
