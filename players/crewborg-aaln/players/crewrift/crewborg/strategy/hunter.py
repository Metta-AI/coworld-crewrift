"""Hunter profile: the anti-sussyboi fork (``CREWBORG_HUNTER=1``).

Built from the 2026-06-11 league replay reconstruction of softmax-sussyboi:v1
(``episode_data/league_2026-06-11_sussyboi/FINDINGS_sussyboi_behavior.md``).
sussyboi's whole edge is the emergency button + ``applyVoteResult``: every
meeting teleports everyone home, deletes bodies, and resets every imposter's
kill cooldown to full (500 ticks). Its crew presses the button at segment
offset ~500 — exactly when the imposters' kill first comes off cooldown — and
its imposter presses at ~150 to wreck the crew's task opening.

The hunter profile both *steals* the strong half and *counters* it:

- **Jam button (crew)**: spend our one button call at the moment of maximum
  denial — stage at the button and press when the imposters' reconstructed
  kill cooldown is ~``JAM_PRESS_LEAD_TICKS`` from ready. Pressing earlier is
  strictly worse (the reset is always to full, so kill-free time from segment
  start = press offset + 500); pressing later risks eating the first kill.
  Our previous button users pressed at mean offset ~144 — wasting the jam.

- **Button stakeout (imposter)**: sussyboi's crew (and any copycat jammer)
  walks to the button in a narrow, predictable window before the press. Once
  our own kill is near ready, lurk by the button and kill the approacher: it
  denies the cooldown reset *and* preferentially removes sussyboi itself.

- **Early-caller suspicion**: a *button* meeting opened at segment offset
  ≤ ``EARLY_BUTTON_CALL_MAX_ELAPSED_TICKS`` is sussyboi's imposter signature
  (6/6 of its imposter games in the league sample). Moderate evidence — some
  crews also rush the button — so it stacks with real evidence instead of
  triggering votes alone.

This module owns the flag, the shared constants, and the pure belief readouts;
the wiring lives in ``strategy.rule_based``, ``modes.jam_button``,
``modes.search``, ``strategy.opportunity`` and ``strategy.suspicion``.
"""

from __future__ import annotations

import math
import os

from players.crewrift.crewborg.nav import plan_route
from players.crewrift.crewborg.types import Belief, PlayerRecord

# --- flag ----------------------------------------------------------------------

_ENV_FLAGS = ("CREWBORG_HUNTER", "HUNTER")


