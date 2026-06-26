"""Shared imposter victim-selection and witness logic (design §7.2, §10).

Hunt commits to a *victim* and stalks it, striking only when the kill would go
**unwitnessed**. This module is the single source of truth for: which crewmate to
commit to (``select_victim`` — the most-isolated visible straggler, easiest to
finish off unseen), whether a kill on a given target is currently unwitnessed
(``unwitnessed``), and whether any victim is visible or trackable right now.

The witness bar is not fixed: the longer the imposter has been *able* to kill
without doing so, the more it relaxes (``kill_urgency_ticks``), so a cautious
imposter that never finds a clean opening still escalates rather than stalling
forever (design §10 "act with urgency").
"""

from __future__ import annotations

from players.crewrift.crewborg.nav import plan_route
from players.crewrift.crewborg.types import Belief, PlayerRecord

# Clearance (world px) required around a target at zero urgency: no other crewmate
# may be within this distance for the kill to count as unwitnessed.
BASE_ISOLATION_RADIUS = 48

# At zero urgency, another crewmate seen within this many ticks still counts as a
# potential witness; the window shrinks with urgency so stale sightings stop vetoing.
WITNESS_WINDOW_TICKS = 72

# Ticks of being able-to-kill-without-killing at which the witness bar reaches zero —
# i.e. the imposter will strike any victim regardless of witnesses (~10s at 24 Hz).
URGENCY_FULL_TICKS = 240

# A non-teammate seen within this many ticks is still "trackable" — Search can
# follow it to its last-known position even while it is briefly out of view.
TRACK_WINDOW_TICKS = 120

# If a fellow imposter was seen closer than us to a victim within this radius, treat
# that victim as "claimed" and prefer another target when one exists.
TEAMMATE_CLAIM_RADIUS = 80

# The kill cooldown's full length (ticks), used to estimate time-to-ready before we
# have measured a real cooldown from the binary HUD (design §7.2) and before the
# pre-game GAME INFO interstitial has taught the live value
# (``belief.kill_cooldown_config_ticks``). The game default — sim.nim
# KillCooldownTicks = 500, verified upstream 2026-06-10 (was 900 in older builds).
DEFAULT_KILL_COOLDOWN_TICKS = 500

# Enter Search this many ticks before the kill comes off cooldown. Search finds
# and follows a victim; Hunt only activates once the kill is ready and a victim is
# visible. Widened 100 → 160 for v4 to close the measured ~160-tick conversion
# gap vs truecrew:v14 (their imps_win episodes end at median tick 2846 vs our
# 3003; our teammate visibly out-kills us): start acquiring/shadowing the next
# victim earlier so the kill converts the moment the cooldown clears.
SEARCH_LEAD_TICKS = 160

# Backwards-compatible name for docs/tests that still refer to the old Hunt lead
# term. New code should use SEARCH_LEAD_TICKS.
HUNT_LEAD_TICKS = SEARCH_LEAD_TICKS


def kill_urgency_ticks(belief: Belief) -> int:
    """How long we have been able to kill without doing so (0 if not kill-ready)."""

    if not belief.self_kill_ready or belief.kill_ready_since_tick is None:
        return 0
    return max(0, belief.last_tick - belief.kill_ready_since_tick)


def kill_cooldown_duration_ticks(belief: Belief) -> int:
    """The kill cooldown's full length: measured > game-info config > game default.

    A cooldown actually watched run to ready (``kill_cooldown_estimate``) is the
    ground truth; otherwise the pre-game GAME INFO interstitial's advertised
    ``killCooldownTicks`` (``kill_cooldown_config_ticks``); otherwise the compiled
    game default.
    """

    return (
        belief.kill_cooldown_estimate
        or belief.kill_cooldown_config_ticks
        or DEFAULT_KILL_COOLDOWN_TICKS
    )


def ticks_until_kill_ready(belief: Belief) -> int:
    """Estimated ticks until the kill becomes available (0 if ready now).

    The HUD is binary (ready / cooldown, no countdown), so this reconstructs the
    countdown from the tracked cooldown start (`kill_cooldown_start_tick`) plus
    the cooldown length (:func:`kill_cooldown_duration_ticks`). With no cooldown
    start observed yet it assumes a full cooldown remains, so callers won't enter
    Search on no information. Note the cooldown only runs during Playing — the
    game clock (and the cooldown) pause during meetings (upstream 2026-06-10) —
    but ``kill_cooldown_start_tick`` is re-anchored on every meeting→Playing
    transition (the server resets the cooldown after each meeting), so the
    estimate never spans a meeting.
    """

    if belief.self_kill_ready:
        return 0
    duration = kill_cooldown_duration_ticks(belief)
    if belief.kill_cooldown_start_tick is None:
        return duration
    return max(0, belief.kill_cooldown_start_tick + duration - belief.last_tick)


