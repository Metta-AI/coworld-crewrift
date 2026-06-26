import
  std/os,
  chroma, supersnappy,
  bitworld/pixelfonts, bitworld/profile, bitworld/spriteprotocol, bitworld/server,
  sim

const
  ReplayScrubberSpriteId = 4004
  ReplayScrubberObjectId = 4004
  ReplayScrubberWidth = 84
  ReplayScrubberHeight = 5
  ReplayScrubberTrackY = 2
  ReplayScrubberY = 8
  ReplayPanelHeight = 20
  ReplayCenterBottomLayerId = 8
  ReplayBottomLeftLayerId = 9
  ReplayCenterBottomLayerType = 8
  ReplayBottomLeftLayerType = 4
  ReplayMismatchLayerId = 10
  ReplayMismatchLayerType = 5
  ReplayTickSpriteId = 4002
  ReplayControlsSpriteId = 4003
  ReplayMismatchSpriteId = 4006
  ReplayTickObjectId = 4002
  ReplayControlsObjectId = 4003
  ReplayMismatchObjectId = 4006
  ReplayMismatchMinWidth = 128
  ReplayMismatchPadX = 4
  ReplayMismatchPadY = 3
  ReplayMismatchBgR = 220'u8
  ReplayMismatchBgG = 20'u8
  ReplayMismatchBgB = 20'u8
  ReplayMismatchBgA = 255'u8
  ScoreboardWidth = 160
  ScoreboardHeight = 130
  ScoreboardY = 2
  ScoreboardRowHeight = 8
  ScoreboardPipX = 2
  ScoreboardPipY = 2
  ScoreboardPipSize = 4
  ScoreboardTextX = 8
  ScoreboardTextSpriteBase = 12000
  ScoreboardTextObjectBase = 12100
  ScoreboardPipSpriteBase = 12200
  ScoreboardPipObjectBase = 12300
  ScoreboardTextColor = 2'u8
  ScoreboardSelectedTextColor = 10'u8
  AgentClickRadius = 100
  AgentClickRadiusSq = AgentClickRadius * AgentClickRadius
  InterstitialObjectId = 4005
  InterstitialLayerId = 2
  InterstitialLayerType = 2
  ImposterBarSpriteBase = 740
  ImposterBarObjectBase = 5000
  ImposterBarWidth = 10
  ImposterBarHeight = 2
  ImposterBarYOffset = 4
  ImposterBarBackgroundColor = 5'u8
  ImposterBarReadyColor = 3'u8
  TrailDotSpriteBase = 720
  TrailDotObjectBase = 6000
  TrailDotSize = 3
  TrailDotSpacing = 10
  TrailMaxDots = 10
  PlayerNameSpriteBase = 7000
  PlayerNameObjectBase = 7000
  PlayerNameZ = 30002
  PlayerNameMaxChars = 16
  PlayerNameColor = 2'u8
  TransportIconSize = 6
  TransportIconHeight = 6
  TransportIconCount = 5
  TransportButtonGap = 2
  TransportButtonStride = TransportIconSize + TransportButtonGap
  TransportSpeedX = 0
  TransportSpeedY = 8
  TransportWidth = 108
  TransportHeight = 18
  TransportSpeedGap = 16
  TransportDebugX = 100
  TransportX = 2
  TransportY = 1
  DebugSpriteIdOffset = 55000
  DebugSpriteIdRange = 8000
  DebugSpriteZBase = 31000
  SpritePlayerKillSpriteId = 5000
  SpritePlayerKillShadowSpriteId = 5001
  SpritePlayerGhostIconSpriteId = 5002
  SpritePlayerRemainingSpriteId = 5003
  SpritePlayerProgressSpriteId = 5004
  SpritePlayerArrowSpriteId = 5005
  SpritePlayerInterstitialSpriteId = 5006
  SpritePlayerWalkabilitySpriteId = 5007
  SpritePlayerInterstitialObjectId = 5006
  SpritePlayerRemainingObjectId = 5008
  SpritePlayerProgressObjectId = 5009
  SpritePlayerShadowSpriteId = 5010
  SpritePlayerShadowObjectId = 13000
  SpritePlayerShadowZ = -32767
  SpritePlayerVoteCursorSpriteId = 5011
  SpritePlayerVoteSkipCursorSpriteId = 5012
  SpritePlayerVoteProgressSpriteId = 5013
  SpritePlayerVoteChatBgSpriteId = 5014
  SpritePlayerKillProgressSpriteId = 5015
  SpritePlayerTickSpriteId = 5016
  SpritePlayerMeetingButtonSpriteId = 5017
  SpritePlayerVoteMarkerSpriteBase = 5020
  SpritePlayerVoteDotSpriteBase = 5040
  SpritePlayerTickObjectId = 5016
  SpritePlayerVoteCursorObjectId = 10000
  SpritePlayerVoteSelfMarkerObjectId = 10001
  SpritePlayerVoteProgressObjectId = 10002
  SpritePlayerVoteChatBgObjectId = 10003
  SpritePlayerKillProgressObjectId = 10004
  SpritePlayerVoteDotObjectBase = 10100
  SpritePlayerVoteSkipDotObjectBase = 10400
  SpritePlayerTaskArrowObjectBase = 7000
  MapMarkerSpriteBase = 20000
  MapMarkerObjectBase = 20000
  MapMarkerZ = -32767
  ProtocolTextSpriteBase = 9000
  ProtocolTextObjectBase = 9000
  ProtocolTextZ = 30010
  ProtocolTextColor = 2'u8
  ProtocolChatIconObjectBase = 9200
  ProtocolChatIconZ = 30009
  ProtocolVoteIconObjectBase = 9300
  ProtocolVoteIconZ = 30008
  ProtocolLobbyIconObjectBase = 9400
  ProtocolRoleIconObjectBase = 9500
  ProtocolResultIconObjectBase = 9600
  ProtocolGameOverIconObjectBase = 9700
  ProtocolMeetingIconObjectBase = 9800
  MeetingCallTextY = 36
  MeetingCallIconY = 74
  MeetingCallLeftIconX = 30
  MeetingCallRightIconX = 80
  MeetingCallButtonX = MeetingCallRightIconX + 1
  MeetingCallButtonY = MeetingCallIconY + 1

type
  TrailDot = object
    x, y: int
    colorIndex: int

  PlayerTrail = ref object
    joinOrder: int
    lastX, lastY: int
    dots: seq[TrailDot]

  SpriteDefinition = ref object
    spriteId: int
    width: int
    height: int
    label: string

  GlobalViewerState* = object
    initialized*: bool
    objectIds*: seq[int]
    mouseX*: int
    mouseY*: int
    mouseLayer*: int
    mouseDown*: bool
    mousePressed*: bool
    mouseReleased*: bool
    mousePressX*: int
    mousePressY*: int
    mousePressLayer*: int
    selectedJoinOrder*: int
    clickPending*: bool
    povActive*: bool
    povJoinOrder*: int
    povState*: PlayerViewerState
    scrubbingReplay*: bool
    replaySeekTick*: int
    replayCommands*: seq[char]
    debugSpritesVisible*: bool
    trails: seq[PlayerTrail]
    spriteDefs: seq[SpriteDefinition]

  PlayerViewerState* = ref object
    initialized*: bool
    objectIds*: seq[int]
    spriteDefs: seq[SpriteDefinition]
    shadowReady: bool
    shadowCameraX: int
    shadowCameraY: int
    shadowOriginMx: int
    shadowOriginMy: int

  ProtocolTextItem = ref object
    spriteId: int
    objectId: int
    x, y, z: int
    color: uint8
    struck: bool
    label: string
    lines: seq[string]

var TransportSheet: Sprite

proc initGlobalViewerState*(): GlobalViewerState =
  ## Returns the default state for one global protocol viewer.
  result.mouseLayer = MapLayerId
  result.selectedJoinOrder = -1
  result.povJoinOrder = -1
  new(result.povState)
  result.replaySeekTick = -1
  result.replayCommands = @[]
  # Default the replay debug-sprite overlay on so it renders without first
  # clicking the "D" transport toggle. Only affects replay rendering, which is
  # gated on replayEnabled; the "D" button still toggles it back off.
  result.debugSpritesVisible = true

proc initPlayerViewerState*(): PlayerViewerState =
  ## Returns the default state for one sprite player viewer.
  new(result)

proc clearGlobalMouseEdges*(state: var GlobalViewerState) =
  ## Clears one-frame global mouse button edges.
  state.mousePressed = false
  state.mouseReleased = false
  state.clickPending = false

proc mergeGlobalMouseEdges*(
  state: var GlobalViewerState,
  pending: GlobalViewerState
) =
  ## Preserves global viewer input edges that arrived during a frame.
  if pending.mousePressed or pending.clickPending:
    state.mousePressed = state.mousePressed or pending.mousePressed
    state.clickPending = state.clickPending or pending.clickPending
    state.mousePressX = pending.mousePressX
    state.mousePressY = pending.mousePressY
    state.mousePressLayer = pending.mousePressLayer
  if pending.mouseReleased:
    state.mouseReleased = true
  if pending.mousePressed or pending.mouseReleased or pending.clickPending:
    state.mouseX = pending.mouseX
    state.mouseY = pending.mouseY
    state.mouseLayer = pending.mouseLayer
    state.mouseDown = pending.mouseDown
    if not pending.mouseDown:
      state.scrubbingReplay = false
  for command in pending.replayCommands:
    state.replayCommands.add(command)
  if pending.replaySeekTick >= 0:
    state.replaySeekTick = pending.replaySeekTick

proc putRgbaPixel(pixels: var seq[uint8], pixelIndex: int, color: uint8) =
  ## Writes one palette color as a global protocol RGBA pixel.
  let
    rgba = Palette[color and 0x0f]
    offset = pixelIndex * 4
  pixels[offset] = rgba.r
  pixels[offset + 1] = rgba.g
  pixels[offset + 2] = rgba.b
  pixels[offset + 3] = rgba.a

proc putRgbaPixel(
  pixels: var seq[uint8],
  pixelIndex: int,
  color: ColorRGBA
) =
  ## Writes one true-color global protocol RGBA pixel.
  let offset = pixelIndex * 4
  pixels[offset] = color.r
  pixels[offset + 1] = color.g
  pixels[offset + 2] = color.b
  pixels[offset + 3] = color.a

proc newRgbaPixels(width, height: int): seq[uint8] =
  ## Allocates a transparent RGBA sprite buffer.
  newSeq[uint8](width * height * 4)

proc putRawRgbaPixel(
  pixels: var seq[uint8],
  pixelIndex: int,
  r, g, b, a: uint8
) =
  ## Writes one true-color RGBA pixel.
  let offset = pixelIndex * 4
  pixels[offset] = r
  pixels[offset + 1] = g
  pixels[offset + 2] = b
  pixels[offset + 3] = a

proc crewSpriteIsSolid(sprite: CrewSprite, x, y: int, flipH: bool): bool =
  ## Returns true when one crew sprite pixel has visible alpha.
  let srcX = if flipH: sprite.width - 1 - x else: x
  if srcX < 0 or srcX >= sprite.width or y < 0 or y >= sprite.height:
    return false
  sprite.rgba[sprite.crewSpriteOffset(srcX, y) + 3] >= 20'u8

proc putCrewPixel(
  pixels: var seq[uint8],
  pixelIndex: int,
  sprite: CrewSprite,
  x, y: int,
  tint: uint8
) =
  ## Writes one selectively tinted true-color crew pixel.
  let
    sourceOffset = sprite.crewSpriteOffset(x, y)
    r = sprite.rgba[sourceOffset]
    g = sprite.rgba[sourceOffset + 1]
    b = sprite.rgba[sourceOffset + 2]
    a = sprite.rgba[sourceOffset + 3]
  if a < 20'u8:
    return
  if crewPixelIsTint(r, g, b, a):
    pixels.putRgbaPixel(pixelIndex, playerColorRgba(tint))
  elif crewPixelIsShade(r, g, b, a):
    pixels.putRgbaPixel(pixelIndex, playerShadeRgba(tint))
  else:
    pixels.putRawRgbaPixel(pixelIndex, r, g, b, a)

proc transportSheet(): Sprite =
  ## Returns the cached transport icon sheet.
  if TransportSheet.width == 0:
    TransportSheet = readRequiredSprite(clientDataDir() / "transport.png")
  TransportSheet

proc playerColorIndex(color: uint8): int =
  ## Returns the player color slot for a palette color.
  for i in 0 ..< PlayerColors.len:
    if PlayerColors[i] == color:
      return i
  0

proc playerColorName(index: int): string =
  ## Returns the display name for one player color slot.
  if index >= 0 and index < PlayerColorNames.len:
    return PlayerColorNames[index]
  "unknown"

proc actorRgba(colorIndex, tint: uint8): ColorRGBA =
  ## Returns the true-color RGBA value for actor wildcard pixels.
  if colorIndex == TintColor:
    return playerColorRgba(tint)
  if colorIndex == ShadeTintColor:
    return playerShadeRgba(tint)
  Palette[colorIndex and 0x0f]

proc crewSpriteForSlot(sim: SimServer, slotId: int): CrewSprite =
  ## Returns the crew sprite assigned to one player slot.
  sim.crewSprites[crewVariantIndex(slotId)]

proc crewPlayerSpriteId(colorIndex, slotId: int, flipH: bool): int =
  ## Returns the sprite id for one living crew variant.
  let
    variant = crewVariantIndex(slotId)
    side = if flipH: 1 else: 0
  PlayerSpriteBase + (colorIndex * CrewSpriteVariants + variant) * 2 + side

proc bodySpriteId(colorIndex, slotId: int): int =
  ## Returns the sprite id for one dead body variant.
  BodySpriteBase + colorIndex * CrewSpriteVariants + crewVariantIndex(slotId)

proc selectedCrewPlayerSpriteId(colorIndex, slotId: int, flipH: bool): int =
  ## Returns the selected sprite id for one living crew variant.
  let
    variant = crewVariantIndex(slotId)
    side = if flipH: 1 else: 0
  SelectedPlayerSpriteBase + (colorIndex * CrewSpriteVariants + variant) * 2 +
    side

proc spriteDefinitionIndex(
  defs: openArray[SpriteDefinition],
  spriteId: int
): int =
  ## Returns the cache index for one sprite definition.
  for i in 0 ..< defs.len:
    if defs[i].spriteId == spriteId:
      return i
  -1

proc protocolObjectId(objectId, objectIdOffset: int): int =
  ## Returns an object id in the selected protocol namespace.
  objectId + objectIdOffset

proc protocolSpriteId(spriteId, spriteIdOffset: int): int =
  ## Returns a sprite id in the selected protocol namespace.
  spriteId + spriteIdOffset

proc addProtocolObject(
  currentIds: var seq[int],
  packet: var seq[uint8],
  objectId,
  x,
  y,
  z,
  layer,
  spriteId: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) =
  ## Adds one namespaced object and tracks it for cleanup.
  let shiftedObjectId = objectId.protocolObjectId(objectIdOffset)
  currentIds.add(shiftedObjectId)
  packet.addObject(
    shiftedObjectId,
    x,
    y,
    z,
    layer,
    spriteId.protocolSpriteId(spriteIdOffset)
  )

