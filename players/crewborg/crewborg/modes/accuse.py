"""Accuse mode: call a meeting on a player who is tailing us (design §7.1).

When we detect we're being actively shadowed by someone we've grown suspicious of
(``active_tail_suspect`` — an ongoing ``tailing_self`` interval pushing their
posterior over ``ACCUSE_THRESHOLD``), we stop what we're doing and go **call a
meeting** rather than run away: emit ``call_meeting`` (the action layer walks to the
emergency button and presses it), and once the meeting opens, Attend Meeting accuses
and votes the suspect from the same suspicion model.

This replaces the old keep-away Flee behaviour: a believed imposter shadowing us is
better answered by dragging them into a public vote than by fleeing. The emergency
button is a **one-shot** resource (``buttonCalls = 1``), so the *selector*
(``strategy.rule_based``) — not this mode — decides when to spend it and won't
re-enter Accuse once the call is used.

Collaborators
-------------
Relies on:
  - ``strategy.suspicion.active_tail_suspect`` — the suspect currently tailing us whose
    posterior is over ``ACCUSE_THRESHOLD`` (the trigger and best-effort vote target).
  - ``types`` — ``ActionState`` / ``Belief`` / ``Intent``.
Used by:
  - ``strategy.rule_based`` selects this mode (live crewmate, button still unspent and
    reachable, an active tail suspect; §10) and owns the one-shot button budget.
  - ``__init__.build_runtime`` registers it in the ``ModeRegistry``.
Emits: a ``call_meeting`` intent (``action.py`` walks to the emergency button and presses it).

Modifying this file: it only signals *intent to call a meeting* — it never walks to or
presses the button, and it does not cast the vote (Attend Meeting re-derives that from
suspicion). Button-budget and re-entry policy live in the selector, not here.
"""

from __future__ import annotations

from crewborg.strategy.suspicion import active_tail_suspect
from crewborg.types import ActionState, Belief, Intent
from players.player_sdk import EmptyModeParams, Mode


class AccuseMode(Mode[Belief, ActionState, Intent]):
    """Call a meeting on an active tail. Stateless — the vote target is re-derived from
    suspicion each tick and again by Attend Meeting once the meeting opens."""

    name = "accuse"
    params_type = EmptyModeParams

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        """Return a ``call_meeting`` intent tagged with the current tail suspect (best-effort
        only; the meeting re-derives the actual vote). ``target_color`` is ``None`` if the
        tail lapsed this tick — the action layer still heads for the button.
        ``action_state`` is unused — Accuse is pure over belief."""
        del action_state
        # Best-effort record of whom we mean to accuse (the meeting re-derives the
        # vote from suspicion); None if the tail just lapsed while we commit to the run.
        target = active_tail_suspect(belief)
        return Intent(kind="call_meeting", target_color=target, reason="calling a meeting to accuse a tail")
