import
  std/[json, math, os, random, strutils, tables, times],
  bitworld/aseprite, bitworld/client as bitworldClient,
  bitworld/pixelfonts, bitworld/profile, bitworld/spriteprotocol, bitworld/resources,
  bitworld/server,
  jsony, pixie,
  tasks as taskAssignments

const
  GameName* = "crewrift"
  GameVersion* = "1"
  ReplayFps* = 24
  DefaultMapPath* = "data/croatoan.resources"
  DarkBgPath* = "data/darkbg.aseprite"
  SpriteSheetAsepritePath = "data/spritesheet.aseprite"
  MapWidth* = 1235
  MapHeight* = 659
  SpriteSize* = 12
  CrewSpriteSize* = 16
  CrewSpriteVariants* = 8
  VoteActorSize* = CrewSpriteSize + 2
  VoteCellW* = VoteActorSize
  VoteCellH* = VoteActorSize + 4
  VoteColsMax* = 7
  VoteStartY* = 2
  VoteSkipW* = 28
  VoteSkipCursorW* = VoteSkipW + 2
  VoteSkipCursorH* = 8
  CollisionW* = 1
  CollisionH* = 1
  SpriteDrawOffX* = 2
  SpriteDrawOffY* = 8
  MotionScale* = 256
  Accel* = 76
  FrictionNum* = 144
  FrictionDen* = 256
  MaxSpeed* = 704
  StopThreshold* = 8
  MovementSlideMaxScan = 3
  TargetFps* = 24
  StuckPenaltyTicks* = TargetFps * 20
  ConnectTimeoutTicks* = TargetFps * 30
  DisconnectTimeoutTicks* = TargetFps * 30
  SpaceColor* = 0'u8
  MapVoidColor* = 12'u8
  TintColor* = 3'u8
  ShadeTintColor* = 9'u8
  OutlineColor* = 0'u8
  KillRange* = 20
  KillCooldownTicks* = 500
  GameInfoTicks* = 3 * TargetFps
  RoleRevealTicks* = 120
  TaskCompleteTicks* = 72
  TaskBarWidth* = 14
  VentRange* = 16
  TaskBarGap* = 1
  ProgressEmpty* = 1'u8
  ProgressFilled* = 10'u8
  ReportRange* = 20
  VoteResultTicks* = 72
  MaxPlayers* = 16
  MinPlayers* = 8
  ImposterCount* = 2
  AutoImposterCount* = true
  StartWaitTicks* = 5 * TargetFps
  VoteTimerTicks* = TargetFps * 10
  MessageCooldownTicks* = 100
  GameOverTicks* = 360
  MaxTicks* = 10_000  ## 0 = no limit.
  MaxGames* = 0  ## 0 = no limit.
  TasksPerPlayer* = 8
  ShowTaskArrows* = true
  ButtonCalls* = 1
  VoteChatVisibleMessages* = 6
  VoteChatIconX* = 1
  VoteChatTextX* = VoteChatIconX + VoteActorSize + 1
  VoteChatRightPad* = 1
  VoteChatTextPixels* = ScreenWidth - VoteChatTextX - VoteChatRightPad
  VoteChatCharsPerLine* = 32
  VoteChatLineCount* = 10
  VoteChatMaxChars* = VoteChatCharsPerLine * VoteChatLineCount
  RandomSeedSentinel* = -1
  RandomSeedMod = int(high(int32))
  ScreenPixelCount = ScreenWidth * ScreenHeight
  ShadowOriginSx =
    ScreenWidth div 2 + SpriteDrawOffX + CollisionW div 2 - SpriteSize div 2
  ShadowOriginSy =
    ScreenHeight div 2 + SpriteDrawOffY + CollisionH div 2 - SpriteSize div 2
  TextColor* = 2'u8
  TextLineHeight* = 7
  TaskReward* = 1
  KillReward* = 10
  WinReward* = 100
  VoteTimeoutPenalty* = -10
  StuckPenalty* = -1
  ConnectionTimeoutPenalty* = -100
  MapSpriteId* = 1
  MapObjectId* = 1
  MapLayerId* = 0
  PovLayerId* = 7
  PovObjectIdOffset* = 30000
  PovSpriteIdOffset* = 30000
  MapLayerType* = 0
  FullScreenLayerType* = 9
  TopLeftLayerId* = 1
  TopLeftLayerType* = 1
  BottomRightLayerId* = 3
  BottomRightLayerType* = 3
  ZoomableLayerFlag* = 1
  UiLayerFlag* = 2
  PlayerSpriteBase* = 100
  GhostSpriteBase* = 400
  BodySpriteBase* = 500
  TaskSpriteId* = 700
  SelectedPlayerSpriteBase* = 800
  SelectedGhostSpriteBase* = 1100
  SelectedTextSpriteId* = 4000
  SelectedViewportSpriteId* = 4001
  PlayerObjectBase* = 1000
  BodyObjectBase* = 2000
  TaskObjectBase* = 3000
  SelectedTextObjectId* = 4000
  SelectedViewportObjectId* = 4001
  PlayerColors* = [
    3'u8,
    7,
    8,
    14,
    4,
    11,
    13,
    15,
    1,
    2,
    5,
    6,
    9,
    10,
    12,
    0
  ]
  PlayerColorNames* = [
    "red",
    "orange",
    "yellow",
    "light blue",
    "pink",
    "lime",
    "blue",
    "pale blue",
    "gray",
    "white",
    "dark brown",
    "brown",
    "dark teal",
    "green",
    "dark navy",
    "black"
  ]
  ShadowMap* = [
    0'u8,  #  0 black       -> black
    12,    #  1 gray         -> dark navy
    9,     #  2 white        -> dark teal
    5,     #  3 red          -> dark brown
    5,     #  4 pink         -> dark brown
    0,     #  5 dark brown   -> black
    5,     #  6 brown        -> dark brown
    5,     #  7 orange       -> dark brown
    5,     #  8 yellow       -> dark brown
    12,    #  9 dark teal    -> dark navy
    9,     # 10 green        -> dark teal
    9,     # 11 lime         -> dark teal
    0,     # 12 dark navy    -> black
    12,    # 13 blue         -> dark navy
    12,    # 14 light blue   -> dark navy
    9,     # 15 pale blue    -> dark teal
  ]
  WebSocketPath* = "/player"
  GlobalWebSocketPath* = "/global"
  ReplayWebSocketPath* = "/replay"
  RewardWebSocketPath* = "/reward"

type
  PlayerRole* = enum
    Crewmate
    Imposter

  CrewriftError* = object of ValueError

  GamePhase* = enum
    Lobby
    Playing
    Voting
    VoteResult
    GameOver
    RoleReveal
    GameInfo

  VoteCallKind* = enum
    VoteCalledUnknown
    VoteCalledButton
    VoteCalledBody

  VoteState* = object
    callKind*: VoteCallKind
    callerIndex*: int
    bodyColor*: uint8
    bodySlotId*: int
    votes*: seq[int]
    cursor*: seq[int]
    resultTimer*: int
    voteTimer*: int
    ejectedPlayer*: int

  TaskStation* = object
    name*: string
    resourceName*: string
    x*, y*, w*, h*: int
    completed*: seq[bool]

  Vent* = object
    resourceName*: string
    x*, y*, w*, h*: int
    group*: char
    groupIndex*: int

  Room* = object
    name*: string
    x*, y*, w*, h*: int

  MapRect* = object
    x*, y*, w*, h*: int

  MapPoint* = object
    x*, y*: int

  CrewriftMap* = object
    name*: string
    path*: string
    asepritePath*: string
    width*, height*: int
    mapLayer*, walkLayer*, wallLayer*: int
    button*: MapRect
    home*: MapPoint
    tasks*: seq[TaskStation]
    vents*: seq[Vent]
    rooms*: seq[Room]

  Body* = object
    x*, y*: int
    color*: uint8
    slotId*: int

  CrewSprite* = ref object
    width*, height*: int
    rgba*: seq[uint8]

  ChatMessage* = object
    slotId*: int
    color*: uint8
    text*: string

  RewardAccount* = object
    address*: string
    slotIndex*: int
    role*: PlayerRole
    hasRole*: bool
    won*: bool
    abandoned*: bool
    connectTimeout*: int
    disconnectTimeout*: int
    reward*: int
    winsImposter*: int
    winsCrewmate*: int
    gamesImposter*: int
    gamesCrewmate*: int
    kills*: int
    tasks*: int
    votePlayers*: int
    voteSkip*: int
    voteTimeout*: int

  PlayerSlotConfig* = object
    name*: string
    token*: string
    role*: PlayerRole
    color*: uint8
    hasRole*: bool
    hasColor*: bool

  GameConfig* = object
    motionScale*: int
    accel*: int
    frictionNum*: int
    frictionDen*: int
    maxSpeed*: int
    stopThreshold*: int
    seed*: int
    speed*: int
    killRange*: int
    killCooldownTicks*: int
    gameInfoTicks*: int
    roleRevealTicks*: int
    taskCompleteTicks*: int
    ventRange*: int
    reportRange*: int
    voteResultTicks*: int
    connectTimeoutTicks*: int
    disconnectTimeoutTicks*: int
    minPlayers*: int
    imposterCount*: int
    autoImposterCount*: bool
    startWaitTicks*: int
    voteTimerTicks*: int
    messageCooldownTicks*: int
    gameOverTicks*: int
    maxTicks*: int
    maxGames*: int
    tasksPerPlayer*: int
    showTaskArrows*: bool
    showTaskBubbles*: bool
    showPlayerLabels*: bool
    buttonCalls*: int
    mapPath*: string
    closedRoster*: bool
    slots*: seq[PlayerSlotConfig]

  Player* = object
    x*, y*: int
    homeX*, homeY*: int
    velX*, velY*: int
    carryX*, carryY*: int
    lastMoveTick*: int
    flipH*: bool
    role*: PlayerRole
    alive*: bool
    connected*: bool
    disconnectTick*: int
    killCooldown*: int
    joinOrder*: int
    address*: string
    color*: uint8
    taskProgress*: int
    activeTask*: int
    tasksRewarded*: int
    ventCooldown*: int
    buttonCallsUsed*: int
    lastChatTick*: int
    assignedTasks*: seq[int]
    reward*: int

  ShadowPathCache = object
    ready: bool
    originSx, originSy: int
    starts: seq[int]
    offsets: seq[int32]
    xs, ys: seq[int16]

  PlayerShadowMask = object
    valid: bool
    cameraX, cameraY: int
    originMx, originMy: int
    mask: seq[bool]

  SimServer* = object
    config*: GameConfig
    players*: seq[Player]
    chatMessages*: seq[ChatMessage]
    rewardAccounts*: seq[RewardAccount]
    bodies*: seq[Body]
    crewSprites*: seq[CrewSprite]
    bodySprites*: seq[CrewSprite]
    boneSprite*: Sprite
    killButtonSprite*: Sprite
    taskIconSprite*: Sprite
    ghostSprite*: Sprite
    ghostIconSprite*: Sprite
    gameMap*: CrewriftMap
    tasks*: seq[TaskStation]
    vents*: seq[Vent]
    rooms*: seq[Room]
    mapPixels*: seq[uint8]
    mapRgba*: seq[uint8]
    darkBgPixels*: seq[uint8]
    walkMask*: seq[bool]
    wallMask*: seq[bool]
    shadowBuf*: seq[bool]
    shadowCaches: seq[PlayerShadowMask]
    rng*: Rand
    nextJoinOrder*: int
    tickCount*: int
    gameStartTick*: int
    startWaitTimer*: int
    phase*: GamePhase
    voteState*: VoteState
    asciiSprites*: PixelFont
    winner*: PlayerRole
    gameOverTimer*: int
    gameInfoTimer*: int
    roleRevealTimer*: int
    timeLimitReached*: bool
    needsReregister*: bool
    gameEventLoggingEnabled*: bool
    lastLobbyPlayersLogged*: int
    lastLobbyNeededLogged*: int
    lastLobbySecondsLogged*: int

  PlayerView* = object
    cameraX*, cameraY*: int
    originMx*, originMy*: int
    viewerIsGhost*: bool

const
  SpritePlayerObservationHeaderFeatures = 4
  SpritePlayerObservationGridSize = 32
  SpritePlayerObservationGridFeatures = SpritePlayerObservationGridSize * SpritePlayerObservationGridSize
  SpritePlayerObservationPlayerSlots = MaxPlayers
  SpritePlayerObservationPlayerFeatures = 4
  SpritePlayerObservationBodySlots = MaxPlayers
  SpritePlayerObservationBodyFeatures = 4
  SpritePlayerObservationTaskSlots = 15
  SpritePlayerObservationTaskFeatures = 5
  SpritePlayerObservationGridOffset = SpritePlayerObservationHeaderFeatures
  SpritePlayerObservationPlayerOffset = SpritePlayerObservationGridOffset + SpritePlayerObservationGridFeatures
  SpritePlayerObservationBodyOffset =
    SpritePlayerObservationPlayerOffset + SpritePlayerObservationPlayerSlots * SpritePlayerObservationPlayerFeatures
  SpritePlayerObservationTaskOffset =
    SpritePlayerObservationBodyOffset + SpritePlayerObservationBodySlots * SpritePlayerObservationBodyFeatures
  SpritePlayerObservationFeatures* =
    SpritePlayerObservationTaskOffset + SpritePlayerObservationTaskSlots * SpritePlayerObservationTaskFeatures

  RenderHeaderKillIcon = 1
  RenderHeaderTaskProgress = 2
  RenderHeaderTasksRemaining = 3

  RenderPlayerFlagsFeature = 3

  RenderTaskFlagsFeature = 4

  RenderPlayerPresent = 1'u8
  RenderPlayerAlive = 4'u8
  RenderPlayerFlipH = 16'u8
  RenderPlayerGhost = 32'u8

  RenderTaskIconVisible = 1'u8
  RenderTaskArrowVisible = 2'u8

var
  ShadowPaths: ShadowPathCache

proc gameDir*(): string =
  ## Returns the Crewrift game directory.
  getCurrentDir()

proc clientDataDir*(): string =
  ## Returns the shared client data directory.
  bitworldClient.clientDir() / "data"

proc resolveGamePath*(path: string, baseDir = ""): string =
  ## Resolves a game data path against the map file and game directory.
  let trimmed = path.strip()
  if trimmed.len == 0 or trimmed.isAbsolute():
    return trimmed
  if baseDir.len > 0:
    let basePath = baseDir / trimmed
    if fileExists(basePath):
      return basePath
  if fileExists(trimmed):
    return trimmed
  if baseDir.len > 0:
    return baseDir / trimmed
  gameDir() / trimmed

proc resolveMapPath*(path: string): string =
  ## Resolves a Crewrift resource map path.
  let trimmed =
    if path.strip().len == 0:
      DefaultMapPath
    else:
      path.strip()
  if trimmed.isAbsolute() or fileExists(trimmed):
    trimmed
  else:
    gameDir() / trimmed

proc spriteSheetPath(): string =
  ## Returns the sprite sheet aseprite path.
  gameDir() / SpriteSheetAsepritePath

proc loadSpriteSheet*(): Image =
  ## Loads the sprite sheet from aseprite.
  readAsepriteImage(spriteSheetPath())

proc crewSheetPath(): string =
  ## Returns the crew sprite sheet path.
  let path = clientDataDir() / "crew.aseprite"
  if fileExists(path):
    return path
  gameDir() / "data" / "crew.aseprite"

proc crewSpriteOffset*(sprite: CrewSprite, x, y: int): int =
  ## Returns the RGBA byte offset for one crew sprite pixel.
  (y * sprite.width + x) * 4

proc crewPixelIsTint*(r, g, b, a: uint8): bool =
  ## Returns true when one crew source pixel is pure tint white.
  a >= 20'u8 and r == 255'u8 and g == 255'u8 and b == 255'u8

proc crewPixelIsShade*(r, g, b, a: uint8): bool =
  ## Returns true when one crew source pixel is the darker tint marker.
  a >= 20'u8 and r == 0x9b'u8 and g == 0xad'u8 and b == 0xb7'u8

proc crewSpriteFromImage(image: Image, index, row: int): CrewSprite =
  ## Extracts one raw 16x16 crew sprite from one sheet row.
  result = CrewSprite(
    width: CrewSpriteSize,
    height: CrewSpriteSize,
    rgba: newSeq[uint8](CrewSpriteSize * CrewSpriteSize * 4)
  )
  let
    baseX = index * CrewSpriteSize
    baseY = row * CrewSpriteSize
  for y in 0 ..< CrewSpriteSize:
    for x in 0 ..< CrewSpriteSize:
      let
        pixel = image[baseX + x, baseY + y]
        offset = result.crewSpriteOffset(x, y)
      result.rgba[offset] = pixel.r
      result.rgba[offset + 1] = pixel.g
      result.rgba[offset + 2] = pixel.b
      result.rgba[offset + 3] = pixel.a

proc loadCrewSpriteRow*(row: int, label: string): seq[CrewSprite] =
  ## Loads eight 16x16 crew sprites from one sheet row.
  if row < 0:
    raise newException(CrewriftError, "Crew sprite sheet row is negative.")
  let
    path = crewSheetPath()
    image = readAsepriteImage(path)
  if image.width < CrewSpriteSize * CrewSpriteVariants or
      image.height < CrewSpriteSize * (row + 1):
    raise newException(
      CrewriftError,
      label & " sprite sheet row is missing eight 16x16 sprites: " & path
    )
  for i in 0 ..< CrewSpriteVariants:
    result.add(image.crewSpriteFromImage(i, row))

proc loadCrewSprites*(): seq[CrewSprite] =
  ## Loads the first eight 16x16 living crew sprites.
  loadCrewSpriteRow(0, "Crew")

proc loadCrewBodySprites*(): seq[CrewSprite] =
  ## Loads the first eight 16x16 dead body sprites.
  loadCrewSpriteRow(1, "Crew body")

proc crewVariantIndex*(slotId: int): int =
  ## Returns the crew sprite variant for one player slot.
  if CrewSpriteVariants <= 0:
    return 0
  ((slotId mod CrewSpriteVariants) + CrewSpriteVariants) mod
    CrewSpriteVariants

proc centerPoint(rect: ResourceRect): MapPoint =
  ## Returns the center point for one resource rectangle.
  MapPoint(x: rect.x + rect.w div 2, y: rect.y + rect.h div 2)

proc centerPoint(room: Room): MapPoint =
  ## Returns the center point for one room rectangle.
  MapPoint(x: room.x + room.w div 2, y: room.y + room.h div 2)

