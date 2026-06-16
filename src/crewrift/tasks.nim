import
  std/[algorithm, random]

const
  NoDistance = high(int) div 4
  SpawnNode = 0
  TaskRouteMin* = 1450
  TaskRouteMax* = 1550
  TaskRouteGoal* = 1500
  TaskRouteRerolls = 10

type
  TaskRect* = object
    x*, y*, w*, h*: int

  TaskAssignment* = object
    taskIds*: seq[int]
    routeCost*: int

  TaskPoint = object
    x, y: int

  TaskInfo = object
    id: int
    node: int
    target: TaskPoint
    distance: int

  DistanceMatrix = object
    size: int
    distances: seq[int]

  Assignment = object
    tasks: seq[TaskInfo]
    routeCost: int

proc clamp(value, low, high: int): int =
  ## Returns one value clamped to an inclusive integer range.
  min(max(value, low), high)

proc mapIndex(width, x, y: int): int {.inline.} =
  ## Returns the flat walk-mask index for one map point.
  y * width + x

proc isInside(width, height, x, y: int): bool {.inline.} =
  ## Returns true when one map point is inside the map bounds.
  x >= 0 and y >= 0 and x < width and y < height

proc isWalkable(
  walkMask: openArray[bool],
  width, height, x, y: int
): bool =
  ## Returns true when one map point is inside bounds and walkable.
  if not isInside(width, height, x, y):
    return false
  walkMask[mapIndex(width, x, y)]

proc nearestWalkablePoint(
  walkMask: openArray[bool],
  width, height, x, y: int
): TaskPoint =
  ## Returns the nearest walkable point to one map coordinate.
  if walkMask.isWalkable(width, height, x, y):
    return TaskPoint(x: x, y: y)

  for radius in 1 .. max(width, height):
    for dy in -radius .. radius:
      for dx in -radius .. radius:
        if abs(dx) != radius and abs(dy) != radius:
          continue
        let
          px = x + dx
          py = y + dy
        if walkMask.isWalkable(width, height, px, py):
          return TaskPoint(x: px, y: py)

  TaskPoint(
    x: clamp(x, 0, width - 1),
    y: clamp(y, 0, height - 1)
  )

proc taskCenter(task: TaskRect): TaskPoint =
  ## Returns the center point of one task rectangle.
  TaskPoint(
    x: task.x + task.w div 2,
    y: task.y + task.h div 2
  )

proc targetIndexMap(
  points: openArray[TaskPoint],
  width, height: int
): tuple[index: seq[int], count: int] =
  ## Returns target map indices used to stop shortest-path search early.
  result.index = newSeq[int](width * height)
  for i in 0 ..< result.index.len:
    result.index[i] = -1
  for i, point in points:
    if not isInside(width, height, point.x, point.y):
      continue
    let index = mapIndex(width, point.x, point.y)
    if result.index[index] == -1:
      result.index[index] = i
      inc result.count

proc pointDistances(
  walkMask: openArray[bool],
  width, height: int,
  start: TaskPoint,
  points: openArray[TaskPoint],
  targets: openArray[int],
  targetCount: int,
  costs, seen, queue: var seq[int],
  stamp: int
): seq[int] =
  ## Returns exact grid distances from one point to target points.
  var
    head = 0
    tail = 0
    found = 0

  if not isInside(width, height, start.x, start.y):
    result = newSeq[int](points.len)
    for distance in result.mitems:
      distance = NoDistance
    return

  let startIndex = mapIndex(width, start.x, start.y)
  costs[startIndex] = 0
  seen[startIndex] = stamp
  queue[tail] = startIndex
  inc tail

  template addNeighbor(index: int, nextCost: int) =
    if seen[index] != stamp and walkMask[index]:
      seen[index] = stamp
      costs[index] = nextCost
      queue[tail] = index
      inc tail

  while head < tail:
    let current = queue[head]
    inc head
    if targets[current] >= 0:
      inc found
      if found >= targetCount:
        break

    let
      x = current mod width
      y = current div width
      nextCost = costs[current] + 1
    if y > 0:
      addNeighbor(current - width, nextCost)
    if y + 1 < height:
      addNeighbor(current + width, nextCost)
    if x > 0:
      addNeighbor(current - 1, nextCost)
    if x + 1 < width:
      addNeighbor(current + 1, nextCost)

  result = newSeq[int](points.len)
  for i, point in points:
    if not isInside(width, height, point.x, point.y):
      result[i] = NoDistance
      continue
    let index = mapIndex(width, point.x, point.y)
    result[i] =
      if seen[index] == stamp:
        costs[index]
      else:
        NoDistance

