# Crewrift wire protocol (Sprite-v1 I/O contract)

The exact I/O contract **any** Crewrift player must speak over its WebSocket: the
bytes that arrive, what they mean, and the bytes you send back. This is the
*protocol* reference — the transport, the Sprite-v1 message framing, the Crewrift
scene vocabulary (labels + object-id ranges), and the input semantics. It is
implementation-agnostic: it describes what a from-scratch player must do as much
as what crewborg does.

For the *meaning* of the game (rules, scoring, strategy) see
[`./crewrift-gameplay.md`](./crewrift-gameplay.md); for crewborg's concrete
decoder that turns this stream into beliefs see
[`../../crewborg/docs/perception-and-belief.md`](../../crewborg/docs/perception-and-belief.md).

---

## VERSION WARNING — read first

- **Verified at coworld-crewrift commit `a3e2859`**, where `sim.nim:GameVersion`
  is `"1"` (`grep -n 'GameVersion\*' src/crewrift/sim.nim`).
- The **Sprite-v1 transport** (message types, framing, button bits) is owned by
  bitworld (`bitworld/docs/sprite_v1.md`, `bitworld/src/bitworld/spriteprotocol.nim`)
  and is stable.
- The **scene vocabulary is GAME-DEFINED, not in `sprite_v1.md`.** Every label
  string, every object-id base, and the camera/offset math live in Crewrift's
  `src/crewrift/{sim,global}.nim` and are the game's to change at any version
  bump. They are **not** part of the transport spec.
- **The deployed league game is pinned at `CREWRIFT_REF`** (in the toolkit's
  `versions.env`), which may differ from this checkout. If perception
  misbehaves, **re-derive the labels / id bases / offsets from that ref's
  `global.nim` and `sim.nim`** — do not trust this doc against a different ref.

---

## How to use / re-verify this doc

Every fact below cites a **`file:Symbol`** (a proc or const **name**, never a
line number, so it survives edits) plus a tiny **re-check recipe** (a `grep`).
Paths are relative to the coworld-crewrift repo root
(the repo this player ships in).

Three source files are authoritative:

| File | Owns |
| --- | --- |
| `bitworld/src/bitworld/spriteprotocol.nim` | the transport: message-type bytes, button bits, `decodeInputMask`, `InputState` |
| `src/crewrift/global.nim` | the `/player` renderer: what labels/object-ids get emitted, camera application, walkability + shadow pixels |
| `src/crewrift/sim.nim` | the game: input semantics (`applyInput`, `tryKill`/`tryVent`/`tryReport`/`tryCallButton`), voting cursor, all the id-base/offset constants |

The spec text (`bitworld/docs/sprite_v1.md`) is the transport contract; the two
`crewrift/*.nim` files are the scene contract. **When this doc and the source
disagree, the source wins — and so does `CREWRIFT_REF`'s source over this
checkout's.**

If your decoder breaks after a game bump, the fastest re-derivation is:
`grep -nE 'addSpriteChanged|addObject|addProtocolObject' src/crewrift/global.nim`
to see every sprite label and object placement the renderer emits.

---

## 1. Transport

A Crewrift player is a containerized process that:

1. Reads its WebSocket URL from the env var **`COWORLD_PLAYER_WS_URL`** (a
   fully-formed `ws://<host>:8080/player?slot=<N>&token=<T>` — the platform fills
   it in). The path is `/player`
   (`sim.nim:WebSocketPath` = `"/player"`; default port `8080` is
   `spriteprotocol.nim:DefaultPort`).
   Re-check: `grep -n 'WebSocketPath\*\|DefaultPort\*' src/crewrift/sim.nim bitworld/src/bitworld/spriteprotocol.nim`
2. Connects over a **binary** WebSocket. Frames are binary; never expect JSON or
   text on this socket. Sprite payloads can be large — use a client with no
   frame-size cap.
3. Plays **one slot**. Join query params `name`, `slot`, `token` are read
   server-side (`server.nim` handler reads `request.queryParams` for
   `"name"`/`"slot"`/`"token"`; `slot` is zero-based, `token` is simple slot
   auth). Re-check: `grep -n 'queryParams.getOrDefault("slot"\|"token"\|"name"' src/crewrift/server.nim`