proc clampMapX(x: int): int =
  ## Returns an x coordinate inside the map bounds.
  clamp(x, 0, MapWidth - 1)

proc clampMapY(y: int): int =
  ## Returns a y coordinate inside the map bounds.
  clamp(y, 0, MapHeight - 1)

proc roomDistanceSquared*(room: Room, x, y: int): int =
  ## Returns the squared distance from a point to a room edge.
  let
    px = clampMapX(x)
    py = clampMapY(y)
    dx =
      if px < room.x:
        room.x - px
      elif px >= room.x + room.w:
        px - (room.x + room.w - 1)
      else:
        0
    dy =
      if py < room.y:
        room.y - py
      elif py >= room.y + room.h:
        py - (room.y + room.h - 1)
      else:
        0
  dx * dx + dy * dy

proc nearestRoomAt*(
  rooms: openArray[Room],
  x, y: int
): tuple[found: bool, inside: bool, name: string] =
  ## Returns the containing or nearest room for one map point.
  var bestDistance = high(int)
  for room in rooms:
    let distance = room.roomDistanceSquared(x, y)
    if distance == 0:
      return (true, true, room.name)
    if distance < bestDistance:
      bestDistance = distance
      result = (true, false, room.name)

proc resourceNameKey(value: string): string =
  ## Returns a normalized resource name key.
  value.strip().toLowerAscii()

proc isVentResource(name: string): bool =
  ## Returns true when a resource block is a vent marker.
  let key = name.resourceNameKey()
  key.startsWith("vent") and key != "vents"

proc isTaskResource(name: string): bool =
  ## Returns true when a resource block is a task marker.
  name.resourceNameKey() == "task"

proc isRoomResource(name: string): bool =
  ## Returns true when a resource block is a named room rectangle.
  let key = name.resourceNameKey()
  key.len > 0 and key notin ["vents", "tasks", "rooms"] and
    not key.startsWith("vent") and key != "task"

proc ventGroupChar(name: string): char =
  ## Returns the compact vent group id for one resource vent name.
  let key = name.resourceNameKey()
  for i in countdown(key.high, 0):
    if key[i] in {'a' .. 'z', '0' .. '9'}:
      return key[i]
  'v'

proc nextVentGroupIndex(
  counts: var Table[string, int],
  name: string
): int =
  ## Returns the next serial index for one repeated vent resource name.
  let key = name.resourceNameKey()
  result = counts.getOrDefault(key, 0) + 1
  counts[key] = result

proc taskNameForResource(
  rooms: openArray[Room],
  rect: ResourceRect,
  index: int
): string =
  ## Builds a useful task name from the nearest room.
  let
    center = rect.centerPoint()
    room = nearestRoomAt(rooms, center.x, center.y)
  if room.found:
    "Task near " & room.name
  else:
    "Task " & $(index + 1)

proc centeredMapRect(
  center: MapPoint,
  width, height, mapWidth, mapHeight: int
): MapRect =
  ## Builds a map rectangle centered on one point and clamped to the map.
  MapRect(
    x: clamp(center.x - width div 2, 0, max(0, mapWidth - width)),
    y: clamp(center.y - height div 2, 0, max(0, mapHeight - height)),
    w: width,
    h: height
  )

proc validateMapRect(name: string, rect: MapRect, width, height: int) =
  ## Raises if one map rectangle is outside the map.
  if rect.w <= 0 or rect.h <= 0:
    raise newException(CrewriftError, "Map " & name & " size must be positive.")
  if rect.x < 0 or rect.y < 0 or
      rect.x + rect.w > width or rect.y + rect.h > height:
    raise newException(CrewriftError, "Map " & name & " is outside the map.")

proc validateMapPoint(name: string, point: MapPoint, width, height: int) =
  ## Raises if one map point is outside the map.
  if point.x < 0 or point.y < 0 or point.x >= width or point.y >= height:
    raise newException(CrewriftError, "Map " & name & " is outside the map.")

proc validateMap(gameMap: CrewriftMap) =
  ## Raises if a loaded map has invalid geometry.
  if gameMap.asepritePath.len == 0:
    raise newException(CrewriftError, "Map aseprite path cannot be empty.")
  if gameMap.width != MapWidth or gameMap.height != MapHeight:
    raise newException(
      CrewriftError,
      "Map dimensions must be " & $MapWidth & "x" & $MapHeight & "."
    )
  validateMapRect("button", gameMap.button, gameMap.width, gameMap.height)
  validateMapPoint("home", gameMap.home, gameMap.width, gameMap.height)
  for i, task in gameMap.tasks:
    validateMapRect(
      "task " & $i,
      MapRect(x: task.x, y: task.y, w: task.w, h: task.h),
      gameMap.width,
      gameMap.height
    )
  for i, vent in gameMap.vents:
    if vent.groupIndex < 1:
      raise newException(CrewriftError, "Map vent " & $i & " index must be positive.")
    validateMapRect(
      "vent " & $i,
      MapRect(x: vent.x, y: vent.y, w: vent.w, h: vent.h),
      gameMap.width,
      gameMap.height
    )
  for i, room in gameMap.rooms:
    validateMapRect(
      "room " & $i,
      MapRect(x: room.x, y: room.y, w: room.w, h: room.h),
      gameMap.width,
      gameMap.height
    )

proc loadResourceCrewriftMap(
  resolvedPath: string,
  readAsepriteSize = true
): CrewriftMap =
  ## Loads a Crewrift map from a CSS-like resource file.
  let
    baseDir = resolvedPath.splitFile().dir
    asepritePath = resolvedPath.changeFileExt(".aseprite")
  if readAsepriteSize and not fileExists(asepritePath):
    raise newException(
      CrewriftError,
      "Resource map is missing matching aseprite: " & asepritePath
    )

  let rects = loadResourceRects(resolvedPath)
  if rects.len == 0:
    raise newException(
      CrewriftError,
      "Resource map did not contain any rectangles: " & resolvedPath
    )

  result.name = resolvedPath.splitFile().name
  result.path = resolvedPath
  result.asepritePath = resolveGamePath(asepritePath, baseDir)
  if readAsepriteSize:
    let sprite = readAseprite(asepritePath)
    result.width = sprite.header.width
    result.height = sprite.header.height
  else:
    result.width = MapWidth
    result.height = MapHeight
  result.mapLayer = 0
  result.walkLayer = 1
  result.wallLayer = 2

  var
    taskRects: seq[ResourceRect] = @[]
    ventCounts: Table[string, int]
  for rect in rects:
    if rect.name.isTaskResource():
      taskRects.add(rect)
    elif rect.name.isVentResource():
      result.vents.add Vent(
        resourceName: rect.name,
        x: rect.x,
        y: rect.y,
        w: rect.w,
        h: rect.h,
        group: rect.name.ventGroupChar(),
        groupIndex: ventCounts.nextVentGroupIndex(rect.name)
      )
    elif rect.name.isRoomResource():
      result.rooms.add Room(
        name: rect.name,
        x: rect.x,
        y: rect.y,
        w: rect.w,
        h: rect.h
      )

  for i, rect in taskRects:
    result.tasks.add TaskStation(
      name: taskNameForResource(result.rooms, rect, i),
      resourceName: rect.name,
      x: rect.x,
      y: rect.y,
      w: rect.w,
      h: rect.h
    )

  let homeRoom =
    block:
      var index = 0
      for i, room in result.rooms:
        if room.name.resourceNameKey() == "bridge":
          index = i
          break
      index
  if result.rooms.len > 0:
    result.home = result.rooms[homeRoom].centerPoint()
  else:
    result.home = MapPoint(x: result.width div 2, y: result.height div 2)
  result.button = centeredMapRect(
    result.home,
    28,
    34,
    result.width,
    result.height
  )
  result.validateMap()

proc loadCrewriftMap*(path = ""): CrewriftMap =
  ## Loads a Crewrift resource map and its matching Aseprite file.
  let resolvedPath = resolveMapPath(path)
  if resolvedPath.splitFile().ext.toLowerAscii() != ".resources":
    raise newException(
      CrewriftError,
      "Map path must be a .resources file: " & resolvedPath
    )
  loadResourceCrewriftMap(resolvedPath)

proc loadCrewriftMapMetadata*(path = ""): CrewriftMap =
  ## Loads resource map metadata without opening map image assets.
  let resolvedPath = resolveMapPath(path)
  if resolvedPath.splitFile().ext.toLowerAscii() != ".resources":
    raise newException(
      CrewriftError,
      "Map path must be a .resources file: " & resolvedPath
    )
  loadResourceCrewriftMap(resolvedPath, readAsepriteSize = false)

proc asepritePixelAt(
  aseprite: AsepriteSprite,
  cel: AsepriteCel,
  i: int
): ColorRGBA =
  ## Converts one decoded aseprite cel pixel to RGBA.
  case aseprite.header.colorDepth
  of DepthRgba:
    let base = i * 4
    rgba(
      cel.data[base],
      cel.data[base + 1],
      cel.data[base + 2],
      cel.data[base + 3]
    )
  of DepthGrayscale:
    let base = i * 2
    rgba(cel.data[base], cel.data[base], cel.data[base], cel.data[base + 1])
  of DepthIndexed:
    let index = cel.data[i].int
    if index == aseprite.header.transparentIndex:
      rgba(0, 0, 0, 0)
    elif index < aseprite.palette.len:
      aseprite.palette[index]
    else:
      rgba(0, 0, 0, 0)

proc asepriteLayerImage(
  aseprite: AsepriteSprite,
  layerIndex: int
): Image =
  ## Renders one normal aseprite layer from the first frame.
  if aseprite.frames.len == 0:
    raise newException(CrewriftError, "Map aseprite has no frames.")
  if layerIndex < 0 or layerIndex >= aseprite.layers.len:
    raise newException(
      CrewriftError,
      "Map aseprite is missing layer " & $(layerIndex + 1) & "."
    )
  result = newImage(aseprite.header.width, aseprite.header.height)
  result.fill(rgba(0, 0, 0, 0))
  for cel in aseprite.frames[0].cels:
    if cel.layerIndex != layerIndex:
      continue
    if cel.kind notin {CelRaw, CelCompressed}:
      continue
    for y in 0 ..< cel.height:
      let dstY = cel.y + y
      if dstY < 0 or dstY >= result.height:
        continue
      for x in 0 ..< cel.width:
        let dstX = cel.x + x
        if dstX < 0 or dstX >= result.width:
          continue
        let pixel = aseprite.asepritePixelAt(cel, y * cel.width + x)
        if pixel.a > 0:
          result[dstX, dstY] = pixel

proc loadMapLayers*(gameMap: CrewriftMap): tuple[mapImage, walkImage, wallImage: Image] =
  ## Loads the map, floor mask, and wall mask from aseprite layers.
  let
    path = gameMap.asepritePath
    sprite = readAseprite(path)
  if sprite.header.width != gameMap.width or sprite.header.height != gameMap.height:
    raise newException(
      CrewriftError,
      path & " dimensions must be " &
        $gameMap.width & "x" & $gameMap.height & "."
    )
  (
    mapImage: sprite.asepriteLayerImage(gameMap.mapLayer),
    walkImage: sprite.asepriteLayerImage(gameMap.walkLayer),
    wallImage: sprite.asepriteLayerImage(gameMap.wallLayer)
  )

proc loadSkeld2Layers*(): tuple[mapImage, walkImage, wallImage: Image] =
  ## Loads the default Skeld map layers.
  loadMapLayers(loadCrewriftMap())

proc loadDarkBgPixels*(): seq[uint8] =
  ## Loads the dark interstitial background as palette pixels.
  let image = readAsepriteImage(gameDir() / DarkBgPath)
  if image.width != ScreenWidth or image.height != ScreenHeight:
    raise newException(
      CrewriftError,
      DarkBgPath & " must be " & $ScreenWidth & "x" & $ScreenHeight & "."
    )
  result = newSeq[uint8](ScreenWidth * ScreenHeight)
  for y in 0 ..< ScreenHeight:
    for x in 0 ..< ScreenWidth:
      let color = nearestPaletteIndex(image[x, y])
      result[y * ScreenWidth + x] =
        if color == TransparentColorIndex: SpaceColor else: color

proc asciiIndex*(ch: char): int =
  ## Returns the ASCII sheet index for a character.
  ord(ch) - ord(' ')

proc blitAsciiText*(
  fb: var Framebuffer,
  asciiSprites: PixelFont,
  text: string,
  screenX, screenY: int
) =
  ## Draws text using the Crewrift tiny UI font.
  fb.drawText(asciiSprites, text, screenX, screenY, TextColor)

proc blitCenteredAsciiText*(
  fb: var Framebuffer,
  asciiSprites: PixelFont,
  text: string,
  screenY: int
) =
  ## Draws centered text using the Crewrift tiny UI font.
  let screenX = (ScreenWidth - asciiSprites.textWidth(text)) div 2
  fb.blitAsciiText(asciiSprites, text, screenX, screenY)

proc blitCenteredAsciiText*(
  fb: var Framebuffer,
  asciiSprites: PixelFont,
  text: string,
  screenY,
  offsetX: int
) =
  ## Draws horizontally offset centered text.
  let screenX = (ScreenWidth - asciiSprites.textWidth(text)) div 2 + offsetX
  fb.blitAsciiText(asciiSprites, text, screenX, screenY)

proc cleanChatMessage*(message: string): string =
  ## Returns a printable, bounded chat message.
  let trimmed = message.strip()
  for ch in trimmed:
    if result.len >= VoteChatMaxChars:
      return
    if ch >= ' ' and ch <= '~':
      result.add(ch)

proc nextChatLineStart(
  font: PixelFont,
  text: string,
  startIndex: int
): int =
  ## Returns the next pixel-width chat line start.
  var
    x = 0
    lastSpace = -1
    i = startIndex
  while i < text.len:
    let ch = text[i]
    let advance = font.glyphAdvance(ch)
    if x > 0 and x + advance > VoteChatTextPixels:
      if lastSpace > startIndex:
        result = lastSpace
      else:
        result = i
      while result < text.len and text[result] == ' ':
        inc result
      return
    x += advance
    if ch == ' ':
      lastSpace = i + 1
    inc i
  text.len

proc chatLineStart(font: PixelFont, text: string, lineIndex: int): int =
  ## Returns the source index for one visible chat line.
  result = 0
  for i in 0 ..< lineIndex:
    result = font.nextChatLineStart(text, result)

proc sliceChatLine*(font: PixelFont, text: string, lineIndex: int): string =
  ## Returns one pixel-width chat line.
  let startIndex = font.chatLineStart(text, lineIndex)
  if startIndex >= text.len:
    return ""
  let endIndex = font.nextChatLineStart(text, startIndex)
  result = text[startIndex ..< endIndex]
  result = result.strip()

proc chatLineCount*(font: PixelFont, text: string): int =
  ## Returns the visible line count for one chat message.
  result = 1
  var startIndex = 0
  while startIndex < text.len and result < VoteChatLineCount:
    let nextIndex = font.nextChatLineStart(text, startIndex)
    if nextIndex >= text.len:
      break
    inc result
    startIndex = nextIndex

proc chatMessageHeight*(font: PixelFont, text: string): int =
  ## Returns the pixel height for one chat message row.
  max(VoteActorSize, font.chatLineCount(text) * TextLineHeight) + 1

proc defaultGameConfig*(): GameConfig =
  ## Returns the default Crewrift gameplay config.
  GameConfig(
    motionScale: MotionScale,
    accel: Accel,
    frictionNum: FrictionNum,
    frictionDen: FrictionDen,
    maxSpeed: MaxSpeed,
    stopThreshold: StopThreshold,
    seed: RandomSeedSentinel,
    speed: 1,
    killRange: KillRange,
    killCooldownTicks: KillCooldownTicks,
    gameInfoTicks: GameInfoTicks,
    roleRevealTicks: RoleRevealTicks,
    taskCompleteTicks: TaskCompleteTicks,
    ventRange: VentRange,
    reportRange: ReportRange,
    voteResultTicks: VoteResultTicks,
    connectTimeoutTicks: ConnectTimeoutTicks,
    disconnectTimeoutTicks: DisconnectTimeoutTicks,
    minPlayers: MinPlayers,
    imposterCount: ImposterCount,
    autoImposterCount: AutoImposterCount,
    startWaitTicks: StartWaitTicks,
    voteTimerTicks: VoteTimerTicks,
    messageCooldownTicks: MessageCooldownTicks,
    gameOverTicks: GameOverTicks,
    maxTicks: MaxTicks,
    maxGames: MaxGames,
    tasksPerPlayer: TasksPerPlayer,
    showTaskArrows: ShowTaskArrows,
    showTaskBubbles: true,
    showPlayerLabels: true,
    buttonCalls: ButtonCalls,
    mapPath: DefaultMapPath,
    closedRoster: false,
    slots: @[]
  )

proc readConfigInt(node: JsonNode, name: string, value: var int) =
  ## Reads one optional integer config field.
  if not node.hasKey(name):
    return
  let item = node[name]
  if item.kind != JInt:
    raise newException(CrewriftError, "Config field " & name & " must be an integer.")
  value = item.getInt()

proc readConfigBool(node: JsonNode, name: string, value: var bool) =
  ## Reads one optional boolean config field.
  if not node.hasKey(name):
    return
  let item = node[name]
  if item.kind != JBool:
    raise newException(CrewriftError, "Config field " & name & " must be a boolean.")
  value = item.getBool()

proc readConfigString(node: JsonNode, name: string, value: var string) =
  ## Reads one optional string config field.
  if not node.hasKey(name):
    return
  let item = node[name]
  if item.kind != JString:
    raise newException(CrewriftError, "Config field " & name & " must be a string.")
  value = item.getStr()

proc readSlotRole(text: string, slotIndex: int): PlayerRole =
  ## Reads one slot role string.
  case text.strip().toLowerAscii()
  of "crew":
    Crewmate
  of "imp", "imposter", "impostor":
    Imposter
  else:
    raise newException(
      CrewriftError,
      "Config field slots[" & $slotIndex & "].role must be crew or imposter."
    )

proc normalizedSlotColor(text: string): string =
  ## Returns a normalized slot color name.
  result = text.strip().toLowerAscii()
  result = result.replace("_", " ")
  result = result.replace("-", " ")
  result = result.replace(" ", "")

proc playerColorText*(color: uint8): string =
  ## Returns the readable player color name.
  for i in 0 ..< PlayerColors.len:
    if PlayerColors[i] == color:
      return PlayerColorNames[i]
  "unknown"

