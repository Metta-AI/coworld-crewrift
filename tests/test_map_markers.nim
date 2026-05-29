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

proc markerLabel(name, fallback: string): string =
  ## Returns the expected marker label from a resource name.
  if name.len > 0:
    name
  else:
    fallback

proc spritePixels(sprite: SpritePacketSpriteDef): seq[uint8] =
  ## Returns the uncompressed pixels for one protocol sprite.
  uncompress(sprite.compressedPixels)

proc assertTransparent(sprite: SpritePacketSpriteDef) =
  ## Checks that one protocol sprite is fully transparent black.
  let pixels = sprite.spritePixels()
  doAssert pixels.len == sprite.width * sprite.height * 4
  for byte in pixels:
    doAssert byte == 0'u8

proc collectSprites(
  messages: openArray[SpritePacketMessage]
): Table[int, SpritePacketSpriteDef] =
  ## Collects sprite definitions by id from one protocol packet.
  for message in messages:
    if message.kind == spkSprite:
      result[message.sprite.id] = message.sprite

proc requireMarker(
  messages: openArray[SpritePacketMessage],
  sprites: Table[int, SpritePacketSpriteDef],
  label: string,
  x, y, w, h: int
) =
  ## Requires one blank labeled marker object at the given rectangle.
  for message in messages:
    if message.kind != spkObject:
      continue
    let objectDef = message.objectDef
    if objectDef.layer != MapLayerId or objectDef.x != x or objectDef.y != y:
      continue
    if objectDef.spriteId notin sprites:
      continue
    let sprite = sprites[objectDef.spriteId]
    if sprite.label == label and sprite.width == w and sprite.height == h:
      sprite.assertTransparent()
      return
  doAssert false, "Missing map marker " & label & " at " & $x & "," & $y

proc buildInitialPlayerMessages(
  sim: var SimServer
): seq[SpritePacketMessage] =
  ## Builds and parses the initial sprite player packet.
  var
    state = initPlayerViewerState()
    nextState: PlayerViewerState
  sim.buildSpriteProtocolPlayerUpdates(-1, state, nextState).parseSpritePacket()

proc testMapMarkers() =
  ## Tests initial blank map markers for tasks, vents, and rooms.
  var game = initCrewriftForTest(defaultGameConfig())
  let
    messages = game.buildInitialPlayerMessages()
    sprites = messages.collectSprites()

  for task in game.tasks:
    messages.requireMarker(
      sprites,
      markerLabel(task.resourceName, "task"),
      task.x,
      task.y,
      task.w,
      task.h
    )

  for vent in game.vents:
    messages.requireMarker(
      sprites,
      markerLabel(vent.resourceName, "vent"),
      vent.x,
      vent.y,
      vent.w,
      vent.h
    )

  for room in game.rooms:
    messages.requireMarker(
      sprites,
      "Room " & room.name,
      room.x,
      room.y,
      room.w,
      room.h
    )

echo "Testing map marker sprites"
testMapMarkers()
echo "ok"
