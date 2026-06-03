import
  std/[json, strutils, unittest],
  crewrift/sim,
  scribe/[event_log, events, identity, protocol, uri_io]

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

    let httpsRequest = parseReportRequest($(%*{
      "type": RequestType,
      "request_id": "req-2",
      "replay_uri": "https://example.test/replay.bitreplay"
    }))
    check httpsRequest.requestId == "req-2"
    check httpsRequest.replayUri.startsWith("https://")

  test "rejects unsupported replay URI schemes":
    check not supportedReplayUri("http://example.test/replay.bitreplay")
    expect ProtocolError:
      discard parseReportRequest($(%*{
        "type": RequestType,
        "request_id": "req-1",
        "replay_uri": "http://example.test/replay.bitreplay"
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

  test "builds response metadata":
    let metadata = parseJson(csvMetadataMessage(
      "req-1",
      rowCount = 7,
      hashValidated = true,
      warningCount = 0
    ))
    check metadata["type"].getStr() == CsvMetadataType
    check metadata["request_id"].getStr() == "req-1"
    check metadata["content_type"].getStr() == CsvContentType
    check metadata["columns"].elems.len == 4
    check metadata["row_count"].getInt() == 7
    check metadata["hash_validated"].getBool()
