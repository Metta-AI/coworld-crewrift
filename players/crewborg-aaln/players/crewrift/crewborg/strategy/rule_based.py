"""Rule-based mode selector (design §10).

A deterministic ``decide(snapshot) -> ModeDirective`` run every tick via
``SynchronousStrategyRunner`` — pure rules over belief. Because it runs every
tick, transitions are re-evaluated each cycle (no reflexes).

Crewmate priority order (design §10):

1. ``phase == Voting`` → Attend Meeting
2. with ``CREWBORG_DICK_MODE=1``, once the first kill cooldown is near ready →
   Dick Mode (call the emergency button, taunt only if that call opens the
   meeting, then resume tasking)
3. a body in view → Report Body (a meeting protects us; outranks fleeing)
4. a believed imposter approaching → Flee, with hysteresis so we do not bounce
   back to tasks while skirting the trigger radius
5. tailed (a follower shadowing us with no witnesses near,
   ``strategy.shadow.active_tail``) → Seek Crowd, breaking the shadow-kill setup
6. ``phase == Playing`` → Normal; crewmate ghosts use Crewmate Ghost to finish
   tasks with noclip navigation
7. otherwise → idle

``believed_imposters`` (which gates Flee) is filled by the suspicion model
(``strategy.suspicion``, design §10.1), folded into belief each tick.

Imposter priority order (design §10):

1. ``phase == Voting`` → Attend Meeting
2. just killed → Evade (vent / leave the body)
3. a body in view → Report Body (non-fresh bodies only)
4. kill ready + a visible victim → Hunt (commit to a victim and strike / close)
5. kill ready or within ``SEARCH_LEAD_TICKS`` of ready → Search (find/follow a target)
6. otherwise → Pretend (fake tasks in likely occupied rooms)

(2) prevents instant self-reports after our own kill: the imposter first leaves the
scene, preferably through a vent. A non-fresh body can still be reported later if it
remains visible after the evade window.

(5) fires once the kill cooldown is within a short lead window of being ready
(`ticks_until_kill_ready ≤ SEARCH_LEAD_TICKS`, reconstructed from the binary HUD via
`strategy.opportunity`). Search walks occupancy hot spots until it sees a crewmate,
then follows that target. Hunt does not pre-position anymore; it activates only when
the kill is ready and a victim is visible.

Aggressive experiment: ``CREWBORG_BE_DUMB=1`` (or ``BE_DUMB=1``) replaces the
imposter ``Playing`` priority with only Search/Hunt: Hunt when kill-ready with a
visible victim, otherwise Search. It deliberately skips Pretend, Evade, and
Report Body so we can isolate "always prepare to kill" behavior.

Crewmate nuisance experiment: ``CREWBORG_DICK_MODE=1`` (or ``DICK_MODE=1``)
interrupts normal tasking once per game, far enough before the first kill
cooldown clears that a worst-case walk to the emergency button should still land
with a small buffer. The selector switches to ``dick_mode`` until either our
button press opens the emergency meeting or the attempt times out.

Anti-sussyboi fork: ``CREWBORG_HUNTER=1`` (``strategy.hunter``) adds the timed
crew button (``jam_button``: stage at the button, press at cooldown expiry —
maximum kill denial) and the imposter button stakeout (``stakeout``: lurk by the
button through the window where sussyboi-style crews walk in to press, and kill
the approacher). Both share the single ButtonCalls budget with dick mode and the
evidence call.
"""

from __future__ import annotations

import os

from players.crewrift.crewborg.modes.seek_crowd import SeekCrowdParams
from players.crewrift.crewborg.strategy.hunter import (
    JAM_MAX_STAGING_TICKS,
    JAM_START_MARGIN_TICKS,
    button_press_goal,
    estimate_travel_ticks,
    hunter_enabled,
    stakeout_window_active,
)
from players.crewrift.crewborg.strategy.meeting import MeetingParams, read_meeting_params_from_env
from players.crewrift.crewborg.strategy.opportunity import (
    SEARCH_LEAD_TICKS,
    has_visible_victim,
    ticks_until_kill_ready,
)
from players.crewrift.crewborg.strategy.shadow import active_tail
from players.crewrift.crewborg.types import ActionState, Belief, PlayerRecord
from players.player_sdk import ModeDirective
from players.player_sdk.types import BeliefSnapshot

