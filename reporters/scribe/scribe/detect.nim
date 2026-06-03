import
  std/algorithm,
  ../../../src/crewrift/sim,
  driver,
  events,
  identity,
  probes

proc rewardKillsForPlayer(sim: SimServer, playerIndex: int): int =
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return 0
  let player = sim.players[playerIndex]
  for i in countdown(sim.rewardAccounts.high, 0):
    let account = sim.rewardAccounts[i]
    if account.slotIndex == player.joinOrder:
      return account.kills
  for i in countdown(sim.rewardAccounts.high, 0):
    let account = sim.rewardAccounts[i]
    if account.address == player.address:
      return account.kills
  0

proc voteTargetFromIndex(
  ids: var IdentityTable,
  sim: SimServer,
  vote: int,
  context: string
): VoteTarget =
  if vote == -2:
    return skipTarget()
  if vote == -1:
    return noTarget()
  if vote >= 0 and vote < sim.players.len:
    return playerTarget(refForLiveIndex(sim, vote))
  ids.addWarning(context & " references invalid vote target " & $vote)
  unknownTarget()

proc bodyTarget(ids: var IdentityTable, sim: SimServer, probe: BodyReportProbe): VoteTarget =
  if probe.bodyIndex < 0 or probe.bodyIndex >= sim.bodies.len:
    return unknownTarget()
  let body = sim.bodies[probe.bodyIndex]
  var victim = ids.byJoinOrder(body.slotId)
  if not victim.isValid:
    victim = ids.byColor(probe.bodyColor)
  if victim.isValid:
    playerTarget(victim)
  else:
    ids.addWarning("body report resolved a body but not a stable victim identity")
    unknownTarget()

proc gameOverReason(sim: SimServer): string =
  if sim.timeLimitReached:
    return "time limit"

  var
    hasImposters = false
    aliveCrewmates = 0
    aliveImposters = 0
  for player in sim.players:
    if player.role == Imposter:
      hasImposters = true
    if player.alive:
      if player.role == Imposter:
        inc aliveImposters
      else:
        inc aliveCrewmates

  if hasImposters and aliveImposters == 0 and sim.players.len > 0:
    return "imposters eliminated"
  if sim.winner == Imposter and hasImposters and
      aliveImposters >= aliveCrewmates and sim.players.len > 0:
    return "crew outnumbered"
  if sim.winner == Crewmate and sim.allTasksDone() and sim.players.len > 0:
    return "tasks completed"
  "win condition"

proc survivorRefs(sim: SimServer): seq[PlayerRef] =
  for i in 0 ..< sim.players.len:
    if sim.players[i].alive:
      result.add refForLiveIndex(sim, i)

proc detectGameStart(
  step: ReplayStep,
  ids: var IdentityTable,
  eventsOut: var seq[GameEvent]
) =
  let
    prev = step.preStep
    cur = step.postStep
  if prev.phase == Lobby and cur.phase in {RoleReveal, Playing}:
    ids.observeGameStart(cur)
    var imposterCount = 0
    for player in cur.players:
      if player.role == Imposter:
        inc imposterCount
    eventsOut.add GameEvent(
      kind: gekGameStarted,
      tick: step.tick,
      imposterCount: imposterCount,
      roles: ids.identities()
    )
  if (prev.phase == RoleReveal and cur.phase == Playing) or
      (prev.phase == Lobby and cur.phase == Playing):
    eventsOut.add GameEvent(kind: gekPlayingStarted, tick: step.tick)

proc detectKills(
  step: ReplayStep,
  ids: var IdentityTable,
  eventsOut: var seq[GameEvent]
) =
  let
    prev = step.preStep
    cur = step.postStep
  if cur.bodies.len <= prev.bodies.len:
    return

  var killerIndices: seq[int]
  for i in 0 ..< min(prev.players.len, cur.players.len):
    if cur.players[i].role != Imposter:
      continue
    let
      before = rewardKillsForPlayer(prev, i)
      after = rewardKillsForPlayer(cur, i)
    if after > before:
      killerIndices.add(i)
      if cur.config.killCooldownTicks > 0 and
          cur.players[i].killCooldown != cur.config.killCooldownTicks:
        ids.addWarning(
          "kill counter increased for live index " & $i &
            " without the expected cooldown reset"
        )
  killerIndices.sort()

  let newBodyCount = cur.bodies.len - prev.bodies.len
  if killerIndices.len != newBodyCount:
    ids.addWarning(
      "kill attribution count mismatch at tick " & $step.tick &
        ": " & $killerIndices.len & " killers for " & $newBodyCount & " bodies"
    )

  for pairIndex in 0 ..< min(killerIndices.len, newBodyCount):
    let
      killer = refForLiveIndex(cur, killerIndices[pairIndex])
      body = cur.bodies[prev.bodies.len + pairIndex]
    var victim = ids.byJoinOrder(body.slotId)
    if not victim.isValid:
      victim = ids.byColor(body.color)
    if not victim.isValid:
      ids.addWarning("could not resolve kill victim at tick " & $step.tick)
      continue
    eventsOut.add GameEvent(
      kind: gekKill,
      tick: step.tick,
      killer: killer,
      victim: victim,
      atX: body.x,
      atY: body.y
    )

proc detectTasks(step: ReplayStep, eventsOut: var seq[GameEvent]) =
  let
    prev = step.preStep
    cur = step.postStep
  for taskIndex in 0 ..< min(prev.tasks.len, cur.tasks.len):
    let playerCount =
      min(prev.tasks[taskIndex].completed.len, cur.tasks[taskIndex].completed.len)
    for playerIndex in 0 ..< playerCount:
      if cur.tasks[taskIndex].completed[playerIndex] and
          not prev.tasks[taskIndex].completed[playerIndex]:
        eventsOut.add GameEvent(
          kind: gekTaskCompleted,
          tick: step.tick,
          who: refForLiveIndex(cur, playerIndex),
          taskIndex: taskIndex,
          taskName: cur.tasks[taskIndex].name
        )

