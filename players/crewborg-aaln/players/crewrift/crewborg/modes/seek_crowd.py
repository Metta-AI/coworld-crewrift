"""Seek Crowd mode: break a shadow-kill setup by escaping the follower.

The crewmate response to being tailed (``strategy.shadow``): a follower has
been glued to us for several seconds with no other crew nearby — exactly the
6-12 s shadow-kill approach behind most of our crewmate deaths.

**Buzzer-field re-design (iter-2, 2026-06-12).** The original response routed to
the nearest crowd, but the iter-1 hosted eval showed that crew survival
*regressed* when that response fired more — in this field the killers camp the
central hub, so running to a crowd runs us into the kill zone. The full-replay
ground truth had the killer glued within 120 px for a median 249 ticks before
striking at-ready. So the primary response is now to STEER AWAY from the
follower (the ``flee_from`` keep-away primitive), opening the killer's approach
distance and buying the kill clock time; the crowd-seek route is kept only as a
fallback when the tail is momentarily unlocatable.

The mode only picks *where* to go; movement mechanics live in the action
resolver (design rules). Triggering/hysteresis live in the rule-based strategy
via ``strategy.shadow.active_tail``.
"""

from __future__ import annotations

from players.crewrift.crewborg.agent_tracking import ranked_seek_points
from players.crewrift.crewborg.modes import imposter_common as ic
from players.crewrift.crewborg.types import ActionState, Belief, Intent
from players.player_sdk import Mode, ModeParams

# A roster sighting at most this old (~10 s) still counts as "known crew there".
CROWD_SIGHTING_MAX_AGE_TICKS = 240
# Don't route to a crowd point that sits closer to the tail's last position than
# this — running *through* the follower is not an escape.
AVOID_TAIL_RADIUS_SQ = 96**2


class SeekCrowdParams(ModeParams):
    """Strategy-supplied parameters: the color of the player tailing us."""

    avoid_color: str | None = None


class SeekCrowdMode(Mode[Belief, ActionState, Intent]):
    name = "seek_crowd"
    params_type = SeekCrowdParams

    def __init__(self, params: ModeParams | None = None) -> None:
        super().__init__(params or SeekCrowdParams())

    def is_legal(self, belief: Belief) -> bool:
        return belief.phase == "Playing"

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        self_xy = ic.self_xy(belief)
        if self_xy is None:
            return Intent(kind="idle", reason="seek crowd: no self position")
        params = self.params
        avoid = params.avoid_color if isinstance(params, SeekCrowdParams) else None

        # Buzzer-field re-design (sussybuster_iteration iter-2, 2026-06-12):
        # iter-1 widened the seek-crowd response and crew survival REGRESSED
        # 38%→26% — running TO crowds walks us into the central hub where the
        # buzzer field's killers camp (Science Bay/Storage deaths jumped). The
        # ground truth: the killer glues to us for a median 249 ticks then
        # strikes at-ready, and crowds are the danger, not the refuge. So the
        # primary tail response is now to STEER AWAY from the follower (the
        # ``flee_from`` keep-away primitive) — increase the killer's approach
        # distance and buy the kill clock time — instead of routing into a
        # witness cluster. Crowd-seek is kept only as a fallback when the tail
        # is momentarily unlocatable. Movement mechanics stay in the resolver.
        if avoid is not None:
            record = belief.roster.get(avoid)
            if record is not None and record.life_status != "dead":
                return Intent(kind="flee_from", target_color=avoid, reason="tailed: breaking away from follower")

        point = self._nearest_crew_point(belief, self_xy, avoid)
        if point is not None:
            return Intent(kind="navigate_to", point=point, reason="tailed: moving to nearest crew")
        point = self._busy_point(belief, self_xy, avoid)
        if point is not None:
            return Intent(kind="navigate_to", point=point, reason="tailed: moving to busy room")
        if belief.map is not None:
            # Last resort: the button room — the most-trafficked spot on the map.
            home = ic.reachable_point(belief, (belief.map.home.x, belief.map.home.y))
            return Intent(kind="navigate_to", point=home, reason="tailed: falling back to spawn room")
        return Intent(kind="idle", reason="seek crowd: nowhere to go")

    def _nearest_crew_point(
        self, belief: Belief, self_xy: ic.Point, avoid: str | None
    ) -> ic.Point | None:
        """The nearest recently seen live player who is not the tail itself."""

        avoid_xy = self._avoid_xy(belief, avoid)
        candidates: list[tuple[int, ic.Point]] = []
        for record in belief.roster.values():
            if record.color == avoid or record.life_status == "dead":
                continue
            if belief.last_tick - record.last_seen_tick > CROWD_SIGHTING_MAX_AGE_TICKS:
                continue
            point = (record.world_x, record.world_y)
            if avoid_xy is not None and ic.dist2(point, avoid_xy) <= AVOID_TAIL_RADIUS_SQ:
                continue
            candidates.append((ic.dist2(self_xy, point), point))
        if not candidates:
            return None
        return ic.reachable_point(belief, min(candidates)[1])

    def _busy_point(self, belief: Belief, self_xy: ic.Point, avoid: str | None) -> ic.Point | None:
        """The hottest expected-occupancy point away from the tail."""

        del self_xy
        avoid_xy = self._avoid_xy(belief, avoid)
        for point in ranked_seek_points(belief):
            if avoid_xy is not None and ic.dist2(point, avoid_xy) <= AVOID_TAIL_RADIUS_SQ:
                continue
            return ic.reachable_point(belief, point)
        return None

    def _avoid_xy(self, belief: Belief, avoid: str | None) -> ic.Point | None:
        if avoid is None:
            return None
        record = belief.roster.get(avoid)
        if record is None:
            return None
        return record.world_x, record.world_y
