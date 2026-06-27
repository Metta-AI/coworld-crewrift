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

This module also reconstructs the kill cooldown from the binary HUD
(``ticks_until_kill_ready``) and owns the Recon pre-position trigger
(``recon_window`` / ``most_recent_victim``).

Collaborators
-------------
Relies on:
  - ``nav.plan_route`` — reachability filter in ``select_victim`` (only commit to a
    victim we can actually route to).
  - ``types.Belief`` — reads the roster, ``teammate_colors``, self position, and the
    cooldown-tracking fields (``self_kill_ready``, ``kill_ready_since_tick``,
    ``kill_cooldown_start_tick``, ``kill_cooldown_estimate``). Reads only; nothing stored.
Used by:
  - ``strategy.rule_based`` — the imposter mode gates (``has_visible_victim``,
    ``ticks_until_kill_ready``, ``recon_window``, ``most_recent_victim``).
  - ``modes.hunt`` (``select_victim`` / ``unwitnessed`` / ``visible_victims``),
    ``modes.recon`` (``most_recent_victim``), ``strategy.commander.context`` and
    ``events.py`` (``ticks_until_kill_ready`` / ``kill_urgency_ticks`` readouts).
Emits / touches: nothing — pure queries over belief. The selection it returns is acted
  on by the mode objects, not here.

Modifying this file: these are read-only helpers; keep them side-effect-free. The
witness relaxation (``unwitnessed`` shrinking ``radius_sq``/``window`` with urgency) and
the isolation-then-nearest victim ranking are the kill-conversion levers — tune them
against A/Bs, not by eye.
"""

from __future__ import annotations

import os

from crewborg.nav import plan_route
from crewborg.types import Belief, PlayerRecord

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

# The kill cooldown fallback before HUD measurement: Crewrift Prime (0.3.9, our
# target league) uses 500 ticks; regular Crewrift (0.1.58) uses 800. We still learn
# the true value from the HUD once a cooldown runs to ready.
DEFAULT_KILL_COOLDOWN_TICKS = 500


def kill_urgency_ticks(belief: Belief) -> int:
    """How long we have been able to kill without doing so (0 if not kill-ready)."""

    if not belief.self_kill_ready or belief.kill_ready_since_tick is None:
        return 0
    return max(0, belief.last_tick - belief.kill_ready_since_tick)


def ticks_until_kill_ready(belief: Belief) -> int:
    """Estimated ticks until the kill becomes available (0 if ready now).

    The HUD is binary (ready / cooldown, no countdown), so this reconstructs the
    countdown from the tracked cooldown start (`kill_cooldown_start_tick`) plus the
    learned duration (`kill_cooldown_estimate`, falling back to the game default
    before anything has been measured). With no cooldown start observed yet it
        assumes a full cooldown remains, so callers won't enter Search on no
        information.
    """

    if belief.self_kill_ready:
        return 0
    if belief.kill_cooldown_start_tick is None:
        return DEFAULT_KILL_COOLDOWN_TICKS
    duration = belief.kill_cooldown_estimate or DEFAULT_KILL_COOLDOWN_TICKS
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
    """Whether a recently-seen living fellow imposter is both closer to ``target`` than we
    are and within ``TEAMMATE_CLAIM_RADIUS`` of it — i.e. the partner is better placed to
    take this victim, so we should prefer another (a soft, deconfliction-only signal)."""
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


# --- Recon (pre-position on a crewmate just before the kill comes off cooldown) ----
#
# Diagnosis (2026-06-25 warehouse head-to-head vs crewborg-aaln): at the moment our
# cooldown comes off we have a crewmate in view only ~53% of the time (Aaron: 83%) —
# we drift away from crew we saw earlier in the cooldown cycle. Recon closes that gap:
# inside RECON_WINDOW ticks of ready, beeline to the most-recently-seen crewmate so the
# instant we can kill, a victim is in hand and Hunt fires immediately.

# Ticks-before-ready at which to start recon. Deliberately short for now (a long window
# = Aaron-style overextension that gets caught); env-tunable so we can sweep it.
RECON_WINDOW_TICKS = 100


def recon_window() -> int:
    """The recon trigger window (ticks before kill-ready), env-overridable via
    ``CREWBORG_RECON_WINDOW`` so it can be swept without a rebuild."""

    raw = os.environ.get("CREWBORG_RECON_WINDOW")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return RECON_WINDOW_TICKS


def most_recent_victim(belief: Belief) -> PlayerRecord | None:
    """The most-recently-seen live non-teammate crewmate — the target to close on
    during recon (its ``world_x/y`` is the live position when visible, else last-known).
    ``None`` when no crewmate has been seen at all."""

    crew = [
        entry
        for entry in belief.roster.values()
        if entry.color not in belief.teammate_colors and entry.life_status != "dead"
    ]
    if not crew:
        return None
    return max(crew, key=lambda entry: entry.last_seen_tick)


def _self_xy(belief: Belief) -> tuple[int, int] | None:
    if belief.self_world_x is None or belief.self_world_y is None:
        return None
    return belief.self_world_x, belief.self_world_y


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
