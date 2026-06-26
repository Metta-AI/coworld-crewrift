"""Report Body mode: report a body in view (design §7.1, §12).

Active while a body is currently visible. It emits ``report`` for the nearest
visible body; the action layer navigates to it and presses A in range. When the
report opens a meeting (``phase`` becomes ``Voting``) the selector switches to
Attend Meeting, so this mode yields automatically. Report policy default
(design §12) = always report a visible body; suspicion-aware reporting is later.
"""

from __future__ import annotations

from players.crewrift.crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode


class ReportBodyMode(Mode[Belief, ActionState, Intent]):
    name = "report_body"
    params_type = EmptyModeParams

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
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
    body = belief.bodies[body_id]
    return body.world_x, body.world_y


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
