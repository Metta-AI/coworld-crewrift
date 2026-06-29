import
  std/[algorithm, locks, monotimes, nativesockets, os, strutils, tables, times],
  bitworld/client as bitworldClient, bitworld/profile, bitworld/spriteprotocol,
  bitworld/runtime,
  curly, mummy,
  sim, global, replays

when defined(posix):
  from std/posix import SHUT_RDWR, shutdown

type
  WebSocketSocketFields = object
    server: Server
    clientSocket: SocketHandle
    clientId: uint64

  WebSocketAppState = object
    lock: Lock
    replayServerMode: bool
    replayLoaded: bool
    pendingReplayUri: string
    resetRequested: bool
    kickRequests: seq[string]
    kickedIdentities: Table[string, bool]
    inputMasks: Table[WebSocket, uint8]
    inputPressedMasks: Table[WebSocket, uint8]
    lastAppliedMasks: Table[WebSocket, uint8]
    playerReady: Table[WebSocket, bool]
    chatMessages: Table[WebSocket, string]
    debugSprites: Table[WebSocket, seq[uint8]]
    playerIndices: Table[WebSocket, int]
    playerAddresses: Table[WebSocket, string]
    playerSlots: Table[WebSocket, int]
    playerTokens: Table[WebSocket, string]
    globalViewers: Table[WebSocket, GlobalViewerState]
    playerViewers: Table[WebSocket, PlayerViewerState]
    rewardViewers: Table[WebSocket, bool]
    closedSockets: seq[WebSocket]
    nextAnonymousPlayer: int
    config: GameConfig

  ServerThreadArgs = object
    server: ptr Server
    address: string
    port: int

  PendingPlayerJoin = object
    websocket: WebSocket
    address: string
    token: string
    requestedSlot: int
    slotIndex: int

const
  HealthPath = "/healthz"
  AdminWebSocketPath = "/admin"
  ControlRestartPath = "/control/restart"
  ControlKickPath = "/control/kick"

proc liveProgressMaxTick(config: GameConfig): int =
  ## Returns the live viewer tick-bar budget.
  if config.maxTicks > 0:
    config.maxTicks
  else:
    MaxTicks

proc liveSpeedIndex(config: GameConfig): int =
  ## Returns the live playback speed index for a config.
  for i, speed in PlaybackSpeeds:
    if speed == config.speed:
      return i
  0

proc finalGameQuitReady*(
  config: GameConfig,
  gamesPlayed: int,
  phase: GamePhase,
  gameOverTimer: int
): bool =
  ## Returns true when a finite run has shown the final game-over screen.
  config.maxGames > 0 and gamesPlayed >= config.maxGames and
    (phase != GameOver or gameOverTimer <= 1)

proc isPlayerReadyPacket*(message: string): bool =
  ## Returns true when a binary client message is a player-ready signal.
  message.len == 1 and message[0].uint8 == SpriteClientReady

proc isWebSocketUpgrade(request: Request): bool =
  ## Returns true when the GET request is a websocket upgrade.
  request.headers["Sec-WebSocket-Key"].len > 0

proc replayFilePath(uri: string): string =
  ## Resolves one local replay URI to a host path.
  const FilePrefix = "file://"
  if uri.startsWith(FilePrefix):
    return uri[FilePrefix.len .. ^1]
  if "://" in uri:
    return ""
  uri

let replayDownloadPool = newCurlPool(1)

proc loadReplayUri(uri: string): ReplayData =
  ## Loads a replay from a local file URI or HTTP(S) URL.
  parseReplayBytes(readCogameUri(uri, CogameLoadReplayUriEnv))

proc readableReplayUri(uri: string): bool =
  ## Returns true when a replay URI can be opened by this server.
  if uri.len == 0:
    return false
  if uri.startsWith("http://") or uri.startsWith("https://"):
    return replayDownloadPool.head(uri).code == 200
  let path = replayFilePath(uri)
  path.len > 0 and fileExists(path)

proc rewardAddress(address: string): string =
  ## Formats one reward address as host:port.
  let parts = address.splitWhitespace()
  if parts.len >= 2:
    return parts[0] & ":" & parts[1]
  address

var appState: WebSocketAppState

proc markSocketClosed(websocket: WebSocket): bool =
  ## Queues a websocket for closed-socket cleanup and returns true once.
  result = websocket notin appState.closedSockets
  if result:
    appState.closedSockets.add(websocket)

proc initAppState() =
  initLock(appState.lock)
  appState.replayServerMode = false
  appState.replayLoaded = false
  appState.pendingReplayUri = ""
  appState.resetRequested = false
  appState.kickRequests = @[]
  appState.kickedIdentities = initTable[string, bool]()
  appState.inputMasks = initTable[WebSocket, uint8]()
  appState.inputPressedMasks = initTable[WebSocket, uint8]()
  appState.lastAppliedMasks = initTable[WebSocket, uint8]()
  appState.playerReady = initTable[WebSocket, bool]()
  appState.chatMessages = initTable[WebSocket, string]()
  appState.debugSprites = initTable[WebSocket, seq[uint8]]()
  appState.playerIndices = initTable[WebSocket, int]()
  appState.playerAddresses = initTable[WebSocket, string]()
  appState.playerSlots = initTable[WebSocket, int]()
  appState.playerTokens = initTable[WebSocket, string]()
  appState.globalViewers = initTable[WebSocket, GlobalViewerState]()
  appState.playerViewers = initTable[WebSocket, PlayerViewerState]()
  appState.rewardViewers = initTable[WebSocket, bool]()
  appState.closedSockets = @[]
  appState.nextAnonymousPlayer = 1
  appState.config = defaultGameConfig()

proc comparePendingPlayerJoins(
  a,
  b: PendingPlayerJoin
): int =
  ## Orders pending players by resolved slot and identity.
  result = cmp(a.slotIndex, b.slotIndex)
  if result != 0:
    return
  result = cmp(a.address, b.address)

proc pendingPlayerJoin(
  sim: SimServer,
  websocket: WebSocket
): PendingPlayerJoin =
  ## Resolves one pending websocket into a join candidate.
  result.websocket = websocket
  result.address = appState.playerAddresses.getOrDefault(websocket, "unknown")
  result.requestedSlot = appState.playerSlots.getOrDefault(websocket, -1)
  result.token = appState.playerTokens.getOrDefault(websocket, "")
  result.slotIndex = sim.resolvePlayerSlot(
    result.address,
    result.token,
    result.requestedSlot
  )

