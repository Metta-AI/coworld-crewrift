import
  std/[algorithm, json, os, osproc, sets, streams, strutils,
    tables, times],
  chrono,
  common,
  replay_extractor

const
  SoftmaxObservatoryUrl = "https://softmax.com/observatory/v2"
  DefaultLeagueId = "league_a12f5172-0907-4d04-8bcb-ca02f5360e3a"
  DefaultS3Bucket = "crewrift-prime-tournament"
  DefaultS3Prefix = "notsus"
  DefaultAwsProfile = "sandbox-andre"
  DefaultRoundLimit = 1000
  DefaultUpdateRoundLimit = 50
  MaxRoundsPerPage = 200
  MaxEpisodesPerPage = 1000
  CoworldMaxAttempts = 4
  CoworldRetryBaseMs = 2000
  FileWriteMaxAttempts = 4
  FileWriteRetryBaseMs = 150
  AutoFailureRetrySeconds = 60.0
  MetaFileName = "tournament.json"
  RoundCacheDirName = "rounds"
  CacheVersion = 5
  ScoreEpsilon = 0.005
  MinRoundNumber = 0
  CurrentRoundCount = 24
  FreshGameSkipLimit = 100
  StablePolicyGames = 50
  HeatCellSize = 32
  HeatLeftLabelWidth = 170
  HeatRightLabelWidth = HeatLeftLabelWidth
  HeatTopLabelHeight = 130
  HeatPadding = 12
  WinRatePlotWidth = 738
  WinRateLeftPadding = 80
  WinRateRightPadding = 80
  WinRateTopPadding = 330
  WinRateBottomPadding = 34
  WinRateTickHeight = 9
  WinRateDotRadius = 6
  WinRateBaseStem = 20
  WinRateSlotStep = 28
  WinRateExtraLeftSlots = 10
  WinRateSlotReach = WinRateSlotStep * WinRateExtraLeftSlots
  WinRateMinSlot = -WinRateExtraLeftSlots
  WinRateMaxSlot = WinRatePlotWidth div WinRateSlotStep
  WinRateLevelStep = 28
  WinRateStemDiagonal = 15
  WinRateLabelShiftX = 3
  RoundPlayerSlots = 8
  RoundPlayerLetters = ["A", "B", "C", "D", "E", "F", "G", "H"]
  RoundTimeZone = "America/Los_Angeles"
  RoundTimeZoneData = staticRead("tournament_tzdata.json")
  RoundTimeFormat = "{hour/ap}:{minute/2}{am/pm}"
  RoundTimestampFormats = [
    "{year/4}-{month/2}-{day/2}T{hour/2}:{minute/2}:{second}.{secondFraction}Z",
    "{year/4}-{month/2}-{day/2}T{hour/2}:{minute/2}:{second/2}Z"
  ]
  PreloadFonts = [
    "ETBembo-RomanOSF.otf",
    "ETBembo-DisplayItalic.otf",
    "ETBembo-SemiBoldOSF.otf"
  ]
  Usage = """
Usage:
  nim r players/notsus/tools/tournament.nim -- [options]

Options:
      --league ID          League target. Default: Crewrift Prime.
      --out-dir PATH       Static report root. Default: ./tournament.
      --coworld-dir PATH   Directory for uv run coworld. Default: ../metta.
      --tufte-dir PATH     Tufte assets directory. Default: ../offstream/tufte.
      --round-limit N      Maximum recent rounds to scan. Default: 1000.
      --render-round ROUND Rebuild one cached round page by number, ID, or
                           JSON path. Skips API refresh and S3 sync.
      --update             Use a smaller recent-round scan window.
                           Cached rounds are merged automatically.
      --rebuild            Refresh all cached report rounds in the scan window.
      --auto MINUTES       Rebuild repeatedly, starting every MINUTES minutes.
      --with-replays       Download replay artifacts. Off by default.
      --no-replays         Do not download replay artifacts.
      --no-s3-sync         Do not publish the report to S3.
      --s3-bucket NAME     S3 bucket to sync. Extra objects are preserved.
                           Default: crewrift-prime-tournament.
      --s3-prefix PREFIX   S3 prefix to sync under. Default: notsus.
      --aws-profile NAME   AWS profile to use. Default: sandbox-andre.
      --server URL         Observatory API server passed to coworld.
  -h, --help               Show this help text.
"""

type
  TournamentError = object of CatchableError

  PolicyRef = object
    id: string
    label: string
    playerId: string
    playerName: string
    membershipId: string
    status: string
    substatus: string
    divisionName: string
    isChampion: bool

  PolicyStats = object
    games: int
    scoredGames: int
    wins: int
    losses: int
    ties: int
    totalScore: float

  HeatPolicy = object
    id: string
    label: string
    score: float
    scoredGames: int

  HeatStats = object
    wins: int
    losses: int
    ties: int

  WinRatePoint = object
    id: string
    label: string
    rate: float
    games: int
    wins: int
    losses: int
    ties: int
    slot: int
    level: int

  RoundInfo = object
    id: string
    number: int
    status: string
    divisionName: string
    startedAt: string
    completedAt: string
    entrantIds: seq[string]

  RoundLabelRule = enum
    StripGamePrefix,
    StripVersionToken,
    StripScriptedToken,
    StripBaselineToken

  RoundLabelEntry = object
    shortLabel: string
    fullLabel: string

  Participant = object
    position: int
    policyId: string
    label: string
    playerId: string
    playerName: string

  Score = object
    policyId: string
    score: float

  Game = object
    id: string
    roundId: string
    roundNumber: int
    divisionName: string
    status: string
    createdAt: string
    replayUrl: string
    liveUrl: string
    error: string
    participants: seq[Participant]
    scores: seq[Score]

  GamePageStats = object
    checked: int
    rendered: int
    skipped: int
    replayPages: int
    pendingPages: int
    parseErrors: int
    downloaded: int
    downloadErrors: int
    limited: int

  CachedRound = object
    roundId: string
    roundNumber: int
    status: string
    divisionName: string
    startedAt: string
    completedAt: string
    games: seq[Game]

  StrategyDocs = object
    markdown: string
    headings: HashSet[string]
    strategies: Table[string, string]

  ToolConfig = object
    leagueId: string
    outDir: string
    coworldDir: string
    tufteDir: string
    roundLimit: int
    roundLimitSet: bool
    update: bool
    rebuild: bool
    renderRound: string
    autoMinutes: int
    downloadReplays: bool
    syncS3: bool
    s3Bucket: string
    s3Prefix: string
    awsProfile: string
    server: string

  CommandResult = object
    output: string
    code: int

proc fail(message: string) =
  ## Raises a tournament report error with a clear message.
  raise newException(TournamentError, message)

proc loadRoundTimezones() =
  ## Loads the timezone data needed for round time display.
  loadTzData(RoundTimeZoneData)

proc rootDir(): string =
  ## Returns the Notsus player root for this tool.
  currentSourcePath().parentDir().parentDir()

proc gameDir(): string =
  ## Returns the Crewrift repository root for this player.
  rootDir().parentDir().parentDir()

proc workspaceDir(): string =
  ## Returns the parent workspace directory.
  gameDir().parentDir()

proc defaultConfig(): ToolConfig =
  ## Returns the default tournament report configuration.
  ToolConfig(
    leagueId: DefaultLeagueId,
    outDir: rootDir() / "tournament",
    coworldDir: workspaceDir() / "metta",
    tufteDir: workspaceDir() / "offstream" / "tufte",
    roundLimit: DefaultRoundLimit,
    downloadReplays: false,
    syncS3: true,
    s3Bucket: DefaultS3Bucket,
    s3Prefix: DefaultS3Prefix,
    awsProfile: DefaultAwsProfile
  )

proc optionValue(
  params: openArray[string],
  i: var int,
  key,
  value: string
): string =
  ## Returns an inline option value or consumes the next argument.
  if value.len > 0:
    return value
  inc i
  if i >= params.len:
    fail("Option " & key & " requires a value.")
  result = params[i]
  if result.len == 0:
    fail("Option " & key & " requires a value.")

proc parseIntValue(key, value: string): int =
  ## Parses one integer command-line value.
  try:
    result = value.parseInt()
  except ValueError:
    fail("Option " & key & " must be an integer.")

proc parseConfig(): ToolConfig =
  ## Parses command-line options.
  result = defaultConfig()
  let params = commandLineParams()
  var i = 0
  while i < params.len:
    let param = params[i]
    if param == "--":
      inc i
      continue
    if param == "-h" or param == "--help":
      echo Usage
      quit 0
    if not param.startsWith("--"):
      fail("Unexpected argument: " & param)
    let
      clean = param[2 .. ^1]
      colon = clean.find(':')
      eq = clean.find('=')
      split =
        if colon >= 0:
          colon
        else:
          eq
      name =
        if split >= 0:
          clean[0 ..< split]
        else:
          clean
      inlineValue =
        if split >= 0:
          clean[split + 1 .. ^1]
        else:
          ""
    case name
    of "league":
      result.leagueId = optionValue(params, i, name, inlineValue)
    of "out-dir":
      result.outDir = optionValue(params, i, name, inlineValue)
    of "coworld-dir":
      result.coworldDir = optionValue(params, i, name, inlineValue)
    of "tufte-dir":
      result.tufteDir = optionValue(params, i, name, inlineValue)
    of "round-limit":
      result.roundLimit = parseIntValue(
        name,
        optionValue(params, i, name, inlineValue)
      )
      result.roundLimitSet = true
    of "render-round":
      result.renderRound = optionValue(params, i, name, inlineValue)
    of "update":
      result.update = true
    of "rebuild":
      result.rebuild = true
    of "auto":
      result.autoMinutes = parseIntValue(
        name,
        optionValue(params, i, name, inlineValue)
      )
    of "no-replays":
      result.downloadReplays = false
    of "with-replays":
      result.downloadReplays = true
    of "no-s3-sync":
      result.syncS3 = false
    of "s3-bucket":
      result.s3Bucket = optionValue(params, i, name, inlineValue)
    of "s3-prefix":
      result.s3Prefix = optionValue(params, i, name, inlineValue)
    of "aws-profile":
      result.awsProfile = optionValue(params, i, name, inlineValue)
    of "server":
      result.server = optionValue(params, i, name, inlineValue)
    else:
      fail("Unknown option: --" & name)
    inc i
  if not dirExists(result.coworldDir):
    fail("--coworld-dir does not exist: " & result.coworldDir)
  if not dirExists(result.tufteDir):
    fail("--tufte-dir does not exist: " & result.tufteDir)
  if result.update and not result.roundLimitSet:
    result.roundLimit = DefaultUpdateRoundLimit
  if result.autoMinutes < 0:
    fail("--auto must be zero or greater.")
  if result.autoMinutes > 0 and result.renderRound.len > 0:
    fail("--auto cannot be combined with --render-round.")
  if result.syncS3 and result.s3Bucket.len == 0:
    fail("--s3-bucket requires a non-empty value.")
  if result.syncS3 and result.awsProfile.len == 0:
    fail("--aws-profile requires a non-empty value.")

proc field(node: JsonNode, key: string): JsonNode =
  ## Returns an object field or JSON null when absent.
  if node != nil and node.kind == JObject and node.hasKey(key):
    return node[key]
  newJNull()

proc strField(node: JsonNode, key: string): string =
  ## Returns a string field or an empty string.
  let child = node.field(key)
  if child.kind == JString:
    return child.getStr()

proc intField(node: JsonNode, key: string, fallback = 0): int =
  ## Returns an integer field or a fallback value.
  let child = node.field(key)
  if child.kind == JInt:
    return child.getInt()
  fallback

proc boolField(node: JsonNode, key: string): bool =
  ## Returns a boolean field or false.
  let child = node.field(key)
  if child.kind == JBool:
    return child.getBool()

proc numberField(node: JsonNode, key: string, fallback = 0.0): float =
  ## Returns a numeric field or a fallback value.
  let child = node.field(key)
  case child.kind
  of JInt:
    child.getInt().float
  of JFloat:
    child.getFloat()
  else:
    fallback

proc scoreValue(node: JsonNode): float =
  ## Returns a score value from a JSON number or score object.
  case node.kind
  of JInt:
    node.getInt().float
  of JFloat:
    node.getFloat()
  of JObject:
    node.numberField("score")
  else:
    0.0

proc rowsNode(node: JsonNode): JsonNode =
  ## Returns array rows from either a raw array or an entries wrapper.
  if node.kind == JArray:
    return node
  let entries = node.field("entries")
  if entries.kind == JArray:
    return entries
  newJArray()

proc numberText(value: float, decimals = 2): string =
  ## Formats one floating-point value for tables.
  result = value.formatFloat(ffDecimal, decimals)
  while result.len > 0 and result[^1] == '0':
    result.setLen(result.len - 1)
  if result.len > 0 and result[^1] == '.':
    result.setLen(result.len - 1)
  if result.len == 0 or result == "-0":
    result = "0"

proc shortId(id: string): string =
  ## Returns a compact display ID.
  if id.len <= 13:
    return id
  id[0 .. 12]

proc slugify(value: string): string =
  ## Converts a label into a filesystem-safe slug.
  var previousDash = false
  for ch in value.toLowerAscii():
    if ch in {'a' .. 'z', '0' .. '9'}:
      result.add ch
      previousDash = false
    elif not previousDash:
      result.add '-'
      previousDash = true
  while result.len > 0 and result[^1] == '-':
    result.setLen(result.len - 1)
  if result.len == 0:
    result = "item"

proc commandText(args: openArray[string]): string =
  ## Returns a safely quoted command string.
  args.quoteShellCommand()

proc shortCommandError(message: string): string =
  ## Returns the most useful line from a command failure.
  var fallback = ""
  for line in message.splitLines():
    let clean = line.strip()
    if clean.len == 0:
      continue
    fallback = clean
    for marker in [
      "HTTPStatusError:",
      "RuntimeError:",
      "Client error",
      "Server error",
      "ConnectError",
      "ReadTimeout",
      "Timeout",
      "timed out"
    ]:
      if marker in clean:
        return clean
  fallback

proc htmlEscape(text: string): string =
  ## Escapes text for HTML content and attributes.
  for ch in text:
    case ch
    of '&':
      result.add "&amp;"
    of '<':
      result.add "&lt;"
    of '>':
      result.add "&gt;"
    of '"':
      result.add "&quot;"
    of '\'':
      result.add "&#39;"
    else:
      result.add ch

proc ensureParentDir(path: string) =
  ## Creates the parent directory for one output file.
  let dir = path.parentDir()
  if dir.len > 0 and dir != ".":
    createDir(dir)

proc writeFileRetry(path, content: string) =
  ## Writes a file after recreating its parent directory, with retries.
  for attempt in 1 .. FileWriteMaxAttempts:
    try:
      path.ensureParentDir()
      writeFile(path, content)
      return
    except CatchableError as e:
      if attempt >= FileWriteMaxAttempts:
        raise
      let delay = FileWriteRetryBaseMs * attempt
      echo "Could not write ", path, "; retry ", attempt + 1, "/",
        FileWriteMaxAttempts, " in ", delay, "ms: ",
        e.msg.shortCommandError()
      sleep(delay)

proc runCommand(
  args: openArray[string],
  workingDir = "",
  check = true
): CommandResult =
  ## Runs one command directly and captures combined output.
  if args.len == 0:
    fail("Cannot run an empty command.")
  let
    command = args[0]
    rest =
      if args.len > 1:
        @args[1 .. ^1]
      else:
        newSeq[string]()
  let process = startProcess(
    command,
    workingDir = workingDir,
    args = rest,
    options = {poUsePath, poStdErrToStdOut}
  )
  result.output = process.outputStream().readAll()
  result.code = process.waitForExit()
  process.close()
  if check and result.code != 0:
    raise newException(
      OSError,
      "Command failed (" & $result.code & "): " & args.commandText() &
        "\n" & result.output
    )

proc isRetryableCommandError(message: string): bool =
  ## Returns true for transient command failures worth retrying.
  for marker in [
    "ReadTimeout",
    "Timeout",
    "timed out",
    "ConnectError",
    "RemoteProtocolError",
    "Server error",
    "502 Bad Gateway",
    "503 Service Unavailable",
    "504 Gateway Timeout",
    "Too Many Requests"
  ]:
    if marker in message:
      return true