proc distanceIndex(matrix: DistanceMatrix, a, b: int): int =
  ## Returns the flat matrix index for two node ids.
  a * matrix.size + b

proc distance(matrix: DistanceMatrix, a, b: int): int =
  ## Returns the cached route distance between two node ids.
  matrix.distances[matrix.distanceIndex(a, b)]

proc taskInfos(
  tasks: openArray[TaskRect],
  walkMask: openArray[bool],
  width, height: int
): seq[TaskInfo] =
  ## Builds task records with walkable target points.
  for i, task in tasks:
    let
      center = task.taskCenter()
      target = walkMask.nearestWalkablePoint(
        width,
        height,
        center.x,
        center.y
      )
    result.add TaskInfo(
      id: i,
      node: i + 1,
      target: target,
      distance: NoDistance
    )

proc buildDistanceMatrix(
  tasks: openArray[TaskInfo],
  walkMask: openArray[bool],
  width, height: int,
  start: TaskPoint
): DistanceMatrix =
  ## Computes exact spawn distances and approximate task-pair distances.
  result.size = tasks.len + 1
  result.distances = newSeq[int](result.size * result.size)

  var points = @[start]
  for task in tasks:
    points.add task.target

  let targets = targetIndexMap(points, width, height)
  var
    costs = newSeq[int](width * height)
    seen = newSeq[int](width * height)
    queue = newSeq[int](width * height)
  let spawnDistances = pointDistances(
    walkMask,
    width,
    height,
    start,
    points,
    targets.index,
    targets.count,
    costs,
    seen,
    queue,
    1
  )

  for i in 0 ..< result.size:
    for j in 0 ..< result.size:
      result.distances[result.distanceIndex(i, j)] =
        if i == j:
          0
        elif i == SpawnNode:
          spawnDistances[j]
        elif j == SpawnNode:
          spawnDistances[i]
        else:
          abs(points[i].x - points[j].x) + abs(points[i].y - points[j].y)

proc applySpawnDistances(
  tasks: var seq[TaskInfo],
  matrix: DistanceMatrix
) =
  ## Copies spawn distances into tasks and sorts farthest tasks first.
  for task in tasks.mitems:
    task.distance = matrix.distance(SpawnNode, task.node)
  tasks.sort(
    proc(a, b: TaskInfo): int =
      result = cmp(b.distance, a.distance)
      if result == 0:
        result = cmp(a.id, b.id)
  )

proc routeCost(
  matrix: DistanceMatrix,
  tasks: openArray[TaskInfo]
): int =
  ## Returns the approximate greedy route cost for one task list.
  if tasks.len == 0:
    return 0

  var
    used = newSeq[bool](tasks.len)
    currentNode = SpawnNode
  for _ in 0 ..< tasks.len:
    var
      best = -1
      bestDistance = NoDistance
    for i, task in tasks:
      if used[i]:
        continue
      let distance = matrix.distance(currentNode, task.node)
      if best < 0 or distance < bestDistance or
          (distance == bestDistance and task.id < tasks[best].id):
        best = i
        bestDistance = distance
    doAssert best >= 0
    used[best] = true
    result += bestDistance
    currentNode = tasks[best].node

proc isBetterCandidate(candidate, current: Assignment): bool =
  ## Returns true when one candidate is closer to the target route length.
  abs(candidate.routeCost - TaskRouteGoal) <
    abs(current.routeCost - TaskRouteGoal)

