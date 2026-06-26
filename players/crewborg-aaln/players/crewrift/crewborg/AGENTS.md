# Crewborg — Agent Notes

**Crewborg** is a Player-SDK–based agent that plays the Coworld game
**Crewrift**. This file is the orientation doc: what the relevant codebases are,
how they fit together, the API/protocol crewborg must satisfy, and pointers to
source for detail. The durable design decisions live in [`design.md`](./design.md).

Everything below was read from source on **2026-05-28**. These repos are in
active development; **verify any specific symbol, path, or constant against the
cited file before relying on it.**

> **Status:** Implemented end-to-end — the agent plays both roles (crewmate
> tasks / meetings / voting / report / flee, crewmate ghost noclip tasking,
> plus imposter Evade / Pretend / Search / Hunt and the
> `kill`/`vent` intents; the imposter **evades after fresh kills** and may report
> non-fresh visible bodies;
> imposter Pretend fakes real task stations in likely occupied rooms, Search owns
> pre-kill target acquisition, and
> Hunt is gated on a visible kill opportunity). Attend Meeting has an opt-in
> LLM chat/vote path with deterministic fallback. `CREWBORG_BE_DUMB=1` is an
> aggressive imposter experiment that skips Pretend/Evade/body reports and keeps
> the imposter in Search/Hunt. `CREWBORG_DICK_MODE=1` is an opt-in crewmate
> experiment that calls the emergency button once before the first kill cooldown
> can clear, chats `haha, fuck you imposters` only if its own button press opened
> the meeting, skip-votes, then resumes after the meeting.
> See [`README.md`](./README.md) for a capability summary and
> [`design.md`](./design.md) for the settled architecture. crewborg sits at
> `players/players/crewrift/crewborg/` inside the `players` uv workspace.

---

## The big picture

Three layers. Crewborg is the top; it treats the protocol below as a contract.

```
  crewborg (this dir)              the agent we are building
        │ builds an AgentRuntime, feeds it scene state, sends back input packets
        ▼
  Player SDK  (players.player_sdk) generic two-loop "Cyborg" agent framework
        │ binary Sprite-v1 WebSocket  (crewborg writes this bridge itself)
        ▼
  Crewrift game server (Nim)       authoritative game; defines the rules
```

