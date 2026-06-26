"""Jam Button mode (hunter profile): stage at the button, press at cooldown expiry.

The anti-sussyboi timed button (``strategy.hunter``): every meeting resets the
imposters' kill cooldown to *full*, so the kill-free time bought by our one
button call is maximized by pressing just before the first kill becomes
possible — not on arrival. The mode walks to the button anchor and loiters,
emitting ``call_meeting`` (the action layer's walk + edge-press machinery) only
once the reconstructed kill cooldown is within ``JAM_PRESS_LEAD_TICKS`` of
ready. All gating (trigger timing, the 1-call budget, refusal/timeout) lives in
the rule-based strategy's jam FSM.
"""

from __future__ import annotations

from players.crewrift.crewborg.strategy.hunter import JAM_PRESS_LEAD_TICKS, button_press_goal
from players.crewrift.crewborg.strategy.opportunity import ticks_until_kill_ready
from players.crewrift.crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode


class JamButtonMode(Mode[Belief, ActionState, Intent]):
    name = "jam_button"
    params_type = EmptyModeParams

    def is_legal(self, belief: Belief) -> bool:
        return belief.phase == "Playing"

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        remaining = ticks_until_kill_ready(belief)
        if remaining <= JAM_PRESS_LEAD_TICKS:
            return Intent(kind="call_meeting", reason=f"jam: pressing at cooldown expiry (t-{remaining})")
        goal = button_press_goal(belief)
        if goal is None:
            return Intent(kind="idle", reason="jam: no button location")
        return Intent(kind="navigate_to", point=goal, reason=f"jam: staging at button (t-{remaining})")
