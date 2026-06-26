"""Navigation graph + route planning (design §6, §9).

Built **once per episode** from the decoded pixel walkability mask and the baked
static map. Crewrift collides the player as a **1×1 point** (``sim.nim``
``CollisionW=CollisionH=1``), so *every walkable pixel is a legal agent position*.
The grid is therefore coarsened only to keep A* fast on the full ~1235×659 map;
correctness is enforced at **pixel resolution**, not at the coarse approximation:

- **Nodes** are ``cell_size``-pixel cells that contain at least one walkable pixel.
  A node's *point* is the walkable pixel nearest the cell center, so a cell that is
  mostly wall but clips a corridor still becomes a routable node (this is what the
  old conservative "all pixels walkable" rule threw away — and why tasks tucked
  against a wall used to look unreachable).
- **Edges** connect 8-neighbour nodes whose connecting **pixel segment is fully
  walkable** (with no diagonal corner squeeze). Because edges are validated on the
  real mask, both A* and the line-of-sight smoother are sound by construction.
- A flood from ``home`` (the spawn) marks the **reachable** component; planning
  snaps start and goal into it.
- **Destination anchors:** for every baked task / vent / button we precompute the
  reachable pixel that satisfies its interaction condition, so navigation targets a
  known-good point instead of a rect center that may sit in a wall. A destination
  with no reachable anchor is logged at build — surfaced on frame 1 instead of as a
  silent mid-game stall.

``plan_route`` runs A* over the node graph, then string-pulls the result with a
pixel-resolution line-of-sight pass. The action layer (:mod:`.action`) follows the
returned waypoints; this module never touches transport.
"""

from __future__ import annotations

import heapq
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import numpy as np

if TYPE_CHECKING:
    from players.crewrift.crewborg.map.types import MapData

_log = logging.getLogger(__name__)

DEFAULT_CELL_SIZE = 8

# Config-space margin (world px): routes keep this much clearance from walls so the
# bang-bang controller's axis-aligned staircase + momentum drift don't graze walls
# (the cause of task-approach wedging). Reachability/connectivity are unaffected —
# clearance only steers node placement, the clear-shot short-circuit, and route
# string-pulling; A* edges and the spawn flood still use the true walkability mask,
# so tight passages and wall-adjacent destinations stay reachable.
CLEARANCE_RADIUS = 2

# Interaction reach for anchor placement (world pixels, from sim.nim). A vent fires
# within VentRange=16px of the vent center; tasks/button fire from inside the rect.
VENT_REACH = 16

# Cost (graph units, i.e. world pixels) charged for traversing a vent teleport edge.
# A vent use is ~one action regardless of how far the teleport jumps, so the cost is
# a small fixed value — A* then strongly prefers a vent whenever it shortcuts a long
# walk, which is exactly what makes a fleeing imposter vanish through the nearest
# useful vent (these edges are imposter-only; ``plan_route`` never traverses them).
VENT_EDGE_COST = float(DEFAULT_CELL_SIZE)

# 8-neighbour offsets used to build edges. Only the four "forward" directions are
# enumerated; each discovered edge is added symmetrically.
_FORWARD = [(0, 1), (1, 0), (1, 1), (1, -1)]
_DIAGONALS = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
_ORTHOGONALS = [(-1, 0), (1, 0), (0, -1), (0, 1)]
_SNAP_RADIUS_CELLS = 48  # how far to search for a node when snapping a world point

Cell = tuple[int, int]  # (row, col)
Point = tuple[int, int]  # (x, y) world pixel


@dataclass(frozen=True)
class VentEdge:
    """A one-way teleport edge between two same-group vents (imposter-only).

    Standing within VentRange of the vent at ``from_vent`` and pressing B teleports
    to the vent at ``to_vent`` (``sim.nim`` vent groups). ``from_anchor`` /
    ``to_anchor`` are the reachable pixels the route walks onto either side of the
    hop; ``from_cell`` / ``to_cell`` are their graph cells (the A* endpoints).
    """

    from_vent: int
    to_vent: int
    from_cell: Cell
    to_cell: Cell
    from_anchor: Point
    to_anchor: Point
    cost: float


