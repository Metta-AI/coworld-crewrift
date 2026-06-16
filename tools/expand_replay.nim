import
  std/[json, os, strutils],
  ../src/crewrift/replays,
  ../src/crewrift/sim

type
  ExpandReplayError = object of CatchableError

  OutputFormat = enum
    TextFormat
    JsonlFormat

  ReplayCliConfig = object
    replayPath: string
    outputFormat: OutputFormat
    snapshotEvery: int

  ReplayEventKind* = enum
    PlayerJoined
    EnteredRoom
    LeftRoom
    PhaseChanged
    VoteCalledBody
    VoteCalledButton
    Kill
    BodyFound
    Died
    Revived
    StartedTask
    CompletedTask
    VoteCast
    Chat
    Score

  ReplayEvent* = object
    tick*: int
    kind*: ReplayEventKind
    actorSlot*: int
    actorLabel*: string
    secondarySlot*: int
    secondaryLabel*: string
    room*: string
    task*: int
    whileDead*: bool
    phase*: GamePhase
    voteSkip*: bool
    scoreAmount*: int
    scoreReason*: string
    chatText*: string

  ReplayTimeline* = object
    events*: seq[ReplayEvent]
    traceRows*: seq[JsonNode]
    tickCount*: int
    hashFailed*: bool
    failTick*: int

  VisibilityObservation = object
    observerSlot: int
    observerLabel: string
    observerRole: string
    targetId: string
    targetKind: string
    targetSlot: int
    targetLabel: string
    targetRole: string
    room: string
    x: int
    y: int

  VisibilityInterval = object
    sample: VisibilityObservation
    tickStart: int
    lastTick: int

const
  UsageText = "Usage: nim r tools/expand_replay.nim [--format text|jsonl] [--snapshot-every ticks] [replay-path]"
  EventSchemaVersion = "crewrift-events/v1"
  GameDir = currentSourcePath().parentDir().parentDir()
  DefaultReplayPath = GameDir / "tests" / "replays" / "notsus.bitreplay"

proc fail(message: string) =
  ## Raises one replay expansion failure.
  raise newException(ExpandReplayError, message)

proc parseOutputFormat(value: string): OutputFormat =
  ## Returns one requested output format.
  case value
  of "text":
    result = TextFormat
  of "jsonl":
    result = JsonlFormat
  else:
    fail("Unknown format: " & value & "\n" & UsageText)

proc parsePositiveInt(value, flag: string): int =
  ## Parses one positive integer CLI option.
  try:
    result = parseInt(value)
  except ValueError:
    fail("Invalid integer for " & flag & ": " & value & "\n" & UsageText)
  if result <= 0:
    fail(flag & " must be positive.\n" & UsageText)

proc cliConfigFromArgs(): ReplayCliConfig {.used.} =
  ## Returns replay expansion configuration passed on the command line.
  var paths: seq[string]
  var outputFormat = TextFormat
  var snapshotEvery = 0
  let args = commandLineParams()
  var i = 0
  while i < args.len:
    let arg = args[i]
    if arg == "--":
      discard
    elif arg in ["--help", "-h"]:
      echo UsageText
      quit(0)
    elif arg == "--format":
      inc i
      if i >= args.len:
        fail("Missing value for --format.\n" & UsageText)
      outputFormat = parseOutputFormat(args[i])
    elif arg.startsWith("--format="):
      outputFormat = parseOutputFormat(arg["--format=".len .. ^1])
    elif arg == "--snapshot-every":
      inc i
      if i >= args.len:
        fail("Missing value for --snapshot-every.\n" & UsageText)
      snapshotEvery = parsePositiveInt(args[i], "--snapshot-every")
    elif arg.startsWith("--snapshot-every="):
      snapshotEvery = parsePositiveInt(
        arg["--snapshot-every=".len .. ^1],
        "--snapshot-every"
      )
    elif arg.startsWith("--"):
      fail("Unknown option: " & arg & "\n" & UsageText)
    else:
      paths.add(arg)
    inc i
  if paths.len > 1:
    fail("Expected at most one replay path.\n" & UsageText)
  result.outputFormat = outputFormat
  result.snapshotEvery = snapshotEvery
  result.replayPath =
    if paths.len == 0:
      DefaultReplayPath
    else:
      paths[0].absolutePath()

proc replayConfig(data: ReplayData): GameConfig =
  ## Returns the game config embedded in a replay.
  result = defaultGameConfig()
  result.update(data.configJson)

proc player(sim: SimServer, i: int): string =
  ## Returns color and username for one player.
  let p = sim.players[i]
  playerColorText(p.color) & "(" & p.address & ")"

proc playerSlot(sim: SimServer, i: int): int =
  ## Returns one player's stable join slot.
  if i >= 0 and i < sim.players.len:
    return sim.players[i].joinOrder
  -1

proc playerForSlot(sim: SimServer, slotId: int): int =
  ## Returns the player index for one join slot.
  for i, player in sim.players:
    if player.joinOrder == slotId:
      return i
  -1

