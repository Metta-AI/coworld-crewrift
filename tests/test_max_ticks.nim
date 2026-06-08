import
  std/[json, os, unittest],
  bitworld/spriteprotocol,
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

suite "max ticks":
  test "default vote timer is ten seconds":
    check defaultGameConfig().voteTimerTicks == TargetFps * 10

  test "default kill cooldown is five hundred ticks":
    check KillCooldownTicks == 500
    check defaultGameConfig().killCooldownTicks == 500

  test "config json":
    var config = defaultGameConfig()
    config.maxTicks = 123

    let serialized = parseJson(config.configJson())
    check serialized["maxTicks"].getInt() == 123

    var roundTrip = defaultGameConfig()
    roundTrip.update($serialized)
    check roundTrip.maxTicks == 123

  test "starts at game start":
    var config = defaultGameConfig()
    config.minPlayers = 3
    config.imposterCount = 1
    config.autoImposterCount = false
    config.maxTicks = 2
    config.tasksPerPlayer = 1
    config.startWaitTicks = 0

    var sim = initCrewriftForTest(config)
    discard sim.addPlayer("player1")
    discard sim.addPlayer("player2")
    discard sim.addPlayer("player3")

    var inputs = newSeq[InputState](sim.players.len)
    sim.step(inputs, inputs)
    check sim.phase == RoleReveal
    check sim.gameTicksElapsed() == 0

    for _ in 0 ..< sim.config.roleRevealTicks:
      sim.step(inputs, inputs)
    check sim.phase == Playing
    check sim.gameTicksElapsed() == 0

    sim.step(inputs, inputs)
    check sim.phase == Playing
    check sim.gameTicksElapsed() == 1

    sim.step(inputs, inputs)
    check sim.phase == GameOver
    check sim.winner == Crewmate
    check sim.timeLimitReached

    for player in sim.players:
      check player.reward == 0
