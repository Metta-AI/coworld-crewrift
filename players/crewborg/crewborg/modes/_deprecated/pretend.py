"""DEPRECATED — DO NOT USE (cold storage, retired 2026-06-24). See ``_deprecated/__init__.py``.
Replaced by a group-follow → peel-off seeking approach; this occupancy-density version
drove crewborg toward crowds (the centroid hub), the worst place to find an isolated victim.
Kept for reference only; not imported anywhere.

Pretend mode: the imposter's default blending behaviour (design §7.2).

A small FSM that keeps the imposter doing crewmate-like things and never standing
still. It carries no notion of a "victim" — Search owns target acquisition near
the kill window. Pretend just looks busy by moving to real task stations in rooms
where crew occupancy is high.

    DISPATCH (transient): occupancy room with task? → DO_TASK(best task)
                          else → GOTO_ROOM(next task room)
    GOTO_ROOM(R)      fallback movement to a real task station; never idles
        • arrived near fallback station                        → DO_TASK
    DO_TASK(station)  go to the station, then hold TASK_TICKS (a fake task) → DISPATCH
        • but only hold while a crewmate is visible — a fake task with no audience
          fools no one and burns cooldown, so an empty room re-dispatches (keeps
          moving toward crew/victims) instead of idling there (get kills ASAP).

Occupancy room choices are deterministic but no longer synchronized round-robin:
room density comes from the crew occupancy grid, while a separate teammate estimate
penalizes rooms another imposter is likely to occupy. Rooms without fake-task
stations are skipped. The starting room never triggers a fake task (every player
spawns there, and anchoring a task there stranded the imposter when the crew
dispersed). The mode keeps its state across ticks: the runtime preserves one
Pretend instance while the directive stays ``pretend``.
"""

from __future__ import annotations

from crewborg.agent_tracking import best_pretend_room_target
from crewborg.modes import imposter_common as ic
from crewborg.map.types import Room
from crewborg.strategy.opportunity import has_visible_victim
from crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode, ModeParams

# Within this distance² (world px) of a station / waypoint we count as "arrived".
ARRIVE_RADIUS_SQ = 24**2
# One task-time hold (≈ the 72-tick task progress in sim.nim).
TASK_TICKS = 72
# Keep an occupancy room target for effectively the whole Pretend window. Search/Hunt
# can still preempt Pretend immediately through the strategy.
ROOM_TARGET_MIN_TICKS = 10_000


