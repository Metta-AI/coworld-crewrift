#!/usr/bin/env python3
"""A/B compare two MATCHED batches of Crewrift episodes — the quantitative half.

Diffs a BASELINE vs a CANDIDATE policy version on role-decomposed metrics from
results.json + episode.json, leads with a chosen `--target` axis, and flags whether
each delta is a real move or within noise (effect size + a normal-approx significance
test). This is the hard-metrics engine of the `crewrift-ab` skill; the qualitative
half — reading logs/replays across the two sides for the *why* (killed by whom, voting
wrong and why, …) — is the agent's job, steered by context. See SKILL.md.

CRITICAL — the two batches must be FRESH + MATCHED: both versions run in the same
window against the same roster/roles/count (fire two matched experience requests; see
SKILL.md). The league field drifts as others change their agents, so only a
same-window head-to-head makes the delta attributable to *your* change — the question
is "better *now*," not "better than a stale baseline."
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path


# --- per-appearance record (compact; mirrors crewrift-report's results.json model) ---

@dataclass
class Rec:
    role: str          # "crew" | "imposter"
    score: int
    tasks: int
    kills: int
    win: bool
    vote_timeout: int
    ops_fail: bool
    penalty: int
    game_tasks_done: int
    game_tasks_total: int


def parse_spec(spec: str) -> tuple[str, int | None]:
    """'crewborg:v15' -> ('crewborg', 15); 'crewborg' -> ('crewborg', None)."""
    if ":v" in spec:
        name, v = spec.split(":v", 1)
        return name, int(v)
    return spec, None


def slot_entries(episode: dict) -> list[tuple[int, str | None, int | None]]:
    """Normalize an episode's slot->policy map to ``(position, policy_name, version)``.

    The downloader writes the raw episode record in two shapes:
    - **league** episodes: ``policy_results[]`` = ``[{position, policy:{name,version}}]``
    - **experience-request** episodes: ``participants[]`` =
      ``[{position, policy_name, version}]``

    A/B comparison runs on matched experience requests, so the ``participants`` shape is
    the common case here — both must work.
    """
    out: list[tuple[int, str | None, int | None]] = []
    policy_results = episode.get("policy_results")
    if policy_results:
        for entry in policy_results:
            pol = entry.get("policy") or {}
            if entry.get("position") is not None:
                out.append((entry["position"], pol.get("name"), pol.get("version")))
        return out
    for entry in episode.get("participants") or []:
        if entry.get("position") is not None:
            out.append((entry["position"], entry.get("policy_name"), entry.get("version")))
    return out


def load_batch(root: Path, policy: str, version: int | None) -> list[Rec]:
    """Every appearance of (policy[:version]) across the episode dirs in `root`."""
    recs: list[Rec] = []
    for ep in sorted(p for p in root.iterdir() if p.is_dir()):
        ej, rj = ep / "episode.json", ep / "results.json"
        if not (ej.exists() and rj.exists()):
            continue
        try:
            episode, results = json.loads(ej.read_text()), json.loads(rj.read_text())
        except json.JSONDecodeError:
            continue
        slots = [pos for pos, name, ver in slot_entries(episode)
                 if name == policy and (version is None or ver == version)]
        for slot in slots:
            rec = _record(results, slot)
            if rec is not None:
                recs.append(rec)
    return recs


def _record(results: dict, slot: int) -> Rec | None:
    scores = results.get("scores") or []
    if slot is None or slot >= len(scores):
        return None
    def col(k):
        a = results.get(k) or []
        return a[slot] if slot < len(a) else 0
    crew_flags = results.get("crew") or []
    tasks_arr = results.get("tasks") or []
    win = bool(col("win"))
    tasks, kills, score = int(col("tasks")), int(col("kills")), int(col("scores"))
    crew_count = sum(1 for v in crew_flags if v)
    return Rec(
        role="imposter" if col("imposter") else "crew",
        score=score, tasks=tasks, kills=kills, win=win,
        vote_timeout=int(col("vote_timeout")),
        ops_fail=bool(col("connect_timeout") or col("disconnect_timeout")),
        penalty=int(100 * win + tasks + 10 * kills - score),
        game_tasks_done=sum(int(t) for t, c in zip(tasks_arr, crew_flags) if c),
        game_tasks_total=8 * crew_count,
    )


# --- metrics: (key, higher_is_better, kind, applies_to_role) -------------------------
# kind: "rate" (fraction of appearances) or "mean" (continuous average).

METRICS = [
    ("win_rate",                True,  "rate", None),
    ("score_mean",              True,  "mean", None),
    ("tasks_mean",              True,  "mean", "crew"),
    ("kills_mean",              True,  "mean", "imposter"),
    ("penalty_mean",            False, "mean", None),
    ("no_vote_rate",            False, "rate", None),
    ("ops_fail_rate",           False, "rate", None),
    ("imposter_no_kills_rate",  False, "rate", "imposter"),
    ("crew_low_tasks_rate",     False, "rate", "crew"),
    ("crew_lost_nearly_won_rate", False, "rate", "crew"),
]
LOW_TASKS_ABS = 4
NEARLY_WON_FRAC = 0.85


def metric_value(recs: list[Rec], key: str) -> tuple[float, int] | None:
    """Return (value, n) for a metric over a role's records, or None if N/A."""
    if not recs:
        return None
    n = len(recs)
    if key == "win_rate":
        return sum(r.win for r in recs) / n, n
    if key == "score_mean":
        return statistics.mean(r.score for r in recs), n
    if key == "tasks_mean":
        return statistics.mean(r.tasks for r in recs), n
    if key == "kills_mean":
        return statistics.mean(r.kills for r in recs), n
    if key == "penalty_mean":
        return statistics.mean(r.penalty for r in recs), n
    if key == "no_vote_rate":
        return sum(r.vote_timeout > 0 for r in recs) / n, n
    if key == "ops_fail_rate":
        return sum(r.ops_fail for r in recs) / n, n
    if key == "imposter_no_kills_rate":
        return sum(r.kills == 0 for r in recs) / n, n
    if key == "crew_low_tasks_rate":
        return sum(r.tasks <= LOW_TASKS_ABS for r in recs) / n, n
    if key == "crew_lost_nearly_won_rate":
        return sum((not r.win) and r.game_tasks_total
                   and r.game_tasks_done / r.game_tasks_total >= NEARLY_WON_FRAC
                   for r in recs) / n, n
    return None