# A recently seen believed imposter within this distance (squared, world px)
# counts as "approaching" and triggers Flee.
FLEE_ENTER_SQ = 60**2
# Once Flee is active, keep it until the current threat is clearly farther away.
FLEE_EXIT_SQ = 100**2
# Stop fleeing a stale last-known position after this many unseen ticks.
FLEE_STALE_TICKS = 48
# Ticks after a kill during which the imposter prefers to Evade (≈1.5s at 24 Hz).
# Shortened 72 → 36 for v4: the 2026-06-11 replay reconstruction showed post-kill
# flight is unpunished at this meta (daveey-notsus barely flees and wins 86%;
# bodies mostly go unreported) while our kills/ep trailed the leaders — leave the
# body briefly, then re-acquire the next target immediately.
EVADE_TICKS = 36
# Conservative upper bound for walking from the far side of Croatoan to the bridge
# emergency button. Croatoan is 1235x659 px (diagonal ≈1400 px); with MaxSpeed
# 704/256≈2.75 px/tick the straight-line bound is ≈510 ticks, rounded up for path
# shape, controller settling, and route churn.
DICK_MAX_BUTTON_TRAVEL_TICKS = 600
# Start the button run this many ticks before the kill cooldown would clear after
# the worst-case walk.
DICK_KILL_COOLDOWN_BUFFER_TICKS = 10
# Once the action layer has actually pressed A on the emergency button, wait this
# long for a meeting before assuming the server refused the call (for example,
# because ButtonCalls is already spent) and resuming normal tasking.
DICK_CALL_NO_MEETING_GRACE_TICKS = 48

# Tail-response gating (v4.1 fix, measured on the first v4 hosted eval): the raw
# tail detector fires on every champion shadower (truecrew shadows us 54% of
# co-alive time), and an ungated response burned 14% of crewmate Playing ticks
# (~6 activations/ep) — tasks collapsed 7.4 → 5.4/8 and the task-out win path
# with them. A shadow kill needs the killer's cooldown to be READY, and the kill
# clock is globally observable (it resets at every meeting / game start), so:
# - respond only when the imposters' estimated kill clock is within
#   ``TAIL_KILL_WINDOW_TICKS`` of ready (shadows during a fresh cooldown are
#   harmless — keep tasking);
# - cap one response burst at ``TAIL_RESPONSE_MAX_TICKS`` (enough to reach a
#   crowd) and then return to tasking for at least
#   ``TAIL_RESPONSE_REFRACTORY_TICKS`` so the response can never re-monopolize
#   the tick budget against a persistent shadower.
#
# NB (sussybuster_iteration iter-1, 2026-06-12): widening these (window 320,
# burst 220, refractory 240) to react harder to the buzzer field's 249-tick
# tails REGRESSED crew survival 38%→26% (killed 62%→74%, p≈0.09) — the
# break-to-crowd response walks us INTO the central hub where the killers camp
# (Science Bay deaths 2→12). Reverted to the champion-tuned values. The crowd is
# the danger, not the refuge, in this field — see iter-2.
TAIL_KILL_WINDOW_TICKS = 150
TAIL_RESPONSE_MAX_TICKS = 96
TAIL_RESPONSE_REFRACTORY_TICKS = 480

