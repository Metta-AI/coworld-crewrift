import
  std/[math, os, strutils],
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

  ReplayQuality* = object
    valid*: bool
    reason*: string
    playersJoined*: int
    reachedPlaying*: bool
    disconnected*: bool
    reconnected*: bool
    hashFailed*: bool

  ReplayRender* = object
    html*: string
    quality*: ReplayQuality

  PlayerInfo = object
    label: string
    role: string
    won: bool
    hasResult: bool
    dead: bool
    score: int
    tasks: int
    kills: int
    chats: int

  PhasePlayer = object
    tasks: int
    kills: int
    report: string
    aliveEnd: bool
    died: bool

  PhaseReport = object
    index: int
    startTick: int
    endTick: int
    hasMeeting: bool
    endedGame: bool
    initiatorSlot: int
    players: seq[PhasePlayer]
    chats: seq[ReplayEvent]
    votes: seq[ReplayEvent]

  SvgPoint = object
    x: float
    y: float

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
  PhaseWords = [
    "zero", "one", "two", "three", "four", "five", "six",
    "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty"
  ]

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

proc playerChipHtml(label: string, dead = false): string =
  ## Renders one replay player label with its color swatch.
  let clean = label.oneLine()
  if clean.len == 0:
    return ""
  result.add "<span class=\"crew-chip\">"
  result.add "<span class=\"crew-swatch\" style=\"background: "
  result.add colorHex(clean.colorNameFromLabel()).htmlEscape()
  result.add "\"></span><span"
  if dead:
    result.add " class=\"crew-name player-dead\""
  else:
    result.add " class=\"crew-name\""
  result.add ">"
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
  of Disconnected:
    "disconnect"
  of Reconnected:
    "reconnect"
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
      VoteCalledButton, VoteCast, Chat, Disconnected,
      Reconnected, PhaseChanged:
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
  of Disconnected:
    result = event.actorText(labels) & " disconnected"
  of Reconnected:
    result = event.actorText(labels) & " reconnected"
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

proc replayQualityForTimeline*(
  timeline: ReplayTimeline,
  expectedPlayers = PlayerLetters.len
): ReplayQuality =
  ## Classifies whether one replay should count in run analysis.
  result.valid = true
  result.reason = "ok"
  result.hashFailed = timeline.hashFailed
  var joined: seq[int]
  for event in timeline.events:
    case event.kind
    of PlayerJoined:
      if event.actorSlot >= 0 and event.actorSlot notin joined:
        joined.add event.actorSlot
    of PhaseChanged:
      if $event.phase == "Playing":
        result.reachedPlaying = true
    of Disconnected:
      result.disconnected = true
    of Reconnected:
      result.reconnected = true
    else:
      discard
  result.playersJoined = joined.len
  if result.hashFailed:
    result.valid = false
    result.reason = "replay hash failed"
  elif expectedPlayers > 0 and result.playersJoined < expectedPlayers:
    result.valid = false
    result.reason = "only " & $result.playersJoined & "/" &
      $expectedPlayers & " players joined"
  elif not result.reachedPlaying:
    result.valid = false
    result.reason = "never reached playing"
  elif result.disconnected or result.reconnected:
    result.valid = false
    if result.disconnected and result.reconnected:
      result.reason = "disconnect/reconnect"
    elif result.disconnected:
      result.reason = "disconnect"
    else:
      result.reason = "reconnect"

proc replayQualityForPath*(
  path: string,
  expectedPlayers = PlayerLetters.len
): ReplayQuality =
  ## Loads one replay and classifies whether it should count in analysis.
  expandReplayTimeline(loadReplay(path)).replayQualityForTimeline(
    expectedPlayers
  )

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

proc setPlayerDead(players: var seq[PlayerInfo], slot: int) =
  ## Marks one player as dead or voted out.
  if slot < 0:
    return
  while players.len <= slot:
    players.add PlayerInfo()
  players[slot].dead = true

