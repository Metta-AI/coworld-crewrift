import
  std/[algorithm, strutils]

const
  ReportScoreMin* = -10.0
  ReportScoreMax* = 150.0
  ReportScoreTickStep* = 20.0
  ScorePlotWidth = 720
  ScorePlotLeftLabelWidth = 270
  ScorePlotRightPadding = 28
  ScorePlotTickEpsilon = 0.000001
  ScorePlotTopHeight = 58
  ScorePlotRowHeight = 56
  ScorePlotHistogramStep = 4.0
  ScorePlotHistogramHeight = ScorePlotRowHeight div 2 - 5
  ScorePlotSmoothPasses = 2
  ScorePlotShowTieHistogram = false
  ScorePlotDotRadius = 1.5625
  ScorePlotDotOverlapPercent = 80
  ScorePlotDotStackSlots = 18
  ScorePlotDotSpacing = int(
    (
      ScorePlotDotRadius * (200 - ScorePlotDotOverlapPercent).float + 50.0
    ) / 100.0
  )

type
  ScoreChartScale* = object
    minScore*: float
    maxScore*: float
    tickStep*: float
    histogramStep*: float

  ScoreChartOutcome* = enum
    ScoreLoss,
    ScoreTie,
    ScoreWin

  ScoreChartRow* = object
    id*: string
    label*: string

  ScoreChartPoint* = object
    rowId*: string
    score*: float
    outcome*: ScoreChartOutcome
    title*: string
    sortKey*: string

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

proc scoreText(score: float): string =
  ## Formats one chart score without unnecessary trailing zeros.
  result = score.formatFloat(ffDecimal, 4)
  while result.len > 0 and result[^1] == '0':
    result.setLen(result.len - 1)
  if result.len > 0 and result[^1] == '.':
    result.setLen(result.len - 1)
  if result.len == 0 or result == "-0":
    result = "0"

proc scoreChartCss*(): string =
  ## Returns CSS shared by score charts.
  result.add "    .scoreplot-wrap { overflow-x: hidden; max-width: 100%; }\n"
  result.add "    .scoreplot-svg { display: block; height: auto; "
  result.add "margin: 0 auto; max-width: 100%; "
  result.add "shape-rendering: crispEdges; }\n"
  result.add "    .scoreplot-svg text { font-family: inherit; "
  result.add "font-size: 0.9rem; shape-rendering: auto; }\n"
  result.add "    .scoreplot-svg .score-label { fill: #111; "
  result.add "font-weight: 700; }\n"
  result.add "    .score-label-html { box-sizing: border-box; width: 100%; "
  result.add "overflow: hidden; text-overflow: ellipsis; white-space: nowrap; "
  result.add "text-align: right; color: #111; font-family: inherit; "
  result.add "font-size: 0.9rem; font-weight: 700; "
  result.add "line-height: 24px; }\n"
  result.add "    .scoreplot-svg .score-axis { stroke: #111; "
  result.add "stroke-width: 1; fill: none; }\n"
  result.add "    .scoreplot-svg .score-line { stroke: rgba(0,0,0,0.18); "
  result.add "stroke-width: 1; }\n"
  result.add "    .scoreplot-svg .score-win-area { fill: #000; "
  result.add "stroke: none; shape-rendering: auto; }\n"
  result.add "    .scoreplot-svg .score-loss-hist { fill: none; "
  result.add "stroke: #000; stroke-width: 1; shape-rendering: auto; }\n"
  result.add "    .scoreplot-svg .score-tie-hist { fill: none; "
  result.add "stroke: rgba(0,0,0,0.45); stroke-dasharray: 2 2; "
  result.add "stroke-width: 1; shape-rendering: auto; }\n"
  result.add "    .scoreplot-svg .score-dot { stroke: #000; "
  result.add "stroke-width: 1; shape-rendering: auto; }\n"

proc scoreX(score, minScore, maxScore: float): int =
  ## Returns the plot-local x position for one score.
  let scaled = int(((score - minScore) / (maxScore - minScore)) *
    ScorePlotWidth.float + 0.5)
  if scaled < 0:
    0
  elif scaled > ScorePlotWidth:
    ScorePlotWidth
  else:
    scaled

proc defaultScoreChartScale(): ScoreChartScale =
  ## Returns the default tournament score chart scale.
  ScoreChartScale(
    minScore: ReportScoreMin,
    maxScore: ReportScoreMax,
    tickStep: ReportScoreTickStep,
    histogramStep: ScorePlotHistogramStep
  )

proc histogramCount(scale: ScoreChartScale): int =
  ## Returns the number of histogram buckets for one chart scale.
  int((scale.maxScore - scale.minScore) / scale.histogramStep + 1.5)

