import
  std/[os, unittest],
  crewrift/static_replay

const
  GameDir = currentSourcePath.parentDir.parentDir
  ReplayPath = GameDir / "tests" / "replays" / "notsus.bitreplay"

proc loadFixtureViewer(): StaticReplayViewer =
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initStaticReplayViewer(readFile(ReplayPath))
  finally:
    setCurrentDir(previousDir)

suite "static replay viewer core":
  test "loads the current replay fixture and emits Sprite v1 frames":
    let viewer = loadFixtureViewer()
    check viewer.replayBuild() == "crewrift:1"
    check viewer.frame.len > 0
    check viewer.frame[0] in [1'u8, 2'u8, 3'u8, 4'u8, 5'u8, 6'u8]

  test "resimulation remains hash-valid and transport commands work":
    let viewer = loadFixtureViewer()
    for _ in 0 ..< 32:
      viewer.advanceFrame()
    check viewer.sim.tickCount == 32
    check not viewer.replay.hashValidationFailed

    viewer.applyCommand('P')
    let pausedTick = viewer.sim.tickCount
    viewer.advanceFrame()
    check viewer.sim.tickCount == pausedTick

    viewer.applyCommand('p')
    viewer.advanceFrame()
    check viewer.sim.tickCount == pausedTick + 1
    check viewer.frame.len > 0
