---
name: pattern-toolkit
description: "Use to shape crewborg eval data so a behavior pattern becomes recognizable: heuristics, cheesy patterns, and after-the-fact visualizations (grids/heatmaps/timelines) fed back into the LLM. Trigger on 'find patterns', 'visualize the behavior', 'make a heatmap', 'what is crewborg doing wrong', or 'shape the data so I can see the pattern'."
---

# Pattern & Heuristic Toolkit

## Purpose

A pattern is only as findable as the *shape* of the data you put it in. This is a
toolkit of ways to **construct data so a pattern is recognized** — heuristics over
the joined artifact+replay data, cheesy quick-look patterns, and visualizations
written **from the data after the fact** (never during the game) that get fed back
into the LLM for interpretation. The loop quality depends on this: "then you need
good data."

**Announce at start:** "Shaping the eval data to surface patterns: I'll run the
quick heuristics, build the relevant view (heatmap/timeline/matrix), and read it
back for hypothesis candidates."

## Inputs

The joined records from `artifact-capture` + `replay-reconstruction`:
`positions` (24 Hz `self_x/y`, `room_id`, `mode`, `intent_kind`, `phase`,
`visible`), `domain.*` events, ground-truth replay events, and roles-by-color.

## 1. Cheesy patterns (cheap, run these first)

Fast scalar/count look-ups that flag an obvious problem before any plotting.
Compute per role and per version:

- **Outcome splits** — imp/crew win rate, who-ejected-whom, self-ejection count.
- **Tempo** — meetings/episode + trigger mix (report vs button), tick of meeting 2
  (median), kills/episode, time-to-first-kill.
- **Vote accuracy** — crewmate non-skip votes landing on a real imposter vs a
  crewmate (`vi/vc` in `analyze.py`); led-accusation accuracy.
- **Activity** — tasks/episode (crewmate), vent hops (imposter), fraction of ticks
  in each mode (e.g. "follower-response fired on 14% of crewmate ticks" was a
  cheesy red flag in `FINDINGS_v4.md`).
- **Degenerate behaviors** — ticks idle at spawn, route thrash (mode flips/min),
  stuck penalties.

A cheesy pattern that diverges between versions is the cheapest hypothesis seed.

## 2. Heuristics (derived predicates over the log)

Materialize human-readable predicates by querying the event log + life status,
not by storing new event kinds (mirror crewborg's own "raw observations, derived
interpretations" rule, `design.md` §5.2):

- "followed X who then died" = a `proximity`/shadow interval toward a color whose
  `roster[color]` is `dead`.
- "faked a task convincingly" = total `task`-rect dwell with no real completion.
- "killed where a witness could see" = `kill_landed` with another live player in
  `visible` that tick.
- "fled a real imposter vs a crewmate" = Flee onset color × ground-truth role.

Each heuristic is a function over the joined data; a new one is code, not a schema
change.

## 3. Visualizations (after the fact, for the LLM)

Write images **from the collected data** and feed them back in. Normal
data-vis — grids and pictures — not rigidly spatial/temporal:

- **Position heatmap** — bin `positions.self_x/self_y` into the map grid; overlay
  baked rooms/tasks/vents. "Where does this version spend time?"
- **Encounter / kill geometry** — plot kills + the victim/witness positions from
  `visible` at the kill tick. "Are kills happening in sightlines?"
- **Mode timeline** — `mode`/`intent_kind` vs `server_tick`, with replay events
  (kills/meetings) marked. "What was it doing when the kill it missed happened?"
- **Vote/accusation matrix** — voter color × target color, colored by truth.
- **Occupancy vs reality** — crewborg's expected-crew grid vs actual positions
  from other slots' artifacts.

The `viewer/` (`CREWBORG_TRACE=viewer`) renders agent-perspective replays over
the map directly from `viewer_*` records — use it to eyeball a single suspicious
episode before building aggregate plots.

## 4. Spatial-temporal gap (known limitation — flag it)

The current toolkit leans on **static** grids/pictures, which **flatten ordering
and timing**. This is the loop's biggest open gap: spatial/temporal analysis is
not done as rigorously as it could be. When a pattern is inherently
sequential-spatial — intercept geometry over time, who-converged-on-whom-then-a-
death, room flow, lead/lag of two trajectories — a static heatmap will hide it.

In those cases:

- prefer **sequence-preserving** views (trajectory polylines with time gradient,
  event-anchored windows around a kill, per-tick deltas) over a summed grid;
- say explicitly that an **adaptive / sequential** analysis would help and that
  the static view may be lossy here, rather than over-reading a heatmap.

This honesty is part of the loop: don't manufacture a spatial-temporal conclusion
from a tool that can't support it.

## Output

A ranked list of **pattern candidates**, each with: the view/heuristic that
revealed it, the version/role split, and a one-line "what it suggests." Hand the
ranked list to `hypothesis-generation`.

## Integration

- **Consumes:** `artifact-capture`, `replay-reconstruction`.
- **Feeds:** `hypothesis-generation`.
- **Grounded in:** `episode_data/eval_2026-06-11_v3_vs_v8/analyze.py`,
  `episode_data/FINDINGS_v4.md`, `design.md` §5.2 / §10.2 / §11, `viewer/`.
