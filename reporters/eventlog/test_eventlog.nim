import
  std/[json, net, options, os, osproc, unittest],
  whisky,
  zippy,
  crewrift/replays,
  ../../tools/expand_replay,
  protocol,
  report

type
  SavedEnv = object
    key: string
    value: string
    present: bool

const
  GameDir = currentSourcePath.parentDir.parentDir.parentDir
  NotsusReplayPath = GameDir / "tests" / "replays" / "notsus.bitreplay"
  RoundTripStartPort = 19080
  RoundTripEndPort = 19180

proc saveEnv(key: string): SavedEnv =
  ## Saves one environment variable for later restoration.
  result.key = key
  result.present = existsEnv(key)
  if result.present:
    result.value = getEnv(key)

proc restore(saved: SavedEnv) =
  ## Restores one previously saved environment variable.
  if saved.present:
    putEnv(saved.key, saved.value)
  else:
    delEnv(saved.key)

proc firstOpenPort(): int =
  ## Returns a localhost TCP port that is currently bindable.
  for port in RoundTripStartPort .. RoundTripEndPort:
    var socket = newSocket()
    try:
      socket.bindAddr(Port(port), "127.0.0.1")
      socket.close()
      return port
    except OSError:
      socket.close()
  raise newException(OSError, "no open test port found")

proc waitForServer(port: int) =
  ## Waits until the reporter service accepts TCP connections.
  for _ in 0 ..< 100:
    var socket = newSocket()
    try:
      socket.connect("127.0.0.1", Port(port), 100)
      socket.close()
      return
    except OSError:
      socket.close()
      sleep(100)
  raise newException(OSError, "reporter service did not start")

proc receiveJson(ws: WebSocket, timeoutMs: int): JsonNode =
  ## Receives one text WebSocket JSON message.
  let message = ws.receiveMessage(timeoutMs)
  doAssert message.isSome(), "timed out waiting for WebSocket message"
  doAssert message.get().kind == TextMessage, "expected text message"
  parseJson(message.get().data)

proc runReporterRoundTrip(): JsonNode =
  ## Runs a local reporter service and returns its report_output message.
  let
    port = firstOpenPort()
    hostEnv = saveEnv("REPORTER_HOST")
    portEnv = saveEnv("REPORTER_PORT")
    replayEnv = saveEnv("REPORTER_REPLAY_URI")
    nim = findExe("nim")

  require nim.len > 0
  putEnv("REPORTER_HOST", "127.0.0.1")
  putEnv("REPORTER_PORT", $port)
  putEnv("REPORTER_REPLAY_URI", "file://" & NotsusReplayPath)

  var process: Process
  var ws: WebSocket
  try:
    process = startProcess(
      nim,
      workingDir = GameDir,
      args = ["r", "reporters/eventlog/service.nim"],
      options = {poUsePath, poStdErrToStdOut}
    )
    waitForServer(port)

    ws = newWebSocket("ws://127.0.0.1:" & $port & "/report")
    ws.send($(%*{
      "type": "report_request",
      "request_id": "roundtrip-1",
      "target": {"kind": "episode", "id": "episode-1"},
      "reason": "test",
      "context": {}
    }))

    for _ in 0 ..< 10:
      let node = ws.receiveJson(30_000)
      case node["type"].getStr()
      of ReportAcceptedType:
        discard
      of ReportOutputType:
        result = node
        break
      else:
        doAssert false, "unexpected reporter message: " & $node

    require not result.isNil
    ws.send($(%*{"type": "drain", "reason": "test complete"}))
    let exitCode = process.waitForExit(10_000)
    check exitCode == 0
  finally:
    if not ws.isNil:
      ws.close()
    if not process.isNil and process.running():
      process.terminate()
      discard process.waitForExit(2_000)
      if process.running():
        process.kill()
    hostEnv.restore()
    portEnv.restore()
    replayEnv.restore()

