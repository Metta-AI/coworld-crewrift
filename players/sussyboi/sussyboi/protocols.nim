import
  std/[algorithm, options, strutils],
  bitworld/[profile, spriteprotocol, server],
  pixie, supersnappy, whisky

const
  MaxFrameDrain* = 128
  MapSpriteId = 1
  MapObjectId = 1

type
  SpriteInfo* = ref object
    defined*: bool
    width*: int
    height*: int
    label*: string
    pixels*: seq[uint8]

  ObjectState = object
    present: bool
    x: int
    y: int
    z: int
    layer: int
    spriteId: int

  SpriteState = ref object
    sprites: seq[SpriteInfo]
    objects: seq[ObjectState]

  SpriteObjectInfo* = object
    objectId*: int
    x*: int
    y*: int
    width*: int
    height*: int

  ProtocolClient* = ref object
    sprite: SpriteState
    spritePending: int
    frameAdvance*: int
    frameBufferLen*: int
    framesDropped*: int
    skippedFrames*: int
    mapCameraReady*: bool
    mapCameraX*: int
    mapCameraY*: int
    walkabilityReady*: bool
    walkabilityWidth*: int
    walkabilityHeight*: int
    walkabilityMask*: seq[bool]
    packed*: seq[uint8]
    unpacked*: seq[uint8]
    packetBytes: seq[uint8]
    objectIds: seq[int]

proc initSpriteState(): SpriteState =
  ## Builds the initial sprite protocol state.
  SpriteState()

proc initProtocolClient*(): ProtocolClient =
  ## Builds protocol state for one websocket connection.
  result = ProtocolClient(
    sprite: initSpriteState(),
    packed: newSeq[uint8](ProtocolBytes),
    unpacked: newSeq[uint8](ScreenWidth * ScreenHeight)
  )

proc reset*(client: ProtocolClient) =
  ## Clears queued wire data while preserving reusable frame buffers.
  client.sprite = initSpriteState()
  client.spritePending = 0
  client.frameAdvance = 0
  client.frameBufferLen = 0
  client.framesDropped = 0
  client.skippedFrames = 0
  client.mapCameraReady = false
  client.mapCameraX = 0
  client.mapCameraY = 0
  client.walkabilityReady = false
  client.walkabilityWidth = 0
  client.walkabilityHeight = 0
  client.walkabilityMask.setLen(0)

proc queryEscape*(value: string): string =
  ## Escapes a small string for use in a websocket query parameter.
  const Hex = "0123456789ABCDEF"
  for ch in value:
    if ch in {'a' .. 'z'} or ch in {'A' .. 'Z'} or ch in {'0' .. '9'} or
        ch in {'-', '_', '.', '~'}:
      result.add(ch)
    else:
      let byte = ord(ch)
      result.add('%')
      result.add(Hex[(byte shr 4) and 0x0f])
      result.add(Hex[byte and 0x0f])

proc hasQueryParam(url, key: string): bool =
  ## Returns true when a URL already carries one query key.
  url.contains("?" & key & "=") or url.contains("&" & key & "=")

proc addQueryParam*(url, key, value: string): string =
  ## Adds one encoded query parameter to a URL.
  if value.len == 0 or url.hasQueryParam(key):
    return url
  url & (if '?' in url: "&" else: "?") & key & "=" & value.queryEscape()

proc playerConnectUrl*(
  endpoint,
  name,
  token: string,
  slot: int
): string =
  ## Adds player join query parameters to an endpoint.
  result = endpoint
  result = result.addQueryParam("name", name)
  if slot >= 0:
    result = result.addQueryParam("slot", $slot)
  result = result.addQueryParam("token", token)

proc ensureWsPath*(url: string, defaultPath: string): string =
  ## Inserts `defaultPath` when a websocket URL has no path.
  let scheme = url.find("://")
  let start =
    if scheme < 0:
      0
    else:
      scheme + 3
  for i in start ..< url.len:
    case url[i]
    of '/':
      return url
    of '?', '#':
      return url[0 ..< i] & defaultPath & url[i .. ^1]
    else:
      discard
  url & defaultPath

