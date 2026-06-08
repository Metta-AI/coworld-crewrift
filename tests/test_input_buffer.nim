import
  bitworld/spriteprotocol,
  crewrift/global

proc testQuickPressRelease() =
  ## Tests that a down and up packet leaves one pressed bit.
  var
    state = initPlayerViewerState()
    downMask = 0'u8
    pressedMask = 0'u8
    chatText = ""

  state.applyPlayerViewerMessage(
    blobFromSpriteMask(ButtonA) & blobFromSpriteMask(0),
    downMask,
    pressedMask,
    chatText
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

  state.applyPlayerViewerMessage(
    blobFromSpriteMask(ButtonA),
    downMask,
    pressedMask,
    chatText
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

  state.applyPlayerViewerMessage(
    blobFromSpriteMask(0) & blobFromSpriteMask(ButtonA),
    downMask,
    pressedMask,
    chatText
  )

  doAssert downMask == ButtonA
  doAssert (pressedMask and ButtonA) == ButtonA

echo "Testing input buffer"
testQuickPressRelease()
testHeldRepeat()
testHeldRetap()
echo "ok"
