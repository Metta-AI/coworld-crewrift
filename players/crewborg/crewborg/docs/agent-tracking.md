# Agent tracking — the probabilistic occupancy / location belief

This is the cross-cutting reference for crewborg's **location belief**: where each
*other* agent probably is *now*, kept useful after they leave view. It backs the
imposter's post-kill re-approach by answering "which way do I walk to find crew."

The whole subsystem lives in `crewborg/agent_tracking.py`. It is a pure readout
over the finalized perception fold: it observes `Belief` and writes only its own
`belief.agent_tracking` sub-state, never the roster, suspicion, intents, or trace
events.

For orientation see [`README.md`](../README.md) and the architecture overview in
[`design.md`](../design.md). This doc is the deep reference for the spatial belief
only; it links out for the perception that feeds it, the nav graph it is built on,
and the imposter modes that consume it.

| Concern | Where |
| --- | --- |
| Perception that produces sightings + the LoS mask | [`perception-and-belief.md`](./perception-and-belief.md) |
| Nav graph + reachable-node construction (the substrate's input) | [`navigation.md`](./navigation.md) |
| How Evade / Recon / Search *use* the readouts | [`imposter-play.md`](./imposter-play.md) |
| Crewmate-side use of beliefs | [`crewmate-play.md`](./crewmate-play.md) |
| The separate suspicion belief layer | [`suspicion.md`](./suspicion.md) |
| Meetings | [`meetings.md`](./meetings.md) · Commander | [`commander.md`](./commander.md) |
| Occupancy trace events | [`trace-logs.md`](./trace-logs.md) |

---

## 1. What the belief answers

For every *other* live agent, the tracker maintains a distribution over the ship:
a **reachability disc** that collapses to a point when the agent is in view and
spreads — speed-bounded, not uniformly — when it is not. Summed over all tracked
crew it becomes a coarse **expected-occupancy grid**, and that grid is reduced to
two readouts: the densest-crew room and the hottest crew cell.

The engine is role-agnostic. The live consumer is the imposter's **Evade** mode
(post-kill re-approach); the crew side is free to read the same belief later.

Crew and fellow imposters are tracked in **separate** distributions, so an
imposter blends toward crew while avoiding piling onto a teammate imposter.

---

## 2. The static substrate (built once per episode)

`build_occupancy_substrate(nav, map_data)` produces an immutable `OccupancySubstrate`
— a pure function of the nav graph and the map. The `O(anchors²)` `plan_route`
sweep makes it the heavy half of the offline bake, so `navbake.py` serializes the
`(nav, substrate)` pair as a vendored asset (`navbake.serialize_navbake` /
`load_navbake`); `update_agent_tracking` builds it lazily only when no bake loaded.

### Anchors

`agent_tracking._anchors(nav, map_data)` collects the named map points used as
route endpoints, each snapped to the nearest reachable nav node (`_snap`):

```
home  ........  map_data.home              (kind="home")
button .......  nav.button_anchor          (kind="button")
task:i .......  nav.task_anchor(i)         (kind="task", one per map task)
```

### Pairwise route polylines

For every ordered anchor pair, the substrate stores the baked A* route as a
`RoutePolyline` (`polylines[(start_name, end_name)]`). A polyline is a deduplicated
pixel path plus a cumulative arc-length table:

```
points:             p0 ── p1 ── p2 ── … ── pN
cumulative_lengths:  0   d1    d2         total_length
```

`RoutePolyline.point_at(arc_length)` binary-searches the table to map "distance
walked along this route" to a world pixel in `O(log n)` — the substrate's motion
lookup primitive. (The live disc filter below does not yet ride these polylines;
they are baked and available for the destination-mixture readout.)

### Coarse reachable grid

`_coarse_grid` buckets every reachable nav node into a `GRID_CELL_SIZE = 32` px
grid (coarser than the nav grid — this is a room-scale "where is the crew" readout,
not pixel pathing). Each occupied bucket becomes one `OccupancyCell`:

- `index = row * cols + col`
- `center` — the bucket's most central reachable node (not the geometric cell
  center), so the cell is always a walkable point;
- `label` — the room name containing the center (`_region_label`), or `None` for
  corridors. Labels drive the room-level readout and make traces legible.

---

## 3. The per-agent reachability-disc estimate

Each tick `update_agent_tracking` rebuilds one `AgentPositionEstimate` per live
agent, split into a crew set (`estimates`) and a teammate-imposter set
(`teammate_estimates`). Dead players are dropped. `_estimate_colors` chooses the
estimate kind per color:

