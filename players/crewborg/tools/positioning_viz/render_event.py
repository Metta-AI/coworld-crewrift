"""Render kill-ready positioning graphics to PNG (so a coding agent can view them too).

Same picture as the web viewer, but matplotlib -> file: for a kill-ready event (an imposter's
kill_cooldown hitting 0) it draws the focal imposter's path past P ticks -> ready -> future F
ticks (or until next kill), everyone else's past P ticks, on the map, with the kill marked.

Examples:
  # one event
  python render_event.py /tmp/v50_pertick --episode EREQ --event 0 --past 150 --future 50 -o /tmp/e.png
  # find the Nth ready event for a policy (across episodes) and render it
  python render_event.py /tmp/v50_pertick --find crewborg --nth 0 -o /tmp/c0.png
  # montage: first N ready events for a policy in one grid
  python render_event.py /tmp/v50_pertick --montage crewborg --count 12 -o /tmp/crewborg_grid.png
  # list events
  python render_event.py /tmp/v50_pertick --find crewborg --list
"""

from __future__ import annotations

import argparse
import math
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import extract_positions as ex

POLICY_ALIASES = {"crewborg": "crewborg", "us": "crewborg", "aaron": "crewborg-aaln",
                  "crewborg-aaln": "crewborg-aaln", "andre": "truecrew", "truecrew": "truecrew"}


def _color(slot, role, policy, focal):
    if slot == focal:
        return "crimson"
    if policy == "crewborg":
        return "#23c172"
    return "darkorange" if role == "imposter" else "#3a86d8"


def _pts(track, a, b):
    return [p for p in track if a <= p[0] <= b]


def _xy(track, a, b):
    """x/y arrays over [a,b] with NaN breaks at meeting ticks (p[4] is the Playing flag),
    so the polyline never draws the teleport-to-Bridge jump during a meeting."""
    xs, ys = [], []
    for p in track:
        if p[0] < a or p[0] > b:
            continue
        if len(p) > 4 and not p[4]:
            xs.append(float("nan")); ys.append(float("nan"))
        else:
            xs.append(p[1]); ys.append(p[2])
    return xs, ys


def _at(track, t):
    best, bd = None, 1e9
    for p in track:
        d = abs(p[0] - t)
        if d < bd:
            bd, best = d, p
    return best


def draw_event(ax, data, ev, past, future, until_kill=True, others_future=False, labels=True):
    m = data["map"]
    ax.set_facecolor("#0f1420")
    # navigable footprint: where any player walked this game (rooms AND hallways)
    fx, fy = [], []
    for tr in data["tracks"].values():
        for p in tr:
            fx.append(p[1]); fy.append(p[2])
    if fx:
        ax.scatter(fx, fy, s=1.0, color="#a0b4d2", alpha=0.10, linewidths=0)
    # rooms
    for r in m.get("rooms", []):
        ax.add_patch(plt.Rectangle((r["x"], r["y"]), r["w"], r["h"], fill=False, edgecolor="#8aa6cc66", lw=0.8))
        ax.text(r["x"] + 4, r["y"] + 14, r.get("name", ""), color="#aabbdd80", fontsize=6)
    # tasks (green crew stations), vents (orange imposter routes), button (yellow)
    for t in m.get("tasks", []):
        ax.add_patch(plt.Rectangle((t["x"] - 2, t["y"] - 2), 4, 4, color="#46d27880"))
    for v in m.get("vents", []):
        ax.add_patch(plt.Polygon([(v["x"], v["y"] - 5), (v["x"] + 5, v["y"]), (v["x"], v["y"] + 5), (v["x"] - 5, v["y"])],
                                 color="#ff963cdd"))
    if m.get("button"):
        b = m["button"]
        ax.add_patch(plt.Circle((b["x"], b["y"]), 6, fill=False, edgecolor="#ffd24a", lw=1.5))

    T, focal = ev["tick"], ev["slot"]
    # meeting-aware forward window: to the kill / interrupting meeting, not across it
    fut_end = ev["window_end"] if (until_kill and ev.get("window_end")) else T + future
    players = {p["slot"]: p for p in data["players"]}

    for p in data["players"]:
        tr = data["tracks"].get(str(p["slot"])) or data["tracks"].get(p["slot"]) or []
        col = _color(p["slot"], p["role"], p["policy"], focal)
        xs, ys = _xy(tr, T - past, T)
        if xs:
            ax.plot(xs, ys, color=col, lw=2.4 if p["slot"] == focal else 1.1,
                    alpha=0.9 if p["slot"] == focal else 0.45)
        if others_future and p["slot"] != focal:
            fx, fy = _xy(tr, T, fut_end)
            if fx:
                ax.plot(fx, fy, color=col, lw=0.8, alpha=0.3, ls=":")
    # focal future
    ftr = data["tracks"].get(str(focal)) or data["tracks"].get(focal) or []
    fx, fy = _xy(ftr, T, fut_end)
    if fx:
        ax.plot(fx, fy, color="#ff6a6a", lw=2.8, ls="--", alpha=0.95)
    # dots at ready moment
    for p in data["players"]:
        tr = data["tracks"].get(str(p["slot"])) or data["tracks"].get(p["slot"]) or []
        pt = _at(tr, T)
        if not pt:
            continue
        alive = pt[3]
        col = _color(p["slot"], p["role"], p["policy"], focal) if alive else "#666"
        ax.scatter([pt[1]], [pt[2]], s=70 if p["slot"] == focal else 38, color=col, zorder=5,
                   edgecolors="white" if p["policy"] == "crewborg" else "none", linewidths=1.2)
        if labels:
            lbl = ("★" if p["policy"] == "crewborg" else "") + p["label"].replace("Us (crewborg)", "Us")
            ax.text(pt[1] + 8, pt[2] + 4, lbl, color="#dfe6f2", fontsize=6.5,
                    fontweight="bold" if p["slot"] == focal else "normal")
    # kill marker by focal within window (location = killer's spot at the kill tick)
    for k in data["kills"]:
        if k["killer"] == focal and k["x"] is not None and T <= k["tick"] <= fut_end:
            ax.scatter([k["x"]], [k["y"]], s=320, color="#ff2828", alpha=0.30, zorder=5, linewidths=0)
            ax.scatter([k["x"]], [k["y"]], marker="X", s=240, color="white", edgecolors="black",
                       linewidths=1.6, zorder=6)
            ax.text(k["x"] + 12, k["y"] - 10, f"KILL t={k['tick']}", color="#ff9a9a", fontsize=7, fontweight="bold")

    idle = ev.get("idle_ready_ticks", 0)
    out = f"KILL@{ev['kill_tick']}" if ev.get("converted") else ev.get("ended_by", "?")
    ax.set_title(f"{ev['label']}  ready@{T}  hunt {idle}t (~{idle/24:.1f}s) → {out}", color="#e8eef8", fontsize=8)
    ax.set_xlim(0, m["width"]); ax.set_ylim(0, m["height"]); ax.invert_yaxis()
    ax.set_xticks([]); ax.set_yticks([]); ax.set_aspect("equal")


