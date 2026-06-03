import
  std/json,
  ../../../src/crewrift/sim,
  csv,
  events,
  identity

type
  EventLogRow* = object
    ts*: int64
    player*: int64
    key*: string
    value*: string

const EventLogColumns* = ["ts", "player", "key", "value"]

proc roleName(role: PlayerRole): string =
  case role
  of Crewmate:
    "crewmate"
  of Imposter:
    "imposter"

proc reasonName(reason: MeetingReason): string =
  case reason
  of mrBody:
    "body"
  of mrButton:
    "button"
  of mrUnknown:
    "unknown"

proc playerSlot(player: PlayerRef): int64 =
  if player.isValid:
    int64(player.slot)
  else:
    -1'i64

proc playerValue(player: PlayerRef): JsonNode =
  if player.isValid:
    %*{"slot": player.slot, "join_order": player.joinOrder}
  else:
    newJNull()

proc targetValue(target: VoteTarget): JsonNode =
  case target.kind
  of vtkPlayer:
    %*{"kind": "player", "player": target.player.playerValue()}
  of vtkSkip:
    %*{"kind": "skip"}
  of vtkNone:
    %*{"kind": "none"}
  of vtkUnknown:
    %*{"kind": "unknown"}

proc row(ts: int, player: int64, key: string, value: JsonNode): EventLogRow =
  EventLogRow(
    ts: int64(ts),
    player: player,
    key: key,
    value: $value
  )

proc row(ts: int, player: PlayerRef, key: string, value: JsonNode): EventLogRow =
  row(ts, player.playerSlot(), key, value)

proc eventLogRows*(timeline: EpisodeTimeline): seq[EventLogRow] =
  ## Converts the in-memory reporter timeline to Coworld event-log rows.
  for event in timeline.events:
    case event.kind
    of gekGameStarted:
      var roles = newJArray()
      for identity in event.roles:
        roles.add(%*{
          "slot": identity.slot,
          "join_order": identity.joinOrder,
          "name": identity.name,
          "address": identity.address,
          "color": int(identity.color),
          "role": identity.role.roleName()
        })
      result.add row(
        event.tick,
        -1,
        "game.started",
        %*{"imposter_count": event.imposterCount, "roles": roles}
      )
    of gekPlayingStarted:
      result.add row(event.tick, -1, "playing.started", %*{})
    of gekKill:
      result.add row(
        event.tick,
        event.killer,
        "kill",
        %*{
          "killer": event.killer.playerValue(),
          "victim": event.victim.playerValue(),
          "x": event.atX,
          "y": event.atY
        }
      )
    of gekTaskCompleted:
      result.add row(
        event.tick,
        event.who,
        "task.completed",
        %*{"task_index": event.taskIndex, "task_name": event.taskName}
      )
    of gekMeetingCalled:
      result.add row(
        event.tick,
        event.caller,
        "meeting.called",
        %*{
          "reason": event.reason.reasonName(),
          "caller": event.caller.playerValue(),
          "body": event.body.targetValue()
        }
      )
    of gekVoteCast:
      result.add row(
        event.tick,
        event.voter,
        "vote.cast",
        %*{"target": event.target.targetValue()}
      )
    of gekVoteEnded:
      result.add row(
        event.tick,
        -1,
        "vote.ended",
        %*{"ejected": event.ejected.targetValue(), "timed_out": event.timedOut}
      )
    of gekEjectionApplied:
      let player =
        if event.ejectedPlayer.kind == vtkPlayer:
          event.ejectedPlayer.player.playerSlot()
        else:
          -1'i64
      result.add row(
        event.tick,
        player,
        "ejection.applied",
        %*{"ejected": event.ejectedPlayer.targetValue()}
      )
    of gekChatMessage:
      result.add row(
        event.tick,
        event.speaker,
        "chat.message",
        %*{"text": event.text}
      )
    of gekStuckPenalty:
      result.add row(event.tick, event.penalized, "stuck.penalty", %*{})
    of gekVent:
      result.add row(
        event.tick,
        event.venter,
        "vent",
        %*{"x": event.toX, "y": event.toY}
      )
    of gekGameOver:
      var survivors = newJArray()
      for survivor in event.survivors:
        survivors.add(survivor.playerValue())
      result.add row(
        event.tick,
        -1,
        "game.over",
        %*{
          "winner": event.winner.roleName(),
          "reason": event.reasonText,
          "survivors": survivors
        }
      )
    of gekPlayerLeft:
      result.add row(event.tick, event.left, "player.left", %*{})

proc renderEventLogCsv*(rows: openArray[EventLogRow]): string =
  result.add csvHeader(EventLogColumns)
  for event in rows:
    result.add csvLine([
      $event.ts,
      $event.player,
      event.key,
      event.value
    ])

proc renderEventLogCsv*(timeline: EpisodeTimeline): string =
  timeline.eventLogRows().renderEventLogCsv()
