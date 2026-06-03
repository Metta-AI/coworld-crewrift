# Scribe Re-Simulation & Event Capture — Implementation Plan

Audience: a coding agent implementing the next stage of the Crewrift reporter
(`scribe`). Read `reporters/README.md` first for what already exists.

## Goal

Re-simulate a decoded replay tick-by-tick and produce a precise, **tick-aligned
event timeline** for one episode: game start + role assignments, kills/deaths,
task completions, meetings called (with caller and reason), individual vote
casts, vote results / ejections, voting chat messages, stuck penalties, vents,
and game over / win.

Each event must carry the exact `tick` it occurred on and resolve to stable
player identities (slot, name, color, role).

## Hard constraints

- **All code lives under `reporters/`.** Do **not** modify `src/crewrift/*`,
  `players/*`, or anything outside `reporters/`. The game simulator and replay
  codec are consumed read-only, through their exported (`*`) APIs only.
- **Reuse the game's own logic; do not reimplement game rules.** Where an event
  needs logic that lives inside the sim, call the exported sim procs against a
  cloned `SimServer` when that clone can exactly match the real tick state.
  Body-report attribution uses `tryReport`; kill attribution uses the sim's
  ordered side effects (cooldown reset + appended bodies). Do not call `tryKill`
  from a state that predates the killer's movement/cooldown updates for the
  tick. The one unavoidable exception is replay input application (see Driver
  below); reimplement it faithfully and minimally.
- **Chat is in scope.** The replay codec records voting chat. The re-sim driver
  must apply chat records with `addVotingChat` so hash validation stays aligned,
  and `scribe` should also emit those records as tick-aligned chat events. Chat
  text comes from the replay record; do not try to infer or reconstruct chat
  from sim state.

## What already exists (starting point)

- `reporters/scribe/scribe/report.nim` — `decodeReplayBytes` returns an
  `EpisodeReport { config: GameConfig, replay: ReplayData }`.
- `reporters/scribe/scribe.nim` — local CLI entry point that prints a timeline.
- `reporters/scribe/service.nim` — persistent websocket service entry point.

This plan adds the event-extraction layer on top of `decodeReplayBytes`.

---

## Background: how re-simulation works and why it's sound

The replay is a game-owned byte stream of **inputs**, not state. To recover what
happened you replay the inputs through the real simulator:

- `initSimServer(config)` (`src/crewrift/sim.nim:3740`) builds a fresh sim from
  the `GameConfig` recovered from the replay header.
- The replay codec (`src/crewrift/replays.nim`) drives the sim. `stepReplay`
  (`replays.nim:165`) applies that tick's joins/leaves/inputs and calls
  `sim.step(inputs, prevInputs)` once (`src/crewrift/sim.nim:3851`).
- Determinism is guaranteed: `gameHash()` (`sim.nim:1551`) hashes essentially
  the entire `SimServer`/`Player` state every tick, and the replay carries those
  hashes (`ReplayData.hashes`). If our re-sim's `gameHash()` matches the recorded
  hash each tick, our reconstructed state is provably identical to the original
  run. **Use this as a built-in correctness check.**

So after each tick we hold the full, authoritative `SimServer` state. Almost all
events are recovered by **diffing the state before vs. after the tick**. Body
reports need an actor/body pair that the sim decides transiently inside `step`
and does not persist; recover those with a `tryReport` probe (below).
Simultaneous kills are paired by simulator processing order and body append
order, not by replaying `tryKill` from a stale global pre-step state.

### Detection strategy summary

- **Diff** (compare pre-tick vs post-tick state): deaths/victims, task
  completions, vote casts, ejections, role assignments, phase transitions,
  button-meeting caller, game over/win, stuck penalties, vents.
- **Replay records** (already timestamped by the codec): voting chat messages
  are emitted from `ReplayData.chats` as they are applied to the sim.
- **Probe** (clone sim, call the real exported proc): body-report meeting caller
  and reported body, using the same body-limit the real tick used.
- **Order-based attribution:** when multiple kills land on one tick, sort the
  successful killers by live sim index and pair them with the newly appended
  bodies in order. The sim processes players in index order and `tryKill`
  appends one body immediately when that player's kill succeeds.