def _values(recs: list[Rec], key: str) -> list[float]:
    """Per-appearance values for a metric (for the continuous significance test)."""
    if key == "score_mean":   return [float(r.score) for r in recs]
    if key == "tasks_mean":   return [float(r.tasks) for r in recs]
    if key == "kills_mean":   return [float(r.kills) for r in recs]
    if key == "penalty_mean": return [float(r.penalty) for r in recs]
    return []


# --- significance (normal-approx; no scipy) -----------------------------------------

def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def two_sided_p(z: float) -> float:
    return 2 * (1 - _phi(abs(z)))


def rate_sig(p_a: float, n_a: int, p_b: float, n_b: int) -> tuple[float, float]:
    """Two-proportion z-test. Returns (z, p)."""
    if n_a == 0 or n_b == 0:
        return 0.0, 1.0
    pool = (p_a * n_a + p_b * n_b) / (n_a + n_b)
    se = math.sqrt(pool * (1 - pool) * (1 / n_a + 1 / n_b))
    if se == 0:
        return 0.0, 1.0
    z = (p_b - p_a) / se
    return z, two_sided_p(z)


def mean_sig(vals_a: list[float], vals_b: list[float]) -> tuple[float, float, float]:
    """Welch-ish z on the mean difference + Cohen's d. Returns (z, p, d)."""
    if len(vals_a) < 2 or len(vals_b) < 2:
        return 0.0, 1.0, 0.0
    ma, mb = statistics.mean(vals_a), statistics.mean(vals_b)
    va, vb = statistics.variance(vals_a), statistics.variance(vals_b)
    se = math.sqrt(va / len(vals_a) + vb / len(vals_b))
    z = (mb - ma) / se if se else 0.0
    pooled_sd = math.sqrt((va + vb) / 2)
    d = (mb - ma) / pooled_sd if pooled_sd else 0.0
    return z, two_sided_p(z), d


SIG_P = 0.05
SMALL_N = 30


@dataclass
class Delta:
    metric: str
    role: str
    higher_is_better: bool
    base: float | None
    cand: float | None
    n_base: int
    n_cand: int
    kind: str
    p: float = 1.0
    effect: float = 0.0          # Cohen's d for means; z for rates
    verdict: str = "n/a"         # improved | regressed | noise | n/a

    def compute(self, base_recs, cand_recs):
        if self.base is None or self.cand is None:
            return
        delta = self.cand - self.base
        if self.kind == "rate":
            z, p = rate_sig(self.base, self.n_base, self.cand, self.n_cand)
            self.p, self.effect = p, z
        else:
            z, p, d = mean_sig(_values(base_recs, self.metric), _values(cand_recs, self.metric))
            self.p, self.effect = p, d
        sig = self.p < SIG_P and min(self.n_base, self.n_cand) >= 2
        if not sig or delta == 0:
            self.verdict = "noise"
        else:
            better = (delta > 0) == self.higher_is_better
            self.verdict = "improved" if better else "regressed"


def build_deltas(base: dict[str, list[Rec]], cand: dict[str, list[Rec]]) -> list[Delta]:
    out: list[Delta] = []
    for key, hib, kind, only_role in METRICS:
        roles = [only_role] if only_role else ["crew", "imposter"]
        for role in roles:
            br, cr = base.get(role, []), cand.get(role, [])
            bv = metric_value(br, key)
            cv = metric_value(cr, key)
            d = Delta(metric=key, role=role, higher_is_better=hib,
                      base=bv[0] if bv else None, cand=cv[0] if cv else None,
                      n_base=bv[1] if bv else 0, n_cand=cv[1] if cv else 0, kind=kind)
            d.compute(br, cr)
            out.append(d)
    return out