proc readSlotColor(text: string, slotIndex: int): uint8 =
  ## Reads one slot color string.
  case text.normalizedSlotColor()
  of "red":
    PlayerColors[0]
  of "orange":
    PlayerColors[1]
  of "yellow":
    PlayerColors[2]
  of "lightblue", "cyan":
    PlayerColors[3]
  of "pink":
    PlayerColors[4]
  of "lime":
    PlayerColors[5]
  of "blue":
    PlayerColors[6]
  of "paleblue":
    PlayerColors[7]
  of "gray", "grey":
    PlayerColors[8]
  of "white":
    PlayerColors[9]
  of "darkbrown":
    PlayerColors[10]
  of "brown":
    PlayerColors[11]
  of "darkteal", "teal":
    PlayerColors[12]
  of "green":
    PlayerColors[13]
  of "darknavy", "navy":
    PlayerColors[14]
  of "black":
    PlayerColors[15]
  else:
    raise newException(
      CrewriftError,
      "Config field slots[" & $slotIndex & "].color is unknown."
    )

proc readConfigSlots(node: JsonNode, slots: var seq[PlayerSlotConfig]) =
  ## Reads optional fixed player slot config entries.
  if not node.hasKey("slots"):
    return
  let items = node["slots"]
  if items.kind != JArray:
    raise newException(CrewriftError, "Config field slots must be an array.")
  slots.setLen(0)
  for i, item in items.elems:
    if item.kind != JObject:
      raise newException(
        CrewriftError,
        "Config field slots[" & $i & "] must be an object."
      )
    if item.hasKey("name"):
      raise newException(
        CrewriftError,
        "Config field slots[" & $i & "].name is not supported; use players[" &
          $i & "].name instead."
      )
    var slot: PlayerSlotConfig
    item.readConfigString("token", slot.token)
    if item.hasKey("role"):
      let role = item["role"]
      if role.kind != JString:
        raise newException(
          CrewriftError,
          "Config field slots[" & $i & "].role must be a string."
        )
      slot.role = readSlotRole(role.getStr(), i)
      slot.hasRole = true
    if item.hasKey("color"):
      let color = item["color"]
      if color.kind != JString:
        raise newException(
          CrewriftError,
          "Config field slots[" & $i & "].color must be a string."
        )
      slot.color = readSlotColor(color.getStr(), i)
      slot.hasColor = true
    slots.add(slot)

proc readConfigPlayers(node: JsonNode, slots: var seq[PlayerSlotConfig]) =
  ## Reads optional fixed player display names by slot index.
  if node.hasKey("player_names"):
    raise newException(
      CrewriftError,
      "Config field player_names is not supported; use players[].name instead."
    )
  if not node.hasKey("players"):
    return
  let items = node["players"]
  if items.kind != JArray:
    raise newException(CrewriftError, "Config field players must be an array.")
  if items.len > MaxPlayers:
    raise newException(
      CrewriftError,
      "Config field players cannot have more than 16 entries."
    )
  if slots.len < items.len:
    slots.setLen(items.len)
  for i, item in items.elems:
    if item.kind != JObject:
      raise newException(
        CrewriftError,
        "Config field players[" & $i & "] must be an object."
      )
    if not item.hasKey("name"):
      raise newException(
        CrewriftError,
        "Config field players[" & $i & "].name is required."
      )
    let nameNode = item["name"]
    if nameNode.kind != JString:
      raise newException(
        CrewriftError,
        "Config field players[" & $i & "].name must be a string."
      )
    let name = nameNode.getStr()
    if name.len == 0:
      raise newException(
        CrewriftError,
        "Config field players[" & $i & "].name must not be empty."
      )
    slots[i].name = name

proc defaultSlotName(slotIndex: int): string =
  ## Returns the canonical name for one generated tournament slot.
  "Player" & $(slotIndex + 1)

proc readConfigTokens(
  node: JsonNode,
  slots: var seq[PlayerSlotConfig],
  closedRoster: bool
) =
  ## Reads optional fixed player slot tokens.
  if not node.hasKey("tokens"):
    return
  let items = node["tokens"]
  if items.kind != JArray:
    raise newException(CrewriftError, "Config field tokens must be an array.")
  if items.len > MaxPlayers:
    raise newException(
      CrewriftError,
      "Config field tokens cannot have more than 16 entries."
    )
  if slots.len < items.len:
    slots.setLen(items.len)
  for i, item in items.elems:
    if item.kind != JString:
      raise newException(
        CrewriftError,
        "Config field tokens[" & $i & "] must be a string."
      )
    let token = item.getStr()
    if slots[i].token.len > 0 and slots[i].token != token:
      raise newException(
        CrewriftError,
        "Config field tokens[" & $i & "] conflicts with slots[" & $i &
          "].token."
      )
    slots[i].token = token
    if closedRoster and slots[i].name.len == 0:
      slots[i].name = defaultSlotName(i)

proc validate(config: GameConfig) =
  ## Raises if a gameplay config has invalid values.
  if config.motionScale <= 0:
    raise newException(CrewriftError, "Config field motionScale must be positive.")
  if config.frictionDen <= 0:
    raise newException(CrewriftError, "Config field frictionDen must be positive.")
  if config.seed < RandomSeedSentinel:
    raise newException(
      CrewriftError,
      "Config field seed must be -1 or greater."
    )
  if config.minPlayers < 1:
    raise newException(CrewriftError, "Config field minPlayers must be at least 1.")
  if config.minPlayers > MaxPlayers:
    raise newException(CrewriftError, "can't do more than 16 players.")
  if config.imposterCount < 0:
    raise newException(CrewriftError, "Config field imposterCount must be non-negative.")
  if config.speed notin [1, 2, 3, 4, 8, 16]:
    raise newException(
      CrewriftError,
      "Config field speed must be 1, 2, 3, 4, 8, or 16."
    )
  if config.startWaitTicks < 0:
    raise newException(CrewriftError, "Config field startWaitTicks must be non-negative.")
  if config.tasksPerPlayer < 0:
    raise newException(CrewriftError, "Config field tasksPerPlayer must be non-negative.")
  if config.buttonCalls < 0:
    raise newException(CrewriftError, "Config field buttonCalls must be non-negative.")
  if config.roleRevealTicks < 0:
    raise newException(CrewriftError, "Config field roleRevealTicks must be non-negative.")
  if config.gameInfoTicks < 0:
    raise newException(CrewriftError, "Config field gameInfoTicks must be non-negative.")
  if config.voteTimerTicks <= 0:
    raise newException(CrewriftError, "Config field voteTimerTicks must be positive.")
  if config.connectTimeoutTicks < 0:
    raise newException(
      CrewriftError,
      "Config field connectTimeoutTicks must be non-negative."
    )
  if config.disconnectTimeoutTicks < 0:
    raise newException(
      CrewriftError,
      "Config field disconnectTimeoutTicks must be non-negative."
    )
  if config.messageCooldownTicks < 0:
    raise newException(CrewriftError, "Config field messageCooldownTicks must be non-negative.")
  if config.killCooldownTicks < 0 or config.gameOverTicks < 0 or
      config.voteResultTicks < 0 or config.maxTicks < 0 or
      config.maxGames < 0:
    raise newException(CrewriftError, "Timer config fields must not be negative.")
  if config.slots.len > MaxPlayers:
    raise newException(CrewriftError, "Config field slots cannot have more than 16 entries.")
  if config.closedRoster and config.slots.len < config.minPlayers:
    raise newException(
      CrewriftError,
      "Config field closedRoster requires at least minPlayers configured slots."
    )
  if config.closedRoster:
    for i, slot in config.slots:
      if slot.name.len == 0:
        raise newException(
          CrewriftError,
          "Config field closedRoster requires players[" & $i & "].name."
        )
      if slot.token.len == 0:
        raise newException(
          CrewriftError,
          "Config field closedRoster requires slots[" & $i & "].token."
        )
  for i in 0 ..< config.slots.len:
    for j in i + 1 ..< config.slots.len:
      if config.slots[i].name.len > 0 and
          config.slots[i].name == config.slots[j].name:
        raise newException(
          CrewriftError,
          "Config field players has duplicate name " & config.slots[i].name & "."
        )
      if config.slots[i].token.len > 0 and
          config.slots[i].token == config.slots[j].token:
        raise newException(
          CrewriftError,
          "Config field slots has duplicate token."
        )

proc update*(config: var GameConfig, jsonText: string) =
  ## Updates a gameplay config from a JSON object.
  if jsonText.len == 0:
    return
  var node: JsonNode
  try:
    node = fromJson(jsonText)
  except jsony.JsonError as e:
    raise newException(CrewriftError, "Could not parse config JSON: " & e.msg)
  if node.kind != JObject:
    raise newException(CrewriftError, "Config must be a JSON object.")
  node.readConfigInt("motionScale", config.motionScale)
  node.readConfigInt("accel", config.accel)
  node.readConfigInt("frictionNum", config.frictionNum)
  node.readConfigInt("frictionDen", config.frictionDen)
  node.readConfigInt("maxSpeed", config.maxSpeed)
  node.readConfigInt("stopThreshold", config.stopThreshold)
  node.readConfigInt("seed", config.seed)
  node.readConfigInt("speed", config.speed)
  node.readConfigInt("killRange", config.killRange)
  node.readConfigInt("killCooldownTicks", config.killCooldownTicks)
  node.readConfigInt("gameInfoTicks", config.gameInfoTicks)
  node.readConfigInt("roleRevealTicks", config.roleRevealTicks)
  node.readConfigInt("taskCompleteTicks", config.taskCompleteTicks)
  node.readConfigInt("ventRange", config.ventRange)
  node.readConfigInt("reportRange", config.reportRange)
  node.readConfigInt("voteResultTicks", config.voteResultTicks)
  node.readConfigInt("connectTimeoutTicks", config.connectTimeoutTicks)
  node.readConfigInt("disconnectTimeoutTicks", config.disconnectTimeoutTicks)
  node.readConfigInt("minPlayers", config.minPlayers)
  let
    hasImposterCount = node.hasKey("imposterCount")
    hasAutoImposterCount =
      node.hasKey("autoImposterCount") or node.hasKey("imposterRatio")
  node.readConfigInt("imposterCount", config.imposterCount)
  node.readConfigBool("autoImposterCount", config.autoImposterCount)
  node.readConfigBool("imposterRatio", config.autoImposterCount)
  if hasImposterCount and not hasAutoImposterCount:
    config.autoImposterCount = false
  node.readConfigInt("startWaitTicks", config.startWaitTicks)
  node.readConfigInt("gameStartWaitTicks", config.startWaitTicks)
  node.readConfigInt("voteTimerTicks", config.voteTimerTicks)
  node.readConfigInt("messageCooldownTicks", config.messageCooldownTicks)
  node.readConfigInt("gameOverTicks", config.gameOverTicks)
  node.readConfigInt("maxTicks", config.maxTicks)
  node.readConfigInt("maxGameTicks", config.maxTicks)
  node.readConfigInt("maxGames", config.maxGames)
  node.readConfigInt("tasksPerPlayer", config.tasksPerPlayer)
  node.readConfigInt("buttonCalls", config.buttonCalls)
  node.readConfigInt("numberOfButtonCalls", config.buttonCalls)
  node.readConfigBool("showTaskArrows", config.showTaskArrows)
  node.readConfigBool("showTaskBubbles", config.showTaskBubbles)
  node.readConfigBool("showPlayerLabels", config.showPlayerLabels)
  node.readConfigString("map", config.mapPath)
  node.readConfigString("mapPath", config.mapPath)
  node.readConfigSlots(config.slots)
  node.readConfigBool("closedRoster", config.closedRoster)
  node.readConfigTokens(config.slots, config.closedRoster)
  node.readConfigPlayers(config.slots)
  config.validate()

proc timeGameSeed*(): int =
  ## Returns a positive game seed derived from wall-clock time.
  result = int(epochTime() * 1000) mod RandomSeedMod
  if result < 0:
    result += RandomSeedMod

proc resolveRandomSeed*(config: var GameConfig) =
  ## Replaces the random seed sentinel with a concrete seed.
  if config.seed == RandomSeedSentinel:
    config.seed = timeGameSeed()

proc slotRoleText(slot: PlayerSlotConfig): string =
  ## Returns a JSON role string for one slot.
  if not slot.hasRole:
    return ""
  case slot.role
  of Crewmate:
    "crew"
  of Imposter:
    "imposter"

proc slotColorText(slot: PlayerSlotConfig): string =
  ## Returns a JSON color string for one slot.
  if not slot.hasColor:
    return ""
  playerColorText(slot.color)

proc configJson*(config: GameConfig): string =
  ## Returns the complete replay JSON for a gameplay config.
  var
    players = newJArray()
    slots = newJArray()
    tokens = newJArray()
    includePlayers = false
  for slot in config.slots:
    var item = newJObject()
    if slot.name.len > 0:
      includePlayers = true
    tokens.add(%slot.token)
    players.add(%*{"name": slot.name})
    if slot.hasRole:
      item["role"] = %slot.slotRoleText()
    if slot.hasColor:
      item["color"] = %slot.slotColorText()
    slots.add(item)
  var node = %*{
    "motionScale": config.motionScale,
    "accel": config.accel,
    "frictionNum": config.frictionNum,
    "frictionDen": config.frictionDen,
    "maxSpeed": config.maxSpeed,
    "stopThreshold": config.stopThreshold,
    "seed": config.seed,
    "speed": config.speed,
    "killRange": config.killRange,
    "killCooldownTicks": config.killCooldownTicks,
    "gameInfoTicks": config.gameInfoTicks,
    "roleRevealTicks": config.roleRevealTicks,
    "taskCompleteTicks": config.taskCompleteTicks,
    "ventRange": config.ventRange,
    "reportRange": config.reportRange,
    "voteResultTicks": config.voteResultTicks,
    "connectTimeoutTicks": config.connectTimeoutTicks,
    "disconnectTimeoutTicks": config.disconnectTimeoutTicks,
    "minPlayers": config.minPlayers,
    "imposterCount": config.imposterCount,
    "autoImposterCount": config.autoImposterCount,
    "startWaitTicks": config.startWaitTicks,
    "voteTimerTicks": config.voteTimerTicks,
    "messageCooldownTicks": config.messageCooldownTicks,
    "gameOverTicks": config.gameOverTicks,
    "maxTicks": config.maxTicks,
    "maxGameTicks": config.maxTicks,
    "maxGames": config.maxGames,
    "tasksPerPlayer": config.tasksPerPlayer,
    "buttonCalls": config.buttonCalls,
    "mapPath": config.mapPath,
    "closedRoster": config.closedRoster,
    "showTaskArrows": config.showTaskArrows,
    "showTaskBubbles": config.showTaskBubbles,
    "showPlayerLabels": config.showPlayerLabels,
    "tokens": tokens,
    "slots": slots
  }
  if includePlayers:
    node["players"] = players
  $node

proc ratioImposterCount*(playerCount: int): int =
  ## Returns the default impostor count for a player count.
  if playerCount < 5:
    return 0
  (playerCount - 3) div 2

proc effectiveImposterCount*(config: GameConfig, playerCount: int): int =
  ## Returns the active impostor count for a config and player count.
  let desired =
    if config.autoImposterCount:
      ratioImposterCount(playerCount)
    else:
      config.imposterCount
  min(desired, max(0, playerCount - 1))

proc lobbyIsStarting*(sim: SimServer): bool =
  ## Returns whether the lobby is in the start countdown.
  sim.players.len >= sim.config.minPlayers

proc lobbyStartTicksRemaining*(sim: SimServer): int =
  ## Returns ticks left before the lobby starts the game.
  if not sim.lobbyIsStarting() or sim.config.startWaitTicks <= 0:
    return 0
  if sim.startWaitTimer > 0:
    sim.startWaitTimer
  else:
    sim.config.startWaitTicks

proc lobbyStartSecondsRemaining*(sim: SimServer): int =
  ## Returns visible seconds left before the lobby starts the game.
  let ticks = sim.lobbyStartTicksRemaining()
  if ticks <= 0:
    return 0
  max(1, (ticks + TargetFps - 1) div TargetFps)

proc roleText(role: PlayerRole): string =
  ## Returns the readable role name.
  case role
  of Crewmate:
    "crew"
  of Imposter:
    "imposter"

proc playerText(sim: SimServer, playerIndex: int): string =
  ## Returns the readable player color for one player index.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return "unknown"
  playerColorText(sim.players[playerIndex].color)

proc taskIdsText(tasks: openArray[int]): string =
  ## Returns a compact comma-separated task id list.
  for i, task in tasks:
    if i > 0:
      result.add ","
    result.add $task

proc requiredLobbyPlayers(sim: SimServer): int =
  ## Returns the player count required before the lobby can start.
  if sim.config.closedRoster and sim.config.slots.len > 0:
    return sim.config.slots.len
  sim.config.minPlayers

proc logGameEvent(sim: SimServer, text: string) =
  ## Writes one game event to stdout for Docker logs.
  if sim.gameEventLoggingEnabled:
    echo text

proc logTaskAssignments(
  sim: SimServer,
  crew: openArray[int],
  assignments: openArray[taskAssignments.TaskAssignment]
) =
  ## Logs a compact table of crewmate tasks and route distances.
  if crew.len == 0:
    return
  sim.logGameEvent("crewmate tasks:")
  sim.logGameEvent("slot  color       path  tasks")
  sim.logGameEvent("----  ----------  ----  ----------------")
  for i, playerIndex in crew:
    let assignment = assignments[i]
    sim.logGameEvent(
      align($sim.players[playerIndex].joinOrder, 4) & "  " &
      align(sim.playerText(playerIndex), 10) & "  " &
      align($assignment.routeCost, 4) & "  " &
      assignment.taskIds.taskIdsText()
    )

proc voteTargetText(sim: SimServer, vote: int): string =
  ## Returns a readable vote target.
  if vote == -2:
    return "skip"
  if vote >= 0 and vote < sim.players.len:
    return sim.playerText(vote)
  "unknown"

proc logLobbyWaiting(sim: var SimServer) =
  ## Logs waiting-for-player state when it changes.
  let
    required = sim.requiredLobbyPlayers()
    needed = max(0, required - sim.players.len)
    players = sim.players.len
  if players == sim.lastLobbyPlayersLogged and
      needed == sim.lastLobbyNeededLogged:
    return
  sim.lastLobbyPlayersLogged = players
  sim.lastLobbyNeededLogged = needed
  sim.lastLobbySecondsLogged = -1
  sim.logGameEvent(
    "waiting for players: " & $players & "/" &
      $required & ", need " & $needed & " more"
  )