- **Reconstruct once, reused by probes:** per-tick input masks (fresh-attack
  detection), because the driver needs them anyway.

### Tick-order facts the detector relies on

1. The Playing-phase loop in `step` does **not** break when a meeting starts
   mid-loop (`sim.nim:3928-3945`): every player's movement for that tick is still
   applied, and `applyInput` only skips the *post-movement* kill/task/button work
   after `phase` becomes `Voting` (`sim.nim:2887-2895`). Therefore the
   **post-step snapshot holds correct tick-T positions for all players**, and the
   reported body is still in `sim.bodies` (bodies are cleared only later, in
   `applyVoteResult`). This is what makes the post-step `tryReport` probe valid.
2. `tryReport` receives `bodiesBeforeTick`, captured before the Playing-phase
   player loop (`sim.nim:3927`). A body created earlier in the same tick is not
   reportable until a later tick. Report probes must therefore use the pre-step
   body count as their `bodyLimit`, not `postStep.bodies.len`.
3. Within one player's `applyInput`, movement is applied first, `b` venting runs
   before `attack`, and a fresh `attack` runs report/button checks before
   `tryKill` (`sim.nim:2868-2898`). A global pre-step clone is not the right
   state for kill probing: the real killer may have moved into range, vented, or
   had cooldown decrement from 1 to 0 earlier in the same tick.
4. Killer-side effects of a kill **do** persist: `tryKill` (`sim.nim:2541-2555`)
   resets the killer's `killCooldown` to max, bumps the killer's `reward`, and
   increments the killer's reward-account `kills`. Successful killers are
   diffable by reward-account `kills` increments; the cooldown reset is a useful
   cross-check when `killCooldownTicks > 0`. Victims are the newly appended
   `Body` entries.

---

## Key sim types and fields (read-only references)

`SimServer` (`sim.nim:343-382`) — relevant fields:
`players: seq[Player]`, `bodies: seq[Body]`, `phase: GamePhase`,
`voteState: VoteState`, `tickCount`, `gameStartTick`, `roleRevealTimer`,
`gameOverTimer`, `winner: PlayerRole`, `timeLimitReached`, `tasks: seq[TaskStation]`,
`rewardAccounts: seq[RewardAccount]`, `config: GameConfig`, `gameMap: CrewriftMap`.

`Player` (`sim.nim:308-328`):
`x, y`, `role: PlayerRole`, `alive: bool`, `color: uint8`, `killCooldown`,
`ventCooldown`, `buttonCallsUsed`, `taskProgress`, `activeTask`, `tasksRewarded`,
`assignedTasks: seq[int]`, `joinOrder`, `address: string`, `reward`, `lastMoveTick`.

`VoteState` (`sim.nim:194-199`): `votes: seq[int]` (−1 not voted, −2 skip, ≥0
target index), `cursor: seq[int]`, `voteTimer`, `resultTimer`,
`ejectedPlayer: int` (−1 = tie/none). **No caller, kind, or reported-body field.**

`Body` (`sim.nim:235-238`): `x, y`, `color: uint8`, `slotId: int` (victim's
`joinOrder`).

`TaskStation` (`sim.nim:201-205`): `name`, `x, y, w, h`, `completed: seq[bool]`
(indexed by player index).

`GamePhase` (`sim.nim:181-187`): `Lobby, Playing, Voting, VoteResult, GameOver,
RoleReveal`.

`PlayerRole`: `Crewmate, Imposter`. `VoteCallKind` (`sim.nim:189-192`):
`VoteCalledUnknown, VoteCalledButton, VoteCalledBody`.

`InputState` (`bitworld/spriteprotocol.nim:43`):
`up, down, left, right, select, attack, b: bool`.
`decodeInputMask(mask: uint8): InputState` (`spriteprotocol.nim:125`).

`ReplayData` (`bitworld/replays.nim:52`): `configJson`, `joins: seq[ReplayJoin]`,
`leaves: seq[ReplayLeave]`, `inputs: seq[ReplayInput]`, `hashes: seq[ReplayHash]`.
`ReplayInput {time: uint32, player: uint8, keys: uint8}`.
`ReplayJoin {time, player, name, slot, token, address}`.
`tickTime(tick, fps)` converts tick→ms (`replays.nim:74`); crewrift uses
`ReplayFps = 24` (`sim.nim:11`). `ReplayData` input/join/leave times are in ms.