- **Crewrift** is an *Among Us–style social-deduction game* (crewmates do tasks
  & vote; imposters kill, vent, and blend in). It is **not** cooperative
  survival. Implemented in **Nim**; it speaks the **binary Sprite v1** wire
  protocol. Crucially, Sprite v1 is a **structured scene protocol, not a
  framebuffer**: the server streams object placements with **exact world
  coordinates** plus sprites that carry **text labels**, *specifically so agents
  read game state from structured data and labels — no computer vision*
  (see [§2 Sprite v1](#sprite-v1-protocol-structured-scene-not-a-framebuffer)).
  It is uploaded to Softmax/Observatory as a **Coworld** (a packaged,
  league-runnable game).
- **The Player SDK** (`players.player_sdk`) is a generic Python framework for
  building agents with a *fast symbolic inner loop* + a *slower strategy loop*
  (the architecture is historically called "Cyborg" in the SDK docs). It is
  transport- and game-agnostic.
- **Crewborg** plugs Crewrift-specific perception/belief/modes/strategy into the
  SDK's runtime, and ships as a Docker image the Coworld runner launches.

**Key consequence:** the SDK gives you the *loop and the scaffolding*
(`AgentRuntime`, `Mode`, `ModeDirective`, strategy runners, tracing). You supply
*everything game-specific*: maintain Crewrift's scene state (objects + their
labels and coordinates) as belief, decide modes, and resolve intents into
Sprite-v1 button/text packets. Crewborg also owns its **own websocket bridge** —
the SDK's JSON bridge does not fit a binary game (see
[Transport](#transport-the-part-the-sdk-does-not-do-for-you)).

---

## 1. The Player SDK (`players.player_sdk`)

**Location:** `~/coding/players_checkouts/players/players/player_sdk`
**Import root:** `players.player_sdk` (the repo is the `players` package).
Pure Python, deps `numpy`/`pydantic`/`websockets` (workspace `pyproject.toml`).

### The two-loop model

```
perceive ──► update_belief ──► mode.decide ──► resolve_action ──► wire command
   ▲                                                                   (per tick)
   │   reflexes (priority-ordered, urgent overrides)
   │
   └── strategy loop (slower):  BeliefSnapshot ──► validated ModeDirective
```

The **inner loop never blocks on strategy**. If no fresh directive is ready, the
runtime applies a configured **default directive**. Directives carry a **TTL**;
on expiry the runtime falls back to default. This keeps the agent live even when
the strategy (e.g. an LLM call) is slow or stalls.

### What you import (verified from `player_sdk/__init__.py`)

```python
from players.player_sdk import (
    AgentRuntime,                 # per-tick inner-loop orchestrator (runtime.py)
    Mode, ModeRegistry,           # deterministic local policies + validation (modes.py)
    ModeDirective, ModeParams, EmptyModeParams, ModeDecision,  # directive types (types.py)
    ActionIntent, ActionCommand,  # symbolic intent / concrete command (types.py)
    SharedMemory, SharedMemoryView, BeliefSnapshot, StrategyResult,  # (types.py)
    Strategy, AsyncStrategy, StrategyRunner,                    # strategy protocols (strategy.py)
    SynchronousStrategyRunner, ThreadedStrategyRunner,
    AsyncStrategyRunner, ManualStrategyRunner,                  # concrete runners
    Reflex, ReflexRule, RuntimeContext,                         # reflexes (runtime.py)
    DirectiveValidationError,                                   # (modes.py)
    OverwriteBuffer,                                            # latest-wins buffer (buffers.py)
    # tracing/metrics (trace.py):
    TraceSink, MetricsSink, TraceEvent, MetricSample,
    ListTraceSink, ListMetricsSink, LoggingTraceSink, LoggingMetricsSink,
    NullTraceSink, NullMetricsSink, WandbMetricsSink,
)
```

The SDK is **generic over six type parameters**: `AgentRuntime[Observation,
Percept, Belief, ActionState, Intent, Command]`. You define all six for
Crewrift.

### What you implement (the game supplies three functions + modes + strategy)

1. **Types** — `Observation` (the current Sprite-v1 scene state: the object /
   sprite / layer tables), `Percept` (parsed tick view: resolved entities with
   labels + coordinates), `Belief` (persistent world model), `ActionState`
   (transport-side mechanics: pending chat, button pulses, movement plan),
   `Intent` (symbolic, e.g. `noop`/`input`/`chat`), `Command` (concrete wire
   packets).
2. **`perceive(observation, tick) -> percept`** — interpret the scene: join each
   object to its sprite's **label** and read the object's **x/y** directly.
   Keep raw sprite pixels *out* of belief.
3. **`update_belief(belief, percept) -> None`** — fold the percept into belief.
   Belief is the **only** interface the strategy sees.
4. **`resolve_action(intent, belief, action_state) -> command`** — translate a
   symbolic intent into wire packets. *All* transport mechanics live here
   (button-bitmask encoding, chat buffering, movement/pathing timing).
5. **`Mode` subclasses** — deterministic local policies, each with a typed
   `params_type` and a `decide(belief, action_state) -> Intent | ModeDecision`.
   Register them in a `ModeRegistry`.
6. **`Strategy`** — reads a `BeliefSnapshot` and returns a validated
   `ModeDirective` (which mode + typed params + TTL). Run it via a
   `*StrategyRunner`.

### Assembly pattern (mirror this exactly)

The SDK's `docs/metta_cogames_framework/examples/toy_grid_agent.py` shows the
canonical `build_runtime()`:

```python
registry = ModeRegistry()
registry.register(IdleMode)
return AgentRuntime(
    belief=Belief(), action_state=ActionState(),
    perceive=perceive, update_belief=update_belief, resolve_action=resolve_action,
    mode_registry=registry,
    default_directive=ModeDirective(mode="idle", source="default", reason="..."),
    strategy_runner=SynchronousStrategyRunner(RuleBasedStrategy()),
    trace_sink=trace_sink, metrics_sink=metrics_sink,
)
```
Then each tick: `command = runtime.step(observation)`; send `command`'s packets.

### Design rules (from `PYTHON_FRAMEWORK.md` — enforce these)

- Raw observations (and sprite pixels) stay out of belief; belief is the
  strategy interface.
- Mode params are typed (`ModeParams`); modes emit **symbolic intents**, never
  transport actions.
- Movement, cursor/button timing, chat buffers, UI mechanics → **action
  resolver**, not modes.
- Keep `snapshot.read()/write()` scopes short; **never hold the lock across an
  LLM/network call**.
- Use reflexes for urgent events that can't wait for the strategy loop.
- Use TTLs + default directives so the agent stays live when strategy stalls.
- Return `ModeDecision.complete(...)` / `.stalled(...)` when a mode finishes or
  is stuck.
- Validate directives in `ModeRegistry` before installing.
- Trace every boundary; emit metrics while developing.

### SDK files

| File | What |
| --- | --- |
| `player_sdk/runtime.py` | `AgentRuntime`, reflexes, fallbacks, the `step()` loop |
| `player_sdk/modes.py` | `Mode`, `ModeRegistry`, directive validation |
| `player_sdk/types.py` | directives, intents, commands, `SharedMemory`/`BeliefSnapshot` |
| `player_sdk/strategy.py` | `Strategy`/`AsyncStrategy` protocols + 4 runners |
| `player_sdk/trace.py` | trace & metrics sinks |
| `player_sdk/buffers.py` | `OverwriteBuffer` (latest-wins, thread-safe) |
| `player_sdk/coworld_json_bridge.py` | JSON-protocol bridge — **NOT for Crewrift** (see below) |
| `player_sdk/docs/metta_cogames_framework/README.md` | full framework reference + invariants/anti-patterns |
| `player_sdk/docs/metta_cogames_framework/PYTHON_FRAMEWORK.md` | quickstart + minimal agent |
| `player_sdk/docs/metta_cogames_framework/examples/toy_grid_agent.py` | complete runnable example — the assembly pattern to mirror |

### Transport: the part the SDK does *not* do for you

`coworld_json_bridge.py` speaks the **JSON `coworld.player.v1`** protocol —
observations are `(location, feature_id, value)` token triplets, the action is a
single `action_name` string, and it hosts a **mettagrid `MultiAgentPolicy`**.
That is for *grid/token* games (cogsguard-style), **not** for a pixel game.

Crewrift speaks **binary Sprite v1** (structured scene updates in, button/text
packets out — see [§2](#sprite-v1-protocol-structured-scene-not-a-framebuffer)).
So crewborg must **write its own websocket bridge** that:
1. reads `COGAMES_ENGINE_WS_URL` (the runner fills in `?slot=N&token=...`);
2. `websockets.connect(url, max_size=None)` — token validation is at HTTP
   upgrade, no app handshake;
3. decodes each incoming binary message and applies it to the **scene tables**
   (Layers / Sprites / Objects — see [§2](#sprite-v1-protocol-structured-scene-not-a-framebuffer)),
   then on each tick drives `runtime.step(scene)` and sends the resulting wire
   packets (a button packet, optionally a chat packet);
4. exits cleanly when the server closes the socket (= game end).

Each incoming binary message is a complete frame (the decoder applies all of its
concatenated sub-messages), so the bridge applies one message, runs one
`perceive→…→resolve` cycle, and sends input only if the mask changed. `notsus`'
`receiveLatestFrameInto` (`np:611`) additionally drains any already-queued
messages and acts only on the freshest — a latency optimization (skip stale
intermediate frames) that crewborg does not currently need, since it has no rate
limiter and self-corrects from any transient backlog.

---

## 2. Crewrift (the game)

Crewborg's contract. Two checkouts matter:

- **Game source (Nim):** `~/coding/games/coworld-crewrift` — the authoritative
  rules, protocol docs, reference bots, map assets. Read this for behavior.
- **Coworld platform docs:** `~/coding/metta/packages/coworld` *(inside the
  metta checkout — **read-only**, never write there)* — the generic Coworld
  packaging/runner/CLI contract Crewrift conforms to.

### Concept & objective (`coworld-crewrift/README.md`, `docs/rules.md`)

Social deduction, 8 players / 2 imposters default, retro pixel art.
- **Crewmates** win by completing all assigned tasks **or** voting out all
  imposters.
- **Imposters** win by killing crew down to parity, or surviving the vote.
- Meetings are triggered by reporting a body or the emergency button; players
  chat then vote (or skip); ties/timeouts eject no one.

### Loop, mechanics, scoring

> **Citation key** (all paths under `~/coding/games/coworld-crewrift/`, verified
> 2026-05-28). Game source: `sim` = `src/crewrift/sim.nim`, `global` =
> `src/crewrift/global.nim` (the `/player` renderer), `server` =
> `src/crewrift/server.nim`, `protocol` = `src/crewrift/common/protocol.nim`.
> Reference consumer: `notsus` = `players/notsus/notsus.nim`, `np` =
> `players/notsus/notsus/protocols.nim`. Cited as e.g. `sim:2464`.

Runs at **24 FPS** (`TargetFps`). Crewmates do timed tasks at stations; imposters
kill (range + cooldown), vent (teleport between grouped vents), and blend in.
Dead players become ghosts (a Crewmate ghost can still finish its own tasks; no
other actions). Phases: `Lobby → RoleReveal → Playing → (Voting → VoteResult)* →
GameOver`. **Inputs only do anything during `Playing` and `Voting`** — all other
phases ignore buttons.

Verified constants and rewards (`sim` const block; defaults, all config-overridable
via `defaultGameConfig` at `sim:1016`):

| Constant | Value | Cite | | Reward | Value | Cite |
| --- | --- | --- | --- | --- | --- | --- |
| `TargetFps` | 24 | `sim:49` | | task complete | +1 | `sim:95`, awarded `sim:2231` |
| `KillRange` | 20 px (dist² ≤ 400) | `sim:56` | | kill | +10 | `sim:96`, `sim:2502` |
| `KillCooldownTicks` | 900 (37.5 s) | `sim:57` | | win (each winner) | +100 | `sim:97` |
| `VentRange` | 16 px (dist² ≤ 256) | `sim:61` | | vote timeout (per non-voter) | −10 | `sim:98`, `sim:3082` |
| `ReportRange` | 20 px | `sim:65` | | stuck (idle crewmate) | −1 | `sim:99` |
| `TaskCompleteTicks` | 72 (3 s hold-A) | `sim:59` | | map size | 1235×659 | `sim:25-26` |
| `VoteTimerTicks` | 240 (10 s) | `sim:72` | | screen/camera | 128×128 | `sim:100-109` |
| `TasksPerPlayer` | 8 | `sim:77` | | | | |
| `ButtonCalls` | 1 per player | `sim:79` | | | | |

Win: crew win if all imposters dead **or** all tasks done; imposters win when alive
imposters ≥ alive crewmates (`checkWinCondition`, `sim:3248`). Imposter count
auto-scales `(players−3)//2` by default. Movement is **momentum-based**
(`Accel=76`, friction `144/256`, `MaxSpeed=704`, sub-pixel `MotionScale=256`;
`sim:42-47`, applied in `applyInput` `sim:2768-2830`) — the action resolver owns
that, not modes. Endpoint path is `/player` (`sim:177`).

### Sprite v1 protocol: structured scene, not a framebuffer (`coworld-crewrift/docs/sprite_v1.md` — READ THIS)

This is the most important thing to get right. **Sprite v1 sends structured
scene data, and Crewrift uses it specifically so agents do *not* need computer
vision.** Don't parse pixels to recover game state — the state is already
structured and labelled in the messages.

Binary WebSocket. The client maintains **three tables** as messages arrive:

| Table | Keyed by | Holds |
| --- | --- | --- |
| **Layers** | `u8` layer id | type (map / one of 8 UI anchors) + flags + viewport size |
| **Sprites** | `u16` sprite id | width, height, **text label**, RGBA pixels |
| **Objects** | `u16` object id | **x, y** (`i16`; *camera-relative* on `/player` — see below), z (draw order), layer, **sprite id** |

**Server → client** messages: define-sprite (`0x01`, id + w/h + snappy RGBA +
label), define-object (`0x02`, id + x/y/z + layer + sprite_id), delete-object
(`0x03`), clear-objects (`0x04`, keeps sprite defs), set-viewport (`0x05`),
define-layer (`0x06`). These are **incremental and stateful** — there is no
"frame" message; you apply each update to the tables and read the tables. Wire
format is in `docs/sprite_v1.md`; a clean reference decoder (the exact byte
layout, little-endian, `i16` coords) is `notsus`' `applySpritePacket` at
`np:408-523`.

The `/player` stream — facts below verified against `global` (the renderer,
`buildSpriteProtocolPlayerUpdates` `global:2276`, init `buildSpriteProtocolPlayerInit`
`global:1720`), `server` (send loop), and the `notsus` parser (`np`):

**Stream shape.** First message is an **init burst**: Clear-objects (`0x04`),
one Define-layer (`0x06`) for **layer 0** (map, type `0x00`), one Set-viewport
(`0x05`) of **128×128**, then Define-sprite (`0x01`) for all *static* sprites
(`map`, `walkability map`, the per-color player/ghost/body sprites, task/HUD
icons) — `global:1720-1855`. After that, **one binary message per game tick**
carrying only changes: Define-object (`0x02`) for everything currently visible
(re-sent each tick) and Delete-object (`0x03`) for anything that left (the
diff-against-last-frame loop, `global:2533`). Init is sent once, gated on
`nextState.initialized` (`global:2289`). **Everything is on layer 0** for
`/player` — no separate UI-anchor layers; HUD/voting elements are objects at
fixed screen coordinates. Treat one received binary message = one atomic frame.

**Coordinates are camera-relative, NOT world space.** This is the single most
important implementation fact and the protocol doc does *not* spell it out for
`/player`. The camera is centered on the local player (`playerView`, `sim:2879`;
camera applied at `global:2323`); an object's `x/y` is `world − camera`, in the
0–128 screen range.
- **You recover the camera from the map object:** the world-map object has
  **object id 1 and sprite id 1** (`MapObjectId`/`MapSpriteId`, `np:14-15`),
  placed at `(−cameraX, −cameraY)` (`global:2331-2333`). So `cameraX =
  −mapObject.x`, `worldX = obj.x + cameraX` — exactly what `notsus` does at
  `np:496-499`.
- **Your own player is *not* an object** — it's the implicit camera center. Your
  world position ≈ `camera + (SpriteDrawOffX + 64 − SpriteSize/2, …)` (the fixed
  center offset, `notsus:22`, applied `notsus:486-492`; `SpriteDrawOffX=2`,
  `SpriteDrawOffY=8` at `sim:38-40`). Read your own *color/role/state* from HUD
  sprites (see labels below), not from a self object.

**Identity by sprite label + object-id range** (no CV). An object references a
sprite; that sprite's **label** tells you what it is. Verified label vocabulary
(all in the `/player` init at `global`):
- Players: `player <color> right` / `player <color> left` (`global:1807,1815`);
  `ghost <color> right`/`left` (`global:1835,1843`); dead bodies `body <color>`
  (`global:1855`). `<color>` ∈ 16 names (`PlayerColorNames`, `global:109-126`):
  `red, orange, yellow, light blue, pink, lime, blue, pale blue, gray, white,
  dark brown, brown, dark teal, green, dark navy, black`.
- World/HUD: `map` (`global:1736`), `walkability map` (`global:1744`),
  `task bubble` (`global:1752`), `task arrow` (off-screen task pointer,
  `global:1784`), `imposter icon` / `imposter icon cooldown` (⇒ *you* are
  imposter, kill ready / cooling, `global:1760,1768`), `ghost icon` (⇒ *you* are
  dead, `global:1776`), `progress bar N%` (`global:2464`), `task counter N`
  (`global:2519`), `shadow` (screen-sized per-player vision overlay, object `13000`
  / sprite `5010`; decoded into a line-of-sight mask — opaque ⇒ occluded,
  transparent ⇒ visible — resent on any camera move, `global:2212`, `sim:2974`).
- Voting: `vote cursor` (`global:1521`), `vote skip cursor` (`global:1529`),
  `vote self marker <color>` (`global:1538`), `vote dot <color>` (tally,
  `global:1546`), `vote timer` (`global:1252`).
- Social UI (meeting/result screens, decoded into chat + census + ejection;
  `global:739-1280`): chat is a text sprite (label = the raw message, id `9000+`)
  paired by screen-y to a speaker icon (`player <color>`, id `9200+`); the voting
  candidate grid (`9300+seq`) shows each player as `player <color>` (alive) or
  `body <color>` (dead) — an authoritative alive/dead census by color; the
  vote-result icon (`9600`, `player <color>`) names the ejected player.
- Phase/result **text** (read game phase from which appear; `interstitialTextItems`,
  `global:957-1044`): `WAITING`/`NEED MORE!` (`global:966-967`), `STARTING`
  (`global:970`), `IMPS`/`CREWMATE` (role reveal, `global:992`), `SKIP`
  (`global:1009`), `NO ONE`/`WAS KILLED` (vote result, `global:1014,1017`),
  `DRAW`/`CREW WINS`/`IMPS WIN` (game over, `global:1021-1025`).
- **Object ids are stable per entity across ticks** (track by id): players
  `1000 + joinOrder` (`spriteObjectId`, `global:1858`), bodies `2000 + i`
  (`spriteBodyObjectId`, `global:2069` — note `i` is the bodies-seq index, **not**
  the dead player's joinOrder, so a body links to its player by **color**, not id),
  task bubbles `3000 + taskIndex` (`spriteTaskObjectId`, `global:2073`), map `1`,
  vote dots `10100 + target*MaxPlayers + voter` (base `notsus:167`, decoded
  `notsus:1827`).

#### Contract delta — upstream changes of 2026-06-10 (verified against game source)

Six upstream commits (`5d00d84`..`bc1fb99`) changed the `/player` contract; the
facts below were verified against the game source at those commits and are
implemented in crewborg's perception/belief (`perception/`, `types.py`):

- **Edge culling fixed** (`5d00d84` "fix player edge culling"): players are now
  culled by **sprite-rect overlap** with the 128×128 frame
  (`playerActorInFrame`), not by their collision center being on-screen, and the
  LoS check uses a **clamped in-frame point** (`playerActorVisibilityPoint`).
  Consequence: player objects can arrive with screen coords slightly outside
  0..127 (down to −(CrewSpriteSize+2)); a player straddling the screen edge is
  now streamed where it previously vanished. No crewborg change needed (we never
  filtered by screen bounds) — but don't add such a filter.
- **Server tick marker** (`4f89e79` "add tick log marker"): every per-tick
  player frame carries an invisible 1×1 sprite, **sprite id 5016 / object id
  5016**, label **`tick <N>`** with `N = sim.tickCount` — the same counter the
  `.bitreplay` timeline uses. The sprite is *redefined* each tick (label
  changes). Crewborg folds it into `belief.server_tick` and records it per tick
  in the artifact's `positions` table (the trace↔replay join key).
- **Random default seed** (`fe49010`): config `seed` default changed from a
  fixed `0xA6019`/`679961` to **−1 = time-based** (`resolveRandomSeed`,
  `timeGameSeed`); explicit seeds and replays still reproduce. Role assignment
  is therefore **no longer correlated with player color** across hosted
  episodes — never bake color→role priors (crewborg has none).
- **GameInfo interstitial** (`61b6c3c`): a new phase **`Lobby → GameInfo →
  RoleReveal → …`** (default `gameInfoTicks` = 72 = 3 s, config-overridable)
  shows text sprites (9000-range): `GAME INFO`, `KILL COOLDOWN <N>T`,
  `TASKS <N> EACH`, `VOTE TIMER <N>T`, `GAME TIMER <N>T` / `GAME TIMER NONE` —
  i.e. the **live episode config**. Crewborg learns
  `kill_cooldown_config_ticks` / `tasks_per_player` / `vote_timer_ticks` /
  `game_max_ticks` from it.
- **MeetingCall interstitial** (`4b9297d`): `startVote` now enters a new
  **`MeetingCall`** phase (const `MeetingCallTicks` = 72 = 3 s) before
  `Voting`, exposing **who opened the meeting and how** (previously
  unobservable): object **9800** = caller's `player <color> right` icon, object
  **9801** = reported `body <color>` sprite (report) or the new
  **`meeting button`** sprite (sprite id 5017; button call). Text lines:
  `<Color> reported` + `<Color>'s body`/`a body`, `<Color> pressed` +
  `the button`, `<Color> called` + `a meeting` (`Someone` when the caller
  left). Inputs are ignored during MeetingCall. Crewborg latches
  `meeting_called_by` / `meeting_trigger` / `meeting_reported_body_color` and
  emits `domain.meeting_called`.
- **Game clock paused in meetings** (`bc1fb99` "pause timer"): a new
  `gameTickCount` advances **only during Playing**, and `maxTicks` now counts
  task-phase ticks (MeetingCall/Voting/VoteResult no longer burn game clock).
  Kill/vent cooldowns also only decrement during Playing, and every meeting
  still resets imposter kill cooldowns. Defaults moved: **`VoteTimerTicks`
  240 → 1200** (10 s → 50 s) and **`KillCooldownTicks` is 500** (the §2 table's
  900 is stale). Crewborg prefers the GameInfo-advertised values and keeps
  conservative fallbacks (240 / 500) for older servers.
- Also new on the GameOver screen: per-player roster icons (objects **9700+i**,
  `player <color>`) row-paired with **`IMP`/`CREW`** text items — an
  end-of-game ground-truth **role census by color** (struck-through pixels mark
  the dead; the label does not change). The no-ejection vote result now reads
  `NO ONE` + `DIED`.

**Vents, rooms, and the emergency button are NOT objects** — they're baked into
the `map` sprite's pixels (`buildMapSpritePixels`, `global:701`). crewborg can't
read their positions from the stream; get them from the static map /
`walkability map` mask or out-of-band map data.

**Walkability** = the alpha channel of the one sprite labeled `walkability map`
(alpha > 0 ⇒ walkable), sized to the full 1235×659 map. Built server-side at
`buildWalkabilitySpritePixels` `global:709`; decoded by `notsus` at `np:389-406`
(alpha-only mask). Snappy-decompress it once; `nav.py` builds the pixel-validated
nav graph over it (§6). The only other sprite whose pixels crewborg decodes is the
dynamic `shadow` line-of-sight overlay (above); ignore every other sprite's pixels.

**Client → server (input).** Player input is `0x84` + one **byte** bitmask
(`[0x84, mask & 0x7f]`; encoder `np:194`, server masks bit 7 at `global:501`,
decodes via `decodeInputMask` at `server:850`). Bits (`protocol:14-20`):
up/down/left/right = `0x01/0x02/0x04/0x08`, Select `0x10` (unused by the game), A
`0x20`, B `0x40`, bit 7 reserved = 0. **Send only when the held mask changes**;
omitted bits = released. Meeting chat is `0x81` + `u16` len + printable ASCII
(accumulated at `global:485`), accepted **only during Voting** (routed
`server:866` → `addVotingChat` which early-returns off-phase, `sim:2586`). Mouse
(`0x82`/`0x83`) is *not* read by Crewrift's logic — don't rely on it. Verified
game semantics (handler `applyInput`, `sim:2751`):
- **A (`0x20`) is edge-triggered** (`freshA`, `sim:2837`; press→release→press to
  repeat). On a fresh press during `Playing`, in order: try **report** a body in
  range (`sim:2839`, `tryReport` `sim:2614`) → try the **emergency button**
  (`sim:2842`, `tryCallButton` `sim:2634`, ≤1 call/player) → if imposter, try
  **kill** (`sim:2846-2847`, `tryKill` `sim:2464`, nearest crewmate in
  `KillRange`, off cooldown). Report/button open a meeting and short-circuit, so
  you can't kill in the same press that calls one.
- **Task completion = hold A while standing still** inside an assigned,
  incomplete task rect for `TaskCompleteTicks`; any d-pad input resets progress
  (`sim:2866-2877`). Crewmates only.
- **B (`0x40`) = vent**, imposter only, held/level-triggered (`sim:2834`,
  `tryVent` `sim:2506`), gated by `VentRange` + a 30-tick vent cooldown
  (`sim:2547`); teleports to the next vent in the group.
- **Voting** (the `step` Voting branch, `sim:3834-3867`): move a cursor with the
  d-pad (up **or** left = −1, down **or** right = +1; `moveCursor` calls at
  `sim:3851,3853`, proc `sim:3120`), then press **A to confirm** (`sim:3854`).
  The **skip** choice is the last cell (index `== player count` ⇒ vote `−2`,
  `sim:3857`); a player vote stores the cursor index (`sim:3859`). No mouse, no
  text-vote; `0x81` is chat only. A vote is final once cast; not voting before
  the timer ⇒ −10.

**Debug sprites (client → server, opt-in).** Engine PR #67 added a debug-sprite
channel: `0x86` + `u32` length + an inner server→client–format sprite packet
(`blobFromSpriteDebugSprites`). The engine records it in the `.bitreplay` and
renders the player's sprites/objects over that player's POV when a replay viewer
toggles "D". Crewborg encodes these in
`players/crewrift/crewborg/debug_overlay.py` (the decoder's mirror image; pixels
use **framed** `cramjam.snappy.compress`, *not* the raw-block `compress_raw` the
incoming masks use) and emits a per-tick plan overlay — route waypoints, goal,
self position — only when **`CREWBORG_DEBUG_SPRITES`** is truthy
(`1`/`true`/`yes`/`on`). Default OFF; the send is deduped and wrapped so it can
never fail an episode. It complements the SQLite trace artifact (offline,
queryable) with an in-replay visualization (where the plan pointed, per frame).

So **crewborg's perception is structured-scene maintenance**, not vision: keep
the object/sprite tables current, derive the camera from object 1, resolve each
object to `(label, world-x, world-y)`, and fold that into belief (self
pos/role/state, other players, bodies, task icons, voting state, phase). The only
image steps are two sprite alpha masks: the static `walkability map` and the
dynamic `shadow` line-of-sight overlay. This is dramatically simpler than a
pixel-CV stack — crewborg's perception is **structured-scene maintenance**, not
computer vision; there is no framebuffer parser and no pixel atlas (the only image
steps are Snappy-decoding those two alpha masks).

**Step cadence (a crewborg design choice, grounded in the server).** The server
emits exactly one binary message per socket per 24 Hz tick (one `sim.step` per
loop, send loop, rate-limited by `runFrameLimiter` `server:578`). There is no
"frame complete" sub-message marker, but each WebSocket message is itself one
complete frame: the decoder applies all of its concatenated sub-messages. So the
bridge blocks for one message, applies it, runs one `perceive→…→resolve` cycle,
and sends input *only if the mask changed*. `notsus`' `receiveLatestFrameInto`
(`np:611`) also drains any already-queued messages to act on the freshest frame —
a latency optimization crewborg skips (it has no rate limiter and self-corrects
from transient backlog). Tolerate partial startup frames — don't assume the map
object / walkability sprite have arrived yet on the first ticks.

### Connecting / running locally (`coworld-crewrift/README.md`)

Hosted play sets `COGAMES_ENGINE_WS_URL=ws://<svc>:8080/player?slot=<s>&token=<t>`
— connect to it **exactly**; don't hardcode slot/token. For local dev, run the
Nim server then point a bot at it:
```sh
# server (1 player, no imposters, 1 task — smallest smoke):
nim r src/crewrift.nim --address:0.0.0.0 --port:2000 \
  --config:'{"minPlayers":1,"imposterCount":0,"tasksPerPlayer":1}'
# a reference bot in another shell:
COGAMES_ENGINE_WS_URL='ws://localhost:2000/player?slot=0&token=' \
  nim r players/nottoodumb/nottoodumb.nim -- --name nottoodumb --slot 0
```
Browser clients: `/client/player`, `/client/global`, `/client/replay`,
`/client/admin`. Reference bots live in `coworld-crewrift/players/` (`notsus`,
`evidencebot_v2`, `nottoodumb`) with guides `how_to_make_a_bot.md`,
`SMART_BOT_GUIDE.md`, `how_to_submit_coworld_policy.md`.

**Watching a `.bitreplay` locally:** do **not** use `coworld replay` — it's broken
for the Crewrift image (it relies on `COGAME_REPLAY_SERVER` + a client `?uri=`,
neither of which the game honors, so it shows a live "waiting for players" game).
Launch the game image directly with `COGAME_LOAD_REPLAY_URI` instead. Full,
source-verified recipe: [`docs/crewrift-replays.md`](./docs/crewrift-replays.md).

### Packaging & submission (the Coworld path)

Crewborg ships as a Linux/amd64 Docker image; **stdout = protocol channel,
stderr = logs/traces**. Trace/metric logging defaults to the **episode debug
artifact** rather than stderr: `artifact.py` records the full unfiltered stream
into in-memory SQLite and, at episode end, the bridge zips `trace.db` +
`summary.json` and PUTs them to the runner-injected
`COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` (presigned `https://`, or `file://` on
local runs; absent ⇒ skip; failure logged, never fatal — see README §"Logging &
the episode artifact"). Stderr JSON streaming is opt-in via the
`CREWBORG_TRACE*` envs. Upload/submit with the `coworld` CLI:
```sh
docker buildx build --platform linux/amd64 -t crewborg:latest --load .
coworld upload-policy crewborg:latest --name crewborg
coworld submit crewborg:v1 --league <crewrift-league-id>
```
Local episode/iteration uses `coworld run-episode` / `coworld play` against the
game's `coworld_manifest.json` — but `run-episode` needs two workarounds against
crewrift 0.1.23 + coworld 0.1.13 (verified 2026-06-02): (1) the manifest's
`config_schema` trips a legacy-schema validator — re-download fresh and delete
`slots.items.properties.name`; (2) pass `--run /srv/players/players/crewrift/crewborg/coworld/entrypoint.sh`,
else it reuses the manifest's reference-player command `/bin/notsus` (absent in
our image) and the game hangs at `waiting for players: 0/8`. To exercise
kills/meetings/voting, patch `certification.game_config = variants[0].game_config`
(10k ticks, 8 tasks). Full platform contract:
`coworld/src/coworld/docs/README.md` (platform overview + role/artifact docs
under `docs/roles/` and `docs/artifacts/`) and `runner/runner.py` (protocol
authority). *(The flat `COWORLD_README.md`/`GAME_RUNTIME_README.md` were
reorganized into `docs/` as of coworld 0.1.13.)*

**Retrieving hosted episodes crewborg played.** The Observatory API records
every league episode. **Use [`scripts/fetch_episodes.py`](./scripts/fetch_episodes.py)**
for a crewborg-filtered bulk pull of replays + per-slot traces + metadata: it
reads raw JSON against the current routes, so it survives the client/server drift
that periodically breaks the typed CLI. That drift is biting now — as of
2026-06-02 the official `coworld episodes` / `coworld replays` / `coworld
episode-logs` commands are **broken** even on the latest CLI (0.1.13): the server
renamed `/v2/episode-requests*` → `/v2/experience-request*` and the CLI still
calls the old paths (404). (Earlier instance: coworld 0.1.11's
`V2EpisodeRequestRow.assignments` `ValidationError`.) When a route 404s, the live
map is at `<api>/observatory/openapi.json`. The API is reached via the official
gateway `<softmax-api-server>/observatory` (the `coworld` CLI's route) or directly
at `https://api.observatory.softmax-research.net` with routes at the host root.

---

## 3. Layout and references

The durable architecture and the full set of design decisions live in
[`design.md`](./design.md) — read it before writing code. It owns the package
layout (`__init__.py`/`types.py`/`action.py`/`nav.py`/`modes/`/`strategy/`/
`perception/`/`map/`/`coworld/`/`tests/`), the type contracts, and the
mode/intent/strategy design. The meeting LLM implementation lives under
`strategy/meeting/` and supports two backends: the direct Anthropic API
(`CREWBORG_LLM_MEETINGS=1` + `ANTHROPIC_API_KEY`) and AWS Bedrock (any of
`USE_BEDROCK=1` / `CREWBORG_USE_BEDROCK=1` / `CLAUDE_CODE_USE_BEDROCK=1`, with AWS
credentials from the environment). A Bedrock flag implies the meetings flag. On
the hosted runner, Bedrock is enabled at upload time with
`coworld upload-policy ... --use-bedrock`. See [`README.md`](./README.md)
§"LLM meetings" for the full env-var and upload-time reference.

Perception is **structured-scene maintenance**, not computer vision: there is no
framebuffer parser, no pixel atlas, and no CV parity oracle. The only image steps
are Snappy-decoding two sprite alpha masks: the `walkability map` (used only to
*validate* a baked map — vent/button/task locations are not in the stream) and the
dynamic `shadow` line-of-sight overlay (real per-point visibility); see
[§2](#sprite-v1-protocol-structured-scene-not-a-framebuffer) and `design.md` §3.

Behavior & parsing references:
- **Crewrift's `notsus` / `evidencebot_v2` Nim bots** are the behavior references;
  `notsus`' Sprite-v1 *parsing* code (`players/notsus/notsus/protocols.nim`) — how
  it maintains the scene tables, recovers the camera, and interprets labels — is
  the perception reference (label path only; ignore its legacy pixel-CV path).
- **`players/cogsguard/{baseline,buggy,cranky}`** — JSON/`coworld_json_bridge`
  players (token-grid game). **Not** crewborg's transport, but a good
  Dockerfile/`build.sh` example.

---

## 4. Build & test (this workspace)

```sh
# repo root: ~/coding/players_checkouts/players
uv sync
uv run pytest players/crewrift/crewborg/tests      # crewborg tests
uv run ruff check players/crewrift/crewborg
```
Tests use `pytest-asyncio` (strict mode) and a `docker` marker for image-driven
tests (root `pyproject.toml`). Cover: action resolver, modes, trace sinks,
assembled runtime, and an in-process bridge smoke. For perception,
test the **scene decoder** against recorded Sprite-v1 message sequences (decode a
captured stream → assert the resolved objects/labels/coordinates), rather than
pixel parity.

---

## Quick file index

| Need | Path |
| --- | --- |
| SDK runtime / `step()` | `players/player_sdk/runtime.py` |
| SDK mode base + registry | `players/player_sdk/modes.py` |
| SDK directive/intent/command/belief types | `players/player_sdk/types.py` |
| SDK strategy runners | `players/player_sdk/strategy.py` |
| SDK framework reference (invariants) | `players/player_sdk/docs/metta_cogames_framework/README.md` |
| SDK minimal example to mirror | `players/player_sdk/docs/metta_cogames_framework/examples/toy_grid_agent.py` |
| Crewborg design decisions | `players/crewrift/crewborg/design.md` |
| Crewrift Sprite-v1 parser (perception reference) | `~/coding/games/coworld-crewrift/players/notsus/notsus/protocols.nim` |
| Crewrift rules / mechanics | `~/coding/games/coworld-crewrift/README.md`, `docs/rules.md`, `src/crewrift/sim.nim` |
| Crewrift wire protocol | `~/coding/games/coworld-crewrift/docs/sprite_v1.md` |
| Crewrift reference bots + guides | `~/coding/games/coworld-crewrift/players/` |
| Coworld platform/runner contract | `~/coding/metta/packages/coworld/src/coworld/docs/README.md` + `runner/runner.py` *(read-only)* |
| Fetch hosted episodes crewborg played | `players/crewrift/crewborg/scripts/fetch_episodes.py` (the typed `coworld episodes`/`replays`/`episode-logs` are 404-broken since the server's `episode-requests`→`experience-request` rename) |
| View crewborg trace replays | `players/crewrift/crewborg/viewer/index.html` (load logs captured with `CREWBORG_TRACE=viewer` or `CREWBORG_TRACE=debug`) |

Absolute roots:
- Player SDK & this workspace: `~/coding/players_checkouts/players` (pkg `players`)
- Crewrift game source: `~/coding/games/coworld-crewrift`
- Coworld platform: `~/coding/metta/packages/coworld` *(read-only — metta checkout)*

## Source-of-truth & caveats

The protocol/mechanics in §2 were read from `coworld-crewrift` source on
2026-05-28 (`global.nim`/`server.nim` for the `/player` render, `sim.nim` for
mechanics/constants, `players/notsus/notsus/{protocols,votereader}.nim` for the
proven consumer). Crewrift is in active development, so when something doesn't
match, re-derive from those files (and a live `coworld-crewrift` capture) — they
win over this doc. Specifics worth re-confirming against the live game:

- The **label strings** and **object-id bases** are the perception contract and
  are game-defined (not in `sprite_v1.md`). They're current as of the date above;
  re-check `global.nim` if perception misbehaves.
- `notsus` is a **dual-path** bot: a legacy pixel-CV path *and* the label path.
  Over Sprite v1 the **label path is authoritative** (`spriteDetectionsReady`);
  mirror that and **ignore** its CV/OCR/patch-hash machinery (`votereader.nim`,
  `scoreCamera`, crewmate sprite matching) — it's for the older bitstream wire.
- Constants in the §2 table are defaults; a league variant may override them via
  game config. Read the actual episode config when it matters.