proc drawUniqueTasks(
  tasks: openArray[TaskInfo],
  tasksPerCrew: int,
  rng: var Rand
): seq[TaskInfo] =
  ## Draws one random task list without duplicates.
  doAssert tasks.len > 0
  doAssert tasksPerCrew <= tasks.len
  var pool = @tasks
  for i in 0 ..< tasksPerCrew:
    let j = i + rng.rand(pool.high - i)
    swap(pool[i], pool[j])
    result.add pool[i]

proc randomAssignment(
  tasks: openArray[TaskInfo],
  matrix: DistanceMatrix,
  tasksPerCrew: int,
  rng: var Rand
): Assignment =
  ## Draws one random assignment and computes its route.
  result.tasks = drawUniqueTasks(tasks, tasksPerCrew, rng)
  result.routeCost = matrix.routeCost(result.tasks)

proc assignRouteBand(
  tasks: openArray[TaskInfo],
  matrix: DistanceMatrix,
  tasksPerCrew: int,
  rng: var Rand
): Assignment =
  ## Rerolls until one crew route lands in the target path band.
  result = randomAssignment(tasks, matrix, tasksPerCrew, rng)
  for _ in 0 ..< TaskRouteRerolls:
    if result.routeCost >= TaskRouteMin and result.routeCost <= TaskRouteMax:
      return
    let candidate = randomAssignment(tasks, matrix, tasksPerCrew, rng)
    if candidate.routeCost >= TaskRouteMin and
        candidate.routeCost <= TaskRouteMax:
      return candidate
    if candidate.isBetterCandidate(result):
      result = candidate

proc assignRandomRoutes(
  tasks: openArray[TaskInfo],
  matrix: DistanceMatrix,
  crewCount, tasksPerCrew: int,
  rng: var Rand
): seq[Assignment] =
  ## Assigns each crew member by rerolling an independent random route.
  result = newSeq[Assignment](crewCount)
  for i in 0 ..< crewCount:
    result[i] = assignRouteBand(tasks, matrix, tasksPerCrew, rng)

proc assignmentIds(assignment: Assignment): seq[int] =
  ## Returns task ids assigned to one crew member.
  for task in assignment.tasks:
    result.add task.id

proc taskAssignment(assignment: Assignment): TaskAssignment =
  ## Returns exported assignment details for one crew member.
  TaskAssignment(
    taskIds: assignment.assignmentIds(),
    routeCost: assignment.routeCost
  )

proc assignTaskDetails*(
  tasks: openArray[TaskRect],
  walkMask: openArray[bool],
  width, height: int,
  homeX, homeY: int,
  crewCount, tasksPerCrew: int,
  rng: var Rand
): seq[TaskAssignment] =
  ## Assigns balanced task details to crewmates using loaded map data.
  doAssert width > 0
  doAssert height > 0
  doAssert walkMask.len >= width * height
  doAssert crewCount >= 0
  doAssert tasksPerCrew >= 0

  result = newSeq[TaskAssignment](crewCount)
  if crewCount == 0 or tasksPerCrew == 0 or tasks.len == 0:
    return
  doAssert tasksPerCrew <= tasks.len

  let start = walkMask.nearestWalkablePoint(width, height, homeX, homeY)
  var infos = taskInfos(tasks, walkMask, width, height)
  let matrix = buildDistanceMatrix(infos, walkMask, width, height, start)
  infos.applySpawnDistances(matrix)

  let assignments = assignRandomRoutes(
    infos,
    matrix,
    crewCount,
    tasksPerCrew,
    rng
  )
  for i, assignment in assignments:
    result[i] = assignment.taskAssignment()

proc assignTasks*(
  tasks: openArray[TaskRect],
  walkMask: openArray[bool],
  width, height: int,
  homeX, homeY: int,
  crewCount, tasksPerCrew: int,
  rng: var Rand
): seq[seq[int]] =
  ## Assigns balanced task ids to crewmates using loaded map data.
  let assignments = assignTaskDetails(
    tasks,
    walkMask,
    width,
    height,
    homeX,
    homeY,
    crewCount,
    tasksPerCrew,
    rng
  )
  for assignment in assignments:
    result.add assignment.taskIds