4. Exits cleanly (status 0) when the socket closes — that is "episode over," not
   an error to retry.

The game advances at **24 ticks/sec** (`sim.nim:TargetFps` = `24`). The server
pushes a burst of Sprite-v1 messages per tick; you send input packets when your
held state changes. There is no request/response handshake — two independent
streams over one socket. An optional `0x85` *Player Ready* packet is a frame-
pacing hint only (`spriteprotocol.nim:SpriteClientReady`); Crewrift ignores its
content in the player input path (`global.nim:applyPlayerViewerMessage` discards
`SpriteClientReadyMessage`).

The game-agnostic image/build/secrets contract is covered by the platform doc
[`./coworld-platform.md`](./coworld-platform.md) and crewborg's
[`../best_practices.md`](../best_practices.md); this doc is only the wire bytes.

---

## 2. Sprite-v1 framing (the transport)

Every message starts with a **1-byte message type**. All multi-byte integers are
**little-endian**; coordinates are signed `i16`. (`bitworld/docs/sprite_v1.md`
§Integer Encoding.)

### 2.1 Message types

Source of truth: the `SpriteMessage*` / `SpriteClient*` consts in
`spriteprotocol.nim`. Re-check:
`grep -nE 'SpriteMessage|SpriteClient' bitworld/src/bitworld/spriteprotocol.nim`

| Byte | Dir | Message | const |
| ---: | --- | --- | --- |
| `0x01` | S→C | Define Sprite | `SpriteMessageSprite` |
| `0x02` | S→C | Define Object | `SpriteMessageObject` |
| `0x03` | S→C | Delete Object | `SpriteMessageDeleteObject` |
| `0x04` | S→C | Clear Objects | `SpriteMessageClearObjects` |
| `0x05` | S→C | Set Viewport | `SpriteMessageViewport` |
| `0x06` | S→C | Define Layer | `SpriteMessageLayer` |
| `0x81` | C→S | Input Text (chat) | `SpriteClientChat` |
| `0x82` | C→S | Mouse Position | `SpriteClientMouseMove` |
| `0x83` | C→S | Mouse Button | `SpriteClientMouseButton` |
| `0x84` | C→S | Player Input (buttons) | `SpriteClientInput` |
| `0x85` | C→S | Player Ready | `SpriteClientReady` |
| `0x86` | C→S | Debug Sprites | `SpriteClientDebugSprite` |

Crewrift play needs only **`0x84`** (buttons) and **`0x81`** (chat) outbound.
Mouse (`0x82`/`0x83`) is for the spectator `/global` viewer, not `/player`.
`0x86` lets a player draw private debug overlays into its own replay
(`global.nim:addDebugSpritePacket`) — diagnostic only, no game-state effect.

### 2.2 The three retained tables

Sprite v1 is a **structured retained scene**, not a framebuffer. The server sends
**deltas**; you keep three tables and mutate them per message
(`sprite_v1.md` §Rendering Model). You **cannot** treat each frame as a complete
scene.

| Table | Key | Holds |
| --- | --- | --- |
| **Layers** | `u8` layer id | type, flags, viewport W/H |
| **Sprites** | `u16` sprite id | width, height, **label** (UTF-8), Snappy-compressed RGBA |
| **Objects** | `u16` object id | x, y, z, layer, sprite id |

Message → table effect:

| Byte | Effect |
| ---: | --- |
| `0x01` Define Sprite | upsert sprite `id` → (w, h, **label**, RGBA). Label is the semantic key (§4); pixels matter only for two sprites (§5). |
| `0x02` Define Object | upsert object `id` → (x, y, z, layer, sprite id). How entities appear and move. |
| `0x03` Delete Object | remove object `id`. The renderer deletes objects that were present last tick but not this tick (`global.nim:buildSpriteProtocolPlayerUpdates` end: it diffs `state.objectIds` vs `currentIds`). |
| `0x04` Clear Objects | drop **all** objects (sprite defs stay). Sent once at init (see §3). |
| `0x05` Set Viewport | set a layer's viewport W/H. |
| `0x06` Define Layer | declare a layer's type + flags. |

The Define-Sprite pixel payload (when you need it) is a **Snappy** stream that
decompresses to exactly `W*H*4` bytes of RGBA (`sprite_v1.md` §Define Sprite).
Crewrift writes pixels with `global.nim:addSpriteChanged` → `addSprite`.

