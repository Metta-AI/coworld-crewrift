import
  ../../../src/crewrift/[replays, sim],
  bundle

## The core of the Crewrift reporter: turn one episode bundle into the decoded
## episode state a report is built from.
##
## Step one (this module) is purely "unbundle and decode": open the bundle,
## read its manifest, locate the replay, and parse the replay bytes into
## memory using Crewrift's own replay codec (`src/crewrift/replays.nim`). The
## replay format is game-owned, so reusing the game's codec is the only correct
## way to decode it; reimplementing the byte format here would silently drift
## from the game.
##
## What a report actually contains — narrative, highlights, statistics, event
## logs — is deliberately left for later. This module gets us to a faithfully
## decoded `ReplayData` (plus the game config the episode ran with) that those
## later stages can walk.

const
  ReplayToken* = "replay"
  ResultsToken* = "results"

type
  EpisodeReport* = object
    ## The decoded core of one episode, ready for downstream reporting.
    manifest*: BundleManifest
    config*: GameConfig    ## Game config the episode ran with, from the replay.
    replay*: ReplayData    ## Decoded replay: joins, leaves, inputs, hashes.

proc decodeEpisode*(bundle: EpisodeBundle): EpisodeReport =
  ## Unbundles one episode and decodes its replay into memory.
  result.manifest = bundle.readManifest()

  if result.manifest.status == "failed":
    raise newException(BundleError,
      "Episode bundle reports a failed episode; there is no replay to decode.")

  if not result.manifest.hasToken(ReplayToken):
    raise newException(BundleError,
      "Episode bundle has no replay; nothing to decode.")

  let
    replayEntry = result.manifest.tokenEntryName(ReplayToken)
    replayBytes = bundle.readEntry(replayEntry)

  # parseReplayBytes validates the Crewrift magic and version, and transparently
  # decompresses hosted replays that arrive zlib-compressed.
  result.replay = parseReplayBytes(replayBytes)

  # The replay header carries the exact config JSON the episode ran with.
  result.config = defaultGameConfig()
  result.config.update(result.replay.configJson)

proc decodeEpisodeFile*(path: string): EpisodeReport =
  ## Convenience: decode an episode from a bundle zip on disk.
  decodeEpisode(openBundleFile(path))

proc decodeEpisodeBytes*(bytes: string): EpisodeReport =
  ## Convenience: decode an episode from bundle zip bytes in memory.
  decodeEpisode(openBundleBytes(bytes))

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
  let ereq = if report.manifest.ereqId.len > 0: report.manifest.ereqId else: "(unknown)"
  let status = if report.manifest.status.len > 0: report.manifest.status else: "(unset)"
  "episode " & ereq & " [" & status & "]\n" &
    "  game:    " & r.gameName & " v" & r.gameVersion & "\n" &
    "  players: " & $r.joins.len & " joined, " & $r.leaves.len & " left\n" &
    "  inputs:  " & $r.inputs.len & " recorded\n" &
    "  ticks:   " & $report.finalTick() & " (final recorded tick)"
