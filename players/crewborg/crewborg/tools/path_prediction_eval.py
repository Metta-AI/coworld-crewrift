#!/usr/bin/env python3
"""Score path predictions against ground truth at visible→obscured transitions.

The moment a crewmate leaves crewborg's view is exactly when prediction matters:
"where were they going?" This tool finds every such transition across one or many
episodes in a built crewrift-event-warehouse, captures the prediction *at the
instant they vanish*, then uses the replay's ground-truth ``player_state`` to score
it two complementary ways:

- **NEXT-room match** — did the predicted top destination's room equal the *first
  new room the crewmate actually entered* (``inside_room`` flips in a room != the
  onset room)? This is the fair target for "follow them to their next room", far
  more predictable than where they are 1-2 rooms later.
- **Path reward** (−1..+1) — a *decaying, hallway-weighted* agreement between the
  predicted and actual paths, aligned by arc-length from the shared onset point.
  Getting the early segment (which hallway) right is rewarded heavily; far-out
  divergence (the exact final room) is forgiven, because that is what lets us start
  a chase even when we can't name the destination.

Outputs to ``--out``: a CSV (one row per occlusion), sampled overlay PNGs (actual
path orange vs predicted weighted routes blue), and a self-contained ``report.html``
(write-up + result cards + calibration + embedded images + instance table).

Run (matplotlib is pulled in for images; duckdb is a warehouse dep):
    uv run --with matplotlib --with duckdb python \\
      crewborg/tools/path_prediction_eval.py \\
      --warehouse /tmp/xp_imp_warehouse --episodes 20 --images 40 --out /tmp/pred_eval

Knobs: ``--min-occlusion`` (default 24t, ignore blinks); ``--horizon`` (default 240t,
the window the next-room must be entered within); episode sweep is deterministic
(ORDER BY episode_id) so tuning runs are comparable.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import replay_frames as rf  # noqa: E402
from path_prediction_ui import build_nav, run_predictions  # noqa: E402


@dataclass
class Instance:
    episode_id: str
    slot: int
    policy: str
    onset_tick: int
    reacquire_tick: int | None
    horizon_tick: int
    onset_room: str | None
    pred_room: str | None
    pred_prob: float
    next_room: str | None        # first new room actually entered (the fair target)
    actual_room: str | None      # = next_room if they left, else onset_room (stay-put)
    match: bool                  # pred_room == actual_room
    changed_rooms: bool          # did they actually enter a new room
    path_reward: float | None    # decaying-weighted path agreement [-1, 1]
    endpoint_err: float | None
    pred_path: list
    actual_path: list
    pred_dest: list | None
    actual_pos: list | None


def _task_rooms(map_dict: dict) -> dict[int, str]:
    return {int(t["id"]): t.get("room", "") for t in map_dict.get("tasks", [])}


def _dest_room(label: str, task_rooms: dict[int, str]) -> str | None:
    """The room a candidate's destination sits in. ``room:Name`` -> Name;
    ``task:i:..`` -> the task's room."""

    if label.startswith("room:"):
        return label.split("room:", 1)[1]
    if label.startswith("task:"):
        parts = label.split(":")
        try:
            return task_rooms.get(int(parts[1]))
        except (ValueError, IndexError):
            return None
    return None


def _room_at(positions, tick: int, slot: int) -> str | None:
    p = positions.get(tick, {}).get(slot)
    return p[3] if p else None


def _pos_at(positions, tick: int, slot: int):
    p = positions.get(tick, {}).get(slot)
    return (p[0], p[1]) if p else None


def first_new_room(fr: rf.ReplayFrames, slot: int, onset: int, end: int, onset_room: str | None):
    """The first room the target actually ENTERS (inside_room True) that differs from
    ``onset_room``, scanning [onset, end]. Returns (room, tick) or (None, None) if they
    never leave the onset room within the window. This is the 'next room' — the fair
    target for 'follow them to their next room', far more predictable than where they
    are 1-2 rooms later."""

    for t in fr.ticks:
        if t < onset or t > end:
            continue
        p = fr.positions.get(t, {}).get(slot)
        if not p:
            continue
        _x, _y, alive, room, inside = p
        if inside and room and room != onset_room:
            return room, t
    return None, None


