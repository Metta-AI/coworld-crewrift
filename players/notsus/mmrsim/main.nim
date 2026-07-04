import
  std/[algorithm, strutils]

const
  PolicyCount = 15
  ActivePolicyCount = 8
  SeatsPerGame = 8
  MinGamesPerClick = 1
  MaxGamesPerClick = 100
  PairingSearchSamples = 192
  PairingChoiceWindow = 4
  AutoMinPairTarget = 10
  AutoMaxPairTarget = 1000
  AutoDefaultPairTarget = 10
  AutoTickMs = 100
  AutoTolerance = 0.10
  HeatCellSize = 32
  HeatLeftLabelWidth = 170
  HeatRightLabelWidth = HeatLeftLabelWidth
  HeatTopLabelHeight = 130
  HeatPadding = 12
  HeatLowColor = [255, 255, 248]
  HeatHighColor = [17, 17, 17]
  PolicyLetters = [
    "A", "B", "C", "D", "E", "F",
    "G", "H", "I", "J", "K", "L",
    "M", "N", "O"
  ]

type
  MatchupCounts = array[PolicyCount, array[PolicyCount, int]]

  AllocationType = enum
    AllocateRandom,
    AllocatePolicies,
    AllocatePairings

  Policy = object
    label: string
    active: bool

  Game = object
    seats: array[SeatsPerGame, int]

  PolicyRank = object
    index: int
    games: int
    tie: float

  PairStats = object
    pairs: int
    total: int
    inside: int
    below: int
    above: int
    average: float

  AppState = object
    policies: array[PolicyCount, Policy]
    games: seq[Game]
    sliderGames: int
    targetPairGames: int
    allocationType: AllocationType
    autoRunning: bool
    autoTimerId: int
    autoSteps: int
    message: string

var state: AppState

proc setHtml(id, html: cstring) {.
  importjs: "document.getElementById(#).innerHTML = #"
.}

proc setText(id, text: cstring) {.
  importjs: "document.getElementById(#).textContent = #"
.}

proc setDisabled(id: cstring, disabled: bool) {.
  importjs: "document.getElementById(#).disabled = #"
.}

proc setAttribute(id, name, value: cstring) {.
  importjs: "document.getElementById(#).setAttribute(#, #)"
.}

proc addClickListener(id: cstring, callback: proc() {.closure.}) {.
  importjs: "document.getElementById(#).addEventListener('click', #)"
.}

proc addInputListener(id: cstring, callback: proc() {.closure.}) {.
  importjs: "document.getElementById(#).addEventListener('input', #)"
.}

proc startTimer(callback: proc() {.closure.}, ms: int): int {.
  importjs: "setInterval(#, #)"
.}

proc clearTimer(id: int) {.
  importjs: "clearInterval(#)"
.}

proc addPolicyChangeListener(callback: proc(
  index: int,
  selected: bool
) {.closure.}) {.
  importjs: """
document.getElementById('policy-panel').addEventListener('change', function(event) {
  var input = event.target.closest('input[data-policy-index]');
  if (input == null) return;
  #(parseInt(input.getAttribute('data-policy-index'), 10), input.checked);
})
"""
.}

proc readAllocationValue(): cstring {.
  importjs: """
(document.querySelector('input[name="allocation-type"]:checked') || {}).value ||
  'random'
"""
.}

proc readSliderValue(): int {.
  importjs: "parseInt(document.getElementById('game-slider').value, 10)"
.}

proc readTargetValue(): int {.
  importjs: "parseInt(document.getElementById('auto-target-slider').value, 10)"
.}

proc randomUnit(): float {.
  importjs: "Math.random()"
.}

proc escapeHtml(text: string): string =
  ## Escapes text for safe HTML insertion.
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

proc clampInt(value, low, high: int): int =
  ## Returns a value inside an inclusive integer range.
  if value < low:
    low
  elif value > high:
    high
  else:
    value

proc plural(count: int, one, many: string): string =
  ## Returns the singular or plural noun for a count.
  if count == 1:
    one
  else:
    many

proc policyInputId(index: int): string =
  ## Returns the DOM id for one policy checkbox.
  "policy-check-" & $index

proc policyLabel(index: int): string =
  ## Returns the default display label for one policy.
  "Policy " & PolicyLetters[index]