# Evidence-driven emergency call (design §10; always on, unlike the DICK_MODE
# experiment): a crewmate holding actionable suspicion — a believed imposter,
# i.e. a witnessed kill/vent or a posterior over the flee bar — walks to the
# button and opens the meeting itself instead of sitting on the information
# (bodies in low-traffic rooms otherwise never get found; the 2026-06-10 eval
# saw every player's single button call go unused). Same travel bound as dick
# mode; the attempt is one-shot per episode (the server's ButtonCalls budget is
# 1), spent once we actually press or the walk times out.
EVIDENCE_CALL_MAX_TRAVEL_TICKS = DICK_MAX_BUTTON_TRAVEL_TICKS
EVIDENCE_CALL_NO_MEETING_GRACE_TICKS = DICK_CALL_NO_MEETING_GRACE_TICKS


class RuleBasedStrategy:
    def __init__(
        self,
        *,
        be_dumb: bool | None = None,
        meeting_params: MeetingParams | None = None,
        dick_enabled: bool | None = None,
        hunter: bool | None = None,
    ) -> None:
        self._be_dumb = be_dumb if be_dumb is not None else _be_dumb_enabled()
        self._meeting_params = meeting_params if meeting_params is not None else read_meeting_params_from_env()
        self._dick_enabled = dick_enabled if dick_enabled is not None else _dick_mode_enabled()
        self._hunter = hunter if hunter is not None else hunter_enabled()
        self._flee_target: str | None = None
        self._dick_state: str = "idle"
        self._dick_call_started_tick: int | None = None
        self._dick_button_spent = False
        self._button_state: str = "idle"  # evidence-driven emergency call FSM
        self._button_started_tick: int | None = None
        self._evidence_button_spent = False
        self._jam_state: str = "idle"  # hunter timed-button FSM (strategy.hunter)
        self._jam_started_tick: int | None = None
        self._jam_button_spent = False
        self._tail_response_started_tick: int | None = None
        self._tail_refractory_until_tick = 0

    def decide(self, snapshot: BeliefSnapshot[Belief, ActionState]) -> ModeDirective:
        with snapshot.read() as memory:
            belief = memory.belief
            directive = self._select(belief, memory.action_state)
        return directive

    def _select(self, belief: Belief, action_state: ActionState) -> ModeDirective:
        phase = belief.phase

        if phase == "Voting":
            self._clear_flee()
            if self._jam_state == "staging":
                # A meeting opened (ours or someone else's). If we actually
                # pressed, the jam budget is spent; otherwise the meeting reset
                # the cooldown for us — keep the budget for the next segment.
                if self._did_press_button(action_state, self._jam_started_tick):
                    self._jam_button_spent = True
                self._finish_jam_attempt()
            if self._button_state == "calling":
                # A meeting opened (ours or someone else's). If we actually
                # pressed, the call budget is spent either way.
                if self._did_press_button(action_state, self._button_started_tick):
                    self._evidence_button_spent = True
                self._finish_button_attempt()
            if self._dick_state == "calling":
                if self._did_press_emergency_button(action_state):
                    self._dick_state = "meeting"
                else:
                    self._finish_dick_attempt()
            if self._dick_state == "meeting":
                return ModeDirective(mode="dick_mode", source="strategy", reason="dick mode: emergency meeting")
            return ModeDirective(
                mode="attend_meeting",
                params=self._meeting_params,
                source="strategy",
                reason="meeting open",
            )

        if phase == "Playing":
            if self._dick_state == "meeting":
                self._finish_dick_attempt()
            # A crewmate ghost can't report or be threatened; it only finishes its
            # own tasks (design §7.3), so it uses wall-ignoring task navigation.
            if belief.self_role == "dead":
                self._clear_flee()
                self._reset_dick_mode()
                self._finish_button_attempt()
                self._finish_jam_attempt()
                return ModeDirective(mode="crewmate_ghost", source="strategy", reason="ghost: finish own tasks")
            if belief.self_role == "imposter":
                self._clear_flee()
                self._reset_dick_mode()
                self._finish_button_attempt()
                self._finish_jam_attempt()
                return self._select_imposter(belief)
            # Live crewmate (or not-yet-known role): full field priority. Reporting a
            # visible body outranks fleeing — a meeting protects us and lets the crew
            # act, which beats running from a suspect we could instead report.
            if self._dick_state == "calling":
                if self._dick_call_timed_out(belief, action_state):
                    self._finish_dick_attempt()
                else:
                    return ModeDirective(mode="dick_mode", source="strategy", reason="dick mode: call meeting")
            if self._should_start_dick_mode(belief):
                self._dick_state = "calling"
                self._dick_call_started_tick = belief.last_tick
                self._dick_button_spent = True
                self._clear_flee()
                return ModeDirective(mode="dick_mode", source="strategy", reason="dick mode: kill cooldown reset")
            if any(bid in belief.bodies for bid in belief.visible_body_ids):
                self._clear_flee()
                return ModeDirective(mode="report_body", source="strategy", reason="body in view")
            if self._sticky_flee_target(belief) is not None:
                return ModeDirective(mode="flee", source="strategy", reason="believed imposter near")
            self._clear_flee()
            # Tailed (a follower glued to us with no witnesses around — the
            # shadow-kill setup behind 34/46 of our crewmate deaths in the
            # 2026-06-11 replay reconstruction): break toward the nearest crowd
            # before the strike window opens. Below Flee (a believed imposter at
            # close range is a *now* threat) but above tasking. Gated on the
            # kill clock + a burst cap/refractory (see TAIL_* constants) so
            # persistent shadowers can't starve the task engine.
            tail = active_tail(belief)
            if tail is not None and self._tail_response_allowed(belief):
                return ModeDirective(
                    mode="seek_crowd",
                    params=SeekCrowdParams(avoid_color=tail),
                    source="strategy",
                    reason="tailed: seeking crowd",
                )
            if tail is None:
                self._tail_response_started_tick = None
            jam_directive = self._select_jam(belief, action_state)
            if jam_directive is not None:
                return jam_directive
            button_directive = self._select_evidence_call(belief, action_state)
            if button_directive is not None:
                return button_directive
            return ModeDirective(mode="normal", source="strategy", reason="playing: do tasks")

        # All non-play phases (GameInfo / RoleReveal / Lobby / MeetingCall /
        # VoteResult / GameOver / unknown). MeetingCall deliberately preserves an
        # in-flight dick-mode attempt: the meeting our button press opened shows
        # the call interstitial before Voting.
        self._clear_flee()
        if self._dick_state == "meeting":
            self._finish_dick_attempt()
        elif phase in {"Lobby", "GameInfo", "RoleReveal", "GameOver", "unknown"}:
            self._reset_dick_mode()
            self._reset_button()
            self._reset_jam()
        return ModeDirective(mode="idle", source="strategy", reason=f"idle in phase {phase}")

    def _select_imposter(self, belief: Belief) -> ModeDirective:
        # Imposter priority (design §10): just killed -> Evade; non-fresh visible
        # body -> Report; kill ready and a victim visible -> Hunt; kill ready or
        # about to be -> Search; else Pretend.
        #
        # Deliberately NOT adopted for v4 (evaluated against the 2026-06-11
        # evidence):
        # - Venting: zero vent hops across all 196 champion imposter
        #   slot-episodes in the replay reconstruction — venting's value is tied
        #   to body-discovery risk, which is ~nil at this meta (22/31 of our
        #   kills go unreported). The `vent` intent/resolver stays for escape
        #   routing, but no mode seeks vents.
        # - The truecrew-style early *imposter* button (median tick 811): a
        #   meeting resets our own and our teammate's kill cooldowns, directly
        #   opposing the kill-tempo fixes (EVADE_TICKS / SEARCH_LEAD_TICKS), and
        #   our imposter blend already holds (0 ejections in 44 imposter
        #   episodes across both evals) — the blend upside is unproven while the
        #   tempo cost is mechanical. Deferred, not just unimplemented.
        if self._be_dumb:
            if belief.self_kill_ready and has_visible_victim(belief):
                return ModeDirective(mode="hunt", source="strategy", reason="be dumb: kill ready with visible victim")
            if self._hunter and stakeout_window_active(belief):
                return ModeDirective(mode="stakeout", source="strategy", reason="hunter: staking out the button")
            return ModeDirective(mode="search", source="strategy", reason="be dumb: always seek kill setup")
        if _recent_self_kill(belief):
            return ModeDirective(mode="evade", source="strategy", reason="just killed: evade")
        if any(bid in belief.bodies for bid in belief.visible_body_ids):
            return ModeDirective(mode="report_body", source="strategy", reason="body in view after evade window")
        if belief.self_kill_ready and has_visible_victim(belief):
            return ModeDirective(mode="hunt", source="strategy", reason="kill ready: hunt visible victim")
        if self._hunter and stakeout_window_active(belief):
            return ModeDirective(mode="stakeout", source="strategy", reason="hunter: staking out the button")
        if ticks_until_kill_ready(belief) <= SEARCH_LEAD_TICKS:
            return ModeDirective(mode="search", source="strategy", reason="kill window near: search for target")
        return ModeDirective(mode="pretend", source="strategy", reason="blend in")

    def _sticky_flee_target(self, belief: Belief) -> str | None:
        """Return the threat that should keep Flee active this tick.

        Flee enters on the existing 60px trigger, then exits only when the same
        threat is clearly farther away or its last-known position is stale. This
        prevents the normal/task selector and flee selector from fighting at the
        exact trigger radius.
        """

        if self._flee_target is not None and _should_continue_flee(belief, self._flee_target):
            return self._flee_target
        self._flee_target = _nearest_enter_threat(belief)
        return self._flee_target

    def _clear_flee(self) -> None:
        self._flee_target = None

    def _tail_response_allowed(self, belief: Belief) -> bool:
        """Whether the crowd-seek response may run this tick (see TAIL_* gating).

        The kill-clock gate: a shadow kill needs the imposter's cooldown ready,
        and the cooldown clock is globally observable (reset at every meeting →
        Playing transition), so during a fresh cooldown the shadow is harmless
        and tasking continues. Within the danger window, responses run in
        bounded bursts with a refractory between them.
        """

        tick = belief.last_tick
        if tick < self._tail_refractory_until_tick:
            return False
        if ticks_until_kill_ready(belief) > TAIL_KILL_WINDOW_TICKS:
            self._tail_response_started_tick = None
            return False
        if self._tail_response_started_tick is None:
            self._tail_response_started_tick = tick
            return True
        if tick - self._tail_response_started_tick >= TAIL_RESPONSE_MAX_TICKS:
            self._tail_response_started_tick = None
            self._tail_refractory_until_tick = tick + TAIL_RESPONSE_REFRACTORY_TICKS
            return False
        return True

    def _select_evidence_call(self, belief: Belief, action_state: ActionState) -> ModeDirective | None:
        """The evidence-driven emergency-call directive, or ``None`` to task on.

        Trigger: an alive crewmate with a believed imposter (the flee-bar
        posterior — in practice a witnessed kill/vent) and an unspent button
        call. The walk aborts on a server-refused press (no meeting within the
        grace window), on travel timeout, or when the evidence dissolves (the
        believed imposter died); refusal/timeout mark the budget spent so the
        attempt never loops.
        """

        if self._button_state == "calling":
            if not belief.believed_imposters:
                self._finish_button_attempt()  # evidence dissolved: keep the budget
                return None
            if self._button_press_refused(belief, action_state):
                self._evidence_button_spent = True
                self._finish_button_attempt()
                return None
            if self._button_walk_timed_out(belief, action_state):
                self._evidence_button_spent = True  # unreachable button: don't retry forever
                self._finish_button_attempt()
                return None
            return ModeDirective(mode="call_button", source="strategy", reason="evidence call: walking to button")
        if self._should_call_button(belief):
            self._button_state = "calling"
            self._button_started_tick = belief.last_tick
            return ModeDirective(mode="call_button", source="strategy", reason="evidence call: believed imposter")
        return None

    def _should_call_button(self, belief: Belief) -> bool:
        if self._button_budget_spent():
            return False
        if belief.self_role not in {None, "crewmate"}:
            return False
        if belief.map is None:
            return False
        return bool(belief.believed_imposters)

    def _button_budget_spent(self) -> bool:
        """Whether our single server-side ButtonCalls budget is (assumed) gone.

        Three FSMs share the one budget: the evidence call, dick mode, and the
        hunter jam — any of them pressing (or burning the attempt on a refusal/
        timeout) spends it for all.
        """

        return self._evidence_button_spent or self._dick_button_spent or self._jam_button_spent

    # --- hunter jam (strategy.hunter): the timed anti-sussyboi button ---------

    def _select_jam(self, belief: Belief, action_state: ActionState) -> ModeDirective | None:
        """The hunter timed-button directive, or ``None`` to fall through.

        Trigger: an alive crewmate with the button budget free, once the
        reconstructed imposter kill cooldown is within (estimated travel +
        ``JAM_START_MARGIN_TICKS``) of ready. The mode stages at the button and
        presses at ``JAM_PRESS_LEAD_TICKS`` — the moment of maximum cooldown
        denial. Refusal / staging timeout spends the budget so we never loop.
        """

        if not self._hunter:
            return None
        if self._jam_state == "staging":
            if self._jam_press_refused(belief, action_state):
                self._jam_button_spent = True
                self._finish_jam_attempt()
                return None
            if self._jam_staging_timed_out(belief, action_state):
                self._jam_button_spent = True  # unreachable/contested button: don't retry forever
                self._finish_jam_attempt()
                return None
            return ModeDirective(mode="jam_button", source="strategy", reason="hunter: jam staging")
        if self._should_start_jam(belief):
            self._jam_state = "staging"
            self._jam_started_tick = belief.last_tick
            return ModeDirective(mode="jam_button", source="strategy", reason="hunter: jam window opening")
        return None

    def _should_start_jam(self, belief: Belief) -> bool:
        if self._button_budget_spent() or self._button_state == "calling" or self._dick_state != "idle":
            return False
        if belief.self_role not in {None, "crewmate"}:
            return False
        if belief.map is None or belief.self_world_x is None or belief.self_world_y is None:
            return False
        goal = button_press_goal(belief)
        if goal is None:
            return False
        travel = estimate_travel_ticks(belief, (belief.self_world_x, belief.self_world_y), goal)
        return ticks_until_kill_ready(belief) <= travel + JAM_START_MARGIN_TICKS

    def _jam_press_refused(self, belief: Belief, action_state: ActionState) -> bool:
        attempt_tick = action_state.last_call_meeting_attempt_tick
        if self._jam_started_tick is None or attempt_tick is None or attempt_tick < self._jam_started_tick:
            return False
        return belief.last_tick - attempt_tick >= EVIDENCE_CALL_NO_MEETING_GRACE_TICKS

    def _jam_staging_timed_out(self, belief: Belief, action_state: ActionState) -> bool:
        if self._jam_started_tick is None or self._did_press_button(action_state, self._jam_started_tick):
            return False
        return belief.last_tick - self._jam_started_tick >= JAM_MAX_STAGING_TICKS

    def _finish_jam_attempt(self) -> None:
        self._jam_state = "idle"
        self._jam_started_tick = None

    def _reset_jam(self) -> None:
        self._jam_state = "idle"
        self._jam_started_tick = None
        self._jam_button_spent = False

    def _did_press_button(self, action_state: ActionState, started_tick: int | None) -> bool:
        if started_tick is None or action_state.last_call_meeting_attempt_tick is None:
            return False
        return action_state.last_call_meeting_attempt_tick >= started_tick

    def _button_press_refused(self, belief: Belief, action_state: ActionState) -> bool:
        attempt_tick = action_state.last_call_meeting_attempt_tick
        if self._button_started_tick is None or attempt_tick is None or attempt_tick < self._button_started_tick:
            return False
        return belief.last_tick - attempt_tick >= EVIDENCE_CALL_NO_MEETING_GRACE_TICKS

    def _button_walk_timed_out(self, belief: Belief, action_state: ActionState) -> bool:
        if self._button_started_tick is None or self._did_press_button(action_state, self._button_started_tick):
            return False
        return belief.last_tick - self._button_started_tick >= EVIDENCE_CALL_MAX_TRAVEL_TICKS

    def _finish_button_attempt(self) -> None:
        self._button_state = "idle"
        self._button_started_tick = None

    def _reset_button(self) -> None:
        self._button_state = "idle"
        self._button_started_tick = None
        self._evidence_button_spent = False

    def _should_start_dick_mode(self, belief: Belief) -> bool:
        if not self._dick_enabled:
            self._reset_dick_mode()
            return False
        if self._button_budget_spent():
            return False
        if belief.self_role not in {None, "crewmate"}:
            return False
        trigger_window = DICK_MAX_BUTTON_TRAVEL_TICKS + DICK_KILL_COOLDOWN_BUFFER_TICKS
        return ticks_until_kill_ready(belief) <= trigger_window

    def _did_press_emergency_button(self, action_state: ActionState) -> bool:
        if self._dick_call_started_tick is None or action_state.last_call_meeting_attempt_tick is None:
            return False
        return action_state.last_call_meeting_attempt_tick >= self._dick_call_started_tick

    def _dick_call_timed_out(self, belief: Belief, action_state: ActionState) -> bool:
        if self._dick_call_started_tick is None:
            return False
        attempt_tick = action_state.last_call_meeting_attempt_tick
        if attempt_tick is None or attempt_tick < self._dick_call_started_tick:
            return False
        return belief.last_tick - attempt_tick >= DICK_CALL_NO_MEETING_GRACE_TICKS

    def _finish_dick_attempt(self) -> None:
        self._dick_state = "idle"
        self._dick_call_started_tick = None

    def _reset_dick_mode(self) -> None:
        self._dick_state = "idle"
        self._dick_call_started_tick = None
        self._dick_button_spent = False