# --- decaying-reward path accuracy ------------------------------------------------
# Getting the HALLWAY right early matters most (it's what lets us start chasing); the
# far end of a long prediction is unknowable, so weight early agreement heavily and
# forgive late divergence.
PATH_DECAY_LEN = 110.0   # arc-length (px) scale of the early-agreement weight
PATH_ERR_SCALE = 45.0    # error (px) at which agreement reward crosses zero
PATH_SAMPLE_PX = 12.0    # sampling step along the paths
PATH_MAX_ARC = 420.0     # don't score beyond this far out


def _arc_table(path):
    cum = [0.0]
    for a, b in zip(path, path[1:]):
        cum.append(cum[-1] + ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)
    return cum


def _at_arc(path, cum, arc):
    if arc <= 0 or len(path) == 1:
        return path[0]
    if arc >= cum[-1]:
        return path[-1]
    import bisect
    i = bisect.bisect_left(cum, arc)
    a, b = path[i - 1], path[i]
    seg = cum[i] - cum[i - 1]
    t = (arc - cum[i - 1]) / seg if seg > 0 else 0.0
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def path_score(actual_path, pred_path) -> float | None:
    """Decaying-weighted agreement between the actual and predicted paths, aligned by
    arc-length from their shared onset point. +1 ≈ perfectly co-located early; → negative
    if the predicted route goes down the wrong hallway right away. Far-out divergence is
    down-weighted (we can't predict the final room, only the hallway)."""

    import math
    if len(actual_path) < 2 or len(pred_path) < 2:
        return None
    ca, cp = _arc_table(actual_path), _arc_table(pred_path)
    max_arc = min(PATH_MAX_ARC, ca[-1])
    if max_arc < PATH_SAMPLE_PX:
        return None
    num = den = 0.0
    s = 0.0
    while s <= max_arc:
        ap = _at_arc(actual_path, ca, s)
        pp = _at_arc(pred_path, cp, s)
        err = math.dist(ap, pp)
        reward = max(-1.0, min(1.0, 1.0 - err / PATH_ERR_SCALE))  # +1 close, 0 at scale, -1 far
        w = math.exp(-s / PATH_DECAY_LEN)
        num += w * reward
        den += w
        s += PATH_SAMPLE_PX
    return num / den if den else None


def occlusion_instances(fr: rf.ReplayFrames, frames: list[dict], slot: int,
                        min_occlusion: int, horizon: int) -> list[Instance]:
    """Find visible→obscured transitions for ``slot`` and score each."""

    task_rooms = _task_rooms(fr.map)
    by_tick = {f["tick"]: f for f in frames}
    seen = fr.visible.get(slot, set())
    ticks = fr.ticks
    policy = fr.players.get(slot, {}).get("policy") or "?"

    out: list[Instance] = []
    prev_seen = False
    onset = None
    for t in ticks:
        s = t in seen and (fr.positions.get(t, {}).get(slot, (0, 0, False, ""))[2])
        if prev_seen and not s:
            onset = t  # they just left view (last visible was the prior tick)
        elif not prev_seen and s and onset is not None:
            # re-acquired at t; close the occlusion that began at `onset`
            self_close(out, fr, by_tick, slot, policy, onset, t, min_occlusion, horizon, task_rooms)
            onset = None
        prev_seen = s
    if onset is not None:  # occluded through end of episode
        self_close(out, fr, by_tick, slot, policy, onset, None, min_occlusion, horizon, task_rooms)
    return out