proc activeCount(): int =
  ## Returns the number of policies currently in the game pool.
  for policy in state.policies:
    if policy.active:
      inc result

proc activeIndices(): seq[int] =
  ## Returns the active policy indices.
  for i, policy in state.policies:
    if policy.active:
      result.add i

proc allocationText(allocationType: AllocationType): string =
  ## Returns a display name for one allocation type.
  case allocationType
  of AllocateRandom:
    "random"
  of AllocatePolicies:
    "policy-equalized"
  of AllocatePairings:
    "pairing-equalized"

proc gameIncluded(game: Game): bool =
  ## Returns true when every policy in a game is selected.
  for index in game.seats:
    if not state.policies[index].active:
      return false
  true

proc activeGameCount(): int =
  ## Returns the number of stored games passing the selection filter.
  for game in state.games:
    if game.gameIncluded():
      inc result

proc computeCounts(): MatchupCounts =
  ## Builds matchup counts from stored games that pass the selection filter.
  for game in state.games:
    if not game.gameIncluded():
      continue
    for i in 0 ..< SeatsPerGame:
      for j in i + 1 ..< SeatsPerGame:
        let
          a = game.seats[i]
          b = game.seats[j]
        inc result[a][b]
        inc result[b][a]

proc maxCount(counts: MatchupCounts): int =
  ## Returns the largest matchup count in the matrix.
  for i in 0 ..< PolicyCount:
    for j in 0 ..< PolicyCount:
      if i != j:
        result = max(result, counts[i][j])

proc policyGameCount(counts: MatchupCounts, index: int): int =
  ## Returns included games that contain one selected policy.
  if not state.policies[index].active:
    return 0
  var pairs = 0
  for j in 0 ..< PolicyCount:
    if index != j:
      pairs += counts[index][j]
  pairs div (SeatsPerGame - 1)

proc autoLowTarget(): float =
  ## Returns the lower pairing target bound.
  state.targetPairGames.float * (1.0 - AutoTolerance)

proc autoHighTarget(): float =
  ## Returns the upper pairing target bound.
  state.targetPairGames.float * (1.0 + AutoTolerance)

proc computePairStats(
  counts: MatchupCounts,
  active: openArray[int]
): PairStats =
  ## Computes target-range stats for active policy pairings.
  let
    low = autoLowTarget()
    high = autoHighTarget()
  for i in 0 ..< active.len:
    for j in i + 1 ..< active.len:
      let count = counts[active[i]][active[j]]
      inc result.pairs
      result.total += count
      if count.float < low:
        inc result.below
      elif count.float > high:
        inc result.above
      else:
        inc result.inside
  if result.pairs > 0:
    result.average = result.total.float / result.pairs.float

proc autoTargetReached(counts: MatchupCounts): bool =
  ## Returns true when active pairings are inside the target band.
  let active = activeIndices()
  if active.len < SeatsPerGame:
    return false
  let stats = computePairStats(counts, active)
  stats.pairs > 0 and stats.below == 0 and stats.above == 0

proc randomIndex(limit: int): int =
  ## Returns a random integer from zero up to but not including limit.
  if limit <= 0:
    return 0
  result = int(randomUnit() * limit.float)
  if result >= limit:
    result = limit - 1

proc shuffle(values: var seq[int]) =
  ## Shuffles integer values in place.
  if values.len <= 1:
    return
  var i = values.len - 1
  while i > 0:
    let j = randomIndex(i + 1)
    swap values[i], values[j]
    dec i

proc randomSeats(active: openArray[int]): seq[int] =
  ## Picks random seats from the active policies.
  for index in active:
    result.add index
  result.shuffle()
  result.setLen SeatsPerGame

proc equalizedSeats(active: openArray[int]): seq[int] =
  ## Picks the active policies with the fewest included games.
  let counts = computeCounts()
  var ranks: seq[PolicyRank]
  for index in active:
    ranks.add PolicyRank(
      index: index,
      games: policyGameCount(counts, index),
      tie: randomUnit()
    )
  ranks.sort do (a, b: PolicyRank) -> int:
    if a.games < b.games:
      -1
    elif a.games > b.games:
      1
    elif a.tie < b.tie:
      -1
    elif a.tie > b.tie:
      1
    else:
      cmp(a.index, b.index)
  for i in 0 ..< SeatsPerGame:
    result.add ranks[i].index

