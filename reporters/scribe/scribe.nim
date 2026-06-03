import
  std/os,
  scribe/[report, timeline]

## Scribe: the Crewrift episode reporter.
##
## Provisional entry point. The real Coworld reporter contract (per
## `packages/coworld/src/coworld/docs/roles/REPORTER.md`) expects canonical
## report artifacts. That wiring is intentionally not built yet — see
## `reporters/README.md`. For now this takes a replay path on the command line,
## decodes it, re-simulates the episode, and prints the extracted tick-aligned
## timeline so the core can be exercised end to end.

when isMainModule:
  if paramCount() < 1:
    quit("usage: scribe <replay.bitreplay>", 1)
  let episode = decodeReplayFile(paramStr(1))
  echo episode.extractTimeline().renderTimeline()