proc coworldCommand(config: ToolConfig, args: openArray[string]): seq[string] =
  ## Builds one uv run coworld command.
  result = @["uv", "run", "coworld"]
  for arg in args:
    result.add arg
  if config.server.len > 0:
    result.add "--server"
    result.add config.server

proc softmaxCommand(config: ToolConfig, args: openArray[string]): seq[string] =
  ## Builds one uv run softmax command.
  result = @["uv", "run", "softmax"]
  for arg in args:
    result.add arg
  if config.server.len > 0:
    result.add "--server"
    result.add config.server

proc statusValue(output, key: string): string =
  ## Returns one field from softmax status output.
  let prefix = key & ":"
  for line in output.splitLines():
    let clean = line.strip()
    if clean.startsWith(prefix):
      return clean[prefix.len .. ^1].strip()

proc ensureUserToken(config: ToolConfig) =
  ## Switches softmax to the main user token when needed.
  echo "Checking Softmax auth..."
  let status = runCommand(
    softmaxCommand(config, ["status"]),
    workingDir = config.coworldDir
  ).output
  let subjectType = status.statusValue("subject_type")
  if subjectType == "user":
    echo "Using Softmax user token."
    return
  if subjectType != "player":
    fail("Could not determine Softmax auth subject_type.")

  echo "Softmax is using a player token",
    "; switching to the main user token."
  discard runCommand(
    softmaxCommand(config, ["player", "unset"]),
    workingDir = config.coworldDir
  )
  let updated = runCommand(
    softmaxCommand(config, ["status"]),
    workingDir = config.coworldDir
  ).output
  let updatedType = updated.statusValue("subject_type")
  if updatedType != "user":
    fail("Expected Softmax user token after player unset.")
  echo "Using Softmax user token."

proc coworldJson(config: ToolConfig, args: openArray[string]): JsonNode =
  ## Runs coworld and parses its JSON output.
  var command = coworldCommand(config, args)
  command.add "--json"
  for attempt in 1 .. CoworldMaxAttempts:
    let response = runCommand(
      command,
      workingDir = config.coworldDir,
      check = false
    )
    if response.code == 0:
      return parseJson(response.output)
    let summary = response.output.shortCommandError()
    if attempt >= CoworldMaxAttempts or
      not response.output.isRetryableCommandError():
        raise newException(
          OSError,
          "Command failed (" & $response.code & "): " &
            command.commandText() & "\n" & response.output
        )
    let delay = CoworldRetryBaseMs * attempt
    echo "Coworld command failed; retry ", attempt + 1, "/",
      CoworldMaxAttempts, " in ", delay div 1000, "s: ", summary
    sleep(delay)
  fail("Coworld command retry loop exited unexpectedly.")

proc policyFromVersion(node, player: JsonNode): PolicyRef =
  ## Builds a policy reference from a policy_version object.
  result.id = node.strField("id")
  result.label = node.strField("label")
  result.playerId = node.strField("player_id")
  result.playerName = player.strField("name")
  if result.playerId.len == 0:
    result.playerId = player.strField("id")
  if result.label.len == 0:
    let
      policy = node.field("policy")
      name = policy.strField("name")
      version = node.intField("version", -1)
    if name.len > 0 and version >= 0:
      result.label = name & ":v" & $version
  if result.playerName.len == 0:
    result.playerName = "unknown"

proc mergePolicy(policies: var Table[string, PolicyRef], policy: PolicyRef) =
  ## Adds or updates one policy reference.
  if policy.id.len == 0:
    return
  if not policies.hasKey(policy.id):
    policies[policy.id] = policy
    return
  var existing = policies[policy.id]
  if existing.label.len == 0:
    existing.label = policy.label
  if existing.playerId.len == 0:
    existing.playerId = policy.playerId
  if existing.playerName.len == 0 or existing.playerName == "unknown":
    existing.playerName = policy.playerName
  if policy.membershipId.len > 0:
    existing.membershipId = policy.membershipId
  if policy.status.len > 0:
    existing.status = policy.status
  if policy.substatus.len > 0:
    existing.substatus = policy.substatus
  if policy.divisionName.len > 0:
    existing.divisionName = policy.divisionName
  existing.isChampion = existing.isChampion or policy.isChampion
  policies[policy.id] = existing

proc fetchPolicies(config: ToolConfig): Table[string, PolicyRef] =
  ## Fetches all submitted or placed policies in the league.
  let submissions = coworldJson(
    config,
    [
      "submissions",
      "--league",
      config.leagueId,
      "--limit",
      "1000"
    ]
  )
  for row in submissions.rowsNode():
    var policy = policyFromVersion(
      row.field("policy_version"),
      row.field("player")
    )
    policy.membershipId = row.strField("league_policy_membership_id")
    policy.status = row.strField("status")
    mergePolicy(result, policy)

  let memberships = coworldJson(
    config,
    [
      "memberships",
      "--league",
      config.leagueId,
      "--limit",
      "1000"
    ]
  )
  for row in memberships.rowsNode():
    var policy = policyFromVersion(
      row.field("policy_version"),
      row.field("player")
    )
    policy.membershipId = row.strField("id")
    policy.status = row.strField("status")
    policy.substatus = row.strField("substatus")
    policy.isChampion = row.boolField("is_champion")
    policy.divisionName = row.field("division").strField("name")
    mergePolicy(result, policy)

proc entrantIds(row: JsonNode): seq[string] =
  ## Extracts entrant policy version IDs from a round row.
  let ids = row.field("round_config").field("entrant_policy_version_ids")
  if ids.kind != JArray:
    return
  for item in ids:
    if item.kind == JString:
      result.add item.getStr()

proc parseRound(row: JsonNode): RoundInfo =
  ## Parses one round row from Coworld JSON.
  RoundInfo(
    id: row.strField("id"),
    number: row.intField("round_number"),
    status: row.strField("status"),
    divisionName: row.field("division").strField("name"),
    startedAt: row.strField("started_at"),
    completedAt: row.strField("completed_at"),
    entrantIds: row.entrantIds()
  )

proc fetchRounds(config: ToolConfig): seq[RoundInfo] =
  ## Fetches recent rounds for the configured league.
  var offset = 0
  while result.len < config.roundLimit:
    let remaining = config.roundLimit - result.len
    let limit = min(MaxRoundsPerPage, remaining)
    let node = coworldJson(
      config,
      [
        "rounds",
        "--league",
        config.leagueId,
        "--limit",
        $limit,
        "--offset",
        $offset
      ]
    )
    let rows = node.rowsNode()
    if rows.len == 0:
      break
    for row in rows:
      let round = row.parseRound()
      if round.number < MinRoundNumber:
        continue
      result.add round
      if result.len >= config.roundLimit:
        break
    offset += rows.len
    let total = node.intField("total_count", offset)
    if offset >= total:
      break

proc isQualifier(divisionName: string): bool =
  ## Returns true when a division is a qualifier division.
  divisionName.toLowerAscii() == "qualifiers"

proc isQualifier(round: RoundInfo): bool =
  ## Returns true when a round belongs to a qualifier division.
  round.divisionName.isQualifier()

proc isQualifier(game: Game): bool =
  ## Returns true when a game belongs to a qualifier division.
  game.divisionName.isQualifier()

proc isReportRound(round: RoundInfo): bool =
  ## Returns true when a round should appear in this report.
  round.number >= MinRoundNumber and not round.isQualifier()

proc isReportGame(game: Game): bool =
  ## Returns true when a game should appear in this report.
  game.roundNumber >= MinRoundNumber and not game.isQualifier()

proc isCompleteStatus(status: string): bool =
  ## Returns true when an API status means no more rows are expected.
  status == "completed"

proc parseParticipants(row: JsonNode): seq[Participant] =
  ## Parses episode participant rows.
  let participants = row.field("participants")
  if participants.kind != JArray:
    return
  for participant in participants:
    result.add Participant(
      position: participant.intField("position", result.len),
      policyId: participant.strField("policy_version_id"),
      label: participant.strField("label"),
      playerId: participant.strField("player_id"),
      playerName: participant.strField("player_name")
    )

proc parseScores(row: JsonNode): seq[Score] =
  ## Parses episode score rows.
  let scores = row.field("scores")
  if scores.kind != JArray:
    return
  for item in scores:
    if item.kind == JObject:
      result.add Score(
        policyId: item.strField("policy_version_id"),
        score: item.numberField("score")
      )
    else:
      result.add Score(score: item.scoreValue())

proc parseGame(row: JsonNode, round: RoundInfo): Game =
  ## Parses one episode request row as a tournament game.
  Game(
    id: row.strField("id"),
    roundId: round.id,
    roundNumber: round.number,
    divisionName: round.divisionName,
    status: row.strField("status"),
    createdAt: row.strField("created_at"),
    replayUrl: row.strField("replay_url"),
    liveUrl: row.strField("live_url"),
    error: row.strField("error"),
    participants: row.parseParticipants(),
    scores: row.parseScores()
  )

proc participantJson(participant: Participant): JsonNode =
  ## Converts one participant to JSON metadata.
  %*{
    "position": participant.position,
    "policy_id": participant.policyId,
    "label": participant.label,
    "player_id": participant.playerId,
    "player_name": participant.playerName
  }

proc scoreJson(score: Score): JsonNode =
  ## Converts one score to JSON metadata.
  %*{
    "policy_id": score.policyId,
    "score": score.score
  }

proc gameJson(game: Game): JsonNode =
  ## Converts one tournament game to JSON metadata.
  result = newJObject()
  result["id"] = %game.id
  result["round_id"] = %game.roundId
  result["round_number"] = %game.roundNumber
  result["division_name"] = %game.divisionName
  result["status"] = %game.status
  result["created_at"] = %game.createdAt
  result["replay_url"] = %game.replayUrl
  result["live_url"] = %game.liveUrl
  result["error"] = %game.error
  var participants = newJArray()
  for participant in game.participants:
    participants.add participant.participantJson()
  result["participants"] = participants
  var scores = newJArray()
  for score in game.scores:
    scores.add score.scoreJson()
  result["scores"] = scores

proc metadataPath(config: ToolConfig): string =
  ## Returns the tournament metadata cache path.
  config.outDir / MetaFileName

proc roundCacheDir(config: ToolConfig): string =
  ## Returns the per-round cache directory.
  config.outDir / RoundCacheDirName

proc roundCachePath(config: ToolConfig, roundId: string): string =
  ## Returns the per-round cache path.
  config.roundCacheDir() / (roundId.slugify() & ".json")

proc writeMetadata(config: ToolConfig, games: openArray[Game]) =
  ## Writes cached tournament game metadata.
  var root = newJObject()
  root["cache_version"] = %CacheVersion
  root["league_id"] = %config.leagueId
  var rows = newJArray()
  for game in games:
    rows.add game.gameJson()
  root["games"] = rows
  writeFileRetry(config.metadataPath(), $root)

proc roundCacheJson(config: ToolConfig, round: CachedRound): JsonNode =
  ## Converts one cached round to JSON metadata.
  result = newJObject()
  result["cache_version"] = %CacheVersion
  result["league_id"] = %config.leagueId
  result["round_id"] = %round.roundId
  result["round_number"] = %round.roundNumber
  result["status"] = %round.status
  result["division_name"] = %round.divisionName
  result["started_at"] = %round.startedAt
  result["completed_at"] = %round.completedAt
  var rows = newJArray()
  for game in round.games:
    rows.add game.gameJson()
  result["games"] = rows

proc writeRoundCache(config: ToolConfig, round: CachedRound) =
  ## Writes one cached round file.
  createDir(config.roundCacheDir())
  writeFileRetry(
    config.roundCachePath(round.roundId),
    $config.roundCacheJson(round)
  )

proc toCachedRound(round: RoundInfo): CachedRound =
  ## Builds an empty cached round from round metadata.
  CachedRound(
    roundId: round.id,
    roundNumber: round.number,
    status: round.status,
    divisionName: round.divisionName,
    startedAt: round.startedAt,
    completedAt: round.completedAt
  )

proc shouldRefreshRound(
  round: RoundInfo,
  cachedRounds: Table[string, CachedRound]
): bool =
  ## Returns true when a cached round may still be changing.
  if not round.status.isCompleteStatus():
    return true
  if not cachedRounds.hasKey(round.id):
    return false
  not cachedRounds[round.id].status.isCompleteStatus()

proc fetchRoundCaches(
  config: ToolConfig,
  rounds: openArray[RoundInfo],
  cachedMaxRound: int,
  cachedRounds: Table[string, CachedRound]
): seq[CachedRound] =
  ## Fetches new round caches plus the newest cached round.
  var skipped = 0
  for round in rounds:
    if not round.isReportRound():
      continue
    let refreshRound = round.shouldRefreshRound(cachedRounds)
    if cachedMaxRound >= 0 and
      round.number < cachedMaxRound and
      not refreshRound:
      inc skipped
      continue
    echo "Scanning round ", round.number, " ", round.id.shortId()
    var
      cached = round.toCachedRound()
      offset = 0
    while true:
      let node = coworldJson(
        config,
        [
          "episodes",
          "--round",
          round.id,
          "--limit",
          $MaxEpisodesPerPage,
          "--offset",
          $offset
        ]
      )
      let rows = node.rowsNode()
      for row in rows:
        cached.games.add parseGame(row, round)
      if rows.len < MaxEpisodesPerPage:
        break
      offset += rows.len
    config.writeRoundCache(cached)
    result.add cached
  if skipped > 0:
    echo "Skipped ", skipped, " cached rounds."

proc parseCachedParticipant(node: JsonNode): Participant =
  ## Parses one cached participant row.
  Participant(
    position: node.intField("position"),
    policyId: node.strField("policy_id"),
    label: node.strField("label"),
    playerId: node.strField("player_id"),
    playerName: node.strField("player_name")
  )

proc parseCachedScore(node: JsonNode): Score =
  ## Parses one cached score row.
  Score(
    policyId: node.strField("policy_id"),
    score: node.numberField("score")
  )

proc parseCachedGame(node: JsonNode): Game =
  ## Parses one cached tournament game.
  result = Game(
    id: node.strField("id"),
    roundId: node.strField("round_id"),
    roundNumber: node.intField("round_number"),
    divisionName: node.strField("division_name"),
    status: node.strField("status"),
    createdAt: node.strField("created_at"),
    replayUrl: node.strField("replay_url"),
    liveUrl: node.strField("live_url"),
    error: node.strField("error")
  )
  let participants = node.field("participants")
  if participants.kind == JArray:
    for participant in participants:
      result.participants.add participant.parseCachedParticipant()
  let scores = node.field("scores")
  if scores.kind == JArray:
    for score in scores:
      result.scores.add score.parseCachedScore()

proc readMetadataGames(config: ToolConfig): seq[Game] =
  ## Reads cached tournament games from the report metadata.
  let path = config.metadataPath()
  if not fileExists(path):
    return
  let node = parseFile(path)
  if node.intField("cache_version") != CacheVersion:
    return
  let leagueId = node.strField("league_id")
  if leagueId.len > 0 and leagueId != config.leagueId:
    return
  let games = node.field("games")
  if games.kind != JArray:
    return
  for game in games:
    let cached = game.parseCachedGame()
    if cached.isReportGame():
      result.add cached

proc readRoundCache(config: ToolConfig, path: string): CachedRound =
  ## Reads one cached round file.
  result.roundNumber = -1
  let node = parseFile(path)
  if node.intField("cache_version") != CacheVersion:
    return
  let leagueId = node.strField("league_id")
  if leagueId.len > 0 and leagueId != config.leagueId:
    return
  result.roundId = node.strField("round_id")
  result.roundNumber = node.intField("round_number", -1)
  result.status = node.strField("status")
  result.divisionName = node.strField("division_name")
  result.startedAt = node.strField("started_at")
  result.completedAt = node.strField("completed_at")
  let games = node.field("games")
  if games.kind != JArray:
    return
  for game in games:
    let cached = game.parseCachedGame()
    if cached.isReportGame():
      result.games.add cached

proc isReportRound(round: CachedRound): bool =
  ## Returns true when a cached round should appear in this report.
  round.roundNumber >= MinRoundNumber and not round.divisionName.isQualifier()