proc minPairCount(
  counts: MatchupCounts,
  active: openArray[int]
): int =
  ## Returns the lowest pairing count among active policies.
  var found = false
  for i in 0 ..< active.len:
    for j in i + 1 ..< active.len:
      let count = counts[active[i]][active[j]]
      if not found or count < result:
        found = true
        result = count

proc pairPenalty(counts: MatchupCounts, a, b, lowest: int): int =
  ## Returns the squared pressure for one policy pairing.
  let delta = counts[a][b] - lowest + 1
  delta * delta

proc pairScore(
  counts: MatchupCounts,
  seats: openArray[int],
  lowest: int
): int =
  ## Returns the pressure score for all pairings in one game.
  for i in 0 ..< seats.len:
    for j in i + 1 ..< seats.len:
      result += pairPenalty(
        counts,
        seats[i],
        seats[j],
        lowest
      )

proc addPairScore(
  counts: MatchupCounts,
  seats: openArray[int],
  index,
  lowest: int
): int =
  ## Returns the added pressure from seating one more policy.
  for seat in seats:
    result += pairPenalty(
      counts,
      seat,
      index,
      lowest
    )

proc removeValue(values: var seq[int], value: int) =
  ## Removes the first matching integer from a sequence.
  for i in 0 ..< values.len:
    if values[i] == value:
      values.delete(i)
      return

proc sampledPairingSeats(
  counts: MatchupCounts,
  active: openArray[int],
  lowest: int
): seq[int] =
  ## Builds one sampled game by favoring low-pressure additions.
  var remaining: seq[int]
  for index in active:
    remaining.add index
  result.add remaining[randomIndex(remaining.len)]
  remaining.removeValue(result[0])
  while result.len < SeatsPerGame:
    var ranks: seq[PolicyRank]
    for index in remaining:
      ranks.add PolicyRank(
        index: index,
        games: addPairScore(counts, result, index, lowest),
        tie: randomUnit()
      )
    ranks.sort do (a, b: PolicyRank) -> int:
      if a.games < b.games:
        -1
      elif a.games > b.games:
        1
      elif a.tie < b.tie:
        -1
      elif a.tie > b.tie:
        1
      else:
        cmp(a.index, b.index)
    let
      window = min(PairingChoiceWindow, ranks.len)
      chosen = ranks[randomIndex(window)].index
    result.add chosen
    remaining.removeValue(chosen)

proc pairizedSeats(active: openArray[int]): seq[int] =
  ## Samples rosters and picks the lowest-pressure pairing game.
  let
    counts = computeCounts()
    lowest = minPairCount(counts, active)
  var
    bestFound = false
    bestScore = 0
    tieCount = 0
    bestSeats: seq[int]
  for i in 0 ..< PairingSearchSamples:
    let seats =
      if i mod 5 == 0:
        randomSeats(active)
      else:
        sampledPairingSeats(counts, active, lowest)
    let score = pairScore(counts, seats, lowest)
    if not bestFound or score < bestScore:
      bestFound = true
      bestScore = score
      tieCount = 1
      bestSeats = seats
    elif score == bestScore:
      inc tieCount
      if randomIndex(tieCount) == 0:
        bestSeats = seats
  bestSeats

proc heatRatio(count, highest: int): float =
  ## Returns the local heat ratio for one matchup count.
  if highest <= 0 or count <= 0:
    return 0.0
  result = count.float / highest.float
  if result < 0.0:
    result = 0.0
  elif result > 1.0:
    result = 1.0

proc heatColor(count, highest: int): string =
  ## Returns the heat-map tile color for one count.
  if count <= 0:
    return "rgb(255,255,255)"
  let ratio = heatRatio(count, highest)
  var colors: array[3, int]
  for i in 0 ..< colors.len:
    colors[i] = int(
      HeatLowColor[i].float +
        (HeatHighColor[i] - HeatLowColor[i]).float * ratio + 0.5
    )
  "rgb(" & $colors[0] & "," & $colors[1] & "," & $colors[2] & ")"

proc heatTextColor(count, highest: int): string =
  ## Returns a readable text color for one heat-map tile.
  if heatRatio(count, highest) >= 0.55:
    "#ffffff"
  else:
    "#111111"

proc countTitle(row, column, count: int): string =
  ## Returns a tooltip for one matchup cell.
  let noun = plural(count, "game", "games")
  state.policies[row].label & " vs " &
    state.policies[column].label & ": " & $count & " " & noun

