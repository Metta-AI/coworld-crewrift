"""Trajectory prediction from the roster sighting trail (design §7.4).

Estimates a player's velocity from its recent sightings and extrapolates a future
position, so Hunt can **lead** (intercept) a moving victim instead of tail-chasing
its live position — a tail-chase at equal speed never closes, so kills only ever
landed on stopped crewmates before.
"""

from __future__ import annotations

import math

from players.crewrift.crewborg.types import PlayerRecord

Point = tuple[int, int]

# Only trust a velocity estimate from two sightings at most this many ticks apart
# (a wider gap means the player was off-screen between them — the line is unreliable).
VELOCITY_MAX_DT = 4
# Rough agent travel speed (world px/tick) for estimating how long it takes us to
# close on the target — used to pick how far ahead to lead.
AGENT_SPEED_PX = 3.0
# Cap the lead so a stale/noisy velocity can't fling the aim point across the map.
MAX_LEAD_TICKS = 24


def velocity(entry: PlayerRecord) -> tuple[float, float]:
    """Per-tick velocity from the player's two most recent sightings, or ``(0, 0)``.

    Returns zero when there is no usable pair (too few sightings, or a gap wider than
    ``VELOCITY_MAX_DT`` ticks, i.e. the player was out of view between them).
    """

    history = entry.history
    if len(history) < 2:
        return 0.0, 0.0
    (t0, x0, y0), (t1, x1, y1) = history[-2], history[-1]
    dt = t1 - t0
    if dt <= 0 or dt > VELOCITY_MAX_DT:
        return 0.0, 0.0
    return (x1 - x0) / dt, (y1 - y0) / dt


def lead_ticks(self_xy: Point, target_xy: Point) -> int:
    """How far ahead to lead: roughly the time for us to close the current gap."""

    return min(MAX_LEAD_TICKS, int(math.dist(self_xy, target_xy) / AGENT_SPEED_PX))


def predict(entry: PlayerRecord, lead: int) -> Point:
    """The player's last-known position extrapolated ``lead`` ticks along its velocity.

    A stationary target (no usable velocity) predicts to its current position, so
    leading a stopped crewmate is a no-op — exactly right.
    """

    vx, vy = velocity(entry)
    return round(entry.world_x + vx * lead), round(entry.world_y + vy * lead)