Exported procs the implementation will call:
`initSimServer` (`sim.nim:3740`), `step` (`sim.nim:3851`),
`addPlayer` (`sim.nim:2020`), `removePlayerAt` (`sim.nim:1993`),
`gameHash` (`sim.nim:1551`), `tryReport` (`sim.nim:2665`),
`decodeInputMask`, plus `parseReplayBytes` / `tickTime` re-exported via
`src/crewrift/replays.nim`.

Note: `rewardAccountForPlayer` is **not** exported — do not call it. If killer
attribution needs reward-account counters, implement a read-only lookup over
`sim.rewardAccounts` by `slotIndex == player.joinOrder` (or address as a
fallback) and compare the exported `kills` field across `preStep` and `postStep`.
Use `killCooldown` reset only as a cross-check; it is ambiguous when
`killCooldownTicks == 0`.

---

## Module layout

All new files under `reporters/scribe/scribe/`:

| File | Responsibility |
| --- | --- |
| `driver.nim` | `ReplayDriver`: owns the `SimServer` and the per-tick input masks; advances one tick; returns a `ReplayStep` with `tick`, `inputs`, `prevInputs`, `preStep`, `postStep`, `bodyLimit`, replay leaves, and hash validation state. |
| `identity.nim` | Build a stable player-identity table from replay joins + post-start sim state; provide joinOrder/slot/color lookups and per-tick live-index resolution. |
| `events.nim` | `GameEvent` variant type, `GameEventKind`, and the `EpisodeTimeline` result object. |
| `probes.nim` | `findBodyReport` via cloned `SimServer` + exported `tryReport`, using the real tick's `bodyLimit`. |
| `detect.nim` | Pure per-tick detection: takes pre/post sim + inputs + identity, returns `seq[GameEvent]`. Calls into `probes.nim`. |
| `timeline.nim` | Orchestration: `extractTimeline(EpisodeReport): EpisodeTimeline` — runs the driver to the end, calls `detect` each tick, accumulates events. |
| `event_log.nim` | Converts `EpisodeTimeline` events to Coworld event-log rows with `ts,player,key,value`. |
| `csv.nim` | Renders event-log rows as CSV for the websocket service's debug/compat mode. |
| `parquet.nim` | Renders the fixed Coworld event-log schema as Parquet for the default websocket response. |
| `protocol.nim` | Parses `/report` websocket requests and builds response envelopes. |
| `uri_io.nim` | Reads `file://` and `https://` replay URIs. |

`report.nim` gains nothing structural; `timeline.nim` consumes its
`EpisodeReport`. `scribe.nim` prints the timeline for local debugging; the
service entry point wakes on websocket requests and returns event logs as
Parquet by default, with CSV available by explicit request.

---

## Implementation steps

### Step 1 — `driver.nim`: own the re-sim and the inputs

Why not just call `stepReplay`? Because its replay-event and input helpers are
private to `src/crewrift/replays.nim`, while the detection and probe layers need
the per-tick `inputs`/`prevInputs`, the pre-step body count, and replay
leaves/chat in hand. Keep this driver as a tiny mirror of `stepReplay` and
hash-check every recorded tick so drift is caught immediately. If the game later
exports a helper that returns this same per-tick data, use it instead of
maintaining the mirror.

`ReplayDriver` holds:

```nim
type
  ReplayLeaveEvent* = object
    tick*: int
    player*: PlayerRef             # from identity.nim

  ReplayChatEvent* = object
    tick*: int
    speaker*: PlayerRef
    text*: string

  ReplayStep* = object
    tick*: int                    # post-step sim.tickCount; matches hash ticks
    inputs*: seq[InputState]
    prevInputs*: seq[InputState]
    preStep*: SimServer           # after replay events, before step
    postStep*: SimServer          # after sim.step
    bodyLimit*: int               # preStep.bodies.len; pass to tryReport probes
    leaves*: seq[ReplayLeaveEvent]
    chats*: seq[ReplayChatEvent]
    hashChecked*: bool
    hashMatched*: bool

  ReplayDriver* = object
    data*: ReplayData
    sim*: SimServer                # current (post last step) state
    masks: seq[uint8]              # sticky per-player key masks, sim-index aligned
    lastMasks: seq[uint8]          # masks applied on the previous tick
    joinIndex, leaveIndex, chatIndex, inputIndex, hashIndex: int
    allHashesMatched*: bool
    warnings*: seq[string]
```

