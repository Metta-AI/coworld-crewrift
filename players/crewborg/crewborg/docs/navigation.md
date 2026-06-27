# Navigation

How crewborg gets from where it is to where it wants to be. This is the spatial
substrate the whole agent stands on: a static map parsed from the game's resource
file, an A\* navigation graph built over the decoded walkability mask, an offline
bake that ships that graph as a vendored asset, route planning over the graph, and
a momentum-aware controller that turns a route into d-pad input.

This document is the cross-cutting narrative. For the structural spec see
[`../design.md`](../design.md) (§6 the static map / nav graph, §9 the action
sequencing, §12 the movement controller). For orientation and setup see
[`../README.md`](../README.md).

Navigation is pure geometry. It holds no game state, makes no strategic choices,
and never decides *where* to go — it only answers "given a goal point, how do I
reach it, and what buttons do I hold this tick to make progress." Which modes pick
goals and why lives in [`./imposter-play.md`](./imposter-play.md) and
[`./crewmate-play.md`](./crewmate-play.md).

---

## The pipeline at a glance

```
                          croatoan.resources  (vendored CSS-like rect file)
                                   |
                map/parser.py:load_resource_rects   -> list[ResourceRect]
                                   |
                map/bake.py:bake_map                 -> map.types.MapData
                                   |   (tasks/vents/rooms/button, world-pixel rects)
                                   |
   stream ---> walkability mask (bool HxW)           [decoded elsewhere: see
   (Sprite-v1)         |                              perception-and-belief.md]
                       v
   nav.py:build_nav_graph(walkability, map_data)     -> nav.NavGraph
        (coarse cells + pixel-validated edges + reachability + anchors + vent edges)
                       |
                       |  built ONCE per episode; normally LOADED, not built:
                       v
   navbake.py:load_navbake(walkability)  <-- map/croatoan_navbake.pkl.gz (offline bake)
                       |
                       v
   nav.py:plan_route / plan_route_via_vents          -> list[Point] world waypoints
                       |
                       v
   action.py:_navigate_mask -> _movement_mask -> _axis_input   -> held button bitmask
        (bang-bang d-pad + predictive-stop release within the momentum stop distance)
```

Two distinct geometries flow into the graph. The **static map** (vent / button /
room / task rectangles) is parsed from a file the engine ships and the Sprite-v1
stream never sends. The **walkability mask** (which pixels an agent may stand on)
*is* decoded from the stream. The graph is the join of the two: pathfinding over
the mask, with the map's rectangles turned into known-good destination anchors.

---

## 1. The static map

### Source: a CSS-like rectangle file

The geometry crewborg needs but never receives over the wire — where the vents,
emergency button, rooms, and task stations sit — lives in the game's own map
resource file. crewborg vendors a copy, `map/croatoan.resources`, and parses it at
startup (`map/__init__.py` docstring). The file is a flat list of named blocks in a
CSS-like grammar:

```
/* vent4 */
position: absolute;
width: 14px;
height: 14px;
left: 733px;
top: 334px;
background: rgba(255, 0, 0, 0.4);
```

A `/* name */` single-line block comment opens a block; `width` / `height` /
`left` / `top` (pixels) and a `background` / `border` color property fill it in;
any other line is ignored.

### Parsing — `map/parser.py`

`map/parser.py:load_resource_rects` is a faithful behavioral port of the engine's
Nim reader (`croatoan.resources` is the wire format the engine ships maps in, so
the property names, color grammar, and keep/drop rules are a contract, not a local
choice). It walks the text line by line accumulating a `_Draft`:

- `_parse_block_name` recognizes a `/* name */` header; a header **finalizes the
  previous block** and starts a new one.
- `_split_property` splits `key: value`; `width`/`height` set `w`/`h`, `left`/`top`
  set `x`/`y` (all via `_parse_px`, which requires a `<n>px` value).
- `_parse_color` accepts `#rrggbb`, `rgb()`, `rgba()` (with the alpha read as a
  0–1 fraction *or* a 0–255 int), or a `#rrggbb` embedded in a `border` shorthand.

`_finalize` appends a `ResourceRect` **only if the block is complete**: it has a
name, all four bounds, a color, and strictly positive width and height. Incomplete
or degenerate blocks are silently dropped (not errored). A malformed property value
raises `ResourceError` annotated with the 1-based line number. The result is a flat
`list[ResourceRect]` **in file order** — and that order is load-bearing.

### Classification — `map/bake.py`