proc inputBlob*(mask: uint8): string =
  ## Builds one sprite player input packet.
  blobFromSpriteMask(mask)

proc chatBlob*(text: string): string =
  ## Builds one sprite player chat packet.
  blobFromSpriteChat(text)

proc unpack4bpp*(
  packed: openArray[uint8],
  unpacked: var seq[uint8]
) {.measure.} =
  ## Expands one packed 4 bit framebuffer into palette indices.
  let targetLen = packed.len * 2
  if unpacked.len != targetLen:
    unpacked.setLen(targetLen)
  for i, byte in packed:
    unpacked[i * 2] = byte and 0x0f
    unpacked[i * 2 + 1] = (byte shr 4) and 0x0f

proc pack4bpp*(
  unpacked: openArray[uint8],
  packed: var seq[uint8]
) {.measure.} =
  ## Packs palette indices into the 4 bit framebuffer layout.
  let targetLen = unpacked.len div 2
  if packed.len != targetLen:
    packed.setLen(targetLen)
  for i in 0 ..< targetLen:
    let
      lo = unpacked[i * 2] and 0x0f
      hi = unpacked[i * 2 + 1] and 0x0f
    packed[i] = lo or (hi shl 4)

proc copyBytes(
  source: openArray[uint8],
  target: var seq[uint8]
) {.measure.} =
  ## Copies byte data while reusing the target allocation.
  if target.len != source.len:
    target.setLen(source.len)
  for i, value in source:
    target[i] = value

proc ensureSprite(state: SpriteState, spriteId: int) =
  ## Ensures the sprite table can hold one sprite id.
  if spriteId >= state.sprites.len:
    state.sprites.setLen(spriteId + 1)

proc ensureObject(state: SpriteState, objectId: int) =
  ## Ensures the object table can hold one object id.
  if objectId >= state.objects.len:
    state.objects.setLen(objectId + 1)

proc spriteInfo(state: SpriteState, spriteId: int): SpriteInfo =
  ## Returns sprite metadata or nil for an unknown sprite.
  if spriteId >= 0 and spriteId < state.sprites.len:
    return state.sprites[spriteId]

proc spriteObjectsWithLabel*(
  client: ProtocolClient,
  label: string
): seq[SpriteObjectInfo] =
  ## Returns present sprite objects whose sprite label matches exactly.
  if client.sprite.isNil:
    return
  for objectId, objectState in client.sprite.objects:
    if not objectState.present:
      continue
    let sprite = client.sprite.spriteInfo(objectState.spriteId)
    if sprite.isNil or not sprite.defined or sprite.label != label:
      continue
    result.add(SpriteObjectInfo(
      objectId: objectId,
      x: objectState.x,
      y: objectState.y,
      width: sprite.width,
      height: sprite.height
    ))

iterator spriteObjects*(
  client: ProtocolClient
): tuple[
  objectId: int,
  x: int,
  y: int,
  width: int,
  height: int,
  label: string
] =
  ## Iterates present sprite objects with their sprite metadata.
  if not client.sprite.isNil:
    for objectId, objectState in client.sprite.objects:
      if not objectState.present:
        continue
      let sprite = client.sprite.spriteInfo(objectState.spriteId)
      if sprite.isNil or not sprite.defined:
        continue
      yield (
        objectId: objectId,
        x: objectState.x,
        y: objectState.y,
        width: sprite.width,
        height: sprite.height,
        label: sprite.label
      )

iterator spriteObjectRefs*(
  client: ProtocolClient
): tuple[
  objectId: int,
  x: int,
  y: int,
  sprite: SpriteInfo
] =
  ## Iterates present sprite objects with a metadata reference.
  if not client.sprite.isNil:
    for objectId, objectState in client.sprite.objects:
      if not objectState.present:
        continue
      let sprite = client.sprite.spriteInfo(objectState.spriteId)
      if sprite.isNil or not sprite.defined:
        continue
      yield (
        objectId: objectId,
        x: objectState.x,
        y: objectState.y,
        sprite: sprite
      )