`advance(driver): Option[ReplayStep]` (or an equivalent `hasStep` tuple) performs
**one tick**, faithfully mirroring `replays.nim:89-171`:

1. If `data.hashes.len == 0`, record a warning (`"replay has no tick hashes"`),
   set `allHashesMatched = false`, and return no step. The hashes are the only
   reliable replay end marker.
2. `let time = tickTime(driver.sim.tickCount)`. This matches
   `applyReplayEvents`: replay records at time 0 are applied before the sim steps
   from tick 0 to tick 1.
3. Apply leaves whose `time <= time`: before `sim.removePlayerAt(idx)`, capture
   the leaving player's stable `PlayerRef` from the current sim. Then delete the
   same index from `masks`/`lastMasks` to keep them sim-index aligned
   (mirror `replays.nim:92-102`). Return these leaves on the `ReplayStep`; do not
   rediscover them by comparing player counts after the step.
4. Apply joins whose `time <= time`: `sim.addPlayer(name, slot, token,
   trusted = true)` and grow `masks`/`lastMasks` (mirror `replays.nim:104-111`).
5. Apply inputs whose `time <= time`: `masks[player] = keys`
   (mirror `replays.nim:113-118`).
6. Apply chats whose `time <= time`: before `sim.addVotingChat(player,
   message)`, resolve the current live player index to a stable `PlayerRef` and
   return a `ReplayChatEvent` on the `ReplayStep`; then call `addVotingChat` so
   the sim state and hash stay aligned with `stepReplay`.
7. Build `prevInputs[i] = decodeInputMask(lastMasks[i])` and
   `inputs[i] = decodeInputMask(masks[i])`; then `lastMasks = masks`.
8. Copy `preStep = sim` and `bodyLimit = preStep.bodies.len`.
9. `sim.step(inputs, prevInputs)`.
10. Hash-check `sim.gameHash()` when the next recorded hash tick equals
   `sim.tickCount`. Mirror `checkReplayHash` exactly: missing or mismatched
   hashes set `allHashesMatched = false`, add a warning, and continue until
   `sim.tickCount >= replayMaxTick`.
11. Return `ReplayStep(tick: sim.tickCount, preStep: preStep, postStep: sim, ...)`.
   Return no step once `sim.tickCount >= int(data.hashes[^1].tick)` and the final
   hash has been checked or marked failed.

Crucially, `preStep` is **after** replay joins/leaves/input masks/chat have been
applied for the tick but **before** `sim.step`. The timeline loop should not copy
`driver.sim` before calling `advance`; that snapshot would predate same-tick
leaves/joins/chat and would not be index-aligned with `postStep`.

```nim
while true:
  let maybeStep = driver.advance()
  if maybeStep.isNone: break
  let step = maybeStep.get()
  result.events.add detect(step, identityTbl)
```

`SimServer` is a value `object`; assignment gives an independent pre-step
snapshot for ordinary `seq` mutation in current Nim. Cost: one full copy per
tick. Acceptable — this is an offline batch tool. If profiling later shows it
matters, narrow the copy to the mutable fields, but do not prematurely optimize.

### Step 2 — `identity.nim`: stable player identities

Player indices can shift on mid-game leaves, and events should resolve to a
stable identity. After the game has started (roles assigned), build:

```nim
type
  PlayerRef* = object
    joinOrder*: int                # stable sim slot/order
    slot*: int                     # same value for current Crewrift slots

  PlayerIdentity* = object
    slot*: int
    name*: string
    address*: string
    color*: uint8
    role*: PlayerRole
    joinOrder*: int
```

- Names/slots/tokens come from replay `joins`. For Crewrift's current
  `rjkNameSlotToken` replay format, `ReplayJoin.address` is not populated; use
  `sim.players[i].address` after `addPlayer` (normally the replay join `name`).
