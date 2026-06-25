import
  std/[os, tables, unittest],
  bitworld/[server, spriteprotocol],
  pixie,
  crewrift/[global, sim]

const
  GameDir = currentSourcePath.parentDir.parentDir
  InterstitialLayerId = 2
  ProtocolMeetingIconObjectBase = 9800
  MeetingLeftObjectX = 29
  MeetingRightObjectX = 79
  MeetingIconObjectY = 73
  MeetingButtonObjectX = 81
  MeetingButtonObjectY = 75

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc expectedMeetingButtonSprite(): Sprite =
  ## Extracts the expected meeting button from the first sheet cell.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    let sheet = loadSpriteSheet()
    result = spriteFromImage(sheet.subImage(0, 0, SpriteSize, SpriteSize))
  finally:
    setCurrentDir(previousDir)

proc addPlayers(sim: var SimServer, count: int) =
  ## Adds named test players to the simulation.
  for i in 0 ..< count:
    discard sim.addPlayer("player" & $(i + 1))

proc globalMessages(sim: var SimServer): seq[SpritePacketMessage] =
  ## Builds and parses one global sprite protocol packet.
  var
    state = initGlobalViewerState()
    nextState: GlobalViewerState
  sim.buildSpriteProtocolUpdates(state, nextState).parseSpritePacket()

proc collectSprites(
  messages: openArray[SpritePacketMessage]
): Table[int, SpritePacketSpriteDef] =
  ## Collects sprite definitions by id from one protocol packet.
  for message in messages:
    if message.kind == spkSprite:
      result[message.sprite.id] = message.sprite

proc hasSpriteLabel(
  messages: openArray[SpritePacketMessage],
  label: string
): bool =
  ## Returns true when one packet defines a sprite with the given label.
  for message in messages:
    if message.kind == spkSprite and message.sprite.label == label:
      return true

proc spriteLabelForObject(
  messages: openArray[SpritePacketMessage],
  sprites: Table[int, SpritePacketSpriteDef],
  objectId: int
): string =
  ## Returns the sprite label for one object in a parsed packet.
  for message in messages:
    if message.kind != spkObject:
      continue
    let objectDef = message.objectDef
    if objectDef.id != objectId or objectDef.layer != InterstitialLayerId:
      continue
    if objectDef.spriteId in sprites:
      return sprites[objectDef.spriteId].label
  ""

proc objectForId(
  messages: openArray[SpritePacketMessage],
  objectId: int
): SpritePacketObject =
  ## Returns the interstitial object with the given id.
  for message in messages:
    if message.kind != spkObject:
      continue
    let objectDef = message.objectDef
    if objectDef.id == objectId and objectDef.layer == InterstitialLayerId:
      return objectDef

suite "meeting call interstitial":
  test "button icon comes from first sprite sheet cell":
    let
      sim = initCrewriftForTest(defaultGameConfig())
      expected = expectedMeetingButtonSprite()
    check sim.meetingButtonSprite.width == expected.width
    check sim.meetingButtonSprite.height == expected.height
    check sim.meetingButtonSprite.pixels == expected.pixels

  test "body report shows reporter and body":
    var sim = initCrewriftForTest(defaultGameConfig())
    sim.addPlayers(3)
    sim.phase = Playing
    sim.players[0].color = PlayerColors[0]
    sim.players[2].color = PlayerColors[6]
    sim.players[2].alive = false

    sim.startVote(
      VoteCalledBody,
      0,
      sim.players[2].color,
      sim.players[2].joinOrder
    )

    check sim.phase == MeetingCall
    check sim.voteState.callTimer == MeetingCallTicks
    let
      messages = sim.globalMessages()
      sprites = messages.collectSprites()
      playerObject = messages.objectForId(ProtocolMeetingIconObjectBase)
      bodyObject = messages.objectForId(ProtocolMeetingIconObjectBase + 1)
    check messages.hasSpriteLabel("Red reported")
    check messages.hasSpriteLabel("Purple's body")
    check messages.spriteLabelForObject(
      sprites,
      ProtocolMeetingIconObjectBase
    ) == "player red right"
    check messages.spriteLabelForObject(
      sprites,
      ProtocolMeetingIconObjectBase + 1
    ) == "body purple"
    check playerObject.x == MeetingLeftObjectX
    check playerObject.y == MeetingIconObjectY
    check bodyObject.x == MeetingRightObjectX
    check bodyObject.y == MeetingIconObjectY

    for _ in 0 ..< MeetingCallTicks - 1:
      var inputs = newSeq[InputState](sim.players.len)
      sim.step(inputs, inputs)
    check sim.phase == MeetingCall
    var inputs = newSeq[InputState](sim.players.len)
    sim.step(inputs, inputs)
    check sim.phase == Voting
    check sim.voteState.voteTimer == sim.config.voteTimerTicks

  test "button call shows reporter left and button right":
    var sim = initCrewriftForTest(defaultGameConfig())
    sim.addPlayers(3)
    sim.phase = Playing
    sim.players[0].color = PlayerColors[0]

    sim.startVote(VoteCalledButton, 0)

    let
      messages = sim.globalMessages()
      sprites = messages.collectSprites()
      playerObject = messages.objectForId(ProtocolMeetingIconObjectBase)
      buttonObject = messages.objectForId(ProtocolMeetingIconObjectBase + 1)
    check messages.hasSpriteLabel("Red pressed")
    check messages.hasSpriteLabel("the button")
    check messages.spriteLabelForObject(
      sprites,
      ProtocolMeetingIconObjectBase
    ) == "player red right"
    check messages.spriteLabelForObject(
      sprites,
      ProtocolMeetingIconObjectBase + 1
    ) == "meeting button"
    check playerObject.x == MeetingLeftObjectX
    check playerObject.y == MeetingIconObjectY
    check buttonObject.x == MeetingButtonObjectX
    check buttonObject.y == MeetingButtonObjectY
    check buttonObject.x > playerObject.x