proc logLobbyCountdown(sim: var SimServer) =
  ## Logs the lobby countdown once per visible second.
  let seconds = sim.lobbyStartSecondsRemaining()
  if seconds <= 0 or seconds == sim.lastLobbySecondsLogged:
    return
  sim.lastLobbySecondsLogged = seconds
  sim.logGameEvent("game starting in " & $seconds)

proc lobbyIconStartY*(sim: SimServer): int =
  ## Returns the lobby icon row y coordinate.
  if sim.lobbyIsStarting(): 32 else: 26

proc mapIndex*(x, y: int): int {.inline.} =
  y * MapWidth + x

proc mixHash(hash: var uint64, value: uint64) =
  ## Mixes one integer into a deterministic FNV-1a hash.
  hash = hash xor value
  hash *= 1099511628211'u64

proc mixHashInt(hash: var uint64, value: int) =
  ## Mixes one signed integer into a deterministic hash.
  hash.mixHash(cast[uint64](int64(value)))

proc mixHashBool(hash: var uint64, value: bool) =
  ## Mixes one boolean into a deterministic hash.
  hash.mixHashInt(ord(value))

proc gameHash*(sim: SimServer): uint64 =
  ## Returns a deterministic hash of gameplay state.
  result = 14695981039346656037'u64
  result.mixHashInt(sim.tickCount)
  result.mixHashInt(ord(sim.phase))
  result.mixHashInt(ord(sim.winner))
  result.mixHashInt(sim.gameOverTimer)
  result.mixHashInt(sim.roleRevealTimer)
  result.mixHashInt(sim.gameStartTick)
  result.mixHashInt(sim.startWaitTimer)
  result.mixHashBool(sim.timeLimitReached)
  result.mixHashBool(sim.needsReregister)
  result.mixHashInt(sim.nextJoinOrder)
  result.mixHashInt(sim.players.len)
  for player in sim.players:
    result.mixHashInt(player.x)
    result.mixHashInt(player.y)
    result.mixHashInt(player.homeX)
    result.mixHashInt(player.homeY)
    result.mixHashInt(player.velX)
    result.mixHashInt(player.velY)
    result.mixHashInt(player.carryX)
    result.mixHashInt(player.carryY)
    result.mixHashInt(player.lastMoveTick)
    result.mixHashBool(player.flipH)
    result.mixHashInt(ord(player.role))
    result.mixHashBool(player.alive)
    result.mixHashBool(player.connected)
    result.mixHashInt(player.disconnectTick)
    result.mixHashInt(player.killCooldown)
    result.mixHashInt(player.joinOrder)
    result.mixHashInt(int(player.color))
    result.mixHashInt(player.taskProgress)
    result.mixHashInt(player.activeTask)
    result.mixHashInt(player.tasksRewarded)
    result.mixHashInt(player.ventCooldown)
    result.mixHashInt(player.buttonCallsUsed)
    result.mixHashInt(player.lastChatTick)
    result.mixHashInt(player.reward)
    result.mixHashInt(player.assignedTasks.len)
    for task in player.assignedTasks:
      result.mixHashInt(task)
  result.mixHashInt(sim.bodies.len)
  for body in sim.bodies:
    result.mixHashInt(body.x)
    result.mixHashInt(body.y)
    result.mixHashInt(int(body.color))
    result.mixHashInt(body.slotId)
  result.mixHashInt(sim.tasks.len)
  for task in sim.tasks:
    result.mixHashInt(task.completed.len)
    for done in task.completed:
      result.mixHashBool(done)
  result.mixHashInt(sim.voteState.votes.len)
  for vote in sim.voteState.votes:
    result.mixHashInt(vote)
  result.mixHashInt(sim.voteState.cursor.len)
  for cursor in sim.voteState.cursor:
    result.mixHashInt(cursor)
  result.mixHashInt(sim.voteState.resultTimer)
  result.mixHashInt(sim.voteState.voteTimer)
  result.mixHashInt(sim.voteState.ejectedPlayer)

proc isWalkable*(sim: SimServer, x, y: int): bool =
  if x < 0 or y < 0 or x >= MapWidth or y >= MapHeight:
    return false
  sim.walkMask[mapIndex(x, y)]

proc canOccupy*(sim: SimServer, x, y: int): bool =
  for dy in 0 ..< CollisionH:
    for dx in 0 ..< CollisionW:
      if not sim.isWalkable(x + dx, y + dy):
        return false
  true

proc homePosition*(sim: SimServer, index, total: int): tuple[x, y: int] =
  ## Returns one deterministic home position around the meeting button.
  let
    homeX = sim.gameMap.home.x
    homeY = sim.gameMap.home.y
    spawnRadius = 28
    n = max(1, total)
    angle = float(index) * 2.0 * 3.14159265 / float(n)
    px = homeX + int(float(spawnRadius) * cos(angle))
    py = homeY + int(float(spawnRadius) * sin(angle))
  if sim.canOccupy(px, py):
    return (px, py)
  (homeX, homeY)

proc resetPlayerToHome*(sim: var SimServer, playerIndex: int) =
  ## Moves one player back to its saved meeting home position.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  sim.players[playerIndex].x = sim.players[playerIndex].homeX
  sim.players[playerIndex].y = sim.players[playerIndex].homeY
  sim.players[playerIndex].velX = 0
  sim.players[playerIndex].velY = 0
  sim.players[playerIndex].carryX = 0
  sim.players[playerIndex].carryY = 0
  sim.players[playerIndex].lastMoveTick = sim.tickCount
  sim.players[playerIndex].activeTask = -1
  sim.players[playerIndex].taskProgress = 0

proc arrangeHomePositions*(sim: var SimServer) =
  ## Saves and applies evenly spaced home positions for all players.
  var total = sim.players.len
  for player in sim.players:
    total = max(total, player.joinOrder + 1)
  for i in 0 ..< sim.players.len:
    let slot = sim.players[i].joinOrder
    let home = sim.homePosition(slot, total)
    sim.players[i].homeX = home.x
    sim.players[i].homeY = home.y
    sim.resetPlayerToHome(i)

proc findSpawn*(sim: SimServer): tuple[x, y: int] =
  ## Returns the next lobby spawn position.
  sim.homePosition(sim.players.len, sim.players.len + 1)

proc playerSlotLimit(config: GameConfig): int =
  ## Returns the number of slots players may occupy.
  if config.closedRoster: config.slots.len else: MaxPlayers

proc canAddPlayer*(sim: SimServer): bool =
  ## Returns whether the game has room for another player.
  sim.players.len < sim.config.playerSlotLimit()

proc playerLimitError(config: GameConfig): string =
  ## Returns a user-facing message for the current player cap.
  if config.closedRoster:
    let limit = config.playerSlotLimit()
    return "Configured roster is full (" & $limit &
      (if limit == 1: " player)." else: " players).")
  "can't do more than " & $MaxPlayers & " players."

proc slotConfig(config: GameConfig, slotIndex: int): PlayerSlotConfig =
  ## Returns one slot config or an empty config for missing entries.
  if slotIndex >= 0 and slotIndex < config.slots.len:
    config.slots[slotIndex]
  else:
    PlayerSlotConfig()

proc slotRestricted(config: GameConfig, slotIndex: int): bool =
  ## Returns true when a slot has identity restrictions.
  let slot = config.slotConfig(slotIndex)
  slot.name.len > 0 or slot.token.len > 0

proc slotAuthMatches(
  config: GameConfig,
  slotIndex: int,
  address,
  token: string
): bool =
  ## Returns true when a player satisfies one configured slot.
  let slot = config.slotConfig(slotIndex)
  if slot.name.len > 0 and address != slot.name:
    return false
  if slot.token.len > 0 and token != slot.token:
    return false
  true

proc hasConfiguredToken(config: GameConfig, token: string): bool =
  ## Returns true when a token matches any configured slot.
  for slot in config.slots:
    if slot.token.len > 0 and slot.token == token:
      return true
  false

proc hasConfiguredTokens(config: GameConfig): bool =
  ## Returns true when any slot has an auth token.
  for slot in config.slots:
    if slot.token.len > 0:
      return true
  false

proc validatePlayerSlot(
  config: GameConfig,
  slotIndex: int,
  address,
  token: string
) =
  ## Raises when a player does not satisfy one configured slot.
  let slot = config.slotConfig(slotIndex)
  if slot.name.len > 0 and address != slot.name:
    raise newException(
      CrewriftError,
      "Player name does not match configured slot " & $slotIndex & "."
    )
  if slot.token.len > 0 and token != slot.token:
    raise newException(
      CrewriftError,
      "Player token does not match configured slot " & $slotIndex & "."
    )

proc configuredPlayerName*(config: GameConfig, requestedSlot: int, token: string): string =
  ## Returns the configured identity for a tokenized slot request.
  if token.len == 0:
    return ""
  if requestedSlot >= 0 and requestedSlot < config.slots.len:
    let slot = config.slots[requestedSlot]
    if slot.name.len > 0 and slot.token.len > 0 and slot.token == token:
      return slot.name
    return ""
  for slot in config.slots:
    if slot.name.len > 0 and slot.token.len > 0 and slot.token == token:
      return slot.name
  ""

proc playerJoinAllowed*(
  config: GameConfig,
  address: string,
  requestedSlot: int,
  token: string
): bool =
  ## Returns whether a player websocket request can pass configured slot auth.
  if requestedSlot >= config.playerSlotLimit():
    return false
  if token.len > 0 and config.hasConfiguredTokens() and
      not config.hasConfiguredToken(token):
    return false
  if requestedSlot >= 0:
    return config.slotAuthMatches(requestedSlot, address, token)
  for i in 0 ..< config.slots.len:
    let slot = config.slots[i]
    let matchedName = slot.name.len > 0 and slot.name == address
    let matchedToken =
      slot.token.len > 0 and token.len > 0 and slot.token == token
    if matchedName or matchedToken:
      return config.slotAuthMatches(i, address, token)
  not config.closedRoster

proc slotOccupied(sim: SimServer, slotIndex: int): bool =
  ## Returns true when a player already owns a slot.
  for player in sim.players:
    if player.joinOrder == slotIndex:
      return true
  false

proc matchingConfiguredSlot(
  sim: SimServer,
  address,
  token: string
): int =
  ## Returns a matching configured slot for a player or -1.
  for i in 0 ..< sim.config.slots.len:
    if sim.slotOccupied(i):
      continue
    let slot = sim.config.slots[i]
    let couldMatchName = slot.name.len > 0 and slot.name == address
    let couldMatchToken = slot.token.len > 0 and slot.token == token
    if (couldMatchName or couldMatchToken) and
        sim.config.slotAuthMatches(i, address, token):
      return i
  -1

proc conflictingConfiguredSlot(
  sim: SimServer,
  address,
  token: string
): int =
  ## Returns a configured slot matched by name or token but not both.
  for i in 0 ..< sim.config.slots.len:
    if sim.slotOccupied(i):
      continue
    let slot = sim.config.slots[i]
    let matchedName = slot.name.len > 0 and slot.name == address
    let matchedToken =
      slot.token.len > 0 and token.len > 0 and slot.token == token
    if (matchedName or matchedToken) and
        not sim.config.slotAuthMatches(i, address, token):
      return i
  -1

proc namedConfiguredSlot(sim: SimServer, address: string): int =
  ## Returns an open configured slot with a matching name.
  for i in 0 ..< sim.config.slots.len:
    if sim.slotOccupied(i):
      continue
    let slot = sim.config.slots[i]
    if slot.name.len > 0 and slot.name == address:
      return i
  -1

proc nextAutoSlot(sim: SimServer, address, token: string): int =
  ## Returns the next open unrestricted or matching slot.
  let slotLimit = sim.config.playerSlotLimit()
  for i in sim.nextJoinOrder ..< slotLimit:
    if sim.slotOccupied(i):
      continue
    if not sim.config.slotRestricted(i) or
        sim.config.slotAuthMatches(i, address, token):
      return i
  for i in 0 ..< sim.nextJoinOrder:
    if i >= slotLimit:
      break
    if sim.slotOccupied(i):
      continue
    if not sim.config.slotRestricted(i) or
        sim.config.slotAuthMatches(i, address, token):
      return i
  -1

proc advanceJoinOrder(sim: var SimServer) =
  ## Moves the auto-slot cursor to the next open slot.
  while sim.nextJoinOrder < MaxPlayers and
      sim.slotOccupied(sim.nextJoinOrder):
    inc sim.nextJoinOrder

proc resolvePlayerSlot*(
  sim: SimServer,
  address,
  token: string,
  requestedSlot: int
): int =
  ## Returns the slot a player should use or raises on rejection.
  if requestedSlot >= MaxPlayers:
    raise newException(
      CrewriftError,
      "Player slot must be between 0 and 15."
    )
  if token.len > 0 and sim.config.hasConfiguredTokens() and
      not sim.config.hasConfiguredToken(token):
    raise newException(CrewriftError, "Player token is not configured.")
  if requestedSlot >= 0:
    if requestedSlot >= sim.config.playerSlotLimit():
      raise newException(CrewriftError, "Player slot is outside configured roster.")
    if sim.slotOccupied(requestedSlot):
      raise newException(
        CrewriftError,
        "Player slot " & $requestedSlot & " is already occupied."
      )
    sim.config.validatePlayerSlot(requestedSlot, address, token)
    return requestedSlot
  result = sim.matchingConfiguredSlot(address, token)
  if result >= 0:
    return result
  let conflict = sim.conflictingConfiguredSlot(address, token)
  if conflict >= 0:
    raise newException(
      CrewriftError,
      "Player credentials do not match configured slot " & $conflict & "."
    )
  result = sim.nextAutoSlot(address, token)
  if result < 0:
    raise newException(CrewriftError, "No available player slot.")

proc nextPlayerSlot*(sim: SimServer): int =
  ## Returns the slot required for the next live player index.
  sim.players.len

proc resolveTrustedPlayerSlot(
  sim: SimServer,
  address: string,
  requestedSlot: int
): int =
  ## Returns a trusted replay slot without requiring the original token.
  if requestedSlot >= MaxPlayers:
    raise newException(
      CrewriftError,
      "Player slot must be between 0 and 15."
    )
  if requestedSlot >= 0:
    if requestedSlot >= sim.config.playerSlotLimit():
      raise newException(CrewriftError, "Player slot is outside configured roster.")
    if sim.slotOccupied(requestedSlot):
      raise newException(
        CrewriftError,
        "Player slot " & $requestedSlot & " is already occupied."
      )
    return requestedSlot
  result = sim.namedConfiguredSlot(address)
  if result >= 0:
    return result
  result = sim.nextAutoSlot(address, "")
  if result < 0:
    raise newException(CrewriftError, "No available player slot.")

proc rewardAccountIndex(sim: SimServer, address: string): int =
  ## Returns the reward account index for an address.
  for i in 0 ..< sim.rewardAccounts.len:
    if sim.rewardAccounts[i].address == address:
      return i
  -1

proc ensureRewardAccount(sim: var SimServer, address: string): int =
  ## Returns the reward account index, creating the account if needed.
  result = sim.rewardAccountIndex(address)
  if result < 0:
    sim.rewardAccounts.add RewardAccount(
      address: address,
      slotIndex: -1,
      reward: 0
    )
    result = sim.rewardAccounts.high

proc bindRewardAccountSlot(
  sim: var SimServer,
  accountIndex,
  slotIndex: int
) =
  ## Binds a reward account to the stable player slot for this match.
  if accountIndex < 0 or accountIndex >= sim.rewardAccounts.len:
    return
  for i in 0 ..< sim.rewardAccounts.len:
    if i != accountIndex and sim.rewardAccounts[i].slotIndex == slotIndex:
      sim.rewardAccounts[i].slotIndex = -1
  sim.rewardAccounts[accountIndex].slotIndex = slotIndex

proc rewardAccountIndexForSlot(sim: SimServer, slotIndex: int): int =
  ## Returns the newest reward account index for a player slot.
  if slotIndex < 0 or sim.rewardAccounts.len == 0:
    return -1
  for i in countdown(sim.rewardAccounts.high, 0):
    if sim.rewardAccounts[i].slotIndex == slotIndex:
      return i
  -1

proc playerIndexForSlot*(sim: SimServer, slotIndex: int): int =
  ## Returns the live player index for a player slot.
  for i in 0 ..< sim.players.len:
    if sim.players[i].joinOrder == slotIndex:
      return i
  -1

proc resultSlotName(sim: SimServer, slotIndex: int): string =
  ## Returns the stable result name for one player slot.
  let slot = sim.config.slotConfig(slotIndex)
  if slot.name.len > 0:
    return slot.name
  "player-" & $slotIndex

proc ensureRewardAccountForSlot(
  sim: var SimServer,
  slotIndex: int
): int =
  ## Returns the reward account index for one result slot.
  result = sim.rewardAccountIndexForSlot(slotIndex)
  if result >= 0:
    return
  result = sim.ensureRewardAccount(sim.resultSlotName(slotIndex))
  sim.bindRewardAccountSlot(result, slotIndex)

proc playerResultSlotCount(sim: SimServer): int =
  ## Returns the number of player slots represented in final results.
  result = sim.config.slots.len
  if sim.config.closedRoster:
    return
  for player in sim.players:
    result = max(result, player.joinOrder + 1)
  for account in sim.rewardAccounts:
    if account.slotIndex >= 0:
      result = max(result, account.slotIndex + 1)

proc playerAddressOccupied*(sim: SimServer, address: string): bool =
  ## Returns true when a player identity is already connected.
  for player in sim.players:
    if player.address == address:
      return true
  false

proc removePlayerAt*(sim: var SimServer, playerIndex: int) =
  ## Removes one live player and keeps index-keyed state aligned.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  sim.players.delete(playerIndex)
  if playerIndex < sim.shadowCaches.len:
    sim.shadowCaches.delete(playerIndex)
  for task in sim.tasks.mitems:
    if playerIndex < task.completed.len:
      task.completed.delete(playerIndex)
  if sim.phase in {Voting, VoteResult}:
    if playerIndex < sim.voteState.votes.len:
      sim.voteState.votes.delete(playerIndex)
    if playerIndex < sim.voteState.cursor.len:
      sim.voteState.cursor.delete(playerIndex)
    let skipIndex = sim.players.len
    for vote in sim.voteState.votes.mitems:
      if vote > playerIndex:
        dec vote
      if vote > skipIndex:
        vote = -2
    for cursor in sim.voteState.cursor.mitems:
      if cursor > playerIndex:
        dec cursor
      if cursor > skipIndex:
        cursor = skipIndex

