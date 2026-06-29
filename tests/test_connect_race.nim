import
  std/[json, os, sets, unittest],
  bitworld/spriteprotocol,
  crewrift/sim

const
  GameDir = currentSourcePath.parentDir.parentDir
  ClosedRosterJson = """{"tokens":[
    "token-0","token-1","token-2","token-3"
  ],"closedRoster":true}"""

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc closedRosterConfig(): GameConfig =
  ## Returns a 4-seat closed roster with a short connect deadline.
  result = defaultGameConfig()
  result.minPlayers = 4
  result.startWaitTicks = 3
  result.roleRevealTicks = 0
  result.gameInfoTicks = 3
  result.tasksPerPlayer = 1
  result.connectTimeoutTicks = 5
  result.update(ClosedRosterJson)

proc connectTimeoutForSlot(resultsJson: string, slotIndex: int): int =
  ## Reads the connect_timeout flag for one slot from results JSON.
  parseJson(resultsJson)["connect_timeout"][slotIndex].getInt()

suite "connect race":
  test "explicit out-of-order slots all count toward the roster":
    # Reproduces concurrent connects landing on non-sequential seats. Each
    # accepted slot must become a registered player immediately, regardless of
    # the order the joins are admitted. Previously slot N could only join when
    # N == players.len, stranding out-of-order sockets in the pending state and
    # under-counting the lobby.
    var sim = initCrewriftForTest(closedRosterConfig())

    discard sim.addPlayer("Player4", 3, "token-3")
    discard sim.addPlayer("Player1", 0, "token-0")
    discard sim.addPlayer("Player3", 2, "token-2")
    discard sim.addPlayer("Player2", 1, "token-1")

    check sim.players.len == 4
    var seenSlots: HashSet[int]
    for player in sim.players:
      seenSlots.incl(player.joinOrder)
    check seenSlots == toHashSet([0, 1, 2, 3])
    # All seats are filled, so the lobby starts on the configured countdown
    # rather than waiting on the wall-clock connect deadline.
    var inputs = newSeq[InputState](sim.players.len)
    for _ in 0 ..< sim.config.startWaitTicks:
      check sim.phase == Lobby
      sim.step(inputs, inputs)
    check sim.phase != Lobby
    for slotIndex in 0 ..< 4:
      check connectTimeoutForSlot(sim.playerResultsJson(), slotIndex) == 0

  test "a slot with a live socket is never timed out":
    # Even when sockets have not yet been promoted into sim.players, a slot that
    # has an accepted /player socket must never be declared connect_timeout. The
    # server loop publishes the live-connected slots before each step; here we
    # mark every seat connected but register no players, then step past the
    # connect deadline. The match must not end as a connect-timeout draw.
    var sim = initCrewriftForTest(closedRosterConfig())
    sim.setLiveConnectedSlots([0, 1, 2, 3])

    var inputs: seq[InputState] = @[]
    for _ in 0 .. sim.config.connectTimeoutTicks + 2:
      sim.step(inputs, inputs)

    check sim.phase == Lobby
    check not sim.timeLimitReached
    for slotIndex in 0 ..< 4:
      check connectTimeoutForSlot(sim.playerResultsJson(), slotIndex) == 0

  test "a partially connected roster only times out the missing seats":
    # Slots 0 and 2 have live sockets (mid-handshake, not yet promoted) while
    # slots 1 and 3 never connected. After the deadline, only the seats with no
    # socket may be flagged; the live-socket seats must be preserved.
    var sim = initCrewriftForTest(closedRosterConfig())
    sim.setLiveConnectedSlots([0, 2])

    var inputs: seq[InputState] = @[]
    for _ in 0 .. sim.config.connectTimeoutTicks + 2:
      sim.step(inputs, inputs)

    check sim.phase == GameOver
    check sim.timeLimitReached
    let results = sim.playerResultsJson()
    check connectTimeoutForSlot(results, 0) == 0
    check connectTimeoutForSlot(results, 1) == 1
    check connectTimeoutForSlot(results, 2) == 0
    check connectTimeoutForSlot(results, 3) == 1

  test "a roster with no sockets still times out":
    # The timeout remains a true upper bound: a seat that genuinely never
    # established a socket is flagged after the deadline.
    var sim = initCrewriftForTest(closedRosterConfig())

    var inputs: seq[InputState] = @[]
    for _ in 0 .. sim.config.connectTimeoutTicks + 2:
      sim.step(inputs, inputs)

    check sim.phase == GameOver
    check sim.timeLimitReached
    let results = sim.playerResultsJson()
    for slotIndex in 0 ..< 4:
      check connectTimeoutForSlot(results, slotIndex) == 1
