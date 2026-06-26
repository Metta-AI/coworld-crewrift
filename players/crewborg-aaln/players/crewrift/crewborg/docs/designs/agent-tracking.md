# Agent location tracking — a probabilistic occupancy belief

**Status:** stages 1-2 implemented (2026-06-03): static substrate,
reachability-disc occupancy with negative LoS, `domain.occupancy_*` traces,
room-level Pretend targeting, separate teammate-imposter pressure, and imposter
Search-mode occupancy seeking are live. The task-assignment/destination mixture remains the next
gated stage. Sibling of [`suspicion.md`](./suspicion.md) (a tunable belief layer
with learnable params + provenance).

## 1. Goal

Maintain, for every *other* agent, a belief about **where they are now** that stays
useful after they leave view — a distribution over the ship that collapses to a point
when we see them and degrades *intelligently* (not uniformly) when we don't. First
consumer: the **imposter**, especially Pretend: walk toward rooms where crew most
likely are while subtracting pressure from the teammate-imposter distribution, so a
victim is in view by the time the cooldown clears (the "window opens hot" other
half; the timing half is done).
The engine is **role-agnostic on purpose**: a crewmate will later use the same beliefs
for alibi-based deduction in voting. v1 ships the imposter consumer only.

## 2. Why this is tractable (premises + the key reframing)

1. **Motion is deterministic and speed-bounded** (`≈2.75 px/tick`). ⇒ a hard
   **reachability disc**: `T` ticks after the last sighting the agent is within `speed·T`
   px of it. Exact, cheap, assumption-free — the **backbone** that bounds the support.
2. **Agents navigate to a destination** (usually a task; also button/corpse/player), not
   randomly. We model the task case.
3–5. **Efficient, room-circulating behaviour** — head to assigned tasks, don't redo a
   finished task, persist a direction. *These assume reasonably good opponents. Decided
   acceptable (§11-A): if opponents are instead inefficient they finish tasks slowly,
   which buys us hunting time, so the mismatch is a minor cost, not fatal.*

**The reframing that makes it cheap:** the only real *latents* are **(i) which tasks each
agent is assigned** and **(ii) which one they're currently heading to.** Given a
destination, the path (A*) and the position at the current time are *deterministic* from
the static map. So the filter is just: per agent, a soft **task-assignment distribution**
+ a **destination mixture**, rolled forward along **precomputed A\* polylines**. No live
search, **no Monte-Carlo** (a hard requirement — this codebase is RNG-free for
reproducibility; the spread comes from the mixture, not sampling).

## 3. Architecture — two layers

- **Macro (this system): a per-agent pixel-level position belief, aggregated to a coarse
  occupancy grid for the search decision.** Answers "which way do I walk to find crew."
- **Micro (already built): the kill.** `strategy.trajectory` + Hunt's `select_victim` /
  `unwitnessed`. KillRange is 20 px, so the macro layer can never finish a kill — it only
  walks the imposter to the right area; once a crewmate is actually **visible** there,
  today's pixel Hunt takes over (search-only integration, §11-1).

## 4. Backbone: the reachability disc

For agent `a` last seen at `(x,y,t_seen)`: at now `t`, mass is confined to cells within
`speed·(t−t_seen)` of `(x,y)` that are reachable (`NavGraph.reachable`) — and **not**
currently ruled out by line of sight (§6, negative observations). Right after a sighting
this is ~a point; it grows until it covers the map (belief → prior). This alone beats
round-robin wandering and needs **zero** opponent assumptions — it is stage 2.

## 5. Prerequisite (static bake): anchors + A\* polylines + the grid

Built once (extends main design §6), all from the existing `NavGraph`/`plan_route`:

- **Anchor set** = every task-station anchor + the spawn (`map.home`) + the button.
  (~20–30 points.)
- **Pairwise A\* polylines** between anchors — each stored as a pixel polyline with
  cumulative arc-length, so "position at arc-length `d`" is an O(log n) lookup. ~hundreds
  of polylines, fully static. (Node/precompute budget is a non-issue — well under ~1000.)
- **Coarse occupancy grid** — the map binned into ~32 px cells (see §8 for why this size).
  This is the readout substrate; rooms/corridors emerge from it for free.
- *(Optional, non-load-bearing)* room/region **labels** per grid cell, purely so traces
  read "heading to east corridor" — the engine never depends on hand-drawn regions
  (§11-4). A room-adjacency graph is **not** required: "no revisit" is tracked at the task
  level (§6), and "direction" is geometric.

## 6. The per-agent filter

Per other agent `a` (dead players are dropped; teammate-imposters are tracked in
a separate pressure distribution):

- **Task-assignment belief `A_a(t) = P(a is assigned task t)`** (§11-5). Prior: uniform
  over all tasks scaled to the known per-player count (`tasksPerPlayer`). Updated by
  observation: seeing `a` dwell at / complete task `t` (we log `task` intervals; the global
  tasks-remaining counter corroborates) pushes `A_a(t)→1`; seeing `a` pass through a room
  without working a task there gently lowers those tasks. Completed tasks are removed from
  `a`'s live destination set (premise 4, at task granularity).
- **Destination mixture `π_a(t)`** over `a`'s *un-completed likely-assigned* tasks:
  `π_a(t) ∝ A_a(t) · dir(heading, bearing_to_t) · dist_discount(t)` (premises 3–5, all
  cheap & geometric).
- **Predict (closed form, pixel-level).** For each hypothesis `t`, the predicted position
  is the pixel at arc-length `speed·(t_now − t_seen)` along the precomputed polyline
  `last_anchor → t`, clamped to the §4 disc. The agent belief is the `π_a`-weighted set of
  these predicted pixels — a mixture, no sampling. Hallways are handled for free (the
  predicted pixel is *in* a corridor when the agent is between rooms).