### Observed this tick — collapse to a point

`_observed_estimate`: the agent is visible, so the disc collapses to the single
cell containing its true pixel — `disc_radius = 0`, `mass_by_cell = {cell: 1.0}`,
`observed_this_tick = True`.

### Unseen — a speed-limited disc minus what we can see

`_reachability_estimate` spreads **uniform** mass over the cells the agent could
have walked to since its last fix:

```
age           = now_tick − last_seen_tick
radius        = MAX_SPEED_PX_PER_TICK (2.75) · age
support_radius = radius + (cell half-diagonal)      # cell-rounding slack
support       = { cell : dist(last_seen, cell.center) ≤ support_radius
                          AND NOT currently visible }     # negative LoS
mass_by_cell  = uniform 1/|support| over support
top           = the support cell nearest last_seen   # "hasn't moved far" prior
```

The disc is exact and assumption-free: `T` ticks after a sighting the agent is
within `2.75·T` px of it. Right after a sighting the support is ~a point; it grows
with age until it covers the map (belief → prior).

**Negative line-of-sight.** `_point_visible(frame, point)` tests a cell center
against the frame's true LoS mask (`frame.visible_mask`, camera-relative). Any cell
we can currently see into but do *not* see the agent in is removed from the support
— "I swept these rooms and didn't see them, so they're over *there*." If the disc
empties (all candidate cells are visible), support falls back to all non-visible
cells, then to all cells, so the estimate never goes empty.

> The LoS mask itself comes from perception; the frame-local visibility predicates
> `rect_visible` / `rect_observed` live in `strategy/occupancy.py` and serve
> suspicion. The tracker's cell-level negative test is the self-contained
> `agent_tracking._point_visible`, against the same `visible_mask`.

### First-sighting prior

Before an agent has ever been seen (`last_seen_tick == 0`), `_estimate_colors`
seeds `last_seen` at `map.home` — everyone starts co-located at spawn and the disc
disperses from there.

```
AgentPositionEstimate fields
  color, last_seen_tick, age_ticks, disc_radius, observed_this_tick
  mass_by_cell : {cell_index → probability}
  top_cell / top_point / top_probability   # single most-likely spot
  support_cell_count                        # width of uncertainty
```

---

## 4. The expected-occupancy grid

`_snapshot` sums every per-agent estimate into one `OccupancySnapshot`:
`expected_by_cell[cell] = Σ_a P_a(cell)` — the expected number of agents in each
cell. Crew and teammates get **separate** snapshots:

- `belief.agent_tracking.snapshot` — expected **crew** occupancy;
- `belief.agent_tracking.teammate_snapshot` — expected **teammate-imposter**
  occupancy (`None` when no teammates are tracked).

The snapshot also carries `top_cell` / `top_point` / `top_expected` (hottest cell,
ties broken toward the lower cell index), `tracked_count`, and `support_cell_count`.

---

## 5. The readouts consumed downstream

Three pure functions reduce the crew snapshot to navigation targets.

### Densest-crew room — `best_pretend_room_target`

> **Name vs. caller.** `best_pretend_room_target` is a **legacy name** — it dates
> to the retired Pretend imposter mode (removed 2026-06-24). The live caller is the
> **Evade** mode (`modes/evade.py`), which beelines toward where the crew most
> likely are after a kill. The live imposter modes are Search / Recon / Hunt /
> Evade.

It scores each room by crew density with teammate pressure subtracted:

```
density          = crew_expected_in_room      / cells_in_room
teammate_density = teammate_expected_in_room  / cells_in_room
score            = density − TEAMMATE_ROOM_PENALTY (3.0) · teammate_density
```

Scoring at *room* scale (not per cell) is deliberate: cell-level maxima get twitchy
once per-agent support spreads. Teammate pressure is **subtracted**, not folded into
crew occupancy, so two imposters spread out instead of crowding the same room. The
winner is chosen by `(score, expected, −dist² to self, room_name)`.

`ROOM_TARGET_HYSTERESIS = 0.80` gives the caller's `current_room_name` stickiness:
the current room is kept unless a rival beats it by more than this factor, damping
room-to-room flip-flopping when two rooms have near-equal density. An optional
`eligible_room_names` restricts the candidate set. Returns `None` when no room has
positive expected crew. The target's `point` is the room's representative cell
center (`_room_center_cell`).

### Hottest cell — `best_seek_point` / `ranked_seek_points`