proc removePlayerWebSocketState(websocket: WebSocket): int =
  ## Removes player-owned websocket state and returns its former index.
  result = -1
  if websocket in appState.playerViewers:
    appState.playerViewers.del(websocket)
  if websocket in appState.playerIndices:
    result = appState.playerIndices[websocket]
    appState.playerIndices.del(websocket)
  appState.inputMasks.del(websocket)
  appState.inputPressedMasks.del(websocket)
  appState.lastAppliedMasks.del(websocket)
  appState.playerReady.del(websocket)
  appState.chatMessages.del(websocket)
  appState.debugSprites.del(websocket)
  appState.playerAddresses.del(websocket)
  appState.playerSlots.del(websocket)
  appState.playerTokens.del(websocket)

proc addressIsKicked(address: string): bool =
  ## Returns true when an address is blocked from this match.
  let identity = address.rewardAddress()
  address in appState.kickedIdentities or identity in appState.kickedIdentities

proc registerPlayerWebSocket(
  websocket: WebSocket,
  identity: string,
  slot: int,
  token: string
): bool =
  ## Registers one websocket as a player connection.
  appState.globalViewers.del(websocket)
  appState.rewardViewers.del(websocket)
  discard removePlayerWebSocketState(websocket)
  if identity.addressIsKicked():
    return false
  appState.playerViewers[websocket] = initPlayerViewerState()
  appState.playerAddresses[websocket] = identity
  appState.playerSlots[websocket] = slot
  appState.playerTokens[websocket] = token
  appState.playerIndices[websocket] =
    if appState.replayLoaded:
      -1
    else:
      0x7fffffff
  appState.inputMasks[websocket] = 0
  appState.inputPressedMasks[websocket] = 0
  appState.lastAppliedMasks[websocket] = 0
  appState.playerReady[websocket] = false
  true

proc registerGlobalWebSocket(websocket: WebSocket) =
  ## Registers one websocket as a global viewer connection.
  discard removePlayerWebSocketState(websocket)
  appState.rewardViewers.del(websocket)
  appState.globalViewers[websocket] = initGlobalViewerState()

proc registerRewardWebSocket(websocket: WebSocket) =
  ## Registers one websocket as a reward stream connection.
  discard removePlayerWebSocketState(websocket)
  appState.globalViewers.del(websocket)
  appState.rewardViewers[websocket] = true

proc isPlayerWebSocket(websocket: WebSocket): bool =
  ## Returns true when a websocket is exclusively a player connection.
  result =
    websocket in appState.playerViewers and
      websocket notin appState.globalViewers and
      websocket notin appState.rewardViewers

proc removeWebSocketState(websocket: WebSocket): int =
  ## Removes websocket-owned state and returns its former player index.
  if websocket in appState.globalViewers:
    appState.globalViewers.del(websocket)
  if websocket in appState.rewardViewers:
    appState.rewardViewers.del(websocket)
  result = removePlayerWebSocketState(websocket)

proc removePlayer(sim: var SimServer, websocket: WebSocket) =
  ## Removes a websocket and keeps live player indices consistent.
  let removedIndex = removeWebSocketState(websocket)
  if removedIndex >= 0 and removedIndex < sim.players.len:
    sim.removePlayerAt(removedIndex)
    for ws, value in appState.playerIndices.mpairs:
      if value > removedIndex:
        dec value

proc cleanPlayerName(name: string): string =
  ## Returns a protocol-safe player display name.
  result = name.strip()
  for ch in result.mitems:
    if ch.isSpaceAscii:
      ch = '_'

proc generatedPlayerName*(index: int): string =
  ## Returns the generated display name for an anonymous player index.
  "Player" & $index

proc anonymousPlayerIdentity*(
  nextIndex: var int,
  existingNames: openArray[string]
): string =
  ## Returns a unique generated identity for one nameless player.
  if nextIndex <= 0:
    nextIndex = 1
  while true:
    result = generatedPlayerName(nextIndex)
    inc nextIndex
    var taken = false
    for name in existingNames:
      if name == result:
        taken = true
        break
    if not taken:
      return

proc nextAnonymousPlayerIdentity(): string =
  ## Returns a unique generated identity from current server state.
  {.gcsafe.}:
    withLock appState.lock:
      var existingNames: seq[string] = @[]
      for _, address in appState.playerAddresses.pairs:
        existingNames.add(address)
      for identity in appState.kickedIdentities.keys:
        existingNames.add(identity)
      result = anonymousPlayerIdentity(
        appState.nextAnonymousPlayer,
        existingNames
      )

proc playerIdentity(request: Request, slot: int, token: string): string =
  ## Returns the websocket player identity for rewards and displays.
  let name = request.queryParams.getOrDefault("name", "").cleanPlayerName()
  if name.len > 0:
    return name
  {.gcsafe.}:
    withLock appState.lock:
      result = appState.config.configuredPlayerName(slot, token)
      if result.len > 0:
        return
  result = nextAnonymousPlayerIdentity()

proc playerSlot(request: Request): int =
  ## Returns the requested player slot or -1 for automatic assignment.
  let text = request.queryParams.getOrDefault("slot", "").strip()
  if text.len == 0:
    return -1
  try:
    result = parseInt(text)
  except ValueError:
    return MaxPlayers
  if result < 0 or result >= MaxPlayers:
    return MaxPlayers

proc playerToken(request: Request): string =
  ## Returns the player join token.
  request.queryParams.getOrDefault("token", "").strip()

proc controlHeaders(): HttpHeaders =
  ## Returns headers for admin-panel control requests.
  result["Content-Type"] = "text/plain; charset=utf-8"
  result["Cache-Control"] = "no-cache"
  result["Access-Control-Allow-Origin"] = "*"
  result["Access-Control-Allow-Methods"] = "POST, OPTIONS"
  result["Access-Control-Allow-Headers"] = "Content-Type"

proc respondControl(request: Request, status: int, body: string) =
  ## Sends a plain text control response.
  request.respond(status, controlHeaders(), body)

proc replayControlsDisabled(): bool =
  ## Returns true when live match controls are disabled.
  {.gcsafe.}:
    withLock appState.lock:
      result = appState.replayLoaded

proc replayServerModeEnabled(): bool =
  ## Returns true when the process is serving Coworld replay sessions.
  {.gcsafe.}:
    withLock appState.lock:
      result = appState.replayServerMode

proc disconnectWebSocket(websocket: WebSocket) =
  ## Tears down a player connection immediately.
  when defined(posix):
    let fields = cast[WebSocketSocketFields](websocket)
    discard shutdown(fields.clientSocket, SHUT_RDWR)
  else:
    websocket.close()

