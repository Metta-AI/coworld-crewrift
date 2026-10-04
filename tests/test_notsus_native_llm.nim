import std/[json, monotimes, os, times]
import bitworld/native_stop
import ../players/notsus/notsus/native as nativeAi

installNativeStopHandlers()
nativeAi.initializeNative(paramStr(1))
nativeAi.seat = nativeAi.NativeSeat(observed: true, configured: true, slot: 3, engineIndex: 0)
let mode = paramStr(2)
let deadline = getMonoTime() + initDuration(seconds = 5)
let firstTag = if mode in ["phase_cancel", "SIGTERM", "SIGINT"]: "first" else: "fixture"
nativeAi.startTalkToAI(@[nativeAi.ConversationMessage(role: "system", content: "Owned native fixture"),
  nativeAi.ConversationMessage(role: "user", content: "No policy or strength qualification")],
  firstTag, 1, deadline)
var response: NativeAsyncResult
if mode in ["phase_cancel", "SIGTERM", "SIGINT"]:
  while not fileExists(paramStr(1).parentDir() / "request-started") and getMonoTime() < deadline: sleep(1)
  doAssert getMonoTime() < deadline, "Native fixture did not actually start"
  if mode == "phase_cancel":
    doAssert nativeAi.cancelTalkToAI(getMonoTime() + initDuration(seconds = 2))
    nativeAi.startTalkToAI(@[ConversationMessage(role: "user", content: "Later ordinary phase")],
      "second", 2, getMonoTime() + initDuration(seconds = 5))
    while not response.ready:
      response = nativeAi.pollTalkToAI()
      sleep(1)
    doAssert response.error.len == 0 and response.reply == "later phase"
  else:
    while not interruptionRequested(): sleep(1)
else:
  while not response.ready and not interruptionRequested():
    response = nativeAi.pollTalkToAI()
    sleep(1)
let cleanup = nativeAi.beginFinalization()
let complete = nativeAi.finishNative(newJNull(), true, cleanup)
echo $(%*{"ready": response.ready, "error_kind": response.error,
  "private_artifact_returned": true, "complete": complete, "usage": response.usage})
