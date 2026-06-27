"""Report Body mode: report a body in view (design §7.1, §12).

Active while a body is currently visible. It emits ``report`` for the nearest
visible body; the action layer navigates to it and presses A in range. When the
report opens a meeting (``phase`` becomes ``Voting``) the selector switches to
Attend Meeting, so this mode yields automatically. Report policy default
(design §12) = always report a visible body; suspicion-aware reporting is later.

Crewmate-only: the selector never routes an imposter here — self-reporting our own
kill opens a meeting that resets the kill cooldown (design §10).

Collaborators
-------------
Relies on:
  - ``belief.visible_body_ids`` / ``belief.bodies`` — the bodies currently in view and
    their world positions.
  - ``belief.self_world_x/y`` — our position, for picking the nearest body.
  - ``types`` — ``ActionState`` / ``Belief`` / ``Intent``.
Used by:
  - ``strategy.rule_based`` selects this mode for a live crewmate with a body in view
    (outranks Accuse; §10).
  - ``__init__.build_runtime`` registers it in the ``ModeRegistry``.
Emits: a ``report`` intent for a body id (``action.py`` navigates to it and presses A
  in range), or ``idle`` when no visible body remains.

Modifying this file: it only chooses *which* body to report — it never navigates or
presses the button (that is ``action.py``). Reporting policy (always-report) lives here;
who is allowed to reach this mode (crewmate-only) lives in the selector.
"""

from __future__ import annotations

from crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode


class ReportBodyMode(Mode[Belief, ActionState, Intent]):
    """Report the nearest visible body. Stateless — the target is re-chosen each tick
    from the current set of visible bodies."""

    name = "report_body"
    params_type = EmptyModeParams

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        """Return a ``report`` intent for the nearest visible body (by squared distance;
        falls back to the lowest body id when our own position is unknown), or ``idle`` if
        no visible body remains in ``belief.bodies``. ``action_state`` is unused — pure over
        belief."""
        del action_state
        candidates = [bid for bid in belief.visible_body_ids if bid in belief.bodies]
        if not candidates:
            return Intent(kind="idle", reason="no body in view")
        if belief.self_world_x is None or belief.self_world_y is None:
            target = min(candidates)
        else:
            self_xy = (belief.self_world_x, belief.self_world_y)
            target = min(candidates, key=lambda b: _dist2(self_xy, _body_xy(belief, b)))
        return Intent(kind="report", target_id=target, reason="reporting visible body")


def _body_xy(belief: Belief, body_id: int) -> tuple[int, int]:
    """World position of the body with ``body_id`` (assumed present in ``belief.bodies``)."""
    body = belief.bodies[body_id]
    return body.world_x, body.world_y


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Squared Euclidean distance in world px (cheap nearest-body comparison)."""
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
