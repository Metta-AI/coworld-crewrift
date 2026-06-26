"""Normal mode: the default crewmate stance — complete assigned tasks (design §7.1).

Targeting is driven off the **live task-signal set** (``visible_task_indices`` — the
arrows + bubbles, which together mark exactly the incomplete assigned tasks): pick
the nearest **reachable** signalled task, emit ``complete_task(T)`` until it's done,
then move to the next. When **no** task signal remains, every task is done, so head
back to the spawn / start room rather than standing still.

**Completion detection.** The authoritative signal is the **bubble disappearing**
(``T`` leaving the signal set while we are inside its rect). But a bubble can also
blink out for a tick from occlusion (an imposter overlapping us) or a screen-edge —
so we *gate* it on the progress bar: ``T`` is concluded done only if we recently saw
its progress reach ``COMPLETION_PROGRESS_PCT`` (≈ done). A bubble vanishing without
that progress is treated as a flicker — we keep holding the same task. Progress is
only a gate, never the trigger (so we never stop the hold early at, say, 98%); and
because targeting uses the live signals, a falsely-concluded task that is still
signalled is simply re-targeted (self-healing).

Two stall guards (design §5):

- *Reachability* — prefer tasks the nav graph can actually route to, so we don't
  fixate on an unreachable station (the action layer holds still on no path).
- *Arrows-disabled sweep* — when ``showTaskArrows`` is off, off-screen tasks emit
  no signals, so the signal set can be empty at spawn even with tasks to do. Rather
  than head home immediately, sweep the baked stations to discover assigned ones.
"""

from __future__ import annotations

import math
from typing import ClassVar

from players.crewrift.crewborg.map.types import TaskStation
from players.crewrift.crewborg.nav import plan_route
from players.crewrift.crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode

# A bubble leaving the signal set counts as completion only if progress recently
# reached at least this — otherwise it's treated as a flicker/occlusion.
COMPLETION_PROGRESS_PCT = 90
SWEEP_ARRIVE_RADIUS = 24  # within this of a station center ⇒ count it as checked

# Survival tie-break (2026-06-11 evals): Storage Deck is the map's kill zone —
# 24-29% of observed kills across both opponent fields — and the deaths trace our
# own task arteries. When a known-alive non-teammate is near us (a potential
# shadower), nudge the task ORDER away from danger-room stations. A small
# additive penalty only: our routing is best-in-class (285 px/task) and hub
# exposure barely predicts death league-wide, so this must stay a tie-break,
# never a re-route.
DANGER_ROOM_NAMES = frozenset({"Storage Deck"})
DANGER_ROOM_PENALTY_PX = 250.0
THREAT_NEAR_DIST_SQ = 150**2  # a player this close is near enough to shadow us
THREAT_RECENT_TICKS = 24  # …if seen this recently

# A roster sighting at most this old still marks "crew there" for the post-task
# survival drift (stand near witnesses instead of idling alone at spawn).
CROWD_SIGHTING_MAX_AGE_TICKS = 240


