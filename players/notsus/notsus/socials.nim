import
  std/[json, strutils],
  ../../../src/crewrift/sim

const
  SocialPlayerCount* = PlayerColors.len
  SocialMaxSlots* = MaxPlayers
  SocialUnknown* = -1
  SocialSkip* = -2
  SocialLowClaim* = 25
  SocialMediumClaim* = 55
  SocialHighClaim* = 90
  SocialSelfTrust* = 1000
  SocialMaxFriendTrust* = 850
  SocialTrustIterations* = 4
  SocialCrewBrigadeVotes* = 2
  SocialImposterBrigadeVotes* = 2
  SocialImposterDangerVotes* = 2
  SocialSusWords = [
    "sus", "suspicious", "accuse", "accused", "vote", "voted",
    "eject", "ejected", "bad", "lying", "lie", "liar", "fake",
    "faking", "follow", "follows", "following", "followed",
    "chase", "chasing", "threat", "danger"
  ]
  SocialHighSusWords = [
    "kill", "kills", "killed", "killer", "murder", "murdered",
    "vent", "vents", "vented"
  ]
  SocialClearWords = [
    "clear", "cleared", "safe", "trust", "trusts", "trusted",
    "task", "tasks", "tasking"
  ]
  SocialNegationWords = [
    "not", "no", "dont", "never", "isnt", "wasnt", "cant"
  ]
  SocialSoftWords = [
    "maybe", "might", "little", "soft", "weak", "slight"
  ]
  SocialRiskWords = [
    "body", "bodies", "kill", "kills", "killed", "murder", "murdered"
  ]
  SocialStrongWords = [
    "main", "most", "clearest", "strong", "hard"
  ]

type
  SocialStance* = enum
    SocialSus
    SocialClear

  SocialClaim* = object
    speaker*: int
    target*: int
    stance*: SocialStance
    strength*: int
    reason*: string

  SocialLlmResult* = object
    message*: string
    claims*: seq[SocialClaim]

  SocialMatrix* = array[SocialPlayerCount, array[SocialPlayerCount, int]]

  SocialVoteState* = object
    playerCount*: int
    selfSlot*: int
    selfColor*: int
    slotColors*: array[SocialMaxSlots, int]
    slotAlive*: array[SocialMaxSlots, bool]
    choices*: array[SocialPlayerCount, int]
    knownImposters*: array[SocialPlayerCount, bool]

  SocialVoteDecision* = object
    found*: bool
    target*: int
    reason*: string
    instant*: bool

proc normalizeSocialText*(text: string): string =
  ## Normalizes text for social claim parsing.
  var hadSpace = true
  for ch in text:
    var outCh = ch
    if outCh in {'A' .. 'Z'}:
      outCh = char(ord(outCh) - ord('A') + ord('a'))
    if outCh in {'a' .. 'z'} or outCh in {'0' .. '9'}:
      result.add outCh
      hadSpace = false
    elif not hadSpace:
      result.add ' '
      hadSpace = true
  result = result.strip()

proc socialJsonObjectText*(text: string): string =
  ## Extracts the last complete JSON object from one model response.
  var
    start = -1
    depth = 0
    inString = false
    escaped = false
    last = ""
  for i, ch in text:
    if inString:
      if escaped:
        escaped = false
      elif ch == '\\':
        escaped = true
      elif ch == '"':
        inString = false
      continue
    case ch
    of '"':
      inString = true
    of '{':
      if depth == 0:
        start = i
      inc depth
    of '}':
      if depth > 0:
        dec depth
        if depth == 0 and start >= 0:
          last = text[start .. i]
          start = -1
    else:
      discard
  if last.len > 0:
    return last
  text

proc socialNodeString*(node: JsonNode, key: string): string =
  ## Returns a string field from a JSON object.
  if node.kind != JObject or not node.hasKey(key):
    return ""
  let child = node[key]
  if child.kind == JString:
    return child.getStr()
  ""

proc socialNodeArray*(node: JsonNode, key: string): JsonNode =
  ## Returns an array field or an empty array.
  if node.kind == JObject and node.hasKey(key) and node[key].kind == JArray:
    return node[key]
  newJArray()

proc socialColorIndex*(name: string): int =
  ## Parses a player color name.
  let normalized = name.normalizeSocialText()
  for i, colorName in PlayerColorNames:
    if colorName.normalizeSocialText() == normalized:
      return i
  SocialUnknown