proc detectMeeting(
  step: ReplayStep,
  ids: var IdentityTable,
  eventsOut: var seq[GameEvent]
) =
  let
    prev = step.preStep
    cur = step.postStep
  if prev.phase != Playing or cur.phase != Voting:
    return

  var buttonCallers: seq[int]
  for i in 0 ..< min(prev.players.len, cur.players.len):
    if cur.players[i].buttonCallsUsed > prev.players[i].buttonCallsUsed:
      buttonCallers.add(i)

  if buttonCallers.len == 1:
    eventsOut.add GameEvent(
      kind: gekMeetingCalled,
      tick: step.tick,
      caller: refForLiveIndex(cur, buttonCallers[0]),
      reason: mrButton,
      body: noTarget()
    )
    return

  let probe = findBodyReport(cur, step.inputs, step.prevInputs, step.bodyLimit)
  if probe.callerIndex >= 0:
    eventsOut.add GameEvent(
      kind: gekMeetingCalled,
      tick: step.tick,
      caller: refForLiveIndex(cur, probe.callerIndex),
      reason: mrBody,
      body: ids.bodyTarget(cur, probe)
    )
  else:
    ids.addWarning("could not resolve meeting caller at tick " & $step.tick)
    eventsOut.add GameEvent(
      kind: gekMeetingCalled,
      tick: step.tick,
      caller: InvalidPlayerRef,
      reason: mrUnknown,
      body: unknownTarget()
    )

proc detectVotes(
  step: ReplayStep,
  ids: var IdentityTable,
  eventsOut: var seq[GameEvent]
) =
  let
    prev = step.preStep
    cur = step.postStep
    voteCount = min(prev.voteState.votes.len, cur.voteState.votes.len)
  for voterIndex in 0 ..< voteCount:
    if prev.voteState.votes[voterIndex] == -1 and
        cur.voteState.votes[voterIndex] != -1:
      eventsOut.add GameEvent(
        kind: gekVoteCast,
        tick: step.tick,
        voter: refForLiveIndex(cur, voterIndex),
        target: ids.voteTargetFromIndex(
          cur,
          cur.voteState.votes[voterIndex],
          "vote cast at tick " & $step.tick
        )
      )

  if prev.phase == Voting and cur.phase == VoteResult:
    eventsOut.add GameEvent(
      kind: gekVoteEnded,
      tick: step.tick,
      ejected: ids.voteTargetFromIndex(
        cur,
        cur.voteState.ejectedPlayer,
        "vote result at tick " & $step.tick
      ),
      timedOut: prev.voteState.voteTimer <= 1
    )

  if prev.phase == VoteResult and cur.phase == Playing:
    eventsOut.add GameEvent(
      kind: gekEjectionApplied,
      tick: step.tick,
      ejectedPlayer: ids.voteTargetFromIndex(
        prev,
        prev.voteState.ejectedPlayer,
        "vote ejection at tick " & $step.tick
      )
    )

proc detectStuckPenalties(step: ReplayStep, eventsOut: var seq[GameEvent]) =
  let
    prev = step.preStep
    cur = step.postStep
  if prev.phase != Playing:
    return
  for i in 0 ..< min(prev.players.len, cur.players.len):
    if cur.players[i].role == Crewmate and
        cur.players[i].reward < prev.players[i].reward and
        cur.players[i].lastMoveTick == cur.tickCount:
      eventsOut.add GameEvent(
        kind: gekStuckPenalty,
        tick: step.tick,
        penalized: refForLiveIndex(cur, i)
      )

proc detectVents(step: ReplayStep, eventsOut: var seq[GameEvent]) =
  let
    prev = step.preStep
    cur = step.postStep
  if prev.phase != Playing:
    return
  for i in 0 ..< min(prev.players.len, cur.players.len):
    if cur.players[i].role == Imposter and
        prev.players[i].ventCooldown < 30 and
        cur.players[i].ventCooldown == 30 and
        (cur.players[i].x != prev.players[i].x or cur.players[i].y != prev.players[i].y):
      eventsOut.add GameEvent(
        kind: gekVent,
        tick: step.tick,
        venter: refForLiveIndex(cur, i),
        toX: cur.players[i].x,
        toY: cur.players[i].y
      )

proc detectGameOver(step: ReplayStep, eventsOut: var seq[GameEvent]) =
  let
    prev = step.preStep
    cur = step.postStep
  if cur.phase == GameOver and prev.phase != GameOver:
    eventsOut.add GameEvent(
      kind: gekGameOver,
      tick: step.tick,
      winner: cur.winner,
      reasonText: cur.gameOverReason(),
      survivors: cur.survivorRefs()
    )

proc detectChats(step: ReplayStep, eventsOut: var seq[GameEvent]) =
  for chat in step.chats:
    eventsOut.add GameEvent(
      kind: gekChatMessage,
      tick: chat.tick,
      speaker: chat.speaker,
      text: chat.text
    )

proc detect*(step: ReplayStep, ids: var IdentityTable): seq[GameEvent] =
  for leave in step.leaves:
    result.add GameEvent(kind: gekPlayerLeft, tick: step.tick, left: leave.player)

  step.detectGameStart(ids, result)
  step.detectKills(ids, result)
  step.detectTasks(result)
  step.detectMeeting(ids, result)
  step.detectVotes(ids, result)
  step.detectChats(result)
  step.detectStuckPenalties(result)
  step.detectVents(result)
  step.detectGameOver(result)