---

## 3. The `/player` stream shape

The renderer for a player slot is two procs in `global.nim`:

- **`global.nim:buildSpriteProtocolPlayerInit`** — the one-time **init burst**.
  Re-check: `grep -n 'proc buildSpriteProtocolPlayerInit' src/crewrift/global.nim`
- **`global.nim:buildSpriteProtocolPlayerUpdates`** — one **diff message per
  tick** thereafter. Re-check:
  `grep -n 'proc buildSpriteProtocolPlayerUpdates' src/crewrift/global.nim`

`buildSpriteProtocolPlayerUpdates` calls `buildSpriteProtocolPlayerInit` exactly
once (gated on `nextState.initialized`), so the very first frame you receive is
init + first diff concatenated; every later frame is a pure diff. The server
sends each frame with `server.nim` send loop
(`sockets[i].send(frameBlob, BinaryMessage)` after calling
`buildSpriteProtocolPlayerUpdates`). Re-check:
`grep -n 'buildSpriteProtocolPlayerUpdates\|send(frameBlob' src/crewrift/server.nim`

### Everything is on layer 0

For the real `/player` socket, `buildSpriteProtocolPlayerUpdates` is called with
its **default `layerId = MapLayerId` = `0`** (the send loop passes no layer
arg). So **every object you receive is on layer 0.** Re-check:
`grep -n 'MapLayerId\* =' src/crewrift/sim.nim` (→ `0`).

The init burst declares that layer once:

| Init step (`buildSpriteProtocolPlayerInit`) | Bytes | Source |
| --- | --- | --- |
| Clear Objects | `0x04` | `clearObjects` default true |
| Define Layer 0 | type `MapLayerType`=0, flags `ZoomableLayerFlag`=1 | `sim.nim:MapLayerType`, `ZoomableLayerFlag` |
| Set Viewport 0 | `ScreenWidth`×`ScreenHeight` = **128×128** | `spriteprotocol.nim:ScreenWidth`/`ScreenHeight` |
| Define the static sprites | map, walkability, task bubble, imposter icon (+cooldown), ghost icon, meeting button, task arrow, all player/ghost/body color variants, vote UI sprites | see §4 label table |

Re-check viewport size: `grep -n 'ScreenWidth\* =\|ScreenHeight\* =' bitworld/src/bitworld/spriteprotocol.nim` (both `128`).

> **Note — `PovLayerId`=7 / `FullScreenLayerType`=9 is a different path.** The
> `/global` spectator's point-of-view overlay calls the same procs with
> `layerId = PovLayerId` and per-player id/sprite offsets (`PovObjectIdOffset` /
> `PovSpriteIdOffset` = `30000`), and renders a pre-composited "map view" sprite
> instead of a pannable map object. **A `/player` client never sees this.** If
> you decode the map at id 1 on layer 0, you are on the player path. Re-check:
> `grep -n 'PovLayerId\|PovObjectIdOffset\|"map view"' src/crewrift/global.nim`

---

## 4. The Crewrift scene vocabulary (GAME-DEFINED)

This is the part most likely to drift across versions. **None of it is in
`sprite_v1.md`** — it is emitted by `global.nim` using id-base constants from
`sim.nim`. Identity is recovered from the pair **(object-id range, sprite
label)** plus the object's xy — **no computer vision** beyond two pixel masks
(§5).

### 4.1 Camera & self position

You are the **camera, not an object** — there is no "me" object in the stream.

- The **map** is object id `1`, sprite id `1`, label `"map"`
  (`sim.nim:MapObjectId` = `MapSpriteId` = `1`; emitted by
  `buildSpriteProtocolPlayerInit` with label `"map"`). On layer 0 the map object
  is placed at **`(-cameraX, -cameraY)`** each tick
  (`buildSpriteProtocolPlayerUpdates`: `addProtocolObject(MapObjectId, -cameraX, -cameraY, ...)`).
  So **`cameraX = -mapObject.x`, `cameraY = -mapObject.y`.**
  Re-check: `grep -n 'MapObjectId\* =\|MapSpriteId\* =' src/crewrift/sim.nim` and
  `grep -n '\-cameraX' src/crewrift/global.nim`
