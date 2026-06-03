import
  std/os,
  scribe/[report, timeline]

## Scribe: the Crewrift episode reporter.
##
## Provisional entry point. The real Coworld reporter contract (per
## `packages/coworld/src/coworld/docs/roles/REPORTER.md`) hands the reporter an
## episode bundle through `COGAME_EPISODE_BUNDLE_URI` and expects a report zip
## at `COGAME_REPORT_URI`. That wiring is intentionally not built yet — see
## `reporters/README.md`. For now this takes a bundle zip path on the command
## line, decodes its replay, re-simulates the episode, and prints the extracted
## tick-aligned timeline so the core can be exercised end to end.

when isMainModule:
  if paramCount() < 1:
    quit("usage: scribe <episode-bundle.zip>", 1)
  let episode = decodeEpisodeFile(paramStr(1))
  echo episode.extractTimeline().renderTimeline()
