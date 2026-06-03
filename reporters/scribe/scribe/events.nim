import
  ../../../src/crewrift/sim,
  identity

type
  GameEventKind* = enum
    gekGameStarted
    gekPlayingStarted
    gekKill
    gekTaskCompleted
    gekMeetingCalled
    gekVoteCast
    gekVoteEnded
    gekEjectionApplied
    gekChatMessage
    gekStuckPenalty
    gekVent
    gekGameOver
    gekPlayerLeft

  MeetingReason* = enum
    mrBody
    mrButton
    mrUnknown

  VoteTargetKind* = enum
    vtkPlayer
    vtkSkip
    vtkNone
    vtkUnknown

  VoteTarget* = object
    case kind*: VoteTargetKind
    of vtkPlayer:
      player*: PlayerRef
    of vtkSkip, vtkNone, vtkUnknown:
      discard

  GameEvent* = object
    tick*: int
    case kind*: GameEventKind
    of gekGameStarted:
      imposterCount*: int
      roles*: seq[PlayerIdentity]
    of gekPlayingStarted:
      discard
    of gekKill:
      killer*, victim*: PlayerRef
      atX*, atY*: int
    of gekTaskCompleted:
      who*: PlayerRef
      taskIndex*: int
      taskName*: string
    of gekMeetingCalled:
      caller*: PlayerRef
      reason*: MeetingReason
      body*: VoteTarget
    of gekVoteCast:
      voter*: PlayerRef
      target*: VoteTarget
    of gekVoteEnded:
      ejected*: VoteTarget
      timedOut*: bool
    of gekEjectionApplied:
      ejectedPlayer*: VoteTarget
    of gekChatMessage:
      speaker*: PlayerRef
      text*: string
    of gekStuckPenalty:
      penalized*: PlayerRef
    of gekVent:
      venter*: PlayerRef
      toX*, toY*: int
    of gekGameOver:
      winner*: PlayerRole
      reasonText*: string
      survivors*: seq[PlayerRef]
    of gekPlayerLeft:
      left*: PlayerRef

  EpisodeTimeline* = object
    identities*: seq[PlayerIdentity]
    events*: seq[GameEvent]
    finalTick*: int
    hashValidated*: bool
    warnings*: seq[string]

proc playerTarget*(player: PlayerRef): VoteTarget =
  VoteTarget(kind: vtkPlayer, player: player)

proc skipTarget*(): VoteTarget =
  VoteTarget(kind: vtkSkip)

proc noTarget*(): VoteTarget =
  VoteTarget(kind: vtkNone)

proc unknownTarget*(): VoteTarget =
  VoteTarget(kind: vtkUnknown)
