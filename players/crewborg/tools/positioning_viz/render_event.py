"""Render kill-ready positioning graphics to PNG (headless / agent-viewable).

WHAT THIS IS
------------
The same picture as the browser viewer (``server.py`` + ``index.html``), but drawn
with matplotlib to a PNG file so a coding agent (or a headless box) can ``Read`` it.
For a kill-ready event — an imposter's ``kill_cooldown`` hitting 0, see
``extract_positions.py`` — it draws the focal imposter's path P ticks back → ready →
forward to its next kill (or F ticks), everyone else's recent path, and the kill
marker, on the real map.

HOW TO USE IT
-------------
Point it at a per-tick event warehouse (built with ``--snapshot-every 1``; see
``extract_positions.py`` and the ``crewrift-event-warehouse`` skill for the build).

    # one event of a specific episode (event index into that episode's ready_events)
    python render_event.py /tmp/wh --episode <episode_id> --event 0 --past 150 --future 50 -o /tmp/e.png

    # the Nth ready event for a policy, scanned across every episode in the warehouse
    python render_event.py /tmp/wh --find crewborg --nth 0 -o /tmp/c0.png

    # a montage: the first N ready events for a policy in one grid
    python render_event.py /tmp/wh --montage crewborg --count 12 -o /tmp/crewborg_grid.png

    # just list a policy's ready events (no render)
    python render_event.py /tmp/wh --find crewborg --list

``--find`` / ``--montage`` take a policy *name* (e.g. ``crewborg``); ``us`` is a
convenience alias for the ``--us-policy`` value.

HOW TO EDIT IT
--------------
- **What the picture contains** is all in ``draw_event`` (background footprint,
  rooms/tasks/vents/button, past/future paths, ready-moment dots, kill marker,
  title). Add or change a layer there.
- **Colours / who is highlighted** is ``_color`` — it keys "us" off
  ``extract_positions.US_POLICY``; pass ``--us-policy`` to change it.
- **The window forward** (``until_kill``) uses the event's meeting-aware
  ``window_end`` from ``extract_positions`` — don't recompute it here.
- The data shape (tracks, ready_events) is owned by ``extract_positions.py``; read
  its docstring before changing what is drawn.
"""

from __future__ import annotations

import argparse
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (must follow matplotlib.use)

import extract_positions as ex  # noqa: E402


def _track(data: dict, slot: int) -> list:
    """A player's per-tick track, tolerating int- or str-keyed ``tracks`` dicts."""
    return data["tracks"].get(slot) or data["tracks"].get(str(slot)) or []


def _color(slot: int, role: str, policy: str, focal: int) -> str:
    """Colour for a player: focal=crimson, us=green, other imposter=orange, crew=blue."""
    if slot == focal:
        return "crimson"
    if policy == ex.US_POLICY:
        return "#23c172"
    return "darkorange" if role == "imposter" else "#3a86d8"


def _xy(track: list, a: int, b: int) -> tuple[list, list]:
    """x/y arrays over [a, b] with NaN breaks at meeting ticks (sample[4] = playing),
    so the polyline never draws the teleport-to-Bridge jump during a meeting."""
    xs, ys = [], []
    for p in track:
        if p[0] < a or p[0] > b:
            continue
        if len(p) > 4 and not p[4]:
            xs.append(float("nan"))
            ys.append(float("nan"))
        else:
            xs.append(p[1])
            ys.append(p[2])
    return xs, ys


def _at(track: list, t: int):
    """The track sample nearest tick ``t`` (per-tick warehouse => exact)."""
    best, bd = None, 1e9
    for p in track:
        d = abs(p[0] - t)
        if d < bd:
            bd, best = d, p
    return best


def draw_event(ax, data: dict, ev: dict, past: int, future: int,
               until_kill: bool = True, others_future: bool = False, labels: bool = True) -> None:
    """Draw one kill-ready event onto a matplotlib axis.

    ``past`` / ``future`` are tick windows; when ``until_kill`` the forward window is
    the event's meeting-aware ``window_end`` (the kill, or the interrupting meeting)
    instead of ``T + future``. ``others_future`` also draws non-focal forward paths;
    ``labels`` toggles per-player name labels (off for montage cells).
    """
    m = data["map"]
    ax.set_facecolor("#0f1420")
    # navigable footprint: where any player walked this game (rooms AND hallways)
    fx, fy = [], []
    for tr in data["tracks"].values():
        for p in tr:
            fx.append(p[1])
            fy.append(p[2])
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

    for p in data["players"]:
        tr = _track(data, p["slot"])
        col = _color(p["slot"], p["role"], p["policy"], focal)
        xs, ys = _xy(tr, T - past, T)
        if xs:
            ax.plot(xs, ys, color=col, lw=2.4 if p["slot"] == focal else 1.1,
                    alpha=0.9 if p["slot"] == focal else 0.45)
        if others_future and p["slot"] != focal:
            ofx, ofy = _xy(tr, T, fut_end)
            if ofx:
                ax.plot(ofx, ofy, color=col, lw=0.8, alpha=0.3, ls=":")
    # focal future (bold dashed)
    ftr = _track(data, focal)
    ffx, ffy = _xy(ftr, T, fut_end)
    if ffx:
        ax.plot(ffx, ffy, color="#ff6a6a", lw=2.8, ls="--", alpha=0.95)
    # dots at the ready moment
    for p in data["players"]:
        pt = _at(_track(data, p["slot"]), T)
        if not pt:
            continue
        alive = pt[3]
        is_us = p["policy"] == ex.US_POLICY
        col = _color(p["slot"], p["role"], p["policy"], focal) if alive else "#666"
        ax.scatter([pt[1]], [pt[2]], s=70 if p["slot"] == focal else 38, color=col, zorder=5,
                   edgecolors="white" if is_us else "none", linewidths=1.2)
        if labels:
            lbl = ("★ " if is_us else "") + p["label"]
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
    ax.set_title(f"{ev['label']}  ready@{T}  hunt {idle}t (~{idle / 24:.1f}s) → {out}", color="#e8eef8", fontsize=8)
    ax.set_xlim(0, m["width"])
    ax.set_ylim(0, m["height"])
    ax.invert_yaxis()
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal")


