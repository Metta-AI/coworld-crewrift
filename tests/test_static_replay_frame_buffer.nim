import
  std/[os, tables],
  bitworld/spriteprotocol

include ../replay_viewer/crewrift_replay_wasm

const
  GameDir = currentSourcePath.parentDir.parentDir
  ReplayPath = GameDir / "tests" / "replays" / "notsus.bitreplay"
  InterstitialLayerId = 2
  ProtocolVoteIconObjectBase = 9300
  ProtocolLobbyIconObjectBase = 9400
  ProtocolRoleIconObjectBase = 9500
  ProtocolResultIconObjectBase = 9600

type
  TrackedObject = object
    x, y, layer: int

var objects: Table[int, TrackedObject]

proc applyFrameBytes() =
  ## Applies the accumulated frame buffer like the browser renderer would,
  ## then clears it like static_replay_adapter.js does after each read.
  let length = int(crFrameLength())
  if length == 0:
    return
  var raw = newSeq[uint8](length)
  copyMem(raw[0].addr, crFramePointer(), length)
  crFrameClear()
  for message in raw.parseSpritePacket():
    case message.kind
    of spkClearObjects:
      objects.clear()
    of spkObject:
      objects[message.objectDef.id] = TrackedObject(
        x: message.objectDef.x,
        y: message.objectDef.y,
        layer: message.objectDef.layer
      )
    of spkDeleteObject:
      objects.del(message.objectId)
    else:
      discard

proc countLayerRange(layer, base: int): int =
  ## Counts live objects for one interstitial object-id namespace.
  for id, obj in objects:
    if obj.layer == layer and id >= base and id < base + 100:
      inc result

proc loadFixture() =
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    var bytes = readFile(ReplayPath)
    doAssert crLoadReplay(bytes[0].addr, cint(bytes.len)) == 1
  finally:
    setCurrentDir(previousDir)

proc testSlowReaderKeepsDeletions() =
  ## Tests that reading frames slower than they render loses no deletions.
  ## The browser adapter can advance several frames per animation callback
  ## while reading the buffer once; interstitial deletions emitted by the
  ## unread frames must survive, or startup screens (lobby, role reveal)
  ## stay overlaid on later meeting screens.
  loadFixture()
  applyFrameBytes()
  # Advance to a mid-replay voting tick, reading only every third frame.
  var advances = 0
  while crTick() < 2200:
    crAdvance()
    inc advances
    if advances mod 3 == 0:
      applyFrameBytes()
  applyFrameBytes()
  doAssert crTick() >= 2200
  # tick 2200 sits inside the fixture's second meeting: the vote grid must
  # be present and every startup interstitial must be fully deleted.
  doAssert countLayerRange(InterstitialLayerId, ProtocolVoteIconObjectBase) > 0
  doAssert countLayerRange(InterstitialLayerId, ProtocolLobbyIconObjectBase) == 0
  doAssert countLayerRange(InterstitialLayerId, ProtocolRoleIconObjectBase) == 0
  doAssert countLayerRange(InterstitialLayerId, ProtocolResultIconObjectBase) == 0

proc testFrameBufferAccumulatesUntilCleared() =
  ## Tests that unread frames accumulate and cr_frame_clear empties them.
  loadFixture()
  crFrameClear()
  doAssert crFrameLength() == 0
  crAdvance()
  let one = crFrameLength()
  doAssert one > 0
  crAdvance()
  doAssert crFrameLength() > one
  crFrameClear()
  doAssert crFrameLength() == 0

echo "Testing static replay frame buffer"
testSlowReaderKeepsDeletions()
testFrameBufferAccumulatesUntilCleared()
echo "ok"
