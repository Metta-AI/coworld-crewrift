import
  std/[algorithm, math, strutils]

const
  InitialPolicyCount = 15
  CrewSize = 6
  ImposterSize = 2
  SeatsPerGame = CrewSize + ImposterSize
  MmrStart = 1000.0
  MmrAnteFraction = 0.02
  DefaultLowRankWeight = 0.9
  DefaultRandomFactor = 20
  MinRank = 0
  MaxRank = 100
  DefaultRank = 50
  MinGamesPerClick = 1
  MaxGamesPerClick = 100
  PairingSearchSamples = 192
  PairingChoiceWindow = 4
  AutoMinPairTarget = 10
  AutoMaxPairTarget = 10000
  AutoDefaultPairTarget = 100
  AutoTickMs = 100
  AutoTolerance = 0.10
  HeatCellSize = 32
  HeatLeftLabelWidth = 170
  HeatRightLabelWidth = HeatLeftLabelWidth
  HeatTopLabelHeight = 130
  HeatPadding = 12
  HeatLowColor = [255, 255, 248]
  HeatHighColor = [17, 17, 17]

type
  MatchupCounts = seq[seq[int]]

  AllocationType = enum
    AllocateRandom,
    AllocatePolicies,
    AllocatePairings,
    AllocateAndre,
    AllocateMmr

  Policy = object
    label: string
    active: bool
    rank: int
    mmr: float

  Game = object
    seats: array[SeatsPerGame, int]
    winner: int

  VersusCounts = object
    games: MatchupCounts
    wins: MatchupCounts

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
    policies: seq[Policy]
    games: seq[Game]
    sliderGames: int
    targetPairGames: int
    allocationType: AllocationType
    lowRankWeight: float
    randomFactor: int
    autoRemove: bool
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

proc addRankInputListener(callback: proc(
  index: int,
  value: int
) {.closure.}) {.
  importjs: """
document.getElementById('policy-panel').addEventListener('input', function(event) {
  var input = event.target.closest('input[data-rank-index]');
  if (input == null) return;
  #(parseInt(input.getAttribute('data-rank-index'), 10), parseInt(input.value, 10));
})
"""
.}

