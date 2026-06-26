"""Search mode: pre-kill target acquisition for imposters.

Search owns the lead window before the kill cooldown is ready. It first walks
ranked occupancy points to find crew. Once a non-teammate crewmate is visible, it
commits to that target and follows its live or last-seen position until the
strategy can switch to Hunt when the kill is ready and the target is visible.

Deliberately dropped for v4: a dedicated east-wing patrol. The 2026-06-11 replay
reconstruction showed every champion imposter hunts the same hub band (all ≤7.5%
east-room time, zero east-wing camping) and that our occupancy-led search is not
an outlier — the unexploited edge is cycle time, not geography.
"""

from __future__ import annotations

from players.crewrift.crewborg.agent_tracking import ranked_seek_points
from players.crewrift.crewborg.modes import imposter_common as ic
from players.crewrift.crewborg.strategy.opportunity import (
    TRACK_WINDOW_TICKS,
    select_victim,
    ticks_until_kill_ready,
)
from players.crewrift.crewborg.types import ActionState, Belief, Intent, PlayerRecord
from players.player_sdk import EmptyModeParams, Mode, ModeParams

ARRIVE_RADIUS_SQ = 24**2
MAX_VISITED_POINTS = 6
# Deep-cooldown anti-camping: glued this close to a (usually tasking) target
# with the kill still far off, an imposter stands motionless in the open —
# 31-32% of imposter Playing ticks in the 2026-06-10/11 evals. Fake a real task
# station near the target instead: looks crew-like and stays in strike range.
CAMP_RADIUS_SQ = 48**2
# The fake station must keep the target in line of sight (the camera shows
# ~64 px around us), so only stations this close to the target qualify — but
# not the very station the target is standing at (don't stack on the victim).
FAKE_STATION_TARGET_RADIUS_SQ = 56**2
FAKE_STATION_MIN_TARGET_DIST_SQ = 20**2
# Glue directly to the target (instead of faking a nearby station) once the kill
# is within this of ready. Deliberately its own constant — NOT the strategy's
# SEARCH_LEAD_TICKS (widened to 160 for earlier Search entry in v4): widening
# the glue window with it would have us standing openly next to the victim for
# longer, which is the bot-tell the camp-fake exists to avoid.
CAMP_FAKE_LEAD_TICKS = 100


