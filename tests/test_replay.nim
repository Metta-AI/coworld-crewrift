import
  std/[json, os, unittest],
  bitworld/spriteprotocol,
  crewrift/replays,
  crewrift/sim

const
  GameDir = currentSourcePath.parentDir.parentDir
  NotsusReplayPath = GameDir / "tests" / "replays" / "notsus.bitreplay"

proc initReplaySim(data: ReplayData): SimServer =
  ## Initializes a replay simulation from the replay config JSON.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    let config = data.replayGameConfig()
    result = initSimServer(config)
    result.gameEventLoggingEnabled = false
  finally:
    setCurrentDir(previousDir)

proc withFastMode(data: ReplayData): ReplayData =
  ## Returns replay data with fast-mode config enabled.
  result = data
  var node =
    if data.configJson.len > 0:
      parseJson(data.configJson)
    else:
      newJObject()
  if node.kind != JObject:
    node = newJObject()
  node["fastMode"] = %true
  result.configJson = $node

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc debugReplayData(): ReplayData =
  ## Builds a tiny replay with one per-step debug sprite packet.
  let debugPacket = @[1'u8, 2, 3, 4]
  var
    config = defaultGameConfig()
    sim = initCrewriftForTest(config)
  discard sim.addPlayer("debugger", trusted = true)
  var inputs = newSeq[InputState](sim.players.len)
  sim.step(inputs, inputs)
  let firstHash = sim.gameHash()
  sim.step(inputs, inputs)
  let secondHash = sim.gameHash()

  ReplayData(
    gameName: GameName,
    gameVersion: GameVersion,
    configJson: config.configJson(),
    joins: @[ReplayJoin(
      time: 0'u32,
      player: 0'u8,
      name: "debugger",
      slot: -1,
      token: ""
    )],
    debugSprites: @[ReplayDebugSprite(
      time: 0'u32,
      player: 0'u8,
      packet: debugPacket
    )],
    hashes: @[
      ReplayHash(tick: 1'u32, hash: firstHash),
      ReplayHash(tick: 2'u32, hash: secondHash)
    ]
  )

suite "notsus replay":
  # NEVER IGNORE THE HASH.
  # PROVIDE A NEW REPLAY INSTEAD.
  test "sim serializes with flatty":
    let data = loadReplay(NotsusReplayPath)
    var
      sim = data.initReplaySim()
      replay = initReplayPlayer(data)
    replay.looping = false
    replay.mismatchQuit = true

    while sim.tickCount < 250:
      replay.stepReplay(sim)

    let
      hash = sim.gameHash()
      bytes = serializeReplaySim(sim)
      restored = deserializeReplaySim(bytes)

    check bytes.len > 0
    check restored.tickCount == sim.tickCount
    check restored.gameHash() == hash

  test "keyframed seek restores matching state":
    let data = loadReplay(NotsusReplayPath)
    var
      baseline = data.initReplaySim()
      baselineReplay = initReplayPlayer(data)
      sim = data.initReplaySim()
      replay = initReplayPlayer(data)
    baselineReplay.looping = false
    baselineReplay.mismatchQuit = true
    replay.looping = false
    replay.mismatchQuit = true

    let target = 600
    check data.hashes.len > 0
    check int(data.hashes[^1].tick) >= target
    while baseline.tickCount < target:
      baselineReplay.stepReplay(baseline)
    let hash = baseline.gameHash()

    replay.buildReplayKeyframes(sim)
    replay.seekReplay(sim, target)

    check replay.keyframes.len > 1
    check sim.tickCount == target
    check sim.gameHash() == hash

  test "hashes match":
    let data = loadReplay(NotsusReplayPath)
    var
      sim = data.initReplaySim()
      replay = initReplayPlayer(data)
    replay.looping = false
    replay.mismatchQuit = true

    check data.hashes.len > 0
    var checkedHashes = 0
    for expected in data.hashes:
      while sim.tickCount < int(expected.tick):
        doAssert replay.playing,
          "Replay stopped before hash tick " & $expected.tick
        replay.stepReplay(sim)
      check sim.tickCount == int(expected.tick)
      check sim.gameHash() == expected.hash
      inc checkedHashes

    check checkedHashes > 0
    check replay.hashIndex == checkedHashes
    check not replay.hashValidationFailed
    check replay.hashMismatchTick == -1

  test "fast mode config does not affect replay hashes":
    let data = loadReplay(NotsusReplayPath).withFastMode()
    var
      sim = data.initReplaySim()
      replay = initReplayPlayer(data)
    replay.looping = false
    replay.mismatchQuit = true

    while replay.playing:
      replay.stepReplay(sim)

    check replay.hashIndex == data.hashes.len
    check not replay.hashValidationFailed
    check replay.hashMismatchTick == -1

  test "debug sprites are exposed for the recorded replay step only":
    let data = debugReplayData()
    var
      sim = data.initReplaySim()
      replay = initReplayPlayer(data)
    replay.looping = false
    replay.mismatchQuit = true

    replay.stepReplay(sim)
    check replay.debugSprites.len == 1
    check replay.debugSprites[0] == @[1'u8, 2, 3, 4]

    replay.stepReplay(sim)
    check replay.debugSprites.len == 1
    check replay.debugSprites[0].len == 0
