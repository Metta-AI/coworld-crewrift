import
  std/os,
  supersnappy,
  bitworld/spriteprotocol,
  crewrift/[global, sim],
  ../players/notsus/notsus/protocols

const GameDir = currentSourcePath.parentDir.parentDir

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc buildPlayerPacket(
  sim: var SimServer,
  playerIndex: int
): seq[uint8] =
  ## Builds one raw player sprite protocol packet.
  var
    state = initPlayerViewerState()
    nextState: PlayerViewerState
  sim.buildSpriteProtocolPlayerUpdates(
    playerIndex,
    state,
    nextState
  )

proc requireTickSprite(
  messages: openArray[SpritePacketMessage],
  expectedLabel: string
): int =
  ## Requires one transparent tick sprite and returns its id.
  for message in messages:
    if message.kind != spkSprite:
      continue
    let sprite = message.sprite
    if sprite.label != expectedLabel:
      continue
    doAssert sprite.width == 1
    doAssert sprite.height == 1
    let pixels = uncompress(sprite.compressedPixels)
    doAssert pixels.len == 4
    for pixel in pixels:
      doAssert pixel == 0'u8
    return sprite.id
  doAssert false, "Missing tick sprite " & expectedLabel & "."

proc requireTickObject(
  messages: openArray[SpritePacketMessage],
  spriteId: int
) =
  ## Requires one object using the tick sprite on the map layer.
  for message in messages:
    if message.kind == spkObject and message.objectDef.spriteId == spriteId:
      doAssert message.objectDef.x == 0
      doAssert message.objectDef.y == 0
      doAssert message.objectDef.layer == MapLayerId
      return
  doAssert false, "Missing tick marker object."

proc testTickLabelValue() =
  ## Tests tick labels parse only well-formed positive values.
  doAssert tickLabelValue("tick 2000") == 2000
  doAssert tickLabelValue("tick 0") == 0
  doAssert tickLabelValue("tick ") == -1
  doAssert tickLabelValue("tick x") == -1
  doAssert tickLabelValue("task arrow") == -1

proc testTickMarkerPacket() =
  ## Tests player packets include the invisible tick marker.
  var game = initCrewriftForTest(defaultGameConfig())
  let player = game.addPlayer("crew")
  game.phase = Playing
  game.tickCount = 2000

  let
    packet = game.buildPlayerPacket(player)
    messages = packet.parseSpritePacket()
    spriteId = messages.requireTickSprite("tick 2000")
  messages.requireTickObject(spriteId)

proc testNotsusReadsTickMarker() =
  ## Tests the notsus protocol client reads the marker as server time.
  var game = initCrewriftForTest(defaultGameConfig())
  let player = game.addPlayer("crew")
  game.phase = Playing
  game.tickCount = 2000

  let packet = game.buildPlayerPacket(player)
  let client = initProtocolClient()
  doAssert client.applySpritePacket(blobFromBytes(packet), false)
  doAssert client.serverTick() == 2000

echo "Testing tick marker"
testTickLabelValue()
testTickMarkerPacket()
testNotsusReadsTickMarker()
echo "ok"
