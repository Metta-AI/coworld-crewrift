import
  std/[os, unittest],
  bitworld/[pixelfonts, bitstreamprotocol, server],
  crewrift/sim,
  crewrift/texts

const
  GameDir = currentSourcePath.parentDir.parentDir

type TextCase = object
  text: string
  x: int
  y: int

proc loadTestAsciiSprites(): PixelFont =
  ## Loads the Crewrift tiny ASCII font for text OCR tests.
  loadPalette()
  readTiny5Font()

proc renderText(
  asciiSprites: PixelFont,
  text: string,
  x,
  y: int
): seq[uint8] =
  ## Draws one text sample to a fresh 128 by 128 framebuffer.
  var fb = initFramebuffer()
  fb.clearFrame(SpaceColor)
  fb.blitAsciiText(asciiSprites, text, x, y)
  fb.indices

proc assertTextRoundTrip(
  asciiSprites: PixelFont,
  sample: TextCase
) =
  ## Checks that rendered text can be read back from pixels.
  checkpoint sample.text
  require sample.x + asciiSprites.asciiTextWidth(sample.text) <= ScreenWidth
  require sample.y + asciiSprites.height <= ScreenHeight

  let
    frame = renderText(asciiSprites, sample.text, sample.x, sample.y)
    found = frame.findAsciiText(asciiSprites, sample.text)
    read = frame.readAsciiRun(
      asciiSprites,
      sample.x,
      sample.y,
      sample.text.len
    )

  check found.found
  check found.x == sample.x
  check found.y == sample.y
  check read == sample.text

suite "ascii text":
  test "round trips":
    let
      asciiSprites = loadTestAsciiSprites()
      cases = [
        TextCase(text: "red sus", x: 0, y: 0),
        TextCase(text: "body in nav", x: 21, y: 48),
        TextCase(text: "pink saw blue", x: 7, y: 83),
        TextCase(text: "purple sus", x: 30, y: 14),
        TextCase(text: "where?", x: 72, y: 119),
        TextCase(text: "red, sus!", x: 12, y: 101)
      ]
    for sample in cases:
      assertTextRoundTrip(asciiSprites, sample)

  test "blank background is ignored":
    let asciiSprites = loadTestAsciiSprites()
    let text = "red sus"
    var fb = initFramebuffer()
    fb.clearFrame(SpaceColor)
    check not fb.indices.findAsciiText(asciiSprites, text).found
    fb.blitAsciiText(asciiSprites, text, 13, 47)
    let
      found = fb.indices.findAsciiText(asciiSprites, text)
      line = fb.indices.readAsciiRun(
        asciiSprites,
        found.x,
        found.y,
        text.len
      )
    check found.found
    check line == text

  test "chat wrap drops leading space":
    let
      font = loadTestAsciiSprites()
      message = "hi there i am not that bad i did all that and this thing"
      first = font.sliceChatLine(message, 0)
      second = font.sliceChatLine(message, 1)
    check first.len > 15
    check font.textWidth(first) <= VoteChatTextPixels
    require second.len > 0
    check second[0] != ' '
    check font.chatLineCount(message) >= 2

    let trailingSpace = "short trailing "
    check font.chatLineCount(trailingSpace) == 1
