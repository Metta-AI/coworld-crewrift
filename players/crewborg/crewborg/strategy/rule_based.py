"""Rule-based mode selector (design §10).

A deterministic ``decide(snapshot) -> ModeDirective`` run every tick via
``SynchronousStrategyRunner`` — pure rules over belief. Because it runs every
tick, transitions are re-evaluated each cycle (no reflexes).

Crewmate priority order (design §10):

1. ``phase == Voting`` → Attend Meeting
2. a body in view → Report Body (a meeting protects us; outranks accusing)
3. an active tail by a suspect over the "sketched out" bar → Accuse: stop and go
   slam the emergency button to call a meeting (one-shot — see below), then accuse
   them at the vote. This replaces the old Flee/keep-away behaviour entirely.
4. ``phase == Playing`` → Normal (ghosts included — they finish their own tasks)
5. otherwise → idle

The Accuse trigger is ``active_tail_suspect`` (``strategy.suspicion``, design §10.1):
the most-suspicious player currently shadowing us whose posterior is over
``ACCUSE_THRESHOLD``. The emergency button is a **one-shot** resource per game
(``buttonCalls = 1``), so once we've spent the call we fall back to tasks rather than
loop at the button; the budget resets at the next game (``Lobby``/``RoleReveal``).

Imposter priority order (design §10):

1. ``phase == Voting`` → Attend Meeting
2. just killed → Evade (vent / leave the body)
3. kill ready + a visible victim → Hunt (commit to a victim and strike / close)
4. near-ready cooldown + known crew → Recon (close on the last seen crewmate)
5. otherwise → Search (find/follow a target)

(2) prevents instant self-reports after our own kill: the imposter first leaves the
scene, preferably through a vent. Imposters NEVER report bodies; self-reporting our
own kill triggered a meeting that reset the cooldown and killed snowball kills. Once
Evade ends we go straight back to Search (or Hunt/Recon if the gates match).

(5) fires once the kill cooldown is within a short lead window of being ready
(`ticks_until_kill_ready ≤ SEARCH_LEAD_TICKS`, reconstructed from the binary HUD via
`strategy.opportunity`). Search walks occupancy hot spots until it sees a crewmate,
then follows that target. Hunt does not pre-position anymore; it activates only when
the kill is ready and a victim is visible.

Aggressive experiment: ``CREWBORG_BE_DUMB=1`` (or ``BE_DUMB=1``) replaces the
imposter ``Playing`` priority with only Search/Hunt: Hunt when kill-ready with a
visible victim, otherwise Search. It deliberately skips Pretend, Evade, and
Report Body so we can isolate "always prepare to kill" behavior.
"""

from __future__ import annotations

import os

from crewborg.strategy.commander.bias import commander_of
from crewborg.strategy.opportunity import (
    has_visible_victim,
    most_recent_victim,
    recon_window,
    ticks_until_kill_ready,
)
from crewborg.strategy.suspicion import active_tail_suspect
from crewborg.types import ActionState, Belief
from players.player_sdk import ModeDirective
from players.player_sdk.types import BeliefSnapshot

# Ticks after a kill during which the imposter stays in Evade. Evade no longer "flees" —
# it RE-APPROACHES the densest expected-crew area (modes/evade.py), so this is the post-kill
# "go to where we think the crew are" window. Back to 72 (≈3s): a 400t sweep arm (v65)
# A/B'd WORSE than 72t (v63) — camping the crowd most of the cooldown reads as suspicious and
# raised our ejection (win 88%→70%) for no kill gain. The 72t re-approach is inert-but-harmless;
# the witness-drop (modes/hunt.py) is the actual driver of the confirmed +19pp ≥2-kill / +14pp
# win (v63 vs v54, p=0.038). Env-tunable for sweeps via CREWBORG_EVADE_TICKS.
EVADE_TICKS = int(os.environ.get("CREWBORG_EVADE_TICKS", "72"))


