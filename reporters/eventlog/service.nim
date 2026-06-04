import
  std/[json, locks, os, strutils],
  bitworld/runtime,
  mummy,
  protocol,
  report

const
  HealthPath = "/healthz"
  ReportPath = "/report"
  DefaultReporterHost = "0.0.0.0"
  DefaultReporterPort = 8080
  ReporterHostEnv = "REPORTER_HOST"
  ReporterPortEnv = "REPORTER_PORT"
  ReporterReplayUriEnv* = "REPORTER_REPLAY_URI"

var
  reportLock: Lock
  drainRequested = false

proc isWebSocketUpgrade(request: Request): bool =
  ## Returns true when the request is a WebSocket upgrade.
  request.headers["Sec-WebSocket-Key"].len > 0

proc sendJson(websocket: WebSocket, node: JsonNode) =
  ## Sends one JSON object as a text WebSocket message.
  websocket.send($node, TextMessage)

proc failRequest(
  websocket: WebSocket,
  request: ReportRequest,
  error: string
) =
  ## Sends one report_failed response for a parsed report request.
  websocket.sendJson(buildReportFailed(request.requestId, request.target, error))

proc replayUriSource(request: ReportRequest, uri: var string): string =
  ## Resolves the replay URI and returns the source label for error messages.
  uri = request.replayUri()
  if uri.len > 0:
    return "context.replay_uri"
  uri = getEnv(ReporterReplayUriEnv)
  if uri.len > 0:
    return ReporterReplayUriEnv
  ""

proc handleReportRequest(websocket: WebSocket, request: ReportRequest) =
  ## Handles one report_request message.
  {.gcsafe.}:
    withLock reportLock:
      if drainRequested:
        websocket.failRequest(request, "reporter is draining")
        return

      websocket.sendJson(buildReportAccepted(request.requestId))

      try:
        if request.target.kind != "episode":
          websocket.failRequest(
            request,
            "unsupported target kind: " & request.target.kind
          )
          return

        var uri = ""
        let source = request.replayUriSource(uri)
        if uri.len == 0:
          websocket.failRequest(
            request,
            "missing replay URI: set context.replay_uri or " &
              ReporterReplayUriEnv
          )
          return

        let
          bytes = readCogameUri(uri, source)
          payload = replayBytesToPayload(bytes)
        websocket.sendJson(
          buildReportOutput(request.requestId, request.target, payload)
        )
      except CatchableError as e:
        websocket.failRequest(request, e.msg)

proc requestDrain() =
  ## Marks the reporter draining and exits the process.
  {.gcsafe.}:
    withLock reportLock:
      drainRequested = true
  quit(0)

proc httpHandler(request: Request) =
  ## Handles reporter HTTP and WebSocket upgrade requests.
  if request.path == HealthPath and request.httpMethod == "GET":
    var headers: HttpHeaders
    headers["Content-Type"] = "text/plain; charset=utf-8"
    headers["Cache-Control"] = "no-cache"
    request.respond(200, headers, "healthy")
  elif request.path == ReportPath and request.httpMethod == "GET" and
      request.isWebSocketUpgrade():
    discard request.upgradeToWebSocket()
  else:
    var headers: HttpHeaders
    headers["Content-Type"] = "text/plain; charset=utf-8"
    request.respond(404, headers, "not found")

proc websocketHandler(
  websocket: WebSocket,
  event: WebSocketEvent,
  message: Message
) =
  ## Handles reporter WebSocket events.
  case event
  of OpenEvent:
    discard
  of MessageEvent:
    case message.kind
    of Ping:
      websocket.send(message.data, Pong)
    of Pong:
      discard
    of BinaryMessage:
      websocket.close()
    of TextMessage:
      var platformMessage: PlatformMessage
      try:
        platformMessage = parsePlatformMessage(message.data)
      except ProtocolError:
        websocket.close()
        return

      case platformMessage.kind
      of PmkReportRequest:
        websocket.handleReportRequest(platformMessage.request)
      of PmkDrain:
        requestDrain()
  of ErrorEvent, CloseEvent:
    discard

proc parseReporterPort(value: string): int =
  ## Parses one reporter port value.
  try:
    result = value.parseInt()
  except ValueError:
    raise newException(ValueError, ReporterPortEnv & " must be an integer")
  if result <= 0 or result > 65535:
    raise newException(ValueError, ReporterPortEnv & " must be 1..65535")

proc reporterHost(): string =
  ## Returns the reporter bind host from the environment.
  result = getEnv(ReporterHostEnv, DefaultReporterHost)
  if result.len == 0:
    result = DefaultReporterHost

proc reporterPort(): int =
  ## Returns the reporter bind port from the environment.
  let value = getEnv(ReporterPortEnv)
  if value.len == 0:
    return DefaultReporterPort
  value.parseReporterPort()

proc runReporterService*(host: string, port: int) =
  ## Runs the event-log reporter service.
  let server = newServer(
    httpHandler,
    websocketHandler,
    workerThreads = 1
  )
  echo "eventlog reporter listening on ", host, ":", port
  server.serve(Port(port), host)

when isMainModule:
  try:
    initLock(reportLock)
    runReporterService(reporterHost(), reporterPort())
  except CatchableError as e:
    stderr.writeLine("eventlog reporter failed: " & e.msg)
    quit(1)
