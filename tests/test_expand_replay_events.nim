import
  std/[json, os, unittest],
  bitworld/spriteprotocol,
  ../tools/expand_replay,
  crewrift/replays,
  crewrift/sim

const
  GameDir = currentSourcePath.parentDir.parentDir
  NotsusReplayPath = GameDir / "tests" / "replays" / "notsus.bitreplay"

proc hasKey(rows: openArray[JsonNode], key: string): bool =
  for row in rows:
    if row["key"].getStr() == key:
      return true

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc manifestReplayData(): ReplayData =
  ## Builds a replay whose configured imposter role is assigned at game start.
  var config = defaultGameConfig()
  config.seed = 12345
  config.minPlayers = 2
  config.imposterCount = 1
  config.autoImposterCount = false
  config.startWaitTicks = 0
  config.gameInfoTicks = 0
  config.roleRevealTicks = 0
  config.tasksPerPlayer = 1
  config.update("""{"tokens":["crew-token","imp-token"],
    "players":[{"name":"crew-policy:v1"},{"name":"imp-policy:v1"}],
    "slots":[
      {"role":"crew"},
      {"role":"imposter"}
    ]}""")

  var sim = initCrewriftForTest(config)
  sim.gameEventLoggingEnabled = false
  discard sim.addPlayer("crew-policy:v1", 0, "crew-token")
  discard sim.addPlayer("imp-policy:v1", 1, "imp-token")
  let inputs = newSeq[InputState](sim.players.len)
  sim.step(inputs, inputs)

  ReplayData(
    gameName: GameName,
    gameVersion: GameVersion,
    configJson: config.configJson(),
    joins: @[
      ReplayJoin(
        time: 0'u32,
        player: 0'u8,
        name: "crew-policy:v1",
        slot: 0,
        token: "crew-token"
      ),
      ReplayJoin(
        time: 0'u32,
        player: 1'u8,
        name: "imp-policy:v1",
        slot: 1,
        token: "imp-token"
      )
    ],
    hashes: @[ReplayHash(tick: 1'u32, hash: sim.gameHash())]
  )

suite "expand replay event trace":
  test "emits standard metadata and state rows":
    let
      data = loadReplay(NotsusReplayPath)
      timeline = expandReplayTimeline(data, snapshotEvery = 64)

    check timeline.traceRows.len > 0
    check timeline.traceRows.hasKey("episode_metadata")
    check timeline.traceRows.hasKey("map_geometry")
    check timeline.traceRows.hasKey("player_state")
    check timeline.traceRows[0]["ts"].getInt() == 0
    check timeline.traceRows[0]["player"].getInt() == -1
    check timeline.traceRows[0]["value"]["schema_version"].getStr() ==
      "crewrift-events/v1"

  test "player manifest uses assigned roles":
    let timeline = expandReplayTimeline(manifestReplayData(), snapshotEvery = 1)
    var
      crewManifest: JsonNode
      imposterManifest: JsonNode

    for row in timeline.traceRows:
      if row["key"].getStr() == "player_manifest":
        check row["ts"].getInt() == 1
        case row["player"].getInt()
        of 0:
          crewManifest = row
        of 1:
          imposterManifest = row
        else:
          discard

    check crewManifest != nil
    check imposterManifest != nil
    if crewManifest != nil:
      check crewManifest["value"]["role"].getStr() == "crew"
      check crewManifest["value"]["assigned_tasks"].len == 1
    if imposterManifest != nil:
      check imposterManifest["value"]["role"].getStr() == "imposter"
      check imposterManifest["value"]["assigned_tasks"].len == 0