proc addPlayer*(
  sim: var SimServer,
  address: string,
  requestedSlot = -1,
  token = "",
  trusted = false
): int =
  ## Adds one player, optionally validating and using a requested slot.
  if not sim.canAddPlayer():
    raise newException(CrewriftError, sim.config.playerLimitError())
  if sim.playerAddressOccupied(address):
    raise newException(
      CrewriftError,
      "Player name is already connected."
    )
  let
    order =
      if trusted:
        sim.resolveTrustedPlayerSlot(address, requestedSlot)
      else:
        sim.resolvePlayerSlot(address, token, requestedSlot)
    nextSlot = sim.nextPlayerSlot()
  if not trusted and order != nextSlot:
    raise newException(
      CrewriftError,
      "Player slot " & $order & " cannot join before slot " &
        $nextSlot & "."
    )
  let
    slot = sim.config.slotConfig(order)
    spawn = sim.homePosition(order, max(sim.players.len + 1, order + 1))
    color =
      if slot.hasColor:
        slot.color
      else:
        PlayerColors[order mod PlayerColors.len]
    accountIndex = sim.ensureRewardAccount(address)
  sim.bindRewardAccountSlot(accountIndex, order)
  sim.rewardAccounts[accountIndex].hasRole = false
  sim.rewardAccounts[accountIndex].won = false
  sim.rewardAccounts[accountIndex].abandoned = false
  sim.players.add Player(
    x: spawn.x,
    y: spawn.y,
    homeX: spawn.x,
    homeY: spawn.y,
    role: Crewmate,
    alive: true,
    connected: true,
    disconnectTick: -1,
    killCooldown: sim.config.killCooldownTicks,
    joinOrder: order,
    address: address,
    color: color,
    lastChatTick: sim.tickCount - sim.config.messageCooldownTicks,
    lastMoveTick: sim.tickCount,
    activeTask: -1,
    reward: sim.rewardAccounts[accountIndex].reward
  )
  sim.shadowCaches.add PlayerShadowMask(
    valid: false,
    mask: newSeq[bool](ScreenPixelCount)
  )
  sim.advanceJoinOrder()
  sim.arrangeHomePositions()
  for task in sim.tasks.mitems:
    task.completed.add(false)
  sim.players.high

proc hasTask*(player: Player, taskIdx: int): bool =
  for t in player.assignedTasks:
    if t == taskIdx:
      return true
  false

proc addReward*(sim: var SimServer, playerIndex, amount: int) =
  ## Adds accumulated reward to a player and its address account.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  let address = sim.players[playerIndex].address
  let index = sim.ensureRewardAccount(address)
  sim.bindRewardAccountSlot(index, sim.players[playerIndex].joinOrder)
  sim.rewardAccounts[index].reward += amount
  sim.players[playerIndex].reward = sim.rewardAccounts[index].reward

proc rewardAccountForPlayer(
  sim: var SimServer,
  playerIndex: int
): int =
  ## Returns the reward account index for a player, creating it if missing.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return -1
  let address = sim.players[playerIndex].address
  result = sim.ensureRewardAccount(address)
  sim.bindRewardAccountSlot(result, sim.players[playerIndex].joinOrder)

proc recordGameRoleAssigned*(
  sim: var SimServer,
  playerIndex: int
) =
  ## Increments the lifetime role-assignment counter for one player.
  let index = sim.rewardAccountForPlayer(playerIndex)
  if index < 0:
    return
  sim.rewardAccounts[index].role = sim.players[playerIndex].role
  sim.rewardAccounts[index].hasRole = true
  sim.rewardAccounts[index].won = false
  sim.rewardAccounts[index].abandoned = false
  if sim.players[playerIndex].role == Imposter:
    inc sim.rewardAccounts[index].gamesImposter
  else:
    inc sim.rewardAccounts[index].gamesCrewmate

proc recordGameAbandon*(sim: var SimServer, playerIndex: int) =
  ## Marks a player as abandoned for the current game.
  let index = sim.rewardAccountForPlayer(playerIndex)
  if index < 0:
    return
  sim.rewardAccounts[index].abandoned = true

proc recordGameWin*(sim: var SimServer, playerIndex: int) =
  ## Increments the lifetime per-role win counter for one player.
  let index = sim.rewardAccountForPlayer(playerIndex)
  if index < 0:
    return
  sim.rewardAccounts[index].won = true
  if sim.players[playerIndex].role == Imposter:
    inc sim.rewardAccounts[index].winsImposter
  else:
    inc sim.rewardAccounts[index].winsCrewmate

proc recordKill*(sim: var SimServer, playerIndex: int) =
  ## Increments the lifetime kill counter for one player.
  let index = sim.rewardAccountForPlayer(playerIndex)
  if index < 0:
    return
  inc sim.rewardAccounts[index].kills

proc recordTask*(sim: var SimServer, playerIndex: int) =
  ## Increments the lifetime task-completion counter for one player.
  let index = sim.rewardAccountForPlayer(playerIndex)
  if index < 0:
    return
  inc sim.rewardAccounts[index].tasks

proc recordVotePlayer*(sim: var SimServer, playerIndex: int) =
  ## Increments the lifetime player-vote counter for one player.
  let index = sim.rewardAccountForPlayer(playerIndex)
  if index < 0:
    return
  inc sim.rewardAccounts[index].votePlayers

proc recordVoteSkip*(sim: var SimServer, playerIndex: int) =
  ## Increments the lifetime explicit skip-vote counter for one player.
  let index = sim.rewardAccountForPlayer(playerIndex)
  if index < 0:
    return
  inc sim.rewardAccounts[index].voteSkip

proc recordVoteTimeout*(sim: var SimServer, playerIndex: int) =
  ## Increments the lifetime vote-timeout counter for one player.
  let index = sim.rewardAccountForPlayer(playerIndex)
  if index < 0:
    return
  inc sim.rewardAccounts[index].voteTimeout

proc recordConnectTimeout*(sim: var SimServer, slotIndex: int) =
  ## Marks one slot as missing the initial connection deadline.
  if slotIndex < 0 or slotIndex >= sim.config.playerSlotLimit():
    return
  let index = sim.ensureRewardAccountForSlot(slotIndex)
  if index < 0:
    return
  inc sim.rewardAccounts[index].connectTimeout
  sim.rewardAccounts[index].reward = ConnectionTimeoutPenalty
  sim.rewardAccounts[index].won = false

proc recordDisconnectTimeout*(sim: var SimServer, playerIndex: int) =
  ## Marks one player as missing the reconnect deadline.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  let index = sim.rewardAccountForPlayer(playerIndex)
  if index < 0:
    return
  inc sim.rewardAccounts[index].disconnectTimeout
  sim.rewardAccounts[index].reward = ConnectionTimeoutPenalty
  sim.rewardAccounts[index].won = false
  sim.players[playerIndex].reward = ConnectionTimeoutPenalty

proc canGraceDisconnect*(sim: SimServer, playerIndex: int): bool =
  ## Returns true when one socket close should start reconnect grace.
  playerIndex >= 0 and playerIndex < sim.players.len and
    sim.phase in {GameInfo, RoleReveal, Playing, Voting, VoteResult} and
    sim.config.disconnectTimeoutTicks > 0

proc markPlayerDisconnected*(sim: var SimServer, playerIndex: int) =
  ## Starts the reconnect grace timer for one live player.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  if not sim.players[playerIndex].connected:
    return
  sim.players[playerIndex].connected = false
  sim.players[playerIndex].disconnectTick = sim.tickCount
  sim.recordGameAbandon(playerIndex)
  sim.logGameEvent("disconnected: " & sim.playerText(playerIndex))

proc markPlayerConnected*(sim: var SimServer, playerIndex: int) =
  ## Clears the reconnect grace timer for one live player.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  sim.players[playerIndex].connected = true
  sim.players[playerIndex].disconnectTick = -1
  let index = sim.rewardAccountForPlayer(playerIndex)
  if index >= 0:
    sim.rewardAccounts[index].abandoned = false
  sim.logGameEvent("reconnected: " & sim.playerText(playerIndex))

proc reconnectPlayerIndex*(
  sim: SimServer,
  address,
  token: string,
  requestedSlot: int
): int =
  ## Returns the disconnected player index matching one reconnect request.
  for i, player in sim.players:
    if player.connected:
      continue
    if requestedSlot >= 0 and requestedSlot != player.joinOrder:
      continue
    if requestedSlot < 0 and player.address != address:
      continue
    if not sim.config.slotAuthMatches(player.joinOrder, address, token):
      continue
    return i
  -1

proc playerResultsJson*(sim: SimServer): string =
  ## Returns final player rewards and win states as JSON.
  var
    resultSlots: seq[int] = @[]
    names = newJArray()
    scores = newJArray()
    win = newJArray()
    tasksList = newJArray()
    killsList = newJArray()
    imposterList = newJArray()
    crewList = newJArray()
    votePlayersList = newJArray()
    voteSkipList = newJArray()
    voteTimeoutList = newJArray()
    connectTimeoutList = newJArray()
    disconnectTimeoutList = newJArray()
    results = newJObject()
  for slotIndex in 0 ..< sim.playerResultSlotCount():
    resultSlots.add(slotIndex)
  for slotIndex in resultSlots:
    let
      playerIndex = sim.playerIndexForSlot(slotIndex)
      accountIndex =
        if playerIndex >= 0:
          sim.rewardAccountIndex(sim.players[playerIndex].address)
        else:
          sim.rewardAccountIndexForSlot(slotIndex)
      slotConfig = sim.config.slotConfig(slotIndex)
    var
      name =
        if slotConfig.name.len > 0:
          slotConfig.name
        else:
          "player-" & $slotIndex
      reward = 0
      playerRole = Crewmate
      hasRole = false
      playerWon = false
      tasks = 0
      kills = 0
      votePlayers = 0
      voteSkip = 0
      voteTimeout = 0
      connectTimeout = 0
      disconnectTimeout = 0
    if accountIndex >= 0:
      let account = sim.rewardAccounts[accountIndex]
      name = account.address
      reward = account.reward
      playerRole = account.role
      hasRole = account.hasRole
      playerWon = account.won
      tasks = account.tasks
      kills = account.kills
      votePlayers = account.votePlayers
      voteSkip = account.voteSkip
      voteTimeout = account.voteTimeout
      connectTimeout = account.connectTimeout
      disconnectTimeout = account.disconnectTimeout
    if playerIndex >= 0:
      let player = sim.players[playerIndex]
      name = player.address
      if accountIndex < 0:
        reward = player.reward
      playerRole = player.role
      hasRole = true
      playerWon = not sim.timeLimitReached and player.role == sim.winner
    if not hasRole and slotConfig.hasRole:
      playerRole = slotConfig.role
      hasRole = true
    names.add(%name)
    scores.add(%reward)
    win.add(%playerWon)
    tasksList.add(%tasks)
    killsList.add(%kills)
    imposterList.add(%(if hasRole and playerRole == Imposter: 1 else: 0))
    crewList.add(%(if hasRole and playerRole == Crewmate: 1 else: 0))
    votePlayersList.add(%votePlayers)
    voteSkipList.add(%voteSkip)
    voteTimeoutList.add(%voteTimeout)
    connectTimeoutList.add(%connectTimeout)
    disconnectTimeoutList.add(%disconnectTimeout)
  results["names"] = names
  results["scores"] = scores
  results["win"] = win
  results["tasks"] = tasksList
  results["kills"] = killsList
  results["imposter"] = imposterList
  results["crew"] = crewList
  results["vote_players"] = votePlayersList
  results["vote_skip"] = voteSkipList
  results["vote_timeout"] = voteTimeoutList
  results["connect_timeout"] = connectTimeoutList
  results["disconnect_timeout"] = disconnectTimeoutList
  $results

proc completeTask*(sim: var SimServer, playerIndex, taskIndex: int) =
  ## Marks one player task complete and awards task reward.
  if taskIndex < 0 or taskIndex >= sim.tasks.len:
    return
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  if playerIndex >= sim.tasks[taskIndex].completed.len:
    return
  if playerIndex < sim.tasks[taskIndex].completed.len and
      sim.tasks[taskIndex].completed[playerIndex]:
    return
  sim.tasks[taskIndex].completed[playerIndex] = true
  sim.addReward(playerIndex, TaskReward)
  sim.recordTask(playerIndex)
  inc sim.players[playerIndex].tasksRewarded
  sim.players[playerIndex].lastMoveTick = sim.tickCount

proc completedTaskCount(sim: SimServer, playerIndex: int): int =
  ## Returns completed assigned tasks for one current player.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return 0
  for taskIndex in sim.players[playerIndex].assignedTasks:
    if taskIndex < 0 or taskIndex >= sim.tasks.len:
      continue
    if playerIndex >= sim.tasks[taskIndex].completed.len:
      continue
    if sim.tasks[taskIndex].completed[playerIndex]:
      inc result

proc settleCompletedTaskRewards(sim: var SimServer, playerIndex: int) =
  ## Awards any completed task flags that have not yet paid out.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  let completed = sim.completedTaskCount(playerIndex)
  while sim.players[playerIndex].tasksRewarded < completed:
    sim.addReward(playerIndex, TaskReward)
    sim.recordTask(playerIndex)
    inc sim.players[playerIndex].tasksRewarded

proc settleAllCompletedTaskRewards(sim: var SimServer) =
  ## Settles task rewards before the match result is awarded.
  for i in 0 ..< sim.players.len:
    sim.settleCompletedTaskRewards(i)

proc enterPlaying(sim: var SimServer) =
  ## Starts active gameplay timing and movement accounting.
  sim.phase = Playing
  sim.gameStartTick = sim.tickCount
  for player in sim.players.mitems:
    player.lastMoveTick = sim.tickCount

proc enterRoleRevealOrPlaying(sim: var SimServer) =
  ## Enters role reveal, or active play when reveal is disabled.
  sim.gameInfoTimer = 0
  sim.roleRevealTimer = sim.config.roleRevealTicks
  if sim.roleRevealTimer > 0:
    sim.phase = RoleReveal
    sim.gameStartTick = -1
  else:
    sim.enterPlaying()

proc startGame*(sim: var SimServer, showInfo = false) =
  ## Assigns roles and tasks, then enters the first game phase.
  sim.logGameEvent(
    "game started: players=" & $sim.players.len &
      ", imposters=" & $sim.config.effectiveImposterCount(sim.players.len)
  )
  sim.arrangeHomePositions()
  let imposterCount = sim.config.effectiveImposterCount(sim.players.len)
  for player in sim.players.mitems:
    player.role = Crewmate
    player.assignedTasks = @[]
    player.tasksRewarded = 0
  var
    candidates: seq[int] = @[]
    fixedImposters = 0
  for i in 0 ..< sim.players.len:
    let slot = sim.config.slotConfig(sim.players[i].joinOrder)
    if slot.hasRole:
      sim.players[i].role = slot.role
      if slot.role == Imposter:
        inc fixedImposters
    else:
      candidates.add(i)
  for j in countdown(candidates.high, 1):
    let k = sim.rng.rand(j)
    swap(candidates[j], candidates[k])
  let randomImposters = min(
    max(0, imposterCount - fixedImposters),
    candidates.len
  )
  for i in 0 ..< randomImposters:
    sim.players[candidates[i]].role = Imposter
  for i in 0 ..< sim.players.len:
    sim.recordGameRoleAssigned(i)
  var
    crew: seq[int] = @[]
    taskRects: seq[taskAssignments.TaskRect] = @[]
  for i in 0 ..< sim.players.len:
    if sim.players[i].role == Crewmate:
      crew.add(i)
  for task in sim.tasks:
    taskRects.add taskAssignments.TaskRect(
      x: task.x,
      y: task.y,
      w: task.w,
      h: task.h
    )
  let taskDetails = taskAssignments.assignTaskDetails(
    taskRects,
    sim.walkMask,
    MapWidth,
    MapHeight,
    sim.gameMap.home.x,
    sim.gameMap.home.y,
    crew.len,
    sim.config.tasksPerPlayer,
    sim.rng
  )
  for i, playerIndex in crew:
    sim.players[playerIndex].assignedTasks = taskDetails[i].taskIds
  sim.logTaskAssignments(crew, taskDetails)
  for player in sim.players.mitems:
    player.lastMoveTick = sim.tickCount
  if showInfo and sim.config.gameInfoTicks > 0:
    sim.phase = GameInfo
    sim.gameInfoTimer = sim.config.gameInfoTicks
    sim.roleRevealTimer = 0
    sim.gameStartTick = -1
  else:
    sim.enterRoleRevealOrPlaying()
  sim.timeLimitReached = false
  sim.lastLobbyPlayersLogged = -1
  sim.lastLobbyNeededLogged = -1
  sim.lastLobbySecondsLogged = -1

proc signOf(value: int): int {.inline.} =
  ## Returns the sign of one integer.
  if value < 0:
    return -1
  if value > 0:
    return 1
  0

proc slideScanRadius(sim: SimServer, carry, velocity: int): int =
  ## Returns the perpendicular scan radius for blocked movement.
  let
    pending = abs(carry) div sim.config.motionScale
    speed = (
      abs(velocity) + sim.config.motionScale - 1
    ) div sim.config.motionScale
  clamp(max(1, max(pending, speed)), 1, MovementSlideMaxScan)

proc canSlideHorizontal(
  sim: SimServer,
  x, y, step, offset: int
): bool =
  ## Returns true when a horizontal step can slide by one offset.
  if offset == 0:
    return false
  let slideStep = signOf(offset)
  for i in 1 .. abs(offset):
    if not sim.canOccupy(x, y + slideStep * i):
      return false
  sim.canOccupy(x + step, y + offset)

proc canSlideVertical(
  sim: SimServer,
  x, y, step, offset: int
): bool =
  ## Returns true when a vertical step can slide by one offset.
  if offset == 0:
    return false
  let slideStep = signOf(offset)
  for i in 1 .. abs(offset):
    if not sim.canOccupy(x + slideStep * i, y):
      return false
  sim.canOccupy(x + offset, y + step)

proc trySlideOffset(
  sim: SimServer,
  player: var Player,
  step, offset: int,
  horizontal: bool
): bool =
  ## Tries one candidate slide offset for a blocked movement step.
  if horizontal:
    if not sim.canSlideHorizontal(player.x, player.y, step, offset):
      return false
    player.x += step
    player.y += offset
  else:
    if not sim.canSlideVertical(player.x, player.y, step, offset):
      return false
    player.x += offset
    player.y += step
  true