proc bodyPlayer(sim: SimServer, body: Body): string =
  ## Returns color and username for one body.
  let i = sim.playerForSlot(body.slotId)
  if i >= 0:
    return sim.player(i)
  playerColorText(body.color) & "(unknown)"

proc roomAt(sim: SimServer, x, y: int): int =
  ## Returns the room containing one point.
  for roomIndex, room in sim.rooms:
    if x >= room.x and x < room.x + room.w and
      y >= room.y and y < room.y + room.h:
      return roomIndex
  -1

proc roomAt(sim: SimServer, i: int): int =
  ## Returns the room containing one player.
  sim.roomAt(sim.players[i].x, sim.players[i].y)

proc roomName(sim: SimServer, i: int): string =
  ## Returns the room name for one room index.
  sim.rooms[i].name

proc roomNameAt(sim: SimServer, x, y: int): string =
  ## Returns the nearest room name for one point.
  let room = sim.roomAt(x, y)
  if room >= 0:
    return sim.roomName(room)
  var
    bestRoom = 0
    bestDistance = high(int)
  for i, room in sim.rooms:
    let
      cx = room.x + room.w div 2
      cy = room.y + room.h div 2
      distance = distSq(x, y, cx, cy)
    if distance < bestDistance:
      bestDistance = distance
      bestRoom = i
  sim.roomName(bestRoom)

proc roleText(role: PlayerRole): string =
  ## Returns the event-schema role name for one player role.
  case role
  of Crewmate:
    "crew"
  of Imposter:
    "imposter"

proc traceValue(source: string, confidence = 1.0): JsonNode =
  ## Returns common fields for one standard event-schema value object.
  result = newJObject()
  result["schema_version"] = %EventSchemaVersion
  result["source"] = %source
  result["confidence"] = %confidence

proc traceValue(source: string, phase: GamePhase, confidence = 1.0): JsonNode =
  ## Returns common fields for one standard value object tied to a game phase.
  result = traceValue(source, confidence)
  result["phase"] = %($phase)

proc standardRow(tick, player: int, key: string, value: JsonNode): JsonNode =
  ## Returns one standard event-schema row.
  result = newJObject()
  result["ts"] = %tick
  result["player"] = %player
  result["key"] = %key
  result["value"] = value

proc rectJson(x, y, w, h: int): JsonNode =
  ## Returns one rectangle as JSON.
  %*{"x": x, "y": y, "w": w, "h": h}

proc mapGeometryRow(sim: SimServer): JsonNode =
  ## Returns one global map-geometry event row.
  var rooms = newJArray()
  for i, room in sim.rooms:
    rooms.add(%*{
      "id": i,
      "name": room.name,
      "x": room.x,
      "y": room.y,
      "w": room.w,
      "h": room.h
    })

  var tasks = newJArray()
  for i, task in sim.tasks:
    tasks.add(%*{
      "id": i,
      "name": task.name,
      "resource_name": task.resourceName,
      "x": task.x,
      "y": task.y,
      "w": task.w,
      "h": task.h,
      "room": sim.roomNameAt(task.x + task.w div 2, task.y + task.h div 2)
    })

  var vents = newJArray()
  for i, vent in sim.vents:
    vents.add(%*{
      "id": i,
      "resource_name": vent.resourceName,
      "x": vent.x,
      "y": vent.y,
      "w": vent.w,
      "h": vent.h,
      "group": $vent.group,
      "group_index": vent.groupIndex,
      "room": sim.roomNameAt(vent.x + vent.w div 2, vent.y + vent.h div 2)
    })

  var value = traceValue("replay")
  value["map_name"] = %sim.gameMap.name
  value["width"] = %sim.gameMap.width
  value["height"] = %sim.gameMap.height
  value["rooms"] = rooms
  value["tasks"] = tasks
  value["vents"] = vents
  value["button"] = rectJson(
    sim.gameMap.button.x,
    sim.gameMap.button.y,
    sim.gameMap.button.w,
    sim.gameMap.button.h
  )
  value["home"] = %*{"x": sim.gameMap.home.x, "y": sim.gameMap.home.y}
  standardRow(0, -1, "map_geometry", value)

proc episodeMetadataRow(sim: SimServer, snapshotEvery: int): JsonNode =
  ## Returns one global episode-metadata event row.
  var value = traceValue("replay")
  value["snapshot_every_ticks"] = %snapshotEvery
  value["hash_checking"] = %true
  value["config"] = %*{
    "speed": sim.config.speed,
    "max_ticks": sim.config.maxTicks,
    "kill_range": sim.config.killRange,
    "kill_cooldown_ticks": sim.config.killCooldownTicks,
    "report_range": sim.config.reportRange,
    "vent_range": sim.config.ventRange,
    "task_complete_ticks": sim.config.taskCompleteTicks,
    "vote_timer_ticks": sim.config.voteTimerTicks,
    "vote_result_ticks": sim.config.voteResultTicks,
    "imposter_count": sim.config.imposterCount,
    "tasks_per_player": sim.config.tasksPerPlayer,
    "button_calls": sim.config.buttonCalls
  }
  standardRow(0, -1, "episode_metadata", value)