proc playButtonText(): string =
  ## Returns the play button label for the selected game count.
  let noun = plural(state.sliderGames, "game", "games")
  "Play " & $state.sliderGames & " " & noun

proc removeButtonText(): string =
  ## Returns the remove button label for the selected game count.
  let noun = plural(state.sliderGames, "game", "games")
  "Remove " & $state.sliderGames & " " & noun

proc autoButtonText(): string =
  ## Returns the auto-equalizer button label.
  if state.autoRunning:
    "Stop"
  else:
    "Auto-Equalize"

proc renderPolicyTable(counts: MatchupCounts): string =
  ## Renders the policy selection table.
  result.add "<table class=\"report-table wide policy-summary no-sort\">\n"
  result.add "<colgroup>"
  result.add "<col class=\"policy-select-col\">"
  result.add "<col class=\"policy-name-col\">"
  result.add "<col class=\"policy-games-col\">"
  result.add "</colgroup>\n"
  result.add "<thead><tr>"
  result.add "<th>Selected</th>"
  result.add "<th>Policy</th>"
  result.add "<th>Games</th>"
  result.add "</tr></thead>\n"
  result.add "<tbody>\n"
  for i, policy in state.policies:
    let
      rowClass =
        if policy.active:
          ""
        else:
          " class=\"inactive-row\""
      checked =
        if policy.active:
          " checked"
        else:
          ""
    result.add "<tr" & rowClass & ">"
    result.add "<td class=\"policy-check-cell\">"
    result.add "<input id=\"" & policyInputId(i)
    result.add "\" type=\"checkbox\" data-policy-index=\"" & $i & "\""
    result.add " aria-label=\"Select " & policy.label.escapeHtml() & "\""
    result.add checked & "></td>"
    result.add "<td class=\"bot-name name\">" & policy.label.escapeHtml()
    result.add "</td>"
    result.add "<td class=\"num\">" & $policyGameCount(counts, i) & "</td>"
    result.add "</tr>\n"
  result.add "</tbody></table>\n"

proc renderStatus(includedGames: int): string =
  ## Renders the compact simulator status row.
  let
    active = activeCount()
    activeNoun = plural(active, "policy", "policies")
    includedNoun = plural(includedGames, "game", "games")
    storedNoun = plural(state.games.len, "game", "games")
  result.add "<span>" & $active & " active " & activeNoun & "</span>"
  result.add "<span>" & $includedGames & " included " & includedNoun
  result.add "</span>"
  result.add "<span>" & $state.games.len & " stored " & storedNoun
  result.add "</span>"
  if state.message.len > 0:
    result.add "<span>" & state.message.escapeHtml() & "</span>"

proc renderAutoStatus(counts: MatchupCounts): string =
  ## Renders the auto-equalizer status row.
  let
    active = activeIndices()
    stats = computePairStats(counts, active)
    noun = plural(state.autoSteps, "game", "games")
    average = formatFloat(stats.average, ffDecimal, 1)
  result.add "<span>" & $state.autoSteps & " auto " & noun & "</span>"
  result.add "<span>" & average & " average pairing games</span>"
  result.add "<span>" & $stats.inside & "/" & $stats.pairs
  result.add " pairs in range</span>"

proc renderColumnLabel(index: int): string =
  ## Renders one rotated column label.
  let
    x = HeatLeftLabelWidth + index * HeatCellSize + HeatCellSize div 2
    y = HeatTopLabelHeight - 8
    labelClass =
      if state.policies[index].active:
        "heat-label"
      else:
        "heat-label excluded"
  result.add "<text class=\"" & labelClass
  result.add "\" x=\"" & $x & "\" y=\"" & $y
  result.add "\" text-anchor=\"start\" transform=\"rotate(-45 "
  result.add $x & " " & $y & ")\">"
  result.add state.policies[index].label.escapeHtml()
  result.add "</text>\n"

proc renderRowLabel(index: int): string =
  ## Renders one row label.
  let
    x = HeatLeftLabelWidth - 8
    y = HeatTopLabelHeight + index * HeatCellSize + HeatCellSize div 2 + 4
    labelClass =
      if state.policies[index].active:
        "heat-label"
      else:
        "heat-label excluded"
  result.add "<text class=\"" & labelClass
  result.add "\" x=\"" & $x & "\" y=\"" & $y
  result.add "\" text-anchor=\"end\">"
  result.add state.policies[index].label.escapeHtml()
  result.add "</text>\n"