class PretendMode(Mode[Belief, ActionState, Intent]):
    name = "pretend"
    params_type = EmptyModeParams

    def __init__(self, params: ModeParams | None = None) -> None:
        super().__init__(params)
        self._state: str | None = None  # None ⇒ needs DISPATCH
        self._goto_point: ic.Point | None = None  # current wander destination
        self._target_room_name: str | None = None
        self._room_chosen_tick: int | None = None
        self._room_cursor: int = 0  # round-robin index over rooms
        self._task_station: ic.Point | None = None
        self._hold_until: int | None = None

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        self_xy = ic.self_xy(belief)
        if self_xy is None:
            return Intent(kind="idle", reason="no self position")  # camera not up yet
        if self._state is None:
            self._dispatch(belief, self_xy)
        return self._act(belief, self_xy)

    # --- state routing --------------------------------------------------------

    def _act(self, belief: Belief, self_xy: ic.Point) -> Intent:
        if self._state == "goto_room":
            return self._goto_room(belief, self_xy)
        if self._state == "do_task":
            return self._do_task(belief, self_xy)
        return Intent(kind="idle", reason="no behaviour")  # degenerate (no map)

    def _dispatch(self, belief: Belief, self_xy: ic.Point) -> None:
        """Choose the next fake task from occupancy, else keep moving."""

        if self._choose_occupancy_task(belief, self_xy):
            return
        self._state, self._goto_point, self._target_room_name = "goto_room", None, None

    # --- states ---------------------------------------------------------------

    def _goto_room(self, belief: Belief, self_xy: ic.Point) -> Intent:
        target_room = _room_named(belief, self._target_room_name)
        if target_room is not None and self._goto_point is not None and ic.dist2(self_xy, self._goto_point) <= ARRIVE_RADIUS_SQ:
            station = self._task_station or _station_in_room(belief, target_room, self_xy)
            if station is not None and ic.dist2(self_xy, station) <= ARRIVE_RADIUS_SQ:
                self._state, self._task_station, self._hold_until = "do_task", station, None
                return self._do_task(belief, self_xy)
            self._target_room_name = None
            self._goto_point = None
            self._task_station = None
            self._room_chosen_tick = None

        committed = self._room_target_committed(belief, self_xy)
        if not committed and self._choose_occupancy_task(belief, self_xy):
            return self._act(belief, self_xy)

        if self._goto_point is None or ic.dist2(self_xy, self._goto_point) <= ARRIVE_RADIUS_SQ:
            self._target_room_name = None
            self._goto_point = self._next_task_point(belief, self_xy)
            self._task_station = self._goto_point
            self._room_chosen_tick = None
        if self._goto_point is None:
            return Intent(kind="idle", reason="no fake-task target")  # degenerate
        reason = "pretending at likely crew task" if self._room_chosen_tick is not None else "pretending at fallback task"
        return Intent(kind="navigate_to", point=self._goto_point, reason=reason)

    def _do_task(self, belief: Belief, self_xy: ic.Point) -> Intent:
        if self._task_station is None:
            return self._redispatch(belief, self_xy)
        if ic.dist2(self_xy, self._task_station) > ARRIVE_RADIUS_SQ:
            return Intent(kind="navigate_to", point=self._task_station, reason="heading to a task station")
        # A fake task only sells the disguise to someone watching. With no crewmate in
        # view, idling at the station is wasted cooldown — abandon it and keep moving
        # (toward crew-dense rooms) so the cooldown converts to a real kill sooner. This
        # both refuses to *start* a hold with no audience and *stops* one the moment the
        # last crewmate leaves view.
        if not has_visible_victim(belief):
            return self._redispatch(belief, self_xy)
        if self._hold_until is None:
            self._hold_until = belief.last_tick + TASK_TICKS
        if belief.last_tick < self._hold_until:
            return Intent(kind="idle", reason="faking a task (crew watching)")
        return self._redispatch(belief, self_xy)  # hold complete

    def _redispatch(self, belief: Belief, self_xy: ic.Point) -> Intent:
        """Reset the fake-task FSM and pick the next thing to do (a fresh DISPATCH)."""

        self._state = None
        self._target_room_name = None
        self._goto_point = None
        self._task_station = None
        self._hold_until = None
        self._room_chosen_tick = None
        self._dispatch(belief, self_xy)
        return self._act(belief, self_xy)

    def _next_task_point(self, belief: Belief, self_xy: ic.Point) -> ic.Point | None:
        """Round-robin to the next real task station outside the current room."""

        rooms = belief.map.rooms if belief.map is not None else ()
        if not rooms:
            return None
        current = ic.room_containing(belief, self_xy)
        for _ in range(len(rooms)):
            self._room_cursor = (self._room_cursor + 1) % len(rooms)
            room = rooms[self._room_cursor]
            if current is not None and room.name == current.name:
                continue
            station = _station_in_room(belief, room, self_xy)
            if station is not None:
                self._target_room_name = room.name
                return station
        return None  # only one room, and we are in it

    def _choose_occupancy_task(self, belief: Belief, self_xy: ic.Point) -> bool:
        target = best_pretend_room_target(
            belief,
            self_xy,
            current_room_name=self._target_room_name,
            eligible_room_names=_fake_task_room_names(belief),
        )
        if target is None:
            return False
        room = _room_named(belief, target.room_name)
        if room is None:
            return False
        station = _station_in_room(belief, room, self_xy)
        if station is None:
            return False
        self._state = "goto_room"
        self._target_room_name = target.room_name
        self._goto_point = station
        self._task_station = station
        self._room_chosen_tick = belief.last_tick
        return True

    def _room_target_committed(self, belief: Belief, self_xy: ic.Point) -> bool:
        if self._target_room_name is None or self._goto_point is None or self._room_chosen_tick is None:
            return False
        if ic.dist2(self_xy, self._goto_point) <= ARRIVE_RADIUS_SQ:
            return False
        return belief.last_tick - self._room_chosen_tick < ROOM_TARGET_MIN_TICKS


def _station_in_room(belief: Belief, room: Room, self_xy: ic.Point) -> ic.Point | None:
    """The nearest task station inside ``room``, or ``None`` if the start room / none."""

    start = ic.starting_room(belief)
    if start is not None and room.name == start.name:
        return None
    tasks = belief.map.tasks if belief.map is not None else ()
    indices = [i for i in range(len(tasks)) if ic.in_rect((tasks[i].center.x, tasks[i].center.y), room)]
    if not indices:
        return None
    nearest = min(indices, key=lambda i: ic.dist2(self_xy, (tasks[i].center.x, tasks[i].center.y)))
    return ic.task_point(belief, nearest)


def _fake_task_room_names(belief: Belief) -> set[str]:
    if belief.map is None:
        return set()
    out: set[str] = set()
    start = ic.starting_room(belief)
    for room in belief.map.rooms:
        if start is not None and room.name == start.name:
            continue
        for task in belief.map.tasks:
            if ic.in_rect((task.center.x, task.center.y), room):
                out.add(room.name)
                break
    return out


def _room_named(belief: Belief, name: str | None) -> Room | None:
    if name is None or belief.map is None:
        return None
    return next((room for room in belief.map.rooms if room.name == name), None)