def hunter_enabled() -> bool:
    """Whether the anti-sussyboi hunter profile is active (env-driven)."""

    return any(os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"} for name in _ENV_FLAGS)


# --- jam button (crew) -----------------------------------------------------------

# Press the button when the reconstructed imposter kill cooldown is within this
# many ticks of ready. The reset is always to a *full* cooldown, so the optimal
# press is just before the first kill becomes possible (sussyboi presses at mean
# offset 577 and still beats the first kill in most segments; earlier is safer).
JAM_PRESS_LEAD_TICKS = 16

# Start walking to the button this many ticks before the press deadline beyond
# the estimated travel time — covers controller settling and route churn without
# loitering at the button long enough to become stakeout bait ourselves.
JAM_START_MARGIN_TICKS = 90

# Staging without a successful press for this long means the button is
# unreachable / contested — abandon and spend the budget so we never loop.
JAM_MAX_STAGING_TICKS = 700

# Walking speed: MaxSpeed 704/256 px per tick (see sim), derated for path shape
# and the bang-bang controller.
_SPEED_PX_PER_TICK = 704 / 256
_ROUTE_TICKS_FUDGE = 1.2
_TRAVEL_SETTLE_TICKS = 24
# With no nav/route available, assume the dick-mode worst-case walk bound.
_FALLBACK_TRAVEL_TICKS = 600


def button_press_goal(belief: Belief) -> tuple[int, int] | None:
    """The point to stand on while staging to press (the nav anchor when baked)."""

    if belief.map is None:
        return None
    anchor = belief.nav.button_anchor if belief.nav is not None else None
    if anchor is not None:
        return anchor
    center = belief.map.button.center
    return (center.x, center.y)


def estimate_travel_ticks(belief: Belief, start: tuple[int, int], goal: tuple[int, int]) -> int:
    """Estimated walk time from ``start`` to ``goal`` along the planned route."""

    length = 0.0
    if belief.nav is not None:
        waypoints = plan_route(belief.nav, start, goal)
        if not waypoints:
            return _FALLBACK_TRAVEL_TICKS
        prev = start
        for point in waypoints:
            length += math.dist(prev, point)
            prev = point
    else:
        length = math.dist(start, goal)
    return int(length / _SPEED_PX_PER_TICK * _ROUTE_TICKS_FUDGE) + _TRAVEL_SETTLE_TICKS


# --- button stakeout (imposter) ---------------------------------------------------

# Enter the stakeout once our kill is within this of ready (the jammer crew
# arrives just before the cooldown clears) …
STAKEOUT_LEAD_TICKS = 120
# … and stop staking out this far into a segment: every observed sussyboi crew
# press landed by offset ~700, so beyond it the camper is wasting the kill.
STAKEOUT_MAX_ELAPSED_TICKS = 760
# Stand this far below the button rect: outside the action layer's inflated
# no-kill zone (BUTTON_ZONE_MARGIN_PX = 4) so a strike resolves immediately.
STAKEOUT_STANDOFF_PX = 14
# A visible crewmate within this distance of the button during the stakeout
# window is treated as a button approacher — the priority kill target.
BUTTON_APPROACH_RADIUS_SQ = 80**2


def segment_elapsed(belief: Belief) -> int | None:
    """Playing ticks elapsed since this segment's cooldown anchor (None pre-game)."""

    if belief.kill_cooldown_start_tick is None:
        return None
    return max(0, belief.last_tick - belief.kill_cooldown_start_tick)


def stakeout_window_active(belief: Belief) -> bool:
    """Whether the imposter should be lurking at the button right now."""

    if belief.map is None:
        return False
    # Imported lazily: opportunity imports this module for the victim bias.
    from players.crewrift.crewborg.strategy.opportunity import ticks_until_kill_ready

    if ticks_until_kill_ready(belief) > STAKEOUT_LEAD_TICKS:
        return False
    elapsed = segment_elapsed(belief)
    return elapsed is None or elapsed <= STAKEOUT_MAX_ELAPSED_TICKS


def stakeout_point(belief: Belief) -> tuple[int, int] | None:
    """Where to lurk: just outside the button's no-kill zone."""

    if belief.map is None:
        return None
    button = belief.map.button
    return (button.x + button.w // 2, button.y + button.h + STAKEOUT_STANDOFF_PX)


def button_approachers(belief: Belief, candidates: list[PlayerRecord]) -> list[PlayerRecord]:
    """The candidates close enough to the button to be walking in for a press."""

    if belief.map is None:
        return []
    center = belief.map.button.center
    return [
        record
        for record in candidates
        if (record.world_x - center.x) ** 2 + (record.world_y - center.y) ** 2 <= BUTTON_APPROACH_RADIUS_SQ
    ]


# --- early-caller suspicion (crew) -------------------------------------------------

# A button meeting opened at segment offset ≤ this is the sussyboi imposter
# signature (its imposter presses at offsets 136-162; its crew presses ≥ ~447).
EARLY_BUTTON_CALL_MAX_ELAPSED_TICKS = 250

# Moderate, single-shot evidence: some crews (dick-mode crewborg, jernau) also
# press early, so alone this lands well under every vote bar — it exists to
# stack with witnessed/graded evidence, not to convict by itself.
EARLY_BUTTON_LOG_LR = math.log(2.5)


def early_button_caller_log_lr(belief: Belief, color: str) -> float:
    """The early-button-caller evidence contribution for one player (≥ 0)."""

    for record in belief.meeting_history:
        if (
            record.trigger == "button"
            and record.called_by == color
            and record.called_elapsed_ticks is not None
            and record.called_elapsed_ticks <= EARLY_BUTTON_CALL_MAX_ELAPSED_TICKS
        ):
            return EARLY_BUTTON_LOG_LR
    return 0.0
