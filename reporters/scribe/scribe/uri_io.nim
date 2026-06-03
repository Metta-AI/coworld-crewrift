import
  std/strutils,
  bitworld/runtime

const EpisodeBundleUriField* = "episode_bundle_uri"

type
  ReporterUriError* = object of CatchableError

proc supportedEpisodeBundleUri*(uri: string): bool =
  uri.startsWith("file://") or uri.startsWith("https://")

proc readEpisodeBundleUri*(uri: string): string =
  ## Reads an episode bundle from one service request URI.
  if uri.len == 0:
    raise newException(ReporterUriError, EpisodeBundleUriField & " is required")
  if not uri.supportedEpisodeBundleUri():
    raise newException(
      ReporterUriError,
      "unsupported episode bundle URI scheme; expected file:// or https://"
    )
  readCogameUri(uri, EpisodeBundleUriField)
