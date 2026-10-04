## One owned native model request. Private records precede worker admission;
## request controls and received buffers remain alive through the worker join.
import std/[atomics, base64, json, monotimes, options, os, sets, strutils,
  sysrand, tables, times, unicode]
import bitworld/[artifact_runtime, native_http, native_stop]
import zippy/ziparchives
import scripted

type
  PolicyOrigin* = enum
    NativeOrigin, TeacherOrigin
  PolicyProfile* = object
    case origin*: PolicyOrigin
    of NativeOrigin: discard
    of TeacherOrigin: teacherAuthoritySha256*: string
  ConversationMessage* = object
    role*, content*: string
  NativeAsyncResult* = object
    origin*: PolicyOrigin
    ready*: bool
    tag*, generationId*, reply*, usage*, error*: string
    observationTick*: int
  OwnedRequest = object
    control: NativeRequestControl
    completed: Atomic[bool]
    requestBytes, responseBytes: pointer
    requestLen, responseLen: int
    deadline: MonoTime
  NativeSeat* = object
    observed*, configured*: bool
    slot*, engineIndex*: int

var
  seat*: NativeSeat
  job: ptr OwnedRequest
  worker: Thread[ptr OwnedRequest]
  active = false
  sealed = false
  requestTag, generationId: string
  generation: JsonNode
  journal: File
  journalPath: string
  outcome: JsonNode
  finalDeadline: Option[MonoTime]
  teacherPending: Option[NativeAsyncResult]

proc policyProfile*(): PolicyProfile =
  case getEnv("NOTSUS_POLICY_ORIGIN", "native")
  of "native": PolicyProfile(origin: NativeOrigin)
  of "teacher": PolicyProfile(origin: TeacherOrigin,
    teacherAuthoritySha256: scripted.teacherAuthoritySha256())
  else: raise newException(ValueError, "Unsupported Notsus policy origin")

proc privateRecord*(event: JsonNode) =
  doAssert not sealed, "Private native records are sealed"
  journal.writeLine($event)
  journal.flushFile()

proc initializeNative*(path: string) =
  doAssert not active and not sealed
  journalPath = path
  createDir(path.parentDir())
  doAssert not fileExists(path), "Private native journal already exists"
  journal = open(path, fmWrite)
  setFilePermissions(path, {fpUserRead, fpUserWrite})

proc beginFinalization*(): MonoTime =
  ## Every terminal/STOP owner shares this original deadline through ZIP delivery.
  if finalDeadline.isNone:
    let started = getMonoTime()
    finalDeadline = some(started + initDuration(seconds = 2))
    privateRecord(%*{"kind": "owned_finalization_started",
      "observed_monotonic_ns": started.ticks, "deadline_monotonic_ns": finalDeadline.get().ticks})
  finalDeadline.get()

proc bindEngineSeat*(expectedSlot: int, welcome: JsonNode) =
  privateRecord(%*{"kind": "engine_admission", "payload": welcome})
  doAssert not seat.observed, "Duplicate native engine admission"
  doAssert welcome["kind"].getStr() == "native_welcome" and
    welcome["protocol"].getStr() == "crewrift.native-evidence.v1",
    "Unsupported native engine admission"
  let actual = NativeSeat(observed: true,
    configured: welcome["configured_seat"].getBool(),
    slot: welcome["player_slot"].getInt(),
    engineIndex: welcome["engine_player_index"].getInt())
  doAssert actual.slot >= 0 and actual.engineIndex >= 0, "Invalid assigned engine seat"
  if expectedSlot >= 0:
    doAssert actual.configured and actual.slot == expectedSlot,
      "Hosted native seat differs from authenticated engine assignment"
  seat = actual

