import
  std/[algorithm, options, strutils],
  pixie, supersnappy, whisky,
  protocol, profile, framebuffers

const
  MaxFrameDrain* = 128
  FrameDropThreshold* = 32
  ProtocolBytes* = (ScreenWidth * ScreenHeight) div 2
  PacketInput = 0'u8
  PacketChat = 1'u8
  InputPacketBytes = 2
  MapSpriteId = 1
  MapObjectId = 1

type
  WireProtocolMode* = enum
    WireBitstream
    WireSprite

  SpriteInfo* = ref object
    defined*: bool
    width*: int
    height*: int
    label*: string
    pixels: seq[uint8]

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
    mode*: WireProtocolMode
    queuedFrames: seq[string]
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
    objectIds: seq[int]

proc initSpriteState(): SpriteState =
  ## Builds the initial sprite protocol state.
  SpriteState()

proc initProtocolClient*(mode: WireProtocolMode): ProtocolClient =
  ## Builds protocol state for one websocket connection.
  result = ProtocolClient(
    mode: mode,
    sprite: initSpriteState(),
    packed: newSeq[uint8](ProtocolBytes),
    unpacked: newSeq[uint8](ScreenWidth * ScreenHeight)
  )

proc reset*(client: ProtocolClient) =
  ## Clears queued wire data while preserving reusable frame buffers.
  client.queuedFrames.setLen(0)
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

proc protocolName*(mode: WireProtocolMode): string =
  ## Returns a short protocol mode name.
  case mode
  of WireBitstream:
    "bitstream"
  of WireSprite:
    "sprite"

proc parseProtocolMode*(value: string): WireProtocolMode =
  ## Parses one command line protocol mode.
  case value.toLowerAscii()
  of "bitstream", "bits", "frame", "frames", "old":
    WireBitstream
  of "", "sprite", "sprites", "new":
    WireSprite
  else:
    raise newException(ValueError, "Unknown protocol mode: " & value)

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

proc blobToBytes*(blob: string, bytes: var seq[uint8]) {.measure.} =
  ## Copies websocket blob bytes into a reusable byte buffer.
  if bytes.len != blob.len:
    bytes.setLen(blob.len)
  for i in 0 ..< blob.len:
    bytes[i] = blob[i].uint8

proc blobFromMask*(mask: uint8): string =
  ## Builds a legacy bitstream input packet from one input mask.
  result = newString(InputPacketBytes)
  result[0] = char(PacketInput)
  result[1] = char(mask)

proc blobFromChat*(text: string): string =
  ## Builds a legacy bitstream chat packet from ASCII text.
  result = newString(text.len + 1)
  result[0] = char(PacketChat)
  for i, ch in text:
    result[i + 1] = ch

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
  ## Builds one legacy player input packet.
  blobFromMask(mask)

proc inputBlob*(mode: WireProtocolMode, mask: uint8): string =
  ## Builds one player input packet for the selected protocol.
  case mode
  of WireBitstream:
    blobFromMask(mask)
  of WireSprite:
    blobFromBytes([0x84'u8, mask and 0x7f'u8])

proc chatBlob*(text: string): string =
  ## Builds one legacy player chat packet.
  blobFromChat(text)