`map/bake.py:bake_map` (a port of the engine's `sim.nim`) sorts the flat rect list
into typed geometry. Classification is purely by name:

| Rule (`_name_key` = trimmed, lowercased) | Bucket |
| --- | --- |
| name is exactly `task` (`_is_task`) | a `TaskStation` |
| name starts with `vent` but isn't `vents` (`_is_vent`) | a `Vent` |
| any other non-empty name, excluding the `vents`/`tasks`/`rooms` legends (`_is_room`) | a `Room` |

The typed output is `map/types.py:MapData` — frozen, `extra="forbid"` pydantic
models in **world (map) pixels**, the same coordinate space the walkability mask
validates against. It carries `width`/`height` (default croatoan `1235×659`),
`tasks`, `vents`, `rooms`, the derived `button`, and `home`.

`load_croatoan_map` reads the vendored resource via `importlib.resources` and bakes
it; `__init__.build_runtime` calls it at startup and stores the result on
`belief.map`.

### The file-order = task-index invariant

This is the single most important contract in the static map. The perception
stream refers to a task by an integer index (`3000`/`7000` + `idx`). `bake_map`
preserves the **file order** of `task` rects when building `MapData.tasks`, so the
task at `tasks[i]` *is* the task the stream calls index `i`
(`map/types.py:TaskStation` docstring, `map/bake.py:bake_map`). Reordering the
rects, or changing what `_is_task` matches, would silently shift every task index
and send crewborg to the wrong stations. Task *names* (`Task near <nearest room>`,
via `_nearest_room_name`) are cosmetic — they only make logs readable; a task's
identity is its index.

### Vents and teleport groups

Each `Vent` records its rectangle plus a `group` and a `group_index`. The group is
`_vent_group_char`: the vent's **last ASCII-alphanumeric character** (`vent a` →
`a`, `vent4` → `4`). Vents that share a group character teleport together.
`group_index` is the 1-based ordinal of the vent within its group, in bake order.
The croatoan file contains repeated `/* vent4 */` blocks at different locations —
those are the members of one teleport group.

### Home and the emergency button

`MapData.home` is the **center of the `bridge` room** (falling back to the first
room, then the map center). It is the meeting / emergency-button anchor and the
seed for the reachability flood (below). `MapData.button` is *derived*, not parsed:
`_centered_rect` places a fixed `BUTTON_WIDTH`×`BUTTON_HEIGHT` (`28×34`) rectangle
centered on `home`, clamped inside the map.

### Validating the map against the server

When the streamed walkability mask arrives, `map/bake.py:walkability_matches`
checks that its `(width, height)` equals the baked map's. A mismatch means the
server is running a different map than the one crewborg baked. The frozen,
`extra="forbid"` models give the same guarantee for the asset: a stale prebaked
`MapData` schema fails to load loudly rather than silently dropping a field.

---

## 2. The A\* navigation graph — `nav.py`

`nav.py:build_nav_graph` turns the walkability mask (plus the baked `MapData`) into
a `NavGraph`: a coarse cell graph for fast A\*, with **correctness enforced at pixel
resolution** rather than at the coarse approximation.

The key fact driving every design choice here: Crewrift collides the player as a
**1×1 point** (`sim.nim` `CollisionW = CollisionH = 1`). *Every walkable pixel is a
legal agent position.* The coarse grid exists only to keep A\* fast on the full
~`1235×659` map — it must never be allowed to gate reachability.

### Pixel-level primitives

- **`_pixel_walkable`** — bounds-checked lookup into the mask.
- **`_segment_clear`** — an Amanatides–Woo grid DDA at pixel resolution: walks
  every pixel a straight segment `a→b` passes through and requires all walkable. A
  segment crossing a pixel corner exactly requires *both* flanking pixels walkable
  — the **no-corner-cutting rule**, so a route can never squeeze diagonally between
  two blocked pixels. This is the soundness guarantee for both A\* edges and the
  line-of-sight smoother.
- **`_clearance_mask`** — a box erosion: pixels whose full `(2·radius+1)²` box is
  walkable, i.e. `CLEARANCE_RADIUS = 2` px clear of any wall. Map-edge pixels count
  as non-clear.
- **`_flood_reachable_pixels`** — an 8-connected flood from a seed over walkable
  pixels, where a diagonal step is allowed only if at least one of its two
  orthogonal neighbors is walkable (mirroring the engine's per-axis
  slide-or-block stepping). This produces ground-truth reachability.

### Nodes, edges, reachability