proc trySlideMove(
  sim: SimServer,
  player: var Player,
  step, radius, preferredSlide: int,
  horizontal: bool
): bool =
  ## Tries nearby slide offsets for one blocked movement step.
  if radius <= 0:
    return false
  let preferred = signOf(preferredSlide)
  for distance in 1 .. radius:
    if preferred != 0:
      if sim.trySlideOffset(
        player,
        step,
        preferred * distance,
        horizontal
      ):
        return true
      if sim.trySlideOffset(
        player,
        step,
        -preferred * distance,
        horizontal
      ):
        return true
    else:
      if sim.trySlideOffset(player, step, -distance, horizontal):
        return true
      if sim.trySlideOffset(player, step, distance, horizontal):
        return true
  false

proc applyMomentumAxis(
  sim: SimServer,
  player: var Player,
  carry: var int,
  velocity, preferredSlide: int,
  horizontal: bool
) =
  ## Applies one fixed-point movement axis with collision sliding.
  carry += velocity
  while abs(carry) >= sim.config.motionScale:
    let step = if carry < 0: -1 else: 1
    let
      nx = if horizontal: player.x + step else: player.x
      ny = if horizontal: player.y else: player.y + step
    if sim.canOccupy(nx, ny):
      if horizontal:
        player.x = nx
      else:
        player.y = ny
      carry -= step * sim.config.motionScale
    else:
      let radius = sim.slideScanRadius(carry, velocity)
      if sim.trySlideMove(
        player,
        step,
        radius,
        preferredSlide,
        horizontal
      ):
        carry -= step * sim.config.motionScale
      else:
        carry = 0
        break

proc distSq*(ax, ay, bx, by: int): int =
  let
    dx = ax - bx
    dy = ay - by
  dx * dx + dy * dy

proc actorColor*(colorIndex, tint: uint8): uint8 =
  ## Returns the final color for actor wildcard pixels.
  if colorIndex == TintColor:
    return tint
  if colorIndex == ShadeTintColor:
    return ShadowMap[tint and 0x0f]
  colorIndex

proc tryKill*(sim: var SimServer, killerIndex: int) =
  ## Kills the nearest eligible crewmate in range.
  let killer = sim.players[killerIndex]
  if killer.role != Imposter or not killer.alive:
    return
  if killer.killCooldown > 0:
    return
  let
    kx = killer.x + CollisionW div 2
    ky = killer.y + CollisionH div 2
    rangeSq = sim.config.killRange * sim.config.killRange
  var
    bestDist = high(int)
    bestTarget = -1
  for i in 0 ..< sim.players.len:
    if i == killerIndex or not sim.players[i].alive:
      continue
    if sim.players[i].role == Imposter:
      continue
    let
      tx = sim.players[i].x + CollisionW div 2
      ty = sim.players[i].y + CollisionH div 2
      d = distSq(kx, ky, tx, ty)
    if d <= rangeSq and d < bestDist:
      bestDist = d
      bestTarget = i
  if bestTarget >= 0:
    sim.logGameEvent(
      playerColorText(sim.players[bestTarget].color) &
        " killed by " & playerColorText(killer.color) & " (imposter)"
    )
    sim.players[bestTarget].alive = false
    sim.bodies.add Body(
      x: sim.players[bestTarget].x,
      y: sim.players[bestTarget].y,
      color: sim.players[bestTarget].color,
      slotId: sim.players[bestTarget].joinOrder
    )
    sim.addReward(killerIndex, KillReward)
    sim.recordKill(killerIndex)
    sim.players[killerIndex].killCooldown = sim.config.killCooldownTicks

proc tryVent*(sim: var SimServer, playerIndex: int) =
  ## Teleport an imposter to the next vent in the same group.
  let p = sim.players[playerIndex]
  if p.role != Imposter or not p.alive:
    return
  if p.ventCooldown > 0:
    return
  let
    px = p.x + CollisionW div 2
    py = p.y + CollisionH div 2
    rangeSq = sim.config.ventRange * sim.config.ventRange
  for i in 0 ..< sim.vents.len:
    let v = sim.vents[i]
    let
      vx = v.x + v.w div 2
      vy = v.y + v.h div 2
    if distSq(px, py, vx, vy) <= rangeSq:
      var nextIdx = -1
      for j in 0 ..< sim.vents.len:
        if j == i:
          continue
        if sim.vents[j].group == v.group:
          if sim.vents[j].groupIndex == v.groupIndex + 1:
            nextIdx = j
            break
      if nextIdx < 0:
        for j in 0 ..< sim.vents.len:
          if sim.vents[j].group == v.group and
              sim.vents[j].groupIndex == 1:
            nextIdx = j
            break
      if nextIdx >= 0:
        let dest = sim.vents[nextIdx]
        sim.players[playerIndex].x =
          dest.x + dest.w div 2 - CollisionW div 2
        sim.players[playerIndex].y =
          dest.y + dest.h div 2 - CollisionH div 2
        sim.players[playerIndex].velX = 0
        sim.players[playerIndex].velY = 0
        sim.players[playerIndex].carryX = 0
        sim.players[playerIndex].carryY = 0
        sim.players[playerIndex].ventCooldown = 30
      return

proc startVote*(
  sim: var SimServer,
  kind = VoteCalledUnknown,
  callerIndex = -1,
  bodyColor = 255'u8,
  bodySlotId = -1
) =
  ## Starts a voting meeting and logs its cause.
  sim.voteState.callKind = kind
  sim.voteState.callerIndex = callerIndex
  sim.voteState.bodyColor = bodyColor
  sim.voteState.bodySlotId = bodySlotId
  case kind
  of VoteCalledBody:
    sim.logGameEvent(
      "vote called: " & sim.playerText(callerIndex) &
        " called body (" & playerColorText(bodyColor) & ")"
    )
  of VoteCalledButton:
    sim.logGameEvent(
      "vote called: " & sim.playerText(callerIndex) &
        " called emergency button"
    )
  of VoteCalledUnknown:
    sim.logGameEvent("vote called")
  sim.phase = Voting
  sim.chatMessages.setLen(0)
  let n = sim.players.len
  sim.voteState.votes = newSeq[int](n)
  sim.voteState.cursor = newSeq[int](n)
  sim.voteState.voteTimer = sim.config.voteTimerTicks
  for i in 0 ..< n:
    sim.voteState.votes[i] = -1
    sim.players[i].lastChatTick = sim.tickCount - sim.config.messageCooldownTicks
    var firstAlive = 0
    for j in 0 ..< n:
      if sim.players[j].alive:
        firstAlive = j
        break
    sim.voteState.cursor[i] = firstAlive

proc addVotingChat*(sim: var SimServer, playerIndex: int, message: string) =
  ## Adds one visible chat message while voting.
  if sim.phase != Voting:
    return
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  if not sim.players[playerIndex].alive:
    return
  let cooldown = sim.config.messageCooldownTicks
  if cooldown > 0:
    let elapsed = sim.tickCount - sim.players[playerIndex].lastChatTick
    if elapsed < cooldown:
      return
  let text = cleanChatMessage(message)
  if text.len == 0:
    return
  sim.players[playerIndex].lastChatTick = sim.tickCount
  while sim.chatMessages.len >= VoteChatVisibleMessages:
    sim.chatMessages.delete(0)
  sim.chatMessages.add ChatMessage(
    slotId: sim.players[playerIndex].joinOrder,
    color: sim.players[playerIndex].color,
    text: text
  )
  sim.logGameEvent(
    "vote chat: " & sim.playerText(playerIndex) & ": " & text
  )

proc tryReport*(sim: var SimServer, reporterIndex: int, bodyLimit: int) =
  ## Starts a vote when a living player reports a nearby body.
  if sim.phase != Playing:
    return
  let p = sim.players[reporterIndex]
  if not p.alive:
    return
  let
    px = p.x + CollisionW div 2
    py = p.y + CollisionH div 2
    rangeSq = sim.config.reportRange * sim.config.reportRange
  for bi in 0 ..< bodyLimit:
    let body = sim.bodies[bi]
    let
      bx = body.x + CollisionW div 2
      by = body.y + CollisionH div 2
    if distSq(px, py, bx, by) <= rangeSq:
      sim.startVote(VoteCalledBody, reporterIndex, body.color, body.slotId)
      return

proc tryCallButton*(sim: var SimServer, callerIndex: int) =
  ## Starts a vote when a living player presses the meeting button.
  if sim.phase != Playing:
    return
  let p = sim.players[callerIndex]
  if not p.alive:
    return
  if p.buttonCallsUsed >= sim.config.buttonCalls:
    return
  let
    px = p.x + CollisionW div 2
    py = p.y + CollisionH div 2
    button = sim.gameMap.button
  if px >= button.x and px < button.x + button.w and
      py >= button.y and py < button.y + button.h:
    inc sim.players[callerIndex].buttonCallsUsed
    sim.startVote(VoteCalledButton, callerIndex)

proc containGhost(player: var Player) =
  ## Keeps ghost movement inside the map rectangle.
  let
    maxX = MapWidth - CollisionW
    maxY = MapHeight - CollisionH
  if player.x < 0:
    player.x = 0
    player.velX = max(player.velX, 0)
    player.carryX = 0
  elif player.x > maxX:
    player.x = maxX
    player.velX = min(player.velX, 0)
    player.carryX = 0
  if player.y < 0:
    player.y = 0
    player.velY = max(player.velY, 0)
    player.carryY = 0
  elif player.y > maxY:
    player.y = maxY
    player.velY = min(player.velY, 0)
    player.carryY = 0

proc applyGhostMovement*(sim: var SimServer, playerIndex: int, input: InputState) =
  template player: untyped = sim.players[playerIndex]
  var inputX = 0
  var inputY = 0
  if input.left: inputX -= 1
  if input.right: inputX += 1
  if input.up: inputY -= 1
  if input.down: inputY += 1

  if inputX != 0:
    player.velX = clamp(
      player.velX + inputX * sim.config.accel,
      -sim.config.maxSpeed,
      sim.config.maxSpeed
    )
  else:
    player.velX =
      (player.velX * sim.config.frictionNum) div sim.config.frictionDen
    if abs(player.velX) < sim.config.stopThreshold:
      player.velX = 0

  if inputY != 0:
    player.velY = clamp(
      player.velY + inputY * sim.config.accel,
      -sim.config.maxSpeed,
      sim.config.maxSpeed
    )
  else:
    player.velY =
      (player.velY * sim.config.frictionNum) div sim.config.frictionDen
    if abs(player.velY) < sim.config.stopThreshold:
      player.velY = 0

  if inputX < 0: player.flipH = true
  elif inputX > 0: player.flipH = false

  player.carryX += player.velX
  while abs(player.carryX) >= sim.config.motionScale:
    let step = if player.carryX < 0: -1 else: 1
    player.x += step
    player.carryX -= step * sim.config.motionScale
  player.carryY += player.velY
  while abs(player.carryY) >= sim.config.motionScale:
    let step = if player.carryY < 0: -1 else: 1
    player.y += step
    player.carryY -= step * sim.config.motionScale
  player.containGhost()

  if player.role == Crewmate and input.attack:
    let
      px = player.x + CollisionW div 2
      py = player.y + CollisionH div 2
    var inTask = -1
    for t in 0 ..< sim.tasks.len:
      if not player.hasTask(t): continue
      let task = sim.tasks[t]
      if playerIndex < task.completed.len and task.completed[playerIndex]: continue
      if px >= task.x and px < task.x + task.w and
          py >= task.y and py < task.y + task.h:
        inTask = t
        break
    if inTask >= 0 and inputX == 0 and inputY == 0:
      if player.activeTask != inTask:
        player.activeTask = inTask
        player.taskProgress = 0
      inc player.taskProgress
      if player.taskProgress >= sim.config.taskCompleteTicks:
        sim.completeTask(playerIndex, inTask)
        player.activeTask = -1
        player.taskProgress = 0
    else:
      player.activeTask = -1
      player.taskProgress = 0
  else:
    player.activeTask = -1
    player.taskProgress = 0

proc applyInput*(
  sim: var SimServer,
  playerIndex: int,
  input: InputState,
  prevInput: InputState,
  bodiesBeforeTick: int
) {.measure.} =
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  if not sim.players[playerIndex].alive:
    sim.applyGhostMovement(playerIndex, input)
    return
  template player: untyped = sim.players[playerIndex]

  var
    inputX = 0
    inputY = 0
  if input.left:
    inputX -= 1
  if input.right:
    inputX += 1
  if input.up:
    inputY -= 1
  if input.down:
    inputY += 1

  if inputX != 0:
    player.velX = clamp(
      player.velX + inputX * sim.config.accel,
      -sim.config.maxSpeed,
      sim.config.maxSpeed
    )
  else:
    player.velX =
      (player.velX * sim.config.frictionNum) div sim.config.frictionDen
    if abs(player.velX) < sim.config.stopThreshold:
      player.velX = 0

  if inputY != 0:
    player.velY = clamp(
      player.velY + inputY * sim.config.accel,
      -sim.config.maxSpeed,
      sim.config.maxSpeed
    )
  else:
    player.velY =
      (player.velY * sim.config.frictionNum) div sim.config.frictionDen
    if abs(player.velY) < sim.config.stopThreshold:
      player.velY = 0

  if inputX < 0:
    player.flipH = true
  elif inputX > 0:
    player.flipH = false

  let
    preferredSlideY =
      if inputY != 0:
        inputY
      else:
        signOf(player.velY)
    preferredSlideX =
      if inputX != 0:
        inputX
      else:
        signOf(player.velX)
  sim.applyMomentumAxis(
    player,
    player.carryX,
    player.velX,
    preferredSlideY,
    true
  )
  sim.applyMomentumAxis(
    player,
    player.carryY,
    player.velY,
    preferredSlideX,
    false
  )

  let freshB = input.b and not prevInput.b
  if freshB:
    if player.role == Imposter:
      sim.tryVent(playerIndex)

  if input.attack:
    let freshA = input.attack and not prevInput.attack
    if freshA:
      sim.tryReport(playerIndex, bodiesBeforeTick)
      if sim.phase == Voting:
        return
      sim.tryCallButton(playerIndex)
      if sim.phase == Voting:
        return
    if player.role == Imposter:
      if freshA:
        sim.tryKill(playerIndex)
    elif player.role == Crewmate:
      let
        px = player.x + CollisionW div 2
        py = player.y + CollisionH div 2
      var inTask = -1
      for t in 0 ..< sim.tasks.len:
        if not player.hasTask(t):
          continue
        let task = sim.tasks[t]
        if playerIndex < task.completed.len and task.completed[playerIndex]:
          continue
        if px >= task.x and px < task.x + task.w and
            py >= task.y and py < task.y + task.h:
          inTask = t
          break
      if inTask >= 0 and inputX == 0 and inputY == 0:
        if player.activeTask != inTask:
          player.activeTask = inTask
          player.taskProgress = 0
        inc player.taskProgress
        if player.taskProgress >= sim.config.taskCompleteTicks:
          sim.completeTask(playerIndex, inTask)
          player.activeTask = -1
          player.taskProgress = 0
      else:
        player.activeTask = -1
        player.taskProgress = 0
  else:
    player.activeTask = -1
    player.taskProgress = 0

proc playerView*(sim: SimServer, playerIndex: int): PlayerView =
  ## Returns the canonical per-player camera and visibility origin.
  let
    player = sim.players[playerIndex]
    spriteX = player.x - SpriteDrawOffX
    spriteY = player.y - SpriteDrawOffY
    centerX = spriteX + SpriteSize div 2
    centerY = spriteY + SpriteSize div 2
  result.cameraX = centerX - ScreenWidth div 2
  result.cameraY = centerY - ScreenHeight div 2
  result.originMx = player.x + CollisionW div 2
  result.originMy = player.y + CollisionH div 2
  result.viewerIsGhost = not player.alive

proc screenPointInFrame*(view: PlayerView, worldX, worldY: int): bool =
  ## Returns true when a world point lands inside this player's camera frame.
  let
    sx = worldX - view.cameraX
    sy = worldY - view.cameraY
  sx >= 0 and sx < ScreenWidth and sy >= 0 and sy < ScreenHeight

proc screenRectInFrame*(x, y, w, h: int): bool =
  ## Returns true when a screen rectangle overlaps the player camera frame.
  x < ScreenWidth and y < ScreenHeight and x + w > 0 and y + h > 0

proc playerActorScreenRect*(
  player: Player,
  view: PlayerView
): tuple[x, y, w, h: int] =
  ## Returns the drawn crew actor rectangle in screen coordinates.
  (
    x: player.x - SpriteDrawOffX - 1 - view.cameraX,
    y: player.y - SpriteDrawOffY - 1 - view.cameraY,
    w: CrewSpriteSize + 2,
    h: CrewSpriteSize + 2
  )

proc playerActorInFrame*(player: Player, view: PlayerView): bool =
  ## Returns true when any part of a crew actor overlaps the frame.
  let rect = player.playerActorScreenRect(view)
  screenRectInFrame(rect.x, rect.y, rect.w, rect.h)

proc playerActorVisibilityPoint*(player: Player, view: PlayerView): MapPoint =
  ## Returns an in-frame point to use for actor visibility checks.
  MapPoint(
    x: clamp(
      player.x + CollisionW div 2,
      view.cameraX,
      view.cameraX + ScreenWidth - 1
    ),
    y: clamp(
      player.y + CollisionH div 2,
      view.cameraY,
      view.cameraY + ScreenHeight - 1
    )
  )

proc screenPointVisible*(sim: SimServer, view: PlayerView, worldX, worldY: int): bool =
  ## Returns true when a world point is visible in this player's rendered view.
  let
    sx = worldX - view.cameraX
    sy = worldY - view.cameraY
  if not screenPointInFrame(view, worldX, worldY):
    return false
  view.viewerIsGhost or not sim.shadowBuf[sy * ScreenWidth + sx]

proc isWall*(sim: SimServer, mx, my: int): bool =
  if mx < 0 or my < 0 or mx >= MapWidth or my >= MapHeight:
    return true
  sim.wallMask[mapIndex(mx, my)]

