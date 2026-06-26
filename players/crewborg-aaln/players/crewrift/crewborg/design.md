# Crewborg — Design Specification

Crewborg is a [Player-SDK](../player_sdk/) agent that plays **Crewrift**, a
Coworld social-deduction game (Among Us–style: crewmates do tasks and vote;
imposters kill, vent, and blend in). This document is the implementation spec.
For codebase orientation, game constants, and source pointers, see
[`AGENTS.md`](./AGENTS.md).

> **Status:** This spec is implemented end-to-end for both roles. Attend Meeting
> now has an opt-in LLM chat/vote path ([§10.3](#103-llm-meeting-decisions)) with
> deterministic fallback, and the tuning parameters in
> [§12](#12-tuning-parameters) await tuning against a live server.

Conventions: paths like `sim:2464` cite the Crewrift Nim source (`sim` =
`src/crewrift/sim.nim`, `global` = `src/crewrift/global.nim`, `protocols.nim` =
`players/notsus/notsus/protocols.nim`), all under
`~/coding/games/coworld-crewrift/`.

---

## 1. Architecture

Crewborg plugs game-specific code into the Player SDK's two-loop runtime. Control
flows through three tiers:

```
   ┌─────────────────────────────────────────────────────────────────────┐
   │ STRATEGY (mode selector)   rules over belief → which mode is active   │
   │        │ ModeDirective                                                │
   │        ▼                                                              │
   │ MODE (behavioral stance)   one Intent per tick, from belief           │
   │        │ Intent ("what to do now")                                    │
   │        ▼                                                              │
   │ ACTION LAYER (executor)    Intent → wire Command, stateful over ticks │
   └─────────────────────────────────────────────────────────────────────┘
```

The SDK drives this every tick via `AgentRuntime.step(observation)`
(`runtime.py:120`), under one shared-memory write lock:

```
perceive(obs, tick) → update_belief(belief, percept) → strategy.observe/poll
   → mode.decide(belief, action_state) → resolve_action(intent, belief, action_state) → Command
```

The inner loop never blocks on the strategy: the mode runs every tick from the
latest belief, while the strategy publishes mode directives asynchronously and
the runtime applies a default directive if none is ready.

**Tier responsibilities**

| Tier | SDK surface | Decides | Owns |
|---|---|---|---|
| Strategy | `Strategy` → `ModeDirective` | *which mode* | role/phase rules over belief |
| Mode | `Mode.decide` → `Intent` | *what to do now* | intent selection, "done" detection |
| Action layer | `resolve_action` + `ActionState` | *how, over time* | pathing, momentum, button timing |

**Invariants (non-negotiable, from the SDK):**

- Raw scene data — especially sprite pixels — never enters belief. Belief is the
  only interface the strategy and modes see.
- Modes emit symbolic intents, never wire actions. All movement, button/cursor
  timing, chat buffering, and momentum control live in the action layer.
- The agent stays live under strategy stall via a default directive + directive
  TTLs.

---

## 2. Types

Crewborg supplies the six `AgentRuntime` type parameters and three functions:

| Type | Role | Mutability |
|---|---|---|
| `Observation` | reference to the bridge's live `SceneState` + tick | frozen ref |
| `Percept` | resolved per-tick view (entities, HUD, phase signals) | frozen |
| `Belief` | persistent world model | mutable |
| `ActionState` | action-layer execution state | mutable |
| `Intent` | symbolic "what to do now" | frozen |
| `Command` | wire payload (input ± chat packet) | frozen |

| Function | Contract |
|---|---|
| `perceive(observation, tick) -> Percept` | interpret the scene tables into entities/labels/world-coords |
| `update_belief(belief, percept) -> None` | fold the percept into belief in place |
| `resolve_action(intent, belief, action_state) -> Command` | execute the intent into wire packets |

**Type style:** all SDK-facing types are **pydantic** models — frozen where the
value is immutable (`Percept`, `Intent`, `Command`), non-frozen where the loop
mutates them in place (`Belief`, `ActionState`). The sole exception is
`SceneState` (§3), a plain dataclass holding numpy/byte buffers that never reach
the strategy.

---

## 3. Transport & bridge

Crewrift speaks **binary Sprite v1**: a structured scene protocol, **not** a
framebuffer. The server streams object placements with exact coordinates and
sprites carrying **text labels** — agents read state from structured data, with
no computer vision. The only image decodes crewborg performs are two sprite alpha
channels: the static `walkability map` and the dynamic `shadow` vision overlay (§4.4).

Crewborg writes its own websocket bridge (`coworld/policy_player.py`):

1. Read `COGAMES_ENGINE_WS_URL` (runner fills `?slot=N&token=…`);
   `websockets.connect(url, max_size=None)` — token validation is at HTTP upgrade.
2. Maintain a **`SceneState`** (a plain dataclass, owned by the bridge): three
   retained tables plus the decoded camera, walkability mask, and `shadow`
   line-of-sight mask.
3. Per tick: block for one binary message — each message is a complete frame (the
   decoder applies all of its concatenated sub-packets) — apply it to
   `SceneState`, then run `runtime.step(observation)` and send the result.
4. Close the socket ⇒ game over; exit cleanly.

The server sends exactly one message per tick per socket, paced to 24 Hz, so the
bridge processes one message per `step`. It has no rate limiter of its own and a
step is sub-millisecond, so if frames ever transiently queue (a scheduler or
GC hiccup), it burns through the backlog faster than 24 Hz and self-corrects
rather than lagging. Coalescing multiple queued frames into one `step` (acting
only on the freshest, as `notsus`' `receiveLatestFrameInto` does) is a latency
optimization, not a correctness requirement, and is not currently implemented.

`Observation` is a thin pydantic wrapper holding a reference to the live
`SceneState` + the tick. Byte-level decoding happens in the bridge; `perceive`
does interpretation only.

### 3.1 Scene tables

The three tables are stateful and incremental — there is no "frame" message; each
update mutates the tables, which are then read as the current scene.

| Table | Keyed by | Holds |
|---|---|---|
| Layers | `u8` layer id | type, flags, viewport |
| Sprites | `u16` sprite id | width, height, **label**, RGBA pixels |
| Objects | `u16` object id | **x, y** (`i16`, camera-relative), z, layer, sprite id |

**Message types** (byte layout per `protocols.nim:408-523`):

| Byte | Message | Payload |
|---|---|---|
| `0x01` | define-sprite | id `u16`, w `u16`, h `u16`, compressedLen `u32`, snappy RGBA, labelLen `u16`, label |
| `0x02` | define-object | id `u16`, x `i16`, y `i16`, z `i16`, layer `u8`, sprite id `u16` (11 bytes) |
| `0x03` | delete-object | id `u16` |
| `0x04` | clear-objects | (marks all objects absent; keeps sprite defs) |
| `0x05` | set-viewport | 5 bytes |
| `0x06` | define-layer | 3 bytes |

The first message is an init burst (clear, define-layer 0, set-viewport 128×128,
define all static sprites including `walkability map`); thereafter one message per
24 Hz tick carries only changed objects.

### 3.2 Camera & self position

The world-map object has **object id 1, sprite id 1**, placed at
`(−cameraX, −cameraY)`. Recover the camera as `cameraX = −mapObject.x`; world
coords are `worldX = obj.x + cameraX` (`protocols.nim:496-499`). World coords are
unavailable until the map object arrives — degrade gracefully on the first ticks.

**Self is not an object** — it is the implicit camera center. Self world position
≈ `camera + fixed center offset`; self role/state comes from HUD labels (§4).

### 3.3 Input & output

Input packet: `[0x84, mask & 0x7f]`. Bits: up/down/left/right =
`0x01/0x02/0x04/0x08`, A = `0x20`, B = `0x40` (bit 7 reserved). **Send only when
the held mask changes**; omitted bits are released. Chat: `0x81 + u16 len + ASCII`,
accepted **only during Voting**.

The action layer computes the desired held mask; the bridge owns the last-sent
mask and the send-only-on-change comparison.

Input semantics (handler `applyInput`, `sim:2751`):

- **A is edge-triggered** (`freshA`): on a fresh press during `Playing`, the game
  tries report → emergency button → kill (imposter), in that order. To repeat A,
  release then re-press.
- **Task completion** = hold A while standing still inside an assigned task rect
  for `TaskCompleteTicks` (72); any d-pad input resets progress.
- **B** = vent (imposter), level-triggered, gated by `VentRange` + cooldown.
- **Voting**: d-pad steps a cursor (up/left = −1, down/right = +1; skip = last
  cell), A confirms.

Inputs do anything only during `Playing` and `Voting`; all other phases ignore them.

---

## 4. Perception

`perceive` iterates the Objects table, joins each object to its Sprite's **label**,
converts camera-relative coords to world coords, and classifies by `(label,
object-id range)`. No pixels are retained.

### 4.1 Percept fields

The entity arrays contain **only what is currently in the agent's vision**; a
player/body absent from an array is *not visible*, which is not the same as *not
present*.

| Field | Source (label / id range) | Notes |
|---|---|---|
| `tick`, `camera_ready`, `camera_x/y` | map object id 1 / sprite 1 | gates world coords |
| `self_role` | `imposter icon`/`imposter icon cooldown` ⇒ imposter; `ghost icon` ⇒ dead; neither ⇒ crewmate | HUD (`global:2484-2506`) |
| `self_kill_ready` | `imposter icon` (ready) vs `imposter icon cooldown` | imposter only |
| `self_world_xy` | camera + fixed center offset | approximate |
| `visible_players[]` | `player <color> left/right`; ids `1000+joinOrder` | id, color, facing, world xy. Visible & alive only — a living agent never sees ghost objects (`global:2389-2398`) |
| `visible_bodies[]` | `body <color>`; ids `2000+i` | id, color, world xy |
| `task_signals[]` | `task bubble` (ids `3000+idx`) and `task arrow` (ids `7000+idx`) | one per incomplete assigned task; crewmate-only. See §4.2 |
| `active_task_progress_pct` | `progress bar N%` | **per-task** progress of *your current* task; present only while in progress (`global:2441-2464`) |
| `crew_tasks_remaining` | `task counter N` | **crew-wide** incomplete-task count (`totalTasksRemaining`, `sim:3175`); visible to both roles |
| `phase_signals` | interstitial text + presence of voting objects | raw signals; the phase machine lives in belief (§5) |
| `voting` | `vote cursor`, `vote skip cursor`, `vote self marker <color>`, `vote dot <color>` (ids `10100+target*MaxPlayers+voter`), `vote timer` | cursor, tally, timer |

Color names (16) and the full label vocabulary are listed in `AGENTS.md` §2.

### 4.2 Task bubbles vs. arrows

For each incomplete assigned task the renderer emits exactly one signal per tick,
chosen by an on/off-screen test (`global:2202-2274`, `:2410-2440`):

- **Bubble** (`3000+idx`) — emitted only when the task is **on/near-screen**, at
  the task's location. Gives an exact world position (`screen + camera`).
- **Arrow** (`7000+idx`) — emitted only when the task is **off-screen** (and only
  if `showTaskArrows` is enabled): a 1×1 pixel on the screen edge along the ray to
  the task. Gives **bearing only**, no location.

### 4.3 Social signals (voting / vote-result screens)

The meeting screens render social information as labeled sprites in id ranges
disjoint from the in-world entity ranges (`global:739-1280`), so the same
`player <color>` / `body <color>` labels never collide with live-world objects:

| Field | Source (label / id range) | Notes |
|---|---|---|
| `chat_lines[]` | text sprite (`9000+`, label = the raw message) paired by screen-y to a speaker icon (`9200+`, `player <color>`) | one `(speaker_color, text)` per visible message; the last `VoteChatVisibleMessages` are re-rendered every tick |
| `census[]` | candidate grid (`9300+seq`): `player <color>` ⇒ alive, `body <color>` ⇒ dead | an **authoritative per-meeting alive/dead census by color** |
| `voting.candidates[]` + `voting.cursor_slot` | the same candidate grid (slot = `id − 9300`) + the `vote cursor` object's screen position | per-slot `(slot, color, alive)`, and the slot the cursor is on (nearest cell to the cursor) — drives **targeted voting** (map a target color → its slot, step the cursor to it) |
| `ejected_color` | vote-result icon (`9600`, `player <color>`) | the player the just-finished vote ejected; absent when the vote skipped |

Chat text shares the `9000` range with phase/HUD text, so chat cannot be told
apart by id alone; we anchor on the icon range (exclusively chat) and only emit a
line when an icon sits within a small y-tolerance of a text sprite. The vote cursor
on slot `s` is drawn at the same grid position as candidate cell `s`, so we recover
`cursor_slot` by nearest-cell match (no need to vendor the grid layout constants).

### 4.4 Line of sight (the `shadow` overlay)

The server sends each non-ghost player a **vision overlay** — a screen-sized
sprite (object `13000`, sprite `5010`, label `shadow`; `global:2212`) whose opaque
pixels are occluded and transparent pixels are visible, computed by raycasting
against walls (`castShadows`, `sim.nim:2974`). Crewborg decodes its alpha exactly
like `walkability` into `scene.visible_mask` (a screen-space bool grid,
`True ⇒ visible`; `visible = alpha == 0`). Unlike walkability it is **dynamic**:
the server resends it on *any* camera/player move (cache keyed on camera+origin,
`sim.nim:3037`), so the retained mask always matches the current camera — there is
no staleness window. This is true per-point line of sight (it powers
`rect_visible`, §10.1), distinct from mere viewport containment. It is absent for
ghosts and during meetings (no camera).

---

## 5. Belief

`update_belief` folds each percept into the persistent `Belief`. Sections:

- **self** — role, alive/dead, world xy, kill-ready + cooldown estimate, active
  task + progress, vote cast this meeting, emergency-button-used flag.
- **map / nav** — the static map (§6): task rects (by index), vent rects + groups,
  rooms, emergency-button location, walkability grid, and a nav graph built over it.
- **roster** — keyed by player **color** (the one identity stable and unique
  across every Crewrift namespace — in-world sprites, bodies, chat icons, vote
  markers). Per `PlayerRecord`: color, the live-world `object_id`
  (`PLAYER_OBJECT_BASE + joinOrder`, learned on first live sighting), the
  **last-seen-alive fix** (world xy, facing, last-seen tick — written only from
  live `player <color>` sightings, so it *is* "the last time/place I saw them
  alive"), a bounded **sighting trail** (`history`: recent `(tick, x, y)`), and
  **life status** (`alive`/`dead`/`unknown`) with how/when the death was learned
  (`death_source` ∈ `body`/`census`/`ejection`, `death_seen_tick`, `body_xy`).
  The alive-fix is preserved when the death is recorded, connecting "last seen
  alive" to "now dead" on one record. Also carries the player's **event log**
  (`events`, §5.2).
- **tape** — `recent_frames`: a bounded ring of recent raw observation frames
  (§5.1), the substrate for frame-to-frame transition detection.
- **bodies** — by id: color, world xy, reported flag. Each body sighting also
  flips the matching color's roster record to `dead` (linking by color). Cleared
  when a meeting opens (the server removes bodies then); the death stays on the
  roster.
- **chat** — `chat_log`: the current meeting's transcript (`(tick, speaker_color,
  text)`), de-duplicated across the per-tick re-render and cleared when a new
  meeting opens. Raw material for suspicion reasoning.
- **social history** — episode-persistent (never cleared): `accusations`, the
  who-sus'd-who graph parsed from every meeting chat line
  (`strategy/meeting/social.py`: color mentions classified accuse/defend per
  clause), and `meeting_history`, one `MeetingRecord` per meeting with the final
  vote tally (voter color → target color or `skip`, refreshed from the live dots
  each Voting tick) and the ejected color. Feeds the social suspicion cues
  (§10.1) and the meeting LLM context (§10.3).
- **tasks** — assigned task indices (from `task_signals` ids), per-task world
  location (from the map), per-task completion; `crew_tasks_remaining`;
  `task_arrows_enabled` (below).
- **phase** — current phase + start tick + the phase state machine, advanced from
  `phase_signals` (emit a `phase_change` trace on transition).
- **voting** — live tally, cursor, timer, who has voted.
- **social / evidence** — `suspicion[color]` = the Bayesian posterior **P(imposter)**
  ∈ [0, 1] per other player (combinatorial prior + likelihood-ratio updates),
  `confirmed_imposters` (near-certain catches contributed as overwhelming-LR
  evidence), and `believed_imposters` (alive colors over the flee probability).
  `imposter_count` (K) overrides the player-count-derived default. Maintained each
  tick by the suspicion model (§10.1). The opt-in meeting LLM (§10.3) consumes the
  `chat_log`, vote tally, roster, and suspicion posterior for chat/vote decisions.
- **agent tracking** — `agent_tracking` holds a static occupancy substrate
  (anchors, pairwise route polylines, coarse reachable grid) plus per-player
  reachability-disc location estimates, a separate teammate-imposter estimate,
  and the latest expected-crew occupancy grid. It is maintained after perception
  folding and feeds imposter Pretend room selection and pre-kill search (see
  §10.2).
- **inferences** — reserved slot for other strategy-produced facts.

**Total player count.** Players appear as objects only when visible, but the
roster spawns co-located at the first `Playing` tick, so the visible set ≈ the
full roster. Seed `total_player_count` from the count of distinct colors seen;
thereafter we know how many players exist and how many are currently unseen.
(Relies on a co-located spawn — a strong estimate, not a guarantee.) The meeting
**census** (§4.3) lists every player and so is authoritative when present.

**Staleness / stillness.** Per player, keep last-known position + last-seen tick;
comparing against the current tick yields staleness and stillness. The bounded
sighting `history` trail (for velocity/heading and re-finding lost crew) is
tracked.

**`task_arrows_enabled`** (tri-state `None`/`True`/`False`). Discovered by
observation — the `task arrow` sprite is always *defined* in init; what's gated is
whether arrow *objects* (`7000+idx`) are emitted. Once a crewmate in `Playing` has
a known off-screen incomplete task: set `True` on the first `7000+idx` object seen,
`False` if several ticks pass with off-screen tasks and no arrow. Behavior fork:

- **On** — follow arrow bearings to off-screen tasks.
- **Off** — no off-screen task signal; task-finding becomes a baked-map
  room-by-room sweep until each station's bubble appears.

### 5.1 Perception tape (`recent_frames`)

The roster/bodies aggregates answer *"what is true now"* but flatten time. A
second, complementary layer answers *"what changed between frames"*: a bounded
ring of recent **raw** observation frames (`PerceptionFrame`, oldest first,
`RECENT_FRAMES_MAX` ≈ 24 ≈ 1 s at 24 Hz), appended in `update_belief` **only on
camera-ready frames**. Each frame holds its `tick`, the **camera** (`camera_x/y`),
the alive `players` + `bodies` seen that frame (color → world xy), and the
**line-of-sight mask** for that frame (`visible_mask`, §4.4, held by reference).

Two design choices make it the right substrate for transition detection:

- **Raw, not derived.** Occupancy (vent/task rects) and adjacency (kill-range) are
  *pure functions* over the tape (`strategy.occupancy`), never materialised — so a
  new region/predicate is a function, not a schema change. (A hot derived view such
  as kill-range adjacency could later be cached in its own belief slot; the tape
  makes that additive.)
- **Carries observability.** Storing the camera + LoS mask means an absence from
  `players` is distinguishable from "we weren't looking there": `rect_visible`
  answers whether a region was *actually in line of sight* that frame (true
  occlusion, not just inside the viewport rectangle) — essential for any "this
  region was clear" claim. It falls back to viewport containment (`rect_observed`)
  only before the mask has arrived.

Camera-ready-only appends mean a meeting leaves a **tick gap** in the tape;
transition detectors require the two frames they compare to be consecutive, so the
gap is self-protecting. This overlaps slightly with the per-player `roster.history`
trails (different scope: uniform recent all-player frames vs. long per-player
trails for velocity/recovery); both are kept.

### 5.2 Per-player event log (`PlayerRecord.events`)

Where the tape is short-term raw frames, the **event log** is the long-term
*"what have I seen X doing"* memory — a human's basis for suspicion, and the
natural thing to hand the future LLM (`strategy/event_log.py`,
`update_event_log`, run in the fast loop after `update_belief`). Each tick it
records, per visible player, the **durative intervals** it observed them in
(`PlayerEvent`; unbounded — a game produces few intervals, so it keeps the whole
match):

| Kind | Predicate | Carries |
|---|---|---|
| `room` | inside a baked room/corridor rect | `region_index` |
| `task` | inside a task-station rect (looks like working — fakeable) | `region_index` |
| `vent` | collision point inside a vent rect | `region_index` |
| `near_body` | within `NEAR_BODY_RADIUS` of a discovered body | `target_color` (body), `min_dist` |
| `proximity` | within `KILL_RANGE` of another live player | `target_color` (the other), `min_dist` |

Two principles:

- **Intervals from observation, with a small grace.** A predicate true while we
  watch a player extends one interval; it splits when we *see the player but the
  predicate is false* (a real departure), or after an unobserved gap longer than
  `EVENT_MERGE_GRACE_TICKS`. A *brief* unobserved gap (losing sight for a few
  frames) is **bridged**, so a 1-tick occlusion blip doesn't fragment a dwell. The
  bridge vs. split decision keys on the logger's previous-observed tick per player
  (`last_event_tick`): we only merge when the predicate held the last time we
  actually saw them. Duration is "observed (± a few bridged frames) for ≈ N".
- **Raw observations, derived interpretations.** Only direct sightings are stored;
  compound signals are *queries* over the log + life-status, never their own kind —
  e.g. *"orange followed yellow, who then died"* = a `proximity` event toward
  yellow plus `roster["yellow"].life_status == "dead"`; *"red looked like a real
  crewmate"* = total `task` dwell.

It is **neutral memory**, built for every role (an imposter benefits too); only
*acting* on it (suspicion → Flee) is crewmate-gated. Meeting chat stays in
`chat_log` (§4.3) for now — a unified per-player view can merge the two later.
The graded suspicion layer (§10.1) already consumes a conservative subset of these
events (`vent`/`near_body`/`proximity`); `near_body` is sound because `belief.bodies`
is cleared when a meeting opens (matching the server), so it never fires on a stale
body location.

---

## 6. Static map (resource-file bake)

Vent, emergency-button, and task locations are **not in the stream** (the `map`
object is a flat prerendered picture, `global:701-707`; only object positions and
the walkability alpha mask are structured). They live in the game's map resource
file, which is server-side data and never delivered to a player. Crewborg bakes
them.

**Source & format.** `data/croatoan.resources` in the game repo — a CSS-like list
of named rectangles (parser `resources.nim:140-230`). Each block is a `/* name */`
comment followed by `width/height/left/top` (px) and a `background` color; a rect
is kept only if it has all of those. Classification (`sim:744-775`):

- `task` → task list **in file order** — this order *is* the `3000/7000+idx`
  stream index, so it maps a task signal to a world rect.
- `ventN` → a vent whose **group is the trailing digit** (same-group vents
  teleport together).
- named rooms → rooms.
- **emergency button** — *derived*: a 28×34 rect centered on the **bridge** room's
  center (`sim:789`).

**Mechanism.** Vendor the raw `croatoan.resources` into the `map/` package and port
the ~40-line parser to Python. Parse it **at container startup** into belief's map
section (never per-tick — the map is static for an episode).

**Walkability & validation.** The walkability grid comes from the stream's
`walkability map` alpha (decoded once); the nav graph is built over it (`nav.py`,
once per episode). Because Crewrift collides the player as a **1×1 point**
(`sim.nim` `CollisionW=CollisionH=1`), every walkable pixel is a legal position, so
the graph is coarsened (8px cells) only for A* speed while **correctness is
enforced at pixel resolution**:

- A cell is a routable **node** iff it contains a *reachable* walkable pixel; the
  node's point is the reachable pixel nearest the cell center (so a cell that only
  clips a corridor still routes — the old "all pixels walkable" rule discarded it).
- **Edges** join 8-neighbour nodes whose connecting pixel segment is fully walkable
  (no diagonal corner squeeze), so A* and the line-of-sight smoother are sound on
  the real mask, not the coarse approximation.
- **Reachability** is a pixel flood from `home` (spawn) — ground truth, immune to a
  thin wall passing *through* a cell.
- **Clearance** (`CLEARANCE_RADIUS`): a config-space margin so routes run down
  corridor centres rather than grazing walls — the bang-bang controller's
  axis-aligned staircase + momentum would otherwise drift into a grazed wall and
  wedge. An eroded mask (a pixel is "clear" iff its `(2r+1)²` box is walkable) steers
  node placement, the clear-shot short-circuit, and route string-pulling. Edges and
  the reachability flood still use the **true** mask, so tight passages and
  wall-adjacent destinations stay reachable (only the final hop onto an anchor is
  un-inflated).
- **Destination anchors:** for every baked task / vent / button, the reachable
  pixel satisfying its interaction condition (inside the task/button rect; within
  VentRange of a vent) is precomputed, so navigation targets a known-good point
  instead of a rect center that may sit in a wall. A destination with no reachable
  anchor is logged at build — surfaced on frame 1, not as a silent mid-game stall.
- **Vent teleport edges:** same-group vents teleport together, so the graph also
  holds a directed edge between every pair of reachable same-group vent anchors.
  These are **imposter-only**: only `plan_route_via_vents` (the `escape` intent)
  traverses them, so crewmate routes are unaffected by their presence.

The decoded walkability also validates the bake: if it doesn't match `croatoan`,
the server is running a different map — fail loud / fall back. (`mapPath` is
config-overridable, `sim:1320-1321`; today only `croatoan` exists.)

> Building crewborg requires the game repo (or the vendored `croatoan.resources`)
> present.

---

## 7. Modes

A mode is a coarse **behavioral stance** (a handful per role), selected by the
strategy (§10). Each tick the active mode reads belief and emits **one intent**
(§8) — possibly the same intent for many ticks, or a new one. A mode's logic is:
*which intent best serves this stance now*, including detecting from belief that
the current intent is finished and switching. Modes never touch buttons, paths, or
momentum. Modes may report `ModeDecision.complete/.stalled` so the strategy
re-decides.

### 7.1 Crewmate modes

| Mode | Active when | Intents emitted |
|---|---|---|
| **Normal** | default while `Playing` | target the nearest reachable **signalled** task (live arrows+bubbles = the remaining tasks) and `complete_task(T)`; conclude `T` done when its **bubble disappears**, gated on having seen ≥ `COMPLETION_PROGRESS_PCT` (≈90%) progress (so an occlusion/edge flicker doesn't false-complete); when **no task signal remains**, `navigate_to` the spawn / **start room** rather than standing still |
| **Crewmate Ghost** | `self_role == "dead"` while `Playing` | reuse Normal's task bookkeeping, but travel to task centers with `navigate_to_noclip` so walls do not constrain pathing; once inside the task rect, `complete_task(T)` holds A exactly like Normal |
| **Attend Meeting** | phase = `Voting` | `chat(text)`, then `vote` per the game-theory vote policy (`strategy/meeting/vote_policy.py`): the top suspect over a **state-dependent bar** (0 in a must-eject endgame, up to 0.9 when the margin is tight), with a near-deadline **anti-split swap** onto the plurality, else skip — always cast before the timer |
| **Dick Mode** | opt-in with `CREWBORG_DICK_MODE=1`; live crewmate, one-shot, and first kill cooldown is within the worst-case button walk plus buffer | while `Playing`, `call_meeting`; during the triggered meeting only if our button press opened it, `chat("haha, fuck you imposters")`, then skip-vote until the meeting closes |
| **Report Body** | a body is in view | `report(body_id)`; yields when a meeting opens |
| **Flee** | a believed-imposter is approaching | `flee_from(player)`, or a strategic `navigate_to(point)` |

### 7.2 Imposter modes

| Mode | Active when | Intents emitted |
|---|---|---|
| **Pretend** | default imposter stance, while kill cooldown is not near ready | pick a room from **expected crew occupancy density**, penalize rooms where a teammate-imposter is likely present, choose a real task station in that room, move there, and hold for one task duration. Rooms without fake-task stations and the start room are skipped |
| **Search** | kill ready or within `SEARCH_LEAD_TICKS` of ready, but no visible kill target | walk ranked occupancy hot spots until a non-teammate crewmate is visible; once one is found, follow its live / recent last-seen position until Hunt can take over |
| **Hunt** | kill ready **and** a victim is visible | **commit to a visible victim and close/strike**: `select_victim` picks the most-isolated reachable visible crewmate, preferring targets not already claimed by a closer teammate; navigate to its **predicted intercept** (`strategy.trajectory` — lead a moving target); when in KillRange *and* unwitnessed → `kill`, else keep shadowing in range (lie in wait) |
| **Evade** | for `EVADE_TICKS` after our own kill | `vent` if a vent exists; otherwise move away from the nearest known body. This avoids instant self-reports and gets the imposter away from the corpse before Search/Hunt/Pretend resume |
| **Report Body** | a non-fresh body is in view after the evade window | `report` the nearest visible body — reuses the crewmate Report Body mode. Fresh self-kill bodies are handled by **Evade** first |
| **Attend Meeting** | phase = `Voting` | `chat(text)`, then `vote` — currently **skip** (suspicion is crewmate-only, so `top_suspect` is empty for an imposter); suspicion-aware bluff/deflect is future |

**Pretend is the imposter's default blending behaviour.** It does not follow
visible crew and it carries no victim state. It chooses a highest-scoring
occupancy room that has a real task station, moves to that station, and idles for
`TASK_TICKS` (72) to fake task completion. If no occupancy room is available, it
falls back to deterministic task-station wandering outside the start room.

```
                 ╔══════════════════════════════════════════════════╗
                 ║                     DISPATCH                       ║
                 ║   (entry; re-entered after a fake task)             ║
                 ║                                                     ║
                 ║     occupancy task room? ─ yes ─▶ GOTO_TASK         ║
                 ║     else ──────────────────────▶ GOTO_TASK          ║
                 ╚══════════════════════════════════════════════════╝
                               │
                               ▼
                    ┌────────────────────┐
                    │      GOTO_TASK      │
                    │ navigate to station │
                    └────────────────────┘
                               │ arrived
                               ▼
                    ┌────────────────────┐
                    │       DO_TASK       │
                    │ idle for 72 ticks   │
                    └────────────────────┘
                               │ hold complete
                               ▼
                            DISPATCH
```

| State | Behaviour | Transitions |
|---|---|---|
| **DISPATCH** | transient chooser | occupancy target available → **GOTO_TASK**(best room's task station); else → **GOTO_TASK**(fallback station outside current/start room) |
| **GOTO_TASK**(station) | `navigate_to` a real task station in the selected room. Keep the chosen occupancy room for `ROOM_TARGET_MIN_TICKS` unless arriving, so noisy room scores do not cause route thrash | arrived at station → **DO_TASK**; no station/target exhausted → pick another station |
| **DO_TASK**(station) | **hold `TASK_TICKS` (72)** (`idle` — a fake task) | hold complete → **DISPATCH** |

Notes: the **starting room never triggers DO_TASK** (every player is co-located
there at spawn, and anchoring a task there strands the imposter when the crew
disperses). DO_TASK **holds the full duration** even if crewmates pass by — only
Evade / Search / Hunt / Report Body (via the selector) can preempt it. Occupancy and
fallback choices are **arbitrary-but-deterministic** — no RNG — so runs are
reproducible without synchronizing both imposters onto the same round-robin path.

**Search owns the pre-kill lead window.** When the cooldown is ready or within
`SEARCH_LEAD_TICKS`, but no visible victim is available, Search walks ranked
occupancy cells. When it sees a non-teammate crewmate, it follows that target's
live or recent last-seen position until Hunt can take over.

**Hunt is kill-ready and visible-target only.** It commits to one visible victim,
leads that victim's motion to close range, and fires only when the kill would go
**unwitnessed**. The witness bar relaxes with urgency (how long we have been able
to kill without doing so), so a perpetually-shadowed kill still eventually fires
rather than never (§10). If a recently seen teammate-imposter is closer to a
victim within the claim radius, Hunt prefers another victim when one exists; this
is a lightweight coordination rule that also helps against non-crewborg teammates
who happen to be near a target.

### 7.3 Division of labour

The **action layer executes**; it does not decide when work is done. The **mode**
watches belief — task icon gone, `active_task_progress_pct` at 100%, meeting
opened, target dead — and changes the intent. A ghost crewmate uses
Crewmate Ghost mode: the mode still chooses task/world targets, but wall-ignoring
movement remains an action-layer concern via `navigate_to_noclip`.

### 7.4 Possible refinements

Mode-level enhancements to keep in view: arrow-bearing **task triangulation** under
arrows-only; **travelling-salesman** task ordering over the nav graph; **safety in
numbers** (prefer routes/tasks near other crewmates); **strategic flee targets**
(toward a trusted player / the button / a sightline-breaking corner); richer
**imposter coordination** (shared claims, role assignment, or bluff-aware spacing
beyond the current local teammate-pressure/claim heuristic). Victim commitment,
the most-isolated visible-target pick, trajectory-led interception, lead-window
Search, and lightweight teammate avoidance are now implemented.

---

## 8. Intents

An intent is "what to do now" — above a button press, below a behavior. One
**shared vocabulary** serves both roles; modes differ only in which they emit.

| Intent | Carries | Meaning |
|---|---|---|
| `idle` / `loiter` | (optional anchor) | stand still / wander to blend in |
| `navigate_to` | world point | go to a point |
| `navigate_to_noclip` | world point | go directly to a point, ignoring wall-aware nav (crewmate ghosts) |
| `flee_from` | player id | maximize distance from a player |
| `complete_task` | task index | go to the task rect and complete it |
| `report` | body id | go to a body and report |
| `call_meeting` | none | go to the emergency button and call a meeting |
| `vote` | choice (player id / skip) | cast a meeting vote |
| `chat` | text | speak in a meeting |
| `kill` | target player id | go to a crewmate and kill (imposter) |
| `vent` | vent / group target | go to a vent and use it (imposter) |
| `escape` | world point | flee to a point, vanishing through a vent if one is on the fast route (imposter) |

`flee_from` is the simple keep-away primitive (geometry owned by the action
layer), used by the crewmate Flee mode — it never vents. Situational fleeing —
toward a trusted player, the button, or around a corner — is the Flee mode emitting
`navigate_to` instead. `escape` is its imposter counterpart: the action layer plans
a vent-aware route to the point, so the only way an agent uses a vent in transit is
an imposter emitting `escape` (crewmate routes never touch the teleport edges).

---

## 9. Action layer

`resolve_action(intent, belief, action_state) -> Command` is the only place
transport mechanics live, and it is **stateful across ticks** (state in
`ActionState`). Each tick:

1. **Diff** the incoming intent against the stored one.
2. **Unchanged** → continue executing (advance the nav route, keep holding A, step
   the vote cursor).
3. **Changed** → discard in-progress execution (route, button FSM) and start the
   new intent fresh.
4. Compute and return this tick's `Command`.

**Composite intents** internally sequence *navigate-then-interact*, reusing one
"move toward a world point" routine (follows the nav route, does momentum control):

- `complete_task` → navigate to the station's **baked anchor**, then **hold A while
  standing still** (movement suppressed — d-pad resets the 72-tick progress).
- `navigate_to_noclip` → steer directly to the requested world point with the same
  movement controller, but without a nav route; used by Crewmate Ghost while
  traveling to task centers before it switches to `complete_task` inside the rect.
- `report` / `kill` → navigate to the body/target (a dynamic point, no anchor),
  then edge-press A.
- `call_meeting` → navigate to the emergency button's baked anchor, then
  edge-press A inside the button rect. The action layer records the press tick so
  the strategy can abandon the experiment cleanly if the server refuses the call.
- `vent` → navigate to the vent's **baked anchor**, then press B (the trigger gate
  stays on the true vent center — `sim.nim` VentRange — even though nav aims at the
  anchor).
- `escape` → follow a **vent-aware route** (`plan_route_via_vents`) to the point.
  Ordinary legs walk; a teleport leg walks onto the entry vent's anchor and presses
  B (gated on real VentRange) to vanish to the exit, then resumes walking. The
  route's teleport legs are carried in `ActionState.route_teleports` (waypoint index
  → entry vent index).

Static destinations (tasks, vents, button) navigate to their **baked anchor** — a
reachable walkable pixel satisfying the interaction condition (§6) — so a rect
center that sits in a wall never strands the agent; dynamic targets (bodies, kill
targets) use their live position.

**Transport mechanics owned here:**

- Button bitmask encoding and the `[0x84, mask&0x7f]` packet.
- The edge-triggered A press FSM (release then re-press to refire).
- Momentum control / nav-route following (the `nav` helper plans over the baked
  graph; the action layer follows). The route is **re-rooted at the agent's live
  position every `REPLAN_INTERVAL` ticks** (and whenever the goal changes), so the
  follower never commits to a stale route after drifting off the planned line — A*
  is ~0.2 ms, so this is nearly free and is what eliminates residual approach-wedging.
- Vote-cursor stepping then A-confirm — with a **last-resort confirm**: the cursor
  walk is budgeted (`vote_steps`, ~two laps of the candidate grid); past the budget
  the resolver confirms wherever the cursor is, because any vote beats the −10
  no-vote penalty (a cursor/grid that never decodes must not spin DOWN forever).
- Chat buffering + ASCII validation (emit only during Voting).
- Hand the held mask to the bridge, which de-dups (send-only-on-change).

**`ActionState` holds:** the current intent (for the diff), the active nav route +
progress cursor (+ which legs are vent teleports), the A-press FSM state,
emergency-button attempt bookkeeping, and the pending-chat buffer.

`Command` carries the per-tick wire payload (input packet ± chat); an empty payload
means "send nothing this tick."

---

## 10. Strategy (mode selector)

The strategy **selects the mode** (modes pick intents). For v1 it is a
deterministic `Strategy.decide(snapshot) -> ModeDirective` run via
`SynchronousStrategyRunner` **every tick** — pure rules over belief. The
`AsyncStrategyRunner` LLM seam stays available for future mode-selection
experiments, but the implemented LLM behavior is currently scoped to the
meeting-mode chat/vote path (§10.3).

Because the selector runs every tick, **v1 uses no reflexes** — transitions ("body
sighted → Report", "Voting → Attend Meeting") are re-evaluated each cycle. The
default directive is `idle` mode (the stall/TTL fallback, rarely reached).

**Crewmate selection** (priority order):

1. phase = `Voting` → **Attend Meeting** (or **Dick Mode** if it opened the meeting);
   `RoleReveal`/`Lobby`/`GameOver` → **idle**
2. Dick Mode armed → **Dick Mode** until its emergency-button attempt resolves
3. body in view → **Report Body** (a meeting protects us and lets the crew act, so
   reporting outranks fleeing a suspect we could instead report)
4. believed-imposter approaching → **Flee**. This transition has spatial
   hysteresis: enter when a recently seen believed imposter is close, then stay in
   Flee until that same threat is clearly farther away or its last-known position
   is stale. This prevents task/flee oscillation at the trigger radius.
5. `self_role == "dead"` → **Crewmate Ghost** (finish tasks with noclip navigation;
   no body reporting or Flee)
6. otherwise → **Normal**

**Imposter selection** (priority order):

1. phase = `Voting` → **Attend Meeting**
2. just killed → **Evade** for `EVADE_TICKS` (vent if possible, else leave the body)
3. a body in view → **Report Body** only after the fresh-kill evade window
4. kill ready **and** a visible victim → **Hunt** (commit + close, strike when isolated)
5. kill ready **or within `SEARCH_LEAD_TICKS` of ready** (`ticks_until_kill_ready`) → **Search**
   (walk occupancy hot spots; follow the first visible non-teammate target)
6. otherwise → **Pretend** (choose likely crew rooms from occupancy density,
   penalize teammate-imposter pressure, move to a real task station, fake the task)

(4) fires only when the kill is ready and a live non-teammate is visible. Hunt then
commits to a victim (§7.2), firing the kill only when it would go **unwitnessed**.
The witness bar relaxes with **urgency** — `last_tick − kill_ready_since_tick`, how
long we have been able to kill without doing so — shrinking the required clearance
radius and the witness-staleness window to zero by `URGENCY_FULL_TICKS`, at which
point the imposter strikes regardless of witnesses. When no visible victim is
available, Search owns acquisition during the lead window rather than Hunt chasing
stale targets.

**Aggressive experiment.** `CREWBORG_BE_DUMB=1` (or `BE_DUMB=1`) replaces the
imposter `Playing` selector with only **Search**/**Hunt**: if kill-ready with a
visible victim, Hunt; otherwise Search. This deliberately skips Pretend, Evade,
and imposter body reports so hosted/local runs can isolate whether always preparing
to kill improves imposter outcomes versus the default blend-in policy.

**Dick Mode experiment.** `CREWBORG_DICK_MODE=1` (or `DICK_MODE=1`) arms a
crewmate-only, one-shot interruption timed against the first imposter kill
cooldown. The strategy reconstructs `ticks_until_kill_ready` from the globally
visible Playing transition after role reveal / meetings, using the default
`DEFAULT_KILL_COOLDOWN_TICKS = 900` until an imposter HUD can teach a better
duration. When the remaining cooldown is no more than
`DICK_MAX_BUTTON_TRAVEL_TICKS + DICK_KILL_COOLDOWN_BUFFER_TICKS`, the selector
switches to **Dick Mode**, which rushes to the emergency button and presses A.
`DICK_MAX_BUTTON_TRAVEL_TICKS = 600` is a conservative hardcoded Croatoan bound:
the map diagonal is about 1,400 px and the server max speed is about 2.75 px/tick,
so the straight-line bound is about 510 ticks before path shape and controller
settling. The 10-tick buffer aims to land before the cooldown clears. If that
button press opens Voting, Dick Mode chats `haha, fuck you imposters`, casts a
skip vote so it does not eat the no-vote penalty, then normal tasking resumes
after the meeting closes. If Voting opens before our recorded button press, the
strategy treats it as someone else's meeting/body report and routes to Attend
Meeting, so the taunt is gated on our own call. Crewrift's default `ButtonCalls`
is one per player, so the strategy does not re-arm Dick Mode within the same game;
if the button press is refused, the attempt times out after
`DICK_CALL_NO_MEETING_GRACE_TICKS` and stays spent.

**Future imposter refinement.** Emergency-button rules imply a useful target
priority: imposters should prefer killing crewmates who can still call an
emergency meeting. The current Crewrift opponents tend to chat after calling the
button (for example, "just resetting imposter cool downs"), which gives a noisy but
actionable signal that their one `ButtonCalls` charge is spent. A future Search /
Hunt selector should remove or heavily deprioritize those colors from hunt targets
and keep pressure on crewmates who can still stop the next kill cooldown.

### 10.1 Suspicion — Bayesian P(imposter) (`strategy/suspicion.py`)

> **Full reference:** [`docs/designs/suspicion.md`](docs/designs/suspicion.md) — the
> living home for the model, the likelihood-ratio table's per-entry rationale, the
> offline LR-learning workflow, and the provenance log of every weight. This section
> is the summary.

`update_suspicion(belief)` runs every tick in the fast loop *after* `update_belief`
+ `update_event_log` (composed in `build_runtime`). Crewmate POV: it maintains
`belief.suspicion[color]` = the posterior **probability that player is an imposter**,
∈ [0, 1] — a real probability, so thresholds mean something. `believed_imposters`
(which gates Flee) is every **alive** player with `P ≥ FLEE_PROBABILITY` (0.9).
Crewmate-only — an imposter knows the truth (suspicion cleared, never flees), nor
does a ghost.

**Prior.** With `P` players and `K` imposters, a crewmate knows the `K` are among
the other `P − 1`. The prior **redistributes the imposter budget** as the game
reveals information: an *unconfirmed* player's marginal prior is the hidden budget
over the remaining candidates — `max(0, K − |confirmed|) / (P − 1 − dead −
confirmed_alive)` — so catching one of two imposters roughly halves everyone
else's prior. `K` is derived from the player count via the game's auto formula
`(P − 3) // 2` (`sim.nim:1387`; `effectiveImposterCount`), overridable by
`belief.imposter_count`.

**Update (log-odds Bayes).** `logit(P) = logit(prior) + Σ logLR(e)` over observed
evidence `e`. Each graded cue's `logLR` is a **function of the event's features**
(duration/distance), not a flat constant — the function form + constants are the
parameterization (and learnable surface). Per type we take the **max** over the
player's events (most-suspicious instance), so an unbounded event log (§5.2) can't
inflate the posterior and there's no double-counting; and because role is a fixed
latent, evidence **persists** (no time decay — the prior is the baseline). Full
detail (the function shapes and how to fit them) lives in
[`docs/designs/suspicion.md`](docs/designs/suspicion.md) §3.

Two evidence sources, unified — a witnessed catch is just evidence with an
overwhelming `logLR` (`WITNESSED_LOG_LR = ln 1e6 ⇒ P ≈ 1`), not a special case:

- **Near-certain** (`confirmed_imposters`, persisted), from **consecutive**
  frame-to-frame transitions on the tape (§5.1): *witnessed kill* (lone
  `KILL_RANGE_SQ` neighbour of a victim alive last frame, body now) and *witnessed
  vent* — *emergence* (vent + a `VENT_WALK_MARGIN` margin was in line of sight and
  clear last frame, occupied now) or *submersion* (a player in the vent last frame
  gone while it stays in sight). "In line of sight" is the decoded `shadow` mask
  (§4.4) via `rect_visible`, so occlusion can't fake a "clear".
- **Graded functions** over the event log (§5.2): **vent dwell** (weak, ~flat past a
  pass-through), **body proximity** (log-LR *decreases* with dwell — a skilled
  imposter flees, so brief presence is the only window on a killer; a long camp is a
  reporter), and **follow-to-death** (log-LR *increases* with how long the shadowing
  of a now-dead victim lasted). A single graded cue lands below `FLEE_PROBABILITY`,
  so graded fleeing needs corroboration.
- **Social cues** (`_social_log_lr`) over the episode accusation graph +
  meeting vote history (§5 social history): *defended by a confirmed imposter*
  (up — teammates defend each other), *accused by a confirmed imposter* (down —
  imposters scapegoat crew, so their speech is inverted evidence), *crowd
  accusation* by ≥2 independent ordinary speakers (weakly up), and *voted to
  eject a confirmed imposter* (down — crew-like; the model's first exculpatory
  terms). Each is boolean per player, so the contribution is bounded.

Deliberately **excluded** as too noisy (an innocent reporter is next to the body;
crew cluster while tasking): brief proximity, single-body passing, and *task dwell*
as exculpation (imposters fake tasks).

v1 simplifications (documented for later): **naive-Bayes** independence between
evidence types, and a **marginal** (not joint) prior — the imposter budget is
redistributed as players are confirmed or die, but a proper joint model over the
full K-imposter assignment is a refinement. Chat semantics and vote history now
feed the social cues; still-future evidence for the deterministic Bayesian model:
*area-recency*, *alibi clearing*, richer cross-meeting patterns (mutual-defense
pairs, vote-bloc correlation), and an absence/exculpation model beyond the social
terms. The meeting LLM sees all of those signals as serialized context, but it
does not write back durable suspicion facts.

### 10.2 Agent location tracking (`agent_tracking.py`)

> **Full reference:** [`docs/designs/agent-tracking.md`](docs/designs/agent-tracking.md).

`update_agent_tracking(belief)` runs every tick in the fast loop after
`update_belief`. It builds a deterministic static substrate once the nav graph
exists: task/home/button anchors, pairwise A* route polylines, and a coarse
reachable occupancy grid (32px cells). For each live non-teammate, it maintains a
position distribution bounded by the speed-limited reachability disc from the
last sighting. A fresh sighting collapses that player to the observed cell; when
the player is absent, line-of-sight-visible cells are removed from their mass.

The readout sums all tracked crew into an expected-crew occupancy grid and tracks
teammate-imposter occupancy separately. Pretend aggregates the crew grid to
room-level density, subtracts teammate pressure, and commits to the chosen room
for the full Pretend window so it reaches and fakes the task instead of
periodically retargeting. Visible kills still require Hunt's existing victim
selection, trajectory lead, KillRange check, and unwitnessed gate. The
task-assignment/destination mixture from the design doc is not implemented yet;
it is the next gated stage after measuring reachability-disc accuracy and kill
impact.

### 10.3 LLM meeting decisions

Attend Meeting remains a mode, not a strategy runner: meetings intentionally slow
the game loop into a social phase, so the LLM call can run on the mode fast path
without starving movement or combat decisions. The path is opt-in and supports
three backends, selected by `CREWBORG_LLM_PROVIDER`
(`anthropic` | `bedrock` | `openrouter`); if unset, a Bedrock flag selects
Bedrock, a present `OPENROUTER_API_KEY` selects OpenRouter, else direct Anthropic:

- **Direct Anthropic API** — `CREWBORG_LLM_MEETINGS=1` plus `ANTHROPIC_API_KEY`.
- **OpenRouter** — `OPENROUTER_API_KEY` (and optionally
  `CREWBORG_LLM_PROVIDER=openrouter`). Routes through OpenRouter's
  OpenAI-compatible Chat Completions API via the `openai` SDK, so any
  `vendor/model` slug works without depending on Bedrock or a single vendor SDK;
  a present key implies `CREWBORG_LLM_MEETINGS`. Default model
  `anthropic/claude-haiku-4.5`. On the hosted runner, attach the key at upload
  time with `coworld upload-policy ... --secret-env OPENROUTER_API_KEY=sk-or-...`
  (stored in AWS Secrets Manager, injected as an env var; never baked into the
  image).
- **AWS Bedrock** — any of `USE_BEDROCK=1`, `CREWBORG_USE_BEDROCK=1`, or
  `CLAUDE_CODE_USE_BEDROCK=1`. A Bedrock flag implies `CREWBORG_LLM_MEETINGS`, so
  it alone enables the feature; credentials come from the standard AWS
  environment (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` /
  `AWS_SESSION_TOKEN` / `AWS_REGION`) rather than `ANTHROPIC_API_KEY`. On the
  hosted runner this is enabled at upload time with
  `coworld upload-policy ... --use-bedrock`.

Without a viable backend (a flag plus the matching credentials), the mode
preserves the deterministic fallback (`"no read, skipping"` once, then the
game-theory `vote_policy.fallback_vote` — the Bayesian top suspect over the
state-dependent bar, or skip).

Configuration is resolved at the strategy boundary, never inside the mode.
`RuleBasedStrategy` reads these environment variables once at construction (via
`read_meeting_params_from_env`) and stamps the result onto the Attend Meeting
directive as `MeetingParams`; the mode builds its client from those params
(`build_meeting_client`) and reads no environment itself. This keeps modes
env-free — the strategy owns all behavior configuration, the same way it gates the
aggressive imposter selector (§10).

The implementation is split into six portable pieces under `strategy/meeting/`:

- `social.py` parses each chat line into who-sus'd-who `(target, stance)` pairs
  (color mentions, longest-first; clauses classified accuse/defend by keyword) —
  `update_belief` folds them into the episode-persistent `belief.accusations`.
- `vote_policy.py` owns the game-theory vote: `imposters_remaining` (K-budget
  tracking), the state-dependent `vote_bar` with the must-eject endgame rule,
  `anti_split_swap` (near-deadline plurality convergence), and the per-role
  `fallback_vote` (crewmate top-suspect-over-bar; imposter joins the crew
  plurality on a non-teammate).
- `context.py` serializes `Belief` into explicit meeting state: timer estimate,
  self/team, legal vote targets, candidate grid, vote tally, chat transcript,
  roster, event summaries, suspicion ranking/fallback vote, the `social` record
  (accusation graph + prior meetings' votes/ejections), and the `game_state`
  payload (alive/imposter counts, must-eject flag, plurality target, anti-split
  recommendation). `valid_vote_targets`
  is the single chokepoint for vote legality — it excludes self, and, when we are
  the imposter, our teammates. Because the LLM menu, the decision validator, and
  the submit-time re-check all read it, this **enforces** the imposter prompt's
  "never vote a teammate" rule regardless of what the model returns (and safely
  yields `skip` when only teammates remain alive). This deliberately forecloses
  bussing a teammate for cover.
- `schema.py` owns the `MeetingDecision` contract and sanitizes/validates chat and
  vote targets against the current legal state.
- `prompts.py` owns the **role-specialized system prompt**, assembled from three
  independently tunable tiers: `SHARED_BOILERPLATE` (role-independent output
  contract and mechanics), `ROLE_GOALS` (per-role objective), and `ROLE_STRATEGY`
  (per-role tactics — the knob to tune). `build_system_prompt(role)` templates
  them; the crewmate and imposter strategies are edited independently and never
  touch each other or the shared contract. Imposter tactics include never voting
  or accusing a teammate, deflecting toward a plausible crewmate, and voting with
  the crew plurality; the crewmate prompt leans on `state.fallback_vote`, the
  suspicion ranking, the social record (mutual-defense pairs, inverted evidence
  from caught imposters), and the `game_state` rules (never skip in a must-eject
  endgame; converge near the deadline to avoid vote splits). Unknown /
  not-yet-revealed (`None`) / ghost (`dead`) roles resolve to the crewmate prompt
  so imposter tactics are never disclosed to a non-imposter. The client picks the
  prompt from `context.self.role` at call time, so no role plumbing is needed
  beyond the already-serialized context.
- `llm.py` owns provider-specific infra and the config seam:
  `read_meeting_params_from_env` (environment → `MeetingParams`, called by the
  strategy) and `build_meeting_client` (`MeetingParams` → client, called by the
  mode). `read_meeting_params_from_env` resolves the backend choice
  (`provider`), the API key (from a secrets-manager env var: `OPENROUTER_API_KEY`
  or `ANTHROPIC_API_KEY`), and the model: the direct path defaults to
  `claude-haiku-4-5-20251001`, Bedrock to the inference-profile ID
  `us.anthropic.claude-haiku-4-5-20251001-v1:0`, and OpenRouter to the
  `vendor/model` slug `anthropic/claude-haiku-4.5`; any is overridable via
  `CREWBORG_LLM_MODEL`. The client builder selects `AnthropicMeetingClient`
  (direct `Anthropic` or `AnthropicBedrock`, chosen from the provider) or
  `OpenRouterMeetingClient` (the `openai` SDK pointed at
  `https://openrouter.ai/api/v1`); the underlying SDK client is constructed
  lazily on the first call. Bedrock needs the optional `boto3` dependency (the
  `players` distribution's `bedrock` extra); OpenRouter needs the `openai`
  package (in the crewborg image's `requirements.txt`).

`MeetingDecision.action` is one of `send_chat`, `set_tentative_vote`,
`submit_vote`, or `wait`. A tentative vote is stored in mode-local state and is
auto-submitted near the deadline; `submit_vote` casts immediately. The mode calls
the LLM on meeting start, new external chat, chat-cooldown readiness, and deadline
pressure, with a small tick interval to avoid repeated calls from one visual
state. Distinct chat messages can be sent across the same meeting; duplicate model
text is suppressed.

A meeting client that reports `enabled` but whose calls keep failing (an ungated
404 model, a bad key, a network outage) must not bypass the deterministic
chat→vote path and cost us our vote. On a permanent error (HTTP 401/403/404) or
after two failures in an episode, the mode latches `_llm_disabled_for_episode`
and routes every subsequent meeting tick — in this and later meetings of the same
episode — through the deterministic fallback, tracing `meeting_llm_disabled`.

---

## 11. Package layout and tracing

```
crewborg/
  __init__.py        # build_runtime(): assemble AgentRuntime
  agent_tracking.py  # reachability-disc location beliefs + coarse occupancy search
  types.py           # the six types + perceive/update_belief
  action.py          # action layer: stateful resolve_action, composite execution, momentum + button FSM
  nav.py             # baked-map nav graph + route planning (used by the action layer)
  trace.py           # stderr JSON trace + metrics sinks
  events.py          # CrewborgEventTracer: on_step_complete hook emitting domain.* events
  modes/             # idle, normal, crewmate_ghost, dick_mode, attend_meeting, report_body, flee, evade, pretend, search, hunt
  strategy/          # rule_based.py: mode selector; suspicion.py: near-certain detection; event_log.py: per-player observation log; occupancy.py: tape predicates; opportunity/trajectory
  perception/        # Sprite-v1 scene decoder: maintain tables, resolve objects → (label, world xy)
  map/               # vendored croatoan.resources + ported parser (§6)
  coworld/           # policy_player.py (bridge), Dockerfile, entrypoint.sh
  viewer/            # browser UI for inspecting trace-driven agent-perspective replays
  scripts/play_local.sh
  build.sh
  tests/             # action/modes/strategy/trace/runtime + bridge smoke + scene-decode tests
  AGENTS.md  design.md  README.md
```

**Tracing.** Stdout = protocol channel, stderr = logs/traces. Hosted Coworld logs
are capped, so the policy bridge (`coworld/policy_player.py`) uses a lean stderr
trace by default. It keeps durable domain events and low-volume mode boundary
events, but filters per-tick SDK framework noise such as `perception`,
`belief_updated`, `action_intent`, `act_command`, `snapshot_submitted`,
`strategy_evaluated`, and repeated directive traces. The full framework stream is
still available with `CREWBORG_TRACE=debug` or `CREWBORG_TRACE=viewer`.
The bridge also supports targeted log streams without full debug volume:
`CREWBORG_TRACE_GROUPS` names event families, `CREWBORG_TRACE_INCLUDE` /
`CREWBORG_TRACE_EXCLUDE` accept comma-separated glob patterns, and
`CREWBORG_TRACE_DECISION_FIELDS` trims `decision_snapshot` to selected top-level
fields. Event shorthands without `domain.` expand to both the literal name and
the `domain.` event name, so `meeting_*` and `vote_cast` are valid filters.

Crewborg's own game-level events are emitted through the SDK's **domain-event
seam** (`EventEmitter` + `AgentRuntime(on_step_complete=…)`): `CrewborgEventTracer`
(`events.py`) is wired as the `on_step_complete` hook and, from each tick's
`StepContext` (finalized belief + chosen intent + produced command), emits these
`domain.`-prefixed events:

- *state / outcome* (belief & action-state deltas): `phase_change`,
  `role_resolved`, `body_sighted`, `task_completed`, `kill_landed`, `vote_cast`.
- *attempt* (keyed on the wire command's button edge): `task_started`,
  `kill_attempted`, `report_attempted`, `vent_attempted`, `chat_sent`.
- *knowledge layer* (the per-player event log §5.2 + the suspicion reasoning
  §10.1 *behind* the actions — read off the finalized belief so `strategy/` stays
  pure). Always on, lean enough for the tournament: `player_event` when a new
  observation interval opens on someone's log; `player_died` on an alive→dead
  transition; `imposter_confirmed` / `believed_changed` when the suspicion sets
  move; and a full `suspicion_snapshot` (ranked posteriors + each suspect's event
  log + the would-be vote and the bar) at the start of every meeting — the single
  record that explains a vote after the fact.
- *location tracking* (§10.2): `occupancy_substrate` once the static grid/polylines
  are built, `occupancy_reacquired` when a lost player re-enters view
  (predicted-vs-actual cell and distance error), and `occupancy_seek_target` when
  the imposter's hottest search cell changes.
- *decision audit* (debug only): `decision_snapshot` links the active
  mode/directive, symbolic intent, held mask, self position, currently visible
  players/bodies, believed/confirmed threats with last-seen age and flee gate
  distances, and task/flee/nav geometry. It is one record per tick, so it is
  useful for single-game forensics but too noisy for capped hosted logs.
- *trace replay viewer* (opt-in via `CREWBORG_TRACE=viewer` or `debug`):
  `viewer_map` emits static map geometry, `viewer_occupancy_grid` emits the
  reachable coarse grid once available, and `viewer_frame` emits one browser-ready
  frame per tick with active mode + directive params, current intent, command,
  camera/self, nav route/target, roster/body/task beliefs, and the live occupancy
  grid.

Countable outcomes/attempts also emit a matching `domain.*` metrics counter when
a metrics sink is enabled. Hosted Coworld runs leave metrics off by default; set
`CREWBORG_METRICS=1` to include counters/gauges without enabling full debug, or
`CREWBORG_TRACE=debug` to enable both metrics and the full debug trace.
`kill_attempted` (we pressed) is distinct from `kill_landed` (the kill registered,
seen as the kill-ready→cooldown edge). Incoming meeting chat is decoded into
`belief.chat_log` (§4.3) and emitted once per meeting line as `chat_received`.
When the meeting LLM is enabled, `meeting_context_serialized`,
`meeting_llm_decision`, fallback reasons, `meeting_llm_disabled` (when repeated
or permanent call failures latch the episode onto the deterministic fallback),
and selected chat/vote are in the default trace; the LLM latency histogram is
emitted when metrics are enabled. Raw LLM request/response tracing is opt-in via
`CREWBORG_LLM_TRACE_RAW=1` or `CREWBORG_TRACE=debug`.

**Viewer/debug verbosity.** `CREWBORG_TRACE=viewer` is opt-in and heavy: it emits
the `viewer_*` records used by [`viewer/index.html`](./viewer/index.html) to draw
agent-perspective replays over the map, and the bridge stops filtering SDK
framework traces. `CREWBORG_TRACE=debug` includes those viewer records plus the
deeper per-tick debugging stream: `decision_snapshot`, the entire live
`P(imposter)` vector each tick (`suspicion_tick`), `suspicion.top_p` /
`suspicion.believed_count` gauges, `kill_state`, and `occupancy_snapshot` with
the top grid cells plus per-agent support sizes. Off by default; the lean deltas,
meeting actions, chat/vote decisions, and meeting suspicion snapshots above are
what ships in the tournament image.

**Targeting examples.** `CREWBORG_TRACE_GROUPS=voting` keeps meeting/vote/chat
events and meeting suspicion snapshots. `CREWBORG_TRACE_GROUPS=action` keeps
domain action attempts plus SDK `action_intent` / `act_command` boundaries.
`CREWBORG_TRACE_GROUPS=decision` with
`CREWBORG_TRACE_DECISION_FIELDS=mode,intent,command` emits one compact per-tick
decision record. `CREWBORG_TRACE_INCLUDE=meeting_*,vote_cast` keeps only matching
events. `CREWBORG_TRACE=debug` with
`CREWBORG_TRACE_EXCLUDE=domain.viewer_*,domain.decision_snapshot` keeps full debug
except the specified noisy families. Supported groups are `lean`, `state`,
`action`, `voting` / `meeting`, `chat`, `llm`, `knowledge`, `suspicion`, `kill`,
`occupancy`, `decision`, `viewer`, `framework`, `mode`, `task`, `belief`, `debug`,
and `all`.

Putting emission in `on_step_complete` (not a mode) is deliberate: the attempt
events key on the produced `command`, which modes never see, and `task_completed`
is concluded inside Normal mode's `decide`, so both are only observable after the
mode has run (§7.3).

---

## 12. Tuning parameters

The behavior parameters below are implemented with these defaults; none is
structural, and each still awaits tuning against a live server.

| Parameter | Current default |
|---|---|
| Movement-controller style | bang-bang + a release-near-target deadband with a predictive stop — release an axis within the estimated momentum stopping distance so the agent coasts onto the target instead of overshooting |
| Path clearance | `CLEARANCE_RADIUS = 2` px config-space margin (routes keep off walls) |
| Re-plan cadence | `REPLAN_INTERVAL = 8` ticks (re-root the route at the live position; A* ≈ 0.2 ms) |
| Voting policy | game-theory vote policy (`strategy/meeting/vote_policy.py`): crewmate votes the highest-posterior live non-self/non-teammate suspect over a **state-dependent bar** — 0.75 at margin ≥ 4, 0.8 at margin 3, 0.9 at margin ≤ 2, and **0 in a must-eject endgame** (crew within one of parity: a skip loses to the next kill) — else **skip**; within `ANTI_SPLIT_REMAINING_TICKS = 96` of the deadline a trailing vote swaps onto the plurality if it is plausibly guilty (`ANTI_SPLIT_MIN_PROBABILITY = 0.3`); the **imposter** joins the crew plurality on a non-teammate (else skips). Always cast *something* before the timer (not voting costs −10): auto-submit at ≤ `AUTO_SUBMIT_REMAINING_TICKS = 72`, and the action layer's last-resort confirm (§9) bounds the cursor walk |
| LLM meetings | opt-in with `CREWBORG_LLM_MEETINGS=1` + `ANTHROPIC_API_KEY` (direct), or a Bedrock flag (`USE_BEDROCK=1` / `CREWBORG_USE_BEDROCK=1` / `CLAUDE_CODE_USE_BEDROCK=1`) + AWS env credentials; default model `claude-haiku-4-5-20251001` (direct) or `us.anthropic.claude-haiku-4-5-20251001-v1:0` (Bedrock), overridable via `CREWBORG_LLM_MODEL`; deadline LLM prompt at ≤96 ticks remaining and auto-submit at ≤72 ticks remaining (the backstop runs ahead of every path — LLM, deterministic, or failing client); chat cooldown is 100 ticks |
| Aggressive imposter selector | opt-in with `CREWBORG_BE_DUMB=1` or `BE_DUMB=1`; during `Playing`, imposters skip Pretend/Evade/ReportBody and always select Search unless kill-ready with a visible victim, then Hunt |
| Dick Mode selector | opt-in with `CREWBORG_DICK_MODE=1` or `DICK_MODE=1`; live crewmates make one emergency-button call when `ticks_until_kill_ready <= DICK_MAX_BUTTON_TRAVEL_TICKS + DICK_KILL_COOLDOWN_BUFFER_TICKS`; `DICK_MAX_BUTTON_TRAVEL_TICKS = 600`, `DICK_KILL_COOLDOWN_BUFFER_TICKS = 10`; chat `haha, fuck you imposters` only when our recorded button press opened the meeting, skip-vote, then resume; refused calls time out after `DICK_CALL_NO_MEETING_GRACE_TICKS = 48` and do not re-arm |
| Report policy | crewmates always report visible bodies; imposters evade for `EVADE_TICKS = 72` after their own kill, then may report a non-fresh visible body (§7.2). Suspicion-aware reporting is a possible refinement |
| Pretend fake-task hold | one task-time (`TASK_TICKS = 72`) held at the station, then re-dispatch |
| Pretend room targeting | room score = expected crew density minus teammate-imposter pressure (`TEAMMATE_ROOM_PENALTY = 3.0`); choose a real task station in the selected room; keep a chosen room for `ROOM_TARGET_MIN_TICKS = 10000` unless arriving or being preempted |
| Kill isolation bar | clearance `BASE_ISOLATION_RADIUS = 48` px and witness window `WITNESS_WINDOW_TICKS = 72`, both relaxed to zero by urgency `URGENCY_FULL_TICKS = 240` |
| Search lead | enter Search `SEARCH_LEAD_TICKS = 100` before the kill is ready. Time-to-ready is reconstructed from the binary HUD: a learned `kill_cooldown_estimate` (or `DEFAULT_KILL_COOLDOWN_TICKS = 900` until measured) from the tracked cooldown start |
| Hunt victim tracking | Hunt requires a visible victim; Search may follow a committed victim seen within `TRACK_WINDOW_TICKS = 120`; trajectory lead is capped at `MAX_LEAD_TICKS = 24` (velocity from sightings ≤ `VELOCITY_MAX_DT = 4` apart, `AGENT_SPEED_PX = 3`) |
| Hunt teammate claim | prefer an unclaimed victim when a teammate-imposter seen within `TRACK_WINDOW_TICKS` is closer to another victim inside `TEAMMATE_CLAIM_RADIUS = 80` px |

---

## 13. Operational notes

- Confirm `showTaskArrows` is enabled in the target episode config; if not,
  off-screen task tracking uses the room-by-room sweep (§5).
- Vent and emergency-button locations are not exposed over the protocol (no stream
  message, no HTTP endpoint — the manifest only names the server-side resource
  path). A bot author without game-repo access cannot obtain them. Worth
  surfacing upstream to Crewrift (e.g. emit them as labeled zero-size objects).
