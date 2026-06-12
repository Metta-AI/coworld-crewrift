import
  std/[json, os, unittest],
  ../tools/expand_replay,
  crewrift/replays

const
  GameDir = currentSourcePath.parentDir.parentDir
  NotsusReplayPath = GameDir / "tests" / "replays" / "notsus.bitreplay"

proc hasKey(rows: openArray[JsonNode], key: string): bool =
  for row in rows:
    if row["key"].getStr() == key:
      return true

suite "expand replay event trace":
  test "emits standard metadata and state rows":
    let
      data = loadReplay(NotsusReplayPath)
      timeline = expandReplayTimeline(data, snapshotEvery = 64)

    check timeline.traceRows.len > 0
    check timeline.traceRows.hasKey("episode_metadata")
    check timeline.traceRows.hasKey("map_geometry")
    check timeline.traceRows.hasKey("player_manifest")
    check timeline.traceRows.hasKey("player_state")
    check timeline.traceRows[0]["ts"].getInt() == 0
    check timeline.traceRows[0]["player"].getInt() == -1
    check timeline.traceRows[0]["value"]["schema_version"].getStr() ==
      "crewrift-events/v1"
