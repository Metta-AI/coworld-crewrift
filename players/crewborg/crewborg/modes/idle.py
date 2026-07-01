"""Idle mode: stand still. The default stance and the stall/TTL fallback.

Idle is active during non-actionable phases (``RoleReveal``/``Lobby``/``GameOver``)
and is the runtime's default directive (design §7.1, §10).
"""

from __future__ import annotations

from crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode


class IdleMode(Mode[Belief, ActionState, Intent]):
    name = "idle"
    params_type = EmptyModeParams

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del belief, action_state
        return Intent(kind="idle", reason="idle stance")