@dataclass
class NavGraph:
    """A coarse navigation graph over the pixel walkability mask (design §6)."""

    walkability: np.ndarray  # bool, shape (height, width); True == walkable pixel
    cell_size: int
    rows: int
    cols: int
    node_point: dict[Cell, Point]  # cell -> its representative walkable pixel
    adjacency: dict[Cell, list[tuple[Cell, float]]]  # cell -> [(neighbour, cost)]
    reachable: set[Cell]  # nodes connected to home over the graph
    # Walkable pixels that also keep CLEARANCE_RADIUS clear of walls (the eroded
    # mask). Routes prefer these so imperfect control doesn't graze walls; falls
    # back to ``walkability`` if unset.
    clearance: np.ndarray | None = None
    task_anchors: dict[int, Point] = field(default_factory=dict)
    vent_anchors: dict[int, Point] = field(default_factory=dict)
    # Teleport edges between same-group vents, keyed by the entry vent's anchor cell.
    # Consulted only by ``plan_route_via_vents`` (imposter flee), never by walking
    # routes — so crewmate pathing is unaffected by their presence.
    vent_edges: dict[Cell, list[VentEdge]] = field(default_factory=dict)
    button_anchor: Point | None = None
    unreachable: tuple[str, ...] = ()

    @property
    def map_height(self) -> int:
        return int(self.walkability.shape[0])

    @property
    def map_width(self) -> int:
        return int(self.walkability.shape[1])

    def world_to_cell(self, x: int, y: int) -> Cell:
        col = min(max(x // self.cell_size, 0), self.cols - 1)
        row = min(max(y // self.cell_size, 0), self.rows - 1)
        return row, col

    def task_anchor(self, index: int) -> Point | None:
        return self.task_anchors.get(index)

    def vent_anchor(self, index: int) -> Point | None:
        return self.vent_anchors.get(index)

    def nearest_reachable_node(self, x: int, y: int) -> Cell | None:
        """Snap a world point to the nearest reachable node cell, or ``None``."""

        row, col = self.world_to_cell(x, y)
        return _spiral_nearest(lambda r, c: (r, c) in self.reachable, row, col, _SNAP_RADIUS_CELLS)


# --------------------------------------------------------------------------- #
# Pixel-level primitives                                                      #
# --------------------------------------------------------------------------- #


def _pixel_walkable(walkability: np.ndarray, x: int, y: int) -> bool:
    return 0 <= y < walkability.shape[0] and 0 <= x < walkability.shape[1] and bool(walkability[y, x])


def _segment_clear(walkability: np.ndarray, a: Point, b: Point) -> bool:
    """True iff the straight segment ``a→b`` stays on walkable pixels.

    An Amanatides–Woo grid DDA at pixel resolution walks every pixel the segment
    passes through; all must be walkable. A segment crossing a pixel corner exactly
    requires both flanking pixels walkable — the no-corner-cutting rule, so a leg
    can never squeeze diagonally between two blocked pixels.
    """

    # Sample from pixel centers so a corner crossing is detected symmetrically.
    ax, ay = a[0] + 0.5, a[1] + 0.5
    bx, by = b[0] + 0.5, b[1] + 0.5
    col, row = a[0], a[1]
    end_col, end_row = b[0], b[1]
    dx, dy = bx - ax, by - ay

    step_x = 1 if dx > 0 else -1 if dx < 0 else 0
    step_y = 1 if dy > 0 else -1 if dy < 0 else 0
    t_max_x = ((col + (1 if step_x > 0 else 0)) - ax) / dx if step_x else math.inf
    t_max_y = ((row + (1 if step_y > 0 else 0)) - ay) / dy if step_y else math.inf
    t_delta_x = abs(1.0 / dx) if dx else math.inf
    t_delta_y = abs(1.0 / dy) if dy else math.inf

    if not _pixel_walkable(walkability, col, row):
        return False
    budget = abs(end_col - col) + abs(end_row - row) + 4  # guard against float drift
    while (col, row) != (end_col, end_row):
        budget -= 1
        if budget < 0:
            return False
        if abs(t_max_x - t_max_y) < 1e-9:  # corner crossing: no diagonal squeeze
            if not _pixel_walkable(walkability, col + step_x, row):
                return False
            if not _pixel_walkable(walkability, col, row + step_y):
                return False
            col += step_x
            row += step_y
            t_max_x += t_delta_x
            t_max_y += t_delta_y
        elif t_max_x < t_max_y:
            col += step_x
            t_max_x += t_delta_x
        else:
            row += step_y
            t_max_y += t_delta_y
        if not _pixel_walkable(walkability, col, row):
            return False
    return True


def _cell_node_point(
    reachable: np.ndarray, clearance: np.ndarray, row: int, col: int, cell_size: int
) -> Point | None:
    """The cell's representative pixel: nearest-center reachable pixel, preferring one
    that also keeps clearance. Returns the nearest clearance-keeping reachable pixel if
    any, else the nearest reachable pixel, else ``None`` (no reachable pixel in cell)."""

    y0, x0 = row * cell_size, col * cell_size
    yc, xc = y0 + cell_size // 2, x0 + cell_size // 2
    best: Point | None = None
    best_d: int | None = None
    best_clear: Point | None = None
    best_clear_d: int | None = None
    for py in range(y0, y0 + cell_size):
        for px in range(x0, x0 + cell_size):
            if not reachable[py, px]:
                continue
            d = (px - xc) ** 2 + (py - yc) ** 2
            if best_d is None or d < best_d:
                best_d, best = d, (px, py)
            if clearance[py, px] and (best_clear_d is None or d < best_clear_d):
                best_clear_d, best_clear = d, (px, py)
    return best_clear if best_clear is not None else best


def _nearest_walkable_pixel(walkability: np.ndarray, x: int, y: int, max_radius: int) -> Point | None:
    """The walkable pixel nearest ``(x, y)`` within ``max_radius`` (expanding rings)."""

    if _pixel_walkable(walkability, x, y):
        return x, y
    for radius in range(1, max_radius + 1):
        best: Point | None = None
        best_d: int | None = None
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                if _pixel_walkable(walkability, x + dx, y + dy):
                    d = dx * dx + dy * dy
                    if best_d is None or d < best_d:
                        best_d, best = d, (x + dx, y + dy)
        if best is not None:
            return best
    return None


def _clearance_mask(walkability: np.ndarray, radius: int) -> np.ndarray:
    """Pixels whose full ``(2·radius+1)²`` box is walkable — i.e. ``radius`` px clear of
    any wall. Map-edge pixels (box extends out of bounds) count as non-clear. Computed
    once at build time by ANDing the mask against its shifts (a box erosion)."""

    if radius <= 0:
        return walkability
    height, width = walkability.shape
    out = walkability.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            shifted = np.zeros_like(walkability)
            ys0, ys1 = max(0, dy), height + min(0, dy)
            xs0, xs1 = max(0, dx), width + min(0, dx)
            yd0, yd1 = max(0, -dy), height + min(0, -dy)
            xd0, xd1 = max(0, -dx), width + min(0, -dx)
            shifted[yd0:yd1, xd0:xd1] = walkability[ys0:ys1, xs0:xs1]
            out &= shifted
    return out


def _flood_reachable_pixels(walkability: np.ndarray, seed: Point) -> np.ndarray:
    """Bool mask of pixels reachable from ``seed`` over walkable pixels.

    8-connected; a diagonal step is allowed only when at least one of the two
    orthogonal pixels is walkable — the agent slides around a convex corner but
    cannot squeeze through a fully blocked diagonal pinch (sim.nim 1×1 collision +
    per-axis ``canOccupy``-or-slide stepping).
    """

    height, width = walkability.shape
    reachable = np.zeros((height, width), dtype=bool)
    start = _nearest_walkable_pixel(walkability, seed[0], seed[1], max_radius=64)
    if start is None:
        return reachable
    sx, sy = start
    reachable[sy, sx] = True
    queue = deque([(sx, sy)])
    while queue:
        x, y = queue.popleft()
        for dx, dy in _ORTHOGONALS:
            nx, ny = x + dx, y + dy
            if 0 <= ny < height and 0 <= nx < width and walkability[ny, nx] and not reachable[ny, nx]:
                reachable[ny, nx] = True
                queue.append((nx, ny))
        for dx, dy in _DIAGONALS:
            nx, ny = x + dx, y + dy
            if not (0 <= ny < height and 0 <= nx < width) or not walkability[ny, nx] or reachable[ny, nx]:
                continue
            if not (walkability[y, nx] or walkability[ny, x]):  # both orthogonals blocked: no squeeze
                continue
            reachable[ny, nx] = True
            queue.append((nx, ny))
    return reachable


def _spiral_nearest(ok: Callable[[int, int], bool], row: int, col: int, max_radius: int) -> Cell | None:
    """The nearest cell (expanding rings) to ``(row, col)`` for which ``ok`` holds."""

    if ok(row, col):
        return row, col
    for radius in range(1, max_radius + 1):
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                if max(abs(dr), abs(dc)) != radius:
                    continue
                if ok(row + dr, col + dc):
                    return row + dr, col + dc
    return None


# --------------------------------------------------------------------------- #
# Graph construction                                                          #
# --------------------------------------------------------------------------- #


def build_nav_graph(
    walkability: np.ndarray,
    *,
    map_data: MapData | None = None,
    cell_size: int = DEFAULT_CELL_SIZE,
) -> NavGraph:
    """Build the per-episode :class:`NavGraph` from the pixel walkability mask.

    ``map_data`` (when given) seeds the reachable flood from ``home`` and supplies
    the task / vent / button rects whose anchors are precomputed and validated.
    Without it the graph is pure pathfinding: every walkable pixel is reachable.
    """

    # Ground-truth reachability is a pixel property (a 1×1 agent flood from spawn),
    # not something to infer from the coarse grid — that keeps a thin wall passing
    # *through* a cell from wrongly severing a genuinely reachable region.
    if map_data is not None:
        reachable_pixels = _flood_reachable_pixels(walkability, (map_data.home.x, map_data.home.y))
    else:
        reachable_pixels = walkability

    clearance = _clearance_mask(walkability, CLEARANCE_RADIUS)
    node_point = _build_nodes(reachable_pixels, clearance, cell_size)
    adjacency = _build_edges(walkability, node_point)

    graph = NavGraph(
        walkability=walkability,
        cell_size=cell_size,
        rows=walkability.shape[0] // cell_size,
        cols=walkability.shape[1] // cell_size,
        node_point=node_point,
        adjacency=adjacency,
        reachable=set(node_point),  # every node sits on a reachable pixel by construction
        clearance=clearance,
    )
    if map_data is not None:
        _build_anchors(graph, map_data)
    return graph


def _build_nodes(reachable_pixels: np.ndarray, clearance: np.ndarray, cell_size: int) -> dict[Cell, Point]:
    """One node per cell containing a reachable pixel; point = nearest-center pixel,
    **preferring one that keeps clearance** so node-to-node travel runs down corridor
    centres. Every cell with a reachable pixel still gets a node (a cell with no
    clearance pixel falls back to its nearest-center reachable pixel), so connectivity
    and reachability are unchanged."""

    height, width = reachable_pixels.shape
    rows, cols = height // cell_size, width // cell_size
    node_point: dict[Cell, Point] = {}
    for row in range(rows):
        for col in range(cols):
            point = _cell_node_point(reachable_pixels, clearance, row, col, cell_size)
            if point is not None:
                node_point[(row, col)] = point
    return node_point


def _build_edges(
    walkability: np.ndarray, node_point: dict[Cell, Point]
) -> dict[Cell, list[tuple[Cell, float]]]:
    """Edge between 8-neighbour nodes iff the segment between their points is clear."""

    adjacency: dict[Cell, list[tuple[Cell, float]]] = {cell: [] for cell in node_point}
    for (row, col), point in node_point.items():
        for dr, dc in _FORWARD:
            neighbour = (row + dr, col + dc)
            other = node_point.get(neighbour)
            if other is None or not _segment_clear(walkability, point, other):
                continue
            cost = math.dist(point, other)
            adjacency[(row, col)].append((neighbour, cost))
            adjacency[neighbour].append(((row, col), cost))
    return adjacency


def _build_anchors(graph: NavGraph, map_data: MapData) -> None:
    """Precompute and validate the reachable target pixel for every destination."""

    unreachable: list[str] = []

    for index, task in enumerate(map_data.tasks):
        anchor = _find_anchor(graph, task.x, task.y, task.x + task.w, task.y + task.h, (task.center.x, task.center.y))
        if anchor is not None:
            graph.task_anchors[index] = anchor
        else:
            unreachable.append(f"task[{index}] {task.name!r}")

    for index, vent in enumerate(map_data.vents):
        cx, cy = vent.center.x, vent.center.y
        anchor = _find_anchor(
            graph,
            cx - VENT_REACH,
            cy - VENT_REACH,
            cx + VENT_REACH + 1,
            cy + VENT_REACH + 1,
            (cx, cy),
            predicate=lambda px, py, cx=cx, cy=cy: (px - cx) ** 2 + (py - cy) ** 2 <= VENT_REACH**2,
        )
        if anchor is not None:
            graph.vent_anchors[index] = anchor
        else:
            unreachable.append(f"vent[{index}] group {vent.group!r}")

    button = map_data.button
    graph.button_anchor = _find_anchor(
        graph, button.x, button.y, button.x + button.w, button.y + button.h, (button.center.x, button.center.y)
    )
    if graph.button_anchor is None:
        unreachable.append("emergency button")

    _build_vent_edges(graph, map_data)

    graph.unreachable = tuple(unreachable)
    if unreachable:
        _log.warning(
            "crewborg nav: %d destination(s) have no reachable anchor and cannot be "
            "navigated to: %s",
            len(unreachable),
            ", ".join(unreachable),
        )


def _build_vent_edges(graph: NavGraph, map_data: MapData) -> None:
    """Add a teleport edge between every ordered pair of same-group vents.

    A vent only teleports the imposter to the *other* vents in its group
    (``sim.nim``), so the edges connect each reachable vent anchor to every other
    reachable anchor sharing its ``group``. Vents whose anchor was unreachable (no
    entry in ``vent_anchors``) are skipped — you cannot walk to them to vent.
    """

    groups: dict[str, list[int]] = {}
    for index, vent in enumerate(map_data.vents):
        if index in graph.vent_anchors:
            groups.setdefault(vent.group, []).append(index)

    for members in groups.values():
        if len(members) < 2:
            continue  # a lone reachable vent in its group teleports nowhere useful
        for from_vent in members:
            from_anchor = graph.vent_anchors[from_vent]
            from_cell = graph.world_to_cell(*from_anchor)
            for to_vent in members:
                if to_vent == from_vent:
                    continue
                to_anchor = graph.vent_anchors[to_vent]
                edge = VentEdge(
                    from_vent=from_vent,
                    to_vent=to_vent,
                    from_cell=from_cell,
                    to_cell=graph.world_to_cell(*to_anchor),
                    from_anchor=from_anchor,
                    to_anchor=to_anchor,
                    cost=VENT_EDGE_COST,
                )
                graph.vent_edges.setdefault(from_cell, []).append(edge)


def _find_anchor(
    graph: NavGraph,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    target: Point,
    *,
    predicate: Callable[[int, int], bool] | None = None,
) -> Point | None:
    """The reachable, routable walkable pixel in a window nearest ``target``.

    A candidate pixel qualifies when it is walkable, satisfies ``predicate`` (the
    interaction condition, default: anywhere in the window), lies in a reachable
    node cell, and has clear line of sight from that node's point (so the agent can
    actually drive the final hop onto it).
    """

    walkability = graph.walkability
    cs = graph.cell_size
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(graph.map_width, x1), min(graph.map_height, y1)
    tx, ty = target
    best: Point | None = None
    best_d: int | None = None
    for py in range(y0, y1):
        for px in range(x0, x1):
            if not walkability[py, px]:
                continue
            if predicate is not None and not predicate(px, py):
                continue
            cell = (py // cs, px // cs)
            if cell not in graph.reachable:
                continue
            node = graph.node_point.get(cell)
            if node is None or not _segment_clear(walkability, node, (px, py)):
                continue
            d = (px - tx) ** 2 + (py - ty) ** 2
            if best_d is None or d < best_d:
                best_d, best = d, (px, py)
    return best


# --------------------------------------------------------------------------- #
# Route planning                                                              #
# --------------------------------------------------------------------------- #


def _path_mask(graph: NavGraph) -> np.ndarray:
    """The mask routes follow — the clearance (eroded) mask, or walkability if unset.

    Used for the clear-shot short-circuit and string-pulling so followed segments keep
    clearance from walls. A* edges and reachability still use the true walkability mask
    (in ``adjacency`` / the spawn flood), so connectivity is unaffected.
    """

    return graph.clearance if graph.clearance is not None else graph.walkability


def plan_route(graph: NavGraph, start_world: Point, goal_world: Point) -> list[Point]:
    """A* a string-pulled route of world waypoints from start to goal, or ``[]``.

    A clear (clearance-keeping) straight shot short-circuits A* entirely. Otherwise
    start and goal are snapped into the reachable component, A* plans over the node
    graph, and the cell-point path is string-pulled by a pixel-resolution
    clearance-keeping line-of-sight pass. The final waypoint is the exact
    ``goal_world`` so the action layer drives onto the real target (which may itself
    sit just off a node / against a wall, e.g. a task anchor or a dynamic kill target).
    """

    if _segment_clear(_path_mask(graph), start_world, goal_world):
        return [goal_world]
    start = graph.nearest_reachable_node(*start_world)
    goal = graph.nearest_reachable_node(*goal_world)
    if start is None or goal is None:
        return []
    if start == goal:
        return [goal_world]

    goal_point = graph.node_point[goal]
    came_from: dict[Cell, Cell] = {}
    g_score = {start: 0.0}
    open_heap: list[tuple[float, Cell]] = [(0.0, start)]
    closed: set[Cell] = set()

    while open_heap:
        _, current = heapq.heappop(open_heap)
        if current == goal:
            return _reconstruct(graph, came_from, current, start_world, goal_world)
        if current in closed:
            continue
        closed.add(current)
        for neighbour, step_cost in graph.adjacency[current]:
            if neighbour in closed:
                continue
            tentative = g_score[current] + step_cost
            if tentative < g_score.get(neighbour, math.inf):
                came_from[neighbour] = current
                g_score[neighbour] = tentative
                f = tentative + math.dist(graph.node_point[neighbour], goal_point)
                heapq.heappush(open_heap, (f, neighbour))
    return []


def plan_route_via_vents(
    graph: NavGraph, start_world: Point, goal_world: Point
) -> tuple[list[Point], dict[int, int]]:
    """A* a route to ``goal_world`` that may teleport through vents (imposter flee).

    Identical to :func:`plan_route` but the search may also traverse the graph's
    vent teleport edges, so the cheapest route to a far point can vanish through a
    vent instead of walking the long way round. Returns ``(waypoints, teleports)``
    where ``teleports`` maps the index of a waypoint *reached by venting* to the
    vent index the agent must stand on and press B to get there; the action layer
    walks the ordinary legs and fires the vent on the teleport legs. ``([], {})``
    when the goal is unreachable even with vents.
    """

    if _segment_clear(_path_mask(graph), start_world, goal_world):
        return [goal_world], {}
    start = graph.nearest_reachable_node(*start_world)
    goal = graph.nearest_reachable_node(*goal_world)
    if start is None or goal is None:
        return [], {}
    if start == goal:
        return [goal_world], {}

    came_from, came_edge = _astar_via_vents(graph, start, goal)
    if goal not in came_from:
        return [], {}

    nodes = [goal]
    current = goal
    while current != start:
        current = came_from[current]
        nodes.append(current)
    nodes.reverse()

    # Walk the cell path, splitting it into walk-segments at each teleport. Each
    # walk-segment is string-pulled on its own (a teleport boundary is never
    # smoothed across — the two anchors aren't mutually visible), and the exit
    # anchor of a hop is marked as a teleport waypoint.
    waypoints: list[Point] = []
    teleports: dict[int, int] = {}
    segment: list[Point] = [start_world]
    for cell in nodes[1:]:
        edge = came_edge[cell]
        if edge is None:
            segment.append(graph.node_point[cell])
            continue
        segment.append(edge.from_anchor)  # walk onto the vent entry, then teleport
        waypoints.extend(_smooth_route(_path_mask(graph), segment)[1:])
        waypoints.append(edge.to_anchor)
        teleports[len(waypoints) - 1] = edge.from_vent
        segment = [edge.to_anchor]
    segment.append(goal_world)
    waypoints.extend(_smooth_route(_path_mask(graph), segment)[1:])
    return waypoints, teleports


def _astar_via_vents(
    graph: NavGraph, start: Cell, goal: Cell
) -> tuple[dict[Cell, Cell], dict[Cell, VentEdge | None]]:
    """A* over walk edges + vent teleport edges; records how each cell was reached.

    ``came_edge[cell]`` is the :class:`VentEdge` used to arrive at ``cell`` (a
    teleport leg) or ``None`` (a walked leg), so the route can be split at hops.
    """

    goal_point = graph.node_point[goal]
    came_from: dict[Cell, Cell] = {}
    came_edge: dict[Cell, VentEdge | None] = {}
    g_score = {start: 0.0}
    open_heap: list[tuple[float, Cell]] = [(0.0, start)]
    closed: set[Cell] = set()

    def relax(neighbour: Cell, step_cost: float, edge: VentEdge | None) -> None:
        tentative = g_score[current] + step_cost
        if tentative < g_score.get(neighbour, math.inf):
            came_from[neighbour] = current
            came_edge[neighbour] = edge
            g_score[neighbour] = tentative
            f = tentative + math.dist(graph.node_point[neighbour], goal_point)
            heapq.heappush(open_heap, (f, neighbour))

    while open_heap:
        _, current = heapq.heappop(open_heap)
        if current == goal:
            break
        if current in closed:
            continue
        closed.add(current)
        for neighbour, step_cost in graph.adjacency[current]:
            if neighbour not in closed:
                relax(neighbour, step_cost, None)
        for edge in graph.vent_edges.get(current, []):
            if edge.to_cell not in closed:
                relax(edge.to_cell, edge.cost, edge)
    return came_from, came_edge


def _reconstruct(
    graph: NavGraph,
    came_from: dict[Cell, Cell],
    goal_cell: Cell,
    start_world: Point,
    goal_world: Point,
) -> list[Point]:
    cells = [goal_cell]
    current = goal_cell
    while current in came_from:
        current = came_from[current]
        cells.append(current)
    cells.reverse()
    # Node points for every step except the last, then the exact goal point.
    waypoints = [graph.node_point[cell] for cell in cells[:-1]]
    waypoints.append(goal_world)
    # String-pull the staircase into straight runs. Anchor at the agent's real start
    # position (not the start cell's node point) so the first leg is cut too, then
    # drop that anchor — the follower drives from where it actually is.
    return _smooth_route(_path_mask(graph), [start_world] + waypoints)[1:]


def _smooth_route(walkability: np.ndarray, waypoints: list[Point]) -> list[Point]:
    """Collapse waypoints the agent can see straight across (line-of-sight string-pull).

    Greedy: keep extending from the current anchor to the furthest waypoint still in
    clear pixel-level line of sight; when the next is occluded, commit the last
    visible waypoint as a corner and make it the new anchor. Adjacent graph waypoints
    are mutually visible by construction, so progress is guaranteed. The final
    waypoint (the exact goal, possibly just off a walkable pixel) is always kept.
    """

    if len(waypoints) <= 2:
        return list(waypoints)
    out = [waypoints[0]]
    anchor = 0
    i = 2
    while i < len(waypoints):
        if _segment_clear(walkability, waypoints[anchor], waypoints[i]):
            i += 1  # still visible from the anchor — skip the intermediate point
        else:
            out.append(waypoints[i - 1])  # furthest visible point becomes a corner
            anchor = i - 1
            i += 1
    out.append(waypoints[-1])
    return out
