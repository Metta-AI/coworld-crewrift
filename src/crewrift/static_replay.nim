import
  global,
  replays,
  sim

type
  StaticReplayViewer* = ref object
    ## Game-owned replay runtime used by the static browser bundle.
    sim*: SimServer
    replay*: ReplayPlayer
    viewerState*: GlobalViewerState
    frame*: seq[uint8]

proc renderFrame*(viewer: StaticReplayViewer) =
  ## Produces the next public Bitworld Sprite v1 presentation packet.
  var nextState: GlobalViewerState
  viewer.frame = viewer.sim.buildSpriteProtocolUpdates(
    viewer.viewerState,
    nextState,
    viewer.sim.tickCount,
    viewer.replay.playing,
    viewer.replay.replaySpeed(),
    viewer.replay.replayMaxTick(),
    viewer.replay.looping,
    true,
    viewer.replay.hashMismatchTick,
    viewer.replay.debugSprites
  )
  viewer.viewerState = nextState

  # Clicks on the shared renderer's replay controls are translated by the
  # shared Crewrift global-view code. Apply them here, without exposing that
  # private contract to Coworld or to the renderer.
  let
    seekTick = viewer.viewerState.replaySeekTick
    commands = viewer.viewerState.replayCommands
  viewer.viewerState.replaySeekTick = -1
  viewer.viewerState.replayCommands.setLen(0)
  if seekTick >= 0:
    viewer.replay.applyReplaySeek(viewer.sim, seekTick)
  for command in commands:
    viewer.replay.applyReplayCommand(viewer.sim, command)

  if seekTick >= 0 or commands.len > 0:
    var postCommandState: GlobalViewerState
    viewer.frame.add viewer.sim.buildSpriteProtocolUpdates(
      viewer.viewerState,
      postCommandState,
      viewer.sim.tickCount,
      viewer.replay.playing,
      viewer.replay.replaySpeed(),
      viewer.replay.replayMaxTick(),
      viewer.replay.looping,
      true,
      viewer.replay.hashMismatchTick,
      viewer.replay.debugSprites
    )
    viewer.viewerState = postCommandState

proc initStaticReplayViewer*(bytes: string): StaticReplayViewer =
  ## Parses and pins playback to this Crewrift build's replay contract.
  let data = parseReplayBytes(bytes)
  result = StaticReplayViewer()
  result.sim = initSimServer(data.replayGameConfig())
  result.sim.gameEventLoggingEnabled = false
  result.replay = initReplayPlayer(data)
  result.replay.looping = false
  result.replay.mismatchQuit = true
  result.viewerState = initGlobalViewerState()
  result.renderFrame()

proc advanceFrame*(viewer: StaticReplayViewer) =
  ## Advances one 24 fps presentation frame at the selected replay speed.
  if viewer.replay.playing:
    for _ in 0 ..< viewer.replay.replaySpeed():
      if viewer.replay.playing:
        viewer.replay.stepReplay(viewer.sim)
    if viewer.replay.looping and not viewer.replay.playing:
      viewer.replay.seekReplay(viewer.sim, 0)
      viewer.replay.playing = true
  viewer.renderFrame()

proc applyClientPacket*(viewer: StaticReplayViewer, packet: string) =
  ## Applies the shared Sprite v1 renderer's input packet.
  viewer.viewerState.applyGlobalViewerMessage(packet)
  viewer.renderFrame()

proc applyCommand*(viewer: StaticReplayViewer, command: char) =
  ## Applies one replay transport command (primarily useful to tests).
  viewer.replay.applyReplayCommand(viewer.sim, command)
  viewer.renderFrame()

proc frameBytes*(viewer: StaticReplayViewer): string =
  ## Returns a stable copy of the most recently generated Sprite v1 packet.
  result = newString(viewer.frame.len)
  if viewer.frame.len > 0:
    copyMem(result[0].addr, viewer.frame[0].unsafeAddr, viewer.frame.len)

proc replayBuild*(viewer: StaticReplayViewer): string =
  ## Returns the replay build identity enforced by parseReplayBytes.
  viewer.replay.data.gameName & ":" & viewer.replay.data.gameVersion

proc maxTick*(viewer: StaticReplayViewer): int =
  ## Returns the final tick recorded by the loaded replay.
  viewer.replay.replayMaxTick()