proc playerManifestRow(sim: SimServer, tick, playerIndex: int): JsonNode =
  ## Returns one player manifest row for a joined player.
  let p = sim.players[playerIndex]
  var value = traceValue("replay", sim.phase)
  value["label"] = %sim.player(playerIndex)
  value["address"] = %p.address
  value["color"] = %playerColorText(p.color)
  value["color_id"] = %int(p.color)
  value["role"] = %roleText(p.role)
  value["home_x"] = %p.homeX
  value["home_y"] = %p.homeY
  value["assigned_tasks"] = %p.assignedTasks
  standardRow(tick, sim.playerSlot(playerIndex), "player_manifest", value)

proc roleAssigned(sim: SimServer, playerIndex: int): bool =
  ## Returns true when startGame has assigned this player's game role.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return false
  let player = sim.players[playerIndex]
  for account in sim.rewardAccounts:
    if account.address == player.address and
        account.slotIndex == player.joinOrder and
        account.hasRole:
      return true
  false

proc addPlayerManifestRows(
  sim: SimServer,
  tick: int,
  rows: var seq[JsonNode],
  emitted: var seq[bool]
) =
  ## Adds one manifest row for each newly seen player.
  while emitted.len < sim.players.len:
    emitted.add(false)
  for i in 0 ..< sim.players.len:
    if not emitted[i] and sim.roleAssigned(i):
      rows.add(sim.playerManifestRow(tick, i))
      emitted[i] = true

proc playerStateRow(sim: SimServer, tick, playerIndex: int): JsonNode =
  ## Returns one sampled player-state row.
  let
    p = sim.players[playerIndex]
    roomIndex = sim.roomAt(playerIndex)
  var value = traceValue("replay", sim.phase)
  value["label"] = %sim.player(playerIndex)
  value["role"] = %roleText(p.role)
  value["alive"] = %p.alive
  value["connected"] = %p.connected
  value["x"] = %p.x
  value["y"] = %p.y
  value["vel_x"] = %p.velX
  value["vel_y"] = %p.velY
  value["room"] = %sim.roomNameAt(p.x, p.y)
  value["inside_room"] = %(roomIndex >= 0)
  value["active_task"] = %p.activeTask
  value["task_progress"] = %p.taskProgress
  value["assigned_tasks"] = %p.assignedTasks
  value["kill_cooldown"] = %p.killCooldown
  value["vent_cooldown"] = %p.ventCooldown
  value["button_calls_used"] = %p.buttonCallsUsed
  value["reward"] = %p.reward
  standardRow(tick, sim.playerSlot(playerIndex), "player_state", value)

proc bodyStateRow(sim: SimServer, tick: int, body: Body): JsonNode =
  ## Returns one sampled body-state row.
  let victim = sim.playerForSlot(body.slotId)
  var value = traceValue("replay", sim.phase)
  value["victim_slot"] = %body.slotId
  value["victim_label"] = %sim.bodyPlayer(body)
  value["color"] = %playerColorText(body.color)
  value["color_id"] = %int(body.color)
  value["x"] = %body.x
  value["y"] = %body.y
  value["room"] = %sim.roomNameAt(body.x, body.y)
  value["victim_connected"] =
    if victim >= 0:
      %sim.players[victim].connected
    else:
      %false
  standardRow(tick, body.slotId, "body_state", value)

proc addStateRows(sim: SimServer, tick: int, rows: var seq[JsonNode]) =
  ## Adds sampled player and body state rows for one tick.
  for i in 0 ..< sim.players.len:
    rows.add(sim.playerStateRow(tick, i))
  for body in sim.bodies:
    rows.add(sim.bodyStateRow(tick, body))

proc voteCallerText(sim: SimServer): string

proc addPlayerEvent(
  events: var seq[ReplayEvent],
  tick: int,
  kind: ReplayEventKind,
  sim: SimServer,
  playerIndex: int
) =
  ## Adds one single-player replay event.
  events.add ReplayEvent(
    tick: tick,
    kind: kind,
    actorSlot: sim.playerSlot(playerIndex),
    actorLabel: sim.player(playerIndex),
    secondarySlot: -1,
    task: -1,
    phase: sim.phase
  )

proc addRoomEvent(
  events: var seq[ReplayEvent],
  tick: int,
  kind: ReplayEventKind,
  sim: SimServer,
  playerIndex,
  roomIndex: int
) =
  ## Adds one player room transition event.
  events.add ReplayEvent(
    tick: tick,
    kind: kind,
    actorSlot: sim.playerSlot(playerIndex),
    actorLabel: sim.player(playerIndex),
    secondarySlot: -1,
    room: sim.roomName(roomIndex),
    task: -1,
    phase: sim.phase
  )

proc bodyKey(body: Body): string =
  ## Returns a stable key for one body instance.
  $body.slotId & ":" & $body.x & ":" & $body.y