proc readCachedRounds(config: ToolConfig): seq[CachedRound] =
  ## Reads cached tournament rounds from per-round files.
  let dir = config.roundCacheDir()
  if not dirExists(dir):
    return
  for kind, path in walkDir(dir):
    if kind != pcFile or path.splitFile().ext != ".json":
      continue
    let round = config.readRoundCache(path)
    if round.roundId.len == 0 or not round.isReportRound():
      continue
    result.add round

proc maxCachedRound(games: openArray[Game]): int =
  ## Returns the newest report round present in cached games.
  result = -1
  for game in games:
    if game.isReportGame() and game.roundNumber > result:
      result = game.roundNumber

proc maxCachedRound(rounds: openArray[CachedRound]): int =
  ## Returns the newest report round present in cached rounds.
  result = -1
  for round in rounds:
    if round.isReportRound() and round.roundNumber > result:
      result = round.roundNumber

proc cachedGames(rounds: openArray[CachedRound]): seq[Game] =
  ## Returns all games stored in cached rounds.
  for round in rounds:
    for game in round.games:
      if game.isReportGame():
        result.add game

proc roundIdSet(rounds: openArray[CachedRound]): HashSet[string] =
  ## Returns IDs for cached rounds.
  for round in rounds:
    if round.roundId.len > 0:
      result.incl round.roundId

proc roundTable(rounds: openArray[CachedRound]): Table[string, CachedRound] =
  ## Returns cached rounds keyed by round ID.
  for round in rounds:
    if round.roundId.len > 0:
      result[round.roundId] = round

proc roundTableByNumber(
  rounds: openArray[CachedRound]
): Table[int, CachedRound] =
  ## Returns cached rounds keyed by round number.
  for round in rounds:
    if round.isReportRound():
      result[round.roundNumber] = round

proc mergeCachedRounds(
  cached,
  fresh: openArray[CachedRound]
): seq[CachedRound] =
  ## Merges cached rounds while replacing refreshed round metadata.
  var byId: OrderedTable[string, CachedRound]
  for round in cached:
    if round.roundId.len > 0 and round.isReportRound():
      byId[round.roundId] = round
  for round in fresh:
    if round.roundId.len > 0 and round.isReportRound():
      byId[round.roundId] = round
  for round in byId.values:
    result.add round

proc mergeGamesReplacingRounds(
  cached,
  fresh: openArray[Game],
  refreshedRoundIds: HashSet[string]
): seq[Game] =
  ## Merges games while replacing refreshed rounds as whole units.
  var byId: OrderedTable[string, Game]
  for game in cached:
    if game.roundId in refreshedRoundIds:
      continue
    if game.id.len > 0 and game.isReportGame():
      byId[game.id] = game
  for game in fresh:
    if game.id.len > 0 and game.isReportGame():
      byId[game.id] = game
  for game in byId.values:
    result.add game

proc policyFromParticipant(participant: Participant): PolicyRef =
  ## Builds a policy reference from one episode participant.
  result.id = participant.policyId
  result.label =
    if participant.label.len > 0:
      participant.label
    elif participant.playerName.len > 0:
      participant.playerName
    else:
      participant.policyId.shortId()
  result.playerId = participant.playerId
  result.playerName = participant.playerName
  if result.playerName.len == 0:
    result.playerName = "unknown"

proc expandPolicies(
  policies: Table[string, PolicyRef],
  games: openArray[Game]
): Table[string, PolicyRef] =
  ## Builds policy metadata for every downloaded participant.
  for game in games:
    if not game.isReportGame():
      continue
    for participant in game.participants:
      result.mergePolicy(participant.policyFromParticipant())
      if policies.hasKey(participant.policyId):
        result.mergePolicy(policies[participant.policyId])

proc scoreFor(game: Game, policyId: string): tuple[found: bool, score: float] =
  ## Finds one policy score in an episode.
  for score in game.scores:
    if score.policyId == policyId:
      return (true, score.score)
  (false, 0.0)

proc nearScore(value, target: float): bool =
  ## Returns true when a score is close enough to a target.
  abs(value - target) <= ScoreEpsilon

proc binaryScore(value: float): bool =
  ## Returns true when one score belongs to the binary result format.
  value.nearScore(0.0) or value.nearScore(1.0)

proc binaryScoreFlags(scores: openArray[Score]): tuple[
  binary,
  hasZero,
  hasOne: bool
] =
  ## Returns the binary-state flags for one set of policy scores.
  if scores.len == 0:
    return (false, false, false)
  result.binary = true
  for score in scores:
    if not score.score.binaryScore():
      result.binary = false
      return
    if score.score.nearScore(0.0):
      result.hasZero = true
    if score.score.nearScore(1.0):
      result.hasOne = true

proc stripVersionSuffix(label: string): string =
  ## Returns a display label without a trailing version suffix.
  let clean = label.strip()
  let marker = clean.toLowerAscii().rfind(":v")
  if marker < 0 or marker + 2 >= clean.len:
    return clean
  for i in marker + 2 ..< clean.len:
    if clean[i] < '0' or clean[i] > '9':
      return clean
  clean[0 ..< marker]

proc stripDashVersionSuffix(label: string): string =
  ## Returns a display label without a trailing dash-version suffix.
  let clean = label.strip()
  let marker = clean.toLowerAscii().rfind("-v")
  if marker < 0 or marker + 2 >= clean.len:
    return clean
  for i in marker + 2 ..< clean.len:
    if clean[i] < '0' or clean[i] > '9':
      return clean
  clean[0 ..< marker]

proc heatFamilyLabel(label: string): string =
  ## Returns the collapsed display label for the heat map.
  label.stripVersionSuffix().stripDashVersionSuffix()

proc opponentScores(game: Game, policyId: string): seq[float] =
  ## Returns scores for policies other than the selected one.
  for score in game.scores:
    if score.policyId.len > 0 and score.policyId != policyId:
      result.add score.score

proc policyResult(game: Game, policyId: string): string =
  ## Classifies one policy's result in a game.
  let own = game.scoreFor(policyId)
  if game.status != "completed" or not own.found:
    return "-"
  let opponents = game.opponentScores(policyId)
  if opponents.len == 0:
    return "-"
  let binary = game.scores.binaryScoreFlags()
  if binary.binary:
    if binary.hasZero and binary.hasOne:
      if own.score.nearScore(1.0):
        return "W"
      return "L"
    return "T"
  var bestOpponent = opponents[0]
  for i in 1 ..< opponents.len:
    bestOpponent = max(bestOpponent, opponents[i])
  let margin = own.score - bestOpponent
  if margin > ScoreEpsilon:
    "W"
  elif margin < -ScoreEpsilon:
    "L"
  else:
    "T"

proc updateStats(stats: var PolicyStats, game: Game, policyId: string) =
  ## Adds one game to a policy summary.
  if game.status != "completed":
    return
  inc stats.games
  let score = game.scoreFor(policyId)
  if score.found:
    inc stats.scoredGames
    stats.totalScore += score.score
  case game.policyResult(policyId)
  of "W":
    inc stats.wins
  of "L":
    inc stats.losses
  of "T":
    inc stats.ties
  else:
    discard

proc computeStats(
  policies: Table[string, PolicyRef],
  games: openArray[Game]
): Table[string, PolicyStats] =
  ## Computes per-policy tournament summaries.
  for id in policies.keys:
    result[id] = PolicyStats()
  for game in games:
    var seen: HashSet[string]
    for participant in game.participants:
      if policies.hasKey(participant.policyId) and
        participant.policyId notin seen:
          var stats = result.getOrDefault(participant.policyId)
          stats.updateStats(game, participant.policyId)
          result[participant.policyId] = stats
          seen.incl participant.policyId

proc avgScore(stats: PolicyStats): float =
  ## Returns the average scored episode score.
  if stats.scoredGames == 0:
    return 0.0
  stats.totalScore / stats.scoredGames.float

proc winPercentText(stats: PolicyStats): string =
  ## Returns win percentage over every observed game.
  if stats.games == 0:
    return "-"
  numberText(stats.wins.float / stats.games.float * 100.0, 0) & "%"

proc winRate(stats: PolicyStats): float =
  ## Returns win percentage over every observed game.
  if stats.games == 0:
    return 0.0
  stats.wins.float / stats.games.float * 100.0

proc winRateX(rate, axisMax: float): int =
  ## Returns one win-rate x coordinate.
  var clamped = rate
  if clamped < 0.0:
    clamped = 0.0
  if clamped > axisMax:
    clamped = axisMax
  WinRateLeftPadding + int(
    clamped / axisMax * WinRatePlotWidth.float + 0.5
  )

proc winRateSlotX(slot: int): int =
  ## Returns the baseline x coordinate for one win-rate slot.
  WinRateLeftPadding + slot * WinRateSlotStep

proc winRateLabelPosition(
  rate: float,
  axisMax: float,
  slot,
  level: int
): tuple[
  x,
  elbowY,
  labelX,
  labelY: float
] =
  ## Returns one relative win-rate label position on a fixed slot.
  let
    slotX = slot.winRateSlotX().float
    levelOffset = WinRateBaseStem.float +
      level.float * WinRateLevelStep.float
  result.x = rate.winRateX(axisMax).float
  result.elbowY = -levelOffset - (result.x - slotX)
  result.labelX = result.x + WinRateStemDiagonal.float
  result.labelY = result.elbowY - WinRateStemDiagonal.float

proc closestWinRateSlot(rate, axisMax: float): int =
  ## Returns the rightmost fixed slot under one anchor point.
  let x = rate.winRateX(axisMax)
  result = (x - WinRateLeftPadding) div WinRateSlotStep
  if result < WinRateMinSlot:
    result = WinRateMinSlot
  elif result > WinRateMaxSlot:
    result = WinRateMaxSlot

proc minWinRateSlot(rate, axisMax: float): int =
  ## Returns the leftmost slot still under one anchor point.
  let x = rate.winRateX(axisMax)
  result = (x - WinRateLeftPadding - WinRateSlotReach) div
    WinRateSlotStep
  if result < WinRateMinSlot:
    result = WinRateMinSlot
  elif result > WinRateMaxSlot:
    result = WinRateMaxSlot

proc occupiedWinRateSlot(
  occupied: openArray[int],
  slot: int
): bool =
  ## Returns true when a label slot has already been used.
  for used in occupied:
    if used == slot:
      return true

proc chooseWinRateSlot(
  occupied: openArray[int],
  startSlot,
  minSlot: int
): int =
  ## Returns the rightmost unused slot for one win-rate label.
  for slot in countdown(startSlot, minSlot):
    if not occupied.occupiedWinRateSlot(slot):
      return slot
  result = minSlot - 1
  while occupied.occupiedWinRateSlot(result):
    dec result

proc winRatePoints(
  policies: Table[string, PolicyRef],
  stats: Table[string, PolicyStats]
): seq[WinRatePoint] =
  ## Returns all policies with completed win-rate data.
  for policy in policies.values:
    let stat = stats.getOrDefault(policy.id)
    if stat.games == 0:
      continue
    result.add WinRatePoint(
      id: policy.id,
      label: policy.label,
      rate: stat.winRate(),
      games: stat.games,
      wins: stat.wins,
      losses: stat.losses,
      ties: stat.ties
    )
  result.sort do (a, b: WinRatePoint) -> int:
    if a.rate > b.rate + ScoreEpsilon:
      -1
    elif a.rate + ScoreEpsilon < b.rate:
      1
    else:
      let byLabel = cmp(a.label, b.label)
      if byLabel != 0:
        byLabel
      else:
        cmp(a.id, b.id)

proc winRateAxisMax(points: openArray[WinRatePoint]): int =
  ## Returns the visible win-rate ceiling rounded up to ten percent.
  result = 10
  for point in points:
    while result < 100 and result.float < point.rate - ScoreEpsilon:
      result += 10

proc assignWinRateLevels(
  points: var seq[WinRatePoint],
  axisMax: float
) =
  ## Assigns fixed 45-degree slots from right to left.
  var occupied: seq[int]
  for i in 0 ..< points.len:
    let
      startSlot = points[i].rate.closestWinRateSlot(axisMax)
      minSlot = points[i].rate.minWinRateSlot(axisMax)
      chosen = occupied.chooseWinRateSlot(startSlot, minSlot)
    points[i].slot = chosen
    points[i].level = 0
    occupied.add chosen

proc maxWinRateLevel(points: openArray[WinRatePoint]): int =
  ## Returns the highest assigned win-rate stem level.
  for point in points:
    if point.level > result:
      result = point.level

proc minWinRateLabelY(
  points: openArray[WinRatePoint],
  axisMax: float
): int =
  ## Returns the highest relative y coordinate used by win-rate labels.
  for point in points:
    let
      position = winRateLabelPosition(
        point.rate,
        axisMax,
        point.slot,
        point.level
      )
      labelY = int(position.labelY - 0.5)
    if labelY < result:
      result = labelY

proc winRateTitle(point: WinRatePoint): string =
  ## Returns the tooltip text for one win-rate point.
  point.label & ": " & numberText(point.rate, 0) & "%, " &
    $point.wins & "-" & $point.losses & "-" & $point.ties &
    " over " & $point.games & " games"

proc renderWinRateChart(
  policies: Table[string, PolicyRef],
  stats: Table[string, PolicyStats]
): string =
  ## Renders a compact policy win-rate axis chart.
  var points = winRatePoints(policies, stats)
  if points.len == 0:
    return "<p>No completed win rates yet.</p>\n"
  let axisMax = points.winRateAxisMax()
  points.assignWinRateLevels(axisMax.float)
  let
    maxLevel = points.maxWinRateLevel()
    minLabelY = points.minWinRateLabelY(axisMax.float)
    plotEnd = WinRateLeftPadding + WinRatePlotWidth
    axisY = max(
      WinRateTopPadding + WinRateBaseStem +
        maxLevel * WinRateLevelStep,
      -minLabelY + WinRateTickHeight
    )
    width = plotEnd + WinRateRightPadding
    height = axisY + WinRateBottomPadding
  result.add "<div class=\"winrate-wrap\">\n"
  result.add "<svg class=\"winrate-svg\" width=\"" & $width
  result.add "\" height=\"" & $height & "\" viewBox=\"0 0 "
  result.add $width & " " & $height
  result.add "\" role=\"img\" aria-label=\"Policy win rates\">\n"
  result.add "<path class=\"winrate-axis\" d=\"M "
  result.add $WinRateLeftPadding & " " & $axisY
  result.add " H " & $plotEnd
  for tick in countup(0, axisMax, 10):
    let x = tick.float.winRateX(axisMax.float)
    result.add " M " & $x & " " & $axisY
    result.add " v " & $WinRateTickHeight
  result.add "\" />\n"
  for tick in countup(0, axisMax, 10):
    let x = tick.float.winRateX(axisMax.float)
    result.add "<text class=\"winrate-tick\" x=\"" & $x
    result.add "\" y=\"" & $(axisY + 28)
    result.add "\" text-anchor=\"middle\">"
    result.add ($tick).htmlEscape()
    result.add "</text>\n"
  for point in points:
    let
      position = winRateLabelPosition(
        point.rate,
        axisMax.float,
        point.slot,
        point.level
      )
      x = int(position.x + 0.5)
      elbowY = axisY + int(position.elbowY - 0.5)
      labelX = int(position.labelX + 0.5)
      labelY = axisY + int(position.labelY - 0.5)
      textX = labelX + WinRateLabelShiftX
    result.add "<path class=\"winrate-stem\" d=\"M " & $x
    result.add " " & $axisY & " V " & $elbowY
    result.add " L " & $labelX & " " & $labelY & "\" />\n"
    result.add "<circle class=\"winrate-dot\" cx=\"" & $x
    result.add "\" cy=\"" & $axisY & "\" r=\"" & $WinRateDotRadius
    result.add "\"><title>"
    result.add point.winRateTitle().htmlEscape()
    result.add "</title></circle>\n"
    result.add "<text class=\"winrate-label\" x=\"" & $textX
    result.add "\" y=\"" & $labelY & "\" transform=\"rotate(-45 "
    result.add $textX & " " & $labelY & ")\">"
    result.add point.label.htmlEscape()
    result.add "</text>\n"
  result.add "</svg>\n</div>\n"

