import
  std/json,
  event_log,
  uri_io

const
  RequestType* = "report.generate"
  AcceptedType* = "report.accepted"
  CsvMetadataType* = "report.csv"
  DoneType* = "report.done"
  ErrorType* = "report.error"
  CsvContentType* = "text/csv"
  CsvFilename* = "events.csv"
  EventLogSchema* = "coworld.event_log.csv.v1"

type
  ProtocolError* = object of CatchableError

  ReportRequest* = object
    requestId*: string
    episodeBundleUri*: string

proc stringField(node: JsonNode, name: string): string =
  if not node.hasKey(name) or node[name].kind != JString:
    raise newException(ProtocolError, name & " must be a string")
  node[name].getStr()

proc requestIdFromMessage*(message: string): string =
  try:
    let node = parseJson(message)
    if node.kind == JObject and node.hasKey("request_id") and
        node["request_id"].kind == JString:
      return node["request_id"].getStr()
  except JsonParsingError:
    discard
  ""

proc parseReportRequest*(message: string): ReportRequest =
  let node =
    try:
      parseJson(message)
    except JsonParsingError as e:
      raise newException(ProtocolError, "invalid JSON request: " & e.msg)
  if node.kind != JObject:
    raise newException(ProtocolError, "request must be a JSON object")
  let messageType = node.stringField("type")
  if messageType != RequestType:
    raise newException(
      ProtocolError,
      "unsupported request type " & messageType & "; expected " & RequestType
    )
  result.requestId = node.stringField("request_id")
  if result.requestId.len == 0:
    raise newException(ProtocolError, "request_id must not be empty")
  result.episodeBundleUri = node.stringField(EpisodeBundleUriField)
  if not result.episodeBundleUri.supportedEpisodeBundleUri():
    raise newException(
      ProtocolError,
      EpisodeBundleUriField & " must use file:// or https://"
    )
  if node.hasKey("format"):
    if node["format"].kind != JString or node["format"].getStr() != "csv":
      raise newException(ProtocolError, "format must be csv when provided")

proc acceptedMessage*(requestId: string): string =
  $(%*{
    "type": AcceptedType,
    "request_id": requestId
  })

proc csvMetadataMessage*(
  requestId: string,
  rowCount: int,
  hashValidated: bool,
  warningCount: int
): string =
  $(%*{
    "type": CsvMetadataType,
    "request_id": requestId,
    "content_type": CsvContentType,
    "filename": CsvFilename,
    "schema": EventLogSchema,
    "columns": EventLogColumns,
    "row_count": rowCount,
    "hash_validated": hashValidated,
    "warning_count": warningCount
  })

proc doneMessage*(requestId: string): string =
  $(%*{
    "type": DoneType,
    "request_id": requestId
  })

proc errorMessage*(requestId, code, message: string): string =
  $(%*{
    "type": ErrorType,
    "request_id": requestId,
    "code": code,
    "message": message
  })