class SearchMode(Mode[Belief, ActionState, Intent]):
    name = "search"
    params_type = EmptyModeParams

    def __init__(self, params: ModeParams | None = None) -> None:
        super().__init__(params)
        self._target_color: str | None = None
        self._search_point: ic.Point | None = None
        self._visited_points: list[ic.Point] = []
        self._patrol_point: ic.Point | None = None
        self._patrol_cursor: int = 0

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        self_xy = ic.self_xy(belief)
        if self_xy is None:
            return Intent(kind="idle", reason="no self position")

        target = self._target(belief)
        if target is not None:
            self._target_color = target.color
            return self._follow_intent(
                belief, self_xy, (target.world_x, target.world_y), reason="search: following visible target"
            )

        if self._target_color is not None:
            target = belief.roster.get(self._target_color)
            if target is not None and target.life_status != "dead" and _trackable(belief, target):
                target_xy = (target.world_x, target.world_y)
                if ic.dist2(self_xy, target_xy) > ARRIVE_RADIUS_SQ:
                    return Intent(kind="navigate_to", point=target_xy, reason="search: following last-seen target")
            self._target_color = None

        return self._search_occupancy(belief, self_xy)

    def _follow_intent(self, belief: Belief, self_xy: ic.Point, target_xy: ic.Point, *, reason: str) -> Intent:
        """Follow the target — but with the kill far off, don't camp in the open.

        Glued to a tasking crewmate during deep cooldown, the bang-bang
        controller just stands still. If a real task station sits close enough
        to keep the target in sight, fake at it instead: crew-like, moving, and
        still within strike distance when the cooldown nears ready.
        """

        if (
            ic.dist2(self_xy, target_xy) <= CAMP_RADIUS_SQ
            and ticks_until_kill_ready(belief) > CAMP_FAKE_LEAD_TICKS
        ):
            station = _station_near(belief, target_xy)
            if station is not None:
                return Intent(kind="navigate_to", point=station, reason="search: faking a task near target")
        return Intent(kind="navigate_to", point=target_xy, reason=reason)

    def _target(self, belief: Belief) -> PlayerRecord | None:
        current = belief.roster.get(self._target_color) if self._target_color is not None else None
        if (
            current is not None
            and current.life_status != "dead"
            and current.color not in belief.teammate_colors
            and current.last_seen_tick == belief.last_tick
        ):
            return current
        return select_victim(belief)

    def _search_occupancy(self, belief: Belief, self_xy: ic.Point) -> Intent:
        if self._search_point is None or ic.dist2(self_xy, self._search_point) <= ARRIVE_RADIUS_SQ:
            if self._search_point is not None:
                self._visited_points.append(self._search_point)
                self._visited_points = self._visited_points[-MAX_VISITED_POINTS:]
            self._search_point = self._next_search_point(belief, self_xy)

        if self._search_point is None:
            # No occupancy signal (nobody tracked / substrate not up): patrol the
            # real task stations instead of standing still — crew are at tasks,
            # and an idling imposter both wastes the cooldown and looks bot-like
            # (the 2026-06-10 eval measured imposters stationary 31% of Playing
            # ticks during cooldown).
            return self._patrol(belief, self_xy)
        return Intent(kind="navigate_to", point=self._search_point, reason="searching likely crew occupancy")

    def _patrol(self, belief: Belief, self_xy: ic.Point) -> Intent:
        task_count = len(belief.map.tasks) if belief.map is not None else 0
        if task_count == 0:
            return Intent(kind="idle", reason="search: no occupancy or patrol target")
        if self._patrol_point is None or ic.dist2(self_xy, self._patrol_point) <= ARRIVE_RADIUS_SQ:
            self._patrol_point = self._next_patrol_point(belief, self_xy, task_count)
        if self._patrol_point is None:
            return Intent(kind="idle", reason="search: no patrol target")
        return Intent(kind="navigate_to", point=self._patrol_point, reason="search: patrolling task stations")

    def _next_patrol_point(self, belief: Belief, self_xy: ic.Point, task_count: int) -> ic.Point | None:
        """Round-robin over the map's task stations, skipping where we stand."""

        for _ in range(task_count):
            self._patrol_cursor = (self._patrol_cursor + 1) % task_count
            point = ic.task_point(belief, self._patrol_cursor)
            if ic.dist2(self_xy, point) > ARRIVE_RADIUS_SQ:
                return point
        return None

    def _next_search_point(self, belief: Belief, self_xy: ic.Point) -> ic.Point | None:
        points = ranked_seek_points(belief)
        for point in points:
            if ic.dist2(self_xy, point) <= ARRIVE_RADIUS_SQ:
                continue
            if any(ic.dist2(point, visited) <= ARRIVE_RADIUS_SQ for visited in self._visited_points):
                continue
            return ic.reachable_point(belief, point)
        for point in points:
            if ic.dist2(self_xy, point) > ARRIVE_RADIUS_SQ:
                return ic.reachable_point(belief, point)
        return None


def _trackable(belief: Belief, target: PlayerRecord) -> bool:
    return belief.last_tick - target.last_seen_tick <= TRACK_WINDOW_TICKS


def _station_near(belief: Belief, target_xy: ic.Point) -> ic.Point | None:
    """The task station nearest ``target_xy`` that keeps the target in sight."""

    tasks = belief.map.tasks if belief.map is not None else ()
    best: ic.Point | None = None
    best_d: int | None = None
    for index in range(len(tasks)):
        point = ic.task_point(belief, index)
        d = ic.dist2(point, target_xy)
        if FAKE_STATION_MIN_TARGET_DIST_SQ < d <= FAKE_STATION_TARGET_RADIUS_SQ and (best_d is None or d < best_d):
            best, best_d = point, d
    return best