proc addPlayerTask(players: var seq[PlayerInfo], slot: int) =
  ## Counts one completed task for a player.
  if slot < 0:
    return
  while players.len <= slot:
    players.add PlayerInfo()
  inc players[slot].tasks

proc addPlayerKill(players: var seq[PlayerInfo], slot: int) =
  ## Counts one kill for a player.
  if slot < 0:
    return
  while players.len <= slot:
    players.add PlayerInfo()
  inc players[slot].kills

proc addPlayerChat(players: var seq[PlayerInfo], slot: int) =
  ## Counts one chat message for a player.
  if slot < 0:
    return
  while players.len <= slot:
    players.add PlayerInfo()
  inc players[slot].chats

proc addPlayerScore(
  players: var seq[PlayerInfo],
  slot,
  amount: int
) =
  ## Adds one score delta for a player.
  if slot < 0:
    return
  while players.len <= slot:
    players.add PlayerInfo()
  players[slot].score += amount

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
    case event.kind
    of Kill:
      result.addPlayerKill(event.actorSlot)
      result.setPlayerDead(event.secondarySlot)
    of Died:
      result.setPlayerDead(event.actorSlot)
    of CompletedTask:
      result.addPlayerTask(event.actorSlot)
    of Chat:
      result.addPlayerChat(event.actorSlot)
    of Score:
      result.addPlayerScore(event.actorSlot, event.scoreAmount)
    else:
      discard
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

proc renderLogCell(href: string): string =
  ## Renders one player log table cell.
  if href.startsWith("missing:"):
    return "<span class=\"log-missing\">missing</span>"
  if href.startsWith("not-requested:"):
    return "<span class=\"log-muted\">opponent</span>"
  if href.len > 0:
    return "<a href=\"" & href.htmlEscape() & "\">log</a>"
  "-"

proc phasePlayerCount(
  timeline: ReplayTimeline,
  players: openArray[PlayerInfo]
): int =
  ## Returns the player slots needed by phase reports.
  result = max(PlayerLetters.len, players.len)
  for event in timeline.events:
    if event.actorSlot >= 0:
      result = max(result, event.actorSlot + 1)
    if event.secondarySlot >= 0:
      result = max(result, event.secondarySlot + 1)

proc initPhase(index, startTick, playerCount: int): PhaseReport =
  ## Builds an empty phase report.
  result.index = index
  result.startTick = startTick
  result.endTick = startTick
  result.initiatorSlot = -1
  result.players = newSeq[PhasePlayer](playerCount)
  for player in result.players.mitems:
    player.aliveEnd = true

proc ensurePhaseSlot(phase: var PhaseReport, slot: int) =
  ## Expands phase player stats to include one slot.
  if slot < 0:
    return
  while phase.players.len <= slot:
    phase.players.add PhasePlayer(aliveEnd: true)

proc ensureAliveSlot(alive: var seq[bool], slot: int) =
  ## Expands alive tracking to include one slot.
  if slot < 0:
    return
  while alive.len <= slot:
    alive.add true

proc markPhaseDeath(
  phase: var PhaseReport,
  alive: var seq[bool],
  slot: int
) =
  ## Marks one player as dead during a phase.
  if slot < 0:
    return
  phase.ensurePhaseSlot(slot)
  alive.ensureAliveSlot(slot)
  phase.players[slot].died = true
  alive[slot] = false

proc markPhaseAlive(alive: var seq[bool], slot: int) =
  ## Marks one player as alive again.
  if slot < 0:
    return
  alive.ensureAliveSlot(slot)
  alive[slot] = true

