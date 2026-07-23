import
  global,
  replays,
  sim

proc initReplayRuntime*(
  data: ReplayData,
  looping,
  mismatchQuit,
  buildKeyframes,
  gameEventLoggingEnabled: bool
): tuple[sim: SimServer, replay: ReplayPlayer] =
  ## Initializes the simulation and transport state shared by replay viewers.
  result.sim = initSimServer(data.replayGameConfig())
  result.sim.gameEventLoggingEnabled = gameEventLoggingEnabled
  result.replay = initReplayPlayer(data)
  result.replay.looping = looping
  result.replay.mismatchQuit = mismatchQuit
  if buildKeyframes:
    result.replay.buildReplayKeyframes(result.sim)

proc applyReplayControls*(
  sim: var SimServer,
  replay: var ReplayPlayer,
  seekTicks: openArray[int],
  commands: openArray[char]
) =
  ## Applies replay seek and transport controls.
  for seekTick in seekTicks:
    replay.applyReplaySeek(sim, seekTick)
  for command in commands:
    replay.applyReplayCommand(sim, command)

proc advanceReplayFrame*(sim: var SimServer, replay: var ReplayPlayer) =
  ## Advances one presentation frame at the selected replay speed.
  if replay.playing:
    for _ in 0 ..< replay.replaySpeed():
      if replay.playing:
        replay.stepReplay(sim)
    if replay.looping and not replay.playing:
      replay.seekReplay(sim, 0)
      replay.playing = true

proc buildReplayGlobalUpdates*(
  sim: var SimServer,
  replay: ReplayPlayer,
  state: GlobalViewerState,
  nextState: var GlobalViewerState
): seq[uint8] =
  ## Builds one global Sprite v1 packet from shared replay state.
  sim.buildSpriteProtocolUpdates(
    state,
    nextState,
    sim.tickCount,
    replay.playing,
    replay.replaySpeed(),
    replay.replayMaxTick(),
    replay.looping,
    true,
    replay.hashMismatchTick,
    replay.debugSprites
  )
