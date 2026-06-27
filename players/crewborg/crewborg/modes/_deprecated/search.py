"""DEPRECATED — DO NOT USE (cold storage, retired 2026-06-24). See ``_deprecated/__init__.py``.
Replaced by a group-follow → peel-off seeking approach; this version walked occupancy
density hot-spots (the crowd centroid) and committed to the single most-isolated visible
crew up front (a gamble). Kept for reference only; not imported anywhere.

Search mode: pre-kill target acquisition for imposters.

Search owns the lead window before the kill cooldown is ready. It first walks
ranked occupancy points to find crew. Once a non-teammate crewmate is visible, it
commits to that target and follows its live or last-seen position until the
strategy can switch to Hunt when the kill is ready and the target is visible.
"""

from __future__ import annotations

from crewborg.agent_tracking import ranked_seek_points
from crewborg.modes import imposter_common as ic
from crewborg.strategy.opportunity import TRACK_WINDOW_TICKS, select_victim
from crewborg.types import ActionState, Belief, Intent, PlayerRecord
from players.player_sdk import EmptyModeParams, Mode, ModeParams

ARRIVE_RADIUS_SQ = 24**2
MAX_VISITED_POINTS = 6


class SearchMode(Mode[Belief, ActionState, Intent]):
    name = "search"
    params_type = EmptyModeParams

    def __init__(self, params: ModeParams | None = None) -> None:
        super().__init__(params)
        self._target_color: str | None = None
        self._search_point: ic.Point | None = None
        self._visited_points: list[ic.Point] = []

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        self_xy = ic.self_xy(belief)
        if self_xy is None:
            return Intent(kind="idle", reason="no self position")

        target = self._target(belief)
        if target is not None:
            self._target_color = target.color
            return Intent(kind="navigate_to", point=(target.world_x, target.world_y), reason="search: following visible target")

        if self._target_color is not None:
            target = belief.roster.get(self._target_color)
            if target is not None and target.life_status != "dead" and _trackable(belief, target):
                target_xy = (target.world_x, target.world_y)
                if ic.dist2(self_xy, target_xy) > ARRIVE_RADIUS_SQ:
                    return Intent(kind="navigate_to", point=target_xy, reason="search: following last-seen target")
            self._target_color = None

        return self._search_occupancy(belief, self_xy)

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
            return Intent(kind="idle", reason="search: no occupancy target")
        return Intent(kind="navigate_to", point=self._search_point, reason="searching likely crew occupancy")

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
