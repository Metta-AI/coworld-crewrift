import
  std/[json, unittest],
  ../players/notsus/notsus/socials

proc emptyVoteState(): SocialVoteState =
  ## Builds a simple eight-player social vote state.
  result.playerCount = 8
  result.selfSlot = 0
  result.selfColor = 0
  for i in 0 ..< result.playerCount:
    result.slotColors[i] = i
    result.slotAlive[i] = true
  for i in 0 ..< result.choices.len:
    result.choices[i] = SocialUnknown

suite "notsus social reasoning":
  test "parses social JSON claims":
    let parsed = parseSocialLlmResult($ %*{
      "message": "Blue is sus. Red is clear.",
      "claims": [
        {
          "speaker": "red",
          "target": "blue",
          "stance": "sus",
          "strength": "high",
          "reason": "near body"
        },
        {
          "speaker": "green",
          "target": "red",
          "stance": "clear",
          "strength": "medium"
        }
      ]
    })
    check parsed.ok
    check parsed.social.message == "Blue is sus. Red is clear."
    check parsed.social.claims.len == 2
    check parsed.social.claims[0].speaker == 0
    check parsed.social.claims[0].target == 1
    check parsed.social.claims[0].stance == SocialSus
    check parsed.social.claims[0].strength == SocialHighClaim

  test "parses plain English suspicion":
    let claims = parsePlainSocialClaims(0, "Blue vented. Vote pink too.")
    check claims.len == 2
    check claims[0].speaker == 0
    check claims[0].target == 1
    check claims[0].stance == SocialSus
    check claims[0].strength == SocialHighClaim
    check claims[1].target == 3
    check claims[1].stance == SocialSus

  test "parses plain English clears and negation":
    let claims = parsePlainSocialClaims(1, "I trust red. Green is not sus.")
    check claims.len == 2
    check claims[0].target == 0
    check claims[0].stance == SocialClear
    check claims[1].target == 2
    check claims[1].stance == SocialClear

  test "does not blame plain English kill victims":
    let claims = parsePlainSocialClaims(2, "Red killed blue.")
    check claims.len == 1
    check claims[0].target == 0
    check claims[0].stance == SocialSus

  test "questions about nearby players are not accusations":
    let claims = parsePlainSocialClaims(
      3,
      "Yellow is dead. Green called the body. Who was near yellow last? " &
        "Blue, orange, I saw you both near Storage Deck."
    )
    check claims.len == 0

  test "dead player questions are not accusations":
    let claims = parsePlainSocialClaims(
      1,
      "Green is dead. Yellow called the meeting. Where was green killed, " &
        "and who was near that body?"
    )
    check claims.len == 0

  test "reporting and being with dead is not an accusation":
    let claims = parsePlainSocialClaims(
      0,
      "Green called the body and was with orange most of round. " &
        "Who was near orange when they died?"
    )
    check claims.len == 0

  test "dead player context does not eat real target":
    let claims = parsePlainSocialClaims(
      2,
      "Red is dead. Vote cyan."
    )
    check claims.len == 1
    check claims[0].target == 7
    check claims[0].stance == SocialSus

  test "near kill statements are high suspicion":
    let claims = parsePlainSocialClaims(
      2,
      "Purple was near the kill. Purple, where exactly were you?"
    )
    check claims.len == 1
    check claims[0].target == 6
    check claims[0].stance == SocialSus
    check claims[0].strength == SocialHighClaim

  test "near body statements are high suspicion":
    let claims = parsePlainSocialClaims(
      2,
      "Cyan was near blue body and followed around."
    )
    check claims.len == 1
    check claims[0].target == 7
    check claims[0].stance == SocialSus
    check claims[0].strength == SocialHighClaim

  test "risk words do not leak across sentences":
    let claims = parsePlainSocialClaims(
      5,
      "Purple was near the kill. Blue and pink followed others around."
    )
    check claims.len == 3
    check claims[0].target == 6
    check claims[0].stance == SocialSus
    check claims[0].strength == SocialHighClaim
    check claims[1].target == 1
    check claims[1].stance == SocialSus
    check claims[1].strength == SocialMediumClaim
    check claims[2].target == 3
    check claims[2].stance == SocialSus
    check claims[2].strength == SocialMediumClaim

  test "plain room sightings are not suspicion":
    let claims = parsePlainSocialClaims(
      2,
      "I saw blue near Bridge earlier."
    )
    check claims.len == 0

  test "near a room when someone died is not suspicion":
    let claims = parsePlainSocialClaims(
      2,
      "Red and orange were both near Storage Deck when green died."
    )
    check claims.len == 0

  test "main sus phrasing is high suspicion":
    let claims = parsePlainSocialClaims(
      2,
      "Blue is my main sus from body timing."
    )
    check claims.len == 1
    check claims[0].target == 1
    check claims[0].stance == SocialSus
    check claims[0].strength == SocialHighClaim

  test "trust flows through clears":
    var graph: SocialMatrix
    graph.applySocialClaim SocialClaim(
      speaker: 0,
      target: 1,
      stance: SocialClear,
      strength: SocialHighClaim
    )
    graph.applySocialClaim SocialClaim(
      speaker: 1,
      target: 2,
      stance: SocialClear,
      strength: SocialHighClaim
    )
    var alive: array[SocialPlayerCount, bool]
    for i in 0 ..< alive.len:
      alive[i] = true
    let trust = graph.socialTrustScores(0, alive)
    check trust[0] == SocialSelfTrust
    check trust[1] > 0
    check trust[2] > 0

  test "trusted accusation changes effective sus":
    var
      state = emptyVoteState()
      graph: SocialMatrix
      direct: array[SocialPlayerCount, int]
    graph.applySocialClaim SocialClaim(
      speaker: 0,
      target: 1,
      stance: SocialClear,
      strength: SocialHighClaim
    )
    graph.applySocialClaim SocialClaim(
      speaker: 1,
      target: 2,
      stance: SocialSus,
      strength: SocialHighClaim
    )
    let scores = effectiveSocialSus(direct, graph, state)
    check scores[2] > scores[3]
    check scores[1] < scores[3]

  test "untrusted accusations do not beat direct evidence":
    var
      state = emptyVoteState()
      graph: SocialMatrix
      direct: array[SocialPlayerCount, int]
    direct[6] = 180
    graph.applySocialClaim SocialClaim(
      speaker: 6,
      target: 1,
      stance: SocialSus,
      strength: SocialHighClaim
    )
    graph.applySocialClaim SocialClaim(
      speaker: 7,
      target: 1,
      stance: SocialSus,
      strength: SocialHighClaim
    )
    let scores = effectiveSocialSus(direct, graph, state)
    check scores[6] > scores[1]

  test "threshold falls with alive count":
    check socialVoteThreshold(8) > socialVoteThreshold(5)
    check socialVoteThreshold(4) == low(int)

  test "joins a plausible brigade":
    var
      state = emptyVoteState()
      scores: array[SocialPlayerCount, int]
    scores[2] = 50
    state.choices[1] = 2
    state.choices[3] = 2
    let decision = chooseSocialVote(state, scores, false, false)
    check decision.found
    check decision.target == 2

  test "crew ignores one vote below threshold":
    var
      state = emptyVoteState()
      scores: array[SocialPlayerCount, int]
    scores[2] = 50
    state.choices[1] = 2
    let decision = chooseSocialVote(state, scores, false, false)
    check not decision.found

  test "crew ignores votes from more suspicious voters":
    var
      state = emptyVoteState()
      scores: array[SocialPlayerCount, int]
    scores[1] = 80
    scores[2] = 120
    state.choices[2] = 1
    state.choices[3] = 1
    let decision = chooseSocialVote(state, scores, false, false)
    check decision.found
    check decision.target == 2

  test "imposter joins visible crew pile over stronger sus":
    var
      state = emptyVoteState()
      scores: array[SocialPlayerCount, int]
    state.selfSlot = 6
    state.selfColor = 6
    state.knownImposters[6] = true
    state.knownImposters[7] = true
    scores[2] = 300
    state.choices[4] = 1
    state.choices[5] = 1
    state.choices[1] = 4
    state.choices[2] = 7
    let decision = chooseSocialVote(state, scores, true, false)
    check decision.found
    check decision.target == 1
    check not decision.instant

  test "imposter waits against visible pile on partner":
    var
      state = emptyVoteState()
      scores: array[SocialPlayerCount, int]
    state.selfSlot = 6
    state.selfColor = 6
    state.knownImposters[6] = true
    state.knownImposters[7] = true
    state.choices[4] = 7
    state.choices[5] = 7
    let decision = chooseSocialVote(state, scores, true, false)
    check not decision.found

  test "imposter skips to defend accused partner":
    var
      state = emptyVoteState()
      scores: array[SocialPlayerCount, int]
    state.selfSlot = 6
    state.selfColor = 6
    state.knownImposters[6] = true
    state.knownImposters[7] = true
    state.choices[1] = SocialSkip
    state.choices[4] = 7
    state.choices[5] = 7
    let decision = chooseSocialVote(state, scores, true, false)
    check decision.found
    check decision.target == state.playerCount
    check not decision.instant

  test "forced vote picks top sus even under threshold":
    var
      state = emptyVoteState()
      scores: array[SocialPlayerCount, int]
    scores[4] = 5
    let decision = chooseSocialVote(state, scores, false, true)
    check decision.found
    check decision.target == 4