`ranked_seek_points(belief)` returns the occupancy cell centers from hottest to
coldest (positive mass only); `best_seek_point` returns the first. Evade uses
`best_seek_point` as the fallback when no room target is available. `self_xy` is
accepted for call-site symmetry but unused — cells are already prefiltered to the
reachable component, so no live A* is needed. (`ranked_seek_points` is otherwise
consumed only by the cold-stored occupancy Search, `modes/_deprecated/search.py`.)

### Evade's preference order

`modes/evade.py` reads these in order: densest crew room → hottest occupancy cell →
most-recently-seen crewmate → idle. See [`imposter-play.md`](./imposter-play.md) for
how the modes act on the targets.

---

## 6. The per-tick fold

`update_agent_tracking(belief)` runs once per tick, called from
`__init__.build_runtime` (`__init__.py:147`) immediately after `update_belief` —
the perception fold is finalized before the tracker reads it. Each tick:

```
1. lazily build/keep the substrate (needs nav + map; no-op until ready)
2. read the current frame → visible_colors; split roster into
   live crew vs live teammates (drop dead)
3. for each crew color that just re-entered view → append a ReacquisitionEvent
4. _estimate_colors for crew and for teammates (observed → point; else disc)
5. _snapshot crew → snapshot;  _snapshot teammates → teammate_snapshot
6. remember previous_visible_colors (powers step 3 next tick)
```

All of this mutates only `belief.agent_tracking` (`AgentTrackingState`):
`substrate`, `estimates`, `teammate_estimates`, `snapshot`, `teammate_snapshot`,
`previous_visible_colors`, and the append-only `reacquisitions` log.

---

## 7. Reacquisition diagnostics

When a lost crew color re-enters view, `_reacquisition` records a
`ReacquisitionEvent`: the disc's prediction from the prior tick (`predicted_cell` /
`predicted_point` at `top_probability` and `disc_radius`) versus where the agent
actually reappeared (`actual_cell` / `actual_point`), plus the `distance_error` in
world px. This is diagnostic only — never read by any decision. `events.py` drains
the log into occupancy traces (`occupancy_substrate`, `occupancy_reacquired`,
`occupancy_seek_target`, and debug-only `occupancy_snapshot`); see
[`trace-logs.md`](./trace-logs.md).

---

## 8. Determinism and cost

The whole layer is deterministic and reproducible — uniform disc mass, table
lookups over the coarse grid, no live A* (routes are pre-baked) and no sampling.
The static bake (anchors + `O(anchors²)` polylines + grid) is the only heavy step
and is offline-bakeable via `navbake`; per tick the work is a handful of agents ×
their support cells plus a grid sum.

The `GRID_CELL_SIZE = 32` px bin doubles as the prediction error model: it blurs
position to the scale at which a baked route and an agent's real path plausibly
agree, so the belief is not falsely confident at pixel precision.

---

## 9. Key symbols

| Symbol | File | Role |
| --- | --- | --- |
| `update_agent_tracking` | `agent_tracking.py` | per-tick entry, folded after `update_belief` |
| `build_occupancy_substrate` | `agent_tracking.py` | static anchors + polylines + grid |
| `OccupancySubstrate` | `agent_tracking.py` | immutable per-episode substrate |
| `RoutePolyline.point_at` | `agent_tracking.py` | arc-length → world pixel |
| `_reachability_estimate` | `agent_tracking.py` | speed-limited disc + negative LoS |
| `_observed_estimate` | `agent_tracking.py` | collapse to a point in view |
| `_point_visible` | `agent_tracking.py` | cell-level negative-LoS test |
| `_snapshot` | `agent_tracking.py` | sum per-agent mass → expected grid |
| `best_pretend_room_target` | `agent_tracking.py` | densest-crew room (legacy name; Evade) |
| `best_seek_point` / `ranked_seek_points` | `agent_tracking.py` | hottest cell readouts |
| `_reacquisition` / `ReacquisitionEvent` | `agent_tracking.py` | predicted-vs-actual diagnostic |
| `rect_visible` / `rect_observed` | `strategy/occupancy.py` | frame-local visibility predicates (suspicion) |
| `serialize_navbake` / `load_navbake` | `navbake.py` | bake the `(nav, substrate)` pair |

**Constants** (`agent_tracking.py`): `GRID_CELL_SIZE = 32`,
`MAX_SPEED_PX_PER_TICK = 2.75`, `ROOM_TARGET_HYSTERESIS = 0.80`,
`TEAMMATE_ROOM_PENALTY = 3.0`.
