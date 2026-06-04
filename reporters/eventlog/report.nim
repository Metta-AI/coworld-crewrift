import
  std/[json, strutils],
  zippy,
  crewrift/replays,
  ../../tools/expand_replay

type
  ReportError* = object of CatchableError

const
  ReplayMagic = "CREWRIFT"
  EventLogSchema* = """
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "array",
  "items": {
    "type": "object",
    "additionalProperties": false,
    "required": ["ts", "player", "key", "value"],
    "properties": {
      "ts": {"type": "integer"},
      "player": {"type": "integer"},
      "key": {
        "type": "string",
        "enum": [
          "player_joined",
          "entered_room",
          "left_room",
          "phase",
          "vote_called_body",
          "vote_called_button",
          "kill",
          "body",
          "died",
          "revived",
          "started_task",
          "completed_task",
          "vote_cast",
          "chat",
          "score"
        ]
      },
      "value": {"type": "object"}
    }
  }
}
"""

proc reportFail(message: string) =
  ## Raises one reporter extraction error.
  raise newException(ReportError, message)

proc replayPayloadBytes(bytes: string): string =
  ## Returns uncompressed Crewrift replay bytes.
  if bytes.startsWith(ReplayMagic):
    return bytes

  try:
    result = uncompress(bytes)
  except ZippyError as e:
    reportFail(
      "replay bytes are neither raw CREWRIFT nor zlib/gzip-compressed: " &
        e.msg
    )

  if not result.startsWith(ReplayMagic):
    reportFail("decompressed replay magic does not match CREWRIFT")

proc parseReplayPayload(bytes: string): ReplayData =
  ## Parses one replay payload into replay data.
  try:
    result = parseReplayBytes(bytes.replayPayloadBytes())
  except ReplayError as e:
    reportFail("replay decode failed: " & e.msg)

proc eventRows(timeline: ReplayTimeline): JsonNode =
  ## Converts one structured replay timeline into event-log rows.
  result = newJArray()
  for event in timeline.events:
    result.add(event.jsonRow())

proc replayBytesToPayload*(bytes: string): JsonNode =
  ## Builds the reporter JSON payload for one replay byte buffer.
  let data = bytes.parseReplayPayload()
  let timeline = expandReplayTimeline(data)
  if timeline.hashFailed:
    reportFail("replay hash validation failed at tick " & $timeline.failTick)
  timeline.eventRows()

proc eventLogSchemaJson*(): JsonNode =
  ## Parses the event-log JSON schema constant.
  parseJson(EventLogSchema)
