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

proc stepVote(
  sim: var SimServer,
  inputs: var seq[InputState],
  prevInputs: var seq[InputState]
) =
  ## Advances one vote tick and records the sampled input.
  sim.step(inputs, prevInputs)
  prevInputs = inputs

proc advanceMeetingCall(sim: var SimServer) =
  ## Advances the meeting-call interstitial into voting.
  var
    inputs = newSeq[InputState](sim.players.len)
    prevInputs = inputs
  for _ in 0 ..< MeetingCallTicks:
    sim.stepVote(inputs, prevInputs)

suite "vote cursor input":
  test "pressed direction can reach and commit a target slot":
    var config = defaultGameConfig()
    config.minPlayers = 4
    config.imposterCount = 0
    config.autoImposterCount = false
    config.tasksPerPlayer = 1
    config.voteTimerTicks = 120

    var sim = initCrewriftForTest(config)
    sim.addPlayers(4)
    sim.startVote()
    sim.advanceMeetingCall()

    var
      inputs = newSeq[InputState](sim.players.len)
      prevInputs = inputs

    for _ in 0 ..< 3:
      inputs[0].right = true
      sim.stepVote(inputs, prevInputs)
      inputs[0].right = false
      sim.stepVote(inputs, prevInputs)

    inputs[0].attack = true
    sim.stepVote(inputs, prevInputs)

    check sim.voteState.cursor[0] == 3
    check sim.voteState.votes[0] == 3

  test "held direction only moves one target slot":
    var config = defaultGameConfig()
    config.minPlayers = 4
    config.imposterCount = 0
    config.autoImposterCount = false
    config.tasksPerPlayer = 1
    config.voteTimerTicks = 120

    var sim = initCrewriftForTest(config)
    sim.addPlayers(4)
    sim.startVote()
    sim.advanceMeetingCall()

    var
      inputs = newSeq[InputState](sim.players.len)
      prevInputs = inputs

    inputs[0].right = true
    for _ in 0 ..< 3:
      sim.stepVote(inputs, prevInputs)

    check sim.voteState.cursor[0] == 1
