import
  std/[algorithm, json, monotimes, os, osproc, streams,
    strutils, times],
  common,
  replay_extractor

const
  SoftmaxObservatoryUrl = "https://softmax.com/observatory/v2"
  DefaultLeagueId = "league_a12f5172-0907-4d04-8bcb-ca02f5360e3a"
  DefaultCoworldId = ""
  DefaultGames = 10
  DefaultPollMs = 5_000
  DefaultWaitSeconds = 1_800
  MaxPollBackoffMs = 30_000
  PlayerLogTimeoutMs = 5_000
  UploadAttempts = 12
  UploadRetryMs = 30_000
  UploadRetrySeconds = UploadRetryMs div 1_000
  DefaultBedrockModel = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
  DefaultUploadName = "notsus"
  BaselinePolicyLabel = "notsus:v1"
  LogMissingPrefix = "missing:"
  LogNotRequestedPrefix = "not-requested:"
  FirstImposterSlot = 6
  ScoreEpsilon = 0.005
  VersionChartWidth = 760
  VersionChartHeight = 400
  VersionChartLeft = 52
  VersionChartRight = 16
  VersionChartTop = 22
  VersionChartBottom = 44
  VersionChartScoreMin = 0.0
  VersionChartScoreMax = 1.0
  VersionChartScoreStep = 0.1
  PreloadFonts = [
    "ETBembo-RomanOSF.otf",
    "ETBembo-DisplayItalic.otf",
    "ETBembo-SemiBoldOSF.otf"
  ]
  PlayerLetters = ["A", "B", "C", "D", "E", "F", "G", "H"]
  Usage = """
Usage:
  nim r players/notsus/tools/run.nim -- [options]
  nim r players/notsus/tools/run.nim -- OPPONENT [options]
  nim r players/notsus/tools/run.nim -- BOT_0 ... BOT_7 [options]
  nim r players/notsus/tools/run.nim -- --request-id xreq_... BOT_0 ... BOT_7
  nim r players/notsus/tools/run.nim -- [MY_BOT] --vs OPPONENT -n 20

Examples:
  nim r players/notsus/tools/run.nim -- -n:2
  nim r players/notsus/tools/run.nim -- notsus:v1 -n:2
  nim r players/notsus/tools/run.nim -- 'notsus:*' notsus:v1 -n:10
  nim r players/notsus/tools/run.nim -- notsus:v9 notsus:v1 -n 10
  nim r players/notsus/tools/run.nim -- --vs crewborg:v70 -n 20
  nim r players/notsus/tools/run.nim -- notsus:v12 --vs crewborg:v70 -n 20
  nim r players/notsus/tools/run.nim -- --request-id xreq_... notsus:v9 notsus:v1

Options:
      --bot BOT              Bot alias or policy version id.
                             May be repeated to build the eight-player roster.
                             No bot means current checkout vs seven notsus:v1
                             players. One bot means current checkout vs seven
                             copies of that opponent.
      --vs BOT               Direct versus mode. May be repeated. With no
                             MY_BOT, uploads the current checkout. With MY_BOT,
                             runs that policy. Games split evenly across every
                             target and both fixed role sides.
  -n, --games N              Number of hosted eight-player games. Default: 10.
      --name NAME            Human run name.
      --out-dir PATH         Static report root.
                             Default: players/notsus/runs.
      --asset-prefix PATH    Relative asset prefix from --out-dir to a
                             shared tufte.css and fonts directory.
      --upload-name NAME     Policy name for current-checkout uploads.
                             Default: notsus, which uploads as notsus:vN.
      --image-tag TAG        Docker image tag for current-checkout uploads.
      --bedrock-key-file PATH
                             Ignored legacy option. Uploads use --use-bedrock.
      --bedrock-model MODEL  Bedrock model for uploaded current bot.
      --aws-region REGION   Ignored legacy option. The runner sets region.
      --coworld ID           Direct Coworld target. Defaults to the league.
      --eight-player          Kept for compatibility. This is the default.
      --league ID            League target instead of direct Coworld.
                             Default: Crewrift Prime.
      --coworld-dir PATH     Directory for uv run coworld. Default: ../metta.
      --tufte-dir PATH       Tufte assets directory. Default: ../offstream/tufte.
      --request-id ID        Render and poll an existing XP request.
      --index-only           Regenerate the top-level index and exit.
      --repair-run PATH      Refresh one existing run folder from Softmax.
      --repair-incomplete    Refresh all non-completed runs in --out-dir.
      --poll-ms N            Poll delay in milliseconds. Default: 5000.
      --wait-timeout N       Wait timeout in seconds. Default: 1800.
      --no-replays           Do not download replay artifacts.
      --server URL           Observatory API server passed to coworld.
  -h, --help                 Show this help text.
"""

type
  HostedRunError = object of CatchableError

  BotLabelRule = enum
    StripGamePrefix,
    StripVersionToken,
    StripScriptedToken,
    StripBaselineToken

  BotRef = object
    input: string
    key: string
    label: string
    policyId: string

  Score = object
    policyId: string
    score: float

  SeatScore = object
    found: bool
    score: float

  Episode = object
    index: int
    id: string
    status: string
    episodeId: string
    replayUrl: string
    liveUrl: string
    errorType: string
    error: string
    scores: seq[Score]
    seatScores: seq[SeatScore]
    excluded: bool
    exclusionReason: string

  Summary = object
    completed: int
    failed: int
    running: int
    pending: int
    excluded: int
    wins: int
    losses: int
    ties: int
    totalA: float
    totalB: float
    avgA: float
    avgB: float
    margin: float
    avgMargin: float

  ToolConfig = object
    bots: seq[BotRef]
    games: int
    name: string
    nameSet: bool
    outDir: string
    uploadCurrent: bool
    uploadCurrentIndex: int
    uploadName: string
    imageTag: string
    bedrockModel: string
    leagueId: string
    coworldId: string
    coworldDir: string
    tufteDir: string
    assetPrefix: string
    requestId: string
    focusIndex: int
    slotRoles: seq[string]
    vsMode: bool
    vsOpponents: seq[BotRef]
    pollMs: int
    waitSeconds: int
    downloadReplays: bool
    server: string
    indexOnly: bool
    repairRuns: seq[string]
    repairIncomplete: bool

  RunPaths = object
    root: string
    request: string
    meta: string
    replays: string
    logs: string

  PlayerLogSummary = object
    found: bool
    lineCount: int
    bedrockCalls: int
    bedrockErrors: int
    healthOk: int
    healthPending: int
    sentChat: int
    noOutput: int
    duplicateSuppressed: int
    deadSkips: int
    frameDrops: int
    finalStatus: string
    normalEnd: bool
    notes: seq[string]

  PlayerSummary = object
    total: float
    avg: float
    scored: int
    wins: int
    ties: int
    losses: int

  RecordOutcome = enum
    OutcomeMissing,
    OutcomeWin,
    OutcomeLoss,
    OutcomeTie

  BotGroup = object
    key: string
    label: string
    title: string
    abbr: string
    slots: seq[int]
    policyIds: seq[string]

  RunScoreGroup = object
    label: string
    version: int
    points: seq[ScoreChartPoint]

  RunFocus = object
    found: bool
    groupIndex: int
    label: string
    abbr: string
    opponentLabel: string
    scored: int
    wins: int
    losses: int
    ties: int
    avg: float
    opponentAvg: float
    avgMargin: float

  VersusSide = enum
    CandidateImposters,
    OpponentImposters

  VersusSegment = object
    side: VersusSide
    opponentIndex: int
    opponent: BotRef
    label: string
    config: ToolConfig
    requestPath: string
    detail: JsonNode
    gameOffset: int

  VersionAverage = object
    version: int
    totalScore: float
    games: int

  CommandResult = object
    output: string
    code: int

proc fail(message: string) =
  ## Raises a hosted run error with a clear message.
  raise newException(HostedRunError, message)

proc rootDir(): string =
  ## Returns the Notsus player root for this tool.
  currentSourcePath().parentDir().parentDir()

proc gameDir(): string =
  ## Returns the Crewrift repository root for this player.
  rootDir().parentDir().parentDir()

proc workspaceDir(): string =
  ## Returns the parent workspace directory.
  gameDir().parentDir()

proc centralRunsDir(): string =
  ## Returns the local Notsus report directory.
  rootDir() / "runs"

