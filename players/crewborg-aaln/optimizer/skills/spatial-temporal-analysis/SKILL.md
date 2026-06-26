---
name: spatial-temporal-analysis
description: Analyze map/positional policy data spatially and temporally so behavior patterns become visible, and generate after-the-fact visualizations (heatmaps, trajectory polylines, event-anchored windows, timelines, encounter geometry) for pattern mining and hypotheses. Use for games with maps, movement, line of sight, phases, or any spatial/temporal structure, or when a flat table hides the pattern you need.
---

# Spatial-Temporal Analysis

A pattern is only as findable as the **shape** of the data you put it in. For
games with a map, movement, vision, or time/phase structure, the score gap is
usually a *where* and *when* story that a scalar table flattens away. This skill
is the toolkit for **constructing data so a spatial/temporal pattern is
recognizable** — cheap scalar look-ups, derived heuristics over the joined
replay+artifact data, and visualizations written **from stored data after the
eval** (never from live screenshots) that get fed back to the LLM for
interpretation.

This is a **specialty skill**: load it when the game has spatial/temporal
structure. It is conditionally applicable in the same way `map-navigation` is —
together they cover map-based games. It feeds `policy-hypothesis-loop`.

**When to use:** positions/coordinates, rooms/regions, line of sight, pathing,
proximity/interception, kills/captures with a location, phase or round timing,
"who did X then Y," or any time a per-row table is not surfacing the failure.

## Inputs

The joined per-episode records from `replay-artifact-analysis`:

- per-tick **positions** (`x/y`, region/room, current mode/action, visible set),
- **domain events** with a location and a `tick` (a kill, a capture, a vote, a
  task completion, a phase flip),
- **ground-truth replay events** (real roles/outcomes/positions),
- role/seat/version labels.

If any of these is missing, that is a `data-collection-design` task before you
analyze — say so rather than over-reading what you have.

## 1. Cheesy patterns (cheap scalars — run these first)

Fast counts/ratios that flag an obvious spatial/temporal problem before any
plotting. Always compute **per role and per version**:

- **Coverage / dwell** — fraction of ticks spent in each region; time idle at
  spawn; map area never visited. ("This version spends 40% of ticks in one
  room.")
- **Tempo** — time-to-first-objective, inter-event gaps, tick of the Nth event,
  events/episode. ("Time-to-first-kill is 200 ticks slower for v8.")
- **Proximity outcomes** — events that happened within sight/range of another
  player; "acted where a witness could see."
- **Activity rates** — objective completions, traversal hops, fraction of ticks
  in each mode ("follower mode fired on 14% of ticks" is a cheesy red flag).
- **Degenerate motion** — route thrash (mode flips/min), stuck/idle penalties,
  oscillation between two regions.

A cheesy pattern that **diverges between versions** is the cheapest hypothesis
seed.

## 2. Heuristics (derived predicates over the log)

Materialize human-readable predicates by **querying** the joined log + entity
status — do not store new event kinds for them (raw observations, derived
interpretations). Each heuristic is a function over the data; a new one is code,
not a schema change:

- "followed X who then died/was-eliminated" = a proximity interval toward an
  entity whose ground-truth status later becomes dead/out.
- "faked the objective" = dwell inside an objective region with no real
  completion.
- "intercepted vs missed" = trajectory converged to within range before/after
  the opportunity tick.
- "exposed position" = was inside an opponent's line-of-sight at the event tick.

These predicates turn raw position/event data into the *vocabulary the
hypothesis is written in*.

## 3. Visualizations (after the fact, for the LLM)

Render images **from the collected data** and feed them back in. Standard
data-vis, generated post-eval from stored replay/artifact data — not manual
screenshots, not transient logs:

- **Position heatmap** — bin `x/y` into the map grid; overlay static regions /
  objectives / special points. "Where does this version spend time?"
- **Encounter / event geometry** — plot events plus the positions of nearby
  players at the event tick. "Are kills/captures happening in sightlines?"
- **Mode / action timeline** — mode vs tick with replay events marked. "What was
  it doing when the opportunity it missed happened?"
- **Interaction matrix** — actor × target, colored by ground-truth correctness
  (e.g. votes/accusations vs true role).
- **Belief-vs-reality overlay** — the policy's expected map state vs the actual
  positions reconstructed from other slots / the replay.

Render one suspicious episode first (if a replay viewer exists, eyeball it)
before building aggregate plots, so you know which view will carry signal.

## 4. The spatial-temporal gap (the headline limitation — flag it, don't fake it)

The cheap tools above lean on **static** grids/heatmaps, which **flatten ordering
and timing**. This is the biggest open gap in spatial analysis: a summed grid
cannot show *sequence*. When the pattern is inherently sequential-spatial —
interception geometry over time, who-converged-on-whom-then-an-event, region flow
over a match, lead/lag of two trajectories, a chase — a static heatmap will hide
it or, worse, invite a fabricated conclusion.

In those cases:

- prefer **sequence-preserving views**: trajectory polylines with a **time
  gradient** (color = tick), **event-anchored windows** (the N ticks around a
  kill/capture for every actor in range), **per-tick deltas** (velocity, closing
  distance) rather than a summed grid;
- consider an **adaptive / sequential** analysis (segment trajectories by phase,
  align episodes on the event tick, animate or small-multiple by time bucket);
- if the available view cannot represent the ordering, **say so explicitly** and
  mark the read as lossy, rather than over-reading a heatmap.

This honesty is part of the loop: do not manufacture a spatial-temporal
conclusion from a tool that cannot support it. A flagged gap is a legitimate
`data-collection-design` request (log the missing sequence) or a tooling
hypothesis.

## Output

A ranked list of **pattern candidates**, each with: the view/heuristic that
revealed it, the version/role/seat split with rough uncertainty, a one-line "what
it suggests," and whether it is a static or sequence-preserving read. Hand the
ranked list to `policy-hypothesis-loop`; visuals it relied on should be saved
under the eval's artifact directory so the next agent can reproduce them.

## Integration

- **Consumes:** `replay-artifact-analysis` (joined positions/events/ground
  truth), `data-collection-design` (when the needed signal is not logged yet).
- **Pairs with:** `map-navigation` (spatial games usually need both),
  `eval-aggregation` (a visualized pattern still needs a variance-aware verdict).
- **Feeds:** `policy-hypothesis-loop`.
- **Game-specific grounding:** see `games/<game>/skills/...` (e.g. Crewrift's
  position track + `server_tick`-joined replay events and viewer).
