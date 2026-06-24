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

proc testDebugSpritePacket() =
  ## Tests that a player debug sprite packet is captured beside input state.
  var
    state = initPlayerViewerState()
    downMask = 0'u8
    pressedMask = 0'u8
    chatText = ""
    debugSprites: seq[uint8] = @[]
    packet: seq[uint8]
  packet.addObject(1, 2, 3, 4, MapLayerId, 5)

  state.applyPlayerViewerMessage(
    blobFromSpriteDebugSprites(packet),
    downMask,
    pressedMask,
    chatText,
    debugSprites
  )

  doAssert debugSprites == packet

echo "Testing input buffer"
testQuickPressRelease()
testHeldRepeat()
testHeldRetap()
testDebugSpritePacket()
echo "ok"