proc renderHeatCell(
  counts: MatchupCounts,
  row,
  column,
  highest: int
): string =
  ## Renders one matchup count cell.
  if row == column:
    return
  let
    count = counts[row][column]
    x = HeatLeftLabelWidth + column * HeatCellSize
    y = HeatTopLabelHeight + row * HeatCellSize
    textX = x + HeatCellSize div 2
    textY = y + HeatCellSize div 2 + 4
  result.add "<rect class=\"heat-cell\" x=\"" & $x
  result.add "\" y=\"" & $y & "\" width=\"" & $HeatCellSize
  result.add "\" height=\"" & $HeatCellSize
  result.add "\" fill=\"" & heatColor(count, highest)
  result.add "\"><title>"
  result.add countTitle(row, column, count).escapeHtml()
  result.add "</title></rect>\n"
  if count > 0:
    result.add "<text class=\"heat-rate\" x=\"" & $textX
    result.add "\" y=\"" & $textY
    result.add "\" text-anchor=\"middle\" fill=\""
    result.add heatTextColor(count, highest)
    result.add "\">" & $count & "</text>\n"

proc renderChart(counts: MatchupCounts): string =
  ## Renders the full matchup count heat map.
  let
    gridWidth = PolicyCount * HeatCellSize
    width = HeatLeftLabelWidth + gridWidth + HeatRightLabelWidth
    height =
      HeatTopLabelHeight + PolicyCount * HeatCellSize + HeatPadding
    highest = counts.maxCount()
  result.add "<div class=\"heat-wrap\">\n"
  result.add "<svg class=\"heatmap-svg\" width=\"" & $width
  result.add "\" height=\"" & $height & "\" viewBox=\"0 0 "
  result.add $width & " " & $height
  result.add "\" role=\"img\" aria-label=\"Policy matchup count heat map\">\n"
  for i in 0 ..< PolicyCount:
    result.add renderColumnLabel(i)
  for i in 0 ..< PolicyCount:
    result.add renderRowLabel(i)
  for i in 0 ..< PolicyCount:
    for j in 0 ..< PolicyCount:
      result.add renderHeatCell(counts, i, j, highest)
  result.add "</svg>\n</div>\n"

proc render()

proc canPlay(): bool =
  ## Returns true when the current controls can launch simulations.
  state.sliderGames > 0 and activeCount() >= SeatsPerGame

proc canRemove(includedGames: int): bool =
  ## Returns true when included games can be removed.
  state.sliderGames > 0 and includedGames > 0

proc recordGame(seats: openArray[int]) =
  ## Stores one simulated game roster.
  var game: Game
  for i in 0 ..< SeatsPerGame:
    game.seats[i] = seats[i]
  state.games.add game

proc playOneGame(): bool =
  ## Simulates one tournament game using the current allocation type.
  let active = activeIndices()
  if active.len < SeatsPerGame:
    state.message = "Need at least 8 active policies."
    return false
  let seats =
    case state.allocationType
    of AllocateRandom:
      randomSeats(active)
    of AllocatePolicies:
      equalizedSeats(active)
    of AllocatePairings:
      pairizedSeats(active)
  recordGame(seats)
  true

proc removeStoredGame(index: int) =
  ## Removes one stored game by index while keeping the order stable.
  if index < 0 or index >= state.games.len:
    return
  state.games.delete(index)

proc includedGameIndices(): seq[int] =
  ## Returns stored game indices that pass the selection filter.
  for i, game in state.games:
    if game.gameIncluded():
      result.add i

proc policyRemovalScore(counts: MatchupCounts, game: Game): int =
  ## Returns how strongly one game contains overplayed policies.
  for index in game.seats:
    result += policyGameCount(counts, index)

proc highestPolicyGame(counts: MatchupCounts): int =
  ## Picks an included game with the most heavily played policies.
  var
    bestFound = false
    bestScore = 0
    tieCount = 0
  for index, game in state.games:
    if not game.gameIncluded():
      continue
    let score = policyRemovalScore(counts, game)
    if not bestFound or score > bestScore:
      bestFound = true
      bestScore = score
      tieCount = 1
      result = index
    elif score == bestScore:
      inc tieCount
      if randomIndex(tieCount) == 0:
        result = index
  if not bestFound:
    result = -1

