"""Probabilistic per-agent location tracking for imposter search.

This module implements the first behaviour-changing slice of
``docs/agent-tracking.md``:

* a deterministic static substrate: anchors, pairwise route polylines, and a
  coarse reachable occupancy grid;
* a per-player reachability-disc position belief with line-of-sight negative
  observations; and
* a readout helper for "walk toward the hottest likely crew cell".

The richer task-destination mixture from the design intentionally remains a
later stage. The reachability filter is useful on its own, deterministic, and
keeps all live work to cheap table lookups over a coarse grid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from crewborg.nav import NavGraph, plan_route

if TYPE_CHECKING:
    from crewborg.map.types import MapData
    from crewborg.types import Belief, PerceptionFrame

Point = tuple[int, int]
AnchorKind = Literal["home", "button", "task"]

GRID_CELL_SIZE = 32
MAX_SPEED_PX_PER_TICK = 2.75


class TrackingAnchor(BaseModel):
    """A static map anchor used for precomputed route polylines."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: AnchorKind
    point: Point
    index: int | None = None


class RoutePolyline(BaseModel):
    """A pixel polyline with cumulative arc-length lookup."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    points: tuple[Point, ...]
    cumulative_lengths: tuple[float, ...]
    total_length: float

    @classmethod
    def from_points(cls, points: list[Point]) -> "RoutePolyline":
        deduped: list[Point] = []
        for point in points:
            if not deduped or deduped[-1] != point:
                deduped.append(point)
        cumulative = [0.0]
        for prev, cur in zip(deduped, deduped[1:]):
            cumulative.append(cumulative[-1] + math.dist(prev, cur))
        return cls(
            points=tuple(deduped),
            cumulative_lengths=tuple(cumulative),
            total_length=cumulative[-1],
        )

    def point_at(self, arc_length: float) -> Point:
        """Return the world pixel reached after ``arc_length`` along the route."""

        if not self.points:
            return 0, 0
        if len(self.points) == 1 or arc_length <= 0:
            return self.points[0]
        if arc_length >= self.total_length:
            return self.points[-1]

        lo, hi = 0, len(self.cumulative_lengths) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.cumulative_lengths[mid] < arc_length:
                lo = mid + 1
            else:
                hi = mid
        end_index = max(1, lo)
        start_index = end_index - 1
        start = self.points[start_index]
        end = self.points[end_index]
        segment_start = self.cumulative_lengths[start_index]
        segment_len = self.cumulative_lengths[end_index] - segment_start
        if segment_len <= 0:
            return end
        frac = (arc_length - segment_start) / segment_len
        return round(start[0] + (end[0] - start[0]) * frac), round(start[1] + (end[1] - start[1]) * frac)


class OccupancyCell(BaseModel):
    """One reachable coarse-grid cell used for occupancy readout."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    row: int
    col: int
    center: Point
    label: str | None = None


@dataclass(frozen=True)
class OccupancySubstrate:
    """Static per-episode tracking substrate."""

    anchors: tuple[TrackingAnchor, ...]
    polylines: dict[tuple[str, str], RoutePolyline]
    cells: dict[int, OccupancyCell]
    cell_size: int
    rows: int
    cols: int


class AgentPositionEstimate(BaseModel):
    """Latest position distribution for one other agent."""

    model_config = ConfigDict(extra="forbid")

    color: str
    last_seen_tick: int
    age_ticks: int
    disc_radius: float
    observed_this_tick: bool
    mass_by_cell: dict[int, float] = Field(default_factory=dict)
    top_cell: int | None = None
    top_point: Point | None = None
    top_probability: float = 0.0
    support_cell_count: int = 0


class OccupancySnapshot(BaseModel):
    """Coarse-grid expected crew occupancy for the latest tick."""

    model_config = ConfigDict(extra="forbid")

    tick: int
    expected_by_cell: dict[int, float] = Field(default_factory=dict)
    top_cell: int | None = None
    top_point: Point | None = None
    top_expected: float = 0.0
    tracked_count: int = 0
    support_cell_count: int = 0


@dataclass(frozen=True)
class OccupancyRoomTarget:
    """Room-level target for imposter Pretend routing."""

    room_name: str
    point: Point
    expected: float
    density: float
    teammate_density: float
    score: float