proc runRequest(current: ptr OwnedRequest) {.thread.} =
  var bytes = newString(current.requestLen)
  copyMem(bytes[0].addr, current.requestBytes, bytes.len)
  let request = parseJson(bytes)
  var headers: HttpHeaders
  for pair in request["headers"]: headers.add((pair[0].getStr(), pair[1].getStr()))
  let response = performNativePost(request["url"].getStr(), headers,
    request["body"].getStr(), current.deadline, current.control)
  let observed = response.httpStatus.isSome or response.headerBytes.len > 0 or response.bodyBytes.len > 0
  let received = $(%*{"transfer_kind": $response.kind, "http_status": response.httpStatus,
    "response_headers_b64": (if observed: %encode(response.headerBytes) else: newJNull()),
    "response_body_b64": (if observed: %encode(response.bodyBytes) else: newJNull()),
    "response_complete": (if observed: %response.transferComplete else: newJNull()),
    "response_reader_joined": response.responseReaderJoined,
    "latency_ms": response.latencyMs})
  current.responseLen = received.len
  current.responseBytes = allocShared(received.len)
  doAssert current.responseBytes != nil
  copyMem(current.responseBytes, received[0].unsafeAddr, received.len)
  current.completed.store(true, moRelease)

proc startTalkToAI*(messages: openArray[ConversationMessage], tag: string,
    observationTick: int, deadline: MonoTime, profile: PolicyProfile) =
  doAssert not sealed and finalDeadline.isNone and not active and teacherPending.isNone, "Previous policy request must join before admission"
  doAssert seat.observed, "Native request requires actual engine admission"
  doAssert not interruptionRequested(), "Native inference is stopped"
  var prompt = newJArray()
  var chat = newJArray()
  var system = ""
  for message in messages:
    prompt.add(%*{"role": message.role, "content": message.content})
    if message.role == "system":
      if system.len > 0: system.add "\n\n"
      system.add message.content
    else:
      chat.add(%*{"role": message.role, "content": message.content})
  requestTag = tag
  generationId = ""
  for value in urandom(16): generationId.add(toHex(value, 2).toLowerAscii())
  if profile.origin == TeacherOrigin:
    doAssert seat.configured, "A teacher requires authenticated configured seat admission"
    doAssert profile.teacherAuthoritySha256 == scripted.teacherAuthoritySha256()
    let started = getMonoTime()
    let action = scriptedSocialAction(prompt, observationTick)
    let reply = $(%action)
    generation = %*{"kind": "teacher_generation", "origin": "teacher",
      "generation_id": generationId, "teacher_authority_sha256": profile.teacherAuthoritySha256,
      "tag": tag, "phase": "social_controller", "parser_id": "notsus.social.parseSocialLlmResult",
      "observation_tick": observationTick, "engine_player_index": seat.engineIndex,
      "player_slot": seat.slot, "prompt": prompt, "completion_text": reply,
      "parsed_action": action, "duration_ms": (getMonoTime() - started).inMilliseconds}
    privateRecord(generation)
    teacherPending = some(NativeAsyncResult(origin: TeacherOrigin, ready: true,
      tag: tag, generationId: generationId, reply: reply, observationTick: observationTick))
    return
  let endpoint = getEnv("COWORLD_LLM_ENDPOINT").strip(chars = {'/'})
  doAssert endpoint.len > 0, "COWORLD_LLM_ENDPOINT is required"
  let model = getEnv("COWORLD_LLM_MODEL", "anthropic/claude-haiku-4.5")
  let temperature = parseFloat(getEnv("COWORLD_LLM_TEMPERATURE", "0.2"))
  doAssert temperature >= 0 and temperature <= 2
  let request = %*{"model": model, "max_tokens": 512, "temperature": temperature,
    "system": system, "messages": chat}
  var headers: HttpHeaders = @[("Content-Type", "application/json"),
    ("Accept-Encoding", "identity")]
  if seat.configured: headers.add(("X-Coworld-Player-Slot", $seat.slot))
  generation = %*{"kind": "native_generation", "origin": "native", "generation_id": generationId,
    "tag": tag, "purpose": "learner", "inference_mode": "text_action",
    "phase": "social_controller", "parser_id": "notsus.social.parseSocialLlmResult",
    "observation_tick": observationTick, "engine_player_index": seat.engineIndex,
    "player_slot": (if seat.configured: %seat.slot else: newJNull()),
    "prompt": prompt, "request": request, "model": model,
    "decoder": {"transport": "native_messages", "temperature": temperature,
      "max_tokens": 512}, "platform_call_id": newJNull(),
    "raw_response": newJNull(), "response_headers": newJNull(),
    "response_body_b64": newJNull(), "response_headers_b64": newJNull(),
    "http_status": newJNull(), "response_complete": newJNull(),
    "response_reader_joined": newJNull()}
  privateRecord(generation)
  var headerPairs = newJArray()
  for (name, value) in headers: headerPairs.add(%*[name, value])
  let serialized = $(%*{"url": endpoint & "/v1/messages",
    "headers": headerPairs, "body": $request})
  job = cast[ptr OwnedRequest](allocShared0(sizeof(OwnedRequest)))
  doAssert job != nil
  job.requestLen = serialized.len
  job.requestBytes = allocShared(serialized.len)
  doAssert job.requestBytes != nil
  copyMem(job.requestBytes, serialized[0].unsafeAddr, serialized.len)
  job.deadline = deadline
  active = true
  createThread(worker, runRequest, job)

