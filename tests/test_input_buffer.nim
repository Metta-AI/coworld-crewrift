import
  bitworld/spriteprotocol,
  crewrift/[global, sim]

proc testQuickPressRelease() =
  ## Tests that a down and up packet leaves one pressed bit.
  var
    state = initPlayerViewerState()
    downMask = 0'u8
    pressedMask = 0'u8
    chatText = ""
    debugSprites: seq[uint8] = @[]

  state.applyPlayerViewerMessage(
    blobFromSpriteMask(ButtonA) & blobFromSpriteMask(0),
    downMask,
    pressedMask,
    chatText,
    debugSprites
  )

  doAssert downMask == 0'u8
  doAssert (pressedMask and ButtonA) == ButtonA

proc testHeldRepeat() =
  ## Tests that a repeated held mask does not make another press.
  var
    state = initPlayerViewerState()
    downMask = ButtonA
    pressedMask = 0'u8
    chatText = ""
    debugSprites: seq[uint8] = @[]

  state.applyPlayerViewerMessage(
    blobFromSpriteMask(ButtonA),
    downMask,
    pressedMask,
    chatText,
    debugSprites
  )

  doAssert downMask == ButtonA
  doAssert pressedMask == 0'u8

proc testHeldRetap() =
  ## Tests that release and press packets leave one pressed bit.
  var
    state = initPlayerViewerState()
    downMask = ButtonA
    pressedMask = 0'u8
    chatText = ""
    debugSprites: seq[uint8] = @[]

  state.applyPlayerViewerMessage(
    blobFromSpriteMask(0) & blobFromSpriteMask(ButtonA),
    downMask,
    pressedMask,
    chatText,
    debugSprites
  )

  doAssert downMask == ButtonA
  doAssert (pressedMask and ButtonA) == ButtonA

proc testDebugSpritePackets() =
  ## Tests that player debug sprite packets are captured beside input state.
  var
    state = initPlayerViewerState()
    downMask = 0'u8
    pressedMask = 0'u8
    chatText = ""
    debugSprites: seq[uint8] = @[]
    packet: seq[uint8]
    extraPacket: seq[uint8]
  packet.addObject(1, 2, 3, 4, MapLayerId, 5)
  extraPacket.addObject(6, 7, 8, 9, MapLayerId, 10)

  state.applyPlayerViewerMessage(
    blobFromSpriteDebugSprites(packet) &
      blobFromSpriteDebugSprites(extraPacket),
    downMask,
    pressedMask,
    chatText,
    debugSprites
  )

  doAssert debugSprites == packet & extraPacket

echo "Testing input buffer"
testQuickPressRelease()
testHeldRepeat()
testHeldRetap()
testDebugSpritePackets()
echo "ok"