proc addU16(packet: var seq[uint8], value: int) =
  ## Appends one little endian unsigned 16 bit value.
  let v = uint16(value)
  packet.add(uint8(v and 0xff'u16))
  packet.add(uint8(v shr 8))

proc chatBlob*(mode: WireProtocolMode, text: string): string =
  ## Builds one player chat packet for the selected protocol.
  case mode
  of WireBitstream:
    blobFromChat(text)
  of WireSprite:
    var bytes: seq[uint8] = @[0x81'u8]
    bytes.addU16(text.len)
    for ch in text:
      bytes.add(uint8(ord(ch)))
    blobFromBytes(bytes)

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
  ## Packs palette indices into the old 4 bit framebuffer layout.
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
  if client.mode != WireSprite or client.sprite.isNil:
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
  if client.mode == WireSprite and not client.sprite.isNil:
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
  if client.mode == WireSprite and not client.sprite.isNil:
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

proc readU16(blob: string, offset: int): int =
  ## Reads one little endian unsigned 16 bit value.
  int(uint16(blob[offset].uint8) or
    (uint16(blob[offset + 1].uint8) shl 8))

proc readI16(blob: string, offset: int): int =
  ## Reads one little endian signed 16 bit value.
  let value = uint16(blob[offset].uint8) or
    (uint16(blob[offset + 1].uint8) shl 8)
  int(cast[int16](value))

proc readU32(blob: string, offset: int): int =
  ## Reads one little endian unsigned 32 bit value.
  int(uint32(blob[offset].uint8) or
    (uint32(blob[offset + 1].uint8) shl 8) or
    (uint32(blob[offset + 2].uint8) shl 16) or
    (uint32(blob[offset + 3].uint8) shl 24))

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
  var offset = 0
  while offset < packet.len:
    let messageType = packet[offset].uint8
    inc offset
    case messageType
    of 0x01:
      if offset + 10 > packet.len:
        return false
      let
        spriteId = packet.readU16(offset)
        width = packet.readU16(offset + 2)
        height = packet.readU16(offset + 4)
        compressedLen = packet.readU32(offset + 6)
      offset += 10
      if compressedLen < 0 or offset + compressedLen + 2 > packet.len:
        return false
      let compressedStart = offset
      offset += compressedLen
      let labelLen = packet.readU16(offset)
      offset += 2
      if offset + labelLen > packet.len:
        return false
      let label =
        if labelLen > 0:
          packet.substr(offset, offset + labelLen - 1)
        else:
          ""
      offset += labelLen
      let
        shouldDecodeWalkability = label == "walkability map"
        shouldDecodePixels = decodePixels
      var
        compressed = ""
        pixels: seq[uint8]
      if shouldDecodeWalkability or shouldDecodePixels:
        compressed =
          if compressedLen > 0:
            packet.substr(compressedStart, compressedStart + compressedLen - 1)
          else:
            ""
      if shouldDecodeWalkability:
        if not decodeWalkabilityPixels(
          width,
          height,
          compressed,
          client.walkabilityMask
        ):
          return false
        client.walkabilityReady = true
        client.walkabilityWidth = width
        client.walkabilityHeight = height
      if shouldDecodePixels:
        if not decodeSpritePixels(width, height, compressed, pixels):
          return false
      client.sprite.ensureSprite(spriteId)
      client.sprite.sprites[spriteId] = SpriteInfo(
        defined: true,
        width: width,
        height: height,
        label: label,
        pixels: pixels
      )
    of 0x02:
      if offset + 11 > packet.len:
        return false
      let
        objectId = packet.readU16(offset)
        x = packet.readI16(offset + 2)
        y = packet.readI16(offset + 4)
        z = packet.readI16(offset + 6)
        layer = int(packet[offset + 8].uint8)
        spriteId = packet.readU16(offset + 9)
      offset += 11
      client.sprite.ensureObject(objectId)
      client.sprite.objects[objectId] = ObjectState(
        present: true,
        x: x,
        y: y,
        z: z,
        layer: layer,
        spriteId: spriteId
      )
      if objectId == MapObjectId and spriteId == MapSpriteId:
        client.mapCameraReady = true
        client.mapCameraX = -x
        client.mapCameraY = -y
    of 0x03:
      if offset + 2 > packet.len:
        return false
      let objectId = packet.readU16(offset)
      offset += 2
      if objectId >= 0 and objectId < client.sprite.objects.len:
        client.sprite.objects[objectId].present = false
      if objectId == MapObjectId:
        client.mapCameraReady = false
    of 0x04:
      for item in client.sprite.objects.mitems:
        item.present = false
      client.mapCameraReady = false
    of 0x05:
      if offset + 5 > packet.len:
        return false
      offset += 5
    of 0x06:
      if offset + 3 > packet.len:
        return false
      offset += 3
    else:
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
    case client.mode
    of WireBitstream:
      if message.data.len == ProtocolBytes:
        client.queuedFrames.add(message.data)
    of WireSprite:
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
  if client.queuedFrames.len == 0 and client.spritePending == 0:
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

  case client.mode
  of WireBitstream:
    if client.queuedFrames.len == 0:
      client.frameBufferLen = 0
      client.framesDropped = 0
      return false
    var frame = ""
    client.frameAdvance = 1
    client.framesDropped = 0
    if client.queuedFrames.len >= FrameDropThreshold:
      client.framesDropped = client.queuedFrames.len - 1
      client.frameAdvance = client.queuedFrames.len
      frame = client.queuedFrames[^1]
      client.queuedFrames.setLen(0)
    else:
      frame = client.queuedFrames[0]
      client.queuedFrames.delete(0)
    client.frameBufferLen = client.queuedFrames.len
    client.skippedFrames += client.framesDropped
    blobToBytes(frame, packed)
    unpack4bpp(packed, unpacked)
  of WireSprite:
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
