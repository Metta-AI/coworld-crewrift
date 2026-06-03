import
  ../../../src/crewrift/[replays, sim]

## The core of the Crewrift reporter: turn one replay into the decoded episode
## state a report is built from.
##
## Step one (this module) is purely "decode": parse the replay bytes into memory
## using Crewrift's own replay codec (`src/crewrift/replays.nim`). The replay
## format is game-owned, so reusing the game's codec is the only correct way to
## decode it; reimplementing the byte format here would silently drift from the
## game.
##
## What a report actually contains — narrative, highlights, statistics, event
## logs — is deliberately left for later. This module gets us to a faithfully
## decoded `ReplayData` (plus the game config the episode ran with) that those
## later stages can walk.

type
  EpisodeReport* = object
    ## The decoded core of one episode, ready for downstream reporting.
    config*: GameConfig    ## Game config the episode ran with, from the replay.
    replay*: ReplayData    ## Decoded replay: joins, leaves, inputs, hashes.

proc decodeReplayBytes*(replayBytes: string): EpisodeReport =
  ## Decodes one Crewrift replay buffer into memory.
  # parseReplayBytes validates the Crewrift magic and version, and transparently
  # decompresses hosted replays that arrive zlib-compressed.
  result.replay = parseReplayBytes(replayBytes)

  # The replay header carries the exact config JSON the episode ran with.
  result.config = defaultGameConfig()
  result.config.update(result.replay.configJson)

proc decodeReplayFile*(path: string): EpisodeReport =
  ## Convenience: decode a replay from a local file.
  decodeReplayBytes(readFile(path))

proc finalTick*(report: EpisodeReport): int =
  ## Returns the last recorded tick in the replay, or 0 when none.
  if report.replay.hashes.len == 0:
    0
  else:
    int(report.replay.hashes[^1].tick)

proc summary*(report: EpisodeReport): string =
  ## Builds a short human-readable overview of a decoded episode. This is a
  ## placeholder stand-in for a real report while the report contents are
  ## still being designed.
  let r = report.replay
  "replay\n" &
    "  game:    " & r.gameName & " v" & r.gameVersion & "\n" &
    "  players: " & $r.joins.len & " joined, " & $r.leaves.len & " left\n" &
    "  inputs:  " & $r.inputs.len & " recorded\n" &
    "  ticks:   " & $report.finalTick() & " (final recorded tick)"
