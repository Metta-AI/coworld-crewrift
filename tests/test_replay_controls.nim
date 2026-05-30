import
  std/[os, unittest],
  crewrift/[global, sim]

const
  GameDir = currentSourcePath.parentDir.parentDir
  ReplayControlLayerId = 8

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc clickReplayControl(state: var GlobalViewerState, x, y: int) =
  ## Queues one replay control click on the browser-visible replay UI layer.
  state.mouseLayer = ReplayControlLayerId
  state.mouseX = x
  state.mouseY = y
  state.mouseDown = false
  state.clickPending = true

suite "replay controls":
  test "transport and scrubber share one browser hit layer":
    var game = initCrewriftForTest(defaultGameConfig())
    var state = initGlobalViewerState()
    var next: GlobalViewerState

    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    state = next

    state.clickReplayControl(11, 2)
    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    check next.replayCommands == @[' ']
    check next.replaySeekTick == -1

    state = next
    state.replayCommands.setLen(0)
    state.replaySeekTick = -1
    state.clickReplayControl(64, 17)
    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = false,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    check next.replayCommands.len == 0
    check next.replaySeekTick == 506

    state = next
    state.replayCommands.setLen(0)
    state.replaySeekTick = -1
    state.clickReplayControl(11, 2)
    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 506,
      replayPlaying = false,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    check next.replayCommands == @[' ']
    check next.replaySeekTick == -1
