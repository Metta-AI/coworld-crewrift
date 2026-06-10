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

proc stepInputs(
  sim: var SimServer,
  inputs: var seq[InputState],
  prevInputs: var seq[InputState]
) =
  ## Advances one tick and remembers the sampled input.
  sim.step(inputs, prevInputs)
  prevInputs = inputs

proc advanceMeetingCall(sim: var SimServer) =
  ## Advances the meeting-call interstitial into voting.
  var
    inputs = newSeq[InputState](sim.players.len)
    prevInputs = inputs
  for _ in 0 ..< MeetingCallTicks:
    sim.stepInputs(inputs, prevInputs)

proc addPlayers(sim: var SimServer, count: int) =
  ## Adds named test players to the simulation.
  for i in 0 ..< count:
    discard sim.addPlayer("player" & $(i + 1))

proc addUnfinishedTask(sim: var SimServer, playerIndex: int) =
  ## Gives one player an unfinished task to keep the game active.
  doAssert sim.tasks.len > 0
  sim.players[playerIndex].assignedTasks = @[0]
  sim.tasks[0].completed[playerIndex] = false

proc placePlayer(sim: var SimServer, playerIndex, x, y: int) =
  ## Moves one player and clears movement carry.
  sim.players[playerIndex].x = x
  sim.players[playerIndex].y = y
  sim.players[playerIndex].velX = 0
  sim.players[playerIndex].velY = 0
  sim.players[playerIndex].carryX = 0
  sim.players[playerIndex].carryY = 0

proc placePlayerAt(sim: var SimServer, playerIndex: int, rect: MapRect) =
  ## Moves one player inside a map rectangle.
  sim.placePlayer(playerIndex, rect.x, rect.y)

proc initActionSim(playerCount: int): SimServer =
  ## Builds a playing simulation with one imposter and living crewmates.
  var config = defaultGameConfig()
  config.killCooldownTicks = 0
  config.tasksPerPlayer = 1
  config.maxTicks = 0
  result = initCrewriftForTest(config)
  result.addPlayers(playerCount)
  result.phase = Playing
  result.players[0].role = Imposter
  result.players[0].killCooldown = 0
  for i in 1 ..< result.players.len:
    result.players[i].role = Crewmate
    result.addUnfinishedTask(i)

proc deadCrewmates(sim: SimServer): int =
  ## Counts dead crewmates.
  for player in sim.players:
    if player.role == Crewmate and not player.alive:
      inc result

proc nextVentIndex(sim: SimServer, ventIndex: int): int =
  ## Returns the next vent destination index used by tryVent.
  let vent = sim.vents[ventIndex]
  for i in 0 ..< sim.vents.len:
    if i == ventIndex:
      continue
    if sim.vents[i].group == vent.group and
        sim.vents[i].groupIndex == vent.groupIndex + 1:
      return i
  for i in 0 ..< sim.vents.len:
    if sim.vents[i].group == vent.group and sim.vents[i].groupIndex == 1:
      return i
  -1

proc placePlayerAtVent(sim: var SimServer, playerIndex, ventIndex: int) =
  ## Moves one player to the center of a vent.
  let vent = sim.vents[ventIndex]
  sim.placePlayer(
    playerIndex,
    vent.x + vent.w div 2 - CollisionW div 2,
    vent.y + vent.h div 2 - CollisionH div 2
  )

proc playerVentPosition(sim: SimServer, ventIndex: int): tuple[x, y: int] =
  ## Returns the player position produced by venting to one vent.
  let vent = sim.vents[ventIndex]
  (
    x: vent.x + vent.w div 2 - CollisionW div 2,
    y: vent.y + vent.h div 2 - CollisionH div 2
  )

