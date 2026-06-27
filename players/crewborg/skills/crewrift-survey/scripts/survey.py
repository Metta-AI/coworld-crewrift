#!/usr/bin/env python3
"""Fast, lightweight batch survey of a set of Crewrift episodes -> a polished HTML report.

Reads ONLY `results.json` + `episode.json` from each episode directory (no replay parsing,
no logs) — so it scales to hundreds of episodes in seconds. It presents what those two
files carry, per policy, aggregated across the batch and decomposed by role:

  * a per-policy stats table (win% by role, kills/game, tasks/game, votes vs skip,
    vote-timeouts, connect/disconnect ops),
  * a policy x policy win heat map (how often the row policy's side beats the column
    policy's side when they're opponents), and
  * an "interesting episodes" list — each with a natural-language reason (you, the agent
    running the skill, write these) and a clickable Observatory replay link.

Death / ejection / chat counts are NOT here — they aren't in the artifact JSONs
(`game_stats` is empty, agent metrics are just `reward`); they need the replay (the
separate DEEP survey). Keep this one fast.

Two-pass usage (see SKILL.md):
  1. survey.py <eps> --out survey.html --mint-replays
       -> renders the report + mints replay links; writes <out>.interesting.json
  2. (you read the flagged episodes, write a one-line human reason for each) then
     survey.py <eps> --out survey.html --mint-replays --reasons reasons.json
       -> bakes your reasons into the "interesting episodes" section.

The design is the Softmax "Ink & Print" house style (cream paper, Merriweather, mono
numerals, sage/terracotta semantics).
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

# ---------------------------------------------------------------- loading

def find_episode_dirs(paths: list[Path]) -> list[Path]:
    dirs: list[Path] = []
    for p in paths:
        if (p / "results.json").exists():
            dirs.append(p)
        else:
            dirs += [c for c in sorted(p.iterdir()) if c.is_dir() and (c / "results.json").exists()]
    return dirs


def load_episode(d: Path) -> tuple[dict, dict] | None:
    try:
        return json.loads((d / "episode.json").read_text()), json.loads((d / "results.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def slot_policy_map(episode: dict) -> dict[int, str]:
    """position -> 'name:vN'. Handles league (policy_results) + XP-request (participants)."""
    out: dict[int, str] = {}
    for pr in episode.get("policy_results") or []:
        pol = pr.get("policy") or {}
        if pol.get("name") is not None:
            out[pr["position"]] = f"{pol['name']}:v{pol.get('version')}"
    for pt in episode.get("participants") or []:
        if pt.get("policy_name") is not None:
            out[pt["position"]] = f"{pt['policy_name']}:v{pt.get('version')}"
    return out


# ---------------------------------------------------------------- aggregation

@dataclass
class RoleAgg:
    n: int = 0
    wins: int = 0
    scores: list[float] = field(default_factory=list)
    kills: int = 0
    tasks: int = 0
    voted: int = 0
    skip: int = 0
    vote_timeout: int = 0
    ops_fail: int = 0

    def add(self, r: dict, slot: int) -> None:
        self.n += 1
        self.wins += int(bool(r["win"][slot]))
        self.scores.append(r["scores"][slot])
        self.kills += r["kills"][slot]
        self.tasks += r["tasks"][slot]
        self.voted += r["vote_players"][slot]
        self.skip += r["vote_skip"][slot]
        self.vote_timeout += r["vote_timeout"][slot]
        self.ops_fail += int(bool(r["connect_timeout"][slot] or r["disconnect_timeout"][slot]))


@dataclass
class PolicyAgg:
    overall: RoleAgg = field(default_factory=RoleAgg)
    crew: RoleAgg = field(default_factory=RoleAgg)
    imposter: RoleAgg = field(default_factory=RoleAgg)

    def add(self, r: dict, slot: int) -> None:
        self.overall.add(r, slot)
        (self.imposter if r["imposter"][slot] else self.crew).add(r, slot)


def survey(dirs: list[Path]) -> dict:
    policies: dict[str, PolicyAgg] = {}
    matrix: dict[tuple[str, str], list[int]] = {}
    episodes: list[dict] = []
    for d in dirs:
        loaded = load_episode(d)
        if not loaded:
            continue
        episode, r = loaded
        smap = slot_policy_map(episode)
        if not smap or "win" not in r:
            continue
        n = len(r["win"])
        for slot, pol in smap.items():
            if slot < n:
                policies.setdefault(pol, PolicyAgg()).add(r, slot)
        for a, b in combinations([s for s in smap if s < n], 2):
            if bool(r["imposter"][a]) == bool(r["imposter"][b]):
                continue
            for row, col, won in ((smap[a], smap[b], r["win"][a]), (smap[b], smap[a], r["win"][b])):
                m = matrix.setdefault((row, col), [0, 0])
                m[0] += int(bool(won)); m[1] += 1
        ep = flag_episode(d.name, episode, r, smap, n)
        if ep["flags"]:
            episodes.append(ep)
    return {"policies": policies, "matrix": matrix, "episodes": episodes,
            "names": sorted(policies, key=lambda p: -policies[p].overall.n)}


# ------------------------------------------------ interesting episodes + reasons

# tag -> a readable fallback sentence (the AGENT should override these via --reasons)
def auto_reason(flags: list[str]) -> str:
    f = flags[0]
    if f.startswith("crew_lost_nearly_won"):
        return "Crew all but finished its tasks and still lost — a 'should-have-won' game worth dissecting."
    if f.startswith("imposter_no_kills"):
        return "An imposter that recorded zero kills — it failed the core objective."
    if f.startswith("imposter_won_no_kills"):
        return "An imposter that won without a single kill — pure vote manipulation, worth studying."
    if f.startswith("operational_failure"):
        return "A connect/disconnect timeout (−100) — an operational crash, not a strategy fault."
    if f.startswith("no_vote_penalty"):
        return "Abstained in a meeting (−10 penalty) — it should never skip-by-timeout."
    return "Flagged as an outlier in this batch."


# surface-first ordering: rarer / more behaviourally interesting flags before the common ones
FLAG_PRIORITY = {"imposter_won_no_kills": 5, "imposter_no_kills": 4, "crew_lost_nearly_won": 3,
                 "operational_failure": 2, "no_vote_penalty": 1}


def primary_flag(ep: dict) -> str:
    return ep["flags"][0].split(" (")[0]


def select_interesting(episodes: list[dict], per_type: int = 5, cap: int = 14) -> tuple[list[dict], str]:
    """A focused, varied shortlist + a one-line summary of all flag-type counts."""
    counts: dict[str, int] = {}
    for ep in episodes:
        counts[primary_flag(ep)] = counts.get(primary_flag(ep), 0) + 1
    ordered = sorted(episodes, key=lambda e: -FLAG_PRIORITY.get(primary_flag(e), 0))
    shown: list[dict] = []
    seen: dict[str, int] = {}
    for ep in ordered:
        t = primary_flag(ep)
        if seen.get(t, 0) >= per_type or len(shown) >= cap:
            continue
        seen[t] = seen.get(t, 0) + 1
        shown.append(ep)
    summary = " · ".join(f"{c}× {t.replace('_', ' ')}" for t, c in
                         sorted(counts.items(), key=lambda kv: -kv[1]))
    return shown, summary


def flag_episode(name: str, episode: dict, r: dict, smap: dict[int, str], n: int) -> dict:
    flags: list[str] = []
    for slot, pol in smap.items():
        if slot >= n:
            continue
        imp, win, tasks, kills = r["imposter"][slot], r["win"][slot], r["tasks"][slot], r["kills"][slot]
        if r["connect_timeout"][slot] or r["disconnect_timeout"][slot]:
            flags.append(f"operational_failure ({pol})")
        elif imp and kills == 0:
            flags.append(f"imposter_no_kills ({pol})")
        elif not imp and not win and tasks >= 7:
            flags.append(f"crew_lost_nearly_won ({pol}, {tasks}/8 tasks)")
        elif imp and win and kills == 0:
            flags.append(f"imposter_won_no_kills ({pol})")
        if r["vote_timeout"][slot]:
            flags.append(f"no_vote_penalty ({pol})")
    return {"episode_dir": name, "id": episode.get("id"),
            "coworld_id": (episode.get("tags") or {}).get("coworld_id"),
            "replay_uri": episode.get("replay_url"), "flags": flags}


def mint_replay_links(episodes: list[dict]) -> None:
    """POST each flagged episode to the Observatory replay-session API -> a clickable viewer_url."""
    import httpx, softmax.auth as auth
    api = auth.get_api_server(); base = api.rstrip("/") + "/observatory"
    tok = auth.load_current_token(server=api)
    with httpx.Client(base_url=base, headers={"X-Auth-Token": tok}, timeout=60.0, follow_redirects=True) as c:
        for ep in episodes:
            if not (ep.get("coworld_id") and ep.get("replay_uri")):
                continue
            try:
                resp = c.post("/v2/coworlds/replays/session",
                              json={"coworld_id": ep["coworld_id"], "replay_uri": ep["replay_uri"]})
                resp.raise_for_status()
                ep["viewer_url"] = resp.json().get("viewer_url")
            except httpx.HTTPError as exc:
                ep["viewer_url"] = None
                print(f"  ! replay link failed for {ep['episode_dir']}: {exc}")


# ---------------------------------------------------------------- HTML render

def pct(a: int, b: int) -> str:
    return f"{100*a/b:.0f}" if b else "—"


def win_class(a: int, b: int) -> str:
    if not b:
        return ""
    v = a / b
    return "pos" if v >= 0.5 else "neg" if v < 0.30 else ""


def heat_css(v: float) -> str:
    """terracotta (0) -> paper (0.5) -> sage (1)."""
    sage, paper, terra = (110, 128, 80), (240, 235, 225), (179, 110, 78)
    lo, hi, t = (terra, paper, v / 0.5) if v < 0.5 else (paper, sage, (v - 0.5) / 0.5)
    rgb = tuple(round(lo[i] + (hi[i] - lo[i]) * t) for i in range(3))
    fg = "#23200f" if 0.30 < v < 0.78 else "#fffdf4"
    return f"background:rgb{rgb};color:{fg}"


def render_html(data: dict, title: str, highlight: str | None, reasons: dict[str, str]) -> str:
    pols, mat, names = data["policies"], data["matrix"], data["names"]
    n_eps = max((p.overall.n for p in pols.values()), default=0)
    hi = lambda name: ' class="me"' if highlight and name.startswith(highlight) else ""

    body_rows = []
    for name in names:
        a = pols[name]
        kills_g = f"{a.imposter.kills/a.imposter.n:.2f}" if a.imposter.n else "·"
        tasks_g = f"{a.crew.tasks/a.crew.n:.1f}" if a.crew.n else "·"
        score = f"{statistics.mean(a.overall.scores):.0f}" if a.overall.scores else "·"
        body_rows.append(
            f'<tr{hi(name)}><th scope="row">{name}</th>'
            f'<td class="num dim">{a.overall.n}<span class="split">{a.crew.n}c·{a.imposter.n}i</span></td>'
            f'<td class="num {win_class(a.overall.wins,a.overall.n)}"><b>{pct(a.overall.wins,a.overall.n)}</b></td>'
            f'<td class="num {win_class(a.crew.wins,a.crew.n)}">{pct(a.crew.wins,a.crew.n)}</td>'
            f'<td class="num {win_class(a.imposter.wins,a.imposter.n)}">{pct(a.imposter.wins,a.imposter.n)}</td>'
            f'<td class="num">{score}</td>'
            f'<td class="num">{kills_g}</td><td class="num">{tasks_g}</td>'
            f'<td class="num">{a.overall.voted/a.overall.n:.1f}</td>'
            f'<td class="num">{a.overall.skip/a.overall.n:.1f}</td>'
            f'<td class="num {"neg" if a.overall.vote_timeout else "dim"}">{a.overall.vote_timeout/a.overall.n:.2f}</td>'
            f'<td class="num {"neg" if a.overall.ops_fail else "dim"}">{pct(a.overall.ops_fail,a.overall.n)}</td></tr>')

    heat_head = "".join(f'<th class="vert"><span>{n.split(":")[0]}<i>:{n.split(":")[1]}</i></span></th>' for n in names)
    heat_rows = []
    for row in names:
        cells = []
        for col in names:
            if row == col:
                cells.append('<td class="diag"></td>'); continue
            m = mat.get((row, col))
            if not m or not m[1]:
                cells.append('<td class="na">·</td>')
            else:
                v = m[0] / m[1]
                cells.append(f'<td style="{heat_css(v)}" title="{row} won {m[0]}/{m[1]} vs {col}">{100*v:.0f}</td>')
        heat_rows.append(f'<tr{hi(row)}><th scope="row">{row}</th>{"".join(cells)}</tr>')

    shown, flag_summary = select_interesting(data["episodes"])
    more = len(data["episodes"]) - len(shown)
    items = []
    for ep in shown:
        reason = reasons.get(ep["episode_dir"]) or auto_reason(ep["flags"])
        tags = " · ".join(dict.fromkeys(f.split(" (")[0] for f in ep["flags"]))
        link = (f'<a class="ink-link" href="{ep["viewer_url"]}" target="_blank" rel="noopener">▸ watch replay</a>'
                if ep.get("viewer_url") else '<span class="dim">replay link not minted</span>')
        items.append(
            f'<li><p class="reason">{reason}</p>'
            f'<p class="ep"><span class="tag">{tags}</span><code>{ep["episode_dir"]}</code> {link}</p></li>')
    if not items:
        items = ['<li><p class="reason dim"><i>No Tier-1 outliers fired in this batch.</i></p></li>']

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Merriweather:wght@400;700;900&family=Merriweather+Sans:wght@400;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
  :root {{ --bg:#fffdf4; --surface:#fffaf0; --alt:#f8f6ef; --fg:#111827; --sub:#555; --muted:#999;
    --navy:#1a3875; --sage:#6e8050; --terra:#b36e4e; --gold:#d4a853; --exclusive:#8f5b3f;
    --border:#e4dac8; --border-strong:#d4c9b5; }}
  * {{ box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--fg); margin:0; padding:0;
    font:400 15px/1.6 'Merriweather Sans',sans-serif; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:40px 36px 72px; }}
  .num,code,.split,.mono {{ font-family:'IBM Plex Mono',monospace; font-feature-settings:"tnum" 1; }}
  header {{ border-bottom:2px solid var(--fg); padding-bottom:14px; margin-bottom:8px; }}
  h1 {{ font:900 30px/1.15 'Merriweather',Georgia,serif; margin:0; letter-spacing:-.01em; }}
  .eyebrow {{ font:700 11px/1 'Merriweather Sans',sans-serif; text-transform:uppercase;
    letter-spacing:.16em; color:var(--terra); margin:0 0 10px; }}
  .meta {{ font-size:13px; color:var(--sub); margin:7px 0 0; }}
  .meta b {{ color:var(--fg); }}
  h2 {{ font:700 13px/1 'Merriweather Sans',sans-serif; text-transform:uppercase; letter-spacing:.12em;
    color:var(--navy); margin:48px 0 4px; }}
  h2 .n {{ color:var(--muted); font-weight:600; letter-spacing:.04em; }}
  .sub {{ font-size:12.5px; color:var(--sub); margin:0 0 16px; border-bottom:1px solid var(--border); padding-bottom:14px; }}
  table {{ border-collapse:collapse; width:100%; }}
  .data th, .data td {{ padding:7px 9px; border-bottom:1px solid var(--border); white-space:nowrap; }}
  .data thead th {{ font:700 10px/1.3 'Merriweather Sans',sans-serif; text-transform:uppercase; letter-spacing:.07em;
    color:var(--sub); text-align:right; border-bottom:2px solid var(--border-strong); vertical-align:bottom; }}
  .data thead th:first-child {{ text-align:left; }}
  .data tbody th {{ text-align:left; font:600 13px/1.2 'IBM Plex Mono',monospace; color:var(--fg); }}
  .num {{ text-align:right; font-size:13px; }}
  td.dim, .dim {{ color:var(--muted); }}
  td.pos b, td.pos {{ color:var(--sage); }}
  td.neg b, td.neg {{ color:var(--terra); }}
  .split {{ display:block; font-size:9.5px; color:var(--muted); margin-top:1px; }}
  tbody tr:hover td, tbody tr:hover th {{ background:rgba(26,56,117,.05); }}
  tr.me th[scope=row] {{ box-shadow:inset 3px 0 0 var(--terra); padding-left:12px; }}
  tr.me td, tr.me th {{ background:#fbf6e6; }}
  /* heat map */
  .heat {{ table-layout:fixed; }}
  .heat th[scope=row] {{ font:600 12px/1.1 'IBM Plex Mono',monospace; text-align:right; padding:0 10px;
    white-space:nowrap; width:130px; }}
  .heat td {{ text-align:center; font:600 13px 'IBM Plex Mono',monospace; height:42px; border:1px solid var(--bg); }}
  .heat td.diag {{ background:repeating-linear-gradient(45deg,#efe9dd,#efe9dd 4px,#f5f1e6 4px,#f5f1e6 8px); }}
  .heat td.na {{ background:var(--alt); color:var(--muted); }}
  .heat thead th.vert {{ height:96px; vertical-align:bottom; padding-bottom:8px; }}
  .heat thead th.vert span {{ display:inline-block; writing-mode:vertical-rl; transform:rotate(180deg);
    font:600 11px 'IBM Plex Mono',monospace; color:var(--sub); }}
  .heat thead th.vert i {{ color:var(--muted); font-style:normal; }}
  .heat thead th:first-child {{ width:130px; }}
  /* interesting episodes */
  ol.finds {{ list-style:none; margin:0; padding:0; }}
  ol.finds li {{ padding:14px 0; border-bottom:1px solid var(--border); }}
  ol.finds li:first-child {{ border-top:1px solid var(--border); }}
  .reason {{ margin:0 0 5px; font-size:14.5px; color:var(--fg); }}
  .ep {{ margin:0; font-size:12px; color:var(--muted); display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  .tag {{ font:700 9.5px 'Merriweather Sans',sans-serif; text-transform:uppercase; letter-spacing:.06em;
    color:var(--exclusive); background:rgba(143,91,63,.1); padding:2px 7px; border-radius:999px; }}
  code {{ font-size:11.5px; color:var(--sub); background:var(--alt); padding:1px 5px; border-radius:3px; }}
  .ink-link {{ color:var(--navy); font-weight:600; text-decoration:none; border-bottom:1.5px solid var(--accent,#859ebe); }}
  .ink-link:hover {{ color:var(--terra); border-color:var(--terra); }}
  .note {{ font-size:11.5px; color:var(--muted); font-style:italic; margin:9px 0 0; }}
  .legend {{ display:inline-flex; align-items:center; gap:6px; font-size:11px; color:var(--sub); margin-left:4px; }}
  .legend i {{ width:34px; height:11px; border-radius:2px; display:inline-block;
    background:linear-gradient(90deg,#b36e4e,#f0ebe1,#6e8050); }}
  @media (max-width:760px) {{ .wrap {{ padding:24px 16px 56px; }} .scroll {{ overflow-x:auto; }} h1 {{ font-size:24px; }} }}
</style></head><body><div class="wrap">

<header>
  <p class="eyebrow">Crewrift · Batch Survey</p>
  <h1>{title}</h1>
  <p class="meta"><b>{n_eps}</b> episodes · <b>{len(names)}</b> policies · fast pass over
  <span class="mono">results.json</span> + <span class="mono">episode.json</span> only</p>
</header>

<h2>Per-policy stats <span class="n">— aggregated, role-split</span></h2>
<p class="sub">Win rate and per-game rates for every policy in the batch, split by role. Crewmate and
imposter are effectively different policies — read them apart.</p>
<div class="scroll"><table class="data"><thead><tr>
  <th>Policy</th><th>Games</th><th>Win%</th><th>Crew%</th><th>Imp%</th><th>Score</th>
  <th>Kills/g</th><th>Tasks/g</th><th>Voted/g</th><th>Skip/g</th><th>NoVote/g</th><th>Ops%</th>
</tr></thead><tbody>{''.join(body_rows)}</tbody></table></div>
<p class="note">Kills/g over imposter games · Tasks/g over crew games (of 8) · NoVote = vote-timeout
abstentions (−10 each) · Ops% = connect/disconnect-timeout rate (a crash, not strategy).</p>

<h2>Win heat map <span class="n">— row's side beats column's side, when opponents</span></h2>
<p class="sub">Each cell is the % of opposite-team games the <b>row</b> policy won against the
<b>column</b> policy. Hover a cell for the raw count.<span class="legend"><i></i>loss → win</span></p>
<div class="scroll"><table class="heat"><thead><tr><th></th>{heat_head}</tr></thead><tbody>{''.join(heat_rows)}</tbody></table></div>

<h2>Interesting episodes <span class="n">— where to look first</span></h2>
<p class="sub">The outliers worth opening, each with why it's worth your time and a one-click replay.
{f'<br><b>Flagged:</b> {flag_summary}.' if flag_summary else ''}</p>
<ol class="finds">{''.join(items)}</ol>
<p class="note">Showing {len(items)} of {len(data['episodes'])} flagged{f' (+{more} more in the JSON sidecar)' if more > 0 else ''}.
Tier-1 flags only (results.json) — death / ejection / chat detail needs the deep survey.</p>

</div></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path, help="Episode dir(s) or a parent of them.")
    ap.add_argument("--out", type=Path, default=Path("survey.html"))
    ap.add_argument("--title", default="Crewrift batch survey")
    ap.add_argument("--highlight", default="crewborg", help="Highlight policies whose name starts with this.")
    ap.add_argument("--mint-replays", action="store_true", help="Mint Observatory replay links for flagged episodes.")
    ap.add_argument("--reasons", type=Path, help='JSON {episode_dir: "one-line reason"} for the interesting list.')
    ap.add_argument("--json", type=Path, help="Also write the raw aggregates here.")
    args = ap.parse_args()

    dirs = find_episode_dirs(args.paths)
    if not dirs:
        raise SystemExit("No episode directories (with results.json) found.")
    data = survey(dirs)
    if args.mint_replays:
        mint_replay_links(data["episodes"])
    reasons = json.loads(args.reasons.read_text()) if args.reasons else {}

    args.out.write_text(render_html(data, args.title, args.highlight, reasons))
    # the flagged episodes (+ minted links) for you to write reasons against
    args.out.with_suffix(".interesting.json").write_text(json.dumps(
        [{"episode_dir": e["episode_dir"], "flags": e["flags"], "viewer_url": e.get("viewer_url")}
         for e in data["episodes"]], indent=1))
    print(f"{len(dirs)} episodes, {len(data['names'])} policies, {len(data['episodes'])} flagged -> {args.out}")
    if args.json:
        args.json.write_text(json.dumps({n: {"n": a.overall.n, "win": a.overall.wins,
            "crew_n": a.crew.n, "crew_win": a.crew.wins, "imp_n": a.imposter.n, "imp_win": a.imposter.wins,
            "kills": a.imposter.kills, "tasks": a.crew.tasks} for n, a in data["policies"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