proc highestPairingGame(counts: MatchupCounts): int =
  ## Picks an included game with the most heavily played pairings.
  let
    active = activeIndices()
    lowest = minPairCount(counts, active)
  var
    bestFound = false
    bestScore = 0
    tieCount = 0
  for index, game in state.games:
    if not game.gameIncluded():
      continue
    let score = pairScore(counts, game.seats, lowest)
    if not bestFound or score > bestScore:
      bestFound = true
      bestScore = score
      tieCount = 1
      result = index
    elif score == bestScore:
      inc tieCount
      if randomIndex(tieCount) == 0:
        result = index
  if not bestFound:
    result = -1

proc removalGameIndex(): int =
  ## Picks one stored game index to remove for the allocation mode.
  case state.allocationType
  of AllocateRandom:
    let candidates = includedGameIndices()
    if candidates.len == 0:
      return -1
    candidates[randomIndex(candidates.len)]
  of AllocatePolicies:
    highestPolicyGame(computeCounts())
  of AllocatePairings:
    highestPairingGame(computeCounts())

proc removeOneGame(): bool =
  ## Removes one included game using the current allocation type.
  let index = removalGameIndex()
  if index < 0:
    return false
  removeStoredGame(index)
  true

proc playGames(gameCount: int) =
  ## Simulates a requested number of tournament games.
  let count = clampInt(gameCount, MinGamesPerClick, MaxGamesPerClick)
  var played = 0
  for i in 0 ..< count:
    if not playOneGame():
      break
    inc played
  if played > 0:
    let noun = plural(played, "game", "games")
    state.message = "Played " & $played & " " & noun & " with " &
      state.allocationType.allocationText() & " allocation."
  render()

proc removeGames(gameCount: int) =
  ## Removes included games according to the allocation mode.
  let count = clampInt(gameCount, MinGamesPerClick, MaxGamesPerClick)
  var removed = 0
  while removed < count:
    if not removeOneGame():
      break
    inc removed
  if removed == 0:
    state.message = "No included games to remove."
  else:
    let noun = plural(removed, "game", "games")
    state.message = "Removed " & $removed & " " & noun & " with " &
      state.allocationType.allocationText() & " allocation."
  render()

proc canAutoEqualize(): bool =
  ## Returns true when the auto-equalizer can be started or stopped.
  state.autoRunning or activeCount() >= SeatsPerGame

proc autoProgressMessage(): string =
  ## Returns the current auto-equalizer progress message.
  "Auto-equalizing toward " & $state.targetPairGames &
    " pairing games."

proc stopAuto(message: string) =
  ## Stops the auto-equalizer timer and records a message.
  if state.autoTimerId != 0:
    clearTimer(state.autoTimerId)
  state.autoRunning = false
  state.autoTimerId = 0
  state.message = message

proc autoTick() =
  ## Runs one auto-equalizer batch using the games slider.
  if not state.autoRunning:
    return
  if activeCount() < SeatsPerGame:
    stopAuto("Need at least 8 active policies.")
    render()
    return
  let counts = computeCounts()
  if autoTargetReached(counts):
    stopAuto("Auto-equalized target reached.")
    render()
    return
  let
    active = activeIndices()
    stats = computePairStats(counts, active)
    removing = stats.average > state.targetPairGames.float
    batch = clampInt(
      state.sliderGames,
      MinGamesPerClick,
      MaxGamesPerClick
    )
  var changed = 0
  for i in 0 ..< batch:
    let didChange =
      if removing:
        removeOneGame()
      else:
        playOneGame()
    if not didChange:
      break
    inc changed
    if autoTargetReached(computeCounts()):
      break
  if changed == 0:
    stopAuto("No included games to remove.")
    render()
    return
  state.autoSteps += changed
  if autoTargetReached(computeCounts()):
    stopAuto("Auto-equalized target reached.")
  else:
    state.message = autoProgressMessage()
  render()

