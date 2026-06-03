import
  std/[algorithm, os, unittest],
  bitworld/spriteprotocol,
  crewrift/[replays, sim],
  scribe/[events, report, timeline]

const GameDir = currentSourcePath.parentDir.parentDir.parentDir

type
  InputChange = object
    tickBeforeStep: int
    player: int
    mask: uint8

  ChatChange = object
    tickBeforeStep: int
    player: int
    message: string

proc roleSlot(name: string, role: PlayerRole): PlayerSlotConfig =
  PlayerSlotConfig(name: name, role: role, hasRole: true)

proc configuredGame(
  roles: openArray[PlayerRole],
  roleRevealTicks = 0,
  killCooldownTicks = 0
): GameConfig =
  result = defaultGameConfig()
  result.minPlayers = roles.len
  result.startWaitTicks = 0
  result.roleRevealTicks = roleRevealTicks
  result.killCooldownTicks = killCooldownTicks
  result.killRange = 1000
  result.reportRange = 1000
  result.voteTimerTicks = 20
  result.maxTicks = 500
  result.maxGames = 1
  result.tasksPerPlayer = 1
  result.autoImposterCount = false
  result.imposterCount = 0
  result.slots = @[]
  for i, role in roles:
    if role == Imposter:
      inc result.imposterCount
    result.slots.add roleSlot("player" & $(i + 1), role)

proc withGameDir[T](body: proc(): T): T =
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = body()
  finally:
    setCurrentDir(previousDir)

proc synthesizeReplay(
  config: GameConfig,
  maxTick: int,
  changes: openArray[InputChange] = [],
  chats: openArray[ChatChange] = []
): ReplayData =
  let changesCopy = @changes
  let chatsCopy = @chats
  withGameDir(proc(): ReplayData =
    var sim = initSimServer(config)
    result.gameName = "crewrift"
    result.gameVersion = "test"
    result.configJson = config.configJson()

    let playerCount = config.minPlayers
    for playerIndex in 0 ..< playerCount:
      result.joins.add ReplayJoin(
        time: 0'u32,
        player: uint8(playerIndex),
        name: "player" & $(playerIndex + 1),
        slot: playerIndex,
        token: ""
      )
      discard sim.addPlayer(
        "player" & $(playerIndex + 1),
        playerIndex,
        "",
        trusted = true
      )

    var sortedChanges = changesCopy
    sortedChanges.sort(proc(a, b: InputChange): int =
      cmp(a.tickBeforeStep, b.tickBeforeStep)
    )
    for change in sortedChanges:
      result.inputs.add ReplayInput(
        time: tickTime(change.tickBeforeStep),
        player: uint8(change.player),
        keys: change.mask
      )

    var sortedChats = chatsCopy
    sortedChats.sort(proc(a, b: ChatChange): int =
      cmp(a.tickBeforeStep, b.tickBeforeStep)
    )
    for chat in sortedChats:
      result.chats.add ReplayChat(
        time: tickTime(chat.tickBeforeStep),
        player: uint8(chat.player),
        message: chat.message
      )

    var
      changeIndex = 0
      chatIndex = 0
      masks = newSeq[uint8](playerCount)
      lastMasks = newSeq[uint8](playerCount)

    while sim.tickCount < maxTick:
      let time = tickTime(sim.tickCount)
      while changeIndex < sortedChanges.len and
          tickTime(sortedChanges[changeIndex].tickBeforeStep) <= time:
        let change = sortedChanges[changeIndex]
        masks[change.player] = change.mask
        inc changeIndex

      while chatIndex < sortedChats.len and
          tickTime(sortedChats[chatIndex].tickBeforeStep) <= time:
        let chat = sortedChats[chatIndex]
        sim.addVotingChat(chat.player, chat.message)
        inc chatIndex

      var
        inputs = newSeq[InputState](playerCount)
        prevInputs = newSeq[InputState](playerCount)
      for playerIndex in 0 ..< playerCount:
        prevInputs[playerIndex] = decodeInputMask(lastMasks[playerIndex])
        inputs[playerIndex] = decodeInputMask(masks[playerIndex])
        lastMasks[playerIndex] = masks[playerIndex]

      sim.step(inputs, prevInputs)
      result.hashes.add ReplayHash(
        tick: uint32(sim.tickCount),
        hash: sim.gameHash()
      )
  )

