"""Per-player observation event log (design §5.2).

Each tick, record what we saw every visible player *doing* as **durative
intervals** on their ``PlayerRecord.events``: which room/task/vent rect they sat
in, when they were near a body, and when they were within kill range of another
player. This is the "what have I seen X doing" memory a human builds — the raw
material for the future LLM strategy and for an automated suspicion layer.

Design choices:

- **Raw observations, derived interpretations.** We log only what we directly
  saw; compound signals ("orange followed yellow, who then died") are *queries*
  over these events plus the death linkage (§5), never stored as their own kind.
- **Intervals from contiguous observation.** A predicate true across consecutive
  observed ticks extends one interval; a break (the player leaves view, or the
  predicate goes false) closes it. So a duration is honestly *"observed for ≥
  this long"* — players are only rendered in line of sight, so seeing a player
  already implies we could see them.
- **Neutral memory.** Built for every role (an imposter benefits too, for
  deflection/voting); only *acting* on it (suspicion → Flee) is crewmate-gated.
"""

from __future__ import annotations

import math

from crewborg.action import KILL_RANGE_SQ
from crewborg.types import Belief, PlayerEvent, PlayerEventKind, PlayerRecord

# A player within this distance of a discovered body is logged as "near" it.
NEAR_BODY_RADIUS_SQ = 48**2

# A player sustained within this distance of *us* is logged as ``tailing_self`` — a
# possible stalker shadowing its target. Wider than kill range (20 px) so we catch the
# tail forming before it closes in; the suspicion weight ramps with how long it lasts.
TAIL_SELF_RADIUS_SQ = 64**2

# Bridge an interval across an unobserved gap of at most this many ticks (~a few
# frames of occlusion / out-of-view). A longer gap, or any tick where we saw the
# player but the predicate was false, starts a fresh interval instead.
EVENT_MERGE_GRACE_TICKS = 3


def update_event_log(belief: Belief) -> None:
    """Fold this tick's observations of every visible player into their event logs.

    Runs in the fast loop after ``update_belief`` (composed in ``build_runtime``).
    """

    tick = belief.last_tick
    visible = [r for r in belief.roster.values() if r.last_seen_tick == tick and r.life_status != "dead"]
    if not visible:
        return

    rooms = belief.map.rooms if belief.map is not None else ()
    tasks = belief.map.tasks if belief.map is not None else ()
    vents = belief.map.vents if belief.map is not None else ()
    self_xy = (
        (belief.self_world_x, belief.self_world_y)
        if belief.self_world_x is not None and belief.self_world_y is not None
        else None
    )

    for record in visible:
        here = (record.world_x, record.world_y)
        record.seen_ticks += 1  # exposure for the fitted suspicion model
        # The previous tick we processed this player; captured before we overwrite it,
        # so _mark can tell a brief unobserved gap from an observed departure.
        prev = record.last_event_tick

        room = _index_containing(here, rooms)
        if room is not None:
            _mark(record, tick, prev, "room", region_index=room)
        task = _index_containing(here, tasks)
        if task is not None:
            _mark(record, tick, prev, "task", region_index=task)
        vent = _index_containing(here, vents)
        if vent is not None:
            _mark(record, tick, prev, "vent", region_index=vent)

        for body in belief.bodies.values():
            d2 = _dist2(here, (body.world_x, body.world_y))
            if d2 <= NEAR_BODY_RADIUS_SQ:
                _mark(record, tick, prev, "near_body", target_color=body.color, dist2=d2)

        # Being tailed: a player sustained near *us* (target_color None = me). Unlike
        # third-party proximity this needs no death — a stalker shadowing me is live
        # evidence — and it's a signal we read best, since we always know our own spot.
        # Never log it for our *own* sprite: we are trivially always at our own spot, so
        # this would make us "tail" ourselves and suspect/vote ourself.
        if self_xy is not None and record.color != belief.self_color:
            d2 = _dist2(here, self_xy)
            if d2 <= TAIL_SELF_RADIUS_SQ:
                _mark(record, tick, prev, "tailing_self", dist2=d2)

        for other in visible:
            if other.color == record.color:
                continue
            d2 = _dist2(here, (other.world_x, other.world_y))
            if d2 <= KILL_RANGE_SQ:
                _mark(record, tick, prev, "proximity", target_color=other.color, dist2=d2)

        record.last_event_tick = tick


def _mark(
    record: PlayerRecord,
    tick: int,
    prev: int,
    kind: PlayerEventKind,
    *,
    region_index: int | None = None,
    target_color: str | None = None,
    dist2: int | None = None,
) -> None:
    """Extend the matching open interval or open a new one.

    Extend only when the predicate held at the *previous time we saw this player*
    (``latest.end_tick == prev``) and that was at most ``EVENT_MERGE_GRACE_TICKS``
    ago — i.e. a continuous run, or a brief unobserved gap. If we saw the player
    since (an observed tick where the predicate was false), ``end_tick < prev`` and
    we start a fresh interval; likewise after a long gap.
    """

    dist = math.isqrt(dist2) if dist2 is not None else None
    latest = _latest_matching(record, kind, region_index, target_color)
    if latest is not None and latest.end_tick == prev and tick - latest.end_tick <= EVENT_MERGE_GRACE_TICKS:
        latest.end_tick = tick
        if dist is not None:
            latest.min_dist = dist if latest.min_dist is None else min(latest.min_dist, dist)
        return
    record.events.append(
        PlayerEvent(
            kind=kind,
            start_tick=tick,
            end_tick=tick,
            region_index=region_index,
            target_color=target_color,
            min_dist=dist,
        )
    )


def _latest_matching(
    record: PlayerRecord, kind: PlayerEventKind, region_index: int | None, target_color: str | None
) -> PlayerEvent | None:
    for event in reversed(record.events):
        if event.kind == kind and event.region_index == region_index and event.target_color == target_color:
            return event
    return None


def _index_containing(point: tuple[int, int], rects) -> int | None:
    """Index of the first rect (with ``x``/``y``/``w``/``h``) containing the point."""

    px, py = point
    for i, rect in enumerate(rects):
        if rect.x <= px < rect.x + rect.w and rect.y <= py < rect.y + rect.h:
            return i
    return None


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