proc recordPhaseEvent(
  phase: var PhaseReport,
  alive: var seq[bool],
  event: ReplayEvent
) =
  ## Applies one replay event to a phase summary.
  case event.kind
  of CompletedTask:
    phase.ensurePhaseSlot(event.actorSlot)
    if event.actorSlot >= 0:
      inc phase.players[event.actorSlot].tasks
  of Kill:
    phase.ensurePhaseSlot(event.actorSlot)
    if event.actorSlot >= 0:
      inc phase.players[event.actorSlot].kills
    phase.markPhaseDeath(alive, event.secondarySlot)
  of Died:
    phase.markPhaseDeath(alive, event.actorSlot)
  of Revived:
    alive.markPhaseAlive(event.actorSlot)
  of VoteCalledBody, VoteCalledButton:
    phase.hasMeeting = true
    phase.initiatorSlot = event.actorSlot
    phase.ensurePhaseSlot(event.actorSlot)
    if event.actorSlot >= 0:
      phase.players[event.actorSlot].report =
        if event.kind == VoteCalledBody:
          "body"
        else:
          "button"
  of Chat:
    phase.chats.add event
  of VoteCast:
    phase.votes.add event
  else:
    discard

proc closePhase(
  phases: var seq[PhaseReport],
  phase: var PhaseReport,
  alive: openArray[bool],
  endTick: int,
  endedGame: bool
) =
  ## Finishes one phase with its end-of-phase alive state.
  phase.endTick = endTick
  phase.endedGame = endedGame
  for i in 0 ..< phase.players.len:
    phase.players[i].aliveEnd = i < alive.len and alive[i]
  phases.add phase

proc collectPhases(
  timeline: ReplayTimeline,
  players: openArray[PlayerInfo]
): seq[PhaseReport] =
  ## Builds high-level play and voting phases from replay events.
  let playerCount = timeline.phasePlayerCount(players)
  var
    alive = newSeq[bool](playerCount)
    phase = initPhase(1, 0, playerCount)
    open = true
  for i in 0 ..< alive.len:
    alive[i] = true
  for event in timeline.events:
    if not open:
      break
    phase.recordPhaseEvent(alive, event)
    if event.kind == PhaseChanged and $event.phase == "GameOver":
      result.closePhase(phase, alive, event.tick, true)
      open = false
    elif phase.hasMeeting and
        event.kind == PhaseChanged and
        $event.phase == "Playing":
      result.closePhase(phase, alive, event.tick, false)
      phase = initPhase(result.len + 1, event.tick, alive.len)
  if open:
    result.closePhase(phase, alive, timeline.tickCount, false)

proc phaseName(index: int): string =
  ## Returns a human phase label.
  if index >= 0 and index < PhaseWords.len:
    return "Phase " & PhaseWords[index]
  "Phase " & $index

proc phasePlayerLabel(
  players: openArray[PlayerInfo],
  slot: int
): string =
  ## Returns one phase-table player label.
  if slot >= 0 and slot < players.len and players[slot].label.len > 0:
    return players[slot].label
  if slot >= 0:
    return slot.seatPrefix()
  ""

proc isMainBotSlot(labels: openArray[string], slot: int): bool =
  ## Returns true when one slot belongs to the optimized notsus bot.
  if slot < 0 or slot >= labels.len:
    return false
  var clean = ""
  for ch in labels[slot].toLowerAscii():
    if ch in {'a' .. 'z', '0' .. '9'}:
      clean.add ch
  clean.contains("notsus")

proc renderPhaseTable(
  phase: PhaseReport,
  players: openArray[PlayerInfo]
): string =
  ## Renders one phase player summary table.
  result.add "<table class=\"report-table no-sort phase-table\">\n"
  result.add "<thead><tr><th>Seat</th><th>Player</th><th>Role</th>"
  result.add "<th>Tasks</th><th>Kills</th><th>Report</th></tr></thead>\n"
  result.add "<tbody>\n"
  for slot, stats in phase.players:
    let label = players.phasePlayerLabel(slot)
    if label.len == 0:
      continue
    let role =
      if slot < players.len:
        players[slot].role.roleDisplay()
      else:
        "-"
    result.add "<tr><td>" & slot.seatPrefix().htmlEscape()
    result.add "</td><td class=\"player-col\">"
    result.add label.playerChipHtml(not stats.aliveEnd)
    result.add "</td><td class=\"role-col\">"
    result.add role.htmlEscape()
    result.add "</td><td class=\"count-col\">" & $stats.tasks
    result.add "</td><td class=\"count-col\">" & $stats.kills
    result.add "</td><td class=\"report-col\">"
    if stats.report.len > 0:
      result.add stats.report.htmlEscape()
    else:
      result.add "-"
    result.add "</td></tr>\n"
  result.add "</tbody></table>\n"

