import
  std/os,
  bitworld/runtime,
  crewrift/inputs,
  crewrift/sim,
  crewrift/server

proc limitText(value: int): string =
  ## Returns a readable text value for a numeric limit.
  if value > 0:
    $value
  else:
    "infinite"

proc echoStartupConfig(
  config: GameConfig,
  runtimeConfig: RuntimeConfig
) =
  ## Prints the effective startup config without token secrets.
  echo "Crewrift config: host=", runtimeConfig.host,
    " port=", runtimeConfig.port,
    " seed=", config.seed,
    " speed=", config.speed, "x",
    " minPlayers=", config.minPlayers,
    " slots=", config.slots.len,
    " maxTicks=", config.maxTicks.limitText(),
    " maxGames=", config.maxGames.limitText(),
    " map=", config.mapPath

when isMainModule:
  let
    runtimeConfig = readRuntimeConfig(readGameInput)
    localReplayPath =
      if runtimeConfig.replayUri.len > 0:
        getTempDir() / ("crewrift-replay-" & $getCurrentProcessId() &
          ".bitreplay")
      else:
        ""

  var config = defaultGameConfig()
  config.update(runtimeConfig.config)
  if not runtimeConfig.replayMode:
    config.resolveRandomSeed()
  config.echoStartupConfig(runtimeConfig)
  echo "Using map file: " & config.mapPath

  let loadReplayPath =
    if runtimeConfig.replayMode:
      let path = getTempDir() / ("crewrift-load-replay-" &
        $getCurrentProcessId() & ".bitreplay")
      writeFile(path, runtimeConfig.replay)
      path
    else:
      ""

  echo "starting crewrift on ", runtimeConfig.host, ":", runtimeConfig.port
  runServerLoop(
    runtimeConfig.host,
    runtimeConfig.port,
    config,
    localReplayPath,
    loadReplayPath,
    "",
    runtimeConfig
  )