proc pollTalkToAI*(): NativeAsyncResult =
  if teacherPending.isSome:
    result = teacherPending.get()
    teacherPending = none(NativeAsyncResult)
    return
  if not active or not job.completed.load(moAcquire): return
  joinThread(worker)
  var receivedBytes = newString(job.responseLen)
  copyMem(receivedBytes[0].addr, job.responseBytes, receivedBytes.len)
  let received = parseJson(receivedBytes)
  deallocShared(job.requestBytes)
  deallocShared(job.responseBytes)
  deallocShared(job)
  job = nil
  active = false
  result.origin = NativeOrigin
  result.ready = true
  result.tag = requestTag
  result.generationId = generationId
  result.observationTick = generation["observation_tick"].getInt()
  for name, value in received: generation[name] = value
  let body = if received["response_body_b64"].kind == JNull: "" else: decode(received["response_body_b64"].getStr())
  let headerBytes = if received["response_headers_b64"].kind == JNull: "" else: decode(received["response_headers_b64"].getStr())
  if received["response_body_b64"].kind != JNull and validateUtf8(body) == -1: generation["raw_response"] = %body
  # Exact byte evidence is durable before identity/schema validation can fail.
  privateRecord(generation)
  var headers = initTable[string, string]()
  var identities = initHashSet[string]()
  for line in headerBytes.splitLines():
    if line.startsWith("HTTP/"):
      headers.clear()
      identities.clear()
    elif line.len > 0:
      let colon = line.find(':')
      if colon <= 0:
        result.error = "invalid_received_header"
        break
      let name = line[0 ..< colon].toLowerAscii()
      if validateUtf8(name) != -1:
        result.error = "invalid_received_header_name"
        break
      var value = line[colon + 1 .. ^1].strip()
      if validateUtf8(value) != -1:
        # Header values may contain HTTP obs-text. Keep exact wire bytes separately;
        # the JSON text projection uses Latin-1 when those bytes are not UTF-8.
        var text = ""
        for byte in value: text.add(Rune(ord(byte)).toUTF8())
        value = text
      if name in ["content-encoding", "x-softmax-llm-call-id", "request-id", "x-request-id",
          "x-coworld-checkpoint-sha256", "x-coworld-tokenizer-sha256",
          "x-coworld-chat-template-sha256"]:
        if name in identities:
          result.error = "duplicate_response_control_header"
        identities.incl(name)
      headers[name] = value
  if received["response_headers_b64"].kind != JNull: generation["response_headers"] = %headers
  if "request-id" in headers and "x-request-id" in headers and headers["request-id"] != headers["x-request-id"]:
    result.error = "conflicting_identity_headers"
  for (header, field) in [("x-softmax-llm-call-id", "platform_call_id"),
      ("x-coworld-checkpoint-sha256", "model_identity"),
      ("x-coworld-tokenizer-sha256", "tokenizer_identity"),
      ("x-coworld-chat-template-sha256", "chat_template_sha256")]:
    if header in headers and result.error.len == 0: generation[field] = %headers[header]
  if "request-id" in headers: generation["provider_request_id"] = %headers["request-id"]
  elif "x-request-id" in headers: generation["provider_request_id"] = %headers["x-request-id"]
  if received["transfer_kind"].getStr() != "nhComplete": result.error = received["transfer_kind"].getStr()
  elif received["http_status"].kind == JNull or received["http_status"].getInt() != 200:
    result.error = "native_http_rejected"
  elif validateUtf8(body) != -1: result.error = "invalid_utf8"
  elif "content-encoding" in headers and headers["content-encoding"].toLowerAscii() notin ["", "identity"]:
    result.error = "unsupported_content_encoding"
  if result.error.len == 0:
    try:
      let response = parseJson(body)
      generation["response"] = response
      if response.kind != JObject or response["content"].kind != JArray:
        raise newException(ValueError, "Invalid native response schema")
      for part in response["content"]:
        if part.kind != JObject or part["type"].kind != JString:
          raise newException(ValueError, "Invalid native content schema")
        if part["type"].getStr() == "text":
          if part["text"].kind != JString:
            raise newException(ValueError, "Invalid native text schema")
          result.reply.add part["text"].getStr()
      generation["response_text"] = %result.reply
      if response.hasKey("usage"):
        let usage = response["usage"]
        if usage.kind != JObject or usage["input_tokens"].kind != JInt or
            usage["output_tokens"].kind != JInt or usage["input_tokens"].getInt() < 0 or
            usage["output_tokens"].getInt() < 0:
          raise newException(ValueError, "Invalid native usage schema")
        generation["usage"] = usage
        result.usage = $(%*{"input_tokens": usage["input_tokens"].getInt(),
          "output_tokens": usage["output_tokens"].getInt()})
      if response.hasKey("stop_reason"): generation["stop_reason"] = response["stop_reason"]
    except JsonParsingError, KeyError, ValueError:
      # The old poll boundary retains native failure, without exposing private tokens.
      result.error = "native_response_schema_rejected"
  generation["error_kind"] = (if result.error.len > 0: %result.error else: newJNull())
  privateRecord(generation)