proc copyAssets(outDir, tufteDir: string) =
  ## Copies Tufte CSS and fonts into the report root.
  createDir(outDir)
  var css = readFile(tufteDir / "tufte.css")
  for font in PreloadFonts:
    css = css.replace(
      "url(\"fonts/" & font & "\") format(\"opentype\");\n" &
        "  font-display: swap;",
      "url(\"fonts/" & font & "\") format(\"opentype\");\n" &
        "  font-display: block;"
    )
  writeFileRetry(outDir / "tufte.css", css)
  let fontsDir = tufteDir / "fonts"
  if dirExists(fontsDir):
    createDir(outDir / "fonts")
    for file in walkFiles(fontsDir / "*"):
      copyFile(file, outDir / "fonts" / extractFilename(file))

proc cssBase(cssHref: string): string =
  ## Returns the relative asset directory for a stylesheet URL.
  let slash = cssHref.rfind('/')
  if slash >= 0:
    return cssHref[0 .. slash]
  ""

proc pageStart(title, cssHref: string): string =
  ## Returns the opening HTML for one static page.
  let base = cssBase(cssHref)
  result.add "<!doctype html>\n"
  result.add "<html lang=\"en\">\n<head>\n"
  result.add "  <meta charset=\"utf-8\">\n"
  result.add "  <meta name=\"viewport\" content=\"width=device-width, "
  result.add "initial-scale=1\">\n"
  result.add "  <title>" & title.htmlEscape() & "</title>\n"
  for font in PreloadFonts:
    result.add "  <link rel=\"preload\" href=\""
    result.add (base & "fonts/" & font).htmlEscape()
    result.add "\" as=\"font\" type=\"font/otf\" crossorigin>\n"
  result.add "  <link rel=\"stylesheet\" href=\"" & cssHref.htmlEscape()
  result.add "\">\n"
  result.add "  <style>\n"
  result.add "    table { table-layout: fixed; width: 100%; }\n"
  result.add "    .report-table { max-width: var(--max-width); "
  result.add "table-layout: auto; }\n"
  result.add "    .report-table.wide { max-width: calc(100vw - 4rem); "
  result.add "table-layout: auto; width: "
  result.add $WinRatePlotWidth & "px; }\n"
  result.add "    .report-table.policy-summary { table-layout: fixed; }\n"
  result.add "    .report-table th, .report-table td { "
  result.add "overflow: hidden; padding-right: 1.1rem; "
  result.add "text-overflow: ellipsis; white-space: nowrap; }\n"
  result.add "    .report-table th { cursor: pointer; }\n"
  result.add "    .report-table th.sort-active { font-weight: 700; }\n"
  result.add "    .report-table td.wrap { white-space: normal; }\n"
  result.add "    .report-table.round-name-key { "
  result.add "table-layout: auto; width: auto; "
  result.add "max-width: 100%; margin-bottom: 1.5rem; }\n"
  result.add "    .round-name-key th, .round-name-key td { "
  result.add "overflow: hidden; padding-right: 1.75rem; "
  result.add "text-overflow: ellipsis; "
  result.add "white-space: nowrap; }\n"
  result.add "    .round-games-wrap { margin: 1rem 0 2rem; "
  result.add "overflow-x: auto; }\n"
  result.add "    .round-games { display: table; table-layout: fixed; "
  result.add "margin: 0; max-width: var(--max-width); width: 100%; }\n"
  result.add "    .round-games th, .round-games td { "
  result.add "overflow: hidden; padding-right: 0.65rem; "
  result.add "text-overflow: ellipsis; "
  result.add "white-space: nowrap; }\n"
  result.add "    .round-games .game-col { width: 7.3rem; }\n"
  result.add "    .round-games .score-col { width: 3.8rem; }\n"
  result.add "    .round-games .game-cell a, "
  result.add ".round-games .player-cell a { display: block; max-width: 100%; "
  result.add "overflow: hidden; text-overflow: ellipsis; "
  result.add "white-space: nowrap; }\n"
  result.add "    .inactive-row { opacity: 0.5; }\n"
  result.add "    .heat-wrap { box-sizing: border-box; margin: 1rem "
  result.add "calc(50% - 50vw); max-width: 100vw; overflow-x: hidden; "
  result.add "padding: 0 1rem; }\n"
  result.add "    .heatmap-svg { display: block; height: auto; margin: 0 auto; "
  result.add "max-width: calc(100vw - 2rem); width: auto; "
  result.add "shape-rendering: crispEdges; }\n"
  result.add "    .heatmap-svg text { font-family: inherit; "
  result.add "font-size: 0.78rem; shape-rendering: auto; }\n"
  result.add "    .heatmap-svg .heat-label { fill: #111; }\n"
  result.add "    .heatmap-svg .heat-rate { font-size: 0.62rem; "
  result.add "font-weight: 700; }\n"
  result.add "    .winrate-wrap { box-sizing: border-box; margin: 1rem "
  result.add "calc(50% - 50vw); max-width: 100vw; overflow-x: hidden; "
  result.add "padding: 0 1rem; }\n"
  result.add "    .winrate-svg { display: block; height: auto; "
  result.add "margin: 0 auto; max-width: calc(100vw - 2rem); width: auto; "
  result.add "shape-rendering: crispEdges; }\n"
  result.add "    .winrate-svg text { font-family: \"ETBembo\", "
  result.add "\"Palatino Linotype\", Palatino, serif; "
  result.add "shape-rendering: auto; }\n"
  result.add "    .winrate-svg .winrate-axis { stroke: #111; "
  result.add "stroke-width: 1.25; fill: none; }\n"
  result.add "    .winrate-svg .winrate-stem { stroke: #111; "
  result.add "stroke-width: 1; fill: none; }\n"
  result.add "    .winrate-svg .winrate-dot { fill: #000; }\n"
  result.add "    .winrate-svg .winrate-tick { fill: #111; "
  result.add "font-size: 1rem; }\n"
  result.add "    .winrate-svg .winrate-label { fill: #111; "
  result.add "font-size: 0.92rem; font-weight: 600; }\n"
  result.add scoreChartCss()
  result.add "    td, th { vertical-align: top; }\n"
  result.add "    .num { text-align: right; white-space: nowrap; }\n"
  result.add "    .name { overflow: hidden; text-overflow: ellipsis; "
  result.add "white-space: nowrap; }\n"
  result.add "    .bot-name { max-width: 16rem; overflow: hidden; "
  result.add "text-overflow: ellipsis; white-space: nowrap; }\n"
  result.add "    .bot-name a { display: inline-block; max-width: 100%; "
  result.add "overflow: hidden; text-overflow: ellipsis; "
  result.add "vertical-align: bottom; }\n"
  result.add "    .strategy { max-width: 12rem; overflow: hidden; "
  result.add "text-overflow: ellipsis; white-space: nowrap; }\n"
  result.add "    .strategy a { display: inline-block; max-width: 100%; "
  result.add "overflow: hidden; text-overflow: ellipsis; "
  result.add "vertical-align: bottom; }\n"
  result.add "    .auto-refresh { position: fixed; top: 0.65rem; "
  result.add "right: 0.9rem; display: inline-flex; align-items: "
  result.add "baseline; gap: 0.35rem; font-size: 0.85rem; color: #000; "
  result.add "opacity: 0.28; z-index: 5; }\n"
  result.add "    .auto-refresh.is-on { opacity: 1; }\n"
  result.add "    .auto-refresh input { margin: 0; }\n"
  result.add "    .small { font-size: 0.85rem; }\n"
  result.add replayExtractorCss()
  result.add "  </style>\n"
  result.add "</head>\n<body>\n<main>\n"

proc autoRefreshControl(): string =
  ## Returns the persisted auto-refresh toggle.
  "<label class=\"auto-refresh\"><input id=\"auto-refresh-toggle\" " &
    "type=\"checkbox\" data-auto-refresh> auto reload</label>\n"

proc sortScript(): string =
  ## Returns the table sorting script for generated reports.
  """
<script>
(() => {
  function cellValue(row, index) {
    const cell = row.children[index];
    return cell ? cell.textContent.trim() : "";
  }

  function compareValues(left, right) {
    const leftNumber = Number(left.replace(/,/g, ""));
    const rightNumber = Number(right.replace(/,/g, ""));
    if (!Number.isNaN(leftNumber) && !Number.isNaN(rightNumber)) {
      return leftNumber - rightNumber;
    }
    return left.localeCompare(right, undefined, {
      numeric: true,
      sensitivity: "base"
    });
  }

  function resetHeaders(table) {
    table.querySelectorAll("th.sort-active").forEach((header) => {
      header.classList.remove("sort-active");
    });
  }

  function sortTable(table, column, header) {
    const body = table.tBodies[0];
    if (!body) {
      return;
    }
    const rows = Array.from(body.rows);
    rows.forEach((row, index) => {
      if (!row.dataset.originalIndex) {
        row.dataset.originalIndex = String(index);
      }
    });

    const sameColumn = table.dataset.sortColumn === String(column);
    let direction = "asc";
    if (sameColumn && table.dataset.sortDirection === "asc") {
      direction = "desc";
    } else if (sameColumn && table.dataset.sortDirection === "desc") {
      direction = "";
    }

    resetHeaders(table);
    if (direction.length === 0) {
      delete table.dataset.sortColumn;
      delete table.dataset.sortDirection;
      rows.sort((left, right) => {
        return Number(left.dataset.originalIndex) -
          Number(right.dataset.originalIndex);
      });
    } else {
      table.dataset.sortColumn = String(column);
      table.dataset.sortDirection = direction;
      header.classList.add("sort-active");
      rows.sort((left, right) => {
        const compared = compareValues(
          cellValue(left, column),
          cellValue(right, column)
        );
        return direction === "asc" ? compared : -compared;
      });
    }

    rows.forEach((row) => body.appendChild(row));
  }

  document.querySelectorAll("table:not(.no-sort)").forEach((table) => {
    const headers = table.tHead ? Array.from(table.tHead.rows[0].cells) : [];
    headers.forEach((header, column) => {
      header.addEventListener("click", () => sortTable(table, column, header));
    });
  });
})();
</script>
"""

proc autoRefreshScript(): string =
  ## Returns the index-page auto-refresh script.
  """
<script>
(() => {
  const storageKey = "crewrift-prime-tournament-auto-refresh";
  const refreshMs = 300000;
  const toggle = document.querySelector("[data-auto-refresh]");
  if (!toggle) {
    return;
  }

  function isEnabled() {
    try {
      return localStorage.getItem(storageKey) === "1";
    } catch {
      return false;
    }
  }

  function setEnabled(enabled) {
    try {
      if (enabled) {
        localStorage.setItem(storageKey, "1");
      } else {
        localStorage.removeItem(storageKey);
      }
    } catch {
    }
  }

  const label = toggle.closest(".auto-refresh");
  const enabled = isEnabled();
  toggle.checked = enabled;
  if (label) {
    label.classList.toggle("is-on", enabled);
  }
  let timer = 0;
  if (toggle.checked) {
    timer = window.setTimeout(() => window.location.reload(), refreshMs);
  }

  toggle.addEventListener("change", () => {
    setEnabled(toggle.checked);
    if (label) {
      label.classList.toggle("is-on", toggle.checked);
    }
    if (timer !== 0) {
      window.clearTimeout(timer);
    }
    window.location.reload();
  });
})();
</script>
"""

proc pageEnd(autoRefresh = false): string =
  ## Returns the closing HTML for one static page.
  result.add "</main>\n"
  result.add sortScript()
  if autoRefresh:
    result.add autoRefreshScript()
  result.add "</body>\n</html>\n"

proc linkHtml(href, text: string): string =
  ## Renders one HTML link.
  "<a href=\"" & href.htmlEscape() & "\">" & text.htmlEscape() & "</a>"

proc linkHtml(href, text, title: string): string =
  ## Renders one HTML link with an optional title.
  if title.len == 0:
    return linkHtml(href, text)
  "<a href=\"" & href.htmlEscape() & "\" title=\"" & title.htmlEscape() &
    "\">" & text.htmlEscape() & "</a>"

proc spanHtml(text, title: string): string =
  ## Renders one HTML span with an optional title.
  if title.len == 0:
    return text.htmlEscape()
  "<span title=\"" & title.htmlEscape() & "\">" &
    text.htmlEscape() & "</span>"

proc markHtml(text: string): string =
  ## Renders highlighted HTML.
  "<mark>" & text.htmlEscape() & "</mark>"

proc tableCell(text: string): string =
  ## Renders one escaped table cell.
  "<td>" & text.htmlEscape() & "</td>"

proc htmlCell(html: string): string =
  ## Renders one table cell from already escaped HTML.
  "<td>" & html & "</td>"

proc numCell(text: string): string =
  ## Renders one numeric table cell.
  "<td class=\"num\">" & text.htmlEscape() & "</td>"

proc classCell(text, className: string): string =
  ## Renders one escaped table cell with a CSS class.
  "<td class=\"" & className.htmlEscape() & "\">" &
    text.htmlEscape() & "</td>"

proc classHtmlCell(html, className: string): string =
  ## Renders one HTML table cell with a CSS class.
  "<td class=\"" & className.htmlEscape() & "\">" & html & "</td>"

proc strategyMarkdownPath(): string =
  ## Returns the local strategy markdown source path.
  rootDir() / "strategy.md"

proc splitMarkdownRow(line: string): seq[string] =
  ## Splits one simple markdown table row into cells.
  var text = line.strip()
  if text.len == 0 or text[0] != '|':
    return
  if text.len > 0 and text[0] == '|':
    if text.len == 1:
      return
    text = text[1 .. ^1]
  if text.len > 0 and text[^1] == '|':
    text.setLen(text.len - 1)
  for cell in text.split('|'):
    result.add cell.strip()

proc isMarkdownSeparator(cells: openArray[string]): bool =
  ## Returns true for a markdown table separator row.
  if cells.len == 0:
    return false
  for cell in cells:
    var clean = cell.strip()
    clean = clean.replace(":", "")
    clean = clean.replace("-", "")
    if clean.len > 0:
      return false
  true

proc columnIndex(cells: openArray[string], name: string): int =
  ## Returns the matching table column index or -1.
  result = -1
  for i, cell in cells:
    if cell == name:
      return i

proc markdownHeading(line: string): tuple[level: int, title: string] =
  ## Returns markdown heading details from one line.
  let text = line.strip()
  var level = 0
  while level < text.len and text[level] == '#':
    inc level
  if level == 0 or level > 6:
    return
  if level >= text.len or text[level] != ' ':
    return
  if level + 1 >= text.len:
    return
  result.level = level
  result.title = text[level + 1 .. ^1].strip()

proc readStrategyDocs(): StrategyDocs =
  ## Reads strategy docs and bot strategy assignments.
  let path = strategyMarkdownPath()
  if not fileExists(path):
    return
  result.markdown = readFile(path)
  var headers: seq[string]
  for line in result.markdown.splitLines():
    let heading = line.markdownHeading()
    if heading.title.len > 0:
      result.headings.incl heading.title
    let cells = line.splitMarkdownRow()
    if cells.len == 0:
      continue
    if cells.isMarkdownSeparator():
      continue
    if cells.columnIndex("Bot") >= 0 and cells.columnIndex("Strategy") >= 0:
      headers = cells
      continue
    let
      botIndex = headers.columnIndex("Bot")
      strategyIndex = headers.columnIndex("Strategy")
    if botIndex < 0 or strategyIndex < 0:
      continue
    if cells.len <= max(botIndex, strategyIndex):
      continue
    let
      bot = cells[botIndex]
      strategy = cells[strategyIndex]
    if bot.len > 0 and strategy.len > 0:
      result.strategies[bot] = strategy

proc closeMarkdownParagraph(html: var string, paragraph: var seq[string]) =
  ## Flushes one accumulated markdown paragraph.
  if paragraph.len == 0:
    return
  html.add "<p>"
  html.add paragraph.join(" ").htmlEscape()
  html.add "</p>\n"
  paragraph.setLen(0)

proc closeMarkdownTable(html: var string, tableOpen: var bool) =
  ## Flushes one generated markdown table.
  if not tableOpen:
    return
  html.add "</tbody></table>\n"
  tableOpen = false

proc strategyDocCellHtml(docs: StrategyDocs, text: string): string =
  ## Renders one strategy-doc table cell.
  if text in docs.headings:
    return linkHtml("#" & text.slugify(), text)
  text.htmlEscape()

