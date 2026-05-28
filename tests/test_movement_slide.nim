import
  std/[os, unittest],
  bitworld/protocol,
  crewrift/sim

const GameDir = currentSourcePath.parentDir.parentDir

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc blockAll(sim: var SimServer) =
  ## Marks all map cells blocked for movement tests.
  for i in 0 ..< sim.walkMask.len:
    sim.walkMask[i] = false

proc setWalk(sim: var SimServer, x, y: int, walk: bool) =
  ## Updates one walk mask cell for movement tests.
  sim.walkMask[mapIndex(x, y)] = walk

suite "movement slide":
  test "slides toward input around a blocked step":
    var sim = initCrewriftForTest(defaultGameConfig())
    let playerIndex = sim.addPlayer("slider")
    sim.blockAll()
    sim.players[playerIndex].x = 20
    sim.players[playerIndex].y = 20
    sim.players[playerIndex].velX =
      sim.config.motionScale - sim.config.accel
    sim.setWalk(20, 20, true)
    sim.setWalk(20, 21, true)
    sim.setWalk(21, 21, true)

    sim.applyInput(
      playerIndex,
      InputState(right: true, down: true),
      InputState(),
      sim.bodies.len
    )

    check sim.players[playerIndex].x == 21
    check sim.players[playerIndex].y == 21

  test "scans farther when several pixels are queued":
    var sim = initCrewriftForTest(defaultGameConfig())
    let playerIndex = sim.addPlayer("scanner")
    sim.blockAll()
    sim.players[playerIndex].x = 30
    sim.players[playerIndex].y = 30
    sim.players[playerIndex].velX =
      sim.config.maxSpeed - sim.config.accel
    sim.setWalk(30, 30, true)
    sim.setWalk(30, 31, true)
    sim.setWalk(30, 32, true)
    sim.setWalk(30, 33, true)
    sim.setWalk(31, 33, true)

    sim.applyInput(
      playerIndex,
      InputState(right: true, down: true),
      InputState(),
      sim.bodies.len
    )

    check sim.players[playerIndex].x == 31
    check sim.players[playerIndex].y == 33