proc socialColorName*(colorIndex: int): string =
  ## Returns a player color name.
  if colorIndex >= 0 and colorIndex < PlayerColorNames.len:
    return PlayerColorNames[colorIndex]
  "unknown"

proc socialStance*(text: string): tuple[ok: bool, stance: SocialStance] =
  ## Parses one social stance string.
  case text.normalizeSocialText()
  of "sus", "suspicious", "accuse", "accuses", "enemy", "enemies":
    (true, SocialSus)
  of "clear", "cleared", "safe", "trust", "trusts", "friend", "friends":
    (true, SocialClear)
  else:
    (false, SocialSus)

proc socialStrength*(text: string): int =
  ## Parses one social claim strength.
  case text.normalizeSocialText()
  of "high", "strong", "very", "hard", "extreme", "extremely":
    SocialHighClaim
  of "medium", "med", "some", "normal":
    SocialMediumClaim
  of "low", "weak", "little", "slight":
    SocialLowClaim
  else:
    SocialMediumClaim

proc socialWords(text: string): seq[string] =
  ## Splits social text into normalized words.
  for word in strutils.splitWhitespace(text.normalizeSocialText()):
    result.add word

proc socialWordSentences(text: string): seq[seq[string]] =
  ## Splits social text into normalized sentence word lists.
  var current = ""
  for ch in text:
    if ch in {'.', '!', '?', ';', '\n'}:
      let words = current.socialWords()
      if words.len > 0:
        result.add words
      current.setLen(0)
    else:
      current.add ch
  let words = current.socialWords()
  if words.len > 0:
    result.add words

proc socialNear(
  words: openArray[string],
  index: int,
  terms: openArray[string],
  before,
  after: int
): bool =
  ## Returns true when a term appears near one word index.
  let
    first = max(0, index - before)
    last = min(words.len - 1, index + after)
  for i in first .. last:
    for term in terms:
      if words[i] == term:
        return true

proc socialNearNegated(
  words: openArray[string],
  index: int,
  terms: openArray[string]
): bool =
  ## Returns true when a nearby term is directly negated.
  let
    first = max(0, index - 4)
    last = min(words.len - 1, index + 4)
  for i in first .. last:
    var found = false
    for term in terms:
      if words[i] == term:
        found = true
        break
    if not found:
      continue
    let negationFirst = max(0, i - 2)
    for j in negationFirst ..< i:
      for negation in SocialNegationWords:
        if words[j] == negation:
          return true

proc socialKilledVictim(words: openArray[string], index: int): bool =
  ## Returns true when this color appears as a kill victim.
  if index <= 0:
    return false
  words[index - 1] in ["killed", "murdered"]

proc socialDeadMention(words: openArray[string], index: int): bool =
  ## Returns true when this color is named as dead, not accused.
  if index + 1 < words.len and words[index + 1] in ["dead", "died"]:
    return true
  if index + 1 < words.len and words[index + 1] in ["body", "bodies"]:
    return true
  if index + 2 < words.len and
      words[index + 1] in ["is", "was"] and
      words[index + 2] in ["dead", "died", "killed", "murdered"]:
    return true
  if index + 2 < words.len and
      words[index + 1] in ["s"] and
      words[index + 2] in ["body"]:
    return true
  if index + 3 < words.len and
      words[index + 1] in ["was"] and
      words[index + 2] in ["found"] and
      words[index + 3] in ["dead"]:
    return true

proc socialQuestionMention(words: openArray[string], index: int): bool =
  ## Returns true when this color is part of an information question.
  let
    first = max(0, index - 5)
    last = min(words.len - 1, index + 5)
  var
    hasQuestion = false
    hasRequest = false
    hasInfo = false
  for i in first .. last:
    if words[i] in ["who", "where", "when", "what"]:
      hasQuestion = true
    if words[i] in ["give", "list", "explain", "say"]:
      hasRequest = true
    if words[i] in ["route", "routes", "room", "rooms", "task", "tasks", "path"]:
      hasInfo = true
  if hasRequest and hasInfo:
    return true
  if not hasQuestion:
    return false
  for i in first .. last:
    if words[i] in [
      "near", "with", "body", "dead", "died", "killed", "murdered"
    ]:
      return true
  hasQuestion and hasInfo

