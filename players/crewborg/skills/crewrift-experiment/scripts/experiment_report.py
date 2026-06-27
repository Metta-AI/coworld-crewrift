#!/usr/bin/env python3
"""Render a crewrift-experiment design → a clean Ink & Print HTML page to show the human BEFORE running
(and to update with the verdict after). You supply the design as JSON; this lays it out clearly with
the falsifiability predictions side-by-side and a design→run→verdict flow. Schema:

{
  "hypothesis":   "The claim: what's happening, why, and the observable consequence.",
  "what_changes": "What's being varied/tested (or 'nothing — re-analysing existing data').",
  "instrument": {
    "kind":   "warehouse-query | experience-request | instrumentation",
    "summary":"One line on the approach.",
    "detail": "The actual query / the XP-request roster+roles+count / the tracing added (shown mono)."
  },
  "if_true":   "What we'd observe if the hypothesis is TRUE.",
  "if_false":  "What we'd observe if the hypothesis is FALSE.",
  "decision_rule": "The exact threshold/comparison that reads true vs false — committed BEFORE running.",
  "confounds": ["A confound and how it's controlled", "..."],
  "verdict":   {"result":"confirmed|refuted|inconclusive","evidence":"..."}   // optional, post-run
}

Usage:  experiment_report.py design.json --out experiment.html
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

STYLE = """
:root{--bg:#fffdf4;--alt:#f8f6ef;--fg:#111827;--sub:#555;--muted:#999;--navy:#1a3875;
 --sage:#6e8050;--terra:#b36e4e;--gold:#d4a853;--exclusive:#8f5b3f;--border:#e4dac8;--border-strong:#d4c9b5;}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0;font:400 15px/1.6 'Merriweather Sans',sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:40px 36px 72px}
header{border-bottom:2px solid var(--fg);padding-bottom:14px;margin-bottom:6px}
.eyebrow{font:700 11px/1 'Merriweather Sans';text-transform:uppercase;letter-spacing:.16em;color:var(--terra);margin:0 0 10px}
h1{font:900 26px/1.25 'Merriweather',Georgia,serif;margin:0;letter-spacing:-.01em}
h2{font:700 12px/1 'Merriweather Sans';text-transform:uppercase;letter-spacing:.12em;color:var(--navy);margin:30px 0 8px}
p{margin:0 0 4px}
.flow{display:flex;align-items:stretch;gap:0;margin:20px 0 6px;flex-wrap:wrap}
.flow .step{flex:1;min-width:120px;background:var(--alt);border:1px solid var(--border);border-radius:3px;padding:9px 11px}
.flow .step .l{font:700 9px 'Merriweather Sans';text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.flow .step .b{font:600 13px 'IBM Plex Mono',monospace;color:var(--navy)}
.flow .arr{display:flex;align-items:center;color:var(--border-strong);font-size:20px;padding:0 7px}
.detail{background:var(--alt);border-left:3px solid var(--navy);padding:12px 16px;font-size:14px;color:var(--sub)}
.detail pre{font:500 12px/1.5 'IBM Plex Mono',monospace;color:var(--fg);white-space:pre-wrap;margin:6px 0 0;overflow-x:auto}
.preds{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:8px 0}
.pred{border-radius:3px;padding:12px 14px;font-size:14px}
.pred.t{background:rgba(110,128,80,.1);border:1px solid rgba(110,128,80,.3)}
.pred.f{background:rgba(179,110,78,.08);border:1px solid rgba(179,110,78,.3)}
.pred .h{font:700 10px 'Merriweather Sans';text-transform:uppercase;letter-spacing:.07em;margin-bottom:5px}
.pred.t .h{color:var(--sage)} .pred.f .h{color:var(--terra)}
.rule{background:#fbf6e6;border:1px solid var(--gold);border-radius:3px;padding:11px 14px;font-size:14px}
.rule b{color:#9a7b2e}
ul{margin:6px 0;padding-left:20px;font-size:14px;color:var(--sub)}
.verdict{border-radius:3px;padding:14px 16px;margin-top:8px;font-size:15px}
.v-confirmed{background:rgba(110,128,80,.14);border:1px solid var(--sage)}
.v-refuted{background:rgba(179,110,78,.12);border:1px solid var(--terra)}
.v-inconclusive{background:rgba(212,168,83,.16);border:1px solid var(--gold)}
.vchip{font:700 10px 'Merriweather Sans';text-transform:uppercase;letter-spacing:.07em;padding:2px 9px;border-radius:999px;margin-right:8px}
.note{font-size:11.5px;color:var(--muted);font-style:italic;margin-top:22px}
code{font-family:'IBM Plex Mono';font-size:12px;background:var(--alt);padding:1px 5px;border-radius:3px}
@media(max-width:640px){.wrap{padding:24px 16px}.preds{grid-template-columns:1fr}.flow{flex-direction:column}.flow .arr{transform:rotate(90deg);padding:3px 0}}
"""

KIND_LABEL = {"warehouse-query": "Warehouse query", "experience-request": "Experience request",
              "instrumentation": "Instrumentation"}


def esc(s: str) -> str:
    return html.escape(str(s or ""))


def render(d: dict) -> str:
    inst = d.get("instrument") or {}
    kind = inst.get("kind", "warehouse-query")
    verdict_state = (d.get("verdict") or {}).get("result")
    flow_steps = [("Hypothesis", "claim"), ("Instrument", KIND_LABEL.get(kind, kind)),
                  ("Decision", "pre-committed"), ("Verdict", verdict_state or "pending")]
    flow = '<span class="arr">→</span>'.join(
        f'<div class="step"><div class="l">{esc(l)}</div><div class="b">{esc(b)}</div></div>'
        for l, b in flow_steps)
    detail = ""
    if inst.get("detail"):
        detail = (f'<div class="detail">{esc(inst.get("summary"))}'
                  f'<pre>{esc(inst.get("detail"))}</pre></div>')
    elif inst.get("summary"):
        detail = f'<div class="detail">{esc(inst.get("summary"))}</div>'
    confounds = "".join(f"<li>{esc(c)}</li>" for c in (d.get("confounds") or []))
    verdict = ""
    if verdict_state:
        v = d["verdict"]
        verdict = (f'<h2>Verdict</h2><div class="verdict v-{esc(verdict_state)}">'
                   f'<span class="vchip v-{esc(verdict_state)}">{esc(verdict_state)}</span>'
                   f'{esc(v.get("evidence"))}</div>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Experiment design</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Merriweather:wght@400;700;900&family=Merriweather+Sans:wght@400;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>{STYLE}</style></head><body><div class="wrap">
<header><p class="eyebrow">Crewrift · Experiment design</p><h1>{esc(d.get("hypothesis"))}</h1></header>
<div class="flow">{flow}</div>

<h2>What's being tested</h2><p>{esc(d.get("what_changes"))}</p>

<h2>How — the instrument</h2>{detail}

<h2>Predictions — the falsifiability contract</h2>
<div class="preds">
  <div class="pred t"><div class="h">If TRUE — confirms</div>{esc(d.get("if_true"))}</div>
  <div class="pred f"><div class="h">If FALSE — refutes</div>{esc(d.get("if_false"))}</div>
</div>
<h2>Decision rule (committed before running)</h2>
<div class="rule"><b>Read as:</b> {esc(d.get("decision_rule"))}</div>
{f'<h2>Confounds controlled</h2><ul>{confounds}</ul>' if confounds else ''}
{verdict}
<p class="note">Shown for your go-ahead before running. The if-true and if-false predictions differ,
so the result can actually decide it — that's the bar for running.</p>
</div></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("design", type=Path, help="The experiment-design JSON (see the docstring schema).")
    ap.add_argument("--out", type=Path, default=Path("experiment.html"))
    args = ap.parse_args()
    args.out.write_text(render(json.loads(args.design.read_text())))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