class RuleBasedStrategy:
    def __init__(self) -> None:
        # The tail we've committed to accusing (sticky across the walk to the button),
        # and whether we've already spent this game's single emergency-button call.
        self._accuse_target: str | None = None
        self._button_call_spent: bool = False

    def decide(self, snapshot: BeliefSnapshot[Belief, ActionState]) -> ModeDirective:
        with snapshot.read() as memory:
            belief = memory.belief
            directive = self.select(belief)
        return directive

    def select(self, belief: Belief) -> ModeDirective:
        return self._select(belief)

    def _select(self, belief: Belief) -> ModeDirective:
        phase = belief.phase

        if phase in ("Lobby", "RoleReveal"):
            self._reset_for_new_game()  # a fresh game restores the button-call budget

        if phase == "Voting":
            self._accuse_target = None  # the meeting is open; nothing to walk to
            return ModeDirective(mode="attend_meeting", source="strategy", reason="meeting open")

        if phase == "Playing":
            # A ghost (dead, either role) can't report or be threatened; it only
            # finishes its own tasks (design §7.3), so it goes straight to Normal.
            if not belief.self_alive:
                self._accuse_target = None
                return ModeDirective(mode="normal", source="strategy", reason="ghost: finish own tasks")
            if belief.self_role == "imposter":
                self._accuse_target = None
                return self._select_imposter(belief)
            # Live crewmate (or not-yet-known role): full field priority. Reporting a
            # visible body outranks accusing — a body report opens a meeting right here
            # and doesn't spend our one button call.
            if any(bid in belief.bodies for bid in belief.visible_body_ids):
                self._accuse_target = None
                return ModeDirective(mode="report_body", source="strategy", reason="body in view")
            if _button_reachable(belief) and self._sticky_accuse_target(belief) is not None:
                if self._inside_button_rect(belief):
                    self._button_call_spent = True  # the A-press at the button fires this tick
                return ModeDirective(mode="accuse", source="strategy", reason="being tailed: call a meeting")
            self._accuse_target = None
            return ModeDirective(mode="normal", source="strategy", reason="playing: do tasks")

        # All other non-play phases (VoteResult / GameOver / unknown).
        self._accuse_target = None
        return ModeDirective(mode="idle", source="strategy", reason=f"idle in phase {phase}")

    def _select_imposter(self, belief: Belief) -> ModeDirective:
        # Imposter priority (design §10): just killed -> Evade; kill ready and a
        # victim visible -> Hunt; near-ready with known crew -> Recon; else SEARCH.
        # Imposters never report bodies: self-reporting our own kill opens a meeting
        # and resets the cooldown, so once Evade ends we go back to the kill loop.
        # SEARCH is the always-on seeking stance (Pretend removed 2026-06-24): it
        # keeps us near crew — watching a room and following a crewmate to their next
        # room — so a kill window opens, which is when Hunt takes over. RECON (added
        # 2026-06-25) sits just before the kill comes ready: within recon_window() ticks
        # of ready we beeline to the most-recently-seen crewmate so a victim is already
        # in hand the instant we can kill (warehouse: we had a crew in view at ready only
        # 53% of the time vs Aaron's 83%).
        if _be_dumb_enabled():
            if belief.self_kill_ready and has_visible_victim(belief):
                return ModeDirective(mode="hunt", source="strategy", reason="be dumb: kill ready with visible victim")
            return ModeDirective(mode="search", source="strategy", reason="be dumb: always seek kill setup")
        cmd = commander_of(belief)
        if _recent_self_kill(belief) and not (cmd is not None and cmd.skip_evade):
            return ModeDirective(mode="evade", source="strategy", reason="just killed: evade")
        if _recent_self_kill(belief) and cmd is not None and cmd.skip_evade:
            belief.commander_danger_events.append(
                {
                    "lever": "skip_evade",
                    "danger_reason": cmd.danger_reason or "",
                }
            )
        if belief.self_kill_ready and has_visible_victim(belief):
            return ModeDirective(mode="hunt", source="strategy", reason="kill ready: hunt visible victim")
        # Recon only in the strictly PRE-ready window. If the kill is already ready but no
        # victim is visible, do NOT recon (it would beeline to a stale last-known position and
        # freeze on it — see the recon-stall lesson); go to Search to actively find a victim.
        if not belief.self_kill_ready and ticks_until_kill_ready(belief) <= recon_window() and most_recent_victim(belief) is not None:
            return ModeDirective(mode="recon", source="strategy", reason="kill nearly ready: close on a crewmate")
        return ModeDirective(mode="search", source="strategy", reason="seek crew to be near a kill")

    def _sticky_accuse_target(self, belief: Belief) -> str | None:
        """The tail we should keep heading to the button to accuse, or ``None``.

        Once we've spent the one button call this game, we never accuse again (fall
        back to tasks). Otherwise we **commit** to a target: stay locked on it through
        the walk to the button even if the tail briefly lapses, until it's voted out /
        dies. We re-acquire from ``active_tail_suspect`` only when not already committed.
        """

        if self._button_call_spent:
            self._accuse_target = None
            return None
        if self._accuse_target is not None and self._accuse_target_alive(belief, self._accuse_target):
            return self._accuse_target
        self._accuse_target = active_tail_suspect(belief)
        return self._accuse_target

    def _accuse_target_alive(self, belief: Belief, color: str) -> bool:
        record = belief.roster.get(color)
        return record is not None and record.life_status != "dead"

    def _inside_button_rect(self, belief: Belief) -> bool:
        if belief.map is None or belief.self_world_x is None or belief.self_world_y is None:
            return False
        button = belief.map.button
        return (
            button.x <= belief.self_world_x < button.x + button.w
            and button.y <= belief.self_world_y < button.y + button.h
        )

    def _reset_for_new_game(self) -> None:
        self._accuse_target = None
        self._button_call_spent = False


def _button_reachable(belief: Belief) -> bool:
    """Whether we can actually walk to the emergency button to call a meeting.

    Without a nav graph yet we optimistically allow it (the action layer steers
    straight at the button center, and the graph builds within a tick or two). Once
    the graph exists, a missing ``button_anchor`` means the button is unreachable —
    don't commit to Accuse (we'd stall at an unrouteable goal); just keep tasking.
    """

    if belief.map is None:
        return False
    if belief.nav is None:
        return True
    return belief.nav.button_anchor is not None


def _recent_self_kill(belief: Belief) -> bool:
    return belief.last_kill_tick is not None and belief.last_tick - belief.last_kill_tick < EVADE_TICKS


def _be_dumb_enabled() -> bool:
    return _truthy_env("CREWBORG_BE_DUMB") or _truthy_env("BE_DUMB")


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