proc socialWithMe(words: openArray[string], index: int): bool =
  ## Returns true when plain chat says this color was with me.
  if not words.socialNear(index, ["with"], 3, 3):
    return false
  words.socialNear(index, ["me"], 3, 3)

proc socialNearRisk(words: openArray[string], index: int): bool =
  ## Returns true when this color is placed near a body or kill.
  if not words.socialNear(index, ["near"], 3, 3):
    return false
  words.socialNear(index, SocialRiskWords, 6, 6)

proc plainSocialStance(
  words: openArray[string],
  index: int
): tuple[found: bool, stance: SocialStance, strength: int] =
  ## Infers a social stance around one color mention.
  let
    trustNegated = words.socialNearNegated(
      index,
      ["trust", "trusts", "trusted"]
    )
    susNegated = words.socialNearNegated(index, SocialSusWords)
    clearNear = words.socialNear(index, SocialClearWords, 4, 4)
    susNear = words.socialNear(index, SocialSusWords, 4, 4)
    highSusNear = words.socialNear(index, SocialHighSusWords, 3, 3)
    softNear = words.socialNear(index, SocialSoftWords, 4, 4)
    riskNear = words.socialNearRisk(index)
    strongNear = words.socialNear(index, SocialStrongWords, 4, 4)
  result.strength =
    if softNear:
      SocialLowClaim
    elif highSusNear or riskNear or (susNear and strongNear):
      SocialHighClaim
    else:
      SocialMediumClaim
  if words.socialDeadMention(index) or words.socialQuestionMention(index):
    return
  if (highSusNear or riskNear) and not words.socialKilledVictim(index):
    return (true, SocialSus, result.strength)
  if trustNegated:
    return (true, SocialSus, SocialMediumClaim)
  if susNegated:
    return (true, SocialClear, SocialMediumClaim)
  if clearNear or words.socialWithMe(index):
    return (true, SocialClear, result.strength)
  if susNear:
    return (true, SocialSus, result.strength)

proc parsePlainSocialClaims*(
  speaker: int,
  text: string
): seq[SocialClaim] =
  ## Extracts plain-English sus and clear claims from visible chat.
  if speaker < 0 or speaker >= SocialPlayerCount:
    return
  for words in text.socialWordSentences():
    if words.len == 0:
      continue
    for colorIndex, colorName in PlayerColorNames:
      if colorIndex == speaker:
        continue
      let normalizedColor = colorName.normalizeSocialText()
      for i, word in words:
        if word != normalizedColor:
          continue
        let stance = words.plainSocialStance(i)
        if not stance.found:
          continue
        result.add SocialClaim(
          speaker: speaker,
          target: colorIndex,
          stance: stance.stance,
          strength: stance.strength,
          reason: text
        )

proc socialTargetHardActionClaim*(text: string, target: int): bool =
  ## Returns true when text directly says the target killed or vented.
  if target < 0 or target >= PlayerColorNames.len:
    return false
  let normalizedColor = PlayerColorNames[target].normalizeSocialText()
  for words in text.socialWordSentences():
    for i, word in words:
      if word != normalizedColor:
        continue
      if words.socialKilledVictim(i) or
          words.socialDeadMention(i) or
          words.socialQuestionMention(i):
        continue
      if words.socialNear(i, SocialHighSusWords, 3, 3):
        return true

proc parseSocialClaim(node: JsonNode): tuple[ok: bool, claim: SocialClaim] =
  ## Parses one social claim JSON node.
  if node.kind != JObject:
    return
  let
    speaker = node.socialNodeString("speaker").socialColorIndex()
    target = node.socialNodeString("target").socialColorIndex()
    parsedStance = node.socialNodeString("stance").socialStance()
  if speaker == SocialUnknown or target == SocialUnknown:
    return
  if speaker == target or not parsedStance.ok:
    return
  result.claim = SocialClaim(
    speaker: speaker,
    target: target,
    stance: parsedStance.stance,
    strength: node.socialNodeString("strength").socialStrength(),
    reason: node.socialNodeString("reason").strip()
  )
  result.ok = true

proc parseSocialLlmResult*(
  text: string
): tuple[ok: bool, social: SocialLlmResult] =
  ## Parses the social JSON returned by the meeting LLM.
  let raw = text.strip()
  if raw.len == 0:
    return
  let node = parseJson(raw.socialJsonObjectText())
  result.social.message = node.socialNodeString("message").strip()
  for item in node.socialNodeArray("claims"):
    let parsed = item.parseSocialClaim()
    if parsed.ok:
      result.social.claims.add parsed.claim
  result.ok = result.social.message.len > 0 or result.social.claims.len > 0

