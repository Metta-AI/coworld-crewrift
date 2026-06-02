import
  bitworld/spriteprotocol,
  bitworld/replays as replayCodec,
  sim

type
  ReplayPlayer* = object
    data*: ReplayData
    joinIndex*: int
    leaveIndex*: int
    inputIndex*: int
    hashIndex*: int
    masks*: seq[uint8]
    lastAppliedMasks*: seq[uint8]
    playing*: bool
    looping*: bool
    speedIndex*: int
    mismatchQuit*: bool
    hashValidationFailed*: bool
    hashMismatchTick*: int

const
  PlaybackSpeeds* = [1, 2, 3, 4, 8]
  CrewriftReplayMagic = "CREWRIFT"
  CrewriftReplayFormatVersion = 3'u16
  CrewriftReplaySpec = ReplaySpec(
    magic: CrewriftReplayMagic,
    formatVersion: CrewriftReplayFormatVersion,
    gameName: GameName,
    gameVersion: GameVersion,
    joinKind: rjkNameSlotToken,
    allowChat: false,
    allowCompressed: true,
    hashOrder: rhoStop
  )

export replayCodec

proc tickTime*(tick: int): uint32 =
  ## Converts a simulation tick to replay milliseconds.
  replayCodec.tickTime(tick, ReplayFps)

proc openReplayWriter*(path: string, configJson: string): ReplayWriter =
  ## Opens a replay file and writes the header.
  replayCodec.openReplayWriter(path, configJson, CrewriftReplaySpec)

proc parseReplayBytes*(bytes: string): ReplayData =
  ## Parses one replay file buffer into memory.
  replayCodec.parseReplayBytes(bytes, CrewriftReplaySpec)

proc loadReplay*(path: string): ReplayData =
  ## Loads a replay file into memory.
  replayCodec.loadReplay(path, CrewriftReplaySpec)

proc initReplayPlayer*(data: ReplayData): ReplayPlayer =
  ## Builds replay playback state.
  result.data = data
  result.masks = @[]
  result.lastAppliedMasks = @[]
  result.playing = true
  result.looping = true
  result.speedIndex = 0
  result.hashMismatchTick = -1

proc replaySpeed*(replay: ReplayPlayer): int =
  ## Returns the current integer replay speed.
  PlaybackSpeeds[clamp(replay.speedIndex, 0, PlaybackSpeeds.high)]

proc replayMaxTick*(replay: ReplayPlayer): int =
  ## Returns the final tick available in the replay.
  if replay.data.hashes.len == 0:
    return 0
  int(replay.data.hashes[^1].tick)

proc resetReplay*(replay: var ReplayPlayer) =
  ## Resets replay playback cursors.
  replay.joinIndex = 0
  replay.leaveIndex = 0
  replay.inputIndex = 0
  replay.hashIndex = 0
  replay.hashValidationFailed = false
  replay.hashMismatchTick = -1
  replay.masks = @[]
  replay.lastAppliedMasks = @[]

proc ensureReplayPlayer(replay: var ReplayPlayer, player: int) =
  ## Expands replay input tables for one player.
  while replay.masks.len <= player:
    replay.masks.add(0)
    replay.lastAppliedMasks.add(0)

proc applyReplayEvents(replay: var ReplayPlayer, sim: var SimServer) =
  ## Applies replay joins and inputs for the current tick.
  let time = tickTime(sim.tickCount)
  while replay.leaveIndex < replay.data.leaves.len and
      replay.data.leaves[replay.leaveIndex].time <= time:
    let leave = replay.data.leaves[replay.leaveIndex]
    if int(leave.player) < 0 or int(leave.player) >= sim.players.len:
      raise newException(ReplayError, "Replay player leave is invalid")
    sim.removePlayerAt(int(leave.player))
    if int(leave.player) < replay.masks.len:
      replay.masks.delete(int(leave.player))
    if int(leave.player) < replay.lastAppliedMasks.len:
      replay.lastAppliedMasks.delete(int(leave.player))
    inc replay.leaveIndex

  while replay.joinIndex < replay.data.joins.len and
      replay.data.joins[replay.joinIndex].time <= time:
    let join = replay.data.joins[replay.joinIndex]
    if int(join.player) != sim.players.len:
      raise newException(ReplayError, "Replay player join order is invalid")
    discard sim.addPlayer(join.name, join.slot, join.token, trusted = true)
    replay.ensureReplayPlayer(int(join.player))
    inc replay.joinIndex

  while replay.inputIndex < replay.data.inputs.len and
      replay.data.inputs[replay.inputIndex].time <= time:
    let input = replay.data.inputs[replay.inputIndex]
    replay.ensureReplayPlayer(int(input.player))
    replay.masks[int(input.player)] = input.keys
    inc replay.inputIndex

