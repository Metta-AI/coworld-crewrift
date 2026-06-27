import
  std/[os, strutils],
  ../../../tools/expand_replay,
  ../../../src/crewrift/replays

type
  ReplayExtractError = object of CatchableError

  OutputFormat = enum
    TextFormat
    HtmlFormat

  ReplayCliConfig = object
    replayPath: string
    outputPath: string
    outputFormat: OutputFormat
    cssHref: string
    labels: seq[string]

  ReplaySummary = object
    kills: int
    tasks: int
    meetings: int
    chats: int
    votes: int
    deaths: int

  PlayerInfo = object
    label: string
    role: string
    won: bool
    hasResult: bool

const
  Usage = """
Usage:
  nim r players/notsus/tools/replay_extractor.nim -- REPLAY [options]

Options:
  -o, --output PATH       Write the extracted log to a file.
      --format text|html  Output format. Default: text.
      --css HREF          CSS href for HTML output. Default: tufte.css.
      --label SLOT=NAME   Optional seat label for HTML and text logs.
  -h, --help              Show this help text.
"""
  DefaultCssHref = "tufte.css"
  PlayerLetters = ["A", "B", "C", "D", "E", "F", "G", "H"]

proc fail(message: string) {.raises: [ReplayExtractError].} =
  ## Raises one replay extraction error.
  raise newException(ReplayExtractError, message)

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

proc parseOutputFormat(value: string): OutputFormat =
  ## Parses one output format string.
  case value.toLowerAscii()
  of "text":
    result = TextFormat
  of "html":
    result = HtmlFormat
  else:
    fail("Unknown output format: " & value)

proc addLabel(labels: var seq[string], value: string) =
  ## Adds or replaces one seat label from SLOT=NAME.
  let split = value.find("=")
  if split <= 0:
    fail("Label must look like SLOT=NAME: " & value)
  let slotText = value[0 ..< split]
  var slot = -1
  try:
    slot = slotText.parseInt()
  except ValueError:
    fail("Label slot must be an integer: " & slotText)
  if slot < 0:
    fail("Label slot must be non-negative: " & slotText)
  while labels.len <= slot:
    labels.add ""
  labels[slot] = value[split + 1 .. ^1]

proc readConfig(): ReplayCliConfig {.used.} =
  ## Reads replay extraction command-line options.
  result.cssHref = DefaultCssHref
  result.outputFormat = TextFormat
  let params = commandLineParams()
  var i = 0
  while i < params.len:
    let param = params[i]
    if param == "--":
      discard
    elif param == "-h" or param == "--help":
      echo Usage.strip()
      quit 0
    elif param == "-o":
      result.outputPath = optionValue(params, i, param, "")
    elif param == "--output":
      result.outputPath = optionValue(params, i, param, "")
    elif param.startsWith("--output="):
      result.outputPath = param["--output=".len .. ^1]
    elif param == "--format":
      result.outputFormat = parseOutputFormat(
        optionValue(params, i, param, "")
      )
    elif param.startsWith("--format="):
      result.outputFormat = parseOutputFormat(param["--format=".len .. ^1])
    elif param == "--css":
      result.cssHref = optionValue(params, i, param, "")
    elif param.startsWith("--css="):
      result.cssHref = param["--css=".len .. ^1]
    elif param == "--label":
      result.labels.addLabel(optionValue(params, i, param, ""))
    elif param.startsWith("--label="):
      result.labels.addLabel(param["--label=".len .. ^1])
    elif param.startsWith("-"):
      fail("Unknown option: " & param)
    elif result.replayPath.len == 0:
      result.replayPath = param.absolutePath()
    else:
      fail("Only one replay path can be extracted at a time.")
    inc i
  if result.replayPath.len == 0:
    fail("A replay path is required.")
  if result.cssHref.len == 0:
    result.cssHref = DefaultCssHref

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

proc oneLine(text: string): string =
  ## Collapses text to one readable line.
  result = text.strip().replace("\r", " ").replace("\n", " ")
  while result.contains("  "):
    result = result.replace("  ", " ")

proc seatPrefix(slot: int): string =
  ## Returns the stable display prefix for one replay slot.
  if slot >= 0 and slot < PlayerLetters.len:
    return PlayerLetters[slot]
  if slot >= 0:
    return "P" & $slot
  "Table"