proc identityIsKicked(identity: string): bool =
  ## Returns true when an identity is blocked from rejoining this match.
  let rewardIdentity = identity.rewardAddress()
  {.gcsafe.}:
    withLock appState.lock:
      result =
        identity in appState.kickedIdentities or
        rewardIdentity in appState.kickedIdentities

proc respondKicked(request: Request) =
  ## Rejects a kicked player before upgrading to a WebSocket.
  var headers: HttpHeaders
  headers["Content-Type"] = "text/plain; charset=utf-8"
  headers["Cache-Control"] = "no-cache"
  headers["Connection"] = "close"
  request.respond(409, headers, "player was kicked\n")

proc respondReplayRequestError(request: Request, status: int, body: string) =
  ## Rejects a replay websocket request before upgrade.
  var headers: HttpHeaders
  headers["Content-Type"] = "text/plain; charset=utf-8"
  headers["Cache-Control"] = "no-cache"
  headers["Connection"] = "close"
  request.respond(status, headers, body)

proc respondForbiddenWebSocket(request: Request, reason: string) =
  ## Rejects a forbidden websocket request before upgrading.
  var headers: HttpHeaders
  headers["Content-Type"] = "text/plain; charset=utf-8"
  headers["Cache-Control"] = "no-cache"
  headers["Connection"] = "close"
  request.respond(403, headers, reason & "\n")

proc hasPlayerCredentialParams*(name, slot, token: string): bool =
  ## Returns true when query fields identify a player connection.
  name.strip().len > 0 or slot.strip().len > 0 or token.strip().len > 0

proc hasPlayerCredentialParams(request: Request): bool =
  ## Returns true when a websocket request carries player credentials.
  hasPlayerCredentialParams(
    request.queryParams.getOrDefault("name", ""),
    request.queryParams.getOrDefault("slot", ""),
    request.queryParams.getOrDefault("token", "")
  )

proc respondForbiddenViewer(request: Request) =
  ## Rejects a viewer websocket request with player credentials.
  request.respondForbiddenWebSocket(
    "Viewer websocket cannot include player name, slot, or token."
  )

proc configuredPlayerJoinError(
  config: GameConfig,
  address: string,
  slot: int,
  token: string
): string =
  ## Returns a rejection reason for bad configured roster credentials.
  if config.playerJoinAllowed(address, slot, token):
    return ""
  if slot >= MaxPlayers:
    return "Player slot must be between 0 and 15."
  if slot >= config.slots.len:
    if config.closedRoster:
      return "Player slot is outside configured roster."
    return ""
  if slot >= 0 and config.slots[slot].token.len > 0 and
      token != config.slots[slot].token:
    return "Player token does not match configured slot " & $slot & "."
  "Player credentials do not match configured roster."

proc replayRequestUri(request: Request): string =
  ## Returns the replay artifact URI requested by a Coworld replay client.
  request.queryParams.getOrDefault("uri", "").strip()

proc replayRequestUriOrPending(request: Request): tuple[uri: string, loaded: bool] =
  ## Returns the websocket URI, falling back to the URI captured when serving
  ## /client/replay. Kubernetes service-proxy websocket upgrades do not
  ## preserve query params, so the preceding client HTML request is the durable
  ## place to capture the artifact URI.
  result.uri = request.replayRequestUri()
  {.gcsafe.}:
    withLock appState.lock:
      result.loaded = appState.replayLoaded
      if result.uri.len == 0:
        result.uri = appState.pendingReplayUri

proc httpHandler(request: Request) =
  if request.path == HealthPath and request.httpMethod == "GET":
    var headers: HttpHeaders
    headers["Content-Type"] = "text/plain; charset=utf-8"
    headers["Cache-Control"] = "no-cache"
    request.respond(200, headers, "healthy")
  elif request.path == WebSocketPath and request.httpMethod == "GET" and
      request.isWebSocketUpgrade():
    let
      slot = request.playerSlot()
      token = request.playerToken()
      identity = request.playerIdentity(slot, token)
    {.gcsafe.}:
      withLock appState.lock:
        let joinError = appState.config.configuredPlayerJoinError(
          identity,
          slot,
          token
        )
        if joinError.len > 0:
          request.respondForbiddenWebSocket(joinError)
          return
    if identity.identityIsKicked():
      request.respondKicked()
      return
    let websocket = request.upgradeToWebSocket()
    var accepted = false
    {.gcsafe.}:
      withLock appState.lock:
        accepted = websocket.registerPlayerWebSocket(identity, slot, token)
    if not accepted:
      websocket.disconnectWebSocket()
      return
    echo "player connected: ", identity
  elif request.path == GlobalWebSocketPath and request.httpMethod == "GET" and
      request.isWebSocketUpgrade():
    if request.hasPlayerCredentialParams():
      request.respondForbiddenViewer()
      return
    let websocket = request.upgradeToWebSocket()
    {.gcsafe.}:
      withLock appState.lock:
        websocket.registerGlobalWebSocket()
  elif request.path == ReplayWebSocketPath and request.httpMethod == "GET" and
      request.isWebSocketUpgrade():
    if request.hasPlayerCredentialParams():
      request.respondForbiddenViewer()
      return
    let replayServerMode = replayServerModeEnabled()
    let replayRequest =
      if replayServerMode:
        request.replayRequestUriOrPending()
      else:
        (uri: "", loaded: false)
    if replayServerMode:
      if replayRequest.uri.len == 0 and not replayRequest.loaded:
        request.respondReplayRequestError(400, "missing replay uri\n")
        return
      if replayRequest.uri.len > 0 and not replayRequest.uri.readableReplayUri():
        request.respondReplayRequestError(404, "replay uri is not readable\n")
        return
    let websocket = request.upgradeToWebSocket()
    {.gcsafe.}:
      withLock appState.lock:
        websocket.registerGlobalWebSocket()
        if replayServerMode and replayRequest.uri.len > 0:
          appState.pendingReplayUri = replayRequest.uri
  elif request.path == AdminWebSocketPath and request.httpMethod == "GET" and
      request.isWebSocketUpgrade():
    if request.hasPlayerCredentialParams():
      request.respondForbiddenViewer()
      return
    let websocket = request.upgradeToWebSocket()
    {.gcsafe.}:
      withLock appState.lock:
        websocket.registerGlobalWebSocket()
  elif request.path == RewardWebSocketPath and request.httpMethod == "GET" and
      request.isWebSocketUpgrade():
    let websocket = request.upgradeToWebSocket()
    {.gcsafe.}:
      withLock appState.lock:
        websocket.registerRewardWebSocket()
  elif (request.path == ControlRestartPath or request.path == ControlKickPath) and
      request.httpMethod == "OPTIONS":
    request.respondControl(204, "")
  elif request.path == ControlRestartPath and request.httpMethod == "POST":
    if replayControlsDisabled():
      request.respondControl(409, "match controls are disabled for replays\n")
    else:
      {.gcsafe.}:
        withLock appState.lock:
          appState.resetRequested = true
      request.respondControl(202, "restart queued\n")
  elif request.path == ControlKickPath and request.httpMethod == "POST":
    if replayControlsDisabled():
      request.respondControl(409, "match controls are disabled for replays\n")
    else:
      let identity = request.queryParams.getOrDefault(
        "identity",
        ""
      ).cleanPlayerName()
      if identity.len == 0:
        request.respondControl(400, "missing identity\n")
      else:
        {.gcsafe.}:
          withLock appState.lock:
            appState.kickRequests.add(identity)
        request.respondControl(202, "kick queued\n")
  elif request.path in [
      bitworldClient.ReplayClientRoute,
      bitworldClient.CoworldReplayClientRoute
    ] and request.httpMethod == "GET":
    if replayServerModeEnabled():
      let replayRequest = request.replayRequestUriOrPending()
      if replayRequest.uri.len == 0 and not replayRequest.loaded:
        request.respondReplayRequestError(400, "missing replay uri\n")
        return
      if replayRequest.uri.len > 0 and not replayRequest.uri.readableReplayUri():
        request.respondReplayRequestError(404, "replay uri is not readable\n")
        return
      if replayRequest.uri.len > 0:
        {.gcsafe.}:
          withLock appState.lock:
            appState.pendingReplayUri = replayRequest.uri
    discard bitworldClient.serveClientFile(
      request,
      request.path,
      bitworldClient.GlobalClientRoute
    )
  elif bitworldClient.serveClientRoute(
    request,
    bitworldClient.GlobalClientRoute
  ):
    discard
  else:
    var headers: HttpHeaders
    headers["Content-Type"] = "text/plain"
    request.respond(200, headers, "Crewrift server")

