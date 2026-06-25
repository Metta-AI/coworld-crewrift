import
  std/[json, os, unittest],
  zippy,
  bitworld/spriteprotocol,
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
    {"role":"crew","color":"pink"},
    {"role":"crew","color":"orange"},
    {"role":"crew","color":"yellow"},
    {"role":"imposter","color":"purple"},
    {"role":"imposter","color":"cyan"}
  ]}"""
  AllPlayerColorsJson = """{"slots":[
    {"color":"red"},
    {"color":"blue"},
    {"color":"green"},
    {"color":"pink"},
    {"color":"orange"},
    {"color":"yellow"},
    {"color":"purple"},
    {"color":"cyan"},
    {"color":"lime"},
    {"color":"brown"},
    {"color":"beige"},
    {"color":"navy"},
    {"color":"teal"},
    {"color":"rose"},
    {"color":"maroon"},
    {"color":"gray"}
  ]}"""

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc advanceMeetingCall(sim: var SimServer) =
  ## Advances the meeting-call interstitial into voting.
  var
    inputs = newSeq[InputState](sim.players.len)
    prevInputs = inputs
  for _ in 0 ..< MeetingCallTicks:
    sim.step(inputs, prevInputs)
    prevInputs = inputs

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
  test "replay player autoplays and loops by default":
    let replay = initReplayPlayer(ReplayData())

    check replay.playing
    check replay.looping

  test "finite server waits through final game over screen":
    var config = defaultGameConfig()
    config.maxGames = 1

    check not config.finalGameQuitReady(0, Playing, 0)
    check not config.finalGameQuitReady(1, GameOver, 2)
    check config.finalGameQuitReady(1, GameOver, 1)
    check config.finalGameQuitReady(1, Lobby, 0)

  test "player ready packet is recognized":
    check isPlayerReadyPacket("" & char(0x85))
    check not isPlayerReadyPacket("")
    check not isPlayerReadyPacket("" & char(0x84))
    check not isPlayerReadyPacket("" & char(0x85) & char(0))

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
    check config.slots[5].color == PlayerColors[5]
    check config.slots[7].name == "player8"
    check config.slots[7].hasRole
    check config.slots[7].role == Imposter
    check config.slots[7].color == PlayerColors[7]

    let serialized = parseJson(config.configJson())
    check serialized["tokens"].len == 8
    check serialized["tokens"][6].getStr() == "0xBADA55_6"
    check serialized["players"][0]["name"].getStr() == "player1"
    check serialized["players"][7]["name"].getStr() == "player8"
    check serialized["slots"].len == 8
    check not serialized["slots"][6].hasKey("name")
    check not serialized["slots"][6].hasKey("token")
    check serialized["slots"][5]["color"].getStr() == "yellow"

    var roundTrip = defaultGameConfig()
    roundTrip.update($serialized)
    check roundTrip.slots.len == 8
    check roundTrip.slots[7].role == Imposter
    check roundTrip.slots[7].color == PlayerColors[7]

  test "config parses all player colors":
    var config = defaultGameConfig()
    config.update(AllPlayerColorsJson)

    check PlayerColors.len == PlayerColorNames.len
    check PlayerColors.len == PlayerColorPalette.len
    check config.slots.len == PlayerColors.len
    for i in 0 ..< PlayerColors.len:
      check config.slots[i].hasColor
      check config.slots[i].color == PlayerColors[i]

  test "player palette does not replace shared palette":
    loadPalette()
    let expectedPalette = Palette

    discard initCrewriftForTest(defaultGameConfig())

    check Palette == expectedPalette

  test "matching name and token assigns configured slot":
    var config = defaultGameConfig()
    config.update(ExampleSlotsJson)
    var sim = initCrewriftForTest(config)

    sim.addExamplePlayers(6)
    let playerIndex = sim.addPlayer("player7", -1, "0xBADA55_6")
    check sim.players[playerIndex].joinOrder == 6
    check sim.players[playerIndex].color == PlayerColors[6]

  test "trusted replay join uses configured name":
    var config = defaultGameConfig()
    config.update(ExampleSlotsJson)
    var sim = initCrewriftForTest(config)

    sim.addExamplePlayers(7)
    let playerIndex = sim.addPlayer("player8", trusted = true)
    check sim.players[playerIndex].joinOrder == 7
    check sim.players[playerIndex].color == PlayerColors[7]

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

    check config.slots[0].name == "Player1"
    check config.slots[0].token == "secret"
    check config.playerJoinAllowed("Player1", -1, "secret")
    check not config.playerJoinAllowed("Player1", -1, "bad")
    check not config.playerJoinAllowed("intruder", -1, "secret")
    check not config.playerJoinAllowed("extra", -1, "")

    var missingToken = defaultGameConfig()
    missingToken.minPlayers = 1
    expect CrewriftError:
      missingToken.update("""{"players":[{"name":"Player1"}],"closedRoster":true}""")

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
    writer.writeChat(36'u32, 1, "body in engine")
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
    check data.chats.len == 1
    check data.chats[0].time == 36'u32
    check data.chats[0].player == 1'u8
    check data.chats[0].message == "body in engine"

    let compressedData = parseReplayBytes(
      compress(replayBytes, dataFormat = dfZlib)
    )
    check compressedData.joins.len == 2
    check compressedData.joins[1].name == "player2"
    check compressedData.joins[1].slot == 3
    check compressedData.joins[1].token == "0xBADA55"
    check compressedData.chats[0].message == "body in engine"

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

  test "replay chat is applied before hash validation":
    var liveSim = initCrewriftForTest(defaultGameConfig())
    discard liveSim.addPlayer("player1")
    liveSim.startVote()
    liveSim.advanceMeetingCall()
    liveSim.addVotingChat(0, "hello")
    liveSim.step(@[InputState()], @[InputState()])
    let
      expectedTick = liveSim.tickCount
      expectedChatTick = expectedTick - 1
      expectedHash = liveSim.gameHash()

    var replaySim = initCrewriftForTest(defaultGameConfig())
    discard replaySim.addPlayer("player1")
    replaySim.startVote()
    replaySim.advanceMeetingCall()
    var replay = initReplayPlayer(ReplayData(
      gameName: GameName,
      gameVersion: GameVersion,
      configJson: "{}",
      chats: @[
        ReplayChat(
          time: tickTime(expectedChatTick),
          player: 0'u8,
          message: "hello"
        )
      ],
      hashes: @[
        ReplayHash(
          tick: uint32(expectedTick),
          hash: expectedHash
        )
      ]
    ))

    replay.stepReplay(replaySim)

    check replaySim.tickCount == expectedTick
    check replaySim.chatMessages.len == 1
    check replaySim.players[0].lastChatTick == expectedChatTick
    check not replay.hashValidationFailed
    check replay.hashIndex == 1

  test "replay hash mismatch marks mismatch and stops at recorded end":
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
    check replay.hashMismatchTick == 1

    replay.stepReplay(sim)
    check sim.tickCount == 2
    check replay.playing

    replay.stepReplay(sim)
    check sim.tickCount == 3
    check not replay.playing

  test "replay hash mismatch quit raises":
    var replay = initReplayPlayer(ReplayData(
      gameName: GameName,
      gameVersion: GameVersion,
      configJson: "{}",
      joins: @[ReplayJoin(time: 0'u32, player: 0'u8, name: "player1", slot: -1, token: "")],
      hashes: @[ReplayHash(tick: 1'u32, hash: 0'u64)]
    ))
    var sim = initCrewriftForTest(defaultGameConfig())
    replay.mismatchQuit = true

    expect ReplayError:
      replay.stepReplay(sim)

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

  test "automatic slots wait behind token slots":
    var config = defaultGameConfig()
    config.minPlayers = 2
    config.update("""{"tokens":["crew-token","imp-token"]}""")
    var sim = initCrewriftForTest(config)

    expect CrewriftError:
      discard sim.addPlayer("browser")
    let firstIndex = sim.addPlayer("crew", -1, "crew-token")
    let secondIndex = sim.addPlayer("imp", -1, "imp-token")
    check sim.players[firstIndex].joinOrder == 0
    check sim.players[secondIndex].joinOrder == 1

  test "automatic slots stop at an explicitly closed configured roster":
    var config = defaultGameConfig()
    config.minPlayers = 2
    config.update("""{"tokens":["crew-token","imp-token"],"closedRoster":true}""")
    var sim = initCrewriftForTest(config)

    discard sim.addPlayer("Player1", -1, "crew-token")
    discard sim.addPlayer("Player2", -1, "imp-token")
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