proc sameTarget(interval: VisibilityInterval, sample: VisibilityObservation): bool =
  ## Returns true when one visibility sample extends an active interval.
  interval.sample.observerSlot == sample.observerSlot and
    interval.sample.targetId == sample.targetId

proc visibilityIntervalRow(
  interval: VisibilityInterval,
  endTick: int,
  endedBy: string
): JsonNode =
  ## Returns one player-centric visibility interval row.
  var value = traceValue("replay")
  value["observer_slot"] = %interval.sample.observerSlot
  value["observer_label"] = %interval.sample.observerLabel
  value["observer_role"] = %interval.sample.observerRole
  value["target_kind"] = %interval.sample.targetKind
  value["target_id"] = %interval.sample.targetId
  value["target_slot"] = %interval.sample.targetSlot
  value["target_label"] = %interval.sample.targetLabel
  if interval.sample.targetRole.len > 0:
    value["target_role"] = %interval.sample.targetRole
  value["room"] = %interval.sample.room
  value["x"] = %interval.sample.x
  value["y"] = %interval.sample.y
  value["tick_start"] = %interval.tickStart
  value["tick_end"] = %endTick
  value["last_observed_tick"] = %interval.lastTick
  value["duration_ticks"] = %(endTick - interval.tickStart)
  value["visibility_basis"] = %"rendered_view"
  value["boundary_precision"] = %"exact"
  value["ended_by"] = %endedBy
  standardRow(
    interval.tickStart,
    interval.sample.observerSlot,
    interval.sample.targetKind & "_visible_interval",
    value
  )

proc visibleObservations(sim: var SimServer): seq[VisibilityObservation] =
  ## Returns player/body objects visible to each living player this tick.
  if sim.phase != Playing:
    return
  for observerIndex, observer in sim.players:
    if not observer.alive or not observer.connected:
      continue
    let view = sim.playerView(observerIndex)
    discard sim.usePlayerShadowMask(observerIndex, view)
    for targetIndex, target in sim.players:
      if targetIndex == observerIndex or not target.alive or not target.connected:
        continue
      if not target.playerActorInFrame(view):
        continue
      let visiblePoint = target.playerActorVisibilityPoint(view)
      if not sim.screenPointVisible(view, visiblePoint.x, visiblePoint.y):
        continue
      result.add VisibilityObservation(
        observerSlot: sim.playerSlot(observerIndex),
        observerLabel: sim.player(observerIndex),
        observerRole: roleText(observer.role),
        targetId: "player:" & $sim.playerSlot(targetIndex),
        targetKind: "player",
        targetSlot: sim.playerSlot(targetIndex),
        targetLabel: sim.player(targetIndex),
        targetRole: roleText(target.role),
        room: sim.roomNameAt(target.x, target.y),
        x: target.x,
        y: target.y
      )
    for body in sim.bodies:
      let
        x = body.x + CollisionW div 2
        y = body.y + CollisionH div 2
      if not sim.screenPointVisible(view, x, y):
        continue
      result.add VisibilityObservation(
        observerSlot: sim.playerSlot(observerIndex),
        observerLabel: sim.player(observerIndex),
        observerRole: roleText(observer.role),
        targetId: "body:" & body.bodyKey(),
        targetKind: "body",
        targetSlot: body.slotId,
        targetLabel: sim.bodyPlayer(body),
        room: sim.roomNameAt(body.x, body.y),
        x: body.x,
        y: body.y
      )

proc addVisibilityRows(
  sim: var SimServer,
  tick: int,
  rows: var seq[JsonNode],
  active: var seq[VisibilityInterval]
) =
  ## Extends or closes player-centric visibility intervals.
  let samples = sim.visibleObservations()
  var nextActive: seq[VisibilityInterval]
  for interval in active:
    var found = -1
    for i, sample in samples:
      if interval.sameTarget(sample):
        found = i
        break
    if found >= 0:
      var continued = interval
      continued.sample = samples[found]
      continued.lastTick = tick
      nextActive.add(continued)
    elif tick > interval.tickStart:
      let endedBy = if sim.phase == Playing: "not_visible" else: "phase_changed"
      rows.add(interval.visibilityIntervalRow(tick, endedBy))
  for sample in samples:
    var alreadyActive = false
    for interval in nextActive:
      if interval.sameTarget(sample):
        alreadyActive = true
        break
    if not alreadyActive:
      nextActive.add VisibilityInterval(
        sample: sample,
        tickStart: tick,
        lastTick: tick
      )
  active = nextActive

proc flushVisibilityRows(
  rows: var seq[JsonNode],
  active: var seq[VisibilityInterval],
  endTick: int
) =
  ## Emits active intervals that last until the replay trace ends.
  for interval in active:
    if endTick > interval.tickStart:
      rows.add(interval.visibilityIntervalRow(endTick, "trace_end"))
  active.setLen(0)

proc hasKey(keys: openArray[string], key: string): bool =
  ## Returns true when a key is already present.
  for item in keys:
    if item == key:
      return true
  false