proc addSpriteChanged(
  packet: var seq[uint8],
  defs: var seq[SpriteDefinition],
  spriteId, width, height: int,
  pixels: openArray[uint8],
  label: string = "",
  changed = false
) {.measure.} =
  ## Appends a sprite definition when metadata or caller dirtiness changed.
  let index = defs.spriteDefinitionIndex(spriteId)
  if index >= 0:
    if defs[index].width == width and
        defs[index].height == height and
        defs[index].label == label and
        not changed:
      return
    defs[index].width = width
    defs[index].height = height
    defs[index].label = label
  else:
    defs.add SpriteDefinition(
      spriteId: spriteId,
      width: width,
      height: height,
      label: label
    )
  packet.addSprite(spriteId, width, height, pixels, label)

proc applyGlobalViewerMessage*(
  state: var GlobalViewerState,
  message: string
) =
  ## Applies one or more global protocol client messages.
  for item in message.parseSpriteClientMessages():
    case item.kind
    of SpriteClientMouseMoveMessage:
      state.mouseX = item.x
      state.mouseY = item.y
      state.mouseLayer =
        if item.hasLayer:
          item.layer
        else:
          MapLayerId
    of SpriteClientMouseButtonMessage:
      if item.button == 0x01'u8:
        state.mouseDown = item.down
        if state.mouseDown:
          state.mousePressed = true
          state.mousePressX = state.mouseX
          state.mousePressY = state.mouseY
          state.mousePressLayer = state.mouseLayer
          state.clickPending = true
        else:
          state.mouseReleased = true
          state.scrubbingReplay = false
    of SpriteClientChatMessage:
      state.replayCommands.add(item.text)
    of SpriteClientInputMessage:
      discard
    of SpriteClientReadyMessage:
      discard
    of SpriteClientDebugSpriteMessage:
      discard

proc applyPlayerViewerMessage*(
  state: var PlayerViewerState,
  message: string,
  inputMask: var uint8,
  pressedMask: var uint8,
  chatText: var string,
  debugSprites: var seq[uint8]
) =
  ## Applies sprite player protocol input messages.
  for item in message.parseSpriteClientMessages():
    case item.kind
    of SpriteClientChatMessage:
      chatText.add(item.text)
    of SpriteClientDebugSpriteMessage:
      debugSprites.add(item.debugSprites)
    of SpriteClientInputMessage:
      pressedMask = pressedMask or (item.mask and not inputMask)
      inputMask = item.mask
    of SpriteClientMouseMoveMessage, SpriteClientMouseButtonMessage,
        SpriteClientReadyMessage:
      discard

proc isSolid(sprite: Sprite, x, y: int, flipH: bool): bool =
  let srcX = if flipH: sprite.width - 1 - x else: x
  if srcX < 0 or srcX >= sprite.width or y < 0 or y >= sprite.height:
    return false
  sprite.pixels[sprite.spriteIndex(srcX, y)] != TransparentColorIndex

proc buildSpriteProtocolActorSprite(
  sprite: Sprite,
  tint: uint8,
  flipH: bool,
  selected: bool = false
): seq[uint8] {.measure.} =
  ## Builds a tinted actor sprite for the global viewer.
  let
    outWidth = sprite.width + 2
    outHeight = sprite.height + 2
    outline = if selected: 8'u8 else: OutlineColor
  result = newRgbaPixels(outWidth, outHeight)

  proc outIndex(x, y: int): int =
    y * outWidth + x

  if selected:
    for y in -1 .. sprite.height:
      for x in -1 .. sprite.width:
        if sprite.isSolid(x, y, flipH):
          continue
        let adjacent =
          sprite.isSolid(x - 1, y, flipH) or
          sprite.isSolid(x + 1, y, flipH) or
          sprite.isSolid(x, y - 1, flipH) or
          sprite.isSolid(x, y + 1, flipH)
        if adjacent:
          result.putRgbaPixel(outIndex(x + 1, y + 1), outline)

  for y in 0 ..< sprite.height:
    for x in 0 ..< sprite.width:
      let srcX = if flipH: sprite.width - 1 - x else: x
      let colorIndex = sprite.pixels[sprite.spriteIndex(srcX, y)]
      if colorIndex == TransparentColorIndex:
        continue
      result.putRgbaPixel(outIndex(x + 1, y + 1), actorRgba(colorIndex, tint))

proc buildCrewProtocolActorSprite(
  sprite: CrewSprite,
  tint: uint8,
  flipH: bool,
  selected: bool = false
): seq[uint8] {.measure.} =
  ## Builds a selectively tinted true-color crew sprite.
  let
    outWidth = sprite.width + 2
    outHeight = sprite.height + 2
    outline = if selected: 8'u8 else: OutlineColor
  result = newRgbaPixels(outWidth, outHeight)

  proc outIndex(x, y: int): int =
    y * outWidth + x

  if selected:
    for y in -1 .. sprite.height:
      for x in -1 .. sprite.width:
        if sprite.crewSpriteIsSolid(x, y, flipH):
          continue
        let adjacent =
          sprite.crewSpriteIsSolid(x - 1, y, flipH) or
          sprite.crewSpriteIsSolid(x + 1, y, flipH) or
          sprite.crewSpriteIsSolid(x, y - 1, flipH) or
          sprite.crewSpriteIsSolid(x, y + 1, flipH)
        if adjacent:
          result.putRgbaPixel(outIndex(x + 1, y + 1), outline)

  for y in 0 ..< sprite.height:
    for x in 0 ..< sprite.width:
      let srcX = if flipH: sprite.width - 1 - x else: x
      result.putCrewPixel(
        outIndex(x + 1, y + 1),
        sprite,
        srcX,
        y,
        tint
      )

proc buildSpriteProtocolBodySprite(
  bodySprite: CrewSprite,
  tint: uint8
): seq[uint8] {.measure.} =
  ## Builds a selectively tinted true-color dead body sprite.
  buildCrewProtocolActorSprite(bodySprite, tint, false)

proc buildSpriteProtocolRawSprite(sprite: Sprite): seq[uint8] {.measure.} =
  ## Builds a raw global protocol sprite from a game sprite.
  result = newRgbaPixels(sprite.width, sprite.height)
  for y in 0 ..< sprite.height:
    for x in 0 ..< sprite.width:
      let colorIndex = sprite.pixels[sprite.spriteIndex(x, y)]
      if colorIndex != TransparentColorIndex:
        result.putRgbaPixel(sprite.spriteIndex(x, y), colorIndex)

proc buildSpriteProtocolShadowSprite(sprite: Sprite): seq[uint8] {.measure.} =
  ## Builds a shadowed global protocol sprite from a game sprite.
  result = newRgbaPixels(sprite.width, sprite.height)
  for y in 0 ..< sprite.height:
    for x in 0 ..< sprite.width:
      let colorIndex = sprite.pixels[sprite.spriteIndex(x, y)]
      if colorIndex != TransparentColorIndex:
        result.putRgbaPixel(
          sprite.spriteIndex(x, y),
          ShadowMap[colorIndex and 0x0f]
        )

proc buildSolidSprite(
  width, height: int,
  color: uint8
): seq[uint8] {.measure.} =
  ## Builds a solid protocol sprite.
  result = newRgbaPixels(width, height)
  for i in 0 ..< width * height:
    result.putRgbaPixel(i, color)

proc buildSolidSprite(
  width, height: int,
  color: ColorRGBA
): seq[uint8] {.measure.} =
  ## Builds a solid true-color protocol sprite.
  result = newRgbaPixels(width, height)
  for i in 0 ..< width * height:
    result.putRgbaPixel(i, color)

proc buildTransparentBlackSprite(width, height: int): seq[uint8] {.measure.} =
  ## Builds a fully transparent black protocol sprite.
  newRgbaPixels(width, height)

proc addSpritePlayerTickMarker(
  sim: SimServer,
  defs: var seq[SpriteDefinition],
  currentIds: var seq[int],
  packet: var seq[uint8],
  layerId,
  objectIdOffset,
  spriteIdOffset: int
) {.measure.} =
  ## Adds an invisible server tick marker to the player scene.
  let
    spriteId = SpritePlayerTickSpriteId.protocolSpriteId(spriteIdOffset)
    objectId = SpritePlayerTickObjectId.protocolObjectId(objectIdOffset)
  currentIds.add(objectId)
  packet.addSpriteChanged(
    defs,
    spriteId,
    1,
    1,
    buildTransparentBlackSprite(1, 1),
    "tick " & $sim.tickCount,
    changed = true
  )
  packet.addObject(
    objectId,
    0,
    0,
    low(int16),
    layerId,
    spriteId
  )

proc buildIndexedSpritePixels(
  indices: openArray[uint8],
  width,
  height: int,
  fallback: uint8
): seq[uint8] {.measure.} =
  ## Builds an RGBA sprite from palette indices.
  result = newRgbaPixels(width, height)
  for i in 0 ..< width * height:
    let color =
      if i < indices.len:
        indices[i]
      else:
        fallback
    result.putRgbaPixel(i, color)