proc scoreBin(score: float, scale: ScoreChartScale): int =
  ## Returns the full-range histogram bin for one score.
  var clamped = score
  if clamped < scale.minScore:
    clamped = scale.minScore
  if clamped > scale.maxScore:
    clamped = scale.maxScore
  result = int(
    (clamped - scale.minScore) /
      scale.histogramStep + 0.5
  )
  if result < 0:
    result = 0
  let count = scale.histogramCount()
  if result >= count:
    result = count - 1

proc histogramX(plotX, index, count: int): int =
  ## Returns the x coordinate for one histogram sample.
  let scaled = index.float / max(1, count - 1).float
  plotX + int(scaled * ScorePlotWidth.float + 0.5)

proc histogramFor(
  points: openArray[ScoreChartPoint],
  outcome: ScoreChartOutcome,
  scale: ScoreChartScale
): seq[float] =
  ## Returns full-range histogram counts for one outcome.
  result = newSeq[float](scale.histogramCount())
  for point in points:
    if point.outcome == outcome:
      result[point.score.scoreBin(scale)] += 1.0

proc outcomeCount(
  points: openArray[ScoreChartPoint],
  outcome: ScoreChartOutcome
): int =
  ## Counts score points for one outcome.
  for point in points:
    if point.outcome == outcome:
      inc result

proc smoothHistogram(values: openArray[float]): seq[float] =
  ## Returns a lightly smoothed histogram.
  result = @values
  for _ in 0 ..< ScorePlotSmoothPasses:
    var next = newSeq[float](result.len)
    for i in 0 ..< result.len:
      let
        left =
          if i == 0:
            result[i]
          else:
            result[i - 1]
        right =
          if i + 1 >= result.len:
            result[i]
          else:
            result[i + 1]
      next[i] = (left + result[i] * 2.0 + right) / 4.0
    result = next

proc maxHistogramValue(
  wins,
  losses,
  ties: openArray[float]
): float =
  ## Returns the tallest smoothed histogram value.
  for value in wins:
    if value > result:
      result = value
  for value in losses:
    if value > result:
      result = value
  for value in ties:
    if value > result:
      result = value

proc histogramHeight(value, maxValue: float): int =
  ## Returns one normalized histogram height in pixels.
  if maxValue <= 0.0:
    return 0
  int(value / maxValue * ScorePlotHistogramHeight.float + 0.5)

proc histogramLinePath(
  plotX,
  plotEnd,
  baseline: int,
  values: openArray[float],
  maxValue: float,
  direction: int
): string =
  ## Returns a line path that touches both baseline endpoints.
  result = "M " & $plotX & " " & $baseline
  for i, value in values:
    let
      x = histogramX(plotX, i, values.len)
      y = baseline + direction * histogramHeight(value, maxValue)
    result.add " L " & $x & " " & $y
  result.add " L " & $plotEnd & " " & $baseline

proc histogramAreaPath(
  plotX,
  plotEnd,
  baseline: int,
  values: openArray[float],
  maxValue: float
): string =
  ## Returns a filled area path for a top-side histogram.
  result = "M " & $plotX & " " & $baseline
  for i, value in values:
    let
      x = histogramX(plotX, i, values.len)
      y = baseline - histogramHeight(value, maxValue)
    result.add " L " & $x & " " & $y
  result.add " L " & $plotEnd & " " & $baseline
  result.add " L " & $plotX & " " & $baseline & " Z"

proc scoreOffset(
  x: int,
  placed: openArray[tuple[x, offset: int]]
): int =
  ## Returns a vertical offset that avoids nearby score-dot collisions.
  let threshold = ScorePlotDotSpacing
  for step in 0 ..< ScorePlotDotStackSlots:
    let offset =
      if step == 0:
        0
      elif step mod 2 == 1:
        -((step + 1) div 2) * ScorePlotDotSpacing
      else:
        (step div 2) * ScorePlotDotSpacing
    var collides = false
    for point in placed:
      if abs(x - point.x) < threshold and
        abs(offset - point.offset) < threshold:
          collides = true
          break
    if not collides:
      return offset
  0

proc pointFill(point: ScoreChartPoint): string =
  ## Returns the SVG fill for one score point.
  if point.outcome == ScoreWin:
    "#000"
  else:
    "none"

proc scoreChartOutcomeText*(outcome: ScoreChartOutcome): string =
  ## Returns a short display label for one chart outcome.
  case outcome
  of ScoreWin:
    "win"
  of ScoreTie:
    "tie"
  of ScoreLoss:
    "loss"