- The camera is centered on your avatar: `sim.nim:playerView` sets
  `cameraX = player.x - ScreenWidth div 2` (= `player.x - 64`), likewise y. So
  **your world position = `(cameraX + 64, cameraY + 64)`**.
  Re-check: `grep -n 'proc playerView' src/crewrift/sim.nim`
- On-screen test: a world point `(wx,wy)` is visible iff
  `camera ≤ (wx,wy) < camera + (128,128)`.

> **Decoder caveat — crewborg's tuned offsets differ from the strict source.**
> crewborg's `perception/constants.py` uses `SELF_OFFSET_X/Y = (60, 66)` and
> `ENTITY_COLLISION_DX/DY = (3, 9)`, not the source-exact `(64, 64)` and the
> sprite draw offset (`sim.nim:SpriteDrawOffX`=8, `SpriteDrawOffY`=12, +1px
> outline → 9/13). These are empirically tuned values in the decoder, not
> protocol facts. **If you build your own decoder, derive from the source math
> above**; if you read crewborg's, know its numbers are pragmatic approximations.
> Re-check source: `grep -n 'SpriteDrawOffX\* =\|SpriteDrawOffY\* =' src/crewrift/sim.nim`

### 4.2 Object-id ranges (the `/player` stream)

Bases are `sim.nim` consts; the `+offset` is computed by the named `global.nim`
proc. Ranges are **disjoint**, so the same label in two ranges means two
different things (a live player at `1000+` vs. a voting-grid cell at `9300+`).

| Object id | What it is | Emitter (`global.nim`) | Base const (`sim.nim`/`global.nim`) |
| --- | --- | --- | --- |
| `1` | **map** | `buildSpriteProtocolPlayerUpdates` | `MapObjectId` |
| `1000 + joinOrder` | **live players in view** | `spriteObjectId` | `PlayerObjectBase` = 1000 |
| `2000 + bodyIndex` | **bodies on the floor** | `spriteBodyObjectId` | `BodyObjectBase` = 2000 |
| `3000 + taskIndex` | **task bubble** (a task you can do, in view) | `spriteTaskObjectId` | `TaskObjectBase` = 3000 |
| `4000` | **task-counter** text (tasks remaining) | `buildSpriteProtocolPlayerUpdates` | `SelectedTextObjectId` = 4000 |
| `5006` | **interstitial background** (present ⇒ you are *not* in Playing) | `buildSpriteProtocolPlayerUpdates` | `SpritePlayerInterstitialObjectId` = 5006 |
| `5008` | **ghost-icon** (you are dead) **or kill-icon** (you are imposter) | `buildSpriteProtocolPlayerUpdates` | `SpritePlayerRemainingObjectId` = 5008 |
| `5009` | **task progress bar** | `buildSpriteProtocolPlayerUpdates` | `SpritePlayerProgressObjectId` = 5009 |
| `5016` | **tick marker** (invisible 1×1, label `"tick <N>"`) | `addSpritePlayerTickMarker` | `SpritePlayerTickObjectId` = 5016 |
| `7000 + taskIndex` | **task arrow** (radar pointer to off-screen task) | `addSpritePlayerTaskArrows` | `SpritePlayerTaskArrowObjectBase` = 7000 |
| `9000 + i` | **interstitial / chat-line text** (label = the text) | `addProtocolTextSprites` | `ProtocolTextObjectBase` = 9000 |
| `9200 + j` | **chat speaker icon** | `addVisibleVoteChatIcons` | `ProtocolChatIconObjectBase` = 9200 |
| `9300 + idx` | **voting candidate grid** (alive/dead census) | `addProtocolVoteActorSprites` | `ProtocolVoteIconObjectBase` = 9300 |
| `9400 + i` | **lobby icons** | `addProtocolLobbyActorSprites` | `ProtocolLobbyIconObjectBase` = 9400 |
| `9500 + slot` | **role-reveal icons** (imposter sees its *team* here) | `addProtocolRoleRevealActorSprites` | `ProtocolRoleIconObjectBase` = 9500 |
| `9600` | **vote-result** ejected-player icon | `addProtocolVoteResultActorSprites` | `ProtocolResultIconObjectBase` = 9600 |
| `9700 + i` | **game-over** per-player icons | (game-over interstitial) | `ProtocolGameOverIconObjectBase` = 9700 |
| `9800`, `9801` | **meeting-call** icons (caller / body / button) | `addProtocolMeetingCallActorSprites` | `ProtocolMeetingIconObjectBase` = 9800 |
| `10000` | **vote cursor** (your selection) | `addProtocolVoteUiSprites` | `SpritePlayerVoteCursorObjectId` = 10000 |
| `10001` | **vote self marker** | `addProtocolVoteUiSprites` | `SpritePlayerVoteSelfMarkerObjectId` = 10001 |
| `10002` | **vote timer** bar | `addProtocolVoteUiSprites` | `SpritePlayerVoteProgressObjectId` = 10002 |
| `10003` | **vote chat background** | `addProtocolVoteUiSprites` | `SpritePlayerVoteChatBgObjectId` = 10003 |
| `10004` | **kill cooldown progress bar** | `buildSpriteProtocolPlayerUpdates` | `SpritePlayerKillProgressObjectId` = 10004 |
| `10100 + target*16 + voter` | **vote dots** (who voted for whom) | `addProtocolVoteUiSprites` | `SpritePlayerVoteDotObjectBase` = 10100, `MaxPlayers`=16 |
| `10400 + voter` | **skip-vote dots** | `addProtocolVoteUiSprites` | `SpritePlayerVoteSkipDotObjectBase` = 10400 |
| `13000` | **shadow / line-of-sight overlay** | `buildSpriteProtocolPlayerUpdates` | `SpritePlayerShadowObjectId` = 13000 |