class NormalMode(Mode[Belief, ActionState, Intent]):
    name = "normal"
    params_type = EmptyModeParams
    travel_intent_kind: ClassVar[str] = "navigate_to"
    use_nav_targets: ClassVar[bool] = True

    def __init__(self, params=None) -> None:
        super().__init__(params)
        self._target: int | None = None
        self._max_progress: int = 0  # peak progress seen for the current target
        self._swept: set[int] = set()

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        tasks = belief.map.tasks if belief.map is not None else ()

        self._update_target(belief, tasks)
        if self._target is not None:
            return self._task_intent(belief, tasks, self._target)

        pending = self._pending_assigned_intent(belief, tasks)
        if pending is not None:
            return pending
        sweep = self._sweep_intent(belief, tasks)
        if sweep is not None:
            return sweep
        return self._return_to_start(belief)

    def _update_target(self, belief: Belief, tasks: tuple[TaskStation, ...]) -> None:
        """Conclude/keep the current target, then pick a new one off the live signals."""

        signals = belief.visible_task_indices
        target = self._target
        if target is not None and target < len(tasks):
            on_station = _inside(tasks[target], belief.self_world_x, belief.self_world_y)
            if on_station and belief.active_task_progress_pct is not None:
                self._max_progress = max(self._max_progress, belief.active_task_progress_pct)
            if target not in signals:
                # Bubble gone: a real completion only if progress reached ~done.
                # Otherwise it's a flicker/occlusion — keep holding the same task.
                if self._max_progress >= COMPLETION_PROGRESS_PCT:
                    belief.completed_task_indices.add(target)
                    self._target = None
            # if still signalled, keep the current target (avoids thrashing).

        if self._target is None:
            self._target = self._pick_target(belief, tasks, signals)
            self._max_progress = 0

    def _pick_target(self, belief: Belief, tasks: tuple[TaskStation, ...], signals: set[int]) -> int | None:
        # The live signal set is the authoritative list of remaining tasks; a task
        # still signalled is still to do (even if we earlier mis-concluded it done).
        candidates = [index for index in signals if index < len(tasks)]
        if not candidates:
            return None

        # Prefer tasks with a baked reachable anchor; fall back to all if none have
        # one (rare — the action layer then holds still rather than wall-drive).
        if self.use_nav_targets and belief.nav is not None:
            reachable = [i for i in candidates if belief.nav.task_anchor(i) is not None]
            if reachable:
                candidates = reachable

        self_xy = _self_xy(belief)
        if self_xy is None:
            return min(candidates)
        # Route-aware ordering with a tour lookahead (the 2026-06-10 eval lost
        # the task race at 6.68/8): the first leg is costed along the actual nav
        # route — straight-line distance lies badly across walls — and each
        # candidate adds a greedy nearest-neighbour tour over the remaining
        # stations, so we start at an endpoint of the remaining-task chain
        # instead of its middle and stop criss-crossing the map. Computed only
        # on retarget (a few A* calls per completed task), not per tick.
        # While a potential shadower is near, danger-room stations additionally
        # carry a small ordering penalty (see DANGER_ROOM_PENALTY_PX).
        points = {i: self._task_target_point(belief, tasks[i], i) for i in candidates}
        use_nav = self.use_nav_targets
        threatened = self.use_nav_targets and _threat_nearby(belief, self_xy)

        def score(index: int) -> float:
            rest = [points[j] for j in candidates if j != index]
            total = _travel_dist(belief, self_xy, points[index], use_nav=use_nav) + _chain_estimate(
                points[index], rest
            )
            if threatened and _in_danger_room(belief, points[index]):
                total += DANGER_ROOM_PENALTY_PX
            return total

        return min(candidates, key=lambda i: (score(i), i))

    def _pending_assigned_intent(self, belief: Belief, tasks: tuple[TaskStation, ...]) -> Intent | None:
        """Head to a known-assigned, not-yet-completed station with no live signal.

        Post-meeting respawns and arrows-disabled configs can leave the signal
        set empty while assigned tasks remain — previously this fell through to
        "return to start" and parked the agent idle at the Bridge spawn (the
        ~800 s off-task blob in the 2026-06-11 spatial analysis). Walking to the
        nearest remembered station re-dispatches immediately; if the station is
        actually incomplete its bubble reappears on approach and normal
        targeting takes over. Standing at a station that shows no signal proves
        it is done (in-view incomplete assigned tasks always signal) — mark it
        completed and move on, so a missed completion can't wedge us.
        """

        self_xy = _self_xy(belief)
        if self_xy is None:
            return None
        pending = sorted(
            (i for i in belief.assigned_task_indices - belief.completed_task_indices if i < len(tasks)),
            key=lambda i: _dist2(self_xy, self._task_target_point(belief, tasks[i], i)),
        )
        for index in pending:
            point = self._task_target_point(belief, tasks[index], index)
            if (
                _dist2(self_xy, point) <= SWEEP_ARRIVE_RADIUS**2
                and index not in belief.visible_task_indices
            ):
                belief.completed_task_indices.add(index)
                continue
            return self._travel_intent(point, reason="re-dispatch: assigned task without live signal", task_index=index)
        return None

    def _sweep_intent(self, belief: Belief, tasks: tuple[TaskStation, ...]) -> Intent | None:
        """Sweep baked stations to discover assigned tasks (arrows-disabled, §5)."""

        # Only sweep before any task signal has arrived, while the crew still has
        # tasks to do, and once we know where we are.
        if belief.assigned_task_indices or not tasks or belief.crew_tasks_remaining == 0:
            return None
        self_xy = _self_xy(belief)
        if self_xy is None:
            return None

        # Mark stations we have reached as checked.
        for index, task in enumerate(tasks):
            if _dist2(self_xy, _center(task)) <= SWEEP_ARRIVE_RADIUS**2:
                self._swept.add(index)

        remaining = [i for i in range(len(tasks)) if i not in self._swept]
        if not remaining:
            return None  # checked every station and found no assigned tasks
        nearest = min(remaining, key=lambda i: _dist2(self_xy, self._task_target_point(belief, tasks[i], i)))
        return self._travel_intent(
            self._task_target_point(belief, tasks[nearest], nearest),
            reason="sweeping for tasks",
            task_index=nearest,
        )

    def _task_intent(self, belief: Belief, tasks: tuple[TaskStation, ...], target: int) -> Intent:
        del belief, tasks
        return Intent(kind="complete_task", task_index=target, reason="completing assigned task")

    def _task_target_point(self, belief: Belief, task: TaskStation, index: int) -> tuple[int, int]:
        if self.use_nav_targets:
            return _nav_point(belief, task, index)
        return _center(task)

    def _travel_intent(self, point: tuple[int, int], *, reason: str, task_index: int | None = None) -> Intent:
        return Intent(kind=self.travel_intent_kind, point=point, task_index=task_index, reason=reason)

    def _return_to_start(self, belief: Belief) -> Intent:
        # Tasks done: drift to the nearest known crew instead of idling alone at
        # the Bridge spawn (the most-watched room of the map; ~800 s of off-task
        # standing in the 2026-06-11 spatial analysis). Standing next to
        # witnesses is also the survival posture — shadow kills need the
        # witness gap. Falls back to the spawn room when nobody is tracked.
        crowd = _nearest_crowd_point(belief)
        if crowd is not None:
            return Intent(
                kind=self.travel_intent_kind,
                point=crowd,
                reason="tasks done: staying near crew",
            )
        return _return_to_start(belief, kind=self.travel_intent_kind, snap_to_nav=self.use_nav_targets)