proc addRankCommitListener(callback: proc() {.closure.}) {.
  importjs: """
document.getElementById('policy-panel').addEventListener('change', function(event) {
  if (event.target.closest('input[data-rank-index]') == null) return;
  #();
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

proc readRemoveGamesValue(): bool {.
  importjs: "document.getElementById('auto-remove-check').checked"
.}

proc readAverageWeightValue(): float {.
  importjs: """
parseFloat(document.getElementById('average-weight-slider').value)
"""
.}

proc readRandomFactorValue(): int {.
  importjs: """
parseInt(document.getElementById('random-factor-slider').value, 10)
"""
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

proc policyRankId(index: int): string =
  ## Returns the DOM id for one policy rank slider.
  "policy-rank-" & $index

proc policyRankValueId(index: int): string =
  ## Returns the DOM id for one policy rank value label.
  "policy-rank-value-" & $index

proc policyLetters(index: int): string =
  ## Returns spreadsheet-style letters for one policy index.
  var i = index
  while true:
    result.insert($chr(ord('A') + i mod 26), 0)
    i = i div 26 - 1
    if i < 0:
      break

proc policyLabel(index: int): string =
  ## Returns the default display label for one policy.
  "Policy " & policyLetters(index)

proc policyCount(): int =
  ## Returns the number of policies in the table.
  state.policies.len

proc newCounts(): MatchupCounts =
  ## Returns a zeroed policy-by-policy count matrix.
  result = newSeq[seq[int]](policyCount())
  for i in 0 ..< result.len:
    result[i] = newSeq[int](policyCount())

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
  of AllocateAndre:
    "Andre-special"
  of AllocateMmr:
    "MMR-matched"

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
  result = newCounts()
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

proc computeVersus(): VersusCounts =
  ## Builds crew-versus-imposter game and win counts from included games.
  result.games = newCounts()
  result.wins = newCounts()
  for game in state.games:
    if not game.gameIncluded():
      continue
    for i in 0 ..< CrewSize:
      for j in CrewSize ..< SeatsPerGame:
        let
          a = game.seats[i]
          b = game.seats[j]
        inc result.games[a][b]
        inc result.games[b][a]
        if game.winner == 0:
          inc result.wins[a][b]
        else:
          inc result.wins[b][a]

proc overallWinRate(versus: VersusCounts, index: int): float =
  ## Returns the observed win rate for one policy, or -1 with no games.
  var
    wins = 0
    games = 0
  for j in 0 ..< policyCount():
    if j != index:
      wins += versus.wins[index][j]
      games += versus.games[index][j]
  if games <= 0:
    -1.0
  else:
    wins.float / games.float

proc simulatedOrder(versus: VersusCounts): seq[int] =
  ## Returns active policies sorted by observed win rate.
  result = activeIndices()
  result.sort do (a, b: int) -> int:
    let
      rateA = overallWinRate(versus, a)
      rateB = overallWinRate(versus, b)
    if rateA > rateB:
      -1
    elif rateA < rateB:
      1
    else:
      cmp(a, b)

proc diagonalPairs(): seq[array[2, int]] =
  ## Returns each ladder policy paired with its neighbor below.
  let order = simulatedOrder(computeVersus())
  for i in 0 ..< order.len - 1:
    result.add [order[i], order[i + 1]]

proc targetPairs(): seq[array[2, int]] =
  ## Returns the pairings the auto-equalizer tries to fill.
  if state.allocationType == AllocateAndre:
    return diagonalPairs()
  let active = activeIndices()
  for i in 0 ..< active.len:
    for j in i + 1 ..< active.len:
      result.add [active[i], active[j]]

proc maxCount(counts: MatchupCounts): int =
  ## Returns the largest matchup count in the matrix.
  for i in 0 ..< counts.len:
    for j in 0 ..< counts.len:
      if i != j:
        result = max(result, counts[i][j])

proc policyGameCount(counts: MatchupCounts, index: int): int =
  ## Returns included games that contain one selected policy.
  if not state.policies[index].active:
    return 0
  var pairs = 0
  for j in 0 ..< policyCount():
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
  pairs: openArray[array[2, int]]
): PairStats =
  ## Computes target-range stats for the given policy pairings.
  let
    low = autoLowTarget()
    high = autoHighTarget()
  for pair in pairs:
    let count = counts[pair[0]][pair[1]]
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
  ## Returns true when the targeted pairings have reached the target.
  ## Without game removal, pairs above the band still count as done.
  if activeCount() < SeatsPerGame:
    return false
  let stats = computePairStats(counts, targetPairs())
  if stats.pairs == 0:
    return false
  if state.autoRemove:
    stats.below == 0 and stats.above == 0
  else:
    stats.below == 0

proc randomIndex(limit: int): int =
  ## Returns a random integer from zero up to but not including limit.
  if limit <= 0:
    return 0
  result = int(randomUnit() * limit.float)
  if result >= limit:
    result = limit - 1

proc randomRank(): int =
  ## Returns a random actual rank inside the rank range.
  MinRank + randomIndex(MaxRank - MinRank + 1)

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

proc rankPositions(versus: VersusCounts): seq[int] =
  ## Returns each policy's position in the win-rate ladder.
  result = newSeq[int](policyCount())
  for i in 0 ..< result.len:
    result[i] = -1
  for position, index in simulatedOrder(versus):
    result[index] = position

proc andreSeats(): seq[int] =
  ## Seats a contiguous ladder window around the least-played
  ## ladder-neighbor pair, so every game fills diagonal pairings
  ## and never seats policies far apart on the ladder.
  let
    counts = computeCounts()
    order = simulatedOrder(computeVersus())
  var
    bestPair = 0
    bestCount = 0
    pairFound = false
    pairTies = 0
  for i in 0 ..< order.len - 1:
    let count = counts[order[i]][order[i + 1]]
    if not pairFound or count < bestCount:
      pairFound = true
      bestCount = count
      bestPair = i
      pairTies = 1
    elif count == bestCount:
      inc pairTies
      if randomIndex(pairTies) == 0:
        bestPair = i
  let
    lowStart = max(0, bestPair - (SeatsPerGame - 2))
    highStart = min(bestPair, order.len - SeatsPerGame)
  var
    bestStart = lowStart
    bestSum = 0
    sumFound = false
    sumTies = 0
  for start in lowStart .. highStart:
    var total = 0
    for i in start ..< start + SeatsPerGame - 1:
      total += counts[order[i]][order[i + 1]]
    if not sumFound or total < bestSum:
      sumFound = true
      bestSum = total
      bestStart = start
      sumTies = 1
    elif total == bestSum:
      inc sumTies
      if randomIndex(sumTies) == 0:
        bestStart = start
  for i in bestStart ..< bestStart + SeatsPerGame:
    result.add order[i]
  result.shuffle()

proc mmrOrder(active: openArray[int]): seq[int] =
  ## Returns active policies sorted by MMR, highest first.
  for index in active:
    result.add index
  result.sort do (a, b: int) -> int:
    if state.policies[a].mmr > state.policies[b].mmr:
      -1
    elif state.policies[a].mmr < state.policies[b].mmr:
      1
    else:
      cmp(a, b)

proc mmrSeats(active: openArray[int]): seq[int] =
  ## Picks a random policy and the seven closest by MMR, randomly
  ## skewing the window three or four seats above the anchor.
  let
    order = mmrOrder(active)
    anchor = randomIndex(order.len)
    above = 3 + randomIndex(2)
    start = clampInt(anchor - above, 0, order.len - SeatsPerGame)
  for i in start ..< start + SeatsPerGame:
    result.add order[i]
  result.shuffle()

proc heatRatio(count, highest: int): float =
  ## Returns the local heat ratio for one matchup count.
  if highest <= 0 or count <= 0:
    return 0.0
  result = count.float / highest.float
  if result < 0.0:
    result = 0.0
  elif result > 1.0:
    result = 1.0

proc ratioColor(ratio: float): string =
  ## Returns the heat-map tile color for one zero-to-one ratio.
  var colors: array[3, int]
  for i in 0 ..< colors.len:
    colors[i] = int(
      HeatLowColor[i].float +
        (HeatHighColor[i] - HeatLowColor[i]).float * ratio + 0.5
    )
  "rgb(" & $colors[0] & "," & $colors[1] & "," & $colors[2] & ")"

proc ratioTextColor(ratio: float): string =
  ## Returns a readable text color for one heat-map ratio.
  if ratio >= 0.55:
    "#ffffff"
  else:
    "#111111"

proc heatColor(count, highest: int): string =
  ## Returns the heat-map tile color for one count.
  if count <= 0:
    return "rgb(255,255,255)"
  ratioColor(heatRatio(count, highest))

proc heatTextColor(count, highest: int): string =
  ## Returns a readable text color for one heat-map tile.
  ratioTextColor(heatRatio(count, highest))

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
  result.add "<col class=\"policy-rank-col\">"
  result.add "<col class=\"policy-games-col\">"
  result.add "</colgroup>\n"
  result.add "<thead><tr>"
  result.add "<th>Selected</th>"
  result.add "<th>Policy</th>"
  result.add "<th>Actual Rank</th>"
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
    result.add "<td class=\"policy-rank-cell\">"
    result.add "<input id=\"" & policyRankId(i)
    result.add "\" type=\"range\" min=\"" & $MinRank
    result.add "\" max=\"" & $MaxRank & "\" step=\"1\""
    result.add " value=\"" & $policy.rank & "\""
    result.add " data-rank-index=\"" & $i & "\""
    result.add " aria-label=\"Actual rank for "
    result.add policy.label.escapeHtml() & "\">"
    result.add "<span id=\"" & policyRankValueId(i)
    result.add "\" class=\"rank-value\">" & $policy.rank & "</span></td>"
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
    stats = computePairStats(counts, targetPairs())
    noun = plural(state.autoSteps, "game", "games")
    average = formatFloat(stats.average, ffDecimal, 1)
    reached =
      if state.autoRemove:
        stats.inside
      else:
        stats.inside + stats.above
  result.add "<span>" & $state.autoSteps & " auto " & noun & "</span>"
  result.add "<span>" & average & " average pairing games</span>"
  result.add "<span>" & $reached & "/" & $stats.pairs
  result.add " pairs at target</span>"

proc renderColumnLabel(position, index: int): string =
  ## Renders one rotated column label.
  let
    x = HeatLeftLabelWidth + position * HeatCellSize + HeatCellSize div 2
    y = HeatTopLabelHeight - 8
  result.add "<text class=\"heat-label"
  result.add "\" x=\"" & $x & "\" y=\"" & $y
  result.add "\" text-anchor=\"start\" transform=\"rotate(-45 "
  result.add $x & " " & $y & ")\">"
  result.add state.policies[index].label.escapeHtml()
  result.add "</text>\n"

proc renderRowLabel(position, index: int): string =
  ## Renders one row label.
  let
    x = HeatLeftLabelWidth - 8
    y = HeatTopLabelHeight + position * HeatCellSize + HeatCellSize div 2 + 4
  result.add "<text class=\"heat-label"
  result.add "\" x=\"" & $x & "\" y=\"" & $y
  result.add "\" text-anchor=\"end\">"
  result.add state.policies[index].label.escapeHtml()
  result.add "</text>\n"

proc renderHeatCell(
  counts: MatchupCounts,
  rowPos,
  colPos,
  row,
  column,
  highest: int
): string =
  ## Renders one matchup count cell.
  if row == column:
    return
  let
    count = counts[row][column]
    x = HeatLeftLabelWidth + colPos * HeatCellSize
    y = HeatTopLabelHeight + rowPos * HeatCellSize
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

proc renderChart(counts: MatchupCounts, order: openArray[int]): string =
  ## Renders the matchup count heat map in the given policy order.
  let
    gridWidth = order.len * HeatCellSize
    width = HeatLeftLabelWidth + gridWidth + HeatRightLabelWidth
    height =
      HeatTopLabelHeight + order.len * HeatCellSize + HeatPadding
    highest = counts.maxCount()
  result.add "<div class=\"heat-wrap\">\n"
  result.add "<svg class=\"heatmap-svg\" width=\"" & $width
  result.add "\" height=\"" & $height & "\" viewBox=\"0 0 "
  result.add $width & " " & $height
  result.add "\" role=\"img\" aria-label=\"Policy matchup count heat map\">\n"
  for position, index in order:
    result.add renderColumnLabel(position, index)
  for position, index in order:
    result.add renderRowLabel(position, index)
  for i in 0 ..< order.len:
    for j in 0 ..< order.len:
      result.add renderHeatCell(
        counts,
        i,
        j,
        order[i],
        order[j],
        highest
      )
  result.add "</svg>\n</div>\n"

proc winRatio(versus: VersusCounts, row, column: int): float =
  ## Returns the win rate for one policy against another.
  let games = versus.games[row][column]
  if games <= 0:
    return 0.0
  versus.wins[row][column].float / games.float

proc winPercentText(ratio: float): string =
  ## Returns the rounded percentage label for one win rate.
  $int(ratio * 100.0 + 0.5)

proc versusTitle(versus: VersusCounts, row, column: int): string =
  ## Returns a tooltip for one versus cell.
  let
    games = versus.games[row][column]
    wins = versus.wins[row][column]
  if games == 0:
    return state.policies[row].label & " vs " &
      state.policies[column].label & ": no games"
  let
    ratio = winRatio(versus, row, column)
    noun = plural(games, "game", "games")
  state.policies[row].label & " vs " &
    state.policies[column].label & ": won " & $wins & " of " &
    $games & " " & noun & " (" & winPercentText(ratio) & "%)"

proc renderVersusCell(
  versus: VersusCounts,
  rowPos,
  colPos,
  row,
  column: int
): string =
  ## Renders one versus win percentage cell.
  if row == column:
    return
  let
    games = versus.games[row][column]
    ratio = winRatio(versus, row, column)
    x = HeatLeftLabelWidth + colPos * HeatCellSize
    y = HeatTopLabelHeight + rowPos * HeatCellSize
    textX = x + HeatCellSize div 2
    textY = y + HeatCellSize div 2 + 4
    fill =
      if games == 0:
        "rgb(255,255,255)"
      else:
        ratioColor(ratio)
  result.add "<rect class=\"heat-cell\" x=\"" & $x
  result.add "\" y=\"" & $y & "\" width=\"" & $HeatCellSize
  result.add "\" height=\"" & $HeatCellSize
  result.add "\" fill=\"" & fill
  result.add "\"><title>"
  result.add versusTitle(versus, row, column).escapeHtml()
  result.add "</title></rect>\n"
  if games > 0:
    result.add "<text class=\"heat-rate\" x=\"" & $textX
    result.add "\" y=\"" & $textY
    result.add "\" text-anchor=\"middle\" fill=\""
    result.add ratioTextColor(ratio)
    result.add "\">" & winPercentText(ratio) & "</text>\n"

proc renderVersusChart(
  versus: VersusCounts,
  order: openArray[int]
): string =
  ## Renders the versus win percentage heat map in the given order.
  let
    gridWidth = order.len * HeatCellSize
    width = HeatLeftLabelWidth + gridWidth + HeatRightLabelWidth
    height =
      HeatTopLabelHeight + order.len * HeatCellSize + HeatPadding
  result.add "<div class=\"heat-wrap\">\n"
  result.add "<svg class=\"heatmap-svg\" width=\"" & $width
  result.add "\" height=\"" & $height & "\" viewBox=\"0 0 "
  result.add $width & " " & $height
  result.add "\" role=\"img\""
  result.add " aria-label=\"Policy versus win percentage heat map\">\n"
  for position, index in order:
    result.add renderColumnLabel(position, index)
  for position, index in order:
    result.add renderRowLabel(position, index)
  for i in 0 ..< order.len:
    for j in 0 ..< order.len:
      result.add renderVersusCell(
        versus,
        i,
        j,
        order[i],
        order[j]
      )
  result.add "</svg>\n</div>\n"

proc truthOrder(): seq[int] =
  ## Returns active policies sorted by actual rank.
  result = activeIndices()
  result.sort do (a, b: int) -> int:
    let
      rankA = state.policies[a].rank
      rankB = state.policies[b].rank
    if rankA > rankB:
      -1
    elif rankA < rankB:
      1
    else:
      cmp(a, b)

proc renderOrderPanel(
  versus: VersusCounts,
  counts: MatchupCounts
): string =
  ## Renders the simulated ordering against the ground truth ordering.
  let
    ours = simulatedOrder(versus)
    truth = truthOrder()
  if ours.len == 0:
    return "<p class=\"sim-status\">No active policies.</p>"
  var truthPosition = newSeq[int](policyCount())
  for position, index in truth:
    truthPosition[index] = position
  var
    matches = 0
    totalOffset = 0
    topTruth: seq[int]
  for position in 0 ..< min(3, truth.len):
    topTruth.add truth[position]
  result.add "<table class=\"report-table wide no-sort\">\n"
  result.add "<thead><tr>"
  result.add "<th>#</th>"
  result.add "<th>Policy</th>"
  result.add "<th>Win %</th>"
  result.add "<th>Games</th>"
  result.add "<th>Ground Truth Rank</th>"
  result.add "</tr></thead>\n<tbody>\n"
  for position, index in ours:
    let
      rate = overallWinRate(versus, index)
      winText =
        if rate < 0.0:
          "-"
        else:
          winPercentText(rate) & "%"
    if index == truth[position]:
      inc matches
    totalOffset += abs(truthPosition[index] - position)
    let
      rankText = $state.policies[index].rank
      rankCell =
        if index in topTruth:
          "<mark>" & rankText & "</mark>"
        else:
          rankText
    result.add "<tr>"
    result.add "<td class=\"num\">" & $(position + 1) & "</td>"
    result.add "<td class=\"bot-name name\">"
    result.add state.policies[index].label.escapeHtml() & "</td>"
    result.add "<td class=\"num\">" & winText & "</td>"
    result.add "<td class=\"num\">" & $policyGameCount(counts, index)
    result.add "</td>"
    result.add "<td class=\"num\">" & rankCell & "</td>"
    result.add "</tr>\n"
  result.add "</tbody></table>\n"
  let average = totalOffset.float / ours.len.float
  result.add "<p class=\"sim-status\">"
  result.add $matches & " of " & $ours.len & " positions match. "
  result.add "Average offset " & formatFloat(average, ffDecimal, 1) & "."
  result.add "</p>\n"

proc render()

proc canPlay(): bool =
  ## Returns true when the current controls can launch simulations.
  state.sliderGames > 0 and activeCount() >= SeatsPerGame

proc canRemove(includedGames: int): bool =
  ## Returns true when included games can be removed.
  state.sliderGames > 0 and includedGames > 0

proc teamScore(seats: openArray[int], start, size: int): float =
  ## Returns one team's strength: mostly its weakest link, plus a
  ## small contribution from the team average.
  var
    lowest = state.policies[seats[start]].rank
    total = 0
  for i in start ..< start + size:
    let rank = state.policies[seats[i]].rank
    lowest = min(lowest, rank)
    total += rank
  state.lowRankWeight * lowest.float +
    (1.0 - state.lowRankWeight) * (total.float / size.float)

proc gaussianUnit(): float =
  ## Returns a standard normal sample via the Box-Muller transform.
  var u = randomUnit()
  if u <= 0.0:
    u = 1e-12
  sqrt(-2.0 * ln(u)) * cos(2.0 * PI * randomUnit())

proc randomScoreOffset(): float =
  ## Returns bell-curve score noise; the random factor is roughly the
  ## 95% bound of the draws (two standard deviations).
  state.randomFactor.float * 0.5 * gaussianUnit()

proc decideWinner(seats: openArray[int]): int =
  ## Returns the winning side between the crew team and the imposter
  ## team: the higher team score wins, after adding random noise.
  let
    crew = teamScore(seats, 0, CrewSize) + randomScoreOffset()
    imposter =
      teamScore(seats, CrewSize, ImposterSize) + randomScoreOffset()
  if crew > imposter:
    0
  elif imposter > crew:
    1
  else:
    randomIndex(2)

proc applyMmr(game: Game) =
  ## Every seat antes a small portion of its MMR into the game pot;
  ## the winning side splits and keeps the pot.
  var pot = 0.0
  for index in game.seats:
    let ante = state.policies[index].mmr * MmrAnteFraction
    state.policies[index].mmr -= ante
    pot += ante
  let
    start =
      if game.winner == 0:
        0
      else:
        CrewSize
    size =
      if game.winner == 0:
        CrewSize
      else:
        ImposterSize
    share = pot / size.float
  for i in start ..< start + size:
    state.policies[game.seats[i]].mmr += share

proc recordGame(seats: openArray[int]) =
  ## Stores one simulated game roster and its winning team.
  var game: Game
  for i in 0 ..< SeatsPerGame:
    game.seats[i] = seats[i]
  game.winner = decideWinner(seats)
  applyMmr(game)
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
    of AllocateAndre:
      andreSeats()
    of AllocateMmr:
      mmrSeats(active)
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

proc lowestDiagonalGame(positions: seq[int]): int =
  ## Picks an included game covering the fewest ladder-neighbor pairs.
  var
    bestFound = false
    bestScore = 0
    tieCount = 0
  for index, game in state.games:
    if not game.gameIncluded():
      continue
    var score = 0
    for i in 0 ..< SeatsPerGame:
      for j in i + 1 ..< SeatsPerGame:
        if abs(positions[game.seats[i]] - positions[game.seats[j]]) == 1:
          inc score
    if not bestFound or score < bestScore:
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

proc widestMmrGame(): int =
  ## Picks an included game with the widest current MMR spread.
  var
    bestFound = false
    bestScore = 0.0
    tieCount = 0
  for index, game in state.games:
    if not game.gameIncluded():
      continue
    var
      lowest = state.policies[game.seats[0]].mmr
      highest = lowest
    for seat in game.seats:
      lowest = min(lowest, state.policies[seat].mmr)
      highest = max(highest, state.policies[seat].mmr)
    let score = highest - lowest
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
  of AllocateAndre:
    lowestDiagonalGame(rankPositions(computeVersus()))
  of AllocateMmr:
    widestMmrGame()

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
    stats = computePairStats(counts, targetPairs())
    removing =
      state.autoRemove and
      stats.average > state.targetPairGames.float
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

proc addPolicy() =
  ## Appends one new active policy with a random rank.
  let label = policyLabel(policyCount())
  state.policies.add Policy(
    label: label,
    active: true,
    rank: randomRank(),
    mmr: MmrStart
  )
  state.message = "Added " & label & "."
  render()

proc setPolicyRank(index, value: int) =
  ## Sets one policy's actual rank while a slider moves.
  if index < 0 or index >= policyCount():
    return
  state.policies[index].rank = clampInt(value, MinRank, MaxRank)
  # No full render here: replacing the table HTML would break the drag.
  setText(
    policyRankValueId(index).cstring,
    ($state.policies[index].rank).cstring
  )

proc resetRanks() =
  ## Resets every policy's actual rank to the middle value.
  for policy in state.policies.mitems:
    policy.rank = DefaultRank
  state.message = "Reset all ranks to " & $DefaultRank & "."
  render()

proc randomizeRanks() =
  ## Randomizes every policy's actual rank.
  for policy in state.policies.mitems:
    policy.rank = randomRank()
  state.message = "Randomized all policy ranks."
  render()

proc setPolicySelected(index: int, selected: bool) =
  ## Sets whether one policy is in the game pool.
  if index < 0 or index >= policyCount():
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

proc updateAverageWeight() =
  ## Reads how much the team average counts in the team score.
  var value = readAverageWeightValue()
  if value < 0.0:
    value = 0.0
  elif value > 1.0:
    value = 1.0
  state.lowRankWeight = 1.0 - value
  setText(
    "average-weight-value",
    ("(" & formatFloat(value, ffDecimal, 2) & ")").cstring
  )

proc updateRandomFactor() =
  ## Reads the random score offset range for deciding games.
  state.randomFactor = clampInt(readRandomFactorValue(), 0, MaxRank)
  setText(
    "random-factor-value",
    ("(±" & $state.randomFactor & " rank)").cstring
  )

proc updateRemoveGames() =
  ## Reads whether the auto-equalizer may remove games.
  state.autoRemove = readRemoveGamesValue()
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
  of "andre":
    state.allocationType = AllocateAndre
  of "mmr":
    state.allocationType = AllocateMmr
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
  let
    versus = computeVersus()
    order = simulatedOrder(versus)
  setHtml("chart", renderChart(counts, order).cstring)
  setHtml("versus-chart", renderVersusChart(versus, order).cstring)
  setHtml("order-panel", renderOrderPanel(versus, counts).cstring)

proc initState() =
  ## Initializes policies, counts, and control state.
  state.policies = @[]
  for i in 0 ..< InitialPolicyCount:
    state.policies.add Policy(
      label: policyLabel(i),
      active: true,
      rank: randomRank(),
      mmr: MmrStart
    )
  state.games = @[]
  state.sliderGames = MinGamesPerClick
  state.targetPairGames = AutoDefaultPairTarget
  state.allocationType = AllocateRandom
  state.lowRankWeight = DefaultLowRankWeight
  state.randomFactor = DefaultRandomFactor
  state.autoRemove = false
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
    "auto-remove-check",
    proc() =
      updateRemoveGames()
  )
  addInputListener(
    "average-weight-slider",
    proc() =
      updateAverageWeight()
  )
  addInputListener(
    "random-factor-slider",
    proc() =
      updateRandomFactor()
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
  addInputListener(
    "allocation-andre",
    proc() =
      updateAllocation()
  )
  addInputListener(
    "allocation-mmr",
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
  addClickListener(
    "add-policy-button",
    proc() =
      addPolicy()
  )
  addClickListener(
    "rank-reset-button",
    proc() =
      resetRanks()
  )
  addClickListener(
    "rank-randomize-button",
    proc() =
      randomizeRanks()
  )
  addPolicyChangeListener(
    proc(index: int, selected: bool) =
      setPolicySelected(index, selected)
  )
  addRankInputListener(
    proc(index: int, value: int) =
      setPolicyRank(index, value)
  )
  addRankCommitListener(
    proc() =
      render()
  )
  render()

initApp()