proc ensureShadowPaths(originSx, originSy: int) {.measure.} =
  ## Builds reusable screen-space shadow rays for one origin.
  if ShadowPaths.ready and
      ShadowPaths.originSx == originSx and
      ShadowPaths.originSy == originSy:
    return

  ShadowPaths = ShadowPathCache(
    ready: true,
    originSx: originSx,
    originSy: originSy,
    starts: newSeq[int](ScreenPixelCount + 1),
    offsets: newSeqOfCap[int32](ScreenPixelCount * 64),
    xs: newSeqOfCap[int16](ScreenPixelCount * 64),
    ys: newSeqOfCap[int16](ScreenPixelCount * 64)
  )

  for sy in 0 ..< ScreenHeight:
    for sx in 0 ..< ScreenWidth:
      let
        pixelIndex = sy * ScreenWidth + sx
        dx = sx - originSx
        dy = sy - originSy
        steps = max(abs(dx), abs(dy))
      ShadowPaths.starts[pixelIndex] = ShadowPaths.offsets.len
      if steps == 0:
        continue
      for step in 1 .. steps:
        let
          rx = originSx + dx * step div steps
          ry = originSy + dy * step div steps
        ShadowPaths.offsets.add(int32(ry * MapWidth + rx))
        ShadowPaths.xs.add(int16(rx))
        ShadowPaths.ys.add(int16(ry))
  ShadowPaths.starts[ScreenPixelCount] = ShadowPaths.offsets.len

proc clearShadowBuffer(sim: var SimServer) =
  ## Clears the active screen shadow buffer.
  if sim.shadowBuf.len != ScreenPixelCount:
    sim.shadowBuf = newSeq[bool](ScreenPixelCount)
    return
  if sim.shadowBuf.len > 0:
    zeroMem(addr sim.shadowBuf[0], sim.shadowBuf.len * sizeof(bool))

proc copyShadowMask(dst: var seq[bool], src: seq[bool]) =
  ## Copies a screen-sized shadow mask.
  if dst.len != ScreenPixelCount:
    dst = newSeq[bool](ScreenPixelCount)
  if src.len != ScreenPixelCount:
    zeroMem(addr dst[0], dst.len * sizeof(bool))
    return
  copyMem(addr dst[0], unsafeAddr src[0], dst.len * sizeof(bool))

proc ensureShadowCacheSlots(sim: var SimServer) =
  ## Keeps player-indexed shadow cache storage aligned with players.
  while sim.shadowCaches.len < sim.players.len:
    sim.shadowCaches.add PlayerShadowMask(
      valid: false,
      mask: newSeq[bool](ScreenPixelCount)
    )
  if sim.shadowCaches.len > sim.players.len:
    sim.shadowCaches.setLen(sim.players.len)
  for cache in sim.shadowCaches.mitems:
    if cache.mask.len != ScreenPixelCount:
      cache.valid = false
      cache.mask = newSeq[bool](ScreenPixelCount)

{.push checks: off.}
proc castShadows*(
  sim: var SimServer,
  originMx,
  originMy,
  cameraX,
  cameraY: int
) {.measure.} =
  let
    originSx = originMx - cameraX
    originSy = originMy - cameraY
  ensureShadowPaths(originSx, originSy)
  sim.clearShadowBuffer()

  let
    viewportInside =
      cameraX >= 0 and cameraY >= 0 and
      cameraX + ScreenWidth <= MapWidth and
      cameraY + ScreenHeight <= MapHeight
    baseIndex = cameraY * MapWidth + cameraX
    starts = cast[ptr UncheckedArray[int]](addr ShadowPaths.starts[0])
    offsets = cast[ptr UncheckedArray[int32]](addr ShadowPaths.offsets[0])
    wallMask = cast[ptr UncheckedArray[bool]](addr sim.wallMask[0])
    shadowBuf = cast[ptr UncheckedArray[bool]](addr sim.shadowBuf[0])

  if viewportInside:
    for pixelIndex in 0 ..< ScreenPixelCount:
      let finish = starts[pixelIndex + 1]
      var stepIndex = starts[pixelIndex]
      while stepIndex < finish:
        if wallMask[baseIndex + int(offsets[stepIndex])]:
          shadowBuf[pixelIndex] = true
          break
        inc stepIndex
    return

  let
    xs = cast[ptr UncheckedArray[int16]](addr ShadowPaths.xs[0])
    ys = cast[ptr UncheckedArray[int16]](addr ShadowPaths.ys[0])
  for pixelIndex in 0 ..< ScreenPixelCount:
    let finish = starts[pixelIndex + 1]
    var stepIndex = starts[pixelIndex]
    while stepIndex < finish:
      let
        mx = cameraX + int(xs[stepIndex])
        my = cameraY + int(ys[stepIndex])
      if mx < 0 or my < 0 or mx >= MapWidth or my >= MapHeight or
          wallMask[my * MapWidth + mx]:
        shadowBuf[pixelIndex] = true
        break
      inc stepIndex

proc usePlayerShadowMask*(
  sim: var SimServer,
  playerIndex: int,
  view: PlayerView
): bool {.measure.} =
  ## Loads the shadow mask and returns true when it was refreshed.
  if playerIndex < 0 or playerIndex >= sim.players.len or view.viewerIsGhost:
    sim.clearShadowBuffer()
    return false

  sim.ensureShadowCacheSlots()
  template cache: untyped = sim.shadowCaches[playerIndex]
  if cache.valid and
      cache.cameraX == view.cameraX and
      cache.cameraY == view.cameraY and
      cache.originMx == view.originMx and
      cache.originMy == view.originMy:
    sim.shadowBuf.copyShadowMask(cache.mask)
    return false

  sim.castShadows(view.originMx, view.originMy, view.cameraX, view.cameraY)
  cache.valid = true
  cache.cameraX = view.cameraX
  cache.cameraY = view.cameraY
  cache.originMx = view.originMx
  cache.originMy = view.originMy
  cache.mask.copyShadowMask(sim.shadowBuf)
  result = true
{.pop.}

proc allVotesCast*(sim: SimServer): bool =
  for i in 0 ..< sim.players.len:
    if sim.players[i].alive and sim.voteState.votes[i] == -1:
      return false
  true

proc tallyVotes*(sim: var SimServer, timedOut = false) =
  ## Counts the votes and moves to the vote-result phase.
  var counts = newSeq[int](sim.players.len)
  var skipCount = 0
  for i in 0 ..< sim.players.len:
    if sim.players[i].alive:
      let v = sim.voteState.votes[i]
      if v >= 0 and v < counts.len:
        inc counts[v]
        sim.recordVotePlayer(i)
      elif v == -2:
        inc skipCount
        sim.recordVoteSkip(i)
      elif v == -1:
        inc skipCount
        sim.recordVoteTimeout(i)
        if timedOut:
          sim.addReward(i, VoteTimeoutPenalty)
  var maxVotes = skipCount
  var maxPlayer = -1
  var tied = false
  for i in 0 ..< counts.len:
    if counts[i] > maxVotes:
      maxVotes = counts[i]
      maxPlayer = i
      tied = false
    elif counts[i] == maxVotes and counts[i] > 0:
      tied = true
  if tied or maxVotes == 0 or maxPlayer < 0:
    sim.voteState.ejectedPlayer = -1
    sim.logGameEvent("vote ended: no one killed by vote")
  else:
    sim.voteState.ejectedPlayer = maxPlayer
    sim.logGameEvent(
      "vote ended: " & sim.playerText(maxPlayer) & " killed by vote"
    )
  sim.phase = VoteResult
  sim.voteState.resultTimer = sim.config.voteResultTicks

proc applyVoteResult*(sim: var SimServer) =
  let ej = sim.voteState.ejectedPlayer
  if ej >= 0 and ej < sim.players.len:
    sim.players[ej].alive = false
  sim.bodies.setLen(0)
  sim.chatMessages.setLen(0)
  for i in 0 ..< sim.players.len:
    sim.resetPlayerToHome(i)
    if sim.players[i].alive and sim.players[i].role == Imposter:
      sim.players[i].killCooldown = sim.config.killCooldownTicks
  sim.phase = Playing

proc moveCursor*(sim: var SimServer, playerIndex: int, delta: int) =
  let n = sim.players.len
  if n == 0:
    return
  let total = n + 1
  var cur = sim.voteState.cursor[playerIndex]
  for step in 1 .. total:
    cur = (cur + delta + total) mod total
    if cur == n or sim.players[cur].alive:
      break
  sim.voteState.cursor[playerIndex] = cur

proc hasUnfinishedTasks*(sim: SimServer, playerIndex: int): bool =
  ## Returns true when one player still has assigned tasks to finish.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return false
  if sim.players[playerIndex].role != Crewmate:
    return false
  for t in sim.players[playerIndex].assignedTasks:
    if t >= 0 and t < sim.tasks.len and
        playerIndex < sim.tasks[t].completed.len and
        not sim.tasks[t].completed[playerIndex]:
      return true
  false

proc applyStuckPenalty(sim: var SimServer, playerIndex: int) =
  ## Penalizes players who stop moving while tasks remain.
  if sim.phase != Playing:
    return
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  if sim.players[playerIndex].activeTask >= 0:
    return
  if not sim.hasUnfinishedTasks(playerIndex):
    return
  if sim.tickCount - sim.players[playerIndex].lastMoveTick < StuckPenaltyTicks:
    return
  sim.addReward(playerIndex, StuckPenalty)
  sim.players[playerIndex].lastMoveTick = sim.tickCount
  sim.logGameEvent("stuck penalty: " & sim.playerText(playerIndex))

proc trackMovementAndStuckPenalty(
  sim: var SimServer,
  playerIndex,
  oldX,
  oldY: int
) =
  ## Updates movement timers and applies idle task penalties.
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  if sim.players[playerIndex].x != oldX or sim.players[playerIndex].y != oldY:
    sim.players[playerIndex].lastMoveTick = sim.tickCount
    return
  sim.applyStuckPenalty(playerIndex)

proc totalTasksRemaining*(sim: SimServer): int =
  for i in 0 ..< sim.players.len:
    if sim.players[i].role != Crewmate:
      continue
    for t in sim.players[i].assignedTasks:
      if t < sim.tasks.len and i < sim.tasks[t].completed.len and
          not sim.tasks[t].completed[i]:
        inc result

proc allTasksDone*(sim: SimServer): bool =
  sim.totalTasksRemaining() == 0

proc finishGame*(sim: var SimServer, winner: PlayerRole, timeLimitReached = false) =
  ## Moves to game over and awards all winning players.
  if sim.phase == GameOver:
    return
  sim.settleAllCompletedTaskRewards()
  if timeLimitReached:
    sim.logGameEvent("draw: time limit reached")
  else:
    sim.logGameEvent(roleText(winner) & " win")
  sim.phase = GameOver
  sim.winner = winner
  sim.gameOverTimer = sim.config.gameOverTicks
  sim.timeLimitReached = timeLimitReached
  if timeLimitReached:
    return
  var awardedAccounts = newSeq[bool](sim.rewardAccounts.len)
  for i in 0 ..< sim.players.len:
    if sim.players[i].role == winner:
      let accountIndex = sim.rewardAccountForPlayer(i)
      if awardedAccounts.len < sim.rewardAccounts.len:
        awardedAccounts.setLen(sim.rewardAccounts.len)
      if accountIndex >= 0 and accountIndex < awardedAccounts.len:
        awardedAccounts[accountIndex] = true
      sim.addReward(i, WinReward)
      sim.recordGameWin(i)
  for i in 0 ..< sim.rewardAccounts.len:
    if i < awardedAccounts.len and awardedAccounts[i]:
      continue
    if not sim.rewardAccounts[i].hasRole or sim.rewardAccounts[i].role != winner:
      continue
    sim.rewardAccounts[i].reward += WinReward
    sim.rewardAccounts[i].won = true
    if winner == Imposter:
      inc sim.rewardAccounts[i].winsImposter
    else:
      inc sim.rewardAccounts[i].winsCrewmate

proc gameTicksElapsed*(sim: SimServer): int =
  ## Returns ticks elapsed since the current game left the lobby.
  if sim.gameStartTick < 0:
    return 0
  max(0, sim.tickCount - sim.gameStartTick)

proc maxTicksReached(sim: SimServer): bool =
  sim.config.maxTicks > 0 and sim.phase in {Playing, Voting, VoteResult} and
    sim.gameTicksElapsed() >= sim.config.maxTicks

proc checkMaxTicks(sim: var SimServer) =
  if sim.maxTicksReached():
    sim.finishGame(Crewmate, timeLimitReached = true)

proc shouldAbortFiniteMatch*(sim: SimServer): bool =
  ## Returns true when a finite match cannot continue after roster loss.
  if sim.config.maxGames <= 0:
    return false
  if sim.phase == Lobby:
    if sim.config.closedRoster and sim.config.connectTimeoutTicks > 0:
      return false
    return sim.startWaitTimer > 0 and sim.players.len < sim.config.minPlayers
  sim.phase in {GameInfo, RoleReveal, Playing, Voting, VoteResult} and
    sim.players.len == 0

proc checkWinCondition*(sim: var SimServer) {.measure.} =
  var
    hasImposters = false
    aliveCrewmates = 0
    aliveImposters = 0
  for p in sim.players:
    if p.role == Imposter:
      hasImposters = true
    if p.alive:
      if p.role == Crewmate:
        inc aliveCrewmates
      else:
        inc aliveImposters
  if hasImposters and aliveImposters == 0 and sim.players.len > 0:
    sim.finishGame(Crewmate)
  elif hasImposters and aliveImposters >= aliveCrewmates and
      sim.players.len > 0:
    sim.finishGame(Imposter)
  elif sim.allTasksDone() and sim.players.len > 0:
    sim.finishGame(Crewmate)

proc spritePlayerObservationPointShadowed(
  sim: SimServer,
  originMx, originMy, worldX, worldY: int
): bool {.inline.} =
  let
    dx = worldX - originMx
    dy = worldY - originMy
    steps = max(abs(dx), abs(dy))
  if steps == 0:
    return false
  for s in 1 .. steps:
    let
      rx = originMx + dx * s div steps
      ry = originMy + dy * s div steps
    if sim.isWall(rx, ry):
      return true
  false

proc spritePlayerObservationWorldPointVisible(
  sim: SimServer,
  view: PlayerView,
  worldX, worldY: int
): bool {.inline.} =
  if not view.screenPointInFrame(worldX, worldY):
    return false
  view.viewerIsGhost or not sim.spritePlayerObservationPointShadowed(
    view.originMx,
    view.originMy,
    worldX,
    worldY
  )

proc spritePlayerObservationProgressByte(progress, totalTicks, barWidth: int): uint8 =
  if progress <= 0 or totalTicks <= 0 or barWidth <= 0:
    return 0'u8
  let filled = clamp(progress * barWidth div totalTicks, 0, barWidth)
  uint8(filled * 255 div barWidth)

proc spritePlayerObservationKillIconByte(sim: SimServer, playerIndex: int): uint8 =
  if sim.phase != Playing or playerIndex < 0 or playerIndex >= sim.players.len:
    return 0'u8
  let player = sim.players[playerIndex]
  if player.role != Imposter or not player.alive:
    return 0'u8
  if player.killCooldown > 0: 1'u8 else: 255'u8

proc writeSpritePlayerObservationHeader(
  sim: SimServer,
  playerIndex: int,
  output: var openArray[uint8]
) =
  output[0] = uint8(ord(sim.phase))
  if sim.phase == Playing:
    output[RenderHeaderKillIcon] = sim.spritePlayerObservationKillIconByte(playerIndex)
    if playerIndex >= 0 and playerIndex < sim.players.len:
      output[RenderHeaderTaskProgress] = spritePlayerObservationProgressByte(
        sim.players[playerIndex].taskProgress,
        sim.config.taskCompleteTicks,
        TaskBarWidth
      )
    output[RenderHeaderTasksRemaining] = uint8(clamp(sim.totalTasksRemaining(), 0, 255))

proc writeSpritePlayerObservationGrid(
  sim: SimServer,
  playerIndex: int,
  output: var openArray[uint8]
) =
  if sim.phase != Playing or playerIndex < 0 or playerIndex >= sim.players.len:
    return
  let
    view = sim.playerView(playerIndex)
    step = ScreenWidth div SpritePlayerObservationGridSize
  for gy in 0 ..< SpritePlayerObservationGridSize:
    for gx in 0 ..< SpritePlayerObservationGridSize:
      let
        sx = gx * step + step div 2
        sy = gy * step + step div 2
        mx = view.cameraX + sx
        my = view.cameraY + sy
        index = SpritePlayerObservationGridOffset + gy * SpritePlayerObservationGridSize + gx
      var color = MapVoidColor
      if mx >= 0 and my >= 0 and mx < MapWidth and my < MapHeight:
        let mapIdx = mapIndex(mx, my)
        # Static map colors are allowed, but collision masks are not.
        color = sim.mapPixels[mapIdx] and 0x0F
      output[index] = color

proc writeSpritePlayerObservationPlayerSlotAt(
  sim: SimServer,
  playerIndex, targetIndex, slotIndex, sx, sy: int,
  flags: uint8,
  output: var openArray[uint8]
) =
  let
    player = sim.players[targetIndex]
    base = SpritePlayerObservationPlayerOffset + slotIndex * SpritePlayerObservationPlayerFeatures
  output[base] = uint8(clamp(sx, 0, 255))
  output[base + 1] = uint8(clamp(sy, 0, 255))
  output[base + 2] = player.color
  output[base + RenderPlayerFlagsFeature] = flags

proc writeSpritePlayerObservationPlayerSlot(
  sim: SimServer,
  playerIndex, targetIndex, sx, sy: int,
  flags: uint8,
  output: var openArray[uint8]
) =
  sim.writeSpritePlayerObservationPlayerSlotAt(
    playerIndex,
    targetIndex,
    targetIndex,
    sx,
    sy,
    flags,
    output
  )

proc writeSpritePlayerObservationPlayingPlayers(
  sim: SimServer,
  playerIndex: int,
  output: var openArray[uint8]
) =
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  let
    view = sim.playerView(playerIndex)
    cameraX = view.cameraX
    cameraY = view.cameraY
  for i in 0 ..< sim.players.len:
    let
      p = sim.players[i]
      sx = p.x - SpriteDrawOffX - cameraX
      sy = p.y - SpriteDrawOffY - cameraY
    if not p.playerActorInFrame(view):
      continue
    var flags = RenderPlayerPresent
    if p.alive:
      let visiblePoint = p.playerActorVisibilityPoint(view)
      if i != playerIndex and
          not sim.spritePlayerObservationWorldPointVisible(
            view,
            visiblePoint.x,
            visiblePoint.y
          ):
        continue
      flags = flags or RenderPlayerAlive
    elif view.viewerIsGhost:
      flags = flags or RenderPlayerGhost
    else:
      continue
    if p.flipH:
      flags = flags or RenderPlayerFlipH
    sim.writeSpritePlayerObservationPlayerSlot(playerIndex, i, sx, sy, flags, output)