proc toggleAuto() =
  ## Starts or stops the auto-equalizer.
  if state.autoRunning:
    stopAuto("Auto-equalizer stopped.")
    render()
    return
  if activeCount() < SeatsPerGame:
    state.message = "Need at least 8 active policies."
    render()
    return
  state.autoSteps = 0
  if autoTargetReached(computeCounts()):
    state.message = "Already within the target range."
    render()
    return
  state.autoRunning = true
  state.message = autoProgressMessage()
  state.autoTimerId = startTimer(
    proc() =
      autoTick(),
    AutoTickMs
  )
  render()

proc setPolicySelected(index: int, selected: bool) =
  ## Sets whether one policy is in the game pool.
  if index < 0 or index >= PolicyCount:
    return
  state.policies[index].active = selected
  state.message = ""
  render()

proc updateSlider() =
  ## Reads the slider value and refreshes dependent controls.
  state.sliderGames = clampInt(
    readSliderValue(),
    MinGamesPerClick,
    MaxGamesPerClick
  )
  state.message = ""
  render()

proc updateAutoTarget() =
  ## Reads the auto-equalizer target value.
  state.targetPairGames = clampInt(
    readTargetValue(),
    AutoMinPairTarget,
    AutoMaxPairTarget
  )
  if not state.autoRunning:
    state.message = ""
  render()

proc updateAllocation() =
  ## Reads the selected allocation mode.
  let value = $readAllocationValue()
  case value
  of "policies":
    state.allocationType = AllocatePolicies
  of "pairings":
    state.allocationType = AllocatePairings
  else:
    state.allocationType = AllocateRandom
  state.message = ""
  render()

proc render() =
  ## Renders all dynamic app surfaces.
  let
    counts = computeCounts()
    includedGames = activeGameCount()
    playEnabled = canPlay()
    removeEnabled = canRemove(includedGames)
    autoEnabled = canAutoEqualize()
    playDisabledText =
      if playEnabled:
        "false"
      else:
        "true"
    removeDisabledText =
      if removeEnabled:
        "false"
      else:
        "true"
    autoDisabledText =
      if autoEnabled:
        "false"
      else:
        "true"
  setHtml("policy-panel", renderPolicyTable(counts).cstring)
  setText("game-value", ($state.sliderGames).cstring)
  setText("play-button", playButtonText().cstring)
  setText("remove-button", removeButtonText().cstring)
  setText("auto-target-value", ($state.targetPairGames).cstring)
  setText("auto-button", autoButtonText().cstring)
  setDisabled("play-button", not playEnabled)
  setDisabled("remove-button", not removeEnabled)
  setDisabled("auto-button", not autoEnabled)
  setAttribute(
    "play-button",
    "aria-disabled",
    playDisabledText.cstring
  )
  setAttribute(
    "remove-button",
    "aria-disabled",
    removeDisabledText.cstring
  )
  setAttribute(
    "auto-button",
    "aria-disabled",
    autoDisabledText.cstring
  )
  setHtml("status-line", renderStatus(includedGames).cstring)
  setHtml("auto-status-line", renderAutoStatus(counts).cstring)
  setHtml("chart", renderChart(counts).cstring)

proc initState() =
  ## Initializes policies, counts, and control state.
  for i in 0 ..< PolicyCount:
    state.policies[i] = Policy(
      label: policyLabel(i),
      active: i < ActivePolicyCount
    )
  state.games = @[]
  state.sliderGames = MinGamesPerClick
  state.targetPairGames = AutoDefaultPairTarget
  state.allocationType = AllocateRandom
  state.autoRunning = false
  state.autoTimerId = 0
  state.autoSteps = 0
  state.message = ""

proc initApp() =
  ## Boots the browser app after the document is loaded.
  initState()
  addInputListener(
    "game-slider",
    proc() =
      updateSlider()
  )
  addInputListener(
    "auto-target-slider",
    proc() =
      updateAutoTarget()
  )
  addInputListener(
    "allocation-random",
    proc() =
      updateAllocation()
  )
  addInputListener(
    "allocation-equalize",
    proc() =
      updateAllocation()
  )
  addInputListener(
    "allocation-pairings",
    proc() =
      updateAllocation()
  )
  addClickListener(
    "play-button",
    proc() =
      playGames(state.sliderGames)
  )
  addClickListener(
    "remove-button",
    proc() =
      removeGames(state.sliderGames)
  )
  addClickListener(
    "auto-button",
    proc() =
      toggleAuto()
  )
  addPolicyChangeListener(
    proc(index: int, selected: bool) =
      setPolicySelected(index, selected)
  )
  render()

initApp()