Re-check all bases at once:
`grep -nE 'ObjectBase\* =|ObjectId\* =|ObjectId =|ObjectBase =' src/crewrift/sim.nim src/crewrift/global.nim`

**Role-reveal asymmetry (id `9500+`):** during RoleReveal,
`addProtocolRoleRevealActorSprites` shows an **imposter only the imposter team's
icons**, and a crewmate **all players**. So if `9500+` shows a strict subset, you
are an imposter and that subset is your team. Re-check:
`grep -n 'viewerIsImp' src/crewrift/global.nim`

### 4.3 Sprite labels (the perception contract)

Labels are emitted by `global.nim` `addSpriteChanged(... "<label>")` calls. The
color token comes from `sim.nim:PlayerColorNames`. Re-check the full set:
`grep -nE 'addSpriteChanged' src/crewrift/global.nim` and read the label
argument.

| Label (exact) | Meaning | Emitter |
| --- | --- | --- |
| `"map"` | full game map sprite (decode region under camera) | `buildSpriteProtocolPlayerInit` |
| `"walkability map"` | pixel mask of walkable cells (§5) | `buildSpriteProtocolPlayerInit` |
| `"shadow"` | screen-sized line-of-sight overlay (§5) | `buildSpriteProtocolPlayerUpdates` |
| `"task bubble"` | a doable task marker | `buildSpriteProtocolPlayerInit` |
| `"task arrow"` | off-screen task pointer | `buildSpriteProtocolPlayerInit` |
| `"imposter icon"` | present ⇒ **you are imposter**, kill ready | `buildSpriteProtocolPlayerInit` |
| `"imposter icon cooldown"` | imposter, kill **not** ready | `buildSpriteProtocolPlayerInit` |
| `"ghost icon"` | present ⇒ **you are dead** (ghost) | `buildSpriteProtocolPlayerInit` |
| `"meeting button"` | emergency button sprite | `buildSpriteProtocolPlayerInit` |
| `"player <color> right"` / `"player <color> left"` | a live player, facing | per-color loop in `buildSpriteProtocolPlayerInit` |
| `"selected player <color> right\|left"` | selected variant (global viewer) | `buildSpriteProtocolPlayerInit` |
| `"ghost <color> right\|left"` (+ `"selected ghost …"`) | a ghost | `buildSpriteProtocolPlayerInit` |
| `"body <color>"` | a dead body | `buildSpriteProtocolPlayerInit` |
| `"progress bar <N>%"` | task **or** kill-cooldown progress (disambiguate by object id 5009 vs 10004) | `buildSpriteProtocolPlayerUpdates` |
| `"task counter <N>"` | tasks remaining | `buildSpriteProtocolPlayerUpdates` |
| `"tick <N>"` | authoritative engine tick (object id 5016) | `addSpritePlayerTickMarker` |
| `"vote cursor"` / `"vote skip cursor"` | your selection cursor / the skip cell | `addSpriteProtocolInterstitialSprites` |
| `"vote timer"` | voting countdown bar | `addProtocolVoteUiSprites` |
| `"vote self marker <color>"` | marker on your own row | `addSpriteProtocolInterstitialSprites` |
| `"vote dot <color>"` | one cast vote (tally) | `addSpriteProtocolInterstitialSprites` |
| `"vote chat background"` | chat panel backdrop | `addProtocolVoteUiSprites` |
| interstitial text (label = the words) | phase text: `WAITING`, `NEED MORE!`, `STARTING`, `GAME INFO`, `GAME IN`/`PROGRESS`, `IMPS`, `CREWMATE`, `SKIP`, `NO ONE`/`DIED`, `WAS KILLED`, `CREW WINS`, `IMPS WIN`, `DRAW`, and meeting-call lines (`"<Color> reported"`, `"pressed"/"the button"`, `"called"/"a meeting"`) | `interstitialTextItems` |
| chat line (label = raw message text) | what a player said in a meeting | `addVisibleVoteChatText` |