def _recent_self_kill(belief: Belief) -> bool:
    return belief.last_kill_tick is not None and belief.last_tick - belief.last_kill_tick < EVADE_TICKS


def _be_dumb_enabled() -> bool:
    return _truthy_env("CREWBORG_BE_DUMB") or _truthy_env("BE_DUMB")


def _dick_mode_enabled() -> bool:
    return _truthy_env("CREWBORG_DICK_MODE") or _truthy_env("DICK_MODE")


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _threat_approaching(belief: Belief) -> bool:
    return _nearest_enter_threat(belief) is not None


def _nearest_enter_threat(belief: Belief) -> str | None:
    self_xy = _self_xy(belief)
    if self_xy is None:
        return None
    candidates: list[tuple[int, str]] = []
    for color in belief.believed_imposters:
        record = _fresh_believed_record(belief, color)
        if record is None:
            continue
        dist2 = _dist2(self_xy, (record.world_x, record.world_y))
        if dist2 <= FLEE_ENTER_SQ:
            candidates.append((dist2, color))
    if not candidates:
        return None
    return min(candidates)[1]


def _should_continue_flee(belief: Belief, color: str) -> bool:
    self_xy = _self_xy(belief)
    record = _fresh_believed_record(belief, color)
    if self_xy is None or record is None:
        return False
    return _dist2(self_xy, (record.world_x, record.world_y)) <= FLEE_EXIT_SQ


def _fresh_believed_record(belief: Belief, color: str) -> PlayerRecord | None:
    if color not in belief.believed_imposters:
        return None
    record = belief.roster.get(color)
    if record is None or record.life_status == "dead":
        return None
    if belief.last_tick - record.last_seen_tick > FLEE_STALE_TICKS:
        return None
    return record


def _self_xy(belief: Belief) -> tuple[int, int] | None:
    if belief.self_world_x is None or belief.self_world_y is None:
        return None
    return belief.self_world_x, belief.self_world_y


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2
