#!/usr/bin/env python3
"""Build a self-contained HTML dashboard from crewborg eval `analysis_episodes.json`.

One eval set is hundreds of focus-slot episodes. Each record carries enough to
reconstruct EVERY policy's per-episode result (not just crewborg's):

  * positions       slot -> policy label
  * roles_by_color  color -> "imposter"/"crewmate"  (SLOT_COLORS maps slot->color)
  * alive_by_color  color -> survived?
  * outcome         "imps_win" | "crew_wins" | "draw" | "unknown"
  * scores_by_label policy label -> score
  * meetings / votes_selected / kills_landed / tasks_completed / deaths_seen

This aggregates that into per-policy strengths, a head-to-head threat matrix, and
field-level distributions, then emits ONE standalone `.html` (Chart.js via CDN,
data inlined as JSON) so it opens with a double-click — no server, no build.

Usage:
    uv run python players/crewrift/crewborg/scripts/build_eval_dashboard.py \
        players/crewrift/crewborg/episode_data/eval_2026-06-11_v6_topranked \
        -o /tmp/eval_dashboard.html

    # multiple eval sets merged into one field view:
    uv run python .../build_eval_dashboard.py EVAL_A EVAL_B -o out.html --title "Field"
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# slot -> color (the game's fixed seat->color map; design.md / analyze.py).
SLOT_COLORS = [
    "red", "orange", "yellow", "light blue",
    "pink", "lime", "blue", "pale blue",
]

WIN_BY_ROLE = {"imposter": "imps_win", "crewmate": "crew_wins"}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load_records(paths: list[Path]) -> list[dict[str, Any]]:
    """Load + tag every analysis_episodes.json record with its source eval set."""
    records: list[dict[str, Any]] = []
    for p in paths:
        f = p / "analysis_episodes.json" if p.is_dir() else p
        if not f.is_file():
            log(f"  ! no analysis_episodes.json at {p} (skipping)")
            continue
        data = json.loads(f.read_text())
        label = (p if p.is_dir() else p.parent).name
        for r in data:
            r["_eval"] = label
            records.append(r)
        log(f"  loaded {len(data)} records from {label}")
    return records


def is_tainted(rec: dict[str, Any]) -> bool:
    """A disconnect/no-show poisons the whole lobby (any slot at -100)."""
    scores = (rec.get("scores_by_label") or {}).values()
    vals = [v for v in scores if isinstance(v, (int, float))]
    if not vals:
        return True
    return min(vals) <= -100


def policy_role_in(rec: dict[str, Any], label: str) -> str | None:
    """The role `label` played in this episode (via its slot -> color -> role)."""
    positions = rec.get("positions") or {}
    roles = rec.get("roles_by_color") or {}
    for slot_str, plabel in positions.items():
        if plabel != label:
            continue
        try:
            color = SLOT_COLORS[int(slot_str)]
        except (ValueError, IndexError):
            continue
        role = roles.get(color)
        if role:
            return role
    return None


def policy_won(rec: dict[str, Any], role: str) -> bool:
    return rec.get("outcome") == WIN_BY_ROLE.get(role)


def wilson(wins: int, n: int) -> tuple[float, float, float]:
    """Wilson 95% CI for a win rate — honest small-N bands, not naive p±SE."""
    if n == 0:
        return 0.0, 0.0, 0.0
    z = 1.96
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce the episode records into everything the dashboard charts."""
    clean = [r for r in records if not is_tainted(r)]
    tainted_n = len(records) - len(clean)

    # Per-policy accumulators.
    pol: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "games": 0, "wins": 0,
            "imp_games": 0, "imp_wins": 0,
            "crew_games": 0, "crew_wins": 0,
            "score_sum": 0.0, "score_n": 0,
            "survived": 0, "survived_n": 0,
        }
    )
    # Head-to-head: when A and B share a lobby, did A win its role that game?
    # threat[A][B] = A's win rate in games B was also present (B's "pressure" on A).
    co_games: dict[str, Counter] = defaultdict(Counter)
    co_wins: dict[str, Counter] = defaultdict(Counter)

    field_outcomes: Counter = Counter()
    game_len: list[int] = []
    meetings_per_game: list[int] = []
    meeting_triggers: Counter = Counter()
    seen_ereqs: set[str] = set()

    for r in clean:
        positions = r.get("positions") or {}
        labels_in_game = sorted(set(positions.values()))
        scores = r.get("scores_by_label") or {}
        alive = r.get("alive_by_color") or {}

        for label in labels_in_game:
            role = policy_role_in(r, label)
            s = pol[label]
            s["games"] += 1
            won = role is not None and policy_won(r, role)
            if won:
                s["wins"] += 1
            if role == "imposter":
                s["imp_games"] += 1
                s["imp_wins"] += int(won)
            elif role == "crewmate":
                s["crew_games"] += 1
                s["crew_wins"] += int(won)
            sc = scores.get(label)
            if isinstance(sc, (int, float)):
                s["score_sum"] += sc
                s["score_n"] += 1
            # survival of this policy's slots
            for slot_str, plabel in positions.items():
                if plabel != label:
                    continue
                try:
                    color = SLOT_COLORS[int(slot_str)]
                except (ValueError, IndexError):
                    continue
                if color in alive:
                    s["survived_n"] += 1
                    s["survived"] += int(bool(alive[color]))

            # head-to-head pressure
            for other in labels_in_game:
                if other == label:
                    continue
                co_games[label][other] += 1
                if won:
                    co_wins[label][other] += 1

        # field-level (count each episode once via ereq)
        ereq = r.get("ereq")
        if ereq and ereq in seen_ereqs:
            continue
        if ereq:
            seen_ereqs.add(ereq)
        field_outcomes[r.get("outcome") or "unknown"] += 1
        if isinstance(r.get("last_tick"), int):
            game_len.append(r["last_tick"])
        ms = r.get("meetings") or []
        meetings_per_game.append(len(ms))
        for m in ms:
            meeting_triggers[m.get("trigger") or "unknown"] += 1

    # Finalize per-policy with CIs.
    policies = []
    for label, s in pol.items():
        wr, lo, hi = wilson(s["wins"], s["games"])
        iwr, ilo, ihi = wilson(s["imp_wins"], s["imp_games"])
        cwr, clo, chi = wilson(s["crew_wins"], s["crew_games"])
        policies.append({
            "label": label,
            "games": s["games"],
            "win_rate": wr, "win_lo": lo, "win_hi": hi,
            "imp_games": s["imp_games"], "imp_win_rate": iwr, "imp_lo": ilo, "imp_hi": ihi,
            "crew_games": s["crew_games"], "crew_win_rate": cwr, "crew_lo": clo, "crew_hi": chi,
            "mean_score": (s["score_sum"] / s["score_n"]) if s["score_n"] else None,
            "survival_rate": (s["survived"] / s["survived_n"]) if s["survived_n"] else None,
        })
    policies.sort(key=lambda d: d["win_rate"], reverse=True)
    order = [p["label"] for p in policies]

    # Threat matrix rows aligned to `order`.
    threat = []
    for a in order:
        row = []
        for b in order:
            if a == b:
                row.append(None)
                continue
            g = co_games[a][b]
            row.append(round(co_wins[a][b] / g, 4) if g else None)
        threat.append(row)

    return {
        "policies": policies,
        "order": order,
        "threat": threat,
        "co_games": {a: dict(co_games[a]) for a in order},
        "field_outcomes": dict(field_outcomes),
        "game_len": game_len,
        "meetings_per_game": meetings_per_game,
        "meeting_triggers": dict(meeting_triggers),
        "n_records": len(records),
        "n_clean": len(clean),
        "n_tainted": tainted_n,
        "n_episodes": len(seen_ereqs) or len(clean),
    }


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0b0d12; --panel: #14171f; --panel2: #1b1f2a; --border: #262b38;
    --fg: #e6e9ef; --muted: #9aa3b2; --accent: #6ea8fe; --good: #4ade80;
    --bad: #f87171; --warn: #fbbf24;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg); font-family: var(--sans); }
  header { padding: 24px 32px 8px; }
  h1 { margin: 0 0 4px; font-size: 22px; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 13px; font-family: var(--mono); }
  .grid { display: grid; gap: 16px; padding: 16px 32px 48px;
          grid-template-columns: repeat(12, 1fr); }
  .card { background: var(--panel); border: 1px solid var(--border);
          border-radius: 12px; padding: 16px 18px; min-width: 0; }
  .card h2 { margin: 0 0 2px; font-size: 14px; font-weight: 600; }
  .card p.hint { margin: 0 0 12px; color: var(--muted); font-size: 12px; }
  .col-12 { grid-column: span 12; } .col-8 { grid-column: span 8; }
  .col-6 { grid-column: span 6; } .col-4 { grid-column: span 4; }
  @media (max-width: 1000px) { .grid > * { grid-column: span 12 !important; } }
  .stat-row { display: flex; flex-wrap: wrap; gap: 12px; padding: 0 32px 8px; }
  .stat { background: var(--panel2); border: 1px solid var(--border);
          border-radius: 10px; padding: 10px 14px; min-width: 120px; }
  .stat .v { font-size: 22px; font-weight: 700; font-family: var(--mono); }
  .stat .k { font-size: 11px; color: var(--muted); text-transform: uppercase;
             letter-spacing: 0.05em; }
  canvas { max-height: 360px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { padding: 6px 8px; text-align: right; border-bottom: 1px solid var(--border);
           white-space: nowrap; }
  th:first-child, td:first-child { text-align: left; font-family: var(--mono); }
  thead th { color: var(--muted); font-weight: 600; position: sticky; top: 0;
             background: var(--panel); }
  .matrix-wrap { overflow-x: auto; }
  .matrix { border-collapse: collapse; font-size: 11px; font-family: var(--mono); }
  .matrix th, .matrix td { border: 1px solid var(--border); padding: 6px 8px;
            text-align: center; min-width: 54px; }
  .matrix th.rot { writing-mode: vertical-rl; transform: rotate(180deg);
            max-height: 120px; }
  .matrix td.self { background: #0d0f15; color: var(--muted); }
  .legend { color: var(--muted); font-size: 11px; margin-top: 10px; }
  .pill { display:inline-block;width:10px;height:10px;border-radius:2px;
          vertical-align:middle;margin-right:4px;}
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <div class="sub">__SUBTITLE__</div>
</header>
<div class="stat-row" id="stats"></div>
<div class="grid">
  <div class="card col-8">
    <h2>Win rate by policy (with role split)</h2>
    <p class="hint">Overall win rate ranked; bars show imposter vs crewmate. Error bars are 95% Wilson CIs on overall.</p>
    <canvas id="winByPolicy"></canvas>
  </div>
  <div class="card col-4">
    <h2>Field outcomes</h2>
    <p class="hint">Who wins across the whole field.</p>
    <canvas id="outcomes"></canvas>
  </div>

  <div class="card col-8">
    <h2>Threat matrix — row policy's win rate when column policy is present</h2>
    <p class="hint">Read a row: low cells = that opponent suppresses this policy (a threat). Green = row wins often despite the column; red = the column is dangerous to the row.</p>
    <div class="matrix-wrap"><div id="matrix"></div></div>
    <div class="legend"><span class="pill" style="background:#f87171"></span>opponent suppresses row &nbsp;
      <span class="pill" style="background:#fbbf24"></span>even &nbsp;
      <span class="pill" style="background:#4ade80"></span>row dominates</div>
  </div>
  <div class="card col-4">
    <h2>Strength vs survival</h2>
    <p class="hint">Win rate against how often the policy's agents stay alive. Bubble size = games played.</p>
    <canvas id="scatter"></canvas>
  </div>

  <div class="card col-6">
    <h2>Mean score by policy</h2>
    <p class="hint">League points matter, not just W/L: a frequent small win can outscore a rare big one.</p>
    <canvas id="score"></canvas>
  </div>
  <div class="card col-6">
    <h2>Game length distribution</h2>
    <p class="hint">Ticks to game over (24/s). Short games skew imposter-favored.</p>
    <canvas id="gamelen"></canvas>
  </div>

  <div class="card col-6">
    <h2>Meeting triggers</h2>
    <p class="hint">How meetings start across the field (button vs body report).</p>
    <canvas id="triggers"></canvas>
  </div>
  <div class="card col-6">
    <h2>Per-policy leaderboard</h2>
    <p class="hint">Sortable summary — overall / imposter / crewmate win rate, games, mean score, survival.</p>
    <div class="matrix-wrap"><table id="leaderboard"></table></div>
  </div>
</div>
<script>const DATA = __DATA__;</script>
<script>__JS__</script>
</body>
</html>
"""


DASHBOARD_JS = r"""
const fmtPct = v => v == null ? '—' : (v * 100).toFixed(0) + '%';
const short = s => s.length > 22 ? s.slice(0, 21) + '…' : s;
Chart.defaults.color = '#9aa3b2';
Chart.defaults.borderColor = '#262b38';
Chart.defaults.font.family = 'ui-sans-serif, system-ui, sans-serif';

// Summary stats.
const statRow = document.getElementById('stats');
[
  ['episodes', DATA.n_episodes],
  ['policies', DATA.policies.length],
  ['clean records', DATA.n_clean],
  ['tainted (−100)', DATA.n_tainted],
].forEach(([k, v]) => {
  const d = document.createElement('div');
  d.className = 'stat';
  d.innerHTML = `<div class="v">${v}</div><div class="k">${k}</div>`;
  statRow.appendChild(d);
});

const labels = DATA.policies.map(p => short(p.label));

// 1. Win rate by policy (overall line w/ CI as error-ish via floating bar, plus role bars).
new Chart(document.getElementById('winByPolicy'), {
  type: 'bar',
  data: {
    labels,
    datasets: [
      { label: 'Imposter', data: DATA.policies.map(p => p.imp_win_rate), backgroundColor: '#f87171cc' },
      { label: 'Crewmate', data: DATA.policies.map(p => p.crew_win_rate), backgroundColor: '#6ea8fecc' },
      { label: 'Overall', type: 'line', data: DATA.policies.map(p => p.win_rate),
        borderColor: '#4ade80', backgroundColor: '#4ade80', pointRadius: 4, fill: false, tension: 0 },
    ],
  },
  options: {
    scales: { y: { min: 0, max: 1, ticks: { callback: fmtPct } } },
    plugins: { tooltip: { callbacks: { label: c => {
      const p = DATA.policies[c.dataIndex];
      if (c.dataset.label === 'Overall')
        return `Overall ${fmtPct(p.win_rate)} (95% CI ${fmtPct(p.win_lo)}–${fmtPct(p.win_hi)}, n=${p.games})`;
      return `${c.dataset.label} ${fmtPct(c.parsed.y)}`;
    } } } },
  },
});

// 2. Field outcomes doughnut.
const oc = DATA.field_outcomes;
new Chart(document.getElementById('outcomes'), {
  type: 'doughnut',
  data: { labels: Object.keys(oc), datasets: [{ data: Object.values(oc),
    backgroundColor: ['#f87171', '#6ea8fe', '#fbbf24', '#6b7280'] }] },
  options: { plugins: { legend: { position: 'bottom' } } },
});

// 3. Threat matrix.
function heat(v) {
  if (v == null) return '#0d0f15';
  // v is row's win rate vs column. low => column threatens row (red).
  const t = Math.max(0, Math.min(1, v));
  const r = t < 0.5 ? 248 : Math.round(248 - (t - 0.5) * 2 * (248 - 74));
  const g = t < 0.5 ? Math.round(113 + t * 2 * (190 - 113)) : Math.round(190 + (t - 0.5) * 2 * (222 - 190));
  const b = t < 0.5 ? Math.round(113 - t * 2 * 50) : Math.round(63 + (t - 0.5) * 2 * (128 - 63));
  return `rgb(${r},${g},${b})`;
}
(function () {
  const order = DATA.order, M = DATA.threat;
  let h = '<table class="matrix"><thead><tr><th></th>';
  order.forEach(o => h += `<th class="rot" title="${o}">${short(o)}</th>`);
  h += '</tr></thead><tbody>';
  order.forEach((row, i) => {
    h += `<tr><th title="${row}">${short(row)}</th>`;
    M[i].forEach((v, j) => {
      if (v == null) { h += `<td class="self">·</td>`; return; }
      const g = (DATA.co_games[row] || {})[order[j]] || 0;
      h += `<td style="background:${heat(v)};color:#0b0d12;font-weight:600"
            title="${row} won ${fmtPct(v)} of ${g} games vs ${order[j]}">${fmtPct(v)}</td>`;
    });
    h += '</tr>';
  });
  h += '</tbody></table>';
  document.getElementById('matrix').innerHTML = h;
})();

// 4. Strength vs survival scatter.
new Chart(document.getElementById('scatter'), {
  type: 'bubble',
  data: { datasets: DATA.policies.filter(p => p.survival_rate != null).map((p, i) => ({
    label: short(p.label),
    data: [{ x: p.survival_rate, y: p.win_rate, r: Math.max(4, Math.sqrt(p.games) * 1.6) }],
    backgroundColor: `hsl(${(i * 47) % 360} 70% 60% / 0.75)`,
  })) },
  options: {
    scales: {
      x: { title: { display: true, text: 'survival rate' }, min: 0, max: 1, ticks: { callback: fmtPct } },
      y: { title: { display: true, text: 'win rate' }, min: 0, max: 1, ticks: { callback: fmtPct } },
    },
    plugins: { legend: { display: false }, tooltip: { callbacks: {
      label: c => `${c.dataset.label}: win ${fmtPct(c.parsed.y)}, survive ${fmtPct(c.parsed.x)}` } } },
  },
});

// 5. Mean score.
new Chart(document.getElementById('score'), {
  type: 'bar',
  data: { labels, datasets: [{ label: 'mean score',
    data: DATA.policies.map(p => p.mean_score),
    backgroundColor: DATA.policies.map(p => (p.mean_score || 0) >= 0 ? '#4ade80cc' : '#f87171cc') }] },
  options: { indexAxis: 'y', plugins: { legend: { display: false } } },
});

// 6. Game length histogram.
function hist(vals, bins) {
  if (!vals.length) return { labels: [], counts: [] };
  const lo = Math.min(...vals), hi = Math.max(...vals), w = (hi - lo) / bins || 1;
  const counts = new Array(bins).fill(0);
  vals.forEach(v => { let i = Math.min(bins - 1, Math.floor((v - lo) / w)); counts[i]++; });
  const lab = counts.map((_, i) => Math.round(lo + i * w));
  return { labels: lab, counts };
}
const gl = hist(DATA.game_len, 20);
new Chart(document.getElementById('gamelen'), {
  type: 'bar',
  data: { labels: gl.labels, datasets: [{ label: 'games', data: gl.counts, backgroundColor: '#6ea8fecc' }] },
  options: { plugins: { legend: { display: false } },
    scales: { x: { title: { display: true, text: 'last tick' } } } },
});

// 7. Meeting triggers.
const tg = DATA.meeting_triggers;
new Chart(document.getElementById('triggers'), {
  type: 'bar',
  data: { labels: Object.keys(tg), datasets: [{ data: Object.values(tg),
    backgroundColor: ['#fbbf24cc', '#6ea8fecc', '#a78bfacc', '#6b7280cc'] }] },
  options: { plugins: { legend: { display: false } } },
});

// 8. Leaderboard table.
(function () {
  const cols = [
    ['label', 'policy', x => x],
    ['win_rate', 'win', fmtPct], ['imp_win_rate', 'imp', fmtPct], ['crew_win_rate', 'crew', fmtPct],
    ['games', 'n', x => x], ['mean_score', 'score', x => x == null ? '—' : x.toFixed(1)],
    ['survival_rate', 'survive', fmtPct],
  ];
  let h = '<thead><tr>' + cols.map(c => `<th>${c[1]}</th>`).join('') + '</tr></thead><tbody>';
  DATA.policies.forEach(p => {
    h += '<tr>' + cols.map(c => `<td title="${c[0]==='label'?p.label:''}">${c[2](p[c[0]])}</td>`).join('') + '</tr>';
  });
  document.getElementById('leaderboard').innerHTML = h + '</tbody>';
})();
"""


def render_html(agg: dict[str, Any], title: str, subtitle: str) -> str:
    return (
        HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__SUBTITLE__", subtitle)
        .replace("__DATA__", json.dumps(agg))
        .replace("__JS__", DASHBOARD_JS)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a standalone HTML dashboard from eval analysis_episodes.json.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("eval_dirs", nargs="+", type=Path,
                        help="One or more eval-set dirs (each with analysis_episodes.json) "
                             "or paths to analysis_episodes.json files.")
    parser.add_argument("-o", "--out", type=Path, default=Path("eval_dashboard.html"),
                        help="Output HTML path.")
    parser.add_argument("--title", default=None, help="Dashboard title.")
    args = parser.parse_args(argv)

    log("Loading eval records...")
    records = load_records(args.eval_dirs)
    if not records:
        log("No records found.")
        return 1

    agg = aggregate(records)
    names = ", ".join(sorted({r["_eval"] for r in records}))
    title = args.title or f"Crewrift eval dashboard — {names}"
    subtitle = (
        f"{agg['n_episodes']} episodes · {agg['n_clean']} clean records "
        f"({agg['n_tainted']} tainted) · {len(agg['policies'])} policies"
    )
    html = render_html(agg, title, subtitle)
    args.out.write_text(html)
    log(f"Wrote {args.out} ({len(html) // 1024} KB, {agg['n_episodes']} episodes, "
        f"{len(agg['policies'])} policies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