proc websocketHandler(
  websocket: WebSocket,
  event: WebSocketEvent,
  message: Message
) =
  case event
  of OpenEvent:
    var closeKickedSocket = false
    {.gcsafe.}:
      withLock appState.lock:
        if websocket in appState.globalViewers or
            websocket in appState.rewardViewers:
          discard removePlayerWebSocketState(websocket)
        elif websocket.isPlayerWebSocket():
          let address = appState.playerAddresses.getOrDefault(websocket, "")
          if address.addressIsKicked():
            discard removePlayerWebSocketState(websocket)
            closeKickedSocket = true
          elif websocket notin appState.playerIndices:
            appState.playerIndices[websocket] =
              if appState.replayLoaded:
                -1
              else:
                0x7fffffff
            appState.inputMasks[websocket] = 0
            appState.inputPressedMasks[websocket] = 0
            appState.lastAppliedMasks[websocket] = 0
            appState.playerReady[websocket] = false
    if closeKickedSocket:
      websocket.disconnectWebSocket()
  of MessageEvent:
    if message.kind == Ping:
      websocket.send(message.data, Pong)
    elif message.kind == BinaryMessage:
      {.gcsafe.}:
        withLock appState.lock:
          if message.data.isPlayerReadyPacket() and
              websocket in appState.playerReady:
            appState.playerReady[websocket] = true
          elif websocket in appState.globalViewers:
            appState.globalViewers[websocket].applyGlobalViewerMessage(
              message.data
            )
          elif websocket in appState.playerViewers and
              not appState.replayLoaded:
            var
              mask = appState.inputMasks.getOrDefault(websocket, 0)
              pressedMask = appState.inputPressedMasks.getOrDefault(
                websocket,
                0
              )
              chatText = ""
              debugSprites: seq[uint8] = @[]
            appState.playerViewers[websocket].applyPlayerViewerMessage(
              message.data,
              mask,
              pressedMask,
              chatText,
              debugSprites
            )
            appState.inputMasks[websocket] = mask
            appState.inputPressedMasks[websocket] = pressedMask
            if chatText.len > 0:
              appState.chatMessages[websocket] = chatText
            if debugSprites.len > 0:
              var pendingDebugSprites = appState.debugSprites.getOrDefault(
                websocket,
                @[]
              )
              pendingDebugSprites.add(debugSprites)
              appState.debugSprites[websocket] = pendingDebugSprites
  of ErrorEvent, CloseEvent:
    var who = ""
    {.gcsafe.}:
      withLock appState.lock:
        let newlyClosed = markSocketClosed(websocket)
        if newlyClosed and websocket in appState.playerAddresses:
          who = appState.playerAddresses[websocket]
    if who.len > 0:
      echo "player disconnected: ", who

proc serverThreadProc(args: ServerThreadArgs) {.thread.} =
  args.server[].serve(Port(args.port), args.address)

proc resetPlayerReady(
  sockets: openArray[WebSocket],
  playerIndices: openArray[int],
  playerCount: int
) =
  ## Clears readiness for active player sockets before sending one frame.
  {.gcsafe.}:
    withLock appState.lock:
      for i, websocket in sockets:
        if i < playerIndices.len and playerIndices[i] >= 0 and
            playerIndices[i] < playerCount and
            websocket in appState.playerReady:
          appState.playerReady[websocket] = false

proc allPlayersReady(
  sockets: openArray[WebSocket],
  playerIndices: openArray[int],
  playerCount: int
): bool =
  ## Returns true when every active player socket sent ready.
  var activePlayers = 0
  {.gcsafe.}:
    withLock appState.lock:
      for i, websocket in sockets:
        if i >= playerIndices.len or playerIndices[i] < 0 or
            playerIndices[i] >= playerCount:
          continue
        inc activePlayers
        if not appState.playerReady.getOrDefault(websocket, false):
          return false
  activePlayers > 0

proc runFrameLimiter(
  previousTick: var MonoTime,
  fastMode: bool,
  sockets: openArray[WebSocket],
  playerIndices: openArray[int],
  playerCount: int
) =
  let frameDuration = initDuration(microseconds = 1_000_000 div TargetFps)
  while true:
    let elapsed = getMonoTime() - previousTick
    if elapsed >= frameDuration:
      break
    if fastMode and sockets.allPlayersReady(playerIndices, playerCount):
      break
    let remaining = frameDuration - elapsed
    sleep(max(1, min(2, int(remaining.inMilliseconds))))
  previousTick = getMonoTime()

proc rewardAccountFor(sim: SimServer, address: string): int =
  ## Returns the reward account index for one address.
  for i in 0 ..< sim.rewardAccounts.len:
    if sim.rewardAccounts[i].address == address:
      return i
  -1