# --- rendering ----------------------------------------------------------------------

VERDICT_MARK = {"improved": "▲ improved", "regressed": "▼ REGRESSED",
                "noise": "· noise", "n/a": "—"}


def fmt(v: float | None, kind: str) -> str:
    if v is None:
        return "—"
    return f"{v*100:.0f}%" if kind == "rate" else f"{v:.2f}"


def by_role(recs: list[Rec]) -> dict[str, list[Rec]]:
    out = {"crew": [], "imposter": []}
    for r in recs:
        out[r.role].append(r)
    return out


def render(base_spec, cand_spec, base, cand, deltas, target):
    L = []
    L.append(f"# A/B: `{cand_spec}` (candidate) vs `{base_spec}` (baseline)")
    L.append("")
    L.append(f"Baseline n: crew {len(base['crew'])}, imposter {len(base['imposter'])}  |  "
             f"Candidate n: crew {len(cand['crew'])}, imposter {len(cand['imposter'])}")
    small = min(len(base['crew']) + len(base['imposter']),
                len(cand['crew']) + len(cand['imposter']))
    if small < SMALL_N:
        L.append("")
        L.append(f"> ⚠ Small sample (min side {small}) — deltas are directional, not "
                 f"conclusive. Run larger matched requests for a firm call.")
    L.append("")

    if target:
        L.append(f"## Target axis: `{target}`")
        hits = [d for d in deltas if d.metric == target]
        if not hits:
            L.append(f"_Unknown metric `{target}`. Known: {', '.join(m[0] for m in METRICS)}._")
        for d in hits:
            if d.base is None and d.cand is None:
                continue
            L.append(f"- **{d.role}**: {fmt(d.base, d.kind)} → {fmt(d.cand, d.kind)}  "
                     f"(**{VERDICT_MARK[d.verdict]}**, p={d.p:.3f}, "
                     f"{'d' if d.kind=='mean' else 'z'}={d.effect:+.2f})")
        L.append("")

    L.append("## All metrics (baseline → candidate, Δ, verdict)")
    L.append("")
    L.append("| metric | role | baseline | candidate | verdict (p) |")
    L.append("| --- | --- | ---: | ---: | --- |")
    for d in deltas:
        if d.base is None and d.cand is None:
            continue
        L.append(f"| {d.metric} | {d.role} | {fmt(d.base, d.kind)} | {fmt(d.cand, d.kind)} "
                 f"| {VERDICT_MARK[d.verdict]} (p={d.p:.2f}) |")
    L.append("")

    regr = [d for d in deltas if d.verdict == "regressed"]
    if regr:
        L.append("## ⚠ Regressions (significant adverse moves — watch these)")
        for d in regr:
            L.append(f"- **{d.metric} / {d.role}**: {fmt(d.base, d.kind)} → {fmt(d.cand, d.kind)} (p={d.p:.2f})")
        L.append("")

    L.append("## Next: the qualitative half")
    L.append("")
    L.append("Numbers say *whether* it moved; they don't say *why*. Now read the two")
    L.append("batches' replays + logs side by side, steered by your context (target")
    L.append("dimension / specific opponent / specific fault) — see SKILL.md §Qualitative.")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline_dir", help="Episodes dir for the BASELINE version (matched, fresh).")
    ap.add_argument("candidate_dir", help="Episodes dir for the CANDIDATE version (matched, fresh).")
    ap.add_argument("--baseline", required=True, help="Baseline policy as NAME or NAME:vN.")
    ap.add_argument("--candidate", required=True, help="Candidate policy as NAME or NAME:vN.")
    ap.add_argument("--target", help="Lead metric (e.g. win_rate, kills_mean, imposter_no_kills_rate).")
    ap.add_argument("--json", help="Also write the structured diff here.")
    args = ap.parse_args()

    bname, bver = parse_spec(args.baseline)
    cname, cver = parse_spec(args.candidate)
    base_recs = load_batch(Path(args.baseline_dir), bname, bver)
    cand_recs = load_batch(Path(args.candidate_dir), cname, cver)
    if not base_recs:
        raise SystemExit(f"no '{args.baseline}' appearances in {args.baseline_dir}")
    if not cand_recs:
        raise SystemExit(f"no '{args.candidate}' appearances in {args.candidate_dir}")

    base, cand = by_role(base_recs), by_role(cand_recs)
    deltas = build_deltas(base, cand)
    print(render(args.baseline, args.candidate, base, cand, deltas, args.target))

    if args.json:
        Path(args.json).write_text(json.dumps({
            "baseline": args.baseline, "candidate": args.candidate, "target": args.target,
            "deltas": [{"metric": d.metric, "role": d.role, "base": d.base, "cand": d.cand,
                        "n_base": d.n_base, "n_cand": d.n_cand, "p": d.p,
                        "effect": d.effect, "verdict": d.verdict} for d in deltas],
        }, indent=2))
        print(f"\n[wrote JSON: {args.json}]")


if __name__ == "__main__":
    main()
