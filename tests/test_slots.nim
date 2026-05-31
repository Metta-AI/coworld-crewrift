import
  std/[json, os, unittest],
  zippy,
  crewrift/replays,
  crewrift/server,
  crewrift/sim

const
  GameDir = currentSourcePath.parentDir.parentDir
  ExampleSlotsJson = """{"tokens":[
    "0xBADA55_0",
    "0xBADA55_1",
    "0xBADA55_2",
    "0xBADA55_3",
    "0xBADA55_4",
    "0xBADA55_5",
    "0xBADA55_6",
    "0xBADA55_7"
  ],"players":[
    {"name":"player1"},
    {"name":"player2"},
    {"name":"player3"},
    {"name":"player4"},
    {"name":"player5"},
    {"name":"player6"},
    {"name":"player7"},
    {"name":"player8"}
  ],"slots":[
    {"role":"crew","color":"red"},
    {"role":"crew","color":"blue"},
    {"role":"crew","color":"green"},
    {"role":"crew","color":"yellow"},
    {"role":"crew","color":"lime"},
    {"role":"crew","color":"cyan"},
    {"role":"imposter","color":"pink"},
    {"role":"imposter","color":"orange"}
  ]}"""

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc roleFor(sim: SimServer, address: string): PlayerRole =
  ## Returns the role for one test player address.
  for player in sim.players:
    if player.address == address:
      return player.role
  raise newException(CrewriftError, "Missing test player " & address & ".")

proc addExamplePlayers(sim: var SimServer, count: int) =
  ## Adds configured example players from the first slot.
  for i in 0 ..< count:
    discard sim.addPlayer(
      "player" & $(i + 1),
      -1,
      "0xBADA55_" & $i
    )

proc writeRunnerJoin(
  writer: var ReplayWriter,
  sim: var SimServer,
  time: uint32,
  slot: int,
  token: string
) =
  ## Records one runner-style slot/token join after resolving the configured name.
  let identity = sim.config.configuredPlayerName(slot, token)
  let playerIndex = sim.addPlayer(identity, slot, token)
  let player = sim.players[playerIndex]
  writer.writeJoin(time, playerIndex, player.address, player.joinOrder, token)

