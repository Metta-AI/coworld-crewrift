import
  std/[json, streams, tables],
  zippy/ziparchives,
  jsony

## Reads a Coworld episode bundle: the single `.zip` that packages one
## episode's artifacts (results, replay, logs) plus a root `manifest.json`
## describing what is present. See the Coworld schema docs at
## `packages/coworld/src/coworld/docs/artifacts/EPISODE_BUNDLE.md`.
##
## The whole bundle is read into memory. Bundles are small (a manifest, one
## replay, a few logs) and the eventual reporter interface may hand us raw
## bytes rather than a path, so an in-memory model keeps both cases simple.

type
  BundleError* = object of CatchableError

  EpisodeBundle* = object
    ## One opened episode bundle, with every file entry held in memory by name.
    entries*: OrderedTable[string, string]

  BundleManifest* = object
    ## The bundle's root `manifest.json`.
    ereqId*: string            ## Episode request id, e.g. "ereq_...".
    status*: string            ## "success" or "failed".
    included*: seq[string]     ## Artifact tokens actually delivered.
    files*: JsonNode           ## token -> entry name(s); value shape varies.

const ManifestEntry* = "manifest.json"

proc openBundleBytes*(bytes: string): EpisodeBundle =
  ## Reads every file entry of a bundle zip held in memory.
  let archive = ZipArchive()
  try:
    archive.open(newStringStream(bytes))
  except CatchableError as e:
    raise newException(BundleError, "Could not open episode bundle zip: " & e.msg)
  for name, entry in archive.contents:
    if entry.kind == ekFile:
      result.entries[name] = entry.contents

proc openBundleFile*(path: string): EpisodeBundle =
  ## Reads a bundle zip from a local file.
  try:
    openBundleBytes(readFile(path))
  except IOError as e:
    raise newException(BundleError, "Could not read episode bundle file: " & e.msg)

proc hasEntry*(bundle: EpisodeBundle, name: string): bool =
  ## Returns true when the bundle contains a file entry with this name.
  name in bundle.entries

proc readEntry*(bundle: EpisodeBundle, name: string): string =
  ## Returns one entry's raw bytes, or raises when it is missing.
  if name notin bundle.entries:
    raise newException(BundleError, "Episode bundle has no entry named " & name)
  bundle.entries[name]

proc readManifest*(bundle: EpisodeBundle): BundleManifest =
  ## Parses the bundle's root `manifest.json`.
  if not bundle.hasEntry(ManifestEntry):
    raise newException(BundleError, "Episode bundle is missing " & ManifestEntry)
  var node: JsonNode
  try:
    node = fromJson(bundle.readEntry(ManifestEntry))
  except jsony.JsonError as e:
    raise newException(BundleError, "Could not parse bundle manifest: " & e.msg)
  if node.kind != JObject:
    raise newException(BundleError, "Bundle manifest must be a JSON object.")
  if node.hasKey("ereq_id") and node["ereq_id"].kind == JString:
    result.ereqId = node["ereq_id"].getStr()
  if node.hasKey("status") and node["status"].kind == JString:
    result.status = node["status"].getStr()
  if node.hasKey("include") and node["include"].kind == JArray:
    for item in node["include"]:
      if item.kind == JString:
        result.included.add(item.getStr())
  result.files =
    if node.hasKey("files") and node["files"].kind == JObject:
      node["files"]
    else:
      newJObject()

proc hasToken*(manifest: BundleManifest, token: string): bool =
  ## Returns true when the manifest lists an artifact under this token.
  not manifest.files.isNil and manifest.files.hasKey(token)

proc tokenEntryName*(manifest: BundleManifest, token: string): string =
  ## Returns the zip entry name for a single-file artifact token such as
  ## `results`, `replay`, or `error_info`. Consumers should resolve entry
  ## names through the manifest rather than hard-coding paths; the layout is a
  ## convention that may evolve. Nested tokens like `game_logs` map to an
  ## object of several entries and are not resolved here.
  if not manifest.hasToken(token):
    raise newException(BundleError, "Bundle manifest has no '" & token & "' entry.")
  let value = manifest.files[token]
  if value.kind != JString:
    raise newException(BundleError,
      "Bundle '" & token & "' entry is not a single file.")
  value.getStr()
