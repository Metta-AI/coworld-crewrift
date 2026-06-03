import
  std/[options, strutils],
  ../../../src/crewrift/sim,
  detect,
  driver,
  events,
  identity,
  report

proc stopAfterFirstGameOver(events: seq[GameEvent]): bool =
  for event in events:
    if event.kind == gekGameOver:
      return true
  false

proc extractTimeline*(report: EpisodeReport): EpisodeTimeline =
  var
    driver = initReplayDriver(report.replay, report.config)
    identityTable = initIdentityTable(report.replay)

  while true:
    let maybeStep = driver.advance()
    if maybeStep.isNone:
      break
    let step = maybeStep.get()
    result.events.add detect(step, identityTable)
    if result.events.stopAfterFirstGameOver():
      break

  result.identities = identityTable.identities()
  result.finalTick = driver.sim.tickCount
  result.hashValidated = driver.allHashesMatched
  result.warnings = driver.warnings
  result.warnings.add identityTable.warnings

proc roleName(role: PlayerRole): string =
  case role
  of Crewmate:
    "crewmate"
  of Imposter:
    "imposter"

proc identityLabel(identities: seq[PlayerIdentity], player: PlayerRef): string =
  if not player.isValid:
    return "unknown"
  for identity in identities:
    if identity.joinOrder == player.joinOrder:
      let name =
        if identity.name.len > 0:
          identity.name
        else:
          "slot " & $identity.slot
      return name & " [slot " & $identity.slot & ", " &
        playerColorText(identity.color) & ", " & roleName(identity.role) & "]"
  "slot " & $player.slot

proc targetLabel(identities: seq[PlayerIdentity], target: VoteTarget): string =
  case target.kind
  of vtkPlayer:
    identities.identityLabel(target.player)
  of vtkSkip:
    "skip"
  of vtkNone:
    "none"
  of vtkUnknown:
    "unknown"

proc meetingReasonName(reason: MeetingReason): string =
  case reason
  of mrBody:
    "body"
  of mrButton:
    "button"
  of mrUnknown:
    "unknown"

proc eventLine(identities: seq[PlayerIdentity], event: GameEvent): string =
  let prefix = "[" & $event.tick & "] "
  case event.kind
  of gekGameStarted:
    prefix & "game started with " & $event.imposterCount & " imposters"
  of gekPlayingStarted:
    prefix & "playing started"
  of gekKill:
    prefix & "kill: " & identities.identityLabel(event.killer) & " killed " &
      identities.identityLabel(event.victim) & " at " & $event.atX & "," & $event.atY
  of gekTaskCompleted:
    prefix & "task completed: " & identities.identityLabel(event.who) &
      " completed " & event.taskName & " (#" & $event.taskIndex & ")"
  of gekMeetingCalled:
    prefix & "meeting called: " & meetingReasonName(event.reason) & " by " &
      identities.identityLabel(event.caller) & " body=" &
      identities.targetLabel(event.body)
  of gekVoteCast:
    prefix & "vote cast: " & identities.identityLabel(event.voter) & " -> " &
      identities.targetLabel(event.target)
  of gekVoteEnded:
    prefix & "vote ended: ejected=" & identities.targetLabel(event.ejected) &
      " timedOut=" & $event.timedOut
  of gekEjectionApplied:
    prefix & "ejection applied: " & identities.targetLabel(event.ejectedPlayer)
  of gekChatMessage:
    prefix & "chat: " & identities.identityLabel(event.speaker) & ": " & event.text
  of gekStuckPenalty:
    prefix & "stuck penalty: " & identities.identityLabel(event.penalized)
  of gekVent:
    prefix & "vent: " & identities.identityLabel(event.venter) & " to " &
      $event.toX & "," & $event.toY
  of gekGameOver:
    var survivorLabels: seq[string]
    for survivor in event.survivors:
      survivorLabels.add identities.identityLabel(survivor)
    prefix & "game over: " & roleName(event.winner) & " win (" &
      event.reasonText & "), survivors=" & survivorLabels.join(", ")
  of gekPlayerLeft:
    prefix & "player left: " & identities.identityLabel(event.left)

proc renderTimeline*(timeline: EpisodeTimeline): string =
  var lines: seq[string]
  lines.add "timeline"
  lines.add "  final tick: " & $timeline.finalTick
  lines.add "  hash validated: " & $timeline.hashValidated
  lines.add "  identities:"
  if timeline.identities.len == 0:
    lines.add "    (none)"
  else:
    for identity in timeline.identities:
      lines.add "    slot " & $identity.slot & ": " &
        (if identity.name.len > 0: identity.name else: "(unnamed)") &
        " / " & playerColorText(identity.color) &
        " / " & roleName(identity.role)
  lines.add "  events:"
  if timeline.events.len == 0:
    lines.add "    (none)"
  else:
    for event in timeline.events:
      lines.add "    " & timeline.identities.eventLine(event)
  if timeline.warnings.len > 0:
    lines.add "  warnings:"
    for warning in timeline.warnings:
      lines.add "    " & warning
  lines.join("\n")
