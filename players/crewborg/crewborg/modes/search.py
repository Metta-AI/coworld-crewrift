"""Search mode — the imposter's always-on seeking stance (design §7.2; reworked 2026-07-01).

Its job is to keep us NEAR crew so a kill window opens — **always moving with intent, never
parking.** A 5-state FSM (see the imposter-FSM doc §8):

  PICK_ROOM    Choose a reachable room to sweep, biased toward where crew are. **Always
               picks a room — never idles** (fallback ladder below). -> GO_TO_ROOM
  GO_TO_ROOM   Navigate to the room centre. Seeing ANY live non-teammate crewmate — room
               OR hallway — -> FOLLOW it (so we pick up hallway encounters en route).
               Arriving in the room -> SEARCH_ROOM.
  SEARCH_ROOM  Sweep the room's interior so crew hidden from the door are found. Crew in
               the room -> WATCH; a crewmate seen elsewhere -> FOLLOW; swept empty ->
               PICK_ROOM.
  WATCH        Only entered with crew confirmed in the room. MULTIPLE crew visible -> hold
               the vantage seeing the most (recomputed as they move). A SINGLE crew -> close
               on it: a task site beside it if one is near, else approach to
               ``kill_range + 15px`` (just outside kill range, poised to strike). Self-loops
               (holds) only while >=1 crew is in view; the LAST crewmate leaving view ->
               FOLLOW; no crew in view AND none seen to leave -> PICK_ROOM (rare fallback).
  FOLLOW       Persistent chase of a leaver using path prediction — keeps going down the
               predicted hallway while the target is occluded, up to FOLLOW_LOST_TICKS (240
               for a hard-commander target). While the target is visible we chase its live
               position; the moment we are in the SAME room as it (we've run it down) we hand
               off to SEARCH_ROOM (-> WATCH). When the follow instead ends occluded (lost past
               the window) we re-scan the room we ended up in (-> SEARCH_ROOM) or -> PICK_ROOM.

Kill hand-off is automatic and lives in the selector (``rule_based``): the instant the kill
is ready and a victim is visible, the strategy gate switches to Hunt. So Search never idles a
ready kill away — a lone target is *approached* to within a step, a crowd is *watched* only
until one peels off. The only ``idle`` this mode can emit is the deliberate multi-crew
vantage hold (and the startup no-op before the camera/map exist).

Never follows the teammate imposter. The path predictor is fed only what we actually see.
"""

from __future__ import annotations

import math
import os
import random

from crewborg.agent_tracking import best_seek_point, room_occupancy
from crewborg.modes import imposter_common as ic
from crewborg.map.types import Room
from crewborg.nav import _segment_clear
from crewborg.strategy.commander.bias import commander_of
from crewborg.strategy.path_prediction import PathPredictor
from crewborg.types import ActionState, Belief, Intent, PlayerRecord
from players.player_sdk import EmptyModeParams, Mode, ModeParams

ARRIVE_RADIUS_SQ = 24**2
# Drop a follow once the target has been unseen this long with no live prediction.
FOLLOW_LOST_TICKS = 120
# Hard commander target-player follows get a little more persistence before Search gives up.
COMMANDER_FOLLOW_LOST_TICKS = 240
# A crewmate counts as "still watchable" from a vantage if seen within this window.
WATCH_RECENT_TICKS = 36
# Line-of-sight range (px) for vantage scoring — generous; LOS through walls is the real gate.
VANTAGE_RANGE = 360
VANTAGE_RANGE_SQ = VANTAGE_RANGE**2
# Coarse grid step (px) for candidate vantage points within a room.
VANTAGE_STEP = 40
# Recompute the vantage at most this often (crew move; LOS scans cost a little).
VANTAGE_REFRESH_TICKS = 18
# Only move to a new vantage if it sees at least this many MORE crew (hysteresis).
VANTAGE_SWITCH_MARGIN = 1
# Single-crew close-in target: kill_range (20px) + 15px margin — just outside kill range,
# poised to dart in the instant the cooldown lifts (the selector flips us to Hunt then).
SINGLE_APPROACH_PX = 35
# A task station this close (px) to a lone target is a natural place to stand and blend.
TASK_SITE_NEAR_SQ = 56**2