proc syncPlayers(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  alive: var seq[bool],
  tasks: var seq[int],
  votes: var seq[int],
  rooms: var seq[int],
  rewards: var seq[int]
) =
  ## Adds tracking state for newly joined players.
  while alive.len < sim.players.len:
    let i = alive.len
    alive.add(sim.players[i].alive)
    tasks.add(sim.players[i].activeTask)
    votes.add(if i < sim.voteState.votes.len: sim.voteState.votes[i] else: -1)
    rooms.add(sim.roomAt(i))
    rewards.add(sim.players[i].reward)
    events.addPlayerEvent(tick, PlayerJoined, sim, i)
    if rooms[i] >= 0:
      events.addRoomEvent(tick, EnteredRoom, sim, i, rooms[i])

proc printNewBodies(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  printed: var seq[string]
): seq[int] =
  ## Adds new body and kill events once.
  for body in sim.bodies:
    let key = body.bodyKey()
    if printed.hasKey(key):
      continue
    let victim = sim.playerForSlot(body.slotId)
    for simEvent in sim.simEvents:
      if simEvent.kind != SimKill or simEvent.tick != tick or
          simEvent.targetSlot != body.slotId:
        continue
      let killer = sim.playerForSlot(simEvent.actorSlot)
      if killer >= 0 and victim >= 0:
        events.add ReplayEvent(
          tick: tick,
          kind: Kill,
          actorSlot: simEvent.actorSlot,
          actorLabel: sim.player(killer),
          secondarySlot: simEvent.targetSlot,
          secondaryLabel: sim.player(victim),
          task: -1,
          phase: sim.phase
        )
      break
    events.add ReplayEvent(
      tick: tick,
      kind: BodyFound,
      actorSlot: if victim >= 0: sim.playerSlot(victim) else: -1,
      actorLabel: sim.bodyPlayer(body),
      secondarySlot: -1,
      room: sim.roomNameAt(body.x, body.y),
      task: -1,
      phase: sim.phase
    )
    printed.add(key)
    if victim >= 0:
      result.add(victim)

proc reportedBodyEvent(sim: SimServer, tick: int): ReplayEvent =
  ## Returns the structured body-report event for the current vote.
  result = ReplayEvent(
    tick: tick,
    kind: VoteCalledBody,
    actorSlot: sim.playerSlot(sim.voteState.callerIndex),
    actorLabel: sim.voteCallerText(),
    secondarySlot: -1,
    secondaryLabel: "unknown",
    task: -1,
    phase: sim.phase
  )
  for body in sim.bodies:
    if body.slotId == sim.voteState.bodySlotId:
      let victim = sim.playerForSlot(body.slotId)
      result.secondarySlot = if victim >= 0: sim.playerSlot(victim) else: -1
      result.secondaryLabel = sim.bodyPlayer(body)
      result.room = sim.roomNameAt(body.x, body.y)
      return
  for body in sim.bodies:
    if body.color == sim.voteState.bodyColor:
      let victim = sim.playerForSlot(body.slotId)
      result.secondarySlot = if victim >= 0: sim.playerSlot(victim) else: -1
      result.secondaryLabel = sim.bodyPlayer(body)
      result.room = sim.roomNameAt(body.x, body.y)
      return
  if sim.voteState.bodyColor != 255'u8:
    result.secondaryLabel = playerColorText(sim.voteState.bodyColor) &
      "(unknown)"

proc voteCallerText(sim: SimServer): string =
  ## Returns the player that started the current vote.
  let reporter = sim.voteState.callerIndex
  if reporter >= 0 and reporter < sim.players.len:
    return sim.player(reporter)
  "unknown"

proc addVoteCall(sim: SimServer, tick: int, events: var seq[ReplayEvent]) =
  ## Adds the event that started a vote.
  case sim.voteState.callKind
  of VoteCalledBody:
    events.add(sim.reportedBodyEvent(tick))
  of VoteCalledButton:
    events.add ReplayEvent(
      tick: tick,
      kind: VoteCalledButton,
      actorSlot: sim.playerSlot(sim.voteState.callerIndex),
      actorLabel: sim.voteCallerText(),
      secondarySlot: -1,
      task: -1,
      phase: sim.phase
    )
  of VoteCalledUnknown:
    discard

proc printPlayerChanges(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  alive: var seq[bool],
  tasks: var seq[int],
  rooms: var seq[int],
  bodyVictims: openArray[int]
) =
  ## Adds player death, task, and room change events.
  for i, p in sim.players:
    if alive[i] and not p.alive and i notin bodyVictims:
      events.addPlayerEvent(tick, Died, sim, i)
    elif not alive[i] and p.alive:
      events.addPlayerEvent(tick, Revived, sim, i)
    alive[i] = p.alive

    if tasks[i] != p.activeTask:
      if p.activeTask >= 0:
        events.add ReplayEvent(
          tick: tick,
          kind: StartedTask,
          actorSlot: sim.playerSlot(i),
          actorLabel: sim.player(i),
          secondarySlot: -1,
          task: p.activeTask,
          phase: sim.phase
        )
      tasks[i] = p.activeTask

    let room = sim.roomAt(i)
    if rooms[i] != room:
      if rooms[i] >= 0:
        events.addRoomEvent(tick, LeftRoom, sim, i, rooms[i])
      if room >= 0:
        events.addRoomEvent(tick, EnteredRoom, sim, i, room)
      rooms[i] = room