proc cancelTalkToAI*(deadline: MonoTime): bool =
  if teacherPending.isSome:
    privateRecord(%*{"kind": "teacher_decision_cancelled", "generation_id": teacherPending.get().generationId})
    teacherPending = none(NativeAsyncResult)
  let phaseCleanup = active and finalDeadline.isNone
  if phaseCleanup:
    # Bind before telemetry: a writer failure cannot reset this owned cleanup budget.
    finalDeadline = some(deadline)
  if active:
    let started = getMonoTime()
    try:
      privateRecord(%*{"kind": "owned_request_cancellation_started",
        "generation_id": generationId, "observed_monotonic_ns": started.ticks,
        "deadline_monotonic_ns": deadline.ticks})
    finally:
      # A failed private writer must still cancel and join its owned native worker.
      cancelNativeRequest(job.control)
      while active:
        discard pollTalkToAI()
        if active: sleep(1)
    let finished = getMonoTime()
    privateRecord(%*{"kind": "owned_request_cancellation_joined",
      "generation_id": generationId, "observed_monotonic_ns": finished.ticks,
      "joined": true, "deadline_met": finished < deadline})
  result = getMonoTime() < deadline
  if result and phaseCleanup:
    finalDeadline = none(MonoTime)
  if not result and finalDeadline.isNone:
    # A missed phase cleanup cannot acquire a fresh terminal/artifact budget.
    finalDeadline = some(deadline)

proc finishNative*(terminal: JsonNode, socketJoined: bool, deadline: MonoTime): bool =
  doAssert not sealed
  try:
    let requestsJoined = cancelTalkToAI(deadline)
    result = terminal.kind != JNull and socketJoined and requestsJoined
    outcome = %*{"kind": "private_outcome", "status": (if result: "completed" else: "truncated"),
      "terminal": terminal, "socket_joined": socketJoined, "requests_joined": not active, "cleanup_deadline_met": requestsJoined,
      "configured_seat": seat.configured, "player_slot": (if seat.configured: %seat.slot else: newJNull()),
      "engine_player_index": (if seat.observed: %seat.engineIndex else: newJNull()),
      "source_revision": getEnv("COWORLD_SOURCE_REVISION"),
      "image_digest": getEnv("COWORLD_GAME_IMAGE_DIGEST")}
    privateRecord(outcome)
  finally:
    journal.close()
    sealed = true
  let zip = createZipArchive({"native.jsonl": readFile(journalPath)}.toTable())
  doAssert zip.len <= 200 * 1024 * 1024, "Private player ZIP exceeds 200 MiB"
  let destination = getEnv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL")
  doAssert destination.len > 0, "Private artifact destination is required"
  writeCogameArtifact(destination, zip, "application/zip", "Notsus private artifact", deadline)
  result = result and getMonoTime() < deadline
