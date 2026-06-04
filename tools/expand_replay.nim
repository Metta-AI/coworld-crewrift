import
  std/[json, os, strutils],
  ../src/crewrift/replays,
  ../src/crewrift/sim

type
  ExpandReplayError = object of CatchableError

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
    tickCount*: int
    hashFailed*: bool
    failTick*: int

const
  UsageText = "Usage: nim r tools/expand_replay.nim [replay-path]"
  GameDir = currentSourcePath().parentDir().parentDir()
  DefaultReplayPath = GameDir / "tests" / "replays" / "notsus.bitreplay"

proc fail(message: string) =
  ## Raises one replay expansion failure.
  raise newException(ExpandReplayError, message)

proc replayPathFromArgs(): string {.used.} =
  ## Returns the replay path passed on the command line.
  var paths: seq[string]
  for arg in commandLineParams():
    if arg == "--":
      discard
    elif arg in ["--help", "-h"]:
      echo UsageText
      quit(0)
    elif arg.startsWith("--"):
      fail("Unknown option: " & arg & "\n" & UsageText)
    else:
      paths.add(arg)
  if paths.len > 1:
    fail("Expected at most one replay path.\n" & UsageText)
  if paths.len == 0:
    return DefaultReplayPath
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
  rewards: var seq[int],
  killCooldowns: var seq[int]
) =
  ## Adds tracking state for newly joined players.
  while alive.len < sim.players.len:
    let i = alive.len
    alive.add(sim.players[i].alive)
    tasks.add(sim.players[i].activeTask)
    votes.add(if i < sim.voteState.votes.len: sim.voteState.votes[i] else: -1)
    rooms.add(sim.roomAt(i))
    rewards.add(sim.players[i].reward)
    killCooldowns.add(sim.players[i].killCooldown)
    events.addPlayerEvent(tick, PlayerJoined, sim, i)
    if rooms[i] >= 0:
      events.addRoomEvent(tick, EnteredRoom, sim, i, rooms[i])

proc killerThisTick(sim: SimServer, killCooldowns: openArray[int]): int =
  ## Returns the imposter whose kill cooldown just reset.
  for i, player in sim.players:
    if i < killCooldowns.len and player.role == Imposter and
      killCooldowns[i] <= 0 and player.killCooldown > 0:
      return i
  -1

proc printNewBodies(
  sim: SimServer,
  tick: int,
  events: var seq[ReplayEvent],
  printed: var seq[string],
  killCooldowns: openArray[int]
): seq[int] =
  ## Adds new body and kill events once.
  for body in sim.bodies:
    let key = body.bodyKey()
    if printed.hasKey(key):
      continue
    let
      victim = sim.playerForSlot(body.slotId)
      killer = sim.killerThisTick(killCooldowns)
    if killer >= 0 and victim >= 0:
      events.add ReplayEvent(
        tick: tick,
        kind: Kill,
        actorSlot: sim.playerSlot(killer),
        actorLabel: sim.player(killer),
        secondarySlot: sim.playerSlot(victim),
        secondaryLabel: sim.player(victim),
        task: -1,
        phase: sim.phase
      )
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

proc updatePlayerCounters(
  sim: SimServer,
  killCooldowns: var seq[int]
) =
  ## Copies player counters after a tick is printed.
  for i, player in sim.players:
    while killCooldowns.len <= i:
      killCooldowns.add(player.killCooldown)
    killCooldowns[i] = player.killCooldown

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

proc eventsAt(timeline: ReplayTimeline, tick: int): seq[ReplayEvent] =
  ## Returns timeline events for one tick in their recorded order.
  for event in timeline.events:
    if event.tick == tick:
      result.add(event)

proc expandReplayTimeline*(data: ReplayData): ReplayTimeline =
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
      killCooldowns: seq[int]
      printedBodies: seq[string]
      done: seq[seq[bool]]
      chatCount = 0
      phase = sim.phase

    sim.gameEventLoggingEnabled = false
    for task in sim.tasks:
      done.add(task.completed)
    replay.looping = false
    replay.mismatchQuit = true

    while replay.playing:
      let tick = sim.tickCount + 1
      result.tickCount = tick
      try:
        replay.stepReplay(sim)
      except ReplayError:
        result.hashFailed = true
        result.failTick = tick
        return

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
        rewards,
        killCooldowns
      )
      let bodyVictims = sim.printNewBodies(
        tick,
        result.events,
        printedBodies,
        killCooldowns
      )
      for victim in bodyVictims:
        if victim < alive.len:
          alive[victim] = sim.players[victim].alive
      sim.printPlayerChanges(tick, result.events, alive, tasks, rooms, bodyVictims)
      sim.printTaskCompletions(tick, result.events, done)
      sim.printVotes(tick, result.events, votes)
      sim.printChats(tick, result.events, chatCount)
      sim.printScoreChanges(tick, result.events, rewards)
      sim.updatePlayerCounters(killCooldowns)
  finally:
    setCurrentDir(previousDir)

proc expandReplay(path: string) {.used.} =
  ## Prints one readable replay timeline.
  if not fileExists(path):
    fail("Replay file does not exist: " & path)

  let data = loadReplay(path)
  let timeline = expandReplayTimeline(data)

  echo "replay ", path
  for tick in 1 .. timeline.tickCount:
    echo "tick ", tick
    for event in timeline.eventsAt(tick):
      echo event.text()
    if timeline.hashFailed and tick == timeline.failTick:
      echo "  hash failed"
      fail("hash failed")
  echo "done"

when isMainModule:
  try:
    expandReplay(replayPathFromArgs())
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
