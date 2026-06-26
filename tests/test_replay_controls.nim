import
  std/[os, unittest],
  bitworld/spriteprotocol,
  crewrift/[global, sim]

const
  GameDir = currentSourcePath.parentDir.parentDir
  ReplayTickObjectId = 4002
  ReplayControlsObjectId = 4003
  ReplayScrubberObjectId = 4004
  ReplayCenterBottomLayerId = 8
  ReplayBottomLeftLayerId = 9
  ReplayMismatchLayerId = 10
  ReplayDebugToggleX = 102
  ReplayDebugToggleY = 2

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc clickReplayLayer(
  state: var GlobalViewerState,
  layer, x, y: int
) =
  ## Queues one replay click on a browser-visible replay UI layer.
  state.mouseLayer = layer
  state.mouseX = x
  state.mouseY = y
  state.mouseDown = false
  state.mousePressed = true
  state.mousePressLayer = layer
  state.mousePressX = x
  state.mousePressY = y

proc mouseButtonMessage(layer, x, y: int, down: bool): string =
  ## Builds one browser-style mouse move plus button packet.
  var packet: seq[uint8] = @[]
  packet.addU8(SpriteClientMouseMove)
  packet.addI16(x)
  packet.addI16(y)
  packet.addU8(uint8(layer))
  packet.addU8(SpriteClientMouseButton)
  packet.addU8(0x01)
  packet.addU8(if down: 1'u8 else: 0'u8)
  blobFromBytes(packet)

proc packetHasObject(packet: openArray[uint8], objectId: int): bool =
  ## Returns true when a sprite packet contains one object id.
  for message in packet.parseSpritePacket():
    if message.kind == spkObject and message.objectDef.id == objectId:
      return true

proc packetHasLayer(packet: openArray[uint8], layerId: int): bool =
  ## Returns true when a sprite packet contains one layer definition.
  for message in packet.parseSpritePacket():
    if message.kind == spkLayer and message.layer.layer == layerId:
      return true

proc packetHasSpriteLabel(
  packet: openArray[uint8],
  label: string
): bool =
  ## Returns true when a sprite definition carries one label.
  for message in packet.parseSpritePacket():
    if message.kind == spkSprite and message.sprite.label == label:
      return true

proc packetHasNamespacedDebugObject(
  packet: openArray[uint8],
  layerId: int
): bool =
  ## Returns true when the debug object was remapped into Crewrift's namespace.
  for message in packet.parseSpritePacket():
    if message.kind != spkObject:
      continue
    let objectDef = message.objectDef
    if objectDef.id == 55002 and objectDef.spriteId == 55001 and
        objectDef.layer == layerId and objectDef.x == 3 and
        objectDef.y == 4 and objectDef.z == 31000:
      return true