proc renderMarkdownTableHeader(
  html: var string,
  docs: StrategyDocs,
  cells: openArray[string]
) =
  ## Renders one markdown table header row.
  html.add "<table class=\"report-table wide\">\n<thead><tr>"
  for cell in cells:
    html.add "<th>"
    html.add docs.strategyDocCellHtml(cell)
    html.add "</th>"
  html.add "</tr></thead>\n<tbody>\n"

proc renderMarkdownTableRow(
  html: var string,
  docs: StrategyDocs,
  cells: openArray[string]
) =
  ## Renders one markdown table body row.
  html.add "<tr>"
  for cell in cells:
    html.add "<td>"
    html.add docs.strategyDocCellHtml(cell)
    html.add "</td>"
  html.add "</tr>\n"

proc renderStrategyMarkdown(docs: StrategyDocs): string =
  ## Renders the strategy markdown document as report HTML.
  var
    paragraph: seq[string]
    tableOpen = false
    tableHasHeader = false
  result.add "<section>\n"
  result.add "<p><a href=\"index.html\">tournament index</a></p>\n"
  for line in docs.markdown.splitLines():
    let
      heading = line.markdownHeading()
      cells = line.splitMarkdownRow()
    if heading.title.len > 0:
      result.closeMarkdownParagraph(paragraph)
      result.closeMarkdownTable(tableOpen)
      tableHasHeader = false
      result.add "<h" & $heading.level & " id=\""
      result.add heading.title.slugify().htmlEscape()
      result.add "\">" & heading.title.htmlEscape()
      result.add "</h" & $heading.level & ">\n"
    elif cells.len > 0:
      result.closeMarkdownParagraph(paragraph)
      if cells.isMarkdownSeparator():
        continue
      if not tableOpen:
        tableOpen = true
        tableHasHeader = true
        result.renderMarkdownTableHeader(docs, cells)
      elif tableHasHeader:
        result.renderMarkdownTableRow(docs, cells)
      else:
        result.renderMarkdownTableHeader(docs, cells)
        tableHasHeader = true
    elif line.strip().len == 0:
      result.closeMarkdownParagraph(paragraph)
      result.closeMarkdownTable(tableOpen)
      tableHasHeader = false
    else:
      paragraph.add line.strip()
  result.closeMarkdownParagraph(paragraph)
  result.closeMarkdownTable(tableOpen)
  result.add "</section>\n"

proc writeStrategyPage(config: ToolConfig, docs: StrategyDocs) =
  ## Writes the strategy reference page.
  if docs.markdown.len == 0:
    return
  var html = pageStart("Crewrift Prime Strategies", "tufte.css")
  html.add docs.renderStrategyMarkdown()
  html.add pageEnd()
  writeFileRetry(config.outDir / "strategy.html", html)

proc softmaxDetailUrl(kind, id: string): string =
  ## Returns one Observatory detail URL.
  let tab =
    case kind
    of "policy-version":
      "uploads"
    of "episode-request":
      "episodes"
    of "round":
      "leagues"
    else:
      "overview"
  SoftmaxObservatoryUrl & "#tab=" & tab & "&detail=" & kind & ":" & id

proc policyPage(policy: PolicyRef): string =
  ## Returns the relative page path for one policy.
  "bots/" & policy.label.slugify() & ".html"

proc gamePage(game: Game): string =
  ## Returns the relative page path for one game.
  "games/" & game.id.slugify() & ".html"

proc roundPage(roundNumber: int): string =
  ## Returns the relative page path for one round.
  "rounds/round-" & $roundNumber & ".html"

proc replayPath(config: ToolConfig, game: Game): string =
  ## Returns the local replay artifact path for one game.
  config.outDir / "replays" / (game.id.slugify() & ".z")

proc localReplayHref(game: Game): string =
  ## Returns the relative replay href from a game page.
  "../replays/" & game.id.slugify() & ".z"

proc labelsText(game: Game): string =
  ## Formats participant labels for a compact table cell.
  var labels: seq[string]
  for participant in game.participants:
    labels.add participant.label
  labels.join(" vs ")

proc displayLabel(participant: Participant): string =
  ## Returns the best display label for one participant.
  if participant.label.len > 0:
    return participant.label
  if participant.playerName.len > 0:
    return participant.playerName
  if participant.policyId.len > 0:
    return participant.policyId.shortId()
  "unknown"

proc roundLabelKey(participant: Participant): string =
  ## Returns the stable label key for one round participant.
  if participant.policyId.len > 0:
    return participant.policyId
  participant.displayLabel()

proc readableBotLabel(label: string): string =
  ## Normalizes one bot label for compact display.
  label.replace(":", " ").replace("-", " ").replace("_", " ").
    splitWhitespace().join(" ")

proc stripGamePrefix(label: string): string =
  ## Removes a Crewrift-style game prefix when present.
  let parts = label.splitWhitespace()
  var offset = 0
  if parts.len > 0:
    let first = parts[0].toLowerAscii()
    if first == "crewrift":
      offset = 1
  if offset <= 0 or offset >= parts.len:
    return label
  parts[offset .. ^1].join(" ")

proc isVersionToken(token: string): bool =
  ## Returns true when one token looks like a version suffix.
  if token.len < 2 or token[0].toLowerAscii() != 'v':
    return false
  for i in 1 ..< token.len:
    if token[i] notin {'0' .. '9'}:
      return false
  true

proc stripVersionToken(label: string): string =
  ## Removes one trailing version token when present.
  var parts = label.splitWhitespace()
  if parts.len == 0 or not parts[^1].isVersionToken():
    return label
  parts.setLen(parts.len - 1)
  if parts.len == 0:
    return label
  parts.join(" ")

proc removeLabelToken(label, token: string): string =
  ## Removes one filler token without reducing a label to empty.
  var parts: seq[string]
  for part in label.splitWhitespace():
    if part.toLowerAscii() != token:
      parts.add part
  if parts.len == 0:
    return label
  parts.join(" ")

proc shortenRoundLabel(label: string, rule: RoundLabelRule): string =
  ## Applies one round-label shortening rule.
  case rule
  of StripGamePrefix:
    stripGamePrefix(label)
  of StripVersionToken:
    stripVersionToken(label)
  of StripScriptedToken:
    removeLabelToken(label, "scripted")
  of StripBaselineToken:
    removeLabelToken(label, "baseline")

proc labelsAreUnique(labels: Table[string, string]): bool =
  ## Returns true when all non-empty labels are unique.
  var seen: HashSet[string]
  for label in labels.values:
    let key = label.toLowerAscii()
    if key.len == 0 or key in seen:
      return false
    seen.incl key
  true

proc applyRoundLabelRule(
  labels: var Table[string, string],
  rule: RoundLabelRule
) =
  ## Applies one shortening rule when it remains collision-free.
  var next = labels
  for key, label in labels:
    next[key] = label.shortenRoundLabel(rule)
  if next.labelsAreUnique():
    labels = next

proc roundFullLabels(games: openArray[Game]): Table[string, string] =
  ## Returns full labels keyed by participant identity for one round.
  for game in games:
    for participant in game.participants:
      let key = participant.roundLabelKey()
      if key.len == 0 or result.hasKey(key):
        continue
      result[key] = participant.displayLabel()

proc roundShortLabels(
  fullLabels: Table[string, string]
): Table[string, string] =
  ## Returns collision-safe short labels for one round.
  for key, label in fullLabels:
    result[key] = label.readableBotLabel()
  for rule in [
    StripGamePrefix,
    StripVersionToken,
    StripScriptedToken,
    StripBaselineToken
  ]:
    result.applyRoundLabelRule(rule)

proc renderReplayHtml(
  game: Game,
  replayPath,
  replayHref: string
): string =
  ## Renders replay metadata and the extracted game event log.
  var labels: seq[string]
  for participant in game.participants:
    let slot = participant.position
    if slot < 0:
      continue
    while labels.len <= slot:
      labels.add ""
    labels[slot] = participant.displayLabel()
  result.add "<section>\n"
  result.add "<h1>Crewrift Prime Game</h1>\n"
  result.add "<ul>\n"
  result.add "<li>Episode: "
  result.add linkHtml(
    softmaxDetailUrl("episode-request", game.id),
    game.id.shortId()
  )
  result.add "</li>\n"
  result.add "<li>Status: " & game.status.htmlEscape() & "</li>\n"
  result.add "<li>Round: " & $game.roundNumber & "</li>\n"
  if game.error.len > 0:
    result.add "<li>Error: " & game.error.htmlEscape() & "</li>\n"
  result.add "</ul>\n"
  result.add "</section>\n"
  result.add "<section>\n"
  result.add "<h2>Replay artifact</h2>\n"
  result.add "<p><a href=\"" & replayHref.htmlEscape()
  result.add "\">Downloaded replay</a></p>\n"
  result.add "</section>\n"
  result.add renderReplayHtmlForPath(replayPath, replayHref, labels)

proc downloadReplay(game: Game, path: string): string =
  ## Downloads one replay artifact and returns an error summary on failure.
  if game.replayUrl.len == 0 or fileExists(path):
    return
  let tmpPath = path & ".download"
  if fileExists(tmpPath):
    removeFile(tmpPath)
  try:
    discard runCommand(
      [
        "curl",
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--output",
        tmpPath,
        game.replayUrl
      ]
    )
    moveFile(tmpPath, path)
  except CatchableError as e:
    result = e.msg.shortCommandError()
    if fileExists(tmpPath):
      removeFile(tmpPath)

proc renderPendingGame(game: Game): string =
  ## Renders one game page when no replay is available.
  result.add "<section>\n"
  result.add "<h1>Crewrift Prime Game</h1>\n"
  result.add "<ul>\n"
  result.add "<li>Episode: "
  result.add linkHtml(
    softmaxDetailUrl("episode-request", game.id),
    game.id.shortId()
  )
  result.add "</li>\n"
  result.add "<li>Status: " & game.status.htmlEscape() & "</li>\n"
  result.add "<li>Round: " & $game.roundNumber & "</li>\n"
  if game.replayUrl.len > 0:
    result.add "<li><a href=\"" & game.replayUrl.htmlEscape()
    result.add "\">Replay artifact</a></li>\n"
  else:
    result.add "<li>Replay artifact: unavailable</li>\n"
  if game.error.len > 0:
    result.add "<li>Error: " & game.error.htmlEscape() & "</li>\n"
  result.add "</ul>\n"
  result.add "</section>\n"

proc gamePageIsFresh(gameFile, replayFile: string): bool =
  ## Returns true when an existing game page is newer than its replay.
  if not fileExists(gameFile):
    return false
  if fileExists(replayFile):
    return getLastModificationTime(gameFile) >=
      getLastModificationTime(replayFile)
  true

proc renderGamePage(
  config: ToolConfig,
  game: Game,
  stats: var GamePageStats
): bool =
  ## Writes one game page and returns true when it was already fresh.
  let
    replayFile = config.replayPath(game)
    gameFile = config.outDir / game.gamePage()
    hadReplay = fileExists(replayFile)
  var replayError = ""
  if config.downloadReplays:
    replayError = game.downloadReplay(replayFile)
    if replayError.len > 0:
      inc stats.downloadErrors
    elif not hadReplay and fileExists(replayFile):
      inc stats.downloaded

  inc stats.checked
  if not config.rebuild and gamePageIsFresh(gameFile, replayFile):
    inc stats.skipped
    return true

  var html = pageStart("Tournament game " & game.id.shortId(), "../tufte.css")
  html.add "<p><a href=\"../index.html\">tournament index</a></p>\n"
  if fileExists(replayFile):
    html.add renderReplayHtml(game, replayFile, game.localReplayHref())
    inc stats.replayPages
  else:
    inc stats.pendingPages
    html.add renderPendingGame(game)
    if replayError.len > 0:
      html.add "<section><h2>Replay download pending</h2><p>"
      html.add replayError.htmlEscape() & "</p></section>\n"
  html.add pageEnd()
  writeFileRetry(gameFile, html)
  inc stats.rendered

proc renderGamePages(
  config: ToolConfig,
  games,
  freshGames: openArray[Game]
) =
  ## Writes static pages for fresh games plus a bounded cache check.
  createDir(config.outDir / "games")
  createDir(config.outDir / "replays")
  echo "Checking ", games.len, " game pages..."
  let startTime = epochTime()
  var
    stats: GamePageStats
    processed: HashSet[string]
    priority: seq[Game]
    priorityIds: HashSet[string]
    ordered: seq[Game]
  for game in freshGames:
    if priorityIds.contains(game.id):
      continue
    priorityIds.incl game.id
    priority.add game
  for game in games:
    if game.status == "completed" or priorityIds.contains(game.id):
      continue
    priorityIds.incl game.id
    priority.add game
  for game in priority:
    discard renderGamePage(config, game, stats)
    processed.incl game.id

  for game in games:
    ordered.add game
  ordered.sort do (a, b: Game) -> int:
    let byRound = cmp(b.roundNumber, a.roundNumber)
    if byRound != 0:
      byRound
    else:
      cmp(a.id, b.id)

  var freshStreak = 0
  for game in ordered:
    if processed.contains(game.id):
      continue
    if not config.rebuild and freshStreak >= FreshGameSkipLimit:
      stats.limited = games.len - processed.len
      break
    let fresh = renderGamePage(config, game, stats)
    processed.incl game.id
    if not config.rebuild and fresh and game.status == "completed":
      inc freshStreak
    else:
      freshStreak = 0

  echo "Checked ", stats.checked, "/", games.len, " game pages (",
    stats.rendered, " rendered, ", stats.skipped, " skipped, ",
    stats.replayPages, " replay, ", stats.pendingPages, " pending, ",
    stats.parseErrors, " parse errors, ", stats.downloaded,
    " downloaded, ", stats.downloadErrors, " download errors) in ",
    numberText(epochTime() - startTime, 1), "s."
  if stats.limited > 0:
    echo "Kept ", stats.limited, " older completed game pages from cache."

proc sortedPolicies(policies: Table[string, PolicyRef]): seq[PolicyRef] =
  ## Returns policies sorted by version-ish label.
  for policy in policies.values:
    result.add policy
  result.sort do (a, b: PolicyRef) -> int:
    cmp(a.label, b.label)

proc sortedPoliciesByScore(
  policies: Table[string, PolicyRef],
  stats: Table[string, PolicyStats]
): seq[PolicyRef] =
  ## Returns policies sorted by sample bucket, then average score.
  for policy in policies.values:
    result.add policy
  result.sort do (a, b: PolicyRef) -> int:
    let
      aStats = stats.getOrDefault(a.id)
      bStats = stats.getOrDefault(b.id)
      aBucket =
        if aStats.games >= StablePolicyGames:
          0
        else:
          1
      bBucket =
        if bStats.games >= StablePolicyGames:
          0
        else:
          1
      aScore = aStats.avgScore()
      bScore = bStats.avgScore()
    if aBucket != bBucket:
      cmp(aBucket, bBucket)
    elif aScore > bScore + ScoreEpsilon:
      -1
    elif aScore + ScoreEpsilon < bScore:
      1
    else:
      let byLabel = cmp(a.label, b.label)
      if byLabel != 0:
        byLabel
      else:
        cmp(a.id, b.id)

proc sortedGames(games: openArray[Game]): seq[Game] =
  ## Returns games sorted by newest round first.
  for game in games:
    result.add game
  result.sort do (a, b: Game) -> int:
    let byRound = cmp(b.roundNumber, a.roundNumber)
    if byRound != 0:
      byRound
    else:
      cmp(a.id, b.id)

proc sortedGamesByPolicyScore(
  games: openArray[Game],
  policyId: string
): seq[Game] =
  ## Returns games sorted by one policy score.
  for game in games:
    result.add game
  result.sort do (a, b: Game) -> int:
    let
      aScore = a.scoreFor(policyId)
      bScore = b.scoreFor(policyId)
    if aScore.found and bScore.found:
      if aScore.score > bScore.score + ScoreEpsilon:
        -1
      elif aScore.score + ScoreEpsilon < bScore.score:
        1
      else:
        let byRound = cmp(b.roundNumber, a.roundNumber)
        if byRound != 0:
          byRound
        else:
          cmp(a.id, b.id)
    elif aScore.found:
      -1
    elif bScore.found:
      1
    else:
      let byRound = cmp(b.roundNumber, a.roundNumber)
      if byRound != 0:
        byRound
      else:
        cmp(a.id, b.id)