class ReacquisitionEvent(BaseModel):
    """A predicted-vs-actual observation when a lost player re-enters view."""

    model_config = ConfigDict(extra="forbid")

    tick: int
    color: str
    predicted_cell: int | None
    actual_cell: int | None
    predicted_point: Point | None
    actual_point: Point
    top_probability: float
    distance_error: float | None
    disc_radius: float


class AgentTrackingState(BaseModel):
    """Mutable tracking state stored on :class:`Belief`."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    substrate: OccupancySubstrate | None = None
    estimates: dict[str, AgentPositionEstimate] = Field(default_factory=dict)
    teammate_estimates: dict[str, AgentPositionEstimate] = Field(default_factory=dict)
    snapshot: OccupancySnapshot | None = None
    teammate_snapshot: OccupancySnapshot | None = None
    previous_visible_colors: set[str] = Field(default_factory=set)
    reacquisitions: list[ReacquisitionEvent] = Field(default_factory=list)


def build_occupancy_substrate(
    nav: NavGraph,
    map_data: "MapData",
    *,
    cell_size: int = GRID_CELL_SIZE,
) -> OccupancySubstrate:
    """Build anchors, pairwise route polylines, and a coarse reachable grid."""

    anchors = tuple(_anchors(nav, map_data))
    polylines: dict[tuple[str, str], RoutePolyline] = {}
    for start in anchors:
        for end in anchors:
            if start.name == end.name:
                continue
            route = plan_route(nav, start.point, end.point)
            if route:
                polylines[(start.name, end.name)] = RoutePolyline.from_points([start.point, *route])

    cells, rows, cols = _coarse_grid(nav, map_data, cell_size)
    return OccupancySubstrate(
        anchors=anchors,
        polylines=polylines,
        cells=cells,
        cell_size=cell_size,
        rows=rows,
        cols=cols,
    )


def update_agent_tracking(belief: "Belief") -> None:
    """Advance the per-player location tracker from the finalized perception fold."""

    tracking = belief.agent_tracking
    if tracking.substrate is None and belief.nav is not None and belief.map is not None:
        tracking.substrate = build_occupancy_substrate(belief.nav, belief.map)
    substrate = tracking.substrate
    if substrate is None:
        return

    frame = belief.recent_frames[-1] if belief.recent_frames and belief.recent_frames[-1].tick == belief.last_tick else None
    visible_colors = set(frame.players) if frame is not None else set()
    live_crew_colors = {
        color
        for color, record in belief.roster.items()
        if color not in belief.teammate_colors and record.life_status != "dead"
    }
    live_teammate_colors = {
        color
        for color, record in belief.roster.items()
        if color in belief.teammate_colors and record.life_status != "dead"
    }

    for color in visible_colors & live_crew_colors:
        previous = tracking.estimates.get(color)
        if previous is not None and color not in tracking.previous_visible_colors and previous.top_point is not None:
            actual_point = frame.players[color] if frame is not None else (belief.roster[color].world_x, belief.roster[color].world_y)
            tracking.reacquisitions.append(_reacquisition(substrate, previous, belief.last_tick, color, actual_point))

    estimates = _estimate_colors(substrate, belief, live_crew_colors, visible_colors, frame)
    teammate_estimates = _estimate_colors(substrate, belief, live_teammate_colors, visible_colors, frame)

    tracking.estimates = estimates
    tracking.teammate_estimates = teammate_estimates
    tracking.snapshot = _snapshot(substrate, belief.last_tick, estimates.values())
    tracking.teammate_snapshot = (
        _snapshot(substrate, belief.last_tick, teammate_estimates.values()) if teammate_estimates else None
    )
    tracking.previous_visible_colors = visible_colors


def _estimate_colors(
    substrate: OccupancySubstrate,
    belief: "Belief",
    colors: set[str],
    visible_colors: set[str],
    frame: "PerceptionFrame | None",
) -> dict[str, AgentPositionEstimate]:
    estimates: dict[str, AgentPositionEstimate] = {}
    for color in sorted(colors):
        record = belief.roster[color]
        if color in visible_colors and frame is not None:
            estimates[color] = _observed_estimate(substrate, color, belief.last_tick, frame.players[color])
        else:
            last_seen = (record.world_x, record.world_y)
            last_seen_tick = record.last_seen_tick
            if last_seen_tick == 0 and belief.map is not None:
                last_seen = (belief.map.home.x, belief.map.home.y)
            estimates[color] = _reachability_estimate(
                substrate,
                color=color,
                now_tick=belief.last_tick,
                last_seen_tick=last_seen_tick,
                last_seen=last_seen,
                frame=frame,
            )
    return estimates


def best_seek_point(belief: "Belief", self_xy: Point | None = None) -> Point | None:
    """Return the hottest reachable occupancy cell for imposter search."""

    del self_xy  # cells are prefiltered to the reachable component; no live A* here
    points = ranked_seek_points(belief)
    return points[0] if points else None


def ranked_seek_points(belief: "Belief") -> list[Point]:
    """Return occupancy cell centers from hottest to coldest for active search."""

    substrate = belief.agent_tracking.substrate
    snapshot = belief.agent_tracking.snapshot
    if substrate is None or snapshot is None or not snapshot.expected_by_cell:
        return []

    points: list[Point] = []
    ranked = sorted(snapshot.expected_by_cell.items(), key=lambda item: item[1], reverse=True)
    for cell_id, expected in ranked:
        if expected <= 0:
            break
        cell = substrate.cells.get(cell_id)
        if cell is not None:
            points.append(cell.center)
    return points


ROOM_TARGET_HYSTERESIS = 0.80
TEAMMATE_ROOM_PENALTY = 3.0


def best_pretend_room_target(
    belief: "Belief",
    self_xy: Point,
    *,
    current_room_name: str | None = None,
    eligible_room_names: set[str] | None = None,
) -> OccupancyRoomTarget | None:
    """Return the best room-level Pretend target from crew density and imposter pressure.

    Crew occupancy is useful at room scale; cell-level maxima are too twitchy once
    the per-agent support becomes broad. Teammate pressure is kept separate from
    crew occupancy so an imposter can blend near likely crew while avoiding a
    second imposter already occupying or searching the same room.
    """

    substrate = belief.agent_tracking.substrate
    snapshot = belief.agent_tracking.snapshot
    if substrate is None or snapshot is None or belief.map is None:
        return None

    room_cells = _cells_by_room(substrate)
    expected_by_room = _room_expected(substrate, snapshot.expected_by_cell)
    if not expected_by_room:
        return None
    teammate_expected_by_room = _room_expected(
        substrate,
        belief.agent_tracking.teammate_snapshot.expected_by_cell
        if belief.agent_tracking.teammate_snapshot is not None
        else {},
    )

    targets: list[OccupancyRoomTarget] = []
    for room_name, expected in expected_by_room.items():
        if eligible_room_names is not None and room_name not in eligible_room_names:
            continue
        cells = room_cells.get(room_name)
        if not cells:
            continue
        density = expected / len(cells)
        teammate_density = teammate_expected_by_room.get(room_name, 0.0) / len(cells)
        score = density - TEAMMATE_ROOM_PENALTY * teammate_density
        targets.append(
            OccupancyRoomTarget(
                room_name=room_name,
                point=_room_center_cell(room_name, room_cells, belief.map),
                expected=expected,
                density=density,
                teammate_density=teammate_density,
                score=score,
            )
        )
    if not targets:
        return None

    best = max(targets, key=lambda target: (target.score, target.expected, -_dist2(self_xy, target.point), target.room_name))
    if current_room_name is not None:
        current = next((target for target in targets if target.room_name == current_room_name), None)
        if current is not None and current.score > 0 and current.score >= best.score * ROOM_TARGET_HYSTERESIS:
            return current
    return best if best.expected > 0 else None


def room_occupancy(belief: "Belief") -> dict[str, tuple[float, float]]:
    """Per-room ``(crew_density, teammate_density)`` from the occupancy substrate.

    Density = expected crew (or teammate) mass in the room / its cell count, so rooms of
    different sizes compare fairly. Empty dict when no substrate/snapshot has built yet
    (early game) — callers must tolerate that and fall back to non-occupancy signals.
    """

    substrate = belief.agent_tracking.substrate
    snapshot = belief.agent_tracking.snapshot
    if substrate is None or snapshot is None:
        return {}
    room_cells = _cells_by_room(substrate)
    expected_by_room = _room_expected(substrate, snapshot.expected_by_cell)
    teammate_snapshot = belief.agent_tracking.teammate_snapshot
    teammate_by_room = (
        _room_expected(substrate, teammate_snapshot.expected_by_cell) if teammate_snapshot is not None else {}
    )
    out: dict[str, tuple[float, float]] = {}
    for room_name, expected in expected_by_room.items():
        cells = room_cells.get(room_name)
        if not cells:
            continue
        n = len(cells)
        out[room_name] = (expected / n, teammate_by_room.get(room_name, 0.0) / n)
    return out


def _cells_by_room(substrate: OccupancySubstrate) -> dict[str, list[OccupancyCell]]:
    out: dict[str, list[OccupancyCell]] = {}
    for cell in substrate.cells.values():
        if cell.label is not None:
            out.setdefault(cell.label, []).append(cell)
    return out


def _room_expected(substrate: OccupancySubstrate, expected_by_cell: dict[int, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for cell_id, expected in expected_by_cell.items():
        cell = substrate.cells.get(cell_id)
        if cell is None or cell.label is None:
            continue
        out[cell.label] = out.get(cell.label, 0.0) + expected
    return out


def _room_center_cell(room_name: str, room_cells: dict[str, list[OccupancyCell]], map_data: "MapData") -> Point:
    room = next((candidate for candidate in map_data.rooms if candidate.name == room_name), None)
    cells = room_cells[room_name]
    if room is None:
        return cells[0].center
    center = (room.center.x, room.center.y)
    return min(cells, key=lambda cell: _dist2(center, cell.center)).center


def _anchors(nav: NavGraph, map_data: "MapData") -> list[TrackingAnchor]:
    out: list[TrackingAnchor] = []
    out.append(TrackingAnchor(name="home", kind="home", point=_snap(nav, (map_data.home.x, map_data.home.y))))
    button_point = nav.button_anchor or (map_data.button.center.x, map_data.button.center.y)
    out.append(TrackingAnchor(name="button", kind="button", point=_snap(nav, button_point)))
    for index, task in enumerate(map_data.tasks):
        point = nav.task_anchor(index) or (task.center.x, task.center.y)
        out.append(TrackingAnchor(name=f"task:{index}", kind="task", index=index, point=_snap(nav, point)))
    return out


def _snap(nav: NavGraph, point: Point) -> Point:
    cell = nav.nearest_reachable_node(*point)
    return nav.node_point[cell] if cell is not None else point


def _coarse_grid(nav: NavGraph, map_data: "MapData", cell_size: int) -> tuple[dict[int, OccupancyCell], int, int]:
    rows = math.ceil(nav.map_height / cell_size)
    cols = math.ceil(nav.map_width / cell_size)
    by_cell: dict[tuple[int, int], list[Point]] = {}
    for cell in nav.reachable:
        point = nav.node_point[cell]
        row = min(point[1] // cell_size, rows - 1)
        col = min(point[0] // cell_size, cols - 1)
        by_cell.setdefault((row, col), []).append(point)

    out: dict[int, OccupancyCell] = {}
    for (row, col), points in by_cell.items():
        target = (col * cell_size + cell_size // 2, row * cell_size + cell_size // 2)
        center = min(points, key=lambda point: _dist2(point, target))
        index = row * cols + col
        out[index] = OccupancyCell(index=index, row=row, col=col, center=center, label=_region_label(map_data, center))
    return out, rows, cols


def _region_label(map_data: "MapData", point: Point) -> str | None:
    for room in map_data.rooms:
        if room.x <= point[0] < room.x + room.w and room.y <= point[1] < room.y + room.h:
            return room.name
    return None


def _observed_estimate(
    substrate: OccupancySubstrate,
    color: str,
    tick: int,
    point: Point,
) -> AgentPositionEstimate:
    cell = _cell_for_point(substrate, point)
    mass = {cell.index: 1.0} if cell is not None else {}
    return AgentPositionEstimate(
        color=color,
        last_seen_tick=tick,
        age_ticks=0,
        disc_radius=0.0,
        observed_this_tick=True,
        mass_by_cell=mass,
        top_cell=cell.index if cell is not None else None,
        top_point=cell.center if cell is not None else point,
        top_probability=1.0 if cell is not None else 0.0,
        support_cell_count=len(mass),
    )


def _reachability_estimate(
    substrate: OccupancySubstrate,
    *,
    color: str,
    now_tick: int,
    last_seen_tick: int,
    last_seen: Point,
    frame: "PerceptionFrame | None",
) -> AgentPositionEstimate:
    age = max(0, now_tick - last_seen_tick)
    radius = MAX_SPEED_PX_PER_TICK * age
    support_radius = radius + math.sqrt(2 * substrate.cell_size * substrate.cell_size) / 2
    support = [
        cell
        for cell in substrate.cells.values()
        if math.dist(last_seen, cell.center) <= support_radius and not _point_visible(frame, cell.center)
    ]
    if not support:
        support = [cell for cell in substrate.cells.values() if not _point_visible(frame, cell.center)]
    if not support:
        support = list(substrate.cells.values())

    probability = 1.0 / len(support) if support else 0.0
    mass = {cell.index: probability for cell in support}
    top = min(support, key=lambda cell: (_dist2(last_seen, cell.center), cell.index)) if support else None
    return AgentPositionEstimate(
        color=color,
        last_seen_tick=last_seen_tick,
        age_ticks=age,
        disc_radius=radius,
        observed_this_tick=False,
        mass_by_cell=mass,
        top_cell=top.index if top is not None else None,
        top_point=top.center if top is not None else None,
        top_probability=probability if top is not None else 0.0,
        support_cell_count=len(support),
    )


def _snapshot(
    substrate: OccupancySubstrate,
    tick: int,
    estimates: Iterable[AgentPositionEstimate],
) -> OccupancySnapshot:
    expected: dict[int, float] = {}
    tracked = 0
    support_cells: set[int] = set()
    for estimate in estimates:
        tracked += 1
        for cell_id, mass in estimate.mass_by_cell.items():
            expected[cell_id] = expected.get(cell_id, 0.0) + mass
            support_cells.add(cell_id)

    top_cell: int | None = None
    top_expected = 0.0
    if expected:
        top_cell, top_expected = max(expected.items(), key=lambda item: (item[1], -item[0]))
    cell = substrate.cells.get(top_cell) if top_cell is not None else None
    return OccupancySnapshot(
        tick=tick,
        expected_by_cell=expected,
        top_cell=top_cell,
        top_point=cell.center if cell is not None else None,
        top_expected=top_expected,
        tracked_count=tracked,
        support_cell_count=len(support_cells),
    )


def _reacquisition(
    substrate: OccupancySubstrate,
    previous: AgentPositionEstimate,
    tick: int,
    color: str,
    actual_point: Point,
) -> ReacquisitionEvent:
    actual_cell = _cell_for_point(substrate, actual_point)
    distance_error = math.dist(previous.top_point, actual_point) if previous.top_point is not None else None
    return ReacquisitionEvent(
        tick=tick,
        color=color,
        predicted_cell=previous.top_cell,
        actual_cell=actual_cell.index if actual_cell is not None else None,
        predicted_point=previous.top_point,
        actual_point=actual_point,
        top_probability=previous.top_probability,
        distance_error=distance_error,
        disc_radius=previous.disc_radius,
    )


def _cell_for_point(substrate: OccupancySubstrate, point: Point) -> OccupancyCell | None:
    row = min(max(point[1] // substrate.cell_size, 0), substrate.rows - 1)
    col = min(max(point[0] // substrate.cell_size, 0), substrate.cols - 1)
    index = row * substrate.cols + col
    if index in substrate.cells:
        return substrate.cells[index]
    if not substrate.cells:
        return None
    return min(substrate.cells.values(), key=lambda cell: _dist2(point, cell.center))


def _point_visible(frame: "PerceptionFrame | None", point: Point) -> bool:
    if frame is None or frame.visible_mask is None:
        return False
    sx = point[0] - frame.camera_x
    sy = point[1] - frame.camera_y
    height, width = frame.visible_mask.shape
    if sx < 0 or sy < 0 or sx >= width or sy >= height:
        return False
    return bool(frame.visible_mask[sy, sx])


def _dist2(a: Point, b: Point) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
