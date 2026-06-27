#!/usr/bin/env python3
"""Render an A/B comparison (compare.py --json) → a clean Ink & Print HTML page.

This is a strong DEFAULT, not the only shape — a direct comparison invites visuals (a delta-bar
plot, a role grid, a heat map). Adapt or extend the HTML to fit what *this* comparison shows
(see ../../docs/reference/report-style.md); the agent has creative control over the presentation.

Input: the JSON written by `compare.py --json`:
  {baseline, candidate, target, deltas:[{metric, role, base, cand, n_base, n_cand, p, effect, verdict}]}
Optional: --finding <file> — your qualitative side-by-side finding (markdown/plain text); the part
the numbers can't give. --verdict "<one-line synthesis>".

Usage:  compare_report.py diff.json --out ab.html [--finding finding.md] [--verdict "..."]
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

STYLE = """
:root{--bg:#fffdf4;--alt:#f8f6ef;--fg:#111827;--sub:#555;--muted:#999;--navy:#1a3875;
 --sage:#6e8050;--terra:#b36e4e;--gold:#d4a853;--exclusive:#8f5b3f;--border:#e4dac8;--border-strong:#d4c9b5;}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0;font:400 15px/1.6 'Merriweather Sans',sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:40px 36px 72px}
.mono,.num{font-family:'IBM Plex Mono',monospace;font-feature-settings:"tnum" 1}
header{border-bottom:2px solid var(--fg);padding-bottom:14px;margin-bottom:6px}
.eyebrow{font:700 11px/1 'Merriweather Sans';text-transform:uppercase;letter-spacing:.16em;color:var(--terra);margin:0 0 10px}
h1{font:900 28px/1.2 'Merriweather',Georgia,serif;margin:0;letter-spacing:-.01em}
h1 .arrow{color:var(--muted);font-weight:400}
h2{font:700 12px/1 'Merriweather Sans';text-transform:uppercase;letter-spacing:.12em;color:var(--navy);margin:34px 0 10px}
.headline{display:flex;gap:22px;flex-wrap:wrap;margin:18px 0 4px}
.hcard{flex:1;min-width:190px;background:var(--alt);border:1px solid var(--border);border-radius:3px;padding:12px 15px}
.hcard .l{font:700 9.5px 'Merriweather Sans';text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.hcard .v{font:600 22px 'IBM Plex Mono';margin-top:3px}
.hcard .vd{font-size:12px;color:var(--sub);margin-top:1px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:6px 9px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap}
thead th{font:700 10px 'Merriweather Sans';text-transform:uppercase;letter-spacing:.07em;color:var(--sub);border-bottom:2px solid var(--border-strong)}
td.metric,th:first-child{text-align:left;font:600 13px 'IBM Plex Mono'}
td.role{text-align:left;color:var(--sub);font-size:12px}
.v-improved{color:var(--sage);font-weight:700} .v-regressed{color:var(--terra);font-weight:700} .v-noise,.v-na{color:var(--muted)}
.chip{font:700 9px 'Merriweather Sans';text-transform:uppercase;letter-spacing:.05em;padding:1px 7px;border-radius:999px}
.c-improved{background:rgba(110,128,80,.16);color:var(--sage)} .c-regressed{background:rgba(179,110,78,.14);color:var(--terra)}
.c-noise{background:#efe9dd;color:var(--muted)} .c-na{background:#efe9dd;color:var(--muted)}
/* delta bar (effect size, centred at 0) */
.bar{position:relative;width:120px;height:14px;background:linear-gradient(90deg,rgba(179,110,78,.10),#f3eee2 50%,rgba(110,128,80,.10));border-radius:2px;display:inline-block;vertical-align:middle}
.bar .mid{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--border-strong)}
.bar .fill{position:absolute;top:2px;bottom:2px;border-radius:2px}
.regr{background:rgba(179,110,78,.08);border-left:3px solid var(--terra);padding:11px 15px;font-size:14px;margin:6px 0}
.ok{background:rgba(110,128,80,.08);border-left:3px solid var(--sage);padding:11px 15px;font-size:14px;margin:6px 0}
.finding{background:var(--alt);border-left:3px solid var(--navy);padding:14px 17px;font-size:14.5px;color:var(--fg)}
.finding h3{font:700 11px 'Merriweather Sans';text-transform:uppercase;letter-spacing:.1em;color:var(--navy);margin:0 0 7px}
.verdict{font:600 16px 'Merriweather',serif;margin:8px 0 0}
.note{font-size:11.5px;color:var(--muted);font-style:italic;margin-top:22px}
@media(max-width:680px){.wrap{padding:24px 16px}.bar{width:70px}h1{font-size:22px}}
"""

VERD = {"improved": "▲ improved", "regressed": "▼ regressed", "noise": "· noise", "n/a": "—"}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def fmt(v, metric: str) -> str:
    if v is None:
        return "·"
    return f"{100*v:.0f}%" if metric.endswith("_rate") else f"{v:.2f}"


def bar(effect: float, verdict: str) -> str:
    if verdict in ("noise", "n/a"):
        return '<span class="bar"><span class="mid"></span></span>'
    w = min(abs(effect) / 1.2, 1.0) * 50  # half-width %, |d|~1.2 saturates
    color = "var(--sage)" if verdict == "improved" else "var(--terra)"
    side = f"left:50%;width:{w:.0f}%" if verdict == "improved" else f"right:50%;width:{w:.0f}%"
    return f'<span class="bar"><span class="mid"></span><span class="fill" style="{side};background:{color}"></span></span>'


def render(d: dict, finding: str | None, verdict_line: str | None) -> str:
    deltas = d.get("deltas") or []
    target = d.get("target")
    by = {(x["metric"], x.get("role")): x for x in deltas}
    # headline cards: target axis (if any) + overall win rate
    cards = []
    tgt = next((x for x in deltas if x["metric"] == target), None) if target else None
    win = by.get(("win_rate", None))
    for label, x in (("Target · " + (target or "—"), tgt), ("Overall win rate", win)):
        if not x:
            continue
        delta = (x["cand"] or 0) - (x["base"] or 0)
        m = x["metric"]
        cards.append(f'<div class="hcard"><div class="l">{esc(label)}</div>'
                     f'<div class="v v-{x["verdict"].replace("/","")}">{fmt(x["base"],m)} → {fmt(x["cand"],m)}</div>'
                     f'<div class="vd">Δ {"+" if delta>=0 else ""}{fmt(delta,m) if m.endswith("_rate") else f"{delta:.2f}"} · '
                     f'<span class="chip c-{x["verdict"].replace("/","")}">{esc(VERD.get(x["verdict"],x["verdict"]))}</span> · p={x["p"]:.2f}</div></div>')

    rows = []
    for x in deltas:
        m, role, vd = x["metric"], x.get("role"), x["verdict"]
        if x["base"] is None and x["cand"] is None:
            continue
        delta = (x["cand"] or 0) - (x["base"] or 0)
        rows.append(
            f'<tr><td class="metric">{esc(m)}</td><td class="role">{esc(role or "all")}</td>'
            f'<td class="num">{fmt(x["base"],m)}</td><td class="num">{fmt(x["cand"],m)}</td>'
            f'<td class="num v-{vd.replace("/","")}">{"+" if delta>=0 else ""}{fmt(delta,m) if m.endswith("_rate") else f"{delta:.2f}"}</td>'
            f'<td>{bar(x.get("effect") or 0, vd)}</td>'
            f'<td><span class="chip c-{vd.replace("/","")}">{esc(VERD.get(vd,vd))}</span></td>'
            f'<td class="num dim">{x["p"]:.2f}</td><td class="num dim">{x["n_base"]}/{x["n_cand"]}</td></tr>')

    regr = [x for x in deltas if x["verdict"] == "regressed"]
    scan = (f'<div class="regr"><b>⚠ Regression scan:</b> {len(regr)} metric(s) regressed — '
            + ", ".join(f'{esc(x["metric"])} ({esc(x.get("role") or "all")})' for x in regr)
            + '. Check you didn\'t fix one role by breaking the other.</div>') if regr else \
           '<div class="ok"><b>✓ Regression scan:</b> nothing regressed beyond noise.</div>'

    find_html = (f'<h2>Qualitative finding — what the numbers can\'t say</h2>'
                 f'<div class="finding"><h3>Side-by-side read</h3>{esc(finding)}</div>') if finding else ""
    vline = f'<h2>Verdict</h2><p class="verdict">{esc(verdict_line)}</p>' if verdict_line else ""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>A/B — {esc(d.get("baseline"))} vs {esc(d.get("candidate"))}</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Merriweather:wght@400;700;900&family=Merriweather+Sans:wght@400;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>{STYLE}</style></head><body><div class="wrap">
<header><p class="eyebrow">Crewrift · A/B comparison</p>
<h1>{esc(d.get("baseline"))} <span class="arrow">→</span> {esc(d.get("candidate"))}</h1></header>
<div class="headline">{''.join(cards) or '<div class="hcard"><div class="l">No target/win metric</div></div>'}</div>

<h2>All metrics — baseline → candidate, role-split</h2>
<div style="overflow-x:auto"><table><thead><tr>
  <th>Metric</th><th>Role</th><th>Base</th><th>Cand</th><th>Δ</th><th>Effect</th><th>Verdict</th><th>p</th><th>n b/c</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="note">Effect bar = standardized effect size (Cohen's d / z), centred at 0 — right/sage =
candidate better, left/terracotta = worse; noise = no bar. compare.py errs conservative: a borderline
move reads as <i>noise</i>, not a win. Rates need a few hundred appearances/side to separate from noise.</p>
{scan}
{find_html}
{vline}
<p class="note">Quantitative half from compare.py; the qualitative finding is your side-by-side read of
the two batches' replays/logs. Adapt this layout to the comparison — see report-style.md.</p>
</div></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("diff", type=Path, help="The compare.py --json output.")
    ap.add_argument("--out", type=Path, default=Path("ab.html"))
    ap.add_argument("--finding", type=Path, help="Your qualitative finding (markdown/plain text).")
    ap.add_argument("--verdict", help="One-line synthesis verdict.")
    args = ap.parse_args()
    finding = args.finding.read_text() if args.finding and args.finding.exists() else None
    args.out.write_text(render(json.loads(args.diff.read_text()), finding, args.verdict))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
