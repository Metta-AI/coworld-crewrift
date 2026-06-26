---
name: map-navigation
description: Navigation techniques for scripted policies that move an agent through a map — building a nav graph over a walkability grid, A* with line-of-sight smoothing, clearance margins, destination anchors, special edges (teleports/portals/vents), reachability checks, momentum/bang-bang motion control with predictive stop, route re-rooting, and stuck detection/recovery. Use when a policy must reach points on a map and pathing/arrival is a failure source.
---

# Map Navigation

For scripted policies on a spatial map, "go to point P" is the most common
primitive and a frequent silent failure: the agent wedges on a wall, overshoots
with momentum, targets a point inside an obstacle, or commits to a stale route.
This skill is the set of **navigation techniques** that make movement reliable,
plus how to instrument them so navigation failures show up in the optimizer loop.

This is a **specialty skill**: load it when the game has movement on a map. It
pairs with `spatial-temporal-analysis` (analyzing where/when the agent went) and
with `data-collection-design` (logging route/arrival signals). Keep all of this
in the **action/transport layer**, never in the decision layer — modes/strategy
emit a symbolic "go to P" intent; *how to get there over time* lives here.

## The layering rule

Separate **what** from **how**:

- decision layer (modes/strategy) emits a symbolic intent — `navigate_to(P)`,
  `complete_objective(O)`, `flee_from(E)` — and never touches buttons, routes, or
  momentum;
- action layer (this skill) owns route planning, motion control, and timing, and
  is **stateful across ticks** (it remembers the active route + progress cursor).

This keeps decisions testable and keeps navigation bugs isolated to one seam.

## 1. Build a nav graph (once per episode, not per tick)

If the game exposes a walkability/collision grid, bake a graph from it once:

- **Coarsen for speed, validate at full resolution.** Plan over coarse cells (e.g.
  8px) so A* is cheap, but decide node/edge validity against the **true
  pixel/tile mask** so the coarse approximation never invents or discards a path.
- **Nodes:** a cell is routable iff it contains a *reachable* walkable pixel; put
  the node at the reachable pixel nearest the cell center (so a cell that only
  clips a corridor still routes).
- **Edges:** join neighbors whose connecting segment is fully walkable; forbid
  diagonal corner-squeezing.
- **Reachability flood:** compute reachable cells by a flood from spawn on the
  true mask — ground truth, immune to a thin wall passing through a coarse cell.
  An unreachable destination should fail **loud at bake time**, not as a silent
  mid-game stall.
- **Clearance margin:** plan on an *eroded* mask (a pixel is "clear" iff its
  `(2r+1)²` box is walkable) so routes run down corridor centers instead of
  grazing walls — momentum + axis-aligned control otherwise drifts into a grazed
  wall and wedges. Keep edges/reachability on the **true** mask so tight passages
  and wall-adjacent goals stay reachable; only the final hop onto a goal is
  un-inflated.

If the game gives structured navmesh/waypoints instead of a grid, use those
directly — the principles (validate, clearance, reachability, anchors) carry
over.

## 2. Destination anchors (never target a rect center blindly)

A destination is often a region (an objective station, a button, a portal mouth),
and its geometric center can sit inside a wall or outside interaction range.
**Precompute, per static destination, a reachable point that satisfies the
interaction condition** (inside the rect; within range of the trigger). Navigate
to that anchor, but keep the *trigger gate* on the true condition (e.g. real
interaction range), so arrival and activation are both correct. Dynamic targets
(a moving entity, a body) use their live position with no anchor.

## 3. Special edges (teleports / portals / vents)

Maps with non-walking connectivity (teleporters, portals, same-group vents) need
**directed graph edges** between the reachable anchors of each linked pair. Make
them **role/intent-gated** if only some agents may use them (e.g. imposter-only
vents) so other routes are unaffected. A route that uses a special edge walks
onto the entry anchor, fires the transit action (gated on the real transit
range), then resumes walking from the exit — carry which legs are transits in the
route state.

## 4. Motion control (momentum / bang-bang with predictive stop)

If movement is momentum-based (acceleration, friction, max speed, sub-pixel
scaling) rather than instantaneous, raw "press toward target" overshoots:

- use a **bang-bang controller** on each axis: drive toward the waypoint, then
  **release/brake early** using a predictive stop (estimate stopping distance from
  current velocity + friction) so the agent settles on the point instead of
  oscillating around it;
- suppress movement when the intent requires standing still (e.g. holding an
  interact button that a d-pad input would cancel);
- treat speed/accel/friction/scale as **constants to read from the game**, not to
  guess.

## 5. Route following + re-rooting (don't commit to a stale path)

- Follow the planned polyline with a progress cursor; advance when the current
  waypoint is reached within tolerance.
- **Re-root the route at the agent's live position** on a fixed interval and
  whenever the goal changes, so the follower never sticks to a line it has
  drifted off. A* is sub-millisecond on a baked graph, so replanning is nearly
  free and eliminates approach-wedging.
- **String-pull / line-of-sight smooth** the path: skip intermediate waypoints
  when a clear (eroded-mask) segment exists to a later one, for straighter, faster
  travel.

## 6. Stuck detection & recovery

Navigation fails quietly without an explicit watchdog:

- detect **no progress** (position barely changed over K ticks while a route is
  active) and **route-completion-without-arrival** (cursor exhausted but not at
  the goal);
- on stuck: re-root/replan, nudge off the wall, or fall back to a coarser target;
- if a destination has **no reachable anchor**, report it once at bake time and
  let the decision layer pick another goal — never spin forever.

## Instrument navigation for the optimizer loop

Navigation failures must be visible in artifacts so the loop can act on them
(`data-collection-design`). Log, joinable to the ground-truth clock:

- per-tick position, current mode/intent, and the active goal point;
- route events: `route_planned` (goal, length, has-transit-leg), `replan`,
  `arrived`, `stuck`, `unreachable_destination`;
- counters: replans/episode, stuck ticks, mean approach error, time-to-arrival.

Then `spatial-temporal-analysis` can heatmap dwell, draw the route vs the actual
trajectory (time-gradient polyline), and surface where agents wedge — turning a
vague "it paths badly late game" into a located, falsifiable hypothesis.

## Common failure patterns (hypothesis seeds)

- targets a rect center inside a wall → never arrives (fix: anchors, §2);
- overshoots and oscillates on a point → momentum, no predictive stop (§4);
- commits to a stale route after drifting → no re-rooting (§5);
- grazes a wall in a corridor and wedges → no clearance margin (§1);
- silently idles at an unreachable goal → no reachability check / stuck watchdog
  (§1, §6).

## Integration

- **Pairs with:** `spatial-temporal-analysis` (analyze/visualize the movement),
  `data-collection-design` (log route/arrival signals), `replay-artifact-analysis`
  (join routes to ground-truth outcomes).
- **Feeds:** `policy-hypothesis-loop` (navigation hypotheses tie to one knob:
  clearance radius, replan interval, stop-distance gain, anchor selection).
- **Game-specific grounding:** see `games/<game>/skills/...` (e.g. Crewrift's
  walkability bake, vent teleport edges, and momentum constants).