proc renderFinalOutcomeHtml(players: openArray[PlayerInfo]): string =
  ## Renders the final game outcome as a compact table.
  if players.len == 0:
    return ""
  result.add "<section class=\"final-outcome-section\">\n"
  result.add "<h2>Final Outcome</h2>\n"
  result.add "<table class=\"report-table no-sort phase-table final-outcome\">\n"
  result.add "<thead><tr><th>Seat</th><th>Player</th><th>Role</th>"
  result.add "<th>Score</th><th>Tasks</th><th>Kills</th>"
  result.add "<th>Result</th></tr></thead>\n"
  result.add "<tbody>\n"
  for slot, player in players:
    if player.label.len == 0:
      continue
    result.add "<tr><td>" & slot.seatPrefix().htmlEscape()
    result.add "</td><td class=\"player-col\">"
    result.add player.label.playerChipHtml(player.dead)
    result.add "</td><td class=\"role-col\">"
    result.add player.role.roleDisplay().htmlEscape()
    result.add "</td><td class=\"count-col\">" & $player.score
    result.add "</td><td class=\"count-col\">" & $player.tasks
    result.add "</td><td class=\"count-col\">" & $player.kills
    result.add "</td><td class=\"" & player.resultClass().htmlEscape()
    result.add "\">"
    result.add player.resultDisplay().htmlEscape()
    result.add "</td></tr>\n"
  result.add "</tbody></table>\n"
  result.add "</section>\n"

proc chatActorLabel(
  event: ReplayEvent,
  labels: openArray[string]
): string =
  ## Returns a readable actor label for a phase chat.
  if event.actorLabel.len > 0:
    return event.actorLabel
  if event.actorSlot >= 0 and event.actorSlot < labels.len:
    return labels[event.actorSlot]
  event.actorSlot.seatPrefix()

proc renderPhaseChats(
  phase: PhaseReport,
  labels: openArray[string]
): string =
  ## Renders chat messages for one phase as left/right bubbles.
  result.add "<div class=\"phase-chats\">\n"
  if phase.chats.len == 0:
    result.add "<p class=\"phase-empty\">No chat messages.</p>\n"
  for chat in phase.chats:
    var rowClass = "phase-chat-row"
    if labels.isMainBotSlot(chat.actorSlot):
      rowClass.add " own"
    result.add "<div class=\"" & rowClass & "\"><div class=\"phase-bubble\">"
    result.add "<div class=\"phase-bubble-who\">"
    result.add chat.chatActorLabel(labels).playerChipHtml()
    result.add "</div><div class=\"phase-bubble-text\">"
    result.add chat.chatText.oneLine().htmlEscape()
    result.add "</div></div></div>\n"
  result.add "</div>\n"

proc svgNum(value: float): string =
  ## Formats one SVG coordinate.
  value.formatFloat(ffDecimal, 2)

proc votePoint(
  index,
  count: int,
  center,
  radius: float
): SvgPoint =
  ## Returns one point on the voting circle.
  let angle = -PI / 2.0 + 2.0 * PI * index.float / count.float
  SvgPoint(
    x: center + cos(angle) * radius,
    y: center + sin(angle) * radius
  )