def self_close(out, fr, by_tick, slot, policy, onset, reacquire, min_occlusion, horizon, task_rooms):
    end = reacquire if reacquire is not None else fr.ticks[-1]
    if end - onset < min_occlusion:
        return
    # The prediction we held the instant they vanished: the frame at `onset-?` —
    # use the last frame at/just-before onset (onset is the first occluded tick, so
    # the prediction from the previous visible tick is what we carry in).
    onset_frame = by_tick.get(onset)
    if onset_frame is None or not onset_frame.get("candidates"):
        return
    top = onset_frame["candidates"][0]
    pred_room = _dest_room(top["label"], task_rooms)
    pred_prob = top["prob"]

    horizon_tick = min(end, onset + horizon)
    onset_room = _room_at(fr.positions, onset, slot)
    # The FAIR target: the first new room they actually enter (not where they are at a
    # far horizon). Scored room = that, or the onset room if they never left.
    next_room, next_room_tick = first_new_room(fr, slot, onset, horizon_tick, onset_room)
    actual_room = next_room if next_room is not None else onset_room
    # Actual path runs to whichever is later useful: through the next-room entry (so
    # the path score sees the hallway they took), capped at the horizon.
    path_end = min(horizon_tick, next_room_tick) if next_room_tick is not None else horizon_tick
    actual_pos = _pos_at(fr.positions, path_end, slot)

    actual_path = [(_pos_at(fr.positions, t, slot)) for t in fr.ticks
                   if onset <= t <= path_end and _pos_at(fr.positions, t, slot)]
    reward = path_score(actual_path, top["path"])

    # predicted coasted endpoint at path_end (where the predictor thinks they are then).
    end_frame = by_tick.get(path_end)
    endpoint_err = None
    if end_frame and end_frame.get("candidates") and actual_pos:
        pp = end_frame["candidates"][0]["pred"]
        endpoint_err = ((pp[0] - actual_pos[0]) ** 2 + (pp[1] - actual_pos[1]) ** 2) ** 0.5

    out.append(Instance(
        episode_id=fr.episode_id, slot=slot, policy=policy,
        onset_tick=onset, reacquire_tick=reacquire, horizon_tick=horizon_tick,
        onset_room=onset_room, pred_room=pred_room, pred_prob=pred_prob,
        next_room=next_room, actual_room=actual_room,
        match=(pred_room is not None and pred_room == actual_room),
        changed_rooms=(next_room is not None),
        path_reward=reward, endpoint_err=endpoint_err,
        pred_path=top["path"], actual_path=actual_path,
        pred_dest=top["path"][-1] if top["path"] else None, actual_pos=actual_pos,
    ))


