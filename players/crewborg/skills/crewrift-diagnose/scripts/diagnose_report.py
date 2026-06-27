#!/usr/bin/env python3
"""Render a crewrift-diagnose report → a clean Ink & Print HTML page to show the human.

You (the agent) supply the content as JSON; this just makes it consistent and readable, with each
hypothesis clearly separated. Schema:

{
  "title":   "crewborg — imposter kill-conversion",
  "weakness":"One line: where crewborg is weakest, with the number.",
  "signals": "A short paragraph explaining what the survey/warehouse signals MEAN in the games.",
  "hypotheses": [
    {
      "title":        "Short claim",
      "evidence":     "What you actually saw (episodes / log lines / warehouse rows).",
      "mechanism":    "What is happening and why — pinned to a code location (file:Symbol).",
      "change":       "The one directed change it implies.",
      "predicted_effect": "What should move, per role, and roughly how much.",
      "confidence":   "high | medium | low",
      "experiment":   "A suggested cheap test (often a warehouse query) → crewrift-experiment."
    }
  ]
}

Usage:  diagnose_report.py report.json --out diagnose.html
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

STYLE = """
:root{--bg:#fffdf4;--surface:#fffaf0;--alt:#f8f6ef;--fg:#111827;--sub:#555;--muted:#999;
 --navy:#1a3875;--sage:#6e8050;--terra:#b36e4e;--gold:#d4a853;--exclusive:#8f5b3f;
 --border:#e4dac8;--border-strong:#d4c9b5;}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0;font:400 15px/1.6 'Merriweather Sans',sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:40px 36px 72px}
.mono{font-family:'IBM Plex Mono',monospace}
header{border-bottom:2px solid var(--fg);padding-bottom:14px;margin-bottom:18px}
h1{font:900 30px/1.15 'Merriweather',Georgia,serif;margin:0;letter-spacing:-.01em}
.eyebrow{font:700 11px/1 'Merriweather Sans';text-transform:uppercase;letter-spacing:.16em;color:var(--terra);margin:0 0 10px}
.weak{font-size:15px;color:var(--fg);margin:8px 0 0}.weak b{color:var(--terra)}
.signals{background:var(--alt);border-left:3px solid var(--navy);padding:12px 16px;margin:18px 0 28px;font-size:14px;color:var(--sub)}
.signals h2{font:700 11px/1 'Merriweather Sans';text-transform:uppercase;letter-spacing:.12em;color:var(--navy);margin:0 0 6px}
.hyp{border-top:1px solid var(--border);padding:20px 0}
.hyp:last-child{border-bottom:1px solid var(--border)}
.hyp h3{font:700 18px/1.25 'Merriweather',serif;margin:0 0 4px;display:flex;align-items:baseline;gap:10px}
.hyp h3 .n{font:900 13px 'IBM Plex Mono';color:var(--muted)}
.chip{font:700 9.5px 'Merriweather Sans';text-transform:uppercase;letter-spacing:.06em;padding:2px 8px;border-radius:999px;margin-left:auto}
.c-high{background:rgba(110,128,80,.16);color:var(--sage)} .c-medium{background:rgba(212,168,83,.2);color:#9a7b2e}
.c-low{background:rgba(179,110,78,.14);color:var(--terra)}
.row{display:grid;grid-template-columns:120px 1fr;gap:4px 14px;margin:8px 0;font-size:14px}
.row .k{font:700 10px/1.5 'Merriweather Sans';text-transform:uppercase;letter-spacing:.07em;color:var(--sub);padding-top:2px}
.row .v{color:var(--fg)} .row.mech .v{color:var(--fg)}
.row.exp{background:var(--alt);border-radius:3px;padding:8px 10px;grid-template-columns:120px 1fr}
code{font-family:'IBM Plex Mono';font-size:12px;background:var(--alt);padding:1px 5px;border-radius:3px;color:var(--exclusive)}
.note{font-size:11.5px;color:var(--muted);font-style:italic;margin-top:24px}
@media(max-width:640px){.wrap{padding:24px 16px}.row{grid-template-columns:1fr}h1{font-size:24px}}
"""


def esc(s: str) -> str:
    return html.escape(str(s or ""))


def hyp_block(i: int, h: dict) -> str:
    conf = (h.get("confidence") or "medium").lower()
    rows = [("Evidence", h.get("evidence")), ("Mechanism", h.get("mechanism")),
            ("Change", h.get("change")), ("Predicted effect", h.get("predicted_effect"))]
    body = "".join(
        f'<div class="row{" mech" if k=="Mechanism" else ""}"><div class="k">{k}</div>'
        f'<div class="v">{esc(v)}</div></div>' for k, v in rows if v)
    exp = (f'<div class="row exp"><div class="k">Test it</div><div class="v">{esc(h.get("experiment"))}</div></div>'
           if h.get("experiment") else "")
    return (f'<div class="hyp"><h3><span class="n">H{i}</span>{esc(h.get("title"))}'
            f'<span class="chip c-{conf}">{esc(conf)} confidence</span></h3>{body}{exp}</div>')


def render(d: dict) -> str:
    hyps = "".join(hyp_block(i + 1, h) for i, h in enumerate(d.get("hypotheses") or []))
    signals = (f'<div class="signals"><h2>What the signals mean</h2>{esc(d.get("signals"))}</div>'
               if d.get("signals") else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(d.get("title"))}</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Merriweather:wght@400;700;900&family=Merriweather+Sans:wght@400;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>{STYLE}</style></head><body><div class="wrap">
<header><p class="eyebrow">Crewrift · Diagnosis</p><h1>{esc(d.get("title"))}</h1>
<p class="weak"><b>Weakest:</b> {esc(d.get("weakness"))}</p></header>
{signals}
<p class="eyebrow" style="color:var(--navy)">Mechanistic hypotheses — candidates, not directives</p>
{hyps or '<p class="note">No hypotheses supplied.</p>'}
<p class="note">Each hypothesis is a claim to <b>test</b>, not a recommendation. Hand one to
<code>crewrift-experiment</code> to design + run a falsifiable test.</p>
</div></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path, help="The diagnosis JSON (see the docstring schema).")
    ap.add_argument("--out", type=Path, default=Path("diagnose.html"))
    args = ap.parse_args()
    args.out.write_text(render(json.loads(args.report.read_text())))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