proc defaultConfig(): ToolConfig =
  ## Returns the default hosted-run tool configuration.
  ToolConfig(
    games: DefaultGames,
    outDir: centralRunsDir(),
    bedrockModel: getEnv(
      "BEDROCK_CLAUDE_MODEL_ID",
      getEnv("BEDROCK_MODEL", DefaultBedrockModel)
    ),
    uploadCurrentIndex: -1,
    uploadName: DefaultUploadName,
    focusIndex: -1,
    leagueId: DefaultLeagueId,
    coworldId: DefaultCoworldId,
    coworldDir: workspaceDir() / "metta",
    tufteDir: workspaceDir() / "offstream" / "tufte",
    pollMs: DefaultPollMs,
    waitSeconds: DefaultWaitSeconds,
    downloadReplays: true
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

proc normalizeBot(value: string): string =
  ## Normalizes a bot name for alias lookup.
  for ch in value.toLowerAscii():
    if ch in {'a' .. 'z', '0' .. '9'}:
      result.add ch

proc knownPolicy(key: string): BotRef =
  ## Resolves a known friendly bot name to a policy version.
  const KnownPolicies = [
    (
      key: "notsus",
      label: "notsus:v2",
      policyId: "notsus:v2"
    ),
    (
      key: "notsusv2",
      label: "notsus:v2",
      policyId: "notsus:v2"
    ),
    (
      key: "notsusv1",
      label: "notsus:v1",
      policyId: "notsus:v1"
    ),
    (
      key: "scripted",
      label: BaselinePolicyLabel,
      policyId: BaselinePolicyLabel
    ),
    (
      key: "baseline",
      label: BaselinePolicyLabel,
      policyId: BaselinePolicyLabel
    ),
    (
      key: "baseline8p",
      label: BaselinePolicyLabel,
      policyId: BaselinePolicyLabel
    ),
    (
      key: "crewriftscripted",
      label: BaselinePolicyLabel,
      policyId: BaselinePolicyLabel
    )
  ]

  for policy in KnownPolicies:
    if key == policy.key or key == normalizeBot(policy.label):
      return BotRef(
        key: policy.key,
        label: policy.label,
        policyId: policy.policyId
      )

proc currentBotRef(input = "current"): BotRef =
  ## Returns the placeholder bot reference for this checkout.
  BotRef(
    input: input,
    key: "current",
    label: "current checkout",
    policyId: ""
  )

proc looksLikeUuid(value: string): bool =
  ## Returns true when a string looks like a UUID policy version id.
  if value.len != 36:
    return false
  for i, ch in value:
    if i in [8, 13, 18, 23]:
      if ch != '-':
        return false
    elif not (ch in {'0' .. '9', 'a' .. 'f', 'A' .. 'F'}):
      return false
  true

proc looksLikePolicyLabel(value: string): bool =
  ## Returns true when a string looks like a policy label reference.
  let colon = value.rfind(':')
  if colon <= 0 or colon + 2 >= value.len:
    return false
  if value[colon + 1] != 'v':
    return false
  for i in colon + 2 ..< value.len:
    if value[i] notin {'0' .. '9'}:
      return false
  true

proc resolveBot(value: string): BotRef =
  ## Resolves one CLI bot name or raw policy UUID.
  if value == "notsus:*" or value == "current" or value == "*":
    return currentBotRef(value)
  let key = normalizeBot(value)
  result = knownPolicy(key)
  if result.policyId.len > 0:
    result.input = value
    return
  if looksLikeUuid(value):
    return BotRef(
      input: value,
      key: key,
      label: value[0 .. 7],
      policyId: value
    )
  if looksLikePolicyLabel(value):
    return BotRef(
      input: value,
      key: key,
      label: value,
      policyId: value
    )
  fail("Unknown bot name or policy version id: " & value)

proc isCurrentBot(bot: BotRef): bool =
  ## Returns true when one bot stands for the current checkout.
  bot.policyId.len == 0

proc botTitle(bot: BotRef): string =
  ## Returns the best display label for one bot.
  if bot.label.len > 0:
    return bot.label
  if bot.input.len > 0:
    return bot.input
  "unknown"

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

proc shortenBotLabel(label: string, rule: BotLabelRule): string =
  ## Applies one bot-label shortening rule.
  case rule
  of StripGamePrefix:
    stripGamePrefix(label)
  of StripVersionToken:
    stripVersionToken(label)
  of StripScriptedToken:
    removeLabelToken(label, "scripted")
  of StripBaselineToken:
    removeLabelToken(label, "baseline")

proc botLabelsAreDistinct(
  fullLabels,
  shortLabels: openArray[string]
): bool =
  ## Returns true when distinct full labels have distinct short labels.
  for i in 0 ..< shortLabels.len:
    let label = shortLabels[i].toLowerAscii()
    if label.len == 0:
      return false
    for j in 0 ..< i:
      if fullLabels[i] != fullLabels[j] and
        label == shortLabels[j].toLowerAscii():
          return false
  true

proc applyBotLabelRule(
  fullLabels: openArray[string],
  shortLabels: var seq[string],
  rule: BotLabelRule
) =
  ## Applies one shortening rule when it remains collision-free.
  var next = shortLabels
  for i in 0 ..< next.len:
    next[i] = next[i].shortenBotLabel(rule)
  if fullLabels.botLabelsAreDistinct(next):
    shortLabels = next

proc shortBotLabels(bots: openArray[BotRef]): seq[string] =
  ## Returns collision-safe compact display labels for all roster bots.
  var fullLabels: seq[string]
  for bot in bots:
    fullLabels.add botTitle(bot)
    result.add botTitle(bot).readableBotLabel()
  for rule in [
    StripGamePrefix,
    StripVersionToken,
    StripScriptedToken,
    StripBaselineToken
  ]:
    fullLabels.applyBotLabelRule(result, rule)

proc playerLetter(index: int): string =
  ## Returns a display letter for one player slot.
  if index >= 0 and index < PlayerLetters.len:
    return PlayerLetters[index]
  $(index + 1)

proc titleWords(text: string): string =
  ## Returns a simple title-cased label.
  var words: seq[string]
  for word in text.splitWhitespace():
    if word.len == 0:
      continue
    var clean = word.toLowerAscii()
    clean[0] = clean[0].toUpperAscii()
    words.add clean
  words.join(" ")

proc displayBotName(bot: BotRef): string =
  ## Returns a compact family label for one bot.
  let clean = botTitle(bot).readableBotLabel().stripVersionToken()
  if clean.normalizeBot() == "notsus":
    return "Not Sus"
  clean.titleWords()

proc addPolicyId(group: var BotGroup, value: string) =
  ## Adds one non-empty policy identifier to a group.
  if value.len == 0:
    return
  for existing in group.policyIds:
    if existing == value:
      return
  group.policyIds.add value

proc botGroupIndex(groups: openArray[BotGroup], key: string): int =
  ## Returns the index of one bot group or -1 when absent.
  for i, group in groups:
    if group.key == key:
      return i
  -1

proc botGroups(bots: openArray[BotRef]): seq[BotGroup] =
  ## Returns unique bot groups in roster order.
  for i, bot in bots:
    var key = bot.key
    if key.len == 0:
      key = normalizeBot(botTitle(bot))
    var index = result.botGroupIndex(key)
    if index < 0:
      result.add BotGroup(
        key: key,
        label: bot.displayBotName(),
        title: botTitle(bot),
        slots: @[i]
      )
      index = result.len - 1
    else:
      result[index].slots.add i
    result[index].addPolicyId(bot.policyId)
    result[index].addPolicyId(bot.input)
    result[index].addPolicyId(bot.label)

proc abbrSeed(label: string): string =
  ## Returns the preferred one to three character abbreviation seed.
  if label.normalizeBot() == "notsus":
    return "N"
  var words: seq[string]
  for word in label.splitWhitespace():
    if word.len > 0:
      words.add word
  if words.len >= 2:
    for i in 0 ..< min(2, words.len):
      result.add words[i][0].toUpperAscii()
  elif words.len == 1:
    result.add words[0][0].toUpperAscii()
  if result.len == 0:
    result = "B"

proc compactLetters(label: string, limit: int): string =
  ## Returns up to limit alphanumeric label characters.
  for ch in label.toUpperAscii():
    if ch in {'A' .. 'Z', '0' .. '9'}:
      result.add ch
      if result.len >= limit:
        return

proc assignAbbreviations(groups: var seq[BotGroup]) =
  ## Assigns unique compact abbreviations to bot groups.
  var used: seq[string]
  for i in 0 ..< groups.len:
    var abbr = groups[i].label.abbrSeed()
    if abbr.len > 3:
      abbr.setLen(3)
    var unique = true
    for item in used:
      if item == abbr:
        unique = false
        break
    if not unique:
      for width in 2 .. 3:
        let candidate = groups[i].label.compactLetters(width)
        if candidate.len == 0:
          continue
        unique = true
        for item in used:
          if item == candidate:
            unique = false
            break
        if unique:
          abbr = candidate
          break
    if not unique:
      abbr = i.playerLetter()
    groups[i].abbr = abbr
    used.add abbr

proc groupedBots(bots: openArray[BotRef]): seq[BotGroup] =
  ## Returns unique bot groups with display abbreviations.
  result = botGroups(bots)
  result.assignAbbreviations()

proc groupForSlot(groups: openArray[BotGroup], slot: int): int =
  ## Returns the group index containing one roster slot.
  for i, group in groups:
    for groupSlot in group.slots:
      if groupSlot == slot:
        return i
  -1

proc policyMatches(group: BotGroup, policyId: string): bool =
  ## Returns true when a score policy identifier belongs to a group.
  if policyId.len == 0:
    return false
  for value in group.policyIds:
    if value == policyId:
      return true
  false

proc scoreForGroup(
  episode: Episode,
  groups: openArray[BotGroup],
  groupIndex: int
): tuple[found: bool, score: float] =
  ## Finds one score by unique bot group.
  if groupIndex < 0 or groupIndex >= groups.len:
    return (false, 0.0)
  let group = groups[groupIndex]
  for score in episode.scores:
    if group.policyMatches(score.policyId):
      return (true, score.score)
  if episode.scores.len == groups.len:
    return (true, episode.scores[groupIndex].score)

  var
    total = 0.0
    count = 0
  for slot in group.slots:
    if slot >= 0 and slot < episode.seatScores.len:
      let score = episode.seatScores[slot]
      if score.found:
        total += score.score
        inc count
    elif episode.scores.len > groups.len and slot < episode.scores.len:
      total += episode.scores[slot].score
      inc count
  if count > 0:
    return (true, total / count.float)
  (false, 0.0)

proc rosterTitle(bots: openArray[BotRef]): string =
  ## Returns a display title for a eight-player roster.
  for bot in bots:
    if result.len > 0:
      result.add " vs "
    result.add botTitle(bot)

proc versusTitle(bots: openArray[BotRef]): string =
  ## Returns a display title for one or more versus targets.
  if bots.len == 0:
    return "versus"
  result = botTitle(bots[0]) & " vs "
  if bots.len == 1:
    result.add "opponent"
    return
  for i in 1 ..< bots.len:
    if i > 1:
      result.add ", "
    result.add botTitle(bots[i])

proc splitLongOption(arg: string): tuple[key, value: string] =
  ## Splits a long option into name and inline value.
  let body = arg[2 .. ^1]
  let
    equals = body.find("=")
    colon = body.find(":")
  var split = -1
  if equals >= 0 and (colon < 0 or equals < colon):
    split = equals
  elif colon >= 0:
    split = colon
  if split >= 0:
    result.key = body[0 ..< split]
    result.value = body[split + 1 .. ^1]
  else:
    result.key = body

proc readConfig(): ToolConfig =
  ## Parses command-line options and bot names.
  result = defaultConfig()
  let params = commandLineParams()
  var i = 0
  while i < params.len:
    let arg = params[i]
    if arg == "--":
      discard
    elif arg == "-h" or arg == "--help":
      echo Usage.strip()
      quit 0
    elif arg == "-n" or arg.startsWith("-n:") or arg.startsWith("-n="):
      let value =
        if arg.len > 2:
          arg[3 .. ^1]
        else:
          ""
      result.games = parseIntValue("-n", optionValue(params, i, "-n", value))
    elif arg.startsWith("--"):
      let pair = splitLongOption(arg)
      let
        key = pair.key.toLowerAscii()
        name = "--" & pair.key
      case key
      of "bot":
        result.bots.add resolveBot(
          optionValue(params, i, name, pair.value)
        )
      of "vs":
        result.vsMode = true
        result.vsOpponents.add resolveBot(
          optionValue(params, i, name, pair.value)
        )
      of "bota", "bot-a", "botb", "bot-b":
        fail("Use eight-player roster arguments instead of " & name & ".")
      of "games":
        result.games = parseIntValue(
          name,
          optionValue(params, i, name, pair.value)
        )
      of "name":
        result.name = optionValue(params, i, name, pair.value)
        result.nameSet = true
      of "upload-name":
        result.uploadName = optionValue(params, i, name, pair.value)
      of "image-tag":
        result.imageTag = optionValue(params, i, name, pair.value)
      of "bedrock-key-file":
        discard optionValue(params, i, name, pair.value)
      of "bedrock-model":
        result.bedrockModel = optionValue(params, i, name, pair.value)
      of "aws-region":
        discard optionValue(params, i, name, pair.value)
      of "out-dir":
        result.outDir = optionValue(params, i, name, pair.value)
      of "coworld":
        result.coworldId = optionValue(params, i, name, pair.value)
      of "league":
        result.leagueId = optionValue(params, i, name, pair.value)
        result.coworldId = ""
      of "eight-player", "eightplayer", "four-player", "fourplayer":
        discard
      of "coworld-dir":
        result.coworldDir = optionValue(params, i, name, pair.value)
      of "tufte-dir":
        result.tufteDir = optionValue(params, i, name, pair.value)
      of "asset-prefix":
        result.assetPrefix = optionValue(params, i, name, pair.value)
      of "request-id":
        result.requestId = optionValue(params, i, name, pair.value)
      of "index-only":
        result.indexOnly = true
      of "repair-run":
        result.repairRuns.add optionValue(params, i, name, pair.value)
      of "repair-incomplete":
        result.repairIncomplete = true
      of "poll-ms":
        result.pollMs = parseIntValue(
          name,
          optionValue(params, i, name, pair.value)
        )
      of "wait-timeout":
        result.waitSeconds = parseIntValue(
          name,
          optionValue(params, i, name, pair.value)
        )
      of "no-replays":
        result.downloadReplays = false
      of "server":
        result.server = optionValue(params, i, name, pair.value)
      else:
        fail("Unknown option: " & name)
    elif arg.startsWith("-"):
      fail("Unknown option: " & arg)
    else:
      result.bots.add resolveBot(arg)
    inc i

  if result.indexOnly:
    if not fileExists(result.tufteDir / "tufte.css"):
      fail("--tufte-dir is missing tufte.css: " & result.tufteDir)
    return

  if result.repairRuns.len > 0 or result.repairIncomplete:
    if not dirExists(result.coworldDir):
      fail("--coworld-dir does not exist: " & result.coworldDir)
    if not fileExists(result.tufteDir / "tufte.css"):
      fail("--tufte-dir is missing tufte.css: " & result.tufteDir)
    if result.pollMs <= 0:
      fail("--poll-ms must be positive.")
    return

  if result.bots.len == 0 and result.requestId.len > 0:
    fail("--request-id requires eight roster bots for labels.")

  if result.vsMode:
    if result.requestId.len > 0:
      fail("--vs cannot be used with --request-id.")
    if result.vsOpponents.len == 0:
      fail("--vs requires at least one opponent.")
    for opponent in result.vsOpponents:
      if opponent.isCurrentBot():
        fail("--vs opponent cannot be the current checkout.")
    if result.bots.len == 0:
      result.bots = @[currentBotRef()]
    elif result.bots.len > 1:
      fail("--vs accepts zero or one MY_BOT positional argument.")
    if result.bots[0].isCurrentBot():
      result.uploadCurrent = true
      result.uploadCurrentIndex = 0
    else:
      result.uploadCurrent = false
      result.uploadCurrentIndex = -1
    for opponent in result.vsOpponents:
      result.bots.add opponent
    result.focusIndex = 0
    let divisor = result.vsOpponents.len * 2
    if result.games mod divisor != 0:
      fail(
        "--vs with " & $result.vsOpponents.len &
          " opponents requires --games divisible by " & $divisor & "."
      )
    if result.games <= 0:
      fail("--games must be positive.")
    if result.pollMs <= 0:
      fail("--poll-ms must be positive.")
    if result.waitSeconds <= 0:
      fail("--wait-timeout must be positive.")
    if not dirExists(result.coworldDir):
      fail("--coworld-dir does not exist: " & result.coworldDir)
    if not fileExists(result.tufteDir / "tufte.css"):
      fail("--tufte-dir is missing tufte.css: " & result.tufteDir)
    if result.name.len == 0:
      result.name = result.bots.versusTitle()
    return

  if result.bots.len == 0:
    result.uploadCurrent = true
    result.uploadCurrentIndex = 0
    let opponent = resolveBot("notsus:v1")
    result.bots = @[currentBotRef()]
    for _ in 1 ..< PlayerLetters.len:
      result.bots.add opponent
  elif result.bots.len == 1:
    let opponent = result.bots[0]
    result.uploadCurrent = true
    result.uploadCurrentIndex = 0
    if opponent.isCurrentBot():
      let baseline = resolveBot("notsus:v1")
      result.bots = @[currentBotRef()]
      for _ in 1 ..< PlayerLetters.len:
        result.bots.add baseline
    else:
      result.bots = @[currentBotRef()]
      for _ in 1 ..< PlayerLetters.len:
        result.bots.add opponent
  elif result.bots.len != PlayerLetters.len:
    fail("Provide one opponent bot or exactly eight roster bots.")
  else:
    var currentCount = 0
    for i in 0 ..< result.bots.len:
      if result.bots[i].isCurrentBot():
        inc currentCount
        result.uploadCurrentIndex = i
    if currentCount > 1:
      fail("Only one roster slot can be the current checkout.")
    if currentCount == 1:
      result.uploadCurrent = true

  if result.games <= 0:
    fail("--games must be positive.")
  if result.pollMs <= 0:
    fail("--poll-ms must be positive.")
  if result.waitSeconds <= 0:
    fail("--wait-timeout must be positive.")
  if not dirExists(result.coworldDir):
    fail("--coworld-dir does not exist: " & result.coworldDir)
  if not fileExists(result.tufteDir / "tufte.css"):
    fail("--tufte-dir is missing tufte.css: " & result.tufteDir)
  if result.name.len == 0:
    result.name = rosterTitle(result.bots)

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

proc slugify(text: string): string =
  ## Converts a label to a stable filesystem slug.
  var lastDash = false
  for ch in text.toLowerAscii():
    if ch in {'a' .. 'z', '0' .. '9'}:
      result.add ch
      lastDash = false
    elif not lastDash:
      result.add '-'
      lastDash = true
  result = result.strip(chars = {'-'})
  if result.len == 0:
    result = "run"

proc timestampSlug(): string =
  ## Returns a compact local timestamp for run directories.
  now().format("yyyyMMdd-HHmmss")

proc createdText(): string =
  ## Returns a human-readable local timestamp.
  now().format("yyyy-MM-dd HH:mm:ss zzz")

proc uniqueRunRoot(outDir, name: string): string =
  ## Returns a unique run directory path.
  let base = timestampSlug() & "-" & slugify(name)
  result = outDir / base
  var n = 2
  while dirExists(result):
    result = outDir / (base & "-" & $n)
    inc n

proc runNumber(outDir: string): int =
  ## Returns the next run number for the report index.
  result = 1
  if not dirExists(outDir):
    return
  for kind, path in walkDir(outDir):
    if kind == pcDir and fileExists(path / "run.json"):
      inc result

proc pathsFor(config: ToolConfig): RunPaths =
  ## Creates and returns all directories for this run.
  createDir(config.outDir)
  result.root = uniqueRunRoot(config.outDir, config.name)
  result.request = result.root / "request.json"
  result.meta = result.root / "run.json"
  result.replays = result.root / "replays"
  result.logs = result.root / "logs"
  createDir(result.root)
  createDir(result.replays)
  createDir(result.logs)

proc pathsForRoot(root: string): RunPaths =
  ## Returns report paths for an existing run directory.
  result.root = root
  result.request = root / "request.json"
  result.meta = root / "run.json"
  result.replays = root / "replays"
  result.logs = root / "logs"
  createDir(result.replays)
  createDir(result.logs)

proc numberText(value: float, decimals = 2): string =
  ## Formats one floating-point value for tables and prose.
  result = value.formatFloat(ffDecimal, decimals)
  while result.len > 0 and result[^1] == '0':
    result.setLen(result.len - 1)
  if result.len > 0 and result[^1] == '.':
    result.setLen(result.len - 1)
  if result.len == 0 or result == "-0":
    result = "0"

proc fixedNumberText(value: float, decimals: int): string =
  ## Formats one floating-point value with fixed decimal digits.
  result = value.formatFloat(ffDecimal, decimals)
  if result.len > 0 and result[0] == '-':
    var isZero = true
    for i in 1 ..< result.len:
      if result[i] != '0' and result[i] != '.':
        isZero = false
        break
    if isZero:
      result = result[1 .. ^1]

proc durationText(seconds: float): string =
  ## Formats a wall-clock duration for run summaries.
  if seconds < 0.0:
    return "-"
  if seconds < 60.0:
    return numberText(seconds, 1) & " seconds"
  let totalSeconds = seconds.int
  let
    minutes = totalSeconds div 60
    remainder = totalSeconds mod 60
  if minutes < 60:
    return $minutes & "m " & $remainder & "s"
  let
    hours = minutes div 60
    hourMinutes = minutes mod 60
  $hours & "h " & $hourMinutes & "m " & $remainder & "s"

proc elapsedSeconds(started: MonoTime): float =
  ## Returns elapsed wall-clock seconds from a monotonic start time.
  (getMonoTime() - started).inMilliseconds.float / 1000.0

proc field(node: JsonNode, key: string): JsonNode =
  ## Returns an object field or JSON null when it is absent.
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

proc boolField(node: JsonNode, key: string, fallback = false): bool =
  ## Returns a boolean field or a fallback value.
  let child = node.field(key)
  if child.kind == JBool:
    return child.getBool()
  fallback

proc scoreValue(node: JsonNode): float =
  ## Returns a score value from either a number or score object.
  case node.kind
  of JInt:
    node.getInt().float
  of JFloat:
    node.getFloat()
  of JObject:
    node.numberField("score")
  else:
    0.0

proc redactedArg(arg: string): string =
  ## Redacts secret values from command rendering.
  const SecretPrefixes = [
    "AWS_BEARER_TOKEN_BEDROCK=",
    "BEDROCK_API_KEY=",
    "API_KEY="
  ]
  for prefix in SecretPrefixes:
    if arg.startsWith(prefix):
      return prefix & "<redacted>"
  arg

proc commandText(args: openArray[string]): string =
  ## Returns a safely quoted command string with secrets redacted.
  var redacted: seq[string]
  for arg in args:
    redacted.add redactedArg(arg)
  redacted.quoteShellCommand()

proc shortCommandError(message: string): string =
  ## Returns the useful part of a command failure.
  let markers = [
    "HTTPStatusError:",
    "RuntimeError:",
    "Client error",
    "Server error",
    "ConnectError",
    "ReadTimeout",
    "Timeout",
    "timed out"
  ]
  var fallback = ""
  for line in message.splitLines():
    let clean = line.strip()
    if clean.len == 0:
      continue
    fallback = clean
    for marker in markers:
      if marker in clean:
        return clean
  fallback

proc isTransientPollError(message: string): bool =
  ## Returns true for hosted poll failures that are worth retrying.
  let lower = message.toLowerAscii()
  if "401" in lower or "authentication failed" in lower:
    return false
  if "404 not found" in lower and "experience-requests" in lower:
    return true
  for marker in [
    "408", "409", "425", "429", "500", "502", "503", "504",
    "timeout", "timed out", "connection", "temporarily unavailable"
  ]:
    if marker in lower:
      return true
  false

proc isUploadImageNotReady(output: string): bool =
  ## Returns true when Softmax has not finished preparing an image.
  let lower = output.toLowerAscii()
  "container image" in lower and (
    "is not ready" in lower or "not found" in lower
  )

proc pollBackoffMs(config: ToolConfig, failures: int): int =
  ## Returns a bounded backoff delay for transient poll failures.
  min(MaxPollBackoffMs, config.pollMs * max(1, min(failures, 6)))

proc shouldPrintPollFailure(failures: int): bool =
  ## Returns true when a transient poll failure should be logged.
  failures <= 3 or failures mod 5 == 0

proc runCommand(
  args: openArray[string],
  workingDir = "",
  check = true,
  timeoutMs = -1
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
  if timeoutMs >= 0:
    result.code = process.waitForExit(timeoutMs)
    if result.code == 0:
      result.output = process.outputStream().readAll()
  else:
    result.output = process.outputStream().readAll()
    result.code = process.waitForExit()
  process.close()
  if check and result.code != 0:
    raise newException(
      OSError,
      "Command failed (" & $result.code & "): " & args.commandText() & "\n" &
      result.output
    )

proc runAttached(args: openArray[string], workingDir = "") =
  ## Runs one command with inherited terminal streams.
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
    options = {poUsePath, poParentStreams}
  )
  let code = process.waitForExit()
  process.close()
  if code != 0:
    raise newException(
      OSError,
      "Command failed (" & $code & "): " & args.commandText()
    )

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
  ## Switches Softmax to the main user token when needed.
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

proc coworldJsonCommand(
  config: ToolConfig,
  args: openArray[string]
): seq[string] =
  ## Builds one uv run coworld command that prints JSON.
  var command = coworldCommand(config, args)
  command.add "--json"
  command

proc coworldJson(config: ToolConfig, args: openArray[string]): JsonNode =
  ## Runs coworld and parses its JSON output.
  let command = coworldJsonCommand(config, args)
  let output = runCommand(command, workingDir = config.coworldDir).output
  parseJson(output)

proc gitShortHash(): string =
  ## Returns the current checkout's short git hash when available.
  let output = runCommand(
    ["git", "rev-parse", "--short", "HEAD"],
    workingDir = rootDir(),
    check = false
  )
  if output.code == 0:
    result = output.output.strip()
  if result.len == 0:
    result = "nogit"

proc generatedImageLabel(): string =
  ## Builds a unique local Docker tag label for checkout uploads.
  let
    worktree = normalizeBot(extractFilename(rootDir()))
    worktreePart =
      if worktree.len == 0 or worktree == "notsus":
        ""
      else:
        worktree & "-"
    stamp = now().format("yyyyMMddHHmmss")
  "notsus-" & worktreePart & gitShortHash() & "-" &
    stamp & "-p" & $getCurrentProcessId()

proc currentImageTag(config: ToolConfig): string =
  ## Returns the Docker image tag for a current-checkout upload.
  if config.imageTag.len > 0:
    return config.imageTag
  "crewrift-notsus:" & generatedImageLabel()

proc buildCurrentImage(image: string) =
  ## Builds the current checkout as a linux/amd64 player image.
  var command = @[
    "docker",
    "buildx",
    "build",
    "--platform",
    "linux/amd64",
    "--load",
    "-t",
    image,
    "--label",
    "notsus.upload-image=" & image,
    "-f",
    "players/notsus/Dockerfile",
    "."
  ]
  runAttached(command, workingDir = gameDir())

proc parseUploadLabel(output: string): string =
  ## Extracts the uploaded policy label from coworld output.
  const Prefix = "Upload complete:"
  for line in output.splitLines():
    let clean = line.strip()
    if clean.startsWith(Prefix):
      return clean[Prefix.len .. ^1].strip()
  fail("Could not parse upload-policy output: " & output)

proc uploadCurrentPolicy(config: ToolConfig): BotRef =
  ## Builds and uploads this checkout as a new Softmax policy.
  let
    policyName = config.uploadName
    image = currentImageTag(config)

  echo "Building current checkout: " & image
  buildCurrentImage(image)

  echo "Uploading current checkout as policy: " & policyName
  var command = coworldCommand(
    config,
    [
      "upload-policy",
      image,
      "--name",
      policyName,
      "--run",
      "/bin/notsus",
      "--use-bedrock",
      "--bedrock-model",
      config.bedrockModel,
      "--tag",
      "source=players/notsus/tools/run.nim",
      "--tag",
      "bedrock=use-bedrock",
      "--tag",
      "player=" & policyName,
      "--tag",
      "worktree=" & extractFilename(rootDir()),
      "--tag",
      "git=" & gitShortHash()
    ]
  )
  var output = ""
  for attempt in 0 ..< UploadAttempts:
    let commandResult = runCommand(
      command,
      workingDir = config.coworldDir,
      check = false
    )
    output = commandResult.output
    stdout.write output
    if commandResult.code == 0:
      let label = parseUploadLabel(output)
      return BotRef(
        input: "current",
        key: normalizeBot(label),
        label: label,
        policyId: label
      )
    if not output.isUploadImageNotReady() or attempt + 1 >= UploadAttempts:
      raise newException(
        OSError,
        "Command failed (" & $commandResult.code & "): " &
          command.commandText() & "\n" & output
      )
    echo "Upload image is not ready; retrying in ", UploadRetrySeconds,
      " seconds."
    sleep(UploadRetryMs)
  fail("Upload failed without returning a policy label.")

proc slotRolesJson(roles: openArray[string]): JsonNode =
  ## Builds full Crewrift slot overrides with fixed roles.
  result = newJArray()
  for role in roles:
    var slot = newJObject()
    if role.len > 0:
      slot["role"] = %role
    result.add slot

proc requestBody(config: ToolConfig): JsonNode =
  ## Builds the hosted XP request body.
  if config.slotRoles.len > 0 and config.slotRoles.len != config.bots.len:
    fail("Fixed slot roles must match the roster length.")
  result = newJObject()
  if config.coworldId.len > 0:
    result["target"] = %*{"coworld_id": config.coworldId}
  else:
    result["target"] = %*{"league_id": config.leagueId}
  if config.slotRoles.len > 0:
    result["game_config_overrides"] = newJObject()
    result["game_config_overrides"]["slots"] =
      slotRolesJson(config.slotRoles)
  result["roster"] = newJArray()
  for i, bot in config.bots:
    result["roster"].add %*{
      "player": {
        "policy_ref": bot.policyId
      },
      "slot": i
    }
  result["num_episodes"] = %config.games
  result["notes"] = %(
    "hosted Notsus run: " & config.name &
      ", generated by players/notsus/tools/run.nim"
  )

proc createRequest(config: ToolConfig, requestPath: string): JsonNode =
  ## Creates one hosted XP request from a request body path.
  let body = requestBody(config)
  writeFile(requestPath, body.pretty() & "\n")
  coworldJson(config, ["xp-request", "create", requestPath])

proc createRequest(config: ToolConfig, paths: RunPaths): JsonNode =
  ## Creates one hosted XP request and returns its detail JSON.
  createRequest(config, paths.request)

proc getRequest(config: ToolConfig, requestId: string): JsonNode =
  ## Fetches one hosted XP request by id.
  coworldJson(config, ["xp-request", "get", requestId])

proc getRequestWithRetry(
  config: ToolConfig,
  requestId: string,
  attempts: int
): JsonNode =
  ## Fetches one request with bounded retries for transient API errors.
  var failures = 0
  while true:
    try:
      return getRequest(config, requestId)
    except CatchableError as e:
      inc failures
      if not e.msg.isTransientPollError():
        fail("Request lookup failed: " & e.msg.shortCommandError())
      if failures >= attempts:
        fail(
          "Request lookup failed after " & $attempts &
            " tries: " & e.msg.shortCommandError()
        )
      echo "request lookup transient failure " & $failures & "/" &
        $attempts & ": " & e.msg.shortCommandError()
      sleep(config.pollBackoffMs(failures))

proc episodesNode(detail: JsonNode): JsonNode =
  ## Returns the episodes array from a request detail node.
  let episodes = detail.field("episodes")
  if episodes.kind == JArray:
    return episodes
  newJArray()

proc parseScores(row: JsonNode): seq[Score] =
  ## Parses the scores array from an episode row.
  let scores = row.field("scores")
  if scores.kind != JArray:
    return
  for item in scores:
    case item.kind
    of JObject:
      result.add Score(
        policyId: item.strField("policy_version_id"),
        score: item.numberField("score")
      )
    of JNull:
      discard
    else:
      result.add Score(score: item.scoreValue())

proc markExcluded(episode: var Episode, reason: string) =
  ## Marks one episode as excluded from score analysis.
  if reason.len == 0:
    return
  episode.excluded = true
  if episode.exclusionReason.len == 0:
    episode.exclusionReason = reason

proc statusExclusionReason(episode: Episode): string =
  ## Returns the hosted-status reason for excluding one episode.
  if episode.errorType.len > 0:
    return episode.errorType
  if episode.error.len > 0:
    return episode.error
  if episode.status == "failed":
    return "episode failed"
  if episode.status in ["cancelled", "crashed", "error"]:
    return "episode " & episode.status

proc parseEpisode(index: int, row: JsonNode): Episode =
  ## Parses one episode row from Coworld JSON.
  result = Episode(
    index: index,
    id: row.strField("id"),
    status: row.strField("status"),
    episodeId: row.strField("episode_id"),
    replayUrl: row.strField("replay_url"),
    liveUrl: row.strField("live_url"),
    errorType: row.strField("error_type"),
    error: row.strField("error"),
    scores: parseScores(row)
  )
  result.markExcluded(result.statusExclusionReason())

proc parseEpisodes(detail: JsonNode): seq[Episode] =
  ## Parses all episodes from a request detail node.
  let rows = detail.episodesNode()
  for i in 0 ..< rows.len:
    result.add parseEpisode(i + 1, rows[i])

proc syncBotRefsFromDetail(bots: var seq[BotRef], detail: JsonNode) =
  ## Updates bot labels and policy ids from XP request participants.
  let episodes = detail.episodesNode()
  if episodes.kind != JArray or episodes.len == 0:
    return
  let participants = episodes[0].field("participants")
  if participants.kind != JArray:
    return
  for participant in participants:
    let position = participant.intField("position", -1)
    let
      policyId = participant.strField("policy_version_id")
      label = participant.strField("label")
    var botIndex = -1
    for i in 0 ..< bots.len:
      let
        policyMatches = policyId.len > 0 and (
          bots[i].policyId == policyId or bots[i].input == policyId
        )
        labelMatches = label.len > 0 and (
          bots[i].label == label or bots[i].input == label
        )
      if policyMatches or labelMatches:
        botIndex = i
        break
    if botIndex < 0 and position >= 0 and position < bots.len:
      botIndex = position
    if botIndex < 0:
      continue
    if policyId.len > 0:
      bots[botIndex].policyId = policyId
    if label.len > 0:
      bots[botIndex].label = label

proc syncBotsFromDetail(config: var ToolConfig, detail: JsonNode) =
  ## Updates config bot labels and policy ids from XP request participants.
  config.bots.syncBotRefsFromDetail(detail)

proc scoreForSlot(
  episode: Episode,
  slot: int
): tuple[found: bool, score: float] =
  ## Finds one score by roster slot.
  if slot >= 0 and slot < episode.seatScores.len:
    let score = episode.seatScores[slot]
    if score.found:
      return (true, score.score)
  if slot >= 0 and slot < episode.scores.len:
    return (true, episode.scores[slot].score)
  (false, 0.0)

proc nearScore(value, target: float): bool =
  ## Returns true when a score is close enough to a target.
  abs(value - target) <= ScoreEpsilon

proc binaryScore(value: float): bool =
  ## Returns true when one score belongs to the binary result format.
  value.nearScore(0.0) or value.nearScore(1.0)

proc scoreFlags(scores: openArray[float]): tuple[
  binary,
  hasZero,
  hasOne: bool
] =
  ## Returns the binary-state flags for one score vector.
  if scores.len == 0:
    return (false, false, false)
  result.binary = true
  for score in scores:
    if not score.binaryScore():
      result.binary = false
      return
    if score.nearScore(0.0):
      result.hasZero = true
    if score.nearScore(1.0):
      result.hasOne = true

proc noResultBinaryScores(scores: openArray[float]): bool =
  ## Returns true when binary scores carry no winner signal.
  let flags = scores.scoreFlags()
  flags.binary and not (flags.hasZero and flags.hasOne)

proc recordOutcome(score: float, scores: openArray[float]): RecordOutcome =
  ## Classifies one score under the current score model.
  if scores.len == 0:
    return OutcomeMissing
  let flags = scores.scoreFlags()
  if flags.binary:
    if flags.hasZero and flags.hasOne:
      if score.nearScore(1.0):
        return OutcomeWin
      return OutcomeLoss
    return OutcomeMissing

  var
    best = scores[0]
    bestCount = 0
  for value in scores:
    if value > best:
      best = value
  for value in scores:
    if value.nearScore(best):
      inc bestCount
  if score + ScoreEpsilon < best:
    return OutcomeLoss
  if bestCount == scores.len:
    OutcomeTie
  else:
    OutcomeWin

proc winningScoreIndexes(scores: openArray[float]): seq[int] =
  ## Returns score indexes that should be marked as winners.
  let flags = scores.scoreFlags()
  if flags.binary:
    if not (flags.hasZero and flags.hasOne):
      return @[]
    for i, score in scores:
      if score.nearScore(1.0):
        result.add i
    return
  for i, score in scores:
    if score.recordOutcome(scores) == OutcomeWin:
      result.add i

proc playerSummaries(
  episodes: openArray[Episode],
  bots: openArray[BotRef]
): seq[PlayerSummary] =
  ## Summarizes average score and record for every roster slot.
  result = newSeq[PlayerSummary](bots.len)
  for episode in episodes:
    if episode.excluded:
      continue
    var
      complete = true
      scores = newSeq[float](bots.len)
    for i in 0 ..< bots.len:
      let score = episode.scoreForSlot(i)
      if not score.found:
        complete = false
        continue
      scores[i] = score.score
    if not complete:
      continue
    for i in 0 ..< bots.len:
      let outcome = scores[i].recordOutcome(scores)
      case outcome
      of OutcomeWin:
        inc result[i].wins
      of OutcomeLoss:
        inc result[i].losses
      of OutcomeTie:
        inc result[i].ties
      of OutcomeMissing:
        continue
      result[i].total += scores[i]
      inc result[i].scored
  for item in result.mitems:
    if item.scored > 0:
      item.avg = item.total / item.scored.float

proc groupSummaries(
  episodes: openArray[Episode],
  groups: openArray[BotGroup]
): seq[PlayerSummary] =
  ## Summarizes average score and record for every unique bot group.
  result = newSeq[PlayerSummary](groups.len)
  for episode in episodes:
    if episode.excluded:
      continue
    var
      complete = true
      scores = newSeq[float](groups.len)
    for i in 0 ..< groups.len:
      let score = episode.scoreForGroup(groups, i)
      if not score.found:
        complete = false
        continue
      scores[i] = score.score
    if not complete:
      continue
    for i in 0 ..< groups.len:
      let outcome = scores[i].recordOutcome(scores)
      case outcome
      of OutcomeWin:
        inc result[i].wins
      of OutcomeLoss:
        inc result[i].losses
      of OutcomeTie:
        inc result[i].ties
      of OutcomeMissing:
        continue
      result[i].total += scores[i]
      inc result[i].scored
  for item in result.mitems:
    if item.scored > 0:
      item.avg = item.total / item.scored.float

proc winnerGroups(
  episode: Episode,
  groups: openArray[BotGroup]
): seq[int] =
  ## Returns every bot group that should be shown as a winner.
  if episode.excluded:
    return @[]
  var scores = newSeq[float](groups.len)
  for i in 0 ..< groups.len:
    let score = episode.scoreForGroup(groups, i)
    if not score.found:
      return @[]
    scores[i] = score.score
  scores.winningScoreIndexes()

proc winnerSlots(episode: Episode, bots: openArray[BotRef]): seq[int] =
  ## Returns every slot that should be shown as a winner.
  if episode.excluded:
    return @[]
  var scores = newSeq[float](bots.len)
  for i in 0 ..< bots.len:
    let score = episode.scoreForSlot(i)
    if not score.found:
      return @[]
    scores[i] = score.score
  scores.winningScoreIndexes()

proc winnerText(episode: Episode, bots: openArray[BotRef]): string =
  ## Returns a compact winner label for one episode.
  let winners = winnerSlots(episode, bots)
  if winners.len == 0:
    return "-"
  for i, winner in winners:
    if i > 0:
      result.add ", "
    result.add "P" & $winner

proc scoreText(score: tuple[found: bool, score: float]): string =
  ## Returns one compact score string.
  if score.found:
    return numberText(score.score, 2)
  "-"

proc printNameKey(bots: openArray[BotRef], labels: openArray[string]) =
  ## Prints the full-name to short-name key.
  echo "names:"
  var seen: seq[string]
  for i, bot in bots:
    let full = botTitle(bot)
    if full in seen:
      continue
    seen.add full
    echo "  " & full.align(34) & "  " & labels[i]

proc summaryFor(
  episodes: openArray[Episode],
  bots: openArray[BotRef]
): Summary =
  ## Summarizes hosted episode rows from the first bot group's perspective.
  let groups = groupedBots(bots)
  for episode in episodes:
    case episode.status
    of "completed":
      inc result.completed
    of "failed":
      inc result.failed
    of "running":
      inc result.running
    else:
      inc result.pending

    if episode.excluded:
      inc result.excluded
      continue

    if groups.len >= 2:
      let a = episode.scoreForGroup(groups, 0)
      if a.found:
        var
          bestOpponent = -1.0e300
          complete = true
          scores = newSeq[float](groups.len)
        scores[0] = a.score
        for i in 1 ..< groups.len:
          let b = episode.scoreForGroup(groups, i)
          if not b.found:
            complete = false
            break
          scores[i] = b.score
          bestOpponent = max(bestOpponent, b.score)
        if complete:
          let outcome = a.score.recordOutcome(scores)
          case outcome
          of OutcomeWin:
            inc result.wins
          of OutcomeLoss:
            inc result.losses
          of OutcomeTie:
            inc result.ties
          of OutcomeMissing:
            continue
          result.totalA += a.score
          result.totalB += bestOpponent

  let scored = result.wins + result.losses + result.ties
  result.margin = result.totalA - result.totalB
  if scored > 0:
    result.avgA = result.totalA / scored.float
    result.avgB = result.totalB / scored.float
    result.avgMargin = result.margin / scored.float

proc scoredGames(summary: Summary): int =
  ## Returns games that counted in score summaries.
  summary.wins + summary.losses + summary.ties

proc shortId(value: string): string =
  ## Returns a compact identifier for terminal tables.
  if value.len <= 18:
    return value
  value[0 .. 17]

proc statusText(detail: JsonNode): string =
  ## Returns a compact hosted request status line.
  "status=" & detail.strField("status") &
    " completed=" & $detail.intField("completed_count") &
    " running=" & $detail.intField("running_count") &
    " failed=" & $detail.intField("failed_count") &
    " pending=" & $detail.intField("pending_count")

proc isTerminalStatus(status: string): bool =
  ## Returns true when an XP request status is no longer running.
  status notin ["pending", "submitted", "running"]

proc printStatus(detail: JsonNode) =
  ## Prints a compact hosted request status line.
  echo detail.statusText()

proc focusSummaryFor(
  episodes: openArray[Episode],
  groups: openArray[BotGroup]
): RunFocus

proc printTable(episodes: openArray[Episode], bots: openArray[BotRef]) =
  ## Prints the final command-line score table.
  if bots.len == 0:
    return
  echo ""
  let labels = shortBotLabels(bots)
  printNameKey(bots, labels)
  echo ""
  var header = "game  episode             "
  for i in 0 ..< bots.len:
    header.add ("player " & i.playerLetter()).align(18)
    header.add "  "
  header.add "winner"
  echo header
  for episode in episodes:
    let winners = winnerSlots(episode, bots)
    var line = ($episode.index).align(4) & "  " &
      episode.id.shortId().align(18) & "  "
    for i in 0 ..< bots.len:
      let score = episode.scoreForSlot(i)
      var text = labels[i] & " " & score.scoreText()
      if i in winners:
        text.add "*"
      line.add text.align(18) & "  "
    line.add episode.winnerText(bots)
    echo line

  let summary = summaryFor(episodes, bots)
  let players = playerSummaries(episodes, bots)
  var scoreText: seq[string]
  for i, player in players:
    scoreText.add "P" & $i & " avg=" & numberText(player.avg, 2) &
      " W=" & $player.wins &
      " T=" & $player.ties &
      " L=" & $player.losses
  let
    groups = groupedBots(bots)
    focus = focusSummaryFor(episodes, groups)
  if focus.scored > 0:
    echo "focus " & focus.label &
      " wins=" & $focus.wins &
      " losses=" & $focus.losses &
      " ties=" & $focus.ties &
      " avg=" & numberText(focus.avg, 2) &
      " vs " & focus.opponentLabel & "=" &
      numberText(focus.opponentAvg, 2) &
      " avg_margin=" & numberText(focus.avgMargin, 2) &
      " excluded=" & $summary.excluded &
      " players=[" & scoreText.join(", ") & "]"
  else:
    echo "slot0 wins=" & $summary.wins &
      " losses=" & $summary.losses &
      " ties=" & $summary.ties &
      " avg=" & numberText(summary.avgA, 2) &
      " vs best_opponent=" & numberText(summary.avgB, 2) &
      " avg_margin=" & numberText(summary.avgMargin, 2) &
      " excluded=" & $summary.excluded &
      " players=[" & scoreText.join(", ") & "]"

proc copyAssets(outDir, tufteDir: string) =
  ## Copies Tufte CSS and fonts into the static report root.
  createDir(outDir)
  var css = readFile(tufteDir / "tufte.css")
  for font in PreloadFonts:
    css = css.replace(
      "url(\"fonts/" & font & "\") format(\"opentype\");\n" &
        "  font-display: swap;",
      "url(\"fonts/" & font & "\") format(\"opentype\");\n" &
        "  font-display: block;"
    )
  writeFile(outDir / "tufte.css", css)
  let fontsDir = tufteDir / "fonts"
  if dirExists(fontsDir):
    createDir(outDir / "fonts")
    for file in walkFiles(fontsDir / "*"):
      copyFile(file, outDir / "fonts" / extractFilename(file))

proc assetPrefix(config: ToolConfig): string =
  ## Returns the configured shared asset URL prefix.
  result = config.assetPrefix.replace("\\", "/").strip()
  while result.endsWith("/"):
    result.setLen(result.len - 1)

proc assetHref(config: ToolConfig, depth: int): string =
  ## Returns the stylesheet URL for a page below the output root.
  for i in 0 ..< depth:
    result.add "../"
  let prefix = config.assetPrefix()
  if prefix.len > 0:
    result.add prefix & "/"
  result.add "tufte.css"

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
  result.add "    .runs-index { table-layout: fixed; width: 100%; }\n"
  result.add "    .runs-index .number-col { width: 3rem; }\n"
  result.add "    .runs-index .name-col { width: 19%; }\n"
  result.add "    .runs-index .score-col { width: 5rem; }\n"
  result.add "    .runs-index .win-col { width: 5rem; }\n"
  result.add "    .runs-index .count-col { width: 5.5rem; }\n"
  result.add "    .runs-index .time-col { width: 6rem; }\n"
  result.add "    table:not(.no-sort) th { cursor: pointer; }\n"
  result.add "    table:not(.no-sort) th.sort-active { font-weight: 700; }\n"
  result.add "    .result-win { color: #b00000; font-weight: 700; }\n"
  result.add "    .games-table { table-layout: fixed; width: 100%; }\n"
  result.add "    .games-table .game-col { width: 11rem; }\n"
  result.add "    .games-table .side-col { width: 5rem; }\n"
  result.add "    .games-table .score-col { width: 5rem; }\n"
  result.add "    .games-table .seat-name-col { width: 2.2rem; }\n"
  result.add "    .games-table .seat-score-col { width: 3.8rem; }\n"
  result.add "    .games-table .result-col { width: 4rem; }\n"
  result.add "    .games-table .reason-col { width: 11rem; }\n"
  result.add "    .games-table th, .games-table td { "
  result.add "padding-right: 0.45rem; white-space: nowrap; }\n"
  result.add "    .games-table .seat-score-cell { font-variant-numeric: "
  result.add "tabular-nums; text-align: right; }\n"
  result.add "    .games-table .seat-name-cell { text-align: left; }\n"
  result.add "    .games-table .clip-cell { overflow: hidden; "
  result.add "text-overflow: ellipsis; }\n"
  result.add "    .games-table .clip-cell a { display: block; "
  result.add "overflow: hidden; text-overflow: ellipsis; "
  result.add "white-space: nowrap; }\n"
  result.add "    .games-table tr.excluded-game td:not(.reason-cell) { "
  result.add "color: #777; text-decoration: line-through; }\n"
  result.add "    .games-table tr.excluded-game .reason-cell { "
  result.add "color: #777; font-style: italic; text-decoration: none; }\n"
  result.add "    .games-table .reason-cell { overflow: hidden; "
  result.add "text-overflow: ellipsis; }\n"
  result.add "    .log-muted { color: #777; }\n"
  result.add "    .log-missing { color: #b00000; font-style: italic; }\n"
  result.add "    .log-health td, .log-health th { white-space: nowrap; }\n"
  result.add "    .log-health .notes-cell { white-space: normal; }\n"
  result.add "    .runs-index .bot-name { display: block; "
  result.add "max-width: 100%; overflow: hidden; text-overflow: ellipsis; "
  result.add "white-space: nowrap; }\n"
  result.add "    .runs-index .name-cell { text-align: left; }\n"
  result.add "    .runs-index .score-cell, .runs-index .count-cell, "
  result.add ".runs-index .time-cell, .runs-index .win-cell { "
  result.add "text-align: right; "
  result.add "white-space: nowrap; }\n"
  result.add "    .version-chart-wrap { margin: 1rem 0 1.5rem; }\n"
  result.add "    .version-chart-svg { display: block; max-width: 100%; "
  result.add "height: auto; margin: 0 auto; shape-rendering: crispEdges; }\n"
  result.add "    .version-chart-svg text { font-family: inherit; "
  result.add "font-size: 0.82rem; shape-rendering: auto; }\n"
  result.add "    .version-chart-axis { stroke: #111; stroke-width: 1; "
  result.add "fill: none; }\n"
  result.add "    .version-chart-grid { stroke: rgba(0,0,0,0.18); "
  result.add "stroke-width: 1; }\n"
  result.add "    .version-chart-step { stroke: #000; stroke-width: 1.25; "
  result.add "fill: none; opacity: 0.5; shape-rendering: auto; }\n"
  result.add "    .version-chart-dot { fill: #000; stroke: #000; "
  result.add "stroke-width: 1; shape-rendering: auto; }\n"
  result.add scoreChartCss()
  result.add replayExtractorCss()
  result.add "  </style>\n"
  result.add "</head>\n<body>\n<main>\n"

proc sortScript(): string =
  ## Returns the table sorting script for generated reports.
  """
<script>
(() => {
  function cellValue(row, index) {
    const cell = row.children[index];
    if (!cell) {
      return "";
    }
    return cell.dataset.sort || cell.textContent.trim();
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

proc pageEnd(): string =
  ## Returns the closing HTML for one static page.
  "</main>\n" & sortScript() & "</body>\n</html>\n"

proc softmaxDetailUrl(kind, id: string): string =
  ## Returns one Observatory detail URL for a Softmax object id.
  let tab =
    case kind
    of "experience-request":
      "experience-requests"
    of "episode-request":
      "episodes"
    else:
      "overview"
  SoftmaxObservatoryUrl & "#tab=" & tab & "&detail=" & kind & ":" & id

proc linkHtml(href, text: string, newTab = false): string =
  ## Renders one HTML link.
  result.add "<a href=\"" & href.htmlEscape() & "\""
  if newTab:
    result.add " target=\"_blank\" rel=\"noopener noreferrer\""
  result.add ">" & text.htmlEscape() & "</a>"

proc markHtml(text: string): string =
  ## Renders highlighted HTML using the Tufte mark color.
  "<mark>" & text.htmlEscape() & "</mark>"

proc tableCell(text: string, numeric = false): string =
  ## Renders one HTML table cell.
  if numeric:
    "<td>" & text.htmlEscape() & "</td>"
  else:
    "<td>" & text.htmlEscape() & "</td>"

proc tableHtmlCell(html: string, numeric = false): string {.used.} =
  ## Renders one table cell from already-escaped HTML.
  if numeric:
    "<td>" & html & "</td>"
  else:
    "<td>" & html & "</td>"

proc tableHtmlClass(html, className: string): string =
  ## Renders one table cell from HTML with a CSS class.
  "<td class=\"" & className.htmlEscape() & "\">" & html & "</td>"

proc tableHtmlClassSort(html, className, sortValue: string): string =
  ## Renders one HTML table cell with a sortable value.
  "<td class=\"" & className.htmlEscape() & "\" data-sort=\"" &
    sortValue.htmlEscape() & "\">" & html & "</td>"

proc clipHtmlCell(html, title, className: string): string =
  ## Renders one ellipsized table cell from escaped HTML.
  "<td class=\"clip-cell " & className.htmlEscape() & "\" title=\"" &
    title.htmlEscape() & "\">" & html & "</td>"

proc arrayField(node: JsonNode, key: string): JsonNode =
  ## Returns an array field or an empty array when it is absent.
  let child = node.field(key)
  if child.kind == JArray:
    return child
  newJArray()

proc metaBotLabel(meta: JsonNode, index: int): string =
  ## Returns one bot label from stored run metadata.
  let bots = meta.arrayField("bots")
  if index >= 0 and index < bots.len:
    let label = bots[index].strField("label")
    if label.len > 0:
      return label

  let parts = meta.strField("name").split(" vs ")
  if index >= 0 and index < parts.len:
    return parts[index]
  "Player " & $(index + 1)

proc metaBotRefs(meta: JsonNode): seq[BotRef] =
  ## Returns bot references from stored run metadata.
  let bots = meta.arrayField("bots")
  for i in 0 ..< bots.len:
    var bot = BotRef(
      input: bots[i].strField("input"),
      key: bots[i].strField("key"),
      label: bots[i].strField("label"),
      policyId: bots[i].strField("policy_id")
    )
    if bot.input.len == 0:
      bot.input = bot.label
    if bot.label.len == 0:
      bot.label = "Player " & $(i + 1)
    if bot.key.len == 0:
      bot.key = normalizeBot(bot.label)
    result.add bot

proc indexBotHtml(slug, label: string): string =
  ## Renders one ellipsized bot-name link for the run index.
  let
    href = slug & "/index.html"
    cleanHref = href.htmlEscape()
    cleanLabel = label.htmlEscape()
  "<a class=\"bot-name\" href=\"" & cleanHref & "\" title=\"" &
    cleanLabel & "\">" & cleanLabel & "</a>"

proc indexScoreHtml(score: string, won: bool): string =
  ## Renders one run-index score value.
  if won:
    return markHtml(score)
  score.htmlEscape()

proc winRate(wins, games: int): float =
  ## Returns the plain win rate over requested games.
  if games == 0:
    return -1.0
  wins.float / games.float

proc winRateText(value: float): string =
  ## Formats one win-rate value as a percentage.
  if value < 0.0:
    return "-"
  numberText(value * 100.0, 1) & "%"

proc episodeScoresJson(
  episode: Episode,
  bots: openArray[BotRef]
): JsonNode =
  ## Converts one episode's bot-group scores to JSON.
  result = newJArray()
  var found = false
  let groups = groupedBots(bots)
  for i in 0 ..< groups.len:
    let score = episode.scoreForGroup(groups, i)
    if score.found:
      result.add %score.score
      found = true
    else:
      result.add newJNull()
  if not found:
    result = newJArray()

proc runMetaJson(
  config: ToolConfig,
  paths: RunPaths,
  detail: JsonNode,
  episodes: openArray[Episode],
  runNo: int,
  created: string,
  durationSeconds = -1.0
): JsonNode =
  ## Builds persistent JSON metadata for a static run.
  let summary = summaryFor(episodes, config.bots)
  let gameCount =
    if detail.intField("episode_count") > 0:
      detail.intField("episode_count")
    else:
      episodes.len
  let scored = summary.scoredGames()
  result = newJObject()
  result["run_number"] = %runNo
  result["name"] = %config.name
  result["slug"] = %extractFilename(paths.root)
  result["created_at"] = %created
  if durationSeconds >= 0.0:
    result["duration_seconds"] = %durationSeconds
    result["duration"] = %durationText(durationSeconds)
  result["request_id"] = %detail.strField("id")
  result["status"] = %detail.strField("status")
  if config.focusIndex >= 0:
    result["focus_index"] = %config.focusIndex
  result["games"] = %gameCount
  result["completed"] = %summary.completed
  result["failed"] = %summary.failed
  result["running"] = %summary.running
  result["pending"] = %summary.pending
  result["excluded"] = %summary.excluded
  result["valid_games"] = %scored
  result["wins"] = %summary.wins
  result["losses"] = %summary.losses
  result["ties"] = %summary.ties
  result["win_rate"] = %winRate(summary.wins, scored)
  result["avg_a"] = %summary.avgA
  result["avg_b"] = %summary.avgB
  result["avg_margin"] = %summary.avgMargin
  result["bots"] = newJArray()
  for bot in config.bots:
    result["bots"].add %*{
      "input": bot.input,
      "key": bot.key,
      "label": bot.label,
      "policy_id": bot.policyId
    }
  result["episodes"] = newJArray()
  for episode in episodes:
    var row = %*{
      "index": episode.index,
      "id": episode.id,
      "status": episode.status,
      "episode_id": episode.episodeId,
      "replay_url": episode.replayUrl,
      "live_url": episode.liveUrl,
      "error_type": episode.errorType,
      "error": episode.error,
      "excluded": episode.excluded,
      "exclusion_reason": episode.exclusionReason,
      "winner": episode.winnerText(config.bots)
    }
    if not episode.excluded:
      let scores = episode.episodeScoresJson(config.bots)
      if scores.len > 0:
        row["scores"] = scores
    result["episodes"].add row

proc writeRunMeta(
  config: ToolConfig,
  paths: RunPaths,
  detail: JsonNode,
  episodes: openArray[Episode],
  runNo: int,
  created: string,
  durationSeconds = -1.0
) =
  ## Writes the run metadata JSON file.
  let meta = runMetaJson(
    config,
    paths,
    detail,
    episodes,
    runNo,
    created,
    durationSeconds
  )
  writeFile(paths.meta, meta.pretty() & "\n")

proc loadRunMetas(outDir: string): seq[JsonNode] =
  ## Loads all run metadata files from the static report root.
  if not dirExists(outDir):
    return
  for kind, path in walkDir(outDir):
    if kind == pcDir and fileExists(path / "run.json"):
      try:
        result.add parseFile(path / "run.json")
      except CatchableError:
        discard
  result.sort(
    proc(a, b: JsonNode): int =
      result = cmp(b.strField("created_at"), a.strField("created_at"))
      if result == 0:
        result = cmp(b.intField("run_number"), a.intField("run_number"))
  )

proc gameFileName(index: int): string =
  ## Returns the HTML file name for one game.
  "game-" & ($index).align(3, '0') & ".html"

proc replayFileName(index: int): string =
  ## Returns the replay artifact file name for one game.
  "game-" & ($index).align(3, '0') & ".z"

proc logFileName(gameIndex, slot: int): string =
  ## Returns the player log artifact file name for one game and seat.
  "game-" & ($gameIndex).align(3, '0') & "-" & slot.playerLetter() & ".txt"

proc botVersion(label: string): int =
  ## Returns the trailing version number from a bot label.
  let marker = label.rfind(":v")
  if marker < 0 or marker + 2 >= label.len:
    return -1
  let value = label[marker + 2 .. ^1]
  try:
    value.parseInt()
  except ValueError:
    -1

proc notsusVersion(label: string): int =
  ## Returns the Notsus version number from one bot label.
  if not label.toLowerAscii().startsWith("notsus:v"):
    return -1
  label.botVersion()

proc focusLabelVersion(label: string): int =
  ## Returns the version for one focused bot label or key.
  result = label.notsusVersion()
  if result > 0:
    return
  let
    clean = label.normalizeBot()
    prefix = DefaultUploadName.normalizeBot() & "v"
  if not clean.startsWith(prefix):
    return -1
  let version = clean[prefix.len .. ^1]
  if version.len == 0:
    return -1
  try:
    result = version.parseInt()
  except ValueError:
    result = -1

proc matchesFocusBot(label: string): bool =
  ## Returns true when one label belongs to the optimized bot family.
  let
    clean = label.normalizeBot()
    target = DefaultUploadName.normalizeBot()
  clean == target or clean.startsWith(target & "v")

proc focusVersion(group: BotGroup): int =
  ## Returns the best focused-bot version seen in one bot group.
  result = -1
  for label in [group.key, group.label, group.title]:
    result = max(result, label.focusLabelVersion())
  for label in group.policyIds:
    result = max(result, label.focusLabelVersion())
  if result < 0 and (
      group.key.matchesFocusBot() or group.label.matchesFocusBot() or
      group.title.matchesFocusBot()):
    result = 0

proc focusGroupIndex(groups: openArray[BotGroup]): int =
  ## Returns the optimized bot group index, falling back to roster order.
  result = -1
  var bestVersion = -2
  for i, group in groups:
    let matches = group.key.matchesFocusBot() or
      group.label.matchesFocusBot() or group.title.matchesFocusBot()
    var idMatches = false
    for label in group.policyIds:
      if label.matchesFocusBot():
        idMatches = true
        break
    if not matches and not idMatches:
      continue
    let version = group.focusVersion()
    if result < 0 or version > bestVersion:
      result = i
      bestVersion = version
  if result < 0 and groups.len > 0:
    result = 0

proc configuredFocusIndex(
  config: ToolConfig,
  groups: openArray[BotGroup]
): int =
  ## Returns the configured focus group or the optimized bot group.
  if config.focusIndex >= 0 and config.focusIndex < groups.len:
    return config.focusIndex
  groups.focusGroupIndex()

proc metaFocusIndex(meta: JsonNode, groups: openArray[BotGroup]): int =
  ## Returns the stored focus group or the optimized bot group.
  let focusIndex = meta.intField("focus_index", -1)
  if focusIndex >= 0 and focusIndex < groups.len:
    return focusIndex
  groups.focusGroupIndex()

proc focusDisplayLabel(group: BotGroup): string =
  ## Returns the raw focused-bot label used on top-level reports.
  if group.title.len > 0:
    return group.title
  if group.label.len > 0:
    return group.label
  group.key

proc opponentDisplayLabel(
  groups: openArray[BotGroup],
  focusIndex: int
): string =
  ## Returns the compact opponent label for one focused run.
  if groups.len == 2:
    for i, group in groups:
      if i != focusIndex:
        return group.focusDisplayLabel()
  "best opponent"

proc initRunFocus(
  groups: openArray[BotGroup],
  focusIndex: int
): RunFocus =
  ## Builds one empty focus summary from bot groups.
  result.groupIndex = focusIndex
  result.opponentLabel = "best opponent"
  if focusIndex >= 0 and focusIndex < groups.len:
    result.found = true
    result.label = groups[focusIndex].focusDisplayLabel()
    result.abbr = groups[focusIndex].abbr
    result.opponentLabel = groups.opponentDisplayLabel(focusIndex)
  else:
    result.label = "Player 0"
    result.abbr = "Player 0"

proc finishRunFocus(
  focus: var RunFocus,
  total,
  opponentTotal: float
) =
  ## Finalizes aggregate averages for one focus summary.
  if focus.scored > 0:
    focus.avg = total / focus.scored.float
    focus.opponentAvg = opponentTotal / focus.scored.float
    focus.avgMargin = focus.avg - focus.opponentAvg

proc focusSummaryFor(
  episodes: openArray[Episode],
  groups: openArray[BotGroup],
  focusIndex: int
): RunFocus =
  ## Summarizes one focused bot group from loaded episodes.
  result = groups.initRunFocus(focusIndex)
  if focusIndex < 0 or focusIndex >= groups.len:
    return
  var
    total = 0.0
    opponentTotal = 0.0
  for episode in episodes:
    if episode.excluded:
      continue
    let focusScore = episode.scoreForGroup(groups, focusIndex)
    if not focusScore.found:
      continue
    var
      foundOpponent = false
      bestOpponent = -1.0e300
      scores: seq[float]
    scores.setLen(groups.len)
    scores[focusIndex] = focusScore.score
    for i in 0 ..< groups.len:
      if i == focusIndex:
        continue
      let score = episode.scoreForGroup(groups, i)
      if score.found and score.score > bestOpponent:
        bestOpponent = score.score
        foundOpponent = true
      if score.found:
        scores[i] = score.score
      else:
        foundOpponent = false
        break
    if not foundOpponent:
      continue
    let outcome = focusScore.score.recordOutcome(scores)
    case outcome
    of OutcomeWin:
      inc result.wins
    of OutcomeLoss:
      inc result.losses
    of OutcomeTie:
      inc result.ties
    of OutcomeMissing:
      continue
    total += focusScore.score
    opponentTotal += bestOpponent
    inc result.scored
  result.finishRunFocus(total, opponentTotal)

proc focusSummaryFor(
  episodes: openArray[Episode],
  groups: openArray[BotGroup]
): RunFocus =
  ## Summarizes the optimized bot group from loaded episodes.
  episodes.focusSummaryFor(groups, groups.focusGroupIndex())

proc hasScoreValue(node: JsonNode): bool =
  ## Returns true when a JSON node can be read as a score.
  case node.kind
  of JInt, JFloat:
    true
  of JObject:
    let score = node.field("score")
    score.kind in {JInt, JFloat}
  else:
    false

proc legacyFocusSummary(meta: JsonNode): RunFocus =
  ## Returns the old slot-zero summary for legacy run metadata.
  result = RunFocus(
    found: true,
    groupIndex: 0,
    label: meta.metaBotLabel(0),
    abbr: "Player 0",
    opponentLabel: "best opponent",
    scored: meta.intField("completed", meta.intField("games")),
    wins: meta.intField("wins"),
    losses: meta.intField("losses"),
    ties: meta.intField("ties"),
    avg: meta.numberField("avg_a"),
    opponentAvg: meta.numberField("avg_b"),
    avgMargin: meta.numberField("avg_margin")
  )

proc focusSummaryFor(meta: JsonNode): RunFocus =
  ## Summarizes the optimized bot group from stored run metadata.
  let groups = groupedBots(meta.metaBotRefs())
  let focusIndex = meta.metaFocusIndex(groups)
  result = groups.initRunFocus(focusIndex)
  if focusIndex < 0 or focusIndex >= groups.len:
    return meta.legacyFocusSummary()
  var
    total = 0.0
    opponentTotal = 0.0
  for episode in meta.arrayField("episodes"):
    if episode.boolField("excluded"):
      continue
    let scores = episode.arrayField("scores")
    if focusIndex >= scores.len or not scores[focusIndex].hasScoreValue():
      continue
    var
      foundOpponent = false
      bestOpponent = -1.0e300
      values: seq[float]
    values.setLen(scores.len)
    values[focusIndex] = scores[focusIndex].scoreValue()
    for i in 0 ..< scores.len:
      if i == focusIndex:
        continue
      if not scores[i].hasScoreValue():
        foundOpponent = false
        break
      let score = scores[i].scoreValue()
      if score > bestOpponent:
        bestOpponent = score
        foundOpponent = true
      values[i] = score
    if not foundOpponent:
      continue
    let focusScore = scores[focusIndex].scoreValue()
    let outcome = focusScore.recordOutcome(values)
    case outcome
    of OutcomeWin:
      inc result.wins
    of OutcomeLoss:
      inc result.losses
    of OutcomeTie:
      inc result.ties
    of OutcomeMissing:
      continue
    total += focusScore
    opponentTotal += bestOpponent
    inc result.scored
  if result.scored == 0:
    return meta.legacyFocusSummary()
  result.finishRunFocus(total, opponentTotal)

proc versionAverageIndex(
  averages: var seq[VersionAverage],
  version: int
): int =
  ## Returns an existing version average index or creates one.
  for i, average in averages:
    if average.version == version:
      return i
  averages.add VersionAverage(version: version)
  averages.len - 1

proc addVersionAverage(
  averages: var seq[VersionAverage],
  version: int,
  score: float,
  games: int
) =
  ## Adds one weighted run average to a version bucket.
  if version <= 0 or games <= 0:
    return
  let index = averages.versionAverageIndex(version)
  averages[index].totalScore += score * games.float
  averages[index].games += games

proc averageScore(average: VersionAverage): float =
  ## Returns the weighted score average for one version bucket.
  if average.games <= 0:
    return 0.0
  average.totalScore / average.games.float

proc versionChartX(version, minVersion, maxVersion: int): int =
  ## Returns the SVG x coordinate for one Notsus version.
  let
    plotWidth = VersionChartWidth - VersionChartLeft - VersionChartRight
    range = max(1, maxVersion - minVersion)
    scaled =
      ((version - minVersion).float / range.float) * plotWidth.float
  VersionChartLeft + int(scaled + 0.5)

proc versionChartY(score: float): int =
  ## Returns the SVG y coordinate for one score.
  var clamped = score
  if clamped < VersionChartScoreMin.float:
    clamped = VersionChartScoreMin.float
  if clamped > VersionChartScoreMax.float:
    clamped = VersionChartScoreMax.float
  let
    plotHeight = VersionChartHeight - VersionChartTop - VersionChartBottom
    scoreRange = VersionChartScoreMax - VersionChartScoreMin
    scaled = (
      (VersionChartScoreMax.float - clamped) / scoreRange.float
    ) * plotHeight.float
  VersionChartTop + int(scaled + 0.5)

proc renderVersionProgressChart(metas: openArray[JsonNode]): string =
  ## Renders average Notsus score progression by version.
  var
    averages: seq[VersionAverage]
    minVersion = high(int)
    maxVersion = low(int)
  for meta in metas:
    let
      focus = meta.focusSummaryFor()
      version = focus.label.focusLabelVersion()
      games = focus.scored
      score = focus.avg
    if version <= 0 or games <= 0:
      continue
    averages.addVersionAverage(version, score, games)
    if version < minVersion:
      minVersion = version
    if version > maxVersion:
      maxVersion = version
  if averages.len == 0:
    return ""
  averages.sort do (a, b: VersionAverage) -> int:
    cmp(a.version, b.version)

  let
    plotRight = VersionChartWidth - VersionChartRight
    plotBottom = VersionChartHeight - VersionChartBottom
  result.add "<div class=\"version-chart-wrap\">\n"
  result.add "<svg class=\"version-chart-svg\" width=\""
  result.add $VersionChartWidth & "\" height=\"" & $VersionChartHeight
  result.add "\" viewBox=\"0 0 " & $VersionChartWidth & " "
  result.add $VersionChartHeight
  result.add "\" role=\"img\" aria-label=\"Notsus average score by version\">\n"
  var score = VersionChartScoreMin
  while score <= VersionChartScoreMax + 0.000001:
    let y = versionChartY(score)
    result.add "<line class=\"version-chart-grid\" x1=\""
    result.add $VersionChartLeft & "\" y1=\"" & $y & "\" x2=\""
    result.add $plotRight & "\" y2=\"" & $y & "\"></line>\n"
    result.add "<text x=\"" & $(VersionChartLeft - 10) & "\" y=\""
    result.add $(y + 4) & "\" text-anchor=\"end\">"
    result.add numberText(score, 4).htmlEscape() & "</text>\n"
    score += VersionChartScoreStep
  result.add "<path class=\"version-chart-axis\" d=\"M "
  result.add $VersionChartLeft & " " & $VersionChartTop
  result.add " V " & $plotBottom & " H " & $plotRight & "\"></path>\n"
  for version in minVersion .. maxVersion:
    let x = versionChartX(version, minVersion, maxVersion)
    result.add "<line class=\"version-chart-axis\" x1=\"" & $x
    result.add "\" y1=\"" & $plotBottom & "\" x2=\"" & $x
    result.add "\" y2=\"" & $(plotBottom + 4) & "\"></line>\n"
    if version == minVersion or version mod 5 == 0 or
      version == maxVersion:
      result.add "<text x=\"" & $x & "\" y=\""
      result.add $(plotBottom + 24) & "\" text-anchor=\"middle\">v"
      result.add ($version).htmlEscape() & "</text>\n"

  var
    path = ""
    bestScore = -1.0e300
    bestY = plotBottom
  for average in averages:
    let
      score = average.averageScore()
      x = versionChartX(average.version, minVersion, maxVersion)
    if path.len == 0:
      bestScore = score
      bestY = versionChartY(score)
      path.add "M " & $x & " " & $bestY
    elif score > bestScore + ScoreEpsilon:
      let nextY = versionChartY(score)
      path.add " H " & $x & " V " & $nextY
      bestScore = score
      bestY = nextY
  path.add " H " & $plotRight
  result.add "<path class=\"version-chart-step\" d=\""
  result.add path.htmlEscape() & "\"></path>\n"
  for average in averages:
    let
      score = average.averageScore()
      x = versionChartX(average.version, minVersion, maxVersion)
      y = versionChartY(score)
      title = "notsus:v" & $average.version & " avg " &
        numberText(score, 2) & " over " & $average.games & " games"
    result.add "<circle class=\"version-chart-dot\" cx=\"" & $x
    result.add "\" cy=\"" & $y & "\" r=\"3.75\"><title>"
    result.add title.htmlEscape() & "</title></circle>\n"
  result.add "</svg>\n</div>\n"

proc scoreOutcome(score, bestOpponent: float): ScoreChartOutcome =
  ## Returns a chart outcome for one score against best opponent.
  if score > bestOpponent + ScoreEpsilon:
    ScoreWin
  elif bestOpponent > score + ScoreEpsilon:
    ScoreLoss
  else:
    ScoreTie

proc scoreGroupIndex(
  groups: var seq[RunScoreGroup],
  label: string
): int =
  ## Returns an existing score group index or creates one.
  for i, group in groups:
    if group.label == label:
      return i
  groups.add RunScoreGroup(
    label: label,
    version: label.botVersion()
  )
  groups.len - 1

proc addRunScorePoint(
  groups: var seq[RunScoreGroup],
  label: string,
  score: float,
  outcome: ScoreChartOutcome,
  title,
  sortKey: string
) =
  ## Adds one run score point to a grouped chart row.
  if label.len == 0:
    return
  let index = groups.scoreGroupIndex(label)
  groups[index].points.add ScoreChartPoint(
    rowId: label,
    score: score,
    outcome: outcome,
    title: title,
    sortKey: sortKey
  )

proc addEpisodeScorePoints(
  groups: var seq[RunScoreGroup],
  meta: JsonNode
): bool =
  ## Adds per-game score points from run metadata when available.
  let
    botGroups = groupedBots(meta.metaBotRefs())
    focusIndex = meta.metaFocusIndex(botGroups)
    runNumber = meta.intField("run_number")
    created = meta.strField("created_at")
  if focusIndex < 0 or focusIndex >= botGroups.len:
    return false
  let label = botGroups[focusIndex].focusDisplayLabel()
  for episode in meta.arrayField("episodes"):
    if episode.boolField("excluded"):
      continue
    let scores = episode.arrayField("scores")
    if focusIndex >= scores.len or not scores[focusIndex].hasScoreValue():
      continue
    var
      foundOpponent = false
      values = newSeq[float](scores.len)
      complete = true
    for i in 0 ..< scores.len:
      if not scores[i].hasScoreValue():
        complete = false
        break
      values[i] = scores[i].scoreValue()
      if i == focusIndex:
        continue
      foundOpponent = true
    if not complete or not foundOpponent:
      continue
    let score = values[focusIndex]
    let record = score.recordOutcome(values)
    if record == OutcomeMissing:
      continue
    let
      outcome =
        case record
        of OutcomeWin:
          ScoreWin
        of OutcomeLoss:
          ScoreLoss
        of OutcomeTie:
          ScoreTie
        of OutcomeMissing:
          ScoreLoss
      gameIndex = episode.intField("index")
      title = label & " " & numberText(score, 2) & ", " &
        outcome.scoreChartOutcomeText() & ", run " & $runNumber &
        " game " & $gameIndex
      sortKey = created & "-" & ($gameIndex).align(4, '0')
    groups.addRunScorePoint(
      label,
      score,
      outcome,
      title,
      sortKey
    )
    result = true

proc addPairScorePoints(
  groups: var seq[RunScoreGroup],
  meta: JsonNode
): bool =
  ## Adds legacy per-game score points from stored pair rows.
  let
    label = meta.focusSummaryFor().label
    runNumber = meta.intField("run_number")
    created = meta.strField("created_at")
  for pair in meta.arrayField("pairs"):
    if pair.strField("status") != "completed" and
      pair.intField("completed") <= 0:
        continue
    let
      slot0 = pair.strField("slot_0")
      slot1 = pair.strField("slot_1")
      gameIndex = pair.intField("game_index", pair.intField("index"))
    var
      found = false
      score = 0.0
      bestOpponent = 0.0
    if slot0 == label:
      score = pair.numberField("avg_a")
      bestOpponent = pair.numberField("avg_b")
      found = true
    elif slot1 == label:
      score = pair.numberField("avg_b")
      bestOpponent = pair.numberField("avg_a")
      found = true
    if not found:
      continue
    let
      outcome = score.scoreOutcome(bestOpponent)
      title = label & " " & numberText(score, 2) & ", " &
        outcome.scoreChartOutcomeText() & ", run " & $runNumber &
        " game " & $gameIndex
      sortKey = created & "-" & ($gameIndex).align(4, '0')
    groups.addRunScorePoint(
      label,
      score,
      outcome,
      title,
      sortKey
    )
    result = true

proc addReplayScorePoints(
  groups: var seq[RunScoreGroup],
  outDir: string,
  meta: JsonNode
): bool =
  ## Replay score decoding is intentionally disabled for Crewrift Prime for now.
  discard groups
  discard outDir
  discard meta
  false

proc addAverageScorePoint(
  groups: var seq[RunScoreGroup],
  meta: JsonNode
) =
  ## Adds one average score point for older run metadata.
  let
    focus = meta.focusSummaryFor()
    completed = meta.intField("completed")
  if completed <= 0:
    return
  let
    label = focus.label
    runNumber = meta.intField("run_number")
    score = focus.avg
    bestOpponent = focus.opponentAvg
    outcome = score.scoreOutcome(bestOpponent)
    title = label & " avg " & numberText(score, 2) & ", " &
      outcome.scoreChartOutcomeText() & ", run " & $runNumber &
      ", " & $completed & " games"
    sortKey = meta.strField("created_at")
  groups.addRunScorePoint(
    label,
    score,
    outcome,
    title,
    sortKey
  )

proc runScoreChart(
  outDir: string,
  metas: openArray[JsonNode]
): string =
  ## Renders the top-level run score chart by bot version.
  var groups: seq[RunScoreGroup]
  for meta in metas:
    if groups.addEpisodeScorePoints(meta):
      continue
    if groups.addPairScorePoints(meta):
      continue
    if groups.addReplayScorePoints(outDir, meta):
      continue
    groups.addAverageScorePoint(meta)
  groups.sort do (a, b: RunScoreGroup) -> int:
    result = cmp(b.version, a.version)
    if result == 0:
      result = cmp(b.label, a.label)
  var
    rows: seq[ScoreChartRow]
    points: seq[ScoreChartPoint]
  for group in groups:
    rows.add ScoreChartRow(id: group.label, label: group.label)
    for point in group.points:
      points.add point
  if points.len == 0:
    return ""
  result.add "<section>\n"
  result.add "<h2>Scores by version</h2>\n"
  let scoreScale = ScoreChartScale(
    minScore: 0.0,
    maxScore: 1.0,
    tickStep: 0.1,
    histogramStep: 0.01
  )
  result.add renderScoreChart(
    rows,
    points,
    "No completed run scores yet.",
    scoreScale
  )
  result.add "</section>\n"

proc writeMainIndex(config: ToolConfig) =
  ## Writes the top-level static index of all hosted runs.
  if config.assetPrefix().len == 0:
    copyAssets(config.outDir, config.tufteDir)
  let metas = loadRunMetas(config.outDir)
  var html = pageStart("Crewrift Prime", config.assetHref(0))
  html.add "<section>\n"
  html.add "<h1>Crewrift Prime</h1>\n"
  html.add "<p>Static hosted-run reports generated by "
  html.add "<code>players/notsus/tools/run.nim</code>.</p>\n"
  html.add renderVersionProgressChart(metas)
  html.add "<!-- Bottom score chart hidden while binary scoring is active. -->\n"
  html.add "<table class=\"wide runs-index\">\n"
  html.add "<colgroup><col class=\"number-col\"><col class=\"name-col\">"
  html.add "<col class=\"score-col\"><col class=\"name-col\">"
  html.add "<col class=\"score-col\"><col class=\"win-col\">"
  html.add "<col class=\"count-col\"><col class=\"time-col\"></colgroup>\n"
  html.add "<thead><tr><th>#</th><th>Player</th><th>Avg</th>"
  html.add "<th>Best Opp</th><th>Avg</th><th>Win%</th>"
  html.add "<th>Games</th><th>Run time</th>"
  html.add "</tr></thead>\n<tbody>\n"
  for i, meta in metas:
    let
      focus = meta.focusSummaryFor()
      slug = meta.strField("slug")
      playerA = focus.label
      playerB = focus.opponentLabel
      avgA = focus.avg
      avgB = focus.opponentAvg
      scoreA = fixedNumberText(avgA, 4)
      scoreB = fixedNumberText(avgB, 4)
      wonA = avgA > avgB + ScoreEpsilon
      wins = focus.wins
      scored = focus.scored
      games = meta.intField("games")
      valid = meta.intField("valid_games", scored)
      gamesDisplay =
        if games > 0 and valid != games:
          $valid & "/" & $games
        else:
          $games
      rate = winRate(wins, scored)
      rateText = rate.winRateText()
      durationSeconds = meta.numberField("duration_seconds", -1.0)
      duration = durationText(durationSeconds)
      runNumber = meta.intField("run_number", i + 1)
    html.add "<tr>"
    html.add tableHtmlClassSort(
      $runNumber,
      "count-cell",
      $runNumber
    )
    html.add indexBotHtml(slug, playerA).tableHtmlClass("name-cell")
    html.add tableHtmlClassSort(
      indexScoreHtml(scoreA, wonA),
      "score-cell",
      numberText(avgA, 6)
    )
    html.add indexBotHtml(slug, playerB).tableHtmlClass("name-cell")
    html.add tableHtmlClassSort(
      indexScoreHtml(scoreB, false),
      "score-cell",
      numberText(avgB, 6)
    )
    html.add tableHtmlClassSort(
      rateText.htmlEscape(),
      "win-cell",
      numberText(rate, 6)
    )
    html.add tableHtmlClassSort(
      gamesDisplay,
      "count-cell",
      $games
    )
    html.add tableHtmlClassSort(
      duration.htmlEscape(),
      "time-cell",
      numberText(durationSeconds, 3)
    )
    html.add "</tr>\n"
  html.add "</tbody></table>\n"
  html.add "</section>\n"
  html.add pageEnd()
  writeFile(config.outDir / "index.html", html)

proc seatsText(slots: openArray[int]): string =
  ## Renders compact seat letters for one bot group.
  if slots.len == 0:
    return "-"
  var clean: seq[int]
  for slot in slots:
    clean.add slot
  clean.sort(system.cmp[int])
  var
    i = 0
    parts: seq[string]
  while i < clean.len:
    let start = clean[i]
    var finish = start
    while i + 1 < clean.len and clean[i + 1] == finish + 1:
      inc i
      finish = clean[i]
    if start == finish:
      parts.add start.playerLetter()
    else:
      parts.add start.playerLetter() & "-" & finish.playerLetter()
    inc i
  parts.join(", ")

proc gameRowClass(episode: Episode): string =
  ## Returns the CSS class for one game row.
  if episode.excluded:
    return " class=\"excluded-game\""
  ""

proc exclusionCell(episode: Episode): string =
  ## Renders the exclusion note for one game row.
  if episode.excluded:
    return episode.exclusionReason.tableHtmlClass("reason-cell")
  "-".tableHtmlClass("reason-cell")

proc seatScoreCellsHtml(
  episode: Episode,
  groups: openArray[BotGroup],
  slot: int,
  winners: openArray[int]
): string =
  ## Renders compact seat name and score cells.
  let groupIndex = groups.groupForSlot(slot)
  if groupIndex < 0:
    return "-".tableHtmlClass("seat-name-cell") &
      "-".tableHtmlClass("seat-score-cell")
  let score = episode.scoreForGroup(groups, groupIndex)
  var
    nameHtml = groups[groupIndex].abbr.htmlEscape()
    scoreHtml = score.scoreText().htmlEscape()
  if groupIndex in winners:
    nameHtml = "<mark>" & nameHtml & "</mark>"
    scoreHtml = "<mark>" & scoreHtml & "</mark>"
  nameHtml.tableHtmlClass("seat-name-cell") &
    scoreHtml.tableHtmlClass("seat-score-cell")

proc renderRunIndex(
  config: ToolConfig,
  paths: RunPaths,
  detail: JsonNode,
  episodes: openArray[Episode],
  runNo: int,
  durationSeconds = -1.0
) =
  ## Writes the per-run static index page.
  let
    groups = groupedBots(config.bots)
    summary = summaryFor(episodes, config.bots)
    groupStats = groupSummaries(episodes, groups)
    focus = focusSummaryFor(episodes, groups)
    validGames =
      if focus.scored > 0:
        focus.scored
      else:
        summary.scoredGames()
    firstLabel =
      if focus.abbr.len > 0:
        focus.abbr
      elif groups.len > 0:
        groups[0].abbr
      else:
        "First bot"
  var html = pageStart(config.name, config.assetHref(1))
  html.add "<section>\n"
  html.add "<p><a href=\"../index.html\">all runs</a></p>\n"
  html.add "<h1>" & config.name.htmlEscape() & "</h1>\n"
  html.add "<p>Run <b>" & $runNo & "</b>, "
  html.add linkHtml(
    softmaxDetailUrl("experience-request", detail.strField("id")),
    "Softmax XP request"
  )
  html.add ".</p>\n"
  html.add "<ul>\n"
  html.add "<li>Status: " & detail.strField("status").htmlEscape() & "</li>\n"
  html.add "<li>Games: " & $episodes.len & " total, "
  html.add $validGames & " valid, " & $summary.excluded
  html.add " excluded</li>\n"
  html.add "<li>" & firstLabel.htmlEscape()
  html.add " record versus best opponent: "
  if focus.scored > 0:
    html.add $focus.wins & "-" & $focus.losses
    html.add "-" & $focus.ties & "</li>\n"
  else:
    html.add $summary.wins & "-" & $summary.losses
    html.add "-" & $summary.ties & "</li>\n"
  html.add "<li>" & firstLabel.htmlEscape() & " average score: "
  if focus.scored > 0:
    html.add numberText(focus.avg, 2) & "</li>\n"
  else:
    html.add numberText(summary.avgA, 2) & "</li>\n"
  html.add "<li>Average margin: "
  if focus.scored > 0:
    html.add numberText(focus.avgMargin, 2) & "</li>\n"
  else:
    html.add numberText(summary.avgMargin, 2) & "</li>\n"
  if durationSeconds >= 0.0:
    html.add "<li>Run time: " & durationText(durationSeconds) & "</li>\n"
  html.add "</ul>\n"
  html.add "<table class=\"wide no-sort name-key\">\n"
  html.add "<thead><tr><th>Abbr</th><th>Bot</th><th>Seats</th>"
  html.add "<th>Average</th><th>Wins</th><th>Ties</th>"
  html.add "<th>Losses</th></tr></thead>\n"
  html.add "<tbody>\n"
  for i, group in groups:
    let stats =
      if i < groupStats.len:
        groupStats[i]
      else:
        PlayerSummary()
    html.add "<tr>"
    html.add group.abbr.tableCell()
    html.add group.label.tableCell()
    html.add group.slots.seatsText().tableCell()
    html.add numberText(stats.avg, 2).tableCell(numeric = true)
    html.add ($stats.wins).tableCell(numeric = true)
    html.add ($stats.ties).tableCell(numeric = true)
    html.add ($stats.losses).tableCell(numeric = true)
    html.add "</tr>\n"
  html.add "</tbody></table>\n"
  html.add "<table class=\"wide games-table\">\n"
  html.add "<colgroup><col class=\"game-col\">"
  for i in 0 ..< config.bots.len:
    html.add "<col class=\"seat-name-col\">"
    html.add "<col class=\"seat-score-col\">"
  html.add "<col class=\"reason-col\">"
  html.add "</colgroup>\n"
  html.add "<thead><tr><th>Game</th>"
  for i in 0 ..< config.bots.len:
    html.add "<th>" & i.playerLetter().htmlEscape() & "</th>"
    html.add "<th></th>"
  html.add "<th>Note</th>"
  html.add "</tr></thead>\n<tbody>\n"
  for episode in episodes:
    let gameLabel =
      if episode.id.len > 0:
        episode.id.shortId()
      else:
        $episode.index
    html.add "<tr" & episode.gameRowClass() & ">"
    html.add clipHtmlCell(
      linkHtml(gameFileName(episode.index), gameLabel),
      gameLabel,
      "game-cell"
    )
    let winners = episode.winnerGroups(groups)
    for i in 0 ..< config.bots.len:
      html.add episode.seatScoreCellsHtml(groups, i, winners)
    html.add episode.exclusionCell()
    html.add "</tr>\n"
  html.add "</tbody></table>\n"
  html.add "</section>\n"
  html.add pageEnd()
  writeFile(paths.root / "index.html", html)

proc renderPlayerLogHealthHtml(
  paths: RunPaths,
  logHrefs: openArray[string]
): string

proc renderReplayHtml(
  paths: RunPaths,
  episode: var Episode,
  bots: openArray[BotRef],
  replayPath,
  replayHref: string,
  logHrefs: openArray[string]
): string =
  ## Renders replay metadata and the extracted game event log.
  var labels: seq[string]
  for bot in bots:
    labels.add bot.label
  let rendered = renderReplayBodyForPath(
    replayPath,
    replayHref,
    labels,
    logHrefs,
    bots.len
  )
  if not rendered.quality.valid:
    episode.markExcluded(rendered.quality.reason)
  result.add "<section>\n"
  result.add "<h1>Crewrift Prime Game " & $episode.index & "</h1>\n"
  result.add "<ul>\n"
  result.add "<li>Episode request: "
  result.add linkHtml(
    softmaxDetailUrl("episode-request", episode.id),
    "Softmax episode",
    newTab = true
  )
  result.add "</li>\n"
  result.add "<li>Replay artifact: <a href=\""
  result.add replayHref.htmlEscape()
  result.add "\">download</a></li>\n"
  result.add "<li>Status: " & episode.status.htmlEscape() & "</li>\n"
  if episode.excluded:
    result.add "<li>Analysis: excluded"
    if episode.exclusionReason.len > 0:
      result.add " - " & episode.exclusionReason.htmlEscape()
    result.add "</li>\n"
  if episode.liveUrl.len > 0:
    result.add "<li><a href=\"" & episode.liveUrl.htmlEscape()
    result.add "\">Live view</a></li>\n"
  if episode.error.len > 0:
    result.add "<li>Error: " & episode.error.htmlEscape() & "</li>\n"
  result.add "</ul>\n"
  result.add "</section>\n"
  result.add renderPlayerLogHealthHtml(paths, logHrefs)
  result.add rendered.html

proc renderPendingHtml(episode: Episode, gameIndex = -1): string =
  ## Renders one episode page before a replay is available.
  let index =
    if gameIndex > 0:
      gameIndex
    else:
      episode.index
  result.add "<section>\n"
  result.add "<h1>Crewrift Prime Game " & $index & "</h1>\n"
  result.add "<ul>\n"
  result.add "<li>Episode request: "
  result.add linkHtml(
    softmaxDetailUrl("episode-request", episode.id),
    "Softmax episode",
    newTab = true
  )
  result.add "</li>\n"
  if episode.replayUrl.len > 0:
    result.add "<li>Replay artifact: <a href=\""
    result.add episode.replayUrl.htmlEscape()
    result.add "\">download</a></li>\n"
  result.add "<li>Status: " & episode.status.htmlEscape() & "</li>\n"
  if episode.excluded:
    result.add "<li>Analysis: excluded"
    if episode.exclusionReason.len > 0:
      result.add " - " & episode.exclusionReason.htmlEscape()
    result.add "</li>\n"
  if episode.liveUrl.len > 0:
    result.add "<li><a href=\"" & episode.liveUrl.htmlEscape()
    result.add "\">Live view</a></li>\n"
  if episode.error.len > 0:
    result.add "<li>Error: " & episode.error.htmlEscape() & "</li>\n"
  result.add "</ul>\n"
  result.add "</section>\n"

proc redactLogTokens(text: string): string =
  ## Redacts websocket token query values from copied player logs.
  const Prefix = "token="
  var i = 0
  while i < text.len:
    if i + Prefix.len <= text.len and text[i ..< i + Prefix.len] == Prefix:
      result.add Prefix & "<redacted>"
      i += Prefix.len
      while i < text.len and text[i] notin {'&', ' ', '\t', '\r', '\n'}:
        inc i
    else:
      result.add text[i]
      inc i

proc markerInt(text, marker: string): int =
  ## Returns the integer after one marker or zero when absent.
  let start = text.find(marker)
  if start < 0:
    return 0
  var
    i = start + marker.len
    value = ""
  while i < text.len and text[i] in {'0' .. '9'}:
    value.add text[i]
    inc i
  if value.len == 0:
    return 0
  try:
    value.parseInt()
  except ValueError:
    0

proc finalStatusText(line: string): string =
  ## Extracts the interstitial text from a connection-lost line.
  let start = line.find(" text=")
  if start < 0:
    return ""
  let
    valueStart = start + " text=".len
    stop = line.find(" framesDropped=", valueStart)
  if stop > valueStart:
    return line[valueStart ..< stop]
  line[valueStart .. ^1]

proc normalFinalStatus(text: string): bool =
  ## Returns true when one connection close is a normal game end.
  text in ["CREW WINS", "IMPS WIN", "DRAW"]

proc summarizePlayerLog(path: string): PlayerLogSummary =
  ## Summarizes one downloaded player log for report health tables.
  if not fileExists(path):
    return
  result.found = true
  for line in readFile(path).splitLines():
    inc result.lineCount
    let lower = line.toLowerAscii()
    if "notsus bedrock usage:" in lower:
      inc result.bedrockCalls
    if "notsus bedrock error:" in lower or
        "bedrock invokemodel failed" in lower or
        "returned http 4" in lower or
        "returned http 5" in lower:
      inc result.bedrockErrors
    if "bedrock sidecar health: http=200" in lower:
      inc result.healthOk
    if "bedrock sidecar health pending" in lower or
        "bedrock sidecar health error" in lower:
      inc result.healthPending
    if "notsus sent voting chat:" in lower:
      inc result.sentChat
    if "notsus voting llm no social output" in lower or
        "notsus voting llm fallback: invalid social json" in lower:
      inc result.noOutput
    if "duplicate chat suppressed" in lower:
      inc result.duplicateSuppressed
    if "notsus voting llm skipped: self is dead" in lower:
      inc result.deadSkips
    if lower.startsWith("frames dropped:"):
      result.frameDrops = max(result.frameDrops, line.markerInt("total="))
    if "connection lost:" in lower:
      result.finalStatus = line.finalStatusText()
      result.normalEnd = result.finalStatus.normalFinalStatus()
  if result.bedrockErrors > 0:
    result.notes.add "bedrock errors=" & $result.bedrockErrors
  if result.healthPending > 0 and result.bedrockCalls == 0:
    result.notes.add "sidecar pending, no calls"
  elif result.healthPending > 0:
    result.notes.add "sidecar startup race"
  if result.frameDrops >= 50:
    result.notes.add "frame drops=" & $result.frameDrops
  if result.deadSkips > 20:
    result.notes.add "dead skips=" & $result.deadSkips
  if result.noOutput > 5:
    result.notes.add "empty llm=" & $result.noOutput
  if result.duplicateSuppressed > 0:
    result.notes.add "dupes=" & $result.duplicateSuppressed
  if result.finalStatus.len > 0 and not result.normalEnd:
    result.notes.add "closed: " & result.finalStatus

proc hrefNote(href, prefix: string): string =
  ## Returns the status note after one synthetic log href prefix.
  if not href.startsWith(prefix):
    return ""
  result = href[prefix.len .. ^1]
  if result.len == 0:
    result = "-"

proc realLogHref(href: string): bool =
  ## Returns true when a log href points at an actual local text file.
  href.len > 0 and
    not href.startsWith(LogMissingPrefix) and
    not href.startsWith(LogNotRequestedPrefix)

proc renderPlayerLogHealthHtml(
  paths: RunPaths,
  logHrefs: openArray[string]
): string =
  ## Renders a compact per-seat player log health table.
  var any = false
  for href in logHrefs:
    if href.len > 0:
      any = true
      break
  if not any:
    return ""
  result.add "<section>\n"
  result.add "<h2>Log Health</h2>\n"
  result.add "<table class=\"wide no-sort log-health\">\n"
  result.add "<thead><tr><th>Seat</th><th>Status</th><th>Lines</th>"
  result.add "<th>Bedrock</th><th>Chat</th><th>Dead</th>"
  result.add "<th>Drops</th><th>End</th><th>Notes</th></tr></thead>\n"
  result.add "<tbody>\n"
  for slot, href in logHrefs:
    var
      status = "-"
      lineCount = "-"
      bedrock = "-"
      chat = "-"
      dead = "-"
      drops = "-"
      finalStatus = "-"
      notes = "-"
    if href.realLogHref():
      let summary = summarizePlayerLog(paths.root / href)
      if summary.found:
        status =
          if summary.notes.len > 0:
            "warning"
          else:
            "ok"
        lineCount = $summary.lineCount
        bedrock =
          $summary.bedrockCalls & "/" & $summary.bedrockErrors
        chat = $summary.sentChat
        dead = $summary.deadSkips
        drops = $summary.frameDrops
        finalStatus =
          if summary.finalStatus.len > 0:
            summary.finalStatus
          else:
            "-"
        if summary.notes.len > 0:
          notes = summary.notes.join(", ")
      else:
        status = "missing"
        notes = "file not found"
    elif href.startsWith(LogMissingPrefix):
      status = "missing"
      notes = href.hrefNote(LogMissingPrefix)
    elif href.startsWith(LogNotRequestedPrefix):
      status = "opponent"
      notes = href.hrefNote(LogNotRequestedPrefix)
    result.add "<tr>"
    result.add slot.playerLetter().tableCell()
    result.add status.tableCell()
    result.add lineCount.tableCell(numeric = true)
    result.add bedrock.tableCell(numeric = true)
    result.add chat.tableCell(numeric = true)
    result.add dead.tableCell(numeric = true)
    result.add drops.tableCell(numeric = true)
    result.add finalStatus.tableCell()
    result.add notes.htmlEscape().tableHtmlClass("notes-cell")
    result.add "</tr>\n"
  result.add "</tbody></table>\n"
  result.add "</section>\n"

proc downloadReplay(episode: Episode, path: string): string =
  ## Downloads one replay artifact and returns an error summary on failure.
  if episode.replayUrl.len == 0 or fileExists(path):
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
        episode.replayUrl
      ]
    )
    moveFile(tmpPath, path)
  except CatchableError as e:
    result = e.msg.shortCommandError()
    if fileExists(tmpPath):
      removeFile(tmpPath)

proc downloadPlayerLog(
  config: ToolConfig,
  episode: Episode,
  slot: int,
  path: string
): string =
  ## Downloads one readable player log and returns an error summary on failure.
  if episode.id.len == 0 or fileExists(path):
    return
  try:
    let output = runCommand(
      coworldCommand(config, ["episode-logs", episode.id, "--agent", $slot]),
      workingDir = config.coworldDir,
      check = false,
      timeoutMs = PlayerLogTimeoutMs
    )
    if output.code != 0:
      return "log command failed: " & $output.code
    path.parentDir().createDir()
    writeFile(path, output.output.redactLogTokens())
  except CatchableError as e:
    result = e.msg.shortCommandError()

proc focusedLogHrefs(
  config: ToolConfig,
  paths: RunPaths,
  episode: Episode,
  gameIndex: int
): seq[string] =
  ## Downloads focused-player logs and returns per-seat hrefs that exist.
  result = newSeq[string](config.bots.len)
  let
    groups = groupedBots(config.bots)
    focusIndex = config.configuredFocusIndex(groups)
  if focusIndex < 0 or focusIndex >= groups.len:
    return
  for i in 0 ..< result.len:
    result[i] = LogNotRequestedPrefix & "opponent"
  for slot in groups[focusIndex].slots:
    if slot < 0 or slot >= result.len:
      continue
    let
      fileName = logFileName(gameIndex, slot)
      path = paths.logs / fileName
    var error = ""
    if config.downloadReplays:
      error = downloadPlayerLog(config, episode, slot, path)
    if fileExists(path):
      result[slot] = "logs/" & fileName
    elif error.len > 0:
      result[slot] = LogMissingPrefix & error
    elif config.downloadReplays:
      result[slot] = LogMissingPrefix & "not returned"
    else:
      result[slot] = LogMissingPrefix & "not downloaded"

proc groupScoreVector(
  episode: Episode,
  groups: openArray[BotGroup]
): seq[float] =
  ## Returns complete group scores for one episode or an empty vector.
  if groups.len == 0:
    return
  result.setLen(groups.len)
  for i in 0 ..< groups.len:
    let score = episode.scoreForGroup(groups, i)
    if not score.found:
      result.setLen(0)
      return
    result[i] = score.score

proc replayGroupWinScores(
  replayPath: string,
  groups: openArray[BotGroup]
): seq[float] =
  ## Returns binary group scores from explicit replay win flags.
  let players = replayPlayerResultsForPath(replayPath)
  var slots: seq[tuple[found: bool, won: bool]]
  for player in players:
    if player.slot < 0:
      continue
    if not player.hasResult:
      continue
    while slots.len <= player.slot:
      slots.add (found: false, won: false)
    slots[player.slot] = (found: true, won: player.won)

  result.setLen(groups.len)
  var
    hasZero = false
    hasOne = false
  for i, group in groups:
    var
      wins = 0
      count = 0
    for slot in group.slots:
      if slot >= 0 and slot < slots.len and slots[slot].found:
        if slots[slot].won:
          inc wins
        inc count
    if count == 0:
      result.setLen(0)
      return
    if wins == 0:
      result[i] = 0.0
      hasZero = true
    elif wins == count:
      result[i] = 1.0
      hasOne = true
    else:
      result.setLen(0)
      return
  if not (hasZero and hasOne):
    result.setLen(0)

proc replayBinaryScores(
  replayPath: string,
  groups: openArray[BotGroup]
): seq[float] =
  ## Returns binary scores recovered from explicit replay win flags.
  replayGroupWinScores(replayPath, groups)

proc setGroupScores(episode: var Episode, scores: openArray[float]) =
  ## Replaces episode scores with group-level scores.
  if scores.len == 0:
    return
  while episode.scores.len < scores.len:
    episode.scores.add Score()
  if episode.scores.len > scores.len:
    episode.scores.setLen(scores.len)
  for i, score in scores:
    episode.scores[i].score = score

proc fillReplayScores(
  episodes: var seq[Episode],
  config: ToolConfig,
  paths: RunPaths,
  gameOffset = 0
) =
  ## Recovers no-signal binary timeout rows from replay score tables.
  let groups = groupedBots(config.bots)
  if groups.len < 2:
    return
  for episode in episodes.mitems:
    if episode.excluded or episode.status != "completed":
      continue
    let scores = episode.groupScoreVector(groups)
    if scores.len == 0 or not scores.noResultBinaryScores():
      continue
    let replayPath = paths.replays / replayFileName(gameOffset + episode.index)
    if not fileExists(replayPath):
      continue
    try:
      let recovered = replayBinaryScores(replayPath, groups)
      if recovered.len > 0:
        episode.setGroupScores(recovered)
    except CatchableError as e:
      episode.markExcluded(
        "replay score parse error: " & e.msg.shortCommandError()
      )

proc renderGamePages(
  config: ToolConfig,
  paths: RunPaths,
  episodes: var seq[Episode],
  gameOffset = 0
) =
  ## Writes one static HTML page for every hosted game.
  for episode in episodes.mitems:
    let gameIndex = gameOffset + episode.index
    let
      replayPath = paths.replays / replayFileName(gameIndex)
      gamePath = paths.root / gameFileName(gameIndex)
    var logHrefs = newSeq[string](config.bots.len)
    if episode.status == "completed" or fileExists(replayPath):
      logHrefs = focusedLogHrefs(config, paths, episode, gameIndex)
    var replayError = ""
    if config.downloadReplays and episode.status == "completed":
      replayError = episode.downloadReplay(replayPath)

    var html = pageStart(
      "Game " & $gameIndex & " - " & config.name,
      config.assetHref(1)
    )
    html.add "<p><a href=\"index.html\">run summary</a></p>\n"
    if fileExists(replayPath):
      try:
        html.add renderReplayHtml(
          paths,
          episode,
          config.bots,
          replayPath,
          "replays/" & replayFileName(gameIndex),
          logHrefs
        )
      except CatchableError as e:
        episode.markExcluded("replay parse error: " & e.msg.shortCommandError())
        html.add renderPendingHtml(episode, gameIndex)
        html.add "<section><h2>Replay parse error</h2><p>"
        html.add e.msg.htmlEscape() & "</p></section>\n"
    else:
      html.add renderPendingHtml(episode, gameIndex)
      if replayError.len > 0:
        html.add "<section><h2>Replay download pending</h2><p>"
        html.add replayError.htmlEscape() & "</p></section>\n"
    html.add pageEnd()
    writeFile(gamePath, html)

proc renderAll(
  config: ToolConfig,
  paths: RunPaths,
  detail: JsonNode,
  runNo: int,
  created: string,
  durationSeconds = -1.0
): seq[Episode] =
  ## Writes all static pages and run metadata for the current state.
  result = parseEpisodes(detail)
  renderGamePages(config, paths, result)
  result.fillReplayScores(config, paths)
  writeRunMeta(
    config,
    paths,
    detail,
    result,
    runNo,
    created,
    durationSeconds
  )
  writeMainIndex(config)
  renderRunIndex(config, paths, detail, result, runNo, durationSeconds)

proc botFromMeta(node: JsonNode, index: int): BotRef =
  ## Builds one bot reference from stored run metadata.
  result = BotRef(
    input: node.strField("input"),
    key: node.strField("key"),
    label: node.strField("label"),
    policyId: node.strField("policy_id")
  )
  if result.input.len == 0:
    result.input = result.label
  if result.key.len == 0:
    result.key = normalizeBot(result.label)
  if result.label.len == 0:
    result.label = "Player " & $(index + 1)
  if result.policyId.len == 0:
    fail("Stored bot " & $(index + 1) & " is missing policy_id.")

proc botsFromMeta(
  meta: JsonNode,
  expectedCount = PlayerLetters.len
): seq[BotRef] =
  ## Reads bot references from stored run metadata.
  let bots = meta.arrayField("bots")
  if bots.len != expectedCount:
    fail(
      "Stored run metadata must contain exactly " &
        $expectedCount & " bots."
    )
  for i in 0 ..< bots.len:
    result.add botFromMeta(bots[i], i)

proc configForRunMeta(
  base: ToolConfig,
  runRoot: string,
  meta: JsonNode
): ToolConfig =
  ## Builds a repair config from stored run metadata.
  result = base
  result.outDir = runRoot.parentDir()
  result.name = meta.strField("name")
  result.requestId = meta.strField("request_id")
  result.bots = botsFromMeta(meta)
  result.uploadCurrent = false
  if result.name.len == 0:
    result.name = rosterTitle(result.bots)
  if result.requestId.len == 0:
    fail("Stored run metadata is missing request_id.")

proc configForVersusRunMeta(
  base: ToolConfig,
  runRoot: string,
  meta: JsonNode
): ToolConfig =
  ## Builds a versus repair config from stored run metadata.
  result = base
  result.outDir = runRoot.parentDir()
  result.name = meta.strField("name")
  result.bots = botsFromMeta(meta, meta.arrayField("bots").len)
  result.uploadCurrent = false
  result.games = meta.intField("games")
  result.focusIndex = meta.intField("focus_index", 0)
  result.vsMode = true
  result.vsOpponents.setLen(0)
  if result.bots.len < 2:
    fail("Stored versus run metadata must contain at least two bots.")
  for i in 1 ..< result.bots.len:
    result.vsOpponents.add result.bots[i]
  if result.name.len == 0:
    result.name = result.bots.versusTitle()
  if result.games <= 0:
    fail("Stored versus run metadata is missing games.")

proc requestIdsFromMeta(meta: JsonNode): seq[string] =
  ## Reads stored hosted request ids from run metadata.
  for item in meta.arrayField("request_ids"):
    if item.kind == JString and item.getStr().len > 0:
      result.add item.getStr()
  if result.len == 0:
    for id in meta.strField("request_id").split(","):
      let clean = id.strip()
      if clean.len > 0:
        result.add clean
  if result.len == 0:
    fail("Stored run metadata is missing request ids.")

proc versusSegments(
  config: ToolConfig,
  paths: RunPaths
): seq[VersusSegment]

proc renderVersusAll(
  config: ToolConfig,
  paths: RunPaths,
  segments: var seq[VersusSegment],
  runNo: int,
  created: string,
  durationSeconds = -1.0
): seq[Episode]

proc printVersusStatus(segments: openArray[VersusSegment])

proc printVersusTable(
  episodes: openArray[Episode],
  config: ToolConfig,
  segments: openArray[VersusSegment]
)

proc repairVersusRun(
  config: ToolConfig,
  runRoot: string,
  paths: RunPaths,
  meta: JsonNode,
  runNo: int,
  created: string,
  durationSeconds: float
) =
  ## Refreshes one existing direct versus run folder.
  var repairConfig = configForVersusRunMeta(config, runRoot, meta)
  let requestIds = meta.requestIdsFromMeta()
  var segments = repairConfig.versusSegments(paths)
  if requestIds.len != segments.len:
    fail("Stored versus run has unexpected request count.")
  echo "Repairing versus: " & runRoot
  for i in 0 ..< segments.len:
    let requestId = requestIds[i]
    echo segments[i].label & " request: " & requestId
    segments[i].detail = getRequestWithRetry(repairConfig, requestId, 6)
    segments[i].config.syncBotsFromDetail(segments[i].detail)
  let episodes = renderVersusAll(
    repairConfig,
    paths,
    segments,
    runNo,
    created,
    durationSeconds
  )
  printVersusStatus(segments)
  printVersusTable(episodes, repairConfig, segments)
  echo "Report: " & paths.root / "index.html"

proc repairRun(config: ToolConfig, runPath: string) =
  ## Refreshes one existing run folder from its hosted XP request.
  let runRoot = runPath.absolutePath()
  let metaPath = runRoot / "run.json"
  if not fileExists(metaPath):
    fail("--repair-run is missing run.json: " & runRoot)

  let
    meta = parseFile(metaPath)
    paths = pathsForRoot(runRoot)
    runNo = meta.intField("run_number", runNumber(runRoot.parentDir()))
    created =
      if meta.strField("created_at").len > 0:
        meta.strField("created_at")
      else:
        createdText()
    durationSeconds = meta.numberField("duration_seconds", -1.0)

  if meta.strField("mode") == "vs":
    repairVersusRun(
      config,
      runRoot,
      paths,
      meta,
      runNo,
      created,
      durationSeconds
    )
    return

  var repairConfig = configForRunMeta(config, runRoot, meta)

  echo "Repairing: " & runRoot
  echo "Request: " & repairConfig.requestId
  var detail = getRequestWithRetry(
    repairConfig,
    repairConfig.requestId,
    6
  )
  repairConfig.syncBotsFromDetail(detail)
  let episodes = renderAll(
    repairConfig,
    paths,
    detail,
    runNo,
    created,
    durationSeconds
  )
  printStatus(detail)
  printTable(episodes, repairConfig.bots)
  echo "Report: " & paths.root / "index.html"

proc repairIncompleteRuns(config: ToolConfig) =
  ## Refreshes every non-completed run under the report root.
  if not dirExists(config.outDir):
    fail("--out-dir does not exist: " & config.outDir)
  var roots: seq[string]
  for kind, path in walkDir(config.outDir):
    if kind == pcDir and fileExists(path / "run.json"):
      try:
        let meta = parseFile(path / "run.json")
        if meta.strField("mode") == "vs":
          echo "Skipping versus run repair: " & path
        elif meta.strField("status") != "completed":
          roots.add path
      except CatchableError as e:
        echo "Skipping unreadable run " & path & ": " &
          e.msg.shortCommandError()
  roots.sort()
  if roots.len == 0:
    echo "No incomplete runs found in " & config.outDir
    writeMainIndex(config)
    return
  for root in roots:
    repairRun(config, root)
  writeMainIndex(config)

proc pollRequest(
  config: ToolConfig,
  paths: RunPaths,
  firstDetail: JsonNode,
  runNo: int,
  created: string,
  runStarted: MonoTime
): JsonNode =
  ## Polls a hosted XP request until completion or timeout.
  result = firstDetail
  let requestId = result.strField("id")
  var failures = 0
  let deadline = getMonoTime() + initDuration(
    seconds = config.waitSeconds
  )
  while true:
    discard renderAll(
      config,
      paths,
      result,
      runNo,
      created,
      elapsedSeconds(runStarted)
    )
    printStatus(result)
    if result.strField("status").isTerminalStatus():
      return
    if getMonoTime() >= deadline:
      echo "wait timeout reached; wrote latest report for " & requestId
      return
    sleep(config.pollMs)
    try:
      result = getRequest(config, requestId)
      failures = 0
    except CatchableError as e:
      inc failures
      if not e.msg.isTransientPollError():
        fail("Poll failed: " & e.msg.shortCommandError())
      if failures.shouldPrintPollFailure():
        echo "poll transient failure " & $failures &
          ", keeping last status: " & e.msg.shortCommandError()
      sleep(config.pollBackoffMs(failures))

proc replaceCurrentBots(bots: var seq[BotRef], current: BotRef) =
  ## Replaces current-checkout placeholders with one uploaded policy.
  for i in 0 ..< bots.len:
    if bots[i].isCurrentBot():
      bots[i] = current

proc versusSideText(side: VersusSide): string =
  ## Returns a short label for one versus half.
  case side
  of CandidateImposters:
    "candidate imposters"
  of OpponentImposters:
    "opponent imposters"

proc versusSideRoleText(side: VersusSide): string =
  ## Returns the candidate role label for one versus half.
  case side
  of CandidateImposters:
    "imposter"
  of OpponentImposters:
    "crew"

proc versusRequestFileName(opponentIndex: int, side: VersusSide): string =
  ## Returns the request JSON filename for one versus half.
  let prefix = "request-vs-" & $opponentIndex & "-"
  prefix & (
    case side
    of CandidateImposters:
      "candidate-imposters.json"
    of OpponentImposters:
      "opponent-imposters.json"
  )

proc versusRoster(
  candidate,
  opponent: BotRef,
  side: VersusSide
): seq[BotRef] =
  ## Builds the roster for one fixed-role versus half.
  let
    imposter =
      if side == CandidateImposters:
        candidate
      else:
        opponent
    crew =
      if side == CandidateImposters:
        opponent
      else:
        candidate
  for slot in 0 ..< PlayerLetters.len:
    if slot >= FirstImposterSlot:
      result.add imposter
    else:
      result.add crew

proc versusSlotRoles(): seq[string] =
  ## Returns fixed Crewrift roles for versus slots A-F and G-H.
  for slot in 0 ..< PlayerLetters.len:
    if slot >= FirstImposterSlot:
      result.add "imposter"
    else:
      result.add "crew"

proc versusFocusIndex(side: VersusSide): int =
  ## Returns the focused candidate group for one versus half roster.
  case side
  of CandidateImposters:
    1
  of OpponentImposters:
    0

proc versusSegmentConfig(
  base: ToolConfig,
  candidate,
  opponent: BotRef,
  side: VersusSide,
  games: int
): ToolConfig =
  ## Builds the hosted-run config for one versus half.
  result = base
  result.games = games
  result.name = base.name & " - " & side.versusSideText()
  result.nameSet = true
  result.bots = versusRoster(candidate, opponent, side)
  result.slotRoles = versusSlotRoles()
  result.uploadCurrent = false
  result.uploadCurrentIndex = -1
  result.requestId = ""
  result.focusIndex = side.versusFocusIndex()
  result.vsMode = false

proc versusSegments(config: ToolConfig, paths: RunPaths): seq[VersusSegment] =
  ## Builds every versus request segment.
  if config.bots.len < 2:
    return
  let
    candidate = config.bots[0]
    opponentCount = config.bots.len - 1
    halfGames = config.games div (opponentCount * 2)
  var offset = 0
  for opponentIndex in 1 ..< config.bots.len:
    let opponent = config.bots[opponentIndex]
    for side in [CandidateImposters, OpponentImposters]:
      let label = botTitle(opponent) & " " & side.versusSideText()
      result.add VersusSegment(
        side: side,
        opponentIndex: opponentIndex,
        opponent: opponent,
        label: label,
        config: versusSegmentConfig(
          config,
          candidate,
          opponent,
          side,
          halfGames
        ),
        requestPath: paths.root / opponentIndex.versusRequestFileName(side),
        gameOffset: offset
      )
      offset += halfGames

proc offsetEpisodes(
  episodes: openArray[Episode],
  offset: int
): seq[Episode] =
  ## Returns episode rows with global game indexes.
  for episode in episodes:
    var item = episode
    item.index = episode.index + offset
    result.add item

proc episodesForSegment(
  episodes: openArray[Episode],
  segment: VersusSegment
): seq[Episode] =
  ## Returns aggregate episode rows that belong to one versus segment.
  let
    first = segment.gameOffset + 1
    last = segment.gameOffset + segment.config.games
  for episode in episodes:
    if episode.index >= first and episode.index <= last:
      result.add episode

proc segmentOpponentLabel(segment: VersusSegment): string =
  ## Returns the display label for a segment opponent.
  if segment.opponent.label.len > 0 or segment.opponent.policyId.len > 0:
    return botTitle(segment.opponent)
  "opponent"

proc segmentCandidateScore(
  episode: Episode,
  segment: VersusSegment
): tuple[found: bool, score: float] =
  ## Returns the candidate score for one segment episode.
  let groups = groupedBots(segment.config.bots)
  episode.scoreForGroup(groups, segment.side.versusFocusIndex())

proc segmentOpponentScore(
  episode: Episode,
  segment: VersusSegment
): tuple[found: bool, score: float] =
  ## Returns the opponent score for one segment episode.
  let
    groups = groupedBots(segment.config.bots)
    focusIndex = segment.side.versusFocusIndex()
    opponentIndex =
      if focusIndex == 0:
        1
      else:
        0
  episode.scoreForGroup(groups, opponentIndex)

proc segmentScores(
  episode: Episode,
  segment: VersusSegment
): tuple[
  candidate: tuple[found: bool, score: float],
  opponent: tuple[found: bool, score: float]
] =
  ## Returns candidate and current-opponent scores for one segment row.
  (
    candidate: episode.segmentCandidateScore(segment),
    opponent: episode.segmentOpponentScore(segment)
  )

proc addVersusOutcome(
  summary: var Summary,
  episode: Episode,
  segment: VersusSegment
) =
  ## Adds one segment episode to aggregate versus scoring.
  let scores = episode.segmentScores(segment)
  if not scores.candidate.found or not scores.opponent.found:
    return
  let values = @[scores.candidate.score, scores.opponent.score]
  case scores.candidate.score.recordOutcome(values)
  of OutcomeWin:
    inc summary.wins
  of OutcomeLoss:
    inc summary.losses
  of OutcomeTie:
    inc summary.ties
  of OutcomeMissing:
    return
  summary.totalA += scores.candidate.score
  summary.totalB += scores.opponent.score

proc finishSummary(summary: var Summary) =
  ## Finalizes average scores after record counters have been filled.
  let scored = summary.scoredGames()
  summary.margin = summary.totalA - summary.totalB
  if scored > 0:
    summary.avgA = summary.totalA / scored.float
    summary.avgB = summary.totalB / scored.float
    summary.avgMargin = summary.margin / scored.float

proc versusSummaryFor(
  episodes: openArray[Episode],
  segments: openArray[VersusSegment]
): Summary =
  ## Summarizes aggregate versus rows from segment-local scores.
  for segment in segments:
    let segmentEpisodes = episodes.episodesForSegment(segment)
    for episode in segmentEpisodes:
      case episode.status
      of "completed":
        inc result.completed
      of "failed":
        inc result.failed
      of "running":
        inc result.running
      else:
        inc result.pending
      if episode.excluded:
        inc result.excluded
        continue
      result.addVersusOutcome(episode, segment)
  result.finishSummary()

proc versusFocusFor(
  config: ToolConfig,
  episodes: openArray[Episode],
  segments: openArray[VersusSegment]
): RunFocus =
  ## Returns the aggregate focus summary for multi-target versus runs.
  result = RunFocus(
    found: true,
    groupIndex: 0,
    label:
      if config.bots.len > 0:
        botTitle(config.bots[0])
      else:
        "candidate",
    abbr: "N",
    opponentLabel:
      if config.bots.len == 2:
        botTitle(config.bots[1])
      else:
        "best opponent"
  )
  let summary = episodes.versusSummaryFor(segments)
  result.scored = summary.scoredGames()
  result.wins = summary.wins
  result.losses = summary.losses
  result.ties = summary.ties
  result.avg = summary.avgA
  result.opponentAvg = summary.avgB
  result.avgMargin = summary.avgMargin

proc requestsTerminal(segments: openArray[VersusSegment]): bool =
  ## Returns true when every versus request has reached a terminal state.
  for segment in segments:
    if not segment.detail.strField("status").isTerminalStatus():
      return false
  true

proc combinedStatus(segments: openArray[VersusSegment]): string =
  ## Returns one aggregate status for both versus requests.
  var
    allCompleted = segments.len > 0
    anyActive = false
    anyFailed = false
  for segment in segments:
    let status = segment.detail.strField("status")
    if status != "completed":
      allCompleted = false
    if not status.isTerminalStatus():
      anyActive = true
    elif status == "failed":
      anyFailed = true
  if allCompleted:
    return "completed"
  if anyActive:
    return "running"
  if anyFailed:
    return "failed"
  "finished"

proc combinedVersusDetail(
  segments: openArray[VersusSegment],
  episodes: openArray[Episode]
): JsonNode =
  ## Builds a synthetic XP detail for aggregate metadata.
  var ids: seq[string]
  for segment in segments:
    let id = segment.detail.strField("id")
    if id.len > 0:
      ids.add id
  result = newJObject()
  result["id"] = %ids.join(",")
  result["status"] = %segments.combinedStatus()
  result["episode_count"] = %episodes.len
  var
    completed = 0
    failed = 0
    running = 0
    pending = 0
  for episode in episodes:
    case episode.status
    of "completed":
      inc completed
    of "failed":
      inc failed
    of "running":
      inc running
    else:
      inc pending
  result["completed_count"] = %completed
  result["failed_count"] = %failed
  result["running_count"] = %running
  result["pending_count"] = %pending

proc versusWinnerText(
  candidate,
  opponent: tuple[found: bool, score: float],
  candidateLabel,
  opponentLabel: string
): string

proc writeVersusMeta(
  config: ToolConfig,
  paths: RunPaths,
  segments: openArray[VersusSegment],
  episodes: openArray[Episode],
  runNo: int,
  created: string,
  durationSeconds = -1.0
) =
  ## Writes aggregate versus metadata for the run index.
  let
    detail = combinedVersusDetail(segments, episodes)
    summary = episodes.versusSummaryFor(segments)
    scored = summary.scoredGames()
  var meta = newJObject()
  meta["run_number"] = %runNo
  meta["name"] = %config.name
  meta["slug"] = %extractFilename(paths.root)
  meta["created_at"] = %created
  if durationSeconds >= 0.0:
    meta["duration_seconds"] = %durationSeconds
    meta["duration"] = %durationText(durationSeconds)
  meta["request_id"] = %detail.strField("id")
  meta["status"] = %detail.strField("status")
  if config.focusIndex >= 0:
    meta["focus_index"] = %config.focusIndex
  meta["games"] = %config.games
  meta["completed"] = %summary.completed
  meta["failed"] = %summary.failed
  meta["running"] = %summary.running
  meta["pending"] = %summary.pending
  meta["excluded"] = %summary.excluded
  meta["valid_games"] = %scored
  meta["wins"] = %summary.wins
  meta["losses"] = %summary.losses
  meta["ties"] = %summary.ties
  meta["win_rate"] = %winRate(summary.wins, scored)
  meta["avg_a"] = %summary.avgA
  meta["avg_b"] = %summary.avgB
  meta["avg_margin"] = %summary.avgMargin
  meta["bots"] = newJArray()
  for bot in config.bots:
    meta["bots"].add %*{
      "input": bot.input,
      "key": bot.key,
      "label": bot.label,
      "policy_id": bot.policyId
    }
  meta["episodes"] = newJArray()
  for segment in segments:
    let segmentEpisodes = episodes.episodesForSegment(segment)
    for episode in segmentEpisodes:
      let scores = episode.segmentScores(segment)
      var row = %*{
        "index": episode.index,
        "id": episode.id,
        "status": episode.status,
        "episode_id": episode.episodeId,
        "replay_url": episode.replayUrl,
        "live_url": episode.liveUrl,
        "error_type": episode.errorType,
        "error": episode.error,
        "excluded": episode.excluded,
        "exclusion_reason": episode.exclusionReason,
        "opponent_index": segment.opponentIndex,
        "opponent_label": segment.segmentOpponentLabel(),
        "side": segment.side.versusSideRoleText()
      }
      if not episode.excluded and scores.candidate.found and
          scores.opponent.found:
        row["scores"] = %[
          scores.candidate.score,
          scores.opponent.score
        ]
        row["winner"] = %versusWinnerText(
          scores.candidate,
          scores.opponent,
          botTitle(config.bots[0]),
          segment.segmentOpponentLabel()
        )
      else:
        row["winner"] = %"-"
      meta["episodes"].add row
  meta["mode"] = %"vs"
  meta["request_ids"] = newJArray()
  meta["requests"] = newJArray()
  for segment in segments:
    let id = segment.detail.strField("id")
    meta["request_ids"].add %id
    meta["requests"].add %*{
      "id": id,
      "label": segment.label,
      "status": segment.detail.strField("status"),
      "games": segment.config.games,
      "opponent_index": segment.opponentIndex,
      "opponent_label": segment.segmentOpponentLabel(),
      "side": segment.side.versusSideRoleText(),
      "game_offset": segment.gameOffset
    }
  writeFile(paths.meta, meta.pretty() & "\n")

proc versusWinnerText(
  candidate,
  opponent: tuple[found: bool, score: float],
  candidateLabel,
  opponentLabel: string
): string =
  ## Returns the winner label for one direct versus score row.
  if not candidate.found or not opponent.found:
    return "-"
  let scores = @[candidate.score, opponent.score]
  case candidate.score.recordOutcome(scores)
  of OutcomeWin:
    return candidateLabel
  of OutcomeLoss:
    return opponentLabel
  of OutcomeTie:
    return "tie"
  of OutcomeMissing:
    return "-"

proc versusScoreCell(
  score: tuple[found: bool, score: float],
  won: bool
): string =
  ## Renders one versus score cell.
  var text = score.scoreText().htmlEscape()
  if won:
    text = "<mark>" & text & "</mark>"
  text.tableHtmlClass("score-cell")

proc renderVersusIndex(
  config: ToolConfig,
  paths: RunPaths,
  segments: openArray[VersusSegment],
  episodes: openArray[Episode],
  runNo: int,
  durationSeconds = -1.0
) =
  ## Writes the aggregate versus report page.
  let
    focus = config.versusFocusFor(episodes, segments)
    summary = episodes.versusSummaryFor(segments)
    candidateLabel = botTitle(config.bots[0])
    opponentLabel = focus.opponentLabel
    status = combinedStatus(segments)
  var html = pageStart(config.name, config.assetHref(1))
  html.add "<section>\n"
  html.add "<p><a href=\"../index.html\">all runs</a></p>\n"
  html.add "<h1>" & config.name.htmlEscape() & "</h1>\n"
  html.add "<p>Versus run <b>" & $runNo & "</b>.</p>\n"
  html.add "<ul>\n"
  html.add "<li>Status: " & status.htmlEscape() & "</li>\n"
  html.add "<li>Games: " & $episodes.len & " total, "
  html.add $focus.scored & " valid, " & $summary.excluded
  html.add " excluded</li>\n"
  html.add "<li>" & candidateLabel.htmlEscape() & " record: "
  html.add $focus.wins & "-" & $focus.losses & "-" & $focus.ties
  html.add "</li>\n"
  html.add "<li>" & candidateLabel.htmlEscape() & " average score: "
  html.add numberText(focus.avg, 2) & "</li>\n"
  html.add "<li>" & opponentLabel.htmlEscape() & " average score: "
  html.add numberText(focus.opponentAvg, 2) & "</li>\n"
  html.add "<li>Average margin: "
  html.add numberText(focus.avgMargin, 2) & "</li>\n"
  if durationSeconds >= 0.0:
    html.add "<li>Run time: " & durationText(durationSeconds) & "</li>\n"
  html.add "</ul>\n"
  html.add "<table class=\"wide no-sort name-key\">\n"
  html.add "<thead><tr><th>Target</th><th>Request</th><th>Games</th>"
  html.add "<th>Status</th><th>Roster</th></tr></thead>\n"
  html.add "<tbody>\n"
  for segment in segments:
    let
      id = segment.detail.strField("id")
      targetLabel = segment.segmentOpponentLabel()
      roster =
        if segment.side == CandidateImposters:
          targetLabel & " A-F, " & candidateLabel & " G-H"
        else:
          candidateLabel & " A-F, " & targetLabel & " G-H"
    html.add "<tr>"
    html.add targetLabel.tableCell()
    html.add linkHtml(
      softmaxDetailUrl("experience-request", id),
      segment.side.versusSideRoleText()
    ).tableHtmlClass("name-cell")
    html.add ($segment.config.games).tableCell(numeric = true)
    html.add segment.detail.strField("status").tableCell()
    html.add roster.tableCell()
    html.add "</tr>\n"
  html.add "</tbody></table>\n"
  html.add "<table class=\"wide no-sort name-key\">\n"
  html.add "<thead><tr><th>Side</th><th>Games</th><th>Valid</th>"
  html.add "<th>Excluded</th>"
  html.add "<th>" & candidateLabel.htmlEscape() & "</th>"
  html.add "<th>" & opponentLabel.htmlEscape() & "</th>"
  html.add "<th>Margin</th></tr></thead>\n"
  html.add "<tbody>\n"
  for segment in segments:
    let
      segmentEpisodes = episodes.episodesForSegment(segment)
      segmentSummary = segmentEpisodes.versusSummaryFor([segment])
    html.add "<tr>"
    html.add (
      segment.segmentOpponentLabel() & " " &
        segment.side.versusSideRoleText()
    ).tableCell()
    html.add ($segmentEpisodes.len).tableCell(numeric = true)
    html.add ($segmentSummary.scoredGames()).tableCell(numeric = true)
    html.add ($segmentSummary.excluded).tableCell(numeric = true)
    html.add numberText(segmentSummary.avgA, 2).tableCell(numeric = true)
    html.add numberText(segmentSummary.avgB, 2).tableCell(numeric = true)
    html.add numberText(segmentSummary.avgMargin, 2).tableCell(numeric = true)
    html.add "</tr>\n"
  html.add "</tbody></table>\n"
  html.add "<table class=\"wide games-table\">\n"
  html.add "<colgroup><col class=\"game-col\"><col class=\"name-col\">"
  html.add "<col class=\"side-col\">"
  html.add "<col class=\"score-col\"><col class=\"score-col\">"
  html.add "<col class=\"name-col\">"
  html.add "<col class=\"reason-col\"></colgroup>\n"
  html.add "<thead><tr><th>Game</th><th>Target</th><th>Side</th>"
  html.add "<th>" & candidateLabel.htmlEscape() & "</th>"
  html.add "<th>Opponent</th>"
  html.add "<th>Winner</th><th>Note</th></tr></thead>\n<tbody>\n"
  for segment in segments:
    let segmentEpisodes = episodes.episodesForSegment(segment)
    for episode in segmentEpisodes:
      let
        gameIndex = episode.index
        rowScores = episode.segmentScores(segment)
        candidateScore = rowScores.candidate
        opponentScore = rowScores.opponent
        targetLabel = segment.segmentOpponentLabel()
        scoreValues = @[candidateScore.score, opponentScore.score]
        candidateOutcome =
          if not episode.excluded and candidateScore.found and
            opponentScore.found:
              candidateScore.score.recordOutcome(scoreValues)
          else:
            OutcomeMissing
        opponentOutcome =
          if not episode.excluded and candidateScore.found and
            opponentScore.found:
              opponentScore.score.recordOutcome(scoreValues)
          else:
            OutcomeMissing
        candidateWon = candidateOutcome == OutcomeWin
        opponentWon = opponentOutcome == OutcomeWin
        gameText =
          if episode.id.len > 0:
            episode.id.shortId() & " #" & $gameIndex
          else:
            $gameIndex
        gameTitle =
          if episode.id.len > 0:
            episode.id & " game " & $gameIndex
          else:
            "game " & $gameIndex
        winner =
          if episode.excluded:
            "-"
          else:
            versusWinnerText(
              candidateScore,
              opponentScore,
              candidateLabel,
              targetLabel
            )
      html.add "<tr" & episode.gameRowClass() & ">"
      html.add clipHtmlCell(
        linkHtml(gameFileName(gameIndex), gameText),
        gameTitle,
        "game-cell"
      )
      html.add targetLabel.tableCell()
      html.add segment.side.versusSideRoleText().tableCell()
      html.add candidateScore.versusScoreCell(candidateWon)
      html.add opponentScore.versusScoreCell(opponentWon)
      html.add winner.tableCell()
      html.add episode.exclusionCell()
      html.add "</tr>\n"
  html.add "</tbody></table>\n"
  html.add "</section>\n"
  html.add pageEnd()
  writeFile(paths.root / "index.html", html)

proc renderVersusAll(
  config: ToolConfig,
  paths: RunPaths,
  segments: var seq[VersusSegment],
  runNo: int,
  created: string,
  durationSeconds = -1.0
): seq[Episode] =
  ## Writes all versus pages and aggregate metadata.
  for segment in segments.mitems:
    segment.config.syncBotsFromDetail(segment.detail)
    var segmentEpisodes = parseEpisodes(segment.detail)
    renderGamePages(
      segment.config,
      paths,
      segmentEpisodes,
      segment.gameOffset
    )
    segmentEpisodes.fillReplayScores(
      segment.config,
      paths,
      segment.gameOffset
    )
    result.add segmentEpisodes.offsetEpisodes(segment.gameOffset)
  writeVersusMeta(
    config,
    paths,
    segments,
    result,
    runNo,
    created,
    durationSeconds
  )
  writeMainIndex(config)
  renderVersusIndex(
    config,
    paths,
    segments,
    result,
    runNo,
    durationSeconds
  )

proc printVersusStatus(segments: openArray[VersusSegment]) =
  ## Prints compact status lines for both versus requests.
  for segment in segments:
    echo segment.label & " " & segment.detail.statusText()

proc pollVersusRequests(
  config: ToolConfig,
  paths: RunPaths,
  segments: var seq[VersusSegment],
  runNo: int,
  created: string,
  runStarted: MonoTime
): seq[Episode] =
  ## Polls both versus XP requests until completion or timeout.
  var failures = 0
  let deadline = getMonoTime() + initDuration(
    seconds = config.waitSeconds
  )
  while true:
    result = renderVersusAll(
      config,
      paths,
      segments,
      runNo,
      created,
      elapsedSeconds(runStarted)
    )
    printVersusStatus(segments)
    if segments.requestsTerminal():
      return
    if getMonoTime() >= deadline:
      echo "wait timeout reached; wrote latest versus report"
      return
    sleep(config.pollMs)
    for segment in segments.mitems:
      if segment.detail.strField("status").isTerminalStatus():
        continue
      let requestId = segment.detail.strField("id")
      try:
        segment.detail = getRequest(config, requestId)
        failures = 0
      except CatchableError as e:
        inc failures
        if not e.msg.isTransientPollError():
          fail("Poll failed: " & e.msg.shortCommandError())
        if failures.shouldPrintPollFailure():
          echo "poll transient failure " & $failures &
            ", keeping last status: " & e.msg.shortCommandError()
        sleep(config.pollBackoffMs(failures))

proc printVersusTable(
  episodes: openArray[Episode],
  config: ToolConfig,
  segments: openArray[VersusSegment]
) =
  ## Prints the final command-line versus summary.
  if config.bots.len < 2:
    return
  let
    focus = config.versusFocusFor(episodes, segments)
    summary = episodes.versusSummaryFor(segments)
  echo "versus " & botTitle(config.bots[0]) &
    " wins=" & $focus.wins &
    " losses=" & $focus.losses &
    " ties=" & $focus.ties &
    " avg=" & numberText(focus.avg, 2) &
    " vs " & focus.opponentLabel & "=" &
    numberText(focus.opponentAvg, 2) &
    " avg_margin=" & numberText(focus.avgMargin, 2) &
    " excluded=" & $summary.excluded
  for opponentIndex in 1 ..< config.bots.len:
    var targetSegments: seq[VersusSegment]
    for segment in segments:
      if segment.opponentIndex == opponentIndex:
        targetSegments.add segment
    if targetSegments.len == 0:
      continue
    let targetSummary = episodes.versusSummaryFor(targetSegments)
    echo "  vs " & botTitle(config.bots[opponentIndex]) &
      " wins=" & $targetSummary.wins &
      " losses=" & $targetSummary.losses &
      " ties=" & $targetSummary.ties &
      " avg=" & numberText(targetSummary.avgA, 2) &
      " opp=" & numberText(targetSummary.avgB, 2) &
      " excluded=" & $targetSummary.excluded

proc runVersus(initialConfig: ToolConfig) =
  ## Runs direct two-policy versus mode.
  var config = initialConfig
  let
    runStarted = getMonoTime()
    created = createdText()
  if config.uploadCurrent:
    let current = uploadCurrentPolicy(config)
    config.bots.replaceCurrentBots(current)
    if not config.nameSet:
      config.name = config.bots.versusTitle()
  ensureUserToken(config)
  if not config.nameSet:
    config.name = config.bots.versusTitle()

  let
    paths = pathsFor(config)
    runNo = runNumber(config.outDir)
  var segments = config.versusSegments(paths)
  for segment in segments.mitems:
    segment.detail = createRequest(segment.config, segment.requestPath)
    segment.config.syncBotsFromDetail(segment.detail)
    config.syncBotsFromDetail(segment.detail)
    let requestId = segment.detail.strField("id")
    if requestId.len > 0:
      echo segment.label & " request: " & requestId

  let episodes = pollVersusRequests(
    config,
    paths,
    segments,
    runNo,
    created,
    runStarted
  )
  let durationSeconds = elapsedSeconds(runStarted)
  discard renderVersusAll(
    config,
    paths,
    segments,
    runNo,
    created,
    durationSeconds
  )
  printVersusTable(episodes, config, segments)
  echo "duration=" & durationText(durationSeconds)
  echo ""
  echo "Report: " & paths.root / "index.html"
  echo "All runs: " & config.outDir / "index.html"

proc runFourPlayer(initialConfig: ToolConfig) =
  ## Runs one eight-player hosted report.
  var config = initialConfig
  let
    runStarted = getMonoTime()
    created = createdText()
  if config.uploadCurrent:
    let current = uploadCurrentPolicy(config)
    config.bots.replaceCurrentBots(current)
    if not config.nameSet:
      config.name = rosterTitle(config.bots)
  ensureUserToken(config)
  if not config.nameSet:
    config.name = rosterTitle(config.bots)

  let
    paths = pathsFor(config)
    runNo = runNumber(config.outDir)
  var detail: JsonNode
  if config.requestId.len > 0:
    detail = getRequestWithRetry(config, config.requestId, 6)
  else:
    detail = createRequest(config, paths)
  let requestId = detail.strField("id")
  if requestId.len > 0:
    echo "Request: " & requestId
  config.syncBotsFromDetail(detail)

  detail = pollRequest(config, paths, detail, runNo, created, runStarted)
  config.syncBotsFromDetail(detail)

  let durationSeconds = elapsedSeconds(runStarted)
  let episodes = renderAll(
    config,
    paths,
    detail,
    runNo,
    created,
    durationSeconds
  )
  printTable(episodes, config.bots)
  echo "duration=" & durationText(durationSeconds)
  echo ""
  echo "Report: " & paths.root / "index.html"
  echo "All runs: " & config.outDir / "index.html"

proc runTool(initialConfig: ToolConfig) =
  ## Runs hosted XP requests and static report generation.
  let config = initialConfig

  if config.indexOnly:
    writeMainIndex(config)
    echo "All runs: " & config.outDir / "index.html"
    return

  if config.repairIncomplete or config.repairRuns.len > 0:
    ensureUserToken(config)
    if config.repairIncomplete:
      repairIncompleteRuns(config)
    for runPath in config.repairRuns:
      repairRun(config, runPath)
    writeMainIndex(config)
    echo "All runs: " & config.outDir / "index.html"
    return

  if config.vsMode:
    runVersus(config)
    return

  runFourPlayer(config)

when isMainModule:
  try:
    runTool(readConfig())
  except CatchableError as e:
    stderr.writeLine "run: ", e.msg
    quit 1