def find_events(warehouse: str, policy: str) -> list[tuple]:
    """Every ready event for ``policy`` across all episodes.

    Returns a list of ``(episode_id, event_index, ev, data)``. ``policy`` is matched
    against the event's ``policy`` field; ``us`` is an alias for ``US_POLICY``.
    """
    pol = ex.US_POLICY if policy.lower() == "us" else policy
    out = []
    for e in ex.list_episodes(warehouse):
        if pol not in e["imposters"]:
            continue
        data = ex.extract_replay(warehouse, e["episode_id"])
        for i, ev in enumerate(data["ready_events"]):
            if ev["policy"] == pol:
                out.append((e["episode_id"], i, ev, data))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Render Crewrift kill-ready positioning graphics to PNG.")
    ap.add_argument("warehouse", help="Built event warehouse dir (per-tick / --snapshot-every 1).")
    ap.add_argument("--episode", help="Episode id (with --event, render that episode's Nth ready event).")
    ap.add_argument("--event", type=int, default=0, help="Ready-event index within --episode (default 0).")
    ap.add_argument("--find", help="Policy name: scan all episodes for its ready events (use with --nth/--list).")
    ap.add_argument("--nth", type=int, default=0, help="Which --find event to render (default 0).")
    ap.add_argument("--montage", help="Policy name: render a grid of --count of its ready events.")
    ap.add_argument("--count", type=int, default=12, help="Events in a --montage grid (default 12).")
    ap.add_argument("--past", type=int, default=150, help="Past window in ticks (default 150).")
    ap.add_argument("--future", type=int, default=50, help="Future window in ticks when not until-kill (default 50).")
    ap.add_argument("--no-until-kill", action="store_true", help="Use --future instead of the kill/meeting window.")
    ap.add_argument("--others-future", action="store_true", help="Also draw non-focal players' forward paths.")
    ap.add_argument("--list", action="store_true", help="With --find: list the matching events, don't render.")
    ap.add_argument("--us-policy", default=ex.US_POLICY, help=f"Policy to treat as 'us' (default: {ex.US_POLICY}).")
    ap.add_argument("-o", "--out", default="/tmp/posviz_event.png", help="Output PNG path.")
    a = ap.parse_args()

    ex.US_POLICY = a.us_policy

    if a.montage:
        evs = find_events(a.warehouse, a.montage)[: a.count]
        n = len(evs)
        cols = min(4, n) or 1
        rows = math.ceil(n / cols)
        fig, axes = plt.subplots(rows, cols, figsize=(cols * 4.2, rows * 3.0))
        axes = axes.ravel() if n > 1 else [axes]
        for ax, (eid, i, ev, data) in zip(axes, evs):
            draw_event(ax, data, ev, a.past, a.future, not a.no_until_kill, a.others_future, labels=False)
        for ax in list(axes)[n:]:
            ax.axis("off")
        fig.suptitle(f"{a.montage}: first {n} kill-ready events (past {a.past} / future {a.future})", color="#1c2330")
        fig.tight_layout()
        fig.savefig(a.out, dpi=110, facecolor="white")
        print(f"wrote {a.out}  ({n} events)")
        return

    if a.find:
        evs = find_events(a.warehouse, a.find)
        if a.list:
            print(f"{len(evs)} ready events for {a.find}:")
            for eid, i, ev, _ in evs[:60]:
                ttk = ev["next_kill_tick"] - ev["tick"] if ev.get("next_kill_tick") else None
                print(f"  {eid[:18]} ev{i} t={ev['tick']} {'kill+' + str(ttk) if ttk else 'no-kill'}")
            return
        if not evs:
            raise SystemExit(f"no ready events for policy {a.find!r} in {a.warehouse}")
        eid, i, ev, data = evs[a.nth]
    else:
        if not a.episode:
            raise SystemExit("give --episode, --find, or --montage")
        data = ex.extract_replay(a.warehouse, a.episode)
        ev = data["ready_events"][a.event]

    fig, ax = plt.subplots(figsize=(11, 6))
    draw_event(ax, data, ev, a.past, a.future, not a.no_until_kill, a.others_future)
    fig.tight_layout()
    fig.savefig(a.out, dpi=120, facecolor="white")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