```
build_nav_graph(walkability, map_data):
  reachable_pixels = _flood_reachable_pixels(walkability, home)   # 1x1 flood from spawn
  clearance        = _clearance_mask(walkability, CLEARANCE_RADIUS=2)
  node_point       = _build_nodes(reachable_pixels, clearance, cell_size=8)
  adjacency        = _build_edges(walkability, node_point)        # validated on TRUE mask
  reachable        = set(node_point)                              # every node is reachable
  _build_anchors(graph, map_data)                                # tasks/vents/button + vent edges
```

- **Nodes** (`_build_nodes` → `_cell_node_point`): one node per `cell_size = 8`
  pixel cell that contains a reachable pixel. The node's *point* is the reachable
  pixel **nearest the cell center, preferring one that also keeps clearance** — so
  node-to-node travel runs down corridor centers, but a cell that is mostly wall
  yet clips a corridor still becomes a routable node. (This is what a naive
  "all-pixels-walkable" cell rule threw away, and why tasks tucked against a wall
  used to look unreachable.)
- **Edges** (`_build_edges`): an edge connects two 8-neighbor nodes iff
  `_segment_clear` on the **true walkability mask** says the segment between their
  points is fully walkable with no corner squeeze. Edge cost is Euclidean distance.
  Edges are enumerated over four "forward" offsets (`_FORWARD`) and added
  symmetrically.
- **Reachability**: the flood is seeded from `home` (the spawn). Because every node
  sits on a reachable pixel by construction, `graph.reachable` is just the node
  set. `nearest_reachable_node` snaps a world point to the closest reachable cell
  via an expanding-ring search (`_spiral_nearest`, up to `_SNAP_RADIUS_CELLS = 48`).

The separation of masks is the central invariant (and the module docstring's
warning): **A\* edges and the spawn flood use the TRUE walkability mask**, so tight
passages and wall-adjacent destinations stay reachable; the **clearance (eroded)
mask only steers node placement, the clear-shot short-circuit, and route
string-pulling** for control margin. Letting the coarse grid or clearance gate
reachability reintroduces the wall-adjacent-task false-unreachable bug.

### Destination anchors

A rect center can sit inside a wall, so navigation never targets a rect center
blindly. `_build_anchors` precomputes, for every baked task / vent / button, the
**reachable, routable walkable pixel that satisfies the destination's interaction
condition** (`_find_anchor`). A pixel qualifies when it is walkable, satisfies the
interaction predicate, lies in a reachable node cell, *and* has clear line of sight
from that node's point (so the agent can actually drive the final hop onto it). Of
the qualifying pixels, the one nearest the target wins.

| Destination | Window | Predicate |
| --- | --- | --- |
| task | the task rect | inside the rect (default) |
| vent | `VENT_REACH = 16` px box around center | within `VENT_REACH` of center |
| button | the button rect | inside the rect (default) |

Anchors are stored as `task_anchors` / `vent_anchors` / `button_anchor` and exposed
via `task_anchor(i)` / `vent_anchor(i)`. A destination with **no** reachable anchor
is collected into `NavGraph.unreachable` and logged with a build-time warning — so
an unreachable task surfaces on frame 1 instead of as a silent mid-game stall.

### Vent teleport edges (imposter-only)

`_build_vent_edges` adds a `VentEdge` between every ordered pair of *same-group*
vents whose anchors are both reachable (a lone reachable vent in its group
teleports nowhere useful and is skipped). A `VentEdge` records the from/to vent
indices, their graph cells (the A\* endpoints), the reachable anchors the route
walks onto either side of the hop, and a fixed `cost = VENT_EDGE_COST` (=
`DEFAULT_CELL_SIZE`). Because a vent use is one action regardless of how far it
jumps, the cost is small and fixed, so A\* strongly prefers a vent whenever it
shortcuts a long walk — which is what makes a fleeing imposter vanish through the
nearest useful vent.

Vent edges are keyed by the entry vent's anchor cell in `NavGraph.vent_edges` and
are consulted **only** by `plan_route_via_vents`. Ordinary walking routes
(`plan_route`) never traverse them, so crewmate pathing is completely unaffected by
their presence.

### `NavGraph` shape

`NavGraph` is built once per episode and treated as immutable thereafter. It
carries the exact `walkability` mask it was built from (the freshness key the
offline bake validates against), the `cell_size`/`rows`/`cols` grid dimensions,
`node_point`, `adjacency`, `reachable`, the `clearance` mask, the anchor maps,
`vent_edges`, and `unreachable`. `world_to_cell` maps a world pixel to its
(clamped) cell.