class CrewmateGhostMode(NormalMode):
    """Crewmate ghost tasking: finish tasks with wall-ignoring navigation."""

    name = "crewmate_ghost"
    travel_intent_kind = "navigate_to_noclip"
    use_nav_targets = False

    def _return_to_start(self, belief: Belief) -> Intent:
        # A ghost can't be killed and can't witness — heading home is fine.
        return _return_to_start(belief, kind=self.travel_intent_kind, snap_to_nav=self.use_nav_targets)

    def _task_intent(self, belief: Belief, tasks: tuple[TaskStation, ...], target: int) -> Intent:
        task = tasks[target]
        if _inside(task, belief.self_world_x, belief.self_world_y):
            return Intent(kind="complete_task", task_index=target, reason="ghost: completing assigned task")
        return self._travel_intent(
            _center(task),
            reason="ghost: moving through walls to assigned task",
            task_index=target,
        )


def _return_to_start(belief: Belief, *, kind: str = "navigate_to", snap_to_nav: bool = True) -> Intent:
    """All assigned tasks done — head back to the spawn / start room instead of
    standing still (which strands a finished crewmate and earns stuck penalties)."""

    if belief.map is None:
        return Intent(kind="idle", reason="no incomplete tasks remain")
    goal = (belief.map.home.x, belief.map.home.y)
    if snap_to_nav and belief.nav is not None:
        cell = belief.nav.nearest_reachable_node(*goal)
        if cell is not None:
            goal = belief.nav.node_point[cell]
    return Intent(kind=kind, point=goal, reason="tasks done: returning to the start room")


