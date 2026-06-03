import
  std/[algorithm, tables],
  ../../../src/crewrift/[replays, sim]

type
  PlayerRef* = object
    ## Stable player reference stored in timeline events.
    joinOrder*: int
    slot*: int

  PlayerIdentity* = object
    slot*: int
    name*: string
    address*: string
    color*: uint8
    role*: PlayerRole
    joinOrder*: int

  JoinIdentity = object
    name: string
    address: string
    slot: int

  IdentityTable* = object
    joinsByOrder: Table[int, JoinIdentity]
    identitiesByJoinOrder: Table[int, PlayerIdentity]
    refsByColor: Table[uint8, PlayerRef]
    identitiesList: seq[PlayerIdentity]
    warnings*: seq[string]
    rolesObserved*: bool

const InvalidPlayerRef* = PlayerRef(joinOrder: -1, slot: -1)

proc isValid*(player: PlayerRef): bool =
  player.joinOrder >= 0

proc playerRefForJoinOrder*(joinOrder: int): PlayerRef =
  PlayerRef(joinOrder: joinOrder, slot: joinOrder)

proc playerRefForPlayer*(player: Player): PlayerRef =
  playerRefForJoinOrder(player.joinOrder)

proc initIdentityTable*(data: ReplayData): IdentityTable =
  for join in data.joins:
    let order =
      if join.slot >= 0:
        join.slot
      else:
        int(join.player)
    result.joinsByOrder[order] = JoinIdentity(
      name: join.name,
      address: join.address,
      slot: order
    )

proc addWarning*(ids: var IdentityTable, message: string) =
  ids.warnings.add(message)

proc observeGameStart*(ids: var IdentityTable, sim: SimServer) =
  ## Captures the stable identity table after roles and colors are assigned.
  ids.identitiesByJoinOrder.clear()
  ids.refsByColor.clear()
  ids.identitiesList.setLen(0)

  for player in sim.players:
    let
      order = player.joinOrder
      stableRef = playerRefForJoinOrder(order)
    var
      name = player.address
      address = player.address
      slot = order
    if ids.joinsByOrder.hasKey(order):
      let join = ids.joinsByOrder[order]
      if join.name.len > 0:
        name = join.name
      if join.address.len > 0:
        address = join.address
      slot = join.slot
    let identity = PlayerIdentity(
      slot: slot,
      name: name,
      address: address,
      color: player.color,
      role: player.role,
      joinOrder: order
    )
    ids.identitiesByJoinOrder[order] = identity
    ids.refsByColor[player.color] = stableRef
    ids.identitiesList.add(identity)

  ids.identitiesList.sort(proc(a, b: PlayerIdentity): int =
    cmp(a.slot, b.slot)
  )
  ids.rolesObserved = true

proc identities*(ids: IdentityTable): seq[PlayerIdentity] =
  ids.identitiesList

proc hasIdentity*(ids: IdentityTable, player: PlayerRef): bool =
  ids.identitiesByJoinOrder.hasKey(player.joinOrder)

proc identity*(ids: IdentityTable, player: PlayerRef): PlayerIdentity =
  if ids.identitiesByJoinOrder.hasKey(player.joinOrder):
    return ids.identitiesByJoinOrder[player.joinOrder]
  PlayerIdentity(
    slot: player.slot,
    name: "slot " & $player.slot,
    address: "",
    color: 255'u8,
    role: Crewmate,
    joinOrder: player.joinOrder
  )

proc byColor*(ids: IdentityTable, color: uint8): PlayerRef =
  if ids.refsByColor.hasKey(color):
    return ids.refsByColor[color]
  InvalidPlayerRef

proc byJoinOrder*(ids: IdentityTable, joinOrder: int): PlayerRef =
  if ids.identitiesByJoinOrder.hasKey(joinOrder):
    return playerRefForJoinOrder(joinOrder)
  InvalidPlayerRef

proc refForLiveIndex*(sim: SimServer, index: int): PlayerRef =
  if index < 0 or index >= sim.players.len:
    return InvalidPlayerRef
  playerRefForPlayer(sim.players[index])

proc liveIndexForRef*(sim: SimServer, player: PlayerRef): int =
  for i in 0 ..< sim.players.len:
    if sim.players[i].joinOrder == player.joinOrder:
      return i
  -1
