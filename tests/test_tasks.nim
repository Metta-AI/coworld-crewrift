import
  std/[os, random, unittest],
  crewrift/sim,
  crewrift/tasks as taskAssignments

const
  GameDir = currentSourcePath.parentDir.parentDir
  TestWidth = 12
  TestHeight = 8

proc openWalkMask(): seq[bool] =
  ## Returns a fully walkable test map.
  result = newSeq[bool](TestWidth * TestHeight)
  for cell in result.mitems:
    cell = true

proc hasUniqueTasks(tasks: openArray[int]): bool =
  ## Returns true when one assigned task list has no duplicates.
  for i in 0 ..< tasks.len:
    for j in i + 1 ..< tasks.len:
      if tasks[i] == tasks[j]:
        return false
  true

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

proc taskRects(sim: SimServer): seq[taskAssignments.TaskRect] =
  ## Returns task rectangles for the loaded map.
  for task in sim.tasks:
    result.add taskAssignments.TaskRect(
      x: task.x,
      y: task.y,
      w: task.w,
      h: task.h
    )

suite "task assignment":
  test "draws unique task lists for each crew":
    let taskRects = [
      taskAssignments.TaskRect(x: 1, y: 1, w: 1, h: 1),
      taskAssignments.TaskRect(x: 3, y: 1, w: 1, h: 1),
      taskAssignments.TaskRect(x: 5, y: 1, w: 1, h: 1),
      taskAssignments.TaskRect(x: 1, y: 5, w: 1, h: 1),
      taskAssignments.TaskRect(x: 3, y: 5, w: 1, h: 1),
      taskAssignments.TaskRect(x: 5, y: 5, w: 1, h: 1)
    ]
    var rng = initRand(123)
    let assignments = taskAssignments.assignTasks(
      taskRects,
      openWalkMask(),
      TestWidth,
      TestHeight,
      0,
      0,
      2,
      3,
      rng
    )

    check assignments.len == 2
    for assignment in assignments:
      check assignment.len == 3
      check assignment.hasUniqueTasks()

  test "allows cross crew repeats without same crew duplicates":
    let taskRects = [
      taskAssignments.TaskRect(x: 1, y: 1, w: 1, h: 1),
      taskAssignments.TaskRect(x: 4, y: 1, w: 1, h: 1),
      taskAssignments.TaskRect(x: 8, y: 1, w: 1, h: 1)
    ]
    var rng = initRand(456)
    let assignments = taskAssignments.assignTasks(
      taskRects,
      openWalkMask(),
      TestWidth,
      TestHeight,
      0,
      0,
      2,
      2,
      rng
    )

    check assignments.len == 2
    var seen = newSeq[int](taskRects.len)
    for assignment in assignments:
      check assignment.len == 2
      check assignment.hasUniqueTasks()
      for task in assignment:
        inc seen[task]

    var total = 0
    for count in seen:
      total += count
      check count <= 2
    check total == 4

  test "assignment details include route distance":
    let taskRects = [
      taskAssignments.TaskRect(x: 1, y: 1, w: 1, h: 1),
      taskAssignments.TaskRect(x: 4, y: 1, w: 1, h: 1),
      taskAssignments.TaskRect(x: 8, y: 1, w: 1, h: 1),
      taskAssignments.TaskRect(x: 8, y: 5, w: 1, h: 1)
    ]
    var rng = initRand(789)
    let assignments = taskAssignments.assignTaskDetails(
      taskRects,
      openWalkMask(),
      TestWidth,
      TestHeight,
      0,
      0,
      2,
      2,
      rng
    )

    check assignments.len == 2
    for assignment in assignments:
      check assignment.taskIds.len == 2
      check assignment.taskIds.hasUniqueTasks()
      check assignment.routeCost > 0

  test "production task routes reroll without duplicates":
    let sim = initCrewriftForTest(defaultGameConfig())
    for seed in 0 ..< 10:
      var rng = initRand(seed)
      let assignments = taskAssignments.assignTaskDetails(
        sim.taskRects(),
        sim.walkMask,
        MapWidth,
        MapHeight,
        sim.gameMap.home.x,
        sim.gameMap.home.y,
        6,
        8,
        rng
      )

      check assignments.len == 6
      for assignment in assignments:
        check assignment.taskIds.len == 8
        check assignment.taskIds.hasUniqueTasks()
        check assignment.routeCost > 0

  test "start game assigns balanced lists to crewmates":
    var config = defaultGameConfig()
    config.minPlayers = 8
    config.imposterCount = 2
    config.autoImposterCount = false
    config.roleRevealTicks = 0
    config.tasksPerPlayer = 8

    var sim = initCrewriftForTest(config)
    sim.addPlayers(8)
    sim.startGame()

    var
      crewCount = 0
      imposterCount = 0
    for player in sim.players:
      case player.role
      of Crewmate:
        inc crewCount
        check player.assignedTasks.len == config.tasksPerPlayer
        check player.assignedTasks.hasUniqueTasks()
      of Imposter:
        inc imposterCount
        check player.assignedTasks.len == 0

    check crewCount == 6
    check imposterCount == 2
