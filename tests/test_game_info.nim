import
  std/[json, os],
  bitworld/spriteprotocol,
  crewrift/[global, sim],
  ../players/notsus/notsus/protocols

const GameDir = currentSourcePath.parentDir.parentDir

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc addPlayers(sim: var SimServer, count: int) =
  ## Adds test players to the simulation.
  for i in 0 ..< count:
    discard sim.addPlayer("player" & $(i + 1))

proc stepEmpty(sim: var SimServer) =
  ## Advances the simulation with neutral inputs.
  let inputs = newSeq[InputState](sim.players.len)
  sim.step(inputs, inputs)

proc buildPlayerPacket(
  sim: var SimServer,
  playerIndex: int
): seq[uint8] =
  ## Builds one player sprite packet.
  var
    state = initPlayerViewerState()
    nextState: PlayerViewerState
  sim.buildSpriteProtocolPlayerUpdates(
    playerIndex,
    state,
    nextState
  )

proc buildPlayerMessages(
  sim: var SimServer,
  playerIndex: int
): seq[SpritePacketMessage] =
  ## Builds and parses one player sprite packet.
  sim.buildPlayerPacket(playerIndex).parseSpritePacket()

proc buildGlobalMessages(sim: var SimServer): seq[SpritePacketMessage] =
  ## Builds and parses one global sprite packet.
  var
    state = initGlobalViewerState()
    nextState: GlobalViewerState
  sim.buildSpriteProtocolUpdates(state, nextState).parseSpritePacket()

proc hasSpriteLabel(
  messages: openArray[SpritePacketMessage],
  label: string
): bool =
  ## Returns true when a sprite packet defines one label.
  for message in messages:
    if message.kind == spkSprite and message.sprite.label == label:
      return true

proc requireSpriteLabel(
  messages: openArray[SpritePacketMessage],
  label: string
) =
  ## Requires one sprite label to appear in a packet.
  doAssert messages.hasSpriteLabel(label), "Missing sprite label " & label & "."

proc readNotsusGameInfo(
  sim: var SimServer,
  playerIndex: int
): GameInfoSettings =
  ## Reads game info through the notsus sprite protocol client.
  let client = initProtocolClient()
  doAssert client.applySpritePacket(
    blobFromBytes(sim.buildPlayerPacket(playerIndex)),
    false
  )
  client.gameInfoSettings()

proc testGameInfoConfig() =
  ## Tests the default and serialized game-info timer config.
  var config = defaultGameConfig()
  doAssert config.gameInfoTicks == 3 * TargetFps

  config.update("""{"gameInfoTicks":7}""")
  doAssert config.gameInfoTicks == 7

  let serialized = parseJson(config.configJson())
  doAssert serialized["gameInfoTicks"].getInt() == 7

  var roundTrip = defaultGameConfig()
  roundTrip.update($serialized)
  doAssert roundTrip.gameInfoTicks == 7

proc testCountdownShowsGameInfo() =
  ## Tests that the lobby countdown is followed by game info.
  var config = defaultGameConfig()
  config.minPlayers = 3
  config.imposterCount = 1
  config.autoImposterCount = false
  config.startWaitTicks = 2
  config.gameInfoTicks = 3
  config.roleRevealTicks = 0
  config.tasksPerPlayer = 2

  var sim = initCrewriftForTest(config)
  sim.addPlayers(3)

  sim.stepEmpty()
  doAssert sim.phase == Lobby
  doAssert sim.startWaitTimer == 1

  sim.stepEmpty()
  doAssert sim.phase == GameInfo
  doAssert sim.gameInfoTimer == 3
  doAssert sim.gameTicksElapsed() == 0

  let messages = sim.buildPlayerMessages(0)
  messages.requireSpriteLabel("GAME INFO")
  messages.requireSpriteLabel("KILL COOLDOWN 500T")
  messages.requireSpriteLabel("TASKS 2 EACH")
  messages.requireSpriteLabel("VOTE TIMER 240T")
  messages.requireSpriteLabel("GAME TIMER 10000T")

  let globalMessages = sim.buildGlobalMessages()
  globalMessages.requireSpriteLabel("GAME INFO")
  globalMessages.requireSpriteLabel("KILL COOLDOWN 500T")
  globalMessages.requireSpriteLabel("TASKS 2 EACH")
  globalMessages.requireSpriteLabel("VOTE TIMER 240T")
  globalMessages.requireSpriteLabel("GAME TIMER 10000T")

  let settings = sim.readNotsusGameInfo(0)
  doAssert settings.complete
  doAssert settings.killCooldownTicks == 500
  doAssert settings.tasksPerPlayer == 2
  doAssert settings.voteTimerTicks == 240
  doAssert settings.maxTicks == 10000

  sim.stepEmpty()
  doAssert sim.phase == GameInfo
  doAssert sim.gameInfoTimer == 2

  sim.stepEmpty()
  doAssert sim.phase == GameInfo
  doAssert sim.gameInfoTimer == 1

  sim.stepEmpty()
  doAssert sim.phase == Playing
  doAssert sim.gameTicksElapsed() == 0

proc testNotsusReadsGameTimerNone() =
  ## Tests that notsus reads the disabled game timer setting.
  var config = defaultGameConfig()
  config.killCooldownTicks = 321
  config.tasksPerPlayer = 4
  config.voteTimerTicks = 123
  config.maxTicks = 0

  var sim = initCrewriftForTest(config)
  discard sim.addPlayer("player")
  sim.phase = GameInfo
  sim.gameInfoTimer = 3

  let settings = sim.readNotsusGameInfo(0)
  doAssert settings.complete
  doAssert settings.killCooldownTicks == 321
  doAssert settings.tasksPerPlayer == 4
  doAssert settings.voteTimerTicks == 123
  doAssert settings.maxTicks == 0

echo "Testing game info interstitial"
testGameInfoConfig()
testCountdownShowsGameInfo()
testNotsusReadsGameTimerNone()
echo "ok"