proc replayPrevInputs(
  replay: var ReplayPlayer,
  playerCount: int
): seq[InputState] =
  ## Builds previous replay inputs for the current tick.
  result = newSeq[InputState](playerCount)
  for playerIndex in 0 ..< playerCount:
    replay.ensureReplayPlayer(playerIndex)
    result[playerIndex] = decodeInputMask(replay.lastAppliedMasks[playerIndex])

proc replayInputs(
  replay: var ReplayPlayer,
  playerCount: int
): seq[InputState] =
  ## Builds replay inputs for the current tick.
  result = newSeq[InputState](playerCount)
  for playerIndex in 0 ..< playerCount:
    replay.ensureReplayPlayer(playerIndex)
    result[playerIndex] = decodeInputMask(replay.masks[playerIndex])
    replay.lastAppliedMasks[playerIndex] = replay.masks[playerIndex]

proc checkReplayHash(replay: var ReplayPlayer, sim: SimServer) =
  ## Checks the recorded hash for the current tick.
  if replay.hashValidationFailed:
    if sim.tickCount >= replay.replayMaxTick():
      replay.playing = false
    return
  if replay.hashIndex >= replay.data.hashes.len:
    replay.playing = false
    return
  let expected = replay.data.hashes[replay.hashIndex]
  if int(expected.tick) < sim.tickCount:
    let message = "Replay hash tick is missing at tick " & $sim.tickCount & "."
    if replay.mismatchQuit:
      raise newException(ReplayError, message)
    echo message
    replay.hashValidationFailed = true
    replay.hashMismatchTick = sim.tickCount
    return
  if int(expected.tick) > sim.tickCount:
    return
  let hash = sim.gameHash()
  if hash != expected.hash:
    let message =
      "Replay hash mismatch at tick " & $sim.tickCount &
        "; expected " & $expected.hash & ", got " & $hash & "."
    if replay.mismatchQuit:
      raise newException(ReplayError, message)
    echo message
    replay.hashValidationFailed = true
    replay.hashMismatchTick = sim.tickCount
    return
  inc replay.hashIndex

proc stepReplay*(replay: var ReplayPlayer, sim: var SimServer) =
  ## Advances replay by one simulation tick.
  replay.applyReplayEvents(sim)
  let prevInputs = replay.replayPrevInputs(sim.players.len)
  let inputs = replay.replayInputs(sim.players.len)
  sim.step(inputs, prevInputs)
  replay.checkReplayHash(sim)

proc seekReplay*(replay: var ReplayPlayer, sim: var SimServer, tick: int) =
  ## Seeks replay playback to a target tick.
  sim = initSimServer(sim.config)
  replay.resetReplay()
  while sim.tickCount < tick and replay.hashIndex < replay.data.hashes.len:
    replay.stepReplay(sim)

proc applyReplaySeek*(
  replay: var ReplayPlayer,
  sim: var SimServer,
  tick: int
) =
  ## Seeks replay playback and pauses on the target tick.
  replay.playing = false
  replay.seekReplay(sim, clamp(tick, 0, replay.replayMaxTick()))

proc applyReplayCommand*(
  replay: var ReplayPlayer,
  sim: var SimServer,
  command: char
) =
  ## Applies one global viewer replay command.
  case command
  of ' ':
    replay.playing = not replay.playing
  of 'p':
    replay.playing = true
  of 'P':
    replay.playing = false
  of '+', '=':
    replay.speedIndex = min(replay.speedIndex + 1, PlaybackSpeeds.high)
  of '-', '_':
    replay.speedIndex = max(replay.speedIndex - 1, 0)
  of '1':
    replay.speedIndex = 0
  of '2':
    replay.speedIndex = 1
  of '3':
    replay.speedIndex = 2
  of '4':
    replay.speedIndex = 3
  of '8':
    replay.speedIndex = 4
  of ',', '<':
    replay.playing = false
    replay.seekReplay(sim, 0)
  of 'b':
    replay.playing = false
    replay.seekReplay(sim, max(0, sim.tickCount - 1))
  of 'e':
    replay.playing = false
    replay.seekReplay(sim, replay.replayMaxTick())
  of 'r':
    replay.looping = not replay.looping
  of '.', '>':
    replay.playing = false
    replay.seekReplay(sim, sim.tickCount + ReplayFps * 5)
  else:
    discard

proc applySpeedCommand*(speedIndex: var int, command: char) =
  ## Applies one live playback speed command.
  case command
  of '+', '=':
    speedIndex = min(speedIndex + 1, PlaybackSpeeds.high)
  of '-', '_':
    speedIndex = max(speedIndex - 1, 0)
  of '1':
    speedIndex = 0
  of '2':
    speedIndex = 1
  of '3':
    speedIndex = 2
  of '4':
    speedIndex = 3
  of '8':
    speedIndex = 4
  else:
    discard

proc playbackSpeed*(speedIndex: int): int =
  ## Returns the live playback speed for an index.
  PlaybackSpeeds[clamp(speedIndex, 0, PlaybackSpeeds.high)]