suite "discrete button input":
  test "kill only fires on a fresh attack press":
    var sim = initActionSim(4)
    for i in 0 ..< sim.players.len:
      sim.placePlayer(i, 100, 100)

    var
      inputs = newSeq[InputState](sim.players.len)
      prevInputs = inputs

    inputs[0].attack = true
    sim.stepInputs(inputs, prevInputs)
    check sim.deadCrewmates() == 1

    sim.players[0].killCooldown = 0
    sim.stepInputs(inputs, prevInputs)
    check sim.deadCrewmates() == 1

    inputs[0].attack = false
    sim.stepInputs(inputs, prevInputs)
    sim.bodies.setLen(0)
    inputs[0].attack = true
    sim.stepInputs(inputs, prevInputs)
    check sim.deadCrewmates() == 2

  test "vent only fires on a fresh B press":
    var sim = initActionSim(3)
    let
      sourceVent = 0
      destinationVent = sim.nextVentIndex(sourceVent)
    require destinationVent >= 0
    sim.placePlayerAtVent(0, sourceVent)
    let sourcePos = (x: sim.players[0].x, y: sim.players[0].y)
    let destinationPos = sim.playerVentPosition(destinationVent)

    var
      inputs = newSeq[InputState](sim.players.len)
      prevInputs = inputs

    inputs[0].b = true
    prevInputs[0].b = true
    sim.stepInputs(inputs, prevInputs)
    check (sim.players[0].x, sim.players[0].y) == sourcePos

    inputs[0].b = false
    sim.stepInputs(inputs, prevInputs)
    inputs[0].b = true
    sim.stepInputs(inputs, prevInputs)
    check (sim.players[0].x, sim.players[0].y) == destinationPos

    sim.players[0].ventCooldown = 0
    sim.stepInputs(inputs, prevInputs)
    check (sim.players[0].x, sim.players[0].y) == destinationPos

  test "emergency button requires a fresh attack press":
    var sim = initActionSim(3)
    sim.players[0].role = Crewmate
    sim.players[1].role = Imposter
    sim.players[1].killCooldown = 0
    sim.addUnfinishedTask(0)
    sim.placePlayerAt(0, sim.gameMap.button)

    var
      inputs = newSeq[InputState](sim.players.len)
      prevInputs = inputs

    inputs[0].attack = true
    prevInputs[0].attack = true
    sim.stepInputs(inputs, prevInputs)
    check sim.phase == Playing
    check sim.players[0].buttonCallsUsed == 0

    inputs[0].attack = false
    sim.stepInputs(inputs, prevInputs)
    inputs[0].attack = true
    sim.stepInputs(inputs, prevInputs)
    check sim.phase == MeetingCall
    check sim.voteState.callKind == VoteCalledButton
    check sim.voteState.callerIndex == 0
    check sim.voteState.callTimer == MeetingCallTicks
    check sim.players[0].buttonCallsUsed == 1
    sim.advanceMeetingCall()
    check sim.phase == Voting

  test "body report requires a fresh attack press":
    var sim = initActionSim(3)
    sim.players[0].role = Crewmate
    sim.players[1].role = Imposter
    sim.players[1].killCooldown = 0
    sim.addUnfinishedTask(0)
    sim.placePlayer(0, 100, 100)
    sim.bodies.add Body(
      x: 100,
      y: 100,
      color: sim.players[2].color,
      slotId: sim.players[2].joinOrder
    )

    var
      inputs = newSeq[InputState](sim.players.len)
      prevInputs = inputs

    inputs[0].attack = true
    prevInputs[0].attack = true
    sim.stepInputs(inputs, prevInputs)
    check sim.phase == Playing

    inputs[0].attack = false
    sim.stepInputs(inputs, prevInputs)
    inputs[0].attack = true
    sim.stepInputs(inputs, prevInputs)
    check sim.phase == MeetingCall
    check sim.voteState.callKind == VoteCalledBody
    check sim.voteState.callerIndex == 0
    check sim.voteState.callTimer == MeetingCallTicks
    sim.advanceMeetingCall()
    check sim.phase == Voting

  test "vote cast requires a fresh attack press":
    var sim = initActionSim(3)
    sim.startVote()
    sim.advanceMeetingCall()

    var
      inputs = newSeq[InputState](sim.players.len)
      prevInputs = inputs

    inputs[0].attack = true
    prevInputs[0].attack = true
    sim.stepInputs(inputs, prevInputs)
    check sim.voteState.votes[0] == -1

    inputs[0].attack = false
    sim.stepInputs(inputs, prevInputs)
    inputs[0].attack = true
    sim.stepInputs(inputs, prevInputs)
    check sim.voteState.votes[0] == sim.voteState.cursor[0]

  test "tasks still progress while attack is held":
    var config = defaultGameConfig()
    config.taskCompleteTicks = 2
    config.maxTicks = 0
    var sim = initCrewriftForTest(config)
    let playerIndex = sim.addPlayer("worker")
    sim.phase = Playing
    sim.players[playerIndex].role = Crewmate
    sim.addUnfinishedTask(playerIndex)
    let taskIndex = sim.players[playerIndex].assignedTasks[0]
    sim.placePlayerAt(playerIndex, MapRect(
      x: sim.tasks[taskIndex].x,
      y: sim.tasks[taskIndex].y,
      w: sim.tasks[taskIndex].w,
      h: sim.tasks[taskIndex].h
    ))

    var
      inputs = newSeq[InputState](sim.players.len)
      prevInputs = inputs
    inputs[playerIndex].attack = true

    sim.stepInputs(inputs, prevInputs)
    check not sim.tasks[taskIndex].completed[playerIndex]
    sim.stepInputs(inputs, prevInputs)
    check sim.tasks[taskIndex].completed[playerIndex]
