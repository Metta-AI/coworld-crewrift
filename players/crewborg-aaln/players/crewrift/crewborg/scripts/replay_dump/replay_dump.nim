## replay_dump — expand one .bitreplay into full per-tick multi-agent state.
##
## Unlike expand_replay (event timeline only), this also emits every player's
## position each Playing tick, body drop coordinates, and roster/role lines,
## as NDJSON for downstream analysis. The first positional arg is the replay
## path; the optional second arg is the output file (default stdout).
##
##   nim c -d:release -o:/tmp/replay_dump tools/replay_dump.nim
##   /tmp/replay_dump replay.bitreplay out.ndjson
##
## Line kinds (all JSON objects with "k"):
##   meta   — config subset + rooms/tasks/vents/button geometry
##   roster — per player: index, slot (joinOrder), name, color, role, tasks
##            (re-emitted on every join/phase change; final one has end roles)
##   phase  — phase transition at tick t
##   t      — Playing-tick positions: "p": [[slot, x, y, alive], ...]
##   body   — new body: victim/killer slots + world x/y
##   e      — expand_replay event rows (kill, vote_cast, chat, rooms, ...)
##   end    — tick count, hash status, winner, per-player final state

import
  std/[json, os, strutils],
  ../src/crewrift/replays,
  ../src/crewrift/sim,
  ./expand_replay

const
  GameDir = currentSourcePath().parentDir().parentDir()
  UsageText = "Usage: replay_dump <replay.bitreplay> [out.ndjson]"

proc metaJson(sim: SimServer, path: string): JsonNode =
  var rooms = newJArray()
  for room in sim.rooms:
    rooms.add(%*{"name": room.name, "x": room.x, "y": room.y,
                 "w": room.w, "h": room.h})
  var tasks = newJArray()
  for i, task in sim.tasks:
    tasks.add(%*{"i": i, "name": task.name, "x": task.x, "y": task.y,
                 "w": task.w, "h": task.h})
  var vents = newJArray()
  for vent in sim.vents:
    vents.add(%*{"x": vent.x, "y": vent.y, "w": vent.w, "h": vent.h,
                 "group": $vent.group})
  %*{
    "k": "meta",
    "replay_path": path,
    "config": {
      "seed": sim.config.seed,
      "killCooldownTicks": sim.config.killCooldownTicks,
      "voteTimerTicks": sim.config.voteTimerTicks,
      "maxTicks": sim.config.maxTicks,
      "tasksPerPlayer": sim.config.tasksPerPlayer,
      "imposterCount": sim.config.imposterCount,
      "killRange": sim.config.killRange,
    },
    "map": {
      "width": sim.gameMap.width,
      "height": sim.gameMap.height,
      "button": {"x": sim.gameMap.button.x, "y": sim.gameMap.button.y,
                 "w": sim.gameMap.button.w, "h": sim.gameMap.button.h},
      "rooms": rooms,
      "tasks": tasks,
      "vents": vents,
    },
  }

proc rosterJson(sim: SimServer, tick: int): JsonNode =
  var players = newJArray()
  for i, p in sim.players:
    players.add(%*{
      "i": i,
      "slot": p.joinOrder,
      "name": p.address,
      "color": playerColorText(p.color),
      "role": $p.role,
      "alive": p.alive,
      "tasks": p.assignedTasks,
    })
  %*{"k": "roster", "t": tick, "players": players}

proc tickJson(sim: SimServer, tick: int): JsonNode =
  var positions = newJArray()
  for p in sim.players:
    positions.add(%[%p.joinOrder, %p.x, %p.y, %(if p.alive: 1 else: 0)])
  %*{"k": "t", "t": tick, "p": positions}

proc bodyKeyOf(body: Body): string =
  $body.slotId & ":" & $body.x & ":" & $body.y

proc killerSlotThisTick(sim: SimServer, killCooldowns: seq[int]): int =
  ## The imposter whose kill cooldown just reset killed this tick.
  for i, player in sim.players:
    if i < killCooldowns.len and player.role == Imposter and
        killCooldowns[i] <= 0 and player.killCooldown > 0:
      return player.joinOrder
  -1

