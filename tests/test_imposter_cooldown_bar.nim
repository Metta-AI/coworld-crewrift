import
  std/[os, tables],
  supersnappy,
  bitworld/spriteprotocol,
  crewrift/[global, sim]

const GameDir = currentSourcePath.parentDir.parentDir

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc collectSprites(
  messages: openArray[SpritePacketMessage]
): Table[int, SpritePacketSpriteDef] =
  ## Collects sprite definitions by id from one protocol packet.
  for message in messages:
    if message.kind == spkSprite:
      result[message.sprite.id] = message.sprite

proc assertProgressPixels(sprite: SpritePacketSpriteDef, filled: int) =
  ## Checks the progress sprite has the expected filled pixel count.
  doAssert sprite.width == TaskBarWidth
  doAssert sprite.height == 1
  doAssert filled > 0 and filled < sprite.width
  let
    pixels = uncompress(sprite.compressedPixels)
    filledColor = pixels[0 .. 3]
    emptyColor = pixels[filled * 4 .. filled * 4 + 3]
  doAssert pixels.len == sprite.width * sprite.height * 4
  doAssert filledColor != emptyColor
  for x in 0 ..< filled:
    doAssert pixels[x * 4 .. x * 4 + 3] == filledColor
  for x in filled ..< sprite.width:
    doAssert pixels[x * 4 .. x * 4 + 3] == emptyColor

proc requireCooldownBar(
  messages: openArray[SpritePacketMessage],
  sprites: Table[int, SpritePacketSpriteDef],
  percent,
  filled: int
) =
  ## Requires the imposter cooldown progress bar object.
  let
    expectedLabel = "progress bar " & $percent & "%"
    expectedX = 1 + SpriteSize + TaskBarGap
    expectedY = ScreenHeight - SpriteSize - 1 + SpriteSize div 2
  for message in messages:
    if message.kind != spkObject:
      continue
    let objectDef = message.objectDef
    if objectDef.layer != MapLayerId or
        objectDef.x != expectedX or
        objectDef.y != expectedY:
      continue
    if objectDef.spriteId notin sprites:
      continue
    let sprite = sprites[objectDef.spriteId]
    if sprite.label == expectedLabel:
      sprite.assertProgressPixels(filled)
      return
  doAssert false, "Missing imposter cooldown progress bar."

proc hasPlayerMapLayer(
  messages: openArray[SpritePacketMessage]
): bool =
  ## Returns true when a player packet keeps layer zero as a map layer.
  for message in messages:
    if message.kind == spkLayer and
        message.layer.layer == MapLayerId and
        message.layer.kind == MapLayerType and
        message.layer.flags == ZoomableLayerFlag:
      return true

proc requirePlayerMapCamera(
  messages: openArray[SpritePacketMessage],
  sim: SimServer,
  playerIndex: int
) =
  ## Requires the full map sprite and camera offset used by bots.
  let view = sim.playerView(playerIndex)
  var
    foundMapSprite = false
    foundMapObject = false
  for message in messages:
    case message.kind
    of spkSprite:
      if message.sprite.id == MapSpriteId and
          message.sprite.width == MapWidth and
          message.sprite.height == MapHeight and
          message.sprite.label == "map":
        foundMapSprite = true
    of spkObject:
      if message.objectDef.id == MapObjectId and
          message.objectDef.layer == MapLayerId and
          message.objectDef.spriteId == MapSpriteId and
          message.objectDef.x == -view.cameraX and
          message.objectDef.y == -view.cameraY:
        foundMapObject = true
    else:
      discard
  doAssert foundMapSprite
  doAssert foundMapObject

proc requireObjectAt(
  messages: openArray[SpritePacketMessage],
  objectId,
  x,
  y: int
) =
  ## Requires one object at the requested screen position.
  for message in messages:
    if message.kind == spkObject and
        message.objectDef.id == objectId and
        message.objectDef.x == x and
        message.objectDef.y == y:
      return
  doAssert false, "Missing object " & $objectId & " at " & $x & "," & $y & "."

proc buildPlayerMessages(
  sim: var SimServer,
  playerIndex: int
): seq[SpritePacketMessage] =
  ## Builds and parses one player sprite packet.
  var
    state = initPlayerViewerState()
    nextState: PlayerViewerState
  sim.buildSpriteProtocolPlayerUpdates(
    playerIndex,
    state,
    nextState
  ).parseSpritePacket()

proc testImposterCooldownBar() =
  ## Tests the imposter kill icon cooldown progress bar.
  var config = defaultGameConfig()
  config.killCooldownTicks = 100
  var game = initCrewriftForTest(config)
  let imposter = game.addPlayer("imp")
  game.phase = Playing
  game.players[imposter].role = Imposter
  game.players[imposter].killCooldown = 25

  let
    messages = game.buildPlayerMessages(imposter)
    sprites = messages.collectSprites()
  messages.requireCooldownBar(sprites, 75, 10)

proc testPlayerPacketsUseMapLayer() =
  ## Tests that bot player packets keep layer zero map-shaped.
  var game = initCrewriftForTest(defaultGameConfig())
  let player = game.addPlayer("crew")
  game.phase = Playing
  game.players[player].role = Crewmate

  let messages = game.buildPlayerMessages(player)
  doAssert messages.hasPlayerMapLayer()
  messages.requirePlayerMapCamera(game, player)

proc testPlayerActorCullsBySpriteBounds() =
  ## Tests that actor sprites stay visible while any edge overlaps.
  var game = initCrewriftForTest(defaultGameConfig())
  let
    viewer = game.addPlayer("viewer")
    other = game.addPlayer("other")
    objectId = PlayerObjectBase + game.players[other].joinOrder
  game.phase = Playing
  game.players[viewer].role = Crewmate
  game.players[viewer].alive = false
  game.players[viewer].x = 300
  game.players[viewer].y = 300
  game.players[other].role = Crewmate

  let
    view = game.playerView(viewer)
    actorW = CrewSpriteSize + 2
    actorH = CrewSpriteSize + 2
    positions = [
      (x: 1 - actorW, y: 32),
      (x: ScreenWidth - 1, y: 32),
      (x: 32, y: 1 - actorH),
      (x: 32, y: ScreenHeight - 1)
    ]

  for position in positions:
    game.players[other].x =
      view.cameraX + position.x + SpriteDrawOffX + 1
    game.players[other].y =
      view.cameraY + position.y + SpriteDrawOffY + 1
    let messages = game.buildPlayerMessages(viewer)
    messages.requireObjectAt(objectId, position.x, position.y)

echo "Testing imposter cooldown bar"
testPlayerPacketsUseMapLayer()
testPlayerActorCullsBySpriteBounds()
testImposterCooldownBar()
echo "ok"
