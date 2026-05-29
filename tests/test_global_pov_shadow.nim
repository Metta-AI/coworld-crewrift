import
  std/os,
  bitworld/spriteprotocol,
  crewrift/[global, sim]

const GameDir = currentSourcePath.parentDir.parentDir

proc initCrewriftForTest(config: GameConfig): SimServer =
  ## Initializes Crewrift from the game directory.
  let previousDir = getCurrentDir()
  setCurrentDir(GameDir)
  try:
    result = initSimServer(config)
  finally:
    setCurrentDir(previousDir)

proc hasShadowSprite(messages: openArray[SpritePacketMessage]): bool =
  ## Returns true when one packet updates the player shadow sprite.
  for message in messages:
    if message.kind == spkSprite and message.sprite.label == "shadow":
      return true

proc buildGlobalMessages(
  sim: var SimServer,
  state: GlobalViewerState,
  nextState: var GlobalViewerState
): seq[SpritePacketMessage] =
  ## Builds and parses one global sprite packet.
  sim.buildSpriteProtocolUpdates(state, nextState).parseSpritePacket()

proc testSelectedPovShadowRefresh() =
  ## Tests that selected global PoV sends refreshed shadow sprites.
  var game = initCrewriftForTest(defaultGameConfig())
  let playerIndex = game.addPlayer("pov")
  game.phase = Playing

  var
    state = initGlobalViewerState()
    nextState: GlobalViewerState
  state.selectedJoinOrder = game.players[playerIndex].joinOrder
  let firstMessages = game.buildGlobalMessages(state, nextState)
  doAssert firstMessages.hasShadowSprite()

  state = nextState
  game.players[playerIndex].x += 8
  let view = game.playerView(playerIndex)
  discard game.usePlayerShadowMask(playerIndex, view)
  let secondMessages = game.buildGlobalMessages(state, nextState)
  doAssert secondMessages.hasShadowSprite()

echo "Testing global PoV shadow refresh"
testSelectedPovShadowRefresh()
echo "ok"