def find_events(wh, policy):
    """All ready events for a policy across episodes: list of (episode_id, event_index, ev)."""
    pol = POLICY_ALIASES.get(policy.lower(), policy)
    out = []
    for e in ex.list_episodes(wh):
        if not any(ex.PLAYER_LABEL.get(pol, pol) == lbl for lbl in e["imposters"]) and \
           not any(lbl in (ex.PLAYER_LABEL.get(pol, pol),) for lbl in e["imposters"]):
            # cheap pre-filter by label; still extract to be sure
            if ex.PLAYER_LABEL.get(pol) not in e["imposters"]:
                continue
        data = ex.extract_replay(wh, e["episode_id"])
        for i, ev in enumerate(data["ready_events"]):
            if ev["policy"] == pol:
                out.append((e["episode_id"], i, ev, data))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("warehouse")
    ap.add_argument("--episode"); ap.add_argument("--event", type=int, default=0)
    ap.add_argument("--find", help="policy: crewborg/aaron/andre — pick across episodes")
    ap.add_argument("--nth", type=int, default=0)
    ap.add_argument("--montage", help="policy: grid of --count events")
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--past", type=int, default=150); ap.add_argument("--future", type=int, default=50)
    ap.add_argument("--no-until-kill", action="store_true"); ap.add_argument("--others-future", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-o", "--out", default="/tmp/posviz_event.png")
    a = ap.parse_args()

    if a.montage:
        evs = find_events(a.warehouse, a.montage)[: a.count]
        n = len(evs); cols = min(4, n) or 1; rows = math.ceil(n / cols)
        fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.2, rows * 3.0))
        axes = (axes.ravel() if n > 1 else [axes])
        for ax, (eid, i, ev, data) in zip(axes, evs):
            draw_event(ax, data, ev, a.past, a.future, not a.no_until_kill, a.others_future, labels=False)
        for ax in list(axes)[n:]:
            ax.axis("off")
        fig.suptitle(f"{a.montage}: first {n} kill-ready events (past {a.past} / future {a.future})", color="#1c2330")
        fig.tight_layout(); fig.savefig(a.out, dpi=110, facecolor="white"); print(f"wrote {a.out}  ({n} events)")
        return

    if a.find:
        evs = find_events(a.warehouse, a.find)
        if a.list:
            print(f"{len(evs)} ready events for {a.find}:")
            for eid, i, ev, _ in evs[:60]:
                ttk = ev["next_kill_tick"] - ev["tick"] if ev.get("next_kill_tick") else None
                print(f"  {eid[:18]} ev{i} t={ev['tick']} {'kill+' + str(ttk) if ttk else 'no-kill'}")
            return
        eid, i, ev, data = evs[a.nth]
    else:
        data = ex.extract_replay(a.warehouse, a.episode)
        ev = data["ready_events"][a.event]

    fig, ax = plt.subplots(figsize=(11, 6))
    draw_event(ax, data, ev, a.past, a.future, not a.no_until_kill, a.others_future)
    fig.tight_layout(); fig.savefig(a.out, dpi=120, facecolor="white"); print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
