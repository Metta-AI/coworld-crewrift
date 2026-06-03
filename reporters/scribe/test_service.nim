import
  std/[json, strutils, unittest],
  crewrift/sim,
  scribe/[event_log, events, identity, parquet, protocol, uri_io]

proc readLe32(bytes: string, offset: int): int =
  for i in 0 ..< 4:
    result = result or (int(bytes[offset + i].uint8) shl (8 * i))

suite "scribe reporter service helpers":
  test "parses report requests for file and https replay URIs":
    let fileRequest = parseReportRequest($(%*{
      "type": RequestType,
      "request_id": "req-1",
      "replay_uri": "file:///tmp/replay.bitreplay",
      "format": "csv"
    }))
    check fileRequest.requestId == "req-1"
    check fileRequest.replayUri == "file:///tmp/replay.bitreplay"
    check fileRequest.format == rfCsv

    let httpsRequest = parseReportRequest($(%*{
      "type": RequestType,
      "request_id": "req-2",
      "replay_uri": "https://example.test/replay.bitreplay"
    }))
    check httpsRequest.requestId == "req-2"
    check httpsRequest.replayUri.startsWith("https://")
    check httpsRequest.format == rfParquet

    let parquetRequest = parseReportRequest($(%*{
      "type": RequestType,
      "request_id": "req-3",
      "replay_uri": "file:///tmp/replay.bitreplay",
      "format": "parquet"
    }))
    check parquetRequest.format == rfParquet

  test "rejects unsupported replay URI schemes":
    check not supportedReplayUri("http://example.test/replay.bitreplay")
    expect ProtocolError:
      discard parseReportRequest($(%*{
        "type": RequestType,
        "request_id": "req-1",
        "replay_uri": "http://example.test/replay.bitreplay"
      }))

  test "rejects unsupported report formats":
    expect ProtocolError:
      discard parseReportRequest($(%*{
        "type": RequestType,
        "request_id": "req-1",
        "replay_uri": "file:///tmp/replay.bitreplay",
        "format": "json"
      }))

  test "builds CSV event log with canonical columns and escaped JSON values":
    let speaker = PlayerRef(joinOrder: 2, slot: 2)
    let timeline = EpisodeTimeline(
      identities: @[
        PlayerIdentity(
          slot: 2,
          name: "yellow",
          address: "yellow",
          color: 3'u8,
          role: Crewmate,
          joinOrder: 2
        )
      ],
      events: @[
        GameEvent(
          tick: 4,
          kind: gekChatMessage,
          speaker: speaker,
          text: "found orange, maybe \"red\""
        )
      ],
      finalTick: 4,
      hashValidated: true
    )

    let rows = timeline.eventLogRows()
    check rows.len == 1
    check rows[0].ts == 4
    check rows[0].player == 2
    check rows[0].key == "chat.message"
    check parseJson(rows[0].value)["text"].getStr() == "found orange, maybe \"red\""

    let csv = rows.renderEventLogCsv()
    check csv.startsWith("ts,player,key,value\n")
    check "\"{\"\"text\"\":\"\"found orange, maybe \\\"\"red\\\"\"\"\"}\"" in csv

  test "builds Parquet event log with canonical columns":
    let rows = @[
      EventLogRow(
        ts: 4,
        player: 2,
        key: "chat.message",
        value: "{\"text\":\"found orange\"}"
      )
    ]

    let payload = rows.renderEventLogParquet()
    check payload.len > 32
    check payload[0 .. 3] == "PAR1"
    check payload[^4 .. ^1] == "PAR1"
    let footerLen = payload.readLe32(payload.len - 8)
    check footerLen > 0
    check footerLen < payload.len - 8
    check "crewrift-scribe.parquet.v1" in payload
    check "chat.message" in payload
    check "{\"text\":\"found orange\"}" in payload

    let emptyPayload = renderEventLogParquet([])
    check emptyPayload[0 .. 3] == "PAR1"
    check emptyPayload[^4 .. ^1] == "PAR1"
    check emptyPayload.readLe32(emptyPayload.len - 8) > 0

  test "builds response metadata":
    let csvMetadata = parseJson(csvMetadataMessage(
      "req-1",
      rowCount = 7,
      hashValidated = true,
      warningCount = 0
    ))
    check csvMetadata["type"].getStr() == CsvMetadataType
    check csvMetadata["request_id"].getStr() == "req-1"
    check csvMetadata["content_type"].getStr() == CsvContentType
    check csvMetadata["filename"].getStr() == CsvFilename
    check csvMetadata["columns"].elems.len == 4
    check csvMetadata["row_count"].getInt() == 7
    check csvMetadata["hash_validated"].getBool()

    let parquetMetadata = parseJson(parquetMetadataMessage(
      "req-2",
      rowCount = 8,
      hashValidated = false,
      warningCount = 1
    ))
    check parquetMetadata["type"].getStr() == ParquetMetadataType
    check parquetMetadata["request_id"].getStr() == "req-2"
    check parquetMetadata["content_type"].getStr() == ParquetContentType
    check parquetMetadata["filename"].getStr() == ParquetFilename
    check parquetMetadata["schema"].getStr() == ParquetEventLogSchema
    check parquetMetadata["row_count"].getInt() == 8
    check not parquetMetadata["hash_validated"].getBool()