- `color`, `role`, `joinOrder`, and final `address` are read from
  `sim.players[i]` when `startGame` assigns roles. With `roleRevealTicks > 0`,
  that tick is the `Lobby -> RoleReveal` transition; with `roleRevealTicks == 0`,
  it is `Lobby -> Playing` (`sim.nim:2314-2367`).
- Provide stable lookups: `byColor(color): PlayerRef` (colors are unique per
  game), `byJoinOrder(j): PlayerRef` (bodies carry `slotId == joinOrder`,
  `sim.nim:2551`), and `identity(ref): PlayerIdentity`.
- Provide per-tick live-index helpers: `refForLiveIndex(sim, i): PlayerRef` and
  `liveIndexForRef(sim, ref): int`. Use these only while detecting an event;
  store `PlayerRef` in `GameEvent`, not the live index.

If a player leaves before roles have been assigned, emit `gekPlayerLeft` with a
best-effort `PlayerRef(joinOrder: player.joinOrder, slot: player.joinOrder)` and
leave name/role resolution absent from `identities`.

### Step 3 — `events.nim`: event model

```nim
type
  GameEventKind* = enum
    gekGameStarted        # roles assigned; per-player roles known
    gekPlayingStarted     # RoleReveal -> Playing (gameStartTick)
    gekKill               # imposter killed a crewmate
    gekTaskCompleted
    gekMeetingCalled      # Playing -> Voting
    gekVoteCast
    gekVoteEnded          # Voting -> VoteResult (tally done)
    gekEjectionApplied    # VoteResult -> Playing (ejected player dies, bodies cleared)
    gekChatMessage       # replay voting chat; text is recorded, not inferred
    gekStuckPenalty
    gekVent
    gekGameOver
    gekPlayerLeft         # mid-game disconnect (from replay leaves)

  MeetingReason* = enum mrBody, mrButton, mrUnknown
  VoteTargetKind* = enum vtkPlayer, vtkSkip, vtkNone, vtkUnknown

  VoteTarget* = object
    case kind*: VoteTargetKind
    of vtkPlayer: player*: PlayerRef
    else: discard

  GameEvent* = object
    tick*: int
    case kind*: GameEventKind
    of gekGameStarted:    imposterCount*: int; roles*: seq[PlayerIdentity]
    of gekPlayingStarted: discard
    of gekKill:           killer*, victim*: PlayerRef; atX*, atY*: int
    of gekTaskCompleted:  who*: PlayerRef; taskIndex*: int; taskName*: string
    of gekMeetingCalled:  caller*: PlayerRef; reason*: MeetingReason; body*: VoteTarget
    of gekVoteCast:       voter*: PlayerRef; target*: VoteTarget
    of gekVoteEnded:      ejected*: VoteTarget; timedOut*: bool
    of gekEjectionApplied: ejectedPlayer*: VoteTarget
    of gekChatMessage:    speaker*: PlayerRef; text*: string
    of gekStuckPenalty:   penalized*: PlayerRef
    of gekVent:           venter*: PlayerRef; toX*, toY*: int
    of gekGameOver:       winner*: PlayerRole; reason*: string; survivors*: seq[PlayerRef]
    of gekPlayerLeft:     left*: PlayerRef
    else: discard

  EpisodeTimeline* = object
    identities*: seq[PlayerIdentity]
    events*: seq[GameEvent]
    finalTick*: int
    hashValidated*: bool        # false if any gameHash mismatch was seen
    warnings*: seq[string]
```

Player references in events are stable `PlayerRef` values. Live sim indices are
only a detector implementation detail.

### Step 4 — `probes.nim`: reuse the real sim procs

**Body-report caller/body** — at the tick where `phase` went `Playing -> Voting`
and no `buttonCallsUsed` increment was seen (i.e. it was a body report):

```nim
type
  BodyReportProbe* = object
    callerIndex*: int
    bodyIndex*: int
    bodyColor*: uint8

proc findBodyReport*(postStep: SimServer, inputs, prevInputs: seq[InputState],
                     bodyLimit: int): BodyReportProbe
```

For each player `i`, in **ascending index order** (the sim processes players in
order; the first eligible one starts the vote, `sim.nim:3928`), require a fresh
attack this tick (`inputs[i].attack and not prevInputs[i].attack`). For each such
candidate, probe increasing body limits from `1 .. bodyLimit`:

