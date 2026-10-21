import std/[json, monotimes, os, times]
import bitworld/[native_stop, native_websocket]
import ../players/notsus/notsus/[protocols, native]

installNativeStopHandlers()
initializeNative(paramStr(1))
let connected = connectNativeWebSocket(paramStr(2),
  getMonoTime() + initDuration(seconds = 3), 16 * 1024 * 1024)
doAssert connected.kind == wsReady
let socket = connected.socket
let client = initProtocolClient()
client.expectedSlot = 2
var packed, unpacked: seq[uint8]
var advances = 0
while client.receivedSequence < 4:
  if client.receiveLatestFrameInto(socket, false, packed, unpacked, 1000):
    advances += client.frameAdvance
    doAssert client.consumedSequence == client.receivedSequence
    doAssert client.frameAdvance > 0
    doAssert client.framesDropped == client.frameAdvance - 1
doAssert advances == 3
let beforeVoting = client.frameBatch
doAssert socket.sendNativeText("first_phase_consumed",
  getMonoTime() + initDuration(seconds = 1)).kind == wsReady
doAssert client.receiveLatestFrameInto(socket, false, packed, unpacked, 1000)
doAssert client.frameAdvance == 1 and client.frameBatch == beforeVoting + 1
doAssert client.receivedSequence == 5
doAssert socket.sendNativeText("voting_phase_consumed",
  getMonoTime() + initDuration(seconds = 1)).kind == wsReady
discard client.receiveLatestFrameInto(socket, false, packed, unpacked, 1000)
doAssert client.nativeTerminal.kind != JNull
doAssert client.receivedSequence == 6
let lastBatch = client.frameBatch
let lastSequence = client.receivedSequence
client.reset()
doAssert client.frameBatch == lastBatch and client.receivedSequence == lastSequence
doAssert client.consumedSequence == lastSequence
socket.closeNativeWebSocket()
let joined = finishNative(client.nativeTerminal, true, beginFinalization())
doAssert joined
echo $(%*{"received_sequence": client.receivedSequence, "frame_batches": lastBatch,
  "frames_consumed": advances + 1, "socket_joined": true, "artifact_joined": joined})