def _travel_dist(
    belief: Belief, start: tuple[int, int], goal: tuple[int, int], *, use_nav: bool
) -> float:
    """Walking distance from ``start`` to ``goal``: nav-route length when available.

    Falls back to straight-line distance with no nav graph (or for noclip ghosts,
    whose travel really is straight-line)."""

    if use_nav and belief.nav is not None:
        route = plan_route(belief.nav, start, goal)
        if route:
            return _polyline_length(start, route)
    return math.sqrt(_dist2(start, goal))


def _polyline_length(start: tuple[int, int], waypoints: list[tuple[int, int]]) -> float:
    total = 0.0
    previous = start
    for point in waypoints:
        total += math.sqrt(_dist2(previous, point))
        previous = point
    return total


def _chain_estimate(start: tuple[int, int], rest: list[tuple[int, int]]) -> float:
    """Greedy nearest-neighbour tour length from ``start`` through ``rest``.

    A cheap (straight-line) estimate of finishing the remaining stations after
    visiting ``start`` first — enough to prefer chain endpoints over centres."""

    total = 0.0
    current = start
    remaining = list(rest)
    while remaining:
        next_point = min(remaining, key=lambda p: _dist2(current, p))
        total += math.sqrt(_dist2(current, next_point))
        remaining.remove(next_point)
        current = next_point
    return total


def _threat_nearby(belief: Belief, self_xy: tuple[int, int]) -> bool:
    """Whether a live non-teammate was seen close to us very recently."""

    for record in belief.roster.values():
        if record.life_status == "dead" or record.color in belief.teammate_colors:
            continue
        if belief.last_tick - record.last_seen_tick > THREAT_RECENT_TICKS:
            continue
        if _dist2(self_xy, (record.world_x, record.world_y)) <= THREAT_NEAR_DIST_SQ:
            return True
    return False


def _in_danger_room(belief: Belief, point: tuple[int, int]) -> bool:
    rooms = belief.map.rooms if belief.map is not None else ()
    px, py = point
    for room in rooms:
        if room.name in DANGER_ROOM_NAMES and room.x <= px < room.x + room.w and room.y <= py < room.y + room.h:
            return True
    return False


def _nearest_crowd_point(belief: Belief) -> tuple[int, int] | None:
    """The nearest recently seen live player's position (nav-snapped)."""

    self_xy = _self_xy(belief)
    if self_xy is None:
        return None
    candidates = [
        (record.world_x, record.world_y)
        for record in belief.roster.values()
        if record.life_status != "dead"
        and belief.last_tick - record.last_seen_tick <= CROWD_SIGHTING_MAX_AGE_TICKS
    ]
    if not candidates:
        return None
    point = min(candidates, key=lambda p: _dist2(self_xy, p))
    if belief.nav is not None:
        cell = belief.nav.nearest_reachable_node(*point)
        if cell is not None:
            point = belief.nav.node_point[cell]
    return point


def _inside(task: TaskStation, x: int | None, y: int | None) -> bool:
    if x is None or y is None:
        return False
    return task.x <= x < task.x + task.w and task.y <= y < task.y + task.h


def _center(task: TaskStation) -> tuple[int, int]:
    return task.center.x, task.center.y


def _nav_point(belief: Belief, task: TaskStation, index: int) -> tuple[int, int]:
    """The station's baked reachable anchor, or its center before the graph exists."""

    if belief.nav is not None:
        anchor = belief.nav.task_anchor(index)
        if anchor is not None:
            return anchor
    return task.center.x, task.center.y


def _self_xy(belief: Belief) -> tuple[int, int] | None:
    if belief.self_world_x is None or belief.self_world_y is None:
        return None
    return belief.self_world_x, belief.self_world_y


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