proc decodeSpritePixels(
  width,
  height: int,
  compressed: string,
  pixels: var seq[uint8]
): bool {.measure.} =
  ## Decodes one compressed RGBA sprite payload into palette indices.
  var rawPixels = ""
  try:
    rawPixels = supersnappy.uncompress(compressed)
  except CatchableError:
    return false
  if width <= 0 or height <= 0 or rawPixels.len != width * height * 4:
    return false
  pixels.setLen(width * height)
  for i in 0 ..< pixels.len:
    let pixel = rgba(
      rawPixels[i * 4].uint8,
      rawPixels[i * 4 + 1].uint8,
      rawPixels[i * 4 + 2].uint8,
      rawPixels[i * 4 + 3].uint8
    )
    pixels[i] = nearestPaletteIndex(pixel)
  true

proc decodeWalkabilityPixels(
  width,
  height: int,
  compressed: string,
  mask: var seq[bool]
): bool {.measure.} =
  ## Decodes the sprite protocol walkability payload into a bool mask.
  var rawPixels = ""
  try:
    rawPixels = supersnappy.uncompress(compressed)
  except CatchableError:
    return false
  if width <= 0 or height <= 0 or rawPixels.len != width * height * 4:
    return false
  mask.setLen(width * height)
  for i in 0 ..< mask.len:
    mask[i] = rawPixels[i * 4 + 3].uint8 > 0
  true

proc applySpritePacket(
  client: ProtocolClient,
  packet: string,
  decodePixels: bool
): bool {.measure.} =
  ## Applies sprite protocol messages to the retained scene state.
  blobToBytes(packet, client.packetBytes)
  try:
    for message in parseSpritePacket(client.packetBytes):
      case message.kind
      of spkSprite:
        let sprite = message.sprite
        let
          shouldDecodeWalkability = sprite.label == "walkability map"
          shouldDecodePixels = decodePixels
        var
          compressed = ""
          pixels: seq[uint8]
        if shouldDecodeWalkability or shouldDecodePixels:
          compressed = blobFromBytes(sprite.compressedPixels)
        if shouldDecodeWalkability:
          if not decodeWalkabilityPixels(
            sprite.width,
            sprite.height,
            compressed,
            client.walkabilityMask
          ):
            return false
          client.walkabilityReady = true
          client.walkabilityWidth = sprite.width
          client.walkabilityHeight = sprite.height
        if shouldDecodePixels:
          if not decodeSpritePixels(
            sprite.width,
            sprite.height,
            compressed,
            pixels
          ):
            return false
        client.sprite.ensureSprite(sprite.id)
        client.sprite.sprites[sprite.id] = SpriteInfo(
          defined: true,
          width: sprite.width,
          height: sprite.height,
          label: sprite.label,
          pixels: pixels
        )
      of spkObject:
        let objectDef = message.objectDef
        client.sprite.ensureObject(objectDef.id)
        client.sprite.objects[objectDef.id] = ObjectState(
          present: true,
          x: objectDef.x,
          y: objectDef.y,
          z: objectDef.z,
          layer: objectDef.layer,
          spriteId: objectDef.spriteId
        )
        if objectDef.id == MapObjectId and objectDef.spriteId == MapSpriteId:
          client.mapCameraReady = true
          client.mapCameraX = -objectDef.x
          client.mapCameraY = -objectDef.y
      of spkDeleteObject:
        let objectId = message.objectId
        if objectId >= 0 and objectId < client.sprite.objects.len:
          client.sprite.objects[objectId].present = false
        if objectId == MapObjectId:
          client.mapCameraReady = false
      of spkClearObjects:
        for item in client.sprite.objects.mitems:
          item.present = false
        client.mapCameraReady = false
      of spkViewport, spkLayer:
        discard
  except SpriteProtocolError:
    return false
  true

proc objectCmp(state: SpriteState, a, b: int): int =
  ## Orders objects by the same stable painter order as the viewer.
  let
    oa = state.objects[a]
    ob = state.objects[b]
  result = system.cmp(oa.z, ob.z)
  if result == 0:
    result = system.cmp(oa.y, ob.y)
  if result == 0:
    result = system.cmp(a, b)