def has_trackable_victim(belief: Belief) -> bool:
    """Whether any non-teammate has been seen recently enough for Search to follow.

    Kept as a useful readout; Hunt itself requires current visibility.
    """

    return any(
        entry.color not in belief.teammate_colors
        and entry.life_status != "dead"
        and belief.last_tick - entry.last_seen_tick <= TRACK_WINDOW_TICKS
        for entry in belief.roster.values()
    )


def visible_victims(belief: Belief) -> list[PlayerRecord]:
    """Live non-teammates visible on the current tick."""

    return [
        entry
        for entry in belief.roster.values()
        if entry.color not in belief.teammate_colors
        and entry.life_status != "dead"
        and entry.last_seen_tick == belief.last_tick
    ]


def has_visible_victim(belief: Belief) -> bool:
    """Whether a live non-teammate crewmate is visible right now."""

    return bool(visible_victims(belief))


def select_victim(belief: Belief) -> PlayerRecord | None:
    """The crewmate to commit to hunting: the most-isolated reachable visible
    crewmate (a straggler — easiest to finish off unwitnessed), tie-broken by
    nearest to us. ``None`` when no non-teammate is visible/reachable."""

    self_xy = _self_xy(belief)
    if self_xy is None:
        return None
    crew = visible_victims(belief)
    if not crew:
        return None
    candidates = crew
    if belief.nav is not None:
        candidates = [t for t in crew if plan_route(belief.nav, self_xy, (t.world_x, t.world_y))]
        if not candidates:
            return None
    unclaimed = [target for target in candidates if not _claimed_by_teammate(target, belief, self_xy)]
    if unclaimed:
        candidates = unclaimed
    # Hunter profile (strategy.hunter): a crewmate at the emergency button during
    # the stakeout window is walking in to reset our kill cooldown (sussyboi's
    # timed jam) — killing it denies the reset, so it outranks isolation.
    from players.crewrift.crewborg.strategy.hunter import (
        button_approachers,
        hunter_enabled,
        stakeout_window_active,
    )

    if hunter_enabled() and stakeout_window_active(belief):
        approachers = button_approachers(belief, candidates)
        if approachers:
            candidates = approachers
    # Prefer the most isolated (largest gap to its nearest other crewmate), then nearest.
    return max(candidates, key=lambda t: (_isolation(t, belief), -_dist2(self_xy, (t.world_x, t.world_y))))


def unwitnessed(belief: Belief, target: PlayerRecord) -> bool:
    """Whether killing ``target`` now would go unseen, at the current urgency level."""

    frac = min(1.0, kill_urgency_ticks(belief) / URGENCY_FULL_TICKS)
    radius_sq = (BASE_ISOLATION_RADIUS * (1.0 - frac)) ** 2
    window = int(WITNESS_WINDOW_TICKS * (1.0 - frac))
    return _is_unwitnessed(target, belief, radius_sq, window)


def _isolation(target: PlayerRecord, belief: Belief) -> float:
    """Distance² to the nearest *other* live non-teammate — higher means more isolated."""

    target_xy = (target.world_x, target.world_y)
    gaps = [
        _dist2(target_xy, (o.world_x, o.world_y))
        for o in belief.roster.values()
        if o.color != target.color and o.color not in belief.teammate_colors and o.life_status != "dead"
    ]
    return min(gaps) if gaps else float("inf")


def _is_unwitnessed(target: PlayerRecord, belief: Belief, radius_sq: float, window: int) -> bool:
    """Whether no live non-teammate crewmate is close enough (and recent enough) to see the kill."""

    target_xy = (target.world_x, target.world_y)
    for other in belief.roster.values():
        if other.color == target.color or other.color in belief.teammate_colors:
            continue  # the victim itself and fellow imposters are never witnesses
        if other.life_status == "dead":
            continue  # a dead crewmate cannot witness the kill
        if belief.last_tick - other.last_seen_tick > window:
            continue  # last seen too long ago to credibly still be watching
        if _dist2(target_xy, (other.world_x, other.world_y)) <= radius_sq:
            return False
    return True


def _claimed_by_teammate(target: PlayerRecord, belief: Belief, self_xy: tuple[int, int]) -> bool:
    target_xy = (target.world_x, target.world_y)
    self_dist = _dist2(self_xy, target_xy)
    for teammate in belief.roster.values():
        if teammate.color not in belief.teammate_colors:
            continue
        if teammate.life_status == "dead":
            continue
        if belief.last_tick - teammate.last_seen_tick > TRACK_WINDOW_TICKS:
            continue
        teammate_dist = _dist2((teammate.world_x, teammate.world_y), target_xy)
        if teammate_dist < self_dist and teammate_dist <= TEAMMATE_CLAIM_RADIUS**2:
            return True
    return False


def _self_xy(belief: Belief) -> tuple[int, int] | None:
    if belief.self_world_x is None or belief.self_world_y is None:
        return None
    return belief.self_world_x, belief.self_world_y


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
