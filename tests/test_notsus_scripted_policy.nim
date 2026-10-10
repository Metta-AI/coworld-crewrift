import std/[json, monotimes, os, times]
import ../players/notsus/notsus/[native, scripted, socials]

initializeNative(paramStr(1))
seat = NativeSeat(observed: true, configured: true, slot: 2, engineIndex: 3)
let profile = PolicyProfile(origin: TeacherOrigin, teacherAuthoritySha256: teacherAuthoritySha256())
let messages = @[ConversationMessage(role: "system", content: "Ordinary social schema"),
  ConversationMessage(role: "user", content: "Visible controller context only")]
let deadline = getMonoTime() + initDuration(seconds = 5)
startTalkToAI(messages, "first", 9, deadline, profile)
let response = pollTalkToAI()
doAssert response.ready and response.origin == TeacherOrigin and response.error.len == 0
doAssert response.observationTick == 9
let parsed = parseSocialLlmResult(response.reply)
doAssert parsed.ok and parsed.social.claims.len == 0
let capture = parseJson(readFile(paramStr(1)))
doAssert capture["kind"].getStr() == "teacher_generation"
doAssert capture["teacher_authority_sha256"].getStr() == teacherAuthoritySha256()
doAssert capture["prompt"][1]["content"].getStr() == messages[1].content
for forbidden in ["platform_call_id", "model", "decoder", "request", "raw_response", "response_reader_joined"]:
  doAssert not capture.hasKey(forbidden)
startTalkToAI(messages, "cancelled", 10, deadline, profile)
doAssert cancelTalkToAI(getMonoTime() + initDuration(seconds = 2))
doAssert not pollTalkToAI().ready
let cleanup = beginFinalization()
# The separate negative mode must fail before any new teacher admission.
if paramCount() > 1: startTalkToAI(messages, "late", 11, cleanup, profile)
echo $(%*{"teacher_authority_sha256": teacherAuthoritySha256(), "normal_parser": true,
  "immediate_scripted_profile": true, "cancelled_pending_not_selected": true})