proc pointOpacity(point: ScoreChartPoint): string =
  ## Returns the SVG opacity for one score point.
  if point.outcome == ScoreLoss:
    "0.5"
  else:
    "1"

proc pointStrokeWidth(point: ScoreChartPoint): string =
  ## Returns the SVG stroke width for one score point.
  if point.outcome == ScoreLoss:
    "0.5"
  else:
    "1"

proc pointSortKey(point: ScoreChartPoint): string =
  ## Returns a stable sort key for one score point.
  if point.sortKey.len > 0:
    return point.sortKey
  point.title

proc addScoreChartStart(
  html: var string,
  rowsLen: int,
  scale: ScoreChartScale
): tuple[plotX, plotEnd: int] =
  ## Adds the shared score chart header and axis.
  let
    plotX = ScorePlotLeftLabelWidth
    plotEnd = plotX + ScorePlotWidth
    axisY = 34
    width = plotEnd + ScorePlotRightPadding
    height = ScorePlotTopHeight + rowsLen * ScorePlotRowHeight + 16
  html.add "<div class=\"scoreplot-wrap\">\n"
  html.add "<svg class=\"scoreplot-svg\" width=\"" & $width
  html.add "\" height=\"" & $height & "\" viewBox=\"0 0 "
  html.add $width & " " & $height
  html.add "\" role=\"img\" aria-label=\"Score plot\">\n"
  var tick = scale.minScore
  while tick <= scale.maxScore + ScorePlotTickEpsilon:
    let x = plotX + scoreX(
      tick,
      scale.minScore,
      scale.maxScore
    )
    html.add "<text class=\"score-label\" x=\"" & $x
    html.add "\" y=\"22\" text-anchor=\"middle\">"
    html.add tick.scoreText().htmlEscape()
    html.add "</text>\n"
    tick += scale.tickStep
  html.add "<path class=\"score-axis\" d=\"M " & $plotX & " " & $axisY
  html.add " H " & $plotEnd
  tick = scale.minScore
  while tick <= scale.maxScore + ScorePlotTickEpsilon:
    let x = plotX + scoreX(
      tick,
      scale.minScore,
      scale.maxScore
    )
    html.add " M " & $x & " " & $axisY & " v 12"
    tick += scale.tickStep
  html.add "\" />\n"
  (plotX, plotEnd)

proc addScoreRowStart(
  html: var string,
  row: ScoreChartRow,
  index,
  plotX,
  plotEnd: int
): int =
  ## Adds the shared row label and baseline.
  let
    y = ScorePlotTopHeight + index * ScorePlotRowHeight +
      ScorePlotRowHeight div 2
    labelWidth = plotX - 16
    labelY = y - 12
  html.add "<foreignObject x=\"0\" y=\"" & $labelY
  html.add "\" width=\"" & $labelWidth
  html.add "\" height=\"24\">"
  html.add "<div xmlns=\"http://www.w3.org/1999/xhtml\" "
  html.add "class=\"score-label-html\" title=\""
  html.add row.label.htmlEscape()
  html.add "\">"
  html.add row.label.htmlEscape()
  html.add "</div></foreignObject>\n"
  html.add "<line class=\"score-line\" x1=\"" & $plotX
  html.add "\" y1=\"" & $y & "\" x2=\"" & $plotEnd
  html.add "\" y2=\"" & $y & "\" />\n"
  y

proc rowScorePoints(
  points: openArray[ScoreChartPoint],
  rowId: string
): seq[ScoreChartPoint] =
  ## Returns score points for one chart row.
  for point in points:
    if point.rowId == rowId:
      result.add point

