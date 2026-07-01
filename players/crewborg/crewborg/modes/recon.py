"""Recon mode — pre-position on a crewmate just before the kill comes off cooldown.

Diagnosis (2026-06-25 warehouse head-to-head vs crewborg-aaln, "Aaron"): at the moment
our kill cooldown expires we have a crewmate in crewborg's view only ~53% of the time,
versus Aaron's 83% — we drift away from crew we saw earlier in the cooldown cycle, so
when we *can* kill there's no victim in hand and we dither. Recon closes that gap.

When the strategy gate sees the kill is within ``recon_window()`` ticks of ready (env
``CREWBORG_RECON_WINDOW``, default 100), it routes here instead of Search. Recon does
one thing: **beeline to the most-recently-seen crewmate** (live position when visible,
last-known position otherwise) so that the instant the cooldown clears, a victim is in
view and Hunt takes over and kills immediately.

Intentionally simple and aggressive for now — James's call is to test a short 100-tick
window and see what it does (a longer window risks the over-extension that gets Aaron
caught 39% of the time). Target selection + window live in ``strategy.opportunity``.
"""

from __future__ import annotations

from crewborg.agent_tracking import best_seek_point
from crewborg.modes import imposter_common as ic
from crewborg.strategy.commander.bias import commander_of
from crewborg.strategy.opportunity import most_recent_victim
from crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode, ModeParams

# We count ourselves as having "reached" a target's last-known spot within this radius (px).
REACHED_RADIUS_SQ = 24**2


class ReconMode(Mode[Belief, ActionState, Intent]):
    name = "recon"
    params_type = EmptyModeParams

    def __init__(self, params: ModeParams | None = None) -> None:
        super().__init__(params)
        self._abandoned: str | None = None  # a target we reached but who had already left

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        self_xy = ic.self_xy(belief)
        if self_xy is None:
            return Intent(kind="idle", reason="recon: no self position")  # startup no-op (no camera)

        target = self._commander_target(belief) or most_recent_victim(belief)
        if target is not None:
            target_xy = (target.world_x, target.world_y)
            visible = target.last_seen_tick == belief.last_tick
            if visible:
                self._abandoned = None  # they're back in view — commit
                return Intent(kind="navigate_to", point=target_xy,
                              reason="recon: closing on a crewmate before the kill comes ready")
            reached = ic.dist2(self_xy, target_xy) <= REACHED_RADIUS_SQ
            if reached:
                # We walked to their last-known spot and they aren't here — abandon it (never
                # stand still on a ghost position; that was the recon-stall freeze).
                self._abandoned = target.color
            if target.color != self._abandoned:
                return Intent(kind="navigate_to", point=target_xy,
                              reason="recon: closing on a crewmate's last-known position")

        # No live/unreached target — fall back to SEARCH behaviour: head toward where crew are
        # expected. NEVER idle here (idling with no escape is the trap; see best_practices).
        seek = best_seek_point(belief)
        if seek is not None:
            return Intent(kind="navigate_to", point=ic.reachable_point(belief, seek),
                          reason="recon: no live target — seek toward expected crew")
        return Intent(kind="idle", reason="recon: no crew signal yet")  # rare: no occupancy built yet

    def _commander_target(self, belief: Belief):
        cmd = commander_of(belief)
        if cmd is None or cmd.target_player is None or cmd.target_player in belief.teammate_colors:
            return None
        target = belief.roster.get(cmd.target_player)
        if target is None or target.life_status == "dead":
            return None
        return target