proc endJson(
  sim: SimServer, ticks: int, hashFailed: bool, failTick: int
): JsonNode =
  var players = newJArray()
  for p in sim.players:
    players.add(%*{
      "slot": p.joinOrder,
      "name": p.address,
      "color": playerColorText(p.color),
      "role": $p.role,
      "alive": p.alive,
      "reward": p.reward,
      "tasksRewarded": p.tasksRewarded,
    })
  %*{
    "k": "end",
    "ticks": ticks,
    "hash_failed": hashFailed,
    "fail_tick": failTick,
    "phase": $sim.phase,
    "winner": $sim.winner,
    "players": players,
  }

proc dumpReplay(replayPath, outPath: string) =
  let data = loadReplay(replayPath)
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  var output = if outPath.len > 0: open(outPath, fmWrite) else: stdout
  try:
    var
      sim = initSimServer(data.replayGameConfig())
      replay = initReplayPlayer(data)
      killCooldowns: seq[int]
      printedBodies: seq[string]
      lastPlayerCount = 0
      lastPhase = sim.phase
      hashFailed = false
      failTick = -1
      lastTick = 0

    sim.gameEventLoggingEnabled = false
    replay.looping = false
    replay.mismatchQuit = true

    output.writeLine($sim.metaJson(replayPath))

    while replay.playing:
      try:
        replay.stepReplay(sim)
      except ReplayError:
        hashFailed = true
        failTick = sim.tickCount
        break
      let tick = sim.tickCount
      lastTick = tick

      if sim.players.len != lastPlayerCount:
        output.writeLine($sim.rosterJson(tick))
        lastPlayerCount = sim.players.len
        while killCooldowns.len < sim.players.len:
          killCooldowns.add(sim.players[killCooldowns.len].killCooldown)

      if sim.phase != lastPhase:
        output.writeLine($(%*{"k": "phase", "t": tick, "phase": $sim.phase}))
        if sim.phase in {RoleReveal, Playing, GameOver}:
          output.writeLine($sim.rosterJson(tick))
        lastPhase = sim.phase

      for body in sim.bodies:
        let key = body.bodyKeyOf()
        if key in printedBodies:
          continue
        output.writeLine($(%*{
          "k": "body", "t": tick,
          "victim": body.slotId,
          "killer": sim.killerSlotThisTick(killCooldowns),
          "x": body.x, "y": body.y,
        }))
        printedBodies.add(key)

      for i, player in sim.players:
        if i < killCooldowns.len:
          killCooldowns[i] = player.killCooldown

      if sim.phase == Playing:
        output.writeLine($sim.tickJson(tick))

    # Second pass: the proven event timeline (kills, votes, chats, rooms ...).
    let timeline = expandReplayTimeline(data)
    for event in timeline.events:
      var row = event.jsonRow()
      row["k"] = %"e"
      output.writeLine($row)
    if timeline.hashFailed and not hashFailed:
      hashFailed = true
      failTick = timeline.failTick

    output.writeLine($sim.endJson(lastTick, hashFailed, failTick))
  finally:
    if outPath.len > 0:
      output.close()
    setCurrentDir(previousDir)

when isMainModule:
  var paths: seq[string]
  for arg in commandLineParams():
    if arg in ["--help", "-h"]:
      echo UsageText
      quit(0)
    elif arg.startsWith("--"):
      stderr.writeLine("Unknown option: " & arg & "\n" & UsageText)
      quit(1)
    else:
      paths.add(arg)
  if paths.len < 1 or paths.len > 2:
    stderr.writeLine(UsageText)
    quit(1)
  let replayPath = paths[0].absolutePath()
  let outPath = if paths.len == 2: paths[1].absolutePath() else: ""
  if not fileExists(replayPath):
    stderr.writeLine("Replay file does not exist: " & replayPath)
    quit(1)
  try:
    dumpReplay(replayPath, outPath)
  except ReplayError as e:
    stderr.writeLine("replay_dump replay error: " & e.msg)
    quit(1)
  except CrewriftError as e:
    stderr.writeLine("replay_dump sim error: " & e.msg)
    quit(1)