proc shortenLine(
  source,
  target: SvgPoint,
  sourcePad,
  targetPad: float
): tuple[start, finish: SvgPoint] =
  ## Shortens a vote arrow so it meets node edges.
  let
    dx = target.x - source.x
    dy = target.y - source.y
    distance = sqrt(dx * dx + dy * dy)
  if distance <= sourcePad + targetPad or distance <= 0.001:
    return (start: source, finish: target)
  let
    ux = dx / distance
    uy = dy / distance
  (
    start: SvgPoint(
      x: source.x + ux * sourcePad,
      y: source.y + uy * sourcePad
    ),
    finish: SvgPoint(
      x: target.x - ux * targetPad,
      y: target.y - uy * targetPad
    )
  )

proc voteTextColor(colorName: string): string =
  ## Returns black or white text for one player color.
  case colorName
  of "blue", "green", "purple", "brown", "navy", "maroon",
      "gray", "red":
    "#ffffff"
  else:
    "#000000"

proc roleIsImposter(role: string): bool =
  ## Returns true when one role is an imposter role.
  role.toLowerAscii().contains("imp")

proc renderVoteSvg(
  phase: PhaseReport,
  players: openArray[PlayerInfo]
): string =
  ## Renders an SVG vote graph for one meeting phase.
  const
    Size = 360.0
    Center = Size / 2.0
    Radius = 132.0
    Node = 20.0
    NodePad = 16.0
  var slots: seq[int]
  for slot in 0 ..< phase.players.len:
    if players.phasePlayerLabel(slot).len > 0:
      slots.add slot
  if slots.len == 0:
    return ""
  var
    points = newSeq[SvgPoint](phase.players.len)
    present = newSeq[bool](phase.players.len)
    hasSkip = false
  for i, slot in slots:
    points[slot] = votePoint(i, slots.len, Center, Radius)
    present[slot] = true
  let markerId = "vote-arrow-" & $phase.index
  result.add "<div class=\"phase-vote\"><svg class=\"phase-vote-svg\" "
  result.add "viewBox=\"0 0 360 360\" role=\"img\" "
  result.add "aria-label=\"Voting diagram for "
  result.add phase.index.phaseName().htmlEscape()
  result.add "\">\n"
  result.add "<defs><marker id=\"" & markerId.htmlEscape()
  result.add "\" markerWidth=\"8\" markerHeight=\"8\" refX=\"7\" "
  result.add "refY=\"4\" orient=\"auto\" markerUnits=\"strokeWidth\">"
  result.add "<path d=\"M0,0 L8,4 L0,8 z\" fill=\"#222\"></path>"
  result.add "</marker></defs>\n"
  var voteOrder = 0
  for vote in phase.votes:
    inc voteOrder
    if vote.actorSlot < 0 or vote.actorSlot >= present.len or
        not present[vote.actorSlot]:
      continue
    let source = points[vote.actorSlot]
    var target: SvgPoint
    var targetPad = NodePad
    if vote.voteSkip:
      target = SvgPoint(x: Center, y: Center)
      targetPad = 28.0
      hasSkip = true
    elif vote.secondarySlot >= 0 and
        vote.secondarySlot < present.len and
        present[vote.secondarySlot]:
      target = points[vote.secondarySlot]
    else:
      continue
    let line = shortenLine(source, target, NodePad, targetPad)
    result.add "<line class=\"vote-arrow\" x1=\""
    result.add line.start.x.svgNum() & "\" y1=\""
    result.add line.start.y.svgNum() & "\" x2=\""
    result.add line.finish.x.svgNum() & "\" y2=\""
    result.add line.finish.y.svgNum() & "\" marker-end=\"url(#"
    result.add markerId.htmlEscape() & ")\"></line>\n"
    let
      labelX = (line.start.x + line.finish.x) / 2.0
      labelY = (line.start.y + line.finish.y) / 2.0 - 6.0
    result.add "<g class=\"vote-order\"><circle class=\"vote-order-dot\" cx=\""
    result.add labelX.svgNum() & "\" cy=\"" & labelY.svgNum()
    result.add "\" r=\"8\"></circle><text class=\"vote-order-text\" x=\""
    result.add labelX.svgNum() & "\" y=\"" & (labelY + 4.0).svgNum()
    result.add "\">" & $voteOrder & "</text></g>\n"
  if hasSkip:
    result.add "<circle class=\"vote-skip\" cx=\"180\" cy=\"180\" "
    result.add "r=\"24\"></circle>\n"
    result.add "<text class=\"vote-skip-text\" x=\"180\" y=\"185\">"
    result.add "skip</text>\n"
  for slot in slots:
    let
      label = players.phasePlayerLabel(slot)
      colorName = label.colorNameFromLabel()
      point = points[slot]
      role =
        if slot < players.len:
          players[slot].role
        else:
          ""
      dead = slot < phase.players.len and not phase.players[slot].aliveEnd
      imposter = not dead and role.roleIsImposter()
      nodeClass =
        if dead:
          "vote-node dead"
        else:
          "vote-node"
      nodeFill =
        if dead:
          "var(--page-background, #fffff8)"
        else:
          colorHex(colorName)
      textFill =
        if dead:
          "#777777"
        else:
          colorName.voteTextColor()
    result.add "<g class=\"" & nodeClass & "\"><title>"
    result.add (slot.seatPrefix() & " " & label).htmlEscape()
    if dead:
      result.add " dead"
    result.add "</title>"
    if imposter:
      result.add "<rect class=\"vote-imposter\" x=\""
      result.add (point.x - Node / 2.0 - 3.0).svgNum()
      result.add "\" y=\"" & (point.y - Node / 2.0 - 3.0).svgNum()
      result.add "\" width=\"26\" height=\"26\" fill=\"none\" "
      result.add "stroke=\"#c51111\" stroke-width=\"3\"></rect>"
    result.add "<rect class=\"vote-player-square\" x=\""
    result.add (point.x - Node / 2.0).svgNum()
    result.add "\" y=\"" & (point.y - Node / 2.0).svgNum()
    result.add "\" width=\"20\" height=\"20\" fill=\""
    result.add nodeFill.htmlEscape()
    result.add "\" stroke=\"#000000\" stroke-width=\"1\"></rect><text x=\""
    result.add point.x.svgNum() & "\" y=\"" & (point.y + 4.5).svgNum()
    result.add "\" fill=\"" & textFill
    result.add "\">" & slot.seatPrefix().htmlEscape()
    result.add "</text></g>\n"
  if phase.votes.len == 0:
    result.add "<text class=\"vote-empty\" x=\"180\" y=\"185\">"
    result.add "no votes</text>\n"
  result.add "</svg></div>\n"