**The 16 color names**, in `PlayerColorNames` order (index = color slot):
`red, blue, green, pink, orange, yellow, purple, cyan, lime, brown, beige, navy,
teal, rose, maroon, gray`. Re-check:
`grep -n -A18 'PlayerColorNames\* =' src/crewrift/sim.nim`

> **Tick is ground truth, not a local counter.** Parse the integer after
> `"tick "` (object/sprite id 5016, re-sent every frame) for authoritative game
> time; a local message counter lags whenever you fall behind the real-time
> stream. Source: `addSpritePlayerTickMarker` builds the label
> `"tick " & $sim.tickCount`.

> **Phase detection.** The simplest reliable signal: object id **`5006`**
> (`SpritePlayerInterstitialObjectId`) is present **iff you are not in the
> Playing phase** — `buildSpriteProtocolPlayerUpdates` renders the interstitial
> branch (which adds 5006 + interstitial text) when `sim.phase != Playing`.
> Within an interstitial, read the exact phase from the text labels above.

---

## 5. The two pixel masks you must decode

Everything else is read from labels; these two sprites' **content** is the data.

### `"walkability map"` — static, full map size

`global.nim:buildWalkabilitySpritePixels` builds an RGBA mask the size of the
full map (`gameMap.width × gameMap.height`). A cell is **opaque white
`(255,255,255,255)` ⇒ walkable**, **fully transparent (alpha 0) ⇒ not walkable**
(wall/void). Sent once in the init burst. Use it for pathfinding around walls.
Re-check: `grep -n 'proc buildWalkabilitySpritePixels' src/crewrift/global.nim`

### `"shadow"` — dynamic, screen-sized, line-of-sight

`global.nim:buildPlayerShadowSprite` builds a **128×128** overlay (object id
13000, drawn at z `SpritePlayerShadowZ` = `-32767`, i.e. above the map). An
**opaque shadow-colored pixel ⇒ that screen cell is occluded / out of sight**; a
**transparent pixel ⇒ visible** (line of sight from your avatar). It is re-sent
when the camera or origin moves (`buildSpriteProtocolPlayerUpdates` tracks
`shadowCameraX/Y` and `usePlayerShadowMask`). **Ghosts get no shadow** (a dead
viewer sees everything; the shadow object is skipped when `viewerIsGhost`).
Re-check: `grep -n 'proc buildPlayerShadowSprite\|viewerIsGhost\|SpritePlayerShadowZ' src/crewrift/global.nim`

---

## 6. Sending: input is buttons, not commands

There is **no high-level action API.** You play like a human: a held-button
bitmask (`0x84`) and, in meetings, typed chat (`0x81`). Every action — do task,
kill, report, press button, vote — is the right button at the right place/time.

### 6.1 `0x84` Player Input — the held-button bitmask

