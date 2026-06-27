"""Idle mode: stand still. The default stance and the stall/TTL fallback.

Idle is active during non-actionable phases (``RoleReveal``/``Lobby``/``GameOver``)
and is the runtime's default directive (design §7.1, §10). It is also the universal
no-op other modes fall back to when they have nothing actionable (no self position,
no victim, etc.).

Collaborators
-------------
Relies on: nothing — it is pure and stateless.
Used by:
  - ``strategy.rule_based`` selects this mode for every non-Playing/non-Voting phase
    (Lobby / RoleReveal / VoteResult / GameOver / unknown; §10).
  - ``__init__.build_runtime`` registers it in the ``ModeRegistry``.
Emits: a single ``idle`` intent (the action layer holds the agent still).

Modifying this file: a mode emits a symbolic Intent only — it never moves the agent
or presses buttons (that is ``action.py``). Idle is the one mode that should always
stay a trivial constant ``idle`` emit; behavior belongs in the selector, not here.
"""

from __future__ import annotations

from crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode


class IdleMode(Mode[Belief, ActionState, Intent]):
    """Stand-still stance. Stateless and pure — ``decide`` ignores its inputs and
    always returns the same ``idle`` intent."""

    name = "idle"
    params_type = EmptyModeParams

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        """Always return an ``idle`` intent (hold still). Both args are unused — Idle
        is a constant."""
        del belief, action_state
        return Intent(kind="idle", reason="idle stance")