---

## 3. The offline nav bake — `navbake.py`

The nav graph and the occupancy substrate (see
[`./agent-tracking.md`](./agent-tracking.md)) are pure functions of the static
walkability mask, but building them is a heavy pure-Python pass: a pixel flood over
the ~`1235×659` mask, per-pixel node/edge construction, and an O(anchors²) A\* sweep
for the substrate polylines. At the hosted 250m-CPU budget this first-tick build
costs ~14s — which freezes the agent at spawn while the real-time 24 Hz engine
streams ahead, leaving the agent to drain a stale backlog.

Because there is exactly **one static map** (croatoan), crewborg bakes both
artifacts **once, offline**, into a vendored asset and loads it at runtime. The
asset is `map/croatoan_navbake.pkl.gz`: a gzip-pickled
`{"format", "nav", "substrate"}` payload (`navbake.py:serialize_navbake`,
`NAVBAKE_FORMAT = 1`).

### The load / validate / fallback contract

`navbake.py:load_navbake(walkability)` is the runtime entry point, called from
`types.py:update_belief` on the first tick that carries a walkability mask:

```python
# types.update_belief (first tick with a mask)
if belief.nav is None and percept.walkability is not None:
    baked = load_navbake(percept.walkability)
    if baked is not None:
        belief.nav, belief.agent_tracking.substrate = baked   # fast path
    else:
        belief.nav = build_nav_graph(percept.walkability, map_data=belief.map)  # live fallback
```

The load path is **fail-safe by construction** — every failure mode collapses to
`None` so the runtime falls back to the live build and never crashes on a bad,
absent, or stale asset:

1. `_read_payload` returns `None` on a missing file, a gzip/pickle error, a wrong
   `format`, or version skew that breaks unpickling (a blanket `except`).
2. `load_navbake` returns `None` if `nav` or `substrate` is absent.
3. The **freshness guard**: the baked `NavGraph` carries the exact walkability mask
   it was built from, so `load_navbake` validates by direct comparison — same shape
   **and** same pixels (`np.array_equal`). Any difference (a redeployed or
   different map) returns `None` → live rebuild, and the mismatch *is* the signal to
   re-run the bake tool.

The principle: **correctness never depends on the asset; only startup latency
does.** When the asset is fresh, the first tick is a fast load and the substrate is
delivered alongside the graph; when it is missing or stale, the graph is rebuilt
live (and the substrate then builds lazily as before).

### Producing the asset — `tools/nav_bake.py`

The offline bake is a developer tool, `tools/build/nav_bake.py`, run only
when the league redeploys a changed map. It is two steps:

1. **`extract-walkability`** — pull the authoritative mask crewborg actually sees.
   A local Gate-1 episode run with `CREWBORG_CAPTURE_WALKABILITY=1` prints one
   bit-packed JSON `walkability_capture` line to the policy log; this command
   decodes it (`np.unpackbits`) into a `.npy` mask.
2. **`bake`** — load that mask, `load_croatoan_map()`, then
   `build_nav_graph(mask, map_data=...)` and `build_occupancy_substrate(...)`,
   `serialize_navbake` the pair, and write `map/croatoan_navbake.pkl.gz`. It prints
   per-stage timing (the first-tick cost being removed) and warns about any
   unreachable destinations.

`NAVBAKE_FORMAT` is bumped whenever the serialized payload shape changes, so old
assets are ignored rather than mis-loaded. Re-baking requires rebuilding the player
image so the new asset ships.

---

## 4. Route planning — `nav.py`

Two planners turn a `(start, goal)` world-pixel pair into a list of world
waypoints. Both short-circuit and both string-pull; they differ only in whether
vent teleports are allowed.

### `plan_route` (walking)

```
plan_route(graph, start_world, goal_world):
  if _segment_clear(path_mask, start, goal): return [goal_world]     # clear shot
  start_cell = nearest_reachable_node(start)
  goal_cell  = nearest_reachable_node(goal)
  ... A* over graph.adjacency, heuristic = straight-line to goal point ...
  return _reconstruct(...)                                           # smoothed waypoints
```

1. **Clear-shot short-circuit**: if a clearance-keeping straight line already
   reaches the goal (`_segment_clear` on `_path_mask` — the eroded mask, falling
   back to walkability), return `[goal_world]` and skip A\* entirely.
2. Otherwise snap start and goal into the reachable component and run A\* over
   `adjacency`, with the heuristic being straight-line distance to the goal node's
   point. An empty result means **genuinely unreachable**.