def _wenv(name: str, default: float) -> float:
    """A tunable PICK_ROOM weight/constant, overridable via env for offline sweeps."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- PICK_ROOM scoring weights (all env-tunable so they can be swept/learned) --------------
# Occupancy (go where crew are expected) is the strongest positive signal. Unvisitedness is
# also strong and GROWS with time-since-visit so peripheral rooms get swept occasionally.
# Recency is a strong penalty that DECAYS quickly (anti-ping-pong between two rooms).
W_OCCUPANCY = _wenv("CREWBORG_PICKROOM_W_OCCUPANCY", 3.0)   # where crew are expected — strongest
W_UNVISITED = _wenv("CREWBORG_PICKROOM_W_UNVISITED", 2.5)   # long-unvisited rooms — grows over time
W_RECENCY = _wenv("CREWBORG_PICKROOM_W_RECENCY", 3.0)       # just-visited penalty — decays fast
W_DISTANCE = _wenv("CREWBORG_PICKROOM_W_DISTANCE", 1.0)     # discount far rooms (time-bound windows)
W_TEAMMATE = _wenv("CREWBORG_PICKROOM_W_TEAMMATE", 1.5)     # don't converge with our co-imposter
W_TASKBONUS = _wenv("CREWBORG_PICKROOM_W_TASKBONUS", 0.4)   # small blend bonus for task rooms
W_COMMANDER = _wenv("CREWBORG_PICKROOM_W_COMMANDER", 1.0)   # soft commander hunt-room nudge
# Recency penalty is gone after ~this many ticks; unvisitedness maxes out after ~this many.
RECENCY_DECAY_TICKS = _wenv("CREWBORG_PICKROOM_RECENCY_DECAY", 150.0)
UNVISITED_FULL_TICKS = _wenv("CREWBORG_PICKROOM_UNVISITED_FULL", 800.0)


class SearchMode(Mode[Belief, ActionState, Intent]):
    name = "search"
    params_type = EmptyModeParams

    def __init__(self, params: ModeParams | None = None) -> None:
        super().__init__(params)
        self._state = "pick_room"
        self._target_room: str | None = None
        self._prev_room: str | None = None          # avoid immediately re-picking
        self._goto_point: ic.Point | None = None
        self._room_crew: set[str] = set()           # crew colors seen inside the watched room
        self._scan_points: list[ic.Point] = []      # SEARCH_ROOM sweep waypoints
        self._scan_idx = 0
        self._vantage: ic.Point | None = None       # current watch position (max crew in sight)
        self._vantage_tick: int | None = None       # when the vantage was last recomputed
        self._follow_color: str | None = None
        self._predictor: PathPredictor | None = None
        self._last_seen_tick: int | None = None
        self._last_visit_tick: dict[str, int] = {}   # room name -> last tick we were inside it
        self._rng = random.Random(0xC0FFEE)

    # --- entry ----------------------------------------------------------------
    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        self_xy = ic.self_xy(belief)
        if self_xy is None or belief.map is None:
            # The only unavoidable idle: no camera / map yet (a startup no-op).
            return Intent(kind="idle", reason="no self position / map")

        # Remember which room we're standing in — feeds PICK_ROOM's recency/unvisited scoring.
        here = ic.room_containing(belief, self_xy)
        if here is not None:
            self._last_visit_tick[here.name] = belief.last_tick

        if self._state == "go_to_room":
            return self._go_to_room(belief, self_xy)
        if self._state == "search_room":
            return self._search_room(belief, self_xy)
        if self._state == "watch":
            return self._watch(belief, self_xy)
        if self._state == "follow":
            return self._follow(belief, self_xy)
        return self._pick_room(belief, self_xy)

    # --- PICK_ROOM ------------------------------------------------------------
    def _pick_room(self, belief: Belief, self_xy: ic.Point) -> Intent:
        """Score every reachable room and commit to the best — NEVER idle. The score blends
        (tunable, env-overridable weights): expected crew occupancy (strongest), unvisitedness
        (grows with time-since-visit -> peripheral coverage), a fast-decaying recency penalty
        (anti ping-pong), travel cost, teammate-pressure subtraction, a task-room blend bonus,
        and a soft commander hunt-room nudge. Hard commander directives stay hard constraints."""

        rooms = list(belief.map.rooms)
        if not rooms:
            # Degenerate (no rooms) — head to the hottest occupancy cell so we still MOVE.
            seek = best_seek_point(belief)
            point = ic.reachable_point(belief, seek) if seek is not None else self_xy
            self._target_room = None
            self._goto_point = point
            self._state = "go_to_room"
            return Intent(kind="navigate_to", point=point, reason="search: no rooms — seek crew")

        cmd = commander_of(belief)
        # A HARD commander hunt-room is an override, not a nudge.
        if cmd is not None and cmd.strength == "hard" and cmd.hunt_room is not None:
            forced = self._room(belief, cmd.hunt_room)
            if forced is not None:
                return self._commit_room(belief, self_xy, forced)

        current = ic.room_containing(belief, self_xy)
        current_name = current.name if current is not None else None
        start = ic.starting_room(belief)
        start_name = start.name if start is not None else None
        avoid = cmd.avoid_room if cmd is not None else None
        # Prefer to exclude the room we're standing in, the spawn room, and a commander
        # avoid-room; peel those back only if excluding them would leave nothing to pick.
        candidates: list[Room] = []
        for exclude in ({current_name, start_name, avoid}, {current_name, start_name}, {current_name}, set()):
            candidates = [room for room in rooms if room.name not in exclude]
            if candidates:
                break

        occ = room_occupancy(belief)
        max_density = max((crew for crew, _ in occ.values()), default=0.0) or 1.0
        diag = math.hypot(belief.map.width, belief.map.height) or 1.0
        best = max(candidates, key=lambda room: (
            self._room_score(belief, room, self_xy, occ, max_density, diag, cmd),
            -ic.dist2(self_xy, (room.center.x, room.center.y)),
            room.name,
        ))
        return self._commit_room(belief, self_xy, best)

    def _room_score(self, belief, room, self_xy, occ, max_density, diag, cmd) -> float:
        now = belief.last_tick
        crew_density, teammate_density = occ.get(room.name, (0.0, 0.0))
        occupancy = crew_density / max_density                    # 0..1 — where crew are expected
        teammate = teammate_density / max_density                 # 0..1 — subtract (don't converge)
        last = self._last_visit_tick.get(room.name)
        if last is None:
            unvisited, recency = 1.0, 0.0                          # never seen -> maximally worth a look
        else:
            age = now - last
            unvisited = min(1.0, age / UNVISITED_FULL_TICKS)      # grows with time since we visited
            recency = max(0.0, 1.0 - age / RECENCY_DECAY_TICKS)   # strong right after, decays fast
        distance = math.hypot(room.center.x - self_xy[0], room.center.y - self_xy[1]) / diag
        score = (
            W_OCCUPANCY * occupancy
            + W_UNVISITED * unvisited
            - W_RECENCY * recency
            - W_DISTANCE * distance
            - W_TEAMMATE * teammate
            + (W_TASKBONUS if self._room_task_indices(belief, room) else 0.0)
        )
        if cmd is not None and cmd.hunt_room == room.name:
            score += W_COMMANDER                                   # soft hunt-room nudge
        return score

    def _commit_room(self, belief: Belief, self_xy: ic.Point, room: Room) -> Intent:
        self._target_room = room.name
        # Head to the room CENTRE (go fully inside to check it), not a task spot by the door.
        self._goto_point = ic.reachable_point(belief, (room.center.x, room.center.y))
        self._room_crew = set()
        self._vantage = None
        self._vantage_tick = None
        self._state = "go_to_room"
        return self._go_to_room(belief, self_xy)

    # --- GO_TO_ROOM -----------------------------------------------------------
    def _go_to_room(self, belief: Belief, self_xy: ic.Point) -> Intent:
        # Seeing ANY live non-teammate — room or hallway — is worth chasing right now.
        leaver = self._nearest_visible_crew(belief, self_xy)
        if leaver is not None:
            return self._begin_follow(belief, leaver)

        room = self._room(belief, self._target_room)
        if room is None:
            # Seek-a-point fallback (no room target — the degenerate PICK_ROOM branch).
            if self._goto_point is not None and ic.dist2(self_xy, self._goto_point) > ARRIVE_RADIUS_SQ:
                return Intent(kind="navigate_to", point=self._goto_point, reason="search: heading toward crew")
            self._state = "pick_room"
            return self._pick_room(belief, self_xy)
        if self._goto_point is None:
            self._state = "pick_room"
            return self._pick_room(belief, self_xy)
        if ic.dist2(self_xy, self._goto_point) <= ARRIVE_RADIUS_SQ or ic.in_rect(self_xy, room):
            return self._enter_search_room(belief, self_xy, room.name)
        return Intent(kind="navigate_to", point=self._goto_point, reason="search: heading to a room to scan")

    # --- SEARCH_ROOM ----------------------------------------------------------
    def _enter_search_room(self, belief: Belief, self_xy: ic.Point, room_name: str) -> Intent:
        self._target_room = room_name
        room = self._room(belief, room_name)
        self._scan_points = self._room_scan_points(belief, room, self_xy) if room is not None else []
        self._scan_idx = 0
        self._room_crew = set()
        self._vantage = None
        self._vantage_tick = None
        self._state = "search_room"
        return self._search_room(belief, self_xy)

    def _search_room(self, belief: Belief, self_xy: ic.Point) -> Intent:
        room = self._room(belief, self._target_room)
        if room is None:
            self._state = "pick_room"
            return self._pick_room(belief, self_xy)

        crew_in = self._crew_in_room(belief, room)
        if crew_in:
            self._room_crew = {c.color for c in crew_in}
            self._state = "watch"
            return self._watch(belief, self_xy)

        # A crewmate visible elsewhere (hallway / adjacent room) — go follow it.
        leaver = self._nearest_visible_crew(belief, self_xy)
        if leaver is not None:
            return self._begin_follow(belief, leaver)

        # Keep sweeping the room's interior scan points so hidden crew are revealed.
        while self._scan_idx < len(self._scan_points):
            point = self._scan_points[self._scan_idx]
            if ic.dist2(self_xy, point) <= ARRIVE_RADIUS_SQ:
                self._scan_idx += 1
                continue
            return Intent(kind="navigate_to", point=point, reason="search: scanning the room for crew")

        # Swept the whole room, nobody here.
        self._prev_room = self._target_room
        self._state = "pick_room"
        return self._pick_room(belief, self_xy)

    def _room_scan_points(self, belief: Belief, room: Room, self_xy: ic.Point) -> list[ic.Point]:
        """A short ordered set of reachable interior points that between them break the
        room's line-of-sight occlusion, so crew tucked out of the door's view are found."""

        raw = [
            (room.x + room.w * 0.25, room.y + room.h * 0.25),
            (room.x + room.w * 0.75, room.y + room.h * 0.25),
            (room.x + room.w * 0.75, room.y + room.h * 0.75),
            (room.x + room.w * 0.25, room.y + room.h * 0.75),
            (room.center.x, room.center.y),
        ]
        points: list[ic.Point] = []
        for gx, gy in raw:
            point = ic.reachable_point(belief, (int(gx), int(gy)))
            if ic.in_rect(point, room) and point not in points:
                points.append(point)
        points.sort(key=lambda p: ic.dist2(self_xy, p))
        return points

    # --- WATCH ----------------------------------------------------------------
    def _watch(self, belief: Belief, self_xy: ic.Point) -> Intent:
        room = self._room(belief, self._target_room)
        if room is None:
            self._state = "pick_room"
            return self._pick_room(belief, self_xy)

        visible_here = self._crew_in_room(belief, room)
        if visible_here:
            self._room_crew |= {c.color for c in visible_here}
            if len(visible_here) >= 2:
                # MULTIPLE crew: hold the in-room vantage with line-of-sight to the most of
                # them (recomputed as they move). This is the one deliberate hold in Search.
                self._refresh_vantage(belief, room, self_xy)
                if self._vantage is not None and ic.dist2(self_xy, self._vantage) > ARRIVE_RADIUS_SQ:
                    return Intent(kind="navigate_to", point=self._vantage, reason="search: moving to a vantage over the crew")
                return Intent(kind="idle", reason="search: watching multiple crew from a vantage")
            # SINGLE crew: don't watch from afar — close on it so Hunt can strike at ready.
            target = visible_here[0]
            point = self._single_target_point(belief, target, self_xy)
            return Intent(kind="navigate_to", point=point, reason="search: closing on the lone crewmate")

        # No crew in view right now. Did the last one just leave? Then chase it.
        leaver = self._a_crewmate_left(belief, room)
        if leaver is not None:
            return self._begin_follow(belief, leaver)

        # No crew visible and none seen to leave — re-pick (rare: WATCH starts with crew).
        self._prev_room = self._target_room
        self._state = "pick_room"
        return self._pick_room(belief, self_xy)

    def _single_target_point(self, belief: Belief, target: PlayerRecord, self_xy: ic.Point) -> ic.Point:
        """Where to stand to shadow a lone crewmate: a nearby task site (natural blend),
        else a point ``SINGLE_APPROACH_PX`` from the target on our side (just out of range)."""

        target_xy = (target.world_x, target.world_y)
        site = self._nearest_task_site(belief, target_xy)
        if site is not None:
            return site
        dx = self_xy[0] - target_xy[0]
        dy = self_xy[1] - target_xy[1]
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return ic.reachable_point(belief, target_xy)
        px = target_xy[0] + dx / dist * SINGLE_APPROACH_PX
        py = target_xy[1] + dy / dist * SINGLE_APPROACH_PX
        return ic.reachable_point(belief, (int(px), int(py)))

    def _nearest_task_site(self, belief: Belief, xy: ic.Point) -> ic.Point | None:
        tasks = belief.map.tasks if belief.map is not None else ()
        best: ic.Point | None = None
        best_d = TASK_SITE_NEAR_SQ + 1
        for task in tasks:
            task_xy = (task.center.x, task.center.y)
            d = ic.dist2(xy, task_xy)
            if d < best_d:
                best_d, best = d, task_xy
        return best if best is not None and best_d <= TASK_SITE_NEAR_SQ else None

    def _refresh_vantage(self, belief: Belief, room: Room, self_xy: ic.Point) -> None:
        """(Re)pick the point in ``room`` with line-of-sight to the most watchable crew,
        throttled, with hysteresis so we don't jitter between equal vantages."""

        if (
            self._vantage is not None
            and self._vantage_tick is not None
            and belief.last_tick - self._vantage_tick < VANTAGE_REFRESH_TICKS
        ):
            return
        crew_xy = self._watchable_crew_xy(belief)
        if not crew_xy:
            return
        best = self._best_vantage(belief, room, crew_xy, self_xy)
        if best is None:
            return
        best_point, best_score = best
        current_score = self._visible_count(belief, self._vantage, crew_xy) if self._vantage else -1
        if self._vantage is None or best_score >= current_score + VANTAGE_SWITCH_MARGIN:
            self._vantage = best_point
        self._vantage_tick = belief.last_tick

    def _best_vantage(self, belief: Belief, room: Room, crew_xy, self_xy):
        """Argmax over a coarse grid of reachable in-room points of how many crew each has
        line-of-sight to. Ties broken toward staying put (less movement)."""

        best_point = None
        best_score = -1
        best_move = 0
        x0, y0 = room.x, room.y
        for gx in range(x0 + VANTAGE_STEP // 2, x0 + room.w, VANTAGE_STEP):
            for gy in range(y0 + VANTAGE_STEP // 2, y0 + room.h, VANTAGE_STEP):
                point = ic.reachable_point(belief, (gx, gy))
                if not ic.in_rect(point, room):
                    continue
                score = self._visible_count(belief, point, crew_xy)
                move = ic.dist2(self_xy, point)
                if score > best_score or (score == best_score and move < best_move):
                    best_point, best_score, best_move = point, score, move
        return (best_point, best_score) if best_point is not None else None

    def _visible_count(self, belief: Belief, point: ic.Point | None, crew_xy) -> int:
        """How many of ``crew_xy`` have clear line-of-sight from ``point`` in range."""

        if point is None or belief.nav is None:
            return 0
        walk = belief.nav.walkability
        n = 0
        for cxy in crew_xy:
            if ic.dist2(point, cxy) <= VANTAGE_RANGE_SQ and _segment_clear(walk, point, cxy):
                n += 1
        return n

    def _watchable_crew_xy(self, belief: Belief) -> list[ic.Point]:
        """Recently-seen live non-teammate crew positions — who we want to keep in view."""

        out = []
        for rec in belief.roster.values():
            if rec.color in belief.teammate_colors or rec.life_status == "dead":
                continue
            if belief.last_tick - rec.last_seen_tick <= WATCH_RECENT_TICKS:
                out.append((rec.world_x, rec.world_y))
        return out

    # --- FOLLOW ---------------------------------------------------------------
    def _follow(self, belief: Belief, self_xy: ic.Point) -> Intent:
        if self._follow_color is None or self._predictor is None:
            self._state = "pick_room"
            return self._pick_room(belief, self_xy)
        target = belief.roster.get(self._follow_color)
        if target is None or target.life_status == "dead" or self._follow_color in belief.teammate_colors:
            return self._stop_follow(belief, self_xy)

        visible = target.last_seen_tick == belief.last_tick
        observed = (target.world_x, target.world_y) if visible else None
        self._predictor.observe(belief.last_tick, observed)
        if visible:
            self._last_seen_tick = belief.last_tick
            # Caught up ("settled"): we're in the SAME room as the visible target -> hand off
            # to SEARCH_ROOM. It re-scans the room (picks up anyone else) and routes to WATCH,
            # so a lone target is approached to ~35px rather than walked onto. On a real
            # (corridor'd) map this only fires once we've actually run the leaver down into a
            # room; while chasing through hallways we keep following its live position.
            our_room = ic.room_containing(belief, self_xy)
            if our_room is not None and ic.in_rect((target.world_x, target.world_y), our_room):
                return self._enter_search_room(belief, self_xy, our_room.name)
            return Intent(kind="navigate_to", point=(target.world_x, target.world_y),
                          reason="search: following a leaver (visible)")

        # Out of view: chase down the predicted hallway toward the top route's position.
        if self._last_seen_tick is not None and belief.last_tick - self._last_seen_tick > self._follow_lost_ticks(belief):
            return self._stop_follow(belief, self_xy)
        best = self._predictor.best()
        if best is None:
            return self._stop_follow(belief, self_xy)
        return Intent(kind="navigate_to", point=tuple(best.pred_pos),
                      reason="search: chasing predicted path (occluded)")

    def _begin_follow(self, belief: Belief, leaver: PlayerRecord) -> Intent:
        self._follow_color = leaver.color
        self._predictor = PathPredictor(nav=belief.nav, map=belief.map)
        self._last_seen_tick = belief.last_tick if leaver.last_seen_tick == belief.last_tick else None
        self._state = "follow"
        if leaver.last_seen_tick == belief.last_tick:
            self._predictor.observe(belief.last_tick, (leaver.world_x, leaver.world_y))
        return Intent(kind="navigate_to", point=(leaver.world_x, leaver.world_y),
                      reason="search: a crewmate is in view — follow")

    def _stop_follow(self, belief: Belief, self_xy: ic.Point) -> Intent:
        # Re-scan the room we ended up in (finds hidden crew); else pick a new room.
        self._follow_color = None
        self._predictor = None
        self._last_seen_tick = None
        room = ic.room_containing(belief, self_xy)
        if room is not None:
            return self._enter_search_room(belief, self_xy, room.name)
        self._state = "pick_room"
        return self._pick_room(belief, self_xy)

    def _follow_lost_ticks(self, belief: Belief) -> int:
        cmd = commander_of(belief)
        if (
            cmd is not None
            and cmd.strength == "hard"
            and self._follow_color is not None
            and self._follow_color == cmd.target_player
        ):
            return COMMANDER_FOLLOW_LOST_TICKS
        return FOLLOW_LOST_TICKS

    # --- helpers --------------------------------------------------------------
    def _nearest_visible_crew(self, belief: Belief, self_xy: ic.Point) -> PlayerRecord | None:
        """The nearest live non-teammate crewmate visible THIS tick, or ``None``."""

        crew = ic.visible_crew(belief)
        if not crew:
            return None
        return min(crew, key=lambda c: ic.dist2(self_xy, (c.world_x, c.world_y)))

    def _crew_in_room(self, belief: Belief, room: Room) -> list[PlayerRecord]:
        return [c for c in ic.visible_crew(belief) if ic.in_rect((c.world_x, c.world_y), room)]

    def _a_crewmate_left(self, belief: Belief, room: Room) -> PlayerRecord | None:
        """A crew member we had seen inside ``room`` that is now leaving — visible outside
        the room, or no longer visible (likely out a door). Returns the one to follow, or
        ``None``."""

        leavers = []
        for color in self._room_crew:
            if color in belief.teammate_colors:
                continue
            rec = belief.roster.get(color)
            if rec is None or rec.life_status == "dead":
                continue
            inside = ic.in_rect((rec.world_x, rec.world_y), room)
            recently = belief.last_tick - rec.last_seen_tick
            if not inside and recently <= 8:
                leavers.append(rec)  # last-known position is now outside the watched room
        if not leavers:
            return None
        cmd = commander_of(belief)
        target_player = cmd.target_player if cmd is not None else None
        return next((rec for rec in leavers if rec.color == target_player), leavers[0])

    def _room_task_indices(self, belief: Belief, room: Room) -> list[int]:
        tasks = belief.map.tasks if belief.map is not None else ()
        return [i for i in range(len(tasks)) if ic.in_rect((tasks[i].center.x, tasks[i].center.y), room)]

    def _room(self, belief: Belief, name: str | None) -> Room | None:
        if name is None or belief.map is None:
            return None
        return next((r for r in belief.map.rooms if r.name == name), None)
