"""Follower ("tail") detection: notice when one player is shadowing us.

The 2026-06-11 replay reconstruction (full multi-agent ground truth, 98
episodes vs the champion field) showed **34 of our 46 crewmate deaths were
shadow kills**: the killer latched on, walked within ~120 px of us for 6-12 s,
and struck the moment cooldown + witness conditions allowed (truecrew imposters
spent 54% of co-alive time within 120 px of us; deaths happened at nearest-
witness distance 130 px vs an 88 px baseline). The killer is inside our LoS the
whole approach — our own perception already sees them; we just never reacted.

This module maintains ``belief.tail_streaks`` — per-color contiguous
"observed within tail range of us" intervals, tolerant of brief LoS flicker —
and answers :func:`active_tail`: the player who has shadowed us long enough,
while no other crew is near enough to witness, that we should break toward a
crowd (``seek_crowd`` mode) instead of finishing the task leg.

Detection only; the *response* is the rule-based strategy's crewmate priority
(tailed → seek crowd) and the SeekCrowd mode. Movement mechanics stay in the
action resolver per the design rules.
"""

from __future__ import annotations

from players.crewrift.crewborg.types import Belief

# A player observed within this distance (world px) of us counts as shadowing.
# The replay ground truth used 120 px (well past kill range 20, inside our LoS).
TAIL_RADIUS = 120
TAIL_RADIUS_SQ = TAIL_RADIUS**2
# Sustained shadowing for at least this long (~6 s at 24 Hz) makes a tail. Real
# shadow kills sat at 6-12 s of contact, so 6 s still reacts inside the strike
# window while ordinary task-route co-walks never qualify. (Raised 96 → 144
# after the v6 hosted eval: at 4 s the detector fired ~4/ep against champions
# who shadow more than half the time, and the response cost ~1 task/ep.)
# (iter-1 tried 108 on the buzzer field — reverted with the rest of the
# over-aggressive anti-shadow change that regressed crew survival; see
# rule_based.py TAIL_* note.)
TAIL_MIN_TICKS = 144
# Bridge LoS flicker: a within-radius observation at most this long after the
# previous one extends the streak; a longer gap starts a fresh streak.
TAIL_GAP_GRACE_TICKS = 24
# A streak (and the tail response) ends once the follower has not been seen
# within radius for this long (~2 s) — they broke off, resume tasking.
TAIL_STALE_TICKS = 48
# Witness isolation: another live player seen this recently within this range
# of us means we are not alone — the shadow-kill setup needs the witness gap
# (ground truth: median nearest-witness distance 130 px at our deaths), so a
# nearby third party suppresses the crowd-seek response.
TAIL_WITNESS_RADIUS_SQ = 150**2
TAIL_WITNESS_RECENT_TICKS = 48


def update_tail_tracking(belief: Belief) -> None:
    """Fold this tick's sightings into the per-color shadowing streaks.

    Runs in the fast loop after ``update_belief``. Only meaningful while we are
    a live crewmate during Playing; any other phase/role clears the streaks
    (meetings teleport everyone to spawn, so cross-meeting streaks would lie).
    """

    if belief.phase != "Playing" or belief.self_role in ("imposter", "dead"):
        if belief.tail_streaks:
            belief.tail_streaks.clear()
        return
    self_xy = _self_xy(belief)
    if self_xy is None:
        return

    tick = belief.last_tick
    for record in belief.roster.values():
        if record.life_status == "dead" or record.color in belief.teammate_colors:
            belief.tail_streaks.pop(record.color, None)
            continue
        if record.last_seen_tick != tick:
            continue  # not observed this tick: streak ages, grace decides later
        if _dist2(self_xy, (record.world_x, record.world_y)) > TAIL_RADIUS_SQ:
            continue  # observed but out of range: don't extend (grace decides)
        streak = belief.tail_streaks.get(record.color)
        if streak is None or tick - streak[1] > TAIL_GAP_GRACE_TICKS:
            belief.tail_streaks[record.color] = (tick, tick)
        else:
            belief.tail_streaks[record.color] = (streak[0], tick)

    # Evict streaks whose follower has clearly broken off.
    stale = [color for color, (_, last) in belief.tail_streaks.items() if tick - last > TAIL_STALE_TICKS]
    for color in stale:
        del belief.tail_streaks[color]


def active_tail(belief: Belief) -> str | None:
    """The color currently tailing us, if the crowd-seek response should fire.

    Requires: a live crewmate POV, a streak of at least ``TAIL_MIN_TICKS`` whose
    follower was within range in the last ``TAIL_STALE_TICKS``, and no *other*
    live player nearby to witness (a watched imposter does not strike). Returns
    the longest-streak tail when several qualify.
    """

    if belief.self_role in ("imposter", "dead"):
        return None
    self_xy = _self_xy(belief)
    if self_xy is None:
        return None
    tick = belief.last_tick

    best: tuple[int, str] | None = None
    for color, (start, last) in belief.tail_streaks.items():
        if last - start < TAIL_MIN_TICKS or tick - last > TAIL_STALE_TICKS:
            continue
        record = belief.roster.get(color)
        if record is None or record.life_status == "dead":
            continue
        length = last - start
        if best is None or length > best[0]:
            best = (length, color)
    if best is None:
        return None

    tail = best[1]
    if _witness_nearby(belief, self_xy, exclude=tail):
        return None
    return tail


def _witness_nearby(belief: Belief, self_xy: tuple[int, int], *, exclude: str) -> bool:
    tick = belief.last_tick
    for record in belief.roster.values():
        if record.color == exclude or record.life_status == "dead":
            continue
        if tick - record.last_seen_tick > TAIL_WITNESS_RECENT_TICKS:
            continue
        if _dist2(self_xy, (record.world_x, record.world_y)) <= TAIL_WITNESS_RADIUS_SQ:
            return True
    return False


def _self_xy(belief: Belief) -> tuple[int, int] | None:
    if belief.self_world_x is None or belief.self_world_y is None:
        return None
    return belief.self_world_x, belief.self_world_y


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