suite "replay controls":
  test "transport and scrubber use split browser hit layers":
    var game = initCrewriftForTest(defaultGameConfig())
    var state = initGlobalViewerState()
    var next: GlobalViewerState

    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    state = next

    state.clickReplayLayer(ReplayBottomLeftLayerId, 11, 2)
    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    check next.replayCommands == @[' ']
    check next.replaySeekTick == -1

    state = next
    state.replayCommands.setLen(0)
    state.replaySeekTick = -1
    state.clickReplayLayer(ReplayCenterBottomLayerId, 64, 10)
    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = false,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    check next.replayCommands.len == 0
    check next.replaySeekTick == 506

    state = next
    state.replayCommands.setLen(0)
    state.replaySeekTick = -1
    state.clickReplayLayer(ReplayCenterBottomLayerId, 11, 2)
    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 506,
      replayPlaying = false,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    check next.replayCommands.len == 0
    check next.replaySeekTick == -1

  test "fast replay button click uses press edge":
    var game = initCrewriftForTest(defaultGameConfig())
    var state = initGlobalViewerState()
    var next: GlobalViewerState

    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    state = next

    state.applyGlobalViewerMessage(
      mouseButtonMessage(ReplayBottomLeftLayerId, 11, 2, true)
    )
    state.applyGlobalViewerMessage(
      mouseButtonMessage(ReplayBottomLeftLayerId, 100, 2, false)
    )
    check state.mouseDown == false
    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    check next.replayCommands == @[' ']
    check next.mousePressed == false
    check next.mouseReleased == false

  test "debug sprite overlay defaults to visible":
    check initGlobalViewerState().debugSpritesVisible

  test "debug sprite toggle changes viewer state":
    var game = initCrewriftForTest(defaultGameConfig())
    var state = initGlobalViewerState()
    var next: GlobalViewerState

    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    state = next
    check state.debugSpritesVisible

    state.clickReplayLayer(
      ReplayBottomLeftLayerId,
      ReplayDebugToggleX,
      ReplayDebugToggleY
    )
    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    check not next.debugSpritesVisible
    check next.replayCommands.len == 0

    state = next
    state.clickReplayLayer(
      ReplayBottomLeftLayerId,
      ReplayDebugToggleX,
      ReplayDebugToggleY
    )
    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    check next.debugSpritesVisible
    check next.replayCommands.len == 0

  test "debug sprites draw on player and replay pov views":
    var game = initCrewriftForTest(defaultGameConfig())
    let playerIndex = game.addPlayer("debugger")
    game.phase = Playing
    var debugPacket: seq[uint8]
    debugPacket.addSprite(
      1,
      1,
      1,
      @[255'u8, 255, 255, 255],
      "debug planned path"
    )
    debugPacket.addObject(2, 3, 4, 0, MapLayerId, 1)

    var
      state = initPlayerViewerState()
      nextState: PlayerViewerState
    let playerPacket = game.buildSpriteProtocolPlayerUpdates(
      playerIndex,
      state,
      nextState,
      debugSprites = debugPacket
    )
    check playerPacket.packetHasSpriteLabel("debug planned path")
    check playerPacket.packetHasNamespacedDebugObject(MapLayerId)

    var
      globalState = initGlobalViewerState()
      nextGlobalState: GlobalViewerState
    globalState.selectedJoinOrder = game.players[playerIndex].joinOrder
    globalState.debugSpritesVisible = false

    let hiddenPacket = game.buildSpriteProtocolUpdates(
      globalState,
      nextGlobalState,
      replayTick = 1,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 10,
      replayLooping = false,
      replayEnabled = true,
      debugSprites = @[debugPacket]
    )
    check not hiddenPacket.packetHasSpriteLabel("debug planned path")

    globalState = nextGlobalState
    globalState.debugSpritesVisible = true
    let visiblePacket = game.buildSpriteProtocolUpdates(
      globalState,
      nextGlobalState,
      replayTick = 1,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 10,
      replayLooping = false,
      replayEnabled = true,
      debugSprites = @[debugPacket]
    )
    check visiblePacket.packetHasSpriteLabel("debug planned path")
    check visiblePacket.packetHasNamespacedDebugObject(PovLayerId)

  test "debug sprites render by default once a pov is selected":
    var game = initCrewriftForTest(defaultGameConfig())
    let playerIndex = game.addPlayer("debugger")
    game.phase = Playing
    var debugPacket: seq[uint8]
    debugPacket.addSprite(
      1,
      1,
      1,
      @[255'u8, 255, 255, 255],
      "debug planned path"
    )
    debugPacket.addObject(2, 3, 4, 0, MapLayerId, 1)

    var
      globalState = initGlobalViewerState()
      nextGlobalState: GlobalViewerState
    globalState.selectedJoinOrder = game.players[playerIndex].joinOrder

    # No manual debugSpritesVisible toggle: the default must expose the overlay.
    let packet = game.buildSpriteProtocolUpdates(
      globalState,
      nextGlobalState,
      replayTick = 1,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 10,
      replayLooping = false,
      replayEnabled = true,
      debugSprites = @[debugPacket]
    )
    check nextGlobalState.povActive
    check packet.packetHasSpriteLabel("debug planned path")
    check packet.packetHasNamespacedDebugObject(PovLayerId)

  test "fast click arriving during frame writeback is preserved":
    var
      next = initGlobalViewerState()
      pending = initGlobalViewerState()

    next.clearGlobalMouseEdges()
    pending.clearGlobalMouseEdges()
    pending.applyGlobalViewerMessage(
      mouseButtonMessage(ReplayBottomLeftLayerId, 11, 2, true)
    )
    pending.applyGlobalViewerMessage(
      mouseButtonMessage(ReplayBottomLeftLayerId, 100, 2, false)
    )
    next.mergeGlobalMouseEdges(pending)
    check next.mousePressed
    check next.mouseReleased
    check next.mousePressLayer == ReplayBottomLeftLayerId
    check next.mousePressX == 11
    check next.mousePressY == 2

  test "replay controls are hidden during live spectating":
    var game = initCrewriftForTest(defaultGameConfig())
    var state = initGlobalViewerState()
    var next: GlobalViewerState

    let packet = game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = false
    )
    check not packet.packetHasObject(ReplayTickObjectId)
    check not packet.packetHasObject(ReplayControlsObjectId)
    check not packet.packetHasObject(ReplayScrubberObjectId)

    state = next
    state.clickReplayLayer(ReplayBottomLeftLayerId, 11, 2)
    discard game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = false
    )
    check next.replayCommands.len == 0
    check next.replaySeekTick == -1

  test "replay controls stay visible during selected pov":
    var game = initCrewriftForTest(defaultGameConfig())
    let playerIndex = game.addPlayer("pov")
    game.phase = Playing
    var
      state = initGlobalViewerState()
      next: GlobalViewerState
    state.selectedJoinOrder = game.players[playerIndex].joinOrder

    let packet = game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 100,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 1000,
      replayLooping = false,
      replayEnabled = true
    )
    check next.povActive
    check packet.packetHasLayer(ReplayCenterBottomLayerId)
    check packet.packetHasLayer(ReplayBottomLeftLayerId)
    check packet.packetHasObject(ReplayTickObjectId)
    check packet.packetHasObject(ReplayControlsObjectId)
    check packet.packetHasObject(ReplayScrubberObjectId)

  test "hash mismatch warning is shown in the top center layer":
    var game = initCrewriftForTest(defaultGameConfig())
    var state = initGlobalViewerState()
    var next: GlobalViewerState

    let packet = game.buildSpriteProtocolUpdates(
      state,
      next,
      replayTick = 1208,
      replayPlaying = true,
      replaySpeed = 1,
      replayMaxTick = 2000,
      replayLooping = false,
      replayEnabled = true,
      replayMismatchTick = 1208
    )
    var
      foundSprite = false
      foundObject = false
    for message in packet.parseSpritePacket():
      if message.kind == spkSprite and
          message.sprite.label == "hash mismatch at tick 1208":
        foundSprite = true
      if message.kind == spkObject and
          message.objectDef.layer == ReplayMismatchLayerId:
        foundObject = true

    check foundSprite
    check foundObject