proc timelineFor(config: GameConfig, replay: ReplayData): EpisodeTimeline =
  withGameDir(proc(): EpisodeTimeline =
    EpisodeReport(config: config, replay: replay).extractTimeline()
  )

proc firstEvent(timeline: EpisodeTimeline, kind: GameEventKind): GameEvent =
  for event in timeline.events:
    if event.kind == kind:
      return event
  doAssert false, "missing event " & $kind

suite "scribe timeline extraction":
  test "validates replay hashes and surfaces mismatches":
    let config = configuredGame([Imposter, Crewmate])
    var replay = synthesizeReplay(config, maxTick = 2)

    check timelineFor(config, replay).hashValidated

    replay.hashes[0].hash = replay.hashes[0].hash xor 1'u64
    let corrupted = timelineFor(config, replay)
    check not corrupted.hashValidated
    check corrupted.warnings.len > 0

  test "separates role assignment from playing start":
    let config = configuredGame([Imposter, Crewmate], roleRevealTicks = 2)
    let timeline = timelineFor(config, synthesizeReplay(config, maxTick = 4))

    check timeline.firstEvent(gekGameStarted).tick == 1
    check timeline.firstEvent(gekPlayingStarted).tick == 3
    check timeline.identities.len == 2
    check timeline.identities[0].role == Imposter
    check timeline.identities[1].role == Crewmate

  test "emits same-tick start events when role reveal is disabled":
    let config = configuredGame([Imposter, Crewmate], roleRevealTicks = 0)
    let timeline = timelineFor(config, synthesizeReplay(config, maxTick = 2))

    check timeline.firstEvent(gekGameStarted).tick == 1
    check timeline.firstEvent(gekPlayingStarted).tick == 1

  test "attributes kills through simulator side effects":
    let config = configuredGame([Imposter, Crewmate])
    let replay = synthesizeReplay(
      config,
      maxTick = 3,
      changes = [InputChange(tickBeforeStep: 1, player: 0, mask: ButtonA)]
    )
    let timeline = timelineFor(config, replay)
    let kill = timeline.firstEvent(gekKill)

    check timeline.hashValidated
    check kill.tick == 2
    check kill.killer.joinOrder == 0
    check kill.victim.joinOrder == 1
    let gameOver = timeline.firstEvent(gekGameOver)
    check gameOver.tick == 2
    check gameOver.reasonText == "crew outnumbered"

  test "resolves body report caller and body with the sim probe":
    let config = configuredGame([Imposter, Crewmate, Crewmate, Crewmate])
    let replay = synthesizeReplay(
      config,
      maxTick = 4,
      changes = [
        InputChange(tickBeforeStep: 1, player: 0, mask: ButtonA),
        InputChange(tickBeforeStep: 2, player: 2, mask: ButtonA)
      ]
    )
    let timeline = timelineFor(config, replay)
    let meeting = timeline.firstEvent(gekMeetingCalled)

    check timeline.hashValidated
    check meeting.tick == 3
    check meeting.reason == mrBody
    check meeting.caller.joinOrder == 2
    check meeting.body.kind == vtkPlayer

  test "emits replay chat records and preserves hash validation":
    let config = configuredGame([Imposter, Crewmate, Crewmate, Crewmate])
    let replay = synthesizeReplay(
      config,
      maxTick = 5,
      changes = [
        InputChange(tickBeforeStep: 1, player: 0, mask: ButtonA),
        InputChange(tickBeforeStep: 2, player: 2, mask: ButtonA)
      ],
      chats = [ChatChange(
        tickBeforeStep: 3,
        player: 2,
        message: "found orange"
      )]
    )

    let timeline = timelineFor(config, replay)
    let chat = timeline.firstEvent(gekChatMessage)

    check timeline.hashValidated
    check chat.tick == 4
    check chat.speaker.joinOrder == 2
    check chat.text == "found orange"