One byte after the `0x84` header: the **currently held** buttons. Emit whenever
the held set changes; omitted bits are treated as released (`sprite_v1.md`
§Player Input). Bit values are the `Button*` consts in `spriteprotocol.nim`,
decoded by `spriteprotocol.nim:decodeInputMask` into
`InputState{up,down,left,right,select,attack,b}`. Re-check:
`grep -nE 'Button(Up|Down|Left|Right|Select|A|B)\* =|proc decodeInputMask' bitworld/src/bitworld/spriteprotocol.nim`

| Bit | Value | const | `InputState` field | Crewrift meaning |
| ---: | ---: | --- | --- | --- |
| 0 | `0x01` | `ButtonUp` | `up` | move up / vote cursor backward |
| 1 | `0x02` | `ButtonDown` | `down` | move down / vote cursor forward |
| 2 | `0x04` | `ButtonLeft` | `left` | move left / vote cursor backward |
| 3 | `0x08` | `ButtonRight` | `right` | move right / vote cursor forward |
| 4 | `0x10` | `ButtonSelect` | `select` | **decoded but unused** by the sim (no gameplay effect) |
| 5 | `0x20` | `ButtonA` | `attack` | the universal **A** — task / report / button / kill / confirm vote |
| 6 | `0x40` | `ButtonB` | `b` | **B** — vent (imposter only) |
| 7 | `0x80` | — | — | reserved, must be `0` |

`select` (bit 4) maps to `InputState.select` but `applyInput` never reads it —
verify with `grep -n '\.select' src/crewrift/sim.nim` (no gameplay use).

### 6.2 Edge vs. held — and why presses are never dropped

The server distinguishes a **held** button from a fresh **press**:

- Inbound `0x84` updates two masks per socket: the held `inputMask` and an
  accumulated `pressedMask` (bits that transitioned not-held → held this frame).
  `global.nim:applyPlayerViewerMessage`:
  `pressedMask = pressedMask or (item.mask and not inputMask); inputMask = item.mask`.
  Re-check: `grep -n 'pressedMask = pressedMask' src/crewrift/global.nim`
- Per tick the loop computes `appliedMask = currentMask or pressedMask` and
  decodes **that** into the tick's `InputState`, then clears the pressed bits
  from `prevInputs` so edge tests fire. `server.nim` send loop:
  `inputs[playerIndex] = decodeInputMask(appliedMask)`; see also
  `server.nim:clearPressedInputMask`. Re-check:
  `grep -n 'appliedMask\|clearPressedInputMask' src/crewrift/server.nim`

**Consequence:** a button you press and release **within a single tick still
registers** (it lands in `pressedMask`). One-shot actions are detected as
`fresh = input.x and not prevInput.x` in `sim.nim:applyInput`. So you do **not**
need to hold an edge action for a tick — but you may insert a released tick
between repeated edge presses to be safe.

### 6.3 A-press semantics during Playing (edge-triggered, in order)

`sim.nim:applyInput` computes `freshA = input.attack and not prevInput.attack`.
On a fresh A it runs, **in this exact order, stopping as soon as a meeting
starts**:

1. `sim.nim:tryReport` — report the nearest body within `ReportRange` (20) ⇒
   starts a meeting.
2. if still Playing, `sim.nim:tryCallButton` — if you're standing on the
   emergency button (and `buttonCallsUsed < buttonCalls`, default 1) ⇒ starts a
   meeting.
3. if still Playing and you're the imposter, `sim.nim:tryKill` — kill the nearest
   non-imposter within `KillRange` (20), if `killCooldown == 0`.

Re-check: `grep -n 'freshA\|tryReport\|tryCallButton\|tryKill' src/crewrift/sim.nim`

**Tasks (crewmate) use HELD A, not an edge.** While A is held *and you are
standing still* (`inputX == 0 and inputY == 0`) on an assigned, incomplete task
tile, `applyInput` increments `taskProgress` each tick until
`taskCompleteTicks` (72) ⇒ task done. **Any movement, or releasing A, resets
progress.** Re-check: `grep -n 'taskProgress\|activeTask\|completeTask' src/crewrift/sim.nim`

So the canonical action recipes:

| Action | Buttons |
| --- | --- |
| Walk | hold the d-pad direction(s) |
| Do a task | navigate onto the task tile, then **hold A while motionless** until `"progress bar 100%"` |
| Kill (imposter) | when `"imposter icon"` (not cooldown) is up, stand within range and **tap A** |
| Report body / press emergency button | stand on the body / button and **tap A** |
| Vent (imposter) | stand near a vent and **tap B** |