proc renderPhasesHtml(
  timeline: ReplayTimeline,
  labels: openArray[string],
  players: openArray[PlayerInfo]
): string =
  ## Renders high-level phase summaries before the raw event log.
  let phases = timeline.collectPhases(players)
  if phases.len == 0:
    return ""
  result.add "<section class=\"phases-section\">\n"
  result.add "<h2>Phases</h2>\n"
  for phase in phases:
    result.add "<section class=\"phase-block\">\n"
    result.add "<h3>" & phase.index.phaseName().htmlEscape()
    result.add " <span class=\"phase-time\">"
    result.add phase.startTick.eventTimeText().htmlEscape()
    result.add " - "
    result.add phase.endTick.eventTimeText().htmlEscape()
    result.add "</span></h3>\n"
    result.add renderPhaseTable(phase, players)
    if phase.hasMeeting:
      result.add renderPhaseChats(phase, labels)
      result.add renderVoteSvg(phase, players)
    result.add "</section>\n"
  result.add "</section>\n"

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
  of Disconnected:
    result.add "disconnected"
  of Reconnected:
    result.add "reconnected"
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
  result.add "<th>Win/Loss</th><th>Tasks</th><th>Kills</th><th>Chat</th>"
  if showLogs:
    result.add "<th>Log</th>"
  result.add "</tr>"
  result.add "</thead>\n"
  result.add "<tbody>\n"
  for slot, player in players:
    if player.label.len == 0:
      continue
    result.add "<tr><td>" & slot.seatPrefix().htmlEscape()
    result.add "</td><td class=\"player-col\">"
    result.add player.label.playerChipHtml(player.dead)
    result.add "</td><td class=\"role-col\">"
    result.add player.role.roleDisplay().htmlEscape()
    result.add "</td><td class=\"" & player.resultClass().htmlEscape() & "\">"
    result.add player.resultDisplay().htmlEscape()
    result.add "</td><td class=\"count-col\">" & $player.tasks
    result.add "</td><td class=\"count-col\">" & $player.kills
    result.add "</td><td class=\"count-col\">" & $player.chats & "</td>"
    if showLogs:
      result.add "<td class=\"log-col\">"
      if slot >= 0 and slot < logHrefs.len:
        result.add renderLogCell(logHrefs[slot])
      else:
        result.add "-"
      result.add "</td>"
    result.add "</tr>\n"
  result.add "</tbody></table>\n"
  result.add "</section>\n"