proc buildVoteBorderSprite(width, height: int): seq[uint8] {.measure.} =
  ## Builds a single-color voting cursor border sprite.
  result = newRgbaPixels(width, height)
  if width <= 0 or height <= 0:
    return
  for x in 0 ..< width:
    result.putRgbaPixel(x, 2'u8)
    result.putRgbaPixel((height - 1) * width + x, 2'u8)
  for y in 0 ..< height:
    result.putRgbaPixel(y * width, 2'u8)
    result.putRgbaPixel(y * width + width - 1, 2'u8)

proc buildVoteMarkerSprite(colorIndex: int): seq[uint8] {.measure.} =
  ## Builds the current-voter marker sprite.
  result = newRgbaPixels(2, 1)
  let color = PlayerColorPalette[colorIndex]
  result.putRgbaPixel(0, color)
  result.putRgbaPixel(1, color)

proc buildVoteDotSprite(colorIndex: int): seq[uint8] {.measure.} =
  ## Builds a compact vote marker sprite.
  result = newRgbaPixels(2, 1)
  let color = PlayerColorPalette[colorIndex]
  result.putRgbaPixel(0, color)
  result.putRgbaPixel(1, color)

proc buildImposterBarSprite(
  cooldown, maxCooldown: int
): seq[uint8] {.measure.} =
  ## Builds the global-only impostor cooldown indicator sprite.
  result = newRgbaPixels(ImposterBarWidth, ImposterBarHeight)
  for i in 0 ..< ImposterBarWidth * ImposterBarHeight:
    result.putRgbaPixel(i, ImposterBarBackgroundColor)
  let filled =
    if maxCooldown <= 0 or cooldown <= 0:
      ImposterBarWidth
    else:
      let remaining = clamp(cooldown, 0, maxCooldown)
      let ready = maxCooldown - remaining
      clamp((ready * ImposterBarWidth) div maxCooldown, 0, ImposterBarWidth)
  for y in 0 ..< ImposterBarHeight:
    for x in 0 ..< filled:
      result.putRgbaPixel(y * ImposterBarWidth + x, ImposterBarReadyColor)

proc buildTrailDotSprite(colorIndex: int): seq[uint8] {.measure.} =
  ## Builds one global-only player trail dot sprite.
  result = newRgbaPixels(TrailDotSize, TrailDotSize)
  let color = PlayerColorPalette[colorIndex]
  for i in 0 ..< TrailDotSize * TrailDotSize:
    result.putRgbaPixel(i, color)

proc buildMapSpritePixels(sim: SimServer): seq[uint8] {.measure.} =
  ## Returns the true-color map pixels for a global protocol sprite.
  if sim.mapRgba.len == sim.gameMap.width * sim.gameMap.height * 4:
    return sim.mapRgba
  result = newRgbaPixels(sim.gameMap.width, sim.gameMap.height)
  for i in 0 ..< sim.mapPixels.len:
    result.putRgbaPixel(i, sim.mapPixels[i])

proc buildMapViewSpritePixels(
  sim: SimServer,
  cameraX,
  cameraY: int
): seq[uint8] {.measure.} =
  ## Returns true-color map pixels for one player camera viewport.
  result = newRgbaPixels(ScreenWidth, ScreenHeight)
  let hasRgba = sim.mapRgba.len == sim.gameMap.width * sim.gameMap.height * 4
  for sy in 0 ..< ScreenHeight:
    for sx in 0 ..< ScreenWidth:
      let
        mx = cameraX + sx
        my = cameraY + sy
        dstIndex = sy * ScreenWidth + sx
      if mx < 0 or my < 0 or
          mx >= sim.gameMap.width or my >= sim.gameMap.height:
        result.putRgbaPixel(dstIndex, SpaceColor)
        continue
      let srcIndex = my * sim.gameMap.width + mx
      if hasRgba:
        let
          srcOffset = srcIndex * 4
          dstOffset = dstIndex * 4
        result[dstOffset] = sim.mapRgba[srcOffset]
        result[dstOffset + 1] = sim.mapRgba[srcOffset + 1]
        result[dstOffset + 2] = sim.mapRgba[srcOffset + 2]
        result[dstOffset + 3] = sim.mapRgba[srcOffset + 3]
      else:
        result.putRgbaPixel(dstIndex, sim.mapPixels[srcIndex])

proc buildWalkabilitySpritePixels(sim: SimServer): seq[uint8] {.measure.} =
  ## Returns a binary RGBA walkability mask for sprite agents.
  result = newSeq[uint8](sim.gameMap.width * sim.gameMap.height * 4)
  for i in 0 ..< sim.gameMap.width * sim.gameMap.height:
    let offset = i * 4
    let walkable =
      if i < sim.walkMask.len:
        sim.walkMask[i]
      elif i < sim.wallMask.len:
        not sim.wallMask[i]
      else:
        true
    if walkable:
      result[offset] = 255
      result[offset + 1] = 255
      result[offset + 2] = 255
      result[offset + 3] = 255

proc mapMarkerSpriteId(index: int): int =
  ## Returns the stable sprite id for one static map marker.
  MapMarkerSpriteBase + index

proc mapMarkerObjectId(index: int): int =
  ## Returns the stable object id for one static map marker.
  MapMarkerObjectBase + index

proc markerResourceLabel(name, fallback: string): string =
  ## Returns the resource label for one static map marker.
  if name.len > 0:
    name
  else:
    fallback

proc addMapMarker(
  packet: var seq[uint8],
  spriteDefs: var seq[SpriteDefinition],
  index,
  x,
  y,
  width,
  height: int,
  label: string,
  layerId = MapLayerId,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds one invisible labeled marker object to the map layer.
  let
    spriteId = mapMarkerSpriteId(index).protocolSpriteId(spriteIdOffset)
    objectId = mapMarkerObjectId(index).protocolObjectId(objectIdOffset)
  packet.addSpriteChanged(
    spriteDefs,
    spriteId,
    width,
    height,
    buildTransparentBlackSprite(width, height),
    label
  )
  packet.addObject(objectId, x, y, MapMarkerZ, layerId, spriteId)

proc addMapMarkers(
  sim: SimServer,
  spriteDefs: var seq[SpriteDefinition],
  packet: var seq[uint8],
  layerId = MapLayerId,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds invisible task, vent, and room markers for sprite agents.
  var index = 0
  for task in sim.tasks:
    packet.addMapMarker(
      spriteDefs,
      index,
      task.x,
      task.y,
      task.w,
      task.h,
      markerResourceLabel(task.resourceName, "task"),
      layerId,
      objectIdOffset,
      spriteIdOffset
    )
    inc index
  for vent in sim.vents:
    packet.addMapMarker(
      spriteDefs,
      index,
      vent.x,
      vent.y,
      vent.w,
      vent.h,
      markerResourceLabel(vent.resourceName, "vent"),
      layerId,
      objectIdOffset,
      spriteIdOffset
    )
    inc index
  for room in sim.rooms:
    packet.addMapMarker(
      spriteDefs,
      index,
      room.x,
      room.y,
      room.w,
      room.h,
      "Room " & room.name,
      layerId,
      objectIdOffset,
      spriteIdOffset
    )
    inc index

proc buildPlayerShadowSprite(
  sim: SimServer,
  cameraX, cameraY: int
): seq[uint8] {.measure.} =
  ## Builds one screen-sized transparent shadow overlay.
  result = newRgbaPixels(ScreenWidth, ScreenHeight)
  for sy in 0 ..< ScreenHeight:
    for sx in 0 ..< ScreenWidth:
      let
        screenIndex = sy * ScreenWidth + sx
        mx = cameraX + sx
        my = cameraY + sy
      if not sim.shadowBuf[screenIndex]:
        continue
      if mx < 0 or my < 0 or mx >= MapWidth or my >= MapHeight:
        continue
      let mapPixel = mapIndex(mx, my)
      if sim.wallMask[mapPixel]:
        continue
      result.putRgbaPixel(
        screenIndex,
        ShadowMap[sim.mapPixels[mapPixel] and 0x0f]
      )

proc putTextSpritePixel(
  pixels: var seq[uint8],
  width, height, x, y: int,
  color: uint8
) =
  ## Puts one protocol pixel into a text sprite.
  if x < 0 or y < 0 or x >= width or y >= height:
    return
  pixels.putRgbaPixel(y * width + x, color)

proc blitGlyph(
  target: var seq[uint8],
  targetWidth, targetHeight: int,
  glyph: PixelGlyph,
  baseX, baseY: int,
  color: uint8
) =
  ## Blits a single-color glyph into protocol pixels.
  for y in 0 ..< glyph.height:
    for x in 0 ..< glyph.width:
      if not glyph.glyphPixel(x, y):
        continue
      target.putTextSpritePixel(
        targetWidth,
        targetHeight,
        baseX + x,
        baseY + y,
        color
      )

proc blitSmallText(
  game: SimServer,
  target: var seq[uint8],
  targetWidth, targetHeight: int,
  text: string,
  baseX, baseY: int,
  color: uint8
) =
  ## Blits small text into protocol pixels.
  var x = baseX
  for ch in text:
    let glyph = game.asciiSprites.glyphAt(ch)
    target.blitGlyph(
      targetWidth,
      targetHeight,
      glyph,
      x,
      baseY,
      color
    )
    x += game.asciiSprites.glyphAdvance(ch)

proc buildSpriteProtocolTextSprite(
  game: SimServer,
  lines: openArray[string],
  color: uint8,
  struck = false
): tuple[width, height: int, pixels: seq[uint8]] {.measure.} =
  ## Builds a transparent multi-line text sprite.
  result.width = 1
  for line in lines:
    result.width = max(result.width, game.asciiSprites.textWidth(line))
  result.height = max(1, lines.len * TextLineHeight)
  result.pixels = newRgbaPixels(result.width, result.height)
  for lineIndex, line in lines:
    let baseY = lineIndex * TextLineHeight
    var baseX = 0
    for ch in line:
      let glyph = game.asciiSprites.glyphAt(ch)
      result.pixels.blitGlyph(
        result.width,
        result.height,
        glyph,
        baseX,
        baseY,
        color
      )
      baseX += game.asciiSprites.glyphAdvance(ch)
    if struck:
      let lineY = baseY + 3
      for x in 0 ..< game.asciiSprites.textWidth(line):
        result.pixels.putTextSpritePixel(
          result.width,
          result.height,
          x,
          lineY,
          3'u8
        )

proc textLabel(lines: openArray[string]): string =
  ## Returns a debugger label for one rendered text sprite.
  for i, line in lines:
    if i > 0:
      result.add("\n")
    result.add(line)

proc centeredTextX(sim: SimServer, text: string): int =
  ## Returns the centered x position for interstitial text.
  (ScreenWidth - sim.asciiSprites.textWidth(text)) div 2

proc capitalizedColorName(color: uint8): string =
  ## Returns one player color with an initial capital letter.
  result = playerColorText(color)
  if result.len > 0 and result[0] >= 'a' and result[0] <= 'z':
    result[0] = char(ord(result[0]) - ord('a') + ord('A'))

proc meetingCallName(sim: SimServer, playerIndex: int): string =
  ## Returns the display color for one meeting-call player.
  if playerIndex >= 0 and playerIndex < sim.players.len:
    return capitalizedColorName(sim.players[playerIndex].color)
  "Someone"

proc meetingCallBodyName(sim: SimServer): string =
  ## Returns the display color for the reported body.
  let body = sim.meetingCallBodyIndex()
  if body >= 0:
    return capitalizedColorName(sim.players[body].color)
  if sim.voteState.bodyColor != 255'u8:
    return capitalizedColorName(sim.voteState.bodyColor)
  "Someone"

proc meetingCallLines(sim: SimServer): seq[string] =
  ## Returns the text lines for the meeting-call interstitial.
  let caller = sim.meetingCallName(sim.meetingCallCallerIndex())
  case sim.voteState.callKind
  of VoteCalledBody:
    let body = sim.meetingCallBodyName()
    result.add(caller & " reported")
    if body == "Someone":
      result.add("a body")
    else:
      result.add(body & "'s body")
  of VoteCalledButton:
    result.add(caller & " pressed")
    result.add("the button")
  of VoteCalledUnknown:
    result.add(caller & " called")
    result.add("a meeting")

proc addTextItem(
  items: var seq[ProtocolTextItem],
  x, y: int,
  lines: openArray[string],
  label = "",
  color = ProtocolTextColor,
  struck = false
) =
  ## Adds one text sprite placement to an interstitial layout.
  let index = items.len
  var item = ProtocolTextItem(
    spriteId: ProtocolTextSpriteBase + index,
    objectId: ProtocolTextObjectBase + index,
    x: x,
    y: y,
    z: ProtocolTextZ,
    color: color,
    struck: struck
  )
  for line in lines:
    item.lines.add(line)
  item.label =
    if label.len > 0:
      label
    else:
      textLabel(lines)
  items.add(item)

proc addVisibleVoteChatText(
  sim: SimServer,
  items: var seq[ProtocolTextItem],
  chatY: int
) {.measure.} =
  ## Adds separate text sprites for visible voting chat messages.
  let
    chatH = ScreenHeight - chatY - 3
    textX = VoteChatTextX
  if chatH <= 0:
    return
  var
    visible: seq[int] = @[]
    usedH = 0
  for i in countdown(sim.chatMessages.high, 0):
    let messageH = sim.asciiSprites.chatMessageHeight(sim.chatMessages[i].text)
    if usedH + messageH > chatH - 2:
      break
    visible.add(i)
    usedH += messageH
  var rowY = chatY + 1
  for j in countdown(visible.high, 0):
    let
      message = sim.chatMessages[visible[j]]
      lineCount = sim.asciiSprites.chatLineCount(message.text)
      messageH = sim.asciiSprites.chatMessageHeight(message.text)
    var lines: seq[string] = @[]
    for lineIndex in 0 ..< lineCount:
      lines.add(sim.asciiSprites.sliceChatLine(message.text, lineIndex))
    items.addTextItem(textX, rowY, lines, message.text)
    rowY += messageH

proc addVisibleVoteChatIcons(
  sim: SimServer,
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer: int,
  chatY: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds separate player sprites for visible voting chat speakers.
  let
    chatH = ScreenHeight - chatY - 3
    iconX = VoteChatIconX
  if chatH <= 0:
    return
  var
    visible: seq[int] = @[]
    usedH = 0
  for i in countdown(sim.chatMessages.high, 0):
    let messageH = sim.asciiSprites.chatMessageHeight(sim.chatMessages[i].text)
    if usedH + messageH > chatH - 2:
      break
    visible.add(i)
    usedH += messageH
  var rowY = chatY + 1
  for j in countdown(visible.high, 0):
    let
      message = sim.chatMessages[visible[j]]
      lineCount = sim.asciiSprites.chatLineCount(message.text)
      messageH = sim.asciiSprites.chatMessageHeight(message.text)
      iconY = rowY + max(0, (lineCount * TextLineHeight - VoteActorSize) div 2)
      objectId = ProtocolChatIconObjectBase + j
      spriteId = crewPlayerSpriteId(
        playerColorIndex(message.color),
        message.slotId,
        false
      )
    currentIds.addProtocolObject(
      packet,
      objectId,
      iconX - 1,
      iconY - 1,
      ProtocolChatIconZ,
      layer,
      spriteId,
      objectIdOffset,
      spriteIdOffset
    )
    rowY += messageH

proc gameInfoTextLines(sim: SimServer): seq[string] =
  ## Returns the settings text for the pregame info screen.
  result.add("GAME INFO")
  result.add("KILL COOLDOWN " & $sim.config.killCooldownTicks & "T")
  result.add("TASKS " & $sim.config.tasksPerPlayer & " EACH")
  result.add("VOTE TIMER " & $sim.config.voteTimerTicks & "T")
  if sim.config.maxTicks > 0:
    result.add("GAME TIMER " & $sim.config.maxTicks & "T")
  else:
    result.add("GAME TIMER NONE")

proc addGameInfoTextItems(
  sim: SimServer,
  items: var seq[ProtocolTextItem]
) =
  ## Adds centered text items for the pregame info screen.
  let
    lines = sim.gameInfoTextLines()
    gap = 4
    lineH = TextLineHeight + gap
    blockH = lines.len * lineH - gap
  var y = (ScreenHeight - blockH) div 2
  for line in lines:
    items.addTextItem(sim.centeredTextX(line), y, [line])
    y += lineH

proc interstitialTextItems(
  sim: SimServer,
  playerIndex: int
): seq[ProtocolTextItem] =
  ## Returns separate text sprites for one interstitial player screen.
  case sim.phase
  of Lobby:
    let needed = max(0, sim.config.minPlayers - sim.players.len)
    if needed > 0:
      result.addTextItem(sim.centeredTextX("WAITING"), 4, ["WAITING"])
      result.addTextItem(sim.centeredTextX("NEED MORE!"), 14, ["NEED MORE!"])
    else:
      result.addTextItem(sim.centeredTextX("GAME"), 2, ["GAME"])
      result.addTextItem(sim.centeredTextX("STARTING"), 11, ["STARTING"])
      let
        seconds = sim.lobbyStartSecondsRemaining()
        line = "IN " & $seconds
      if seconds > 0:
        result.addTextItem(sim.centeredTextX(line), 20, [line])
  of GameInfo:
    sim.addGameInfoTextItems(result)
  of Playing:
    if playerIndex < 0 or playerIndex >= sim.players.len:
      let
        gap = 10
        blockH = sim.asciiSprites.height * 2 + gap
        startY = (ScreenHeight - blockH) div 2
      result.addTextItem(sim.centeredTextX("GAME IN"), startY, ["GAME IN"])
      result.addTextItem(
        sim.centeredTextX("PROGRESS"),
        startY + sim.asciiSprites.height + gap,
        ["PROGRESS"]
      )
  of RoleReveal:
    let viewerIsImp =
      playerIndex >= 0 and playerIndex < sim.players.len and
      sim.players[playerIndex].role == Imposter
    let title = if viewerIsImp: "IMPS" else: "CREWMATE"
    result.addTextItem(
      (ScreenWidth - sim.asciiSprites.textWidth(title)) div 2,
      14,
      [title]
    )
  of MeetingCall:
    let lines = sim.meetingCallLines()
    for i, line in lines:
      result.addTextItem(
        sim.centeredTextX(line),
        MeetingCallTextY + i * TextLineHeight,
        [line]
      )
  of Voting:
    let n = sim.players.len
    if n > 0:
      let
        cellH = VoteCellH
        cols = min(n, VoteColsMax)
        rows = (n + cols - 1) div cols
        startY = VoteStartY
        skipW = VoteSkipW
        skipY = startY + rows * cellH + 1
        skipX = (ScreenWidth - skipW) div 2
      result.addTextItem(skipX, skipY, ["SKIP"])
      sim.addVisibleVoteChatText(result, skipY + VoteSkipCursorH + 2)
  of VoteResult:
    let ej = sim.voteState.ejectedPlayer
    if ej < 0 or ej >= sim.players.len:
      result.addTextItem(sim.centeredTextX("NO ONE") + 3, 54, ["NO ONE"])
      result.addTextItem(sim.centeredTextX("DIED") + 3, 64, ["DIED"])
    else:
      result.addTextItem(sim.centeredTextX("WAS KILLED"), 46, ["WAS KILLED"])
  of GameOver:
    let title =
      if sim.timeLimitReached:
        "DRAW"
      elif sim.winner == Crewmate:
        "CREW WINS"
      else:
        "IMPS WIN"
    let
      titleW = sim.asciiSprites.textWidth(title)
      titleX = (ScreenWidth - titleW) div 2
      rowH = 14
      rowsPerCol = 8
      colW = ScreenWidth div 2
      textOffsetX = 19
      startY = 16
    result.addTextItem(titleX, 2, [title])
    for i in 0 ..< sim.players.len:
      let
        p = sim.players[i]
        col = i div rowsPerCol
        row = i mod rowsPerCol
        baseX = min(col, 1) * colW
        textX = baseX + textOffsetX
        textY = startY + row * rowH + (rowH - 6) div 2
        roleText = if p.role == Imposter: "IMP" else: "CREW"
      result.addTextItem(textX, textY, [roleText], struck = not p.alive)

proc addProtocolTextSprites(
  sim: SimServer,
  spriteDefs: var seq[SpriteDefinition],
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer: int,
  playerIndex: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds separate text sprites for current interstitial text.
  let items = sim.interstitialTextItems(playerIndex)
  for item in items:
    let
      objectId = item.objectId.protocolObjectId(objectIdOffset)
      spriteId = item.spriteId.protocolSpriteId(spriteIdOffset)
    let text = sim.buildSpriteProtocolTextSprite(
      item.lines,
      item.color,
      item.struck
    )
    currentIds.add(objectId)
    packet.addSpriteChanged(
      spriteDefs,
      spriteId,
      text.width,
      text.height,
      text.pixels,
      item.label,
      changed = item.struck or item.color != ProtocolTextColor
    )
    packet.addObject(
      objectId,
      item.x,
      item.y,
      item.z,
      layer,
      spriteId
    )

proc addProtocolChatSprites(
  sim: SimServer,
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds separate player sprites for protocol-rendered voting chat.
  if sim.phase != Voting:
    return
  let n = sim.players.len
  if n == 0:
    return
  let
    cellH = VoteCellH
    cols = min(n, VoteColsMax)
    rows = (n + cols - 1) div cols
    startY = VoteStartY
    skipY = startY + rows * cellH + 1
  sim.addVisibleVoteChatIcons(
    currentIds,
    packet,
    layer,
    skipY + VoteSkipCursorH + 2,
    objectIdOffset,
    spriteIdOffset
  )

proc buildVoteProgressSprite(sim: SimServer): seq[uint8] {.measure.} =
  ## Builds the voting countdown bar as a small sprite.
  const
    BarW = ScreenWidth - 4
    BarH = 2
  result = newRgbaPixels(BarW, BarH)
  let
    timer =
      if sim.voteState.finalizeTimer > 0:
        sim.voteState.finalizeTimer
      else:
        sim.voteState.voteTimer
    total =
      if sim.voteState.finalizeTimer > 0:
        VoteFinalizeTicks
      else:
        sim.config.voteTimerTicks
    filled =
      if total > 0:
        clamp(
          timer * BarW div total,
          0,
          BarW
        )
      else:
        0
  for y in 0 ..< BarH:
    for x in 0 ..< BarW:
      result.putRgbaPixel(y * BarW + x, if x < filled: 10'u8 else: 1'u8)

proc addProtocolVoteUiSprites(
  sim: SimServer,
  spriteDefs: var seq[SpriteDefinition],
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer,
  playerIndex: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds cursor, vote dots, chat background, and timer sprites.
  if sim.phase != Voting:
    return
  let n = sim.players.len
  if n == 0:
    return
  let
    cellW = VoteCellW
    cellH = VoteCellH
    cols = min(n, VoteColsMax)
    rows = (n + cols - 1) div cols
    totalW = cols * cellW
    startX = (ScreenWidth - totalW) div 2
    startY = VoteStartY
    skipY = startY + rows * cellH + 1
    skipW = VoteSkipW
    skipX = (ScreenWidth - skipW) div 2
  if playerIndex >= 0 and playerIndex < sim.voteState.cursor.len:
    let cursor = sim.voteState.cursor[playerIndex]
    if cursor >= 0 and cursor < n:
      let
        cx = startX + (cursor mod cols) * cellW
        cy = startY + (cursor div cols) * cellH
      currentIds.addProtocolObject(
        packet,
        SpritePlayerVoteCursorObjectId,
        cx,
        cy - 1,
        ProtocolVoteIconZ - 1,
        layer,
        SpritePlayerVoteCursorSpriteId,
        objectIdOffset,
        spriteIdOffset
      )
    elif cursor == n:
      currentIds.addProtocolObject(
        packet,
        SpritePlayerVoteCursorObjectId,
        skipX - 1,
        skipY - 1,
        ProtocolVoteIconZ - 1,
        layer,
        SpritePlayerVoteSkipCursorSpriteId,
        objectIdOffset,
        spriteIdOffset
      )
    let
      cx = startX + (playerIndex mod cols) * cellW
      cy = startY + (playerIndex div cols) * cellH
      colorIndex = playerColorIndex(sim.players[playerIndex].color)
    currentIds.addProtocolObject(
      packet,
      SpritePlayerVoteSelfMarkerObjectId,
      cx + cellW div 2 - 1,
      cy - 2,
      ProtocolVoteIconZ + 1,
      layer,
      SpritePlayerVoteMarkerSpriteBase + colorIndex,
      objectIdOffset,
      spriteIdOffset
    )
  for target in 0 ..< n:
    let
      cx = startX + (target mod cols) * cellW
      cy = startY + (target div cols) * cellH
    var voterRow = 0
    for voter in 0 ..< n:
      if sim.voteState.votes[voter] == target:
        let
          dotX = cx + 1 + (voterRow mod 8) * 2
          dotY = cy + VoteActorSize + 1 + (voterRow div 8)
          objectId = SpritePlayerVoteDotObjectBase + target * MaxPlayers +
            voter
          colorIndex = playerColorIndex(sim.players[voter].color)
        currentIds.addProtocolObject(
          packet,
          objectId,
          dotX,
          dotY,
          ProtocolVoteIconZ + 1,
          layer,
          SpritePlayerVoteDotSpriteBase + colorIndex,
          objectIdOffset,
          spriteIdOffset
        )
        inc voterRow
  var skipVoterRow = 0
  for voter in 0 ..< n:
    if sim.voteState.votes[voter] == -2:
      let
        dotX = skipX + skipW + 2 + (skipVoterRow mod 8) * 2
        dotY = skipY + (skipVoterRow div 8)
        objectId = SpritePlayerVoteSkipDotObjectBase + voter
        colorIndex = playerColorIndex(sim.players[voter].color)
      currentIds.addProtocolObject(
        packet,
        objectId,
        dotX,
        dotY,
        ProtocolVoteIconZ + 1,
        layer,
        SpritePlayerVoteDotSpriteBase + colorIndex,
        objectIdOffset,
        spriteIdOffset
      )
      inc skipVoterRow
  let
    chatY = skipY + VoteSkipCursorH + 2
    chatH = ScreenHeight - chatY - 3
  if chatH > 0:
    currentIds.add(
      SpritePlayerVoteChatBgObjectId.protocolObjectId(objectIdOffset)
    )
    packet.addSpriteChanged(
      spriteDefs,
      SpritePlayerVoteChatBgSpriteId.protocolSpriteId(spriteIdOffset),
      ScreenWidth,
      chatH,
      buildSolidSprite(ScreenWidth, chatH, 0'u8),
      "vote chat background"
    )
    packet.addObject(
      SpritePlayerVoteChatBgObjectId.protocolObjectId(objectIdOffset),
      0,
      chatY,
      ProtocolVoteIconZ - 2,
      layer,
      SpritePlayerVoteChatBgSpriteId.protocolSpriteId(spriteIdOffset)
    )
  currentIds.add(
    SpritePlayerVoteProgressObjectId.protocolObjectId(objectIdOffset)
  )
  packet.addSpriteChanged(
    spriteDefs,
    SpritePlayerVoteProgressSpriteId.protocolSpriteId(spriteIdOffset),
    ScreenWidth - 4,
    2,
    sim.buildVoteProgressSprite(),
    "vote timer",
    changed = true
  )
  packet.addObject(
    SpritePlayerVoteProgressObjectId.protocolObjectId(objectIdOffset),
    2,
    ScreenHeight - 2,
    ProtocolVoteIconZ + 2,
    layer,
    SpritePlayerVoteProgressSpriteId.protocolSpriteId(spriteIdOffset)
  )

proc addProtocolVoteActorSprites(
  sim: SimServer,
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds separate player and body sprites for the voting candidate grid.
  if sim.phase != Voting:
    return
  let n = sim.players.len
  if n == 0:
    return
  let
    cellW = VoteCellW
    cellH = VoteCellH
    cols = min(n, VoteColsMax)
    totalW = cols * cellW
    startX = (ScreenWidth - totalW) div 2
    startY = VoteStartY
  for idx in 0 ..< n:
    let
      player = sim.players[idx]
      col = idx mod cols
      row = idx div cols
      cx = startX + col * cellW
      cy = startY + row * cellH
      spriteX = cx + (cellW - CrewSpriteSize) div 2
      spriteY = cy + 1
      colorIndex = playerColorIndex(player.color)
      objectId = ProtocolVoteIconObjectBase + idx
      spriteId =
        if player.alive:
          crewPlayerSpriteId(colorIndex, player.joinOrder, false)
        else:
          bodySpriteId(colorIndex, player.joinOrder)
    currentIds.addProtocolObject(
      packet,
      objectId,
      spriteX - 1,
      spriteY - 1,
      ProtocolVoteIconZ,
      layer,
      spriteId,
      objectIdOffset,
      spriteIdOffset
    )

proc playerIconSpriteId(player: Player): int =
  ## Returns the default right-facing player icon sprite id.
  crewPlayerSpriteId(
    playerColorIndex(player.color),
    player.joinOrder,
    false
  )

proc addProtocolLobbyActorSprites(
  sim: SimServer,
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds separate player sprites for the lobby interstitial.
  if sim.phase != Lobby:
    return
  let
    cols = max(1, min(sim.players.len, 6))
    cellW = CrewSpriteSize + 2
    cellH = CrewSpriteSize + 2
    totalW = cols * cellW
    startX = (ScreenWidth - totalW) div 2
    startY = sim.lobbyIconStartY()
  for i in 0 ..< sim.players.len:
    let
      col = i mod cols
      row = i div cols
      sx = startX + col * cellW
      sy = startY + row * cellH
      objectId = ProtocolLobbyIconObjectBase + i
    currentIds.addProtocolObject(
      packet,
      objectId,
      sx - 1,
      sy - 1,
      ProtocolVoteIconZ,
      layer,
      sim.players[i].playerIconSpriteId(),
      objectIdOffset,
      spriteIdOffset
    )

proc addProtocolRoleRevealActorSprites(
  sim: SimServer,
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer,
  playerIndex: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds separate player sprites for the role reveal interstitial.
  if sim.phase != RoleReveal:
    return
  let viewerIsImp =
    playerIndex >= 0 and playerIndex < sim.players.len and
    sim.players[playerIndex].role == Imposter
  var shown: seq[int] = @[]
  if viewerIsImp:
    for i in 0 ..< sim.players.len:
      if sim.players[i].role == Imposter:
        shown.add(i)
  else:
    for i in 0 ..< sim.players.len:
      shown.add(i)
  if shown.len == 0:
    return
  let
    cellW = CrewSpriteSize + 2
    cellH = CrewSpriteSize + 4
    cols = min(shown.len, 7)
    totalW = cols * cellW
    startX = (ScreenWidth - totalW) div 2
    startY = 42
  for slot in 0 ..< shown.len:
    let
      playerIdx = shown[slot]
      col = slot mod cols
      row = slot div cols
      spriteX = startX + col * cellW + (cellW - CrewSpriteSize) div 2
      spriteY = startY + row * cellH
      objectId = ProtocolRoleIconObjectBase + slot
    currentIds.addProtocolObject(
      packet,
      objectId,
      spriteX - 1,
      spriteY - 1,
      ProtocolVoteIconZ,
      layer,
      sim.players[playerIdx].playerIconSpriteId(),
      objectIdOffset,
      spriteIdOffset
    )

proc addProtocolVoteResultActorSprites(
  sim: SimServer,
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds separate player sprites for vote result interstitials.
  if sim.phase != VoteResult:
    return
  let ejected = sim.voteState.ejectedPlayer
  if ejected < 0 or ejected >= sim.players.len:
    return
  let
    sx = ScreenWidth div 2 - CrewSpriteSize div 2
    sy = ScreenHeight div 2 - CrewSpriteSize div 2
  currentIds.addProtocolObject(
    packet,
    ProtocolResultIconObjectBase,
    sx - 1,
    sy - 1,
    ProtocolVoteIconZ,
    layer,
    sim.players[ejected].playerIconSpriteId(),
    objectIdOffset,
    spriteIdOffset
  )

proc addProtocolMeetingCallActorSprites(
  sim: SimServer,
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds caller, reported body, and button sprites for meeting calls.
  if sim.phase != MeetingCall:
    return
  let caller = sim.meetingCallCallerIndex()
  if caller >= 0:
    let
      objectId = ProtocolMeetingIconObjectBase
    currentIds.addProtocolObject(
      packet,
      objectId,
      MeetingCallLeftIconX - 1,
      MeetingCallIconY - 1,
      ProtocolVoteIconZ,
      layer,
      sim.players[caller].playerIconSpriteId(),
      objectIdOffset,
      spriteIdOffset
    )
  case sim.voteState.callKind
  of VoteCalledBody:
    let body = sim.meetingCallBodyIndex()
    if body >= 0:
      let
        colorIndex = playerColorIndex(sim.players[body].color)
        spriteId = bodySpriteId(colorIndex, sim.players[body].joinOrder)
      currentIds.addProtocolObject(
        packet,
        ProtocolMeetingIconObjectBase + 1,
        MeetingCallRightIconX - 1,
        MeetingCallIconY - 1,
        ProtocolVoteIconZ,
        layer,
        spriteId,
        objectIdOffset,
        spriteIdOffset
      )
    elif sim.voteState.bodyColor != 255'u8:
      let spriteId = bodySpriteId(
        playerColorIndex(sim.voteState.bodyColor),
        sim.voteState.bodySlotId
      )
      currentIds.addProtocolObject(
        packet,
        ProtocolMeetingIconObjectBase + 1,
        MeetingCallRightIconX - 1,
        MeetingCallIconY - 1,
        ProtocolVoteIconZ,
        layer,
        spriteId,
        objectIdOffset,
        spriteIdOffset
      )
  of VoteCalledButton:
    currentIds.addProtocolObject(
      packet,
      ProtocolMeetingIconObjectBase + 1,
      MeetingCallButtonX,
      MeetingCallButtonY,
      ProtocolVoteIconZ,
      layer,
      SpritePlayerMeetingButtonSpriteId,
      objectIdOffset,
      spriteIdOffset
    )
  of VoteCalledUnknown:
    discard

proc addProtocolGameOverActorSprites(
  sim: SimServer,
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds separate player sprites for the game over interstitial.
  if sim.phase != GameOver:
    return
  let
    rowH = 14
    rowsPerCol = 8
    colW = ScreenWidth div 2
    iconOffsetX = 4
    startY = 16
  for i in 0 ..< sim.players.len:
    let
      player = sim.players[i]
      col = i div rowsPerCol
      row = i mod rowsPerCol
      baseX = min(col, 1) * colW
      y = startY + row * rowH
      iconX = baseX + iconOffsetX
      iconY = y + (rowH - CrewSpriteSize) div 2
      objectId = ProtocolGameOverIconObjectBase + i
    currentIds.addProtocolObject(
      packet,
      objectId,
      iconX - 1,
      iconY - 1,
      ProtocolVoteIconZ,
      layer,
      player.playerIconSpriteId(),
      objectIdOffset,
      spriteIdOffset
    )

proc addProtocolInterstitialActorSprites(
  sim: SimServer,
  spriteDefs: var seq[SpriteDefinition],
  currentIds: var seq[int],
  packet: var seq[uint8],
  layer,
  playerIndex: int,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds separate actor sprites for sprite protocol interstitials.
  case sim.phase
  of Lobby:
    sim.addProtocolLobbyActorSprites(
      currentIds,
      packet,
      layer,
      objectIdOffset,
      spriteIdOffset
    )
  of RoleReveal:
    sim.addProtocolRoleRevealActorSprites(
      currentIds,
      packet,
      layer,
      playerIndex,
      objectIdOffset,
      spriteIdOffset
    )
  of MeetingCall:
    sim.addProtocolMeetingCallActorSprites(
      currentIds,
      packet,
      layer,
      objectIdOffset,
      spriteIdOffset
    )
  of Voting:
    sim.addProtocolVoteUiSprites(
      spriteDefs,
      currentIds,
      packet,
      layer,
      playerIndex,
      objectIdOffset,
      spriteIdOffset
    )
    sim.addProtocolVoteActorSprites(
      currentIds,
      packet,
      layer,
      objectIdOffset,
      spriteIdOffset
    )
    sim.addProtocolChatSprites(
      currentIds,
      packet,
      layer,
      objectIdOffset,
      spriteIdOffset
    )
  of VoteResult:
    sim.addProtocolVoteResultActorSprites(
      currentIds,
      packet,
      layer,
      objectIdOffset,
      spriteIdOffset
    )
  of GameOver:
    sim.addProtocolGameOverActorSprites(
      currentIds,
      packet,
      layer,
      objectIdOffset,
      spriteIdOffset
    )
  of Playing, GameInfo:
    discard

proc hasInterstitialFrame(sim: SimServer): bool =
  ## Returns true when the global viewer should show a neutral game screen.
  sim.phase in {
    Lobby, MeetingCall, Voting, VoteResult, GameOver, RoleReveal, GameInfo
  }

proc addSpriteProtocolInterstitialSprites(
  sim: SimServer,
  spriteDefs: var seq[SpriteDefinition],
  packet: var seq[uint8],
  spriteIdOffset = 0
) {.measure.} =
  ## Adds reusable sprites for non-playing screens.
  packet.addSpriteChanged(
    spriteDefs,
    SpritePlayerInterstitialSpriteId.protocolSpriteId(spriteIdOffset),
    ScreenWidth,
    ScreenHeight,
    buildIndexedSpritePixels(
      sim.darkBgPixels,
      ScreenWidth,
      ScreenHeight,
      SpaceColor
    ),
    "interstitial background"
  )
  packet.addSpriteChanged(
    spriteDefs,
    SpritePlayerVoteCursorSpriteId.protocolSpriteId(spriteIdOffset),
    VoteCellW,
    VoteActorSize + 2,
    buildVoteBorderSprite(VoteCellW, VoteActorSize + 2),
    "vote cursor"
  )
  packet.addSpriteChanged(
    spriteDefs,
    SpritePlayerVoteSkipCursorSpriteId.protocolSpriteId(spriteIdOffset),
    VoteSkipCursorW,
    VoteSkipCursorH,
    buildVoteBorderSprite(VoteSkipCursorW, VoteSkipCursorH),
    "vote skip cursor"
  )
  for i in 0 ..< PlayerColors.len:
    packet.addSpriteChanged(
      spriteDefs,
      (SpritePlayerVoteMarkerSpriteBase + i).protocolSpriteId(
        spriteIdOffset
      ),
      2,
      1,
      buildVoteMarkerSprite(i),
      "vote self marker " & playerColorName(i)
    )
    packet.addSpriteChanged(
      spriteDefs,
      (SpritePlayerVoteDotSpriteBase + i).protocolSpriteId(
        spriteIdOffset
      ),
      2,
      1,
      buildVoteDotSprite(i),
      "vote dot " & playerColorName(i)
    )

proc buildSpriteProtocolInit(
  sim: SimServer,
  spriteDefs: var seq[SpriteDefinition]
): seq[uint8] {.measure.} =
  ## Builds the initial global viewer snapshot.
  result = @[]
  result.addU8(0x04)
  let mapPixels = sim.buildMapSpritePixels()
  result.addLayer(MapLayerId, MapLayerType, ZoomableLayerFlag)
  result.addViewport(MapLayerId, sim.gameMap.width, sim.gameMap.height)
  result.addLayer(TopLeftLayerId, TopLeftLayerType, UiLayerFlag)
  result.addViewport(TopLeftLayerId, ScoreboardWidth, ScoreboardHeight)
  result.addLayer(InterstitialLayerId, InterstitialLayerType, UiLayerFlag)
  result.addViewport(InterstitialLayerId, ScreenWidth, ScreenHeight)
  result.addLayer(BottomRightLayerId, BottomRightLayerType, UiLayerFlag)
  result.addViewport(BottomRightLayerId, ScreenWidth, ScreenHeight)
  result.addSpriteChanged(
    spriteDefs,
    MapSpriteId,
    sim.gameMap.width,
    sim.gameMap.height,
    mapPixels,
    "map"
  )
  result.addObject(MapObjectId, 0, 0, low(int16), MapLayerId, MapSpriteId)
  sim.addMapMarkers(spriteDefs, result)
  let taskPixels = buildSpriteProtocolRawSprite(sim.taskIconSprite)
  result.addSpriteChanged(
    spriteDefs,
    TaskSpriteId,
    sim.taskIconSprite.width,
    sim.taskIconSprite.height,
    taskPixels,
    "task bubble"
  )
  result.addSpriteChanged(
    spriteDefs,
    SpritePlayerMeetingButtonSpriteId,
    sim.meetingButtonSprite.width,
    sim.meetingButtonSprite.height,
    buildSpriteProtocolRawSprite(sim.meetingButtonSprite),
    "meeting button"
  )
  for i in 0 ..< PlayerColors.len:
    result.addSpriteChanged(
      spriteDefs,
      TrailDotSpriteBase + i,
      TrailDotSize,
      TrailDotSize,
      buildTrailDotSprite(i),
      "trail " & playerColorName(i)
    )
  sim.addSpriteProtocolInterstitialSprites(spriteDefs, result)
  for i in 0 ..< PlayerColors.len:
    for variant in 0 ..< CrewSpriteVariants:
      let
        crew = sim.crewSprites[variant]
        playerRight = buildCrewProtocolActorSprite(
          crew,
          PlayerColors[i],
          false
        )
        playerLeft = buildCrewProtocolActorSprite(
          crew,
          PlayerColors[i],
          true
        )
        selectedPlayerRight = buildCrewProtocolActorSprite(
          crew,
          PlayerColors[i],
          false,
          true
        )
        selectedPlayerLeft = buildCrewProtocolActorSprite(
          crew,
          PlayerColors[i],
          true,
          true
        )
      result.addSpriteChanged(
        spriteDefs,
        crewPlayerSpriteId(i, variant, false),
        crew.width + 2,
        crew.height + 2,
        playerRight,
        "player " & playerColorName(i) & " right"
      )
      result.addSpriteChanged(
        spriteDefs,
        crewPlayerSpriteId(i, variant, true),
        crew.width + 2,
        crew.height + 2,
        playerLeft,
        "player " & playerColorName(i) & " left"
      )
      result.addSpriteChanged(
        spriteDefs,
        selectedCrewPlayerSpriteId(i, variant, false),
        crew.width + 2,
        crew.height + 2,
        selectedPlayerRight,
        "selected player " & playerColorName(i) & " right"
      )
      result.addSpriteChanged(
        spriteDefs,
        selectedCrewPlayerSpriteId(i, variant, true),
        crew.width + 2,
        crew.height + 2,
        selectedPlayerLeft,
        "selected player " & playerColorName(i) & " left"
      )

    let
      ghostRight = buildSpriteProtocolActorSprite(
        sim.ghostSprite,
        PlayerColors[i],
        false
      )
      ghostLeft = buildSpriteProtocolActorSprite(
        sim.ghostSprite,
        PlayerColors[i],
        true
      )
      selectedGhostRight = buildSpriteProtocolActorSprite(
        sim.ghostSprite,
        PlayerColors[i],
        false,
        true
      )
      selectedGhostLeft = buildSpriteProtocolActorSprite(
        sim.ghostSprite,
        PlayerColors[i],
        true,
        true
      )
    result.addSpriteChanged(
      spriteDefs,
      GhostSpriteBase + i * 2,
      sim.ghostSprite.width + 2,
      sim.ghostSprite.height + 2,
      ghostRight,
      "ghost " & playerColorName(i) & " right"
    )
    result.addSpriteChanged(
      spriteDefs,
      GhostSpriteBase + i * 2 + 1,
      sim.ghostSprite.width + 2,
      sim.ghostSprite.height + 2,
      ghostLeft,
      "ghost " & playerColorName(i) & " left"
    )
    result.addSpriteChanged(
      spriteDefs,
      SelectedGhostSpriteBase + i * 2,
      sim.ghostSprite.width + 2,
      sim.ghostSprite.height + 2,
      selectedGhostRight,
      "selected ghost " & playerColorName(i) & " right"
    )
    result.addSpriteChanged(
      spriteDefs,
      SelectedGhostSpriteBase + i * 2 + 1,
      sim.ghostSprite.width + 2,
      sim.ghostSprite.height + 2,
      selectedGhostLeft,
      "selected ghost " & playerColorName(i) & " left"
    )
    for variant in 0 ..< CrewSpriteVariants:
      let
        body = sim.bodySprites[variant]
        bodyPixels = buildSpriteProtocolBodySprite(body, PlayerColors[i])
      result.addSpriteChanged(
        spriteDefs,
        bodySpriteId(i, variant),
        body.width + 2,
        body.height + 2,
        bodyPixels,
        "body " & playerColorName(i)
      )

proc buildSpriteProtocolPlayerInit(
  sim: SimServer,
  spriteDefs: var seq[SpriteDefinition],
  layerId = MapLayerId,
  objectIdOffset = 0,
  spriteIdOffset = 0,
  clearObjects = true,
  addMarkers = true
): seq[uint8] {.measure.} =
  ## Builds the initial sprite player snapshot.
  result = @[]
  if clearObjects:
    result.addU8(0x04)
  let isPovLayer = layerId == PovLayerId
  let
    layerType =
      if isPovLayer:
        FullScreenLayerType
      else:
        MapLayerType
    layerFlags =
      if isPovLayer:
        0
      else:
        ZoomableLayerFlag
  result.addLayer(layerId, layerType, layerFlags)
  result.addViewport(layerId, ScreenWidth, ScreenHeight)
  if not isPovLayer:
    result.addSpriteChanged(
      spriteDefs,
      MapSpriteId.protocolSpriteId(spriteIdOffset),
      sim.gameMap.width,
      sim.gameMap.height,
      sim.buildMapSpritePixels(),
      "map"
    )
  if addMarkers:
    sim.addMapMarkers(
      spriteDefs,
      result,
      layerId,
      objectIdOffset,
      spriteIdOffset
    )
  result.addSpriteChanged(
    spriteDefs,
    SpritePlayerWalkabilitySpriteId.protocolSpriteId(spriteIdOffset),
    sim.gameMap.width,
    sim.gameMap.height,
    sim.buildWalkabilitySpritePixels(),
    "walkability map"
  )
  result.addSpriteChanged(
    spriteDefs,
    TaskSpriteId.protocolSpriteId(spriteIdOffset),
    sim.taskIconSprite.width,
    sim.taskIconSprite.height,
    buildSpriteProtocolRawSprite(sim.taskIconSprite),
    "task bubble"
  )
  result.addSpriteChanged(
    spriteDefs,
    SpritePlayerKillSpriteId.protocolSpriteId(spriteIdOffset),
    sim.killButtonSprite.width,
    sim.killButtonSprite.height,
    buildSpriteProtocolRawSprite(sim.killButtonSprite),
    "imposter icon"
  )
  result.addSpriteChanged(
    spriteDefs,
    SpritePlayerKillShadowSpriteId.protocolSpriteId(spriteIdOffset),
    sim.killButtonSprite.width,
    sim.killButtonSprite.height,
    buildSpriteProtocolShadowSprite(sim.killButtonSprite),
    "imposter icon cooldown"
  )
  result.addSpriteChanged(
    spriteDefs,
    SpritePlayerGhostIconSpriteId.protocolSpriteId(spriteIdOffset),
    sim.ghostIconSprite.width,
    sim.ghostIconSprite.height,
    buildSpriteProtocolRawSprite(sim.ghostIconSprite),
    "ghost icon"
  )
  result.addSpriteChanged(
    spriteDefs,
    SpritePlayerMeetingButtonSpriteId.protocolSpriteId(spriteIdOffset),
    sim.meetingButtonSprite.width,
    sim.meetingButtonSprite.height,
    buildSpriteProtocolRawSprite(sim.meetingButtonSprite),
    "meeting button"
  )
  result.addSpriteChanged(
    spriteDefs,
    SpritePlayerArrowSpriteId.protocolSpriteId(spriteIdOffset),
    1,
    1,
    buildSolidSprite(1, 1, 8'u8),
    "task arrow"
  )
  sim.addSpriteProtocolInterstitialSprites(spriteDefs, result, spriteIdOffset)
  for i in 0 ..< PlayerColors.len:
    for variant in 0 ..< CrewSpriteVariants:
      let
        crew = sim.crewSprites[variant]
        playerRight = buildCrewProtocolActorSprite(
          crew,
          PlayerColors[i],
          false
        )
        playerLeft = buildCrewProtocolActorSprite(
          crew,
          PlayerColors[i],
          true
        )
      result.addSpriteChanged(
        spriteDefs,
        crewPlayerSpriteId(i, variant, false).protocolSpriteId(
          spriteIdOffset
        ),
        crew.width + 2,
        crew.height + 2,
        playerRight,
        "player " & playerColorName(i) & " right"
      )
      result.addSpriteChanged(
        spriteDefs,
        crewPlayerSpriteId(i, variant, true).protocolSpriteId(
          spriteIdOffset
        ),
        crew.width + 2,
        crew.height + 2,
        playerLeft,
        "player " & playerColorName(i) & " left"
      )

    let
      ghostRight = buildSpriteProtocolActorSprite(
        sim.ghostSprite,
        PlayerColors[i],
        false
      )
      ghostLeft = buildSpriteProtocolActorSprite(
        sim.ghostSprite,
        PlayerColors[i],
        true
      )
    result.addSpriteChanged(
      spriteDefs,
      (GhostSpriteBase + i * 2).protocolSpriteId(spriteIdOffset),
      sim.ghostSprite.width + 2,
      sim.ghostSprite.height + 2,
      ghostRight,
      "ghost " & playerColorName(i) & " right"
    )
    result.addSpriteChanged(
      spriteDefs,
      (GhostSpriteBase + i * 2 + 1).protocolSpriteId(spriteIdOffset),
      sim.ghostSprite.width + 2,
      sim.ghostSprite.height + 2,
      ghostLeft,
      "ghost " & playerColorName(i) & " left"
    )
    for variant in 0 ..< CrewSpriteVariants:
      let
        body = sim.bodySprites[variant]
        bodyPixels = buildSpriteProtocolBodySprite(body, PlayerColors[i])
      result.addSpriteChanged(
        spriteDefs,
        bodySpriteId(i, variant).protocolSpriteId(spriteIdOffset),
        body.width + 2,
        body.height + 2,
        bodyPixels,
        "body " & playerColorName(i)
      )

proc spriteObjectId(player: Player): int =
  ## Returns the stable global protocol object id for a player.
  PlayerObjectBase + player.joinOrder

proc spriteImposterBarObjectId(player: Player): int =
  ## Returns the stable global protocol object id for an impostor bar.
  ImposterBarObjectBase + player.joinOrder

proc spriteImposterBarSpriteId(player: Player): int =
  ## Returns the global protocol sprite id for one impostor's cooldown bar.
  ImposterBarSpriteBase + player.joinOrder

proc spritePlayerNameObjectId(player: Player): int =
  ## Returns the stable global protocol object id for a player name label.
  PlayerNameObjectBase + player.joinOrder

proc spritePlayerNameSpriteId(player: Player): int =
  ## Returns the global protocol sprite id for a player name label.
  PlayerNameSpriteBase + player.joinOrder

proc playerLabelText(player: Player): string =
  ## Returns the per-player name label text for the global viewer.
  result = player.address
  if result.len == 0:
    result = "?"
  if result.len > PlayerNameMaxChars:
    result.setLen(PlayerNameMaxChars)

proc scoreboardPipObjectId(row: int): int =
  ## Returns the stable score pip object id for one row.
  ScoreboardPipObjectBase + row

proc scoreboardTextObjectId(row: int): int =
  ## Returns the stable score text object id for one row.
  ScoreboardTextObjectBase + row

proc scoreboardTextSpriteId(row: int): int =
  ## Returns the stable score text sprite id for one row.
  ScoreboardTextSpriteBase + row

proc scoreboardPipSpriteId(colorIndex: int): int =
  ## Returns the stable score pip sprite id for one color.
  ScoreboardPipSpriteBase + colorIndex

proc scoreboardRoleTag(player: Player): string =
  ## Returns the compact scoreboard role tag for one player.
  case player.role
  of Crewmate:
    "crew"
  of Imposter:
    "imp"

proc scoreboardName(player: Player): string =
  ## Returns the clickable scoreboard player label.
  player.playerLabelText() & " (" & player.scoreboardRoleTag() & ")"

proc scoreboardText(player: Player): string =
  ## Returns one compact scoreboard row.
  player.scoreboardName() & " " & $player.reward

proc scoreboardJoinOrderAt(
  sim: SimServer,
  layer,
  mouseX,
  mouseY: int
): int =
  ## Returns the join order for a clicked scoreboard name.
  if layer != TopLeftLayerId:
    return -1
  let row = (mouseY - ScoreboardY) div ScoreboardRowHeight
  if row < 0 or row >= sim.players.len:
    return -1
  let
    player = sim.players[row]
    name = player.scoreboardName()
    rowY = ScoreboardY + row * ScoreboardRowHeight
    nameWidth = sim.asciiSprites.textWidth(name)
  if mouseY < rowY or mouseY >= rowY + TextLineHeight:
    return -1
  if mouseX < ScoreboardTextX or
      mouseX >= ScoreboardTextX + nameWidth:
    return -1
  player.joinOrder

proc toggleSelectedJoinOrder(
  state: var GlobalViewerState,
  joinOrder: int
) =
  ## Selects or clears the current point-of-view join order.
  if joinOrder < 0:
    state.selectedJoinOrder = -1
  elif state.selectedJoinOrder == joinOrder:
    state.selectedJoinOrder = -1
  else:
    state.selectedJoinOrder = joinOrder

proc firstImposterIndex(sim: SimServer): int =
  ## Returns the first impostor player index, or -1.
  for i in 0 ..< sim.players.len:
    if sim.players[i].role == Imposter:
      return i
  -1

proc globalInterstitialPlayerIndex(sim: SimServer): int =
  ## Returns the player index used for the global interstitial layer.
  if sim.phase == RoleReveal:
    return sim.firstImposterIndex()
  -1

proc addScoreboard(
  sim: SimServer,
  spriteDefs: var seq[SpriteDefinition],
  currentIds: var seq[int],
  packet: var seq[uint8],
  selectedJoinOrder: int
) {.measure.} =
  ## Adds the top-left player score picker.
  packet.addLayer(TopLeftLayerId, TopLeftLayerType, UiLayerFlag)
  packet.addViewport(TopLeftLayerId, ScoreboardWidth, ScoreboardHeight)
  for i in 0 ..< sim.players.len:
    let
      player = sim.players[i]
      colorIndex = playerColorIndex(player.color)
      pipSpriteId = scoreboardPipSpriteId(colorIndex)
      pipObjectId = scoreboardPipObjectId(i)
      textSpriteId = scoreboardTextSpriteId(i)
      textObjectId = scoreboardTextObjectId(i)
      rowY = ScoreboardY + i * ScoreboardRowHeight
      color =
        if player.joinOrder == selectedJoinOrder:
          ScoreboardSelectedTextColor
        else:
          ScoreboardTextColor
      text = sim.buildSpriteProtocolTextSprite(
        [player.scoreboardText()],
        color,
        struck = not player.alive
      )
    currentIds.add(pipObjectId)
    currentIds.add(textObjectId)
    packet.addSpriteChanged(
      spriteDefs,
      pipSpriteId,
      ScoreboardPipSize,
      ScoreboardPipSize,
      buildSolidSprite(
        ScoreboardPipSize,
        ScoreboardPipSize,
        playerColorRgba(player.color)
      ),
      "score pip " & playerColorName(colorIndex)
    )
    packet.addObject(
      pipObjectId,
      ScoreboardPipX,
      ScoreboardPipY + i * ScoreboardRowHeight,
      0,
      TopLeftLayerId,
      pipSpriteId
    )
    packet.addSpriteChanged(
      spriteDefs,
      textSpriteId,
      text.width,
      text.height,
      text.pixels,
      "score " & player.scoreboardText() & " color " & $color &
        " alive " & $player.alive,
      changed = not player.alive
    )
    packet.addObject(
      textObjectId,
      ScoreboardTextX,
      rowY,
      0,
      TopLeftLayerId,
      textSpriteId
    )

proc voteLabelLine(sim: SimServer, playerIndex: int): string =
  ## Returns the vote indicator line for one player during a vote.
  if sim.phase notin {Voting, VoteResult}:
    return ""
  if playerIndex < 0 or playerIndex >= sim.voteState.votes.len:
    return ""
  if not sim.players[playerIndex].alive:
    return ""
  let vote = sim.voteState.votes[playerIndex]
  if vote == -1:
    return "-> ?"
  if vote == -2:
    return "-> skip"
  if vote < 0 or vote >= sim.players.len:
    return ""
  var target = sim.players[vote].address
  if target.len == 0:
    target = "?"
  if target.len > PlayerNameMaxChars:
    target.setLen(PlayerNameMaxChars)
  "-> " & target

proc playerLabelLines(
  sim: SimServer,
  player: Player,
  playerIndex: int
): seq[string] =
  ## Returns label lines (name plus optional vote) for one player.
  result = @[playerLabelText(player)]
  let voteLine = voteLabelLine(sim, playerIndex)
  if voteLine.len > 0:
    result.add(voteLine)

proc playerLabelSpriteLabel(lines: openArray[string]): string =
  ## Returns a stable cache label for one floating player name.
  result = "player label"
  for line in lines:
    result.add("|")
    result.add(line)

proc spriteTrailDotObjectId(joinOrder, dotIndex: int): int =
  ## Returns the stable global protocol object id for a trail dot.
  TrailDotObjectBase + joinOrder * TrailMaxDots + dotIndex

proc spritePlayerX(player: Player): int =
  ## Returns the global viewer x position for a player sprite.
  player.x - SpriteDrawOffX - 1

proc spritePlayerY(player: Player): int =
  ## Returns the global viewer y position for a player sprite.
  player.y - SpriteDrawOffY - 1

proc trailCenter(player: Player): tuple[x, y: int] =
  ## Returns the map position used for a player's trail.
  (
    x: player.x + CollisionW div 2,
    y: player.y + CollisionH div 2
  )

proc spriteBodyObjectId(index: int): int =
  ## Returns the global protocol object id for a dead body.
  BodyObjectBase + index

proc spriteTaskObjectId(index: int): int =
  ## Returns the global protocol object id for a task bubble.
  TaskObjectBase + index

proc taskStillNeeded(sim: SimServer, taskIndex: int): bool {.measure.} =
  ## Returns true when any player still needs a task station.
  for i in 0 ..< sim.players.len:
    let player = sim.players[i]
    if not player.hasTask(taskIndex):
      continue
    if taskIndex >= sim.tasks.len:
      continue
    if i >= sim.tasks[taskIndex].completed.len:
      return true
    if not sim.tasks[taskIndex].completed[i]:
      return true
  false

proc trailIndex(state: GlobalViewerState, joinOrder: int): int =
  ## Returns the trail index for one player join order.
  for i in 0 ..< state.trails.len:
    if state.trails[i].joinOrder == joinOrder:
      return i
  -1

proc playerExists(sim: SimServer, joinOrder: int): bool =
  ## Returns true when a player join order is still present.
  for player in sim.players:
    if player.joinOrder == joinOrder:
      return true
  false

proc updateTrails(state: var GlobalViewerState, sim: SimServer) {.measure.} =
  ## Updates global-only player trails from current player positions.
  for i in countdown(state.trails.high, 0):
    if not sim.playerExists(state.trails[i].joinOrder):
      state.trails.delete(i)

  for player in sim.players:
    let
      center = player.trailCenter()
      colorIndex = playerColorIndex(player.color)
    var index = state.trailIndex(player.joinOrder)
    if index < 0:
      state.trails.add PlayerTrail(
        joinOrder: player.joinOrder,
        lastX: center.x,
        lastY: center.y,
        dots: @[TrailDot(
          x: center.x,
          y: center.y,
          colorIndex: colorIndex
        )]
      )
      continue
    if distSq(
      center.x,
      center.y,
      state.trails[index].lastX,
      state.trails[index].lastY
    ) >= TrailDotSpacing * TrailDotSpacing:
      state.trails[index].dots.add TrailDot(
        x: center.x,
        y: center.y,
        colorIndex: colorIndex
      )
      state.trails[index].lastX = center.x
      state.trails[index].lastY = center.y
      while state.trails[index].dots.len > TrailMaxDots:
        state.trails[index].dots.delete(0)

proc spriteActorSpriteId(player: Player, selectedJoinOrder: int): int =
  ## Returns the sprite id for a player in the global viewer.
  let
    colorIndex = playerColorIndex(player.color)
    side = if player.flipH: 1 else: 0
    selected = player.joinOrder == selectedJoinOrder
  if player.alive and selected:
    selectedCrewPlayerSpriteId(colorIndex, player.joinOrder, player.flipH)
  elif player.alive:
    crewPlayerSpriteId(colorIndex, player.joinOrder, player.flipH)
  elif selected:
    SelectedGhostSpriteBase + colorIndex * 2 + side
  else:
    GhostSpriteBase + colorIndex * 2 + side

proc spriteCenterDistanceSq(
  player: Player,
  crew: CrewSprite,
  mouseX,
  mouseY: int
): int =
  ## Returns squared distance from the mouse to a player sprite center.
  let
    x = player.spritePlayerX()
    y = player.spritePlayerY()
    centerX = x + (crew.width + 2) div 2
    centerY = y + (crew.height + 2) div 2
    dx = mouseX - centerX
    dy = mouseY - centerY
  dx * dx + dy * dy

proc selectSpritePlayer(
  sim: SimServer,
  mouseX,
  mouseY: int
): int {.measure.} =
  ## Returns the join order of the best player selected by a map click.
  result = -1
  var
    bestExactY = low(int)
    bestNearY = low(int)
    bestNearDistanceSq = high(int)
    bestNearJoinOrder = -1
  for player in sim.players:
    let crew = sim.crewSpriteForSlot(player.joinOrder)
    let
      x = player.spritePlayerX()
      y = player.spritePlayerY()
      w = crew.width + 2
      h = crew.height + 2
    if mouseX >= x and mouseX < x + w and
        mouseY >= y and mouseY < y + h and
        player.y >= bestExactY:
      bestExactY = player.y
      result = player.joinOrder
      continue

    let distanceSq = player.spriteCenterDistanceSq(crew, mouseX, mouseY)
    if distanceSq <= AgentClickRadiusSq and
        (
          distanceSq < bestNearDistanceSq or
          distanceSq == bestNearDistanceSq and player.y >= bestNearY
        ):
      bestNearDistanceSq = distanceSq
      bestNearY = player.y
      bestNearJoinOrder = player.joinOrder

  if result < 0:
    result = bestNearJoinOrder

proc selectedPlayerIndex(
  sim: SimServer,
  joinOrder: int
): int {.measure.} =
  ## Returns the player index for a join order.
  for i in 0 ..< sim.players.len:
    if sim.players[i].joinOrder == joinOrder:
      return i
  -1

proc buildTaskProgressSprite(progress, total: int): seq[uint8] {.measure.} =
  ## Builds the one-pixel high task progress bar sprite.
  result = newRgbaPixels(TaskBarWidth, 1)
  let filled =
    if total > 0:
      clamp(progress * TaskBarWidth div total, 0, TaskBarWidth)
    else:
      0
  for x in 0 ..< TaskBarWidth:
    let color = if x < filled: ProgressFilled else: ProgressEmpty
    result.putRgbaPixel(x, color)

proc cooldownReadyProgress(cooldown, maxCooldown: int): int =
  ## Returns the elapsed cooldown amount for a progress bar.
  if maxCooldown <= 0 or cooldown <= 0:
    return max(maxCooldown, 1)
  maxCooldown - clamp(cooldown, 0, maxCooldown)

proc cooldownReadyPercent(cooldown, maxCooldown: int): int =
  ## Returns the elapsed cooldown percentage.
  if maxCooldown <= 0 or cooldown <= 0:
    return 100
  let progress = cooldownReadyProgress(cooldown, maxCooldown)
  clamp(progress * 100 div maxCooldown, 0, 100)

proc addSpritePlayerTaskArrows(
  sim: SimServer,
  playerIndex: int,
  cameraX,
  cameraY: int,
  currentIds: var seq[int],
  packet: var seq[uint8],
  layerId = MapLayerId,
  objectIdOffset = 0,
  spriteIdOffset = 0
) {.measure.} =
  ## Adds off-screen task arrow objects to a sprite player packet.
  if not sim.config.showTaskArrows:
    return
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  let player = sim.players[playerIndex]
  if player.role != Crewmate:
    return
  let bob = [0, 0, -1, -1, -1, 0, 0, 1, 1, 1]
  for taskIndex in player.assignedTasks:
    if taskIndex < 0 or taskIndex >= sim.tasks.len:
      continue
    let task = sim.tasks[taskIndex]
    if playerIndex < task.completed.len and task.completed[playerIndex]:
      continue
    let
      bobY =
        if player.activeTask == taskIndex:
          0
        else:
          bob[(sim.tickCount div 3) mod bob.len]
      iconX = task.x + task.w div 2 - cameraX
      iconY = task.y - SpriteSize div 2 - 2 + bobY - cameraY
      iconSx = task.x + task.w div 2 - SpriteSize div 2 - cameraX
      iconSy = task.y - SpriteSize - 2 + bobY - cameraY
    if iconSx + SpriteSize > 0 and iconSy + SpriteSize > 0 and
        iconSx < ScreenWidth and iconSy < ScreenHeight:
      continue
    let
      px = float(player.x + CollisionW div 2 - cameraX)
      py = float(player.y + CollisionH div 2 - cameraY)
      dx = float(iconX) - px
      dy = float(iconY) - py
    if abs(dx) < 0.5 and abs(dy) < 0.5:
      continue
    var ex, ey: float
    let
      minX = 0.0
      maxX = float(ScreenWidth - 1)
      minY = 0.0
      maxY = float(ScreenHeight - 1)
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
    let objectId = SpritePlayerTaskArrowObjectBase + taskIndex
    currentIds.addProtocolObject(
      packet,
      objectId,
      int(ex),
      int(ey),
      30000,
      layerId,
      SpritePlayerArrowSpriteId,
      objectIdOffset,
      spriteIdOffset
    )

proc debugProtocolId(sourceId, baseId: int): int =
  ## Maps a player-local debug id into Crewrift's debug namespace.
  baseId + sourceId mod DebugSpriteIdRange

proc addDebugSpritePacket(
  currentIds: var seq[int],
  packet: var seq[uint8],
  debugSprites: openArray[uint8],
  layerId: int
) {.measure.} =
  ## Adds one player-authored debug sprite packet to a player viewport.
  for message in debugSprites.parseSpritePacket():
    case message.kind
    of spkSprite:
      packet.addSprite(
        message.sprite.id.debugProtocolId(DebugSpriteIdOffset),
        message.sprite.width,
        message.sprite.height,
        uncompress(message.sprite.compressedPixels),
        message.sprite.label
      )
    of spkObject:
      let objectId = message.objectDef.id.debugProtocolId(DebugSpriteIdOffset)
      currentIds.add(objectId)
      packet.addObject(
        objectId,
        message.objectDef.x,
        message.objectDef.y,
        clamp(
          DebugSpriteZBase + message.objectDef.z,
          int(low(int16)),
          int(high(int16))
        ),
        layerId,
        message.objectDef.spriteId.debugProtocolId(DebugSpriteIdOffset)
      )
    of spkDeleteObject:
      packet.addDeleteObject(
        message.objectId.debugProtocolId(DebugSpriteIdOffset)
      )
    of spkClearObjects, spkViewport, spkLayer:
      discard

proc buildSpriteProtocolPlayerUpdates*(
  sim: var SimServer,
  playerIndex: int,
  state: PlayerViewerState,
  nextState: var PlayerViewerState,
  layerId = MapLayerId,
  objectIdOffset = 0,
  spriteIdOffset = 0,
  clearObjects = true,
  addMarkers = true,
  debugSprites: seq[uint8] = @[]
): seq[uint8] {.measure.} =
  ## Builds sprite protocol updates for one playable player view.
  result = @[]
  nextState =
    if state.isNil:
      initPlayerViewerState()
    else:
      state
  if not nextState.initialized:
    result = sim.buildSpriteProtocolPlayerInit(
      nextState.spriteDefs,
      layerId,
      objectIdOffset,
      spriteIdOffset,
      clearObjects,
      addMarkers
    )
    nextState.initialized = true

  var currentIds: seq[int] = @[]
  if sim.phase != Playing or playerIndex < 0 or
      playerIndex >= sim.players.len:
    currentIds.addProtocolObject(
      result,
      SpritePlayerInterstitialObjectId,
      0,
      0,
      0,
      layerId,
      SpritePlayerInterstitialSpriteId,
      objectIdOffset,
      spriteIdOffset
    )
    sim.addProtocolTextSprites(
      nextState.spriteDefs,
      currentIds,
      result,
      layerId,
      playerIndex,
      objectIdOffset,
      spriteIdOffset
    )
    sim.addProtocolInterstitialActorSprites(
      nextState.spriteDefs,
      currentIds,
      result,
      layerId,
      playerIndex,
      objectIdOffset,
      spriteIdOffset
    )
  else:
    let
      player = sim.players[playerIndex]
      view = sim.playerView(playerIndex)
      cameraX = view.cameraX
      cameraY = view.cameraY
      viewerIsGhost = view.viewerIsGhost
    let
      shadowViewChanged =
        not nextState.shadowReady or
        nextState.shadowCameraX != cameraX or
        nextState.shadowCameraY != cameraY or
        nextState.shadowOriginMx != view.originMx or
        nextState.shadowOriginMy != view.originMy
      shadowChanged =
        if viewerIsGhost:
          false
        else:
          sim.usePlayerShadowMask(playerIndex, view)
    if layerId == PovLayerId:
      currentIds.add(MapObjectId.protocolObjectId(objectIdOffset))
      result.addSpriteChanged(
        nextState.spriteDefs,
        MapSpriteId.protocolSpriteId(spriteIdOffset),
        ScreenWidth,
        ScreenHeight,
        sim.buildMapViewSpritePixels(cameraX, cameraY),
        "map view",
        changed = shadowViewChanged
      )
      result.addObject(
        MapObjectId.protocolObjectId(objectIdOffset),
        0,
        0,
        low(int16),
        layerId,
        MapSpriteId.protocolSpriteId(spriteIdOffset)
      )
    else:
      currentIds.addProtocolObject(
        result,
        MapObjectId,
        -cameraX,
        -cameraY,
        low(int16),
        layerId,
        MapSpriteId,
        objectIdOffset,
        spriteIdOffset
      )
    if not viewerIsGhost:
      currentIds.add(
        SpritePlayerShadowObjectId.protocolObjectId(objectIdOffset)
      )
      if shadowChanged or shadowViewChanged or
          nextState.spriteDefs.spriteDefinitionIndex(
            SpritePlayerShadowSpriteId.protocolSpriteId(spriteIdOffset)
          ) < 0:
        result.addSpriteChanged(
          nextState.spriteDefs,
          SpritePlayerShadowSpriteId.protocolSpriteId(spriteIdOffset),
          ScreenWidth,
          ScreenHeight,
          sim.buildPlayerShadowSprite(cameraX, cameraY),
          "shadow",
          changed = shadowChanged or shadowViewChanged
        )
      nextState.shadowReady = true
      nextState.shadowCameraX = cameraX
      nextState.shadowCameraY = cameraY
      nextState.shadowOriginMx = view.originMx
      nextState.shadowOriginMy = view.originMy
      result.addObject(
        SpritePlayerShadowObjectId.protocolObjectId(objectIdOffset),
        0,
        0,
        SpritePlayerShadowZ,
        layerId,
        SpritePlayerShadowSpriteId.protocolSpriteId(spriteIdOffset)
      )
    else:
      nextState.shadowReady = false

    for i in 0 ..< sim.bodies.len:
      let body = sim.bodies[i]
      if not sim.screenPointVisible(
        view,
        body.x + CollisionW div 2,
        body.y + CollisionH div 2
      ):
        continue
      let objectId = spriteBodyObjectId(i)
      currentIds.addProtocolObject(
        result,
        objectId,
        body.x - SpriteDrawOffX - 1 - cameraX,
        body.y - SpriteDrawOffY - 1 - cameraY,
        body.y,
        layerId,
        bodySpriteId(playerColorIndex(body.color), body.slotId),
        objectIdOffset,
        spriteIdOffset
      )

    for other in sim.players:
      if not other.playerActorInFrame(view):
        continue
      if other.alive:
        let visiblePoint = other.playerActorVisibilityPoint(view)
        if other.joinOrder != player.joinOrder:
          if not sim.screenPointVisible(
            view,
            visiblePoint.x,
            visiblePoint.y
          ):
            continue
      elif not viewerIsGhost:
        continue
      let objectId = other.spriteObjectId()
      currentIds.addProtocolObject(
        result,
        objectId,
        other.x - SpriteDrawOffX - 1 - cameraX,
        other.y - SpriteDrawOffY - 1 - cameraY,
        other.y,
        layerId,
        other.spriteActorSpriteId(-1),
        objectIdOffset,
        spriteIdOffset
      )

    if player.role == Crewmate:
      let bob = [0, 0, -1, -1, -1, 0, 0, 1, 1, 1]
      for taskIndex in player.assignedTasks:
        if taskIndex < 0 or taskIndex >= sim.tasks.len:
          continue
        let task = sim.tasks[taskIndex]
        if playerIndex < task.completed.len and
            task.completed[playerIndex]:
          continue
        let
          bobY =
            if player.activeTask == taskIndex:
              0
            else:
              bob[(sim.tickCount div 3) mod bob.len]
          iconSx =
            task.x + task.w div 2 - SpriteSize div 2 - cameraX
          iconSy = task.y - SpriteSize - 2 + bobY - cameraY
        if iconSx + SpriteSize <= 0 or iconSy + SpriteSize <= 0 or
            iconSx >= ScreenWidth or iconSy >= ScreenHeight:
          continue
        let objectId = spriteTaskObjectId(taskIndex)
        currentIds.addProtocolObject(
          result,
          objectId,
          iconSx,
          iconSy,
          30000,
          layerId,
          TaskSpriteId,
          objectIdOffset,
          spriteIdOffset
        )
        if player.activeTask == taskIndex and player.taskProgress > 0:
          let
            barX = iconSx + SpriteSize div 2 - TaskBarWidth div 2
            barY = iconSy + SpriteSize + TaskBarGap
            progressPercent =
              if sim.config.taskCompleteTicks > 0:
                clamp(
                  player.taskProgress * 100 div sim.config.taskCompleteTicks,
                  0,
                  100
                )
              else:
                0
          currentIds.add(
            SpritePlayerProgressObjectId.protocolObjectId(objectIdOffset)
          )
          result.addSpriteChanged(
            nextState.spriteDefs,
            SpritePlayerProgressSpriteId.protocolSpriteId(spriteIdOffset),
            TaskBarWidth,
            1,
            buildTaskProgressSprite(
              player.taskProgress,
              sim.config.taskCompleteTicks
            ),
            "progress bar " & $progressPercent & "%",
            changed = true
          )
          result.addObject(
            SpritePlayerProgressObjectId.protocolObjectId(objectIdOffset),
            barX,
            barY,
            30001,
            layerId,
            SpritePlayerProgressSpriteId.protocolSpriteId(spriteIdOffset)
          )

    sim.addSpritePlayerTaskArrows(
      playerIndex,
      cameraX,
      cameraY,
      currentIds,
      result,
      layerId,
      objectIdOffset,
      spriteIdOffset
    )

    if not player.alive:
      currentIds.addProtocolObject(
        result,
        SpritePlayerRemainingObjectId,
        1,
        ScreenHeight - SpriteSize - 1,
        30002,
        layerId,
        SpritePlayerGhostIconSpriteId,
        objectIdOffset,
        spriteIdOffset
      )
    elif player.role == Imposter:
      let
        killIconX = 1
        killIconY = ScreenHeight - SpriteSize - 1
      currentIds.addProtocolObject(
        result,
        SpritePlayerRemainingObjectId,
        killIconX,
        killIconY,
        30002,
        layerId,
        if player.killCooldown > 0:
          SpritePlayerKillShadowSpriteId
        else:
          SpritePlayerKillSpriteId,
        objectIdOffset,
        spriteIdOffset
      )
      let
        progressTotal = max(sim.config.killCooldownTicks, 1)
        progress = cooldownReadyProgress(player.killCooldown, progressTotal)
        progressPercent = cooldownReadyPercent(
          player.killCooldown,
          progressTotal
        )
      currentIds.add(
        SpritePlayerKillProgressObjectId.protocolObjectId(objectIdOffset)
      )
      result.addSpriteChanged(
        nextState.spriteDefs,
        SpritePlayerKillProgressSpriteId.protocolSpriteId(spriteIdOffset),
        TaskBarWidth,
        1,
        buildTaskProgressSprite(progress, progressTotal),
        "progress bar " & $progressPercent & "%",
        changed = true
      )
      result.addObject(
        SpritePlayerKillProgressObjectId.protocolObjectId(objectIdOffset),
        killIconX + SpriteSize + TaskBarGap,
        killIconY + SpriteSize div 2,
        30003,
        layerId,
        SpritePlayerKillProgressSpriteId.protocolSpriteId(spriteIdOffset)
      )

    let
      remainingText = $sim.totalTasksRemaining()
      remaining = sim.buildSpriteProtocolTextSprite([remainingText], 2'u8)
      textX = ScreenWidth - remaining.width
    currentIds.add(SelectedTextObjectId.protocolObjectId(objectIdOffset))
    result.addSpriteChanged(
      nextState.spriteDefs,
      SpritePlayerRemainingSpriteId.protocolSpriteId(spriteIdOffset),
      remaining.width,
      remaining.height,
      remaining.pixels,
      "task counter " & remainingText
    )
    result.addObject(
      SelectedTextObjectId.protocolObjectId(objectIdOffset),
      textX,
      0,
      30003,
      layerId,
      SpritePlayerRemainingSpriteId.protocolSpriteId(spriteIdOffset)
    )

  sim.addSpritePlayerTickMarker(
    nextState.spriteDefs,
    currentIds,
    result,
    layerId,
    objectIdOffset,
    spriteIdOffset
  )
  currentIds.addDebugSpritePacket(result, debugSprites, layerId)
  if not state.isNil:
    for objectId in state.objectIds:
      if objectId notin currentIds:
        result.addDeleteObject(objectId)
  nextState.objectIds = currentIds

proc replayCommandAt(layer, x, y: int): char =
  ## Returns the replay transport command under a UI coordinate.
  if layer != ReplayBottomLeftLayerId:
    return '\0'
  let
    localX = x - TransportX
    localY = y - TransportY
  if localY >= 0 and localY < TransportIconHeight:
    if localX >= TransportDebugX and
        localX < TransportDebugX + TransportIconSize:
      return 'd'
    let index = localX div TransportButtonStride
    if index < 0 or index >= TransportIconCount:
      return '\0'
    if localX - index * TransportButtonStride >= TransportIconSize:
      return '\0'
    case index
    of 0: return '<'
    of 1: return ' '
    of 2: return 'e'
    of 3: return 'r'
    of 4: return 'b'
    else: return '\0'
  if localY >= TransportSpeedY and localY < TransportSpeedY + 6:
    let speedX = localX - TransportSpeedX
    if speedX >= 0 and speedX < 12:
      return '1'
    if speedX >= 16 and speedX < 28:
      return '2'
    if speedX >= 32 and speedX < 44:
      return '3'
    if speedX >= 48 and speedX < 60:
      return '4'
    if speedX >= 64 and speedX < 76:
      return '8'
    if speedX >= 80 and speedX < 100:
      return '6'
  '\0'

proc replayScrubTickAt(
  layer, x, y, maxTick: int,
  requireInside = true
): int =
  ## Returns the replay tick under the scrubber pointer.
  if layer != ReplayCenterBottomLayerId or maxTick < 0:
    return -1
  let
    scrubberX = max(0, (ScreenWidth - ReplayScrubberWidth) div 2)
    localX = x - scrubberX
    localY = y - ReplayScrubberY
  if requireInside and (
      localX < 0 or localX >= ReplayScrubberWidth or
      localY < 0 or localY >= ReplayScrubberHeight
    ):
    return -1
  if ReplayScrubberWidth <= 1:
    return 0
  let clampedX = clamp(localX, 0, ReplayScrubberWidth - 1)
  clamp((clampedX * maxTick) div (ReplayScrubberWidth - 1), 0, maxTick)

proc buildReplayScrubberSprite(
  tick, maxTick: int,
  enabled: bool
): tuple[width, height: int, pixels: seq[uint8]] {.measure.} =
  ## Builds a compact replay scrubber sprite.
  result.width = ReplayScrubberWidth
  result.height = ReplayScrubberHeight
  result.pixels = newRgbaPixels(ReplayScrubberWidth, ReplayScrubberHeight)
  let knobX =
    if maxTick > 0:
      clamp(
        (tick * (ReplayScrubberWidth - 1)) div maxTick,
        0,
        ReplayScrubberWidth - 1
      )
    else:
      0

  for x in 0 ..< ReplayScrubberWidth:
    result.pixels.putRgbaPixel(
      ReplayScrubberTrackY * ReplayScrubberWidth + x,
      1'u8
    )
  if enabled:
    for x in 0 .. knobX:
      result.pixels.putRgbaPixel(
        ReplayScrubberTrackY * ReplayScrubberWidth + x,
        10'u8
      )
  for y in 0 ..< ReplayScrubberHeight:
    result.pixels.putRgbaPixel(
      y * ReplayScrubberWidth + knobX,
      if enabled: 2'u8 else: 1'u8
    )
  if knobX > 0:
    result.pixels.putRgbaPixel(
      ReplayScrubberTrackY * ReplayScrubberWidth + knobX - 1,
      if enabled: 2'u8 else: 1'u8
    )
  if knobX < ReplayScrubberWidth - 1:
    result.pixels.putRgbaPixel(
      ReplayScrubberTrackY * ReplayScrubberWidth + knobX + 1,
      if enabled: 2'u8 else: 1'u8
    )

proc blitTransportIcon(
  target: var seq[uint8],
  sheet: Sprite,
  cell, baseX, baseY: int,
  tint: uint8
) =
  ## Blits one transport icon cell into protocol pixels.
  let sourceX = cell * TransportIconSize
  for y in 0 ..< TransportIconHeight:
    for x in 0 ..< TransportIconSize:
      let colorIndex = sheet.pixels[sheet.spriteIndex(sourceX + x, y)]
      if colorIndex == TransparentColorIndex:
        continue
      target.putRgbaPixel(
        (baseY + y) * TransportWidth + baseX + x,
        tint
      )

proc buildReplayControlsSprite(
  sim: SimServer,
  replayPlaying: bool,
  replaySpeed: int,
  replayLooping: bool,
  replayEnabled: bool,
  debugSpritesVisible: bool
): tuple[width, height: int, pixels: seq[uint8]] {.measure.} =
  ## Builds the replay transport controls sprite.
  result.width = TransportWidth
  result.height = TransportHeight
  result.pixels = newRgbaPixels(TransportWidth, TransportHeight)
  let
    sheet = transportSheet()
    iconCells = [
      0,
      if replayPlaying: 2 else: 1,
      3,
      4,
      5
    ]
  for i in 0 ..< iconCells.len:
    let tint =
      if not replayEnabled:
        1'u8
      elif i == 3:
        if replayLooping: 10'u8 else: 1'u8
      else:
        2'u8
    result.pixels.blitTransportIcon(
      sheet,
      iconCells[i],
      i * TransportButtonStride,
      0,
      tint
    )

  let speedTexts = ["1X", "2X", "3X", "4X", "8X", "16X"]
  var x = TransportSpeedX
  for i in 0 ..< speedTexts.len:
    let speed =
      case i
      of 0: 1
      of 1: 2
      of 2: 3
      of 3: 4
      of 4: 8
      else: 16
    let color = if speed == replaySpeed: 10'u8 else: 1'u8
    sim.blitSmallText(
      result.pixels,
      TransportWidth,
      TransportHeight,
      speedTexts[i],
      x,
      TransportSpeedY,
      color
    )
    x += TransportSpeedGap

  sim.blitSmallText(
    result.pixels,
    TransportWidth,
    TransportHeight,
    "D",
    TransportDebugX,
    0,
    if replayEnabled and debugSpritesVisible: 10'u8 else: 1'u8
  )

proc buildReplayMismatchSprite(
  sim: SimServer,
  tick: int
): tuple[width, height: int, pixels: seq[uint8], label: string] {.measure.} =
  ## Builds the top-center replay hash mismatch warning sprite.
  result.label = "hash mismatch at tick " & $tick
  let textWidth = sim.asciiSprites.textWidth(result.label)
  result.width = max(ReplayMismatchMinWidth, textWidth + ReplayMismatchPadX * 2)
  result.height = TextLineHeight + ReplayMismatchPadY * 2
  result.pixels = newRgbaPixels(result.width, result.height)
  for i in 0 ..< result.width * result.height:
    result.pixels.putRawRgbaPixel(
      i,
      ReplayMismatchBgR,
      ReplayMismatchBgG,
      ReplayMismatchBgB,
      ReplayMismatchBgA
    )
  sim.blitSmallText(
    result.pixels,
    result.width,
    result.height,
    result.label,
    (result.width - textWidth) div 2,
    ReplayMismatchPadY,
    2'u8
  )

proc addReplayMismatchWarning(
  sim: SimServer,
  spriteDefs: var seq[SpriteDefinition],
  currentIds: var seq[int],
  packet: var seq[uint8],
  tick: int
) {.measure.} =
  ## Adds a fixed top-center replay hash mismatch warning.
  if tick < 0:
    return
  let warning = sim.buildReplayMismatchSprite(tick)
  packet.addLayer(
    ReplayMismatchLayerId,
    ReplayMismatchLayerType,
    UiLayerFlag
  )
  packet.addViewport(
    ReplayMismatchLayerId,
    warning.width,
    warning.height
  )
  currentIds.add(ReplayMismatchObjectId)
  packet.addSpriteChanged(
    spriteDefs,
    ReplayMismatchSpriteId,
    warning.width,
    warning.height,
    warning.pixels,
    warning.label,
    changed = true
  )
  packet.addObject(
    ReplayMismatchObjectId,
    0,
    0,
    0,
    ReplayMismatchLayerId,
    ReplayMismatchSpriteId
  )

proc addReplayControlLayers(packet: var seq[uint8]) =
  ## Adds the fixed UI layers used by replay timing controls.
  packet.addLayer(
    ReplayCenterBottomLayerId,
    ReplayCenterBottomLayerType,
    UiLayerFlag
  )
  packet.addViewport(
    ReplayCenterBottomLayerId,
    ScreenWidth,
    ReplayPanelHeight
  )
  packet.addLayer(
    ReplayBottomLeftLayerId,
    ReplayBottomLeftLayerType,
    UiLayerFlag
  )
  packet.addViewport(
    ReplayBottomLeftLayerId,
    ScreenWidth,
    ReplayPanelHeight
  )

proc addReplayControls(
  sim: SimServer,
  spriteDefs: var seq[SpriteDefinition],
  currentIds: var seq[int],
  packet: var seq[uint8],
  replayTick,
  replaySpeed,
  replayMaxTick: int,
  replayPlaying,
  replayLooping,
  replayEnabled,
  debugSpritesVisible: bool
) {.measure.} =
  ## Adds replay timing controls when replay playback is active.
  if not replayEnabled:
    return
  packet.addReplayControlLayers()
  let
    controlTick = max(0, replayTick)
    controlMaxTick = max(controlTick, replayMaxTick)
    tickText = sim.buildSpriteProtocolTextSprite(
      ["TICK " & $controlTick],
      2'u8
    )
    scrubber = buildReplayScrubberSprite(
      controlTick,
      controlMaxTick,
      true
    )
    controls = sim.buildReplayControlsSprite(
      replayPlaying,
      replaySpeed,
      replayLooping,
      replayEnabled,
      debugSpritesVisible
    )
  currentIds.add(ReplayTickObjectId)
  currentIds.add(ReplayControlsObjectId)
  currentIds.add(ReplayScrubberObjectId)
  packet.addSpriteChanged(
    spriteDefs,
    ReplayTickSpriteId,
    tickText.width,
    tickText.height,
    tickText.pixels,
    "replay tick " & $controlTick
  )
  packet.addObject(
    ReplayTickObjectId,
    max(0, (ScreenWidth - tickText.width) div 2),
    0,
    0,
    ReplayCenterBottomLayerId,
    ReplayTickSpriteId
  )
  packet.addSpriteChanged(
    spriteDefs,
    ReplayScrubberSpriteId,
    scrubber.width,
    scrubber.height,
    scrubber.pixels,
    "replay scrubber",
    changed = true
  )
  packet.addObject(
    ReplayScrubberObjectId,
    max(0, (ScreenWidth - ReplayScrubberWidth) div 2),
    ReplayScrubberY,
    0,
    ReplayCenterBottomLayerId,
    ReplayScrubberSpriteId
  )
  packet.addSpriteChanged(
    spriteDefs,
    ReplayControlsSpriteId,
    controls.width,
    controls.height,
    controls.pixels,
    "replay controls",
    changed = true
  )
  packet.addObject(
    ReplayControlsObjectId,
    TransportX,
    TransportY,
    0,
    ReplayBottomLeftLayerId,
    ReplayControlsSpriteId
  )

proc buildSpriteProtocolUpdates*(
  sim: var SimServer,
  state: GlobalViewerState,
  nextState: var GlobalViewerState,
  replayTick = -1,
  replayPlaying = false,
  replaySpeed = 1,
  replayMaxTick = -1,
  replayLooping = false,
  replayEnabled = false,
  replayMismatchTick = -1,
  debugSprites: seq[seq[uint8]] = @[]
): seq[uint8] {.measure.} =
  ## Builds global viewer object updates for the current tick.
  result = @[]
  nextState = state
  nextState.replayCommands.setLen(0)
  nextState.replaySeekTick = -1
  let
    clickPending = nextState.mousePressed or nextState.clickPending
    clickLayer =
      if nextState.mousePressed:
        nextState.mousePressLayer
      else:
        nextState.mouseLayer
    clickX =
      if nextState.mousePressed:
        nextState.mousePressX
      else:
        nextState.mouseX
    clickY =
      if nextState.mousePressed:
        nextState.mousePressY
      else:
        nextState.mouseY
  if clickPending:
    let scoreJoinOrder = sim.scoreboardJoinOrderAt(
      clickLayer,
      clickX,
      clickY
    )
    if scoreJoinOrder >= 0:
      nextState.toggleSelectedJoinOrder(scoreJoinOrder)
    elif replayEnabled and replayTick >= 0:
      let seekTick = replayScrubTickAt(
        clickLayer,
        clickX,
        clickY,
        replayMaxTick
      )
      if seekTick >= 0:
        nextState.scrubbingReplay = true
        nextState.replaySeekTick = seekTick
      else:
        let command = replayCommandAt(
          clickLayer,
          clickX,
          clickY
        )
        if command == 'd':
          nextState.debugSpritesVisible = not nextState.debugSpritesVisible
        elif command != '\0':
          nextState.replayCommands.add(command)
        elif not nextState.povActive and clickLayer == MapLayerId:
          nextState.toggleSelectedJoinOrder(
            sim.selectSpritePlayer(clickX, clickY)
          )
    elif not nextState.povActive and clickLayer == MapLayerId:
      nextState.toggleSelectedJoinOrder(
        sim.selectSpritePlayer(clickX, clickY)
      )
  nextState.clearGlobalMouseEdges()
  if replayEnabled and replayTick >= 0 and nextState.mouseDown and
      nextState.scrubbingReplay:
    let seekTick = replayScrubTickAt(
      nextState.mouseLayer,
      nextState.mouseX,
      nextState.mouseY,
      replayMaxTick
    )
    if seekTick >= 0:
      nextState.replaySeekTick = seekTick
  let selectedPlayerIndex =
    sim.selectedPlayerIndex(nextState.selectedJoinOrder)
  if selectedPlayerIndex < 0:
    nextState.selectedJoinOrder = -1
  let
    povPlayerIndex = selectedPlayerIndex
    povJoinOrder =
      if povPlayerIndex >= 0:
        sim.players[povPlayerIndex].joinOrder
      else:
        -1
  let
    povActive =
      povPlayerIndex >= 0 and sim.phase == Playing
    activePovJoinOrder =
      if povActive:
        povJoinOrder
      else:
        -1
    povChanged = povActive != state.povActive or
      activePovJoinOrder != state.povJoinOrder
  if povChanged:
    if nextState.povState.isNil:
      nextState.povState = initPlayerViewerState()
    nextState.povState.shadowReady = false
  nextState.povActive = povActive
  nextState.povJoinOrder = activePovJoinOrder
  if not nextState.initialized:
    result = sim.buildSpriteProtocolInit(nextState.spriteDefs)
    result.addReplayControlLayers()
    nextState.initialized = true

  nextState.updateTrails(sim)
  var currentIds: seq[int] = @[]
  sim.addScoreboard(
    nextState.spriteDefs,
    currentIds,
    result,
    nextState.selectedJoinOrder
  )
  for trail in nextState.trails:
    for i in 0 ..< trail.dots.len:
      let
        dot = trail.dots[i]
        objectId = spriteTrailDotObjectId(trail.joinOrder, i)
      currentIds.add(objectId)
      result.addObject(
        objectId,
        dot.x - TrailDotSize div 2,
        dot.y - TrailDotSize div 2,
        dot.y - 100,
        MapLayerId,
        TrailDotSpriteBase + dot.colorIndex
      )

  for playerIndex in 0 ..< sim.players.len:
    let player = sim.players[playerIndex]
    let crew = sim.crewSpriteForSlot(player.joinOrder)
    let objectId = player.spriteObjectId()
    currentIds.add(objectId)
    result.addObject(
      objectId,
      player.spritePlayerX(),
      player.spritePlayerY(),
      player.y,
      MapLayerId,
      player.spriteActorSpriteId(nextState.selectedJoinOrder)
    )
    if player.role == Imposter:
      let
        barObjectId = player.spriteImposterBarObjectId()
        barSpriteId = player.spriteImposterBarSpriteId()
        barX = player.spritePlayerX() +
          (crew.width + 2 - ImposterBarWidth) div 2
        barY = player.spritePlayerY() - ImposterBarYOffset
      currentIds.add(barObjectId)
      result.addSprite(
        barSpriteId,
        ImposterBarWidth,
        ImposterBarHeight,
        buildImposterBarSprite(
          player.killCooldown,
          sim.config.killCooldownTicks
        )
      )
      result.addObject(
        barObjectId,
        barX,
        barY,
        30001,
        MapLayerId,
        barSpriteId
      )

    if sim.config.showPlayerLabels:
      let
        labelLines = playerLabelLines(sim, player, playerIndex)
        label = sim.buildSpriteProtocolTextSprite(
          labelLines,
          PlayerNameColor
        )
        labelName = playerLabelSpriteLabel(labelLines)
        labelSpriteId = player.spritePlayerNameSpriteId()
        labelObjectId = player.spritePlayerNameObjectId()
        labelX = player.spritePlayerX() +
          (crew.width + 2 - label.width) div 2
        labelY = player.spritePlayerY() - ImposterBarYOffset -
          label.height - 1
      currentIds.add(labelObjectId)
      result.addSpriteChanged(
        nextState.spriteDefs,
        labelSpriteId,
        label.width,
        label.height,
        label.pixels,
        labelName
      )
      result.addObject(
        labelObjectId,
        labelX,
        labelY,
        PlayerNameZ,
        MapLayerId,
        labelSpriteId
      )

  for i in 0 ..< sim.bodies.len:
    let
      body = sim.bodies[i]
      objectId = spriteBodyObjectId(i)
    currentIds.add(objectId)
    result.addObject(
      objectId,
      body.x - SpriteDrawOffX - 1,
      body.y - SpriteDrawOffY - 1,
      body.y,
      MapLayerId,
      bodySpriteId(playerColorIndex(body.color), body.slotId)
    )

  if sim.config.showTaskBubbles:
    let bob = [0, 0, -1, -1, -1, 0, 0, 1, 1, 1]
    for i in 0 ..< sim.tasks.len:
      if not sim.taskStillNeeded(i):
        continue
      let
        task = sim.tasks[i]
        objectId = spriteTaskObjectId(i)
        bobY = bob[(sim.tickCount div 3) mod bob.len]
      currentIds.add(objectId)
      result.addObject(
        objectId,
        task.x + task.w div 2 - SpriteSize div 2,
        task.y - SpriteSize - 2 + bobY,
        30000,
        MapLayerId,
        TaskSpriteId
      )

  if sim.hasInterstitialFrame():
    let interstitialPlayerIndex = sim.globalInterstitialPlayerIndex()
    currentIds.add(InterstitialObjectId)
    result.addObject(
      InterstitialObjectId,
      0,
      0,
      0,
      InterstitialLayerId,
      SpritePlayerInterstitialSpriteId
    )
    sim.addProtocolTextSprites(
      nextState.spriteDefs,
      currentIds,
      result,
      InterstitialLayerId,
      interstitialPlayerIndex
    )
    sim.addProtocolInterstitialActorSprites(
      nextState.spriteDefs,
      currentIds,
      result,
      InterstitialLayerId,
      interstitialPlayerIndex
    )

  sim.addReplayControls(
    nextState.spriteDefs,
    currentIds,
    result,
    replayTick,
    replaySpeed,
    replayMaxTick,
    replayPlaying,
    replayLooping,
    replayEnabled,
    nextState.debugSpritesVisible
  )
  sim.addReplayMismatchWarning(
    nextState.spriteDefs,
    currentIds,
    result,
    replayMismatchTick
  )

  for objectId in state.objectIds:
    if objectId notin currentIds:
      result.addDeleteObject(objectId)
  nextState.objectIds = currentIds

  if povActive:
    var povState: PlayerViewerState
    let povDebugSprites =
      if replayEnabled and nextState.debugSpritesVisible and
          povPlayerIndex >= 0 and povPlayerIndex < debugSprites.len:
        debugSprites[povPlayerIndex]
      else:
        @[]
    let povPacket = sim.buildSpriteProtocolPlayerUpdates(
      povPlayerIndex,
      nextState.povState,
      povState,
      PovLayerId,
      PovObjectIdOffset,
      PovSpriteIdOffset,
      false,
      false,
      povDebugSprites
    )
    for value in povPacket:
      result.add(value)
    nextState.povState = povState
  elif state.povState != nil:
    for objectId in state.povState.objectIds:
      result.addDeleteObject(objectId)
    result.addLayer(PovLayerId, TopLeftLayerType, 0)
    result.addViewport(PovLayerId, 1, 1)
    nextState.povState = initPlayerViewerState()
