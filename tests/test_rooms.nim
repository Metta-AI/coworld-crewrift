import
  crewrift/sim

proc testNearestRoomClampsNegativePoint() =
  ## Tests that negative sprite coordinates find the map-edge room.
  let rooms = [
    Room(name: "top left", x: 0, y: 0, w: 10, h: 10),
    Room(
      name: "bottom right",
      x: MapWidth - 10,
      y: MapHeight - 10,
      w: 10,
      h: 10
    )
  ]

  let room = nearestRoomAt(rooms, -32768, -32768)
  doAssert room.found
  doAssert room.inside
  doAssert room.name == "top left"

proc testNearestRoomClampsLargePoint() =
  ## Tests that large sprite coordinates find the map-edge room.
  let rooms = [
    Room(name: "top left", x: 0, y: 0, w: 10, h: 10),
    Room(
      name: "bottom right",
      x: MapWidth - 10,
      y: MapHeight - 10,
      w: 10,
      h: 10
    )
  ]

  let room = nearestRoomAt(rooms, 32767, 32767)
  doAssert room.found
  doAssert room.inside
  doAssert room.name == "bottom right"

proc testRoomDistanceHandlesHugePoints() =
  ## Tests that direct room distance calls are overflow safe.
  let room = Room(name: "top left", x: 0, y: 0, w: 10, h: 10)
  doAssert room.roomDistanceSquared(low(int), low(int)) == 0

testNearestRoomClampsNegativePoint()
testNearestRoomClampsLargePoint()
testRoomDistanceHandlesHugePoints()

