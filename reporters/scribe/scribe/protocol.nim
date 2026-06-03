import
  std/json,
  event_log,
  uri_io

const
  RequestType* = "report.generate"
  AcceptedType* = "report.accepted"
  CsvMetadataType* = "report.csv"
  ParquetMetadataType* = "report.parquet"
  DoneType* = "report.done"
  ErrorType* = "report.error"
  CsvContentType* = "text/csv"
  ParquetContentType* = "application/vnd.apache.parquet"
  CsvFilename* = "events.csv"
  ParquetFilename* = "events.parquet"
  CsvEventLogSchema* = "coworld.event_log.csv.v1"
  ParquetEventLogSchema* = "coworld.event_log.parquet.v1"

type
  ProtocolError* = object of CatchableError

  ReportFormat* = enum
    rfCsv = "csv"
    rfParquet = "parquet"

  ReportRequest* = object
    requestId*: string
    replayUri*: string
    format*: ReportFormat

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
  result.format = rfParquet
  result.replayUri = node.stringField(ReplayUriField)
  if not result.replayUri.supportedReplayUri():
    raise newException(
      ProtocolError,
      ReplayUriField & " must use file:// or https://"
    )
  if node.hasKey("format"):
    if node["format"].kind != JString:
      raise newException(ProtocolError, "format must be a string when provided")
    case node["format"].getStr()
    of "csv":
      result.format = rfCsv
    of "parquet":
      result.format = rfParquet
    else:
      raise newException(ProtocolError, "format must be parquet or csv when provided")

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
    "schema": CsvEventLogSchema,
    "columns": EventLogColumns,
    "row_count": rowCount,
    "hash_validated": hashValidated,
    "warning_count": warningCount
  })

proc parquetMetadataMessage*(
  requestId: string,
  rowCount: int,
  hashValidated: bool,
  warningCount: int
): string =
  $(%*{
    "type": ParquetMetadataType,
    "request_id": requestId,
    "content_type": ParquetContentType,
    "filename": ParquetFilename,
    "schema": ParquetEventLogSchema,
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