```nim
if bodyLimit <= 0:
  return BodyReportProbe(callerIndex: -1, bodyIndex: -1)
for limit in 1 .. bodyLimit:
  var clone = postStep
  clone.phase = Playing
  clone.tryReport(i, limit)
  if clone.phase == Voting:
    return BodyReportProbe(callerIndex: i,
                           bodyIndex: limit - 1,
                           bodyColor: postStep.bodies[limit - 1].color)
```

This reuses the real `tryReport` proximity check and body loop order without
re-deriving the geometry. The `bodyLimit` must be `step.bodyLimit`
(`preStep.bodies.len`), not `postStep.bodies.len`, because bodies created earlier
in the same tick are not reportable until a later tick. `VoteState` does not
store caller/kind/body, and `tryReport` does not consume the body, so the body
cannot be "read back" from the clone except by this limit search.

Return `callerIndex = -1` / `bodyIndex = -1` if no probe matches. The detector
should emit `mrUnknown` and add a warning instead of inventing a caller.

### Step 5 — `detect.nim`: per-tick diff + probe dispatch

```nim
proc detect*(step: ReplayStep, ids: var IdentityTable): seq[GameEvent]
```

Detection rules (each compares `step.preStep` vs `step.postStep`; below, `prev`
= `step.preStep`, `cur` = `step.postStep`):

- **gekGameStarted**: `prev.phase == Lobby` and `cur.phase in {RoleReveal,
  Playing}`. Roles are assigned inside `startGame`, before the optional
  RoleReveal phase. Build the identity table here from `cur.players`. Imposter
  count via counting `role == Imposter` (cross-check `effectiveImposterCount`,
  `sim.nim:1452`).
- **gekPlayingStarted**: `prev.phase == RoleReveal and cur.phase == Playing`, or
  `prev.phase == Lobby and cur.phase == Playing` when `roleRevealTicks == 0`.
  Emit this separately from `gekGameStarted`; they can occur on the same tick
  only when there is no RoleReveal delay.
- **gekKill**: `cur.bodies.len > prev.bodies.len`. New bodies are the appended
  tail `cur.bodies[prev.bodies.len ..< cur.bodies.len]`; each `Body.color`/
  `slotId` identifies the victim. Successful killers are live-indexed imposters
  whose reward-account `kills` counter increased from `prev` to `cur`; use
  `cur.killCooldown == cur.config.killCooldownTicks` as a sanity check when the
  configured cooldown is positive. Sort killers by live sim index and pair them
  with new bodies in append order. This matches the simulator's player loop and
  `tryKill` append side effect. If the killer count and new-body count differ,
  emit the pairable prefix, add a warning, and leave unmatched events out rather
  than guessing.
- **gekTaskCompleted**: any `cur.tasks[t].completed[p]` true where
  `prev.tasks[t].completed[p]` false (`sim.nim:2281`). Emit per (p, t) with
  `tasks[t].name` and `refForLiveIndex(cur, p)`.
- **gekMeetingCalled**: `prev.phase == Playing` and `cur.phase == Voting`.
  Reason: if exactly one player's `buttonCallsUsed` incremented this tick,
  `mrButton`, caller = that player (`sim.nim:2700`), `body = vtkNone`.
  Otherwise call `findBodyReport(cur, step.inputs, step.prevInputs,
  step.bodyLimit)`. If it matches, emit `mrBody`, caller from `callerIndex`, and
  body from `ids.byColor(bodyColor)` / `ids.byJoinOrder(cur.bodies[bodyIndex].slotId)`.
  If it does not match, emit `mrUnknown` and add a warning.
- **gekVoteCast**: any `cur.voteState.votes[i]` changed from `-1` to a value
  (`-2` or ≥0) vs `prev` (`sim.nim:3912-3917`). Emit per voter with target.
  Convert `-2` to `vtkSkip`, valid player indices to `vtkPlayer` via
  `refForLiveIndex(cur, target)`, and unexpected values to `vtkUnknown` plus a
  warning. Guard against index shifts: votes arrays are re-created per meeting
  (`startVote`, `sim.nim:2624`).