3. `_reconstruct` builds the cell path, replaces the final cell point with the
   exact `goal_world` (so the follower drives onto the real target — a task anchor
   or a dynamic kill target that may itself sit just off a node), prepends the
   agent's real start position, and string-pulls.

`_smooth_route` is the string-pull: a greedy line-of-sight pass that keeps
extending from the current anchor to the furthest waypoint still in clear
pixel-level line of sight, committing the last visible waypoint as a corner when
the next is occluded. Adjacent graph waypoints are mutually visible by
construction, so progress is guaranteed, and the exact goal is always kept. The
result is a staircase collapsed into straight runs.

### `plan_route_via_vents` (imposter flee)

Identical to `plan_route`, except `_astar_via_vents` may also traverse the graph's
vent teleport edges, so the cheapest route to a far point can vanish through a vent
instead of walking the long way around. It returns `(waypoints, teleports)` where
`teleports` maps the index of a waypoint *reached by venting* to the vent index the
agent must stand on and press B to reach it. The cell path is split into
walk-segments at each teleport boundary; each walk-segment is string-pulled on its
own (a teleport boundary is never smoothed across — its two anchors aren't mutually
visible), and the hop's exit anchor is marked as a teleport waypoint.

This is the only planner that reads `vent_edges`, and only the `escape` intent uses
it.

---

## 5. From route to motion — `action.py`

`action.py` is the final stage of the stack (modes → action). It is **mechanism,
not policy**: it executes the symbolic `Intent` a mode hands it and makes no
strategic choices. `resolve_action` is stateful across ticks via `ActionState`: a
changed intent discards in-progress execution (`_reset_execution` — route, cursor,
goal, teleport map, vote/chat latches); an unchanged intent continues it.

### Following a route — `_navigate_mask`

Every world-relative intent (`navigate_to`, `escape`, and the navigate phase of
`complete_task` / `report` / `kill` / `vent` / `call_meeting`) routes through one
follower, `_navigate_mask`:

1. **Replan on change or on a timer.** The route is (re)planned when the goal
   changes *and* periodically every `REPLAN_INTERVAL = 8` ticks, re-rooting the
   route at the agent's live position. A\* is ~0.2ms, so frequent replanning is
   effectively free, and it keeps the follower from committing to a stale route
   after drifting off the planned line (the residual cause of task-approach
   wedging: a fresh route from where the agent *actually is* routes around the wall
   it was mashing into). With no nav graph yet, it steers straight at the goal;
   with `via_vents`, it calls `plan_route_via_vents`; otherwise `plan_route`. An
   empty route means genuinely unreachable → **hold still** (a stall the mode can
   react to) rather than steer into a wall.
2. **Advance the cursor** past every waypoint already reached (within
   `WAYPOINT_RADIUS = 8` px), including a teleport target once a hop has dropped the
   agent next to it.
3. **Fire a teleport** if the cursor sits on a teleport waypoint and the agent
   isn't there yet (`_teleport_mask`): press B (level-triggered) once within
   `VENT_RANGE_SQ = 256` (`VentRange = 16`px) of the vent center, otherwise keep
   steering onto the entry anchor so the press lands.
4. Otherwise drive toward the current waypoint with `_movement_mask`.

### The momentum model — bang-bang + predictive stop

The engine applies friction-based momentum (the stopping distance is roughly
`v·fr/(1-fr)` with `fr = 144/256`, i.e. ≈`1.29·v`). A naive "hold the d-pad until
you're on the target" controller overshoots and oscillates. crewborg instead uses a
**bang-bang controller with a predictive-stop release**, computed per axis
independently in `_axis_input`:

```python
def _axis_input(delta, velocity):
    if abs(delta) <= ARRIVE_RADIUS:               # ARRIVE_RADIUS = 4: arrived, release
        return 0
    if velocity != 0 and (velocity > 0) == (delta > 0) \
       and abs(delta) <= STOP_FACTOR * abs(velocity):   # STOP_FACTOR = 1.3
        return 0                                   # coasting toward target, within stop
                                                   # distance -> release, let momentum land it
    return 1 if delta > 0 else -1                  # else drive full toward the target
```

Per axis: if the remaining distance is within `ARRIVE_RADIUS = 4` px, that axis has
arrived (release). If the agent is **already moving toward** the target and the
remaining distance is within the predicted stopping distance
(`STOP_FACTOR = 1.3 × |velocity|`), release the axis so friction coasts it to rest
*on* the target instead of overshooting. Otherwise hold the d-pad fully toward the
target. `_movement_mask` runs this on both axes and assembles the `BTN_UP /
BTN_DOWN / BTN_LEFT / BTN_RIGHT` bits.

