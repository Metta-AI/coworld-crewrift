import
  std/[json, strutils]

type
  ProtocolError* = object of CatchableError

  Target* = object
    kind*: string
    id*: string

  ReportRequest* = object
    requestId*: string
    target*: Target
    reason*: string
    context*: JsonNode

  Drain* = object
    reason*: string

  PlatformMessageKind* = enum
    PmkReportRequest
    PmkDrain

  PlatformMessage* = object
    case kind*: PlatformMessageKind
    of PmkReportRequest:
      request*: ReportRequest
    of PmkDrain:
      drain*: Drain

const
  ReportAcceptedType* = "report_accepted"
  ReportOutputType* = "report_output"
  ReportFailedType* = "report_failed"
  ReportRequestType* = "report_request"
  DrainType* = "drain"

proc protocolFail(message: string) =
  ## Raises one reporter protocol validation error.
  raise newException(ProtocolError, message)

proc requireField(node: JsonNode, field: string): JsonNode =
  ## Returns one required object field.
  if node.kind != JObject:
    protocolFail("message must be a JSON object")
  if not node.hasKey(field):
    protocolFail("missing required field: " & field)
  node[field]

proc requireString(node: JsonNode, field: string): string =
  ## Returns one required non-empty string field.
  let value = node.requireField(field)
  if value.kind != JString:
    protocolFail(field & " must be a string")
  result = value.getStr()
  if result.len == 0:
    protocolFail(field & " must not be empty")

proc requireObject(node: JsonNode, field: string): JsonNode =
  ## Returns one required object field.
  result = node.requireField(field)
  if result.kind != JObject:
    protocolFail(field & " must be an object")

proc parseTarget(node: JsonNode): Target =
  ## Parses and validates one reporter target object.
  if node.kind != JObject:
    protocolFail("target must be an object")
  result.kind = node.requireString("kind")
  result.id = node.requireString("id")

proc targetJson*(target: Target): JsonNode =
  ## Builds the protocol JSON object for one target.
  result = newJObject()
  result["kind"] = %target.kind
  result["id"] = %target.id

proc parseReportRequest(node: JsonNode): ReportRequest =
  ## Parses and validates one report_request message.
  result.requestId = node.requireString("request_id")
  result.target = node.requireField("target").parseTarget()
  result.reason = node.requireString("reason")
  result.context = node.requireObject("context")

proc parseDrain(node: JsonNode): Drain =
  ## Parses and validates one drain message.
  result.reason = node.requireString("reason")

proc parsePlatformMessage*(text: string): PlatformMessage =
  ## Parses one platform-to-reporter WebSocket message.
  var node: JsonNode
  try:
    node = parseJson(text)
  except CatchableError as e:
    protocolFail("invalid JSON: " & e.msg)

  let messageType = node.requireString("type")
  case messageType
  of ReportRequestType:
    result = PlatformMessage(kind: PmkReportRequest)
    result.request = node.parseReportRequest()
  of DrainType:
    result = PlatformMessage(kind: PmkDrain)
    result.drain = node.parseDrain()
  else:
    protocolFail("unknown message type: " & messageType)

proc replayUri*(request: ReportRequest): string =
  ## Returns context.replay_uri when present and string-typed.
  if request.context.kind != JObject or not request.context.hasKey("replay_uri"):
    return ""
  let value = request.context["replay_uri"]
  if value.kind != JString:
    protocolFail("context.replay_uri must be a string")
  value.getStr()

proc buildReportAccepted*(requestId: string): JsonNode =
  ## Builds a report_accepted protocol message.
  result = newJObject()
  result["type"] = %ReportAcceptedType
  result["request_id"] = %requestId

proc buildReportOutput*(
  requestId: string,
  target: Target,
  payload: JsonNode
): JsonNode =
  ## Builds a JSON report_output protocol message.
  result = newJObject()
  result["type"] = %ReportOutputType
  result["request_id"] = %requestId
  result["target"] = target.targetJson()
  result["mime"] = %"application/json"
  result["encoding"] = %"json"
  result["payload"] = payload

proc buildReportFailed*(
  requestId: string,
  target: Target,
  error: string
): JsonNode =
  ## Builds a report_failed protocol message.
  result = newJObject()
  result["type"] = %ReportFailedType
  result["request_id"] = %requestId
  result["target"] = target.targetJson()
  result["error"] = %error.strip()