proc eventTimeText(tick: int): string =
  ## Returns the replay time for one simulation tick.
  let
    millis = tickTime(tick)
    totalSeconds = int(millis div 1000'u32)
    minutes = totalSeconds div 60
    seconds = totalSeconds mod 60
  result = $minutes & ":"
  if seconds < 10:
    result.add "0"
  result.add $seconds

proc colorNameFromLabel(label: string): string =
  ## Returns the player color prefix from one replay label.
  let split = label.find("(")
  if split > 0:
    return label[0 ..< split].strip().toLowerAscii()
  label.strip().toLowerAscii()

proc colorHex(colorName: string): string =
  ## Returns the canonical replay color hex value.
  case colorName
  of "red":
    "#c51111"
  of "blue":
    "#132ed1"
  of "green":
    "#117f2d"
  of "pink":
    "#ed54ba"
  of "orange":
    "#ef7d0d"
  of "yellow":
    "#f5f557"
  of "purple":
    "#6b2fbb"
  of "cyan":
    "#38fedc"
  of "lime":
    "#50ef39"
  of "brown":
    "#71491e"
  of "beige":
    "#f0d7b7"
  of "navy":
    "#1b2148"
  of "teal":
    "#38a9a5"
  of "rose":
    "#f4a6c8"
  of "maroon":
    "#6b2b3a"
  of "gray":
    "#282a30"
  else:
    "#ffffff"

proc playerChipHtml(label: string): string =
  ## Renders one replay player label with its color swatch.
  let clean = label.oneLine()
  if clean.len == 0:
    return ""
  result.add "<span class=\"crew-chip\">"
  result.add "<span class=\"crew-swatch\" style=\"background: "
  result.add colorHex(clean.colorNameFromLabel()).htmlEscape()
  result.add "\"></span><span>"
  result.add clean.htmlEscape()
  result.add "</span></span>"

proc playerText(
  labels: openArray[string],
  slot: int,
  fallback: string
): string =
  ## Returns a readable player label for one replay slot.
  if slot >= 0 and slot < labels.len and labels[slot].len > 0:
    let prefix = slot.seatPrefix() & " " & labels[slot]
    if fallback.len > 0 and fallback != labels[slot]:
      return prefix & " [" & fallback & "]"
    return prefix
  if fallback.len > 0:
    return fallback
  if slot >= 0:
    return slot.seatPrefix()
  "Table"

proc actorText(event: ReplayEvent, labels: openArray[string]): string =
  ## Returns the actor display text for one event.
  labels.playerText(event.actorSlot, event.actorLabel)

proc targetText(event: ReplayEvent, labels: openArray[string]): string =
  ## Returns the target display text for one event.
  labels.playerText(event.secondarySlot, event.secondaryLabel)

proc eventKindText(event: ReplayEvent): string =
  ## Returns the compact display kind for one event.
  case event.kind
  of Kill:
    "kill"
  of BodyFound:
    "body"
  of Died:
    "death"
  of CompletedTask:
    "task"
  of VoteCalledBody, VoteCalledButton:
    "meeting"
  of VoteCast:
    "vote"
  of Chat:
    "chat"
  of PhaseChanged:
    "phase"
  of Score:
    "score"
  else:
    ($event.kind).toLowerAscii()

proc coreEvent(event: ReplayEvent): bool =
  ## Returns true when an event belongs in the compact replay log.
  case event.kind
  of Kill, BodyFound, Died, CompletedTask, VoteCalledBody,
      VoteCalledButton, VoteCast, Chat, PhaseChanged:
    true
  else:
    false

proc eventLine(event: ReplayEvent, labels: openArray[string]): string =
  ## Renders one compact replay event line.
  case event.kind
  of Kill:
    result = event.actorText(labels) & " killed " & event.targetText(labels)
  of BodyFound:
    result = event.actorText(labels) & " body was found"
    if event.room.len > 0:
      result.add " in " & event.room
  of Died:
    result = event.actorText(labels) & " died or was voted out"
  of CompletedTask:
    result = event.actorText(labels) & " completed task " & $event.task
    if event.whileDead:
      result.add " while dead"
  of VoteCalledBody:
    result = event.actorText(labels) & " reported " &
      event.targetText(labels) & " body"
    if event.room.len > 0:
      result.add " in " & event.room
  of VoteCalledButton:
    result = event.actorText(labels) & " called the emergency button"
  of VoteCast:
    result = event.actorText(labels) & " voted "
    if event.voteSkip:
      result.add "skip"
    else:
      result.add event.targetText(labels)
  of Chat:
    result = event.actorText(labels) & ": \"" & event.chatText.oneLine() & "\""
  of PhaseChanged:
    result = "Phase changed to " & $event.phase
  of Score:
    result = event.actorText(labels) & " scored "
    if event.scoreAmount > 0:
      result.add "+"
    result.add $event.scoreAmount
    if event.scoreReason.len > 0:
      result.add " for " & event.scoreReason
  else:
    result = event.text().strip()

proc replaySummary(timeline: ReplayTimeline): ReplaySummary =
  ## Counts key social and task events in one replay timeline.
  for event in timeline.events:
    case event.kind
    of Kill:
      inc result.kills
    of CompletedTask:
      inc result.tasks
    of VoteCalledBody, VoteCalledButton:
      inc result.meetings
    of Chat:
      inc result.chats
    of VoteCast:
      inc result.votes
    of Died:
      inc result.deaths
    else:
      discard

proc setCollectedLabel(
  players: var seq[string],
  labels: openArray[string],
  slot: int,
  value: string
) =
  ## Sets one collected replay player label when the slot is still empty.
  if slot < 0 or value.len == 0:
    return
  while players.len <= slot:
    players.add ""
  if players[slot].len == 0:
    if slot < labels.len and labels[slot].len > 0:
      players[slot] = labels[slot]
      if value.len > 0 and value != labels[slot]:
        players[slot].add " [" & value & "]"
    else:
      players[slot] = value

proc collectPlayerLabels(
  timeline: ReplayTimeline,
  labels: openArray[string]
): seq[string] =
  ## Returns slot-indexed player labels seen in the replay.
  for i, label in labels:
    result.setCollectedLabel(labels, i, label)
  for event in timeline.events:
    result.setCollectedLabel(labels, event.actorSlot, event.actorLabel)
    result.setCollectedLabel(labels, event.secondarySlot, event.secondaryLabel)

proc usableRole(event: ReplayEvent, role: string): string =
  ## Returns a role only after the lobby has finished assigning roles.
  if role.len == 0 or $event.phase == "Lobby":
    return ""
  role

proc setPlayerInfo(
  players: var seq[PlayerInfo],
  slot: int,
  label,
  role: string
) =
  ## Records one player's in-game label and latest known role.
  if slot < 0:
    return
  while players.len <= slot:
    players.add PlayerInfo()
  if label.len > 0:
    players[slot].label = label
  if role.len > 0:
    players[slot].role = role

proc setPlayerWinner(players: var seq[PlayerInfo], slot: int) =
  ## Marks one player as a winner from the score events.
  if slot < 0:
    return
  while players.len <= slot:
    players.add PlayerInfo()
  players[slot].won = true

proc collectPlayers(timeline: ReplayTimeline): seq[PlayerInfo] =
  ## Returns slot-indexed replay players with in-game labels and roles.
  var hasResult = false
  for event in timeline.events:
    result.setPlayerInfo(
      event.actorSlot,
      event.actorLabel,
      event.usableRole(event.actorRole)
    )
    result.setPlayerInfo(
      event.secondarySlot,
      event.secondaryLabel,
      event.usableRole(event.secondaryRole)
    )
    if event.kind == Score and event.scoreReason == "winning":
      result.setPlayerWinner(event.actorSlot)
      hasResult = true
  if hasResult:
    for player in result.mitems:
      if player.label.len > 0:
        player.hasResult = true

proc roleDisplay(role: string): string =
  ## Returns the role shown in player tables.
  if role.len > 0:
    return role
  "-"

proc resultDisplay(player: PlayerInfo): string =
  ## Returns the win or loss result shown in player tables.
  if not player.hasResult:
    return "-"
  if player.won:
    return "win"
  "loss"

proc resultClass(player: PlayerInfo): string =
  ## Returns the CSS class for one player result cell.
  result = "result-col"
  if player.hasResult and player.won:
    result.add " result-win"

proc hasLogHrefs(logHrefs: openArray[string]): bool =
  ## Returns true when any player log link is available.
  for href in logHrefs:
    if href.len > 0:
      return true

proc eventWhoHtml(event: ReplayEvent): string =
  ## Renders the actor cell for one replay event.
  case event.kind
  of PhaseChanged:
    "Table"
  else:
    event.actorLabel.playerChipHtml()

proc eventWhatHtml(event: ReplayEvent): string =
  ## Renders the action cell for one replay event.
  case event.kind
  of Kill:
    result.add "killed "
    result.add event.secondaryLabel.playerChipHtml()
  of BodyFound:
    result.add "body found"
    if event.room.len > 0:
      result.add " in " & event.room.htmlEscape()
  of Died:
    result.add "died or was voted out"
  of CompletedTask:
    result.add "task " & $event.task
    if event.room.len > 0:
      result.add " in " & event.room.htmlEscape()
    if event.whileDead:
      result.add " while dead"
  of VoteCalledBody:
    result.add "reported body "
    result.add event.secondaryLabel.playerChipHtml()
    if event.room.len > 0:
      result.add " in " & event.room.htmlEscape()
  of VoteCalledButton:
    result.add "called the emergency button"
  of VoteCast:
    result.add "voted "
    if event.voteSkip:
      result.add "skip"
    else:
      result.add event.secondaryLabel.playerChipHtml()
  of Chat:
    result.add event.chatText.oneLine().htmlEscape()
  of PhaseChanged:
    result.add "phase changed to " & ($event.phase).htmlEscape()
  of Score:
    result.add "scored "
    if event.scoreAmount > 0:
      result.add "+"
    result.add $event.scoreAmount
    if event.scoreReason.len > 0:
      result.add " for " & event.scoreReason.htmlEscape()
  else:
    result.add event.text().strip().htmlEscape()

proc eventRowClass(event: ReplayEvent): string =
  ## Returns the row class for visually important replay events.
  case event.kind
  of Kill, BodyFound, VoteCalledBody:
    " class=\"mark-row\""
  else:
    ""

proc summaryText(summary: ReplaySummary): string =
  ## Renders compact summary counts for text output.
  @[
    $summary.kills & " kills",
    $summary.tasks & " tasks",
    $summary.meetings & " meetings",
    $summary.chats & " chats",
    $summary.votes & " votes",
    $summary.deaths & " deaths or vote-outs"
  ].join(", ")

proc replayLogTextForTimeline*(
  timeline: ReplayTimeline,
  path: string,
  labels: openArray[string]
): string =
  ## Renders a replay timeline as a compact plain-text log.
  let summary = timeline.replaySummary()
  result.add "Replay: " & path & "\n"
  result.add "Ticks: " & $timeline.tickCount & "\n"
  result.add "Summary: " & summary.summaryText() & "\n"
  if timeline.hashFailed:
    result.add "Warning: replay hash failed at tick " &
      $timeline.failTick & "; log may be partial.\n"
  let players = timeline.collectPlayerLabels(labels)
  if players.len > 0:
    result.add "\nPlayers\n"
    for slot, label in players:
      if label.len > 0:
        result.add "  " & slot.seatPrefix() & ": " & label & "\n"
  result.add "\nEvents\n"
  var wrote = false
  for event in timeline.events:
    if not event.coreEvent():
      continue
    wrote = true
    result.add "  tick " & $event.tick & " [" &
      event.eventKindText() & "] " & event.eventLine(labels) & "\n"
  if not wrote:
    result.add "  No kills, completed tasks, chats, or votes recorded.\n"

proc replayLogTextForPath*(
  path: string,
  labels: openArray[string]
): string =
  ## Loads one replay path and renders a compact plain-text log.
  let timeline = expandReplayTimeline(loadReplay(path))
  replayLogTextForTimeline(timeline, path, labels)

proc renderSummaryHtml(summary: ReplaySummary): string =
  ## Renders compact summary counts as HTML.
  result.add "<ul>\n"
  result.add "<li>Kills: " & $summary.kills & "</li>\n"
  result.add "<li>Completed tasks: " & $summary.tasks & "</li>\n"
  result.add "<li>Meetings called: " & $summary.meetings & "</li>\n"
  result.add "<li>Chat messages: " & $summary.chats & "</li>\n"
  result.add "<li>Votes cast: " & $summary.votes & "</li>\n"
  result.add "<li>Deaths or vote-outs: " & $summary.deaths & "</li>\n"
  result.add "</ul>\n"

proc renderPlayersHtml(
  players: openArray[PlayerInfo],
  logHrefs: openArray[string]
): string =
  ## Renders replay players as HTML.
  if players.len == 0:
    return ""
  let showLogs = logHrefs.hasLogHrefs()
  result.add "<section>\n"
  result.add "<h2>Players</h2>\n"
  result.add "<table class=\"report-table no-sort replay-players\">\n"
  result.add "<thead><tr><th>Seat</th><th>Player</th><th>Role</th>"
  result.add "<th>Win/Loss</th>"
  if showLogs:
    result.add "<th>Log</th>"
  result.add "</tr>"
  result.add "</thead>\n"
  result.add "<tbody>\n"
  for slot, player in players:
    if player.label.len == 0:
      continue
    result.add "<tr><td>" & slot.seatPrefix().htmlEscape() & "</td><td>"
    result.add player.label.playerChipHtml()
    result.add "</td><td class=\"role-col\">"
    result.add player.role.roleDisplay().htmlEscape()
    result.add "</td><td class=\"" & player.resultClass().htmlEscape() & "\">"
    result.add player.resultDisplay().htmlEscape()
    result.add "</td>"
    if showLogs:
      result.add "<td class=\"log-col\">"
      if slot >= 0 and slot < logHrefs.len and logHrefs[slot].len > 0:
        result.add "<a href=\"" & logHrefs[slot].htmlEscape() & "\">log</a>"
      else:
        result.add "-"
      result.add "</td>"
    result.add "</tr>\n"
  result.add "</tbody></table>\n"
  result.add "</section>\n"

proc renderEventsHtml(timeline: ReplayTimeline): string =
  ## Renders the compact replay events as an HTML table.
  result.add "<section>\n"
  result.add "<h2>Events</h2>\n"
  result.add "<table class=\"report-table replay-log\">\n"
  result.add "<thead><tr><th>Time</th><th>Kind</th><th>Who</th>"
  result.add "<th>What</th></tr></thead>\n"
  result.add "<tbody>\n"
  var wrote = false
  for event in timeline.events:
    if not event.coreEvent():
      continue
    wrote = true
    result.add "<tr" & event.eventRowClass() & "><td class=\"num\">"
    result.add event.tick.eventTimeText()
    result.add "</td><td>"
    result.add event.eventKindText().htmlEscape()
    result.add "</td><td class=\"who-col\">"
    result.add event.eventWhoHtml()
    result.add "</td><td class=\"what-col wrap\">"
    result.add event.eventWhatHtml()
    result.add "</td></tr>\n"
  if not wrote:
    result.add "<tr><td></td><td></td><td></td><td>"
    result.add "No kills, completed tasks, chats, or votes recorded."
    result.add "</td></tr>\n"
  result.add "</tbody></table>\n"
  result.add "</section>\n"

proc renderReplayHtmlForTimeline*(
  timeline: ReplayTimeline,
  source,
  replayHref: string,
  labels,
  logHrefs: openArray[string]
): string =
  ## Renders a replay timeline as Tufte-style HTML body sections.
  discard labels
  let summary = timeline.replaySummary()
  result.add "<section>\n"
  result.add "<h2>Replay Log</h2>\n"
  result.add "<ul>\n"
  if replayHref.len > 0:
    result.add "<li>Source: <code>" & replayHref.htmlEscape()
    result.add "</code></li>\n"
  elif source.len > 0:
    result.add "<li>Source: <code>" & source.htmlEscape()
    result.add "</code></li>\n"
  result.add "<li>Ticks parsed: " & $timeline.tickCount & "</li>\n"
  if timeline.hashFailed:
    result.add "<li>Warning: replay hash failed at tick "
    result.add $timeline.failTick
    result.add "; log may be partial.</li>\n"
  result.add "</ul>\n"
  result.add summary.renderSummaryHtml()
  result.add "</section>\n"
  result.add renderPlayersHtml(timeline.collectPlayers(), logHrefs)
  result.add renderEventsHtml(timeline)

proc renderReplayHtmlForTimeline*(
  timeline: ReplayTimeline,
  source,
  replayHref: string,
  labels: openArray[string]
): string =
  ## Renders a replay timeline as Tufte-style HTML body sections.
  let logHrefs: seq[string] = @[]
  renderReplayHtmlForTimeline(timeline, source, replayHref, labels, logHrefs)

proc renderReplayHtmlForPath*(
  path,
  replayHref: string,
  labels,
  logHrefs: openArray[string]
): string =
  ## Loads one replay path and renders Tufte-style HTML body sections.
  let timeline = expandReplayTimeline(loadReplay(path))
  renderReplayHtmlForTimeline(timeline, path, replayHref, labels, logHrefs)

proc renderReplayHtmlForPath*(
  path,
  replayHref: string,
  labels: openArray[string]
): string =
  ## Loads one replay path and renders Tufte-style HTML body sections.
  let logHrefs: seq[string] = @[]
  renderReplayHtmlForPath(path, replayHref, labels, logHrefs)

proc replayExtractorCss*(): string =
  ## Returns optional CSS for replay extraction fragments.
  result.add "    .replay-log .wrap { white-space: normal; }\n"
  result.add "    .replay-log td, .replay-log th { vertical-align: top; }\n"
  result.add "    .replay-log .who-col { width: 13rem; }\n"
  result.add "    .replay-log .what-col { white-space: normal; }\n"
  result.add "    .replay-log .mark-row td { color: "
  result.add "var(--failure-color, #f03b20); font-weight: 600; }\n"
  result.add "    .replay-players { max-width: 48rem; }\n"
  result.add "    .replay-players .role-col { width: 7rem; }\n"
  result.add "    .replay-players .result-col { width: 5rem; }\n"
  result.add "    .replay-players .result-win { color: "
  result.add "var(--failure-color, #f03b20); font-weight: 600; }\n"
  result.add "    .replay-players .log-col { width: 4rem; }\n"
  result.add "    .crew-chip { display: inline-flex; align-items: center; "
  result.add "gap: 0.35rem; white-space: nowrap; }\n"
  result.add "    .crew-swatch { box-sizing: border-box; display: inline-block; "
  result.add "width: 20px; height: 20px; border: 1px solid #000; "
  result.add "vertical-align: middle; flex: 0 0 auto; }\n"

proc pageStart(title, cssHref: string): string =
  ## Returns the standalone HTML page prefix.
  result.add "<!doctype html>\n"
  result.add "<html lang=\"en\">\n<head>\n"
  result.add "  <meta charset=\"utf-8\">\n"
  result.add "  <meta name=\"viewport\" content=\"width=device-width, "
  result.add "initial-scale=1\">\n"
  result.add "  <title>" & title.htmlEscape() & "</title>\n"
  result.add "  <link rel=\"stylesheet\" href=\"" & cssHref.htmlEscape()
  result.add "\">\n"
  result.add "  <style>\n"
  result.add "    table { table-layout: fixed; width: 100%; }\n"
  result.add "    .num { text-align: right; white-space: nowrap; }\n"
  result.add replayExtractorCss()
  result.add "  </style>\n"
  result.add "</head>\n<body>\n<main>\n"

proc pageEnd(): string =
  ## Returns the standalone HTML page suffix.
  "</main>\n</body>\n</html>\n"

proc replayHtmlPageForPath(
  path,
  cssHref: string,
  labels: openArray[string]
): string =
  ## Loads one replay path and renders a complete standalone HTML page.
  result.add pageStart("Crewrift Replay Extract", cssHref)
  result.add "<section>\n<h1>Crewrift Replay Extract</h1>\n</section>\n"
  result.add renderReplayHtmlForPath(path, path, labels)
  result.add pageEnd()

proc writeOutput(path, content: string) =
  ## Writes content to a path or stdout.
  if path.len == 0:
    stdout.write(content)
    if content.len == 0 or content[^1] != '\n':
      stdout.write("\n")
  else:
    path.parentDir().createDir()
    writeFile(path, content)

proc run(config: ReplayCliConfig) {.used.} =
  ## Runs the replay extractor command.
  if not fileExists(config.replayPath):
    fail("Replay file does not exist: " & config.replayPath)
  let content =
    case config.outputFormat
    of TextFormat:
      replayLogTextForPath(config.replayPath, config.labels)
    of HtmlFormat:
      replayHtmlPageForPath(
        config.replayPath,
        config.cssHref,
        config.labels
      )
  writeOutput(config.outputPath, content)

when isMainModule:
  try:
    run(readConfig())
  except ReplayExtractError as e:
    stderr.writeLine("replay_extractor failed: " & e.msg)
    quit 1
  except CatchableError as e:
    stderr.writeLine("replay_extractor failed: " & e.msg)
    quit 1