Velocity is estimated as the per-axis world-pixel displacement since last tick
(`_velocity`, `0,0` on the first observed tick); `resolve_action` records the
agent's position each tick to feed the next tick's estimate. This momentum-aware
release is what lets crewborg approach a task station precisely — important because
`complete_task` (`_resolve_complete_task`) must hold A with **no d-pad** once inside
the rect (any d-pad input resets the 72-tick task progress), so it relies on
residual momentum settling via friction rather than active braking.

### Edge- vs level-triggered buttons

The controller distinguishes two button disciplines, and keeping them straight is a
correctness requirement:

- **Level-triggered (held)** — the d-pad and **B** (vent). B is held within range
  and the server teleports.
- **Edge-triggered (re-fire)** — **A** for kill / report / call-meeting / vote
  confirm. `_edge_press` returns `0` if A was held last tick (forcing a release) so
  the next tick re-presses, matching the engine's "fresh A" requirement.

The interaction range gates (`KILL_RANGE_SQ = 400`, `REPORT_RANGE_SQ = 400`,
`VENT_RANGE_SQ = 256`) are matched to `sim.nim`. Once `_navigate_mask` has carried
the agent within range, the intent handler switches from steering to the
edge-triggered (or held-B) interaction.

---

## Where to look next

| Topic | Document |
| --- | --- |
| How the walkability mask is decoded from the sprite stream | [`./perception-and-belief.md`](./perception-and-belief.md) |
| The occupancy substrate (location tracking) baked alongside the nav graph | [`./agent-tracking.md`](./agent-tracking.md) |
| Which modes navigate where, and why (imposter) | [`./imposter-play.md`](./imposter-play.md) |
| Which modes navigate where, and why (crewmate) | [`./crewmate-play.md`](./crewmate-play.md) |
| Suspicion that picks kill / vote targets | [`./suspicion.md`](./suspicion.md) |
| Meetings, voting, and chat | [`./meetings.md`](./meetings.md) |
| The LLM commander | [`./commander.md`](./commander.md) |
| Trace-log fields, including nav/movement traces | [`./trace-logs.md`](./trace-logs.md) |
| Structural spec (nav graph §6, action §9, controller §12) | [`../design.md`](../design.md) |

## Key files and functions

- **`map/parser.py`** — `load_resource_rects`, `_finalize`, `_parse_color`,
  `_parse_px` (the croatoan.resources port).
- **`map/bake.py`** — `bake_map`, `load_croatoan_map`, `walkability_matches`,
  `_is_task`/`_is_vent`/`_is_room`, `_vent_group_char`; constants
  `DEFAULT_MAP_WIDTH = 1235`, `DEFAULT_MAP_HEIGHT = 659`, `BUTTON_WIDTH = 28`,
  `BUTTON_HEIGHT = 34`.
- **`map/types.py`** — `MapData`, `TaskStation`, `Vent`, `Room`, `MapRect`,
  `MapPoint`.
- **`nav.py`** — `build_nav_graph`, `NavGraph`, `plan_route`,
  `plan_route_via_vents`, `_build_nodes`, `_build_edges`, `_build_anchors`,
  `_find_anchor`, `_build_vent_edges`, `_segment_clear`, `_flood_reachable_pixels`,
  `_clearance_mask`, `_smooth_route`; constants `DEFAULT_CELL_SIZE = 8`,
  `CLEARANCE_RADIUS = 2`, `VENT_REACH = 16`, `VENT_EDGE_COST`.
- **`navbake.py`** — `load_navbake`, `serialize_navbake`, `_read_payload`;
  constants `NAVBAKE_FORMAT = 1`, `NAVBAKE_RESOURCE = "croatoan_navbake.pkl.gz"`.
- **`action.py`** — `resolve_action`, `_navigate_mask`, `_movement_mask`,
  `_axis_input`, `_teleport_mask`, `_velocity`, `_edge_press`; constants
  `ARRIVE_RADIUS = 4`, `WAYPOINT_RADIUS = 8`, `STOP_FACTOR = 1.3`,
  `REPLAN_INTERVAL = 8`, `KILL_RANGE_SQ = 400`, `REPORT_RANGE_SQ = 400`,
  `VENT_RANGE_SQ = 256`.
- **`tools/build/nav_bake.py`** — `extract-walkability` / `bake` offline
  entry points.
</content>
