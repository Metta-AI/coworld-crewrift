# Perception and Belief

How crewborg turns the Sprite-v1 stream into its world model. This is the
cross-cutting "how it works" reference for the first half of the cognitive stack:
**raw bytes → structured scene → resolved entities → percept → folded belief**.
Everything downstream — suspicion, modes, the action layer — reads only the
`Belief` this pipeline produces; none of it ever touches a sprite or a pixel.

For orientation and the file map see [`../README.md`](../README.md); for the
structural spec see [`../design.md`](../design.md) §3–§5 (this doc is the narrative
that ties those sections' files together, not a re-derivation of them). The raw
Sprite-v1 **wire byte layout** and the game's full **label vocabulary** are not
repeated here — their home is the player-directory top-level `AGENTS.md`. This doc
covers what crewborg *does* with that wire format.

---

## The pipeline at a glance

```
  websocket bytes
       │  (one message may concatenate many sub-messages)
       ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ SceneState.apply  →  perception/decoder.apply_message        │  coworld/scene.py
  │   mutates 3 retained tables + camera + 2 alpha masks IN PLACE │  perception/decoder.py
  └─────────────────────────────────────────────────────────────┘
       │  SceneState  (Layers / Sprites / Objects, camera, walkability, visible_mask)
       ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ perceive(observation, tick)                                  │  types.py
  │   → resolve_scene(scene, tick)  → ResolvedScene              │  perception/resolve.py
  │   → Percept (resolved + walkability + visible_mask by ref)   │  perception/entities.py
  └─────────────────────────────────────────────────────────────┘
       │  Percept  (immutable, vision-free, world-coord entities)
       ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ fold_belief(belief, percept)                                 │  __init__.py
  │   update_belief → update_agent_tracking → update_event_log   │  types.py + strategy/
  │     → update_social_evidence → update_suspicion              │
  └─────────────────────────────────────────────────────────────┘
       │  Belief  (the ONLY interface strategy/modes/action see)
       ▼
  strategy → mode → action
```

Two stages do byte/structure work (the bridge's `SceneState`, the perception
layer's `resolve`), and one stage records it (the belief fold). The dividing line
is firm: **raw scene data — especially sprite pixels — never enters belief.**

---

## Stage 1 — the bridge-owned scene (`coworld/scene.py`)

`SceneState` (`coworld/scene.py`) is the lone non-pydantic, SDK-facing type: a
plain mutable dataclass the bridge maintains as Sprite-v1 messages arrive. It holds
everything decoded from the wire and nothing interpreted. `Observation`
(`types.py`) carries it **by pointer** into `runtime.step`; perception reads through
that pointer downstream.

`SceneState.apply(message)` is the bridge's only entry point. It bumps
`messages_applied` and delegates the byte work to
`perception/decoder.py:apply_message` — the dataclass itself holds no decode logic.

### Three retained tables, maintained incrementally

The scene is a structured, retained model — **not** a per-frame snapshot. The
server streams *deltas* (define / delete / clear), and the decoder folds each into
three dicts keyed by their protocol ids:

| Table | Field | Row type (`perception/tables.py`) | Holds |
|---|---|---|---|
| Sprites | `sprites: dict[int, SpriteDef]` | `SpriteDef(width, height, label)` | a defined sprite's size + label; **pixels not retained** |
| Objects | `objects: dict[int, ObjectState]` | `ObjectState(x, y, z, layer, sprite_id)` | a placed object; `x`/`y` are **camera-relative screen** coords |
| Layers | `layers: dict[int, LayerDef]` | `LayerDef(layer_type, flags)` | a layer's kind + flags |

These persist across frames and are overwritten in place: a define replaces a row,
a delete drops one, and `MSG_CLEAR_OBJECTS` empties the objects table wholesale.
Because the model is retained, a single tick's `objects` table is the full current
world the server has drawn — the resolve stage simply reads it.

### Authoritative tick by label prefix

The engine streams an invisible 1×1 sprite labeled `"tick <N>"` every frame, where
N is its authoritative tick. `SceneState.server_tick()` scans the sprite table for
that label and returns the max N (or `-1` before the marker first arrives, so the
bridge can fall back to its local message counter). It matches on the
`SERVER_TICK_LABEL_PREFIX = "tick "` **label prefix**, not on the sprite id, so an
id-offset change in the engine does not break tick ground truth.

---

## Stage 2a — byte decode (`perception/decoder.py`)

`apply_message(scene, message)` walks one websocket packet as a sequence of
sub-messages: read a 1-byte type, dispatch to a per-type decoder, advance `offset`
to exactly what it consumed, repeat until the packet is exhausted. One binary
message may concatenate **many** sub-messages. Every multi-byte field is
little-endian (`_u16` / `_i16` / `_u32`). Malformed input (truncation, an unknown
type byte) raises `SpriteProtocolError`, which the bridge lets propagate — closing
the connection, per the protocol.

The six message types (`perception/constants.py`):

| Byte | Type | `decoder.py` handler | Effect |
|---|---|---|---|
| `0x01` | `MSG_DEFINE_SPRITE` | `_apply_define_sprite` | store `SpriteDef`; decode pixels only for the two retained masks |
| `0x02` | `MSG_DEFINE_OBJECT` | `_apply_define_object` | store `ObjectState`; special-case the map object to recover the camera |
| `0x03` | `MSG_DELETE_OBJECT` | `_apply_delete_object` | drop the object; clear `camera_ready` if it was the map object |
| `0x04` | `MSG_CLEAR_OBJECTS` | inline | empty the objects table; clear `camera_ready` |
| `0x05` | `MSG_SET_VIEWPORT` | inline | skip 5 bytes (not retained) |
| `0x06` | `MSG_DEFINE_LAYER` | `_apply_define_layer` | store `LayerDef` |

### The only two image masks crewborg decodes

A define-sprite message carries a snappy-raw-compressed RGBA pixel payload.
Crewborg **discards every sprite's pixels** except two, identified by label
(`perception/constants.py`). It reads structured state from labels and coordinates,
not from vision:

- **`LABEL_WALKABILITY = "walkability map"`** → `_decode_walkability`. The static
  world-space collision map. Snappy-decompresses the RGBA, keeps `alpha > 0` as
  walkable, reshapes to a `(height, width)` bool grid on `scene.walkability` (plus
  `walkability_width` / `walkability_height`). Consumed once to build the A* nav
  graph — see [`./navigation.md`](./navigation.md).
- **`LABEL_SHADOW = "shadow"`** → `_decode_shadow`. The dynamic per-player vision
  overlay (line of sight). The overlay paints **occluded** pixels opaque and leaves
  **visible** pixels transparent, so the decoder stores `visible = (alpha == 0)` as
  a screen-space bool grid on `scene.visible_mask`. It is resent on every camera
  move and overwrites the prior mask, so it always matches the current camera.

Both decoders raise `SpriteProtocolError` on bad dimensions, a failed snappy
decode, or a payload whose length is not `width * height * 4`.

Every branch of the decoder advances `offset` past exactly the bytes it consumed
and returns the new offset; the sub-message loop desyncs otherwise. No belief or
strategy logic lives here — only protocol parsing.

### Camera-relative → world coordinate recovery

Objects arrive in **camera-relative screen** coordinates. Crewborg recovers world
coordinates from one special object: the world map.

The map is object id `MAP_OBJECT_ID = 1` drawn with sprite id `MAP_SPRITE_ID = 1`,
and the engine draws it at `(-camX, -camY)`. So in `_apply_define_object`, when an
object with that id/sprite pair arrives, the decoder sets:

```
camera_x = -obj.x
camera_y = -obj.y
camera_ready = True
```

World coordinates are unavailable until the map object first arrives; the scene
degrades gracefully meanwhile (`camera_ready` is False, world positions are not
trusted). Deleting the map object, or `MSG_CLEAR_OBJECTS`, flips `camera_ready`
back to False — the camera is no longer valid.

**Self is the implicit camera centre.** The agent's own avatar is not an object in
the stream; the camera is locked to it. Inverting the engine's camera math (with a
128×128 screen, `SpriteSize=12`, the draw offsets) gives self's world position as a
fixed offset from the camera: `self_world = (camera_x + SELF_OFFSET_X, camera_y +
SELF_OFFSET_Y)` with `SELF_OFFSET_X = 60`, `SELF_OFFSET_Y = 66`. This is computed
in `resolve_scene`, valid only when `camera_ready`.

---

## Stage 2b — resolve to entities (`perception/resolve.py`)

`resolve_scene(scene, tick)` turns the raw object/sprite tables into a single
immutable `ResolvedScene` (`perception/entities.py`) of typed, world-coordinate
entities. It is a **single pass** over `scene.objects` and mutates nothing. This is
structured-scene interpretation, **not** computer vision — no pixels are read.

For each object it joins to its sprite's label, converts to world coords
(`world = obj.xy + camera`), and classifies.

### Identity by label + object-id range — the two-keyed contract

Classification keys on **both** the sprite **label** (or label prefix) **and** the
object's **id range**, and both must agree. This is what lets the *same*
`"player <color>"` sprite mean different things in different screens without
colliding — the id range disambiguates:

| Entity / signal | Id range (`constants.py`) | Label / prefix | Resolved as |
|---|---|---|---|
| Live world player | `PLAYER_OBJECT_BASE = 1000` .. 2000 | `PREFIX_PLAYER` + `left`/`right` | `VisiblePlayer` |
| Dead body (in world) | `BODY_OBJECT_BASE = 2000` .. 3000 | `PREFIX_BODY` | `VisibleBody` |
| Task bubble (on-screen) | `TASK_BUBBLE_OBJECT_BASE = 3000` .. 7000 | `LABEL_TASK_BUBBLE` | `TaskSignal(kind="bubble")` with world pos |
| Task arrow (off-screen) | `TASK_ARROW_OBJECT_BASE = 7000` .. 10100 | `LABEL_TASK_ARROW` | `TaskSignal(kind="arrow")`, bearing only |
| Chat line text | `CHAT_TEXT_OBJECT_BASE = 9000` .. 9200 | raw message text | chat-text candidate (paired later) |
| Chat speaker icon | `CHAT_ICON_OBJECT_BASE = 9200` .. 9300 | `PREFIX_PLAYER` | chat-icon candidate (paired later) |
| Vote candidate cell | `VOTE_ICON_OBJECT_BASE = 9300` .. +`MAX_PLAYERS` | `PREFIX_PLAYER` / `PREFIX_BODY` | `VoteCandidate` + `CensusEntry` |
| Role-reveal teammate | `ROLE_ICON_OBJECT_BASE = 9500` .. +`MAX_PLAYERS` | `PREFIX_PLAYER` | a `reveal_player_colors` color |
| Ejected-player icon | `RESULT_ICON_OBJECT_ID = 9600` | `PREFIX_PLAYER` | `ejected_color` |
| Normal vote dot | `VOTE_DOT_OBJECT_BASE = 10100` .. +`MAX_PLAYERS²` | `PREFIX_VOTE_DOT` | `VoteDot(voter, target)` |
| Skip vote dot | `VOTE_SKIP_DOT_OBJECT_BASE = 10400` .. +`MAX_PLAYERS` | `PREFIX_VOTE_DOT` | `VoteDot(target=SKIP_VOTE_TARGET)` |

`MAX_PLAYERS = 16`. A normal vote dot's id encodes both endpoints:
`id = base + target * MAX_PLAYERS + voter`, decoded back to `(target, voter)`
slots. The id ranges are assumed disjoint by these range checks.

Three label-only signals are matched without an id range because their object ids
fall outside the entity ranges — the HUD **self-role** icons, dispatched first and
`continue`d:

- `LABEL_IMPOSTER_ICON` → `self_role="imposter"`, `self_kill_ready=True`
- `LABEL_IMPOSTER_ICON_COOLDOWN` → `self_role="imposter"`, `self_kill_ready=False`
- `LABEL_GHOST_ICON` → `self_role="dead"`

Phase / HUD text, the voting cursor / skip-cursor / timer, the vote self-marker,
the progress bar, the crew-task counter, and the `MeetingCall` caller line
(`"<Color> reported|pressed|called"`, parsed by `MEETING_CALL_TEXT`) are likewise
recognized by label and folded into scalar fields on the result.

### Collision-point recovery

A visible player/body is *drawn* offset from its true collision point. The resolver
adds `ENTITY_COLLISION_DX = 3`, `ENTITY_COLLISION_DY = 9` back to the decoded world
position so the stored `world_x`/`world_y` is the server's collision/report/kill
point — range checks (report distance, kill distance) then match the server.

### Cross-object pairing after the loop

Three resolved facts need matching across objects and are assembled after the
single pass:

- **Chat lines** (`_pair_chat`). Chat text and interstitial/HUD text *share* the
  9000 range, so a text line cannot be told from phase text by id alone. The
  resolver anchors on the chat **icon** range (exclusively chat) and matches each
  icon to the text line at the nearest screen-y within
  `CHAT_ICON_TEXT_Y_TOLERANCE = 32` px, each text consumed at most once. A
  `ChatLine` is emitted only when an icon claims a line — phase text never leaks in.
- **Vote cursor slot** (`_cursor_slot`). The cursor is drawn at the same grid
  position as the candidate cell it sits on, so the nearest candidate cell by
  squared screen distance is the slot it targets — no grid-layout constants
  hardcoded.
- **Census / candidates.** The candidate grid renders every player as a crew
  sprite (alive) or body sprite (dead) tagged by color — an authoritative per-meeting
  alive/dead `census` and the `candidates` slot list for targeted voting.

The result, `ResolvedScene`, is a frozen pydantic model (`frozen=True`,
`extra="forbid"`): an immutable snapshot whose field-name drift against the
resolver fails loudly rather than silently dropping data. World positions (and self
position) on it are meaningful only when `camera_ready`.

---

## Stage 2c — `perceive` builds the `Percept` (`types.py`)

`perceive(observation, tick)` is a thin wrapper: it reads the live `SceneState`
through the observation pointer and returns an immutable `Percept` carrying

- `tick`, `messages_applied`,
- `resolved` — the `ResolvedScene` from `resolve_scene`,
- `walkability` — the decoded mask, **held by reference** (static for the episode,
  so the nav graph is built from it once),
- `visible_mask` — this tick's screen-space line-of-sight mask, held by reference.

`perceive` does interpretation only; byte decoding already happened in the bridge.

---

## Stage 3 — the per-tick belief fold (`__init__.py`)

`build_runtime` wires `update_belief` as part of a single `fold_belief` closure that
runs once per tick, right after `perceive` and before the strategy/modes:

```
fold_belief(belief, percept):          # __init__.py
    update_belief(belief, percept)     # types.py        — perception → belief
    update_agent_tracking(belief)      # agent_tracking.py — spatial location belief
    update_event_log(belief)           # strategy/event_log.py
    update_social_evidence(belief)     # strategy/social_evidence.py
    update_suspicion(belief)           # strategy/suspicion.py
```

Each step reads the belief the previous step left and mutates it in place. The
ordering matters: `update_belief` lays down the current roster / bodies / tape /
phase, then the tracking and evidence folds read that fresh state, and
`update_suspicion` recomputes posteriors last — so the strategy snapshot taken
afterward sees current search state and `believed_imposters`. The agent-tracking
and suspicion folds are documented separately
([`./agent-tracking.md`](./agent-tracking.md), [`./suspicion.md`](./suspicion.md));
this doc covers `update_belief`, the perception → belief step.

**Why belief is the only interface.** `fold_belief` is the *only* per-tick belief
mutation in the whole agent. After it runs, the strategy, every mode, and the
action layer read `Belief` and nothing else — never the scene, the percept, or a
mask. This is an SDK invariant ([`../design.md`](../design.md) §1): raw scene data
is confined to the perception layer, so the entire decision stack is a pure function
of the world model. The same property makes belief the unit of trace forensics
(see [`./trace-logs.md`](./trace-logs.md)).

### `update_belief` — the perception fold (`types.py`)

`update_belief(belief, percept)` is the sole writer of the core belief sections. It
**records, never decides**: no intents, no suspicion, no targeting. In order
(`types.py:update_belief`):

1. **Loop bookkeeping** — `last_tick`, `ticks_observed`, `messages_applied`.
2. **Camera / self** — copies `camera_ready`, `camera_x/y`, `self_world_x/y`.
3. **Nav graph, once** — when `nav is None` and a walkability mask exists, prefer
   the offline bake (`load_navbake`) when it matches the streamed mask, else build
   live (`build_nav_graph`). Details in [`./navigation.md`](./navigation.md).
4. **Tasks** — `visible_task_indices` from this tick's task signals, accumulated
   into `assigned_task_indices`; `crew_tasks_remaining` and
   `active_task_progress_pct` latched. (Completion is concluded by the crewmate
   Normal mode, not here — see [`./crewmate-play.md`](./crewmate-play.md).)
5. **Self-color learning** — see below.
6. **Live sightings → roster** — each `VisiblePlayer` proves that color alive here,
   now; folded into its `PlayerRecord`.
7. **Bodies** — each `VisibleBody` added to `bodies` (keyed by object id) and the
   death reflected onto the color-keyed roster.
8. **Perception tape** — append this frame (camera-ready only).
9. **Census / ejection deaths** — the meeting grid and vote-result interstitial mark
   alive/dead by color.
10. **Phase machine** — `derive_phase`; a meeting opening clears chat + in-world
    bodies (see below).
11. **Meeting-caller latch** — caller color + kind held for the meeting,
    dropped when play resumes.
12. **Chat de-dup** — append only `(speaker, text)` lines not yet logged this
    meeting.
13. **Role-reveal teammates** — on the `IMPS` reveal, record teammate colors.
14. **Self role + imposter kill-cooldown edge tracking** — see below.

---

## The `Belief` world model (`types.py`)

`Belief` is a mutable pydantic model — the persistent world model. Its
perception-owned sections:

### Roster, keyed by color

`roster: dict[str, PlayerRecord]`. **Color** is the only identity stable and unique
across every Crewrift namespace (in-world sprites, bodies, chat icons, vote
markers), so it is the canonical key. A `PlayerRecord` carries:

- the **alive-fix** — `world_x`/`world_y`/`facing`/`last_seen_tick`/`object_id` plus
  a bounded `history` trail (`ROSTER_HISTORY_MAX = 64`), written **only** from live
  `"player <color>"` sightings via `PlayerRecord.record`, so they mean exactly "the
  last time and place I saw this player alive";
- the **death half** — `life_status` (`alive`/`dead`/`unknown`), `death_seen_tick`,
  `death_source` (`body`/`census`/`ejection`), and `body_xy`, set by
  `mark_dead` (first death signal wins; a later body sighting can fill `body_xy`).
  Death is reflected onto the record by color, so "last seen alive" and "now dead"
  live on one object.
- the **event log + social counters** — `events` / `seen_ticks` and the cumulative
  chat/vote/task counters, owned by the event-log and social-evidence folds
  (deferred to [`./suspicion.md`](./suspicion.md)), not by `update_belief`.

`object_id` is `PLAYER_OBJECT_BASE + joinOrder`, so `join_order` is recoverable from
the live-world handle. `total_player_count` is the max distinct colors seen
(authoritatively the census size when a meeting is present).

### `self_color` — excluding self from every suspicion path

The camera is locked to the agent, so the player rendered at the camera centre
(`self_world`) is the agent. `update_belief` learns `self_color` so that self never
leaks into the roster as a suspect, tail target, or vote target:

- the **voting UI self-marker** is authoritative when present;
- otherwise, the visible player nearest to `self_world` is the agent **iff** it is
  within `SELF_SPRITE_MATCH_SQ = 4²` squared px (the self-sprite decodes to exactly
  `self_world`; a real player cannot overlap it).

Learned once and persisted (color is fixed for the game). Without it the agent
suspects, tails, and votes itself.

### Phase machine

`phase: Phase` ∈ `unknown / Lobby / RoleReveal / Playing / Voting / VoteResult /
GameOver`, advanced by `derive_phase(resolved, current)` (`types.py`):

```
interstitial text wins:  DRAW|CREW WINS|IMPS WIN → GameOver
                         IMPS|CREWMATE            → RoleReveal
                         WAITING|NEED MORE!|STARTING → Lobby
                         NO ONE|WAS KILLED        → VoteResult
voting UI active or SKIP                          → Voting
else, if camera_ready:   was RoleReveal/VoteResult/Voting/Playing → Playing
                         crew-task counter or task bubbles present → Playing
else                                              → unchanged
```

The subtlety is `Playing`: ordinary play shows no interstitial and no voting UI, so
the machine must *infer* `Playing` from a live scene once a reveal/meeting clears —
otherwise belief stays stuck at `RoleReveal` and the crewmate Normal mode (keyed on
`Playing`) never activates.

On the transition **into `Voting`**, `update_belief` clears `chat_log`,
`bodies`, and `visible_body_ids` — matching the server, which removes all bodies
when a meeting opens, so a post-meeting walk past where a body lay does not read as
a fresh sighting. Deaths already on the roster persist; only the in-world body
objects drop.

### Perception tape — `recent_frames`

`recent_frames: list[PerceptionFrame]` is a bounded ring (`RECENT_FRAMES_MAX = 24`,
~1 s at 24 Hz), appended **only on camera-ready frames**. Each `PerceptionFrame` is
*what we saw* on one frame: the camera viewport, the alive `players` (color →
collision xy), the `bodies`, and the screen-space `visible_mask` (held by
reference). It is the substrate for frame-to-frame transition detection (kills,
vents) and for occupancy/adjacency predicates, which are pure functions over the
tape and never stored. The viewport + LoS mask let consumers distinguish "a region
I could see and it was empty" from "a region I simply wasn't looking at." Meetings
(no camera) leave a tick gap, which the transition detectors require to be absent
before trusting a transition.

### Other perception-owned sections

- `bodies: dict[int, BodyEntry]` + `visible_body_ids` — bodies keyed by object id
  for the report path.
- `voting: VotingState` — this tick's voting UI presence + tally, copied straight
  from the resolved scene.
- `chat_log: list[ChatEvent]` — meeting chat, de-duplicated by `(speaker, text)`
  and cleared when a new meeting opens.
- `teammate_colors` — imposter teammates from the role-reveal icons, so the
  imposter never targets a teammate (see [`./imposter-play.md`](./imposter-play.md)).
- the **imposter kill-cooldown timing** fields — `last_kill_tick`,
  `kill_ready_since_tick`, `kill_cooldown_start_tick`, `kill_cooldown_estimate`,
  reconstructed by edge-tracking the binary kill-ready/cooldown HUD icon across
  consecutive `Playing` ticks (the HUD gives no countdown). Gated on continuous
  `Playing` so a meeting's cooldown reset is not mistaken for a kill. Consumed by
  the imposter kill loop — see [`./imposter-play.md`](./imposter-play.md).
- `agent_tracking: AgentTrackingState` — the probabilistic location sub-belief,
  carried here but advanced by `update_agent_tracking`
  ([`./agent-tracking.md`](./agent-tracking.md)).
- `suspicion` / `believed_imposters` / `imposter_count` — the Bayesian posteriors,
  carried here but written by `update_suspicion`
  ([`./suspicion.md`](./suspicion.md)).

The interpreted aggregates (`roster`, `bodies`) are distinct from the raw
`recent_frames`: the tape is the unprocessed evidence, the aggregates are the
running conclusions folded from it and from the census/ejection signals.

---

## Where this connects

| Topic | Doc |
|---|---|
| Wire byte layout + label vocabulary | player-directory top-level `AGENTS.md` |
| Structural spec (types §2, transport §3, perception §4, belief §5) | [`../design.md`](../design.md) |
| Orientation + file map | [`../README.md`](../README.md) |
| A* nav graph over the walkability mask | [`./navigation.md`](./navigation.md) |
| Probabilistic per-agent location tracking | [`./agent-tracking.md`](./agent-tracking.md) |
| Bayesian P(imposter) over the event log | [`./suspicion.md`](./suspicion.md) |
| Imposter kill loop, teammates, cooldown use | [`./imposter-play.md`](./imposter-play.md) |
| Crewmate tasks, reporting, voting | [`./crewmate-play.md`](./crewmate-play.md) |
| Meeting chat/vote (incl. LLM path) | [`./meetings.md`](./meetings.md) |
| The opt-in gameplay commander | [`./commander.md`](./commander.md) |
| `domain.*` trace events over belief | [`./trace-logs.md`](./trace-logs.md) |