proc writeInputMaskChange(
  replayWriter: var ReplayWriter,
  time: uint32,
  playerIndex: int,
  mask: uint8
) =
  ## Writes one replay input event when a player's applied mask changes.
  if playerIndex < 0 or playerIndex >= replayWriter.lastMasks.len:
    return
  if replayWriter.lastMasks[playerIndex] == mask:
    return
  replayWriter.writeInput(ReplayInput(
    time: time,
    player: uint8(playerIndex),
    keys: mask
  ))
  replayWriter.lastMasks[playerIndex] = mask

proc writeInputFrameMasks(
  replayWriter: var ReplayWriter,
  time: uint32,
  playerIndex: int,
  appliedMask,
  pressedMask: uint8
) =
  ## Writes replay input changes for one sampled player frame.
  if playerIndex < 0 or playerIndex >= replayWriter.lastMasks.len:
    return
  let repeatedPressedMask = pressedMask and replayWriter.lastMasks[playerIndex]
  if repeatedPressedMask != 0:
    replayWriter.writeInputMaskChange(
      time,
      playerIndex,
      replayWriter.lastMasks[playerIndex] and not repeatedPressedMask
    )
  replayWriter.writeInputMaskChange(time, playerIndex, appliedMask)

proc clearPressedInputMask(input: var InputState, mask: uint8) =
  ## Clears previous input bits that were pressed this frame.
  if (mask and ButtonUp) != 0:
    input.up = false
  if (mask and ButtonDown) != 0:
    input.down = false
  if (mask and ButtonLeft) != 0:
    input.left = false
  if (mask and ButtonRight) != 0:
    input.right = false
  if (mask and ButtonSelect) != 0:
    input.select = false
  if (mask and ButtonA) != 0:
    input.attack = false
  if (mask and ButtonB) != 0:
    input.b = false

proc clearPressedInputMasks(
  inputs: var seq[InputState],
  masks: openArray[uint8]
) =
  ## Clears previous input bits for each per-frame pressed mask.
  for playerIndex, mask in masks:
    if playerIndex < inputs.len:
      inputs[playerIndex].clearPressedInputMask(mask)

proc resetInputMasks(masks: var seq[uint8]) =
  ## Clears all per-frame pressed masks.
  for mask in masks.mitems:
    mask = 0

proc addStatLine(
  packet: var string,
  name, identity: string,
  value: int
) =
  ## Appends one metric line to a reward protocol packet.
  packet.add(name)
  packet.add(' ')
  packet.add(identity)
  packet.add(' ')
  packet.add($value)
  packet.add('\n')

proc buildRewardPacket(sim: SimServer): string {.measure.} =
  ## Builds one reward protocol packet for the current tick.
  for player in sim.players:
    let
      identity = player.address.rewardAddress()
      accountIndex = sim.rewardAccountFor(player.address)
    result.addStatLine("reward", identity, player.reward)
    if accountIndex >= 0:
      let account = sim.rewardAccounts[accountIndex]
      result.addStatLine("wins_imposter", identity, account.winsImposter)
      result.addStatLine("wins_crewmate", identity, account.winsCrewmate)
      result.addStatLine("games_imposter", identity, account.gamesImposter)
      result.addStatLine("games_crewmate", identity, account.gamesCrewmate)
      result.addStatLine("kills", identity, account.kills)
      result.addStatLine("tasks", identity, account.tasks)
      result.addStatLine("vote_players", identity, account.votePlayers)
      result.addStatLine("vote_skip", identity, account.voteSkip)
      result.addStatLine("vote_timeout", identity, account.voteTimeout)
      result.addStatLine("connect_timeout", identity, account.connectTimeout)
      result.addStatLine(
        "disconnect_timeout",
        identity,
        account.disconnectTimeout
      )