proc drawObject(
  client: ProtocolClient,
  objectState: ObjectState,
  unpacked: var seq[uint8]
) {.measure.} =
  ## Draws one sprite object into the current 128x128 frame.
  if objectState.layer != 0:
    return
  let sprite = client.sprite.spriteInfo(objectState.spriteId)
  if sprite.isNil or not sprite.defined:
    return
  if sprite.pixels.len != sprite.width * sprite.height:
    return
  let
    startX = max(0, -objectState.x)
    startY = max(0, -objectState.y)
    stopX = min(sprite.width, ScreenWidth - objectState.x)
    stopY = min(sprite.height, ScreenHeight - objectState.y)
  if startX >= stopX or startY >= stopY:
    return
  for y in startY ..< stopY:
    let
      destY = objectState.y + y
      sourceRow = y * sprite.width
      destRow = destY * ScreenWidth
    for x in startX ..< stopX:
      let color = sprite.pixels[sourceRow + x]
      if color != TransparentColorIndex:
        unpacked[destRow + objectState.x + x] = color and 0x0f

proc renderSpriteFrame(
  client: ProtocolClient,
  unpacked,
  packed: var seq[uint8]
) {.measure.} =
  ## Renders the retained sprite scene into the palette-index framebuffer.
  if unpacked.len != ScreenWidth * ScreenHeight:
    unpacked.setLen(ScreenWidth * ScreenHeight)
  for i in 0 ..< unpacked.len:
    unpacked[i] = 0'u8
  client.objectIds.setLen(0)
  for i, objectState in client.sprite.objects:
    if objectState.present:
      client.objectIds.add(i)
  client.objectIds.sort(proc(a, b: int): int = client.sprite.objectCmp(a, b))
  for objectId in client.objectIds:
    client.drawObject(client.sprite.objects[objectId], unpacked)
  pack4bpp(unpacked, packed)

proc renderSpriteFrame(client: ProtocolClient) =
  ## Renders the retained sprite scene into client-owned buffers.
  client.renderSpriteFrame(client.unpacked, client.packed)

proc acceptPlayerMessage(
  ws: WebSocket,
  message: Message,
  client: ProtocolClient,
  decodePixels: bool
) {.measure.} =
  ## Handles one websocket message and updates the active parser.
  case message.kind
  of BinaryMessage:
    if not client.applySpritePacket(message.data, decodePixels):
      raise newException(ValueError, "Malformed sprite protocol packet.")
    inc client.spritePending
  of Ping:
    ws.send(message.data, Pong)
  of TextMessage, Pong:
    discard

proc receiveLatestFrameInto*(
  client: ProtocolClient,
  ws: WebSocket,
  gui: bool,
  packed,
  unpacked: var seq[uint8]
): bool {.measure.} =
  ## Receives wire data and updates the provided reusable frame buffers.
  client.frameAdvance = 0
  if client.spritePending == 0:
    let firstMessage = ws.receiveMessage(if gui: 10 else: -1)
    if firstMessage.isNone:
      client.frameBufferLen = 0
      client.framesDropped = 0
      return false
    ws.acceptPlayerMessage(firstMessage.get, client, gui)

  var drained = 0
  while drained < MaxFrameDrain:
    let message = ws.receiveMessage(0)
    if message.isNone:
      break
    ws.acceptPlayerMessage(message.get, client, gui)
    inc drained

  if client.spritePending == 0:
    client.frameBufferLen = 0
    client.framesDropped = 0
    return false
  client.frameAdvance = client.spritePending
  client.framesDropped = max(0, client.spritePending - 1)
  client.frameBufferLen = 0
  client.skippedFrames += client.framesDropped
  client.spritePending = 0
  if gui:
    client.renderSpriteFrame(unpacked, packed)
  true

proc receiveLatestFrame*(
  client: ProtocolClient,
  ws: WebSocket,
  gui: bool
): bool =
  ## Receives wire data and updates the latest client-owned frame buffers.
  client.receiveLatestFrameInto(ws, gui, client.packed, client.unpacked)

proc copyLatestFrame*(
  client: ProtocolClient,
  packed,
  unpacked: var seq[uint8]
) =
  ## Copies the current parsed frame into bot-owned buffers.
  copyBytes(client.packed, packed)
  copyBytes(client.unpacked, unpacked)