proc gamesForPolicy(games: openArray[Game], policyId: string): seq[Game] =
  ## Returns games that include one policy.
  for game in games:
    for participant in game.participants:
      if participant.policyId == policyId:
        result.add game
        break

proc roundGamesByNumber(
  games: openArray[Game]
): OrderedTable[int, seq[Game]] =
  ## Returns report games grouped by newest round number first.
  for game in games.sortedGames():
    if game.roundNumber < 0:
      continue
    if not result.hasKey(game.roundNumber):
      result[game.roundNumber] = @[]
    result[game.roundNumber].add game

proc policiesForGames(
  policies: Table[string, PolicyRef],
  games: openArray[Game]
): Table[string, PolicyRef] =
  ## Returns policies that appear in the supplied games.
  var seen: HashSet[string]
  for game in games:
    for participant in game.participants:
      let id = participant.policyId
      if id.len == 0 or id in seen:
        continue
      seen.incl id
      if policies.hasKey(id):
        result[id] = policies[id]
      else:
        result[id] = participant.policyFromParticipant()

proc recentRoundSet(
  games: openArray[Game],
  maxRounds: int
): HashSet[int] =
  ## Returns the newest round numbers present in a game list.
  var count = 0
  for roundNumber, roundGames in games.roundGamesByNumber():
    if roundGames.len == 0:
      continue
    result.incl roundNumber
    inc count
    if count >= maxRounds:
      break

proc currentGames(games: openArray[Game]): seq[Game] =
  ## Returns games from the newest current rounds.
  let roundNumbers = games.recentRoundSet(CurrentRoundCount)
  for game in games:
    if game.roundNumber in roundNumbers:
      result.add game

proc roundNumbersForPolicy(
  games: openArray[Game],
  policyId: string
): HashSet[int] =
  ## Returns completed non-qualifier round numbers that include one policy.
  for game in games:
    if not game.isReportGame() or game.status != "completed":
      continue
    for participant in game.participants:
      if participant.policyId == policyId:
        result.incl game.roundNumber
        break

proc gamesInPolicyRounds(
  games: openArray[Game],
  policyId: string
): seq[Game] =
  ## Returns games from completed round numbers where one policy appeared.
  let roundNumbers = games.roundNumbersForPolicy(policyId)
  for game in games:
    if not game.isReportGame() or game.status != "completed":
      continue
    if game.roundNumber in roundNumbers:
      result.add game

proc heatLabels(
  policies: Table[string, PolicyRef],
  games: openArray[Game],
  includeMissing = true
): Table[string, string] =
  ## Returns display labels for policies in the game cache.
  for game in games:
    for participant in game.participants:
      if participant.policyId.len == 0:
        continue
      if policies.hasKey(participant.policyId):
        let policy = policies[participant.policyId]
        if policy.label.len > 0:
          result[participant.policyId] = policy.label
          continue
      if not includeMissing:
        continue
      let missing =
        not result.hasKey(participant.policyId) or
        result[participant.policyId].len == 0
      if missing:
        result[participant.policyId] = participant.displayLabel()

proc heatScoreStats(games: openArray[Game]): Table[string, PolicyStats] =
  ## Returns average-score inputs for every scored policy.
  for game in games:
    if game.status != "completed":
      continue
    for score in game.scores:
      if score.policyId.len == 0:
        continue
      var stats = result.getOrDefault(score.policyId)
      inc stats.scoredGames
      stats.totalScore += score.score
      result[score.policyId] = stats

proc heatFamilyKey(participant: Participant): string =
  ## Returns the heat-map family key for one participant.
  if participant.label.len > 0:
    return participant.label.heatFamilyLabel().toLowerAscii()
  if participant.playerName.len > 0:
    return participant.playerName.heatFamilyLabel().toLowerAscii()
  participant.policyId.toLowerAscii()

proc heatFamilyKey(game: Game, policyId: string): string =
  ## Returns the heat-map family key for a policy in one game.
  for participant in game.participants:
    if participant.policyId == policyId:
      return participant.heatFamilyKey()
  policyId.toLowerAscii()

proc heatFamilyLabels(
  policies: Table[string, PolicyRef],
  games: openArray[Game],
  includeMissing = true
): Table[string, string] =
  ## Returns display labels for versionless heat-map families.
  for game in games:
    for participant in game.participants:
      if participant.policyId.len == 0:
        continue
      let key = participant.heatFamilyKey()
      if key.len == 0:
        continue
      var label = ""
      if policies.hasKey(participant.policyId):
        label = policies[participant.policyId].label
      if label.len == 0:
        if not includeMissing:
          continue
        label = participant.displayLabel()
      let clean = label.heatFamilyLabel()
      if clean.len == 0:
        continue
      let missing =
        not result.hasKey(key) or
        result[key].len == 0
      if missing:
        result[key] = clean

proc heatFamilyScoreStats(
  games: openArray[Game]
): Table[string, PolicyStats] =
  ## Returns average-score inputs for versionless heat-map families.
  for game in games:
    if game.status != "completed":
      continue
    for score in game.scores:
      if score.policyId.len == 0:
        continue
      let key = game.heatFamilyKey(score.policyId)
      if key.len == 0:
        continue
      var stats = result.getOrDefault(key)
      inc stats.scoredGames
      stats.totalScore += score.score
      result[key] = stats

proc heatPolicy(
  id: string,
  labels: Table[string, string],
  stats: Table[string, PolicyStats]
): HeatPolicy =
  ## Returns one heat-map policy from a label table.
  let
    label = labels.getOrDefault(id)
    policyStats = stats.getOrDefault(id)
  result.id = id
  result.score = policyStats.avgScore()
  result.scoredGames = policyStats.scoredGames
  result.label =
    if label.len > 0:
      label
    else:
      id.shortId()

proc heatPolicies(
  labels: Table[string, string],
  stats: Table[string, PolicyStats]
): seq[HeatPolicy] =
  ## Returns all heat-map policies sorted by average score.
  for id in labels.keys:
    result.add heatPolicy(id, labels, stats)
  result.sort do (a, b: HeatPolicy) -> int:
    if a.score > b.score + ScoreEpsilon:
      -1
    elif a.score + ScoreEpsilon < b.score:
      1
    else:
      let byLabel = cmp(a.label, b.label)
      if byLabel != 0:
        byLabel
      else:
        cmp(a.id, b.id)

proc heatKey(rowId, columnId: string): string =
  ## Returns a table key for one heat-map matchup.
  rowId & "\t" & columnId

proc addHeatResult(stats: var HeatStats, margin: float) =
  ## Adds one score comparison to a heat-map cell.
  if margin > ScoreEpsilon:
    inc stats.wins
  elif margin < -ScoreEpsilon:
    inc stats.losses
  else:
    inc stats.ties

proc heatGames(stats: HeatStats): int =
  ## Returns the number of scored matchups in one heat cell.
  stats.wins + stats.losses + stats.ties

proc heatRate(stats: HeatStats): float =
  ## Returns the tie-adjusted win rate for one heat cell.
  let games = stats.heatGames()
  if games == 0:
    return 0.0
  (stats.wins.float + stats.ties.float * 0.5) / games.float

proc heatScoreEntries(game: Game): seq[tuple[key: string, score: float]] =
  ## Returns scored versionless heat-map entries for one game.
  for score in game.scores:
    if score.policyId.len == 0:
      continue
    let key = game.heatFamilyKey(score.policyId)
    if key.len == 0:
      continue
    result.add (key: key, score: score.score)

proc computeHeat(games: openArray[Game]): Table[string, HeatStats] =
  ## Computes pairwise family head-to-head results from completed games.
  for game in games:
    if game.status != "completed":
      continue
    let entries = game.heatScoreEntries()
    for i in 0 ..< entries.len:
      for j in 0 ..< entries.len:
        if i == j or entries[i].key == entries[j].key:
          continue
        let key = heatKey(entries[i].key, entries[j].key)
        var stats = result.getOrDefault(key)
        stats.addHeatResult(entries[i].score - entries[j].score)
        result[key] = stats

proc heatShade(rate: float): int =
  ## Returns one gray shade for a heat-map win rate.
  255 - int(rate * 255.0 + 0.5)

proc heatTitle(row, column: HeatPolicy, stats: HeatStats): string =
  ## Returns the tooltip text for one heat-map cell.
  let text = numberText(stats.heatRate() * 100.0, 0) & "%"
  row.label & " vs " & column.label & ": " &
    $stats.wins & "-" & $stats.losses & "-" & $stats.ties &
    ", " & text

proc heatTextColor(rate: float): string =
  ## Returns a readable text color for one heat-map tile.
  if rate >= 0.25:
    "#fff"
  else:
    "#111"

proc maxHeatGames(
  heat: Table[string, HeatStats],
  rows: openArray[HeatPolicy]
): int =
  ## Returns the largest matchup count in a heat-map matrix.
  for row in rows:
    for column in rows:
      if row.id == column.id:
        continue
      let games = heat.getOrDefault(heatKey(row.id, column.id)).heatGames()
      result = max(result, games)

proc heatCountShade(games, maxGames: int): int =
  ## Returns one gray shade for a matchup-count heat-map tile.
  if maxGames <= 0:
    return 255
  let rate = games.float / maxGames.float
  255 - int(rate * 255.0 + 0.5)

proc heatCountTextColor(games, maxGames: int): string =
  ## Returns a readable text color for one count heat-map tile.
  if maxGames > 0 and games.float / maxGames.float >= 0.45:
    "#fff"
  else:
    "#111"

proc heatCountTitle(row, column: HeatPolicy, games: int): string =
  ## Returns tooltip text for one matchup-count heat-map cell.
  let noun =
    if games == 1:
      " game"
    else:
      " games"
  row.label & " vs " & column.label & ": " & $games & noun

proc heatRectSvg(
  row: HeatPolicy,
  column: HeatPolicy,
  heat: Table[string, HeatStats],
  x,
  y: int
): string =
  ## Renders one SVG heat-map tile.
  if row.id == column.id:
    return
  let stats = heat.getOrDefault(heatKey(row.id, column.id))
  let games = stats.heatGames()
  if games == 0:
    return
  let
    rate = stats.heatRate()
    shade = rate.heatShade()
    percent = numberText(rate * 100.0, 0) & "%"
    textX = x + HeatCellSize div 2
    textY = y + HeatCellSize div 2 + 4
  result.add "<rect x=\"" & $x & "\" y=\"" & $y
  result.add "\" width=\"" & $HeatCellSize
  result.add "\" height=\"" & $HeatCellSize
  result.add "\" fill=\"rgb(" & $shade & "," & $shade
  result.add "," & $shade & ")\"><title>"
  result.add heatTitle(row, column, stats).htmlEscape()
  result.add "</title></rect>\n"
  result.add "<text class=\"heat-rate\" x=\"" & $textX
  result.add "\" y=\"" & $textY
  result.add "\" text-anchor=\"middle\" fill=\"" & rate.heatTextColor()
  result.add "\">" & percent.htmlEscape() & "</text>\n"

proc heatCountRectSvg(
  row: HeatPolicy,
  column: HeatPolicy,
  heat: Table[string, HeatStats],
  maxGames: int,
  x,
  y: int
): string =
  ## Renders one SVG matchup-count tile.
  if row.id == column.id:
    return
  let
    games = heat.getOrDefault(heatKey(row.id, column.id)).heatGames()
    shade = heatCountShade(games, maxGames)
    textX = x + HeatCellSize div 2
    textY = y + HeatCellSize div 2 + 4
  result.add "<rect x=\"" & $x & "\" y=\"" & $y
  result.add "\" width=\"" & $HeatCellSize
  result.add "\" height=\"" & $HeatCellSize
  result.add "\" fill=\"rgb(" & $shade & "," & $shade
  result.add "," & $shade & ")\"><title>"
  result.add heatCountTitle(row, column, games).htmlEscape()
  result.add "</title></rect>\n"
  if games > 0:
    result.add "<text class=\"heat-rate\" x=\"" & $textX
    result.add "\" y=\"" & $textY
    result.add "\" text-anchor=\"middle\" fill=\""
    result.add heatCountTextColor(games, maxGames)
    result.add "\">" & $games & "</text>\n"

proc renderHeatMap(
  policies: Table[string, PolicyRef],
  games: openArray[Game],
  includeMissing = true
): string =
  ## Renders the full policy matchup heat map.
  let
    heat = computeHeat(games)
    labels = heatFamilyLabels(policies, games, includeMissing)
    scores = heatFamilyScoreStats(games)
    heatRows = heatPolicies(labels, scores)
  if heatRows.len == 0:
    return "<p>No completed matchup data yet.</p>\n"
  let
    gridX = HeatLeftLabelWidth
    gridY = HeatTopLabelHeight
    gridWidth = heatRows.len * HeatCellSize
    width = HeatLeftLabelWidth + gridWidth + HeatRightLabelWidth
    height = gridY + heatRows.len * HeatCellSize + HeatPadding
  result.add "<div class=\"heat-wrap\">\n"
  result.add "<svg class=\"heatmap-svg\" width=\"" & $width
  result.add "\" height=\"" & $height & "\" viewBox=\"0 0 "
  result.add $width & " " & $height
  result.add "\" role=\"img\" aria-label=\"Policy matchup heat map\">\n"
  for i, policy in heatRows:
    let
      x = gridX + i * HeatCellSize + HeatCellSize div 2
      y = gridY - 8
    result.add "<text class=\"heat-label\" x=\"" & $x
    result.add "\" y=\"" & $y
    result.add "\" text-anchor=\"start\" transform=\"rotate(-45 "
    result.add $x & " " & $y & ")\">"
    result.add policy.label.htmlEscape()
    result.add "</text>\n"
  for i, row in heatRows:
    let y = gridY + i * HeatCellSize + HeatCellSize div 2 + 4
    result.add "<text class=\"heat-label\" x=\"" & $(gridX - 8)
    result.add "\" y=\"" & $y & "\" text-anchor=\"end\">"
    result.add row.label.htmlEscape()
    result.add "</text>\n"
  for i, row in heatRows:
    for j, column in heatRows:
      let
        x = gridX + j * HeatCellSize
        y = gridY + i * HeatCellSize
      result.add heatRectSvg(row, column, heat, x, y)
  result.add "</svg>\n</div>\n"

proc renderHeatCountMap(
  policies: Table[string, PolicyRef],
  games: openArray[Game],
  includeMissing = true
): string =
  ## Renders the full policy matchup count heat map.
  let
    heat = computeHeat(games)
    labels = heatFamilyLabels(policies, games, includeMissing)
    scores = heatFamilyScoreStats(games)
    heatRows = heatPolicies(labels, scores)
    maxGames = heat.maxHeatGames(heatRows)
  if heatRows.len == 0:
    return "<p>No completed matchup game-count data yet.</p>\n"
  let
    gridX = HeatLeftLabelWidth
    gridY = HeatTopLabelHeight
    gridWidth = heatRows.len * HeatCellSize
    width = HeatLeftLabelWidth + gridWidth + HeatRightLabelWidth
    height = gridY + heatRows.len * HeatCellSize + HeatPadding
  result.add "<div class=\"heat-wrap\">\n"
  result.add "<svg class=\"heatmap-svg\" width=\"" & $width
  result.add "\" height=\"" & $height & "\" viewBox=\"0 0 "
  result.add $width & " " & $height
  result.add "\" role=\"img\" aria-label=\"Policy matchup count heat map\">\n"
  for i, policy in heatRows:
    let
      x = gridX + i * HeatCellSize + HeatCellSize div 2
      y = gridY - 8
    result.add "<text class=\"heat-label\" x=\"" & $x
    result.add "\" y=\"" & $y
    result.add "\" text-anchor=\"start\" transform=\"rotate(-45 "
    result.add $x & " " & $y & ")\">"
    result.add policy.label.htmlEscape()
    result.add "</text>\n"
  for i, row in heatRows:
    let y = gridY + i * HeatCellSize + HeatCellSize div 2 + 4
    result.add "<text class=\"heat-label\" x=\"" & $(gridX - 8)
    result.add "\" y=\"" & $y & "\" text-anchor=\"end\">"
    result.add row.label.htmlEscape()
    result.add "</text>\n"
  for i, row in heatRows:
    for j, column in heatRows:
      let
        x = gridX + j * HeatCellSize
        y = gridY + i * HeatCellSize
      result.add heatCountRectSvg(row, column, heat, maxGames, x, y)
  result.add "</svg>\n</div>\n"