def render_image(inst: Instance, map_dict: dict, onset_candidates: list, out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.set_facecolor("#0e1116")
    fig.patch.set_facecolor("#0e1116")
    for r in map_dict.get("rooms", []):
        ax.add_patch(plt.Rectangle((r["x"], r["y"]), r["w"], r["h"], fill=False, edgecolor="#39424f", lw=0.7))
        ax.text(r["x"] + 3, r["y"] + 12, r["name"], color="#55606b", fontsize=6)

    # predicted top-k paths, weighted by prob
    for c in onset_candidates[:6]:
        p = c["path"]
        if len(p) >= 2:
            xs, ys = zip(*p)
            ax.plot(xs, ys, color="#58a6ff", alpha=max(0.08, c["prob"]), lw=1 + 3 * c["prob"])
    # actual path
    if len(inst.actual_path) >= 2:
        xs, ys = zip(*inst.actual_path)
        ax.plot(xs, ys, color="#f0883e", lw=2.0, label="actual")
    # markers
    if inst.actual_path:
        ax.scatter(*inst.actual_path[0], c="#ffffff", s=40, zorder=5, label="onset (left view)")
    if inst.pred_dest:
        ax.scatter(*inst.pred_dest, c="#58a6ff", marker="D", s=55, zorder=5, label=f"pred dest [{inst.pred_room}]")
    if inst.actual_pos:
        ax.scatter(*inst.actual_pos, c="#f0883e", marker="*", s=120, zorder=5, label=f"actual end [{inst.actual_room}]")

    ok = "MATCH" if inst.match else "miss"
    ax.set_title(f"{inst.episode_id[:14]} slot{inst.slot}({inst.policy}) "
                 f"t{inst.onset_tick}→{inst.horizon_tick}  pred={inst.pred_room}({inst.pred_prob:.2f}) "
                 f"actual={inst.actual_room}  [{ok}]",
                 color="#e6edf3", fontsize=8)
    ax.set_xlim(0, map_dict["width"]); ax.set_ylim(map_dict["height"], 0)
    ax.set_aspect("equal"); ax.axis("off")
    ax.legend(loc="upper right", fontsize=6, facecolor="#161b22", labelcolor="#e6edf3", framealpha=0.8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, facecolor="#0e1116")
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--episode", help="A single episode_id; omit to sweep --episodes from the warehouse.")
    ap.add_argument("--episodes", type=int, default=10, help="How many episodes to sweep when --episode is absent.")
    ap.add_argument("--min-occlusion", type=int, default=24)
    ap.add_argument("--horizon", type=int, default=240)
    ap.add_argument("--images", type=int, default=30, help="Number of instances to render as PNGs (sampled).")
    ap.add_argument("--out", default="/tmp/pred_eval")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    import duckdb
    con = duckdb.connect()
    if args.episode:
        episode_ids = [args.episode]
    else:
        rows = con.execute(
            "SELECT DISTINCT episode_id FROM "
            f"read_parquet('{args.warehouse}/episode_players.parquet') ORDER BY episode_id LIMIT {args.episodes}"
        ).fetchall()
        episode_ids = [r[0] for r in rows]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_instances: list[Instance] = []
    onset_cands: dict[int, list] = {}  # id(instance) -> onset candidate list (for images)
    maps: dict[str, dict] = {}
    for eid in episode_ids:
        try:
            fr = rf.load(args.warehouse, eid)
        except SystemExit:
            continue
        nav, md = build_nav(fr)
        maps[eid] = fr.map
        # predict for every non-crewborg crew target (skip slot 0 = us, skip imposters)
        for slot, info in fr.players.items():
            if slot == 0 or info.get("role") != "crew":
                continue
            frames = run_predictions(fr, nav, md, slot)
            by_tick = {f["tick"]: f for f in frames}
            insts = occlusion_instances(fr, frames, slot, args.min_occlusion, args.horizon)
            for inst in insts:
                onset_cands[id(inst)] = by_tick.get(inst.onset_tick, {}).get("candidates", [])
            all_instances.extend(insts)
        print(f"  {eid[:18]}…: {len([i for i in all_instances if i.episode_id==eid])} instances", file=sys.stderr)

    scored = [i for i in all_instances if i.pred_room is not None]
    changed = [i for i in scored if i.changed_rooms]
    n = len(scored)

    def rate(items):
        m = sum(1 for i in items if i.match)
        return m, len(items), (100 * m / len(items) if items else 0.0)

    overall = rate(scored)
    changed_rate = rate(changed)
    buckets = []
    for lo, hi in [(0.0, 0.4), (0.4, 0.7), (0.7, 1.01)]:
        buckets.append(((lo, hi), rate([i for i in scored if lo <= i.pred_prob < hi])))
    errs = sorted(i.endpoint_err for i in scored if i.endpoint_err is not None)
    med_err = errs[len(errs) // 2] if errs else None
    rewards = sorted(i.path_reward for i in scored if i.path_reward is not None)
    med_reward = rewards[len(rewards) // 2] if rewards else None
    changed_rewards = sorted(i.path_reward for i in changed if i.path_reward is not None)
    med_reward_changed = changed_rewards[len(changed_rewards) // 2] if changed_rewards else None

    print("\n===== path-prediction accuracy at visible→obscured transitions =====")
    print(f"episodes: {len(episode_ids)}  occlusion instances (scored): {n}")
    if n:
        print(f"NEXT-ROOM MATCH (all):           {overall[0]}/{overall[1]} = {overall[2]:.1f}%")
        print(f"NEXT-ROOM MATCH (changed rooms): {changed_rate[0]}/{changed_rate[1]} = {changed_rate[2]:.1f}%  "
              "(they actually left the room — the cases that matter)")
        if med_reward is not None:
            print(f"PATH REWARD (decaying, hallway-weighted) median: {med_reward:+.2f} all / "
                  f"{(med_reward_changed if med_reward_changed is not None else 0):+.2f} changed  (range -1..+1)")
        if med_err is not None:
            print(f"endpoint error px: median {med_err:.0f}  p90 {errs[int(len(errs)*0.9)]:.0f}")
        for (lo, hi), (m, b, r) in buckets:
            if b:
                print(f"  pred_prob [{lo:.1f},{hi:.1f}): {m}/{b} = {r:.0f}%")

    # CSV
    csv_path = out_dir / "instances.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["episode_id", "slot", "policy", "onset_tick", "horizon_tick", "occlusion_len",
                    "onset_room", "pred_room", "pred_prob", "next_room", "actual_room",
                    "changed_rooms", "match", "path_reward", "endpoint_err"])
        for i in scored:
            w.writerow([i.episode_id, i.slot, i.policy, i.onset_tick, i.horizon_tick,
                        i.horizon_tick - i.onset_tick, i.onset_room, i.pred_room, round(i.pred_prob, 3),
                        i.next_room, i.actual_room, int(i.changed_rooms), int(i.match),
                        round(i.path_reward, 3) if i.path_reward is not None else "",
                        round(i.endpoint_err) if i.endpoint_err else ""])
    print(f"\nraw rows -> {csv_path}")

    # sampled images (balance matches and misses among room-changers where possible)
    image_files: list[tuple[str, Instance]] = []
    if args.images and scored:
        rng = random.Random(args.seed)
        pool = changed if len(changed) >= args.images else scored
        sample = rng.sample(pool, min(args.images, len(pool)))
        img_dir = out_dir / "images"
        img_dir.mkdir(exist_ok=True)
        for k, inst in enumerate(sorted(sample, key=lambda i: (not i.match, i.onset_tick))):
            tag = "match" if inst.match else "MISS"
            fname = f"{k:03d}_{tag}_{inst.episode_id[:10]}_s{inst.slot}_t{inst.onset_tick}.png"
            render_image(inst, maps[inst.episode_id], onset_cands.get(id(inst), []), img_dir / fname)
            image_files.append((fname, inst))
        print(f"{len(image_files)} overlay images -> {img_dir}")

    # self-contained HTML report
    report_path = out_dir / "report.html"
    write_report(report_path, out_dir, len(episode_ids), n, overall, changed_rate, buckets,
                 med_err, med_reward, med_reward_changed, scored, image_files, args)
    print(f"report -> {report_path}")
    return 0


def write_report(path, out_dir, n_episodes, n, overall, changed_rate, buckets, med_err,
                 med_reward, med_reward_changed, scored, image_files, args) -> None:
    import base64

    def pct_bar(r):
        return f'<div class="bar"><i style="width:{r:.0f}%"></i></div>'

    rows = "".join(
        f"<tr class='{'m' if i.match else 'x'}'><td>{i.episode_id[:12]}…</td><td>{i.slot}</td>"
        f"<td>{i.policy}</td><td>{i.onset_room or '—'}</td><td>{i.pred_room or '—'}</td>"
        f"<td>{i.pred_prob:.2f}</td><td>{i.actual_room or '—'}</td>"
        f"<td>{'yes' if i.changed_rooms else 'no'}</td><td>{'✓' if i.match else '✗'}</td>"
        f"<td>{('%+.2f'%i.path_reward) if i.path_reward is not None else '—'}</td></tr>"
        for i in sorted(scored, key=lambda i: (i.episode_id, i.onset_tick))
    )
    bucket_rows = "".join(
        f"<tr><td>pred_prob [{lo:.1f},{hi:.1f})</td><td>{m}/{b}</td><td>{pct_bar(r)}</td><td class='p'>{r:.0f}%</td></tr>"
        for (lo, hi), (m, b, r) in buckets if b
    )
    imgs = ""
    for fname, inst in image_files:
        b64 = base64.b64encode((out_dir / "images" / fname).read_bytes()).decode()
        cls = "match" if inst.match else "miss"
        rew = f" · path {inst.path_reward:+.2f}" if inst.path_reward is not None else ""
        imgs += (f"<figure class='{cls}'><img src='data:image/png;base64,{b64}'>"
                 f"<figcaption>{'MATCH' if inst.match else 'MISS'} · {inst.policy} · "
                 f"{inst.onset_room}→{inst.actual_room} · pred {inst.pred_room} ({inst.pred_prob:.2f}){rew}</figcaption></figure>")
    rew_all = f"{med_reward:+.2f}" if med_reward is not None else "—"
    rew_chg = f"{med_reward_changed:+.2f}" if med_reward_changed is not None else "—"

    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Path-prediction eval</title>
<style>
 body{{font:14px/1.5 ui-sans-serif,system-ui,sans-serif;background:#0e1116;color:#e6edf3;margin:0;padding:24px;max-width:1100px;}}
 h1{{font-size:20px;}} h2{{font-size:15px;color:#8b96a5;border-bottom:1px solid #283040;padding-bottom:4px;margin-top:28px;}}
 .big{{font-size:30px;font-weight:700;color:#58a6ff;}} .sub{{color:#8b96a5;}}
 .cards{{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0;}}
 .card{{background:#161b22;border:1px solid #283040;border-radius:8px;padding:14px 18px;min-width:200px;}}
 table{{border-collapse:collapse;width:100%;font-size:12px;margin-top:8px;}}
 th,td{{padding:4px 8px;border-bottom:1px solid #21262d;text-align:left;}} th{{color:#8b96a5;}}
 td.p{{text-align:right;color:#58a6ff;font-weight:600;}}
 tr.x td{{background:#1c1416;}} tr.m td{{background:#13201a;}}
 .bar{{height:7px;background:#0b0f14;border-radius:4px;width:90px;}} .bar>i{{display:block;height:100%;background:#58a6ff;border-radius:4px;}}
 figure{{margin:0 0 18px;background:#161b22;border:1px solid #283040;border-radius:8px;padding:8px;}}
 figure.miss{{border-color:#5a2a2a;}} figure.match{{border-color:#2a5a3a;}}
 figure img{{width:100%;border-radius:4px;}} figcaption{{font-size:12px;color:#8b96a5;padding:6px 4px 2px;}}
 .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;}}
 details summary{{cursor:pointer;color:#58a6ff;}}
</style></head><body>
<h1>Path-prediction accuracy — visible→obscured transitions</h1>
<p class="sub">{n_episodes} episode(s) · {n} scored occlusions · min-occlusion {args.min_occlusion}t · horizon {args.horizon}t ·
 warehouse <code>{args.warehouse}</code></p>

<h2>Results</h2>
<div class="cards">
  <div class="card"><div class="big">{overall[2]:.0f}%</div><div class="sub">NEXT-room match (all {overall[1]})</div></div>
  <div class="card"><div class="big">{changed_rate[2]:.0f}%</div><div class="sub">next-room match when they CHANGED rooms ({changed_rate[1]})<br>— the cases that matter for "follow to next room"</div></div>
  <div class="card"><div class="big">{rew_all}</div><div class="sub">path reward (median, −1..+1)<br>{rew_chg} on room-changers</div></div>
  <div class="card"><div class="big">{('%.0f'%med_err) if med_err is not None else '—'}px</div><div class="sub">median endpoint error</div></div>
</div>
<p>Two complementary metrics:</p>
<ul>
<li><b>Next-room match</b> — did the predicted top destination's room equal the <b>first new room the
  crewmate actually entered</b> (not where they are 1–2 rooms later)? This is the fair target for "follow
  them to their next room". The <b>changed-rooms</b> figure isolates occlusions where they genuinely left.</li>
<li><b>Path reward</b> (−1..+1) — a <b>decaying, hallway-weighted</b> agreement between the predicted and
  actual paths: getting the early segment (which hallway they took) right is rewarded heavily; far-out
  divergence (which specific room at the end) is forgiven. +1 ≈ rode the same corridor; negative ≈ went
  down the wrong hallway immediately. This is the signal that matters for <i>starting</i> a chase.</li>
</ul>

<h2>Calibration — next-room match rate by prediction confidence</h2>
<p class="sub">A useful predictor is right more often when it is confident. Watch this stay monotonic as the module is tuned.</p>
<table><tr><th>confidence</th><th>n</th><th></th><th></th></tr>{bucket_rows}</table>

<h2>Representative instances</h2>
<p class="sub">Orange = actual path · blue = predicted routes (opacity ∝ probability) · white dot = left view · ◇ predicted dest · ★ actual end.</p>
<div class="grid">{imgs}</div>

<h2>All scored instances</h2>
<details><summary>show table ({n} rows)</summary>
<table><tr><th>episode</th><th>slot</th><th>policy</th><th>onset room</th><th>pred room</th><th>prob</th><th>next room</th><th>changed</th><th>match</th><th>path</th></tr>{rows}</table>
</details>
</body></html>"""
    path.write_text(html)


if __name__ == "__main__":
    raise SystemExit(main())