suite "player slots":
  test "config parses example slots and tokens":
    var config = defaultGameConfig()
    config.update(ExampleSlotsJson)

    check config.slots.len == 8
    check config.slots[0].name == "player1"
    check config.slots[0].token == "0xBADA55_0"
    check config.slots[0].hasRole
    check config.slots[0].role == Crewmate
    check config.slots[0].hasColor
    check config.slots[0].color == PlayerColors[0]
    check config.slots[5].hasColor
    check config.slots[5].color == PlayerColors[3]
    check config.slots[7].name == "player8"
    check config.slots[7].hasRole
    check config.slots[7].role == Imposter
    check config.slots[7].color == PlayerColors[1]

    let serialized = parseJson(config.configJson())
    check serialized["tokens"].len == 8
    check serialized["tokens"][6].getStr() == "0xBADA55_6"
    check serialized["players"][0]["name"].getStr() == "player1"
    check serialized["players"][7]["name"].getStr() == "player8"
    check serialized["slots"].len == 8
    check not serialized["slots"][6].hasKey("name")
    check not serialized["slots"][6].hasKey("token")
    check serialized["slots"][5]["color"].getStr() == "light blue"

    var roundTrip = defaultGameConfig()
    roundTrip.update($serialized)
    check roundTrip.slots.len == 8
    check roundTrip.slots[7].role == Imposter
    check roundTrip.slots[7].color == PlayerColors[1]

  test "matching name and token assigns configured slot":
    var config = defaultGameConfig()
    config.update(ExampleSlotsJson)
    var sim = initCrewriftForTest(config)

    sim.addExamplePlayers(6)
    let playerIndex = sim.addPlayer("player7", -1, "0xBADA55_6")
    check sim.players[playerIndex].joinOrder == 6
    check sim.players[playerIndex].color == PlayerColors[4]

  test "trusted replay join uses configured name":
    var config = defaultGameConfig()
    config.update(ExampleSlotsJson)
    var sim = initCrewriftForTest(config)

    sim.addExamplePlayers(7)
    let playerIndex = sim.addPlayer("player8", trusted = true)
    check sim.players[playerIndex].joinOrder == 7
    check sim.players[playerIndex].color == PlayerColors[1]

  test "bad configured name or token is rejected":
    var config = defaultGameConfig()
    config.update(ExampleSlotsJson)
    var sim = initCrewriftForTest(config)

    expect CrewriftError:
      discard sim.addPlayer("player7", -1, "bad")
    expect CrewriftError:
      discard sim.addPlayer("intruder", -1, "0xBADA55_6")
    expect CrewriftError:
      discard sim.addPlayer("player7", 6, "bad")

  test "configured token can be checked before websocket upgrade":
    var config = defaultGameConfig()
    config.update("""{"tokens":["secret"]}""")

    check config.slots[0].name == ""
    check config.playerJoinAllowed("notsus", 0, "secret")
    check not config.playerJoinAllowed("notsus", 0, "")
    check not config.playerJoinAllowed("notsus", 0, "bad")
    check not config.playerJoinAllowed("notsus", -1, "bad")
    check config.playerJoinAllowed("browser", -1, "")
    check not config.playerJoinAllowed("notsus", MaxPlayers, "secret")
    check config.configuredPlayerName(0, "secret") == ""
    check config.configuredPlayerName(-1, "secret") == ""

  test "viewer routes reject player credential params":
    check not hasPlayerCredentialParams("", "", "")
    check hasPlayerCredentialParams("player1", "", "")
    check hasPlayerCredentialParams("", "0", "")
    check hasPlayerCredentialParams("", "", "secret")
    check hasPlayerCredentialParams("  player1  ", "", "")

  test "closed rosters require restricted slots":
    var config = defaultGameConfig()
    config.minPlayers = 1
    config.update("""{"tokens":["secret"],"closedRoster":true}""")

    check config.slots[0].name == ""
    check config.slots[0].token == "secret"
    check config.playerJoinAllowed("notsus", -1, "secret")
    check not config.playerJoinAllowed("notsus", -1, "bad")
    check not config.playerJoinAllowed("extra", -1, "")

    var nameOnly = defaultGameConfig()
    nameOnly.minPlayers = 1
    nameOnly.update("""{"players":[{"name":"Player1"}],"closedRoster":true}""")
    check nameOnly.playerJoinAllowed("Player1", -1, "")

    var unrestricted = defaultGameConfig()
    unrestricted.minPlayers = 1
    expect CrewriftError:
      unrestricted.update("""{"slots":[{}],"closedRoster":true}""")

  test "duplicate configured names and tokens are rejected":
    var config = defaultGameConfig()

    expect CrewriftError:
      config.update("""{"player_names":["same"]}""")
    expect CrewriftError:
      config.update("""{"players":[{"name":"same"},{"name":"same"}]}""")
    expect CrewriftError:
      config.update("""{"slots":[{"name":"same"}]}""")
    expect CrewriftError:
      config.update("""{"slots":[{"token":"same"},{"token":"same"}]}""")
    expect CrewriftError:
      config.update("""{"tokens":["same","same"]}""")
    expect CrewriftError:
      config.update("""{"tokens":["new"],"slots":[{"token":"old"}]}""")

  test "bad configured color is rejected":
    var config = defaultGameConfig()

    expect CrewriftError:
      config.update("""{"slots":[{"color":"ultraviolet"}]}""")

  test "configured crew role must use canonical crew spelling":
    var config = defaultGameConfig()

    expect CrewriftError:
      config.update("""{"slots":[{"role":"crewmate"}]}""")

  test "duplicate player names are rejected":
    let config = defaultGameConfig()
    var sim = initCrewriftForTest(config)

    discard sim.addPlayer("same-name")
    expect CrewriftError:
      discard sim.addPlayer("same-name")

  test "anonymous player names are unique":
    var nextIndex = 1

    check anonymousPlayerIdentity(nextIndex, []) == "Player1"
    check anonymousPlayerIdentity(nextIndex, ["Player2"]) == "Player3"
    check nextIndex == 4

  test "replay join stores name slot and token":
    let path = getTempDir() / "crewrift_slots_replay.bitreplay"
    if fileExists(path):
      removeFile(path)

    var writer = openReplayWriter(path, "{}")
    writer.writeJoin(12'u32, 0, "player1", -1, "")
    writer.writeJoin(24'u32, 1, "player2", 3, "0xBADA55")
    writer.closeReplayWriter()

    let replayBytes = readFile(path)
    let data = parseReplayBytes(replayBytes)
    check data.joins.len == 2
    check data.joins[0].name == "player1"
    check data.joins[0].slot == -1
    check data.joins[0].token == ""
    check data.joins[1].name == "player2"
    check data.joins[1].slot == 3
    check data.joins[1].token == "0xBADA55"

    let compressedData = parseReplayBytes(
      compress(replayBytes, dataFormat = dfZlib)
    )
    check compressedData.joins.len == 2
    check compressedData.joins[1].name == "player2"
    check compressedData.joins[1].slot == 3
    check compressedData.joins[1].token == "0xBADA55"

    removeFile(path)

  test "runner-style slots record configured policy names in replay joins":
    let path = getTempDir() / "crewrift_runner_slots_replay.bitreplay"
    if fileExists(path):
      removeFile(path)

    var config = defaultGameConfig()
    config.minPlayers = 2
    config.update("""{"tokens":["crew-token","imp-token"],
      "players":[{"name":"crew-policy:v3"},{"name":"imp-policy:v7"}],
      "slots":[
      {"role":"crew"},
      {"role":"imposter"}
    ],"closedRoster":true}""")
    var sim = initCrewriftForTest(config)
    var writer = openReplayWriter(path, config.configJson())

    writer.writeRunnerJoin(sim, 12'u32, 0, "crew-token")
    writer.writeRunnerJoin(sim, 24'u32, 1, "imp-token")
    writer.closeReplayWriter()

    let data = parseReplayBytes(readFile(path))
    check data.joins.len == 2
    check data.joins[0].name == "crew-policy:v3"
    check data.joins[0].slot == 0
    check data.joins[0].token == "crew-token"
    check data.joins[1].name == "imp-policy:v7"
    check data.joins[1].slot == 1
    check data.joins[1].token == "imp-token"

    removeFile(path)

  test "replay hash mismatch stops at recorded end":
    var replay = initReplayPlayer(ReplayData(
      gameName: GameName,
      gameVersion: GameVersion,
      configJson: "{}",
      joins: @[ReplayJoin(time: 0'u32, player: 0'u8, name: "player1", slot: -1, token: "")],
      hashes: @[
        ReplayHash(tick: 1'u32, hash: 0'u64),
        ReplayHash(tick: 3'u32, hash: 0'u64)
      ]
    ))
    var sim = initCrewriftForTest(defaultGameConfig())

    replay.stepReplay(sim)

    check sim.tickCount == 1
    check replay.playing
    check replay.hashValidationFailed

    replay.stepReplay(sim)
    check sim.tickCount == 2
    check replay.playing

    replay.stepReplay(sim)
    check sim.tickCount == 3
    check not replay.playing

  test "automatic slots wait behind restricted slots":
    var config = defaultGameConfig()
    config.update("""{"players":[{"name":"reserved"}],"slots":[{"token":"secret"}]}""")
    var sim = initCrewriftForTest(config)

    expect CrewriftError:
      discard sim.addPlayer("open")
    let reservedIndex = sim.addPlayer("reserved", -1, "secret")
    let playerIndex = sim.addPlayer("open")
    check sim.players[reservedIndex].joinOrder == 0
    check sim.players[playerIndex].joinOrder == 1

  test "automatic slots stay open for configured rosters by default":
    var config = defaultGameConfig()
    config.minPlayers = 2
    config.update("""{"tokens":["crew-token","imp-token"]}""")
    var sim = initCrewriftForTest(config)

    discard sim.addPlayer("crew", -1, "crew-token")
    discard sim.addPlayer("imp", -1, "imp-token")
    let extraIndex = sim.addPlayer("extra")
    check sim.players[extraIndex].joinOrder == 2

  test "automatic slots can use open token slots":
    var config = defaultGameConfig()
    config.minPlayers = 2
    config.update("""{"tokens":["crew-token","imp-token"]}""")
    var sim = initCrewriftForTest(config)

    let firstIndex = sim.addPlayer("browser")
    let secondIndex = sim.addPlayer("guest")
    check sim.players[firstIndex].joinOrder == 0
    check sim.players[secondIndex].joinOrder == 1

  test "automatic slots stop at an explicitly closed configured roster":
    var config = defaultGameConfig()
    config.minPlayers = 2
    config.update("""{"tokens":["crew-token","imp-token"],"closedRoster":true}""")
    var sim = initCrewriftForTest(config)

    discard sim.addPlayer("crew", -1, "crew-token")
    discard sim.addPlayer("imp", -1, "imp-token")
    check not sim.canAddPlayer()
    expect CrewriftError:
      discard sim.addPlayer("extra")

  test "closed configured roster rejects explicit slots outside roster":
    var config = defaultGameConfig()
    config.minPlayers = 2
    config.update("""{"tokens":["crew-token","imp-token"],"closedRoster":true}""")
    var sim = initCrewriftForTest(config)

    check not config.playerJoinAllowed("extra", 2, "")
    expect CrewriftError:
      discard sim.addPlayer("extra", 2)

  test "manual slot must match next player index":
    let config = defaultGameConfig()
    var sim = initCrewriftForTest(config)

    expect CrewriftError:
      discard sim.addPlayer("manual", 5)
    let manualIndex = sim.addPlayer("manual", 0)
    let autoIndex = sim.addPlayer("auto")
    check sim.players[manualIndex].joinOrder == 0
    check sim.players[autoIndex].joinOrder == 1

  test "configured roles override random roles":
    var config = defaultGameConfig()
    config.minPlayers = 2
    config.roleRevealTicks = 0
    config.tasksPerPlayer = 1
    config.update("""{"players":[{"name":"crew"},{"name":"imp"}],"slots":[
      {"token":"crew-token","role":"crew"},
      {"token":"imp-token","role":"imposter"}
    ]}""")
    var sim = initCrewriftForTest(config)

    discard sim.addPlayer("crew", -1, "crew-token")
    discard sim.addPlayer("imp", -1, "imp-token")
    sim.startGame()

    check sim.roleFor("imp") == Imposter
    check sim.roleFor("crew") == Crewmate
