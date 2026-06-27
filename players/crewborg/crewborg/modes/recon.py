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

Collaborators
-------------
Relies on:
  - ``strategy.opportunity.most_recent_victim`` — the most-recently-seen live non-teammate
    crewmate to beeline toward.
  - ``modes.imposter_common`` (``ic``) — ``self_xy``.
  - ``strategy.commander.bias.commander_of`` — optional LLM target-player override.
  - ``types`` — ``ActionState`` / ``Belief`` / ``Intent``.
Used by:
  - ``strategy.rule_based`` selects this mode when the kill is within ``recon_window()``
    ticks of ready and a crewmate has been seen (§10) — it sits between Search and Hunt.
  - ``__init__.build_runtime`` registers it in the ``ModeRegistry``.
Emits: a ``navigate_to`` intent (close on the target), or ``idle`` when there is no
  position/target.

Modifying this file: it only chooses *where to pre-position* — it never moves the agent
or kills (that is ``action.py`` / Hunt). The window/gate that activates Recon lives in the
selector + ``strategy.opportunity``, not here.
"""

from __future__ import annotations

from crewborg.modes import imposter_common as ic
from crewborg.strategy.commander.bias import commander_of
from crewborg.strategy.opportunity import most_recent_victim
from crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode, ModeParams


class ReconMode(Mode[Belief, ActionState, Intent]):
    """Pre-kill beeline to a crewmate. Stateless — the target (commander override, else
    most-recently-seen crewmate) is re-chosen each tick."""

    name = "recon"
    params_type = EmptyModeParams

    def __init__(self, params: ModeParams | None = None) -> None:
        super().__init__(params)

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        """Return a ``navigate_to`` intent aimed at the target crewmate (live position when
        visible, last-known otherwise), so a victim is in view the instant the cooldown clears
        and Hunt takes over. ``idle`` when we have no self position or no known crewmate.
        ``action_state`` is unused — pure over belief."""
        del action_state
        target = self._commander_target(belief) or most_recent_victim(belief)
        if target is None or ic.self_xy(belief) is None:
            # Gate only routes here when a crew has been seen; idle is a safe no-op.
            return Intent(kind="idle", reason="recon: no known crewmate to close on")
        return Intent(
            kind="navigate_to",
            point=(target.world_x, target.world_y),
            reason="recon: closing on a crewmate before the kill comes ready",
        )

    def _commander_target(self, belief: Belief):
        """If the (optional, gated) LLM commander named a ``target_player``, return that
        crewmate when it is a live non-teammate in the roster — else ``None`` so we fall back
        to ``most_recent_victim``. Unlike Hunt, Recon does not require the target to be
        currently visible or reachable (it is only pre-positioning, not striking)."""
        cmd = commander_of(belief)
        if cmd is None or cmd.target_player is None or cmd.target_player in belief.teammate_colors:
            return None
        target = belief.roster.get(cmd.target_player)
        if target is None or target.life_status == "dead":
            return None
        return target