proc chartOutcome(resultText: string): ScoreChartOutcome =
  ## Converts one W/L/T marker into a chart outcome.
  case resultText
  of "W":
    ScoreWin
  of "T":
    ScoreTie
  else:
    ScoreLoss

proc scorePointsByPolicy(
  policies: Table[string, PolicyRef],
  games: openArray[Game],
  includeMissing = true
): seq[ScoreChartPoint] =
  ## Returns scored chart points grouped by policy id.
  let labels = heatLabels(policies, games, includeMissing)
  for game in games:
    if game.status != "completed":
      continue
    for score in game.scores:
      if score.policyId.len == 0:
        continue
      let resultText = game.policyResult(score.policyId)
      if resultText == "-":
        continue
      let
        label = labels.getOrDefault(score.policyId, score.policyId.shortId())
        outcome = resultText.chartOutcome()
        title = label & " " & numberText(score.score, 2) & ", " &
          outcome.scoreChartOutcomeText() & ", " & game.id.shortId()
      result.add ScoreChartPoint(
        rowId: score.policyId,
        score: score.score,
        outcome: outcome,
        title: title,
        sortKey: game.id
      )

proc renderScorePlot(
  policies: Table[string, PolicyRef],
  games: openArray[Game],
  includeMissing = true
): string =
  ## Renders a per-policy score dot plot.
  let
    labels = heatLabels(policies, games, includeMissing)
    scores = heatScoreStats(games)
    rows = heatPolicies(labels, scores)
    points = scorePointsByPolicy(policies, games, includeMissing)
  var chartRows: seq[ScoreChartRow]
  for row in rows:
    chartRows.add ScoreChartRow(id: row.id, label: row.label)
  renderScoreChart(chartRows, points)

proc scoreText(game: Game, policyId: string): string =
  ## Returns one policy score for a game.
  let score = game.scoreFor(policyId)
  if score.found:
    return numberText(score.score, 2)
  "-"

proc participantPolicyLinkHtml(
  policies: Table[string, PolicyRef],
  participant: Participant,
  displayText,
  relativePrefix: string
): string =
  ## Renders one participant's policy link or label.
  let
    fullLabel = participant.displayLabel()
    title =
      if fullLabel != displayText:
        fullLabel
      else:
        ""
  if policies.hasKey(participant.policyId):
    return linkHtml(
      relativePrefix & policies[participant.policyId].policyPage(),
      displayText,
      title
    )
  spanHtml(displayText, title)

proc participantAt(game: Game, position: int): Participant =
  ## Returns the participant in one player slot.
  for participant in game.participants:
    if participant.position == position:
      return participant

proc bestScore(game: Game): tuple[found: bool, score: float] =
  ## Returns the best score in one completed game.
  if game.status != "completed":
    return
  for score in game.scores:
    if not result.found or score.score > result.score:
      result.found = true
      result.score = score.score

proc winningScore(game: Game, value: float): bool =
  ## Returns true when a score is tied for best in one game.
  let best = game.bestScore()
  best.found and value + ScoreEpsilon >= best.score

proc scoreCell(game: Game, participant: Participant): string =
  ## Renders one score cell, marking the winning score.
  let score = game.scoreFor(participant.policyId)
  if not score.found:
    return "-".numCell()
  let text = numberText(score.score, 2)
  if game.winningScore(score.score):
    return classHtmlCell(markHtml(text), "num")
  text.numCell()

proc roundPlayerLetter(position: int): string =
  ## Returns the display letter for one round player slot.
  if position >= 0 and position < RoundPlayerLetters.len:
    return RoundPlayerLetters[position]
  $(position + 1)

proc playerCell(
  game: Game,
  policies: Table[string, PolicyRef],
  shortLabels: Table[string, string],
  position: int,
  relativePrefix: string
): tuple[participant: Participant, html: string] =
  ## Renders one player slot for a round game row.
  result.participant = game.participantAt(position)
  if result.participant.policyId.len == 0 and
    result.participant.label.len == 0 and
    result.participant.playerName.len == 0:
      result.html = "-".classCell("player-cell")
      return
  result.html = classHtmlCell(
    participantPolicyLinkHtml(
      policies,
      result.participant,
      shortLabels.getOrDefault(
        result.participant.roundLabelKey(),
        result.participant.displayLabel().readableBotLabel()
      ),
      relativePrefix
    ),
    "name player-cell"
  )

proc renderGameRow(game: Game, policy: PolicyRef, relativePrefix = ""): string =
  ## Renders one game table row for a bot page.
  let
    resultValue = game.policyResult(policy.id)
    score = scoreText(game, policy.id)
  result.add "<tr>"
  result.add htmlCell(
    linkHtml(relativePrefix & game.gamePage(), game.id.shortId())
  )
  result.add numCell($game.roundNumber)
  result.add game.divisionName.tableCell()
  result.add game.status.tableCell()
  result.add game.labelsText().tableCell()
  result.add score.numCell()
  if resultValue == "W":
    result.add htmlCell(markHtml(resultValue))
  else:
    result.add resultValue.tableCell()
  result.add "</tr>\n"

proc roundStatusText(games: openArray[Game]): string =
  ## Returns a compact status label for a round's games.
  if games.len == 0:
    return "-"
  let status = games[0].status
  for game in games:
    if game.status != status:
      return "mixed"
  if status.len == 0:
    return "-"
  status

proc roundDivisionText(games: openArray[Game]): string =
  ## Returns a compact division label for a round's games.
  if games.len == 0:
    return "-"
  let divisionName = games[0].divisionName
  for game in games:
    if game.divisionName != divisionName:
      return "mixed"
  if divisionName.len == 0:
    return "-"
  divisionName

proc completedGameCount(games: openArray[Game]): int =
  ## Returns the number of completed games in a game list.
  for game in games:
    if game.status == "completed":
      inc result

proc completionRateText(completed, total: int): string =
  ## Returns a completed-over-total percentage.
  if total <= 0:
    return "-"
  numberText(completed.float / total.float * 100.0, 0) & "%"

proc completionRateCell(
  games: openArray[Game],
  latestRound = false
): string =
  ## Renders a completion-rate cell, marking incomplete rounds.
  let
    completed = games.completedGameCount()
    text = completionRateText(completed, games.len)
  if latestRound or (games.len > 0 and completed == games.len):
    return text.numCell()
  classHtmlCell(markHtml(text), "num")

proc roundTimeSource(round: CachedRound, games: openArray[Game]): string =
  ## Returns the best timestamp available for a round.
  if round.startedAt.len > 0:
    return round.startedAt
  for game in games:
    if game.createdAt.len == 0:
      continue
    if result.len == 0 or game.createdAt < result:
      result = game.createdAt
  if result.len == 0:
    result = round.completedAt

proc roundTimeText(iso: string): string =
  ## Formats an ISO timestamp as a compact am/pm time.
  if iso.len == 0:
    return "-"
  for timestampFormat in RoundTimestampFormats:
    try:
      return parseTs(timestampFormat, iso).format(
        RoundTimeFormat,
        tzName = RoundTimeZone
      )
    except ValueError:
      discard
  "-"

proc uniquePolicyCount(games: openArray[Game]): int =
  ## Returns the number of unique policies in a game list.
  var seen: HashSet[string]
  for game in games:
    for participant in game.participants:
      if participant.policyId.len > 0:
        seen.incl participant.policyId
  seen.len

proc averageScore(games: openArray[Game]): float =
  ## Returns the average participant score across a game list.
  var count = 0
  for game in games:
    for score in game.scores:
      result += score.score
      inc count
  if count == 0:
    return 0.0
  result / count.float

proc renderRoundIndexTable(
  games: openArray[Game],
  rounds: openArray[CachedRound]
): string =
  ## Renders the top-level round index table.
  let
    byRound = games.roundGamesByNumber()
    byRoundNumber = rounds.roundTableByNumber()
  if byRound.len == 0:
    return "<p>No rounds yet.</p>\n"
  var latestRound = -1
  for roundNumber in byRound.keys:
    latestRound = max(latestRound, roundNumber)
  result.add "<table class=\"report-table wide\">\n"
  result.add "<thead><tr><th>Round</th><th>Time</th><th>Status</th>"
  result.add "<th>Games</th><th>Completed</th>"
  result.add "<th>Bots</th><th>Completion %</th></tr></thead>\n<tbody>\n"
  for roundNumber, roundGames in byRound:
    let
      round = byRoundNumber.getOrDefault(roundNumber)
      time = round.roundTimeSource(roundGames).roundTimeText()
    result.add "<tr>"
    result.add htmlCell(linkHtml(roundNumber.roundPage(), $roundNumber))
    result.add time.classCell("name")
    result.add roundGames.roundStatusText().classCell("name")
    result.add ($roundGames.len).numCell()
    result.add ($roundGames.completedGameCount()).numCell()
    result.add ($roundGames.uniquePolicyCount()).numCell()
    result.add roundGames.completionRateCell(roundNumber == latestRound)
    result.add "</tr>\n"
  result.add "</tbody></table>\n"

proc renderRoundGameRow(
  game: Game,
  policies: Table[string, PolicyRef],
  shortLabels: Table[string, string],
  relativePrefix: string
): string =
  ## Renders one game row for a round page.
  result.add "<tr>"
  result.add classHtmlCell(
    linkHtml(relativePrefix & game.gamePage(), game.id.shortId()),
    "game-cell"
  )
  for position in 0 ..< RoundPlayerSlots:
    let player = game.playerCell(
      policies,
      shortLabels,
      position,
      relativePrefix
    )
    result.add player.html
    result.add game.scoreCell(player.participant)
  result.add "</tr>\n"

proc renderRoundNameKey(
  fullLabels,
  shortLabels: Table[string, string]
): string =
  ## Renders the compact-name key for one round table.
  var entries: seq[RoundLabelEntry]
  for key, fullLabel in fullLabels:
    let shortLabel = shortLabels.getOrDefault(key, fullLabel.readableBotLabel())
    if shortLabel == fullLabel or shortLabel == fullLabel.readableBotLabel():
      continue
    entries.add RoundLabelEntry(
      shortLabel: shortLabel,
      fullLabel: fullLabel
    )
  if entries.len == 0:
    return
  entries.sort(
    proc(a, b: RoundLabelEntry): int =
      cmp(a.shortLabel.toLowerAscii(), b.shortLabel.toLowerAscii())
  )
  result.add "<table class=\"report-table wide no-sort round-name-key\">\n"
  result.add "<colgroup><col class=\"name-col\">"
  result.add "<col class=\"short-col\"></colgroup>\n"
  result.add "<thead><tr><th>Name</th><th>Short name</th></tr></thead>\n"
  result.add "<tbody>\n"
  for entry in entries:
    result.add "<tr>"
    result.add entry.fullLabel.classCell("name")
    result.add entry.shortLabel.classCell("name")
    result.add "</tr>\n"
  result.add "</tbody></table>\n"

proc statusText(policy: PolicyRef): string =
  ## Returns the display status for one policy.
  if policy.isChampion or
    policy.status == "champion" or
    policy.substatus == "champion":
      return "champion"
  if policy.substatus.len > 0:
    return policy.substatus
  policy.status

proc strategyLinkHtml(strategy, relativePrefix: string): string =
  ## Renders one strategy documentation link.
  linkHtml(relativePrefix & "strategy.html#" & strategy.slugify(), strategy)

proc strategyForBot(docs: StrategyDocs, label: string): string =
  ## Returns the assigned strategy for one bot label.
  result = docs.strategies.getOrDefault(label)
  if result.len > 0:
    return
  let clean = label.toLowerAscii()
  if clean == "notsus" or clean.startsWith("notsus:"):
    return "Balanced"

proc renderIndexTable(
  policies: Table[string, PolicyRef],
  stats: Table[string, PolicyStats],
  relativePrefix = "",
  strategyDocs = StrategyDocs(),
  showStrategy = false
): string =
  ## Renders one index policy summary table.
  if policies.len == 0:
    return "<p>No bots yet.</p>\n"
  result.add "<table class=\"report-table wide policy-summary\">\n"
  if showStrategy:
    result.add "<colgroup><col style=\"width:238px\">"
    result.add "<col style=\"width:174px\"><col style=\"width:57px\">"
    result.add "<col style=\"width:48px\"><col style=\"width:55px\">"
    result.add "<col style=\"width:41px\"><col style=\"width:52px\">"
    result.add "<col style=\"width:73px\"></colgroup>\n"
  else:
    result.add "<colgroup><col style=\"width:250px\">"
    result.add "<col style=\"width:162px\"><col style=\"width:57px\">"
    result.add "<col style=\"width:48px\"><col style=\"width:55px\">"
    result.add "<col style=\"width:41px\"><col style=\"width:52px\">"
    result.add "<col style=\"width:73px\"></colgroup>\n"
  if showStrategy:
    result.add "<thead><tr><th>Bot Name</th>"
  else:
    result.add "<thead><tr><th>Bot</th>"
  if showStrategy:
    result.add "<th>Strategy</th>"
  if not showStrategy:
    result.add "<th>Status</th>"
  result.add "<th>Games</th><th>Wins</th>"
  result.add "<th>Losses</th><th>Ties</th>"
  result.add "<th>Win%</th><th>Avg score</th></tr></thead>\n<tbody>\n"
  for policy in sortedPoliciesByScore(policies, stats):
    let
      stat = stats.getOrDefault(policy.id)
      status = policy.statusText()
    if showStrategy and status == "inactive":
      result.add "<tr class=\"inactive-row\">"
    else:
      result.add "<tr>"
    let botClass =
      if showStrategy:
        "bot-name name"
      else:
        "name"
    result.add classHtmlCell(
      linkHtml(relativePrefix & policy.policyPage(), policy.label),
      botClass
    )
    if showStrategy:
      let strategy = strategyDocs.strategyForBot(policy.label)
      if strategy.len > 0:
        result.add classHtmlCell(
          strategy.strategyLinkHtml(relativePrefix),
          "strategy"
        )
      else:
        result.add "-".classCell("strategy")
    if not showStrategy:
      result.add status.classCell("name")
    result.add ($stat.games).numCell()
    result.add ($stat.wins).numCell()
    result.add ($stat.losses).numCell()
    result.add ($stat.ties).numCell()
    result.add stat.winPercentText().numCell()
    result.add numberText(stat.avgScore(), 2).numCell()
    result.add "</tr>\n"
  result.add "</tbody></table>\n"

proc renderIndexGroup(
  title: string,
  policies: Table[string, PolicyRef],
  stats: Table[string, PolicyStats],
  games: openArray[Game],
  scoreTitle = "Scores",
  strategyDocs = StrategyDocs(),
  showStrategy = false,
  showWinRates = false,
  showScores = false
): string =
  ## Renders one grouped index section.
  result.add "<section>\n"
  result.add "<h2>" & title.htmlEscape() & "</h2>\n"
  result.add renderIndexTable(
    policies,
    stats,
    strategyDocs = strategyDocs,
    showStrategy = showStrategy
  )
  if showWinRates:
    result.add "<h3>Win Rates</h3>\n"
    result.add renderWinRateChart(policies, stats)
  if showScores:
    result.add "<h3>" & scoreTitle.htmlEscape() & "</h3>\n"
    result.add renderScorePlot(policies, games, includeMissing = false)
  result.add "<div class=\"hidden-report-section\">\n"
  result.add "<h3>Matchups</h3>\n"
  result.add renderHeatMap(policies, games, includeMissing = false)
  result.add "<h3>Matchup Games</h3>\n"
  result.add renderHeatCountMap(policies, games, includeMissing = false)
  result.add "</div>\n"
  result.add "</section>\n"

