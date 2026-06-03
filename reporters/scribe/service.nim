import
  std/[locks, nativesockets, os, parseopt, strutils],
  mummy,
  scribe/[event_log, parquet, protocol, report, timeline, uri_io]

type
  ServiceConfig = object
    host: string
    port: int
    maxConcurrency: int

  ServiceState = object
    lock: Lock
    activeJobs: int
    maxConcurrency: int

const
  DefaultHost = "0.0.0.0"
  DefaultPort = 8080
  DefaultMaxConcurrency = 2
  ReportPath = "/report"
  HealthPath = "/healthz"

var serviceState: ServiceState

proc parsePositiveInt(value, source: string): int =
  try:
    result = value.parseInt()
  except ValueError:
    raise newException(ValueError, source & " must be an integer")
  if result <= 0:
    raise newException(ValueError, source & " must be positive")

proc parsePort(value, source: string): int =
  result = parsePositiveInt(value, source)
  if result > 65535:
    raise newException(ValueError, source & " must be <= 65535")

proc usageText(): string =
  """
Crewrift scribe reporter service

Options:
  --host:<host>              Bind host. Default: 0.0.0.0
  --port:<port>              Bind port. Default: 8080
  --max-concurrency:<count>  Concurrent report jobs. Default: 2
  --help, -h                 Show this help.
"""

proc parseServiceConfig(): ServiceConfig =
  result = ServiceConfig(
    host: getEnv("SCRIBE_HOST", DefaultHost),
    port: parsePort(getEnv("SCRIBE_PORT", $DefaultPort), "SCRIBE_PORT"),
    maxConcurrency: parsePositiveInt(
      getEnv("SCRIBE_MAX_CONCURRENCY", $DefaultMaxConcurrency),
      "SCRIBE_MAX_CONCURRENCY"
    )
  )

  var parser = initOptParser(commandLineParams())
  for kind, key, value in parser.getopt():
    case kind
    of cmdEnd:
      discard
    of cmdArgument:
      raise newException(ValueError, "unexpected argument: " & key)
    of cmdShortOption, cmdLongOption:
      case key
      of "h", "help":
        echo usageText()
        quit(0)
      of "host":
        if value.len == 0:
          raise newException(ValueError, "--host requires a value")
        result.host = value
      of "port":
        result.port = parsePort(value, "--port")
      of "max-concurrency":
        result.maxConcurrency = parsePositiveInt(value, "--max-concurrency")
      else:
        raise newException(ValueError, "unknown option: " & key)

proc isWebSocketUpgrade(request: Request): bool =
  request.headers["Sec-WebSocket-Key"].len > 0

proc respondText(request: Request, code: int, text: string) =
  var headers: HttpHeaders
  headers["Content-Type"] = "text/plain; charset=utf-8"
  headers["Cache-Control"] = "no-cache"
  request.respond(code, headers, text)

proc tryStartJob(): bool =
  withLock serviceState.lock:
    if serviceState.activeJobs >= serviceState.maxConcurrency:
      return false
    inc serviceState.activeJobs
    result = true

proc finishJob() =
  withLock serviceState.lock:
    if serviceState.activeJobs > 0:
      dec serviceState.activeJobs

proc errorCode(error: ref Exception): string =
  if error of ProtocolError:
    "invalid_request"
  elif error of ReporterUriError:
    "replay_fetch_failed"
  else:
    "report_failed"

proc runReportJob(websocket: WebSocket, request: ReportRequest) =
  try:
    let
      replayBytes = request.replayUri.readReplayUri()
      report = replayBytes.decodeReplayBytes()
      timeline = report.extractTimeline()
      rows = timeline.eventLogRows()
    let
      metadata =
        case request.format
        of rfCsv:
          csvMetadataMessage(
            request.requestId,
            rows.len,
            timeline.hashValidated,
            timeline.warnings.len
          )
        of rfParquet:
          parquetMetadataMessage(
            request.requestId,
            rows.len,
            timeline.hashValidated,
            timeline.warnings.len
          )
      payload =
        case request.format
        of rfCsv:
          rows.renderEventLogCsv()
        of rfParquet:
          rows.renderEventLogParquet()
    websocket.send(metadata, TextMessage)
    websocket.send(payload, BinaryMessage)
    websocket.send(doneMessage(request.requestId), TextMessage)
  except CatchableError as e:
    websocket.send(errorMessage(request.requestId, e.errorCode(), e.msg), TextMessage)
  finally:
    finishJob()

proc httpHandler(request: Request) {.gcsafe.} =
  if request.path == HealthPath and request.httpMethod == "GET":
    request.respondText(200, "healthy\n")
  elif request.path == ReportPath and request.httpMethod == "GET" and
      request.isWebSocketUpgrade():
    discard request.upgradeToWebSocket()
  elif request.path == ReportPath:
    request.respondText(426, "websocket upgrade required\n")
  else:
    request.respondText(404, "not found\n")

proc websocketHandler(
  websocket: WebSocket,
  event: WebSocketEvent,
  message: Message
) {.gcsafe.} =
  case event
  of OpenEvent, CloseEvent, ErrorEvent:
    discard
  of MessageEvent:
    if message.kind == Ping:
      websocket.send(message.data, Pong)
      return
    if message.kind != TextMessage:
      websocket.send(
        errorMessage("", "invalid_message", "report requests must be JSON text frames"),
        TextMessage
      )
      return

    let request =
      try:
        parseReportRequest(message.data)
      except CatchableError as e:
        let requestId = message.data.requestIdFromMessage()
        websocket.send(errorMessage(requestId, e.errorCode(), e.msg), TextMessage)
        return

    if not tryStartJob():
      websocket.send(
        errorMessage(
          request.requestId,
          "busy",
          "reporter concurrency limit reached"
        ),
        TextMessage
      )
      return

    websocket.send(acceptedMessage(request.requestId), TextMessage)
    {.cast(gcsafe).}:
      runReportJob(websocket, request)

proc runService*(config = parseServiceConfig()) =
  initLock(serviceState.lock)
  serviceState.activeJobs = 0
  serviceState.maxConcurrency = config.maxConcurrency

  let server = newServer(
    httpHandler,
    websocketHandler,
    workerThreads = config.maxConcurrency + 1
  )
  echo "scribe reporter service listening on ", config.host, ":", config.port,
    " ", ReportPath, " max_concurrency=", config.maxConcurrency
  server.serve(Port(config.port), config.host)

when isMainModule:
  try:
    runService()
  except CatchableError as e:
    quit("scribe reporter service error: " & e.msg, 1)