proc writeSpritePlayerObservationUiPlayers(
  sim: SimServer,
  playerIndex: int,
  output: var openArray[uint8]
) =
  let n = sim.players.len
  if n == 0:
    return
  case sim.phase
  of Lobby:
    let startY = sim.lobbyIconStartY()
    for i in 0 ..< n:
      let
        col = i mod 6
        row = i div 6
        sx = 5 + col * 9
        sy = startY + row * 9
      var flags = RenderPlayerPresent or RenderPlayerAlive
      sim.writeSpritePlayerObservationPlayerSlot(playerIndex, i, sx, sy, flags, output)
  of GameInfo:
    discard
  of RoleReveal:
    let viewerIsImp =
      playerIndex >= 0 and playerIndex < sim.players.len and
      sim.players[playerIndex].role == Imposter
    var shown: seq[int] = @[]
    if viewerIsImp:
      for i in 0 ..< n:
        if sim.players[i].role == Imposter:
          shown.add(i)
    else:
      for i in 0 ..< n:
        shown.add(i)
    if shown.len > 0:
      let
        cellW = VoteCellW
        cellH = VoteCellH
        cols = min(shown.len, VoteColsMax)
        totalW = cols * cellW
        startX = (ScreenWidth - totalW) div 2
        startY = 42
      for slot in 0 ..< shown.len:
        let
          i = shown[slot]
          col = slot mod cols
          row = slot div cols
          sx = startX + col * cellW
          sy = startY + row * cellH
        var flags = RenderPlayerPresent or RenderPlayerAlive
        sim.writeSpritePlayerObservationPlayerSlotAt(
          playerIndex,
          i,
          slot,
          sx,
          sy,
          flags,
          output
        )
  of Voting:
    let
      cellW = VoteCellW
      cellH = VoteCellH
      cols = min(n, VoteColsMax)
      totalW = cols * cellW
      startX = (ScreenWidth - totalW) div 2
      startY = VoteStartY
    for i in 0 ..< n:
      let
        col = i mod cols
        row = i div cols
        cx = startX + col * cellW
        cy = startY + row * cellH
        sx = cx
        sy = cy
      var flags = RenderPlayerPresent
      if sim.players[i].alive:
        flags = flags or RenderPlayerAlive
      sim.writeSpritePlayerObservationPlayerSlot(playerIndex, i, sx, sy, flags, output)
  of VoteResult:
    let ej = sim.voteState.ejectedPlayer
    if ej >= 0 and ej < n:
      var flags = RenderPlayerPresent
      if sim.players[ej].alive:
        flags = flags or RenderPlayerAlive
      sim.writeSpritePlayerObservationPlayerSlotAt(
        playerIndex,
        ej,
        0,
        ScreenWidth div 2 - VoteActorSize div 2,
        ScreenHeight div 2 - VoteActorSize div 2,
        flags,
        output
      )
  of GameOver:
    let
      rowH = 14
      rowsPerCol = 8
      colW = ScreenWidth div 2
      iconOffsetX = 4
      startY = 16
    for i in 0 ..< n:
      let
        col = i div rowsPerCol
        row = i mod rowsPerCol
        baseX = min(col, 1) * colW
        y = startY + row * rowH
        iconX = baseX + iconOffsetX
        iconY = y + (rowH - VoteActorSize) div 2
      var flags = RenderPlayerPresent
      if sim.players[i].alive:
        flags = flags or RenderPlayerAlive
      sim.writeSpritePlayerObservationPlayerSlot(playerIndex, i, iconX, iconY, flags, output)
  of Playing:
    discard

proc writeSpritePlayerObservationBodies(
  sim: SimServer,
  playerIndex: int,
  output: var openArray[uint8]
) =
  if sim.phase != Playing or playerIndex < 0 or playerIndex >= sim.players.len:
    return
  let
    view = sim.playerView(playerIndex)
    cameraX = view.cameraX
    cameraY = view.cameraY
  var slot = 0
  for body in sim.bodies:
    if slot >= SpritePlayerObservationBodySlots:
      break
    if not sim.spritePlayerObservationWorldPointVisible(
      view,
      body.x + CollisionW div 2,
      body.y + CollisionH div 2
    ):
      continue
    let
      base = SpritePlayerObservationBodyOffset + slot * SpritePlayerObservationBodyFeatures
      sx = body.x - SpriteDrawOffX - cameraX
      sy = body.y - SpriteDrawOffY - cameraY
    output[base] = uint8(clamp(sx, 0, 255))
    output[base + 1] = uint8(clamp(sy, 0, 255))
    output[base + 2] = body.color
    output[base + 3] = 1
    inc slot

proc writeSpritePlayerObservationTaskEntry(
  sim: SimServer,
  playerIndex, taskIndex: int,
  iconPass: bool,
  slotIndex: var int,
  output: var openArray[uint8]
) =
  if slotIndex >= SpritePlayerObservationTaskSlots:
    return
  if taskIndex < 0 or taskIndex >= sim.tasks.len:
    return
  if playerIndex < 0 or playerIndex >= sim.players.len:
    return
  let
    player = sim.players[playerIndex]
    task = sim.tasks[taskIndex]
  if player.role != Crewmate or not player.hasTask(taskIndex):
    return
  if playerIndex < task.completed.len and task.completed[playerIndex]:
    return

  let
    view = sim.playerView(playerIndex)
    cameraX = view.cameraX
    cameraY = view.cameraY
    bob = [0, 0, -1, -1, -1, 0, 0, 1, 1, 1]
    bobY =
      if player.activeTask == taskIndex:
        0
      else:
        bob[(sim.tickCount div 3) mod bob.len]
    iconSx = task.x + task.w div 2 - SpriteSize div 2 - cameraX
    iconSy = task.y - SpriteSize - 2 + bobY - cameraY
    iconCenterX = task.x + task.w div 2
    iconCenterY = task.y - SpriteSize div 2 - 2 + bobY
    iconOnScreen =
      iconSx + SpriteSize > 0 and iconSy + SpriteSize > 0 and
      iconSx < ScreenWidth and iconSy < ScreenHeight
    base = SpritePlayerObservationTaskOffset + slotIndex * SpritePlayerObservationTaskFeatures
  var flags = 0'u8
  if iconOnScreen:
    if not iconPass:
      return
    flags = flags or RenderTaskIconVisible
    output[base] = uint8(clamp(iconSx, 0, 255))
    output[base + 1] = uint8(clamp(iconSy, 0, 255))
  elif sim.config.showTaskArrows:
    if iconPass:
      return
    let
      px = float(player.x + CollisionW div 2 - view.cameraX)
      py = float(player.y + CollisionH div 2 - view.cameraY)
      dx = float(iconCenterX - view.cameraX) - px
      dy = float(iconCenterY - view.cameraY) - py
    if abs(dx) < 0.5 and abs(dy) < 0.5:
      return
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
    flags = flags or RenderTaskArrowVisible
    output[base + 2] = uint8(int(ex))
    output[base + 3] = uint8(int(ey))
  else:
    return

  output[base + RenderTaskFlagsFeature] = flags
  inc slotIndex

proc writeSpritePlayerObservationTasks(
  sim: SimServer,
  playerIndex: int,
  output: var openArray[uint8]
) =
  if sim.phase != Playing or playerIndex < 0 or playerIndex >= sim.players.len:
    return
  let player = sim.players[playerIndex]
  if player.role != Crewmate:
    return
  var slotIndex = 0
  for taskIndex in 0 ..< sim.tasks.len:
    sim.writeSpritePlayerObservationTaskEntry(playerIndex, taskIndex, true, slotIndex, output)
  for taskIndex in 0 ..< sim.tasks.len:
    sim.writeSpritePlayerObservationTaskEntry(playerIndex, taskIndex, false, slotIndex, output)

proc writeSpritePlayerObservation*(
  sim: var SimServer,
  playerIndex: int,
  output: var openArray[uint8]
) {.measure.} =
  ## Writes a compact sprite-player observation with only visible sprite-route fields.
  if output.len != SpritePlayerObservationFeatures:
    raise newException(
      CrewriftError,
      "SpritePlayer observation must be " & $SpritePlayerObservationFeatures & " bytes."
    )
  for i in 0 ..< output.len:
    output[i] = 0
  sim.writeSpritePlayerObservationHeader(playerIndex, output)
  sim.writeSpritePlayerObservationGrid(playerIndex, output)
  if sim.phase == Playing:
    sim.writeSpritePlayerObservationPlayingPlayers(playerIndex, output)
    sim.writeSpritePlayerObservationBodies(playerIndex, output)
    sim.writeSpritePlayerObservationTasks(playerIndex, output)
  else:
    sim.writeSpritePlayerObservationUiPlayers(playerIndex, output)

proc initSimServer*(config: GameConfig): SimServer =
  var resolvedConfig = config
  resolvedConfig.resolveRandomSeed()
  result.config = resolvedConfig
  result.rng = initRand(resolvedConfig.seed)
  loadPalette(clientDataDir() / "pallete.png")
  result.asciiSprites = readTiny5Font()

  let sheet = loadSpriteSheet()
  result.crewSprites = loadCrewSprites()
  result.bodySprites = loadCrewBodySprites()
  result.boneSprite = spriteFromImage(
    sheet.subImage(SpriteSize * 2, 0, SpriteSize, SpriteSize)
  )
  result.killButtonSprite = spriteFromImage(
    sheet.subImage(SpriteSize * 3, 0, SpriteSize, SpriteSize)
  )
  result.taskIconSprite = spriteFromImage(
    sheet.subImage(SpriteSize * 4, 0, SpriteSize, SpriteSize)
  )
  result.ghostSprite = spriteFromImage(
    sheet.subImage(SpriteSize * 6, 0, SpriteSize, SpriteSize)
  )
  result.ghostIconSprite = spriteFromImage(
    sheet.subImage(SpriteSize * 7, 0, SpriteSize, SpriteSize)
  )

  result.gameMap = loadCrewriftMap(config.mapPath)
  result.tasks = result.gameMap.tasks
  result.vents = result.gameMap.vents
  result.rooms = result.gameMap.rooms

  let (mapImage, walkImage, wallImage) = loadMapLayers(result.gameMap)
  result.mapPixels = newSeq[uint8](MapWidth * MapHeight)
  result.mapRgba = newSeq[uint8](MapWidth * MapHeight * 4)
  result.darkBgPixels = loadDarkBgPixels()
  for y in 0 ..< MapHeight:
    for x in 0 ..< MapWidth:
      let
        pixel = mapImage[x, y]
        index = mapIndex(x, y)
        offset = index * 4
      result.mapPixels[index] = nearestPaletteIndex(pixel)
      result.mapRgba[offset] = pixel.r
      result.mapRgba[offset + 1] = pixel.g
      result.mapRgba[offset + 2] = pixel.b
      result.mapRgba[offset + 3] = pixel.a

  result.walkMask = newSeq[bool](MapWidth * MapHeight)
  for y in 0 ..< MapHeight:
    for x in 0 ..< MapWidth:
      let pixel = walkImage[x, y]
      result.walkMask[mapIndex(x, y)] = pixel.a > 0

  result.wallMask = newSeq[bool](MapWidth * MapHeight)
  for y in 0 ..< MapHeight:
    for x in 0 ..< MapWidth:
      let pixel = wallImage[x, y]
      result.wallMask[mapIndex(x, y)] = pixel.a > 0

  result.shadowBuf = newSeq[bool](ScreenWidth * ScreenHeight)
  result.shadowCaches = @[]
  ensureShadowPaths(ShadowOriginSx, ShadowOriginSy)
  result.bodies = @[]
  result.chatMessages = @[]
  result.players = @[]
  result.nextJoinOrder = 0
  result.gameStartTick = -1
  result.startWaitTimer = 0
  result.gameInfoTimer = 0
  result.gameEventLoggingEnabled = true
  result.voteState.callKind = VoteCalledUnknown
  result.voteState.callerIndex = -1
  result.voteState.bodyColor = 255'u8
  result.voteState.bodySlotId = -1
  result.lastLobbyPlayersLogged = -1
  result.lastLobbyNeededLogged = -1
  result.lastLobbySecondsLogged = -1

proc resetToLobby*(sim: var SimServer) =
  sim.phase = Lobby
  sim.bodies = @[]
  sim.chatMessages = @[]
  sim.players = @[]
  sim.shadowCaches = @[]
  sim.nextJoinOrder = 0
  sim.tickCount = 0
  sim.gameStartTick = -1
  sim.startWaitTimer = 0
  sim.gameInfoTimer = 0
  sim.roleRevealTimer = 0
  sim.timeLimitReached = false
  sim.needsReregister = true
  sim.voteState.callKind = VoteCalledUnknown
  sim.voteState.callerIndex = -1
  sim.voteState.bodyColor = 255'u8
  sim.voteState.bodySlotId = -1
  sim.lastLobbyPlayersLogged = -1
  sim.lastLobbyNeededLogged = -1
  sim.lastLobbySecondsLogged = -1
  for task in sim.tasks.mitems:
    task.completed = @[]
  for account in sim.rewardAccounts.mitems:
    account.hasRole = false
    account.won = false
    account.abandoned = false

proc connectTimeoutSlots(sim: SimServer): seq[int] =
  ## Returns closed-roster slots that missed the initial connect deadline.
  if not sim.config.closedRoster:
    return
  for slotIndex in 0 ..< sim.config.slots.len:
    let playerIndex = sim.playerIndexForSlot(slotIndex)
    if playerIndex < 0 or not sim.players[playerIndex].connected:
      result.add(slotIndex)

proc checkConnectTimeout(sim: var SimServer): bool =
  ## Ends the match as a draw if required slots do not connect in time.
  if sim.phase != Lobby or sim.config.connectTimeoutTicks <= 0:
    return false
  if sim.tickCount < sim.config.connectTimeoutTicks:
    return false
  let slots = sim.connectTimeoutSlots()
  if slots.len == 0:
    return false
  for slotIndex in slots:
    sim.recordConnectTimeout(slotIndex)
  sim.logGameEvent("connect timeout: slots " & slots.taskIdsText())
  sim.finishGame(Crewmate, timeLimitReached = true)
  true

proc checkDisconnectTimeout(sim: var SimServer): bool =
  ## Ends the match as a draw if disconnected players miss reconnect grace.
  if sim.phase notin {GameInfo, RoleReveal, Playing, Voting, VoteResult} or
      sim.config.disconnectTimeoutTicks <= 0:
    return false
  var playerIndices: seq[int]
  for i, player in sim.players:
    if player.connected or player.disconnectTick < 0:
      continue
    if sim.tickCount - player.disconnectTick >=
        sim.config.disconnectTimeoutTicks:
      playerIndices.add(i)
  if playerIndices.len == 0:
    return false
  for playerIndex in playerIndices:
    sim.recordDisconnectTimeout(playerIndex)
  sim.logGameEvent("disconnect timeout: players " & playerIndices.taskIdsText())
  sim.finishGame(Crewmate, timeLimitReached = true)
  true

proc stepLobby(sim: var SimServer) {.measure.} =
  ## Advances the lobby start countdown.
  let required = sim.requiredLobbyPlayers()
  if sim.players.len < required:
    sim.startWaitTimer = 0
    sim.logLobbyWaiting()
    return
  if sim.config.startWaitTicks <= 0:
    sim.startGame()
    return
  if sim.startWaitTimer <= 0:
    sim.startWaitTimer = sim.config.startWaitTicks
  dec sim.startWaitTimer
  if sim.startWaitTimer <= 0:
    sim.startGame(showInfo = true)
  else:
    sim.logLobbyCountdown()

proc step*(
  sim: var SimServer,
  inputs: openArray[InputState],
  prevInputs: openArray[InputState]
) {.measure.} =
  inc sim.tickCount

  if sim.phase == Lobby:
    if sim.checkConnectTimeout():
      return
    sim.stepLobby()
    return

  if sim.checkDisconnectTimeout():
    return

  if sim.phase == GameInfo:
    dec sim.gameInfoTimer
    if sim.gameInfoTimer <= 0:
      sim.enterRoleRevealOrPlaying()
    return

  if sim.phase == RoleReveal:
    dec sim.roleRevealTimer
    if sim.roleRevealTimer <= 0:
      sim.enterPlaying()
    return

  if sim.phase == GameOver:
    dec sim.gameOverTimer
    if sim.gameOverTimer <= 0:
      sim.resetToLobby()
    return

  if sim.phase == VoteResult:
    dec sim.voteState.resultTimer
    if sim.voteState.resultTimer <= 0:
      sim.applyVoteResult()
      sim.checkWinCondition()
    sim.checkMaxTicks()
    return

  if sim.phase == Voting:
    dec sim.voteState.voteTimer
    if sim.voteState.voteTimer <= 0:
      sim.tallyVotes(timedOut = true)
      return
    for i in 0 ..< sim.players.len:
      if not sim.players[i].alive:
        continue
      let input =
        if i < inputs.len: inputs[i]
        else: InputState()
      let prev =
        if i < prevInputs.len: prevInputs[i]
        else: InputState()
      if sim.voteState.votes[i] != -1:
        continue
      let
        backward =
          (input.up and not prev.up) or
          (input.left and not prev.left)
        forward =
          (input.down and not prev.down) or
          (input.right and not prev.right)
      if backward != forward:
        sim.moveCursor(
          i,
          if backward:
            -1
          else:
            1
        )
      if input.attack and not prev.attack:
        let cur = sim.voteState.cursor[i]
        if cur == sim.players.len:
          sim.voteState.votes[i] = -2
        else:
          sim.voteState.votes[i] = cur
        sim.logGameEvent(
          "vote cast: " & sim.playerText(i) & " voted " &
            sim.voteTargetText(sim.voteState.votes[i])
        )
        if sim.allVotesCast():
          sim.tallyVotes()
    sim.checkMaxTicks()
    return

  let bodiesBeforeTick = sim.bodies.len
  for playerIndex in 0 ..< sim.players.len:
    if sim.players[playerIndex].alive and
        sim.players[playerIndex].role == Imposter:
      if sim.players[playerIndex].killCooldown > 0:
        dec sim.players[playerIndex].killCooldown
      if sim.players[playerIndex].ventCooldown > 0:
        dec sim.players[playerIndex].ventCooldown
    let input =
      if playerIndex < inputs.len: inputs[playerIndex]
      else: InputState()
    let prev =
      if playerIndex < prevInputs.len: prevInputs[playerIndex]
      else: InputState()
    let
      oldX = sim.players[playerIndex].x
      oldY = sim.players[playerIndex].y
    sim.applyInput(playerIndex, input, prev, bodiesBeforeTick)
    sim.trackMovementAndStuckPenalty(playerIndex, oldX, oldY)

  sim.checkWinCondition()
  sim.checkMaxTicks()
