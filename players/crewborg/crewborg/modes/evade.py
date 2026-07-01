"""Evade mode: after a kill, beeline toward the most populated area (design §7.2).

Rewritten 2026-06-26 (James). The old Evade *fled* the scene — vent away blindly or
walk directly away from the body — which fed the post-kill drift: we lost crew contact
and had no victim in sight when the cooldown cleared. The new Evade does the opposite —
it heads toward where the crew most likely are, off the expected-crew occupancy grid
(``agent_tracking`` §10.2), so a victim cluster is already nearby when the post-kill
window hands back to Search/Recon.

This is deliberately paired with Hunt's **drop the witness requirement after the first
kill** (`modes/hunt.py`): on its own, re-approaching the densest *crowd* is a poor place
to land an *unwitnessed* kill — but once witnesses no longer veto the second kill, the
crowd becomes target-rich exactly when we need it. The two are meant to be evaluated
together.

Target preference: the densest crew *room* (stable, and it subtracts teammate pressure
so two imposters don't pile onto the same room) → the hottest occupancy *cell* if no room
target → the most-recently-seen crewmate (cold start, before occupancy has mass) → idle.
"""

from __future__ import annotations

from crewborg import agent_tracking as at
from crewborg.modes import imposter_common as ic
from crewborg.strategy.opportunity import most_recent_victim
from crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode


class EvadeMode(Mode[Belief, ActionState, Intent]):
    name = "evade"
    params_type = EmptyModeParams

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        self_xy = ic.self_xy(belief)
        if self_xy is not None:
            room = at.best_pretend_room_target(belief, self_xy)
            if room is not None:
                return Intent(
                    kind="navigate_to",
                    point=room.point,
                    reason=f"evade: beeline to densest crew area ({room.room_name})",
                )
            cell = at.best_seek_point(belief, self_xy)
            if cell is not None:
                return Intent(kind="navigate_to", point=cell, reason="evade: beeline to hottest occupancy cell")

        victim = most_recent_victim(belief)
        if victim is not None:
            return Intent(
                kind="navigate_to",
                point=(victim.world_x, victim.world_y),
                reason="evade: no occupancy yet, close on the last-seen crewmate",
            )
        return Intent(kind="idle", reason="evade: no crew area to approach")