proc renderIndex(
  config: ToolConfig,
  policies: Table[string, PolicyRef],
  stats: Table[string, PolicyStats],
  games: openArray[Game],
  rounds: openArray[CachedRound],
  strategyDocs: StrategyDocs
) =
  ## Writes the top-level tournament index page.
  let
    recentGames = games.currentGames()
    currentPolicies = policies.policiesForGames(recentGames)
    currentStats = computeStats(currentPolicies, recentGames)
  var html = pageStart("Crewrift Prime Tournament", "tufte.css")
  html.add autoRefreshControl()
  html.add "<section>\n"
  html.add "<h1>Crewrift Prime Tournament</h1>\n"
  html.add "</section>\n"
  html.add renderIndexGroup(
    "Current Bots (Last 24 rounds)",
    currentPolicies,
    currentStats,
    recentGames,
    "Scores (Last 24 rounds)",
    strategyDocs,
    showStrategy = true,
    showWinRates = true
  )
  html.add "<section>\n"
  html.add "<h2>Rounds</h2>\n"
  html.add renderRoundIndexTable(games, rounds)
  html.add "</section>\n"
  html.add renderIndexGroup(
    "All Bots (All rounds)",
    policies,
    stats,
    games,
    "Scores (All rounds)"
  )

  html.add pageEnd(autoRefresh = true)
  writeFileRetry(config.outDir / "index.html", html)

proc renderBotPage(
  config: ToolConfig,
  policies: Table[string, PolicyRef],
  policy: PolicyRef,
  stats: PolicyStats,
  games: openArray[Game]
) =
  ## Writes one bot detail page with per-round tables.
  createDir(config.outDir / "bots")
  let
    policyGames = gamesForPolicy(games, policy.id)
    botGames = sortedGamesByPolicyScore(policyGames, policy.id)
    roundOrderedGames = sortedGames(policyGames)
    matrixGames = gamesInPolicyRounds(games, policy.id)
  var html = pageStart(policy.label & " tournament", "../tufte.css")
  html.add "<section>\n"
  html.add "<p><a href=\"../index.html\">all bots</a></p>\n"
  html.add "<h1>" & policy.label.htmlEscape() & "</h1>\n"
  html.add "<ul>\n"
  html.add "<li>Player: " & policy.playerName.htmlEscape() & "</li>\n"
  html.add "<li>Policy: "
  html.add linkHtml(softmaxDetailUrl("policy-version", policy.id), policy.id)
  html.add "</li>\n"
  html.add "<li>Games: " & $stats.games & "</li>\n"
  html.add "<li>Record: " & $stats.wins & "-" & $stats.losses
  html.add "-" & $stats.ties & "</li>\n"
  html.add "<li>Average score: " & numberText(stats.avgScore(), 2)
  html.add "</li>\n"
  html.add "</ul>\n"
  html.add "</section>\n"

  html.add "<section>\n"
  html.add "<h2>Scores</h2>\n"
  html.add renderScorePlot(policies, matrixGames)
  html.add "</section>\n"

  html.add "<section class=\"hidden-report-section\">\n"
  html.add "<h2>Matchups</h2>\n"
  html.add renderHeatMap(policies, matrixGames)
  html.add "<h3>Matchup Games</h3>\n"
  html.add renderHeatCountMap(policies, matrixGames)
  html.add "</section>\n"

  html.add "<section>\n"
  html.add "<h2>All games</h2>\n"
  html.add "<table class=\"report-table wide\">\n<thead><tr><th>Game</th><th>Round</th>"
  html.add "<th>Division</th><th>Status</th><th>Players</th>"
  html.add "<th>Score</th><th>Result</th></tr></thead>\n<tbody>\n"
  for game in botGames:
    html.add renderGameRow(game, policy, "../")
  html.add "</tbody></table>\n"
  html.add "</section>\n"

  var byRound: OrderedTable[int, seq[Game]]
  for game in roundOrderedGames:
    if not byRound.hasKey(game.roundNumber):
      byRound[game.roundNumber] = @[]
    byRound[game.roundNumber].add game
  html.add "<section>\n"
  html.add "<h2>Rounds</h2>\n"
  for roundNumber, roundGames in byRound:
    html.add "<h3>Round " & $roundNumber & "</h3>\n"
    html.add "<table class=\"report-table wide\">\n<thead><tr><th>Game</th><th>Round</th>"
    html.add "<th>Division</th><th>Status</th><th>Players</th>"
    html.add "<th>Score</th><th>Result</th></tr></thead>\n<tbody>\n"
    for game in sortedGamesByPolicyScore(roundGames, policy.id):
      html.add renderGameRow(game, policy, "../")
    html.add "</tbody></table>\n"
  html.add "</section>\n"
  html.add pageEnd()
  writeFileRetry(config.outDir / policy.policyPage(), html)

proc renderRoundPage(
  config: ToolConfig,
  policies: Table[string, PolicyRef],
  roundNumber: int,
  games: openArray[Game]
) =
  ## Writes one round detail page.
  let
    roundPolicies = policiesForGames(policies, games)
    stats = computeStats(roundPolicies, games)
    fullLabels = roundFullLabels(games)
    shortLabels = roundShortLabels(fullLabels)
  var html = pageStart("Round " & $roundNumber & " tournament", "../tufte.css")
  html.add "<section>\n"
  html.add "<p><a href=\"../index.html\">all rounds</a></p>\n"
  html.add "<h1>Round " & $roundNumber & "</h1>\n"
  html.add "<ul>\n"
  html.add "<li>Status: " & games.roundStatusText().htmlEscape() & "</li>\n"
  html.add "<li>Division: " & games.roundDivisionText().htmlEscape()
  html.add "</li>\n"
  html.add "<li>Games: " & $games.len & "</li>\n"
  html.add "<li>Completed: " & $games.completedGameCount() & "</li>\n"
  html.add "<li>Bots: " & $games.uniquePolicyCount() & "</li>\n"
  html.add "<li>Average score: " & numberText(games.averageScore(), 2)
  html.add "</li>\n"
  html.add "</ul>\n"
  html.add "</section>\n"

  html.add "<section>\n"
  html.add "<h2>Bot scores</h2>\n"
  html.add renderIndexTable(roundPolicies, stats, "../")
  html.add "</section>\n"

  html.add "<section>\n"
  html.add "<h2>Scores</h2>\n"
  html.add renderScorePlot(roundPolicies, games, includeMissing = false)
  html.add "</section>\n"

  html.add "<section class=\"hidden-report-section\">\n"
  html.add "<h2>Matchups</h2>\n"
  html.add renderHeatMap(roundPolicies, games, includeMissing = false)
  html.add "<h3>Matchup Games</h3>\n"
  html.add renderHeatCountMap(roundPolicies, games, includeMissing = false)
  html.add "</section>\n"

  html.add "<section>\n"
  html.add "<h2>Games</h2>\n"
  html.add renderRoundNameKey(fullLabels, shortLabels)
  html.add "<div class=\"round-games-wrap\">\n"
  html.add "<table class=\"report-table wide round-games\">\n"
  html.add "<colgroup><col class=\"game-col\">"
  for position in 0 ..< RoundPlayerSlots:
    html.add "<col class=\"player-col\"><col class=\"score-col\">"
  html.add "</colgroup>\n<thead><tr><th>Game</th>"
  for position in 0 ..< RoundPlayerSlots:
    let letter = roundPlayerLetter(position)
    html.add "<th>Player " & letter & "</th>"
    html.add "<th class=\"score-heading\" aria-label=\"Score "
    html.add letter.htmlEscape()
    html.add "\"></th>"
  html.add "</tr></thead>\n<tbody>\n"
  for game in games.sortedGames():
    html.add renderRoundGameRow(game, policies, shortLabels, "../")
  html.add "</tbody></table>\n"
  html.add "</div>\n"
  html.add "</section>\n"
  html.add pageEnd()
  writeFileRetry(config.outDir / roundNumber.roundPage(), html)

proc cleanHtmlFiles(dir: string) =
  ## Removes generated HTML files from one report directory.
  if not dirExists(dir):
    return
  for path in walkFiles(dir / "*.html"):
    removeFile(path)

proc renderRoundPages(
  config: ToolConfig,
  policies: Table[string, PolicyRef],
  games: openArray[Game]
) =
  ## Writes one static page per downloaded tournament round.
  cleanHtmlFiles(config.outDir / "rounds")
  createDir(config.outDir / "rounds")
  for roundNumber, roundGames in games.roundGamesByNumber():
    renderRoundPage(config, policies, roundNumber, roundGames)

proc printSummary(
  policies: Table[string, PolicyRef],
  stats: Table[string, PolicyStats]
) =
  ## Prints a compact command-line summary table.
  echo ""
  echo "bot            games  wins  losses  ties  win_pct  avg_score"
  for policy in sortedPolicies(policies):
    let stat = stats.getOrDefault(policy.id)
    echo policy.label.align(14) & "  " &
      ($stat.games).align(5) & "  " &
      ($stat.wins).align(4) & "  " &
      ($stat.losses).align(6) & "  " &
      ($stat.ties).align(4) & "  " &
      stat.winPercentText().align(7) & "  " &
      numberText(stat.avgScore(), 2).align(9)

proc writeReport(
  config: ToolConfig,
  policies: Table[string, PolicyRef],
  games,
  freshGames: openArray[Game],
  rounds: openArray[CachedRound]
) =
  ## Writes all tournament report artifacts.
  createDir(config.outDir)
  echo "Writing tournament metadata..."
  writeMetadata(config, games)
  echo "Copying report assets..."
  copyAssets(config.outDir, config.tufteDir)
  echo "Writing strategy page..."
  let strategyDocs = readStrategyDocs()
  writeStrategyPage(config, strategyDocs)
  renderGamePages(config, games, freshGames)
  echo "Computing tournament stats..."
  let stats = computeStats(policies, games)
  echo "Rendering round pages..."
  renderRoundPages(config, policies, games)
  echo "Rendering index page..."
  renderIndex(config, policies, stats, games, rounds, strategyDocs)
  echo "Rendering bot pages..."
  cleanHtmlFiles(config.outDir / "bots")
  for policy in sortedPolicies(policies):
    renderBotPage(config, policies, policy, stats.getOrDefault(policy.id), games)
  printSummary(policies, stats)

proc syncReport(config: ToolConfig) =
  ## Publishes the generated report directory to S3.
  if not config.syncS3:
    echo "S3 sync disabled."
    return
  let cleanPrefix = config.s3Prefix.strip(chars = {'/'})
  let target =
    if cleanPrefix.len == 0:
      "s3://" & config.s3Bucket & "/"
    else:
      "s3://" & config.s3Bucket & "/" & cleanPrefix & "/"
  echo "Syncing report to ", target, " while preserving extra objects..."
  discard runCommand(
    [
      "aws",
      "--profile",
      config.awsProfile,
      "s3",
      "sync",
      config.outDir,
      target,
      "--only-show-errors",
      "--exclude",
      ".DS_Store"
    ]
  )
  let publicPath =
    if cleanPrefix.len == 0:
      ""
    else:
      cleanPrefix & "/"
  echo "S3 report: http://", config.s3Bucket,
    ".s3-website-us-east-1.amazonaws.com/", publicPath

proc roundMatchesSelector(round: CachedRound, selector: string): bool =
  ## Returns true when one cached round matches a selector.
  let clean = selector.strip()
  if clean.len == 0:
    return false
  var numberText = clean
  if numberText.startsWith("round-"):
    numberText = numberText[6 .. ^1]
  clean == round.roundId or
    clean == round.roundId.shortId() or
    numberText == $round.roundNumber

proc readSelectedRoundCache(
  config: ToolConfig,
  selector: string
): CachedRound =
  ## Reads one cached round selected by number, ID, or file path.
  let clean = selector.strip()
  if clean.len == 0:
    fail("--render-round requires a non-empty value.")

  var paths: seq[string]
  paths.add clean
  paths.add config.roundCacheDir() / clean
  if clean.splitFile().ext.len == 0:
    paths.add config.roundCachePath(clean)
  for path in paths:
    if not fileExists(path):
      continue
    result = config.readRoundCache(path)
    if result.roundId.len > 0:
      return

  for round in config.readCachedRounds():
    if round.roundMatchesSelector(clean):
      return round
  fail("No cached round matched --render-round " & clean & ".")

proc renderSelectedRound(config: ToolConfig) =
  ## Renders one cached round page without refreshing live data.
  let round = config.readSelectedRoundCache(config.renderRound)
  createDir(config.outDir / "rounds")
  var emptyPolicies: Table[string, PolicyRef]
  let policies = expandPolicies(emptyPolicies, round.games)
  renderRoundPage(config, policies, round.roundNumber, round.games)
  echo "Rendered cached round ", round.roundNumber, " from ",
    round.roundId.shortId(), "."
  echo "Report: ", config.outDir / round.roundNumber.roundPage()

proc runReport(config: ToolConfig) =
  ## Builds the official tournament report once.
  if config.renderRound.len > 0:
    renderSelectedRound(config)
    return
  ensureUserToken(config)
  echo "Loading Crewrift Prime league policy metadata..."
  let policyMetadata = fetchPolicies(config)
  echo "Loaded ", policyMetadata.len, " league policies."
  echo "Scanning recent rounds..."
  let rounds = fetchRounds(config)
  echo "Fetched ", rounds.len, " rounds."
  let cachedRounds = readCachedRounds(config)
  let cachedRoundIds = cachedRounds.roundIdSet()
  let cachedRoundTable = cachedRounds.roundTable()
  let metadataGames = readMetadataGames(config)
  let cachedGames =
    if cachedRoundIds.len > 0:
      mergeGamesReplacingRounds(
        metadataGames,
        cachedRounds.cachedGames(),
        cachedRoundIds
      )
    else:
      metadataGames
  var cachedMaxRound = metadataGames.maxCachedRound()
  let cachedRoundMax = cachedRounds.maxCachedRound()
  if cachedRoundMax > cachedMaxRound:
    cachedMaxRound = cachedRoundMax
  if cachedMaxRound >= 0:
    echo "Loaded ", cachedGames.len, " cached games through round ",
      cachedMaxRound, "."
  let fetchMaxRound =
    if config.rebuild:
      echo "Rebuilding cached report rounds from round ", MinRoundNumber, "."
      -1
    else:
      cachedMaxRound
  let freshRounds = fetchRoundCaches(
    config,
    rounds,
    fetchMaxRound,
    cachedRoundTable
  )
  let reportRounds = mergeCachedRounds(cachedRounds, freshRounds)
  let freshGames = freshRounds.cachedGames()
  echo "Found ", freshGames.len, " downloaded non-qualifier games."
  let games =
    if cachedGames.len > 0 or freshRounds.len > 0:
      mergeGamesReplacingRounds(
        cachedGames,
        freshGames,
        freshRounds.roundIdSet()
      )
    else:
      freshGames
  if cachedGames.len > 0:
    echo "Merged report has ", games.len, " games."
  let policies = expandPolicies(policyMetadata, games)
  echo "Report includes ", policies.len, " policies."
  writeReport(config, policies, games, freshGames, reportRounds)
  echo ""
  echo "Report: ", config.outDir / "index.html"
  syncReport(config)

proc runAutoReport(config: ToolConfig) =
  ## Builds the tournament report forever and retries failed refreshes.
  let intervalSeconds = config.autoMinutes.float * 60.0
  while true:
    let startTime = epochTime()
    var failed = false
    try:
      runReport(config)
    except CatchableError as e:
      failed = true
      echo "Auto refresh failed: ", e.msg.shortCommandError()

    let elapsed = epochTime() - startTime
    var waitSeconds = intervalSeconds - elapsed
    if failed and waitSeconds > AutoFailureRetrySeconds:
      waitSeconds = AutoFailureRetrySeconds
    if waitSeconds <= 0.0:
      if failed:
        echo "Auto refresh failed after ", numberText(elapsed, 1),
          "s; retrying now."
      else:
        echo "Auto refresh took ", numberText(elapsed, 1),
          "s; starting next run now."
      continue
    if failed:
      echo "Auto refresh failed after ", numberText(elapsed, 1),
        "s; retrying in ", numberText(waitSeconds, 1), "s."
    else:
      echo "Auto refresh took ", numberText(elapsed, 1),
        "s; sleeping ", numberText(waitSeconds, 1), "s."
    sleep(int(waitSeconds * 1000.0))

proc main() =
  ## Builds the official tournament report.
  loadRoundTimezones()
  let config = parseConfig()
  if config.autoMinutes == 0:
    runReport(config)
    return
  runAutoReport(config)

when isMainModule:
  main()
