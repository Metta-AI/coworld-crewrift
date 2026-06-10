import
  std/[os, unittest],
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

proc addPlayers(sim: var SimServer, count: int) =
  ## Adds test players to the simulation.
  for i in 0 ..< count:
    discard sim.addPlayer("player" & $(i + 1))

proc advanceMeetingCall(sim: var SimServer) =
  ## Advances the meeting-call interstitial into voting.
  var
    inputs = newSeq[InputState](sim.players.len)
    prevInputs = inputs
  for _ in 0 ..< MeetingCallTicks:
    sim.step(inputs, prevInputs)
    prevInputs = inputs

suite "vote cooldown":
  test "vote result resets imposter cooldown":
    var config = defaultGameConfig()
    config.minPlayers = 3
    config.imposterCount = 1
    config.autoImposterCount = false
    config.killCooldownTicks = 1200
    config.tasksPerPlayer = 1

    var sim = initCrewriftForTest(config)
    sim.addPlayers(3)
    let imposter = 0
    sim.players[imposter].role = Imposter

    sim.players[imposter].killCooldown = 17
    sim.startVote()
    sim.advanceMeetingCall()
    sim.voteState.ejectedPlayer = -1
    sim.applyVoteResult()

    check sim.phase == Playing
    check sim.players[imposter].killCooldown == config.killCooldownTicks
