import
  std/[os, unittest],
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