- **Observe — in view:** collapse to the true pixel; store position + velocity for the
  micro layer; **sharpen** `π_a`/`A_a` by down-weighting hypotheses whose predicted pixel
  diverges from the observed one (premise 7), so the destination posterior is sharp
  *before* we lose them.
- **Observe — negative LoS** (§11-3): for any cell we can currently see into (the `shadow`
  mask, `occupancy.rect_visible`) where `a` is absent, zero `a`'s mass there. Cheap,
  high-value: this is what makes "I've swept these rooms and not seen them, so they're
  over *there*" work.
- **Re-pick (single hop, extensible).** When `t_now` exceeds a hypothesis's ETA, the agent
  would have *arrived*; for v1 that hypothesis's mass decays to **uniform-within-the-disc**
  (premise 8, conservatively). Structure the code so "spawn the next destination(s) from
  the just-reached task" is a drop-in replacement for the decay (§11-6: multi-hop later).

## 7. Readout & use

- **Coarse occupancy grid.** Sum every tracked crewmate's predicted mass into the grid:
  `E[cell] = Σ_a P_a(cell)`. Track teammate-imposters in a separate distribution,
  not in the crew grid.
- **Imposter room/search targeting (v1).** Pretend aggregates crew occupancy to
  room density, subtracts teammate-imposter pressure, and chooses a real task
  station in the selected room. Search uses the ranked cell readout during the
  kill lead window, walking hot spots until a crewmate is visible → micro-layer
  Hunt strikes. Optimize for **any** crew, not lone (§11-2: these opponents don't
  punish witnessed kills; left as a knob).
- **Crew (later, out of scope):** alibi/anti-alibi for suspicion/voting. Engine kept
  role-agnostic for it.

## 8. Cost, determinism, and the grid as an error model

- Static bake: anchors + polylines + grid. Per tick: ≤7 agents × ~10 hypotheses ×
  arc-length lookup + grid accumulation — well inside the ~3 ms step budget.
- **No live A*, no Monte-Carlo ⇒ deterministic/reproducible.**
- **The coarse grid is also the prediction error model.** Our polylines are only an
  *approximation* of each agent's real path (different bake / grid discretization / wall
  avoidance), so pixel-exact prediction would be falsely confident. Binning to ~32 px
  blurs prediction to the scale where our path and theirs plausibly agree — so **size the
  grid to the expected path divergence**, not arbitrarily fine. (Tunable; calibrate from
  the §9 predicted-vs-actual data.)

## 9. Validation (every stage gates on this)

Falsifiable and tunable like suspicion. Via the existing tracer (`events.py` §11): each
time an agent is **re-acquired**, log predicted grid cell (argmax `P_a` the prior tick)
vs actual, plus distance error and the disc radius. Offline: top-1/top-k cell accuracy and
calibration vs replays — the fitness signal for the priors and the grid size. After every
stage, run a **local 0.1.27 game** and check the number moved (and kills/game / time-to-
reacquire) before building the next stage.

## 10. Staged build (local-game check after each)

1. **Static substrate + tracing.** Anchors, pairwise A* polylines, coarse grid, and a
   `domain.occupancy_*`/predicted-vs-actual trace. **Implemented in
   `agent_tracking.py` + `events.py`.** Check: polylines & grid sane in traces; bake
   cost fine.
2. **Reachability-disc occupancy** (§4) + negative LoS (§6). Wire into Pretend
   task targeting and Search hot-spot walking (§7). **Implemented in
   `agent_tracking.py` + `modes/pretend.py` + `modes/search.py`: Pretend uses
   room-level crew occupancy density to pick fake-task stations, with
   teammate-imposter pressure and a long Pretend-window target commitment; Search
   walks ranked occupancy cells in the kill lead window.** Check: beats
   round-robin (time-to-reacquire, kills).
3. **Destination model** (§6): task-assignment belief + destination mixture + pixel-path
   prediction + in-view sharpening. **Not implemented yet.** Re-measure accuracy & kills.
4. **Re-pick decay** + tuning (grid size, priors) against the §9 numbers.
   *(Later, gated on payoff: multi-hop re-pick, lone-target scoring, crew/voting consumer.)*

Stop at the first stage that wins enough; don't build ahead of the numbers.

## 11. Resolved decisions

- **A (opponent policy):** assume efficient/structural behaviour; inefficiency is a minor,
  non-fatal cost (slow opponents = more hunting time). No replay-learned model needed for v1.
- **1 (integration):** search-only — occupancy drives navigation; the kill still requires a
  visible target via existing Hunt.
- **2 (target):** optimize for *any* crew (witnessed kills unpunished by current opponents);
  lone-target scoring is a later knob.
- **3 (negative LoS):** yes, in v1.
- **4 (granularity):** predict at **pixel level** along precomputed A* polylines; aggregate
  to a **coarse grid** for the readout — no corridor graph nodes (the grid covers corridors
  and absorbs path-approximation noise); region labels optional for traces only.
- **5 (task assignments):** infer per-agent task-assignment probabilities and use them to
  weight destinations.
- **6 (re-pick):** single-hop decay now; code so multi-hop is a drop-in later.
- **7 (consumer):** imposter-only v1; engine role-agnostic for crew/voting later.
- **Init:** all agents start as a point mass at the spawn room (everyone co-located at
  `map.home`), then disperse.
- **Build:** staged with a local-game validation check after each stage (§10).

### Still open / tune later
Grid cell size (§8, from §9 data); the `dir`/`dist` weights in `π_a`; the assignment-update
strengths; lone-target scoring; multi-hop re-pick; crew/voting consumer.