proc socialClaimKey*(claim: SocialClaim): string =
  ## Returns a stable key for de-duplicating one claim.
  $claim.speaker & "|" & $claim.target & "|" & $claim.stance & "|" &
    $claim.strength & "|" & claim.reason.normalizeSocialText()

proc applySocialClaim*(matrix: var SocialMatrix, claim: SocialClaim) =
  ## Applies one social claim to a speaker-target graph.
  if claim.speaker < 0 or claim.speaker >= SocialPlayerCount:
    return
  if claim.target < 0 or claim.target >= SocialPlayerCount:
    return
  let delta =
    case claim.stance
    of SocialSus:
      claim.strength
    of SocialClear:
      -claim.strength
  matrix[claim.speaker][claim.target] =
    clamp(matrix[claim.speaker][claim.target] + delta, -300, 300)

proc aliveColorMask*(state: SocialVoteState): array[SocialPlayerCount, bool] =
  ## Returns alive colors for one vote state.
  for slot in 0 ..< state.playerCount:
    let colorIndex = state.slotColors[slot]
    if colorIndex >= 0 and colorIndex < SocialPlayerCount:
      result[colorIndex] = state.slotAlive[slot]

proc socialAliveCount*(state: SocialVoteState): int =
  ## Counts living voting players.
  for slot in 0 ..< state.playerCount:
    if state.slotAlive[slot]:
      inc result

proc slotForSocialColor*(state: SocialVoteState, colorIndex: int): int =
  ## Returns the voting slot for one color.
  for slot in 0 ..< state.playerCount:
    if state.slotColors[slot] == colorIndex:
      return slot
  SocialUnknown

proc socialTargetLegal*(
  state: SocialVoteState,
  targetSlot: int,
  roleImposter: bool
): bool =
  ## Returns true when one voting slot is a legal target.
  if targetSlot < 0 or targetSlot >= state.playerCount:
    return false
  if targetSlot == state.selfSlot or not state.slotAlive[targetSlot]:
    return false
  let colorIndex = state.slotColors[targetSlot]
  if roleImposter and colorIndex >= 0 and
      colorIndex < state.knownImposters.len:
    return not state.knownImposters[colorIndex]
  true

proc socialVoteThreshold*(aliveCount: int): int =
  ## Returns the sus threshold for the current living player count.
  if aliveCount <= 4:
    return low(int)
  if aliveCount == 5:
    return 35
  if aliveCount <= 7:
    return 45
  75

proc socialTrustScores*(
  matrix: SocialMatrix,
  selfColor: int,
  alive: openArray[bool]
): array[SocialPlayerCount, int] =
  ## Propagates trust through clear claims from trusted players.
  if selfColor < 0 or selfColor >= SocialPlayerCount:
    return
  result[selfColor] = SocialSelfTrust
  for _ in 0 ..< SocialTrustIterations:
    var next = result
    for speaker in 0 ..< SocialPlayerCount:
      if result[speaker] <= 0:
        continue
      for target in 0 ..< SocialPlayerCount:
        if target == speaker or target >= alive.len or not alive[target]:
          continue
        let clear = max(0, -matrix[speaker][target])
        if clear <= 0:
          continue
        let offered = min(
          SocialMaxFriendTrust,
          result[speaker] * clear div 140
        )
        if offered > next[target]:
          next[target] = offered
    result = next

proc socialPressureForTarget*(
  matrix: SocialMatrix,
  trust: openArray[int],
  target: int
): int =
  ## Returns trusted social pressure against one target.
  if target < 0 or target >= SocialPlayerCount:
    return
  for speaker in 0 ..< SocialPlayerCount:
    if speaker == target or speaker >= trust.len:
      continue
    if trust[speaker] <= 0:
      continue
    result += matrix[speaker][target] * trust[speaker] div SocialSelfTrust

proc effectiveSocialSus*(
  directSus: openArray[int],
  matrix: SocialMatrix,
  state: SocialVoteState
): array[SocialPlayerCount, int] =
  ## Combines direct evidence, trusted claims, and trust flow.
  let
    alive = state.aliveColorMask()
    trust = matrix.socialTrustScores(state.selfColor, alive)
  for colorIndex in 0 ..< SocialPlayerCount:
    if colorIndex < directSus.len:
      result[colorIndex] = directSus[colorIndex]
    result[colorIndex] += matrix.socialPressureForTarget(trust, colorIndex)
    result[colorIndex] -= trust[colorIndex] div 10