- **gekVoteEnded**: `prev.phase == Voting` and `cur.phase == VoteResult`. Read
  `cur.voteState.ejectedPlayer` (`vtkNone` for −1 tie/none, `sim.nim:3146`).
  Convert valid ejected player indices through `refForLiveIndex(cur, ejected)`.
  `timedOut` = `prev.voteState.voteTimer <= 1` (the tally fired from timeout vs.
  all-cast; `sim.nim:3887` vs `3922`).
- **gekEjectionApplied**: `prev.phase == VoteResult` and `cur.phase == Playing`.
  The ejected player (from `prev.voteState.ejectedPlayer`, if ≥0) is now
  `alive == false` and bodies were cleared (`applyVoteResult`,
  `sim.nim:3159-3169`).
- **gekChatMessage**: emit directly from replay chat records applied this tick.
  Convert the replay `player` index through the current pre-step sim player list
  before applying chat so the event stores a stable `PlayerRef`; preserve the
  replay `message` text exactly as parsed. Chat records should still be applied
  to the sim via `addVotingChat` before `step` so `gameHash` validation matches
  the recorded replay.
- **gekStuckPenalty**: a crewmate whose `reward` decreased and `lastMoveTick`
  reset this tick (`sim.nim:3196-3210`), emitted with `PlayerRef`.
- **gekVent**: an imposter whose `ventCooldown` rose to 30 from a lower value and
  whose `(x, y)` jumped discontinuously (`tryVent`, `sim.nim:2598`). Emit with new
  position. (Heuristic — acceptable; there is no explicit vent flag.)
- **gekGameOver**: `cur.phase == GameOver` and `prev.phase != GameOver`. `winner`
  from `cur.winner`; `reason` derived: `cur.timeLimitReached` → "time limit";
  else all imposters dead → "imposters eliminated"; else imposter parity →
  "crew outnumbered"; else all tasks done (`allTasksDone`, `sim.nim:3235`) →
  "tasks completed". Survivors = alive players as `PlayerRef`.
- **gekPlayerLeft**: emit directly from `step.leaves`. Do not infer leaves from
  `cur.players.len < prev.players.len`; `prev` is already after replay leaves,
  and vote/ejection deaths do not remove players.

Order events deterministically within a tick (e.g. leaves, game-start, kills,
meeting-called, votes/ejections, chats, penalties/vents, game-over) so the
timeline is stable.

### Step 6 — `timeline.nim`: orchestrate

```nim
proc extractTimeline*(report: EpisodeReport): EpisodeTimeline =
  var driver = initReplayDriver(report.replay, report.config)
  var identityTbl = initIdentityTable(report.replay)
  while true:
    let maybeStep = driver.advance()
    if maybeStep.isNone:
      break
    let step = maybeStep.get()
    result.events.add detect(step, identityTbl)
    if stopAfterFirstGameOver(result.events):
      break
  result.identities = identityTbl.identities()
  result.finalTick = driver.sim.tickCount
  result.hashValidated = driver.allHashesMatched
  result.warnings = driver.warnings
```

Handle multi-game replays defensively: a `SimServer` can cycle
`GameOver -> Lobby -> … -> GameOver` if `maxGames > 1`. For v1 assume one game per
episode (the default variant runs `maxGames: 1`, see `coworld_manifest.json`),
but detect a second `gekGameStarted` and either segment the timeline per game or
stop after the first `gekGameOver`. Pick stop-after-first for v1; note the
limitation in output.

### Step 7 — wire `scribe.nim`

Replace the placeholder summary with: decode → `extractTimeline` → print the
event list (one line per event with tick + resolved identities), plus a header
with identities and `hashValidated`. Keep it human-readable for now; the
machine-readable report artifact format is a later step.

---

## Edge cases and gotchas

- **Index alignment.** `masks`/`lastMasks` in the driver must shift exactly as
  the sim's `players` do on join/leave, or inputs misroute. Mirror
  `replays.nim:92-118` precisely, including the `delete` on leave.
- **Pre-step means after replay events.** The diff baseline must be captured
  after replay joins/leaves/inputs/chat have been applied for that tick. A
  baseline copied before leaves/joins/chat will not be index-aligned with the
  post-step sim.
- **Times are milliseconds.** `ReplayData` times are ms via `tickTime`; convert
  with `ReplayFps = 24`. Off-by-one here desyncs input application.