suite "eventlog reporter protocol":
  test "parses report_request":
    let message = parsePlatformMessage($(%*{
      "type": "report_request",
      "request_id": "req-1",
      "target": {"kind": "episode", "id": "episode-1"},
      "reason": "completed",
      "context": {"replay_uri": "file:///tmp/replay.bitreplay"}
    }))

    check message.kind == PmkReportRequest
    check message.request.requestId == "req-1"
    check message.request.target.kind == "episode"
    check message.request.target.id == "episode-1"
    check message.request.reason == "completed"
    check message.request.replayUri() == "file:///tmp/replay.bitreplay"

  test "builds output and failure messages":
    let
      target = Target(kind: "episode", id: "episode-1")
      payload = newJArray()
      output = buildReportOutput("req-1", target, payload)
      failed = buildReportFailed("req-1", target, "boom")

    check output["type"].getStr() == ReportOutputType
    check output["mime"].getStr() == "application/json"
    check output["encoding"].getStr() == "json"
    check output["payload"].kind == JArray
    check failed["type"].getStr() == ReportFailedType
    check failed["error"].getStr() == "boom"

  test "rejects malformed requests":
    expect ProtocolError:
      discard parsePlatformMessage("not json")
    expect ProtocolError:
      discard parsePlatformMessage($(%*{"type": "unknown"}))
    expect ProtocolError:
      discard parsePlatformMessage($(%*{
        "type": "report_request",
        "request_id": "",
        "target": {"kind": "episode", "id": "episode-1"},
        "reason": "completed",
        "context": {}
      }))
    expect ProtocolError:
      discard parsePlatformMessage($(%*{
        "type": "report_request",
        "request_id": "req-1",
        "target": {"kind": "episode", "id": ""},
        "reason": "completed",
        "context": {}
      }))
    expect ProtocolError:
      discard parsePlatformMessage($(%*{
        "type": "report_request",
        "request_id": "req-1",
        "target": {"kind": "episode", "id": "episode-1"},
        "reason": "completed",
        "context": {"replay_uri": 42}
      })).request.replayUri()

suite "eventlog extraction":
  test "expands bundled replay into structured rows":
    let
      data = loadReplay(NotsusReplayPath)
      timeline = expandReplayTimeline(data)
      payload = replayBytesToPayload(readFile(NotsusReplayPath))

    check not timeline.hashFailed
    check timeline.failTick == 0
    check timeline.tickCount >= int(data.hashes[^1].tick)
    check timeline.events.len > 100
    check timeline.events.len < 1000
    check payload.kind == JArray
    check payload.len == timeline.events.len

    for row in payload:
      check row.kind == JObject
      check row.hasKey("ts")
      check row["ts"].kind == JInt
      check row.hasKey("player")
      check row["player"].kind == JInt
      check row.hasKey("key")
      check row["key"].kind == JString
      check row.hasKey("value")
      check row["value"].kind == JObject

  test "accepts zlib-compressed replay bytes":
    let
      raw = readFile(NotsusReplayPath)
      compressed = compress(raw, dataFormat = dfZlib)
      rawPayload = replayBytesToPayload(raw)
      compressedPayload = replayBytesToPayload(compressed)

    check compressedPayload.kind == JArray
    check compressedPayload.len == rawPayload.len

  test "exports parseable JSON schema":
    let schema = eventLogSchemaJson()
    check schema["type"].getStr() == "array"
    check schema["items"]["properties"]["key"]["enum"].len == 15

suite "eventlog service":
  test "serves a mock WebSocket report_request round trip":
    let output = runReporterRoundTrip()

    check output["type"].getStr() == ReportOutputType
    check output["request_id"].getStr() == "roundtrip-1"
    check output["target"]["kind"].getStr() == "episode"
    check output["mime"].getStr() == "application/json"
    check output["encoding"].getStr() == "json"
    check output["payload"].kind == JArray
    check output["payload"].len > 100
