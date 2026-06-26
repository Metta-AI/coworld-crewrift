"""Call Button mode: walk to the emergency button and call a meeting on evidence.

The crewmate analogue of a body report when there is no body to report: we hold
actionable suspicion (a witnessed kill/vent or a posterior over the flee bar —
``belief.believed_imposters``) but the corpse is unfindable, so the information
dies with us unless we open a meeting ourselves. The 2026-06-10 eval saw 53
kills produce only ~17 meetings (all body reports) and every player's single
button call go unused.

The mode is intentionally thin: it emits the ``call_meeting`` intent every
Playing tick and lets the action layer own the walk + edge-press mechanics
(``action._resolve_call_meeting`` — the same machinery Dick Mode uses). All
gating (evidence, the 1-call budget, timeouts) lives in the rule-based strategy.
"""

from __future__ import annotations

from players.crewrift.crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode


class CallButtonMode(Mode[Belief, ActionState, Intent]):
    name = "call_button"
    params_type = EmptyModeParams

    def is_legal(self, belief: Belief) -> bool:
        return belief.phase == "Playing"

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del belief, action_state
        return Intent(kind="call_meeting", reason="evidence: calling emergency meeting")