proc printTaskCompletions(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  done: var seq[seq[bool]]
) =
  ## Adds task completions since the previous tick.
  for taskIndex, task in sim.tasks:
    while done[taskIndex].len < task.completed.len:
      done[taskIndex].add(false)
    for playerIndex, completed in task.completed:
      if not done[taskIndex][playerIndex] and completed:
        events.add ReplayEvent(
          tick: tick,
          kind: CompletedTask,
          actorSlot: sim.playerSlot(playerIndex),
          actorLabel: sim.player(playerIndex),
          secondarySlot: -1,
          task: taskIndex,
          whileDead: not sim.players[playerIndex].alive,
          phase: sim.phase
        )
      done[taskIndex][playerIndex] = completed

proc printVotes(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  votes: var seq[int]
) =
  ## Adds votes cast since the previous tick.
  for i, v in sim.voteState.votes:
    while votes.len <= i:
      votes.add(-1)
    if votes[i] != v:
      if v >= 0 or v == -2:
        events.add ReplayEvent(
          tick: tick,
          kind: VoteCast,
          actorSlot: sim.playerSlot(i),
          actorLabel: sim.player(i),
          secondarySlot: if v >= 0 and v < sim.players.len:
            sim.playerSlot(v)
          else:
            -1,
          secondaryLabel: if v >= 0 and v < sim.players.len:
            sim.player(v)
          else:
            "",
          voteSkip: v == -2 or v == sim.players.len,
          task: -1,
          phase: sim.phase
        )
      votes[i] = v

proc printChats(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  chatCount: var int
) =
  ## Adds visible voting chat since the previous tick.
  if sim.chatMessages.len < chatCount:
    chatCount = 0
  for i in chatCount ..< sim.chatMessages.len:
    let chat = sim.chatMessages[i]
    for playerIndex, p in sim.players:
      if p.joinOrder == chat.slotId:
        events.add ReplayEvent(
          tick: tick,
          kind: Chat,
          actorSlot: p.joinOrder,
          actorLabel: sim.player(playerIndex),
          secondarySlot: -1,
          chatText: chat.text,
          task: -1,
          phase: sim.phase
        )
  chatCount = sim.chatMessages.len

proc scoreAmount(amount: int): string =
  ## Returns a readable score amount.
  if amount > 0:
    "+" & $amount
  else:
    $amount

proc printScoreLine(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  playerIndex,
  amount: int,
  reason: string
) =
  ## Adds one score change event.
  events.add ReplayEvent(
    tick: tick,
    kind: Score,
    actorSlot: sim.playerSlot(playerIndex),
    actorLabel: sim.player(playerIndex),
    secondarySlot: -1,
    task: -1,
    phase: sim.phase,
    scoreAmount: amount,
    scoreReason: reason
  )

proc printPositiveScore(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  playerIndex,
  amount: int
): int =
  ## Adds non-win score changes and returns the win count.
  var remaining = amount
  result = remaining div WinReward
  remaining = remaining mod WinReward
  while remaining >= KillReward:
    sim.printScoreLine(tick, events, playerIndex, KillReward, "killing")
    remaining -= KillReward
  while remaining >= TaskReward:
    sim.printScoreLine(tick, events, playerIndex, TaskReward, "completing task")
    remaining -= TaskReward

proc printNegativeScore(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  playerIndex,
  amount: int
) =
  ## Adds negative score changes as known penalty parts.
  var remaining = amount
  while remaining <= VoteTimeoutPenalty:
    sim.printScoreLine(
      tick,
      events,
      playerIndex,
      VoteTimeoutPenalty,
      "failing to vote or skip"
    )
    remaining -= VoteTimeoutPenalty
  while remaining <= StuckPenalty:
    sim.printScoreLine(tick, events, playerIndex, StuckPenalty, "standing still")
    remaining -= StuckPenalty

proc printScoreChanges(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  rewards: var seq[int]
) =
  ## Adds player score changes since the previous tick.
  var wins = newSeq[int](sim.players.len)
  for i, player in sim.players:
    while rewards.len <= i:
      rewards.add(player.reward)
    let amount = player.reward - rewards[i]
    if amount != 0:
      if amount > 0:
        wins[i] = sim.printPositiveScore(tick, events, i, amount)
      else:
        sim.printNegativeScore(tick, events, i, amount)
      rewards[i] = player.reward
  for i, count in wins:
    for _ in 0 ..< count:
      sim.printScoreLine(tick, events, i, WinReward, "winning")

