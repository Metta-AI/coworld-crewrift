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

echo "Testing imposter cooldown bar"
testImposterCooldownBar()
echo "ok"
