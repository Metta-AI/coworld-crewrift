import crewrift/static_replay

var
  viewer: StaticReplayViewer
  lastError: string
  frameBuffer: string

proc bytesFromMemory(data: pointer, length: cint): string =
  if data.isNil or length < 0:
    raise newException(ValueError, "Invalid replay buffer")
  result = newString(int(length))
  if length > 0:
    copyMem(result[0].addr, data, int(length))

proc refreshFrame() =
  if not viewer.isNil:
    frameBuffer = viewer.frameBytes()

proc crLoadReplay(data: pointer, length: cint): cint
    {.exportc: "cr_load_replay", cdecl.} =
  try:
    viewer = initStaticReplayViewer(bytesFromMemory(data, length))
    lastError.setLen(0)
    refreshFrame()
    1
  except CatchableError as error:
    viewer = nil
    frameBuffer.setLen(0)
    lastError = error.msg
    0

proc crAdvance() {.exportc: "cr_advance", cdecl.} =
  if not viewer.isNil:
    try:
      viewer.advanceFrame()
      refreshFrame()
    except CatchableError as error:
      lastError = error.msg

proc crInput(data: pointer, length: cint) {.exportc: "cr_input", cdecl.} =
  if not viewer.isNil:
    try:
      viewer.applyClientPacket(bytesFromMemory(data, length))
      refreshFrame()
    except CatchableError as error:
      lastError = error.msg

proc crFramePointer(): pointer {.exportc: "cr_frame_ptr", cdecl.} =
  if frameBuffer.len == 0: nil else: frameBuffer[0].addr

proc crFrameLength(): cint {.exportc: "cr_frame_len", cdecl.} =
  cint(frameBuffer.len)

proc crTick(): cint {.exportc: "cr_tick", cdecl.} =
  if viewer.isNil: -1 else: cint(viewer.sim.tickCount)

proc crMaxTick(): cint {.exportc: "cr_max_tick", cdecl.} =
  if viewer.isNil: -1 else: cint(viewer.maxTick())

proc crPlaying(): cint {.exportc: "cr_playing", cdecl.} =
  if not viewer.isNil and viewer.replay.playing: 1 else: 0

proc crErrorPointer(): cstring {.exportc: "cr_error_ptr", cdecl.} =
  lastError.cstring