proc renderReplayStatsHtml(
  timeline: ReplayTimeline,
  source,
  replayHref: string,
  summary: ReplaySummary
): string =
  ## Renders replay metadata and counts in a footer section.
  result.add "<section class=\"replay-stats\">\n"
  result.add "<h2>Replay Log Stats</h2>\n"
  result.add "<ul>\n"
  if replayHref.len > 0:
    result.add "<li>Source: <code>" & replayHref.htmlEscape()
    result.add "</code></li>\n"
  elif source.len > 0:
    result.add "<li>Source: <code>" & source.htmlEscape()
    result.add "</code></li>\n"
  if source.len > 0 and source != replayHref:
    result.add "<li>Local path: <code>" & source.htmlEscape()
    result.add "</code></li>\n"
  result.add "<li>Ticks parsed: " & $timeline.tickCount & "</li>\n"
  if timeline.hashFailed:
    result.add "<li>Warning: replay hash failed at tick "
    result.add $timeline.failTick
    result.add "; log may be partial.</li>\n"
  result.add "<li>Tasks: " & $summary.tasks & "</li>\n"
  result.add "<li>Kills: " & $summary.kills & "</li>\n"
  result.add "<li>Meetings called: " & $summary.meetings & "</li>\n"
  result.add "<li>Chat messages: " & $summary.chats & "</li>\n"
  result.add "<li>Votes cast: " & $summary.votes & "</li>\n"
  result.add "<li>Deaths or vote-outs: " & $summary.deaths & "</li>\n"
  result.add "</ul>\n"
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
  let summary = timeline.replaySummary()
  let players = timeline.collectPlayers()
  result.add renderPlayersHtml(players, logHrefs)
  result.add renderPhasesHtml(timeline, labels, players)
  result.add renderFinalOutcomeHtml(players)
  result.add renderEventsHtml(timeline)
  result.add renderReplayStatsHtml(timeline, source, replayHref, summary)

proc renderReplayHtmlForTimeline*(
  timeline: ReplayTimeline,
  source,
  replayHref: string,
  labels: openArray[string]
): string =
  ## Renders a replay timeline as Tufte-style HTML body sections.
  let logHrefs: seq[string] = @[]
  renderReplayHtmlForTimeline(timeline, source, replayHref, labels, logHrefs)

proc renderReplayBodyForPath*(
  path,
  replayHref: string,
  labels,
  logHrefs: openArray[string],
  expectedPlayers = PlayerLetters.len
): ReplayRender =
  ## Loads one replay path and returns its HTML body and quality.
  let timeline = expandReplayTimeline(loadReplay(path))
  result.quality = timeline.replayQualityForTimeline(expectedPlayers)
  result.html = renderReplayHtmlForTimeline(
    timeline,
    path,
    replayHref,
    labels,
    logHrefs
  )

