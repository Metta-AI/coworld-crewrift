import
  std/[algorithm, exitprocs, json, monotimes, options, os,
    parseopt, random, strutils, times],
  bitworld/[pixelfonts, spriteprotocol, server],
  pixie,
  ../../src/crewrift/sim,
  ../../src/crewrift/tasks as taskAssignments,
  notsus/bedrocks as bedrockAi,
  notsus/protocols,
  notsus/socials,
  notsus/navigation
when defined(profileTracePath):
  import bitworld/profile
else:
  import bitworld/profile except measure

  template measure() {.pragma.}
when not defined(italkalotLibrary):
  import whisky
  when not defined(botHeadless):
    import windy, ../../src/crewrift/common/scales
    import silky except measure
const
  VotingPrompt = staticRead("prompt.md")
  BotReceiveTimeoutMs = 250
  PacketWaitLogAfterMs = 1000
  PacketWaitLogEveryMs = 1000
  ProgressHeartbeatTicks = sim.TargetFps * 10
  RadarPeripheryMargin = 1
  RadarMatchTolerance = 2
  TaskRadarResetTicks = 100
  TaskIconSearchRadius = 2
  TaskIconExpectedSearchRadius = 3
  TaskIconMaxMisses = 4
  TaskIconMaybeMisses = 12
  TaskIconInspectSize = 16
  TaskClearScreenMargin = 8
  TaskIconMissThreshold = 24
  PathLookahead = 18
  PathReuseTicks = 60
  PathDeviationLimit = 16
  TaskInnerMargin = 6
  TaskPreciseApproachRadius = 12
  TaskHoldPadding = 8
  StuckFrameThreshold = 8
  StuckCellSize = 8
  StuckCellThreshold = sim.TargetFps * 2
  UnstuckDuration = sim.TargetFps
  UnstuckPulseTicks = 6
  JiggleDuration = sim.TargetFps + sim.TargetFps div 2
  GhostWanderIntervalTicks = sim.TargetFps * 4
  GhostWanderRadius = 48
  GhostWanderArrivalRadius = 4
  CrewmateSearchRadius = 1
  CrewmateMaxMisses = 8
  CrewmateMinStablePixels = 8
  CrewmateMinBodyPixels = 8
  KillIconMaxMisses = 5
  GhostIconMaxMisses = 3
  GhostIconFrameThreshold = 2
  KillApproachRadius = 3
  BodySearchRadius = 1
  BodyMaxMisses = 9
  BodyMinStablePixels = 6
  BodyMinTintPixels = 6
  GhostSearchRadius = 1
  GhostMaxMisses = 9
  GhostMinStablePixels = 6
  GhostMinTintPixels = 6
  KillTapExtraRadius = 28
  KillTapRepeatTicks = 6
  KillWitnessAvoidRadius = 96
  KillWitnessSelfRadius = 72
  KillWitnessAllowAliveCrew = 1
  CrewRoleColorCount = 6
  ChaseLeadTicks = 6
  ChasePredictionSearchRadius = 12
  PlayerTrackMemoryTicks = sim.TargetFps * 2
  PlayerTrackResetDistance = 96
  FollowingDetectRadius = 128
  FollowingCloseRadius = 16
  StalkerOverlapRadius = 16
  KillerEvidenceRadius = 64
  BodyProximitySusWeight = 2
  BodyProximityPlainMin = 56
  TimeTogetherRadius = 96
  TimeTogetherCreditTicks = sim.TargetFps
  TimeTogetherMaxScore = 90
  StalkerMaxScore = 60
  StandingAroundCreditTicks = sim.TargetFps * 3
  StandingAroundMaxScore = 80
  StandingAroundSusScore = 5
  VentSightRadius = sim.VentRange + 10
  VentSusWindowTicks = sim.TargetFps * 4
  VentSusScore = 300
  FollowingTriggerTicks = 2
  RunawayTriggerRadius = 32
  RunawayCooldownPercent = 80
  MoveAwayTicks = 24
  RunawayLoopTicks = MoveAwayTicks * 5
  RunawayRestartDelayTicks = sim.TargetFps * 8
  EscapeLoopArrivalRadius = 20
  EscapeLoopSearchRadius = 48
  EmergencyButtonCooldownPercent = 95
  EmergencyButtonFollowingTicks = sim.TargetFps div 2
  EmergencyButtonStalkerScore = StalkerMaxScore div 2
  ImposterHuntCooldownPercent = 50
  VoteDeadlineTicks = sim.VoteTimerTicks
  VoteListenBaseTicks = VoteDeadlineTicks div 4
  VoteListenJitterTicks = VoteDeadlineTicks div 16
  VoteCrewmateMinDelayTicks = sim.TargetFps * 8
  VoteCrewmateMaxDelayTicks = sim.TargetFps * 14
  VoteImposterSkipTicks = VoteListenBaseTicks + VoteListenJitterTicks
  VoteRetryTicks = sim.TargetFps div 2
  VoteLlmDeadlineFallbackTicks = sim.TargetFps * 3
  VoteLlmChatGapTicks = 0
  VoteLlmErrorBackoffTicks = sim.TargetFps * 20
  VoteLlmQuotaBackoffTicks = sim.TargetFps * 10 * 60
  VoteLlmMaxCallsPerMeeting = 5
  VoteLlmDuplicateOverlapPercent = 75
  VoteLlmMaxMessageWords = 28
  VoteChatSpeakerSearch = 24
  ProtocolPlayerNameObjectBase = 7000
  ProtocolPlayerNameLabelPrefix = "player label|"
  RoomHistoryMax = 12
  TaskHistoryMax = 8
  VoteCursorConfirmTicks = sim.TargetFps div 2
  VoteMoveAckTicks = sim.TargetFps div 2
  SusVoteMinScore = 75
  CrewPileMinSusScore = 45
  UrgentCrewEffectiveMinScore = 1
  OwnerPairScoreGap = 75
  VotedAgainstSusWeight = 10
  UnsupportedClaimSusScore = 60
  UnsupportedClaimMaxScore = 120
  ReporterEarlyClearScore = 45
  ReporterMidClearScore = 15
  TaskerStationaryVelocity = 1
  BodySuspectRange = 64
  EmergencyButtonStrongSusScore = 250
  BodyEscapeMinGain = 48
  BodyEscapeArrivalRadius = 20
  BodyEscapeCommitTicks = sim.TargetFps * 2
  BodyEscapeIgnoreTicks = sim.TargetFps * 10
  SafeHideSearchRadius = 48
  SafeHideArrivalRadius = 4
  ProwlPointSearchRadius = 48
  InitialConnectWindowMs = 60_000
  ReconnectWindowMs = 60_000
  ReconnectAttemptMs = 1_000
  PlayerScreenX = ScreenWidth div 2
  PlayerScreenY = ScreenHeight div 2
  PlayerWorldOffX = PlayerScreenX
  PlayerWorldOffY = PlayerScreenY
  FullFrameFitMaxErrors = 420
  LocalFrameFitMaxErrors = 320
  FrameFitMinCompared = 12000
  LocalPatchSearchRadius = 8
  PatchSize = 8
  PatchGridW = ScreenWidth div PatchSize
  PatchGridH = ScreenHeight div PatchSize
  PatchHashBase = 16777619'u64
  PatchHashSeed = 14695981039346656037'u64
  PatchMaxMatches = 4096
  PatchTopCandidates = 16
  PatchMinVotes = 3
  PlayerIgnoreRadius = 9
  ProtocolSelfRadius = SpriteSize
  HomeSearchRadius = 20
  PlayerDefaultPort = DefaultPort
  CrewriftGameDir = currentSourcePath()
    .parentDir()
    .parentDir()
    .parentDir()
  NotsusWorkspaceDir = CrewriftGameDir.parentDir()
  ExtractedCrewriftGameDir = NotsusWorkspaceDir / "coworld-crewrift"
  ViewerWindowWidth = 1820
  ViewerWindowHeight = 1060
  ViewerMargin = 16.0'f
  ViewerFrameScale = 4.0'f
  ViewerMapScale = 1.25'f
  ViewerBackground = rgbx(17, 20, 28, 255)
  ViewerPanel = rgbx(33, 38, 50, 255)
  ViewerPanelAlt = rgbx(25, 30, 41, 255)
  ViewerText = rgbx(226, 231, 240, 255)
  ViewerMutedText = rgbx(146, 155, 172, 255)
  ViewerViewport = rgbx(142, 193, 255, 180)
  ViewerButton = rgbx(255, 196, 88, 255)
  ViewerPlayer = rgbx(120, 255, 170, 255)
  ViewerCrew = rgbx(82, 168, 255, 255)
  ViewerImp = rgbx(255, 84, 96, 255)
  ViewerTask = rgbx(255, 132, 146, 255)
  ViewerTaskGuess = rgbx(255, 220, 92, 255)
  ViewerRadarLine = rgbx(255, 220, 92, 210)
  ViewerPath = rgbx(119, 218, 255, 230)
  ViewerWalk = rgbx(46, 61, 75, 255)
  ViewerWall = rgbx(86, 50, 56, 255)
  ViewerUnknown = rgbx(22, 26, 36, 255)
  RadarTaskColor = 8'u8
  KillIconX = 1
  KillIconY = ScreenHeight - SpriteSize - 1
  PlayerColorCount = PlayerColors.len
  VoteCellW = sim.VoteCellW
  VoteCellH = sim.VoteCellH
  VoteColsMax = sim.VoteColsMax
  VoteStartY = sim.VoteStartY
  VoteUnknown = -1
  VoteSkip = -2
  ProtocolMapName = "sprite protocol map"
  SafeHidePoints = [
    (x: 827, y: 427),
    (x: 485, y: 105),
    (x: 648, y: 143),
    (x: 823, y: 190),
    (x: 170, y: 566)
  ]
  ProwlPoints = [
    (x: 334, y: 155),
    (x: 179, y: 345),
    (x: 332, y: 490),
    (x: 486, y: 490),
    (x: 711, y: 501),
    (x: 927, y: 451),
    (x: 758, y: 343),
    (x: 957, y: 306),
    (x: 923, y: 150),
    (x: 731, y: 213),
    (x: 598, y: 343),
    (x: 422, y: 348),
    (x: 591, y: 202)
  ]
  UnstuckMasks = [
    ButtonUp,
    ButtonDown,
    ButtonLeft,
    ButtonRight,
    ButtonUp or ButtonLeft,
    ButtonUp or ButtonRight,
    ButtonDown or ButtonLeft,
    ButtonDown or ButtonRight
  ]
  EscapeLoopStarts = [0, 20, 41, 58]
  EscapeLoopLengths = [20, 21, 17, 16]
  EscapeLoopPoints = [
    (x: 240, y: 369),
    (x: 240, y: 324),
    (x: 244, y: 314),
    (x: 256, y: 303),
    (x: 348, y: 275),
    (x: 384, y: 275),
    (x: 388, y: 277),
    (x: 390, y: 280),
    (x: 425, y: 316),
    (x: 529, y: 358),
    (x: 582, y: 409),
    (x: 586, y: 421),
    (x: 586, y: 434),
    (x: 580, y: 440),
    (x: 503, y: 439),
    (x: 348, y: 429),
    (x: 311, y: 411),
    (x: 254, y: 390),
    (x: 244, y: 380),
    (x: 241, y: 375),
    (x: 637, y: 393),
    (x: 613, y: 410),
    (x: 612, y: 433),
    (x: 612, y: 489),
    (x: 613, y: 492),
    (x: 617, y: 494),
    (x: 651, y: 494),
    (x: 769, y: 510),
    (x: 868, y: 510),
    (x: 872, y: 506),
    (x: 873, y: 478),
    (x: 871, y: 462),
    (x: 837, y: 441),
    (x: 811, y: 417),
    (x: 809, y: 413),
    (x: 805, y: 411),
    (x: 752, y: 388),
    (x: 733, y: 363),
    (x: 730, y: 358),
    (x: 678, y: 358),
    (x: 657, y: 371),
    (x: 757, y: 356),
    (x: 751, y: 363),
    (x: 751, y: 385),
    (x: 753, y: 389),
    (x: 809, y: 413),
    (x: 835, y: 437),
    (x: 837, y: 442),
    (x: 840, y: 443),
    (x: 910, y: 443),
    (x: 916, y: 440),
    (x: 941, y: 418),
    (x: 946, y: 413),
    (x: 946, y: 378),
    (x: 938, y: 360),
    (x: 935, y: 356),
    (x: 903, y: 347),
    (x: 901, y: 347),
    (x: 753, y: 327),
    (x: 751, y: 324),
    (x: 751, y: 298),
    (x: 757, y: 293),
    (x: 806, y: 272),
    (x: 833, y: 224),
    (x: 861, y: 199),
    (x: 885, y: 163),
    (x: 890, y: 158),
    (x: 912, y: 158),
    (x: 947, y: 210),
    (x: 947, y: 253),
    (x: 944, y: 265),
    (x: 899, y: 316),
    (x: 867, y: 329),
    (x: 756, y: 329)
  ]
  SpriteTaskArrowObjectBase = 7000
  ProtocolTextObjectBase = 9000
  ProtocolChatIconObjectBase = 9200
  ProtocolVoteIconObjectBase = 9300
  ProtocolMeetingIconObjectBase = 9800
  SpritePlayerKillProgressObjectId = 10004
  SpritePlayerVoteDotObjectBase = 10100
  SpritePlayerVoteSkipDotObjectBase = 10400

when not defined(italkalotLibrary):
  type ViewerApp = ref object
    when not defined(botHeadless):
      window: Window
      silky: Silky
      contentScale: float32

type
  TileKnowledge = enum
    TileUnknown
    TileOpen
    TileWall

  CameraLock = enum
    NoLock
    LocalPatchMapLock
    FrameMapLock

  TaskState = enum
    TaskNotDoing
    TaskMaybe
    TaskMandatory
    TaskCompleted

  BotRole = enum
    RoleUnknown
    RoleCrewmate
    RoleImposter

  VoteLlmActionKind = enum
    VoteLlmNone
    VoteLlmSay
    VoteLlmFallback

  EvidenceEntry = object
    colorIndex: int
    score: int

  VoteLlmAction = object
    kind: VoteLlmActionKind
    targetColor: int
    message: string
    reason: string

  PlayerTrack = object
    colorIndex: int
    x: int
    y: int

  CameraScore = object
    score: int
    errors: int
    compared: int

  PatchEntry = object
    hash: uint64
    cameraX: int
    cameraY: int

  PatchCandidate = object
    votes: int
    cameraX: int
    cameraY: int

  RadarDot = object
    x: int
    y: int

  IconMatch = object
    x: int
    y: int

  CrewmateMatch = object
    x: int
    y: int
    colorIndex: int
    flipH: bool

  BodyMatch = object
    x: int
    y: int
    colorIndex: int

  GhostMatch = object
    x: int
    y: int
    flipH: bool

  VoteSlot = object
    colorIndex: int
    alive: bool

  VoteChatSpeaker = object
    colorIndex: int
    y: int

  VoteChatLine = ref object
    speakerColor: int
    y: int
    text: string

  TaskRoomCount = object
    room: string
    count: int

  Bot = ref object
    sim: SimServer
    playerSprite: Sprite
    bodySprite: Sprite
    ghostSprite: Sprite
    taskSprite: Sprite
    killButtonSprite: Sprite
    ghostIconSprite: Sprite
    rng: Rand
    configuredName: string
    role: BotRole
    isGhost: bool
    ghostIconFrames: int
    imposterKillReady: bool
    imposterCooldownPercent: int
    imposterGoalIndex: int
    imposterFakeUntilTick: int
    imposterWasHunting: bool
    imposterProwlIndex: int
    fakeTaskIds: seq[int]
    fakeTaskDone: seq[bool]
    fakeTaskGenerated: bool
    fakeTaskHoldIndex: int
    packed: seq[uint8]
    unpacked: seq[uint8]
    navigator: Navigator
    mapTiles: seq[TileKnowledge]
    patchEntries: seq[PatchEntry]
    patchVotes: seq[uint16]
    patchTouched: seq[int]
    patchCandidates: seq[PatchCandidate]
    cameraX: int
    cameraY: int
    lastCameraX: int
    lastCameraY: int
    cameraLock: CameraLock
    cameraScore: int
    localized: bool
    interstitial: bool
    interstitialText: string
    lastGameOverText: string
    lastGameInfoSummary: string
    gameStarted: bool
    serverTick: int
    roundStartTick: int
    roundStartServerTick: int
    bodyReportColors: array[PlayerColorCount, bool]
    meetingCallKind: VoteCallKind
    meetingCallCallerColor: int
    meetingCallBodyColor: int
    meetingCallApplied: bool
    emergencyButtonUsed: bool
    homeSet: bool
    homeX: int
    homeY: int
    ghostWanderGoalSet: bool
    ghostWanderGoalX: int
    ghostWanderGoalY: int
    ghostWanderNextTick: int
    haveMotionSample: bool
    previousPlayerWorldX: int
    previousPlayerWorldY: int
    velocityX: int
    velocityY: int
    stuckFrames: int
    stuckCellX: int
    stuckCellY: int
    stuckCellTicks: int
    unstuckTicks: int
    unstuckMaskIndex: int
    jiggleTicks: int
    jiggleSide: int
    desiredMask: uint8
    controllerMask: uint8
    taskHoldTicks: int
    taskHoldIndex: int
    frameTick: int
    centerMicros: int
    spriteScanMicros: int
    localizeLocalMicros: int
    localizePatchMicros: int
    localizeSpiralMicros: int
    astarMicros: int
    frameBufferLen: int
    framesDropped: int
    skippedFrames: int
    lastDropLogTick: int
    waitingForPacket: bool
    packetWaitStart: MonoTime
    packetWaitMs: int
    packetWaitTimeouts: int
    packetWaitLogMs: int
    lastPacketFrameTick: int
    lastActionFrameTick: int
    lastEventFrameTick: int
    lastHeartbeatFrameTick: int
    lastLoggedRoom: string
    visibleRoomNames: array[PlayerColorCount, string]
    selfRoomHistory: seq[string]
    selfTaskHistory: seq[string]
    selfTaskRooms: seq[TaskRoomCount]
    seenRoomHistory: array[PlayerColorCount, seq[string]]
    bedrockConfigLogged: bool
    lastMask: uint8
    forceActionTap: bool
    lastKillTapTick: int
    lastThought: string
    pendingChat: string
    lastVotingChatSentTick: int
    lastBodySeenX: int
    lastBodySeenY: int
    lastBodyReportX: int
    lastBodyReportY: int
    lastKillBodyX: int
    lastKillBodyY: int
    lastImposterBodyX: int
    lastImposterBodyY: int
    imposterBodyEscapeUntilTick: int
    imposterBodyIgnoreUntilTick: int
    bodySusColor: int
    lastSeenTicks: array[PlayerColorCount, int]
    playerSeenX: array[PlayerColorCount, int]
    playerSeenY: array[PlayerColorCount, int]
    playerVelocityX: array[PlayerColorCount, int]
    playerVelocityY: array[PlayerColorCount, int]
    playerMotionSeen: array[PlayerColorCount, bool]
    bodySeenTicks: array[PlayerColorCount, int]
    stalkerScores: array[PlayerColorCount, int]
    killerScores: array[PlayerColorCount, int]
    taskerScores: array[PlayerColorCount, int]
    votedAgainstScores: array[PlayerColorCount, int]
    unsupportedClaimScores: array[PlayerColorCount, int]
    timeTogetherScores: array[PlayerColorCount, int]
    standingAroundScores: array[PlayerColorCount, int]
    ventScores: array[PlayerColorCount, int]
    taskerTicks: array[PlayerColorCount, int]
    taskerTaskIndex: array[PlayerColorCount, int]
    taskerCredited: seq[bool]
    timeTogetherTicks: array[PlayerColorCount, int]
    standingAroundTicks: array[PlayerColorCount, int]
    nearVentGroup: array[PlayerColorCount, int]
    nearVentIndex: array[PlayerColorCount, int]
    vanishedVentGroup: array[PlayerColorCount, int]
    vanishedVentIndex: array[PlayerColorCount, int]
    vanishedVentTick: array[PlayerColorCount, int]
    playerDisplayNames: array[PlayerColorCount, string]
    selfColorIndex: int
    knownImposters: array[PlayerColorCount, bool]
    socialGraph: SocialMatrix
    socialClaimKeys: seq[string]
    followingTicks: array[PlayerColorCount, int]
    activeHunterColor: int
    moveAwayUntilTick: int
    runawayUntilTick: int
    runawayBackoffColor: int
    runawayBackoffUntilTick: int
    escapeLoopIndex: int
    escapeLoopPointIndex: int
    voting: bool
    votePlayerCount: int
    voteCursor: int
    voteCursorSeenTick: int
    voteNavCursor: int
    voteMoveExpectedCursor: int
    voteMoveSentTick: int
    lastVoteMoveTick: int
    voteConfirmTarget: int
    voteConfirmSentTick: int
    voteSelfSlot: int
    voteTarget: int
    voteStartTick: int
    voteDelayTicks: int
    voteRetryTarget: int
    lastVoteRetryTick: int
    lastUnsafeVoteLogTick: int
    voteChatText: string
    voteChatLines: seq[VoteChatLine]
    voteSlots: array[MaxPlayers, VoteSlot]
    voteChoices: array[PlayerColorCount, int]
    voteCreditedTargets: array[PlayerColorCount, int]
    voteLoggedTarget: int
    voteLoggedReason: string
    voteEvidenceLogged: bool
    voteSaidSomething: bool
    voteImposterDefenseSaid: bool
    voteLlmSayCount: int
    voteLlmCallCount: int
    voteLlmAction: VoteLlmAction
    voteLlmWaiting: bool
    voteLlmNeedsDecision: bool
    voteLlmLastCallTick: int
    voteLlmRequestActive: bool
    voteLlmRequestTag: string
    voteLlmRequestSnapshotKey: string
    voteLlmRequestStartedTick: int
    voteLlmPromptChatText: string
    voteLlmPromptSusText: string
    voteLlmPromptKey: string
    voteLlmPreviousChatText: string
    voteLlmPreviousSusText: string
    voteDeadLoggedStartTick: int
    voteLlmBackoffUntilTick: int
    voteLlmBackoffReason: string
    intent: string
    goalX: int
    goalY: int
    goalIndex: int
    goalName: string
    hasGoal: bool
    hasPathStep: bool
    pathStep: PathStep
    path: seq[PathStep]
    pathCursor: int
    pathGoalX: int
    pathGoalY: int
    pathPlanTick: int
    radarDots: seq[RadarDot]
    spriteRadarDots: seq[RadarDot]
    spriteTaskIcons: seq[IconMatch]
    spriteRadarTasks: seq[bool]
    spriteIconTasks: seq[bool]
    spriteDetectionsReady: bool
    protocolCameraReady: bool
    protocolCameraX: int
    protocolCameraY: int
    protocolMapReady: bool
    protocolWalkabilityReady: bool
    protocolInterstitialReady: bool
    protocolInterstitialText: string
    protocolVotingReady: bool
    protocolVoteScreenSeen: bool
    radarTasks: seq[bool]
    checkoutTasks: seq[bool]
    taskStates: seq[TaskState]
    taskIconMisses: seq[int]
    lastTaskRadarResetTick: int
    visibleTaskIcons: seq[IconMatch]
    visibleCrewmates: seq[CrewmateMatch]
    visibleBodies: seq[BodyMatch]
    visibleGhosts: seq[GhostMatch]
    hadVisibleBody: bool

  VentGroupCount = object
    key: string
    count: int

proc reconnectWindowMs(everConnected: bool): int =
  ## Returns the allowed connection window for the current run state.
  if everConnected:
    ReconnectWindowMs
  else:
    InitialConnectWindowMs

proc gameDir(): string =
  ## Returns the Crewrift game directory.
  let
    sourceDir = currentSourcePath().parentDir().parentDir().parentDir()
    cwd = getCurrentDir()
    candidates = [sourceDir, cwd, cwd.parentDir(), cwd.parentDir().parentDir()]
  for candidate in candidates:
    if fileExists(candidate / "src" / "crewrift.nim"):
      return candidate
  sourceDir

proc bitworldAtlasPath(rootDir: string): string =
  ## Returns the BitWorld client atlas path near one workspace root.
  rootDir / "bitworld" / "client" / "dist" / "atlas.png"

proc atlasPath(): string =
  ## Returns the shared Silky atlas path.
  let
    dir = gameDir()
    cwd = getCurrentDir()
  for candidate in [
    clientDataDir() / "atlas.png",
    bitworldAtlasPath(NotsusWorkspaceDir),
    bitworldAtlasPath(CrewriftGameDir.parentDir()),
    bitworldAtlasPath(dir.parentDir()),
    bitworldAtlasPath(dir.parentDir().parentDir()),
    bitworldAtlasPath(cwd.parentDir()),
    bitworldAtlasPath(cwd.parentDir().parentDir()),
    dir / "dist" / "atlas.png",
    dir / ".." / "client" / "dist" / "atlas.png"
  ]:
    if fileExists(candidate):
      return candidate
  bitworldAtlasPath(cwd.parentDir())

proc sampleColor(index: uint8): ColorRGBX =
  ## Converts one palette index to a Silky color.
  Palette[index and 0x0f].rgbx

proc mapIndexSafe(x, y: int): int =
  ## Returns the map pixel index.
  y * MapWidth + x

proc minCameraX(): int =
  ## Returns the smallest possible centered camera X.
  -ScreenWidth div 2 - SpriteSize

proc maxCameraX(): int =
  ## Returns the largest possible centered camera X.
  MapWidth - ScreenWidth div 2 + SpriteSize

proc minCameraY(): int =
  ## Returns the smallest possible centered camera Y.
  -ScreenHeight div 2 - SpriteSize

proc maxCameraY(): int =
  ## Returns the largest possible centered camera Y.
  MapHeight - ScreenHeight div 2 + SpriteSize

proc cameraIndex(x, y: int): int =
  ## Returns the patch vote index for one camera.
  (y - minCameraY()) * (maxCameraX() - minCameraX() + 1) +
    (x - minCameraX())

proc cameraIndexX(index: int): int =
  ## Returns the camera X coordinate for one vote index.
  minCameraX() + index mod (maxCameraX() - minCameraX() + 1)

proc cameraIndexY(index: int): int =
  ## Returns the camera Y coordinate for one vote index.
  minCameraY() + index div (maxCameraX() - minCameraX() + 1)

proc buttonCameraX(sim: SimServer): int =
  ## Returns the initial camera X guess around the emergency button.
  let button = sim.gameMap.button
  clamp(
    button.x + button.w div 2 - PlayerWorldOffX,
    minCameraX(),
    maxCameraX()
  )

proc buttonCameraY(sim: SimServer): int =
  ## Returns the initial camera Y guess around the emergency button.
  let button = sim.gameMap.button
  clamp(
    button.y + button.h div 2 - PlayerWorldOffY,
    minCameraY(),
    maxCameraY()
  )

proc cameraXForWorld(x: int): int =
  ## Returns the camera X that centers one world X on the player.
  clamp(x - PlayerWorldOffX, minCameraX(), maxCameraX())

proc cameraYForWorld(y: int): int =
  ## Returns the camera Y that centers one world Y on the player.
  clamp(y - PlayerWorldOffY, minCameraY(), maxCameraY())

proc centeredMapRect(centerX, centerY, width, height: int): MapRect =
  ## Builds a map rectangle centered on one point and clamped to the map.
  MapRect(
    x: clamp(centerX - width div 2, 0, max(0, MapWidth - width)),
    y: clamp(centerY - height div 2, 0, max(0, MapHeight - height)),
    w: width,
    h: height
  )

proc protocolMapHome(rooms: openArray[Room]): tuple[x: int, y: int] =
  ## Returns the home point derived from protocol room markers.
  if rooms.len == 0:
    return (MapWidth div 2, MapHeight div 2)
  var index = 0
  for i, room in rooms:
    if room.name.strip().toLowerAscii() == "bridge":
      index = i
      break
  (
    rooms[index].x + rooms[index].w div 2,
    rooms[index].y + rooms[index].h div 2
  )

proc initialProtocolMap(): CrewriftMap =
  ## Builds empty map metadata before the sprite protocol dump arrives.
  let
    homeX = MapWidth div 2
    homeY = MapHeight div 2
  CrewriftMap(
    name: ProtocolMapName,
    width: MapWidth,
    height: MapHeight,
    mapLayer: 0,
    walkLayer: 1,
    wallLayer: 2,
    button: centeredMapRect(homeX, homeY, 28, 34),
    home: MapPoint(x: homeX, y: homeY)
  )

proc inMap(x, y: int): bool =
  ## Returns true when a world pixel is inside the Skeld map.
  x >= 0 and y >= 0 and x < MapWidth and y < MapHeight

proc cameraCanHoldPlayer(cameraX, cameraY: int): bool =
  ## Returns true when a camera candidate can center a real player.
  inMap(cameraX + PlayerWorldOffX, cameraY + PlayerWorldOffY)

proc playerWorldX(bot: Bot): int =
  ## Returns the inferred player collision X coordinate.
  bot.cameraX + PlayerWorldOffX

proc playerWorldY(bot: Bot): int =
  ## Returns the inferred player collision Y coordinate.
  bot.cameraY + PlayerWorldOffY

proc detectionCameraX(bot: Bot): int =
  ## Returns the camera X used to convert protocol detections.
  if bot.protocolCameraReady:
    bot.protocolCameraX
  else:
    bot.cameraX

proc detectionCameraY(bot: Bot): int =
  ## Returns the camera Y used to convert protocol detections.
  if bot.protocolCameraReady:
    bot.protocolCameraY
  else:
    bot.cameraY

proc roomName(bot: Bot): string =
  ## Returns the room containing the inferred player position.
  if not bot.localized:
    return "unknown"
  let
    px = bot.playerWorldX() + CollisionW div 2
    py = bot.playerWorldY() + CollisionH div 2
    room = nearestRoomAt(bot.sim.rooms, px, py)
  if not room.found:
    return "unknown"
  if room.inside:
    room.name
  else:
    "near " & room.name

proc roomAt(
  bot: Bot,
  x, y: int
): tuple[found: bool, inside: bool, name: string] =
  ## Returns the containing or nearest room for one world point.
  nearestRoomAt(bot.sim.rooms, x, y)

proc chatRoomName(name: string): string =
  ## Returns a compact room name for chat.
  for ch in name:
    if ch in {'A' .. 'Z'}:
      result.add(char(ord(ch) - ord('A') + ord('a')))
    elif ch in {'a' .. 'z'} or ch in {'0' .. '9'}:
      result.add(ch)

proc taskCenter(task: TaskStation): tuple[x: int, y: int] =
  ## Returns the center pixel for a task station.
  (task.x + task.w div 2, task.y + task.h div 2)

proc markerNameKey(value: string): string =
  ## Returns a normalized protocol marker label.
  value.strip().toLowerAscii()

proc protocolTaskMarker(label: string): bool =
  ## Returns true when a sprite label identifies a task marker.
  label.markerNameKey() == "task"

proc protocolVentMarker(label: string): bool =
  ## Returns true when a sprite label identifies a vent marker.
  let key = label.markerNameKey()
  key.startsWith("vent") and key != "vents"

proc protocolRoomName(label: string): string =
  ## Returns the room name carried by a sprite map marker.
  if label.startsWith("Room ") and label.len > "Room ".len:
    result = label["Room ".len .. ^1].strip()

proc protocolVentGroupChar(label: string): char =
  ## Returns the compact group id for one protocol vent label.
  let key = label.markerNameKey()
  for i in countdown(key.high, 0):
    if key[i] in {'a' .. 'z', '0' .. '9'}:
      return key[i]
  'v'

proc nextProtocolVentGroupIndex(
  counts: var seq[VentGroupCount],
  label: string
): int =
  ## Returns the next serial index for one repeated vent marker label.
  let key = label.markerNameKey()
  for count in counts.mitems:
    if count.key == key:
      inc count.count
      return count.count
  counts.add(VentGroupCount(key: key, count: 1))
  1

proc taskNameFromRooms(
  rooms: openArray[Room],
  task: TaskStation,
  index: int
): string =
  ## Builds a useful task name from protocol room markers.
  let room = nearestRoomAt(
    rooms,
    task.x + task.w div 2,
    task.y + task.h div 2
  )
  if room.found:
    "Task near " & room.name
  else:
    "Task " & $(index + 1)

proc `<`(a, b: PatchEntry): bool =
  ## Orders patch entries by hash and scan order.
  if a.hash == b.hash:
    if a.cameraY == b.cameraY:
      a.cameraX < b.cameraX
    else:
      a.cameraY < b.cameraY
  else:
    a.hash < b.hash

proc cmpPatchCandidate(a, b: PatchCandidate): int =
  ## Sorts patch candidates by votes and scan order.
  if a.votes != b.votes:
    return cmp(b.votes, a.votes)
  if a.cameraY != b.cameraY:
    return cmp(a.cameraY, b.cameraY)
  cmp(a.cameraX, b.cameraX)

proc ignoreTaskIconPixel(bot: Bot, sx, sy: int): bool =
  ## Returns true when a frame pixel belongs to a matched task icon.
  for icon in bot.visibleTaskIcons:
    let
      ix = sx - icon.x
      iy = sy - icon.y
    if ix < 0 or iy < 0 or
        ix >= bot.taskSprite.width or
        iy >= bot.taskSprite.height:
      continue
    if bot.taskSprite.pixels[bot.taskSprite.spriteIndex(ix, iy)] !=
        TransparentColorIndex:
      return true

proc ignoreCrewmatePixel(bot: Bot, sx, sy: int): bool =
  ## Returns true when a frame pixel belongs to a matched crewmate.
  for crewmate in bot.visibleCrewmates:
    let
      ix = sx - crewmate.x
      iy = sy - crewmate.y
    if ix < 0 or iy < 0 or
        ix >= bot.playerSprite.width or
        iy >= bot.playerSprite.height:
      continue
    let srcX =
      if crewmate.flipH:
        bot.playerSprite.width - 1 - ix
      else:
        ix
    if bot.playerSprite.pixels[bot.playerSprite.spriteIndex(srcX, iy)] !=
        TransparentColorIndex:
      return true

proc ignoreBodyPixel(bot: Bot, sx, sy: int): bool =
  ## Returns true when a frame pixel belongs to a matched dead body.
  for body in bot.visibleBodies:
    let
      ix = sx - body.x
      iy = sy - body.y
    if ix < 0 or iy < 0 or
        ix >= bot.bodySprite.width or
        iy >= bot.bodySprite.height:
      continue
    if bot.bodySprite.pixels[bot.bodySprite.spriteIndex(ix, iy)] !=
        TransparentColorIndex:
      return true

proc ignoreGhostPixel(bot: Bot, sx, sy: int): bool =
  ## Returns true when a frame pixel belongs to a matched ghost.
  for ghost in bot.visibleGhosts:
    let
      ix = sx - ghost.x
      iy = sy - ghost.y
    if ix < 0 or iy < 0 or
        ix >= bot.ghostSprite.width or
        iy >= bot.ghostSprite.height:
      continue
    let srcX =
      if ghost.flipH:
        bot.ghostSprite.width - 1 - ix
      else:
        ix
    if bot.ghostSprite.pixels[bot.ghostSprite.spriteIndex(srcX, iy)] !=
        TransparentColorIndex:
      return true

proc ignoreKillIconPixel(bot: Bot, sx, sy: int): bool =
  ## Returns true when a frame pixel belongs to the imposter kill icon.
  if bot.role != RoleImposter:
    return false
  let
    ix = sx - KillIconX
    iy = sy - KillIconY
  if ix < 0 or iy < 0 or
      ix >= bot.killButtonSprite.width or
      iy >= bot.killButtonSprite.height:
    return false
  bot.killButtonSprite.pixels[
    bot.killButtonSprite.spriteIndex(ix, iy)
  ] != TransparentColorIndex

proc ignoreGhostIconPixel(bot: Bot, sx, sy: int): bool =
  ## Returns true when a frame pixel belongs to the fixed ghost icon.
  if not bot.isGhost and bot.ghostIconFrames == 0:
    return false
  let
    ix = sx - KillIconX
    iy = sy - KillIconY
  if ix < 0 or iy < 0 or
      ix >= bot.ghostIconSprite.width or
      iy >= bot.ghostIconSprite.height:
    return false
  bot.ghostIconSprite.pixels[
    bot.ghostIconSprite.spriteIndex(ix, iy)
  ] != TransparentColorIndex

proc ignoreFramePixel(bot: Bot, frameColor: uint8, sx, sy: int): bool =
  ## Returns true for dynamic screen pixels that are not map evidence.
  if frameColor == RadarTaskColor:
    return true
  if bot.ignoreKillIconPixel(sx, sy):
    return true
  if bot.ignoreGhostIconPixel(sx, sy):
    return true
  if bot.ignoreBodyPixel(sx, sy):
    return true
  if bot.ignoreGhostPixel(sx, sy):
    return true
  if bot.ignoreTaskIconPixel(sx, sy):
    return true
  if bot.ignoreCrewmatePixel(sx, sy):
    return true
  abs(sx - PlayerScreenX) <= PlayerIgnoreRadius and
    abs(sy - PlayerScreenY) <= PlayerIgnoreRadius

proc scoreCamera(
  bot: Bot,
  cameraX,
  cameraY,
  maxErrors: int
): CameraScore {.measure.} =
  ## Counts map-fit errors for a full 128x128 frame rectangle.
  for sy in 0 ..< ScreenHeight:
    for sx in 0 ..< ScreenWidth:
      let frameColor = bot.unpacked[sy * ScreenWidth + sx]
      if bot.ignoreFramePixel(frameColor, sx, sy):
        continue
      let
        mx = cameraX + sx
        my = cameraY + sy
        mapColor =
          if inMap(mx, my):
            bot.sim.mapPixels[mapIndexSafe(mx, my)]
          else:
            MapVoidColor
      if frameColor == mapColor:
        inc result.compared
      elif ShadowMap[mapColor and 0x0f] == frameColor:
        inc result.compared
      else:
        inc result.compared
        inc result.errors
        if result.errors > maxErrors:
          result.score = -result.errors
          return
  result.score = result.compared - result.errors * ScreenWidth

proc patchHashAdd(hash: uint64, color: uint8): uint64 =
  ## Adds one color to an 8 by 8 patch hash.
  hash * PatchHashBase + uint64(color and 0x0f) + 1'u64

proc patchMapColor(bot: Bot, x, y: int): uint8 =
  ## Returns the map color used by patch localization.
  if inMap(x, y):
    bot.sim.mapPixels[mapIndexSafe(x, y)]
  else:
    MapVoidColor

proc mapPatchHash(bot: Bot, x, y: int): uint64 =
  ## Hashes one 8 by 8 map patch.
  result = PatchHashSeed
  for py in 0 ..< PatchSize:
    for px in 0 ..< PatchSize:
      result = patchHashAdd(result, bot.patchMapColor(x + px, y + py))

proc framePatchHash(
  bot: Bot,
  sx,
  sy: int,
  hash: var uint64
): bool =
  ## Hashes one clean 8 by 8 frame patch.
  hash = PatchHashSeed
  for py in 0 ..< PatchSize:
    for px in 0 ..< PatchSize:
      let
        x = sx + px
        y = sy + py
        color = bot.unpacked[y * ScreenWidth + x]
      if bot.ignoreFramePixel(color, x, y):
        return false
      hash = patchHashAdd(hash, color)
  true

proc buildPatchEntries(bot: var Bot) {.measure.} =
  ## Builds a map patch hash index for fast localization.
  let
    minX = minCameraX()
    maxX = maxCameraX() + ScreenWidth - PatchSize
    minY = minCameraY()
    maxY = maxCameraY() + ScreenHeight - PatchSize
    width = maxCameraX() - minCameraX() + 1
    height = maxCameraY() - minCameraY() + 1
  bot.patchEntries = @[]
  bot.patchEntries.setLen((maxX - minX + 1) * (maxY - minY + 1))
  var i = 0
  for y in minY .. maxY:
    for x in minX .. maxX:
      bot.patchEntries[i] = PatchEntry(
        hash: bot.mapPatchHash(x, y),
        cameraX: x,
        cameraY: y
      )
      inc i
  bot.patchEntries.sort()
  bot.patchVotes = newSeq[uint16](width * height)
  bot.patchTouched = @[]
  bot.patchCandidates = @[]

proc patchHashRange(
  entries: openArray[PatchEntry],
  hash: uint64
): tuple[first, last: int] =
  ## Returns the sorted range with a matching patch hash.
  var
    lo = 0
    hi = entries.len
  while lo < hi:
    let mid = (lo + hi) div 2
    if entries[mid].hash < hash:
      lo = mid + 1
    else:
      hi = mid
  result.first = lo
  hi = entries.len
  while lo < hi:
    let mid = (lo + hi) div 2
    if entries[mid].hash > hash:
      hi = mid
    else:
      lo = mid + 1
  result.last = lo

proc addPatchVote(bot: var Bot, x, y: int) =
  ## Adds one patch vote for a camera candidate.
  if x < minCameraX() or x > maxCameraX() or
      y < minCameraY() or y > maxCameraY():
    return
  if not cameraCanHoldPlayer(x, y):
    return
  let index = cameraIndex(x, y)
  if bot.patchVotes[index] == 0:
    bot.patchTouched.add(index)
  bot.patchVotes[index] = bot.patchVotes[index] + 1

proc addNearPatchVote(
  bot: var Bot,
  x,
  y,
  minX,
  maxX,
  minY,
  maxY: int
) =
  ## Adds one patch vote inside a local camera search window.
  if x < minX or x > maxX or y < minY or y > maxY:
    return
  bot.addPatchVote(x, y)

proc collectPatchCandidates(bot: var Bot) =
  ## Collects the best voted camera candidates.
  bot.patchCandidates.setLen(0)
  for index in bot.patchTouched:
    let votes = bot.patchVotes[index].int
    if votes < PatchMinVotes:
      continue
    bot.patchCandidates.add(PatchCandidate(
      votes: votes,
      cameraX: cameraIndexX(index),
      cameraY: cameraIndexY(index)
    ))
  bot.patchCandidates.sort(cmpPatchCandidate)
  if bot.patchCandidates.len > PatchTopCandidates:
    bot.patchCandidates.setLen(PatchTopCandidates)

proc clearPatchVotes(bot: var Bot) =
  ## Clears patch vote counters touched by the last localization pass.
  for index in bot.patchTouched:
    bot.patchVotes[index] = 0
  bot.patchTouched.setLen(0)

proc acceptCameraScore(score: CameraScore, maxErrors: int): bool

proc setCameraLock(
  bot: var Bot,
  x,
  y: int,
  score: CameraScore,
  lock: CameraLock
)

proc locateByPatches(bot: var Bot): bool {.measure.} =
  ## Locates the camera using 8 by 8 map patch votes.
  if bot.patchEntries.len == 0:
    return false
  bot.clearPatchVotes()
  for py in 0 ..< PatchGridH:
    for px in 0 ..< PatchGridW:
      let
        sx = px * PatchSize
        sy = py * PatchSize
      var hash = 0'u64
      if not bot.framePatchHash(sx, sy, hash):
        continue
      let range = patchHashRange(bot.patchEntries, hash)
      if range.last - range.first > PatchMaxMatches:
        continue
      for i in range.first ..< range.last:
        let
          entry = bot.patchEntries[i]
          x = entry.cameraX - sx
          y = entry.cameraY - sy
        bot.addPatchVote(x, y)
  bot.collectPatchCandidates()
  var
    bestScore = CameraScore(score: low(int), errors: high(int), compared: 0)
    bestX = 0
    bestY = 0
  for candidate in bot.patchCandidates:
    let score = bot.scoreCamera(
      candidate.cameraX,
      candidate.cameraY,
      FullFrameFitMaxErrors
    )
    if score.errors < bestScore.errors or
        (score.errors == bestScore.errors and
        score.compared > bestScore.compared):
      bestScore = score
      bestX = candidate.cameraX
      bestY = candidate.cameraY
  bot.clearPatchVotes()
  if not acceptCameraScore(bestScore, FullFrameFitMaxErrors):
    return false
  bot.setCameraLock(bestX, bestY, bestScore, FrameMapLock)
  true

proc acceptCameraScore(score: CameraScore, maxErrors: int): bool =
  ## Returns true when a camera score is good enough to trust.
  score.errors <= maxErrors and score.compared >= FrameFitMinCompared

proc setCameraLock(
  bot: var Bot,
  x,
  y: int,
  score: CameraScore,
  lock: CameraLock
) =
  ## Stores one accepted camera lock.
  bot.cameraX = x
  bot.cameraY = y
  bot.cameraScore = score.score
  bot.cameraLock = lock
  bot.localized = true

proc scanTaskIcons(bot: var Bot)

proc scanCrewmates(bot: var Bot)

proc scanBodies(bot: var Bot)

proc scanGhosts(bot: var Bot)

proc updateRole(bot: var Bot)

proc updateSelfColor(bot: var Bot)

proc logEvent(bot: Bot, message: string)

proc logBlock(bot: Bot, title, text: string)

proc applyMeetingCallToVoting(bot: var Bot)

proc applyVotedAgainstEvidence(bot: var Bot)

proc applyPlainChatClaims(bot: var Bot)

proc updateVotingLlmSnapshots(
  bot: var Bot,
  hadVoting: bool,
  previousChat,
  previousSus: string
)

proc rememberMeetingCall(
  bot: var Bot,
  callerColor,
  bodyColor: int,
  buttonSeen: bool
)

proc rememberPlayerDisplayName(bot: var Bot, objectId: int, label: string)

proc voteChatTextFromLines(lines: openArray[VoteChatLine]): string

proc voteSlotForColor(bot: Bot, colorIndex: int): int

proc voteTargetName(bot: Bot, target: int): string

proc knownDeadVoteCount(bot: Bot): int

proc imposterKnown(bot: Bot): bool

proc voteTargetSafeForRole(bot: Bot, target: int): bool

proc votingEvidenceText(bot: Bot): string

proc votingLlmSnapshotKey(bot: Bot): string

proc randomVoteDelay(bot: var Bot): int

proc passable(bot: Bot, x, y: int): bool

proc pathDistance(bot: var Bot, goalX, goalY: int): int

proc isGameOverText(text: string): bool =
  ## Returns true when interstitial text means the round has ended.
  text == "CREW WINS" or text == "IMPS WIN" or text == "DRAW"

proc clearVotingState(bot: var Bot) =
  ## Clears the parsed voting screen state.
  bot.voting = false
  bot.votePlayerCount = 0
  bot.voteCursor = VoteUnknown
  bot.voteCursorSeenTick = -1
  bot.voteNavCursor = VoteUnknown
  bot.voteMoveExpectedCursor = VoteUnknown
  bot.voteMoveSentTick = -1
  bot.lastVoteMoveTick = -1
  bot.voteConfirmTarget = VoteUnknown
  bot.voteConfirmSentTick = -1
  bot.voteSelfSlot = VoteUnknown
  bot.voteTarget = VoteUnknown
  bot.voteStartTick = -1
  bot.voteDelayTicks = -1
  bot.voteRetryTarget = VoteUnknown
  bot.lastVoteRetryTick = -1
  bot.lastUnsafeVoteLogTick = -1
  bot.voteChatText = ""
  bot.voteChatLines.setLen(0)
  for i in 0 ..< bot.voteSlots.len:
    bot.voteSlots[i].colorIndex = VoteUnknown
    bot.voteSlots[i].alive = false
  for i in 0 ..< bot.voteChoices.len:
    bot.voteChoices[i] = VoteUnknown
  for i in 0 ..< bot.voteCreditedTargets.len:
    bot.voteCreditedTargets[i] = VoteUnknown
  bot.voteLoggedTarget = VoteUnknown
  bot.voteLoggedReason = ""
  bot.voteEvidenceLogged = false
  bot.voteSaidSomething = false
  bot.voteImposterDefenseSaid = false
  bot.voteLlmSayCount = 0
  bot.voteLlmCallCount = 0
  bot.voteLlmAction = VoteLlmAction(
    kind: VoteLlmNone,
    targetColor: VoteUnknown
  )
  bot.voteLlmWaiting = false
  bot.voteLlmNeedsDecision = false
  bot.voteLlmLastCallTick = -1
  bot.voteLlmRequestActive = false
  bot.voteLlmRequestTag = ""
  bot.voteLlmRequestSnapshotKey = ""
  bot.voteLlmRequestStartedTick = -1
  bot.voteLlmPromptChatText = ""
  bot.voteLlmPromptSusText = ""
  bot.voteLlmPromptKey = ""
  bot.voteLlmPreviousChatText = ""
  bot.voteLlmPreviousSusText = ""
  bot.voteDeadLoggedStartTick = -1
  bot.lastVotingChatSentTick = -1

proc clearPath(bot: var Bot) =
  ## Clears the cached A* route.
  bot.hasPathStep = false
  bot.path.setLen(0)
  bot.pathCursor = 0
  bot.pathGoalX = low(int)
  bot.pathGoalY = low(int)
  bot.pathPlanTick = -1

proc navigationReady(bot: Bot): bool =
  ## Returns true when the server walkability map has built navigation data.
  bot.navigator.ready()

proc rebuildNavigation(bot: var Bot) =
  ## Builds the shared navigation maps from server walkability data.
  bot.navigator = initNavigator(bot.sim.walkMask, bot.sim.config)
  bot.clearPath()

proc motionState(bot: Bot): MotionState =
  ## Converts observed bot motion into the shared navigation motion state.
  let
    scale = max(1, bot.sim.config.motionScale)
    maxSpeed = bot.sim.config.maxSpeed
  MotionState(
    x: bot.playerWorldX(),
    y: bot.playerWorldY(),
    velX: clamp(bot.velocityX * scale, -maxSpeed, maxSpeed),
    velY: clamp(bot.velocityY * scale, -maxSpeed, maxSpeed),
    carryX: 0,
    carryY: 0
  )

proc activePlayerTrack(bot: Bot, colorIndex: int): bool =
  ## Returns true when one player has a fresh inferred position.
  colorIndex >= 0 and
    colorIndex < PlayerColorCount and
    bot.playerMotionSeen[colorIndex] and
    bot.frameTick - bot.lastSeenTicks[colorIndex] <= PlayerTrackMemoryTicks

proc trackedPlayerWorld(
  bot: Bot,
  colorIndex: int
): tuple[found: bool, x: int, y: int] =
  ## Returns one player's inferred current world position.
  if not bot.activePlayerTrack(colorIndex):
    return
  let age = max(0, bot.frameTick - bot.lastSeenTicks[colorIndex])
  (
    true,
    bot.playerSeenX[colorIndex] + bot.playerVelocityX[colorIndex] * age,
    bot.playerSeenY[colorIndex] + bot.playerVelocityY[colorIndex] * age
  )

proc rememberPlayerMotion(
  bot: var Bot,
  colorIndex,
  worldX,
  worldY: int
) =
  ## Records one player's observed position and velocity.
  if colorIndex < 0 or colorIndex >= PlayerColorCount:
    return
  if bot.playerMotionSeen[colorIndex] and
      bot.lastSeenTicks[colorIndex] < bot.frameTick:
    let ticks = max(1, bot.frameTick - bot.lastSeenTicks[colorIndex])
    let
      projectedX = bot.playerSeenX[colorIndex] +
        bot.playerVelocityX[colorIndex] * ticks
      projectedY = bot.playerSeenY[colorIndex] +
        bot.playerVelocityY[colorIndex] * ticks
    if abs(worldX - projectedX) + abs(worldY - projectedY) >
        PlayerTrackResetDistance:
      bot.playerVelocityX[colorIndex] = 0
      bot.playerVelocityY[colorIndex] = 0
    else:
      bot.playerVelocityX[colorIndex] =
        (worldX - bot.playerSeenX[colorIndex]) div ticks
      bot.playerVelocityY[colorIndex] =
        (worldY - bot.playerSeenY[colorIndex]) div ticks
  bot.playerSeenX[colorIndex] = worldX
  bot.playerSeenY[colorIndex] = worldY
  bot.playerMotionSeen[colorIndex] = true
  bot.lastSeenTicks[colorIndex] = bot.frameTick

proc playerMovingTowardSelf(bot: Bot, colorIndex: int): bool =
  ## Returns true when a visible player's velocity points toward this bot.
  if colorIndex < 0 or colorIndex >= PlayerColorCount:
    return false
  if colorIndex == bot.selfColorIndex:
    return false
  if bot.lastSeenTicks[colorIndex] != bot.frameTick:
    return false
  let
    dx = bot.playerWorldX() - bot.playerSeenX[colorIndex]
    dy = bot.playerWorldY() - bot.playerSeenY[colorIndex]
    distanceSq = dx * dx + dy * dy
  if distanceSq <= 0 or
      distanceSq > FollowingDetectRadius * FollowingDetectRadius:
    return false
  let
    vx = bot.playerVelocityX[colorIndex]
    vy = bot.playerVelocityY[colorIndex]
    velocitySq = vx * vx + vy * vy
  if velocitySq <= 0:
    return false
  let dot = vx * dx + vy * dy
  dot > 0 and dot * dot * 2 >= velocitySq * distanceSq

proc playerPointSeen(
  bot: Bot,
  colorIndex: int
): tuple[found: bool, x: int, y: int] =
  ## Returns one currently visible player point.
  if colorIndex < 0 or colorIndex >= PlayerColorCount:
    return
  if colorIndex == bot.selfColorIndex and bot.localized:
    return (true, bot.playerWorldX(), bot.playerWorldY())
  if not bot.playerMotionSeen[colorIndex]:
    return
  if bot.lastSeenTicks[colorIndex] != bot.frameTick:
    return
  (true, bot.playerSeenX[colorIndex], bot.playerSeenY[colorIndex])

proc playerMovingTowardPoint(
  bot: Bot,
  colorIndex,
  targetX,
  targetY,
  radius: int
): bool =
  ## Returns true when one visible player's velocity points at a target point.
  if colorIndex < 0 or colorIndex >= PlayerColorCount:
    return false
  if not bot.playerMotionSeen[colorIndex]:
    return false
  if bot.lastSeenTicks[colorIndex] != bot.frameTick:
    return false
  let
    dx = targetX - bot.playerSeenX[colorIndex]
    dy = targetY - bot.playerSeenY[colorIndex]
    distanceSq = dx * dx + dy * dy
  if distanceSq <= 0 or distanceSq > radius * radius:
    return false
  let
    vx = bot.playerVelocityX[colorIndex]
    vy = bot.playerVelocityY[colorIndex]
    velocitySq = vx * vx + vy * vy
  if velocitySq <= 0:
    return false
  let dot = vx * dx + vy * dy
  dot > 0 and dot * dot * 2 >= velocitySq * distanceSq

proc updateStalkerTracking(bot: var Bot) =
  ## Updates per-player stalker evidence from close directed movement.
  if bot.interstitial:
    return
  for stalker in 0 ..< PlayerColorCount:
    if stalker == bot.selfColorIndex:
      continue
    if not bot.playerMotionSeen[stalker]:
      continue
    if bot.lastSeenTicks[stalker] != bot.frameTick:
      continue
    for target in 0 ..< PlayerColorCount:
      if target == stalker:
        continue
      let point = bot.playerPointSeen(target)
      if not point.found:
        continue
      if bot.playerMovingTowardPoint(
        stalker,
        point.x,
        point.y,
        StalkerOverlapRadius
      ):
        bot.stalkerScores[stalker] = min(
          StalkerMaxScore,
          bot.stalkerScores[stalker] + 1
        )
        break

proc taskerCreditIndex(colorIndex, taskIndex: int): int =
  ## Returns the flat credited-task index for one player and task.
  taskIndex * PlayerColorCount + colorIndex

proc resetTaskerProgress(bot: var Bot, colorIndex: int) =
  ## Clears in-progress tasker timing for one player color.
  bot.taskerTicks[colorIndex] = 0
  bot.taskerTaskIndex[colorIndex] = -1

proc taskIndexAtPoint(bot: Bot, x, y: int): int =
  ## Returns the task rectangle containing a world point.
  for i in 0 ..< bot.sim.tasks.len:
    let task = bot.sim.tasks[i]
    if x >= task.x and x < task.x + task.w and
        y >= task.y and y < task.y + task.h:
      return i
  -1

proc playerStationaryForTask(bot: Bot, colorIndex: int): bool =
  ## Returns true when a player is still enough to be doing a task.
  if colorIndex == bot.selfColorIndex:
    return abs(bot.velocityX) + abs(bot.velocityY) <=
      TaskerStationaryVelocity
  if colorIndex < 0 or colorIndex >= PlayerColorCount:
    return false
  if bot.lastSeenTicks[colorIndex] != bot.frameTick:
    return false
  abs(bot.playerVelocityX[colorIndex]) +
    abs(bot.playerVelocityY[colorIndex]) <= TaskerStationaryVelocity

proc resetTaskerTracking(bot: var Bot) =
  ## Clears per-task helper evidence progress.
  bot.taskerCredited = newSeq[bool](bot.sim.tasks.len * PlayerColorCount)
  for colorIndex in 0 ..< PlayerColorCount:
    bot.resetTaskerProgress(colorIndex)

proc updateTaskerTracking(bot: var Bot) =
  ## Updates tasker evidence from stationary players inside task rectangles.
  if bot.interstitial:
    return
  if bot.taskerCredited.len != bot.sim.tasks.len * PlayerColorCount:
    bot.resetTaskerTracking()
  for colorIndex in 0 ..< PlayerColorCount:
    let point = bot.playerPointSeen(colorIndex)
    if not point.found:
      bot.resetTaskerProgress(colorIndex)
      continue
    let taskIndex = bot.taskIndexAtPoint(point.x, point.y)
    if taskIndex < 0 or not bot.playerStationaryForTask(colorIndex):
      bot.resetTaskerProgress(colorIndex)
      continue
    if bot.taskerTaskIndex[colorIndex] != taskIndex:
      bot.taskerTaskIndex[colorIndex] = taskIndex
      bot.taskerTicks[colorIndex] = 0
    let creditIndex = taskerCreditIndex(colorIndex, taskIndex)
    if creditIndex < 0 or creditIndex >= bot.taskerCredited.len:
      continue
    if bot.taskerCredited[creditIndex]:
      continue
    inc bot.taskerTicks[colorIndex]
    if bot.taskerTicks[colorIndex] >= bot.sim.config.taskCompleteTicks:
      inc bot.taskerScores[colorIndex]
      bot.taskerCredited[creditIndex] = true
      bot.taskerTicks[colorIndex] = 0

proc updateTimeTogetherEvidence(bot: var Bot) =
  ## Lowers suspicion for players that stay visible near this bot.
  if bot.interstitial or bot.isGhost:
    return
  let radiusSq = TimeTogetherRadius * TimeTogetherRadius
  for colorIndex in 0 ..< PlayerColorCount:
    if colorIndex == bot.selfColorIndex:
      continue
    let track = bot.trackedPlayerWorld(colorIndex)
    if not track.found:
      if bot.timeTogetherTicks[colorIndex] > 0:
        dec bot.timeTogetherTicks[colorIndex]
      continue
    let
      dx = bot.playerWorldX() - track.x
      dy = bot.playerWorldY() - track.y
      distanceSq = dx * dx + dy * dy
    if distanceSq <= radiusSq:
      inc bot.timeTogetherTicks[colorIndex]
      if bot.timeTogetherTicks[colorIndex] >= TimeTogetherCreditTicks:
        bot.timeTogetherTicks[colorIndex] = 0
        if bot.timeTogetherScores[colorIndex] < TimeTogetherMaxScore:
          inc bot.timeTogetherScores[colorIndex]
    elif bot.timeTogetherTicks[colorIndex] > 0:
      dec bot.timeTogetherTicks[colorIndex]

proc updateStandingAroundEvidence(bot: var Bot) =
  ## Adds suspicion for visible players standing still away from tasks.
  if bot.interstitial:
    return
  for colorIndex in 0 ..< PlayerColorCount:
    if colorIndex == bot.selfColorIndex:
      continue
    let point = bot.playerPointSeen(colorIndex)
    if not point.found:
      bot.standingAroundTicks[colorIndex] = 0
      continue
    if bot.taskIndexAtPoint(point.x, point.y) >= 0 or
        not bot.playerStationaryForTask(colorIndex):
      bot.standingAroundTicks[colorIndex] = 0
      continue
    inc bot.standingAroundTicks[colorIndex]
    if bot.standingAroundTicks[colorIndex] >= StandingAroundCreditTicks:
      bot.standingAroundTicks[colorIndex] = 0
      bot.standingAroundScores[colorIndex] = min(
        StandingAroundMaxScore,
        bot.standingAroundScores[colorIndex] + StandingAroundSusScore
      )

proc nearestVentAt(
  bot: Bot,
  x,
  y: int
): tuple[found: bool, group: int, index: int] =
  ## Returns the nearest vent marker close to one point.
  let radiusSq = VentSightRadius * VentSightRadius
  var bestDistance = radiusSq + 1
  for vent in bot.sim.vents:
    let
      cx = vent.x + vent.w div 2
      cy = vent.y + vent.h div 2
      dx = x + CollisionW div 2 - cx
      dy = y + CollisionH div 2 - cy
      distance = dx * dx + dy * dy
    if distance <= radiusSq and distance < bestDistance:
      bestDistance = distance
      result = (
        true,
        ord(vent.group),
        vent.groupIndex
      )

proc rememberVisibleVentState(
  bot: var Bot,
  colorIndex: int,
  point: tuple[found: bool, x: int, y: int]
) =
  ## Tracks one visible player's current vent proximity.
  if not point.found:
    return
  let vent = bot.nearestVentAt(point.x, point.y)
  if not vent.found:
    bot.nearVentGroup[colorIndex] = -1
    bot.nearVentIndex[colorIndex] = -1
    return
  if bot.vanishedVentGroup[colorIndex] == vent.group and
      bot.vanishedVentIndex[colorIndex] != vent.index and
      bot.vanishedVentTick[colorIndex] >= 0 and
      bot.frameTick - bot.vanishedVentTick[colorIndex] <= VentSusWindowTicks:
    bot.ventScores[colorIndex] += VentSusScore
    bot.logEvent(
      "notsus vent suspicion: " & socialColorName(colorIndex) &
        " disappeared at vent " & $bot.vanishedVentIndex[colorIndex] &
        " and reappeared at vent " & $vent.index
    )
    bot.vanishedVentGroup[colorIndex] = -1
    bot.vanishedVentIndex[colorIndex] = -1
    bot.vanishedVentTick[colorIndex] = -1
  bot.nearVentGroup[colorIndex] = vent.group
  bot.nearVentIndex[colorIndex] = vent.index

proc rememberMissingVentState(bot: var Bot, colorIndex: int) =
  ## Tracks one player that vanished after being seen near a vent.
  if bot.nearVentGroup[colorIndex] < 0:
    return
  if bot.vanishedVentTick[colorIndex] >= 0 and
      bot.frameTick - bot.vanishedVentTick[colorIndex] <= VentSusWindowTicks:
    return
  bot.vanishedVentGroup[colorIndex] = bot.nearVentGroup[colorIndex]
  bot.vanishedVentIndex[colorIndex] = bot.nearVentIndex[colorIndex]
  bot.vanishedVentTick[colorIndex] = bot.frameTick
  bot.nearVentGroup[colorIndex] = -1
  bot.nearVentIndex[colorIndex] = -1

proc updateVentEvidence(bot: var Bot) =
  ## Infers vent suspicion from disappearing and reappearing near linked vents.
  if bot.interstitial:
    return
  for colorIndex in 0 ..< PlayerColorCount:
    if colorIndex == bot.selfColorIndex:
      continue
    let point = bot.playerPointSeen(colorIndex)
    if point.found:
      bot.rememberVisibleVentState(colorIndex, point)
    else:
      bot.rememberMissingVentState(colorIndex)

proc playerCloseToSelf(bot: Bot, colorIndex: int): bool =
  ## Returns true when a tracked player is crowding this bot.
  if colorIndex < 0 or colorIndex >= PlayerColorCount:
    return false
  if colorIndex == bot.selfColorIndex:
    return false
  let track = bot.trackedPlayerWorld(colorIndex)
  if not track.found:
    return false
  let
    dx = bot.playerWorldX() - track.x
    dy = bot.playerWorldY() - track.y
  dx * dx + dy * dy <= FollowingCloseRadius * FollowingCloseRadius

proc roundTimingKnown(bot: Bot): bool =
  ## Returns true when this bot knows the start of the active round.
  bot.roundStartTick >= 0 or (
    bot.serverTick >= 0 and
    bot.roundStartServerTick >= 0
  )

proc roundElapsedTicks(bot: Bot): int =
  ## Returns elapsed active-round ticks using the server clock when available.
  if bot.serverTick >= 0 and bot.roundStartServerTick >= 0:
    return max(0, bot.serverTick - bot.roundStartServerTick)
  if bot.roundStartTick >= 0:
    return max(0, bot.frameTick - bot.roundStartTick)
  0

proc cooldownPastPercent(bot: Bot, percent: int): bool =
  ## Returns true once the kill cooldown has passed one percent.
  if bot.imposterCooldownPercent >= percent:
    return true
  if bot.imposterCooldownPercent >= 0:
    return false
  if not bot.roundTimingKnown() or bot.sim.config.killCooldownTicks <= 0:
    return false
  bot.roundElapsedTicks() * 100 >= bot.sim.config.killCooldownTicks * percent

proc cooldownPastRunawayThreshold(bot: Bot): bool =
  ## Returns true once the kill cooldown is near ready.
  bot.cooldownPastPercent(RunawayCooldownPercent)

proc playerDistanceSqToSelf(bot: Bot, colorIndex: int): int =
  ## Returns the squared distance to one tracked player.
  if colorIndex == bot.selfColorIndex:
    return high(int)
  let track = bot.trackedPlayerWorld(colorIndex)
  if not track.found:
    return high(int)
  let
    dx = bot.playerWorldX() - track.x
    dy = bot.playerWorldY() - track.y
  dx * dx + dy * dy

proc closestPlayerWithin(bot: Bot, radius: int): int =
  ## Returns the closest tracked player inside one radius.
  var bestDistance = radius * radius + 1
  result = VoteUnknown
  for colorIndex in 0 ..< PlayerColorCount:
    let distance = bot.playerDistanceSqToSelf(colorIndex)
    if distance > radius * radius:
      continue
    if distance < bestDistance:
      bestDistance = distance
      result = colorIndex

proc activateEscapeLoop(bot: var Bot, colorIndex: int)

proc updateFollowerTracking(bot: var Bot) =
  ## Updates follower counters without deciding escape behavior.
  if bot.role != RoleCrewmate or bot.isGhost:
    return
  for colorIndex in 0 ..< PlayerColorCount:
    if colorIndex == bot.selfColorIndex:
      continue
    if bot.playerMovingTowardSelf(colorIndex) or
        bot.playerCloseToSelf(colorIndex):
      if bot.followingTicks[colorIndex] < FollowingTriggerTicks:
        inc bot.followingTicks[colorIndex]
    elif bot.followingTicks[colorIndex] > 0:
      dec bot.followingTicks[colorIndex]

proc clearFakeTasks(bot: var Bot) =
  ## Clears the generated imposter shadow task list.
  bot.fakeTaskIds.setLen(0)
  bot.fakeTaskDone.setLen(0)
  bot.fakeTaskGenerated = false
  bot.fakeTaskHoldIndex = -1

proc fakeTaskRectangles(bot: Bot): seq[taskAssignments.TaskRect] =
  ## Returns the current protocol task rectangles for assignment.
  for task in bot.sim.tasks:
    result.add taskAssignments.TaskRect(
      x: task.x,
      y: task.y,
      w: task.w,
      h: task.h
    )

proc ensureFakeTasks(bot: var Bot): bool =
  ## Generates the imposter shadow task list with the sim assignment algorithm.
  if bot.fakeTaskGenerated:
    return bot.fakeTaskIds.len > 0
  if bot.role != RoleImposter or bot.isGhost:
    return false
  if not bot.protocolMapReady or not bot.protocolWalkabilityReady:
    return false
  if bot.sim.tasks.len == 0 or bot.sim.config.tasksPerPlayer <= 0:
    bot.fakeTaskGenerated = true
    return false
  let taskCount = min(bot.sim.config.tasksPerPlayer, bot.sim.tasks.len)
  let assignments = taskAssignments.assignTaskDetails(
    bot.fakeTaskRectangles(),
    bot.sim.walkMask,
    MapWidth,
    MapHeight,
    bot.sim.gameMap.home.x,
    bot.sim.gameMap.home.y,
    1,
    taskCount,
    bot.rng
  )
  bot.fakeTaskGenerated = true
  bot.fakeTaskDone = newSeq[bool](bot.sim.tasks.len)
  if assignments.len == 0:
    return false
  bot.fakeTaskIds = assignments[0].taskIds
  bot.logEvent(
    "notsus fake tasks generated: count=" & $bot.fakeTaskIds.len &
      " route=" & $assignments[0].routeCost
  )
  bot.fakeTaskIds.len > 0

proc fakeTaskPending(bot: Bot, taskIndex: int): bool =
  ## Returns true when an imposter shadow task is still pending.
  if taskIndex < 0 or taskIndex >= bot.fakeTaskDone.len:
    return false
  if bot.fakeTaskDone[taskIndex]:
    return false
  for id in bot.fakeTaskIds:
    if id == taskIndex:
      return true

proc completeFakeTask(bot: var Bot, taskIndex: int) =
  ## Marks one shadow task complete without touching server task state.
  if taskIndex < 0 or taskIndex >= bot.fakeTaskDone.len:
    return
  if not bot.fakeTaskPending(taskIndex):
    return
  bot.fakeTaskDone[taskIndex] = true
  bot.logEvent("notsus fake task complete: " & bot.sim.tasks[taskIndex].name)

proc resetTaskKnowledge(bot: var Bot) =
  ## Resizes task-derived state after protocol map metadata changes.
  bot.radarTasks = newSeq[bool](bot.sim.tasks.len)
  bot.checkoutTasks = newSeq[bool](bot.sim.tasks.len)
  bot.taskStates = newSeq[TaskState](bot.sim.tasks.len)
  bot.taskIconMisses = newSeq[int](bot.sim.tasks.len)
  bot.spriteRadarTasks = newSeq[bool](bot.sim.tasks.len)
  bot.spriteIconTasks = newSeq[bool](bot.sim.tasks.len)
  bot.taskHoldTicks = 0
  bot.taskHoldIndex = -1
  bot.resetTaskerTracking()
  bot.clearFakeTasks()
  bot.goalIndex = -1
  bot.goalName = ""
  bot.hasGoal = false
  bot.clearPath()

proc resetProtocolMap(bot: var Bot) =
  ## Clears map metadata that must arrive from the sprite protocol.
  bot.lastGameInfoSummary = ""
  bot.protocolMapReady = false
  bot.protocolWalkabilityReady = false
  bot.sim.gameMap = initialProtocolMap()
  bot.sim.tasks.setLen(0)
  bot.sim.vents.setLen(0)
  bot.sim.rooms.setLen(0)
  bot.sim.mapPixels = newSeq[uint8](MapWidth * MapHeight)
  bot.sim.walkMask = newSeq[bool](MapWidth * MapHeight)
  bot.sim.wallMask = newSeq[bool](MapWidth * MapHeight)
  bot.mapTiles = newSeq[TileKnowledge](MapWidth * MapHeight)
  bot.resetTaskKnowledge()
  bot.cameraX = bot.sim.buttonCameraX()
  bot.cameraY = bot.sim.buttonCameraY()
  bot.lastCameraX = bot.cameraX
  bot.lastCameraY = bot.cameraY
  bot.cameraLock = NoLock
  bot.localized = false

proc clearMeetingCallState(bot: var Bot) =
  ## Clears the remembered cause for the current meeting.
  bot.meetingCallKind = VoteCalledUnknown
  bot.meetingCallCallerColor = VoteUnknown
  bot.meetingCallBodyColor = VoteUnknown
  bot.meetingCallApplied = false

proc gameTicksLogText(ticks: int): string =
  ## Returns log text for the total game timer.
  if ticks > 0:
    $ticks
  else:
    "none"

proc gameInfoSummary(settings: GameInfoSettings): string =
  ## Returns a compact log summary for learned game settings.
  "game info updated: kill cool down " &
    $settings.killCooldownTicks &
    ", tasks " & $settings.tasksPerPlayer &
    ", vote " & $settings.voteTimerTicks &
    ", game ticks " & settings.maxTicks.gameTicksLogText()

proc applyGameInfoSettings(
  bot: var Bot,
  settings: GameInfoSettings
) =
  ## Applies complete game-info settings learned from the interstitial.
  if not settings.complete:
    return
  bot.sim.config.killCooldownTicks = settings.killCooldownTicks
  bot.sim.config.tasksPerPlayer = settings.tasksPerPlayer
  bot.sim.config.voteTimerTicks = settings.voteTimerTicks
  bot.sim.config.maxTicks = settings.maxTicks
  let summary = settings.gameInfoSummary()
  if summary == bot.lastGameInfoSummary:
    return
  bot.lastGameInfoSummary = summary
  echo summary

proc applyProtocolMap(
  bot: var Bot,
  tasks: seq[TaskStation],
  vents: seq[Vent],
  rooms: seq[Room]
) =
  ## Applies map metadata parsed from the initial sprite dump.
  var gameMap = initialProtocolMap()
  let home = protocolMapHome(rooms)
  gameMap.rooms = rooms
  gameMap.tasks = tasks
  gameMap.vents = vents
  gameMap.home = MapPoint(x: home.x, y: home.y)
  gameMap.button = centeredMapRect(home.x, home.y, 28, 34)
  bot.sim.gameMap = gameMap
  bot.sim.tasks = tasks
  bot.sim.vents = vents
  bot.sim.rooms = rooms
  bot.protocolMapReady = true
  bot.resetTaskKnowledge()
  if not bot.localized:
    bot.cameraX = bot.sim.buttonCameraX()
    bot.cameraY = bot.sim.buttonCameraY()
    bot.lastCameraX = bot.cameraX
    bot.lastCameraY = bot.cameraY

proc updateProtocolMap(bot: var Bot, client: ProtocolClient) {.measure.} =
  ## Parses static map markers and optional map pixels from Sprite v1 state.
  var
    tasks: seq[TaskStation]
    vents: seq[Vent]
    rooms: seq[Room]
    ventCounts: seq[VentGroupCount]
  for item in client.spriteObjectRefs():
    let label = item.sprite.label
    if label == "map" and item.sprite.pixels.len == MapWidth * MapHeight:
      if bot.sim.mapPixels.len != item.sprite.pixels.len:
        bot.sim.mapPixels.setLen(item.sprite.pixels.len)
      for i, color in item.sprite.pixels:
        bot.sim.mapPixels[i] = color
    elif label.protocolTaskMarker():
      tasks.add(TaskStation(
        resourceName: label,
        x: item.x,
        y: item.y,
        w: item.sprite.width,
        h: item.sprite.height
      ))
    elif label.protocolVentMarker():
      vents.add(Vent(
        resourceName: label,
        x: item.x,
        y: item.y,
        w: item.sprite.width,
        h: item.sprite.height,
        group: label.protocolVentGroupChar(),
        groupIndex: ventCounts.nextProtocolVentGroupIndex(label)
      ))
    else:
      let roomName = label.protocolRoomName()
      if roomName.len > 0:
        rooms.add(Room(
          name: roomName,
          x: item.x,
          y: item.y,
          w: item.sprite.width,
          h: item.sprite.height
        ))
  if tasks.len == 0 or vents.len == 0 or rooms.len == 0:
    return
  for i in 0 ..< tasks.len:
    tasks[i].name = taskNameFromRooms(rooms, tasks[i], i)
  bot.applyProtocolMap(tasks, vents, rooms)

proc resetRoundState(bot: var Bot) =
  ## Clears per-round bot state after a detected game-over screen.
  bot.localized = false
  bot.gameStarted = false
  bot.serverTick = -1
  bot.roundStartTick = -1
  bot.roundStartServerTick = -1
  bot.clearMeetingCallState()
  bot.emergencyButtonUsed = false
  bot.homeSet = false
  bot.homeX = 0
  bot.homeY = 0
  bot.ghostWanderGoalSet = false
  bot.ghostWanderGoalX = 0
  bot.ghostWanderGoalY = 0
  bot.ghostWanderNextTick = 0
  bot.role = RoleCrewmate
  bot.isGhost = false
  bot.ghostIconFrames = 0
  bot.imposterKillReady = false
  bot.imposterCooldownPercent = -1
  bot.imposterGoalIndex = -1
  bot.imposterFakeUntilTick = -1
  bot.imposterWasHunting = false
  bot.imposterProwlIndex = -1
  bot.clearFakeTasks()
  bot.cameraLock = NoLock
  bot.cameraScore = 0
  bot.haveMotionSample = false
  bot.velocityX = 0
  bot.velocityY = 0
  bot.stuckFrames = 0
  bot.jiggleTicks = 0
  bot.jiggleSide = 0
  bot.desiredMask = 0
  bot.controllerMask = 0
  bot.lastKillTapTick = -KillTapRepeatTicks
  bot.taskHoldTicks = 0
  bot.taskHoldIndex = -1
  bot.pendingChat = ""
  bot.voteLlmBackoffUntilTick = -1
  bot.voteLlmBackoffReason = ""
  bot.lastLoggedRoom = ""
  bot.selfRoomHistory.setLen(0)
  bot.selfTaskHistory.setLen(0)
  bot.selfTaskRooms.setLen(0)
  for i in 0 ..< bot.visibleRoomNames.len:
    bot.visibleRoomNames[i] = ""
  for rooms in bot.seenRoomHistory.mitems:
    rooms.setLen(0)
  bot.lastBodySeenX = low(int)
  bot.lastBodySeenY = low(int)
  bot.lastBodyReportX = low(int)
  bot.lastBodyReportY = low(int)
  bot.lastKillBodyX = low(int)
  bot.lastKillBodyY = low(int)
  bot.lastImposterBodyX = low(int)
  bot.lastImposterBodyY = low(int)
  bot.imposterBodyEscapeUntilTick = 0
  bot.imposterBodyIgnoreUntilTick = 0
  bot.bodySusColor = VoteUnknown
  bot.selfColorIndex = -1
  bot.activeHunterColor = VoteUnknown
  bot.moveAwayUntilTick = -1
  bot.runawayUntilTick = -1
  bot.runawayBackoffColor = VoteUnknown
  bot.runawayBackoffUntilTick = -1
  bot.escapeLoopIndex = -1
  bot.escapeLoopPointIndex = -1
  bot.clearVotingState()
  for i in 0 ..< bot.lastSeenTicks.len:
    bot.lastSeenTicks[i] = 0
    bot.playerSeenX[i] = 0
    bot.playerSeenY[i] = 0
    bot.playerVelocityX[i] = 0
    bot.playerVelocityY[i] = 0
    bot.playerMotionSeen[i] = false
    bot.followingTicks[i] = 0
    bot.stalkerScores[i] = 0
    bot.killerScores[i] = 0
    bot.taskerScores[i] = 0
    bot.votedAgainstScores[i] = 0
    bot.unsupportedClaimScores[i] = 0
    bot.timeTogetherScores[i] = 0
    bot.standingAroundScores[i] = 0
    bot.ventScores[i] = 0
    bot.taskerTicks[i] = 0
    bot.taskerTaskIndex[i] = -1
    bot.timeTogetherTicks[i] = 0
    bot.standingAroundTicks[i] = 0
    bot.nearVentGroup[i] = -1
    bot.nearVentIndex[i] = -1
    bot.vanishedVentGroup[i] = -1
    bot.vanishedVentIndex[i] = -1
    bot.vanishedVentTick[i] = -1
  for speaker in 0 ..< bot.socialGraph.len:
    for target in 0 ..< bot.socialGraph[speaker].len:
      bot.socialGraph[speaker][target] = 0
  bot.socialClaimKeys.setLen(0)
  bot.taskerCredited = newSeq[bool](bot.sim.tasks.len * PlayerColorCount)
  for i in 0 ..< bot.bodySeenTicks.len:
    bot.bodySeenTicks[i] = 0
  for i in 0 ..< bot.knownImposters.len:
    bot.knownImposters[i] = false
  bot.goalIndex = -1
  bot.goalName = ""
  bot.hasGoal = false
  bot.clearPath()
  bot.lastTaskRadarResetTick = -TaskRadarResetTicks
  bot.radarDots.setLen(0)
  bot.hadVisibleBody = false
  bot.spriteRadarDots.setLen(0)
  bot.spriteTaskIcons.setLen(0)
  bot.spriteRadarTasks.setLen(0)
  bot.spriteIconTasks.setLen(0)
  bot.spriteDetectionsReady = false
  bot.protocolCameraReady = false
  bot.protocolWalkabilityReady = false
  bot.protocolInterstitialReady = false
  bot.protocolInterstitialText = ""
  bot.protocolVotingReady = false
  bot.protocolVoteScreenSeen = false
  bot.visibleTaskIcons.setLen(0)
  bot.visibleCrewmates.setLen(0)
  bot.visibleBodies.setLen(0)
  bot.visibleGhosts.setLen(0)
  if bot.radarTasks.len != bot.sim.tasks.len:
    bot.radarTasks = newSeq[bool](bot.sim.tasks.len)
  else:
    for i in 0 ..< bot.radarTasks.len:
      bot.radarTasks[i] = false
  if bot.checkoutTasks.len != bot.sim.tasks.len:
    bot.checkoutTasks = newSeq[bool](bot.sim.tasks.len)
  else:
    for i in 0 ..< bot.checkoutTasks.len:
      bot.checkoutTasks[i] = false
  if bot.taskStates.len != bot.sim.tasks.len:
    bot.taskStates = newSeq[TaskState](bot.sim.tasks.len)
  else:
    for i in 0 ..< bot.taskStates.len:
      bot.taskStates[i] = TaskNotDoing
  if bot.taskIconMisses.len != bot.sim.tasks.len:
    bot.taskIconMisses = newSeq[int](bot.sim.tasks.len)
  else:
    for i in 0 ..< bot.taskIconMisses.len:
      bot.taskIconMisses[i] = 0

proc reseedLocalizationAtHome(bot: var Bot) =
  ## Re-seeds localization around the remembered home point.
  if bot.homeSet:
    bot.cameraX = cameraXForWorld(bot.homeX)
    bot.cameraY = cameraYForWorld(bot.homeY)
  else:
    bot.cameraX = bot.sim.buttonCameraX()
    bot.cameraY = bot.sim.buttonCameraY()
  bot.lastCameraX = bot.cameraX
  bot.lastCameraY = bot.cameraY
  bot.cameraLock = NoLock
  bot.cameraScore = 0
  bot.localized = false
  bot.haveMotionSample = false
  bot.velocityX = 0
  bot.velocityY = 0
  bot.stuckFrames = 0
  bot.jiggleTicks = 0
  bot.jiggleSide = 0
  bot.desiredMask = 0
  bot.controllerMask = 0
  bot.taskHoldTicks = 0
  bot.taskHoldIndex = -1
  bot.ghostWanderGoalSet = false
  bot.ghostWanderGoalX = 0
  bot.ghostWanderGoalY = 0
  bot.ghostWanderNextTick = 0
  bot.goalIndex = -1
  bot.goalName = ""
  bot.hasGoal = false
  bot.clearPath()

proc locateNearByPatches(bot: var Bot): bool {.measure.} =
  ## Tracks camera near the previous lock using 8 by 8 patch votes.
  if not bot.localized:
    return false
  if bot.patchEntries.len == 0:
    return false
  let
    minX = max(minCameraX(), bot.cameraX - LocalPatchSearchRadius)
    maxX = min(maxCameraX(), bot.cameraX + LocalPatchSearchRadius)
    minY = max(minCameraY(), bot.cameraY - LocalPatchSearchRadius)
    maxY = min(maxCameraY(), bot.cameraY + LocalPatchSearchRadius)
  bot.clearPatchVotes()
  for py in 0 ..< PatchGridH:
    for px in 0 ..< PatchGridW:
      let
        sx = px * PatchSize
        sy = py * PatchSize
      var hash = 0'u64
      if not bot.framePatchHash(sx, sy, hash):
        continue
      let range = patchHashRange(bot.patchEntries, hash)
      if range.last - range.first > PatchMaxMatches:
        continue
      for i in range.first ..< range.last:
        let
          entry = bot.patchEntries[i]
          x = entry.cameraX - sx
          y = entry.cameraY - sy
        bot.addNearPatchVote(x, y, minX, maxX, minY, maxY)
  bot.collectPatchCandidates()
  var
    bestScore = CameraScore(score: low(int), errors: high(int), compared: 0)
    bestX = bot.cameraX
    bestY = bot.cameraY
  for candidate in bot.patchCandidates:
    let score = bot.scoreCamera(
      candidate.cameraX,
      candidate.cameraY,
      LocalFrameFitMaxErrors
    )
    if score.errors < bestScore.errors or
        (score.errors == bestScore.errors and
        score.compared > bestScore.compared):
      bestScore = score
      bestX = candidate.cameraX
      bestY = candidate.cameraY
      if bestScore.errors == 0 and
          bestScore.compared >= FrameFitMinCompared:
        break
  bot.clearPatchVotes()
  if not acceptCameraScore(bestScore, LocalFrameFitMaxErrors):
    return false
  bot.setCameraLock(bestX, bestY, bestScore, LocalPatchMapLock)
  true

proc locateByFrame(bot: var Bot): bool {.measure.} =
  ## Locates the camera by spiraling out from the best prior.
  let patchStart = getMonoTime()
  if bot.locateByPatches():
    bot.localizePatchMicros = int((getMonoTime() - patchStart).inMicroseconds)
    bot.localizeSpiralMicros = 0
    return true
  bot.localizePatchMicros = int((getMonoTime() - patchStart).inMicroseconds)
  let spiralStart = getMonoTime()
  var
    bestScore = CameraScore(
      score: low(int),
      errors: high(int),
      compared: 0
    )
    bestX =
      if bot.gameStarted:
        bot.cameraX
      else:
        bot.sim.buttonCameraX()
    bestY =
      if bot.gameStarted:
        bot.cameraY
      else:
        bot.sim.buttonCameraY()
  let
    minX = minCameraX()
    maxX = maxCameraX()
    minY = minCameraY()
    maxY = maxCameraY()
    seedX = clamp(bestX, minX, maxX)
    seedY = clamp(bestY, minY, maxY)
    maxRadius = max(
      max(abs(seedX - minX), abs(seedX - maxX)),
      max(abs(seedY - minY), abs(seedY - maxY))
    )
  bestX = seedX
  bestY = seedY

  template tryCamera(x, y: int): bool =
    ## Scores one camera candidate if the player could be there.
    if x < minX or x > maxX or y < minY or y > maxY:
      false
    elif not cameraCanHoldPlayer(x, y):
      false
    else:
      let score = bot.scoreCamera(x, y, FullFrameFitMaxErrors)
      if score.errors < bestScore.errors or
          (score.errors == bestScore.errors and
          score.compared > bestScore.compared):
        bestScore = score
        bestX = x
        bestY = y
        bestScore.errors == 0 and
          bestScore.compared >= FrameFitMinCompared
      else:
        false

  var done = tryCamera(seedX, seedY)
  for radius in 1 .. maxRadius:
    if done:
      break
    for dx in -radius .. radius:
      if tryCamera(seedX + dx, seedY - radius):
        done = true
        break
      if tryCamera(seedX + dx, seedY + radius):
        done = true
        break
    if done:
      break
    for dy in -radius + 1 .. radius - 1:
      if tryCamera(seedX - radius, seedY + dy):
        done = true
        break
      if tryCamera(seedX + radius, seedY + dy):
        done = true
        break
  if not acceptCameraScore(bestScore, FullFrameFitMaxErrors):
    bot.cameraLock = NoLock
    bot.cameraScore = bestScore.score
    bot.localized = false
    bot.localizeSpiralMicros =
      int((getMonoTime() - spiralStart).inMicroseconds)
    return false
  bot.setCameraLock(bestX, bestY, bestScore, FrameMapLock)
  bot.localizeSpiralMicros =
    int((getMonoTime() - spiralStart).inMicroseconds)
  true

proc updateLocation(bot: var Bot) {.measure.} =
  ## Updates the camera and player world estimate from the frame.
  let wasInterstitial = bot.interstitial
  bot.spriteScanMicros = 0
  bot.localizeLocalMicros = 0
  bot.localizePatchMicros = 0
  bot.localizeSpiralMicros = 0
  bot.lastCameraX = bot.cameraX
  bot.lastCameraY = bot.cameraY
  let protocolMapReady =
    bot.spriteDetectionsReady and bot.protocolCameraReady
  let protocolTextReady =
    bot.spriteDetectionsReady and bot.protocolInterstitialReady
  let protocolVoteReady =
    bot.spriteDetectionsReady and (
      bot.protocolVotingReady or bot.protocolVoteScreenSeen
    )
  bot.interstitial =
    if protocolMapReady:
      false
    elif protocolTextReady or protocolVoteReady:
      true
    elif bot.spriteDetectionsReady:
      true
    else:
      false
  if bot.interstitial:
    bot.interstitialText =
      if protocolVoteReady:
        "SKIP"
      elif protocolTextReady:
        bot.protocolInterstitialText
      else:
        ""
    bot.visibleTaskIcons.setLen(0)
    bot.visibleCrewmates.setLen(0)
    bot.visibleBodies.setLen(0)
    bot.visibleGhosts.setLen(0)
    if bot.interstitialText.isGameOverText() and
        bot.lastGameOverText != bot.interstitialText:
      bot.resetRoundState()
      bot.lastGameOverText = bot.interstitialText
    elif protocolVoteReady:
      discard
    elif protocolTextReady:
      if bot.voting:
        bot.clearVotingState()
      if bot.interstitialText == "CREWMATE" and bot.role == RoleUnknown:
        bot.role = RoleCrewmate
      elif bot.interstitialText == "IMPS":
        bot.role = RoleImposter
    elif bot.spriteDetectionsReady:
      if bot.voting:
        bot.clearVotingState()
    return
  bot.interstitialText = ""
  bot.lastGameOverText = ""
  if bot.voting:
    bot.clearVotingState()
    bot.lastBodyReportX = low(int)
    bot.lastBodyReportY = low(int)
    bot.bodySusColor = VoteUnknown
  if wasInterstitial:
    bot.roundStartTick = bot.frameTick
    bot.roundStartServerTick = bot.serverTick
    if not protocolMapReady:
      bot.reseedLocalizationAtHome()
  if protocolMapReady:
    bot.cameraX = bot.protocolCameraX
    bot.cameraY = bot.protocolCameraY
    bot.cameraLock = FrameMapLock
    bot.cameraScore = 0
    bot.localized = true
    return
  let spriteStart = getMonoTime()
  bot.updateRole()
  bot.updateSelfColor()
  bot.scanBodies()
  bot.scanGhosts()
  bot.scanCrewmates()
  if bot.role == RoleImposter and not bot.isGhost:
    bot.visibleTaskIcons.setLen(0)
  else:
    bot.scanTaskIcons()
  bot.spriteScanMicros = int((getMonoTime() - spriteStart).inMicroseconds)
  let localStart = getMonoTime()
  if bot.locateNearByPatches():
    bot.localizeLocalMicros =
      int((getMonoTime() - localStart).inMicroseconds)
    return
  bot.localizeLocalMicros =
    int((getMonoTime() - localStart).inMicroseconds)
  discard bot.locateByFrame()

proc rememberVisibleMap(bot: var Bot) {.measure.} =
  ## Copies visible walk and wall knowledge into the coarse map model.
  if not bot.localized:
    return
  for sy in 0 ..< ScreenHeight:
    for sx in 0 ..< ScreenWidth:
      let
        mx = bot.cameraX + sx
        my = bot.cameraY + sy
      if not inMap(mx, my):
        continue
      let idx = mapIndexSafe(mx, my)
      if bot.sim.wallMask[idx]:
        bot.mapTiles[idx] = TileWall
      elif bot.sim.walkMask[idx]:
        bot.mapTiles[idx] = TileOpen

proc isRadarPeriphery(x, y: int): bool =
  ## Returns true for pixels in the task radar strip.
  x <= RadarPeripheryMargin or y <= RadarPeripheryMargin or
    x >= ScreenWidth - 1 - RadarPeripheryMargin or
    y >= ScreenHeight - 1 - RadarPeripheryMargin

proc addRadarDot(dots: var seq[RadarDot], x, y: int) =
  ## Adds one radar dot unless a nearby dot is already present.
  for dot in dots:
    if abs(dot.x - x) <= 1 and abs(dot.y - y) <= 1:
      return
  dots.add(RadarDot(x: x, y: y))

proc scanRadarDots(bot: var Bot) {.measure.} =
  ## Scans screen periphery for yellow task radar pixels.
  bot.radarDots.setLen(0)
  if bot.spriteDetectionsReady:
    for dot in bot.spriteRadarDots:
      bot.radarDots.addRadarDot(dot.x, dot.y)
    return
  for y in 0 ..< ScreenHeight:
    for x in 0 ..< ScreenWidth:
      if not isRadarPeriphery(x, y):
        continue
      if bot.unpacked[y * ScreenWidth + x] == RadarTaskColor:
        bot.radarDots.addRadarDot(x, y)

proc spriteMisses(
  frame: openArray[uint8],
  sprite: Sprite,
  x,
  y: int
): tuple[misses: int, opaque: int] =
  ## Counts opaque sprite pixels that do not match the frame.
  var
    misses = 0
    opaque = 0
  for sy in 0 ..< sprite.height:
    for sx in 0 ..< sprite.width:
      let color = sprite.pixels[sprite.spriteIndex(sx, sy)]
      if color == TransparentColorIndex:
        continue
      inc opaque
      let
        fx = x + sx
        fy = y + sy
      if fx < 0 or fy < 0 or fx >= ScreenWidth or fy >= ScreenHeight:
        inc misses
      elif frame[fy * ScreenWidth + fx] == color:
        discard
      else:
        inc misses
  (misses, opaque)

proc matchesSprite(
  frame: openArray[uint8],
  sprite: Sprite,
  x,
  y: int
): bool =
  ## Returns true if a sprite stringently matches the frame.
  let score = spriteMisses(frame, sprite, x, y)
  score.opaque > 0 and score.misses <= TaskIconMaxMisses

proc maybeMatchesSprite(
  frame: openArray[uint8],
  sprite: Sprite,
  x,
  y: int
): bool =
  ## Returns true when a sprite may be present but imperfect.
  let score = spriteMisses(frame, sprite, x, y)
  score.opaque > 0 and score.misses <= TaskIconMaybeMisses

proc matchesSpriteShadowed(
  frame: openArray[uint8],
  sprite: Sprite,
  x,
  y: int
): bool =
  ## Returns true if a shadowed sprite matches the frame.
  var
    misses = 0
    opaque = 0
  for sy in 0 ..< sprite.height:
    for sx in 0 ..< sprite.width:
      let color = sprite.pixels[sprite.spriteIndex(sx, sy)]
      if color == TransparentColorIndex:
        continue
      inc opaque
      let
        fx = x + sx
        fy = y + sy
      if fx < 0 or fy < 0 or fx >= ScreenWidth or fy >= ScreenHeight:
        inc misses
      elif frame[fy * ScreenWidth + fx] == ShadowMap[color and 0x0f]:
        discard
      else:
        inc misses
      if misses > KillIconMaxMisses:
        return false
  opaque > 0 and misses <= KillIconMaxMisses

proc updateRole(bot: var Bot) {.measure.} =
  ## Updates the known role from fixed status icons.
  let ghostScore = spriteMisses(
    bot.unpacked,
    bot.ghostIconSprite,
    KillIconX,
    KillIconY
  )
  if ghostScore.opaque > 0 and ghostScore.misses <= GhostIconMaxMisses:
    inc bot.ghostIconFrames
    bot.imposterKillReady = false
    if bot.ghostIconFrames >= GhostIconFrameThreshold:
      bot.isGhost = true
      if bot.role == RoleUnknown:
        bot.role = RoleCrewmate
    return
  elif not bot.isGhost:
    bot.ghostIconFrames = 0

  let lit = matchesSprite(
    bot.unpacked,
    bot.killButtonSprite,
    KillIconX,
    KillIconY
  )
  let shaded = matchesSpriteShadowed(
    bot.unpacked,
    bot.killButtonSprite,
    KillIconX,
    KillIconY
  )
  bot.imposterKillReady = lit
  if lit or shaded:
    bot.role = RoleImposter
  elif bot.role == RoleUnknown:
    bot.role = RoleCrewmate

proc addIconMatch(matches: var seq[IconMatch], x, y: int) =
  ## Adds one icon match unless a nearby icon already exists.
  for match in matches:
    if abs(match.x - x) <= 1 and abs(match.y - y) <= 1:
      return
  matches.add(IconMatch(x: x, y: y))

proc protocolBodyColorIndex(label: string): int =
  ## Returns the color index for one sprite protocol body label.
  for i, colorName in PlayerColorNames:
    if label == "body " & colorName:
      return i
  -1

proc protocolActorLabel(
  label: string
): tuple[found: bool, ghost: bool, colorIndex: int, flipH: bool] =
  ## Parses one sprite protocol player or ghost label.
  for i, colorName in PlayerColorNames:
    let playerPrefix = "player " & colorName & " "
    if label == playerPrefix & "right":
      return (true, false, i, false)
    if label == playerPrefix & "left":
      return (true, false, i, true)
    let ghostPrefix = "ghost " & colorName & " "
    if label == ghostPrefix & "right":
      return (true, true, i, false)
    if label == ghostPrefix & "left":
      return (true, true, i, true)

proc protocolSelfObject(x, y, width, height: int): bool =
  ## Returns true when a sprite protocol object is centered on this bot.
  let
    centerX = x + width div 2
    centerY = y + height div 2
  abs(centerX - PlayerScreenX) <= ProtocolSelfRadius and
    abs(centerY - PlayerScreenY) <= ProtocolSelfRadius

proc protocolVoteDotColorIndex(label: string): int =
  ## Returns the voter color encoded in a vote dot sprite label.
  for i, colorName in PlayerColorNames:
    if label == "vote dot " & colorName:
      return i
  VoteUnknown

proc protocolVoteMarkerColorIndex(label: string): int =
  ## Returns the local color encoded in a vote self marker label.
  for i, colorName in PlayerColorNames:
    if label == "vote self marker " & colorName:
      return i
  VoteUnknown

proc protocolVoteCellAt(count, x, y: int): int =
  ## Returns the voting slot containing one protocol object point.
  if count <= 0:
    return VoteUnknown
  let
    cols = min(count, VoteColsMax)
    rows = (count + cols - 1) div cols
    totalW = cols * VoteCellW
    startX = (ScreenWidth - totalW) div 2
    col = (x - startX + VoteCellW div 2) div VoteCellW
    row = (y - (VoteStartY - 1) + VoteCellH div 2) div VoteCellH
  if col < 0 or col >= cols or row < 0 or row >= rows:
    return VoteUnknown
  result = row * cols + col
  if result >= count:
    return VoteUnknown

proc protocolInterstitialLabel(label: string): bool =
  ## Returns true when a text sprite label identifies a modal screen.
  case label
  of "WAITING", "NEED MORE!", "GAME", "STARTING", "GAME IN",
      "PROGRESS", "IMPS", "CREWMATE", "NO ONE", "DIED",
      "WAS KILLED", "DRAW", "CREW WINS", "IMPS WIN":
    true
  else:
    label.startsWith("IN ") or label.gameInfoLabel()

proc protocolProgressBarPercent(label: string): int =
  ## Returns the percent encoded in a protocol progress bar label.
  const Prefix = "progress bar "
  if not label.startsWith(Prefix) or not label.endsWith("%"):
    return -1
  let start = Prefix.len
  if start >= label.high:
    return -1
  result = 0
  for i in start ..< label.high:
    if label[i] notin {'0' .. '9'}:
      return -1
    result = result * 10 + ord(label[i]) - ord('0')
  if result < 0 or result > 100:
    return -1

proc applyProtocolVotingState(
  bot: var Bot,
  playerCount,
  cursor,
  selfSlot: int,
  slots: array[MaxPlayers, VoteSlot],
  choices: array[PlayerColorCount, int],
  chatLines: openArray[VoteChatLine]
): bool {.measure.} =
  ## Applies a voting screen parsed from sprite protocol metadata.
  if playerCount <= 0 or playerCount > MaxPlayers:
    return false
  var seenColors: array[PlayerColorCount, bool]
  for i in 0 ..< playerCount:
    let colorIndex = slots[i].colorIndex
    if colorIndex < 0 or colorIndex >= PlayerColorCount:
      return false
    if seenColors[colorIndex]:
      return false
    seenColors[colorIndex] = true
  let
    hadVoting = bot.voting and bot.voteStartTick >= 0
    previousChat = bot.voteChatText
    previousSus = bot.votingEvidenceText()
    startTick =
      if hadVoting:
        bot.voteStartTick
      else:
        bot.frameTick
    previousDelay = bot.voteDelayTicks
    previousRetryTarget = bot.voteRetryTarget
    previousRetryTick = bot.lastVoteRetryTick
    previousUnsafeVoteLogTick = bot.lastUnsafeVoteLogTick
    previousCursor = bot.voteCursor
    previousLoggedTarget = bot.voteLoggedTarget
    previousLoggedReason = bot.voteLoggedReason
    previousEvidenceLogged = bot.voteEvidenceLogged
    previousNavCursor = bot.voteNavCursor
    previousMoveExpected = bot.voteMoveExpectedCursor
    previousMoveSentTick = bot.voteMoveSentTick
    previousMoveTick = bot.lastVoteMoveTick
    previousConfirmTarget = bot.voteConfirmTarget
    previousConfirmSentTick = bot.voteConfirmSentTick
    previousSaidSomething = bot.voteSaidSomething
    previousLlmSayCount = bot.voteLlmSayCount
    previousLlmCallCount = bot.voteLlmCallCount
    previousLlmAction = bot.voteLlmAction
    previousLlmWaiting = bot.voteLlmWaiting
    previousLlmNeedsDecision = bot.voteLlmNeedsDecision
    previousLlmLastCallTick = bot.voteLlmLastCallTick
    previousLlmRequestActive = bot.voteLlmRequestActive
    previousLlmRequestTag = bot.voteLlmRequestTag
    previousLlmRequestSnapshotKey = bot.voteLlmRequestSnapshotKey
    previousLlmRequestStartedTick = bot.voteLlmRequestStartedTick
    previousLlmPromptChatText = bot.voteLlmPromptChatText
    previousLlmPromptSusText = bot.voteLlmPromptSusText
    previousLlmPromptKey = bot.voteLlmPromptKey
    previousLlmPreviousChatText = bot.voteLlmPreviousChatText
    previousLlmPreviousSusText = bot.voteLlmPreviousSusText
    previousDeadLoggedStartTick = bot.voteDeadLoggedStartTick
    previousChatSentTick = bot.lastVotingChatSentTick
  var previousVoteCredits: array[PlayerColorCount, int]
  for i in 0 ..< previousVoteCredits.len:
    previousVoteCredits[i] = VoteUnknown
  if hadVoting:
    previousVoteCredits = bot.voteCreditedTargets
  bot.clearVotingState()
  bot.voting = true
  bot.votePlayerCount = playerCount
  bot.voteStartTick = startTick
  bot.voteDelayTicks =
    if previousDelay >= 0:
      previousDelay
    else:
      bot.randomVoteDelay()
  bot.voteRetryTarget = previousRetryTarget
  bot.lastVoteRetryTick = previousRetryTick
  bot.lastUnsafeVoteLogTick = previousUnsafeVoteLogTick
  bot.voteLoggedTarget = previousLoggedTarget
  bot.voteLoggedReason = previousLoggedReason
  bot.voteEvidenceLogged = previousEvidenceLogged
  bot.voteSaidSomething = previousSaidSomething
  bot.voteLlmSayCount = previousLlmSayCount
  bot.voteLlmCallCount = previousLlmCallCount
  bot.voteCreditedTargets = previousVoteCredits
  bot.voteLlmAction = previousLlmAction
  bot.voteLlmWaiting = previousLlmWaiting
  bot.voteLlmNeedsDecision = previousLlmNeedsDecision
  bot.voteLlmLastCallTick = previousLlmLastCallTick
  bot.voteLlmRequestActive = previousLlmRequestActive
  bot.voteLlmRequestTag = previousLlmRequestTag
  bot.voteLlmRequestSnapshotKey = previousLlmRequestSnapshotKey
  bot.voteLlmRequestStartedTick = previousLlmRequestStartedTick
  bot.voteLlmPromptChatText = previousLlmPromptChatText
  bot.voteLlmPromptSusText = previousLlmPromptSusText
  bot.voteLlmPromptKey = previousLlmPromptKey
  bot.voteLlmPreviousChatText = previousLlmPreviousChatText
  bot.voteLlmPreviousSusText = previousLlmPreviousSusText
  bot.voteDeadLoggedStartTick = previousDeadLoggedStartTick
  bot.lastVotingChatSentTick =
    if hadVoting:
      previousChatSentTick
    else:
      -1
  bot.voteCursor = cursor
  bot.voteNavCursor =
    if not hadVoting:
      cursor
    elif previousNavCursor != VoteUnknown:
      previousNavCursor
    else:
      cursor
  bot.voteMoveExpectedCursor = previousMoveExpected
  bot.voteMoveSentTick = previousMoveSentTick
  bot.lastVoteMoveTick = previousMoveTick
  bot.voteConfirmTarget = previousConfirmTarget
  bot.voteConfirmSentTick = previousConfirmSentTick
  bot.voteSelfSlot = selfSlot
  for i in 0 ..< playerCount:
    bot.voteSlots[i] = slots[i]
  for i in 0 ..< bot.voteChoices.len:
    bot.voteChoices[i] = choices[i]
  if cursor != VoteUnknown:
    bot.voteCursorSeenTick = bot.frameTick
    if not hadVoting:
      bot.voteNavCursor = cursor
      bot.voteMoveExpectedCursor = VoteUnknown
      bot.voteMoveSentTick = -1
    elif previousMoveExpected != VoteUnknown:
      if cursor == previousMoveExpected or
          (
            previousCursor != VoteUnknown and
            cursor != previousCursor
          ) or
          (
            previousMoveSentTick >= 0 and
            bot.frameTick - previousMoveSentTick >= VoteMoveAckTicks
          ):
        bot.voteNavCursor = cursor
        bot.voteMoveExpectedCursor = VoteUnknown
        bot.voteMoveSentTick = -1
    elif previousCursor != VoteUnknown and cursor != previousCursor:
      bot.voteNavCursor = cursor
  if selfSlot >= 0 and selfSlot < playerCount:
    bot.selfColorIndex = slots[selfSlot].colorIndex
  for line in chatLines:
    bot.voteChatLines.add(line)
  bot.voteChatText = voteChatTextFromLines(bot.voteChatLines)
  bot.applyMeetingCallToVoting()
  bot.applyVotedAgainstEvidence()
  bot.applyPlainChatClaims()
  bot.updateVotingLlmSnapshots(hadVoting, previousChat, previousSus)
  if not hadVoting:
    bot.logEvent(
      "notsus voting protocol detected: count=" & $playerCount &
        " cursor=" & bot.voteTargetName(cursor) &
        " self=" & bot.voteTargetName(selfSlot) &
        " chat=" & $bot.voteChatText.len
    )
  true

proc updateProtocolDetections(bot: var Bot, client: ProtocolClient) {.measure.} =
  ## Caches structured task objects from the current sprite frame.
  let currentServerTick = client.serverTick()
  if currentServerTick >= 0:
    bot.serverTick = currentServerTick
  bot.applyGameInfoSettings(client.gameInfoSettings())
  bot.spriteDetectionsReady = true
  bot.protocolCameraReady = false
  bot.protocolInterstitialReady = false
  bot.protocolInterstitialText = ""
  bot.protocolVotingReady = false
  bot.imposterCooldownPercent = -1
  bot.spriteRadarDots.setLen(0)
  bot.spriteTaskIcons.setLen(0)
  if bot.spriteDetectionsReady:
    bot.visibleCrewmates.setLen(0)
    bot.visibleBodies.setLen(0)
    bot.visibleGhosts.setLen(0)
  if bot.spriteRadarTasks.len != bot.sim.tasks.len:
    bot.spriteRadarTasks = newSeq[bool](bot.sim.tasks.len)
  else:
    for i in 0 ..< bot.spriteRadarTasks.len:
      bot.spriteRadarTasks[i] = false
  if bot.spriteIconTasks.len != bot.sim.tasks.len:
    bot.spriteIconTasks = newSeq[bool](bot.sim.tasks.len)
  else:
    for i in 0 ..< bot.spriteIconTasks.len:
      bot.spriteIconTasks[i] = false
  if not bot.spriteDetectionsReady:
    return
  if not bot.protocolMapReady:
    bot.updateProtocolMap(client)
  if client.walkabilityReady and not bot.protocolWalkabilityReady:
    if client.walkabilityWidth != MapWidth or
        client.walkabilityHeight != MapHeight or
        client.walkabilityMask.len != MapWidth * MapHeight:
      raise newException(
        ValueError,
        "sprite protocol walkability map size did not match the game map"
      )
    if bot.sim.walkMask.len != client.walkabilityMask.len:
      bot.sim.walkMask.setLen(client.walkabilityMask.len)
    if bot.sim.wallMask.len != client.walkabilityMask.len:
      bot.sim.wallMask.setLen(client.walkabilityMask.len)
    for i in 0 ..< client.walkabilityMask.len:
      bot.sim.walkMask[i] = client.walkabilityMask[i]
      bot.sim.wallMask[i] = not client.walkabilityMask[i]
    bot.rebuildNavigation()
    bot.protocolWalkabilityReady = true
  if client.mapCameraReady:
    if not bot.protocolMapReady:
      raise newException(
        ValueError,
        "sprite protocol did not include required task, vent, and room " &
          "map marker sprites"
      )
    if not bot.protocolWalkabilityReady:
      raise newException(
        ValueError,
        "sprite protocol did not include required sprite labeled " &
          "\"walkability map\""
      )
    bot.protocolCameraReady = true
    bot.protocolCameraX = client.mapCameraX
    bot.protocolCameraY = client.mapCameraY
  var
    voteSlots: array[MaxPlayers, VoteSlot]
    voteChoices: array[PlayerColorCount, int]
    votePlayerCount = 0
    voteCursor = VoteUnknown
    voteCursorSeen = false
    voteCursorIsSkip = false
    voteCursorX = 0
    voteCursorY = 0
    voteSelfSlot = VoteUnknown
    voteSelfSeen = false
    voteSelfX = 0
    voteSelfY = 0
    voteSelfColor = VoteUnknown
    voteScreenSeen = false
    meetingCallerColor = VoteUnknown
    meetingBodyColor = VoteUnknown
    meetingButtonSeen = false
    voteGeometryFallbackUsed = false
    chatSpeakers: array[VoteChatVisibleMessages, VoteChatSpeaker]
    chatSpeakerCount = 0
    chatLines: seq[VoteChatLine]
    revealImposters: array[PlayerColorCount, bool]
  for i in 0 ..< voteSlots.len:
    voteSlots[i].colorIndex = VoteUnknown
  for i in 0 ..< voteChoices.len:
    voteChoices[i] = VoteUnknown
  for item in client.spriteObjectRefs():
    let label = item.sprite.label
    bot.rememberPlayerDisplayName(item.objectId, label)
    case label
    of "task bubble":
      bot.spriteTaskIcons.addIconMatch(item.x, item.y)
      let index = item.objectId - TaskObjectBase
      if index >= 0 and index < bot.spriteIconTasks.len:
        bot.spriteIconTasks[index] = true
    of "task arrow":
      bot.spriteRadarDots.addRadarDot(item.x, item.y)
      let index = item.objectId - SpriteTaskArrowObjectBase
      if index >= 0 and index < bot.spriteRadarTasks.len:
        bot.spriteRadarTasks[index] = true
    of "imposter icon":
      bot.role = RoleImposter
      bot.isGhost = false
      bot.imposterKillReady = true
      bot.imposterCooldownPercent = 100
    of "imposter icon cooldown":
      bot.role = RoleImposter
      bot.isGhost = false
      bot.imposterKillReady = false
    of "ghost icon":
      bot.isGhost = true
    of "SKIP":
      voteScreenSeen = true
    of "vote timer", "vote chat background":
      voteScreenSeen = true
    else:
      if item.objectId == SpritePlayerKillProgressObjectId:
        let percent = protocolProgressBarPercent(label)
        if percent >= 0:
          bot.imposterCooldownPercent = percent
      if protocolInterstitialLabel(label) and
          bot.protocolInterstitialText.len == 0:
        bot.protocolInterstitialReady = true
        bot.protocolInterstitialText = label
      let actor = protocolActorLabel(label)
      if actor.found:
        if not actor.ghost and
            actor.colorIndex >= 0 and
            actor.colorIndex < revealImposters.len:
          revealImposters[actor.colorIndex] = true
        let
          voteIconObject =
            item.objectId >= ProtocolVoteIconObjectBase and
              item.objectId < ProtocolVoteIconObjectBase + MaxPlayers
          voteSlot = protocolVoteCellAt(MaxPlayers, item.x, item.y)
        if not voteIconObject and
            voteSlot != VoteUnknown and
            voteSlot < voteSlots.len and
            voteSlots[voteSlot].colorIndex == VoteUnknown:
          voteSlots[voteSlot].colorIndex = actor.colorIndex
          voteSlots[voteSlot].alive = true
          votePlayerCount = max(votePlayerCount, voteSlot + 1)
          voteGeometryFallbackUsed = true
        let selfActor = protocolSelfObject(
          item.x,
          item.y,
          item.sprite.width,
          item.sprite.height
        )
        if selfActor:
          bot.selfColorIndex = actor.colorIndex
          bot.isGhost = actor.ghost
        let knownSelfActor =
          bot.selfColorIndex >= 0 and
            actor.colorIndex == bot.selfColorIndex
        if selfActor or knownSelfActor:
          discard
        elif actor.ghost:
          bot.visibleGhosts.add(GhostMatch(
            x: item.x,
            y: item.y,
            flipH: actor.flipH
          ))
        else:
          bot.visibleCrewmates.add(CrewmateMatch(
            x: item.x,
            y: item.y,
            colorIndex: actor.colorIndex,
            flipH: actor.flipH
          ))
          if item.objectId == ProtocolMeetingIconObjectBase:
            meetingCallerColor = actor.colorIndex
          bot.rememberPlayerMotion(
            actor.colorIndex,
            bot.detectionCameraX() + item.x + SpriteDrawOffX,
            bot.detectionCameraY() + item.y + SpriteDrawOffY
          )
      if item.objectId >= ProtocolVoteIconObjectBase and
          item.objectId < ProtocolVoteIconObjectBase + MaxPlayers:
        let slot = item.objectId - ProtocolVoteIconObjectBase
        if actor.found:
          voteSlots[slot].colorIndex = actor.colorIndex
          voteSlots[slot].alive = true
          votePlayerCount = max(votePlayerCount, slot + 1)
      if item.objectId >= ProtocolChatIconObjectBase and
          item.objectId < ProtocolChatIconObjectBase + VoteChatVisibleMessages:
        if actor.found and chatSpeakerCount < chatSpeakers.len:
          chatSpeakers[chatSpeakerCount] = VoteChatSpeaker(
            colorIndex: actor.colorIndex,
            y: item.y
          )
          inc chatSpeakerCount
      let bodyColor = protocolBodyColorIndex(label)
      if bodyColor >= 0:
        let
          voteIconObject =
            item.objectId >= ProtocolVoteIconObjectBase and
              item.objectId < ProtocolVoteIconObjectBase + MaxPlayers
          voteSlot = protocolVoteCellAt(MaxPlayers, item.x, item.y)
        if not voteIconObject and
            voteSlot != VoteUnknown and
            voteSlot < voteSlots.len and
            voteSlots[voteSlot].colorIndex == VoteUnknown:
          voteSlots[voteSlot].colorIndex = bodyColor
          voteSlots[voteSlot].alive = false
          votePlayerCount = max(votePlayerCount, voteSlot + 1)
          voteGeometryFallbackUsed = true
        bot.visibleBodies.add(BodyMatch(
          x: item.x,
          y: item.y,
          colorIndex: bodyColor
        ))
        if bodyColor < bot.bodySeenTicks.len:
          bot.bodySeenTicks[bodyColor] = bot.frameTick
        if item.objectId == ProtocolMeetingIconObjectBase + 1:
          meetingBodyColor = bodyColor
        if item.objectId >= ProtocolVoteIconObjectBase and
            item.objectId < ProtocolVoteIconObjectBase + MaxPlayers:
          let slot = item.objectId - ProtocolVoteIconObjectBase
          voteSlots[slot].colorIndex = bodyColor
          voteSlots[slot].alive = false
          votePlayerCount = max(votePlayerCount, slot + 1)
      let voteDotColor = protocolVoteDotColorIndex(label)
      if voteDotColor >= 0:
        voteScreenSeen = true
        if item.objectId >= SpritePlayerVoteDotObjectBase and
            item.objectId < SpritePlayerVoteDotObjectBase +
              MaxPlayers * MaxPlayers:
          let target = (item.objectId - SpritePlayerVoteDotObjectBase) div
            MaxPlayers
          if target >= 0 and target < MaxPlayers:
            voteChoices[voteDotColor] = target
        elif item.objectId >= SpritePlayerVoteSkipDotObjectBase and
            item.objectId < SpritePlayerVoteSkipDotObjectBase + MaxPlayers:
          voteChoices[voteDotColor] = VoteSkip
      if label == "vote cursor" or label == "vote skip cursor":
        voteScreenSeen = true
        voteCursorSeen = true
        voteCursorIsSkip = label == "vote skip cursor"
        voteCursorX = item.x
        voteCursorY = item.y
      let markerColor = protocolVoteMarkerColorIndex(label)
      if markerColor >= 0:
        voteScreenSeen = true
        voteSelfSeen = true
        voteSelfColor = markerColor
        voteSelfX = item.x
        voteSelfY = item.y
      if item.objectId == ProtocolMeetingIconObjectBase + 1 and
          label == "meeting button":
        meetingButtonSeen = true
      if item.objectId >= ProtocolTextObjectBase and
          item.objectId < ProtocolTextObjectBase + VoteChatVisibleMessages +
            2 and
          label.len > 0 and label != "SKIP" and
          not protocolInterstitialLabel(label):
        chatLines.add(VoteChatLine(
          speakerColor: VoteUnknown,
          y: item.y,
          text: label
        ))
  if meetingCallerColor != VoteUnknown or
      meetingBodyColor != VoteUnknown or
      meetingButtonSeen:
    bot.rememberMeetingCall(
      meetingCallerColor,
      meetingBodyColor,
      meetingButtonSeen
    )
  if bot.protocolInterstitialText == "IMPS":
    for colorIndex, seen in revealImposters:
      if seen:
        bot.knownImposters[colorIndex] = true
  bot.protocolVoteScreenSeen = voteScreenSeen
  if voteScreenSeen and votePlayerCount > 0:
    if voteGeometryFallbackUsed:
      bot.logEvent(
        "notsus voting geometry fallback ready: cursorSeen=" &
          $voteCursorSeen & " selfSeen=" & $voteSelfSeen
      )
    if voteCursorSeen:
      voteCursor =
        if voteCursorIsSkip:
          votePlayerCount
        else:
          protocolVoteCellAt(votePlayerCount, voteCursorX, voteCursorY)
    if voteSelfSeen:
      voteSelfSlot = protocolVoteCellAt(votePlayerCount, voteSelfX, voteSelfY)
      if voteSelfColor >= 0 and voteSelfColor < PlayerColorCount:
        bot.selfColorIndex = voteSelfColor
    for line in chatLines:
      var
        bestColor = VoteUnknown
        bestDistance = high(int)
      for i in 0 ..< chatSpeakerCount:
        let distance = abs(chatSpeakers[i].y - line.y)
        if distance < bestDistance:
          bestDistance = distance
          bestColor = chatSpeakers[i].colorIndex
      if bestDistance <= VoteChatSpeakerSearch:
        line.speakerColor = bestColor
    bot.protocolVotingReady = bot.applyProtocolVotingState(
      votePlayerCount,
      voteCursor,
      voteSelfSlot,
      voteSlots,
      voteChoices,
      chatLines
    )
  elif voteScreenSeen and bot.voting:
    bot.protocolVotingReady = true

proc stableCrewmateColor(color: uint8): bool =
  ## Returns true for non-body crewmate sprite pixels.
  color != TransparentColorIndex and
    color != TintColor and
    color != ShadeTintColor

proc playerBodyColor(color: uint8): bool =
  ## Returns true when a frame color can be a crewmate body.
  for playerColor in PlayerColors:
    if color == playerColor:
      return true
    if color == ShadowMap[playerColor and 0x0f]:
      return true

proc playerColorIndex(color: uint8): int =
  ## Returns the tracked player color index for a palette color.
  for i, playerColor in PlayerColors:
    if color == playerColor:
      return i
  -1

proc crewmatePixelMatches(spriteColor, frameColor: uint8): bool =
  ## Returns true when one crewmate sprite pixel matches the frame.
  if spriteColor == TintColor or spriteColor == ShadeTintColor:
    return playerBodyColor(frameColor)
  frameColor == spriteColor

proc crewmateColorIndex(bot: Bot, x, y: int, flipH: bool): int =
  ## Returns the most likely visible color for a crewmate match.
  var counts: array[PlayerColorCount, int]
  for sy in 0 ..< bot.playerSprite.height:
    for sx in 0 ..< bot.playerSprite.width:
      let srcX =
        if flipH:
          bot.playerSprite.width - 1 - sx
        else:
          sx
      let color = bot.playerSprite.pixels[
        bot.playerSprite.spriteIndex(srcX, sy)
      ]
      if color != TintColor:
        continue
      let
        fx = x + sx
        fy = y + sy
      if fx < 0 or fy < 0 or fx >= ScreenWidth or fy >= ScreenHeight:
        continue
      let index = playerColorIndex(bot.unpacked[fy * ScreenWidth + fx])
      if index >= 0:
        inc counts[index]
  var bestCount = 0
  result = -1
  for i, count in counts:
    if count > bestCount:
      bestCount = count
      result = i

proc matchesCrewmate(
  bot: Bot,
  x,
  y: int,
  flipH: bool
): bool =
  ## Returns true when stable crewmate pixels match the frame.
  var
    bodyMatched = 0
    bodyPixels = 0
    matchedStable = 0
    misses = 0
    stablePixels = 0
  for sy in 0 ..< bot.playerSprite.height:
    for sx in 0 ..< bot.playerSprite.width:
      let srcX =
        if flipH:
          bot.playerSprite.width - 1 - sx
        else:
          sx
      let color = bot.playerSprite.pixels[
        bot.playerSprite.spriteIndex(srcX, sy)
      ]
      if color == TransparentColorIndex:
        continue
      if stableCrewmateColor(color):
        inc stablePixels
      else:
        inc bodyPixels
      let
        fx = x + sx
        fy = y + sy
      if fx < 0 or fy < 0 or fx >= ScreenWidth or fy >= ScreenHeight:
        inc misses
      elif crewmatePixelMatches(color, bot.unpacked[fy * ScreenWidth + fx]):
        if stableCrewmateColor(color):
          inc matchedStable
        else:
          inc bodyMatched
      else:
        inc misses
      if misses > CrewmateMaxMisses:
        return false
  stablePixels >= CrewmateMinStablePixels and
    matchedStable >= CrewmateMinStablePixels and
    bodyPixels >= CrewmateMinBodyPixels and
    bodyMatched >= CrewmateMinBodyPixels

proc addCrewmateMatch(
  matches: var seq[CrewmateMatch],
  x,
  y: int,
  colorIndex: int,
  flipH: bool
) =
  ## Adds one crewmate match unless a nearby match already exists.
  for i in 0 ..< matches.len:
    if abs(matches[i].x - x) <= CrewmateSearchRadius and
        abs(matches[i].y - y) <= CrewmateSearchRadius:
      if matches[i].colorIndex < 0 and colorIndex >= 0:
        matches[i].colorIndex = colorIndex
      return
  matches.add(CrewmateMatch(
    x: x,
    y: y,
    colorIndex: colorIndex,
    flipH: flipH
  ))

proc scanCrewmates(bot: var Bot) {.measure.} =
  ## Scans the current frame for crewmates by stable sprite pixels.
  bot.visibleCrewmates.setLen(0)
  for y in 0 .. ScreenHeight - bot.playerSprite.height:
    for x in 0 .. ScreenWidth - bot.playerSprite.width:
      if abs(x + SpriteSize div 2 - PlayerScreenX) <= PlayerIgnoreRadius and
          abs(y + SpriteSize div 2 - PlayerScreenY) <= PlayerIgnoreRadius:
        continue
      if bot.matchesCrewmate(x, y, false):
        let colorIndex = bot.crewmateColorIndex(x, y, false)
        bot.visibleCrewmates.addCrewmateMatch(x, y, colorIndex, false)
      elif bot.matchesCrewmate(x, y, true):
        let colorIndex = bot.crewmateColorIndex(x, y, true)
        bot.visibleCrewmates.addCrewmateMatch(x, y, colorIndex, true)
  for crewmate in bot.visibleCrewmates:
    bot.rememberPlayerMotion(
      crewmate.colorIndex,
      bot.cameraX + crewmate.x + SpriteDrawOffX,
      bot.cameraY + crewmate.y + SpriteDrawOffY
    )

proc updateSelfColor(bot: var Bot) {.measure.} =
  ## Learns the local player's color from the centered player sprite.
  let
    x = PlayerScreenX - bot.playerSprite.width div 2
    y = PlayerScreenY - bot.playerSprite.height div 2
  var colorIndex = -1
  if bot.matchesCrewmate(x, y, false):
    colorIndex = bot.crewmateColorIndex(x, y, false)
  elif bot.matchesCrewmate(x, y, true):
    colorIndex = bot.crewmateColorIndex(x, y, true)
  if colorIndex >= 0 and colorIndex < PlayerColorCount:
    bot.selfColorIndex = colorIndex

proc matchesActorSprite(
  bot: Bot,
  sprite: Sprite,
  x,
  y: int,
  flipH: bool,
  maxMisses,
  minStablePixels,
  minTintPixels: int
): bool =
  ## Returns true when a tinted actor sprite matches the frame.
  var
    tintMatched = 0
    tintPixels = 0
    stableMatched = 0
    misses = 0
    stablePixels = 0
  for sy in 0 ..< sprite.height:
    for sx in 0 ..< sprite.width:
      let srcX =
        if flipH:
          sprite.width - 1 - sx
        else:
          sx
      let color = sprite.pixels[sprite.spriteIndex(srcX, sy)]
      if color == TransparentColorIndex:
        continue
      if stableCrewmateColor(color):
        inc stablePixels
      else:
        inc tintPixels
      let
        fx = x + sx
        fy = y + sy
      if fx < 0 or fy < 0 or fx >= ScreenWidth or fy >= ScreenHeight:
        inc misses
      elif crewmatePixelMatches(color, bot.unpacked[fy * ScreenWidth + fx]):
        if stableCrewmateColor(color):
          inc stableMatched
        else:
          inc tintMatched
      else:
        inc misses
      if misses > maxMisses:
        return false
  stablePixels >= minStablePixels and
    stableMatched >= minStablePixels and
    tintPixels >= minTintPixels and
    tintMatched >= minTintPixels

proc actorColorIndex(
  bot: Bot,
  sprite: Sprite,
  x,
  y: int,
  flipH: bool
): int =
  ## Returns the most likely tint color for an actor sprite.
  var counts: array[PlayerColorCount, int]
  for sy in 0 ..< sprite.height:
    for sx in 0 ..< sprite.width:
      let srcX =
        if flipH:
          sprite.width - 1 - sx
        else:
          sx
      let color = sprite.pixels[sprite.spriteIndex(srcX, sy)]
      if color != TintColor:
        continue
      let
        fx = x + sx
        fy = y + sy
      if fx < 0 or fy < 0 or fx >= ScreenWidth or fy >= ScreenHeight:
        continue
      let index = playerColorIndex(bot.unpacked[fy * ScreenWidth + fx])
      if index >= 0:
        inc counts[index]
  var bestCount = 0
  result = VoteUnknown
  for i, count in counts:
    if count > bestCount:
      bestCount = count
      result = i

proc voteChatTextFromLines(lines: openArray[VoteChatLine]): string =
  ## Flattens parsed voting chat lines into one text string.
  for line in lines:
    if result.len > 0:
      result.add(' ')
    result.add(line.text)

proc normalizeChatText(text: string): string =
  ## Normalizes chat text for simple word matching.
  var hadSpace = true
  for ch in text:
    var outCh = ch
    if ch in {'A' .. 'Z'}:
      outCh = char(ord(ch) - ord('A') + ord('a'))
    if outCh in {'a' .. 'z'} or outCh in {'0' .. '9'}:
      result.add(outCh)
      hadSpace = false
    elif not hadSpace:
      result.add(' ')
      hadSpace = true
  result = result.strip()

proc markBodyReporter(bot: var Bot, colorIndex: int) =
  ## Records that one player color has reported a dead body.
  if colorIndex >= 0 and colorIndex < bot.bodyReportColors.len:
    bot.bodyReportColors[colorIndex] = true

proc rememberMeetingCall(
  bot: var Bot,
  callerColor,
  bodyColor: int,
  buttonSeen: bool
) =
  ## Records the caller and cause shown on the meeting-call screen.
  let nextKind =
    if bodyColor != VoteUnknown:
      VoteCalledBody
    elif buttonSeen:
      VoteCalledButton
    else:
      VoteCalledUnknown
  if bot.meetingCallKind != nextKind or
      bot.meetingCallCallerColor != callerColor or
      bot.meetingCallBodyColor != bodyColor:
    bot.meetingCallApplied = false
  bot.meetingCallKind = nextKind
  bot.meetingCallCallerColor = callerColor
  bot.meetingCallBodyColor = bodyColor

proc applyMeetingCallToVoting(bot: var Bot) =
  ## Applies the remembered meeting-call cause once voting is visible.
  if bot.meetingCallApplied or not bot.voting:
    return
  case bot.meetingCallKind
  of VoteCalledButton:
    discard
  of VoteCalledBody:
    bot.markBodyReporter(bot.meetingCallCallerColor)
    if bot.meetingCallBodyColor != VoteUnknown:
      let slot = bot.voteSlotForColor(bot.meetingCallBodyColor)
      if slot >= 0 and slot < bot.votePlayerCount:
        bot.voteSlots[slot].alive = false
  of VoteCalledUnknown:
    discard
  bot.meetingCallApplied = true

proc applyVotedAgainstEvidence(bot: var Bot) =
  ## Credits visible player votes against their current targets once.
  for voterColor, targetSlot in bot.voteChoices:
    if targetSlot < 0 or targetSlot >= bot.votePlayerCount:
      continue
    let voterSlot = bot.voteSlotForColor(voterColor)
    if voterSlot < 0 or voterSlot >= bot.votePlayerCount:
      continue
    let targetColor = bot.voteSlots[targetSlot].colorIndex
    if targetColor < 0 or targetColor >= bot.votedAgainstScores.len:
      continue
    if bot.voteCreditedTargets[voterColor] == targetColor:
      continue
    inc bot.votedAgainstScores[targetColor]
    bot.voteCreditedTargets[voterColor] = targetColor

proc voteSlotForColor(bot: Bot, colorIndex: int): int =
  ## Returns the voting slot index for one player color.
  for i in 0 ..< bot.votePlayerCount:
    if bot.voteSlots[i].colorIndex == colorIndex:
      return i
  VoteUnknown

proc voteTargetCanBeSus(bot: Bot, target: int): bool =
  ## Returns true when a voting target is a living non-self player.
  target >= 0 and
    target < bot.votePlayerCount and
    target != bot.voteSelfSlot and
    bot.voteSlots[target].alive

proc directVoteSusScore(bot: Bot, colorIndex: int): int =
  ## Returns private evidence strong enough to drive a direct vote.
  if colorIndex < 0 or colorIndex >= PlayerColorCount:
    return 0
  result =
    bot.ventScores[colorIndex] +
    bot.stalkerScores[colorIndex] +
    bot.standingAroundScores[colorIndex]
  if bot.role == RoleCrewmate and bot.voting and bot.knownDeadVoteCount() > 0:
    result += bot.killerScores[colorIndex] * BodyProximitySusWeight

proc selfVotingDead(bot: Bot): bool =
  ## Returns true when the local player is known dead in voting.
  bot.voteSelfSlot >= 0 and
    bot.voteSelfSlot < bot.votePlayerCount and
    not bot.voteSlots[bot.voteSelfSlot].alive

proc randomVoteDelay(bot: var Bot): int =
  ## Returns this meeting's randomized vote delay in ticks.
  if not bot.imposterKnown():
    return VoteCrewmateMinDelayTicks +
      bot.rng.rand(VoteCrewmateMaxDelayTicks - VoteCrewmateMinDelayTicks)
  VoteListenBaseTicks - VoteListenJitterTicks +
    bot.rng.rand(VoteListenJitterTicks * 2)

proc addBodyMatch(
  matches: var seq[BodyMatch],
  x,
  y: int,
  colorIndex = VoteUnknown
) =
  ## Adds one body match unless a nearby match already exists.
  for match in matches:
    if abs(match.x - x) <= BodySearchRadius and
        abs(match.y - y) <= BodySearchRadius:
      return
  matches.add(BodyMatch(x: x, y: y, colorIndex: colorIndex))

proc scanBodies(bot: var Bot) {.measure.} =
  ## Scans the current frame for visible dead bodies.
  bot.visibleBodies.setLen(0)
  for y in 0 .. ScreenHeight - bot.bodySprite.height:
    for x in 0 .. ScreenWidth - bot.bodySprite.width:
      if bot.matchesActorSprite(
        bot.bodySprite,
        x,
        y,
        false,
        BodyMaxMisses,
        BodyMinStablePixels,
        BodyMinTintPixels
      ):
        bot.visibleBodies.addBodyMatch(x, y)

proc addGhostMatch(matches: var seq[GhostMatch], x, y: int, flipH: bool) =
  ## Adds one ghost match unless a nearby match already exists.
  for match in matches:
    if abs(match.x - x) <= GhostSearchRadius and
        abs(match.y - y) <= GhostSearchRadius:
      return
  matches.add(GhostMatch(x: x, y: y, flipH: flipH))

proc scanGhosts(bot: var Bot) {.measure.} =
  ## Scans the current frame for visible ghosts.
  bot.visibleGhosts.setLen(0)
  for y in 0 .. ScreenHeight - bot.ghostSprite.height:
    for x in 0 .. ScreenWidth - bot.ghostSprite.width:
      if bot.matchesActorSprite(
        bot.ghostSprite,
        x,
        y,
        false,
        GhostMaxMisses,
        GhostMinStablePixels,
        GhostMinTintPixels
      ):
        bot.visibleGhosts.addGhostMatch(x, y, false)
      elif bot.matchesActorSprite(
        bot.ghostSprite,
        x,
        y,
        true,
        GhostMaxMisses,
        GhostMinStablePixels,
        GhostMinTintPixels
      ):
        bot.visibleGhosts.addGhostMatch(x, y, true)

proc scanTaskIcons(bot: var Bot) {.measure.} =
  ## Scans expected task icon positions for visible task icons.
  bot.visibleTaskIcons.setLen(0)
  if not bot.localized:
    return
  if bot.spriteDetectionsReady:
    for icon in bot.spriteTaskIcons:
      bot.visibleTaskIcons.addIconMatch(icon.x, icon.y)
    return
  for task in bot.sim.tasks:
    let
      baseX = task.x + task.w div 2 - SpriteSize div 2 - bot.cameraX
      baseY = task.y - SpriteSize - 2 - bot.cameraY
    for bobY in -1 .. 1:
      let expectedY = baseY + bobY
      for dy in -TaskIconExpectedSearchRadius .. TaskIconExpectedSearchRadius:
        for dx in -TaskIconExpectedSearchRadius .. TaskIconExpectedSearchRadius:
          let
            x = baseX + dx
            y = expectedY + dy
          if matchesSprite(bot.unpacked, bot.taskSprite, x, y):
            bot.visibleTaskIcons.addIconMatch(x, y)

proc projectedRadarDot(
  bot: Bot,
  task: TaskStation
): tuple[visible: bool, x: int, y: int] =
  ## Projects an offscreen task icon to its expected radar edge pixel.
  if not bot.localized:
    return
  let
    iconSx = task.x + task.w div 2 - SpriteSize div 2 - bot.cameraX
    iconSy = task.y - SpriteSize - 2 - bot.cameraY
    iconX = iconSx + SpriteSize div 2
    iconY = iconSy + SpriteSize div 2
  if iconSx + SpriteSize > 0 and iconSy + SpriteSize > 0 and
      iconSx < ScreenWidth and iconSy < ScreenHeight:
    return (true, iconX, iconY)
  let
    px = float(bot.playerWorldX() + CollisionW div 2 - bot.cameraX)
    py = float(bot.playerWorldY() + CollisionH div 2 - bot.cameraY)
    dx = float(iconX) - px
    dy = float(iconY) - py
  if abs(dx) < 0.5 and abs(dy) < 0.5:
    return
  let
    minX = 0.0
    maxX = float(ScreenWidth - 1)
    minY = 0.0
    maxY = float(ScreenHeight - 1)
  var
    ex: float
    ey: float
  if abs(dx) > abs(dy):
    if dx > 0:
      ex = maxX
    else:
      ex = minX
    ey = py + dy * (ex - px) / dx
    ey = clamp(ey, minY, maxY)
  else:
    if dy > 0:
      ey = maxY
    else:
      ey = minY
    ex = px + dx * (ey - py) / dy
    ex = clamp(ex, minX, maxX)
  (false, int(ex), int(ey))

proc updateTaskGuesses(bot: var Bot) {.measure.} =
  ## Updates ephemeral task candidates from radar dots.
  if bot.taskStates.len != bot.sim.tasks.len:
    bot.taskStates = newSeq[TaskState](bot.sim.tasks.len)
  if bot.radarTasks.len != bot.sim.tasks.len:
    bot.radarTasks = newSeq[bool](bot.sim.tasks.len)
  if bot.checkoutTasks.len != bot.sim.tasks.len:
    bot.checkoutTasks = newSeq[bool](bot.sim.tasks.len)
  for i in 0 ..< bot.radarTasks.len:
    bot.radarTasks[i] = false
  if not bot.localized:
    return
  bot.scanRadarDots()
  if bot.spriteDetectionsReady and
      bot.spriteRadarTasks.len == bot.sim.tasks.len:
    for i in 0 ..< bot.sim.tasks.len:
      if not bot.spriteRadarTasks[i]:
        continue
      bot.radarTasks[i] = true
      bot.checkoutTasks[i] = true
      if bot.taskStates[i] == TaskCompleted:
        bot.taskStates[i] = TaskMaybe
    return
  if bot.radarDots.len == 0:
    return
  for dot in bot.radarDots:
    var
      matches = 0
      matchIndex = -1
    for i in 0 ..< bot.sim.tasks.len:
      let projected = bot.projectedRadarDot(bot.sim.tasks[i])
      if projected.visible:
        continue
      if abs(dot.x - projected.x) <= RadarMatchTolerance and
          abs(dot.y - projected.y) <= RadarMatchTolerance:
        inc matches
        matchIndex = i
        if matches > 1:
          break
    if matches == 1:
      bot.radarTasks[matchIndex] = true
      bot.checkoutTasks[matchIndex] = true
      if bot.taskStates[matchIndex] == TaskCompleted:
        bot.taskStates[matchIndex] = TaskMaybe

proc taskHasCurrentRadar(bot: Bot, index: int): bool =
  ## Returns true when a current radar dot points at one task.
  index >= 0 and index < bot.sim.tasks.len and
    bot.radarTasks.len == bot.sim.tasks.len and bot.radarTasks[index]

proc taskMayBeOnScreen(bot: Bot, index: int): bool =
  ## Returns true when a task is onscreen enough to hide radar dots.
  if index < 0 or index >= bot.sim.tasks.len:
    return false
  bot.projectedRadarDot(bot.sim.tasks[index]).visible

proc taskHasFreshClue(bot: Bot, index: int): bool =
  ## Returns true when radar or visibility still supports one task.
  bot.taskHasCurrentRadar(index) or bot.taskMayBeOnScreen(index)

proc resetTasksFromRadar(bot: var Bot) {.measure.} =
  ## Rebuilds remembered task candidates from current radar dots.
  if not bot.localized:
    return
  if bot.frameTick - bot.lastTaskRadarResetTick < TaskRadarResetTicks:
    return
  bot.lastTaskRadarResetTick = bot.frameTick
  if bot.radarTasks.len != bot.sim.tasks.len:
    bot.radarTasks = newSeq[bool](bot.sim.tasks.len)
  if bot.checkoutTasks.len != bot.sim.tasks.len:
    bot.checkoutTasks = newSeq[bool](bot.sim.tasks.len)
  if bot.taskStates.len != bot.sim.tasks.len:
    bot.taskStates = newSeq[TaskState](bot.sim.tasks.len)
  if bot.taskIconMisses.len != bot.sim.tasks.len:
    bot.taskIconMisses = newSeq[int](bot.sim.tasks.len)
  for i in 0 ..< bot.sim.tasks.len:
    if bot.taskHoldTicks > 0 and bot.taskHoldIndex == i:
      continue
    if bot.radarTasks[i]:
      bot.checkoutTasks[i] = true
      if bot.taskStates[i] in {TaskNotDoing, TaskCompleted}:
        bot.taskStates[i] = TaskMaybe
      bot.taskIconMisses[i] = 0
    elif bot.taskMayBeOnScreen(i):
      continue
    else:
      bot.checkoutTasks[i] = false
      if bot.taskStates[i] != TaskCompleted:
        bot.taskStates[i] = TaskNotDoing
      bot.taskIconMisses[i] = 0

proc projectedTaskIcon(
  bot: Bot,
  task: TaskStation,
  bobY: int
): tuple[visible: bool, x: int, y: int] =
  ## Returns the expected screen position for a visible task icon.
  if not bot.localized:
    return
  let
    iconX = task.x + task.w div 2 - SpriteSize div 2 - bot.cameraX
    iconY = task.y - SpriteSize - 2 + bobY - bot.cameraY
  if iconX + SpriteSize < 0 or iconY + SpriteSize < 0 or
      iconX >= ScreenWidth or iconY >= ScreenHeight:
    return
  (true, iconX, iconY)

proc taskIconInspectRect(
  bot: Bot,
  task: TaskStation
): tuple[x: int, y: int, w: int, h: int] =
  ## Returns the expected screen rectangle for inspecting a task icon.
  (
    task.x + task.w div 2 - TaskIconInspectSize div 2 - bot.cameraX,
    task.y - TaskIconInspectSize - bot.cameraY,
    TaskIconInspectSize,
    TaskIconInspectSize
  )

proc taskIconRenderable(bot: Bot, task: TaskStation): bool =
  ## Returns true when the server could render the task icon.
  let
    center = task.taskCenter()
    sx = center.x - bot.cameraX
    sy = center.y - bot.cameraY
  sx >= 0 and sx < ScreenWidth and sy >= 0 and sy < ScreenHeight

proc taskIconClearAreaVisible(bot: Bot, task: TaskStation): bool =
  ## Returns true when the whole icon inspection area is visible.
  let rect = bot.taskIconInspectRect(task)
  rect.x >= TaskClearScreenMargin and
    rect.y >= TaskClearScreenMargin and
    rect.x + rect.w + TaskClearScreenMargin <= ScreenWidth and
    rect.y + rect.h + TaskClearScreenMargin <= ScreenHeight

proc taskIconMaybeVisibleFor(bot: Bot, task: TaskStation): bool =
  ## Returns true when expected icon pixels look plausibly present.
  let
    baseX = task.x + task.w div 2 - SpriteSize div 2 - bot.cameraX
    baseY = task.y - SpriteSize - 2 - bot.cameraY
  for bobY in -1 .. 1:
    let expectedY = baseY + bobY
    for dy in -TaskIconExpectedSearchRadius .. TaskIconExpectedSearchRadius:
      for dx in -TaskIconExpectedSearchRadius .. TaskIconExpectedSearchRadius:
        if maybeMatchesSprite(
          bot.unpacked,
          bot.taskSprite,
          baseX + dx,
          expectedY + dy
        ):
          return true

proc iconMatchesTask(
  bot: Bot,
  icon: IconMatch,
  task: TaskStation
): bool =
  ## Returns true when one visible icon can belong to a task.
  for bobY in -1 .. 1:
    let projected = bot.projectedTaskIcon(task, bobY)
    if not projected.visible:
      continue
    if abs(icon.x - projected.x) <= TaskIconSearchRadius and
        abs(icon.y - projected.y) <= TaskIconSearchRadius:
      return true

proc matchingTaskCount(bot: Bot, icon: IconMatch): int =
  ## Counts task stations that could explain one visible task icon.
  for task in bot.sim.tasks:
    if bot.iconMatchesTask(icon, task):
      inc result
      if result > 1:
        return

proc taskIconVisibleFor(bot: Bot, task: TaskStation): bool =
  ## Returns true if a visible task station has a unique icon on screen.
  for icon in bot.visibleTaskIcons:
    if bot.iconMatchesTask(icon, task) and
        bot.matchingTaskCount(icon) == 1:
      return true

proc updateTaskIcons(bot: var Bot) {.measure.} =
  ## Updates task states from visible task icons.
  if bot.taskStates.len != bot.sim.tasks.len:
    bot.taskStates = newSeq[TaskState](bot.sim.tasks.len)
  if bot.taskIconMisses.len != bot.sim.tasks.len:
    bot.taskIconMisses = newSeq[int](bot.sim.tasks.len)
  if bot.checkoutTasks.len != bot.sim.tasks.len:
    bot.checkoutTasks = newSeq[bool](bot.sim.tasks.len)
  if not bot.localized:
    return
  bot.scanTaskIcons()
  for i in 0 ..< bot.sim.tasks.len:
    let task = bot.sim.tasks[i]
    if bot.spriteDetectionsReady and
        bot.spriteIconTasks.len == bot.sim.tasks.len and
        bot.spriteIconTasks[i]:
      bot.taskStates[i] = TaskMandatory
      bot.taskIconMisses[i] = 0
    elif bot.taskIconVisibleFor(task):
      bot.taskStates[i] = TaskMandatory
      bot.taskIconMisses[i] = 0
    elif bot.taskHoldTicks == 0 and
        bot.taskIconClearAreaVisible(task) and
        (
          if bot.spriteDetectionsReady and
              bot.spriteIconTasks.len == bot.sim.tasks.len:
            not bot.spriteIconTasks[i]
          else:
            not bot.taskIconMaybeVisibleFor(task)
        ) and
        (bot.radarTasks.len != bot.sim.tasks.len or not bot.radarTasks[i]) and
        bot.taskHoldIndex != i:
      if bot.taskStates[i] == TaskMandatory:
        inc bot.taskIconMisses[i]
        if bot.taskIconMisses[i] >= TaskIconMissThreshold:
          bot.taskStates[i] = TaskCompleted
          bot.checkoutTasks[i] = false
          bot.taskIconMisses[i] = 0
      elif bot.checkoutTasks[i]:
        inc bot.taskIconMisses[i]
        if bot.taskIconMisses[i] >= TaskIconMissThreshold:
          bot.checkoutTasks[i] = false
          bot.taskIconMisses[i] = 0
    else:
      bot.taskIconMisses[i] = 0

proc thought(bot: var Bot, text: string) =
  ## Stores changed bot thoughts for the GUI.
  if text != bot.lastThought:
    bot.lastThought = text

proc movementName(mask: uint8): string =
  ## Returns a compact movement label for one input mask.
  if (mask and ButtonLeft) != 0:
    return "left"
  if (mask and ButtonRight) != 0:
    return "right"
  if (mask and ButtonUp) != 0:
    return "up"
  if (mask and ButtonDown) != 0:
    return "down"
  "idle"

proc hasMovement(mask: uint8): bool =
  ## Returns true when an input mask contains directional movement.
  (mask and (ButtonUp or ButtonDown or ButtonLeft or ButtonRight)) != 0

proc taskHoldActive(bot: Bot): bool =
  ## Returns true while this bot is intentionally holding a task.
  bot.taskHoldTicks > 0 or bot.taskHoldIndex >= 0

proc resetCellStuck(bot: var Bot) =
  ## Clears 8x8-cell stuck tracking.
  bot.stuckCellX = low(int)
  bot.stuckCellY = low(int)
  bot.stuckCellTicks = 0
  bot.unstuckTicks = 0

proc updateCellStuck(bot: var Bot, x, y: int) =
  ## Starts an escape burst after staying in one 8x8 cell too long.
  if bot.taskHoldActive() or not bot.lastMask.hasMovement():
    bot.resetCellStuck()
    return
  let
    cellX = x div StuckCellSize
    cellY = y div StuckCellSize
  if cellX == bot.stuckCellX and cellY == bot.stuckCellY:
    inc bot.stuckCellTicks
  else:
    bot.stuckCellX = cellX
    bot.stuckCellY = cellY
    bot.stuckCellTicks = 1
  if bot.stuckCellTicks < StuckCellThreshold:
    return
  bot.clearPath()
  bot.stuckCellTicks = 0
  bot.unstuckTicks = UnstuckDuration
  bot.unstuckMaskIndex =
    (bot.unstuckMaskIndex + 1) mod UnstuckMasks.len
  bot.jiggleTicks = 0

proc updateMotionState(bot: var Bot) =
  ## Tracks current frame-to-frame player velocity.
  if not bot.localized:
    bot.haveMotionSample = false
    bot.velocityX = 0
    bot.velocityY = 0
    bot.stuckFrames = 0
    bot.resetCellStuck()
    bot.jiggleTicks = 0
    return

  let
    x = bot.playerWorldX()
    y = bot.playerWorldY()
  if bot.taskHoldActive():
    bot.velocityX = 0
    bot.velocityY = 0
    bot.stuckFrames = 0
    bot.resetCellStuck()
    bot.jiggleTicks = 0
    bot.haveMotionSample = true
    bot.previousPlayerWorldX = x
    bot.previousPlayerWorldY = y
    return
  if bot.haveMotionSample and bot.lastMask.hasMovement():
    bot.velocityX = x - bot.previousPlayerWorldX
    bot.velocityY = y - bot.previousPlayerWorldY
    let moved = abs(bot.velocityX) + abs(bot.velocityY)
    if moved == 0:
      inc bot.stuckFrames
    else:
      bot.stuckFrames = 0
    if bot.stuckFrames >= StuckFrameThreshold:
      bot.clearPath()
      bot.stuckFrames = 0
      bot.jiggleTicks = JiggleDuration
      bot.jiggleSide = 1 - bot.jiggleSide
    bot.updateCellStuck(x, y)
  else:
    bot.velocityX = 0
    bot.velocityY = 0
    bot.stuckFrames = 0
    bot.resetCellStuck()

  bot.haveMotionSample = true
  bot.previousPlayerWorldX = x
  bot.previousPlayerWorldY = y

proc applyJiggle(bot: var Bot, mask: uint8): uint8 =
  ## Adds a short perpendicular correction while keeping intent held.
  result = mask
  if bot.taskHoldActive():
    bot.unstuckTicks = 0
    bot.jiggleTicks = 0
    return
  if bot.unstuckTicks > 0 and mask.hasMovement():
    let elapsed = UnstuckDuration - bot.unstuckTicks
    result = UnstuckMasks[
      (bot.unstuckMaskIndex + elapsed div UnstuckPulseTicks) mod
        UnstuckMasks.len
    ]
    dec bot.unstuckTicks
    return
  if bot.jiggleTicks <= 0 or not mask.hasMovement():
    return
  dec bot.jiggleTicks
  let
    vertical = (mask and (ButtonUp or ButtonDown)) != 0
    horizontal = (mask and (ButtonLeft or ButtonRight)) != 0
  if vertical and not horizontal:
    if bot.jiggleSide == 0:
      result = result or ButtonLeft
    else:
      result = result or ButtonRight
  elif horizontal and not vertical:
    if bot.jiggleSide == 0:
      result = result or ButtonUp
    else:
      result = result or ButtonDown
  elif bot.jiggleSide == 0:
    result = result or ButtonLeft
  else:
    result = result or ButtonRight

proc inputMaskSummary(mask: uint8): string =
  ## Returns a human-readable input mask.
  var parts: seq[string] = @[]
  if (mask and ButtonUp) != 0: parts.add("up")
  if (mask and ButtonDown) != 0: parts.add("down")
  if (mask and ButtonLeft) != 0: parts.add("left")
  if (mask and ButtonRight) != 0: parts.add("right")
  if (mask and ButtonA) != 0: parts.add("a")
  if (mask and ButtonB) != 0: parts.add("b")
  if (mask and ButtonSelect) != 0: parts.add("select")
  if parts.len == 0:
    return "idle"
  parts.join(", ")

proc taskStateCount(bot: Bot, state: TaskState): int =
  ## Returns the number of tasks in one state.
  for taskState in bot.taskStates:
    if taskState == state:
      inc result

proc radarTaskCount(bot: Bot): int =
  ## Returns the number of current radar task candidates.
  for radarTask in bot.radarTasks:
    if radarTask:
      inc result

proc checkoutTaskCount(bot: Bot): int =
  ## Returns the number of persistent checkout task candidates.
  for checkoutTask in bot.checkoutTasks:
    if checkoutTask:
      inc result

proc completedTaskCount(bot: Bot): int =
  ## Returns the number of tasks believed to be completed.
  bot.taskStateCount(TaskCompleted)

proc allTasksComplete(bot: Bot): bool =
  ## Returns true when no task work remains for this bot.
  bot.sim.tasks.len > 0 and
    bot.completedTaskCount() >= bot.sim.tasks.len

proc emergencyButtonSpent(bot: Bot): bool =
  ## Returns true because this bot treats emergency buttons as spent.
  discard bot
  true

proc buttonFallbackReady(bot: Bot): bool =
  ## Returns true when home is the only useful remaining goal.
  bot.radarDots.len == 0 and
    bot.radarTaskCount() == 0 and
    bot.checkoutTaskCount() == 0 and
    bot.taskStateCount(TaskMandatory) == 0

proc safeHideReady(bot: Bot): bool =
  ## Returns true when this crewmate should hide instead of doing tasks.
  bot.role == RoleCrewmate and
    not bot.isGhost and
    bot.emergencyButtonSpent() and
    (
      bot.allTasksComplete() or
      bot.buttonFallbackReady()
    )

proc votingChatClockTick(bot: Bot): int =
  ## Returns the tick clock used for voting chat cooldowns.
  if bot.serverTick >= 0:
    bot.serverTick
  else:
    bot.frameTick

proc votingChatCooldownReady(bot: Bot): bool =
  ## Returns true when the server chat cooldown should accept a message.
  if bot.lastVotingChatSentTick < 0:
    return true
  let cooldown = max(0, bot.sim.config.messageCooldownTicks)
  if cooldown == 0:
    return true
  let elapsed = bot.votingChatClockTick() - bot.lastVotingChatSentTick
  elapsed < 0 or elapsed >= cooldown

proc pendingChatReady(bot: Bot): bool =
  ## Returns true when pending chat is safe to send.
  if bot.pendingChat.len == 0 or not bot.interstitial:
    return false
  if bot.interstitialText.isGameOverText() or not bot.voting:
    return false
  if not bot.votingChatCooldownReady():
    return false
  true

proc markPendingChatSent(bot: var Bot) =
  ## Records a sent pending chat packet and clears the buffer.
  if bot.voting and bot.interstitial:
    bot.lastVotingChatSentTick = bot.votingChatClockTick()
    bot.logEvent("notsus sent voting chat: " & bot.pendingChat)
    bot.voteSaidSomething = true
    inc bot.voteLlmSayCount
    bot.voteLlmAction = VoteLlmAction(
      kind: VoteLlmNone,
      targetColor: VoteUnknown
    )
    bot.voteLlmWaiting = false
    bot.voteLlmNeedsDecision =
      bot.votingLlmSnapshotKey() != bot.voteLlmPromptKey or
      bot.voteLlmCallCount < VoteLlmMaxCallsPerMeeting
  bot.pendingChat = ""

proc readyMessageReady(bot: Bot): bool =
  ## Returns true when no pending chat must be sent before ready.
  if bot.voting and
      bot.interstitial and
      not bot.interstitialText.isGameOverText():
    return false
  true

proc rememberHome(bot: var Bot) =
  ## Records the first reliable round position as this bot's home.
  if not bot.localized or bot.interstitial:
    return
  if not bot.gameStarted:
    bot.roundStartTick = bot.frameTick
    bot.roundStartServerTick = bot.serverTick
    bot.gameStarted = true
  if bot.homeSet:
    return
  bot.homeX = bot.playerWorldX()
  bot.homeY = bot.playerWorldY()
  bot.homeSet = true

proc roleName(role: BotRole): string =
  ## Returns a human-readable role name.
  case role
  of RoleUnknown: "unknown"
  of RoleCrewmate: "crewmate"
  of RoleImposter: "imposter"

proc knownImposterColor(bot: Bot, colorIndex: int): bool =
  ## Returns true if the color was shown as an imposter teammate.
  colorIndex >= 0 and
    colorIndex < bot.knownImposters.len and
    bot.knownImposters[colorIndex]

proc imposterKnown(bot: Bot): bool =
  ## Returns true when any reliable signal says this bot is an imposter.
  bot.role == RoleImposter or
    bot.knownImposterColor(bot.selfColorIndex) or
    bot.imposterKillReady or
    bot.imposterCooldownPercent >= 0

proc normalizeRoleFromImposterSignals(bot: var Bot) =
  ## Restores imposter role from hidden or cooldown signals.
  if bot.imposterKnown() and not bot.isGhost:
    bot.role = RoleImposter

proc playerColorName(colorIndex: int): string =
  ## Returns the visible player color name.
  if colorIndex >= 0 and colorIndex < PlayerColorNames.len:
    PlayerColorNames[colorIndex]
  else:
    "unknown"

proc playerLabelColor(objectId: int): int =
  ## Returns the color index implied by one player-name object id.
  let colorIndex = objectId - ProtocolPlayerNameObjectBase
  if colorIndex >= 0 and colorIndex < PlayerColorCount:
    colorIndex
  else:
    VoteUnknown

proc protocolPlayerLabelName(label: string): string =
  ## Returns the player name carried by one floating label sprite.
  if not label.startsWith(ProtocolPlayerNameLabelPrefix):
    return ""
  let start = ProtocolPlayerNameLabelPrefix.len
  var stop = label.len
  for i in start ..< label.len:
    if label[i] == '|':
      stop = i
      break
  label[start ..< stop].strip()

proc ownerNameKey(name: string): string =
  ## Returns a stable owner key from a possibly suffixed player label.
  result = name.strip().toLowerAscii()
  if result.len == 0 or result == "?":
    return ""
  while result.len > 0 and result[^1] == ' ':
    result.setLen(result.len - 1)
  if result.len >= 4 and result[^1] == ')':
    var i = result.len - 2
    while i >= 0 and result[i] in {'0' .. '9'}:
      dec i
    if i >= 1 and result[i] == '(' and result[i - 1] == ' ':
      result.setLen(i - 1)
      result = result.strip()

proc selfOwnerNameKey(bot: Bot): string =
  ## Returns this bot's owner key from screen or configured name.
  if bot.selfColorIndex >= 0 and
      bot.selfColorIndex < bot.playerDisplayNames.len:
    result = bot.playerDisplayNames[bot.selfColorIndex].ownerNameKey()
  if result.len == 0:
    result = bot.configuredName.ownerNameKey()

proc rememberPlayerDisplayName(
  bot: var Bot,
  objectId: int,
  label: string
) =
  ## Stores a visible player display name by color.
  let
    colorIndex = playerLabelColor(objectId)
    name = protocolPlayerLabelName(label)
  if colorIndex < 0 or colorIndex >= bot.playerDisplayNames.len:
    return
  if name.len == 0 or name == "?":
    return
  if bot.playerDisplayNames[colorIndex] == name:
    return
  bot.playerDisplayNames[colorIndex] = name
  bot.logEvent(
    "notsus player name: " & playerColorName(colorIndex) &
      " is " & name
  )

proc logTick(bot: Bot): int =
  ## Returns the server tick used for human-readable log time.
  if bot.serverTick >= 0:
    bot.serverTick
  else:
    bot.frameTick

proc gameTimeText(tick: int): string =
  ## Formats one game tick as minutes and seconds.
  let
    safeTick = max(0, tick)
    millis = (int64(safeTick) * 1000'i64) div int64(sim.TargetFps)
    totalSeconds = int(millis div 1000'i64)
    minutes = totalSeconds div 60
    seconds = totalSeconds mod 60
  result = $minutes & ":"
  if seconds < 10:
    result.add "0"
  result.add $seconds

proc logEvent(bot: Bot, message: string) =
  ## Logs one bot observation with replay-compatible game time.
  echo "[", bot.logTick().gameTimeText(), "] ", message
  bot.lastEventFrameTick = bot.frameTick

proc logBlock(bot: Bot, title, text: string) =
  ## Logs a multiline bot observation with one timestamp per line.
  bot.logEvent(title & " begin")
  if text.len == 0:
    bot.logEvent(title & " | <empty>")
  else:
    for line in text.splitLines():
      bot.logEvent(title & " | " & line)
  bot.logEvent(title & " end")

proc frameAgeText(bot: Bot, previousTick: int): string =
  ## Returns a compact seconds age for one frame timestamp.
  if previousTick < 0:
    return "unknown"
  let
    ageTicks = max(0, bot.frameTick - previousTick)
    ageSeconds = ageTicks.float / sim.TargetFps.float
  ageSeconds.formatFloat(ffDecimal, 1) & "s"

proc positionText(bot: Bot): string =
  ## Returns this bot's current world position or unknown.
  if not bot.localized:
    return "unknown"
  "(" & $bot.playerWorldX() & "," & $bot.playerWorldY() & ")"

proc aliveStateText(bot: Bot): string =
  ## Returns this bot's current alive state.
  if bot.isGhost or bot.selfVotingDead():
    return "dead"
  "alive"

proc notePacketReceived(bot: Bot) =
  ## Records the latest received frame tick.
  bot.lastPacketFrameTick = bot.frameTick

proc noteActionActivity(bot: Bot) =
  ## Records the latest non-idle control or chat action tick.
  bot.lastActionFrameTick = bot.frameTick

proc heartbeatAllowed(bot: Bot): bool =
  ## Returns true when active-run heartbeat logs are useful.
  if not bot.gameStarted:
    return false
  if bot.interstitialText.isGameOverText():
    return false
  bot.aliveStateText() == "alive"

proc logProgressHeartbeat(bot: Bot) =
  ## Logs one active-run heartbeat with packet and progress ages.
  if not bot.heartbeatAllowed():
    return
  if bot.lastHeartbeatFrameTick >= 0 and
      bot.frameTick - bot.lastHeartbeatFrameTick < ProgressHeartbeatTicks:
    return
  let
    packetAge = bot.frameAgeText(bot.lastPacketFrameTick)
    actionAge = bot.frameAgeText(bot.lastActionFrameTick)
    eventAge = bot.frameAgeText(bot.lastEventFrameTick)
    room =
      if bot.localized:
        bot.roomName()
      else:
        "unknown"
    eventTickBeforeLog = bot.lastEventFrameTick
  bot.lastHeartbeatFrameTick = bot.frameTick
  bot.logEvent(
    "notsus heartbeat: serverTick=" & $bot.serverTick &
      " room=" & room &
      " position=" & bot.positionText() &
      " state=" & bot.aliveStateText() &
      " role=" & bot.role.roleName() &
      " voting=" & $bot.voting &
      " interstitial=" & $bot.interstitial &
      " packetAge=" & packetAge &
      " actionAge=" & actionAge &
      " eventAge=" & eventAge &
      " mask=" & inputMaskSummary(bot.lastMask) &
      " velocity=(" & $bot.velocityX & "," & $bot.velocityY & ")" &
      " crewmates=" & $bot.visibleCrewmates.len &
      " bodies=" & $bot.visibleBodies.len &
      " ghosts=" & $bot.visibleGhosts.len &
      " intent=" & bot.intent
  )
  bot.lastEventFrameTick = eventTickBeforeLog

proc packetWaitText(bot: Bot): string =
  ## Returns one human-readable packet wait state.
  if not bot.waitingForPacket:
    return "receiving packets"
  "waiting for packet, no frame for " & $bot.packetWaitMs &
    "ms, timeouts=" & $bot.packetWaitTimeouts

proc clearPacketWait(bot: var Bot) =
  ## Clears packet wait state after a frame arrives.
  if bot.waitingForPacket and bot.packetWaitMs >= PacketWaitLogAfterMs:
    bot.logEvent(
      "notsus packet stream resumed: no frame for " &
        $bot.packetWaitMs & "ms timeouts=" & $bot.packetWaitTimeouts
    )
  bot.waitingForPacket = false
  bot.packetWaitMs = 0
  bot.packetWaitTimeouts = 0
  bot.packetWaitLogMs = -1

proc notePacketTimeout(bot: var Bot) =
  ## Records one receive timeout while waiting for the next frame packet.
  let now = getMonoTime()
  if not bot.waitingForPacket:
    bot.waitingForPacket = true
    bot.packetWaitStart = now
    bot.packetWaitTimeouts = 0
    bot.packetWaitLogMs = -1
  inc bot.packetWaitTimeouts
  bot.packetWaitMs =
    max(0, int((now - bot.packetWaitStart).inMilliseconds))
  bot.intent = bot.packetWaitText()
  bot.thought(bot.intent)
  if bot.packetWaitMs < PacketWaitLogAfterMs:
    return
  if bot.packetWaitLogMs >= 0 and
      bot.packetWaitMs - bot.packetWaitLogMs < PacketWaitLogEveryMs:
    return
  bot.packetWaitLogMs = bot.packetWaitMs
  let expectedPackets =
    max(1, (bot.packetWaitMs * sim.TargetFps) div 1000)
  bot.logEvent(
    "notsus waiting for packet: no frame for " &
      $bot.packetWaitMs & "ms expectedPackets=" & $expectedPackets &
      " timeouts=" & $bot.packetWaitTimeouts &
      " frameTick=" & $bot.frameTick &
      " serverTick=" & $bot.serverTick &
      " role=" & bot.role.roleName() &
      " voting=" & $bot.voting &
      " interstitial=" & $bot.interstitial &
      " text=" & (
        if bot.interstitialText.len > 0:
          bot.interstitialText
        elif bot.protocolInterstitialText.len > 0:
          bot.protocolInterstitialText
        else:
          "none"
      ) &
      " lastMask=" & inputMaskSummary(bot.lastMask) &
      " framesDropped=" & $bot.framesDropped &
      " skipped=" & $bot.skippedFrames
  )

proc roomLabelAt(bot: Bot, x, y: int): string =
  ## Returns a readable room label for one world point.
  let room = bot.roomAt(x, y)
  if not room.found:
    return "unknown"
  if room.inside:
    room.name
  else:
    "near " & room.name

proc visibleCrewmateRoom(bot: Bot, crewmate: CrewmateMatch): string =
  ## Returns the room label for one visible crewmate sprite.
  let tracked = bot.playerPointSeen(crewmate.colorIndex)
  if tracked.found:
    return bot.roomLabelAt(
      tracked.x + CollisionW div 2,
      tracked.y + CollisionH div 2
    )
  bot.roomLabelAt(
    bot.detectionCameraX() + crewmate.x + SpriteDrawOffX +
      CollisionW div 2,
    bot.detectionCameraY() + crewmate.y + SpriteDrawOffY +
      CollisionH div 2
  )

proc clearVisibleRoomLogs(bot: var Bot) =
  ## Logs exits for all previously visible player room observations.
  for i in 0 ..< bot.visibleRoomNames.len:
    if bot.visibleRoomNames[i].len == 0:
      continue
    bot.logEvent(
      "notsus saw " & playerColorName(i) &
        " exit " & bot.visibleRoomNames[i]
    )
    bot.visibleRoomNames[i] = ""

proc addHistory(
  history: var seq[string],
  value: string,
  maxLen: int
) =
  ## Adds one bounded history item unless it repeats the previous item.
  if value.len == 0 or value == "unknown":
    return
  if history.len > 0 and history[^1] == value:
    return
  history.add value
  while history.len > maxLen:
    history.delete(0)

proc rememberSelfRoom(bot: var Bot, room: string) =
  ## Records one room this bot visited during the round.
  bot.selfRoomHistory.addHistory(room, RoomHistoryMax)

proc rememberSeenRoom(
  bot: var Bot,
  colorIndex: int,
  room: string
) =
  ## Records one room where this bot saw a player during the round.
  if colorIndex < 0 or colorIndex >= bot.seenRoomHistory.len:
    return
  bot.seenRoomHistory[colorIndex].addHistory(room, RoomHistoryMax)

proc rememberSelfTaskRoom(bot: var Bot, room: string) =
  ## Counts one completed or fake task in a room.
  if room.len == 0 or room == "unknown":
    return
  for item in bot.selfTaskRooms.mitems:
    if item.room == room:
      inc item.count
      return
  bot.selfTaskRooms.add TaskRoomCount(room: room, count: 1)
  while bot.selfTaskRooms.len > TaskHistoryMax:
    bot.selfTaskRooms.delete(0)

proc rememberSelfTask(bot: var Bot, taskIndex: int) =
  ## Records one task this bot completed or convincingly faked.
  if taskIndex < 0 or taskIndex >= bot.sim.tasks.len:
    return
  var entry = bot.sim.tasks[taskIndex].name
  let room = bot.roomName()
  if room.len > 0 and room != "unknown":
    entry.add(" in " & room)
    bot.rememberSelfTaskRoom(room)
  bot.selfTaskHistory.addHistory(entry, TaskHistoryMax)

proc logRoomTransitions(bot: var Bot) =
  ## Logs room changes for this bot and visible players.
  if bot.interstitial or not bot.localized:
    if bot.lastLoggedRoom.len > 0:
      bot.logEvent("notsus exited " & bot.lastLoggedRoom)
      bot.lastLoggedRoom = ""
    bot.clearVisibleRoomLogs()
    return
  let currentRoom = bot.roomName()
  if currentRoom.len > 0 and
      currentRoom != "unknown" and
      currentRoom != bot.lastLoggedRoom:
    if bot.lastLoggedRoom.len > 0:
      bot.logEvent("notsus exited " & bot.lastLoggedRoom)
    bot.lastLoggedRoom = currentRoom
    bot.rememberSelfRoom(currentRoom)
    bot.logEvent(
      "notsus entered " & currentRoom & " as " & bot.role.roleName()
    )
  var seen: array[PlayerColorCount, bool]
  for crewmate in bot.visibleCrewmates:
    let colorIndex = crewmate.colorIndex
    if colorIndex < 0 or colorIndex >= PlayerColorCount:
      continue
    if colorIndex == bot.selfColorIndex:
      bot.visibleRoomNames[colorIndex] = ""
      continue
    let room = bot.visibleCrewmateRoom(crewmate)
    if room.len == 0 or room == "unknown":
      continue
    seen[colorIndex] = true
    let previousRoom = bot.visibleRoomNames[colorIndex]
    if previousRoom == room:
      continue
    if previousRoom.len > 0:
      bot.logEvent(
        "notsus saw " & playerColorName(colorIndex) &
          " exit " & previousRoom
      )
    bot.visibleRoomNames[colorIndex] = room
    bot.rememberSeenRoom(colorIndex, room)
    bot.logEvent(
      "notsus saw " & playerColorName(colorIndex) &
        " enter " & room
    )
  for i in 0 ..< bot.visibleRoomNames.len:
    if i == bot.selfColorIndex:
      bot.visibleRoomNames[i] = ""
      continue
    if bot.visibleRoomNames[i].len > 0 and not seen[i]:
      bot.logEvent(
        "notsus saw " & playerColorName(i) &
          " exit " & bot.visibleRoomNames[i]
      )
      bot.visibleRoomNames[i] = ""

proc voteTargetBaseSafeForRole(bot: Bot, target: int): bool =
  ## Returns true when target voting is legal before owner protection.
  if not bot.voteTargetCanBeSus(target):
    return false
  let colorIndex = bot.voteSlots[target].colorIndex
  if bot.role == RoleCrewmate and not bot.imposterKnown() and
      colorIndex >= 0 and
      colorIndex < PlayerColorCount:
    let directScore = bot.directVoteSusScore(colorIndex)
    if colorIndex < CrewRoleColorCount and
        directScore < EmergencyButtonStrongSusScore:
      return false
    if directScore < EmergencyButtonStrongSusScore:
      if colorIndex < bot.bodyReportColors.len and
          bot.bodyReportColors[colorIndex]:
        return false
      if bot.meetingCallKind == VoteCalledButton and
          colorIndex == bot.meetingCallCallerColor:
        return false
  if bot.imposterKnown():
    if bot.knownImposterColor(colorIndex):
      return false
  true

proc sameOwnerColor(bot: Bot, colorIndex: int): bool =
  ## Returns true when a color appears to share this bot's owner label.
  if colorIndex < 0 or colorIndex >= bot.playerDisplayNames.len:
    return false
  if bot.role == RoleCrewmate and
      not bot.imposterKnown() and
      bot.selfColorIndex >= 0 and
      bot.selfColorIndex < CrewRoleColorCount and
      colorIndex < CrewRoleColorCount:
    return true
  let
    selfKey = bot.selfOwnerNameKey()
    targetKey = bot.playerDisplayNames[colorIndex].ownerNameKey()
  selfKey.len > 0 and targetKey.len > 0 and selfKey == targetKey

proc sameOwnerVoteTarget(bot: Bot, target: int): bool =
  ## Returns true when a vote target appears to be our own policy copy.
  if target < 0 or target >= bot.votePlayerCount:
    return false
  bot.sameOwnerColor(bot.voteSlots[target].colorIndex)

proc hasDifferentOwnerVoteTarget(bot: Bot): bool =
  ## Returns true when a safe non-owner vote target exists.
  for target in 0 ..< bot.votePlayerCount:
    if not bot.voteTargetBaseSafeForRole(target):
      continue
    if not bot.sameOwnerVoteTarget(target):
      return true

proc voteTargetSafeForRole(bot: Bot, target: int): bool =
  ## Returns true when this role should be willing to vote for a target.
  if not bot.voteTargetBaseSafeForRole(target):
    return false
  if bot.role == RoleCrewmate and not bot.imposterKnown() and
      bot.sameOwnerVoteTarget(target) and
      bot.hasDifferentOwnerVoteTarget():
    return false
  true

proc knownImposterSummary(bot: Bot): string =
  ## Returns a compact debug string for known imposter colors.
  for i, known in bot.knownImposters:
    if not known:
      continue
    if result.len > 0:
      result.add(", ")
    result.add(playerColorName(i))
  if result.len == 0:
    result = "none"

proc cameraLockName(lock: CameraLock): string =
  ## Returns a human-readable camera lock name.
  case lock
  of NoLock: "none"
  of LocalPatchMapLock: "local patch"
  of FrameMapLock: "frame map"

proc passable(bot: Bot, x, y: int): bool =
  ## Returns true when a collision-sized body can occupy a pixel.
  if bot.navigationReady():
    return bot.navigator.passable(x, y)
  if x < 0 or y < 0 or x + CollisionW >= MapWidth or
      y + CollisionH >= MapHeight:
    return false
  for dy in 0 ..< CollisionH:
    for dx in 0 ..< CollisionW:
      if not bot.sim.walkMask[mapIndexSafe(x + dx, y + dy)]:
        return false
  true

proc heuristic(ax, ay, bx, by: int): int =
  ## Returns Manhattan distance between two world points.
  abs(ax - bx) + abs(ay - by)

proc findPath(bot: var Bot, goalX, goalY: int): seq[PathStep] {.measure.} =
  ## Finds a direct JPS+ path toward a goal.
  if not bot.navigationReady():
    return
  let
    startX = bot.playerWorldX()
    startY = bot.playerWorldY()
  if not bot.passable(startX, startY) or not bot.passable(goalX, goalY):
    return
  bot.navigator.findPath(startX, startY, goalX, goalY)

proc pathDistance(bot: var Bot, goalX, goalY: int): int =
  ## Returns the real JPS+ path distance to a goal.
  if bot.playerWorldX() == goalX and bot.playerWorldY() == goalY:
    return 0
  bot.navigator.pathDistance(
    bot.playerWorldX(),
    bot.playerWorldY(),
    goalX,
    goalY
  )

proc pathGoalMatches(bot: Bot, goalX, goalY: int): bool =
  ## Returns true when the cached route targets the requested goal.
  bot.path.len > 0 and bot.pathGoalX == goalX and bot.pathGoalY == goalY

proc advancePathCursor(bot: var Bot) =
  ## Advances the cached route cursor to the current player position.
  if bot.path.len == 0:
    bot.pathCursor = 0
    return
  if bot.pathCursor < 0:
    bot.pathCursor = 0
  if bot.pathCursor > bot.path.high:
    bot.pathCursor = bot.path.high
  if bot.navigationReady():
    bot.pathCursor = bot.navigator.advancePathCursor(
      bot.path,
      bot.pathCursor,
      bot.playerWorldX(),
      bot.playerWorldY()
    )

proc cachedPathUsable(bot: var Bot, goalX, goalY: int): bool =
  ## Returns true when the cached route can still be followed.
  if not bot.navigationReady():
    return false
  if not bot.pathGoalMatches(goalX, goalY):
    return false
  if bot.frameTick - bot.pathPlanTick >= PathReuseTicks:
    return false
  if bot.stuckFrames >= StuckFrameThreshold:
    return false
  bot.advancePathCursor()
  let
    x = bot.playerWorldX()
    y = bot.playerWorldY()
    step = bot.path[bot.pathCursor]
    motion = bot.motionState()
  if motion.waypointBehindMotion(step):
    return false
  heuristic(x, y, step.x, step.y) <= PathDeviationLimit

proc ensurePathTo(bot: var Bot, goalX, goalY: int): bool =
  ## Reuses or rebuilds the cached JPS+ route to one goal.
  if not bot.navigationReady():
    bot.astarMicros = 0
    return false
  if bot.cachedPathUsable(goalX, goalY):
    bot.astarMicros = 0
    return true
  let astarStart = getMonoTime()
  bot.path = bot.findPath(goalX, goalY)
  bot.astarMicros = int((getMonoTime() - astarStart).inMicroseconds)
  bot.pathCursor = 0
  bot.pathGoalX = goalX
  bot.pathGoalY = goalY
  bot.pathPlanTick = bot.frameTick
  if bot.path.len == 0:
    return false
  bot.advancePathCursor()
  true

proc goalDistance(bot: Bot, goalX, goalY: int): int =
  ## Returns the distance metric for choosing the next goal.
  heuristic(bot.playerWorldX(), bot.playerWorldY(), goalX, goalY)

proc taskGoalFor(
  bot: Bot,
  index: int,
  state: TaskState
): tuple[found: bool, index: int, x: int, y: int, name: string, state: TaskState] =
  ## Returns a reachable task goal inside one task rectangle.
  if index < 0 or index >= bot.sim.tasks.len:
    return
  let
    task = bot.sim.tasks[index]
    center = task.taskCenter()
  var
    bestDistance = high(int)
    bestX = 0
    bestY = 0
  proc iconVisibleAt(x, y: int): bool =
    let
      cameraX = x - PlayerWorldOffX
      cameraY = y - PlayerWorldOffY
      iconWorldX = task.x + task.w div 2 - SpriteSize div 2
      iconWorldY = task.y - SpriteSize - 2
    for bobY in -1 .. 1:
      let
        iconX = iconWorldX - cameraX
        iconY = iconWorldY + bobY - cameraY
      if iconX < 0 or iconY < 0 or
          iconX + SpriteSize > ScreenWidth or
          iconY + SpriteSize > ScreenHeight:
        return false
    true
  template considerRange(x0, y0, x1, y1: int, requireIcon: bool) =
    for y in max(task.y, y0) ..< min(task.y + task.h, y1):
      for x in max(task.x, x0) ..< min(task.x + task.w, x1):
        if not bot.passable(x, y):
          continue
        if requireIcon and not iconVisibleAt(x, y):
          continue
        let distance = heuristic(center.x, center.y, x, y)
        if distance < bestDistance:
          bestDistance = distance
          bestX = x
          bestY = y
  considerRange(
    task.x + TaskInnerMargin,
    task.y + TaskInnerMargin,
    task.x + task.w - TaskInnerMargin,
    task.y + task.h - TaskInnerMargin,
    true
  )
  if bestDistance == high(int):
    considerRange(
      task.x + TaskInnerMargin,
      task.y + TaskInnerMargin,
      task.x + task.w - TaskInnerMargin,
      task.y + task.h - TaskInnerMargin,
      false
    )
  if bestDistance == high(int):
    considerRange(
      task.x,
      task.y,
      task.x + task.w,
      task.y + task.h,
      false
    )
  if bestDistance == high(int):
    return
  (true, index, bestX, bestY, task.name, state)

proc buttonGoal(
  bot: Bot
): tuple[found: bool, index: int, x: int, y: int, name: string, state: TaskState] =
  ## Returns a reachable point inside the emergency button rectangle.
  let
    button = bot.sim.gameMap.button
    centerX = button.x + button.w div 2
    centerY = button.y + button.h div 2
  var
    bestDistance = high(int)
    bestX = 0
    bestY = 0
  for y in button.y ..< button.y + button.h:
    for x in button.x ..< button.x + button.w:
      if not bot.passable(x, y):
        continue
      let distance = heuristic(centerX, centerY, x, y)
      if distance < bestDistance:
        bestDistance = distance
        bestX = x
        bestY = y
  if bestDistance == high(int):
    return
  (true, -1, bestX, bestY, "Button", TaskMaybe)

proc homeGoal(
  bot: Bot
): tuple[found: bool, index: int, x: int, y: int, name: string, state: TaskState] =
  ## Returns this bot's remembered cafeteria home point.
  if not bot.homeSet:
    return bot.buttonGoal()
  if bot.isGhost or bot.passable(bot.homeX, bot.homeY):
    return (true, -1, bot.homeX, bot.homeY, "Home", TaskMaybe)
  var
    bestDistance = high(int)
    bestX = 0
    bestY = 0
  for y in max(0, bot.homeY - HomeSearchRadius) ..
      min(MapHeight - 1, bot.homeY + HomeSearchRadius):
    for x in max(0, bot.homeX - HomeSearchRadius) ..
        min(MapWidth - 1, bot.homeX + HomeSearchRadius):
      if not bot.passable(x, y):
        continue
      let distance = heuristic(bot.homeX, bot.homeY, x, y)
      if distance < bestDistance:
        bestDistance = distance
        bestX = x
        bestY = y
  if bestDistance == high(int):
    return bot.buttonGoal()
  (true, -1, bestX, bestY, "Home", TaskMaybe)

proc buttonCenter(bot: Bot): tuple[x, y: int] =
  ## Returns the center point of the emergency button.
  let button = bot.sim.gameMap.button
  (
    button.x + button.w div 2,
    button.y + button.h div 2
  )

proc playerOnButton(bot: Bot): bool =
  ## Returns true when this bot can press the emergency button.
  let
    button = bot.sim.gameMap.button
    px = bot.playerWorldX() + CollisionW div 2
    py = bot.playerWorldY() + CollisionH div 2
  px >= button.x and px < button.x + button.w and
    py >= button.y and py < button.y + button.h

proc emergencyEvidenceColor(bot: Bot): int =
  ## Returns a strong direct-evidence suspect worth calling a button for.
  var bestScore = EmergencyButtonStrongSusScore - 1
  result = VoteUnknown
  for colorIndex in 0 ..< PlayerColorCount:
    if colorIndex == bot.selfColorIndex:
      continue
    if bot.knownImposterColor(colorIndex):
      continue
    if bot.ventScores[colorIndex] <= 0:
      continue
    let score = bot.ventScores[colorIndex]
    if score > bestScore:
      bestScore = score
      result = colorIndex

proc emergencyRunawayColor(bot: Bot): int =
  ## Returns a close late-round hunter worth calling a button for.
  if bot.activeHunterColor < 0 or
      bot.activeHunterColor >= PlayerColorCount:
    return VoteUnknown
  let colorIndex = bot.activeHunterColor
  if colorIndex == bot.selfColorIndex or bot.knownImposterColor(colorIndex):
    return VoteUnknown
  let evasionActive =
    (
      bot.moveAwayUntilTick >= 0 and
      bot.frameTick < bot.moveAwayUntilTick
    ) or (
      bot.runawayUntilTick >= 0 and
      bot.frameTick < bot.runawayUntilTick
    )
  if not evasionActive:
    return VoteUnknown
  if bot.followingTicks[colorIndex] >= EmergencyButtonFollowingTicks:
    return colorIndex
  if bot.stalkerScores[colorIndex] >= EmergencyButtonStalkerScore:
    return colorIndex
  VoteUnknown

proc emergencyButtonThreatColor(bot: Bot): int =
  ## Returns the current hunter that should trigger the button.
  if bot.role != RoleCrewmate or
      bot.isGhost or
      bot.emergencyButtonUsed or
      not bot.cooldownPastPercent(EmergencyButtonCooldownPercent):
    return VoteUnknown
  result = bot.emergencyEvidenceColor()
  if result != VoteUnknown:
    return
  return bot.emergencyRunawayColor()

proc callEmergencyButtonAction(
  bot: var Bot,
  colorIndex: int
): uint8 =
  ## Presses action on the emergency button.
  bot.intent = "calling emergency button"
  result =
    if (bot.lastMask and ButtonA) != 0:
      0
    else:
      ButtonA
  bot.desiredMask = result
  bot.controllerMask = result
  bot.clearPath()
  if result == ButtonA:
    bot.emergencyButtonUsed = true
    let reason =
      if colorIndex == bot.activeHunterColor:
        "late close chase"
      else:
        "strong direct evidence"
    bot.logEvent(
      "notsus emergency button: calling because " &
        playerColorName(colorIndex) & " has " & reason
    )
  bot.thought(bot.intent)

proc ghostWanderReady(bot: Bot): bool =
  ## Returns true when a ghost should wander after finishing tasks.
  bot.isGhost and bot.buttonFallbackReady()

proc chooseGhostWanderGoal(bot: var Bot) =
  ## Chooses a random ghost wander point near the emergency button.
  let center = bot.buttonCenter()
  var
    dx = bot.rng.rand(GhostWanderRadius * 2) - GhostWanderRadius
    dy = bot.rng.rand(GhostWanderRadius * 2) - GhostWanderRadius
  if abs(dx) + abs(dy) <= GhostWanderArrivalRadius:
    dx = GhostWanderRadius div 2
  bot.ghostWanderGoalX = clamp(center.x + dx, 0, MapWidth - CollisionW)
  bot.ghostWanderGoalY = clamp(center.y + dy, 0, MapHeight - CollisionH)
  bot.ghostWanderGoalSet = true
  bot.ghostWanderNextTick = bot.frameTick + GhostWanderIntervalTicks

proc safeHidePointCount(bot: Bot): int =
  ## Returns the number of known crewmate hiding points.
  SafeHidePoints.len

proc safeHideGoalFor(
  bot: Bot,
  index: int
): tuple[found: bool, index: int, x: int, y: int, name: string, state: TaskState] =
  ## Returns a reachable goal near one safe hiding marker.
  if index < 0 or index >= SafeHidePoints.len:
    return
  let point = SafeHidePoints[index]
  if bot.passable(point.x, point.y):
    return (
      true,
      index,
      point.x,
      point.y,
      "Safe hide " & $(index + 1),
      TaskMaybe
    )
  var
    bestDistance = high(int)
    bestX = 0
    bestY = 0
  for y in max(0, point.y - SafeHideSearchRadius) ..
      min(MapHeight - 1, point.y + SafeHideSearchRadius):
    for x in max(0, point.x - SafeHideSearchRadius) ..
        min(MapWidth - 1, point.x + SafeHideSearchRadius):
      if not bot.passable(x, y):
        continue
      let distance = heuristic(point.x, point.y, x, y)
      if distance < bestDistance:
        bestDistance = distance
        bestX = x
        bestY = y
  if bestDistance == high(int):
    return
  (
    true,
    index,
    bestX,
    bestY,
    "Safe hide " & $(index + 1),
    TaskMaybe
  )

proc closestSafeHidePointIndex(bot: Bot): int =
  ## Returns the safe hiding point closest to the current player.
  let count = bot.safeHidePointCount()
  if count == 0:
    return -1
  let
    px = bot.playerWorldX()
    py = bot.playerWorldY()
  var bestDistance = high(int)
  result = 0
  for i in 0 ..< count:
    let
      point = SafeHidePoints[i]
      distance = heuristic(px, py, point.x, point.y)
    if distance < bestDistance:
      bestDistance = distance
      result = i

proc preferredSafeHidePointIndex(bot: Bot): int =
  ## Returns the stable safe hiding point for this bot.
  let count = bot.safeHidePointCount()
  if count == 0:
    return -1
  if bot.selfColorIndex >= 0:
    return bot.selfColorIndex mod count
  bot.closestSafeHidePointIndex()

proc currentSafeHideGoal(
  bot: Bot
): tuple[found: bool, index: int, x: int, y: int, name: string, state: TaskState] =
  ## Returns this bot's preferred reachable safe hiding goal.
  let count = bot.safeHidePointCount()
  if count == 0:
    return
  let startIndex = bot.preferredSafeHidePointIndex()
  if startIndex < 0:
    return
  for attempt in 0 ..< count:
    let index = (startIndex + attempt) mod count
    result = bot.safeHideGoalFor(index)
    if result.found:
      return

proc escapeLoopCount(bot: Bot): int =
  ## Returns the number of known crewmate escape loops.
  EscapeLoopStarts.len

proc escapeLoopPointCount(bot: Bot, loopIndex: int): int =
  ## Returns the number of points in one escape loop.
  if loopIndex < 0 or loopIndex >= bot.escapeLoopCount():
    return 0
  EscapeLoopLengths[loopIndex]

proc escapeLoopPoint(
  bot: Bot,
  loopIndex,
  pointIndex: int
): tuple[x: int, y: int] =
  ## Returns one escape loop point in fixed loop direction.
  let
    count = bot.escapeLoopPointCount(loopIndex)
    index = (pointIndex mod count + count) mod count
    flatIndex = EscapeLoopStarts[loopIndex] + index
  EscapeLoopPoints[flatIndex]

proc nextEscapeLoopPointIndex(bot: Bot, loopIndex, pointIndex: int): int =
  ## Returns the next point in one escape loop.
  let count = bot.escapeLoopPointCount(loopIndex)
  if count == 0:
    return -1
  if pointIndex < 0 or pointIndex >= count:
    return 0
  (pointIndex + 1) mod count

proc escapeLoopGoalFor(
  bot: Bot,
  loopIndex,
  pointIndex: int
): tuple[found: bool, index: int, x: int, y: int, name: string, state: TaskState] =
  ## Returns a reachable goal near one escape loop marker.
  if loopIndex < 0 or loopIndex >= bot.escapeLoopCount():
    return
  if pointIndex < 0 or pointIndex >= bot.escapeLoopPointCount(loopIndex):
    return
  let point = bot.escapeLoopPoint(loopIndex, pointIndex)
  if bot.passable(point.x, point.y):
    return (
      true,
      pointIndex,
      point.x,
      point.y,
      "Escape loop " & $(loopIndex + 1) & "." & $(pointIndex + 1),
      TaskMaybe
    )
  var
    bestDistance = high(int)
    bestX = 0
    bestY = 0
  for y in max(0, point.y - EscapeLoopSearchRadius) ..
      min(MapHeight - 1, point.y + EscapeLoopSearchRadius):
    for x in max(0, point.x - EscapeLoopSearchRadius) ..
        min(MapWidth - 1, point.x + EscapeLoopSearchRadius):
      if not bot.passable(x, y):
        continue
      let distance = heuristic(point.x, point.y, x, y)
      if distance < bestDistance:
        bestDistance = distance
        bestX = x
        bestY = y
  if bestDistance == high(int):
    return
  (
    true,
    pointIndex,
    bestX,
    bestY,
    "Escape loop " & $(loopIndex + 1) & "." & $(pointIndex + 1),
    TaskMaybe
  )

proc closestEscapeLoopPoint(
  bot: Bot
): tuple[found: bool, loopIndex: int, pointIndex: int] =
  ## Returns the nearest point on the nearest escape loop.
  var bestDistance = high(int)
  for loopIndex in 0 ..< bot.escapeLoopCount():
    for pointIndex in 0 ..< bot.escapeLoopPointCount(loopIndex):
      let point = bot.escapeLoopPoint(loopIndex, pointIndex)
      let distance = heuristic(
        bot.playerWorldX(),
        bot.playerWorldY(),
        point.x,
        point.y
      )
      if distance < bestDistance:
        bestDistance = distance
        result = (true, loopIndex, pointIndex)

proc activateEscapeLoop(bot: var Bot, colorIndex: int) =
  ## Starts runaway mode from the nearest point of the nearest loop.
  bot.activeHunterColor = colorIndex
  bot.logEvent(
    "notsus crew evasion: escape loop from " &
      playerColorName(colorIndex)
  )
  let start = bot.closestEscapeLoopPoint()
  if start.found:
    bot.escapeLoopIndex = start.loopIndex
    bot.escapeLoopPointIndex = start.pointIndex
  else:
    bot.escapeLoopIndex = -1
    bot.escapeLoopPointIndex = -1
  bot.clearPath()

proc clearRunawayState(bot: var Bot) =
  ## Clears all close-threat movement state.
  if bot.activeHunterColor != VoteUnknown:
    bot.runawayBackoffColor = bot.activeHunterColor
    bot.runawayBackoffUntilTick =
      bot.frameTick + RunawayRestartDelayTicks
  bot.activeHunterColor = VoteUnknown
  bot.moveAwayUntilTick = -1
  bot.runawayUntilTick = -1
  bot.escapeLoopIndex = -1
  bot.escapeLoopPointIndex = -1
  bot.clearPath()

proc moveAwayActive(bot: Bot): bool =
  ## Returns true while the bot should move directly away.
  bot.role == RoleCrewmate and
    not bot.isGhost and
    bot.activeHunterColor != VoteUnknown and
    bot.frameTick < bot.moveAwayUntilTick

proc runawayActive(bot: Bot): bool =
  ## Returns true while the bot should run an escape loop.
  bot.role == RoleCrewmate and
    not bot.isGhost and
    bot.activeHunterColor != VoteUnknown and
    bot.frameTick < bot.runawayUntilTick

proc runawayBackoffActive(bot: Bot, colorIndex: int): bool =
  ## Returns true when a recent evasion should not restart yet.
  colorIndex != VoteUnknown and
    colorIndex == bot.runawayBackoffColor and
    bot.frameTick < bot.runawayBackoffUntilTick

proc startMoveAway(bot: var Bot, colorIndex: int) =
  ## Starts the direct move-away phase for one close player.
  bot.activeHunterColor = colorIndex
  bot.logEvent(
    "notsus crew evasion: moving away from " &
      playerColorName(colorIndex)
  )
  bot.moveAwayUntilTick = bot.frameTick + MoveAwayTicks
  bot.runawayUntilTick = -1
  bot.escapeLoopIndex = -1
  bot.escapeLoopPointIndex = -1
  bot.clearPath()

proc updateRunawayState(bot: var Bot) =
  ## Updates close-threat move-away and escape-loop timers.
  if bot.role != RoleCrewmate or
      bot.isGhost or
      not bot.cooldownPastRunawayThreshold():
    if bot.activeHunterColor != VoteUnknown:
      bot.clearRunawayState()
    return
  let threatColor = bot.closestPlayerWithin(RunawayTriggerRadius)
  if bot.runawayActive():
    return
  if bot.moveAwayActive():
    if threatColor != VoteUnknown:
      bot.activeHunterColor = threatColor
    return
  if bot.activeHunterColor != VoteUnknown and bot.moveAwayUntilTick >= 0:
    if threatColor != VoteUnknown:
      bot.activateEscapeLoop(threatColor)
      bot.runawayUntilTick = bot.frameTick + RunawayLoopTicks
      bot.runawayBackoffColor = threatColor
      bot.runawayBackoffUntilTick =
        bot.runawayUntilTick + RunawayRestartDelayTicks
      bot.moveAwayUntilTick = -1
    else:
      bot.clearRunawayState()
    return
  if bot.activeHunterColor != VoteUnknown and bot.runawayUntilTick >= 0:
    if threatColor != VoteUnknown:
      if bot.runawayBackoffActive(threatColor):
        bot.clearRunawayState()
        return
      bot.startMoveAway(threatColor)
    else:
      bot.clearRunawayState()
    return
  if threatColor != VoteUnknown and not bot.runawayBackoffActive(threatColor):
    bot.startMoveAway(threatColor)

proc currentEscapeLoopGoal(
  bot: var Bot
): tuple[found: bool, index: int, x: int, y: int, name: string, state: TaskState] =
  ## Returns the current reachable escape loop goal.
  if bot.escapeLoopIndex < 0 or
      bot.escapeLoopIndex >= bot.escapeLoopCount():
    let start = bot.closestEscapeLoopPoint()
    if not start.found:
      return
    bot.escapeLoopIndex = start.loopIndex
    bot.escapeLoopPointIndex = start.pointIndex
  let count = bot.escapeLoopPointCount(bot.escapeLoopIndex)
  if count == 0:
    return
  if bot.escapeLoopPointIndex < 0 or bot.escapeLoopPointIndex >= count:
    bot.escapeLoopPointIndex = 0
  for attempt in 0 ..< count:
    result = bot.escapeLoopGoalFor(
      bot.escapeLoopIndex,
      bot.escapeLoopPointIndex
    )
    if result.found:
      return
    bot.escapeLoopPointIndex = bot.nextEscapeLoopPointIndex(
      bot.escapeLoopIndex,
      bot.escapeLoopPointIndex
    )

proc prowlPointCount(bot: Bot): int =
  ## Returns the number of imposter prowl points.
  ProwlPoints.len

proc prowlGoalFor(
  bot: Bot,
  index: int
): tuple[found: bool, index: int, x: int, y: int, name: string, state: TaskState] =
  ## Returns a reachable goal near one prowl marker.
  if index < 0 or index >= ProwlPoints.len:
    return
  let point = ProwlPoints[index]
  if bot.isGhost or bot.passable(point.x, point.y):
    return (
      true,
      index,
      point.x,
      point.y,
      "Prowl " & $(index + 1),
      TaskMaybe
    )
  var
    bestDistance = high(int)
    bestX = 0
    bestY = 0
  for y in max(0, point.y - ProwlPointSearchRadius) ..
      min(MapHeight - 1, point.y + ProwlPointSearchRadius):
    for x in max(0, point.x - ProwlPointSearchRadius) ..
        min(MapWidth - 1, point.x + ProwlPointSearchRadius):
      if not bot.passable(x, y):
        continue
      let distance = heuristic(point.x, point.y, x, y)
      if distance < bestDistance:
        bestDistance = distance
        bestX = x
        bestY = y
  if bestDistance == high(int):
    return
  (
    true,
    index,
    bestX,
    bestY,
    "Prowl " & $(index + 1),
    TaskMaybe
  )

proc nextProwlPointIndex(bot: Bot, index: int): int =
  ## Returns the next prowl point index in path order.
  let count = bot.prowlPointCount()
  if count == 0:
    return -1
  if index < 0 or index >= count:
    return 0
  (index + 1) mod count

proc closestProwlPointIndex(bot: Bot): int =
  ## Returns the prowl point closest to the current player.
  let count = bot.prowlPointCount()
  if count == 0:
    return -1
  let
    px = bot.playerWorldX()
    py = bot.playerWorldY()
  var bestDistance = high(int)
  result = 0
  for i in 0 ..< count:
    let
      point = ProwlPoints[i]
      distance = heuristic(px, py, point.x, point.y)
    if distance < bestDistance:
      bestDistance = distance
      result = i

proc initialProwlPointIndex(bot: Bot): int =
  ## Returns a prowl point that spreads paired imposters apart.
  let count = bot.prowlPointCount()
  if count == 0:
    return -1
  result = bot.closestProwlPointIndex()
  if bot.imposterKnown() and
      bot.selfColorIndex >= 0 and
      bot.selfColorIndex mod 2 == 1:
    result = (result + count div 2) mod count

proc currentProwlGoal(
  bot: var Bot
): tuple[found: bool, index: int, x: int, y: int, name: string, state: TaskState] =
  ## Returns the current reachable prowl goal in path order.
  let count = bot.prowlPointCount()
  if count == 0:
    return
  if bot.imposterProwlIndex < 0 or bot.imposterProwlIndex >= count:
    bot.imposterProwlIndex = bot.initialProwlPointIndex()
  for attempt in 0 ..< count:
    result = bot.prowlGoalFor(bot.imposterProwlIndex)
    if result.found:
      return
    bot.imposterProwlIndex = bot.nextProwlPointIndex(
      bot.imposterProwlIndex
    )

proc visibleCrewmateWorld(
  bot: Bot,
  crewmate: CrewmateMatch
): tuple[x: int, y: int] =
  ## Converts one visible crewmate match into world coordinates.
  (
    bot.cameraX + crewmate.x + SpriteDrawOffX,
    bot.cameraY + crewmate.y + SpriteDrawOffY
  )

proc chasePointNear(
  bot: Bot,
  x,
  y,
  fallbackX,
  fallbackY: int
): tuple[x: int, y: int] =
  ## Returns a nearby passable chase point for one predicted position.
  let
    targetX = clamp(x, 0, MapWidth - CollisionW)
    targetY = clamp(y, 0, MapHeight - CollisionH)
  if bot.passable(targetX, targetY):
    return (targetX, targetY)
  var bestDistance = high(int)
  result = (fallbackX, fallbackY)
  for yy in max(0, targetY - ChasePredictionSearchRadius) ..
      min(MapHeight - CollisionH, targetY + ChasePredictionSearchRadius):
    for xx in max(0, targetX - ChasePredictionSearchRadius) ..
        min(MapWidth - CollisionW, targetX + ChasePredictionSearchRadius):
      if not bot.passable(xx, yy):
        continue
      let distance = heuristic(targetX, targetY, xx, yy)
      if distance < bestDistance:
        bestDistance = distance
        result = (xx, yy)

proc predictedTrackWorld(
  bot: Bot,
  track: PlayerTrack
): tuple[x: int, y: int] =
  ## Returns the predicted chase point for a tracked player.
  if track.colorIndex < 0 or track.colorIndex >= PlayerColorCount:
    return bot.chasePointNear(track.x, track.y, track.x, track.y)
  bot.chasePointNear(
    track.x + bot.playerVelocityX[track.colorIndex] * ChaseLeadTicks,
    track.y + bot.playerVelocityY[track.colorIndex] * ChaseLeadTicks,
    track.x,
    track.y
  )

proc playerTrackDistanceFrom(
  track: PlayerTrack,
  x,
  y: int
): int =
  ## Returns one tracked player's distance from a world point.
  heuristic(x, y, track.x, track.y)

proc knownAliveCrewTargets(bot: Bot): int =
  ## Counts living non-imposter targets from the vote screen.
  if bot.votePlayerCount <= 0:
    return high(int)
  for slot in 0 ..< bot.votePlayerCount:
    if not bot.voteSlots[slot].alive:
      continue
    let colorIndex = bot.voteSlots[slot].colorIndex
    if colorIndex == bot.selfColorIndex:
      continue
    if bot.knownImposterColor(colorIndex):
      continue
    inc result

proc killWitnessCount(
  bot: Bot,
  candidate: PlayerTrack,
  deadColor = VoteUnknown
): int =
  ## Counts fresh non-imposter witnesses near a candidate kill.
  let
    selfX = bot.playerWorldX()
    selfY = bot.playerWorldY()
  for colorIndex in 0 ..< PlayerColorCount:
    if colorIndex == bot.selfColorIndex or
        colorIndex == candidate.colorIndex or
        colorIndex == deadColor:
      continue
    if bot.knownImposterColor(colorIndex):
      continue
    if bot.frameTick - bot.bodySeenTicks[colorIndex] <= PlayerTrackMemoryTicks:
      continue
    let world = bot.trackedPlayerWorld(colorIndex)
    if not world.found:
      continue
    let
      targetDistance = heuristic(candidate.x, candidate.y, world.x, world.y)
      selfDistance = heuristic(selfX, selfY, world.x, world.y)
    if targetDistance <= KillWitnessAvoidRadius or
        selfDistance <= KillWitnessSelfRadius:
      inc result

proc safeKillTarget(
  bot: Bot,
  candidate: PlayerTrack,
  deadColor = VoteUnknown
): bool =
  ## Returns true when a kill target is isolated or the endgame is small.
  if bot.knownAliveCrewTargets() <= KillWitnessAllowAliveCrew:
    return true
  bot.killWitnessCount(candidate, deadColor) == 0

proc nearestCrewmateFrom(
  bot: Bot,
  x,
  y: int
): tuple[found: bool, track: PlayerTrack, distance: int] =
  ## Returns the nearest tracked non-imposter crewmate from one world point.
  result.distance = high(int)
  for colorIndex in 0 ..< PlayerColorCount:
    if colorIndex == bot.selfColorIndex:
      continue
    if bot.knownImposterColor(colorIndex):
      continue
    let world = bot.trackedPlayerWorld(colorIndex)
    if not world.found:
      continue
    let track = PlayerTrack(colorIndex: colorIndex, x: world.x, y: world.y)
    let distance = track.playerTrackDistanceFrom(x, y)
    if distance < result.distance:
      result = (true, track, distance)

proc imposterClaimsCrewmate(
  bot: Bot,
  imposter,
  candidate: PlayerTrack,
  selfDistance: int
): bool =
  ## Returns true when another imposter has first claim on a crewmate.
  let claimed = bot.nearestCrewmateFrom(imposter.x, imposter.y)
  claimed.found and
    claimed.track.colorIndex == candidate.colorIndex and
    claimed.distance <= selfDistance

proc nearestUnclaimedCrewmate(
  bot: Bot,
  requireSafeKill = false,
  deadColor = VoteUnknown
): tuple[found: bool, track: PlayerTrack] =
  ## Returns the nearest tracked crewmate not claimed by another imposter.
  let
    selfX = bot.playerWorldX()
    selfY = bot.playerWorldY()
  var bestDistance = high(int)
  for colorIndex in 0 ..< PlayerColorCount:
    if colorIndex == bot.selfColorIndex:
      continue
    if bot.knownImposterColor(colorIndex):
      continue
    let world = bot.trackedPlayerWorld(colorIndex)
    if not world.found:
      continue
    let track = PlayerTrack(colorIndex: colorIndex, x: world.x, y: world.y)
    let selfDistance = track.playerTrackDistanceFrom(selfX, selfY)
    var claimed = false
    for imposterColor in 0 ..< PlayerColorCount:
      if imposterColor == bot.selfColorIndex:
        continue
      if not bot.knownImposterColor(imposterColor):
        continue
      let imposterWorld = bot.trackedPlayerWorld(imposterColor)
      if not imposterWorld.found:
        continue
      let imposter = PlayerTrack(
        colorIndex: imposterColor,
        x: imposterWorld.x,
        y: imposterWorld.y
      )
      if bot.imposterClaimsCrewmate(imposter, track, selfDistance):
        claimed = true
        break
    if claimed:
      continue
    if requireSafeKill and not bot.safeKillTarget(track, deadColor):
      continue
    if selfDistance < bestDistance:
      bestDistance = selfDistance
      result = (true, track)

proc visibleBodyWorld(bot: Bot, body: BodyMatch): tuple[x: int, y: int] =
  ## Converts one visible body match into world coordinates.
  (
    bot.cameraX + body.x + SpriteDrawOffX,
    bot.cameraY + body.y + SpriteDrawOffY
  )

proc nearestBody(bot: Bot): tuple[found: bool, x: int, y: int, colorIndex: int] =
  ## Returns the nearest visible body in world coordinates.
  var bestDistance = high(int)
  for body in bot.visibleBodies:
    let world = bot.visibleBodyWorld(body)
    let distance = heuristic(
      bot.playerWorldX(),
      bot.playerWorldY(),
      world.x,
      world.y
    )
    if distance < bestDistance:
      bestDistance = distance
      result = (true, world.x, world.y, body.colorIndex)

proc sameBody(ax, ay, bx, by: int): bool =
  ## Returns true when two body sightings are probably the same body.
  if bx == low(int) or by == low(int):
    return false
  heuristic(ax, ay, bx, by) <= BodySearchRadius + 4

proc killerScoreForDistance(distance: int): int =
  ## Returns the score added for a player near a new body.
  if distance <= 1:
    return KillerEvidenceRadius
  max(0, KillerEvidenceRadius - distance)

proc addKillerScoresForBody(bot: var Bot, body: BodyMatch) =
  ## Adds distance-weighted killer evidence for one new body.
  let world = bot.visibleBodyWorld(body)
  for colorIndex in 0 ..< PlayerColorCount:
    if colorIndex == bot.selfColorIndex:
      continue
    if colorIndex == body.colorIndex:
      continue
    let track = bot.trackedPlayerWorld(colorIndex)
    if not track.found:
      continue
    let
      dx = track.x + CollisionW div 2 - (world.x + CollisionW div 2)
      dy = track.y + CollisionH div 2 - (world.y + CollisionH div 2)
      distance = max(abs(dx), abs(dy))
      score = killerScoreForDistance(distance)
    if score > 0:
      bot.killerScores[colorIndex] += score

proc rememberKillEvidence(bot: var Bot, body: BodyMatch) =
  ## Records player scores when a new body first appears.
  let world = bot.visibleBodyWorld(body)
  if sameBody(world.x, world.y, bot.lastKillBodyX, bot.lastKillBodyY):
    return
  bot.lastKillBodyX = world.x
  bot.lastKillBodyY = world.y
  bot.addKillerScoresForBody(body)

proc updateKillEvidence(bot: var Bot) =
  ## Updates kill evidence when bodies newly appear on screen.
  if bot.interstitial or bot.imposterKnown():
    return
  if bot.visibleBodies.len == 0:
    bot.hadVisibleBody = false
    return
  if bot.hadVisibleBody:
    return
  for body in bot.visibleBodies:
    bot.rememberKillEvidence(body)
  bot.hadVisibleBody = true

proc bodySuspectColorNear(bot: Bot, x, y: int): int =
  ## Returns the nearest living crewmate color within body suspicion range.
  var bestDistance = high(int)
  result = VoteUnknown
  for crewmate in bot.visibleCrewmates:
    if crewmate.colorIndex < 0 or
        crewmate.colorIndex >= PlayerColorNames.len:
      continue
    if crewmate.colorIndex == bot.selfColorIndex:
      continue
    if bot.knownImposterColor(crewmate.colorIndex):
      continue
    let
      world = bot.visibleCrewmateWorld(crewmate)
      dx = world.x + CollisionW div 2 - (x + CollisionW div 2)
      dy = world.y + CollisionH div 2 - (y + CollisionH div 2)
      distance = dx * dx + dy * dy
    if distance > BodySuspectRange * BodySuspectRange:
      continue
    if distance < bestDistance:
      bestDistance = distance
      result = crewmate.colorIndex

proc suspectedColorFrom(
  bot: Bot,
  ticks: openArray[int]
): tuple[found: bool, name: string, tick: int, colorIndex: int] =
  ## Returns the most recently seen valid crewmate color.
  var bestTick = 0
  for i, tick in ticks:
    if i == bot.selfColorIndex:
      continue
    if bot.knownImposterColor(i):
      continue
    if tick > bestTick and i < PlayerColorNames.len:
      bestTick = tick
      result = (true, playerColorName(i), tick, i)

proc suspectedColor(
  bot: Bot
): tuple[found: bool, name: string, tick: int, colorIndex: int] =
  ## Returns the most recently seen crewmate color.
  bot.suspectedColorFrom(bot.lastSeenTicks)

proc bodySuspectedColor(
  bot: Bot
): tuple[found: bool, name: string, tick: int, colorIndex: int] =
  ## Returns the most recently seen crewmate around the reported body.
  result = bot.suspectedColorFrom(bot.bodySeenTicks)
  if not result.found:
    result = bot.suspectedColor()

proc suspectSummary(bot: Bot): string =
  ## Returns a short debug summary for the current suspect.
  let suspect = bot.suspectedColor()
  if not suspect.found:
    return "none"
  suspect.name & " seen=" & $suspect.tick

proc bodyRoomMessage(bot: Bot, x, y: int): string =
  ## Builds a short body-location summary for voting context.
  let room = bot.roomAt(x + CollisionW div 2, y + CollisionH div 2)
  result =
    if not room.found:
      "body"
    elif room.inside:
      "body in " & chatRoomName(room.name)
    else:
      "body near " & chatRoomName(room.name)
  if bot.bodySusColor != VoteUnknown:
    result.add(" sus ")
    result.add(playerColorName(bot.bodySusColor))

proc rememberBodySuspects(bot: var Bot) =
  ## Stores the current seen-player ticks for voting after a body report.
  for i in 0 ..< bot.bodySeenTicks.len:
    bot.bodySeenTicks[i] = bot.lastSeenTicks[i]

proc queueBodySeen(bot: var Bot, x, y: int) =
  ## Stores the room for a discovered body until voting opens.
  if sameBody(x, y, bot.lastBodySeenX, bot.lastBodySeenY):
    return
  bot.lastBodySeenX = x
  bot.lastBodySeenY = y
  bot.rememberBodySuspects()
  bot.bodySusColor = bot.bodySuspectColorNear(x, y)

proc queueBodyReport(bot: var Bot, x, y: int) =
  ## Stores the room for a reported body until voting opens.
  if sameBody(x, y, bot.lastBodyReportX, bot.lastBodyReportY):
    return
  bot.lastBodyReportX = x
  bot.lastBodyReportY = y
  bot.rememberBodySuspects()
  bot.bodySusColor = bot.bodySuspectColorNear(x, y)

proc voteTargetName(bot: Bot, target: int): string =
  ## Returns a short display name for a voting target.
  if target == VoteSkip or target == bot.votePlayerCount:
    return "skip"
  if target >= 0 and target < bot.votePlayerCount:
    return playerColorName(bot.voteSlots[target].colorIndex)
  "unknown"

proc voteSummary(bot: Bot): string =
  ## Returns a compact summary of parsed votes.
  for i, choice in bot.voteChoices:
    if choice == VoteUnknown:
      continue
    if result.len > 0:
      result.add(", ")
    result.add(playerColorName(i))
    result.add("->")
    result.add(bot.voteTargetName(choice))
  if result.len == 0:
    result = "none"

proc sanitizedVoteSummary(bot: Bot): string =
  ## Returns parsed votes as the fake-crew brain would see them.
  for voterColor, choice in bot.voteChoices:
    if choice == VoteUnknown:
      continue
    if result.len > 0:
      result.add(", ")
    result.add(playerColorName(voterColor))
    result.add("->")
    result.add(bot.voteTargetName(choice))
  if result.len == 0:
    result = "none"

proc compareEvidence(a, b: EvidenceEntry): int =
  ## Sorts evidence entries by score, then by color order.
  result = cmp(b.score, a.score)
  if result == 0:
    result = cmp(a.colorIndex, b.colorIndex)

proc playerColorLogName(colorIndex: int): string =
  ## Returns a capitalized player color for logs.
  playerColorName(colorIndex).capitalizeAscii()

proc evidenceSummary(
  title: string,
  scores: openArray[int],
  minScore = 1
): string =
  ## Returns one sorted evidence summary line.
  var entries: seq[EvidenceEntry]
  for colorIndex, score in scores:
    if score >= minScore:
      entries.add(EvidenceEntry(colorIndex: colorIndex, score: score))
  if entries.len == 0:
    return ""
  entries.sort(compareEvidence)
  result = title & ": "
  for i, entry in entries:
    if i > 0:
      result.add(", ")
    result.add(playerColorLogName(entry.colorIndex))
    result.add(" ")
    result.add($entry.score)

proc reporterScores(bot: Bot): array[PlayerColorCount, int] =
  ## Returns one point for each player known to have reported a body.
  for colorIndex, reported in bot.bodyReportColors:
    if reported:
      result[colorIndex] = 1

proc knownDeadVoteCount(bot: Bot): int =
  ## Returns how many players are currently known dead on the vote screen.
  if not bot.voting:
    return
  for i in 0 ..< bot.votePlayerCount:
    if not bot.voteSlots[i].alive:
      inc result

proc reporterClearScore(bot: Bot, colorIndex: int): int =
  ## Returns the small clear score earned for reporting a body.
  if colorIndex < 0 or
      colorIndex >= bot.bodyReportColors.len or
      not bot.bodyReportColors[colorIndex]:
    return 0
  let deadCount = bot.knownDeadVoteCount()
  if deadCount <= 1:
    ReporterEarlyClearScore
  elif deadCount == 2:
    ReporterMidClearScore
  else:
    0

proc criticalCrewVote(bot: Bot): bool =
  ## Returns true when crew must vote with little room for soft clears.
  if bot.role != RoleCrewmate or not bot.voting:
    return false
  var aliveCount = 0
  for i in 0 ..< bot.votePlayerCount:
    if bot.voteSlots[i].alive:
      inc aliveCount
  aliveCount > 0 and aliveCount <= 4

proc urgentCrewVote(bot: Bot): bool =
  ## Returns true when crew should stop gathering and force a read.
  bot.role == RoleCrewmate and bot.voting and bot.knownDeadVoteCount() >= 2

proc susScores(bot: Bot): array[PlayerColorCount, int] =
  ## Returns derived sus scores from harmful and helpful evidence.
  let criticalVote = bot.criticalCrewVote()
  for colorIndex in 0 ..< PlayerColorCount:
    if criticalVote:
      result[colorIndex] =
        bot.stalkerScores[colorIndex] +
        bot.killerScores[colorIndex] * BodyProximitySusWeight +
        bot.standingAroundScores[colorIndex] +
        bot.ventScores[colorIndex] -
        bot.reporterClearScore(colorIndex) +
        bot.votedAgainstScores[colorIndex] * VotedAgainstSusWeight +
        bot.unsupportedClaimScores[colorIndex]
    else:
      result[colorIndex] =
        bot.stalkerScores[colorIndex] div 2 +
        bot.killerScores[colorIndex] * BodyProximitySusWeight -
        bot.taskerScores[colorIndex] * 10 -
        bot.timeTogetherScores[colorIndex] * 2 +
        bot.standingAroundScores[colorIndex] +
        bot.ventScores[colorIndex] -
        bot.reporterClearScore(colorIndex) +
        bot.votedAgainstScores[colorIndex] * VotedAgainstSusWeight +
        bot.unsupportedClaimScores[colorIndex]

proc sanitizedScores(
  bot: Bot,
  scores: openArray[int]
): array[PlayerColorCount, int] =
  ## Returns scores with hidden teammate facts removed for imposters.
  for colorIndex in 0 ..< min(result.len, scores.len):
    if bot.imposterKnown() and bot.knownImposterColor(colorIndex):
      result[colorIndex] = 0
    else:
      result[colorIndex] = scores[colorIndex]

proc sanitizedSusScores(bot: Bot): array[PlayerColorCount, int] =
  ## Returns total sus scores safe for imposter crew-brain reasoning.
  result = bot.sanitizedScores(bot.susScores())

proc votingEvidenceText(bot: Bot): string =
  ## Returns the compact evidence lines for this meeting.
  template addLine(line: string) =
    if line.len > 0:
      if result.len > 0:
        result.add('\n')
      result.add(line)
  addLine(evidenceSummary("stalkers", bot.sanitizedScores(bot.stalkerScores)))
  addLine(evidenceSummary("killers", bot.sanitizedScores(bot.killerScores)))
  addLine(evidenceSummary("taskers", bot.sanitizedScores(bot.taskerScores)))
  addLine(evidenceSummary(
    "safe together",
    bot.sanitizedScores(bot.timeTogetherScores)
  ))
  addLine(evidenceSummary(
    "standing around",
    bot.sanitizedScores(bot.standingAroundScores)
  ))
  addLine(evidenceSummary("venting", bot.sanitizedScores(bot.ventScores)))
  addLine(evidenceSummary("reporters", bot.sanitizedScores(bot.reporterScores())))
  addLine(evidenceSummary(
    "voted against",
    bot.sanitizedScores(bot.votedAgainstScores)
  ))
  addLine(evidenceSummary(
    "unsupported claims",
    bot.sanitizedScores(bot.unsupportedClaimScores)
  ))
  addLine(evidenceSummary("sus", bot.sanitizedSusScores(), SusVoteMinScore))

proc plainEvidenceLine(
  text: string,
  scores: openArray[int],
  minScore = 1
): string =
  ## Returns one plain-English evidence line without numbers.
  var entries: seq[EvidenceEntry]
  for colorIndex, score in scores:
    if score >= minScore:
      entries.add(EvidenceEntry(colorIndex: colorIndex, score: score))
  if entries.len == 0:
    return ""
  entries.sort(compareEvidence)
  result = text & " "
  for i, entry in entries:
    if i >= 3:
      break
    if i > 0:
      result.add(", ")
    result.add(playerColorLogName(entry.colorIndex))
  result.add(".")

proc plainVotingEvidenceText(bot: Bot): string =
  ## Returns private meeting observations as plain English.
  template addLine(line: string) =
    if line.len > 0:
      if result.len > 0:
        result.add('\n')
      result.add(line)
  addLine(plainEvidenceLine(
    "I saw these players following others:",
    bot.sanitizedScores(bot.stalkerScores)
  ))
  if bot.meetingCallKind == VoteCalledBody and
      bot.meetingCallCallerColor == bot.selfColorIndex and
      bot.lastBodyReportX != low(int) and
      bot.lastBodyReportY != low(int):
    addLine(plainEvidenceLine(
      "I need routes from these players near my body report:",
      bot.sanitizedScores(bot.killerScores),
      BodyProximityPlainMin
    ))
  addLine(plainEvidenceLine(
    "I saw these players doing tasks:",
    bot.sanitizedScores(bot.taskerScores)
  ))
  addLine(plainEvidenceLine(
    "I spent safe time near these players:",
    bot.sanitizedScores(bot.timeTogetherScores)
  ))
  addLine(plainEvidenceLine(
    "I saw these players standing around away from tasks:",
    bot.sanitizedScores(bot.standingAroundScores)
  ))
  addLine(plainEvidenceLine(
    "I saw these players move like a vent happened:",
    bot.sanitizedScores(bot.ventScores)
  ))
  addLine(plainEvidenceLine(
    "These players reported bodies quickly:",
    bot.sanitizedScores(bot.reporterScores())
  ))
  addLine(plainEvidenceLine(
    "Other players are pushing these players:",
    bot.sanitizedScores(bot.votedAgainstScores)
  ))
  addLine(plainEvidenceLine(
    "These players made strong claims I could not corroborate:",
    bot.sanitizedScores(bot.unsupportedClaimScores)
  ))

proc printVotingEvidence(bot: var Bot) =
  ## Prints compact sorted evidence once per meeting.
  if bot.voteEvidenceLogged:
    return
  bot.voteEvidenceLogged = true
  let evidence = bot.votingEvidenceText()
  if evidence.len > 0:
    bot.logBlock("notsus voting evidence", evidence)

proc configureVotingBedrock(bot: var Bot): bool =
  ## Copies voting Bedrock env settings into the shared adapter.
  let
    model = getEnv(
      "BEDROCK_CLAUDE_MODEL_ID",
      getEnv("BEDROCK_MODEL")
    ).strip()
    region = getEnv(
      "AWS_REGION",
      getEnv("AWS_DEFAULT_REGION")
    ).strip()
    credentialSource = bedrockAi.credentialSignalText()
  if not bot.bedrockConfigLogged:
    if credentialSource.len > 0:
      bot.logEvent(
        "notsus bedrock credentials enabled: source=" &
          credentialSource
      )
    else:
      bot.logEvent(
        "notsus bedrock credentials missing: expected " &
          "USE_BEDROCK or AWS credential env"
      )
    bot.logEvent(
      "notsus bedrock config: model=" &
        (if model.len > 0: model else: "default") &
        " region=" & (if region.len > 0: region else: "default") &
        " runtime=" & bedrockAi.runtimeText()
    )
    bot.logEvent(
      "notsus bedrock metadata keys: " &
        bedrockAi.requestMetadataKeys()
    )
    bot.bedrockConfigLogged = true
  if not bedrockAi.hasAwsCredentialSignal():
    return false
  if model.len > 0:
    bedrockAi.bedrockModel = model
  if region.len > 0:
    bedrockAi.bedrockRegion = region
  true

proc cleanLlmChatMessage(text: string): string =
  ## Cleans one model chat message into short in-game chat.
  proc hasDigit(word: string): bool =
    for ch in word:
      if ch >= '0' and ch <= '9':
        return true

  proc normalizedWord(word: string): string =
    for ch in word:
      if ch >= 'a' and ch <= 'z':
        result.add ch
      elif ch >= 'A' and ch <= 'Z':
        result.add ch.toLowerAscii()

  proc internalWord(word: string): bool =
    case word.normalizedWord()
    of "score", "scores", "metric", "metrics", "point", "points",
        "stalker", "stalkers", "killer", "killers", "tasker", "taskers",
        "reporter", "reporters", "evidence", "total", "number", "numbers",
        "internal", "private", "value", "values", "rating", "ratings",
        "tick", "ticks", "timer", "timers", "second", "seconds",
        "threshold", "thresholds", "coordinate", "coordinates", "percent",
        "cooldown", "high", "highest", "extremely", "hard", "but":
      true
    else:
      false

  proc trailingWord(word: string): bool =
    case word.normalizedWord()
    of "and", "or", "because", "with", "to", "from":
      true
    else:
      false

  proc casedWord(word, replacement: string): string =
    result = replacement
    let startsUpper =
      word.len > 0 and word[0] >= 'A' and word[0] <= 'Z'
    if startsUpper and result.len > 0:
      result[0] = result[0].toUpperAscii()

  proc publicWord(word: string): string =
    case word.normalizedWord()
    of "stalk":
      word.casedWord("follow")
    of "stalks":
      word.casedWord("follows")
    of "stalked":
      word.casedWord("followed")
    of "stalking":
      word.casedWord("following")
    else:
      word

  proc addWord(words: var seq[string], word: string) =
    if word.len == 0 or word.hasDigit():
      return
    if word.internalWord():
      if words.len > 0 and words[^1].normalizedWord() == "has":
        words.setLen(words.len - 1)
      return
    let visible = word.publicWord()
    if words.len > 0 and words[^1].normalizedWord() == visible.normalizedWord():
      return
    words.add visible

  var words: seq[string]
  var word = ""
  for ch in sim.cleanChatMessage(text):
    if ch <= ' ':
      words.addWord(word)
      word.setLen(0)
    else:
      word.add ch
    if words.len >= VoteLlmMaxMessageWords:
      break
  if words.len < VoteLlmMaxMessageWords:
    words.addWord(word)
  while words.len > 0 and words[^1].trailingWord():
    words.setLen(words.len - 1)
  sim.cleanChatMessage(words.join(" "))

proc unhelpfulSkipChatMessage(message: string): bool =
  ## Returns true when model chat would tell the room to do nothing.
  let text = message.normalizeChatText()
  if text.len == 0:
    return false
  if "do not skip" in text or
      "dont skip" in text or
      "never skip" in text:
    return false
  if "no read" in text or
      "no useful read" in text or
      "no info" in text or
      "nothing useful" in text:
    return true
  for word in strutils.splitWhitespace(text):
    case word
    of "skip", "skipping":
      return true
    else:
      discard

proc chatWords(text: string): seq[string] =
  ## Returns unique normalized words used for duplicate-chat checks.
  for word in strutils.splitWhitespace(text.normalizeChatText()):
    if word.len <= 2:
      continue
    case word
    of "the", "and", "that", "this", "with", "from", "what", "where",
        "who", "why", "was", "were", "are", "you", "near":
      continue
    else:
      if word notin result:
        result.add word

proc chatOverlapPercent(a, b: string): int =
  ## Returns the percent of the smaller chat message shared by word.
  let
    left = a.chatWords()
    right = b.chatWords()
  if left.len == 0 or right.len == 0:
    return 0
  var common = 0
  for word in left:
    if word in right:
      inc common
  common * 100 div max(1, min(left.len, right.len))

proc votingChatAlreadySaid(bot: Bot, message: string): bool =
  ## Returns true when recent visible chat already said this message.
  let normalized = message.normalizeChatText()
  if normalized.len == 0:
    return true
  for line in bot.voteChatLines:
    let other = line.text.normalizeChatText()
    if other.len == 0:
      continue
    if other == normalized:
      return true
    if message.chatOverlapPercent(line.text) >=
        VoteLlmDuplicateOverlapPercent:
      return true

proc socialVoteState(bot: Bot): SocialVoteState =
  ## Builds the pure social vote state from the current bot vote screen.
  result.playerCount = bot.votePlayerCount
  result.selfSlot = bot.voteSelfSlot
  result.selfColor = bot.selfColorIndex
  for i in 0 ..< result.slotColors.len:
    result.slotColors[i] = VoteUnknown
  for i in 0 ..< result.choices.len:
    result.choices[i] = VoteUnknown
  for i in 0 ..< min(bot.votePlayerCount, result.slotColors.len):
    result.slotColors[i] = bot.voteSlots[i].colorIndex
    result.slotAlive[i] = bot.voteSlots[i].alive
  for i in 0 ..< result.choices.len:
    if i < bot.voteChoices.len:
      result.choices[i] = bot.voteChoices[i]
    else:
      result.choices[i] = VoteUnknown
  for i in 0 ..< result.knownImposters.len:
    if i < bot.knownImposters.len:
      result.knownImposters[i] = bot.knownImposters[i]

proc effectiveSusScores(bot: Bot): array[PlayerColorCount, int] =
  ## Returns direct sus plus trusted social pressure.
  let
    direct = bot.sanitizedSusScores()
    state = bot.socialVoteState()
    social = effectiveSocialSus(direct, bot.socialGraph, state)
  for i in 0 ..< result.len:
    if i < social.len:
      result[i] = social[i]

proc bestLegalEffectiveScore(bot: Bot): int =
  ## Returns the best legal effective sus score on the current vote screen.
  let scores = bot.effectiveSusScores()
  result = low(int)
  for slot in 0 ..< bot.votePlayerCount:
    if not bot.voteTargetSafeForRole(slot):
      continue
    let colorIndex = bot.voteSlots[slot].colorIndex
    if colorIndex < 0 or colorIndex >= scores.len:
      continue
    result = max(result, scores[colorIndex])

proc bestLegalEffectiveVoteTarget(
  bot: Bot
): tuple[found: bool, target: int, score: int] =
  ## Returns the strongest legal target from effective sus evidence.
  let scores = bot.effectiveSusScores()
  result.target = VoteUnknown
  result.score = low(int)
  for target in 0 ..< bot.votePlayerCount:
    if not bot.voteTargetSafeForRole(target):
      continue
    let colorIndex = bot.voteSlots[target].colorIndex
    if colorIndex < 0 or colorIndex >= scores.len:
      continue
    let score = scores[colorIndex]
    if not result.found or score > result.score:
      result = (true, target, score)

proc targetDirectVoteSusScore(bot: Bot, target: int): int =
  ## Returns direct sus evidence for one legal voting target.
  if not bot.voteTargetSafeForRole(target):
    return 0
  let colorIndex = bot.voteSlots[target].colorIndex
  bot.directVoteSusScore(colorIndex)

proc bestLegalDirectVoteSusScore(bot: Bot): int =
  ## Returns the strongest direct sus score on the vote screen.
  for target in 0 ..< bot.votePlayerCount:
    if not bot.voteTargetSafeForRole(target):
      continue
    result = max(result, bot.targetDirectVoteSusScore(target))

proc bestLegalDirectVoteTarget(
  bot: Bot
): tuple[found: bool, target: int, score: int] =
  ## Returns the strongest legal target from direct evidence.
  result.target = VoteUnknown
  result.score = low(int)
  for target in 0 ..< bot.votePlayerCount:
    if not bot.voteTargetSafeForRole(target):
      continue
    let score = bot.targetDirectVoteSusScore(target)
    if not result.found or score > result.score:
      result = (true, target, score)

proc socialClaimTargetPlausible(bot: Bot, target: int): bool =
  ## Returns true when chat claims fit our own direct evidence.
  let
    targetDirect = bot.targetDirectVoteSusScore(target)
    bestDirect = bot.bestLegalDirectVoteSusScore()
  if targetDirect >= EmergencyButtonStrongSusScore:
    return true
  targetDirect >= SusVoteMinScore and
    targetDirect + SusVoteMinScore >= bestDirect

proc votePileTargetPlausible(bot: Bot, target: int): bool =
  ## Returns true when a vote pile does not contradict direct evidence.
  if target < 0 or target >= bot.votePlayerCount:
    return false
  let
    targetDirect = bot.targetDirectVoteSusScore(target)
    bestDirect = bot.bestLegalDirectVoteTarget()
  if bot.role == RoleCrewmate and not bot.imposterKnown():
    let targetColor = bot.voteSlots[target].colorIndex
    if targetColor < 0 or targetColor >= PlayerColorCount:
      return false
    let scores = bot.effectiveSusScores()
    if targetDirect < SusVoteMinScore and
        scores[targetColor] < CrewPileMinSusScore:
      return false
  if not bestDirect.found or bestDirect.score < SusVoteMinScore:
    return true
  if target == bestDirect.target:
    return true
  if targetDirect < SusVoteMinScore:
    return false
  if bestDirect.score >= EmergencyButtonStrongSusScore and
      targetDirect + SusVoteMinScore < bestDirect.score:
    return false
  true

proc socialSpeakerPlausible(bot: Bot, speakerSlot: int): bool =
  ## Returns true when one accuser is not privately suspicious.
  if speakerSlot < 0 or speakerSlot >= bot.votePlayerCount:
    return false
  let speakerColor = bot.voteSlots[speakerSlot].colorIndex
  if bot.imposterKnown() and bot.knownImposterColor(speakerColor):
    return false
  if speakerColor == bot.selfColorIndex:
    return true
  if not bot.voteSlots[speakerSlot].alive:
    return false
  let scores = bot.susScores()
  if speakerColor < 0 or speakerColor >= scores.len:
    return false
  scores[speakerColor] < SusVoteMinScore

proc socialSusClaimCountFor(bot: Bot, target: int): int =
  ## Counts living players who accuse one legal vote target in chat.
  if target < 0 or target >= bot.votePlayerCount:
    return
  if not bot.voteTargetSafeForRole(target):
    return
  let targetColor = bot.voteSlots[target].colorIndex
  if targetColor < 0 or targetColor >= PlayerColorCount:
    return
  for speakerColor in 0 ..< PlayerColorCount:
    if speakerColor == bot.selfColorIndex:
      continue
    let speakerSlot = bot.voteSlotForColor(speakerColor)
    if speakerSlot < 0 or
        speakerSlot >= bot.votePlayerCount or
        not bot.voteSlots[speakerSlot].alive:
      continue
    if not bot.socialSpeakerPlausible(speakerSlot):
      continue
    if bot.socialGraph[speakerColor][targetColor] > 0:
      inc result

proc socialSusClaimTarget(
  bot: Bot,
  minCount: int
): tuple[found: bool, target: int, count: int] =
  ## Returns the legal target with the broadest chat suspicion.
  let scores = bot.effectiveSusScores()
  var bestScore = low(int)
  result.target = VoteUnknown
  for target in 0 ..< bot.votePlayerCount:
    if not bot.voteTargetSafeForRole(target):
      continue
    let
      colorIndex = bot.voteSlots[target].colorIndex
      count = bot.socialSusClaimCountFor(target)
      score =
        if colorIndex >= 0 and colorIndex < scores.len:
          scores[colorIndex]
        else:
          low(int)
    if count < minCount:
      continue
    if not result.found or
        count > result.count or
        (count == result.count and score > bestScore) or
        (
          count == result.count and
          score == bestScore and
          colorIndex < bot.voteSlots[result.target].colorIndex
        ):
      result = (true, target, count)
      bestScore = score

proc socialGraphJson(bot: Bot): JsonNode =
  ## Builds plain social graph edges for the LLM.
  result = newJArray()
  for speaker in 0 ..< bot.socialGraph.len:
    for target in 0 ..< bot.socialGraph[speaker].len:
      let score = bot.socialGraph[speaker][target]
      if score == 0:
        continue
      result.add %*{
        "speaker": playerColorName(speaker),
        "target": playerColorName(target),
        "stance":
          if score > 0:
            "sus"
          else:
            "clear"
      }

proc hasConcreteKillOrVentClaim(text: string): bool =
  ## Returns true when text claims a real kill or vent action.
  for word in text.normalizeSocialText().splitWhitespace():
    if word in [
      "kill", "kills", "killed", "killer",
      "murder", "murdered", "vent", "vents", "vented"
    ]:
      return true

proc hasVoteOnlyClaim(text: string): bool =
  ## Returns true when a claim only restates a visible vote.
  var hasVote = false
  for word in text.normalizeSocialText().splitWhitespace():
    if word in ["vote", "votes", "voted", "voting"]:
      hasVote = true
    elif word in [
      "body", "bodies", "kill", "kills", "killed", "killer",
      "vent", "vents", "vented", "follow", "follows", "followed",
      "following", "standing", "task", "tasks", "route", "rooms"
    ]:
      return false
  hasVote

proc hasQuestionProbeClaim(text: string): bool =
  ## Returns true when a claim is only a route or body question.
  if text.hasConcreteKillOrVentClaim():
    return false
  var asks = false
  for word in text.normalizeSocialText().splitWhitespace():
    if word in [
      "ask", "asks", "asked", "asking", "question", "questions",
      "where", "route", "routes", "explain", "who", "closest",
      "exact", "exactly", "timeline", "account", "clarity",
      "quiet", "silent", "gap", "confirmed", "headed",
      "arrived", "late", "body", "bodies", "near", "danger"
    ]:
      asks = true
    elif word in [
      "follow", "follows", "followed", "following",
      "standing", "lurking", "taskless"
    ]:
      return false
  asks

proc unsupportedConcreteClaim(bot: Bot, claim: SocialClaim): bool =
  ## Returns true when a strong accusation lacks private corroboration.
  if bot.role != RoleCrewmate or bot.imposterKnown():
    return false
  if claim.stance != SocialSus:
    return false
  if claim.speaker == bot.selfColorIndex:
    return false
  if not claim.reason.hasConcreteKillOrVentClaim():
    return false
  if claim.target < 0 or claim.target >= PlayerColorCount:
    return false
  if not claim.reason.socialTargetHardActionClaim(claim.target):
    return false
  bot.directVoteSusScore(claim.target) < SusVoteMinScore

proc addUnsupportedClaimScore(bot: var Bot, colorIndex: int) =
  ## Adds suspicion to a player making an unsupported hard claim.
  if colorIndex < 0 or colorIndex >= bot.unsupportedClaimScores.len:
    return
  bot.unsupportedClaimScores[colorIndex] = min(
    UnsupportedClaimMaxScore,
    bot.unsupportedClaimScores[colorIndex] + UnsupportedClaimSusScore
  )

proc rememberSocialClaim(bot: var Bot, claim: SocialClaim): bool =
  ## Stores one new social graph claim if it has not been seen.
  if claim.speaker == bot.selfColorIndex:
    return false
  let key = claim.socialClaimKey()
  if key in bot.socialClaimKeys:
    return false
  if claim.stance == SocialSus and claim.reason.hasVoteOnlyClaim():
    bot.logEvent(
      "notsus social claim ignored: vote-only " &
        playerColorName(claim.speaker) & " -> " &
        playerColorName(claim.target)
    )
    return false
  if claim.stance == SocialSus and claim.reason.hasQuestionProbeClaim():
    bot.logEvent(
      "notsus social claim ignored: question-probe " &
        playerColorName(claim.speaker) & " -> " &
        playerColorName(claim.target)
    )
    return false
  if bot.unsupportedConcreteClaim(claim):
    bot.socialClaimKeys.add key
    bot.addUnsupportedClaimScore(claim.speaker)
    bot.logEvent(
      "notsus social claim reflected: unsupported hard claim " &
        playerColorName(claim.speaker) & " -> " &
        playerColorName(claim.target)
    )
    return true
  if bot.meetingCallKind == VoteCalledBody and
      claim.stance == SocialSus and
      claim.target == bot.meetingCallCallerColor and
      not claim.reason.hasConcreteKillOrVentClaim():
    bot.logEvent(
      "notsus social claim ignored: reporter body-only " &
        playerColorName(claim.speaker) & " -> " &
        playerColorName(claim.target)
    )
    return false
  bot.socialClaimKeys.add key
  bot.socialGraph.applySocialClaim(claim)
  bot.logEvent(
    "notsus social claim: " & playerColorName(claim.speaker) &
      " -> " & playerColorName(claim.target) & " " &
      $claim.stance & " " & $claim.strength
  )
  true

proc applyPlainChatClaims(bot: var Bot) =
  ## Adds social graph claims from visible plain-English chat.
  var applied = 0
  for line in bot.voteChatLines:
    for claim in parsePlainSocialClaims(line.speakerColor, line.text):
      if bot.rememberSocialClaim(claim):
        inc applied
  if applied > 0:
    bot.logEvent(
      "notsus plain chat claims: added " & $applied
    )

proc chatLinesJson(lines: openArray[VoteChatLine]): JsonNode =
  ## Builds JSON for visible voting chat lines.
  result = newJArray()
  for line in lines:
    var item = newJObject()
    item["speaker"] = %playerColorName(line.speakerColor)
    item["text"] = %line.text
    result.add item

proc votingChatLinesJson(bot: Bot): JsonNode =
  ## Builds JSON for voting chat after hidden teammate sanitization.
  result = newJArray()
  for line in bot.voteChatLines:
    var item = newJObject()
    item["speaker"] = %playerColorName(line.speakerColor)
    item["text"] = %line.text
    result.add item

proc sanitizedVotingChatText(bot: Bot): string =
  ## Returns visible chat text after hidden teammate sanitization.
  for line in bot.voteChatLines:
    if result.len > 0:
      result.add(' ')
    result.add(line.text)

proc mentionsKnownTeammate(bot: Bot, text: string): bool =
  ## Returns false because color names are normal fake-crew discussion.
  discard bot
  discard text
  false

proc playersJson(bot: Bot): JsonNode =
  ## Builds JSON for current voting slots.
  result = newJArray()
  for i in 0 ..< bot.votePlayerCount:
    let colorIndex = bot.voteSlots[i].colorIndex
    var item = newJObject()
    item["slot"] = %i
    item["color"] = %playerColorName(colorIndex)
    item["alive"] = %bot.voteSlots[i].alive
    item["self"] = %(i == bot.voteSelfSlot)
    if colorIndex >= 0 and colorIndex < bot.voteChoices.len:
      item["visible_vote"] = %bot.voteTargetName(bot.voteChoices[colorIndex])
    result.add item

proc knownImpostersJson(bot: Bot): JsonNode =
  ## Builds JSON for known imposter teammate colors.
  result = newJArray()
  for i, known in bot.knownImposters:
    if known:
      result.add %playerColorName(i)

proc stringListJson(items: openArray[string]): JsonNode =
  ## Builds a JSON string array.
  result = newJArray()
  for item in items:
    result.add %item

proc taskRoomsJson(bot: Bot): JsonNode =
  ## Builds JSON for how many tasks this bot did in each room.
  result = newJArray()
  for item in bot.selfTaskRooms:
    result.add %*{
      "room": item.room,
      "tasks": item.count
    }

proc seenRoomHistoryJson(bot: Bot): JsonNode =
  ## Builds JSON for rooms where this bot saw each player.
  result = newJObject()
  for colorIndex, rooms in bot.seenRoomHistory:
    if rooms.len == 0:
      continue
    result[playerColorName(colorIndex)] = stringListJson(rooms)

proc hasSelfReportedBody(bot: Bot): bool =
  ## Returns true when this bot reported a body for this meeting.
  bot.meetingCallKind == VoteCalledBody and
    bot.meetingCallCallerColor == bot.selfColorIndex and
    bot.lastBodyReportX != low(int) and
    bot.lastBodyReportY != low(int)

proc votingObservationJson(bot: Bot): JsonNode =
  ## Builds the full voting observation shown to the LLM.
  let
    fakeCrewmateRole =
      if bot.imposterKnown():
        RoleCrewmate
      else:
        bot.role
    selfReportedBody = not bot.imposterKnown() and bot.hasSelfReportedBody()
    bodySusColor =
      if selfReportedBody:
        bot.bodySusColor
      else:
        VoteUnknown
  result = newJObject()
  result["role"] = %fakeCrewmateRole.roleName()
  result["self_color"] = %playerColorName(bot.selfColorIndex)
  result["self_room"] = %bot.roomName()
  result["self_room_history"] = stringListJson(bot.selfRoomHistory)
  result["self_task_history"] = stringListJson(bot.selfTaskHistory)
  result["self_task_rooms"] = bot.taskRoomsJson()
  result["seen_player_rooms"] = bot.seenRoomHistoryJson()
  result["meeting_call_kind"] = %($bot.meetingCallKind)
  result["meeting_caller"] = %playerColorName(bot.meetingCallCallerColor)
  result["meeting_body"] = %playerColorName(bot.meetingCallBodyColor)
  result["body_sus_color"] = %playerColorName(bodySusColor)
  result["said_something"] = %bot.voteSaidSomething
  result["say_count"] = %bot.voteLlmSayCount
  result["self_reported_body"] = %selfReportedBody
  if selfReportedBody:
    result["reported_body_x"] = %bot.lastBodyReportX
    result["reported_body_y"] = %bot.lastBodyReportY
    result["reported_body_summary"] =
      %bot.bodyRoomMessage(bot.lastBodyReportX, bot.lastBodyReportY)
    result["reported_body_sus_color"] =
      %playerColorName(bodySusColor)
  if not bot.imposterKnown():
    result["known_imposters"] = bot.knownImpostersJson()
  result["players"] = bot.playersJson()
  result["visible_votes"] = %bot.sanitizedVoteSummary()
  result["chat_lines"] = bot.votingChatLinesJson()
  result["current_chat_text"] = %bot.sanitizedVotingChatText()
  let previousChat =
    if bot.mentionsKnownTeammate(bot.voteLlmPreviousChatText):
      ""
    else:
      bot.voteLlmPreviousChatText
  result["previous_chat_text"] = %previousChat
  result["private_observations"] = %bot.plainVotingEvidenceText()
  result["pending_chat"] = %bot.pendingChat
  result["social_graph"] = bot.socialGraphJson()

proc validVotingTargetNames(bot: Bot): string =
  ## Returns legal LLM vote target names for this vote.
  for i in 0 ..< bot.votePlayerCount:
    if not bot.voteTargetSafeForRole(i):
      continue
    if result.len > 0:
      result.add(", ")
    result.add(playerColorName(bot.voteSlots[i].colorIndex))
  if result.len == 0:
    result = "none"

proc livingVotingColorNames(bot: Bot): string =
  ## Returns living color names visible in the current vote.
  for i in 0 ..< bot.votePlayerCount:
    if not bot.voteSlots[i].alive:
      continue
    let colorIndex = bot.voteSlots[i].colorIndex
    if colorIndex < 0 or colorIndex >= PlayerColorCount:
      continue
    if result.len > 0:
      result.add(", ")
    result.add(playerColorName(colorIndex))
  if result.len == 0:
    result = "none"

proc votingPromptText(bot: Bot): string =
  ## Builds the current voting prompt sent to Bedrock.
  "Current voting observation JSON:\n" &
    $bot.votingObservationJson() &
    "\n\nLiving player color names for discussion: " &
    bot.livingVotingColorNames() &
    "\nLegal vote target color names for vote calls: " &
    bot.validVotingTargetNames() &
    "\nReturn one JSON object only."

proc externalVoteChatText(bot: Bot): string =
  ## Returns visible chat excluding this bot's own voting chat.
  for line in bot.voteChatLines:
    if bot.selfColorIndex != VoteUnknown and
        line.speakerColor == bot.selfColorIndex:
      continue
    if result.len > 0:
      result.add(' ')
    result.add(line.text)

proc votingLlmSnapshotKey(bot: Bot): string =
  ## Returns the event key that should trigger a fresh voting LLM call.
  $bot.meetingCallKind & "|" &
    playerColorName(bot.meetingCallCallerColor) & "|" &
    playerColorName(bot.meetingCallBodyColor) & "|" &
    bot.voteSummary() & "|" &
    bot.validVotingTargetNames() & "|" &
    bot.externalVoteChatText() & "|" &
    bot.votingEvidenceText()

proc logVotingLlmPrompt(bot: Bot) =
  ## Logs the current and previous private voting evidence.
  let currentSus = bot.votingEvidenceText()
  bot.logBlock(
    "notsus voting sus current",
    if currentSus.len > 0: currentSus else: "none"
  )
  bot.logBlock(
    "notsus voting sus previous",
    if bot.voteLlmPreviousSusText.len > 0:
      bot.voteLlmPreviousSusText
    else:
      "none"
  )

proc updateVotingLlmSnapshots(
  bot: var Bot,
  hadVoting: bool,
  previousChat,
  previousSus: string
) =
  ## Updates LLM prompt snapshots after parsing one voting frame.
  bot.voteLlmPreviousChatText = previousChat
  bot.voteLlmPreviousSusText = previousSus
  if not hadVoting:
    bot.voteLlmAction = VoteLlmAction(
      kind: VoteLlmNone,
      targetColor: VoteUnknown
    )
    bot.voteLlmWaiting = false
    bot.voteLlmNeedsDecision = true
    bot.voteLlmLastCallTick = -1
    bot.voteLlmRequestActive = false
    bot.voteLlmRequestTag = ""
    bot.voteLlmRequestSnapshotKey = ""
    bot.voteLlmRequestStartedTick = -1
    bot.voteLlmPromptChatText = ""
    bot.voteLlmPromptSusText = ""
    bot.voteLlmPromptKey = ""
    return
  if bot.selfVotingDead():
    bot.voteLlmNeedsDecision = false
    bot.pendingChat = ""
    bot.voteLlmAction = VoteLlmAction(
      kind: VoteLlmNone,
      targetColor: VoteUnknown
    )
    bot.voteLlmWaiting = false
    bot.voteLlmRequestActive = false
    bot.voteLlmRequestTag = ""
    bot.voteLlmRequestSnapshotKey = ""
    bot.voteLlmRequestStartedTick = -1
    bot.voteLlmPromptKey = bot.votingLlmSnapshotKey()
    return
  if bot.votingLlmSnapshotKey() != bot.voteLlmPromptKey:
    bot.voteLlmNeedsDecision = true

proc logSendingToLlm(
  bot: Bot,
  messages: openArray[bedrockAi.ConversationMessage]
) =
  ## Logs the full prompt sent to the voting LLM.
  bot.logEvent("notsus llm prompt message count: " & $messages.len)
  for message in messages:
    bot.logBlock("notsus llm prompt " & message.role, message.content)

proc clearVotingLlmDecision(
  bot: var Bot,
  needsDecision: bool
) =
  ## Clears the pending voting LLM decision state.
  bot.voteLlmAction = VoteLlmAction(
    kind: VoteLlmNone,
    targetColor: VoteUnknown
  )
  bot.voteLlmWaiting = false
  bot.voteLlmNeedsDecision = needsDecision
  bot.voteLlmRequestActive = false
  bot.voteLlmRequestTag = ""
  bot.voteLlmRequestSnapshotKey = ""
  bot.voteLlmRequestStartedTick = -1
  bot.pendingChat = ""

proc votingChatLeaksSecrets(bot: Bot, message: string): bool =
  ## Returns true when imposter chat exposes hidden-role information.
  if not bot.imposterKnown():
    return false
  for word in strutils.splitWhitespace(message.normalizeChatText()):
    case word
    of "teammate", "teammates", "partner", "partners", "ally",
        "allies", "imposter", "imposters", "impostor", "impostors",
        "known", "role":
      return true
    else:
      discard

proc safeVotingChatMessage(bot: Bot): string =
  ## Returns a bland safe chat line when a model leaks hidden state.
  case bot.meetingCallKind
  of VoteCalledBody:
    "Who was near the body?"
  of VoteCalledButton:
    "What happened? Who has info?"
  of VoteCalledUnknown:
    "What happened? Who has info?"

proc simpleRoomName(room: string): string =
  ## Returns one room name suitable for short fallback chat.
  result = room
  if result.startsWith("near "):
    result = result[5 .. ^1]

proc shortList(items: openArray[string], limit: int): string =
  ## Returns a short natural-language list without repeating items.
  var seen: seq[string]
  for i in countdown(items.len - 1, 0):
    let item = items[i].simpleRoomName().strip()
    if item.len == 0 or item in seen:
      continue
    seen.add item
    if seen.len >= limit:
      break
  seen.reverse()
  for i, item in seen:
    if i > 0:
      if i + 1 == seen.len:
        result.add " and "
      else:
        result.add ", "
    result.add item

proc taskRoomList(bot: Bot, limit: int): string =
  ## Returns a short natural-language list of rooms where we did tasks.
  var rooms: seq[string]
  for item in bot.selfTaskRooms:
    if item.room.len > 0:
      rooms.add item.room
  rooms.shortList(limit)

proc fallbackVariantIndex(bot: Bot, count, salt: int): int =
  ## Returns a stable per-seat fallback chat variant index.
  if count <= 0:
    return 0
  var value =
    bot.selfColorIndex * 17 +
    bot.voteLlmSayCount * 7 +
    bot.voteLlmCallCount * 5 +
    salt
  if value < 0:
    value = -value
  value mod count

proc selfFallbackChat(bot: Bot): string =
  ## Builds fallback chat answering where we were and what we did.
  let
    rooms = bot.selfRoomHistory.shortList(3)
    taskRooms = bot.taskRoomList(2)
  if rooms.len > 0 and taskRooms.len > 0:
    case bot.fallbackVariantIndex(6, 11)
    of 0:
      return "I was in " & rooms & ". I did tasks in " & taskRooms & "."
    of 1:
      return "My route was " & rooms & ". My tasks were in " & taskRooms & "."
    of 2:
      return "I went through " & rooms & " and worked in " & taskRooms & "."
    of 3:
      return "Rooms for me: " & rooms & ". Tasks were in " & taskRooms & "."
    of 4:
      return "I moved through " & rooms & ". Task work was in " &
        taskRooms & "."
    else:
      return "I passed " & rooms & ". I got task work done in " &
        taskRooms & "."
  if rooms.len > 0:
    case bot.fallbackVariantIndex(4, 23)
    of 0:
      return "I was in " & rooms & ". Who saw anything?"
    of 1:
      return "My route was " & rooms & ". What did others see?"
    of 2:
      return "I went through " & rooms & ". Who has useful info?"
    else:
      return "I passed " & rooms & ". Did anyone see the dead player?"
  if taskRooms.len > 0:
    case bot.fallbackVariantIndex(4, 37)
    of 0:
      return "I did tasks in " & taskRooms & ". Who saw anything?"
    of 1:
      return "My task work was in " & taskRooms & ". What happened?"
    of 2:
      return "I worked in " & taskRooms & ". Who saw something suspicious?"
    else:
      return "Tasks for me were in " & taskRooms & ". Who has info?"

proc externalChatAsksSelf(bot: Bot): bool =
  ## Returns true when visible chat asks this color for rooms or tasks.
  let
    text = bot.externalVoteChatText().normalizeChatText()
    self = playerColorName(bot.selfColorIndex)
  if text.len == 0:
    return false
  let named =
    self.len > 0 and self in strutils.splitWhitespace(text)
  let broad =
    "everyone" in text or "all " in text or "where were you" in text
  if not named and not broad:
    return false
  "where" in text or "room" in text or "rooms" in text or
    "task" in text or "tasks" in text or "saw" in text

proc fallbackTargetReason(bot: Bot, colorIndex: int): string =
  ## Returns a plain fallback reason for suspecting one color.
  if colorIndex < 0 or colorIndex >= PlayerColorCount:
    return "from this round"
  if bot.killerScores[colorIndex] >= BodyProximityPlainMin:
    return "near a body"
  if bot.ventScores[colorIndex] > 0:
    return "moving like a vent happened"
  if bot.standingAroundScores[colorIndex] > 0:
    return "standing away from tasks"
  if bot.stalkerScores[colorIndex] > 0:
    return "following people"
  if bot.votedAgainstScores[colorIndex] > 0:
    return "getting pushed by others"
  "from this round"

proc visibleVoteCountForSlot(bot: Bot, target: int): int =
  ## Returns how many living visible voters are on one target slot.
  if target < 0 or target >= bot.votePlayerCount:
    return
  for voterColor, choice in bot.voteChoices:
    if voterColor == bot.selfColorIndex or choice != target:
      continue
    let voterSlot = bot.voteSlotForColor(voterColor)
    if voterSlot >= 0 and
        voterSlot < bot.votePlayerCount and
        bot.voteSlots[voterSlot].alive:
      inc result

proc imposterVoteDanger(
  bot: Bot
): tuple[found: bool, colorIndex: int, slot: int, votes: int] =
  ## Returns the known imposter currently under the most visible votes.
  if not bot.imposterKnown():
    return
  result.colorIndex = VoteUnknown
  result.slot = VoteUnknown
  for colorIndex, known in bot.knownImposters:
    if not known:
      continue
    let slot = bot.voteSlotForColor(colorIndex)
    if slot < 0 or slot >= bot.votePlayerCount:
      continue
    if not bot.voteSlots[slot].alive:
      continue
    let votes = bot.visibleVoteCountForSlot(slot)
    if votes > result.votes:
      result = (votes > 0, colorIndex, slot, votes)

proc imposterCounterVoteTarget(
  bot: Bot,
  danger: tuple[found: bool, colorIndex: int, slot: int, votes: int]
): tuple[found: bool, target: int, reason: string] =
  ## Returns a legal countervote target when an imposter is pressured.
  result.target = VoteUnknown
  if not danger.found:
    return
  let scores = bot.effectiveSusScores()
  var
    bestTarget = VoteUnknown
    bestScore = low(int)
  for voterColor, choice in bot.voteChoices:
    if choice != danger.slot:
      continue
    let target = bot.voteSlotForColor(voterColor)
    if not bot.voteTargetSafeForRole(target):
      continue
    let score =
      if voterColor >= 0 and voterColor < scores.len:
        scores[voterColor]
      else:
        low(int)
    if target != VoteUnknown and (bestTarget == VoteUnknown or
        score > bestScore):
      bestTarget = target
      bestScore = score
  if bestTarget != VoteUnknown:
    return (
      true,
      bestTarget,
      "countering votes on " & playerColorName(danger.colorIndex) &
        " with accuser " & bot.voteTargetName(bestTarget)
    )
  let effective = bot.bestLegalEffectiveVoteTarget()
  if effective.found:
    return (
      true,
      effective.target,
      "countering votes on " & playerColorName(danger.colorIndex) &
        " with strongest safe target " &
        bot.voteTargetName(effective.target)
    )
  for target in 0 ..< bot.votePlayerCount:
    if bot.voteTargetSafeForRole(target):
      return (
        true,
        target,
        "countering votes on " & playerColorName(danger.colorIndex) &
          " with first safe target " & bot.voteTargetName(target)
      )

proc imposterDefenseChat(bot: Bot): string =
  ## Returns a fake-crew defense when public votes pressure an imposter.
  let
    danger = bot.imposterVoteDanger()
    counter = bot.imposterCounterVoteTarget(danger)
  if not danger.found or not counter.found:
    return
  let
    dangerName = playerColorName(danger.colorIndex)
    targetColor = bot.voteSlots[counter.target].colorIndex
    targetName = playerColorName(targetColor)
    targetReason =
      if bot.meetingCallKind == VoteCalledBody and
          targetColor == bot.meetingCallCallerColor:
        " reported without a full route"
      elif bot.meetingCallKind == VoteCalledButton and
          targetColor == bot.meetingCallCallerColor:
        " called button without a clear reason"
      else:
        " voted before giving route"
  if danger.colorIndex == bot.selfColorIndex:
    return "I do not buy this push. " & targetName &
      targetReason & ". Vote " & targetName & "."
  "I do not buy " & dangerName & " yet. " & targetName &
    targetReason & ". Vote " & targetName & "."

proc fallbackBodyQuestion(bot: Bot): string =
  ## Returns a varied fallback question for a body meeting.
  let
    body = playerColorName(bot.meetingCallBodyColor)
    caller = playerColorLogName(bot.meetingCallCallerColor)
    bodyText =
      if body != "unknown":
        body
      else:
        "the body"
  if body != "unknown" and caller != "Unknown":
    case bot.fallbackVariantIndex(5, 47)
    of 0:
      return body.capitalizeAscii() & " is dead. " & caller &
        " reported. Who was near " & body & " last?"
    of 1:
      return caller & " found " & body & ". Who saw " & body &
        " before the report?"
    of 2:
      return body.capitalizeAscii() & " died. " & caller &
        ", where was the body and who was nearby?"
    of 3:
      return caller & " reported " & body & ". Give rooms near " &
        body & " now."
    else:
      return "Body is " & body & ". " & caller &
        ", say location. Others give routes."
  if body != "unknown":
    case bot.fallbackVariantIndex(4, 53)
    of 0:
      return body.capitalizeAscii() &
        " is dead. Who was near " & body & " last?"
    of 1:
      return body.capitalizeAscii() &
        " died. Who saw them before the report?"
    of 2:
      return "Who was close to " & body & "? Say rooms and sightings."
    else:
      return "Need info on " & bodyText & ". Who crossed them last?"
  case bot.fallbackVariantIndex(4, 59)
  of 0:
    "Body reported. Who was near the body last?"
  of 1:
    "Where was the body? Everyone give route and last sightings."
  of 2:
    "Reporter, say location. Who was near the body?"
  else:
    "Body meeting. Give rooms, tasks, and who you saw."

proc fallbackSuspectLine(bot: Bot, colorIndex: int, reason: string): string =
  ## Returns a varied fallback accusation for one suspect.
  let
    name = playerColorLogName(colorIndex)
    lowerName = playerColorName(colorIndex)
  case bot.fallbackVariantIndex(8, colorIndex * 13 + 83)
  of 0:
    name & " is sus. I saw " & lowerName & " " &
      reason & ". " & name & ", where were you?"
  of 1:
    name & " worries me. I saw " & lowerName & " " &
      reason & ". Give your route."
  of 2:
    "My sus is " & lowerName & ": " & reason &
      ". " & name & ", explain rooms."
  of 3:
    "I do not like " & lowerName & " " & reason &
      ". " & name & ", what tasks?"
  of 4:
    name & " needs to answer. I saw " & lowerName & " " &
      reason & "."
  of 5:
    "I am leaning " & lowerName & " from " & reason &
      ". Who can confirm them?"
  of 6:
    lowerName.capitalizeAscii() & " looks off from " & reason &
      ". " & name & ", route now."
  else:
    "Pressure on " & lowerName & ". I saw " & lowerName & " " &
      reason & "; explain."

proc fallbackUrgentVoteLine(
  bot: Bot,
  colorIndex: int,
  reason: string
): string =
  ## Returns an urgent plain-English vote call for one suspect.
  let
    lowerName = playerColorName(colorIndex)
    name = lowerName.capitalizeAscii()
  case bot.fallbackVariantIndex(6, colorIndex * 19 + 97)
  of 0:
    "Vote " & lowerName & ". " & name & " is my best read from " &
      reason & "."
  of 1:
    "I am voting " & lowerName & ". " & name & " looks worst from " &
      reason & "."
  of 2:
    "We need a vote. " & name & " is my strongest sus from " &
      reason & "."
  of 3:
    "Do not skip. Vote " & lowerName & " from " & reason & "."
  of 4:
    "My vote is " & lowerName & ". " & name &
      " needs to go from " & reason & "."
  else:
    "Several dead. Vote " & lowerName & "; " & reason & " points there."

proc fallbackRouteProbeLine(bot: Bot, colorIndex: int): string =
  ## Returns a varied route question for weak body-area evidence.
  let name = playerColorLogName(colorIndex)
  case bot.fallbackVariantIndex(6, colorIndex * 17 + 89)
  of 0:
    name & ", give your route before the report."
  of 1:
    name & ", who saw you before the body was called?"
  of 2:
    name & ", list rooms and tasks this round."
  of 3:
    name & ", explain your path before meeting."
  of 4:
    "Need " & name & "'s route before we vote."
  else:
    name & ", what rooms did you cross this round?"

proc fallbackButtonQuestion(bot: Bot): string =
  ## Returns a varied fallback question for a button meeting.
  let caller = playerColorLogName(bot.meetingCallCallerColor)
  if caller != "Unknown":
    case bot.fallbackVariantIndex(7, 67)
    of 0:
      return caller & " called button. Why? Everyone say rooms and tasks."
    of 1:
      return caller & ", why button? Give reason, then everyone give route."
    of 2:
      return "Button from " & caller & ". What happened? Rooms and tasks."
    of 3:
      return caller & " hit button. Say why. Others give where you were."
    of 4:
      return "Why did " & caller & " call this? I need routes and tasks."
    of 5:
      return caller & ", explain the button. Everyone else give sightings."
    else:
      return "Button meeting. " & caller &
        ", start with why. Then routes."
  case bot.fallbackVariantIndex(5, 71)
  of 0:
    "Button called. Everyone say rooms and tasks."
  of 1:
    "No body. Why button? Give routes and tasks."
  of 2:
    "Button meeting. Caller explain, everyone give route."
  of 3:
    "What triggered button? Say rooms, tasks, and sightings."
  else:
    "No body here. Who called, and what did you see?"

proc bestFallbackSuspect(
  bot: Bot
): tuple[found: bool, colorIndex: int, score: int] =
  ## Returns the strongest legal fallback suspect.
  let scores = bot.effectiveSusScores()
  var best = low(int)
  for slot in 0 ..< bot.votePlayerCount:
    if not bot.voteTargetSafeForRole(slot):
      continue
    let colorIndex = bot.voteSlots[slot].colorIndex
    if colorIndex < 0 or colorIndex >= scores.len:
      continue
    if scores[colorIndex] > best:
      best = scores[colorIndex]
      result = (true, colorIndex, scores[colorIndex])
  if not result.found:
    return
  let minScore =
    if bot.urgentCrewVote():
      1
    elif bot.criticalCrewVote():
      20
    elif bot.meetingCallKind == VoteCalledBody:
      45
    else:
      65
  if result.score < minScore:
    result = (false, VoteUnknown, 0)

proc fallbackVotingChatCandidates(bot: Bot): seq[string] =
  ## Builds deterministic meeting chat candidates when Bedrock is unavailable.
  let
    target = bot.bestFallbackSuspect()
    selfInfo = bot.selfFallbackChat()
    imposterDefense = bot.imposterDefenseChat()
  if imposterDefense.len > 0:
    result.add imposterDefense
  if bot.externalChatAsksSelf() and selfInfo.len > 0 and
      not bot.urgentCrewVote():
    result.add selfInfo
  if target.found:
    let
      reason = bot.fallbackTargetReason(target.colorIndex)
    if bot.urgentCrewVote():
      result.add bot.fallbackUrgentVoteLine(target.colorIndex, reason)
    elif reason == "near a body":
      result.add bot.fallbackRouteProbeLine(target.colorIndex)
    else:
      result.add bot.fallbackSuspectLine(target.colorIndex, reason)
  if bot.externalChatAsksSelf() and selfInfo.len > 0 and
      bot.urgentCrewVote():
    result.add selfInfo
  case bot.meetingCallKind
  of VoteCalledBody:
    result.add bot.fallbackBodyQuestion()
  of VoteCalledButton:
    result.add bot.fallbackButtonQuestion()
  of VoteCalledUnknown:
    case bot.fallbackVariantIndex(5, 79)
    of 0:
      result.add "What happened? Everyone say rooms and tasks."
    of 1:
      result.add "I need context. Give routes, tasks, and sightings."
    of 2:
      result.add "Unknown meeting. Who called, and what did you see?"
    of 3:
      result.add "Everyone give route and task info. What changed?"
    else:
      result.add "Say where you were and who you saw this round."
  if selfInfo.len > 0:
    result.add selfInfo
  result.add bot.safeVotingChatMessage()

proc queueFallbackVotingChat(
  bot: var Bot,
  reason: string,
  countCall = true
): bool =
  ## Queues deterministic chat in place of one unavailable LLM call.
  if countCall and bot.voteLlmCallCount >= VoteLlmMaxCallsPerMeeting:
    return false
  for candidate in bot.fallbackVotingChatCandidates():
    let message = candidate.cleanLlmChatMessage()
    if message.len == 0 or bot.votingChatAlreadySaid(message):
      continue
    if countCall:
      inc bot.voteLlmCallCount
    bot.voteLlmAction = VoteLlmAction(
      kind: VoteLlmFallback,
      targetColor: VoteUnknown,
      message: message,
      reason: reason
    )
    bot.pendingChat = message
    bot.voteLlmWaiting = true
    bot.voteLlmNeedsDecision = false
    bot.voteLlmPromptKey = bot.votingLlmSnapshotKey()
    bot.logEvent(
      "notsus voting fallback chat: " & reason & " | " & message
    )
    return true

proc queueImposterDefenseChat(bot: var Bot): bool =
  ## Queues a fast fake-crew defense when an imposter is being voted.
  if not bot.imposterKnown():
    return false
  if bot.voteImposterDefenseSaid:
    return false
  if bot.voteLlmCallCount >= VoteLlmMaxCallsPerMeeting:
    return false
  let message = bot.imposterDefenseChat().cleanLlmChatMessage()
  if message.len == 0 or bot.votingChatAlreadySaid(message):
    return false
  inc bot.voteLlmCallCount
  bot.voteLlmAction = VoteLlmAction(
    kind: VoteLlmFallback,
    targetColor: VoteUnknown,
    message: message,
    reason: "fast imposter defense"
  )
  bot.pendingChat = message
  bot.voteLlmWaiting = true
  bot.voteLlmNeedsDecision = false
  bot.voteLlmPromptKey = bot.votingLlmSnapshotKey()
  bot.voteImposterDefenseSaid = true
  bot.logEvent("notsus voting fallback chat: fast imposter defense | " &
    message)
  true

proc votingBedrockQuotaError(message: string): bool =
  ## Returns true when Bedrock is quota-limited for the current run.
  let text = message.toLowerAscii()
  "http 429" in text or "too many tokens" in text or
    "quota" in text or "rate" in text

proc votingBedrockBackoffActive(bot: Bot): bool =
  ## Returns true while Bedrock calls are paused after quota errors.
  bot.voteLlmBackoffUntilTick >= 0 and
    bot.frameTick < bot.voteLlmBackoffUntilTick

proc noteVotingBedrockError(bot: var Bot, message: string) =
  ## Records Bedrock errors and opens a long fallback window for quota errors.
  if message.votingBedrockQuotaError():
    bot.voteLlmBackoffUntilTick = max(
      bot.voteLlmBackoffUntilTick,
      bot.frameTick + VoteLlmQuotaBackoffTicks
    )
    bot.voteLlmBackoffReason = message
    bot.logEvent("notsus voting llm quota backoff enabled")

proc skipDeadVotingLlm(bot: var Bot): bool =
  ## Clears voting LLM state when the local player is dead.
  if not bot.selfVotingDead():
    return false
  if (bot.voteLlmNeedsDecision or
      bot.pendingChat.len > 0 or
      bot.voteLlmAction.kind != VoteLlmNone) and
      bot.voteDeadLoggedStartTick != bot.voteStartTick:
    bot.logEvent("notsus voting llm skipped: self is dead")
    bot.voteDeadLoggedStartTick = bot.voteStartTick
  bot.clearVotingLlmDecision(false)
  bot.voteLlmPromptKey = bot.votingLlmSnapshotKey()
  true

proc votingLlmCutoff(listenedTicks: int): bool =
  ## Returns true when the timer is too low for more LLM chat.
  listenedTicks >= VoteDeadlineTicks - VoteLlmDeadlineFallbackTicks

proc votingLlmGapReady(bot: Bot): bool =
  ## Returns true when enough time passed since the previous LLM call.
  bot.voteLlmLastCallTick < 0 or
    bot.frameTick - bot.voteLlmLastCallTick >= VoteLlmChatGapTicks

proc clearVotingLlmForSnapshot(bot: var Bot) =
  ## Marks the current voting snapshot as handled without a model call.
  bot.clearVotingLlmDecision(false)
  bot.voteLlmPromptKey = bot.votingLlmSnapshotKey()

proc backoffVotingLlmError(bot: var Bot) =
  ## Backs off after Bedrock errors so copies do not flood the sidecar.
  bot.clearVotingLlmForSnapshot()
  bot.voteLlmLastCallTick = bot.frameTick + VoteLlmErrorBackoffTicks

proc votingLlmRequestTag(bot: Bot): string =
  ## Returns a unique tag for one voting LLM request.
  "vote-" & $bot.frameTick & "-" & $bot.voteLlmSayCount

proc startVotingLlm(bot: var Bot): bool =
  ## Starts a nonblocking Bedrock social chat update.
  if bot.skipDeadVotingLlm():
    return false
  let snapshotKey = bot.votingLlmSnapshotKey()
  bot.logVotingLlmPrompt()
  if not bot.configureVotingBedrock():
    bot.logEvent("notsus voting llm skipped: Bedrock is not enabled")
    bot.logEvent(
      "upload with --use-bedrock or run locally with AWS credentials"
    )
    return bot.queueFallbackVotingChat("bedrock disabled")
  bot.voteLlmLastCallTick = bot.frameTick
  inc bot.voteLlmCallCount
  var messages = @[
    bedrockAi.ConversationMessage(role: "system", content: VotingPrompt),
    bedrockAi.ConversationMessage(
      role: "user",
      content: bot.votingPromptText()
    )
  ]
  bot.logSendingToLlm(messages)
  let tag = bot.votingLlmRequestTag()
  bedrockAi.startTalkToAI(messages, tag)
  if bedrockAi.lastErrorText().len > 0:
    let error = bedrockAi.lastErrorText()
    bot.logEvent("notsus bedrock error: " & error)
    bot.noteVotingBedrockError(error)
    if bot.queueFallbackVotingChat("bedrock start error", false):
      return true
    bot.backoffVotingLlmError()
    return false
  bot.voteLlmRequestActive = true
  bot.voteLlmRequestTag = tag
  bot.voteLlmRequestSnapshotKey = snapshotKey
  bot.voteLlmRequestStartedTick = bot.frameTick
  bot.voteLlmNeedsDecision = false
  bot.voteLlmWaiting = true
  bot.voteLlmPromptChatText = bot.voteChatText
  bot.voteLlmPromptSusText = bot.votingEvidenceText()
  bot.voteLlmPromptKey = snapshotKey
  bot.logEvent("notsus voting llm started: tag=" & tag)
  true

proc finishVotingLlm(
  bot: var Bot,
  response: bedrockAi.BedrockAsyncResult
): bool =
  ## Applies a completed Bedrock social chat response.
  if not response.ready:
    return false
  if not bot.voteLlmRequestActive:
    bot.logEvent(
      "notsus voting llm discarded stale response: tag=" & response.tag
    )
    return true
  if response.tag != bot.voteLlmRequestTag:
    bot.logEvent(
      "notsus voting llm ignored response: tag=" & response.tag &
        " active=" & bot.voteLlmRequestTag
    )
    return true
  let snapshotKey = bot.voteLlmRequestSnapshotKey
  bot.voteLlmRequestActive = false
  bot.voteLlmRequestTag = ""
  bot.voteLlmRequestSnapshotKey = ""
  bot.voteLlmRequestStartedTick = -1
  bot.voteLlmWaiting = false
  let
    keepTalking = bot.voteLlmCallCount < VoteLlmMaxCallsPerMeeting
    needsFreshDecision =
      bot.votingLlmSnapshotKey() != snapshotKey or keepTalking
  if response.usage.len > 0:
    bot.logEvent("notsus bedrock usage: " & response.usage)
  if response.error.len > 0:
    bot.logEvent("notsus bedrock error: " & response.error)
    bot.noteVotingBedrockError(response.error)
    if not bot.queueFallbackVotingChat("bedrock response error", false):
      bot.backoffVotingLlmError()
    return true
  let reply = response.reply
  bot.logBlock("notsus voting llm raw", reply)
  let parsed = parseSocialLlmResult(reply)
  if not parsed.ok:
    bot.voteLlmNeedsDecision = needsFreshDecision
    bot.voteLlmPromptChatText = bot.voteChatText
    bot.voteLlmPromptSusText = bot.votingEvidenceText()
    bot.voteLlmPromptKey = snapshotKey
    bot.logEvent("notsus voting llm no social output")
    return false
  var applied = 0
  for claim in parsed.social.claims:
    if bot.rememberSocialClaim(claim):
      inc applied
  bot.logEvent(
    "notsus voting llm social parsed: claims=" &
      $parsed.social.claims.len & " new=" & $applied
  )
  bot.voteLlmNeedsDecision = needsFreshDecision
  bot.voteLlmPromptChatText = bot.voteChatText
  bot.voteLlmPromptSusText = bot.votingEvidenceText()
  bot.voteLlmPromptKey = snapshotKey
  let message = parsed.social.message.cleanLlmChatMessage()
  if message.len > 0:
    if message.unhelpfulSkipChatMessage():
      bot.logEvent(
        "notsus voting llm sanitized skip chat: " & message
      )
      if bot.queueFallbackVotingChat("skip chat sanitized", false):
        bot.voteLlmWaiting = true
      else:
        bot.voteLlmAction = VoteLlmAction(
          kind: VoteLlmFallback,
          targetColor: VoteUnknown,
          reason: "skip chat suppressed"
        )
        bot.pendingChat = bot.safeVotingChatMessage()
        bot.voteLlmWaiting = bot.pendingChat.len > 0
      return true
    if bot.votingChatAlreadySaid(message):
      bot.voteLlmAction = VoteLlmAction(
        kind: VoteLlmFallback,
        targetColor: VoteUnknown,
        reason: "duplicate chat suppressed"
      )
      bot.pendingChat = ""
      bot.voteLlmWaiting = false
      bot.logEvent(
        "notsus voting llm duplicate chat suppressed: " & message
      )
      return true
    if bot.mentionsKnownTeammate(message):
      bot.logEvent(
        "notsus voting llm sanitized teammate chat: " & message
      )
      if bot.queueFallbackVotingChat("teammate chat sanitized", false):
        bot.voteLlmWaiting = true
      else:
        bot.voteLlmAction = VoteLlmAction(
          kind: VoteLlmFallback,
          targetColor: VoteUnknown,
          reason: "teammate chat suppressed"
        )
        bot.pendingChat = ""
        bot.voteLlmWaiting = false
      return true
    bot.voteLlmAction = VoteLlmAction(
      kind: VoteLlmSay,
      targetColor: VoteUnknown,
      message: message,
      reason: "social broadcast"
    )
    if bot.votingChatLeaksSecrets(bot.voteLlmAction.message):
      bot.logEvent(
        "notsus voting llm sanitized secret chat: " &
          bot.voteLlmAction.message
      )
      bot.voteLlmAction.message = bot.safeVotingChatMessage()
      bot.voteLlmAction.reason = "secret chat sanitized"
    bot.pendingChat = bot.voteLlmAction.message
    bot.voteLlmWaiting = true
    bot.logEvent("notsus voting llm say: " & bot.voteLlmAction.message)
  else:
    bot.voteLlmAction = VoteLlmAction(
      kind: VoteLlmFallback,
      targetColor: VoteUnknown,
      reason: "claims only"
    )
    bot.pendingChat = ""
    bot.voteLlmWaiting = false
    bot.logEvent("notsus voting llm social: claims only")
  true

proc pollVotingLlm(bot: var Bot) =
  ## Polls Curly for one completed voting LLM request.
  let response = bedrockAi.pollTalkToAI()
  if response.ready:
    discard bot.finishVotingLlm(response)

proc refreshVotingLlmDecision(bot: var Bot, listenedTicks: int) =
  ## Refreshes social chat when the current snapshot needs one.
  bot.pollVotingLlm()
  if bot.voteLlmRequestActive:
    return
  if not bot.voteLlmNeedsDecision:
    return
  if bot.skipDeadVotingLlm():
    return
  if bot.pendingChat.len > 0:
    return
  if bot.queueImposterDefenseChat():
    return
  if bot.voteLlmCallCount >= VoteLlmMaxCallsPerMeeting:
    if bot.voteLlmNeedsDecision:
      bot.logEvent("notsus voting llm stopped: meeting call budget spent")
    bot.clearVotingLlmForSnapshot()
    return
  if votingLlmCutoff(listenedTicks):
    bot.clearVotingLlmDecision(false)
    bot.logEvent("notsus voting llm stopped: vote deadline is close")
    return
  if not bot.votingLlmGapReady():
    return
  if bot.votingBedrockBackoffActive():
    if not bot.queueFallbackVotingChat("bedrock backoff"):
      bot.clearVotingLlmForSnapshot()
    return
  bot.logEvent(
    "notsus voting llm poll: tick=" & $bot.logTick() &
      " chat=" & $bot.voteLlmSayCount &
      " pending=" & $bot.pendingChat.len &
      " action=" & $bot.voteLlmAction.kind &
      " cutoff=" & $votingLlmCutoff(listenedTicks)
  )
  try:
    if not bot.startVotingLlm():
      bot.clearVotingLlmForSnapshot()
  except CatchableError as e:
    bot.logEvent("notsus voting llm fallback: " & e.msg)
    bot.backoffVotingLlmError()

proc selfVoteChoice(bot: Bot): int =
  ## Returns the parsed vote choice for the local player.
  if bot.selfColorIndex >= 0 and bot.selfColorIndex < bot.voteChoices.len:
    return bot.voteChoices[bot.selfColorIndex]
  if bot.voteSelfSlot >= 0 and bot.voteSelfSlot < bot.votePlayerCount:
    let colorIndex = bot.voteSlots[bot.voteSelfSlot].colorIndex
    if colorIndex >= 0 and colorIndex < bot.voteChoices.len:
      return bot.voteChoices[colorIndex]
  VoteUnknown

proc voteConfirmMask(bot: Bot): uint8 =
  ## Returns a one-frame voting confirmation button press.
  if bot.lastMask == ButtonA:
    0
  else:
    ButtonA

proc voteCursorFresh(bot: Bot): bool =
  ## Returns true when the vote cursor was parsed recently.
  bot.voteCursorSeenTick >= 0 and
    bot.frameTick - bot.voteCursorSeenTick <= VoteRetryTicks

proc actionTapMask(bot: Bot): uint8 =
  ## Returns a repeated edge-triggered action button tap.
  if (bot.lastMask and ButtonA) != 0:
    0
  else:
    ButtonA

proc retryVotedMask(bot: var Bot, ownVote: int): uint8 =
  ## Retries a parsed vote when the voting screen keeps running.
  if ownVote == VoteUnknown:
    return 0
  if ownVote == bot.voteSelfSlot:
    if bot.voteRetryTarget != ownVote:
      bot.logEvent("notsus voting retry skipped: parsed self vote")
    bot.voteRetryTarget = ownVote
    bot.lastVoteRetryTick = bot.frameTick
    return 0
  if bot.voteCursor != ownVote:
    bot.voteRetryTarget = ownVote
    bot.lastVoteRetryTick = bot.frameTick
    return 0
  if bot.voteRetryTarget != ownVote:
    bot.voteRetryTarget = ownVote
    bot.lastVoteRetryTick = bot.frameTick
    return 0
  if bot.frameTick - bot.lastVoteRetryTick < VoteRetryTicks:
    return 0
  result = bot.voteConfirmMask()
  if result == ButtonA:
    bot.lastVoteRetryTick = bot.frameTick

proc ownVoteSafe(bot: Bot, ownVote: int): bool =
  ## Returns true when the parsed local vote should be kept.
  if ownVote == VoteSkip:
    return true
  if ownVote < 0 or ownVote >= bot.votePlayerCount:
    return false
  if ownVote == bot.voteSelfSlot:
    return false
  bot.voteTargetSafeForRole(ownVote)

proc visibleVoteCountFor(bot: Bot, target: int): int =
  ## Returns how many living visible voters are on one target.
  if target < 0 or target >= bot.votePlayerCount:
    return
  for voterColor, choice in bot.voteChoices:
    if voterColor == bot.selfColorIndex or choice != target:
      continue
    let voterSlot = bot.voteSlotForColor(voterColor)
    if voterSlot >= 0 and
        voterSlot < bot.votePlayerCount and
        bot.voteSlots[voterSlot].alive:
      inc result

proc voteScoreForColor(
  scores: openArray[int],
  colorIndex: int
): int =
  ## Returns one score entry, or the lowest score for an invalid color.
  if colorIndex < 0 or colorIndex >= scores.len:
    return low(int)
  scores[colorIndex]

proc shouldSwitchCrewVote(
  bot: Bot,
  ownVote,
  nextVote: int,
  reason: string
): bool =
  ## Returns true when a crew vote should move to stronger consensus.
  if bot.role != RoleCrewmate or ownVote == nextVote:
    return false
  if nextVote < 0 or nextVote >= bot.votePlayerCount:
    return false
  if not bot.voteTargetSafeForRole(nextVote):
    return false
  let
    nextColor = bot.voteSlots[nextVote].colorIndex
    ownColor =
      if ownVote >= 0 and ownVote < bot.votePlayerCount:
        bot.voteSlots[ownVote].colorIndex
      else:
        VoteUnknown
    scores = bot.effectiveSusScores()
    nextScore = scores.voteScoreForColor(nextColor)
    ownScore = scores.voteScoreForColor(ownColor)
    nextDirect = bot.directVoteSusScore(nextColor)
    ownDirect = bot.directVoteSusScore(ownColor)
    nextPile = bot.visibleVoteCountFor(nextVote)
    isPile = reason.startsWith("joining social vote pile")
  if ownVote == VoteSkip:
    if isPile:
      return nextPile >= SocialCrewBrigadeVotes and
        bot.votePileTargetPlausible(nextVote)
    return nextDirect >= SusVoteMinScore
  if nextDirect >= EmergencyButtonStrongSusScore and
      nextDirect >= ownDirect:
    return true
  if isPile and not bot.votePileTargetPlausible(nextVote):
    return false
  if isPile and
      nextPile >= SocialCrewBrigadeVotes and
      nextScore + 5 >= ownScore:
    return true
  if nextPile >= 3 and nextScore + 20 >= ownScore:
    return true
  if bot.criticalCrewVote() and nextScore > ownScore:
    return true

proc nextVoteSelectable(bot: Bot, cursor, direction: int): int =
  ## Returns the next selectable voting cursor slot.
  let total = bot.votePlayerCount + 1
  if total <= 0:
    return VoteUnknown
  var cur = cursor
  for step in 1 .. total:
    cur = (cur + direction + total) mod total
    if cur == bot.votePlayerCount:
      return cur
    if cur >= 0 and cur < bot.votePlayerCount and bot.voteSlots[cur].alive:
      return cur
  VoteUnknown

proc voteStepsFrom(
  bot: Bot,
  cursor,
  target,
  direction: int
): int =
  ## Counts cursor steps in one direction to a target.
  if cursor == VoteUnknown:
    return high(int)
  var cur = cursor
  for step in 0 .. bot.votePlayerCount + 1:
    if cur == target:
      return step
    cur = bot.nextVoteSelectable(cur, direction)
    if cur == VoteUnknown:
      return high(int)
  high(int)

proc voteMoveDirectionFrom(bot: Bot, cursor, target: int): int =
  ## Chooses the shortest voting cursor direction toward a target.
  let
    leftSteps = bot.voteStepsFrom(cursor, target, -1)
    rightSteps = bot.voteStepsFrom(cursor, target, 1)
  if leftSteps < rightSteps:
    -1
  else:
    1

proc voteMovePending(bot: Bot): bool =
  ## Returns true while waiting for a vote cursor movement to appear.
  bot.voteMoveExpectedCursor != VoteUnknown and bot.voteMoveSentTick >= 0

proc recordVoteMove(bot: var Bot, direction: int) =
  ## Records one sent vote cursor movement tap.
  bot.voteMoveExpectedCursor = bot.nextVoteSelectable(
    bot.voteNavCursor,
    direction
  )
  bot.voteMoveSentTick = bot.frameTick
  bot.lastVoteMoveTick = bot.frameTick

proc voteNavCursorReady(bot: Bot, target: int): bool =
  ## Returns true when the simulated vote cursor is settled on target.
  if bot.voteNavCursor != target:
    return false
  if bot.voteCursor != target:
    return false
  if bot.voteMovePending():
    return false
  let tick =
    if bot.lastVoteMoveTick >= 0:
      bot.lastVoteMoveTick
    else:
      bot.voteStartTick
  tick >= 0 and bot.frameTick - tick >= VoteCursorConfirmTicks

proc seenVotingTargetFrom(bot: Bot, ticks: openArray[int]): int =
  ## Returns the latest seen living non-self voting target.
  result = VoteUnknown
  var bestTick = 0
  for i, tick in ticks:
    if i >= PlayerColorNames.len:
      continue
    if bot.knownImposterColor(i):
      continue
    let slot = bot.voteSlotForColor(i)
    if not bot.voteTargetSafeForRole(slot):
      continue
    if tick > bestTick:
      bestTick = tick
      result = slot

proc firstSafeVotingTarget(bot: Bot): int =
  ## Returns the first legal player target for this role.
  for target in 0 ..< bot.votePlayerCount:
    if bot.voteTargetSafeForRole(target):
      return target
  VoteUnknown

proc fallbackVotingTarget(
  bot: Bot
): tuple[found: bool, target: int, reason: string] =
  ## Chooses a legal non-skip fallback target.
  result.target = VoteUnknown
  if not bot.imposterKnown():
    result.target = bot.seenVotingTargetFrom(bot.bodySeenTicks)
    if result.target != VoteUnknown:
      result.found = true
      result.reason = "recent body-context sighting against " &
        bot.voteTargetName(result.target)
      return
    if not bot.urgentCrewVote() and not bot.criticalCrewVote():
      return
  result.target = bot.seenVotingTargetFrom(bot.lastSeenTicks)
  if result.target != VoteUnknown:
    result.found = true
    result.reason = "recent sighting against " &
      bot.voteTargetName(result.target)
    return
  result.target = bot.firstSafeVotingTarget()
  if result.target != VoteUnknown:
    result.found = true
    result.reason = "fallback against " & bot.voteTargetName(result.target)

proc weakButtonMeeting(bot: Bot): bool =
  ## Returns true when a crew button meeting has no strong legal evidence.
  if bot.role != RoleCrewmate or bot.meetingCallKind != VoteCalledButton:
    return false
  if bot.knownDeadVoteCount() > 0:
    return false
  let
    state = bot.socialVoteState()
    threshold = socialVoteThreshold(state.socialAliveCount())
  if threshold == low(int):
    return false
  for slot in 0 ..< bot.votePlayerCount:
    if not bot.voteTargetSafeForRole(slot):
      continue
    let colorIndex = bot.voteSlots[slot].colorIndex
    if colorIndex < 0 or colorIndex >= PlayerColorCount:
      continue
    if bot.directVoteSusScore(colorIndex) >= EmergencyButtonStrongSusScore:
      return false
  true

proc targetInList(target: int, targets: openArray[int]): bool =
  ## Returns true when a target is present in a small target list.
  for item in targets:
    if item == target:
      return true

proc legalDifferentOwnerTargets(bot: Bot): seq[int] =
  ## Returns legal living vote targets from a different owner label.
  for target in 0 ..< bot.votePlayerCount:
    if bot.voteTargetSafeForRole(target) and
        not bot.sameOwnerVoteTarget(target):
      result.add target

proc ownOwnerVoteCountFor(bot: Bot, target: int): int =
  ## Counts living same-owner visible votes on one target.
  if target < 0 or target >= bot.votePlayerCount:
    return
  for voterColor, choice in bot.voteChoices:
    if voterColor == bot.selfColorIndex or choice != target:
      continue
    let voterSlot = bot.voteSlotForColor(voterColor)
    if voterSlot >= 0 and
        voterSlot < bot.votePlayerCount and
        bot.voteSlots[voterSlot].alive and
        bot.sameOwnerColor(voterColor):
      inc result

proc ownerConvergenceVoteTarget(
  bot: Bot,
  listenedTicks: int
): tuple[found: bool, target: int, reason: string, instant: bool] =
  ## Returns a deterministic crew target when owner teams are obvious.
  result.target = VoteUnknown
  if bot.role != RoleCrewmate or bot.imposterKnown():
    return
  if bot.weakButtonMeeting():
    return
  if bot.knownDeadVoteCount() == 0 and
      not bot.urgentCrewVote() and
      not bot.criticalCrewVote():
    return
  let targets = bot.legalDifferentOwnerTargets()
  if targets.len == 0:
    return
  let direct = bot.bestLegalDirectVoteTarget()
  if direct.found and
      direct.score >= EmergencyButtonStrongSusScore and
      targetInList(direct.target, targets):
    return (
      true,
      direct.target,
      "owner convergence, hard evidence against " &
        bot.voteTargetName(direct.target),
      false
    )
  let scores = bot.effectiveSusScores()
  var
    pileTarget = VoteUnknown
    pileCount = 0
    pileScore = low(int)
  for target in targets:
    let
      colorIndex = bot.voteSlots[target].colorIndex
      count = bot.ownOwnerVoteCountFor(target)
      score = scores.voteScoreForColor(colorIndex)
    if count > pileCount or
        (count == pileCount and count > 0 and score > pileScore):
      pileTarget = target
      pileCount = count
      pileScore = score
  if pileTarget != VoteUnknown and pileCount > 0:
    return (
      true,
      pileTarget,
      "joining same-owner vote pile against " &
        bot.voteTargetName(pileTarget),
      false
    )
  if targets.len <= 2 and (
      bot.criticalCrewVote() or listenedTicks >= VoteListenBaseTicks
    ):
    var
      bestTarget = targets[0]
      bestScore = scores.voteScoreForColor(
        bot.voteSlots[bestTarget].colorIndex
      )
    for target in targets:
      let score = scores.voteScoreForColor(bot.voteSlots[target].colorIndex)
      if score > bestScore + OwnerPairScoreGap:
        bestTarget = target
        bestScore = score
    return (
      true,
      bestTarget,
      "same-owner crew converging on " & bot.voteTargetName(bestTarget),
      false
    )

proc socialDecisionSafe(bot: Bot, decision: SocialVoteDecision): bool =
  ## Returns true when a social vote decision is legal for this role.
  if not decision.found:
    return false
  if decision.target == bot.votePlayerCount:
    return true
  bot.voteTargetSafeForRole(decision.target)

proc crewEarlyVoteReady(
  bot: Bot,
  decision: SocialVoteDecision
): bool =
  ## Returns true when crew should commit before deadline in an urgent vote.
  if bot.role != RoleCrewmate or not bot.urgentCrewVote():
    return true
  if bot.criticalCrewVote():
    return true
  if not bot.socialDecisionSafe(decision):
    return false
  if decision.target == bot.votePlayerCount:
    return false
  if bot.visibleVoteCountFor(decision.target) >= SocialCrewBrigadeVotes and
      bot.votePileTargetPlausible(decision.target):
    return true
  if bot.socialSusClaimCountFor(decision.target) >= SocialCrewBrigadeVotes and
      bot.socialClaimTargetPlausible(decision.target):
    return true
  let colorIndex = bot.voteSlots[decision.target].colorIndex
  bot.directVoteSusScore(colorIndex) >= EmergencyButtonStrongSusScore

proc crewTopEffectiveVotePlausible(bot: Bot, target: int): bool =
  ## Returns true when crew should trust a top-effective vote target.
  if bot.role != RoleCrewmate:
    return true
  if target < 0 or target >= bot.votePlayerCount:
    return false
  if bot.criticalCrewVote():
    return true
  let colorIndex = bot.voteSlots[target].colorIndex
  if colorIndex < 0 or colorIndex >= PlayerColorCount:
    return false
  let scores = bot.effectiveSusScores()
  if bot.urgentCrewVote() and scores[colorIndex] >=
      UrgentCrewEffectiveMinScore:
    return true
  let direct = bot.targetDirectVoteSusScore(target)
  if direct >= SusVoteMinScore:
    return true
  if bot.socialSusClaimCountFor(target) >= SocialCrewBrigadeVotes and
      bot.socialClaimTargetPlausible(target):
    return true
  scores[colorIndex] >= SusVoteMinScore

proc imposterSkipDefenseDecision(
  bot: Bot,
  decision: SocialVoteDecision
): bool =
  ## Returns true when a social decision wants defensive imposter skip.
  bot.imposterKnown() and
    decision.found and
    decision.target == bot.votePlayerCount and
    decision.reason.startsWith("defending accused imposter")

proc desiredVotingDecision(
  bot: Bot,
  listenedTicks: int
): tuple[target: int, reason: string, instant: bool] =
  ## Chooses the vote target and explains the current decision.
  let
    deadline = votingLlmCutoff(listenedTicks)
    urgent = bot.urgentCrewVote()
    earlyCritical =
      bot.criticalCrewVote() and
      listenedTicks >= VoteListenBaseTicks
    forced =
      if bot.role == RoleCrewmate:
        earlyCritical or (deadline and urgent)
      else:
        deadline
    weakButton = bot.weakButtonMeeting()
    decision = chooseSocialVote(
      bot.socialVoteState(),
      bot.effectiveSusScores(),
      bot.imposterKnown(),
      forced and not weakButton
    )
  if bot.imposterKnown() and
      bot.socialDecisionSafe(decision) and
      decision.target == bot.votePlayerCount:
    if bot.imposterSkipDefenseDecision(decision) and not deadline:
      return (
        VoteUnknown,
        "waiting before defensive skip",
        false
      )
    return (
      decision.target,
      decision.reason,
      true
    )
  if bot.imposterKnown():
    let
      danger = bot.imposterVoteDanger()
      counter = bot.imposterCounterVoteTarget(danger)
    if counter.found:
      return (counter.target, counter.reason, true)
  let ownerDecision = bot.ownerConvergenceVoteTarget(listenedTicks)
  if ownerDecision.found:
    return (
      ownerDecision.target,
      ownerDecision.reason,
      ownerDecision.instant
    )
  let socialTarget = bot.socialSusClaimTarget(SocialCrewBrigadeVotes)
  if bot.role == RoleCrewmate and
      bot.urgentCrewVote() and
      socialTarget.found and
      bot.socialClaimTargetPlausible(socialTarget.target):
    return (
      socialTarget.target,
      "joining plausible social claims against " &
      bot.voteTargetName(socialTarget.target),
        false
      )
  if bot.role == RoleCrewmate and
      urgent and
      listenedTicks >= VoteListenBaseTicks:
    let direct = bot.bestLegalDirectVoteTarget()
    if direct.found and direct.score >= EmergencyButtonStrongSusScore:
      return (
        direct.target,
        "urgent crew vote, direct evidence against " &
          bot.voteTargetName(direct.target),
        false
      )
    let effective = bot.bestLegalEffectiveVoteTarget()
    if effective.found and
        effective.score >= UrgentCrewEffectiveMinScore and
        bot.crewTopEffectiveVotePlausible(effective.target):
      return (
        effective.target,
        "urgent crew vote, top effective sus " &
          bot.voteTargetName(effective.target),
        false
      )
  if bot.socialDecisionSafe(decision):
    if bot.role == RoleCrewmate and
        decision.reason.startsWith("top effective sus") and
        not bot.crewTopEffectiveVotePlausible(decision.target):
      if forced:
        let fallback = bot.fallbackVotingTarget()
        if fallback.found:
          return (
            fallback.target,
            "blocked weak top effective, " & fallback.reason,
            true
          )
        return (
          bot.votePlayerCount,
          "weak top effective evidence, skipping",
          true
        )
      return (VoteUnknown, "waiting instead of weak top effective", false)
    if bot.role == RoleCrewmate and
        decision.reason.startsWith("joining social vote pile") and
        not bot.votePileTargetPlausible(decision.target):
      let direct = bot.bestLegalDirectVoteTarget()
      if forced and direct.found and direct.score >= SusVoteMinScore:
        return (
          direct.target,
          "direct evidence against " & bot.voteTargetName(direct.target),
          true
        )
      if forced:
        let effective = bot.bestLegalEffectiveVoteTarget()
        if effective.found and
            effective.score >= CrewPileMinSusScore and
            bot.crewTopEffectiveVotePlausible(effective.target):
          return (
            effective.target,
            "blocked weak pile, top effective sus " &
              bot.voteTargetName(effective.target),
            true
          )
        let fallback = bot.fallbackVotingTarget()
        if fallback.found:
          return (
            fallback.target,
            "blocked weak pile, " & fallback.reason,
            true
          )
        return (
          bot.votePlayerCount,
          "blocked weak pile, skipping",
          true
        )
      return (VoteUnknown, "waiting instead of weak vote pile", false)
    if not deadline and not bot.crewEarlyVoteReady(decision):
      return (VoteUnknown, "waiting for shared crew read", false)
    return (
      decision.target,
      decision.reason,
      decision.instant
    )
  if weakButton:
    if forced or deadline:
      return (
        bot.votePlayerCount,
        "weak button meeting, skipping",
        true
      )
    return (VoteUnknown, "waiting through weak button meeting", false)
  if not forced:
    if deadline:
      return (
        bot.votePlayerCount,
        "deadline with weak evidence, skipping",
        true
      )
    return (VoteUnknown, "waiting for stronger social sus", false)
  let fallback = bot.fallbackVotingTarget()
  if fallback.found:
    let reason =
      if urgent:
        "urgent crew vote, " & fallback.reason
      else:
        "deadline fallback, " & fallback.reason
    return (
      fallback.target,
      reason,
      true
    )
  if bot.role == RoleCrewmate:
    return (
      bot.votePlayerCount,
      "weak evidence, skipping",
      true
    )
  if deadline:
    return (
      bot.votePlayerCount,
      "deadline with no safe target, skipping",
      true
    )
  (VoteUnknown, "no legal social vote target", false)

proc logVoteDecision(bot: var Bot, target: int, reason: string) =
  ## Logs a voting choice once per visible decision.
  if bot.voteLoggedTarget == target and bot.voteLoggedReason == reason:
    return
  bot.voteLoggedTarget = target
  bot.voteLoggedReason = reason
  bot.logEvent(
    "notsus vote decision: target=" & bot.voteTargetName(target) &
      " reason=" & reason
  )

proc decideVotingMask(bot: var Bot): uint8 {.measure.} =
  ## Chooses voting-screen input from parsed vote state.
  bot.hasGoal = false
  bot.clearPath()
  let ownVote = bot.selfVoteChoice()
  let listenedTicks =
    if bot.voteStartTick >= 0:
      bot.frameTick - bot.voteStartTick
    else:
      0
  if bot.voteDelayTicks < 0:
    bot.voteDelayTicks = bot.randomVoteDelay()
  bot.printVotingEvidence()
  if bot.skipDeadVotingLlm():
    bot.voteTarget = VoteUnknown
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent = "dead in voting, listening"
    bot.thought(bot.intent)
    return 0
  if ownVote != VoteUnknown and not bot.ownVoteSafe(ownVote):
    if bot.lastUnsafeVoteLogTick < 0 or
        bot.frameTick - bot.lastUnsafeVoteLogTick >= VoteRetryTicks:
      bot.lastUnsafeVoteLogTick = bot.frameTick
      bot.logEvent(
        "notsus voting correcting unsafe parsed vote: " &
          bot.voteTargetName(ownVote)
      )
  bot.refreshVotingLlmDecision(listenedTicks)
  var decision = bot.desiredVotingDecision(listenedTicks)
  if bot.pendingChat.len > 0 and not decision.instant:
    bot.voteTarget = VoteUnknown
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent = "sending social vote chat"
    bot.thought(bot.intent)
    return 0
  if ownVote != VoteUnknown and bot.ownVoteSafe(ownVote):
    if bot.shouldSwitchCrewVote(
      ownVote,
      decision.target,
      decision.reason
    ):
      decision.reason = "switching from " &
        bot.voteTargetName(ownVote) & ", " & decision.reason
    else:
      bot.desiredMask = bot.retryVotedMask(ownVote)
      bot.controllerMask = bot.desiredMask
      bot.intent =
        if bot.desiredMask == ButtonA:
          "confirming vote " & bot.voteTargetName(ownVote)
        else:
          "voted " & bot.voteTargetName(ownVote)
      bot.thought(bot.intent)
      return bot.desiredMask
  let decisionTarget = decision.target
  bot.voteTarget = decisionTarget
  if bot.voteTarget == VoteUnknown:
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent = "waiting for legal vote target"
    bot.thought(bot.intent)
    return 0
  bot.logVoteDecision(bot.voteTarget, decision.reason)
  if bot.voteNavCursor == VoteUnknown:
    bot.voteNavCursor = bot.voteCursor
  if bot.voteNavCursor == VoteUnknown:
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent = "waiting for vote cursor"
    bot.thought(bot.intent)
    return 0
  if bot.voteConfirmTarget == bot.voteTarget:
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent = "waiting for vote confirmation " &
      bot.voteTargetName(bot.voteTarget)
    bot.thought(bot.intent)
    return 0
  if bot.voteMovePending():
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent = "waiting for vote cursor ack " &
      bot.voteTargetName(bot.voteMoveExpectedCursor)
    bot.thought(bot.intent)
    return 0
  if bot.voteNavCursor != bot.voteTarget:
    let direction = bot.voteMoveDirectionFrom(
      bot.voteNavCursor,
      bot.voteTarget
    )
    let mask =
      if direction < 0:
        ButtonLeft
      else:
        ButtonRight
    bot.desiredMask =
      if bot.lastMask == mask:
        0
      else:
        mask
    if bot.desiredMask != 0:
      bot.recordVoteMove(direction)
    bot.controllerMask = bot.desiredMask
    bot.intent = "voting cursor to " & bot.voteTargetName(bot.voteTarget) &
      " from " & bot.voteTargetName(bot.voteNavCursor)
    bot.thought(bot.intent)
    return bot.desiredMask
  let waitTicks =
    if bot.imposterKnown() and not decision.instant:
      VoteImposterSkipTicks
    else:
      bot.voteDelayTicks
  if not decision.instant and listenedTicks < waitTicks:
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent = "listening in vote chat " &
      $listenedTicks & "/" & $waitTicks
    bot.thought(bot.intent)
    return 0
  if not bot.voteCursorFresh():
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent = "waiting for fresh vote cursor"
    bot.logEvent(
      "notsus voting wait: stale cursor " &
        bot.voteTargetName(bot.voteCursor) &
        " target=" & bot.voteTargetName(bot.voteTarget)
    )
    bot.thought(bot.intent)
    return 0
  if not bot.voteNavCursorReady(bot.voteTarget):
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent =
      "settling vote cursor on " & bot.voteTargetName(bot.voteTarget)
    bot.thought(bot.intent)
    return 0
  bot.desiredMask = bot.voteConfirmMask()
  bot.controllerMask = bot.desiredMask
  bot.intent = "voting for " & bot.voteTargetName(bot.voteTarget)
  if bot.desiredMask == ButtonA:
    bot.voteConfirmTarget = bot.voteTarget
    bot.voteConfirmSentTick = bot.frameTick
    bot.logEvent(
      "notsus voting confirm: target=" &
        bot.voteTargetName(bot.voteTarget) &
        " cursor=" & bot.voteTargetName(bot.voteCursor) &
        " nav=" & bot.voteTargetName(bot.voteNavCursor)
    )
    bot.logVoteDecision(bot.voteTarget, decision.reason)
  bot.thought(bot.intent)
  bot.desiredMask

proc reportRangeContains(
  bot: Bot,
  playerX,
  playerY,
  bodyX,
  bodyY: int
): bool =
  ## Returns true when one player point can report one body point.
  let
    ax = playerX + CollisionW div 2
    ay = playerY + CollisionH div 2
    bx = bodyX + CollisionW div 2
    by = bodyY + CollisionH div 2
    dx = ax - bx
    dy = ay - by
  dx * dx + dy * dy <=
    bot.sim.config.reportRange * bot.sim.config.reportRange

proc inReportRange(bot: Bot, targetX, targetY: int): bool =
  ## Returns true when the target point is in report range.
  bot.reportRangeContains(
    bot.playerWorldX(),
    bot.playerWorldY(),
    targetX,
    targetY
  )

proc inKillRange(bot: Bot, targetX, targetY: int): bool =
  ## Returns true when the target point is in imposter kill range.
  let
    ax = bot.playerWorldX() + CollisionW div 2
    ay = bot.playerWorldY() + CollisionH div 2
    bx = targetX + CollisionW div 2
    by = targetY + CollisionH div 2
    dx = ax - bx
    dy = ay - by
  dx * dx + dy * dy <= bot.sim.config.killRange * bot.sim.config.killRange

proc inKillTapRange(bot: Bot, targetX, targetY: int): bool =
  ## Returns true when a kill tap should be tried while closing.
  let
    ax = bot.playerWorldX() + CollisionW div 2
    ay = bot.playerWorldY() + CollisionH div 2
    bx = targetX + CollisionW div 2
    by = targetY + CollisionH div 2
    dx = ax - bx
    dy = ay - by
    range = bot.sim.config.killRange + KillTapExtraRadius
  dx * dx + dy * dy <= range * range

proc reportableBodyVisible(bot: Bot): bool =
  ## Returns true when tapping action would report a visible body.
  let body = bot.nearestBody()
  body.found and bot.inReportRange(body.x, body.y)

proc reportGoalForBody(
  bot: var Bot,
  bodyX,
  bodyY: int
): tuple[found: bool, x: int, y: int] {.measure.} =
  ## Returns the nearest reachable point that can report one body.
  if not bot.navigationReady():
    return
  type
    ReportCandidate = tuple[
      x: int,
      y: int,
      directDistance: int,
      bodyDistance: int
    ]
  let
    range = bot.sim.config.reportRange
    minX = max(0, bodyX - range)
    minY = max(0, bodyY - range)
    maxX = min(MapWidth - CollisionW, bodyX + range)
    maxY = min(MapHeight - CollisionH, bodyY + range)
  var candidates: seq[ReportCandidate] = @[]
  for y in minY .. maxY:
    for x in minX .. maxX:
      if not bot.reportRangeContains(x, y, bodyX, bodyY):
        continue
      if not bot.passable(x, y):
        continue
      candidates.add((
        x,
        y,
        heuristic(bot.playerWorldX(), bot.playerWorldY(), x, y),
        heuristic(bodyX, bodyY, x, y)
      ))
  candidates.sort(proc(a, b: ReportCandidate): int =
    result = cmp(a.directDistance, b.directDistance)
    if result == 0:
      result = cmp(a.bodyDistance, b.bodyDistance)
  )
  for candidate in candidates:
    if bot.pathDistance(candidate.x, candidate.y) != high(int):
      return (true, candidate.x, candidate.y)

proc nearestTaskGoal(
  bot: var Bot
): tuple[
  found: bool,
  index: int,
  x: int,
  y: int,
  name: string,
  state: TaskState
] {.measure.} =
  ## Returns the closest known active task station center.
  if bot.imposterKnown() and not bot.isGhost:
    bot.role = RoleImposter
    if not bot.ensureFakeTasks():
      return
    if bot.goalIndex >= 0 and
        bot.goalIndex < bot.sim.tasks.len and
        bot.fakeTaskPending(bot.goalIndex):
      let goal = bot.taskGoalFor(bot.goalIndex, TaskMandatory)
      if goal.found:
        return goal
    var bestDistance = high(int)
    for taskIndex in bot.fakeTaskIds:
      if not bot.fakeTaskPending(taskIndex):
        continue
      let goal = bot.taskGoalFor(taskIndex, TaskMandatory)
      if not goal.found:
        continue
      let distance = bot.goalDistance(goal.x, goal.y)
      if distance < bestDistance:
        bestDistance = distance
        result = goal
    return
  var bestDistance = high(int)
  for i in 0 ..< bot.sim.tasks.len:
    if not bot.taskIconVisibleFor(bot.sim.tasks[i]):
      continue
    let goal = bot.taskGoalFor(i, TaskMandatory)
    if not goal.found:
      continue
    let distance = bot.goalDistance(goal.x, goal.y)
    if distance < bestDistance:
      bestDistance = distance
      result = goal
  if result.found:
    return
  if bot.goalIndex >= 0 and
      bot.goalIndex < bot.sim.tasks.len and
      bot.taskStates.len == bot.sim.tasks.len and
      bot.taskStates[bot.goalIndex] == TaskMandatory:
    let goal = bot.taskGoalFor(bot.goalIndex, TaskMandatory)
    if goal.found:
      return goal
  bestDistance = high(int)
  for i in 0 ..< bot.sim.tasks.len:
    if bot.taskStates.len == bot.sim.tasks.len and
        bot.taskStates[i] != TaskMandatory:
      continue
    let goal = bot.taskGoalFor(i, TaskMandatory)
    if not goal.found:
      continue
    let distance = bot.goalDistance(goal.x, goal.y)
    if distance < bestDistance:
      bestDistance = distance
      result = goal
  if result.found:
    return
  if bot.goalIndex >= 0 and
      bot.goalIndex < bot.sim.tasks.len and
      bot.taskStates.len == bot.sim.tasks.len and
      bot.taskStates[bot.goalIndex] != TaskCompleted and
      bot.checkoutTasks.len == bot.sim.tasks.len and
      bot.checkoutTasks[bot.goalIndex] and
      bot.taskHasFreshClue(bot.goalIndex):
    let goal = bot.taskGoalFor(bot.goalIndex, TaskMaybe)
    if goal.found:
      return goal
  bestDistance = high(int)
  for i in 0 ..< bot.sim.tasks.len:
    if bot.checkoutTasks.len != bot.sim.tasks.len or
        not bot.checkoutTasks[i]:
      continue
    if not bot.taskHasFreshClue(i):
      continue
    if bot.taskStates.len == bot.sim.tasks.len and
        bot.taskStates[i] == TaskCompleted:
      continue
    let goal = bot.taskGoalFor(i, TaskMaybe)
    if not goal.found:
      continue
    let distance = bot.goalDistance(goal.x, goal.y)
    if distance < bestDistance:
      bestDistance = distance
      result = goal
  if result.found:
    return
  if bot.goalIndex >= 0 and
      bot.goalIndex < bot.sim.tasks.len and
      bot.radarTasks.len == bot.sim.tasks.len and
      bot.radarTasks[bot.goalIndex]:
    let goal = bot.taskGoalFor(bot.goalIndex, TaskMaybe)
    if goal.found:
      return goal
  bestDistance = high(int)
  for i in 0 ..< bot.sim.tasks.len:
    if bot.radarTasks.len != bot.sim.tasks.len or not bot.radarTasks[i]:
      continue
    let goal = bot.taskGoalFor(i, TaskMaybe)
    if not goal.found:
      continue
    let distance = bot.goalDistance(goal.x, goal.y)
    if distance < bestDistance:
      bestDistance = distance
      result = goal
  if result.found:
    return

proc preciseMaskForGoal(bot: Bot, goalX, goalY: int): uint8 =
  ## Converts a nearby goal into exact final-approach steering.
  if not bot.navigationReady():
    return 0
  let path: array[0, PathStep] = []
  bot.navigator.moveToAndStop(bot.motionState(), path, 0, goalX, goalY)

proc choosePathStep(
  bot: Bot,
  lookahead = PathLookahead
): PathStep {.measure.} =
  ## Returns a visible steering waypoint from the current path.
  if bot.path.len == 0 or not bot.navigationReady():
    return
  bot.navigator.chooseSteeringPathStep(
    bot.motionState(),
    bot.path,
    bot.pathCursor,
    lookahead
  )

proc taskReady(bot: Bot, task: TaskStation): bool =
  ## Returns true when the player can safely hold action for a task.
  let
    x = bot.playerWorldX() + CollisionW div 2
    y = bot.playerWorldY() + CollisionH div 2
  x >= task.x and x < task.x + task.w and
    y >= task.y and y < task.y + task.h

proc taskReadyAtGoal(bot: Bot, index: int): bool =
  ## Returns true when a task can be held at a selected goal.
  if index < 0 or index >= bot.sim.tasks.len:
    return false
  let
    task = bot.sim.tasks[index]
  bot.taskReady(task)

proc taskGoalReady(
  bot: Bot,
  goal: tuple[
    found: bool,
    index: int,
    x: int,
    y: int,
    name: string,
    state: TaskState
  ]
): bool =
  ## Returns true when the selected goal is ready for task action.
  if not goal.found:
    return false
  bot.taskReadyAtGoal(goal.index)

proc escapeBodyAction(bot: var Bot, bodyX, bodyY: int): uint8

proc holdTaskAction(bot: var Bot, name: string): uint8 =
  ## Holds only the action button while completing a task.
  let fakeTask =
    bot.imposterKnown() and not bot.isGhost
  if fakeTask:
    let body = bot.nearestBody()
    if body.found:
      bot.taskHoldTicks = 0
      bot.taskHoldIndex = -1
      bot.fakeTaskHoldIndex = -1
      bot.logEvent("notsus fake task canceled near body")
      return bot.escapeBodyAction(body.x, body.y)
  let nearVent =
    fakeTask and bot.nearestVentAt(
      bot.playerWorldX(),
      bot.playerWorldY()
    ).found
  let actionMask =
    if nearVent:
      0'u8
    else:
      ButtonA
  bot.intent =
    if nearVent:
      "faking task at " & name & " hold=" & $bot.taskHoldTicks &
        " no action near vent"
    else:
      "doing task at " & name & " hold=" & $bot.taskHoldTicks
  bot.desiredMask = actionMask
  bot.controllerMask = actionMask
  bot.clearPath()
  bot.stuckFrames = 0
  bot.jiggleTicks = 0
  if bot.taskHoldTicks > 0:
    dec bot.taskHoldTicks
  if bot.taskHoldTicks == 0 and
      bot.taskHoldIndex >= 0 and
      bot.taskHoldIndex < bot.sim.tasks.len:
    if fakeTask:
      bot.rememberSelfTask(bot.taskHoldIndex)
      bot.completeFakeTask(bot.taskHoldIndex)
      bot.fakeTaskHoldIndex = -1
      bot.taskHoldIndex = -1
      bot.thought(bot.intent)
      return actionMask
  if bot.taskHoldTicks == 0 and
      bot.taskHoldIndex >= 0 and
      bot.taskHoldIndex < bot.taskStates.len:
    let task = bot.sim.tasks[bot.taskHoldIndex]
    if not bot.taskIconVisibleFor(task) and bot.taskIconClearAreaVisible(task):
      bot.taskStates[bot.taskHoldIndex] = TaskCompleted
      bot.rememberSelfTask(bot.taskHoldIndex)
      if bot.checkoutTasks.len == bot.sim.tasks.len:
        bot.checkoutTasks[bot.taskHoldIndex] = false
    else:
      bot.taskStates[bot.taskHoldIndex] = TaskMandatory
    bot.taskHoldIndex = -1
  bot.thought(bot.intent)
  actionMask

proc reportBodyAction(bot: var Bot, x, y: int): uint8 =
  ## Presses action to report a visible dead body.
  if bot.imposterKnown() and not bot.isGhost:
    bot.taskHoldTicks = 0
    bot.taskHoldIndex = -1
    bot.fakeTaskHoldIndex = -1
    bot.logEvent("notsus imposter refused body report")
    return bot.escapeBodyAction(x, y)
  bot.intent = "reporting dead body"
  let mask = bot.actionTapMask()
  bot.desiredMask = mask
  bot.controllerMask = mask
  bot.clearPath()
  bot.taskHoldTicks = 0
  bot.taskHoldIndex = -1
  bot.queueBodyReport(x, y)
  bot.thought("reporting dead body")
  mask

proc moveAwayFromPoint(
  bot: var Bot,
  x,
  y: int,
  name: string
): uint8 =
  ## Moves directly away from one world point.
  let
    dx = bot.playerWorldX() - x
    dy = bot.playerWorldY() - y
  var mask: uint8 = 0
  if dx < 0:
    mask = mask or ButtonLeft
  elif dx > 0:
    mask = mask or ButtonRight
  if dy < 0:
    mask = mask or ButtonUp
  elif dy > 0:
    mask = mask or ButtonDown
  if mask == 0:
    mask = ButtonLeft
  bot.clearPath()
  bot.taskHoldTicks = 0
  bot.taskHoldIndex = -1
  bot.desiredMask = mask
  bot.controllerMask = mask
  bot.intent = "moving away from " & name
  bot.thought(bot.intent)
  mask

proc bodyEscapeGoal(
  bot: var Bot,
  bodyX,
  bodyY: int
): tuple[found: bool, x: int, y: int, name: string] =
  ## Returns a prowl waypoint that gets this imposter away from a body.
  let
    playerX = bot.playerWorldX()
    playerY = bot.playerWorldY()
    currentBodyDistance = heuristic(playerX, playerY, bodyX, bodyY)
  var bestScore = low(int)
  for i in 0 ..< bot.prowlPointCount():
    let goal = bot.prowlGoalFor(i)
    if not goal.found:
      continue
    let
      bodyDistance = heuristic(goal.x, goal.y, bodyX, bodyY)
      selfDistance = heuristic(goal.x, goal.y, playerX, playerY)
    if bodyDistance < currentBodyDistance + BodyEscapeMinGain:
      continue
    let score = bodyDistance * 4 - selfDistance
    if score > bestScore:
      bestScore = score
      result = (true, goal.x, goal.y, goal.name)
  if result.found:
    return
  for i in 0 ..< bot.prowlPointCount():
    let goal = bot.prowlGoalFor(i)
    if not goal.found:
      continue
    let
      bodyDistance = heuristic(goal.x, goal.y, bodyX, bodyY)
      selfDistance = heuristic(goal.x, goal.y, playerX, playerY)
      score = bodyDistance * 4 - selfDistance
    if score > bestScore:
      bestScore = score
      result = (true, goal.x, goal.y, goal.name)

proc imposterHuntActive(bot: Bot): bool =
  ## Returns true when this imposter is currently hunting.
  bot.imposterKnown() and
    not bot.isGhost and
    (
      bot.imposterKillReady or
      bot.cooldownPastPercent(ImposterHuntCooldownPercent)
    )

proc killChaseMask(bot: Bot, track: PlayerTrack): uint8 =
  ## Returns a small steering mask to keep closing during kill taps.
  let
    dx = track.x - bot.playerWorldX()
    dy = track.y - bot.playerWorldY()
  if abs(dx) >= 2:
    if dx < 0:
      result = result or ButtonLeft
    else:
      result = result or ButtonRight
  if abs(dy) >= 2:
    if dy < 0:
      result = result or ButtonUp
    else:
      result = result or ButtonDown
  if result == 0:
    result = UnstuckMasks[
      (bot.frameTick div UnstuckPulseTicks) mod UnstuckMasks.len
    ]

proc killTapMask(bot: var Bot, moveMask: uint8): uint8 =
  ## Returns movement plus a forced kill button tap.
  if bot.frameTick - bot.lastKillTapTick < KillTapRepeatTicks:
    return moveMask and not ButtonA
  if bot.reportableBodyVisible():
    return moveMask and not ButtonA
  bot.forceActionTap = true
  bot.lastKillTapTick = bot.frameTick
  moveMask or ButtonA

proc killCrewmateAction(
  bot: var Bot,
  track: PlayerTrack,
  name: string
): uint8 =
  ## Taps kill while still closing on a tracked crewmate.
  bot.imposterGoalIndex = -1
  bot.imposterFakeUntilTick = -1
  var moveMask = bot.killChaseMask(track)
  moveMask = bot.applyJiggle(moveMask)
  bot.intent = "kill " & name
  bot.desiredMask = bot.killTapMask(moveMask)
  bot.controllerMask = bot.desiredMask
  bot.clearPath()
  bot.thought(name & " in range, tapping kill while closing")
  bot.controllerMask

proc killReadyCrewmateAction(
  bot: var Bot,
  deadColor = VoteUnknown
): tuple[found: bool, mask: uint8] =
  ## Kills a nearby crewmate before body avoidance can preempt hunting.
  if not bot.imposterHuntActive() or not bot.imposterKillReady:
    return
  let hunted = bot.nearestUnclaimedCrewmate()
  if not hunted.found:
    return
  if hunted.track.colorIndex == deadColor:
    return
  if bot.reportableBodyVisible():
    if not bot.inKillRange(hunted.track.x, hunted.track.y):
      return
  else:
    if not bot.inKillTapRange(hunted.track.x, hunted.track.y):
      return
  (
    true,
    bot.killCrewmateAction(
      hunted.track,
      "hunting " & playerColorName(hunted.track.colorIndex)
    )
  )

proc attackTrackedCrewmate(
  bot: var Bot,
  track: PlayerTrack,
  name: string
): uint8

proc navigateToPoint(
  bot: var Bot,
  x,
  y: int,
  name: string,
  preciseRadius = TaskPreciseApproachRadius,
  pathLookahead = PathLookahead,
  stopAtGoal = false
): uint8 {.measure.} =
  ## Navigates toward one world point and returns the input mask.
  bot.hasGoal = true
  bot.goalX = x
  bot.goalY = y
  bot.goalName = name
  if bot.isGhost:
    bot.clearPath()
    bot.astarMicros = 0
    bot.intent = "ghost direct to " & name
    bot.desiredMask = bot.preciseMaskForGoal(x, y)
  elif heuristic(bot.playerWorldX(), bot.playerWorldY(), x, y) <=
      preciseRadius:
    bot.clearPath()
    bot.astarMicros = 0
    bot.intent = "precise approach to " & name
    bot.desiredMask = bot.preciseMaskForGoal(x, y)
  else:
    discard bot.ensurePathTo(x, y)
    bot.pathStep = bot.choosePathStep(pathLookahead)
    bot.hasPathStep = bot.pathStep.found
    bot.intent = "JPS+ to " & name & " path=" & $bot.path.len
    if bot.navigationReady():
      if stopAtGoal:
        bot.desiredMask = bot.navigator.momentumMoveToAndStop(
          bot.motionState(),
          bot.path,
          bot.pathCursor,
          x,
          y
        )
      else:
        bot.desiredMask = bot.navigator.momentumMoveTo(
          bot.motionState(),
          bot.path,
          bot.pathCursor,
          x,
          y
        )
    else:
      bot.desiredMask = 0
  bot.controllerMask = bot.desiredMask
  let mask = bot.applyJiggle(bot.controllerMask)
  let prefix =
    if bot.imposterKnown():
      "imposter "
    elif bot.isGhost:
      "ghost "
    else:
      "map lock "
  bot.thought(
    prefix & cameraLockName(bot.cameraLock) & " at camera (" &
    $bot.cameraX & ", " & $bot.cameraY & "), next " &
    movementName(mask)
  )
  mask

proc rememberImposterBodyEscape(bot: var Bot, bodyX, bodyY: int) =
  ## Records the current body avoidance window for an imposter.
  if sameBody(bodyX, bodyY, bot.lastImposterBodyX, bot.lastImposterBodyY) and
      bot.frameTick < bot.imposterBodyIgnoreUntilTick:
    return
  bot.lastImposterBodyX = bodyX
  bot.lastImposterBodyY = bodyY
  bot.imposterBodyEscapeUntilTick = bot.frameTick + BodyEscapeCommitTicks
  bot.imposterBodyIgnoreUntilTick = bot.frameTick + BodyEscapeIgnoreTicks

proc imposterIgnoringBody(bot: Bot, bodyX, bodyY: int): bool =
  ## Returns true when this imposter should stop reacting to one body.
  sameBody(bodyX, bodyY, bot.lastImposterBodyX, bot.lastImposterBodyY) and
    bot.frameTick >= bot.imposterBodyEscapeUntilTick and
    bot.frameTick < bot.imposterBodyIgnoreUntilTick

proc escapeBodyAction(
  bot: var Bot,
  bodyX,
  bodyY: int
): uint8 =
  ## Navigates away from a body using a reachable prowl waypoint.
  bot.rememberImposterBodyEscape(bodyX, bodyY)
  let goal = bot.bodyEscapeGoal(bodyX, bodyY)
  if goal.found:
    return bot.navigateToPoint(
      goal.x,
      goal.y,
      "body escape " & goal.name,
      BodyEscapeArrivalRadius,
      PathLookahead,
      true
    )
  bot.moveAwayFromPoint(bodyX, bodyY, "dead body")

proc emergencyButtonAction(bot: var Bot): tuple[found: bool, mask: uint8] =
  ## Calls emergency when a nearby hunter is active late in cooldown.
  let colorIndex = bot.emergencyButtonThreatColor()
  if colorIndex == VoteUnknown:
    return
  let goal = bot.buttonGoal()
  if not goal.found:
    return
  if bot.playerOnButton():
    return (true, bot.callEmergencyButtonAction(colorIndex))
  (
    true,
    bot.navigateToPoint(
      goal.x,
      goal.y,
      "emergency button",
      TaskPreciseApproachRadius,
      PathLookahead,
      true
    )
  )

proc visibleBodyAction(bot: var Bot): tuple[found: bool, mask: uint8] =
  ## Reports or approaches the nearest visible body before other movement.
  if bot.isGhost:
    return
  let body = bot.nearestBody()
  if not body.found:
    return
  bot.updateKillEvidence()
  if not bot.imposterKnown():
    bot.queueBodySeen(body.x, body.y)
  if bot.imposterKnown():
    if bot.reportableBodyVisible():
      return (true, bot.escapeBodyAction(body.x, body.y))
    if bot.imposterIgnoringBody(body.x, body.y):
      return
    let killAction = bot.killReadyCrewmateAction(body.colorIndex)
    if killAction.found:
      return killAction
    if bot.imposterHuntActive():
      let hunted = bot.nearestUnclaimedCrewmate()
      if hunted.found and hunted.track.colorIndex != body.colorIndex:
        return (
          true,
          bot.attackTrackedCrewmate(
            hunted.track,
            "hunting " & playerColorName(hunted.track.colorIndex)
          )
        )
    return (true, bot.escapeBodyAction(body.x, body.y))
  if bot.inReportRange(body.x, body.y):
    return (true, bot.reportBodyAction(body.x, body.y))
  let goal = bot.reportGoalForBody(body.x, body.y)
  if goal.found:
    return (
      true,
      bot.navigateToPoint(
        goal.x,
        goal.y,
        "dead body report range",
        KillApproachRadius
      )
    )
  (
    true,
    bot.navigateToPoint(
      body.x,
      body.y,
      "dead body",
      KillApproachRadius
    )
  )

proc navigateEscapeLoop(bot: var Bot): uint8 =
  ## Runs a remembered hunter around the selected escape loop.
  var goal = bot.currentEscapeLoopGoal()
  if not goal.found:
    bot.intent = "running from hunter, no escape loop"
    bot.thought(bot.intent)
    return 0
  if heuristic(bot.playerWorldX(), bot.playerWorldY(), goal.x, goal.y) <=
      EscapeLoopArrivalRadius:
    bot.escapeLoopPointIndex = bot.nextEscapeLoopPointIndex(
      bot.escapeLoopIndex,
      bot.escapeLoopPointIndex
    )
    goal = bot.currentEscapeLoopGoal()
    if not goal.found:
      bot.intent = "running from hunter, no next loop point"
      bot.thought(bot.intent)
      return 0
  bot.goalIndex = -3
  bot.navigateToPoint(
    goal.x,
    goal.y,
    goal.name,
    EscapeLoopArrivalRadius
  )

proc navigateMoveAway(bot: var Bot): uint8 =
  ## Moves directly away from the close tracked player.
  let track = bot.trackedPlayerWorld(bot.activeHunterColor)
  if not track.found:
    bot.clearRunawayState()
    return 0
  let
    dx = bot.playerWorldX() - track.x
    dy = bot.playerWorldY() - track.y
  var mask: uint8 = 0
  if dx < 0:
    mask = mask or ButtonLeft
  elif dx > 0:
    mask = mask or ButtonRight
  if dy < 0:
    mask = mask or ButtonUp
  elif dy > 0:
    mask = mask or ButtonDown
  if mask == 0:
    mask = ButtonLeft
  bot.clearPath()
  bot.desiredMask = mask
  bot.controllerMask = mask
  bot.intent = "moving away from " & playerColorName(bot.activeHunterColor)
  bot.thought(bot.intent)
  mask

proc navigateGhostWander(bot: var Bot): uint8 =
  ## Moves a task-finished ghost around near the emergency button.
  if not bot.ghostWanderGoalSet or
      bot.frameTick >= bot.ghostWanderNextTick:
    bot.chooseGhostWanderGoal()
  if heuristic(
    bot.playerWorldX(),
    bot.playerWorldY(),
    bot.ghostWanderGoalX,
    bot.ghostWanderGoalY
  ) <= GhostWanderArrivalRadius:
    bot.hasGoal = true
    bot.goalX = bot.ghostWanderGoalX
    bot.goalY = bot.ghostWanderGoalY
    bot.goalIndex = -1
    bot.goalName = "Ghost wander"
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent = "ghost waiting near button"
    bot.thought(bot.intent)
    return 0
  bot.navigateToPoint(
    bot.ghostWanderGoalX,
    bot.ghostWanderGoalY,
    "Ghost wander"
  )

proc navigateSafeHide(bot: var Bot): uint8 =
  ## Moves a button-used finished crewmate to a safe hiding point.
  let goal = bot.currentSafeHideGoal()
  if not goal.found:
    bot.clearPath()
    bot.intent = "finished crewmate, no safe hide goal"
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.thought("finished crewmate, no safe hide goal")
    return 0
  bot.goalIndex = goal.index
  if heuristic(bot.playerWorldX(), bot.playerWorldY(), goal.x, goal.y) <=
      SafeHideArrivalRadius:
    bot.hasGoal = true
    bot.goalX = goal.x
    bot.goalY = goal.y
    bot.goalName = goal.name
    bot.clearPath()
    bot.intent = "hiding at " & goal.name
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.thought("hiding at " & goal.name)
    return 0
  bot.navigateToPoint(goal.x, goal.y, goal.name)

proc pathKillCrewmateAction(
  bot: var Bot,
  chaseX,
  chaseY: int,
  name: string
): uint8 =
  ## Taps kill while pathing toward a visible crewmate.
  let moveMask = bot.navigateToPoint(chaseX, chaseY, name, 0)
  let pathIntent = bot.intent
  bot.imposterGoalIndex = -1
  bot.imposterFakeUntilTick = -1
  bot.intent = "kill-ready " & pathIntent
  bot.desiredMask = bot.killTapMask(moveMask)
  bot.controllerMask = bot.desiredMask
  bot.thought(name & " kill ready, tapping while closing")
  bot.controllerMask

proc attackTrackedCrewmate(
  bot: var Bot,
  track: PlayerTrack,
  name: string
): uint8 =
  ## Chases a tracked crewmate and kills as soon as possible.
  let chase = bot.predictedTrackWorld(track)
  if bot.imposterKillReady:
    let bodyVisible = bot.reportableBodyVisible()
    if bodyVisible:
      if bot.inKillRange(track.x, track.y):
        return bot.killCrewmateAction(track, name)
    else:
      if bot.inKillRange(track.x, track.y) or
          bot.inKillTapRange(track.x, track.y) or
          bot.inKillTapRange(chase.x, chase.y):
        return bot.killCrewmateAction(track, name)
      return bot.pathKillCrewmateAction(chase.x, chase.y, name)
  bot.goalIndex = -2
  bot.navigateToPoint(chase.x, chase.y, name, 0)

proc imposterHuntReady(bot: Bot): bool =
  ## Returns true when an imposter should stop faking and hunt.
  bot.imposterKillReady or
    bot.cooldownPastPercent(ImposterHuntCooldownPercent)

proc navigateProwlPoint(bot: var Bot): uint8

proc navigateProwlPoint(bot: var Bot): uint8 =
  ## Navigates along the ordered prowl path after the hunt timer expires.
  var goal = bot.currentProwlGoal()
  if not goal.found:
    bot.intent = "imposter idle, unreachable prowl point"
    bot.thought("imposter idle, unreachable prowl point")
    return 0
  if heuristic(bot.playerWorldX(), bot.playerWorldY(), goal.x, goal.y) <=
      TaskPreciseApproachRadius:
    bot.imposterProwlIndex = bot.nextProwlPointIndex(
      bot.imposterProwlIndex
    )
    goal = bot.currentProwlGoal()
    if not goal.found:
      bot.intent = "imposter idle, no next prowl point"
      bot.thought("imposter idle, no next prowl point")
      return 0
  bot.goalIndex = goal.index
  bot.navigateToPoint(goal.x, goal.y, goal.name)

proc navigateTaskBrain(bot: var Bot): tuple[found: bool, mask: uint8] =
  ## Runs the shared task movement and completion behavior.
  if bot.taskHoldTicks > 0:
    return (
      true,
      bot.holdTaskAction(
        if bot.goalName.len > 0:
          bot.goalName
        else:
          "task"
      )
    )
  if bot.ghostWanderReady():
    return (true, bot.navigateGhostWander())
  let goal = bot.nearestTaskGoal()
  if not goal.found:
    return
  bot.hasGoal = true
  bot.goalX = goal.x
  bot.goalY = goal.y
  bot.goalIndex = goal.index
  bot.goalName = goal.name
  if goal.index >= 0 and
      bot.taskGoalReady(goal) and
      (
        goal.state == TaskMandatory or
        bot.taskHasFreshClue(goal.index)
      ):
    bot.taskHoldTicks = bot.sim.config.taskCompleteTicks + TaskHoldPadding
    bot.taskHoldIndex = goal.index
    if bot.imposterKnown():
      bot.fakeTaskHoldIndex = goal.index
    return (true, bot.holdTaskAction(goal.name))
  if bot.isGhost:
    return (true, bot.navigateToPoint(goal.x, goal.y, goal.name))
  if goal.state == TaskMandatory and
      heuristic(
        bot.playerWorldX(),
        bot.playerWorldY(),
        goal.x,
        goal.y
      ) <= TaskPreciseApproachRadius:
    bot.clearPath()
    bot.astarMicros = 0
    bot.intent = "precise task approach to " & goal.name &
      " state=" & $goal.state
    bot.desiredMask = bot.preciseMaskForGoal(goal.x, goal.y)
  else:
    discard bot.ensurePathTo(goal.x, goal.y)
    bot.pathStep = bot.choosePathStep()
    bot.hasPathStep = bot.pathStep.found
    bot.intent =
      if goal.index < 0:
        "gather at " & goal.name & " path=" & $bot.path.len
      else:
        "JPS+ to " & goal.name & " path=" & $bot.path.len &
          " state=" & $goal.state
    if bot.navigationReady():
      if goal.index >= 0:
        bot.desiredMask = bot.navigator.momentumMoveToAndStop(
          bot.motionState(),
          bot.path,
          bot.pathCursor,
          goal.x,
          goal.y
        )
      else:
        bot.desiredMask = bot.navigator.momentumMoveTo(
          bot.motionState(),
          bot.path,
          bot.pathCursor,
          goal.x,
          goal.y
        )
    else:
      bot.desiredMask = 0
  bot.controllerMask = bot.desiredMask
  result.mask = bot.applyJiggle(bot.controllerMask)
  bot.thought(
    "map lock " & cameraLockName(bot.cameraLock) & " at camera (" &
    $bot.cameraX & ", " & $bot.cameraY & "), next " &
    movementName(result.mask)
  )
  result.found = true

proc decideImposterMask(bot: var Bot): uint8 {.measure.} =
  ## Chooses imposter movement and kill behavior.
  if not bot.imposterHuntReady():
    bot.imposterWasHunting = false
    let taskAction = bot.navigateTaskBrain()
    if taskAction.found:
      return taskAction.mask
    return bot.navigateProwlPoint()
  if not bot.imposterWasHunting:
    bot.imposterWasHunting = true
    bot.logEvent(
      "notsus imposter hunt: cooldown=" &
        $bot.imposterCooldownPercent & "% killReady=" &
        $bot.imposterKillReady
    )
  if bot.taskHoldTicks > 0:
    bot.logEvent("notsus fake task interrupted by hunt: " & bot.goalName)
  bot.taskHoldTicks = 0
  bot.taskHoldIndex = -1
  bot.fakeTaskHoldIndex = -1
  bot.imposterFakeUntilTick = -1
  let hunted = bot.nearestUnclaimedCrewmate(requireSafeKill = true)
  if hunted.found:
    return bot.attackTrackedCrewmate(
      hunted.track,
      "hunting " & playerColorName(hunted.track.colorIndex)
    )
  bot.navigateProwlPoint()

proc decideNextMask(bot: var Bot): uint8 {.measure.} =
  ## Updates perception and chooses the next input mask.
  bot.forceActionTap = false
  bot.pollVotingLlm()
  let centerStart = getMonoTime()
  bot.updateLocation()
  bot.normalizeRoleFromImposterSignals()
  bot.logRoomTransitions()
  bot.centerMicros = int((getMonoTime() - centerStart).inMicroseconds)
  bot.astarMicros = 0
  if bot.interstitial:
    bot.updateMotionState()
    bot.hasGoal = false
    bot.clearPath()
    if bot.voting:
      return bot.decideVotingMask()
    bot.desiredMask = 0
    bot.controllerMask = 0
    bot.intent =
      if bot.interstitialText.len > 0:
        "interstitial: " & bot.interstitialText
      else:
        "interstitial screen mode"
    bot.thought(bot.intent)
    return 0
  bot.updateMotionState()
  bot.hasGoal = false
  bot.hasPathStep = false
  bot.desiredMask = 0
  bot.controllerMask = 0
  bot.intent = "localizing"
  if not bot.protocolCameraReady:
    bot.rememberVisibleMap()
  let bodyAction = bot.visibleBodyAction()
  if bodyAction.found:
    return bodyAction.mask
  bot.updateTaskGuesses()
  bot.resetTasksFromRadar()
  bot.updateTaskIcons()
  if not bot.localized:
    bot.clearPath()
    bot.thought("waiting for a reliable map lock")
    return 0
  bot.rememberHome()
  bot.updateStalkerTracking()
  bot.updateKillEvidence()
  bot.updateTaskerTracking()
  bot.updateTimeTogetherEvidence()
  bot.updateStandingAroundEvidence()
  bot.updateVentEvidence()
  bot.updateFollowerTracking()
  bot.updateRunawayState()
  if bot.imposterKnown() and not bot.isGhost:
    return bot.decideImposterMask()
  let buttonAction = bot.emergencyButtonAction()
  if buttonAction.found:
    return buttonAction.mask
  if bot.moveAwayActive():
    return bot.navigateMoveAway()
  if bot.runawayActive():
    return bot.navigateEscapeLoop()
  if bot.safeHideReady():
    return bot.navigateSafeHide()
  let taskAction = bot.navigateTaskBrain()
  if taskAction.found:
    return taskAction.mask
  bot.clearPath()
  bot.intent = "localized, no task goal"
  bot.thought("localized near (" & $bot.playerWorldX() & ", " &
    $bot.playerWorldY() & ")")
  0

proc rememberedDownMask(bot: Bot, mask: uint8): uint8 =
  ## Returns the server down mask after any forced action tap.
  if bot.forceActionTap:
    mask and not ButtonA
  else:
    mask

proc stepUnpackedFrame*(
  bot: var Bot,
  frame: openArray[uint8]
): uint8 {.measure.} =
  ## Steps the bot from one unpacked 4-bit framebuffer and returns an input mask.
  if frame.len != ScreenWidth * ScreenHeight:
    return 0
  if bot.unpacked.len != frame.len:
    bot.unpacked.setLen(frame.len)
  for i, value in frame:
    bot.unpacked[i] = value and 0x0f
  inc bot.frameTick
  result = bot.decideNextMask()
  bot.lastMask = bot.rememberedDownMask(result)

proc stepPackedFrame*(
  bot: var Bot,
  frame: openArray[uint8]
): uint8 {.measure.} =
  ## Steps the bot from one packed 4-bit framebuffer and returns an input mask.
  if frame.len != ProtocolBytes:
    return 0
  if bot.packed.len != frame.len:
    bot.packed.setLen(frame.len)
  for i, value in frame:
    bot.packed[i] = value
  unpack4bpp(bot.packed, bot.unpacked)
  inc bot.frameTick
  result = bot.decideNextMask()
  bot.lastMask = bot.rememberedDownMask(result)

proc sheetSprite(sheet: Image, cellX, cellY: int): Sprite =
  ## Extracts one 12x12 sprite from the local sprite sheet.
  spriteFromImage(
    sheet.subImage(cellX * SpriteSize, cellY * SpriteSize, SpriteSize, SpriteSize)
  )

proc initBotSim(config: GameConfig): SimServer =
  ## Builds only the sim data a headless sprite bot needs.
  result.config = config
  result.gameMap = initialProtocolMap()
  result.mapPixels = newSeq[uint8](MapWidth * MapHeight)
  result.walkMask = newSeq[bool](MapWidth * MapHeight)
  result.wallMask = newSeq[bool](MapWidth * MapHeight)
  when not defined(botHeadless):
    loadPalette(clientDataDir() / "pallete.png")
    result.asciiSprites = readTiny5Font()

proc botGameDir(): string =
  ## Returns the game asset directory used by the private bot.
  for candidate in [ExtractedCrewriftGameDir, CrewriftGameDir]:
    if fileExists(candidate / "src" / "crewrift.nim"):
      return candidate
  gameDir()

proc initBot(mapPath = ""): Bot {.measure.} =
  ## Builds a bot and loads the runtime data required by its build mode.
  setCurrentDir(botGameDir())
  result = Bot()
  var config = defaultGameConfig()
  if mapPath.len > 0:
    config.mapPath = mapPath
  result.sim = initBotSim(config)
  when not defined(botHeadless):
    let sheet = loadSpriteSheet()
    result.playerSprite = sheet.sheetSprite(0, 0)
    result.bodySprite = sheet.sheetSprite(1, 0)
    result.killButtonSprite = sheet.sheetSprite(3, 0)
    result.taskSprite = sheet.sheetSprite(4, 0)
    result.ghostSprite = sheet.sheetSprite(6, 0)
    result.ghostIconSprite = sheet.sheetSprite(7, 0)
  result.rng = initRand(getTime().toUnix() xor int64(getCurrentProcessId()))
  result.packed = newSeq[uint8](ProtocolBytes)
  result.unpacked = newSeq[uint8](ScreenWidth * ScreenHeight)
  result.mapTiles = newSeq[TileKnowledge](MapWidth * MapHeight)
  result.radarTasks = newSeq[bool](result.sim.tasks.len)
  result.checkoutTasks = newSeq[bool](result.sim.tasks.len)
  result.taskStates = newSeq[TaskState](result.sim.tasks.len)
  result.taskIconMisses = newSeq[int](result.sim.tasks.len)
  result.resetTaskerTracking()
  result.lastTaskRadarResetTick = -TaskRadarResetTicks
  result.lastDropLogTick = -1_000_000
  result.packetWaitLogMs = -1
  result.lastPacketFrameTick = -1
  result.lastActionFrameTick = -1
  result.lastEventFrameTick = -1
  result.lastHeartbeatFrameTick = -1
  when not defined(botHeadless):
    result.buildPatchEntries()
  result.cameraX = result.sim.buttonCameraX()
  result.cameraY = result.sim.buttonCameraY()
  result.lastCameraX = result.cameraX
  result.lastCameraY = result.cameraY
  result.taskHoldIndex = -1
  result.imposterGoalIndex = -1
  result.imposterFakeUntilTick = -1
  result.imposterWasHunting = false
  result.imposterProwlIndex = -1
  result.imposterCooldownPercent = -1
  result.fakeTaskHoldIndex = -1
  result.activeHunterColor = VoteUnknown
  result.moveAwayUntilTick = -1
  result.runawayUntilTick = -1
  result.runawayBackoffColor = VoteUnknown
  result.runawayBackoffUntilTick = -1
  result.escapeLoopIndex = -1
  result.escapeLoopPointIndex = -1
  result.voteNavCursor = VoteUnknown
  result.voteMoveExpectedCursor = VoteUnknown
  result.voteMoveSentTick = -1
  result.lastVoteMoveTick = -1
  result.voteConfirmTarget = VoteUnknown
  result.voteConfirmSentTick = -1
  result.lastUnsafeVoteLogTick = -1
  result.goalIndex = -1
  result.clearPath()
  result.lastBodySeenX = low(int)
  result.lastBodySeenY = low(int)
  result.lastBodyReportX = low(int)
  result.lastBodyReportY = low(int)
  result.lastKillBodyX = low(int)
  result.lastKillBodyY = low(int)
  result.lastKillTapTick = -KillTapRepeatTicks
  result.bodySusColor = VoteUnknown
  result.hadVisibleBody = false
  result.serverTick = -1
  result.roundStartTick = -1
  result.roundStartServerTick = -1
  result.cameraLock = NoLock
  result.role = RoleCrewmate
  result.selfColorIndex = -1
  for i in 0 ..< result.nearVentGroup.len:
    result.nearVentGroup[i] = -1
    result.nearVentIndex[i] = -1
    result.vanishedVentGroup[i] = -1
    result.vanishedVentIndex[i] = -1
    result.vanishedVentTick[i] = -1
  result.clearMeetingCallState()
  result.clearVotingState()
  result.intent = "waiting for first frame"

when defined(italkalotLibrary):
  const ITalkALotAbiVersion = 1
  const TrainableMasks = [
    0'u8,
    ButtonA,
    ButtonB,
    ButtonUp,
    ButtonUp or ButtonA,
    ButtonUp or ButtonB,
    ButtonDown,
    ButtonDown or ButtonA,
    ButtonDown or ButtonB,
    ButtonLeft,
    ButtonLeft or ButtonA,
    ButtonLeft or ButtonB,
    ButtonRight,
    ButtonRight or ButtonA,
    ButtonRight or ButtonB,
    ButtonUp or ButtonLeft,
    ButtonUp or ButtonLeft or ButtonA,
    ButtonUp or ButtonLeft or ButtonB,
    ButtonUp or ButtonRight,
    ButtonUp or ButtonRight or ButtonA,
    ButtonUp or ButtonRight or ButtonB,
    ButtonDown or ButtonLeft,
    ButtonDown or ButtonLeft or ButtonA,
    ButtonDown or ButtonLeft or ButtonB,
    ButtonDown or ButtonRight,
    ButtonDown or ButtonRight or ButtonA,
    ButtonDown or ButtonRight or ButtonB
  ]

  type ITalkALotPolicy = ref object
    bots: seq[Bot]

  var ITalkALotPolicies: seq[ITalkALotPolicy]

  proc italkalot_abi_version*(): cint {.exportc, dynlib.} =
    ## Returns the shared-library ABI version expected by Python wrappers.
    cint(ITalkALotAbiVersion)

  proc actionIndexForMask(mask: uint8): int32 =
    ## Maps a BitWorld button mask to the CoGames trainable action index.
    for i, value in TrainableMasks:
      if value == mask:
        return i.int32
    0'i32

  proc stepUnpackedFramePtr(
    bot: var Bot,
    frame: ptr UncheckedArray[uint8],
    frameLen: int
  ): uint8 =
    ## Steps the bot from one pointer-backed unpacked framebuffer.
    if frameLen != ScreenWidth * ScreenHeight:
      return 0
    if bot.unpacked.len != frameLen:
      bot.unpacked.setLen(frameLen)
    for i in 0 ..< frameLen:
      bot.unpacked[i] = frame[i] and 0x0f
    inc bot.frameTick
    result = bot.decideNextMask()
    bot.lastMask = result

  proc italkalot_new_policy*(numAgents: cint): cint {.exportc, dynlib.} =
    ## Creates a persistent Nim-backed ITalkALot policy and returns its handle.
    let count = max(1, int(numAgents))
    var policy = ITalkALotPolicy(bots: newSeq[Bot](count))
    for i in 0 ..< count:
      policy.bots[i] = initBot()
    ITalkALotPolicies.add(policy)
    cint(ITalkALotPolicies.len - 1)

  proc italkalot_step_batch*(
    handle: cint,
    agentIds: ptr UncheckedArray[int32],
    numAgentIds: cint,
    numAgents: cint,
    frameStack: cint,
    height: cint,
    width: cint,
    observations: pointer,
    actions: pointer
  ) {.exportc, dynlib.} =
    ## Steps a batch of unpacked pixel observations into CoGames action indices.
    if handle < 0 or int(handle) >= ITalkALotPolicies.len:
      return
    if observations.isNil or actions.isNil or agentIds.isNil:
      return
    if frameStack <= 0 or height != ScreenHeight or width != ScreenWidth:
      return

    let
      policy = ITalkALotPolicies[int(handle)]
      obs = cast[ptr UncheckedArray[uint8]](observations)
      outs = cast[ptr UncheckedArray[int32]](actions)
      frameLen = int(height) * int(width)
      rowStride = int(frameStack) * frameLen
      latestOffset = (int(frameStack) - 1) * frameLen

    if policy.bots.len < int(numAgents):
      let oldLen = policy.bots.len
      policy.bots.setLen(int(numAgents))
      for i in oldLen ..< policy.bots.len:
        policy.bots[i] = initBot()

    for row in 0 ..< int(numAgentIds):
      let agentId = int(agentIds[row])
      if agentId < 0 or agentId >= policy.bots.len:
        outs[row] = 0
        continue
      let frame = cast[ptr UncheckedArray[uint8]](
        cast[uint](obs) + uint(row * rowStride + latestOffset)
      )
      let mask = policy.bots[agentId].stepUnpackedFramePtr(frame, frameLen)
      outs[row] = actionIndexForMask(mask)

when not defined(italkalotLibrary) and not defined(botHeadless):
  proc drawOutline(sk: Silky, pos, size: Vec2, color: ColorRGBX, thickness = 1.0) =
    ## Draws an unfilled rectangle.
    sk.drawRect(pos, vec2(size.x, thickness), color)
    sk.drawRect(vec2(pos.x, pos.y + size.y - thickness), vec2(size.x, thickness), color)
    sk.drawRect(pos, vec2(thickness, size.y), color)
    sk.drawRect(vec2(pos.x + size.x - thickness, pos.y), vec2(thickness, size.y), color)

  proc drawLine(sk: Silky, a, b: Vec2, color: ColorRGBX) =
    ## Draws a simple pixel-like line.
    let
      dx = b.x - a.x
      dy = b.y - a.y
      steps = max(1, int(max(abs(dx), abs(dy)) / 4.0'f))
    for i in 0 .. steps:
      let t = i.float32 / steps.float32
      sk.drawRect(
        vec2(a.x + dx * t - 1.0'f, a.y + dy * t - 1.0'f),
        vec2(3, 3),
        color
      )

  proc taskStateColor(state: TaskState): ColorRGBX =
    ## Returns a map marker color for a task state.
    case state
    of TaskNotDoing:
      ViewerTask
    of TaskMaybe:
      ViewerTaskGuess
    of TaskMandatory:
      ViewerButton
    of TaskCompleted:
      ViewerMutedText

  proc crewmateOutlineColor(bot: Bot, colorIndex: int): ColorRGBX =
    ## Returns the debug outline color for one visible crewmate.
    if bot.knownImposterColor(colorIndex):
      ViewerImp
    else:
      ViewerCrew

  proc drawCrewmateFrameOutlines(
    sk: Silky,
    bot: Bot,
    x,
    y,
    scale: float32
  ) =
    ## Draws visible crewmate team outlines in screen space.
    for crewmate in bot.visibleCrewmates:
      sk.drawOutline(
        vec2(
          x + crewmate.x.float32 * scale,
          y + crewmate.y.float32 * scale
        ),
        vec2(
          bot.playerSprite.width.float32 * scale,
          bot.playerSprite.height.float32 * scale
        ),
        bot.crewmateOutlineColor(crewmate.colorIndex),
        2
      )

  proc drawCrewmateMapOutlines(
    sk: Silky,
    bot: Bot,
    x,
    y,
    scale: float32
  ) =
    ## Draws visible crewmate team outlines in map space.
    if not bot.localized:
      return
    for crewmate in bot.visibleCrewmates:
      let
        world = bot.visibleCrewmateWorld(crewmate)
        spriteX = world.x - SpriteDrawOffX
        spriteY = world.y - SpriteDrawOffY
      sk.drawOutline(
        vec2(x + spriteX.float32 * scale, y + spriteY.float32 * scale),
        vec2(
          bot.playerSprite.width.float32 * scale,
          bot.playerSprite.height.float32 * scale
        ),
        bot.crewmateOutlineColor(crewmate.colorIndex),
        2
      )

  proc drawFrameView(sk: Silky, bot: Bot, x, y: float32) =
    ## Draws the latest 128x128 game frame.
    let pixelScale = ViewerFrameScale
    sk.drawRect(
      vec2(x, y),
      vec2(ScreenWidth.float32 * pixelScale, ScreenHeight.float32 * pixelScale),
      ViewerPanelAlt
    )
    for py in 0 ..< ScreenHeight:
      for px in 0 ..< ScreenWidth:
        let index = bot.unpacked[py * ScreenWidth + px]
        sk.drawRect(
          vec2(x + px.float32 * pixelScale, y + py.float32 * pixelScale),
          vec2(pixelScale, pixelScale),
          sampleColor(index)
        )
    sk.drawCrewmateFrameOutlines(bot, x, y, pixelScale)
    if bot.interstitial:
      return
    let
      button = bot.sim.gameMap.button
      buttonX = button.x - bot.cameraX
      buttonY = button.y - bot.cameraY
    if buttonX + button.w >= 0 and buttonY + button.h >= 0 and
        buttonX < ScreenWidth and buttonY < ScreenHeight:
      sk.drawOutline(
        vec2(x + buttonX.float32 * pixelScale, y + buttonY.float32 * pixelScale),
        vec2(button.w.float32 * pixelScale, button.h.float32 * pixelScale),
        ViewerButton,
        2
      )
    sk.drawRect(
      vec2(
        x + PlayerScreenX.float32 * pixelScale - 3,
        y + PlayerScreenY.float32 * pixelScale - 3
      ),
      vec2(7, 7),
      ViewerPlayer
    )
    let playerPos = vec2(
      x + PlayerScreenX.float32 * pixelScale,
      y + PlayerScreenY.float32 * pixelScale
    )
    for dot in bot.radarDots:
      let dotPos = vec2(
        x + dot.x.float32 * pixelScale + pixelScale * 0.5,
        y + dot.y.float32 * pixelScale + pixelScale * 0.5
      )
      sk.drawLine(playerPos, dotPos, ViewerRadarLine)
      sk.drawRect(dotPos - vec2(4, 4), vec2(9, 9), ViewerTaskGuess)
    for task in bot.sim.tasks:
      let
        taskX = task.x - bot.cameraX
        taskY = task.y - bot.cameraY
        taskVisible = taskX + task.w >= 0 and taskY + task.h >= 0 and
          taskX < ScreenWidth and taskY < ScreenHeight
      if not taskVisible:
        continue
      let
        icon = bot.taskIconInspectRect(task)
        hasIcon = bot.taskIconVisibleFor(task)
        color =
          if hasIcon:
            ViewerPlayer
          else:
            ViewerTask
        taskPos = vec2(
          x + taskX.float32 * pixelScale,
          y + taskY.float32 * pixelScale
        )
        taskSize = vec2(
          task.w.float32 * pixelScale,
          task.h.float32 * pixelScale
        )
      sk.drawOutline(taskPos, taskSize, color, 2)
      if icon.x + icon.w >= 0 and icon.y + icon.h >= 0 and
          icon.x < ScreenWidth and icon.y < ScreenHeight:
        let
          iconPos = vec2(
            x + icon.x.float32 * pixelScale,
            y + icon.y.float32 * pixelScale
          )
          iconSize = vec2(
            icon.w.float32 * pixelScale,
            icon.h.float32 * pixelScale
          )
        sk.drawOutline(iconPos, iconSize, color, 2)
        sk.drawLine(
          taskPos + taskSize * 0.5,
          iconPos + iconSize * 0.5,
          color
        )
    for icon in bot.visibleTaskIcons:
      sk.drawOutline(
        vec2(
          x + icon.x.float32 * pixelScale,
          y + icon.y.float32 * pixelScale
        ),
        vec2(
          bot.taskSprite.width.float32 * pixelScale,
          bot.taskSprite.height.float32 * pixelScale
        ),
        ViewerButton,
        2
      )

  proc drawMapView(sk: Silky, bot: Bot, x, y: float32) =
    ## Draws the map, inferred viewport, and known task stations.
    let scale = ViewerMapScale
    sk.drawRect(
      vec2(x, y),
      vec2(MapWidth.float32 * scale, MapHeight.float32 * scale),
      ViewerUnknown
    )
    for my in countup(0, MapHeight - 1, 2):
      for mx in countup(0, MapWidth - 1, 2):
        let idx = mapIndexSafe(mx, my)
        let color =
          if bot.sim.wallMask[idx]:
            ViewerWall
          elif bot.sim.walkMask[idx]:
            ViewerWalk
          else:
            sampleColor(bot.sim.mapPixels[idx])
        sk.drawRect(
          vec2(x + mx.float32 * scale, y + my.float32 * scale),
          vec2(max(1.0'f, scale * 2), max(1.0'f, scale * 2)),
          color
        )
    if bot.interstitial:
      return
    for i in 0 ..< bot.sim.tasks.len:
      let
        task = bot.sim.tasks[i]
        center = task.taskCenter()
        state =
          if bot.taskStates.len == bot.sim.tasks.len:
            bot.taskStates[i]
          else:
            TaskNotDoing
      sk.drawRect(
        vec2(x + center.x.float32 * scale - 3, y + center.y.float32 * scale - 3),
        vec2(7, 7),
        taskStateColor(state)
      )
    if bot.taskStates.len == bot.sim.tasks.len:
      for i in 0 ..< bot.sim.tasks.len:
        let
          isRadarTask =
            bot.radarTasks.len == bot.sim.tasks.len and bot.radarTasks[i]
          isCheckoutTask =
            bot.checkoutTasks.len == bot.sim.tasks.len and bot.checkoutTasks[i]
        if bot.taskStates[i] != TaskMandatory and
            not isRadarTask and
            not isCheckoutTask:
          continue
        let
          center = bot.sim.tasks[i].taskCenter()
          color =
            if bot.taskStates[i] == TaskMandatory:
              taskStateColor(TaskMandatory)
            else:
              taskStateColor(TaskMaybe)
          pos = vec2(
            x + center.x.float32 * scale - 5,
            y + center.y.float32 * scale - 5
          )
        sk.drawOutline(pos, vec2(11, 11), color, 2)
        if bot.localized:
          sk.drawLine(
            vec2(
              x + bot.playerWorldX().float32 * scale,
              y + bot.playerWorldY().float32 * scale
            ),
            pos + vec2(5, 5),
            ViewerRadarLine
          )
    let button = bot.sim.gameMap.button
    sk.drawOutline(
      vec2(
        x + button.x.float32 * scale,
        y + button.y.float32 * scale
      ),
      vec2(button.w.float32 * scale, button.h.float32 * scale),
      ViewerButton,
      1
    )
    if bot.localized:
      sk.drawOutline(
        vec2(x + bot.cameraX.float32 * scale, y + bot.cameraY.float32 * scale),
        vec2(ScreenWidth.float32 * scale, ScreenHeight.float32 * scale),
        ViewerViewport,
        1
      )
      sk.drawRect(
        vec2(
          x + bot.playerWorldX().float32 * scale - 2,
          y + bot.playerWorldY().float32 * scale - 2
        ),
        vec2(5, 5),
        ViewerPlayer
      )
      sk.drawCrewmateMapOutlines(bot, x, y, scale)
    if bot.homeSet:
      sk.drawOutline(
        vec2(x + bot.homeX.float32 * scale - 5, y + bot.homeY.float32 * scale - 5),
        vec2(10, 10),
        ViewerButton,
        1
      )
    if bot.hasGoal:
      sk.drawRect(
        vec2(x + bot.goalX.float32 * scale - 4, y + bot.goalY.float32 * scale - 4),
        vec2(9, 9),
        ViewerTask
      )
    if bot.path.len > 0:
      var previous = vec2(
        x + bot.playerWorldX().float32 * scale,
        y + bot.playerWorldY().float32 * scale
      )
      let start = min(max(0, bot.pathCursor), bot.path.high)
      for i in countup(start, bot.path.high, 8):
        let current = vec2(
          x + bot.path[i].x.float32 * scale,
          y + bot.path[i].y.float32 * scale
        )
        sk.drawLine(previous, current, ViewerPath)
        previous = current
      if bot.hasGoal:
        sk.drawLine(
          previous,
          vec2(x + bot.goalX.float32 * scale, y + bot.goalY.float32 * scale),
          ViewerPath
        )
    if bot.hasPathStep:
      sk.drawRect(
        vec2(
          x + bot.pathStep.x.float32 * scale - 2,
          y + bot.pathStep.y.float32 * scale - 2
        ),
        vec2(5, 5),
        ViewerButton
      )

  proc refreshDisplayScale(viewer: ViewerApp) =
    ## Updates UI scaling after the window moves between displays.
    let scale = viewer.window.displayScale()
    if abs(scale - viewer.contentScale) <= 0.001'f:
      return
    viewer.contentScale = scale
    viewer.silky.uiScale = scale
    let logicalSize = (viewer.window.size.vec2 / scale).ivec2
    viewer.window.size = logicalSize.scaledWindowSize(scale)

  proc initViewerApp(): ViewerApp =
    ## Opens the diagnostic viewer window.
    result = ViewerApp()
    result.window = newWindow(
      title = "Notsus Bot Viewer",
      size = ivec2(ViewerWindowWidth, ViewerWindowHeight),
      style = Decorated,
      visible = true
    )
    makeContextCurrent(result.window)
    when not defined(useDirectX):
      loadExtensions()
    result.silky = newSilky(result.window, atlasPath())
    result.contentScale = result.window.displayScale()
    result.silky.uiScale = result.contentScale
    result.window.size =
      ivec2(
        ViewerWindowWidth,
        ViewerWindowHeight
      ).scaledWindowSize(result.contentScale)

  proc pumpViewer(
    viewer: ViewerApp,
    bot: Bot,
    connected: bool,
    url: string
  ) =
    ## Pumps and renders one viewer frame.
    if viewer.isNil:
      return
    pollEvents()
    viewer.refreshDisplayScale()
    if viewer.window.buttonPressed[KeyEscape]:
      viewer.window.closeRequested = true
    if viewer.window.closeRequested:
      return
    let
      frameSize = viewer.window.size
      logicalSize = frameSize.vec2 / viewer.silky.uiScale
      framePos = vec2(ViewerMargin, ViewerMargin + 28)
      mapPos = vec2(
        framePos.x + ScreenWidth.float32 * ViewerFrameScale + 24,
        ViewerMargin + 28
      )
      mapSize = vec2(MapWidth.float32 * ViewerMapScale, MapHeight.float32 * ViewerMapScale)
      infoPos = vec2(ViewerMargin, framePos.y + ScreenHeight.float32 * ViewerFrameScale + 28)
      infoSize = vec2(logicalSize.x - ViewerMargin * 2, 300)
      sk = viewer.silky
    sk.beginUI(viewer.window, frameSize)
    sk.clearScreen(ViewerBackground)
    discard sk.drawText("Default", "Notsus Bot Viewer", vec2(ViewerMargin, ViewerMargin), ViewerText)
    sk.drawRect(
      framePos - vec2(8, 8),
      vec2(ScreenWidth.float32 * ViewerFrameScale + 16, ScreenHeight.float32 * ViewerFrameScale + 16),
      ViewerPanel
    )
    sk.drawRect(mapPos - vec2(8, 8), mapSize + vec2(16, 16), ViewerPanel)
    sk.drawRect(infoPos - vec2(8, 8), infoSize + vec2(16, 16), ViewerPanel)
    sk.drawFrameView(bot, framePos.x, framePos.y)
    sk.drawMapView(bot, mapPos.x, mapPos.y)
    let goalText =
      if bot.hasGoal:
        let ready =
          bot.goalIndex >= 0 and
          bot.goalIndex < bot.sim.tasks.len and
          bot.taskReadyAtGoal(bot.goalIndex)
        "goal: " & bot.goalName &
          " dist=" & $heuristic(
            bot.playerWorldX(),
            bot.playerWorldY(),
            bot.goalX,
            bot.goalY
          ) &
          " ready=" & $ready & "\n"
      else:
        "goal: none\n"
    let infoText =
      "intent: " & bot.intent & "\n" &
      "room: " & bot.roomName() & "\n" &
      "timing sprite scans: " & $bot.spriteScanMicros & "us (" &
        $(bot.spriteScanMicros div 1000) & "ms)\n" &
      "timing localize local: " & $bot.localizeLocalMicros & "us (" &
        $(bot.localizeLocalMicros div 1000) & "ms)\n" &
      "timing localize patch: " & $bot.localizePatchMicros & "us (" &
        $(bot.localizePatchMicros div 1000) & "ms)\n" &
      "timing localize spiral: " & $bot.localizeSpiralMicros & "us (" &
        $(bot.localizeSpiralMicros div 1000) & "ms)\n" &
      "timing pathing: " & $bot.astarMicros & "us (" &
        $(bot.astarMicros div 1000) & "ms)\n" &
      "client tick: " & $bot.frameTick & "\n" &
      "BUTTONS HELD: " & inputMaskSummary(bot.lastMask) & "\n" &
      "timing center: " & $bot.centerMicros & "us (" &
        $(bot.centerMicros div 1000) & "ms)\n" &
      "frames buffered: " & $bot.frameBufferLen &
        " dropped=" & $bot.framesDropped &
        " total=" & $bot.skippedFrames & "\n" &
      "packet: " & bot.packetWaitText() & "\n" &
      "interstitial text: " &
        (if bot.interstitialText.len > 0: bot.interstitialText else: "none") &
        "\n" &
      "lock: " & cameraLockName(bot.cameraLock) & " score=" & $bot.cameraScore & "\n" &
      "role: " & roleName(bot.role) &
        " self=" & playerColorName(bot.selfColorIndex) &
        " ghost=" & $bot.isGhost &
        " ghost icon frames=" & $bot.ghostIconFrames &
        " kill ready=" & $bot.imposterKillReady &
        " kill cooldown=" & $bot.imposterCooldownPercent & "%" &
        " imp goal=" & $bot.imposterGoalIndex &
        " prowl=" & $bot.imposterProwlIndex & "\n" &
      "round tick=" & (
        if bot.roundTimingKnown():
          $bot.roundElapsedTicks()
        else:
          "unset"
      ) & " server tick=" & $bot.serverTick &
        " hunt=" & $bot.imposterHuntActive() & "\n" &
      "known imps: " & bot.knownImposterSummary() & "\n" &
      "voting: " & $bot.voting &
        " count=" & $bot.votePlayerCount &
        " listen=" & $max(0, bot.frameTick - bot.voteStartTick) &
        " cursor=" & bot.voteTargetName(bot.voteCursor) &
        " target=" & bot.voteTargetName(bot.voteTarget) & "\n" &
      "votes: " & bot.voteSummary() & "\n" &
      "vote chat: " & bot.voteChatText & "\n" &
      "camera: (" & $bot.cameraX & ", " & $bot.cameraY & ")\n" &
      "player: (" & $bot.playerWorldX() & ", " & $bot.playerWorldY() & ")\n" &
      "home: " & (
        if bot.homeSet:
          "(" & $bot.homeX & ", " & $bot.homeY & ")"
        else:
          "unset"
      ) & " started=" & $bot.gameStarted & "\n" &
      "velocity: (" & $bot.velocityX & ", " & $bot.velocityY & ")\n" &
      "crewmates masked: " & $bot.visibleCrewmates.len &
        " bodies=" & $bot.visibleBodies.len &
        " ghosts=" & $bot.visibleGhosts.len & "\n" &
      "suspect: " & bot.suspectSummary() & "\n" &
      "radar dots: " & $bot.radarDots.len &
        " radar tasks=" & $bot.radarTaskCount() &
        " checkout=" & $bot.checkoutTaskCount() &
        " task icons=" & $bot.visibleTaskIcons.len & "\n" &
      "tasks mandatory=" & $bot.taskStateCount(TaskMandatory) &
        " completed=" & $bot.taskStateCount(TaskCompleted) & "\n" &
      goalText &
      "path pixels: " & $bot.path.len & "\n" &
      "desired: " & inputMaskSummary(bot.desiredMask) & "\n" &
      "controller: " & inputMaskSummary(bot.controllerMask) & "\n" &
      "stuck: " & $bot.stuckFrames & " jiggle=" & $bot.jiggleTicks & "\n" &
      "last thought: " & (
        if bot.lastThought.len > 0:
          bot.lastThought
        else:
          "waiting"
      ) & "\n" &
      "status: " & (if connected: "connected" else: "reconnecting") & "\n" &
      "url: " & url
    discard sk.drawText("Default", infoText, infoPos, ViewerText, infoSize.x, infoSize.y)
    sk.endUi()
    viewer.window.swapBuffers()

  proc viewerOpen(viewer: ViewerApp): bool =
    ## Returns true when the diagnostic viewer should keep running.
    viewer.isNil or not viewer.window.closeRequested

when not defined(italkalotLibrary) and defined(botHeadless):
  proc initViewerApp(): ViewerApp =
    ## Returns no viewer for headless builds.
    nil

  proc pumpViewer(
    viewer: ViewerApp,
    bot: Bot,
    connected: bool,
    url: string
  ) =
    ## Ignores viewer frames in headless builds.
    discard

  proc viewerOpen(viewer: ViewerApp): bool =
    ## Returns true because headless builds have no viewer window.
    true

when not defined(italkalotLibrary):
  proc redactedTokenUrl(url: string): string =
    ## Returns a URL with any websocket player token redacted.
    const Prefix = "token="
    var i = 0
    while i < url.len:
      if i + Prefix.len <= url.len and
          url[i ..< i + Prefix.len] == Prefix:
        result.add Prefix & "<redacted>"
        i += Prefix.len
        while i < url.len and url[i] notin {'&', ' ', '\t', '\r', '\n'}:
          inc i
      else:
        result.add url[i]
        inc i

  proc urlHasQueryParam(url, key: string): bool =
    ## Returns true when a URL already carries one query key.
    url.contains("?" & key & "=") or url.contains("&" & key & "=")

  proc playerTokenPresent(url, token: string): bool =
    ## Returns true when a websocket player token is configured.
    token.strip().len > 0 or url.urlHasQueryParam("token")

  proc printBedrockStartupStatus() =
    ## Prints a redacted Bedrock startup diagnostic.
    echo "notsus bedrock runtime: ", bedrockAi.runtimeText()
    let signal = bedrockAi.credentialSignalText()
    if signal.len == 0:
      echo "NOTSUS BEDROCK CREDENTIALS MISSING: no AWS credential signal; ",
        "voting LLM chat will not connect."
      flushFile(stdout)
      return
    echo "notsus bedrock credential signal: source=", signal
    if bedrockAi.sidecarConfigured():
      try:
        echo "notsus bedrock sidecar health: ",
          bedrockAi.sidecarHealthText()
      except CatchableError as e:
        echo "notsus bedrock sidecar health pending: ", e.msg
    try:
      echo "notsus bedrock credentials resolved: ",
        bedrockAi.resolvedCredentialText()
    except CatchableError as e:
      echo "NOTSUS BEDROCK CREDENTIAL ERROR: ", e.msg
    try:
      echo "notsus bedrock metadata keys: ",
        bedrockAi.requestMetadataKeys()
    except CatchableError as e:
      echo "NOTSUS BEDROCK METADATA ERROR: ", e.msg
    flushFile(stdout)

  proc runBot(
    host = DefaultHost,
    port = PlayerDefaultPort,
    gui = false,
    name = "",
    mapPath = "",
    url = "",
    token = "",
    slot = -1,
    exitOnDisconnect = false
  ) =
    ## Connects to a Crewrift server and processes player frames.
    ## If `url` is non-empty it is used as the WebSocket endpoint (scheme,
    ## host, port, path); otherwise we build ws://host:port/player. A
    ## missing path is filled in with WebSocketPath.
    if not gui:
      startProfileTrace()
    var bot = initBot(mapPath)
    bot.configuredName = name
    let endpoint =
      if url.len > 0: ensureWsPath(url, WebSocketPath)
      else: "ws://" & host & ":" & $port & WebSocketPath
    let connectUrl = playerConnectUrl(endpoint, name, token, slot)
    let displayUrl = connectUrl.redactedTokenUrl()
    let client = initProtocolClient()

    proc closeConnection(ws: var WebSocket) =
      ## Closes a websocket if one is currently open.
      if not ws.isNil:
        ws.close()
        ws = nil

    proc receiveTimeout(): int =
      ## Returns the frame receive timeout for the active run mode.
      if gui:
        10
      else:
        BotReceiveTimeoutMs

    var
      viewer =
        if gui: initViewerApp()
        else: nil
      connected = false
      notifiedFailure = false
      everConnected = false
      disconnectStart = getMonoTime()

    while viewer.viewerOpen():
      var ws: WebSocket
      try:
        if everConnected or notifiedFailure:
          echo "trying to reconnect to ", displayUrl
        ws = newWebSocket(connectUrl)
        echo "connected to ", displayUrl, " protocol=sprite"
        flushFile(stdout)
        notifiedFailure = false
        var
          lastMask = 0xff'u8
        client.reset()
        bot.resetProtocolMap()
        bot.frameBufferLen = 0
        bot.framesDropped = 0
        bot.waitingForPacket = false
        bot.packetWaitMs = 0
        bot.packetWaitTimeouts = 0
        bot.packetWaitLogMs = -1
        bot.lastPacketFrameTick = -1
        bot.lastActionFrameTick = -1
        bot.lastEventFrameTick = -1
        bot.lastHeartbeatFrameTick = -1
        connected = true
        everConnected = true

        while viewer.viewerOpen():
          if gui:
            viewer.pumpViewer(bot, connected, connectUrl)
            if not viewer.viewerOpen():
              ws.close()
              break
          var receivedFrame = false
          if gui:
            receivedFrame = client.receiveLatestFrameInto(
              ws,
              gui,
              bot.packed,
              bot.unpacked,
              receiveTimeout()
            )
          else:
            profileBlock "receive latest frame":
              receivedFrame = client.receiveLatestFrameInto(
                ws,
                gui,
                bot.packed,
                bot.unpacked,
                receiveTimeout()
              )
          if not receivedFrame:
            bot.notePacketTimeout()
            continue
          bot.frameTick += client.frameAdvance
          bot.notePacketReceived()
          bot.frameBufferLen = client.frameBufferLen
          bot.framesDropped = client.framesDropped
          bot.skippedFrames = client.skippedFrames
          if bot.framesDropped > 0 and (
              bot.frameTick - bot.lastDropLogTick >= 120 or
              bot.framesDropped >= 16
          ):
            echo "frames dropped: ", bot.framesDropped,
              " buffered=", client.frameAdvance,
              " total=", bot.skippedFrames,
              " tick=", bot.frameTick
            bot.lastDropLogTick = bot.frameTick
          if gui:
            bot.updateProtocolDetections(client)
          else:
            profileBlock "update protocol detections":
              bot.updateProtocolDetections(client)
          bot.clearPacketWait()
          var nextMask = 0'u8
          if gui:
            nextMask = bot.decideNextMask()
          else:
            profileBlock "decide next mask":
              nextMask = bot.decideNextMask()
          if nextMask != 0:
            bot.noteActionActivity()
          if not gui and profileShouldDump(bot.frameTick):
            finishProfileTrace()
          let sentDownMask = bot.rememberedDownMask(nextMask)
          bot.lastMask = sentDownMask
          if nextMask != lastMask or bot.forceActionTap:
            if gui:
              if bot.forceActionTap:
                ws.send(actionTapBlob(nextMask), BinaryMessage)
              else:
                ws.send(inputBlob(nextMask), BinaryMessage)
            else:
              profileBlock "send input":
                if bot.forceActionTap:
                  ws.send(actionTapBlob(nextMask), BinaryMessage)
                else:
                  ws.send(inputBlob(nextMask), BinaryMessage)
            lastMask = sentDownMask
          if bot.pendingChatReady():
            if gui:
              ws.send(chatBlob(bot.pendingChat), BinaryMessage)
              bot.markPendingChatSent()
            else:
              profileBlock "send chat":
                ws.send(chatBlob(bot.pendingChat), BinaryMessage)
                bot.markPendingChatSent()
            bot.noteActionActivity()
          if bot.readyMessageReady():
            if gui:
              ws.send(readyBlob(), BinaryMessage)
            else:
              profileBlock "send ready":
                ws.send(readyBlob(), BinaryMessage)
          bot.logProgressHeartbeat()
      except CatchableError as e:
        ws.closeConnection()
        if connected:
          echo "connection lost: ", e.msg,
            " frameTick=", bot.frameTick,
            " serverTick=", bot.serverTick,
            " role=", bot.role.roleName(),
            " voting=", bot.voting,
            " interstitial=", bot.interstitial,
            " text=", (
              if bot.interstitialText.len > 0:
                bot.interstitialText
              elif bot.protocolInterstitialText.len > 0:
                bot.protocolInterstitialText
              else:
                "none"
            ),
            " framesDropped=", bot.framesDropped,
            " skipped=", bot.skippedFrames
          flushFile(stdout)
          if exitOnDisconnect:
            break
          disconnectStart = getMonoTime()
        elif not notifiedFailure:
          echo "connection failed: ", e.msg
          flushFile(stdout)
          notifiedFailure = true
        connected = false
        let windowMs = reconnectWindowMs(everConnected)
        if (getMonoTime() - disconnectStart).inMilliseconds >= windowMs:
          echo "can't connect after ", windowMs div 1000, "s; exiting"
          break
        if gui:
          let reconnectStart = getMonoTime()
          while viewer.viewerOpen() and
              (getMonoTime() - reconnectStart).inMilliseconds <
              ReconnectAttemptMs:
            viewer.pumpViewer(bot, connected, connectUrl)
            sleep(10)
        else:
          sleep(ReconnectAttemptMs)

when isMainModule and not defined(italkalotLibrary):
  type
    BotRunConfig = object
      url: string
      address: string
      port: int
      gui: bool
      name: string
      mapPath: string
      token: string
      slot: int
      exitOnDisconnect: bool
      connectionMethod: string

  proc requireOptionValue(key, val: string) =
    ## Raises when one command-line option is missing its value.
    if val.len == 0:
      raise newException(ValueError, "Option --" & key & " requires a value.")

  proc parseBotPort(value: string): int =
    ## Parses a bot server port.
    try:
      result = value.parseInt()
    except ValueError:
      raise newException(ValueError, "--port must be an integer.")
    if result <= 0 or result > 65535:
      raise newException(ValueError, "--port must be between 1 and 65535.")

  proc parseBotSlot(value: string): int =
    ## Parses a requested bot slot index.
    try:
      result = value.parseInt()
    except ValueError:
      raise newException(ValueError, "--slot must be an integer.")
    if result < 0:
      raise newException(ValueError, "--slot must be non-negative.")

  proc readBotRunConfig(): BotRunConfig =
    ## Reads command-line options for one bot process.
    let
      coworldUrl = getEnv("COWORLD_PLAYER_WS_URL")
      cogamesUrl = getEnv("COGAMES_ENGINE_WS_URL")
      defaultUrl =
        if coworldUrl.len > 0:
          coworldUrl
        else:
          cogamesUrl
      defaultMethod =
        if coworldUrl.len > 0:
          "COWORLD_PLAYER_WS_URL"
        elif cogamesUrl.len > 0:
          "COGAMES_ENGINE_WS_URL"
        else:
          "host-port"
    result = BotRunConfig(
      url: defaultUrl,
      address: DefaultHost,
      port: PlayerDefaultPort,
      slot: -1,
      connectionMethod: defaultMethod
    )
    var
      addressSet = false
      portSet = false
      urlSet = false
    for kind, key, val in getopt():
      case kind
      of cmdLongOption:
        case key
        of "address", "host":
          key.requireOptionValue(val)
          result.address = val
          addressSet = true
        of "port":
          key.requireOptionValue(val)
          result.port = parseBotPort(val)
          portSet = true
        of "url":
          key.requireOptionValue(val)
          result.url = val
          urlSet = true
          result.connectionMethod = "--url"
        of "name":
          key.requireOptionValue(val)
          result.name = val
        of "map", "map-path":
          key.requireOptionValue(val)
          result.mapPath = val
        of "token":
          key.requireOptionValue(val)
          result.token = val
        of "slot":
          key.requireOptionValue(val)
          result.slot = parseBotSlot(val)
        of "gui":
          if val.len > 0:
            raise newException(
              ValueError,
              "Option --gui does not take a value."
            )
          result.gui = true
        of "exit-on-disconnect":
          if val.len > 0:
            raise newException(
              ValueError,
              "Option --exit-on-disconnect does not take a value."
            )
          result.exitOnDisconnect = true
        else:
          raise newException(ValueError, "Unknown option: --" & key)
      of cmdShortOption:
        raise newException(ValueError, "Unknown option: -" & key)
      of cmdArgument:
        raise newException(ValueError, "Unexpected argument: " & key)
      of cmdEnd:
        discard
    if not urlSet and (addressSet or portSet):
      result.url = ""
      result.connectionMethod = "host-port"

  proc connectEndpoint(config: BotRunConfig): string =
    ## Returns the websocket endpoint before player query parameters.
    if config.url.len > 0:
      ensureWsPath(config.url, WebSocketPath)
    else:
      "ws://" & config.address & ":" & $config.port & WebSocketPath

  proc connectUrl(config: BotRunConfig): string =
    ## Returns the full websocket player URL.
    playerConnectUrl(
      config.connectEndpoint(),
      config.name,
      config.token,
      config.slot
    )

  proc requirePlayerToken(config: BotRunConfig, url: string) =
    ## Exits loudly when a headless hosted run has no websocket token.
    let hasToken = url.playerTokenPresent(config.token)
    echo "notsus connection method: ", config.connectionMethod,
      " protocol=sprite token=",
      (if hasToken: "present" else: "MISSING"),
      " slot=", (if config.slot >= 0: $config.slot else: "auto")
    if hasToken:
      flushFile(stdout)
      return
    echo "NOTSUS CONNECTION ERROR: missing websocket player token."
    echo "NOTSUS CONNECTION ERROR: refusing to connect without token; ",
      "pass --token or include token= in the websocket URL."
    flushFile(stdout)
    if not config.gui:
      quit(2)

  let config = readBotRunConfig()
  let target = config.connectUrl()
  echo "starting notsus -> ", target.redactedTokenUrl(), " protocol=sprite"
  config.requirePlayerToken(target)
  printBedrockStartupStatus()
  addExitProc(finishProfileTrace)
  runBot(
    config.address,
    config.port,
    config.gui,
    config.name,
    config.mapPath,
    config.url,
    config.token,
    config.slot,
    config.exitOnDisconnect
  )
