"""Dick mode: call an emergency meeting and taunt the imposters."""

from __future__ import annotations

from players.crewrift.crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode

DICK_MODE_CHAT = "haha, fuck you imposters"


class DickMode(Mode[Belief, ActionState, Intent]):
    name = "dick_mode"
    params_type = EmptyModeParams

    def __init__(self, params=None) -> None:
        super().__init__(params)
        self._meeting_id: int | None = None
        self._chatted = False

    def is_legal(self, belief: Belief) -> bool:
        return belief.phase in {"Playing", "Voting"}

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        if belief.phase == "Voting":
            self._reset_for_meeting_if_needed(belief)
            if not self._chatted:
                self._chatted = True
                return Intent(kind="chat", text=DICK_MODE_CHAT, reason="dick mode taunt")
            if not action_state.vote_confirmed:
                return Intent(kind="vote", reason="dick mode: skip after taunt")
            return Intent(kind="idle", reason="dick mode: meeting work done")

        return Intent(kind="call_meeting", reason="dick mode: rush emergency button")

    def _reset_for_meeting_if_needed(self, belief: Belief) -> None:
        meeting_id = belief.phase_start_tick
        if meeting_id == self._meeting_id:
            return
        self._meeting_id = meeting_id
        self._chatted = False