proc renderReplayHtmlForPath*(
  path,
  replayHref: string,
  labels,
  logHrefs: openArray[string]
): string =
  ## Loads one replay path and renders Tufte-style HTML body sections.
  renderReplayBodyForPath(path, replayHref, labels, logHrefs).html

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
  result.add "    .replay-players { max-width: 56rem; }\n"
  result.add "    .replay-players .player-col { text-align: left; }\n"
  result.add "    .replay-players .role-col { width: 7rem; }\n"
  result.add "    .replay-players .result-col { width: 5rem; }\n"
  result.add "    .replay-players .count-col { width: 4rem; "
  result.add "text-align: right; font-variant-numeric: tabular-nums; }\n"
  result.add "    .replay-players .result-win { color: "
  result.add "var(--failure-color, #f03b20); font-weight: 600; }\n"
  result.add "    .replay-players .log-col { width: 4rem; }\n"
  result.add "    .replay-players .player-dead, "
  result.add ".phase-table .player-dead { color: "
  result.add "var(--failure-color, #f03b20); text-decoration: "
  result.add "line-through; text-decoration-thickness: 2px; }\n"
  result.add "    .phase-block { margin-top: 1.2rem; }\n"
  result.add "    .phase-block h3 { margin-bottom: 0.35rem; }\n"
  result.add "    .phase-time { color: #666; font-size: 0.9rem; "
  result.add "font-weight: 400; }\n"
  result.add "    .phase-table { max-width: 56rem; margin-bottom: 0.6rem; }\n"
  result.add "    .phase-table .player-col { text-align: left; }\n"
  result.add "    .phase-table .role-col { width: 7rem; }\n"
  result.add "    .phase-table .count-col { width: 4rem; text-align: right; "
  result.add "font-variant-numeric: tabular-nums; }\n"
  result.add "    .phase-table .report-col { width: 6rem; }\n"
  result.add "    .phase-table .result-col { width: 5rem; }\n"
  result.add "    .final-outcome .result-win { color: "
  result.add "var(--failure-color, #f03b20); font-weight: 600; }\n"
  result.add "    .phase-chats { margin: 0.65rem 0; max-width: 56rem; }\n"
  result.add "    .phase-chat-row { display: flex; margin: 0.35rem 0; }\n"
  result.add "    .phase-chat-row.own { justify-content: flex-end; }\n"
  result.add "    .phase-bubble { border: 1px solid rgba(0,0,0,0.32); "
  result.add "border-radius: 8px; padding: 0.45rem 0.55rem; "
  result.add "max-width: min(34rem, 76%); background: #fff; }\n"
  result.add "    .phase-chat-row.own .phase-bubble { background: #eef7ff; }\n"
  result.add "    .phase-bubble-who { margin-bottom: 0.2rem; }\n"
  result.add "    .phase-bubble-text { white-space: normal; }\n"
  result.add "    .phase-empty { color: #777; font-style: italic; }\n"
  result.add "    .phase-vote { max-width: 360px; overflow-x: auto; "
  result.add "margin: 0.5rem 0 1rem; }\n"
  result.add "    .phase-vote-svg { display: block; max-width: 100%; "
  result.add "height: auto; }\n"
  result.add "    .vote-arrow { stroke: #222; stroke-width: 1.5; "
  result.add "opacity: 0.72; }\n"
  result.add "    .vote-node text, .vote-skip-text, .vote-empty { "
  result.add "font-family: sans-serif; font-size: 11px; text-anchor: middle; "
  result.add "font-weight: 700; pointer-events: none; }\n"
  result.add "    .vote-node.dead text { opacity: 0.45; }\n"
  result.add "    .vote-order-dot { fill: #fff; stroke: #000; "
  result.add "stroke-width: 1; }\n"
  result.add "    .vote-order-text { font-family: sans-serif; "
  result.add "font-size: 10px; font-weight: 700; text-anchor: middle; "
  result.add "pointer-events: none; fill: #000; }\n"
  result.add "    .vote-skip { fill: #fff; stroke: #000; stroke-width: 1; }\n"
  result.add "    .vote-empty { fill: #777; font-weight: 400; }\n"
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