### 6.4 B-press semantics

`freshB = input.b and not prevInput.b`; on fresh B, if you are the imposter,
`sim.nim:tryVent` teleports you to the next vent in the same group within
`VentRange` (16). Crewmate B does nothing. Re-check:
`grep -n 'freshB\|proc tryVent\|VentRange' src/crewrift/sim.nim`

### 6.5 Voting input — cursor + confirm (edge-triggered)

During Voting the per-tick handler (in `sim.nim`, the Voting branch of the tick
that calls `moveCursor`) processes input only for **living players who have not
yet cast** (`votes[i] == -1`):

- **Cursor move (edge):** `backward = (up edge) or (left edge)`;
  `forward = (down edge) or (right edge)`; if exactly one is true,
  `sim.nim:moveCursor(±1)`. The cursor ranges over `n+1` cells — the `n`
  candidates plus a **skip** cell at index `n` — and `moveCursor` skips dead
  candidates. Re-check: `grep -n 'proc moveCursor' src/crewrift/sim.nim`
- **Confirm (edge A):** `input.attack and not prev.attack` casts: if the cursor
  is on the skip cell (`cur == players.len`), `votes[i] = -2` (skip); else
  `votes[i] = cur`. **A vote cannot be changed once cast** (the `votes[i] == -1`
  guard skips you forever after). Re-check:
  `grep -n 'votes\[i\] = -2\|votes\[i\] = cur\|allVotesCast' src/crewrift/sim.nim`

Not voting at all incurs a penalty (see gameplay doc) — always cast something;
skip is a valid, penalty-free vote.

### 6.6 `0x81` Input Text — meeting chat only

`0x81` + `u16` length + printable-ASCII bytes (`sprite_v1.md` §Input Text).
Inbound text reaches `sim.nim:addVotingChat`, which **drops it unless
`sim.phase == Voting`** (and the speaker is alive, and a per-player
`MessageCooldownTicks` = 100-tick cooldown has elapsed). So chat is functionally
**Voting-only**. Re-check: `grep -n 'proc addVotingChat\|phase != Voting' src/crewrift/sim.nim`

Keep chat bytes out of the button mask — they are separate packets.

---

## 7. Minimal wire examples

**Inbound, first frame (schematic):** `0x04` (clear) · `0x06 00 00 01` (define
layer 0, map, zoomable) · `0x05 00 80 00 80 00` (viewport 0 = 128×128) · a run of
`0x01` Define-Sprite messages (`"map"`, `"walkability map"`, `"player red
right"`, …) · `0x02` Define-Object for the map at `(-cameraX,-cameraY)` and for
each visible player/body/task · the invisible `"tick <N>"` marker (id 5016).
Subsequent frames are just the `0x02`/`0x03` deltas plus the re-sent tick marker
and any changed bars/shadow.

**Outbound — start walking right:** `84 08` (hold `ButtonRight`).
**Outbound — tap A to interact:** `84 28` (hold Right+A = `0x08|0x20`) then
`84 08` (release A). Because of §6.2, even `84 20` followed immediately by
`84 00` registers the A press.
**Outbound — say something in a meeting:** `81` + `u16` length + ASCII.

---

## See also

- [`./crewrift-gameplay.md`](./crewrift-gameplay.md) — rules, scoring, strategy (the *why*).
- [`./crewrift-replays.md`](./crewrift-replays.md) — reading a finished game.
- [`./coworld-platform.md`](./coworld-platform.md) — the game-agnostic image/build/ship contract.
- [`./README.md`](./README.md) — reference-doc index.
- [`../best_practices.md`](../best_practices.md) — crewborg engineering practices.
- [`../../crewborg/docs/perception-and-belief.md`](../../crewborg/docs/perception-and-belief.md) — crewborg's concrete decoder over this stream.
- Authoritative source: `bitworld/docs/sprite_v1.md` (transport),
  `src/crewrift/global.nim` + `src/crewrift/sim.nim` (scene + input), at the ref
  pinned by `CREWRIFT_REF`.
</content>
</invoke>