proc runServerLoop*(
  host = DefaultHost,
  port = DefaultPort,
  initialConfig = defaultGameConfig(),
  saveReplayPath = "",
  loadReplayPath = "",
  saveScoresPath = "",
  runtimeConfig = RuntimeConfig()
) =
  initAppState()
  if saveReplayPath.len > 0 and loadReplayPath.len > 0:
    raise newException(ReplayError, "Cannot save and load a replay together")
  var replayLoaded = loadReplayPath.len > 0
  var replayData =
    if replayLoaded:
      loadReplay(loadReplayPath)
    else:
      ReplayData()
  var config =
    if replayLoaded:
      replayData.replayGameConfig()
    else:
      initialConfig
  if not replayLoaded:
    config.resolveRandomSeed()
  var
    replayWriter = openReplayWriter(saveReplayPath, config.configJson())
    replayPlayer =
      if replayLoaded:
        initReplayPlayer(replayData)
      else:
        ReplayPlayer()
  replayPlayer.mismatchQuit = runtimeConfig.mismatchQuit
  startProfileTrace()
  defer:
    finishProfileTrace()
    replayWriter.closeReplayWriter()
  appState.replayLoaded = replayLoaded
  appState.replayServerMode = replayLoaded
  appState.config = config

  let httpServer = newServer(
    httpHandler,
    websocketHandler,
    workerThreads = 4
  )
  var
    serverThread: Thread[ServerThreadArgs]
    serverPtr = cast[ptr Server](unsafeAddr httpServer)
  createThread(
    serverThread,
    serverThreadProc,
    ServerThreadArgs(server: serverPtr, address: host, port: port)
  )
  httpServer.waitUntilReady()

  var
    sim = initSimServer(config)
    lastTick = getMonoTime()
    prevInputs: seq[InputState]
    liveSpeedIndex = config.liveSpeedIndex()
    gamesPlayed = 0
  if replayLoaded:
    replayPlayer.buildReplayKeyframes(sim)

  while true:
    var
      pendingReplayUri = ""
      sockets: seq[WebSocket] = @[]
      socketsToClose: seq[WebSocket] = @[]
      playerIndices: seq[int] = @[]
      inputs: seq[InputState]
      downInputs: seq[InputState]
      downInputMasks: seq[uint8]
      pressedInputMasks: seq[uint8]
      playerDebugSprites: seq[seq[uint8]]
      globalViewers: seq[WebSocket] = @[]
      globalStates: seq[GlobalViewerState] = @[]
      rewardViewers: seq[WebSocket] = @[]
      playerViewerStates: seq[PlayerViewerState] = @[]
      replayCommands: seq[char] = @[]
      replaySeekTicks: seq[int] = @[]
      shouldReset = false
      quitAfterFrame = false

    {.gcsafe.}:
      withLock appState.lock:
        pendingReplayUri = appState.pendingReplayUri
        appState.pendingReplayUri = ""
    if pendingReplayUri.len > 0:
      replayData = loadReplayUri(pendingReplayUri)
      config = replayData.replayGameConfig()
      sim = initSimServer(config)
      replayPlayer = initReplayPlayer(replayData)
      replayPlayer.mismatchQuit = runtimeConfig.mismatchQuit
      replayPlayer.buildReplayKeyframes(sim)
      replayLoaded = true
      {.gcsafe.}:
        withLock appState.lock:
          appState.replayLoaded = true
          appState.config = config

    {.gcsafe.}:
      withLock appState.lock:
        if not replayLoaded and appState.resetRequested:
          shouldReset = true
          appState.resetRequested = false
          appState.chatMessages.clear()
          appState.debugSprites.clear()
        for websocket in appState.closedSockets:
          if not replayLoaded and websocket in appState.playerIndices:
            let playerIndex = appState.playerIndices[websocket]
            if playerIndex >= 0 and playerIndex < sim.players.len:
              if sim.canGraceDisconnect(playerIndex):
                sim.markPlayerDisconnected(playerIndex)
                replayWriter.writeInputMaskChange(
                  tickTime(sim.tickCount),
                  playerIndex,
                  0
                )
                if playerIndex < prevInputs.len:
                  prevInputs[playerIndex] = InputState()
                discard removeWebSocketState(websocket)
                continue
              else:
                sim.recordGameAbandon(playerIndex)
                replayWriter.writeLeave(tickTime(sim.tickCount), playerIndex)
                if playerIndex < replayWriter.lastMasks.len:
                  replayWriter.lastMasks.delete(playerIndex)
                if playerIndex < prevInputs.len:
                  prevInputs.delete(playerIndex)
          sim.removePlayer(websocket)
        appState.closedSockets.setLen(0)
        if not replayLoaded and appState.kickRequests.len > 0:
          let requestedKicks = appState.kickRequests
          appState.kickRequests = @[]
          var socketsToKick: seq[WebSocket] = @[]
          for websocket, address in appState.playerAddresses.pairs:
            let identity = address.rewardAddress()
            for requestedIdentity in requestedKicks:
              if address == requestedIdentity or identity == requestedIdentity:
                appState.kickedIdentities[address] = true
                appState.kickedIdentities[identity] = true
                if websocket notin socketsToKick:
                  socketsToKick.add(websocket)
          for websocket in socketsToKick:
            if websocket in appState.playerIndices:
              let playerIndex = appState.playerIndices[websocket]
              if playerIndex >= 0 and playerIndex < sim.players.len:
                sim.recordGameAbandon(playerIndex)
                replayWriter.writeLeave(tickTime(sim.tickCount), playerIndex)
                if playerIndex < replayWriter.lastMasks.len:
                  replayWriter.lastMasks.delete(playerIndex)
                if playerIndex < prevInputs.len:
                  prevInputs.delete(playerIndex)
            sim.removePlayer(websocket)
            socketsToClose.add(websocket)
        if not replayLoaded and sim.shouldAbortFiniteMatch():
          if sim.phase == Lobby:
            raise newException(
              CrewriftError,
              "finite match roster dropped below minPlayers before roles were assigned"
            )
          sim.finishGame(Crewmate, timeLimitReached = true)
          gamesPlayed = max(gamesPlayed, config.maxGames)
        elif not replayLoaded and sim.phase != Lobby and sim.players.len == 0:
          sim.resetToLobby()
          prevInputs = @[]
          replayWriter.lastMasks = @[]

        if not replayLoaded:
          var newSockets: seq[WebSocket] = @[]
          for websocket in appState.playerIndices.keys:
            if websocket.isPlayerWebSocket() and
                appState.playerIndices[websocket] == 0x7fffffff:
              newSockets.add(websocket)
          var progressed = true
          while progressed:
            progressed = false
            var pendingPlayers: seq[PendingPlayerJoin] = @[]
            for websocket in newSockets:
              if websocket notin appState.playerIndices or
                  appState.playerIndices[websocket] != 0x7fffffff:
                continue
              let address = appState.playerAddresses.getOrDefault(
                websocket,
                "unknown"
              )
              let identity = address.rewardAddress()
              if address in appState.kickedIdentities or
                  identity in appState.kickedIdentities:
                sim.removePlayer(websocket)
                socketsToClose.add(websocket)
                continue
              let
                slot = appState.playerSlots.getOrDefault(websocket, -1)
                token = appState.playerTokens.getOrDefault(websocket, "")
                reconnectIndex = sim.reconnectPlayerIndex(
                  address,
                  token,
                  slot
                )
              if reconnectIndex >= 0:
                appState.playerIndices[websocket] = reconnectIndex
                appState.playerSlots[websocket] =
                  sim.players[reconnectIndex].joinOrder
                appState.inputMasks[websocket] = 0
                appState.inputPressedMasks[websocket] = 0
                appState.lastAppliedMasks[websocket] = 0
                sim.markPlayerConnected(reconnectIndex)
                progressed = true
                continue
              if sim.phase == Lobby and
                  (sim.canAddPlayer() or slot >= 0 or token.len > 0):
                try:
                  pendingPlayers.add(sim.pendingPlayerJoin(websocket))
                except CrewriftError:
                  sim.removePlayer(websocket)
                  socketsToClose.add(websocket)
              else:
                appState.playerIndices[websocket] = -1
            pendingPlayers.sort(comparePendingPlayerJoins)
            for join in pendingPlayers:
              # Admit any pending socket whose resolved slot is free. The slot is
              # decoupled from the players-seq index, so out-of-order/concurrent
              # connects no longer have to wait for strictly sequential slots,
              # which previously stranded validly-connected sockets in pending.
              try:
                appState.playerIndices[join.websocket] = sim.addPlayer(
                  join.address,
                  join.requestedSlot,
                  join.token
                )
              except CrewriftError:
                sim.removePlayer(join.websocket)
                socketsToClose.add(join.websocket)
                continue
              appState.playerSlots[join.websocket] =
                sim.players[appState.playerIndices[join.websocket]].joinOrder
              replayWriter.writeJoin(
                tickTime(sim.tickCount),
                appState.playerIndices[join.websocket],
                join.address,
                join.requestedSlot,
                join.token
              )
              while replayWriter.lastMasks.len < sim.players.len:
                replayWriter.lastMasks.add(0)
              progressed = true

        if not replayLoaded:
          # Compute the set of slots that currently have an accepted live
          # /player socket. This is the connect-timeout safety net: a slot in
          # this set is never declared connect_timeout, even if the socket has
          # not yet been promoted into sim.players on this exact tick.
          var connectedSlots: seq[int] = @[]
          for websocket, playerIndex in appState.playerIndices.pairs:
            if not websocket.isPlayerWebSocket():
              continue
            if playerIndex >= 0 and playerIndex < sim.players.len:
              let slot = sim.players[playerIndex].joinOrder
              if slot notin connectedSlots:
                connectedSlots.add(slot)
            elif playerIndex == 0x7fffffff:
              let requestedSlot =
                appState.playerSlots.getOrDefault(websocket, -1)
              if requestedSlot >= 0 and requestedSlot < MaxPlayers and
                  requestedSlot notin connectedSlots:
                connectedSlots.add(requestedSlot)
          sim.setLiveConnectedSlots(connectedSlots)

        if not replayLoaded:
          inputs = newSeq[InputState](sim.players.len)
          downInputs = newSeq[InputState](sim.players.len)
          downInputMasks = newSeq[uint8](sim.players.len)
          pressedInputMasks = newSeq[uint8](sim.players.len)
          playerDebugSprites = newSeq[seq[uint8]](sim.players.len)
        for websocket, playerIndex in appState.playerIndices.pairs:
          if not websocket.isPlayerWebSocket():
            continue
          sockets.add(websocket)
          playerIndices.add(playerIndex)
          playerViewerStates.add(appState.playerViewers[websocket])
          if replayLoaded:
            continue
          let pressedMask = appState.inputPressedMasks.getOrDefault(
            websocket,
            0
          )
          appState.inputPressedMasks[websocket] = 0
          if playerIndex < 0 or playerIndex >= inputs.len:
            continue
          let debugSprites = appState.debugSprites.getOrDefault(
            websocket,
            @[]
          )
          appState.debugSprites.del(websocket)
          if debugSprites.len > 0:
            playerDebugSprites[playerIndex] = debugSprites
            replayWriter.writeDebugSprite(
              tickTime(sim.tickCount),
              playerIndex,
              debugSprites
            )
          let currentMask = appState.inputMasks.getOrDefault(websocket, 0)
          let appliedMask = currentMask or pressedMask
          inputs[playerIndex] = decodeInputMask(appliedMask)
          downInputs[playerIndex] = decodeInputMask(currentMask)
          downInputMasks[playerIndex] = currentMask
          pressedInputMasks[playerIndex] = pressedMask
          replayWriter.writeInputFrameMasks(
            tickTime(sim.tickCount),
            playerIndex,
            appliedMask,
            pressedMask
          )
          appState.lastAppliedMasks[websocket] = appliedMask
        if not replayLoaded:
          for websocket, message in appState.chatMessages.pairs:
            let playerIndex = appState.playerIndices.getOrDefault(
              websocket,
              -1
            )
            if playerIndex >= 0:
              replayWriter.writeChat(
                tickTime(sim.tickCount),
                playerIndex,
                message
              )
            sim.addVotingChat(playerIndex, message)
          appState.chatMessages.clear()
        for websocket, state in appState.globalViewers.pairs:
          globalViewers.add(websocket)
          globalStates.add(state)
          if state.replaySeekTick >= 0:
            replaySeekTicks.add(state.replaySeekTick)
          for command in state.replayCommands:
            replayCommands.add(command)
          appState.globalViewers[websocket].clearGlobalMouseEdges()
          appState.globalViewers[websocket].replayCommands.setLen(0)
          appState.globalViewers[websocket].replaySeekTick = -1
        for websocket in appState.rewardViewers.keys:
          rewardViewers.add(websocket)

    for websocket in socketsToClose:
      websocket.disconnectWebSocket()

    if shouldReset:
      let rewardAccounts = sim.rewardAccounts
      inc config.seed
      sim = initSimServer(config)
      sim.rewardAccounts = rewardAccounts
      prevInputs = @[]
      replayWriter.lastMasks = @[]
      sockets.setLen(0)
      playerIndices.setLen(0)
      rewardViewers.setLen(0)
      playerViewerStates.setLen(0)
      {.gcsafe.}:
        withLock appState.lock:
          appState.kickedIdentities.clear()
          var reconnectSockets: seq[WebSocket] = @[]
          for websocket in appState.playerIndices.keys:
            if websocket.isPlayerWebSocket():
              reconnectSockets.add(websocket)
          for websocket in reconnectSockets:
            appState.playerIndices[websocket] = 0x7fffffff
          var progressed = true
          while progressed:
            progressed = false
            var pendingPlayers: seq[PendingPlayerJoin] = @[]
            for websocket in reconnectSockets:
              if websocket notin appState.playerIndices or
                  appState.playerIndices[websocket] != 0x7fffffff:
                continue
              let
                slot = appState.playerSlots.getOrDefault(websocket, -1)
                token = appState.playerTokens.getOrDefault(websocket, "")
              if not sim.canAddPlayer() and slot < 0 and token.len == 0:
                appState.playerIndices[websocket] = -1
                continue
              try:
                pendingPlayers.add(sim.pendingPlayerJoin(websocket))
              except CrewriftError:
                sim.removePlayer(websocket)
                socketsToClose.add(websocket)
            pendingPlayers.sort(comparePendingPlayerJoins)
            for join in pendingPlayers:
              # Admit any pending socket whose resolved slot is free; slots no
              # longer have to arrive in strict sequential order.
              try:
                appState.playerIndices[join.websocket] = sim.addPlayer(
                  join.address,
                  join.requestedSlot,
                  join.token
                )
              except CrewriftError:
                sim.removePlayer(join.websocket)
                socketsToClose.add(join.websocket)
                continue
              appState.playerSlots[join.websocket] =
                sim.players[appState.playerIndices[join.websocket]].joinOrder
              appState.inputMasks[join.websocket] = 0
              appState.inputPressedMasks[join.websocket] = 0
              appState.lastAppliedMasks[join.websocket] = 0
              appState.playerReady[join.websocket] = false
              sockets.add(join.websocket)
              playerIndices.add(appState.playerIndices[join.websocket])
              appState.playerViewers[join.websocket] =
                initPlayerViewerState()
              playerViewerStates.add(appState.playerViewers[join.websocket])
              progressed = true
          replayWriter.lastMasks.setLen(sim.players.len)
          for websocket in appState.rewardViewers.keys:
            rewardViewers.add(websocket)

      let rewardPacket = sim.buildRewardPacket()
      if config.fastMode:
        sockets.resetPlayerReady(playerIndices, sim.players.len)
      for i in 0 ..< sockets.len:
        var nextState: PlayerViewerState
        let framePacket = sim.buildSpriteProtocolPlayerUpdates(
          playerIndices[i],
          playerViewerStates[i],
          nextState
        )
        {.gcsafe.}:
          withLock appState.lock:
            if sockets[i] in appState.playerViewers:
              appState.playerViewers[sockets[i]] = nextState
        let frameBlob = blobFromBytes(framePacket)
        sockets[i].send(frameBlob, BinaryMessage)
      for websocket in rewardViewers:
        websocket.send(rewardPacket, TextMessage)
      runFrameLimiter(
        lastTick,
        config.fastMode,
        sockets,
        playerIndices,
        sim.players.len
      )
      continue

    if replayLoaded:
      for seekTick in replaySeekTicks:
        replayPlayer.applyReplaySeek(sim, seekTick)
      for command in replayCommands:
        replayPlayer.applyReplayCommand(sim, command)
      if replayPlayer.playing:
        for _ in 0 ..< replayPlayer.replaySpeed():
          if replayPlayer.playing:
            replayPlayer.stepReplay(sim)
        if replayPlayer.looping and not replayPlayer.playing:
          replayPlayer.seekReplay(sim, 0)
          replayPlayer.playing = true
    else:
      for command in replayCommands:
        liveSpeedIndex.applySpeedCommand(command)
      var
        stepPrevInputs = prevInputs
        stepInputs = inputs
        stepPressedInputMasks = pressedInputMasks
        lastStepInputs = prevInputs
      for _ in 0 ..< playbackSpeed(liveSpeedIndex):
        if config.finalGameQuitReady(
          gamesPlayed,
          sim.phase,
          sim.gameOverTimer
        ):
          quitAfterFrame = true
          break
        let phaseBeforeStep = sim.phase
        stepPrevInputs.clearPressedInputMasks(stepPressedInputMasks)
        sim.step(stepInputs, stepPrevInputs)
        lastStepInputs = stepInputs
        stepPrevInputs = stepInputs
        stepPressedInputMasks.resetInputMasks()
        replayWriter.writeHash(uint32(sim.tickCount), sim.gameHash())
        if stepInputs.len > 0 and stepInputs != downInputs:
          for playerIndex, mask in downInputMasks:
            replayWriter.writeInputMaskChange(
              tickTime(sim.tickCount),
              playerIndex,
              mask
            )
          stepInputs = downInputs
        if config.maxGames > 0 and phaseBeforeStep != GameOver and
            sim.phase == GameOver:
          inc gamesPlayed
        if sim.needsReregister:
          break
      prevInputs = lastStepInputs

    let rewardPacket = sim.buildRewardPacket()

    if not replayLoaded and sim.needsReregister:
      sim.needsReregister = false
      {.gcsafe.}:
        withLock appState.lock:
          for websocket in appState.playerIndices.keys:
            if websocket.isPlayerWebSocket():
              appState.playerIndices[websocket] = 0x7fffffff
          for websocket in appState.playerViewers.keys:
            appState.playerViewers[websocket] = initPlayerViewerState()

    if not replayLoaded and config.fastMode:
      sockets.resetPlayerReady(playerIndices, sim.players.len)

    for i in 0 ..< sockets.len:
      var nextState: PlayerViewerState
      let debugSprites =
        if replayLoaded and playerIndices[i] >= 0 and
            playerIndices[i] < replayPlayer.debugSprites.len:
          replayPlayer.debugSprites[playerIndices[i]]
        elif not replayLoaded and playerIndices[i] >= 0 and
            playerIndices[i] < playerDebugSprites.len:
          playerDebugSprites[playerIndices[i]]
        else:
          @[]
      let framePacket = sim.buildSpriteProtocolPlayerUpdates(
        playerIndices[i],
        playerViewerStates[i],
        nextState,
        debugSprites = debugSprites
      )
      {.gcsafe.}:
        withLock appState.lock:
          if sockets[i] in appState.playerViewers:
            appState.playerViewers[sockets[i]] = nextState
      let frameBlob = blobFromBytes(framePacket)
      try:
        sockets[i].send(frameBlob, BinaryMessage)
      except:
        {.gcsafe.}:
          withLock appState.lock:
            discard markSocketClosed(sockets[i])

    for websocket in rewardViewers:
      try:
        websocket.send(rewardPacket, TextMessage)
      except:
        {.gcsafe.}:
          withLock appState.lock:
            discard markSocketClosed(websocket)

    for i in 0 ..< globalViewers.len:
      var nextState: GlobalViewerState
      let packet = sim.buildSpriteProtocolUpdates(
        globalStates[i],
        nextState,
        if replayLoaded: sim.tickCount else: sim.gameTicksElapsed(),
        replayPlayer.playing,
        if replayLoaded: replayPlayer.replaySpeed()
        else: playbackSpeed(liveSpeedIndex),
        if replayLoaded: replayPlayer.replayMaxTick()
        else: liveProgressMaxTick(config),
        replayPlayer.looping,
        replayLoaded,
        if replayLoaded: replayPlayer.hashMismatchTick else: -1,
        if replayLoaded: replayPlayer.debugSprites else: @[]
      )
      if packet.len == 0:
        continue
      try:
        globalViewers[i].send(blobFromBytes(packet), BinaryMessage)
        {.gcsafe.}:
          withLock appState.lock:
            if globalViewers[i] in appState.globalViewers:
              nextState.mergeGlobalMouseEdges(
                appState.globalViewers[globalViewers[i]]
              )
              appState.globalViewers[globalViewers[i]] = nextState
      except:
        {.gcsafe.}:
          withLock appState.lock:
            discard markSocketClosed(globalViewers[i])

    if profileShouldDump(sim.gameTicksElapsed()):
      finishProfileTrace()

    if quitAfterFrame:
      if saveReplayPath.len > 0:
        echo "Writing replay file: ", saveReplayPath
      replayWriter.closeReplayWriter()
      if saveReplayPath.len > 0 and fileExists(saveReplayPath):
        echo "Replay written: ", saveReplayPath,
          " (", getFileSize(saveReplayPath), " bytes)"
        runtimeConfig.writeReplay(readFile(saveReplayPath))
      if runtimeConfig.resultsUri.len > 0:
        let scoresJson = sim.playerResultsJson() & "\n"
        runtimeConfig.writeResults(scoresJson)
      elif saveScoresPath.len > 0:
        writeFile(saveScoresPath, sim.playerResultsJson() & "\n")
        echo "Scores written: ", saveScoresPath,
          " (", getFileSize(saveScoresPath), " bytes)"
      httpServer.close()
      joinThread(serverThread)
      break

    runFrameLimiter(
      lastTick,
      not replayLoaded and config.fastMode,
      sockets,
      playerIndices,
      sim.players.len
    )