proc bestSocialTarget(
  state: SocialVoteState,
  scores: openArray[int],
  roleImposter: bool
): tuple[found: bool, slot: int, score: int] =
  ## Returns the legal target with the highest effective sus score.
  result.score = low(int)
  for slot in 0 ..< state.playerCount:
    if not state.socialTargetLegal(slot, roleImposter):
      continue
    let colorIndex = state.slotColors[slot]
    if colorIndex < 0 or colorIndex >= scores.len:
      continue
    if not result.found or scores[colorIndex] > result.score:
      result = (true, slot, scores[colorIndex])

proc bestBrigadeTarget(
  state: SocialVoteState,
  scores: openArray[int],
  roleImposter: bool,
  minScore: int
): tuple[found: bool, slot: int, count: int] =
  ## Returns the strongest public voting pile we still find plausible.
  var counts: array[SocialMaxSlots, int]
  for voterColor, choice in state.choices:
    if voterColor == state.selfColor:
      continue
    if not state.socialTargetLegal(choice, roleImposter):
      continue
    let targetColor = state.slotColors[choice]
    if targetColor < 0 or targetColor >= scores.len:
      continue
    if not roleImposter:
      let voterSlot = state.slotForSocialColor(voterColor)
      if voterSlot < 0 or
          voterSlot >= state.playerCount or
          not state.slotAlive[voterSlot]:
        continue
      if voterColor >= 0 and
          voterColor < scores.len and
          scores[voterColor] > scores[targetColor]:
        continue
    if not roleImposter and scores[targetColor] < minScore:
      continue
    inc counts[choice]
  var tied = false
  for slot in 0 ..< state.playerCount:
    if counts[slot] > result.count:
      result = (true, slot, counts[slot])
      tied = false
    elif counts[slot] == result.count and counts[slot] > 0:
      tied = true
  if tied:
    result.found = false

proc imposterSkipLocked(state: SocialVoteState): bool =
  ## Returns true when visible skip votes already prevent an ejection.
  var
    targetCounts: array[SocialMaxSlots, int]
    aliveCount = 0
    visibleCount = 0
    skipCount = 0
    bestTargetCount = 0
  for slot in 0 ..< state.playerCount:
    if state.slotAlive[slot]:
      inc aliveCount
  for voterColor, choice in state.choices:
    if voterColor == state.selfColor:
      continue
    let voterSlot = state.slotForSocialColor(voterColor)
    if voterSlot < 0 or
        voterSlot >= state.playerCount or
        not state.slotAlive[voterSlot]:
      continue
    if choice == SocialUnknown:
      continue
    inc visibleCount
    if choice == SocialSkip:
      inc skipCount
    elif choice >= 0 and
        choice < state.playerCount and
        state.slotAlive[choice]:
      inc targetCounts[choice]
  for count in targetCounts:
    bestTargetCount = max(bestTargetCount, count)
  let remainingCount = max(0, aliveCount - visibleCount)
  skipCount >= SocialImposterBrigadeVotes and
    skipCount >= bestTargetCount + remainingCount

proc imposterSkipDefense(
  state: SocialVoteState
): tuple[found: bool, colorIndex: int, count: int] =
  ## Returns an accused imposter teammate that skip can defend.
  var
    counts: array[SocialMaxSlots, int]
    skipCount = 0
    openImposterSkips = 0
  result.colorIndex = SocialUnknown
  for voterColor, choice in state.choices:
    if voterColor == state.selfColor:
      continue
    if choice == SocialSkip:
      inc skipCount
      continue
    if choice < 0 or choice >= state.playerCount:
      continue
    let targetColor = state.slotColors[choice]
    if targetColor < 0 or
        targetColor >= state.knownImposters.len or
        not state.knownImposters[targetColor] or
        not state.slotAlive[choice]:
      continue
    inc counts[choice]
  for colorIndex, known in state.knownImposters:
    if not known:
      continue
    let slot = state.slotForSocialColor(colorIndex)
    if slot < 0 or slot >= state.playerCount or not state.slotAlive[slot]:
      continue
    let choice = state.choices[colorIndex]
    if colorIndex == state.selfColor and (
      choice == SocialUnknown or choice == SocialSkip
    ):
      inc openImposterSkips
    elif colorIndex != state.selfColor and choice == SocialUnknown:
      inc openImposterSkips
  for slot in 0 ..< state.playerCount:
    if counts[slot] <= result.count:
      continue
    result.count = counts[slot]
    result.colorIndex = state.slotColors[slot]
  if result.count >= SocialImposterDangerVotes and
      skipCount + openImposterSkips >= result.count:
    result.found = true

