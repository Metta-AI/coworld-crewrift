# Crewborg — Design Specification

Crewborg is a Player-SDK agent (the SDK is the `players.player_sdk` package,
imported from the public `players` install, which tracks `main`) that plays **Crewrift**, a Coworld
social-deduction game (Among Us–style: crewmates do tasks and vote; imposters
kill, vent, and blend in). This document is the implementation spec.
For orientation see [`README.md`](./README.md); for game constants, the Sprite-v1
wire protocol, and source pointers see the player-directory top-level `AGENTS.md`.

> **Status:** This spec is implemented end-to-end for both roles. Attend Meeting
> now has an opt-in LLM chat/vote path ([§10.3](#103-llm-meeting-decisions)) with
> deterministic fallback, and the tuning parameters in
> [§12](#12-tuning-parameters) await tuning against a live server.

Conventions: paths like `sim:2464` cite the Crewrift Nim source (`sim` =
`src/crewrift/sim.nim`, `global` = `src/crewrift/global.nim`, `protocols.nim` =
`players/notsus/notsus/protocols.nim`), all in the
`Metta-AI/coworld-crewrift` repo.

---

## Contents

| § | Section | Governs / key files |
|---|---|---|
| 1 | [Architecture](#1-architecture) | three-tier strategy→mode→action stack, the per-tick fold, invariants |
| 2 | [Types](#2-types) | the six SDK type parameters — `types.py` |
| 3 | [Transport & bridge](#3-transport--bridge) | websocket + Sprite-v1 decode + reconnect — `coworld/policy_player.py`, `coworld/scene.py` |
| 4 | [Perception](#4-perception) | scene → labelled world-coord percepts — `perception/` |
| 5 | [Belief](#5-belief) | world model, perception tape, per-player event log — `types.py` |
| 6 | [Static map](#6-static-map-resource-file-bake) | baked nav graph + occupancy — `map/`, `nav.py`, `navbake.py` |
| 7 | [Modes](#7-modes) | the behavioral stances per role — `modes/` |
| 8 | [Intents](#8-intents) | the symbolic intent vocabulary |
| 9 | [Action layer](#9-action-layer) | intents → wire commands, momentum/button FSMs — `action.py` |
| 10 | [Strategy (mode selector) + suspicion](#10-strategy-mode-selector) | rule-based selector · Bayesian P(imposter) · agent tracking · LLM meeting/commander — `strategy/` |
| 11 | [Package layout & tracing](#11-package-layout-and-tracing) | the file map + `domain.*` trace events |
| 12 | [Tuning parameters](#12-tuning-parameters) | constants + the `CREWBORG_*` env-flag surface |
| 13 | [Operational notes](#13-operational-notes) | runtime gotchas |

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

1. Read `COWORLD_PLAYER_WS_URL` (runner fills `?slot=N&token=…`);
   `websockets.connect(url, max_size=None)` — token validation is at HTTP upgrade.
2. Maintain a **`SceneState`** (a plain dataclass, owned by the bridge): three
   retained tables plus the decoded camera, walkability mask, and `shadow`
   line-of-sight mask.
3. Per tick: block for one binary message — each message is a complete frame (the
   decoder applies all of its concatenated sub-packets) — apply it to
   `SceneState`, then run `runtime.step(observation)` and send the result.
4. Close the socket ⇒ game over; exit cleanly.

**Imposter seeking/positioning — NEW APPROACH (2026-06-24, James; supersedes occupancy-density seeking).**
Diagnosis (event-warehouse, controlled XP data vs Aaron/Andre): crewborg's kill execution is best-in-field
(19% isolation→kill) but it is *near a crew member only ~half as often* as the top imposters (6.6 vs ~12
proximity intervals/game) and gets isolation-with-crew half as often (1.84 vs ~4.4/game; zero in 21% of games
vs 8-9%). Root cause: the old occupancy system **diffuses** every unseen crew's position over a growing
reachability disc, sums them, and seeks the **densest cell/room** — which (a) drifts to the central hub
(diffusion centroid) and (b) by definition heads toward *crowds*, the worst place to find someone ALONE.

The new principle: **don't gamble on an up-front shadow target — preserve optionality.** Stay WITH the group
(keep many potential victims reachable) and only **commit to following an individual once it clearly peels off
the group into isolation**; then shadow that straggler until Hunt can strike. Staying glued to the group
forever gets us frozen out (no isolation); committing to one target early is a coin-flip (we can't tell good
targets from group-stickers, and may chase someone who rejoins). Following the group until a clean peel-off
emerges resolves both. NB the old `select_victim` ("pick the single most-isolated visible crew right now and
commit") is the up-front-gamble anti-pattern for *seeking* — it stays only for Hunt (the strike, after a peel
-off is already chosen). Never follow the teammate imposter. The occupancy substrate is retained as a cold
-start fallback (seen nobody yet → explore the low-traffic PERIPHERAL rooms where stragglers isolate, not the
hub). Implementation in progress; the prior Pretend/Search logic is cold-stored under
`modes/_deprecated/` (DO NOT USE).

**Aggressive initial-connect reconnect (§3.1).** Hosted episodes were failing at a
high rate with a `-100` `connect_timeout`: the symptom (verified from artifacts) was
**0–1 telemetry lines, no stderr, episode never reaching "running"** — i.e. the player
**never received a frame**. That is an *initial-connect race*: the container starts
before the engine's `/player` socket accepts, the single `connect()` throws, and the
process exits, failing the episode. The bridge now **retries the initial connection**
on a short **flat interval** (`RECONNECT_INTERVAL_SECONDS`, env
`CREWBORG_RECONNECT_INTERVAL`, default 0.1s ⇒ ~10 attempts/sec) — *no* exponential
backoff, because this is a startup race and we want to catch the engine the instant
it binds rather than drift to multi-second waits — until the first frame arrives,
bounded by `RECONNECT_DEADLINE_SECONDS` (env `CREWBORG_RECONNECT_DEADLINE`, default
120s) so it can never hang past the runner's episode timeout (~1200 attempts over the
default deadline; the per-attempt log is throttled to the first 3 so a slow start
doesn't flood the policy log). The discriminator is **`frames_seen`**: a close/error *before*
any frame is a connect race → retry on the flat interval.

**Mid-game reconnect (§3.1, 2026-06-26).** A drop *after* ≥1 frame is **ambiguous** — it's
how the engine signals game-over (abrupt 1006), but it's also what a transient mid-game
network blip looks like, and the old "after a frame ⇒ exit, never reconnect" rule turned
every such blip into an unrecoverable `-100` `disconnect_timeout`. The bridge now **tries to
reconnect a few times before concluding game-over**: a reconnect that delivers *new* frames
means the game was still live (we resume and keep playing); a run of **`MIDGAME_RECONNECT_ATTEMPTS`**
(env `CREWBORG_MIDGAME_RECONNECTS`, default 5) reconnects with *no* new frames means the game
really ended (exit 0). New frames refresh the idle budget, so a long game survives several
independent blips; `RECONNECT_DEADLINE_SECONDS` is the backstop against an adversarial engine
that dribbles one frame per reconnect. Interval is `MIDGAME_RECONNECT_INTERVAL_SECONDS` (env
`CREWBORG_MIDGAME_RECONNECT_INTERVAL`, default 0.25s — slightly more patient than the startup
race, to let a blip clear). A *clean* close after frames is still a normal game-over (the
engine only drops *unclean* mid-stream). Session state (`scene`, masks, tick offset) lives in
`_BridgeState` outside the connection so a retry resumes rather than rebuilding belief. **Scope note:**
this only fixes *our own* connect failures (~half the observed `-100`s in the
2026-06-24 sweep); episodes where *opponents* fail to connect still go degenerate and
are unfixable from our side. Eventually this transport+reconnect layer should move into
the player SDK as a shared Coworld module (see root `TODO.md`).

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

Color names (16) and the full label vocabulary are defined by the game's `/player`
renderer (`src/crewrift/global.nim`) and consolidated in the top-level `AGENTS.md`.

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
- **tasks** — assigned task indices (from `task_signals` ids), per-task world
  location (from the map), per-task completion; `crew_tasks_remaining`;
  `task_arrows_enabled` (below).
- **phase** — current phase + start tick + the phase state machine, advanced from
  `phase_signals` (emit a `phase_change` trace on transition).
- **voting** — live tally, cursor, timer, who has voted.
- **social / evidence** — `suspicion[color]` = the Bayesian posterior **P(imposter)**
  ∈ [0, 1] per other player (combinatorial prior + likelihood-ratio updates, including
  witnessed kills/vents logged as `kill`/`vent_use` event-log entries that contribute
  an overwhelming latched LR — there is no separate "confirmed" set), and
  `believed_imposters` (alive colors over the near-certain `FLEE_PROBABILITY`, kept as
  belief state that seeds the vote).
  `imposter_count` (K) overrides the player-count-derived default. Maintained each
  tick by the suspicion model (§10.1). The opt-in meeting LLM (§10.3) consumes the
  `chat_log`, vote tally, roster, and suspicion posterior for chat/vote decisions.
- **agent tracking** — `agent_tracking` holds a static occupancy substrate
  (anchors, pairwise route polylines, coarse reachable grid) plus per-player
  reachability-disc location estimates, a separate teammate-imposter estimate,
  and the latest expected-crew occupancy grid. It is maintained after perception
  folding and feeds imposter Evade re-approach room selection and pre-kill
  search (see §10.2).
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
| `tailing_self` | sustained within `TAIL_SELF_RADIUS` (64 px) of **us** | `target_color = None` (me), `min_dist` |
| `kill` | witnessed kill, a **point** event (tape transition, §5.1) | `target_color` (victim) |
| `vent_use` | witnessed emerge/submerge, a **point** event (tape transition, §5.1) | — |

The last two are **point** events written by the near-certain detectors (§10.1), not
durative observations — they carry the overwhelming witnessed LR, so a caught player's
posterior latches at P ≈ 1 without a separate "confirmed" set.

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
*acting* on it (suspicion → Accuse / vote) is crewmate-gated. Meeting chat stays in
`chat_log` (§4.3) for now — a unified per-player view can merge the two later.
The graded suspicion layer (§10.1) already consumes a conservative subset of these
events (`vent`/`near_body`/`proximity`/`tailing_self`, plus the witnessed `kill`/
`vent_use` point events); `near_body` is sound because `belief.bodies` is cleared when
a meeting opens (matching the server), so it never fires on a stale body location.

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
`walkability map` alpha (decoded once); the nav graph is built over it (`nav.py`) —
normally **loaded from the offline bake** (see "Offline nav bake" below), built live
only as a fallback. Because Crewrift collides the player as a **1×1 point**
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

**Offline nav bake (no per-run rebuild).** The nav graph *and* the occupancy
substrate (§10.2 — anchors + pairwise route polylines, an O(anchors²) A* sweep) are
pure functions of the static walkability mask + baked map, but building both is a
heavy pure-Python pass: **~14s on the first tick under the hosted 250m-CPU budget**,
which froze the agent at spawn while the real-time 24 Hz engine streamed ~330 frames
ahead (it then drained a stale backlog — the "slow to leave start" symptom). Since
there is one static map, we bake both **once, offline** into a vendored asset
(`map/croatoan_navbake.pkl.gz`, ~186 KiB) via `tools/build/nav_bake.py`, and
load it on the first tick instead (`navbake.py`; ~0.1s vs ~29s in a 250m container —
a ~280× cut, with the loaded graph/substrate byte-identical to a live build). The
load **validates** the streamed mask still matches the baked one and, on any
mismatch / missing asset / load error, **falls back to the live build** with a
warning — correctness never depends on the asset, and a mismatch is the signal to
re-run the bake (capture a fresh mask with `CREWBORG_CAPTURE_WALKABILITY=1`, then
`nav_bake.py extract-walkability … && nav_bake.py bake …`, then rebuild the image).

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
| **Attend Meeting** | phase = `Voting` | when there's a **clear leading suspect** (`top_suspect`, §10.1): **accuse then vote** them — `chat("<color> sus: <reasons>")` citing the ranked event-log evidence, then `vote` that color; on a **flat field** stay **silent and skip**. No default-firing opener; chat and vote are coupled. Always casts something before the timer |
| **Report Body** | a body is in view | `report(body_id)`; yields when a meeting opens |
| **Accuse** | an **active tail** by a suspect over `ACCUSE_THRESHOLD` (`active_tail_suspect`, §10.1), one button call left | `call_meeting` — walk to the emergency button and press it; the meeting that opens accuses + votes the tail. Replaces the old Flee/keep-away mode |

### 7.2 Imposter modes

| Mode | Active when | Intents emitted |
|---|---|---|
| **Search** | the imposter's **always-on seeking stance** (default when not evading / reconning / hunting; Pretend retired 2026-06-24) | pick a random nearby task room, go watch it, and when a crewmate **leaves** that room follow them to their next room — using **path prediction** (`strategy.path_prediction`) to keep chasing down the right hallway after they leave view. Keeps us *near crew* so a kill window opens (which is when Hunt takes over) |
| **Recon** | not ready, but the kill comes off cooldown within `recon_window()` ticks (`CREWBORG_RECON_WINDOW`, default 100) **and** a crewmate has been seen | **beeline to the most-recently-seen non-teammate crewmate** (`modes/recon.py`; live position when visible, last-known otherwise) so a victim is in hand the instant we can kill → Hunt fires immediately. Built 2026-06-25 from the warehouse finding that we had a crew in view *at* cooldown-ready only 53% of the time vs Aaron's 83%. Deliberately short window for now (a long one = the over-extension that gets caught) |
| **Hunt** | kill ready **and** a victim is visible | **commit to a visible victim and close/strike**: `select_victim` picks the most-isolated reachable visible crewmate, preferring targets not already claimed by a closer teammate; navigate to its **predicted intercept** (`strategy.trajectory` — lead a moving target); when in KillRange *and* unwitnessed → `kill`, else keep shadowing in range (lie in wait) |
| **Evade** | for `EVADE_TICKS` after our own kill | **beeline toward the most-populated area** — the densest expected-crew room (occupancy grid §10.2, minus teammate pressure), else the hottest occupancy cell, else the last-seen crewmate (cold start). **Rewritten 2026-06-26**: the old Evade *fled* (vent away / walk off the corpse), feeding post-kill drift. New Evade *re-approaches* crew so a victim cluster is nearby when the window hands back to Search/Recon. **Paired with Hunt's drop-the-witness-check-after-first-kill** (§10): re-approaching the crowd only pays off once witnesses no longer veto the 2nd kill (else the crowd is a witness-rich dead end) — the two are evaluated together |
| _(Report Body)_ | **removed from the imposter gate 2026-06-25** | Imposters **never report bodies**. Self-reporting our own kill opened a meeting that reset the cooldown and killed snowball kills (~79% of our body-report meetings were self-reports; warehouse, §perf). `report_body` is now **crewmate-only**. |
| **Attend Meeting** | phase = `Voting` | **deflect onto crewmates, never a teammate** (§10.4): proactively accuse + vote a non-teammate who genuinely *looks* sus (real cues, same format as a crewmate); else wait and **bandwagon** onto a crewmate others suss/vote, citing *fabricated* safe cues in the identical format; else skip at the deadline |

**Search is the imposter's always-on seeking stance** (`modes/search.py`, rebuilt
2026-06-24; the prior occupancy-density Pretend/Search is cold-stored at
`modes/_deprecated/`). Motivation (event-warehouse diagnosis): crewborg was *near a
crew member only ~half as often* as the top imposters — its kill execution is
best-in-field, it just isn't present for enough natural isolation moments. Search
attacks that directly by staying with crew. It does **not** kill; it keeps us
positioned so that when the cooldown clears and a victim is visible, the selector
flips to Hunt. A small FSM:

```
   PICK_ROOM ──▶ GO_TO_ROOM ──arrived,crew──▶ WATCH ──a crewmate leaves──▶ FOLLOW
       ▲              │ arrived,empty            │ no crew left            │
       └──────────────┴──────────────────────────┴── target lost ─────────┘
                         (FOLLOW: settles in a room w/ crew → WATCH)
```

| State | Behaviour | Transitions |
|---|---|---|
| **PICK_ROOM** | choose a **random** nearby reachable task room (nearest `NEARBY_ROOMS`, excluding the current and just-left room, and the start room) | → **GO_TO_ROOM** |
| **GO_TO_ROOM**(room) | `navigate_to` the room **centre** (go fully inside to check it, not stand at the door) | arrived + crew in room → **WATCH**; arrived + empty → **PICK_ROOM**; a crewmate seen leaving anywhere → **FOLLOW** |
| **WATCH**(room) | hold the in-room **vantage point with line-of-sight to the most crew** — recomputed (throttled, with hysteresis) as they move, so we keep crewmates in sight rather than standing at the entrance and letting them walk out of view. LOS via `nav._segment_clear` over the walkability mask, within `VANTAGE_RANGE` | a watched crewmate **leaves** the room → **FOLLOW**(them); no watched crew still around → **PICK_ROOM** |
| **FOLLOW**(crew) | chase the leaver: `navigate_to` their live position while visible; once **occluded**, feed `PathPredictor` and steer to its **top predicted route position** (chase down the hallway) | settles in a room we reach with crew → **WATCH**; target dead/teammate/lost (`FOLLOW_LOST_TICKS` unseen, no prediction) → **PICK_ROOM** |

Notes: **never follows the teammate imposter** (filtered from crew everywhere). The
path predictor is fed **only what we actually see** (the target's position when
`last_seen_tick == now`, else `None`) — identical to how it is scored offline
(`strategy.path_prediction` + `tools/path_prediction_*`). Room choice uses a seeded
RNG so two imposters don't deterministically converge on the same room.

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
opened, target dead — and changes the intent. A ghost crewmate keeps Normal mode +
`complete_task` (it can still finish its own tasks).

### 7.4 Possible refinements

Mode-level enhancements to keep in view: arrow-bearing **task triangulation** under
arrows-only; **travelling-salesman** task ordering over the nav graph; **safety in
numbers** (prefer routes/tasks near other crewmates); **strategic flee targets**
(toward a trusted player / the button / a sightline-breaking corner); richer
**imposter coordination** (shared claims, role assignment, or bluff-aware spacing
beyond the current local teammate-pressure/claim heuristic). Victim commitment,
the most-isolated visible-target pick, trajectory-led interception, lead-window
Search, and lightweight teammate avoidance are now implemented. Several of the rest —
safety-in-numbers, strategic positioning, and imposter spacing — are the planned remit
of the **LLM gameplay commander** (§10.6): rather than hard-coding them, the modes read
`belief.commander` priorities at their candidate-ranking steps and bias toward them.

---

## 8. Intents

An intent is "what to do now" — above a button press, below a behavior. One
**shared vocabulary** serves both roles; modes differ only in which they emit.

| Intent | Carries | Meaning |
|---|---|---|
| `idle` / `loiter` | (optional anchor) | stand still / wander to blend in |
| `navigate_to` | world point | go to a point |
| `complete_task` | task index | go to the task rect and complete it |
| `report` | body id | go to a body and report |
| `call_meeting` | (target color, forensic) | walk to the emergency button and press A inside its rect to call a meeting (crewmate Accuse) |
| `vote` | choice (player id / skip) | cast a meeting vote |
| `chat` | text | speak in a meeting |
| `kill` | target player id | go to a crewmate and kill (imposter) |
| `vent` | vent / group target | go to a vent and use it (imposter) |
| `escape` | world point | flee to a point, vanishing through a vent if one is on the fast route (imposter) |

`call_meeting` mirrors `report` (navigate to the button anchor, then a fresh A press
fires `tryCallButton`); it carries the suspect color only for forensics — the meeting
re-derives the vote from suspicion. `escape` is the imposter's vent-aware flight: the
action layer plans a vent-aware route to the point, so the only way an agent uses a
vent in transit is an imposter emitting `escape` (crewmate routes never touch the
teleport edges).

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
- `report` / `kill` → navigate to the body/target (a dynamic point, no anchor),
  then edge-press A.
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
- Vote-cursor stepping then A-confirm.
- Chat buffering + ASCII validation (emit only during Voting).
- Hand the held mask to the bridge, which de-dups (send-only-on-change).

**`ActionState` holds:** the current intent (for the diff), the active nav route +
progress cursor (+ which legs are vent teleports), the A-press FSM state, and the
pending-chat buffer.

`Command` carries the per-tick wire payload (input packet ± chat); an empty payload
means "send nothing this tick."

---

## 10. Strategy (mode selector)

The strategy **selects the mode** (modes pick intents). For v1 it is a
deterministic `Strategy.decide(snapshot) -> ModeDirective` run via
`SynchronousStrategyRunner` **every tick** — pure rules over belief. The
`AsyncStrategyRunner` LLM seam stays available for future mode-selection
experiments, but the implemented LLM behavior is currently scoped to the
meeting-mode chat/vote path (§10.3). A **planned LLM gameplay commander**
(§10.6, designed) extends this differently: it does **not** select the mode —
it runs asynchronously and writes *priorities* into belief that the modes read
to bias their execution.

Because the selector runs every tick, **v1 uses no reflexes** — transitions ("body
sighted → Report", "Voting → Attend Meeting") are re-evaluated each cycle. The
default directive is `idle` mode (the stall/TTL fallback, rarely reached).

**Crewmate selection** (priority order):

1. phase = `Voting` → **Attend Meeting**; `RoleReveal`/`Lobby`/`GameOver` → **idle**
   (`Lobby`/`RoleReveal` also reset the per-game button-call budget)
2. body in view → **Report Body** (a body report opens a meeting right here and
   doesn't spend our one button call, so it outranks accusing)
3. an **active tail** by a suspect over `ACCUSE_THRESHOLD` (`active_tail_suspect`),
   with a button call left → **Accuse**: drop tasks and go slam the emergency button.
   The selector **commits** to the target (stays in Accuse through the walk even if
   the tail briefly lapses, until it's voted out / dies), and marks the one-shot call
   spent once we reach the button so we fall back to tasks instead of looping there.
4. otherwise → **Normal** (ghosts stay in Normal to finish own tasks)

**Imposter selection** (priority order):

1. phase = `Voting` → **Attend Meeting**
2. just killed → **Evade** for `EVADE_TICKS` (beeline to the densest expected-crew area — re-approach, not flee; paired with Hunt's post-first-kill witness drop)
3. kill ready **and** a visible victim → **Hunt** (commit + close, strike when isolated)
4. not ready but within `recon_window()` of ready **and** a crewmate has been seen →
   **Recon** (beeline to the most-recently-seen crewmate so a victim is in hand at ready)
5. otherwise → **Search** (always-on seeking: watch a room, follow the first crewmate who leaves)

Imposters **never report bodies** (removed 2026-06-25 — see the mode table) and **never
Pretend** (retired 2026-06-24): once Evade ends we go straight back to the kill loop
(Hunt / Recon / Search).

(3) fires only when the kill is ready and a live non-teammate is visible. Hunt then
commits to a victim (§7.2), firing the kill only when it would go **unwitnessed**.
The witness bar relaxes with **urgency** — `last_tick − kill_ready_since_tick`, how
long we have been able to kill without doing so — shrinking the required clearance
radius and the witness-staleness window to zero by `URGENCY_FULL_TICKS`, at which
point the imposter strikes regardless of witnesses. **After our FIRST kill Hunt
drops the witness check entirely** (the strike gate skips `unwitnessed` once
`belief.last_kill_tick is not None`; James 2026-06-26): getting a **second** kill is
the imposter's core job — two imposters × two kills each = the four that reach parity —
and at the second ready we are usually already close to crew (meeting-aware @ready:
~49px, in-view ~57%), so the bottleneck is **conversion, not stealth**; a witnessed
second kill (paid for by a later ejection) beats a clean kill we never get. When no
visible victim is available, Search owns acquisition during the lead window rather than
Hunt chasing stale targets.

**Aggressive experiment.** `CREWBORG_BE_DUMB=1` (or `BE_DUMB=1`) replaces the
imposter `Playing` selector with only **Search**/**Hunt**: if kill-ready with a
visible victim, Hunt; otherwise Search. This deliberately skips Evade
and imposter body reports so hosted/local runs can isolate whether always preparing
to kill improves imposter outcomes versus the default blend-in policy.

### 10.1 Suspicion — Bayesian P(imposter) (`strategy/suspicion.py`)

> **Full reference:** [`docs/suspicion.md`](docs/suspicion.md) — the
> living home for the model, the likelihood-ratio table's per-entry rationale, the
> offline LR-learning workflow, and the provenance log of every weight. This section
> is the summary.

`update_suspicion(belief)` runs every tick, last in the belief fold `update_belief →
update_agent_tracking → update_event_log → update_social_evidence → update_suspicion`
(composed in `build_runtime`). It maintains
`belief.suspicion[color]` = the posterior **probability that player is an imposter**,
∈ [0, 1] — a real probability, so thresholds mean something. It drives the **vote**
(`top_suspect`) and **Accuse** (`active_tail_suspect` — a live tail over
`ACCUSE_THRESHOLD`). `believed_imposters` (alive players with `P ≥ FLEE_PROBABILITY`,
0.9) is the near-certain set, kept as belief state (it seeds the vote) but no longer
gating a reactive run-away mode. Computed for **both live roles** but over different
sets: a crewmate scores every other player (a genuine belief); an imposter scores only
**non-teammates** (it never scores a known teammate) and reads the number as "how sus
this crewmate looks," to pick a deflection target (§10.4). A ghost holds no suspicion.

**Prior.** With `P` players and `K` imposters, a crewmate knows the `K` are among
the other `P − 1`, so each other player's marginal prior is `K / (P − 1)`. `K` is
derived from the player count via the game's auto formula `(P − 3) // 2`
(`sim.nim:1387`; `effectiveImposterCount`), overridable by `belief.imposter_count`.

**Update (log-odds Bayes).** `logit(P) = logit(prior) + Σ logLR(e)` over observed
evidence `e`. Each graded cue's `logLR` is a **function of the event's features**
(duration/distance), not a flat constant — the function form + constants are the
parameterization (and learnable surface). Per type we take the **max** over the
player's events (most-suspicious instance), so an unbounded event log (§5.2) can't
inflate the posterior and there's no double-counting; and because role is a fixed
latent, evidence **persists** (no time decay — the prior is the baseline). Full
detail (the function shapes and how to fit them) lives in
[`docs/suspicion.md`](docs/suspicion.md) §3.

Two evidence sources, unified — a witnessed catch is just evidence with an
overwhelming `logLR` (`WITNESSED_LOG_LR = ln 1e6 ⇒ P ≈ 1`), not a special case:

- **Near-certain**, from **consecutive** frame-to-frame transitions on the tape
  (§5.1), recorded as `kill` / `vent_use` **point events on the perpetrator's log**
  (no separate "confirmed" set): *witnessed kill* (lone `KILL_RANGE_SQ` neighbour of a
  victim alive last frame, body now) and *witnessed vent* — *emergence* (vent + a
  `VENT_WALK_MARGIN` margin was in line of sight and clear last frame, occupied now)
  or *submersion* (a player in the vent last frame gone while it stays in sight). "In
  line of sight" is the decoded `shadow` mask (§4.4) via `rect_visible`, so occlusion
  can't fake a "clear". `witnessed_imposters(belief)` derives the caught set for tracing.
- **Graded functions** over the event log (§5.2): **vent dwell** (weak, ~flat past a
  pass-through), **body proximity** (log-LR *decreases* with dwell — a skilled
  imposter flees, so brief presence is the only window on a killer; a long camp is a
  reporter), **follow-to-death** (log-LR *increases* with how long the shadowing of a
  now-dead victim lasted), and **being tailed** (`tailing_self` — a logistic in how
  long someone shadowed *us*; needs no death, saturating at a *moderate* P ≈ 0.72:
  a sustained live tail over `ACCUSE_THRESHOLD` (0.6) triggers **Accuse** — go call a
  meeting — but doesn't on its own reach the near-certain bar). A single *weak* graded
  cue lands well below near-certainty, so the meeting vote on those needs corroboration.

Deliberately **excluded** as too noisy (an innocent reporter is next to the body;
crew cluster while tasking): brief proximity, single-body passing, and *task dwell*
as exculpation (imposters fake tasks).

v1 simplifications (documented for later): **naive-Bayes** independence between
evidence types; **positive-evidence-only** (no exculpatory terms — the prior is the
baseline); and a **static** `K / (P − 1)` prior without redistributing the imposter
budget as players are caught or die (a proper joint model is a refinement).
*Vote-tally* bandwagons (census-mapped `voting.dots`) and *chat-stance* counts are now
folded every tick via `strategy/social_evidence.py`, feeding the fitted evidence weights
and the imposter bandwagon. Still future evidence for the deterministic Bayesian model:
*area-recency* and *alibi clearing*. The meeting LLM also sees these signals as
serialized context, but does not write back durable suspicion facts.

### 10.2 Agent location tracking (`agent_tracking.py`)

> **Full reference:** [`docs/agent-tracking.md`](docs/agent-tracking.md).

`update_agent_tracking(belief)` runs every tick in the fast loop after
`update_belief`. It builds a deterministic static substrate once the nav graph
exists: task/home/button anchors, pairwise A* route polylines, and a coarse
reachable occupancy grid (32px cells). For each live non-teammate, it maintains a
position distribution bounded by the speed-limited reachability disc from the
last sighting. A fresh sighting collapses that player to the observed cell; when
the player is absent, line-of-sight-visible cells are removed from their mass.

The readout sums all tracked crew into an expected-crew occupancy grid and tracks
teammate-imposter occupancy separately. Evade's re-approach aggregates the crew grid
to room-level density, subtracts teammate pressure, and commits to the chosen room
for the full Evade window so it reaches and fakes the task instead of
periodically retargeting (via the `best_pretend_room_target` readout — the name is a
carry-over from the retired Pretend mode). Visible kills still require Hunt's existing victim
selection, trajectory lead, KillRange check, and unwitnessed gate. The
task-assignment/destination mixture from the design doc is not implemented yet;
it is the next gated stage after measuring reachability-disc accuracy and kill
impact.

### 10.3 LLM meeting decisions

Attend Meeting remains a mode, not a strategy runner: meetings intentionally slow
the game loop into a social phase, so the LLM call can run on the mode fast path
without starving movement or combat decisions. The path is opt-in via
`CREWBORG_LLM_MEETINGS=1` plus either Bedrock (`USE_BEDROCK=1`) or
`ANTHROPIC_API_KEY`; without a configured backend, the mode preserves the
deterministic fallback: accuse the clear leading suspect (`build_accusation` →
`"<color> sus: <reasons>"`) and vote them, or stay silent and skip a flat field
(`top_suspect`, §10.1). Client construction is a no-raise boundary: provider import,
model resolution, and client selection all degrade to `DisabledMeetingClient`.

The implementation is split into three portable pieces under `strategy/meeting/`:

- `context.py` serializes `Belief` into explicit meeting state: timer estimate,
  self/team, legal vote targets, candidate grid, vote tally, chat transcript,
  roster, event summaries, and suspicion ranking/fallback vote.
- `schema.py` owns the `MeetingDecision` contract and sanitizes/validates chat and
  vote targets against the current legal state.
- `llm.py` owns provider-specific infra through the SDK's `players.player_sdk`
  LLM helpers. It selects direct Anthropic or Bedrock from the environment,
  defaults to Haiku 4.5 (`claude-haiku-4-5-20251001` direct,
  `us.anthropic.claude-haiku-4-5-20251001-v1:0` Bedrock), and remains
  configurable through `CREWBORG_LLM_MODEL`.
- `prompts.py` loads role-specific markdown prompts from
  `strategy/meeting/memory/{crewmate,imposter}.md`, selected from the serialized
  context's `self.role`. `CREWBORG_LLM_PROMPT_DIR` can override the prompt
  directory for experiments; missing prompt files fall back to baked minimal
  doctrine rather than crashing a meeting.

`MeetingDecision.action` is one of `send_chat`, `set_tentative_vote`,
`submit_vote`, or `wait`. A tentative vote is stored in mode-local state and is
auto-submitted near the deadline; `submit_vote` casts immediately. The mode calls
the LLM on meeting start, new external chat, chat-cooldown readiness, and deadline
pressure, with a small tick interval to avoid repeated calls from one visual
state. Distinct chat messages can be sent across the same meeting; duplicate model
text is suppressed. The deadline prompt has priority over new chat/cooldown prompts,
and the mode refuses to start an LLM call unless the configured timeout, converted to
meeting ticks with a margin, can return before the auto-submit window.

### 10.4 Imposter meeting tactics (`strategy/meeting/`)

The deterministic Attend Meeting path diverges by role (`_decide_imposter`). An
imposter's job at a meeting is to get a **crewmate** ejected without outing itself —
**never** a teammate. Two crucial invariants underpin this:

- **Suspicion is computed for the imposter too** (§10.1), over **non-teammates only**.
  Crewmates don't kill/vent, so their score comes purely from *innocent-looking*
  graded cues (a vent dwell, walking past a body the imposter made, a coincidental
  follow). That score is "how sus does this crewmate *look* to the table" — exactly
  what a deflection wants.
- **Chat formatting is identical to the crewmate's** — every accusation, real or
  fabricated, goes through the same `build_accusation` templates (`"<color> sus:
  <reasons>"`). A formatting difference would itself be a tell, so there is none.

The priority order:

1. **Proactive deflection.** If a non-teammate is a clear leading suspect
   (`top_suspect`, §10.1), accuse + vote them with their **real** cues — the strongest
   play, because it isn't even a lie.
2. **Reactive bandwagon.** Otherwise wait, watching for a crewmate to take **heat** —
   a vote cast against them (the reliable signal, read from the vote tally:
   `imposter.votes_against` maps `VoteDot.target` slots → candidate colors, excluding
   our own ballot and skips) or a chat accusation (an additive chat-read signal,
   added in §10.5). `imposter.bandwagon_target` picks the most-heated non-teammate; we vote them
   and cite **fabricated** evidence — *safe, hard-to-disprove* cues only (`lurking on a
   vent`, `next to <body>'s body`, `they were tailing me`), never a bold falsifiable
   witnessed kill/vent (`accusation.fabricate_accusation`).
3. **Skip.** If no crewmate ever takes heat, cast a skip at the deadline.

Chat and vote stay coupled (we accuse exactly who we then vote for).

### 10.5 Reading opponents' chat (`strategy/meeting/chat_read.py`, `chat_nlp.py`)

The bandwagon's *additive* signal (§10.4) is detecting which crewmates other players
are **sussing in chat** — getting ahead of suspicion before it hardens into a vote.
Free-form chat is hard, but the target vocabulary is a **closed set of colors**, which
we exploit in two stages:

1. **Keyword pre-gate** (cheap, in `chat_read`) — a message is only parsed if it names
   a color *and* carries a sus cue (`SUS_WORDS`). Most chatter is dropped here.
2. **Dependency-parse negation scope** (spaCy `en_core_web_sm`, in `chat_nlp`) — the
   real value over a crude "is a negation word present?" guard, which mishandles
   `"red isn't sus"` vs `"red is sus not blue"`. The parse tracks which clause a
   negation/defense word governs, handles contrastive negation, and flips a color
   adjacent to a *victim* cue (`"when red died"` ⇒ red is the victim, not the suspect).
   `chat_accusers` then counts the **distinct other speakers** who accused each
   non-teammate. (Validated at ~19/21 on representative chat vs ~16/21 for keyword +
   crude negation; the wins are exactly the negation-scope cases.)

**Loading is off the hot path.** `en_core_web_sm` costs ~1.5–2 s to load under the
hosted ¼-core cap (~40 frames), so `chat_nlp.ensure_loading()` (called at
`build_runtime`) kicks off a **background daemon thread**; the load overlaps the
pre-game idle phases and is ready before the first meeting. `get_model()` returns
`None` until ready, and callers degrade gracefully (no chat signal — the bandwagon
rests on the reliable vote tally). The whole layer is gated by **`CREWBORG_CHAT_NLP`**
(default on); unset it and spaCy is never imported or loaded. When the model is off we
deliberately do **not** fall back to crude keyword matching — its false positives are
exactly what this layer exists to avoid.

### 10.6 LLM gameplay commander (`strategy/commander/`)

> **Full reference:** [`docs/commander.md`](docs/commander.md).
> **Status: BUILT & gated-off (2026-06-26)** — both roles' levers, danger mode, a soft/hard
> strength dial, observability, a forced-priority debug knob, and the Bedrock-in-pod gating fix;
> 460 tests green; disabled path byte-identical; **not submitted**; control demonstrated but not
> yet tuned for performance. This section is the summary.

A background LLM that steers *gameplay* by writing **priorities** into belief, which the
modes read to bias *how* they execute — without ever selecting a mode or blocking a tick.
It realizes the `AsyncStrategyRunner` seam noted above, scoped to the **Playing** phase;
the meeting LLM (§10.3) is untouched (only the Bedrock-enable check is shared).

- **Two loops, one belief.** A `CommanderStrategy` wraps `RuleBasedStrategy` on the
  existing `SynchronousStrategyRunner` (mode selection unchanged, every tick). Each
  tick it delegates to the rules for the mode, feeds a serialized game-state snapshot
  to a **background daemon worker thread** (sync Bedrock `call_json`, one call in
  flight, ~3–5 s), and returns the worker's latest `CommanderPriorities` as
  `StrategyResult.inferences`. The runtime folds those into `belief.commander` via the
  `apply_inferences` hook. Priorities are **sticky** until the next cycle overwrites
  them; the worker never touches live belief (lock-protected latest-value handoff).
- **Priorities (`belief.commander`).** Crew: `target_room`, `target_task`, `posture`
  (stick/isolate/neutral). Imposter: `hunt_room`, `target_player`, `avoid_room`. Plus a
  `strength` dial. Consumed at each mode's existing candidate-ranking step (NormalMode
  `_pick_target`, SearchMode `_pick_room`/follow, ReconMode + HuntMode victim choice) under
  one rule: **bias, don't force** — filter-then-rank or score-nudge, always falling back to
  today's default when the priority would select nothing valid; stale (`as_of_tick` TTL) or
  invalid priorities are ignored. Reactive/safety gates are untouched.
- **Strength dial.** `strength: soft` (default, the bias-with-fallback above) vs `hard`
  (override the default even when suboptimal — Search targets a distant `hunt_room`, NormalMode
  loiters in a no-task `target_room`, longer `target_player` follow). Measured (forced runs):
  soft→hard takes imposter `hunt_room` adherence 29%→100%, crew `target_room` 13%→67%.
- **Forced-priority knob.** `CREWBORG_COMMANDER_FORCE='{…}'` stamps a fixed sanitized priority
  into belief each tick, bypassing the LLM/worker (no backend needed) — for deterministic control demos/QA.
- **Danger mode (imposter, opt-in).** Two LLM-authorized risk levers that are the
  *deliberate exception* to "never touch the gates": `allow_witnessed_kill` (relax
  Hunt's witness test, `hunt.py:68`) and `skip_evade` (suppress the post-kill Evade
  window, `rule_based.py:143`). Both require a traced `danger_reason`; the play-guide
  prompt marks them as ⚠️ DANGER. Unset → today's conservative behaviour.
- **Gating & fallback.** Opt-in via `CREWBORG_LLM_COMMANDER=1` + a backend, mirroring
  `CREWBORG_LLM_MEETINGS`. No flag / no backend → worker disabled, `belief.commander` stays
  `None`, behaviour is exactly current crewborg. **In-pod Bedrock note:** sidecar mode strips
  `USE_BEDROCK` and injects `AWS_ENDPOINT_URL_BEDROCK_RUNTIME`, so both the commander and meeting
  factories now gate on that endpoint (not `USE_BEDROCK`). Observability: `domain.commander_*`
  traces (`CREWBORG_TRACE_GROUPS=commander`), incl. `commander_started.env_seen` for in-pod enable diagnosis.

---

## 11. Package layout and tracing

```
crewborg/
  __init__.py        # build_runtime(): assemble AgentRuntime
  agent_tracking.py  # reachability-disc location beliefs + coarse occupancy search
  types.py           # the six types + perceive/update_belief
  action.py          # action layer: stateful resolve_action, composite execution, momentum + button FSM
  nav.py             # baked-map nav graph + route planning (used by the action layer)
  navbake.py         # load the offline-baked nav graph + occupancy substrate (tools/nav_bake.py bakes it)
  trace.py           # trace selection: event families + env-derived filtering (outputs are the SDK's)
  events.py          # CrewborgEventTracer: on_step_complete hook emitting domain.* events
  modes/             # idle, normal, attend_meeting, report_body, accuse, evade, hunt, recon, search (+ imposter_common.py; _deprecated/ holds retired pretend)
  strategy/          # rule_based.py: mode selector; suspicion.py: near-certain detection; social_evidence.py: vote/chat evidence; event_log.py: per-player observation log; occupancy.py: tape predicates; opportunity/trajectory/path_prediction
  strategy/meeting/  # context/schema/llm (LLM path); accusation (chat templates); imposter (deflect/bandwagon); chat_read + chat_nlp (spaCy chat parsing)
  strategy/commander/ # LLM gameplay commander: biases belief priorities (gated; does not select mode)
  perception/        # Sprite-v1 scene decoder: maintain tables, resolve objects → (label, world xy)
  map/               # vendored croatoan.resources + ported parser (§6)
  coworld/           # policy_player.py (the websocket bridge) + scene.py
  viewer/            # browser UI for inspecting trace-driven agent-perspective replays
  tests/             # action/modes/strategy/trace/runtime + bridge smoke + scene-decode tests
  design.md  README.md  version_log.md
```

**Tracing.** Stdout = protocol channel; traces/metrics flow through the SDK's
`TraceOutputs` (`players.player_sdk.trace_outputs`). **Outputs** (where records
go) are configured by `CREWBORG_TRACE_OUTPUTS` — comma-separated
`format@destination` specs (`jsonl|json|csv|parquet` @
`stderr|stdout|file:<path>|artifact[:name]`). The bridge default is
**`jsonl@artifact`**: records stream to a temp file and are zipped and uploaded
to the runner-provided `COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` when the bridge
exits (one `policy_artifact_{slot}.zip` per slot, 200 MB cap — the metta
`PLAYER_ARTIFACT` contract). This sidesteps Observatory's hosted policy-log line
cap entirely, so even heavy trace levels survive hosted runs. When no upload URL
is present (running outside a Coworld runner), the bridge falls back to
`jsonl@stderr` with a warning instead of crashing — a pre-connect crash would
fail the episode. **Selection** (which events go) is unchanged and lives in
`trace.py`: the default is a lean stream that keeps durable domain events and
low-volume mode boundary events but filters per-tick SDK framework noise such as
`perception`, `belief_updated`, `action_intent`, `act_command`,
`snapshot_submitted`, `strategy_evaluated`, and repeated directive traces. The
full framework stream is still available with `CREWBORG_TRACE=debug` or
`CREWBORG_TRACE=viewer`.
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
  players/bodies, believed/confirmed threats with last-seen age and whether each is
  tailing us, and task/accuse/nav geometry (the `accuse` block records the tail we
  mean to accuse and the button run). It is one record per tick, so it is useful for
  single-game forensics but too noisy for capped hosted logs.
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
**Ground-truth tick.** The engine streams its authoritative tick as an invisible
`"tick <N>"` marker sprite (id 5016; `scene.server_tick()`). The bridge drives the
SDK runtime from it (`runtime.tick = server_tick − 1` before `step()`), so
perception, `belief.last_tick`, mode/directive timing, **and every trace event /
metric `tick`** carry the engine's true tick — not the local received-message
counter (`scene.tick`), which silently lags when we fall behind. (Before the marker
arrives on the first frames, it falls back to the local counter.) **All tracing is
therefore aligned to replay tick numbers.**

With metrics on, the **bridge** also emits per-tick latency instrumentation:
`bridge.step_ms` (histogram — `runtime.step()` wall-time vs the ~42 ms/tick budget),
`bridge.loop_gap_ms` (histogram — wall-clock between frame arrivals; sustained
sub-42 ms gaps mean a queued-frame backlog is draining), and `bridge.tick_drift`
(gauge — **ground-truth** frames we've fallen behind: `server_tick − scene.tick`
relative to their offset when the marker first appeared; `0` = keeping up, growth =
falling behind). Each sample is tagged with the **server** tick. The per-tick
`decision_snapshot` carries a `voting` section during meetings (`cursor_slot`,
`cursor_on_skip`, `candidates`, `vote_confirmed`) — the action→effect record
for vote-actuation forensics.
`kill_attempted` (we pressed) is distinct from `kill_landed` (the kill registered,
seen as the kill-ready→cooldown edge). Incoming meeting chat is decoded into
`belief.chat_log` (§4.3) and emitted once per meeting line as `chat_received`.
When the meeting LLM is enabled, `meeting_context_serialized`,
`meeting_llm_decision`, fallback reasons, and selected chat/vote are in the default
trace; the LLM latency histogram is emitted when metrics are enabled. Raw LLM
request/response tracing is opt-in via `CREWBORG_LLM_TRACE_RAW=1` or `CREWBORG_TRACE=debug`.

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
| Voting policy | accuse + vote the **clear leading suspect** — near-certain (`P ≥ VOTE_PROBABILITY=0.8`) or a clear lead (`P ≥ VOTE_LEAD_MIN_P=0.5` and ahead of the runner-up by `VOTE_LEAD_MARGIN=0.2`), §10.1 — else **silent + skip** a flat field; always cast *something* before the timer (not voting costs −10) |
| LLM meetings | opt-in with `CREWBORG_LLM_MEETINGS=1` plus Bedrock (`USE_BEDROCK=1`) or `ANTHROPIC_API_KEY`; default models `claude-haiku-4-5-20251001` direct / `us.anthropic.claude-haiku-4-5-20251001-v1:0` Bedrock; default timeout 3.0s; deadline prompt wins over chat prompts and is pulled earlier when needed so worst-case timeout plus margin returns before the ≤48-tick auto-submit window; chat cooldown is 100 ticks |
| Chat NLP (§10.5) | **on by default**; kill switch `CREWBORG_CHAT_NLP=0` disables it (never imports/loads spaCy). Drives the imposter bandwagon's chat signal via `en_core_web_sm` dependency-parse negation scope, background-loaded so it never blocks play |
| Aggressive imposter selector | opt-in with `CREWBORG_BE_DUMB=1` or `BE_DUMB=1`; during `Playing`, imposters skip Evade/ReportBody and always select Search unless kill-ready with a visible victim, then Hunt |
| Report policy | crewmates always report visible bodies; **imposters NEVER report** (removed 2026-06-25) — they evade for `EVADE_TICKS = 72` (override `CREWBORG_EVADE_TICKS`) after their own kill, then go straight back to Search. Self-reporting our own kill opened a meeting that reset the kill cooldown and killed snowball kills (§7.2) |
| Evade re-approach room targeting | room score = expected crew density minus teammate-imposter pressure (`TEAMMATE_ROOM_PENALTY = 3.0`, `agent_tracking.py`); commit to a real task station in the chosen room for the Evade window (the `best_pretend_room_target` readout — name carries over from the retired Pretend mode) |
| Kill isolation bar | clearance `BASE_ISOLATION_RADIUS = 48` px and witness window `WITNESS_WINDOW_TICKS = 72`, both relaxed to zero by urgency `URGENCY_FULL_TICKS = 240`; **and skipped entirely after our first kill** (Hunt's strike gate bypasses `unwitnessed` once `last_kill_tick` is set — prioritize banking the 2nd kill over stealth) |
| Search lead | enter Search `SEARCH_LEAD_TICKS = 250` before the kill is ready (half the 500-tick cooldown — raised from 100 so we are already shadowing an isolated victim when the kill comes ready, converting the cooldown window to a kill ASAP; stops short of the BE_DUMB ceiling, which tripled ejections for +10% kills). Time-to-ready is reconstructed from the binary HUD: a learned `kill_cooldown_estimate` (or `DEFAULT_KILL_COOLDOWN_TICKS = 500`, matching the live game's `killCooldownTicks`, until measured) from the tracked cooldown start |
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
