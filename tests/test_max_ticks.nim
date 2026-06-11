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
  test "default vote timer is fifty seconds":
    check defaultGameConfig().voteTimerTicks == TargetFps * 50

  test "default kill cooldown is five hundred ticks":
    check KillCooldownTicks == 500
    check defaultGameConfig().killCooldownTicks == 500

  test "default connection timeouts are split":
    check ConnectTimeoutTicks == TargetFps * 120
    check DisconnectTimeoutTicks == TargetFps * 30
    check defaultGameConfig().connectTimeoutTicks == TargetFps * 120
    check defaultGameConfig().disconnectTimeoutTicks == TargetFps * 30

  test "default seed resolves to a concrete replay seed":
    var config = defaultGameConfig()
    check config.seed == RandomSeedSentinel

    var sim = initCrewriftForTest(config)
    check sim.config.seed != RandomSeedSentinel

    let serialized = parseJson(sim.config.configJson())
    check serialized["seed"].getInt() == sim.config.seed

  test "explicit seed stays fixed":
    var config = defaultGameConfig()
    config.seed = 123

    var sim = initCrewriftForTest(config)
    check sim.config.seed == 123

  test "seed below random sentinel is rejected":
    var config = defaultGameConfig()

    expect ValueError:
      config.update("""{"seed":-2}""")

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
    config.startWaitTicks = 2
    config.gameInfoTicks = 2

    var sim = initCrewriftForTest(config)
    discard sim.addPlayer("player1")
    discard sim.addPlayer("player2")
    discard sim.addPlayer("player3")

    var inputs = newSeq[InputState](sim.players.len)
    sim.step(inputs, inputs)
    check sim.phase == Lobby
    check sim.gameTicksElapsed() == 0

    sim.step(inputs, inputs)
    check sim.phase == GameInfo
    check sim.gameTicksElapsed() == 0

    for _ in 0 ..< sim.config.gameInfoTicks:
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

  test "meeting voting and result screens do not spend max ticks":
    var config = defaultGameConfig()
    config.minPlayers = 3
    config.imposterCount = 1
    config.autoImposterCount = false
    config.maxTicks = 2
    config.tasksPerPlayer = 1
    config.startWaitTicks = 0
    config.roleRevealTicks = 0
    config.voteTimerTicks = 3
    config.voteResultTicks = 2

    var sim = initCrewriftForTest(config)
    discard sim.addPlayer("player1")
    discard sim.addPlayer("player2")
    discard sim.addPlayer("player3")

    let inputs = newSeq[InputState](sim.players.len)
    sim.step(inputs, inputs)
    check sim.phase == Playing
    check sim.gameTicksElapsed() == 0

    sim.step(inputs, inputs)
    check sim.phase == Playing
    check sim.gameTicksElapsed() == 1

    sim.startVote()
    check sim.phase == MeetingCall
    for _ in 0 ..< MeetingCallTicks:
      sim.step(inputs, inputs)
    check sim.phase == Voting
    check sim.gameTicksElapsed() == 1
    check not sim.timeLimitReached

    for _ in 0 ..< config.voteTimerTicks:
      sim.step(inputs, inputs)
    check sim.phase == VoteResult
    check sim.gameTicksElapsed() == 1
    check not sim.timeLimitReached

    for _ in 0 ..< config.voteResultTicks:
      sim.step(inputs, inputs)
    check sim.phase == Playing
    check sim.gameTicksElapsed() == 1
    check not sim.timeLimitReached

    sim.step(inputs, inputs)
    check sim.phase == GameOver
    check sim.gameTicksElapsed() == 2
    check sim.timeLimitReached
