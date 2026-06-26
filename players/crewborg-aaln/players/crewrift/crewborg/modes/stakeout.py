"""Stakeout mode (hunter profile): lurk by the emergency button, kill the jammer.

The imposter half of the anti-sussyboi counter (``strategy.hunter``): sussyboi's
crew — and any policy copying its timed button — walks to the emergency button
in a narrow window just before the kill cooldown clears, to reset it. Instead of
wandering occupancy points through that window, hold position just outside the
button's no-kill zone and treat any approaching crewmate as the priority victim:
killing the jammer both denies the cooldown reset and preferentially removes
sussyboi itself.

Search-like in spirit but positionally pinned: a visible victim is followed (so
Hunt can take over the strike when the kill is ready), otherwise the mode holds
the lurk point. The strategy gates the window (``stakeout_window_active``).
"""

from __future__ import annotations

from players.crewrift.crewborg.modes import imposter_common as ic
from players.crewrift.crewborg.strategy.hunter import stakeout_point
from players.crewrift.crewborg.strategy.opportunity import select_victim
from players.crewrift.crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode

# Hold position once this close to the lurk point (don't jitter on the spot).
HOLD_RADIUS_SQ = 10**2


class StakeoutMode(Mode[Belief, ActionState, Intent]):
    name = "stakeout"
    params_type = EmptyModeParams

    def is_legal(self, belief: Belief) -> bool:
        return belief.phase == "Playing"

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        self_xy = ic.self_xy(belief)
        if self_xy is None:
            return Intent(kind="idle", reason="stakeout: no self position")

        # A visible victim (select_victim already prefers button approachers
        # under the hunter flag): close in so Hunt converts the moment the
        # cooldown clears.
        target = select_victim(belief)
        if target is not None:
            return Intent(
                kind="navigate_to",
                point=(target.world_x, target.world_y),
                reason="stakeout: closing on button approacher",
            )

        point = stakeout_point(belief)
        if point is None:
            return Intent(kind="idle", reason="stakeout: no button location")
        if ic.dist2(self_xy, point) <= HOLD_RADIUS_SQ:
            return Intent(kind="idle", reason="stakeout: holding at button")
        return Intent(kind="navigate_to", point=point, reason="stakeout: moving to button lurk")
