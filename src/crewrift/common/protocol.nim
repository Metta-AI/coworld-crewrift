import pixie

const
  ScreenWidth* = 128
  ScreenHeight* = 128
  TileSize* = 6
  DefaultHost* = "localhost"
  DefaultPort* = 8080
  DefaultBaseAddress* = "ws://localhost:8080"
  DefaultPlayerAddress* = DefaultBaseAddress & "/player"
  DefaultGlobalAddress* = DefaultBaseAddress & "/global"
  DefaultRewardAddress* = DefaultBaseAddress & "/reward"

  ButtonUp* = 1'u8 shl 0
  ButtonDown* = 1'u8 shl 1
  ButtonLeft* = 1'u8 shl 2
  ButtonRight* = 1'u8 shl 3
  ButtonSelect* = 1'u8 shl 4
  ButtonA* = 1'u8 shl 5
  ButtonB* = 1'u8 shl 6
  EmbeddedPalettePng = staticRead("../../../data/pallete.png")

type
  InputState* = object
    up*, down*, left*, right*, select*, attack*, b*: bool

var Palette*: array[16, ColorRGBA]

proc applyPalette(image: Image, source: string) =
  ## Copies the first 16 pixels from a palette image.
  if image.width < Palette.len or image.height < 1:
    raise newException(
      IOError,
      "Palette asset must be at least 16x1: " & source
    )

  for x in 0 ..< Palette.len:
    Palette[x] = image[x, 0]

proc loadPalette*(path = "") =
  ## Loads the embedded palette and ignores runtime palette paths.
  decodeImage(EmbeddedPalettePng).applyPalette("embedded " & path)

proc encodeInputMask*(input: InputState): uint8 =
  if input.up:
    result = result or ButtonUp
  if input.down:
    result = result or ButtonDown
  if input.left:
    result = result or ButtonLeft
  if input.right:
    result = result or ButtonRight
  if input.select:
    result = result or ButtonSelect
  if input.attack:
    result = result or ButtonA
  if input.b:
    result = result or ButtonB

proc decodeInputMask*(mask: uint8): InputState =
  result.up = (mask and ButtonUp) != 0
  result.down = (mask and ButtonDown) != 0
  result.left = (mask and ButtonLeft) != 0
  result.right = (mask and ButtonRight) != 0
  result.select = (mask and ButtonSelect) != 0
  result.attack = (mask and ButtonA) != 0
  result.b = (mask and ButtonB) != 0

proc blobFromBytes*(bytes: openArray[uint8]): string =
  ## Builds a binary websocket payload from protocol bytes.
  result = newString(bytes.len)
  for i, value in bytes:
    result[i] = char(value)