proc key*(event: ReplayEvent): string =
  ## Returns the event-log key for one replay event.
  case event.kind
  of PlayerJoined:
    result = "player_joined"
  of EnteredRoom:
    result = "entered_room"
  of LeftRoom:
    result = "left_room"
  of PhaseChanged:
    result = "phase"
  of VoteCalledBody:
    result = "vote_called_body"
  of VoteCalledButton:
    result = "vote_called_button"
  of Kill:
    result = "kill"
  of BodyFound:
    result = "body"
  of Died:
    result = "died"
  of Revived:
    result = "revived"
  of StartedTask:
    result = "started_task"
  of CompletedTask:
    result = "completed_task"
  of VoteCast:
    result = "vote_cast"
  of Chat:
    result = "chat"
  of Score:
    result = "score"

proc text*(event: ReplayEvent): string =
  ## Renders one replay event as the legacy human-readable CLI line.
  case event.kind
  of PlayerJoined:
    result = "  player " & event.actorLabel & " joined"
  of EnteredRoom:
    result = "  player " & event.actorLabel & " entered room " & event.room
  of LeftRoom:
    result = "  player " & event.actorLabel & " left room " & event.room
  of PhaseChanged:
    result = "  phase " & $event.phase
  of VoteCalledBody:
    result = "  player " & event.actorLabel & " reported body " &
      event.secondaryLabel
    if event.room.len > 0:
      result.add(" room " & event.room)
  of VoteCalledButton:
    result = "  player " & event.actorLabel & " called emergency button"
  of Kill:
    result = "  player " & event.actorLabel & " killed " & event.secondaryLabel
  of BodyFound:
    result = "  body " & event.actorLabel & " room " & event.room
  of Died:
    result = "  player " & event.actorLabel & " died"
  of Revived:
    result = "  player " & event.actorLabel & " revived"
  of StartedTask:
    result = "  player " & event.actorLabel & " started task " & $event.task
  of CompletedTask:
    result = "  player " & event.actorLabel & " completed task " & $event.task
    if event.whileDead:
      result.add(" while dead")
  of VoteCast:
    result = "  player " & event.actorLabel & " voted "
    if event.voteSkip:
      result.add("skip")
    else:
      result.add(event.secondaryLabel)
  of Chat:
    result = "  player " & event.actorLabel & " said " & repr(event.chatText)
  of Score:
    result = "  score player " & event.actorLabel & " " &
      scoreAmount(event.scoreAmount) & " (for " & event.scoreReason & ")"

proc jsonRow*(event: ReplayEvent): JsonNode =
  ## Returns one event-log JSON row for a replay event.
  var value = newJObject()
  case event.kind
  of PlayerJoined:
    value["label"] = %event.actorLabel
  of EnteredRoom, LeftRoom:
    value["room"] = %event.room
  of PhaseChanged:
    value["phase"] = %($event.phase)
  of VoteCalledBody:
    value["body_owner_slot"] = %event.secondarySlot
    value["body_owner_label"] = %event.secondaryLabel
    value["room"] = %event.room
  of VoteCalledButton:
    discard
  of Kill:
    value["victim_slot"] = %event.secondarySlot
    value["victim_label"] = %event.secondaryLabel
  of BodyFound:
    value["label"] = %event.actorLabel
    value["room"] = %event.room
  of Died, Revived:
    discard
  of StartedTask:
    value["task"] = %event.task
  of CompletedTask:
    value["task"] = %event.task
    value["while_dead"] = %event.whileDead
  of VoteCast:
    if event.voteSkip:
      value["target"] = %"skip"
    else:
      value["target_slot"] = %event.secondarySlot
      value["target_label"] = %event.secondaryLabel
  of Chat:
    value["text"] = %event.chatText
  of Score:
    value["amount"] = %event.scoreAmount
    value["reason"] = %event.scoreReason

  result = newJObject()
  result["ts"] = %event.tick
  result["player"] = %event.actorSlot
  result["key"] = %event.key()
  result["value"] = value

proc eventRow*(event: ReplayEvent): JsonNode =
  ## Returns one standard event-schema row for a direct replay event.
  result = event.jsonRow()
  let value = result["value"]
  value["schema_version"] = %EventSchemaVersion
  value["source"] = %"replay"
  value["confidence"] = %1.0
  value["phase"] = %($event.phase)

proc eventsAt(timeline: ReplayTimeline, tick: int): seq[ReplayEvent] =
  ## Returns timeline events for one tick in their recorded order.
  for event in timeline.events:
    if event.tick == tick:
      result.add(event)