proc imposterPartnerPressure(
  state: SocialVoteState
): tuple[found: bool, colorIndex: int, count: int] =
  ## Returns an imposter teammate with any visible vote pressure.
  var
    counts: array[SocialMaxSlots, int]
    skipCount = 0
    openImposterSkips = 0
  result.colorIndex = SocialUnknown
  for voterColor, choice in state.choices:
    if voterColor == state.selfColor:
      continue
    if choice == SocialSkip:
      inc skipCount
      continue
    if choice < 0 or choice >= state.playerCount:
      continue
    let targetColor = state.slotColors[choice]
    if targetColor < 0 or
        targetColor >= state.knownImposters.len or
        not state.knownImposters[targetColor] or
        not state.slotAlive[choice]:
      continue
    inc counts[choice]
  for colorIndex, known in state.knownImposters:
    if not known:
      continue
    let slot = state.slotForSocialColor(colorIndex)
    if slot < 0 or slot >= state.playerCount or not state.slotAlive[slot]:
      continue
    let choice = state.choices[colorIndex]
    if colorIndex == state.selfColor and (
      choice == SocialUnknown or choice == SocialSkip
    ):
      inc openImposterSkips
    elif colorIndex != state.selfColor and choice == SocialUnknown:
      inc openImposterSkips
  for slot in 0 ..< state.playerCount:
    if counts[slot] <= result.count:
      continue
    result.count = counts[slot]
    result.colorIndex = state.slotColors[slot]
  if result.count > 0 and skipCount + openImposterSkips >= result.count:
    result.found = true

proc chooseSocialVote*(
  state: SocialVoteState,
  scores: openArray[int],
  roleImposter,
  forced: bool
): SocialVoteDecision =
  ## Chooses a deterministic vote target from social sus and public votes.
  let
    aliveCount = state.socialAliveCount()
    threshold = socialVoteThreshold(aliveCount)
    brigadeMin =
      if forced:
        low(int)
      elif threshold == low(int):
        low(int)
    else:
        max(10, threshold div 2)
    brigade = state.bestBrigadeTarget(scores, roleImposter, brigadeMin)
    crewBrigadeVotes =
      if aliveCount <= 3:
        1
      else:
        SocialCrewBrigadeVotes
  if roleImposter and state.imposterSkipLocked():
    return SocialVoteDecision(
      found: true,
      target: state.playerCount,
      reason: "visible skip pile already blocks ejection",
      instant: true
    )
  if brigade.found and (
    (not roleImposter and brigade.count >= crewBrigadeVotes) or
    (roleImposter and (forced or brigade.count >= SocialImposterBrigadeVotes))
  ):
    let reason =
      if roleImposter:
        "joining visible vote pile against "
      else:
        "joining social vote pile against "
    return SocialVoteDecision(
      found: true,
      target: brigade.slot,
      reason: reason &
        socialColorName(state.slotColors[brigade.slot]),
      instant: roleImposter
    )
  if roleImposter:
    let defense = state.imposterSkipDefense()
    if defense.found:
      return SocialVoteDecision(
        found: true,
        target: state.playerCount,
        reason: "defending accused imposter " &
          socialColorName(defense.colorIndex) & " with skip",
        instant: true
      )
    let pressure = state.imposterPartnerPressure()
    if pressure.found:
      return SocialVoteDecision(
        found: true,
        target: state.playerCount,
        reason: "early partner pressure skip for " &
          socialColorName(pressure.colorIndex),
        instant: true
      )
  let best = state.bestSocialTarget(scores, roleImposter)
  if not best.found:
    return
  if forced or best.score >= threshold:
    return SocialVoteDecision(
      found: true,
      target: best.slot,
      reason: "top effective sus " &
        socialColorName(state.slotColors[best.slot]),
      instant: forced
    )
