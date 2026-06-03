import
  std/strutils,
  bitworld/runtime

const ReplayUriField* = "replay_uri"

type
  ReporterUriError* = object of CatchableError

proc supportedReplayUri*(uri: string): bool =
  uri.startsWith("file://") or uri.startsWith("https://")

proc readReplayUri*(uri: string): string =
  ## Reads a replay from one service request URI.
  if uri.len == 0:
    raise newException(ReporterUriError, ReplayUriField & " is required")
  if not uri.supportedReplayUri():
    raise newException(
      ReporterUriError,
      "unsupported replay URI scheme; expected file:// or https://"
    )
  readCogameUri(uri, ReplayUriField)
