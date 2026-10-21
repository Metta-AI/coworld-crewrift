import
  std/[json, os, unittest],
  bitworld/spriteprotocol,
  crewrift/replays,
  crewrift/sim

const GameDir = currentSourcePath.parentDir.parentDir

proc rosterConfig(): GameConfig =
  result = defaultGameConfig()
  result.update("""{"tokens":["fixture-0","fixture-1","fixture-2"],
    "closedRoster":true,"minPlayers":3,"imposterCount":1,"seed":41001,"startWaitTicks":0}""")

proc initAtGame(config: GameConfig): SimServer =
  let previous = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previous)

proc initAtGame(data: ReplayData): SimServer =
  let previous = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = data.initReplaySimulation()
  finally:
    setCurrentDir(previous)

proc admit(sim: var SimServer, slot: int): int =
  sim.addPlayer("Player" & $(slot + 1), slot, "fixture-" & $slot)

suite "immutable closed roster indices":
  test "offline seats do not count as joined or start ready":
    var config = rosterConfig()
    config.minPlayers = 1
    var sim = initAtGame(config)
    check sim.players.len == 3
    check sim.lobbyPlayerCount() == 0
    check sim.canAddPlayer()
    for i, player in sim.players:
      check player.joinOrder == i
      check not player.admitted
      check not player.connected
      check not player.alive
    check sim.admit(2) == 2
    let inputs = newSeq[InputState](3)
    sim.step(inputs, inputs)
    check sim.phase == Lobby
    check sim.lobbyPlayerCount() == 1
    check not sim.lobbyIsStarting()
    check sim.admit(0) == 0
    sim.step(inputs, inputs)
    check sim.phase == Lobby
    check sim.admit(1) == 1
    sim.step(inputs, inputs)
    check sim.phase != Lobby
    check sim.lobbyPlayerCount() == 3

  test "seeded roles and assignments do not depend on arrival order":
    var forward = initAtGame(rosterConfig())
    var reverse = initAtGame(rosterConfig())
    for slot in [0, 1, 2]:
      check forward.admit(slot) == slot
    for slot in [2, 1, 0]:
      check reverse.admit(slot) == slot
    forward.startGame()
    reverse.startGame()
    for slot in 0 ..< 3:
      check forward.players[slot].role == reverse.players[slot].role
      check forward.players[slot].assignedTasks == reverse.players[slot].assignedTasks
      check forward.players[slot].address == reverse.players[slot].address
      check forward.players[slot].color == reverse.players[slot].color

  test "lobby disconnect and reconnect retain every other engine index":
    var sim = initAtGame(rosterConfig())
    check sim.admit(2) == 2
    check sim.admit(0) == 0
    sim.removePlayerAt(0)
    check sim.players.len == 3
    check sim.lobbyPlayerCount() == 1
    check sim.players[2].address == "Player3"
    check not sim.players[0].alive
    check sim.reconnectPlayerIndex("Player1", "fixture-0", 0) == 0
    sim.markPlayerConnected(0)
    check sim.lobbyPlayerCount() == 2
    check sim.players[0].alive
    check sim.admit(1) == 1

  test "permanent removal cannot revive a retired gameplay seat":
    var config = rosterConfig()
    config.disconnectTimeoutTicks = 0
    var sim = initAtGame(config)
    for slot in 0 ..< 3:
      discard sim.admit(slot)
    sim.startGame()
    let remaining = sim.totalTasksRemaining()
    let retiredTasks = sim.players[0].assignedTasks.len
    sim.removePlayerAt(0)
    check sim.players.len == 3
    check not sim.players[0].alive
    check not sim.players[0].admitted
    check sim.totalTasksRemaining() == remaining - retiredTasks
    check sim.reconnectPlayerIndex("Player1", "fixture-0", 0) == -1
    check sim.players[2].address == "Player3"
    check sim.players[2].joinOrder == 2

  test "last retired imposter is absent but a dead admitted imposter remains":
    var config = rosterConfig()
    config.roleRevealTicks = 0
    for slot in 0 ..< 3:
      config.slots[slot].hasRole = true
      config.slots[slot].role = if slot == 2: Imposter else: Crewmate
    var retired = initAtGame(config)
    var dead = initAtGame(config)
    var grace = initAtGame(config)
    for slot in 0 ..< 3:
      discard retired.admit(slot)
      discard dead.admit(slot)
      discard grace.admit(slot)
    retired.startGame()
    dead.startGame()
    grace.startGame()
    retired.removePlayerAt(2)
    retired.checkWinCondition()
    check retired.phase == Playing
    dead.players[2].alive = false
    dead.checkWinCondition()
    check dead.phase == GameOver
    check dead.winner == Crewmate
    grace.markPlayerDisconnected(2)
    grace.checkWinCondition()
    check grace.phase == Playing
    check grace.admittedPlayerCount() == 3

  test "all retired finite seats abort as a replayed draw without a phantom win":
    var config = rosterConfig()
    config.gameInfoTicks = 0
    config.roleRevealTicks = 0
    config.maxGames = 1
    config.disconnectTimeoutTicks = 0
    var live = initAtGame(config)
    let path = getTempDir() / "crewrift_retired_roster_draw.bitreplay"
    var writer = openReplayWriter(path, config.configJson())
    for slot in 0 ..< 3:
      let index = live.admit(slot)
      writer.writeJoin(0'u32, index, live.players[index].address, slot, "fixture-" & $slot)
    let inputs = newSeq[InputState](3)
    live.step(inputs, inputs)
    writer.writeHash(uint32(live.tickCount), live.gameHash())
    for slot in 0 ..< 3:
      writer.writeLeave(tickTime(live.tickCount), slot)
      live.removePlayerAt(slot)
    live.checkWinCondition()
    check live.phase == Playing
    check live.shouldAbortFiniteMatch()
    live.finishGame(Crewmate, timeLimitReached = true)
    live.step(inputs, inputs)
    writer.writeHash(uint32(live.tickCount), live.gameHash())
    writer.closeReplayWriter()
    let data = loadReplay(path)
    var decoded = initAtGame(data)
    var replay = initReplayPlayer(data)
    replay.mismatchQuit = true
    while replay.hashIndex < data.hashes.len:
      replay.stepReplay(decoded)
    check not replay.hashValidationFailed
    check decoded.phase == GameOver
    check decoded.timeLimitReached
    check decoded.admittedPlayerCount() == 0
    removeFile(path)

  test "last retired imposter stays absent in the current replay":
    var config = rosterConfig()
    config.gameInfoTicks = 0
    config.roleRevealTicks = 0
    config.disconnectTimeoutTicks = 0
    for slot in 0 ..< 3:
      config.slots[slot].hasRole = true
      config.slots[slot].role = if slot == 2: Imposter else: Crewmate
    var live = initAtGame(config)
    let path = getTempDir() / "crewrift_retired_imposter.bitreplay"
    var writer = openReplayWriter(path, config.configJson())
    for slot in 0 ..< 3:
      let index = live.admit(slot)
      writer.writeJoin(0'u32, index, live.players[index].address, slot, "fixture-" & $slot)
    let inputs = newSeq[InputState](3)
    live.step(inputs, inputs)
    writer.writeHash(uint32(live.tickCount), live.gameHash())
    writer.writeLeave(tickTime(live.tickCount), 2)
    live.removePlayerAt(2)
    live.step(inputs, inputs)
    writer.writeHash(uint32(live.tickCount), live.gameHash())
    writer.closeReplayWriter()
    let data = loadReplay(path)
    var decoded = initAtGame(data)
    var replay = initReplayPlayer(data)
    replay.mismatchQuit = true
    while replay.hashIndex < data.hashes.len:
      replay.stepReplay(decoded)
    check not replay.hashValidationFailed
    check decoded.phase == Playing
    check decoded.admittedPlayerCount() == 2
    check not decoded.players[2].admitted
    removeFile(path)

  test "leave and reconnect clear only their replay input and debug ownership":
    var config = rosterConfig()
    config.startWaitTicks = 1000
    var live = initAtGame(config)
    let path = getTempDir() / "crewrift_roster_input_ownership.bitreplay"
    var writer = openReplayWriter(path, config.configJson())
    for slot in [2, 0, 1]:
      let index = live.admit(slot)
      writer.writeJoin(0'u32, index, live.players[index].address, slot, "fixture-" & $slot)
    writer.writeInput(ReplayInput(time: 0'u32, player: 0'u8, keys: ButtonRight))
    writer.writeInput(ReplayInput(time: 0'u32, player: 2'u8, keys: ButtonDown))
    writer.writeDebugSprite(0'u32, 0, @[1'u8, 2, 3, 4])
    let inputs = newSeq[InputState](3)
    live.step(inputs, inputs)
    writer.writeHash(uint32(live.tickCount), live.gameHash())
    writer.writeLeave(tickTime(live.tickCount), 0)
    live.removePlayerAt(0)
    live.step(inputs, inputs)
    writer.writeHash(uint32(live.tickCount), live.gameHash())
    live.markPlayerConnected(0)
    writer.writeJoin(tickTime(live.tickCount), 0, live.players[0].address, 0, "fixture-0")
    live.step(inputs, inputs)
    writer.writeHash(uint32(live.tickCount), live.gameHash())
    writer.closeReplayWriter()
    let data = loadReplay(path)
    var decoded = initAtGame(data)
    var replay = initReplayPlayer(data)
    replay.mismatchQuit = true
    replay.stepReplay(decoded)
    check replay.masks[0] == ButtonRight
    check replay.lastAppliedMasks[0] == ButtonRight
    replay.stepReplay(decoded)
    check replay.masks[0] == 0
    check replay.pressedMasks[0] == 0
    check replay.lastAppliedMasks[0] == 0
    check replay.debugSprites[0].len == 0
    check replay.masks[2] == ButtonDown
    replay.stepReplay(decoded)
    check decoded.players[0].connected
    check replay.masks[0] == 0
    check replay.masks[2] == ButtonDown
    check not replay.hashValidationFailed
    removeFile(path)

  test "writer marker binds replay joins to configured indices":
    let path = getTempDir() / "crewrift_configured_roster_indices.bitreplay"
    var live = initAtGame(rosterConfig())
    var writer = openReplayWriter(path, live.config.configJson())
    for slot in [2, 0, 1]:
      let index = live.admit(slot)
      writer.writeJoin(0'u32, index, live.players[index].address, slot, "fixture-" & $slot)
    let inputs = newSeq[InputState](3)
    live.step(inputs, inputs)
    writer.writeHash(uint32(live.tickCount), live.gameHash())
    writer.closeReplayWriter()
    let data = loadReplay(path)
    check parseJson(data.configJson)["closedRosterSeatAllocation"].getStr() == "configured_slots"
    var decoded = initAtGame(data)
    var replay = initReplayPlayer(data)
    replay.stepReplay(decoded)
    check not replay.hashValidationFailed
    check decoded.players[2].address == "Player3"
    check decoded.players[0].address == "Player1"
    check decoded.players[1].address == "Player2"
    removeFile(path)