- **Report probe must filter on fresh attack.** `tryReport` does not check input;
  seed the probe only with players who pressed a fresh attack that tick, in
  ascending index order, and take the first match.
- **Report probe must use `bodyLimit`.** Same-tick newly killed bodies are not
  reportable because the sim passes `bodiesBeforeTick` to `tryReport`.
- **Do not probe kills from global pre-step state.** Real kill eligibility is
  after cooldown decrement, movement, and possible venting for that player. Pair
  successful killers with appended bodies in sim processing order.
- **Stable identities only in events.** Live sim indices are allowed inside
  `detect`, but every emitted event should store `PlayerRef`.
- **Vote arrays reset per meeting.** Diff `votes` only within a single meeting;
  `startVote` reallocates them (`sim.nim:2624`).
- **Game-over can fire on a kill tick.** `checkWinCondition` runs at the end of
  the Playing loop (`sim.nim:3947`), so a kill and a game-over may share a tick.
  Emit both.
- **gameHash must validate.** A mismatch means the re-sim diverged (codec drift
  or corrupt replay). Surface it (`hashValidated = false`) rather than emitting a
  wrong timeline silently.
- **Do not optimize the per-tick full copy yet.** Correctness first; this is
  offline.

## Testing plan

Tests live under `reporters/scribe/` (mirror the repo's `tests/` style; a test
adds `--path` to reach `../src` as in `tests/config.nims`, or imports the
reporter modules by relative path). Build standalone with `nim c` like the bots.

1. **Fixture-driven smoke.** A helper that drives `SimServer` and uses the
   exported `openReplayWriter`/`writeJoin`/`writeInput`/`writeHash`
   (`src/crewrift/replays.nim`) to synthesize a replay with a known scripted
   sequence (joins, role assignment, a kill, a later body report, votes), then
   asserts the extracted timeline contains the expected stable `PlayerRef`s and
   ticks.
2. **Hash-validation test.** Assert `hashValidated == true` on a fixture replay
   whose hashes were produced by the sim, and `false` plus a warning on a
   deliberately corrupted hash.
3. **Role timing test.** Cover both `roleRevealTicks > 0` and
   `roleRevealTicks == 0`: `gekGameStarted` occurs when roles are assigned, while
   `gekPlayingStarted` occurs when the phase actually becomes `Playing`.
4. **Body-limit regression test.** Create an old body, then on a later tick have
   one imposter create a new body before another player reports. Assert the
   report can only resolve to the old body because `bodyLimit == preStep.bodies.len`.
5. **Same-tick kill attribution test.** Script two imposters killing two
   crewmates on the same tick and assert successful killers sorted by live index
   pair with newly appended bodies in order. Include at least one case where a
   killer starts the tick with cooldown `1` or moves into range on that tick, so
   a stale pre-step `tryKill` probe would fail.
6. **Leave/index-shift test.** Script a replay leave before a task/vote event and
   assert later events still use stable `PlayerRef`s rather than shifted live
   indices.
7. **Chat event test.** Script a Voting-phase replay chat record using the
   exported replay writer's `writeChat`, assert a `gekChatMessage` event carries
   the exact tick, stable speaker `PlayerRef`, and original message text, and
   assert the replay remains hash-validated.
8. **Real episode.** Run the game locally using the README server + 8 `notsus`
   bots, but set a replay target first:

   ```sh
   mkdir -p tmp/reporter-fixtures
   COGAME_SAVE_REPLAY_URI=file://$PWD/tmp/reporter-fixtures/replay.bitreplay \
     COGAME_HOST=0.0.0.0 \
     COGAME_PORT=2000 \
     COGAME_CONFIG_URI=file://$PWD/config.json \
     nim r src/crewrift.nim
   ```

   Feed the written replay directly to `scribe`, then eyeball the timeline
   against the server's prose `logGameEvent` stdout. The prose lines (`sim.nim`
   kill / vote / win logs) are a useful cross-check, but the hash check remains
   the determinism proof.

## Out of scope (deferred)

- The canonical Coworld report zip format (`render`/`event_log`/`trace` zip per
  `packages/coworld/.../artifacts/REPORT.md`). The service currently returns a
  raw event-log payload over websocket rather than writing a report zip.
- Multi-game segmentation beyond stop-after-first and any narrative/stats layered
  on top of the timeline.