proc expandReplayTimeline*(data: ReplayData, snapshotEvery = 0): ReplayTimeline =
  ## Expands one replay into a structured event timeline.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    var
      sim = initSimServer(data.replayConfig())
      replay = initReplayPlayer(data)
      alive: seq[bool]
      tasks: seq[int]
      votes: seq[int]
      rooms: seq[int]
      rewards: seq[int]
      printedBodies: seq[string]
      done: seq[seq[bool]]
      chatCount = 0
      phase = sim.phase
      manifestedPlayers: seq[bool]
      visibilityIntervals: seq[VisibilityInterval]

    sim.gameEventLoggingEnabled = false
    for task in sim.tasks:
      done.add(task.completed)
    replay.looping = false
    replay.mismatchQuit = true
    if snapshotEvery > 0:
      result.traceRows.add(sim.episodeMetadataRow(snapshotEvery))
      result.traceRows.add(sim.mapGeometryRow())

    while replay.playing:
      let tick = sim.tickCount + 1
      result.tickCount = tick
      try:
        replay.stepReplay(sim)
      except ReplayError:
        result.hashFailed = true
        result.failTick = tick
        return

      let eventStart = result.events.len
      if phase != sim.phase:
        result.events.add ReplayEvent(
          tick: tick,
          kind: PhaseChanged,
          actorSlot: -1,
          secondarySlot: -1,
          task: -1,
          phase: sim.phase
        )
        if sim.phase == Voting:
          sim.addVoteCall(tick, result.events)
        phase = sim.phase

      sim.syncPlayers(
        tick,
        result.events,
        alive,
        tasks,
        votes,
        rooms,
        rewards
      )
      let bodyVictims = sim.printNewBodies(
        tick,
        result.events,
        printedBodies
      )
      for victim in bodyVictims:
        if victim < alive.len:
          alive[victim] = sim.players[victim].alive
      sim.printPlayerChanges(tick, result.events, alive, tasks, rooms, bodyVictims)
      sim.printTaskCompletions(tick, result.events, done)
      sim.printVotes(tick, result.events, votes)
      sim.printChats(tick, result.events, chatCount)
      sim.printScoreChanges(tick, result.events, rewards)
      if snapshotEvery > 0:
        sim.addPlayerManifestRows(tick, result.traceRows, manifestedPlayers)
        sim.addVisibilityRows(tick, result.traceRows, visibilityIntervals)
        if tick mod snapshotEvery == 0 or result.events.len > eventStart:
          sim.addStateRows(tick, result.traceRows)
    if snapshotEvery > 0:
      result.traceRows.flushVisibilityRows(visibilityIntervals, result.tickCount)
  finally:
    setCurrentDir(previousDir)

proc printText(timeline: ReplayTimeline, path: string) =
  ## Prints one readable replay timeline.
  echo "replay ", path
  for tick in 1 .. timeline.tickCount:
    echo "tick ", tick
    for event in timeline.eventsAt(tick):
      echo event.text()
    if timeline.hashFailed and tick == timeline.failTick:
      echo "  hash failed"
      fail("hash failed")
  echo "done"

proc printJsonl(timeline: ReplayTimeline) =
  ## Prints one machine-readable replay timeline.
  var
    traceRowsByTick = newSeq[seq[JsonNode]](timeline.tickCount + 1)
    eventsByTick = newSeq[seq[ReplayEvent]](timeline.tickCount + 1)
  for row in timeline.traceRows:
    let tick = row["ts"].getInt()
    if tick >= 0 and tick < traceRowsByTick.len:
      traceRowsByTick[tick].add(row)
  for event in timeline.events:
    if event.tick >= 0 and event.tick < eventsByTick.len:
      eventsByTick[event.tick].add(event)
  for tick in 0 .. timeline.tickCount:
    for row in traceRowsByTick[tick]:
      echo $row
    for event in eventsByTick[tick]:
      echo $event.eventRow()
  if timeline.hashFailed:
    var warning = traceValue("replay")
    warning["message"] = %"hash failed"
    warning["fail_tick"] = %timeline.failTick
    echo $standardRow(timeline.failTick, -1, "trace_warning", warning)
    var complete = traceValue("replay")
    complete["complete"] = %false
    complete["fail_tick"] = %timeline.failTick
    echo $standardRow(timeline.failTick, -1, "trace_complete", complete)
    fail("hash failed")
  var complete = traceValue("replay")
  complete["complete"] = %true
  complete["tick_count"] = %timeline.tickCount
  echo $standardRow(timeline.tickCount, -1, "trace_complete", complete)

proc expandReplay(config: ReplayCliConfig) {.used.} =
  ## Prints one replay timeline.
  let path = config.replayPath
  if not fileExists(path):
    fail("Replay file does not exist: " & path)

  let data = loadReplay(path)
  let timeline = expandReplayTimeline(
    data,
    if config.outputFormat == JsonlFormat:
      config.snapshotEvery
    else:
      0
  )
  case config.outputFormat
  of TextFormat:
    printText(timeline, path)
  of JsonlFormat:
    printJsonl(timeline)

when isMainModule:
  try:
    expandReplay(cliConfigFromArgs())
  except ExpandReplayError as e:
    if e.msg != "hash failed":
      stderr.writeLine("expand_replay failed: " & e.msg)
    quit(1)
  except ReplayError as e:
    stderr.writeLine("expand_replay replay error: " & e.msg)
    quit(1)
  except CrewriftError as e:
    stderr.writeLine("expand_replay sim error: " & e.msg)
    quit(1)