proc renderScoreHistogramChart*(
  rows: openArray[ScoreChartRow],
  points: openArray[ScoreChartPoint],
  emptyMessage: string,
  scale: ScoreChartScale
): string =
  ## Renders a compact score histogram chart.
  if points.len == 0 or rows.len == 0:
    return "<p>" & emptyMessage.htmlEscape() & "</p>\n"
  let
    frame = result.addScoreChartStart(rows.len, scale)
    plotX = frame.plotX
    plotEnd = frame.plotEnd
  for i, row in rows:
    let
      y = result.addScoreRowStart(row, i, plotX, plotEnd)
      rowPoints = points.rowScorePoints(row.id)
    if rowPoints.len == 0:
      continue
    let
      wins = rowPoints.histogramFor(ScoreWin, scale).smoothHistogram()
      losses = rowPoints.histogramFor(ScoreLoss, scale).smoothHistogram()
      winCount = rowPoints.outcomeCount(ScoreWin)
      lossCount = rowPoints.outcomeCount(ScoreLoss)
    var ties = newSeq[float](scale.histogramCount())
    when ScorePlotShowTieHistogram:
      ties = rowPoints.histogramFor(ScoreTie, scale).smoothHistogram()
    let maxValue = maxHistogramValue(wins, losses, ties)
    if maxValue > 0.0 and winCount > 0:
      result.add "<path class=\"score-win-area\" d=\""
      result.add histogramAreaPath(plotX, plotEnd, y, wins, maxValue)
      result.add "\"><title>"
      result.add row.label.htmlEscape() & ": " & $winCount & " wins"
      result.add "</title></path>\n"
    when ScorePlotShowTieHistogram:
      let tieCount = rowPoints.outcomeCount(ScoreTie)
      if maxValue > 0.0 and tieCount > 0:
        result.add "<path class=\"score-tie-hist\" d=\""
        result.add histogramLinePath(
          plotX,
          plotEnd,
          y,
          ties,
          maxValue,
          1
        )
        result.add "\"><title>"
        result.add row.label.htmlEscape() & ": " & $tieCount & " ties"
        result.add "</title></path>\n"
    if maxValue > 0.0 and lossCount > 0:
      result.add "<path class=\"score-loss-hist\" d=\""
      result.add histogramLinePath(
        plotX,
        plotEnd,
        y,
        losses,
        maxValue,
        1
      )
      result.add "\"><title>"
      result.add row.label.htmlEscape() & ": " & $lossCount & " losses"
      result.add "</title></path>\n"
  result.add "</svg>\n</div>\n"

proc renderScoreHistogramChart*(
  rows: openArray[ScoreChartRow],
  points: openArray[ScoreChartPoint],
  emptyMessage = "No completed scores yet."
): string =
  ## Renders a compact score histogram chart.
  renderScoreHistogramChart(
    rows,
    points,
    emptyMessage,
    defaultScoreChartScale()
  )

proc renderScoreDotChart*(
  rows: openArray[ScoreChartRow],
  points: openArray[ScoreChartPoint],
  emptyMessage: string,
  scale: ScoreChartScale
): string =
  ## Renders the previous compact score dot chart.
  if points.len == 0 or rows.len == 0:
    return "<p>" & emptyMessage.htmlEscape() & "</p>\n"
  let
    frame = result.addScoreChartStart(rows.len, scale)
    plotX = frame.plotX
    plotEnd = frame.plotEnd
  for i, row in rows:
    let y = result.addScoreRowStart(row, i, plotX, plotEnd)
    var
      placed: seq[tuple[x, offset: int]]
      rowPoints = points.rowScorePoints(row.id)
    rowPoints.sort do (a, b: ScoreChartPoint) -> int:
      let byScore = cmp(a.score, b.score)
      if byScore != 0:
        byScore
      else:
        cmp(a.pointSortKey(), b.pointSortKey())
    for point in rowPoints:
      let
        x = plotX + scoreX(
          point.score,
          scale.minScore,
          scale.maxScore
        )
        offset = scoreOffset(x, placed)
        pointY = y + offset
      placed.add (x, offset)
      result.add "<circle class=\"score-dot\" cx=\"" & $x
      result.add "\" cy=\"" & $pointY & "\" r=\"" & $ScorePlotDotRadius
      result.add "\" fill=\"" & point.pointFill()
      result.add "\" opacity=\"" & point.pointOpacity()
      result.add "\" stroke-width=\"" & point.pointStrokeWidth()
      result.add "\"><title>"
      result.add point.title.htmlEscape()
      result.add "</title></circle>\n"
  result.add "</svg>\n</div>\n"

proc renderScoreDotChart*(
  rows: openArray[ScoreChartRow],
  points: openArray[ScoreChartPoint],
  emptyMessage = "No completed scores yet."
): string =
  ## Renders the previous compact score dot chart.
  renderScoreDotChart(
    rows,
    points,
    emptyMessage,
    defaultScoreChartScale()
  )

proc renderScoreChart*(
  rows: openArray[ScoreChartRow],
  points: openArray[ScoreChartPoint],
  emptyMessage: string,
  scale: ScoreChartScale
): string =
  ## Renders the default shared score chart.
  renderScoreHistogramChart(rows, points, emptyMessage, scale)

proc renderScoreChart*(
  rows: openArray[ScoreChartRow],
  points: openArray[ScoreChartPoint],
  emptyMessage = "No completed scores yet."
): string =
  ## Renders the default shared score chart.
  renderScoreHistogramChart(
    rows,
    points,
    emptyMessage,
    defaultScoreChartScale()
  )
