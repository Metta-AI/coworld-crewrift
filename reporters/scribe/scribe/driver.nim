import
  std/options,
  bitworld/spriteprotocol,
  ../../../src/crewrift/[replays, sim],
  identity

type
  ReplayLeaveEvent* = object
    tick*: int
    player*: PlayerRef

  ReplayChatEvent* = object
    tick*: int
    speaker*: PlayerRef
    text*: string

  ReplayStep* = object
    tick*: int
    inputs*: seq[InputState]
    prevInputs*: seq[InputState]
    preStep*: SimServer
    postStep*: SimServer
    bodyLimit*: int
    leaves*: seq[ReplayLeaveEvent]
    chats*: seq[ReplayChatEvent]
    hashChecked*: bool
    hashMatched*: bool

  ReplayDriver* = object
    data*: ReplayData
    sim*: SimServer
    masks: seq[uint8]
    lastMasks: seq[uint8]
    joinIndex, leaveIndex, chatIndex, inputIndex, hashIndex: int
    allHashesMatched*: bool
    warnings*: seq[string]

  ReplayEvents = object
    leaves: seq[ReplayLeaveEvent]
    chats: seq[ReplayChatEvent]

proc replayMaxTick(data: ReplayData): int =
  if data.hashes.len == 0:
    0
  else:
    int(data.hashes[^1].tick)

proc initReplayDriver*(data: ReplayData, config: GameConfig): ReplayDriver =
  result.data = data
  result.sim = initSimServer(config)
  result.allHashesMatched = data.hashes.len > 0
  if data.hashes.len == 0:
    result.warnings.add("replay has no tick hashes")

proc ensurePlayer(driver: var ReplayDriver, player: int) =
  while driver.masks.len <= player:
    driver.masks.add(0)
    driver.lastMasks.add(0)

proc applyReplayEvents(driver: var ReplayDriver): ReplayEvents =
  let time = tickTime(driver.sim.tickCount)

  while driver.leaveIndex < driver.data.leaves.len and
      driver.data.leaves[driver.leaveIndex].time <= time:
    let leave = driver.data.leaves[driver.leaveIndex]
    let playerIndex = int(leave.player)
    if playerIndex < 0 or playerIndex >= driver.sim.players.len:
      raise newException(ReplayError, "Replay player leave is invalid")
    result.leaves.add ReplayLeaveEvent(
      tick: driver.sim.tickCount + 1,
      player: playerRefForPlayer(driver.sim.players[playerIndex])
    )
    driver.sim.removePlayerAt(playerIndex)
    if playerIndex < driver.masks.len:
      driver.masks.delete(playerIndex)
    if playerIndex < driver.lastMasks.len:
      driver.lastMasks.delete(playerIndex)
    inc driver.leaveIndex

  while driver.joinIndex < driver.data.joins.len and
      driver.data.joins[driver.joinIndex].time <= time:
    let join = driver.data.joins[driver.joinIndex]
    if int(join.player) != driver.sim.players.len:
      raise newException(ReplayError, "Replay player join order is invalid")
    discard driver.sim.addPlayer(join.name, join.slot, join.token, trusted = true)
    driver.ensurePlayer(int(join.player))
    inc driver.joinIndex

  while driver.inputIndex < driver.data.inputs.len and
      driver.data.inputs[driver.inputIndex].time <= time:
    let input = driver.data.inputs[driver.inputIndex]
    driver.ensurePlayer(int(input.player))
    driver.masks[int(input.player)] = input.keys
    inc driver.inputIndex

  while driver.chatIndex < driver.data.chats.len and
      driver.data.chats[driver.chatIndex].time <= time:
    let chat = driver.data.chats[driver.chatIndex]
    let playerIndex = int(chat.player)
    let speaker =
      if playerIndex >= 0 and playerIndex < driver.sim.players.len:
        playerRefForPlayer(driver.sim.players[playerIndex])
      else:
        InvalidPlayerRef
    if not speaker.isValid:
      driver.warnings.add(
        "replay chat references invalid player index " & $playerIndex &
          " at source tick " & $driver.sim.tickCount
      )
    result.chats.add ReplayChatEvent(
      tick: driver.sim.tickCount + 1,
      speaker: speaker,
      text: chat.message
    )
    driver.sim.addVotingChat(int(chat.player), chat.message)
    inc driver.chatIndex

proc buildPrevInputs(driver: var ReplayDriver, playerCount: int): seq[InputState] =
  result = newSeq[InputState](playerCount)
  for playerIndex in 0 ..< playerCount:
    driver.ensurePlayer(playerIndex)
    result[playerIndex] = decodeInputMask(driver.lastMasks[playerIndex])

proc buildInputs(driver: var ReplayDriver, playerCount: int): seq[InputState] =
  result = newSeq[InputState](playerCount)
  for playerIndex in 0 ..< playerCount:
    driver.ensurePlayer(playerIndex)
    result[playerIndex] = decodeInputMask(driver.masks[playerIndex])
    driver.lastMasks[playerIndex] = driver.masks[playerIndex]

proc checkReplayHash(driver: var ReplayDriver, step: var ReplayStep) =
  while driver.hashIndex < driver.data.hashes.len and
      int(driver.data.hashes[driver.hashIndex].tick) < driver.sim.tickCount:
    driver.warnings.add(
      "replay hash tick " & $driver.data.hashes[driver.hashIndex].tick &
        " is before simulated tick " & $driver.sim.tickCount
    )
    driver.allHashesMatched = false
    inc driver.hashIndex

  if driver.hashIndex >= driver.data.hashes.len:
    driver.warnings.add("replay has no hash for simulated tick " & $driver.sim.tickCount)
    driver.allHashesMatched = false
    return

  let expected = driver.data.hashes[driver.hashIndex]
  if int(expected.tick) > driver.sim.tickCount:
    return

  step.hashChecked = true
  let actual = driver.sim.gameHash()
  step.hashMatched = actual == expected.hash
  if not step.hashMatched:
    driver.warnings.add(
      "replay hash mismatch at tick " & $driver.sim.tickCount &
        ": expected " & $expected.hash & ", got " & $actual
    )
    driver.allHashesMatched = false
  inc driver.hashIndex

proc advance*(driver: var ReplayDriver): Option[ReplayStep] =
  if driver.data.hashes.len == 0:
    return none(ReplayStep)
  if driver.sim.tickCount >= driver.data.replayMaxTick():
    return none(ReplayStep)

  let replayEvents = driver.applyReplayEvents()
  let
    prevInputs = driver.buildPrevInputs(driver.sim.players.len)
    inputs = driver.buildInputs(driver.sim.players.len)
    preStep = driver.sim
    bodyLimit = preStep.bodies.len

  driver.sim.step(inputs, prevInputs)

  var step = ReplayStep(
    tick: driver.sim.tickCount,
    inputs: inputs,
    prevInputs: prevInputs,
    preStep: preStep,
    postStep: driver.sim,
    bodyLimit: bodyLimit,
    leaves: replayEvents.leaves,
    chats: replayEvents.chats
  )
  driver.checkReplayHash(step)
  some(step)
