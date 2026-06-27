"""Attend Meeting mode: conversational chat plus deadline-safe voting (design §10.4).

Active for the whole ``Voting`` phase. It runs two layers:

- **LLM-driven (primary).** When a meeting LLM client is enabled, each tick may fire a
  cadence-gated call (``_next_llm_trigger``: meeting start / new external chat / chat
  cooldown ready / deadline) that returns a structured ``MeetingDecision`` — send a chat
  line, set a tentative vote, or submit the vote. Validation, duplicate-chat suppression,
  and a chat cooldown sit between the decision and the emitted intent.
- **Deterministic fallback.** When the client is disabled, or a call fails/returns an
  invalid decision, role-specific rules take over (``_decide_crewmate`` /
  ``_decide_imposter``). Chat and vote are always **coupled** — we accuse exactly whom we
  vote (the anti-tell). Crewmate: accuse + vote a clear leading suspect, else stay silent
  and skip a flat field. Imposter: proactively deflect onto a non-teammate with real
  citable evidence; else **bandwagon** onto a crewmate already taking heat with a *safely
  fabricated* accusation in the identical format; else wait and skip at the deadline. We
  never vote a teammate, and a hard guard forbids ever voting ourselves out.

**Deadline safety is the invariant.** No matter the path, the vote is auto-submitted once
the meeting clock is within ``AUTO_SUBMIT_REMAINING_TICKS`` of expiry, and an LLM call is
only started if it can finish (timeout + margin) before that deadline — so a slow or
failing LLM can never cost us the vote.

Collaborators
-------------
Relies on:
  - ``strategy.meeting`` — the LLM client (``MeetingLLMClient`` /
    ``build_meeting_llm_client_from_env``), context serialization, decision validation,
    valid vote targets, and the ``VOTE_SKIP`` / ``CHAT_MAX_CHARS`` constants.
  - ``strategy.meeting.accusation`` — ``build_accusation`` (real) / ``fabricate_accusation``.
  - ``strategy.meeting.context`` — ``CHAT_COOLDOWN_TICKS`` / ``VOTE_TIMER_TICKS``.
  - ``strategy.meeting.imposter`` — ``bandwagon_target`` / ``votes_against``.
  - ``strategy.meeting`` ``chat_nlp`` / ``chat_read`` — chat-accuser parsing for bandwagon.
  - ``strategy.suspicion.top_suspect`` — the deterministic suspect/fallback vote target.
  - ``belief.voting`` / ``action_state.vote_confirmed`` — meeting state and our own marker.
Used by:
  - ``strategy.rule_based`` selects this mode for the entire ``Voting`` phase (§10).
  - ``__init__.build_runtime`` registers it in the ``ModeRegistry``.
Emits: ``chat`` / ``vote`` / ``idle`` intents (executed by ``action.py``) and a rich set of
  ``meeting_*`` trace events + counters (context, decision, fallback, vote/chat selected).

Modifying this file: it decides *what to say and whom to vote* and emits a symbolic Intent
only — it never walks to the vote panel or types (that is ``action.py``). The deadline /
auto-submit timing (``_should_auto_submit`` / ``_can_start_llm_call`` /
``_latest_safe_llm_start_remaining_ticks``) is the load-bearing safety logic; change it
deliberately. All per-meeting state is reset by ``_reset_for_meeting_if_needed`` keyed on
``phase_start_tick`` — add new state there too.
"""

from __future__ import annotations

import math
from typing import Any

from crewborg.strategy.meeting import (
    CHAT_MAX_CHARS,
    VOTE_SKIP,
    MeetingDecision,
    MeetingDecisionValidationError,
    MeetingLLMClient,
    build_meeting_llm_client_from_env,
    serialize_meeting_context,
    valid_vote_targets,
    validate_meeting_decision,
)
from crewborg.strategy.meeting.accusation import build_accusation, fabricate_accusation
from crewborg.strategy.meeting.context import (
    CHAT_COOLDOWN_TICKS,
    VOTE_TIMER_TICKS,
)
from crewborg.strategy.meeting.imposter import bandwagon_target, votes_against
from crewborg.strategy.meeting import chat_nlp, chat_read
from crewborg.strategy.suspicion import top_suspect
from crewborg.types import ActionState, Belief, ChatEvent, Intent
from players.player_sdk import EmptyModeParams, Mode

# Minimum ticks between successive LLM calls — throttles cost and avoids re-prompting on
# every tick while chat trickles in.
LLM_MIN_CALL_INTERVAL_TICKS = 12
# Remaining-ticks threshold at/under which we fire the one final "deadline" LLM prompt
# (floored against the latest-safe start so the call can still finish before auto-submit).
DEADLINE_LLM_REMAINING_TICKS = 96
# Remaining-ticks threshold at/under which we stop deliberating and force the vote out.
AUTO_SUBMIT_REMAINING_TICKS = 48
# Meeting clock conversion: VOTE_TIMER_TICKS spans 10 seconds, so this is ticks per second.
MEETING_TICKS_PER_SECOND = VOTE_TIMER_TICKS // 10
# Safety slack (ticks) added on top of the LLM timeout when deciding the latest safe start.
LLM_TIMEOUT_MARGIN_TICKS = LLM_MIN_CALL_INTERVAL_TICKS
# Assumed LLM timeout (seconds) when the client doesn't expose ``timeout_seconds``.
DEFAULT_LLM_TIMEOUT_SECONDS = 3.0


class AttendMeetingMode(Mode[Belief, ActionState, Intent]):
    """Meeting chat + voting stance. Holds extensive per-meeting state, all reset by
    ``_reset_for_meeting_if_needed`` when ``phase_start_tick`` changes: the LLM client and
    its trigger/cadence bookkeeping (``_last_llm_call_tick``, ``_last_external_chat_signature``,
    ``_deadline_prompted``), chat bookkeeping (``_sent_chat_texts``, ``_pending_chat_text``,
    ``_last_chat_tick``, ``_last_cooldown_prompt_chat_tick``, ``_chat_parse_cache``), the vote
    machine (``_tentative_vote`` we lean toward, ``_active_vote_target``/``_active_vote_reason``
    once committed, ``_vote_submitted``), and one-shot trace latches (``_deterministic_chatted``,
    ``_disabled_traced``, ``_decision_traced``). ``_meeting_id`` is the current
    ``phase_start_tick`` used to detect a new meeting."""

    name = "attend_meeting"
    params_type = EmptyModeParams

    def __init__(self, params=None, *, llm_client: MeetingLLMClient | None = None) -> None:
        super().__init__(params)
        self._llm_client = llm_client if llm_client is not None else build_meeting_llm_client_from_env()
        self._meeting_id: int | None = None
        self._deterministic_chatted = False
        self._disabled_traced = False
        self._sent_chat_texts: set[str] = set()
        self._pending_chat_text: str | None = None
        self._last_chat_tick: int | None = None
        self._last_llm_call_tick: int | None = None
        self._last_external_chat_signature: tuple[tuple[int, str | None, str], ...] = ()
        self._last_cooldown_prompt_chat_tick: int | None = None
        self._deadline_prompted = False
        self._tentative_vote: str | None = None
        self._active_vote_target: str | None = None
        self._active_vote_reason: str = ""
        self._vote_submitted = False
        self._chat_parse_cache: dict[str, set[str]] = {}
        self._decision_traced = False

    def is_legal(self, belief: Belief) -> bool:
        """This mode is only valid during the ``Voting`` phase (a meeting is open)."""
        return belief.phase == "Voting"

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        """Drive one tick of the meeting. Resolves committed/confirmed votes first (idempotent
        once submitted), then: if the LLM is disabled → deterministic fallback; if past the
        auto-submit deadline → force the vote; flush a pending chat once its cooldown clears;
        otherwise, on a cadence trigger, call the LLM and apply/validate its decision (falling
        back on failure). Returns the resulting ``chat`` / ``vote`` / ``idle`` intent. Reads
        ``action_state.vote_confirmed`` to latch that our vote landed."""
        self._reset_for_meeting_if_needed(belief)
        if action_state.vote_confirmed:
            self._vote_submitted = True
            self._active_vote_target = None
            self._active_vote_reason = ""
        if self._vote_submitted:
            return Intent(kind="idle", reason="vote already confirmed")
        if self._active_vote_target is not None:
            return self._vote_intent(self._active_vote_target, reason=self._active_vote_reason)

        if not self._llm_client.enabled:
            return self._decide_deterministic(belief, trace_disabled=True)

        if self._should_auto_submit(belief):
            return self._submit_vote_intent(belief, reason="meeting deadline: auto-submit tentative vote")

        if self._pending_chat_text is not None and self._chat_cooldown_ready(belief):
            return self._send_chat_intent(belief, self._pending_chat_text, reason="sending pending LLM chat")

        trigger = self._next_llm_trigger(belief)
        if trigger is None:
            return Intent(kind="idle", reason="waiting during meeting")

        context = serialize_meeting_context(
            belief,
            trigger=trigger,
            tentative_vote=self._tentative_vote,
            sent_chat_texts=self._sent_chat_texts,
            last_chat_tick=self._last_chat_tick,
        )
        self.emit.event("meeting_context_serialized", {"trigger": trigger, "context": context})
        result = self._call_llm(context, trigger=trigger)
        if result is None:
            return self._decide_after_llm_failure(belief, trigger)
        decision = self._validate_decision(belief, result.decision)
        if decision is None:
            return self._decide_after_llm_failure(belief, trigger)
        self._trace_decision(trigger, decision, result)
        return self._apply_decision(belief, decision)

    # --- deterministic fallback ------------------------------------------

    def _decide_deterministic(self, belief: Belief, *, trace_disabled: bool) -> Intent:
        """No default-firing chat; chat and vote are always coupled (accuse exactly who
        we vote — the anti-tell). The two roles diverge here (design §10.4)."""

        if trace_disabled and not self._disabled_traced:
            self._disabled_traced = True
            self.emit.event(
                "meeting_llm_fallback",
                {"reason": "llm_disabled", "detail": self._llm_client.disabled_reason},
            )
        if belief.self_role == "imposter":
            return self._decide_imposter(belief)
        return self._decide_crewmate(belief)

    def _decide_crewmate(self, belief: Belief) -> Intent:
        """Accuse + vote a clear leading suspect; else stay silent and skip a flat field."""

        if not self._deterministic_chatted:
            self._deterministic_chatted = True
            target = top_suspect(belief)  # the clear leading suspect, or None (flat field)
            if target is not None:
                self._tentative_vote = target  # couple the vote to whoever we accuse
                accusation = build_accusation(belief, target)
                if accusation is not None:
                    self._trace_meeting_decision(belief, role="crewmate", path="accuse", target=target)
                    return self._send_chat_intent(belief, accusation, reason="accusing clear suspect")
                self._trace_meeting_decision(belief, role="crewmate", path="vote_no_chat", target=target)
            else:
                self._trace_meeting_decision(belief, role="crewmate", path="silent_skip", target=None)
        return self._submit_vote_intent(belief, reason="deterministic meeting vote")

    def _decide_imposter(self, belief: Belief) -> Intent:
        """Deflect onto crewmates, never teammates. Prefer a **real** accusation against
        a non-teammate who genuinely looks sus; otherwise wait and **bandwagon** onto a
        crewmate others are sussing/voting, with *fabricated* (safe) evidence in the
        identical format; if nobody takes heat, skip at the deadline."""

        # Already accused someone ⇒ stay coupled: vote exactly them.
        if self._deterministic_chatted and self._tentative_vote is not None:
            return self._submit_vote_intent(belief, reason="imposter: vote whom we accused")

        # 1. Proactive deflection — a non-teammate with strong, real citable evidence.
        target = top_suspect(belief)
        if target is not None:
            accusation = build_accusation(belief, target)
            if accusation is not None:
                self._tentative_vote = target
                self._deterministic_chatted = True
                self._trace_meeting_decision(belief, role="imposter", path="proactive", target=target)
                return self._send_chat_intent(belief, accusation, reason="imposter deflect: real evidence")

        # 2. Reactive bandwagon — a crewmate already taking heat (votes + chat).
        accusers = self._chat_accusers(belief)
        bandwagon = bandwagon_target(belief, accusers)
        if bandwagon is not None:
            self._tentative_vote = bandwagon
            self._deterministic_chatted = True
            fabricated = fabricate_accusation(belief, bandwagon)
            self._trace_meeting_decision(
                belief, role="imposter", path="bandwagon", target=bandwagon,
                fabricated=fabricated is not None, accusers=accusers,
            )
            if fabricated is not None:
                return self._send_chat_intent(belief, fabricated, reason="imposter bandwagon: fabricated")
            return self._submit_vote_intent(belief, reason="imposter bandwagon vote")

        # 3. No one to deflect onto yet — wait, then skip at the deadline.
        if self._should_auto_submit(belief):
            self._trace_meeting_decision(belief, role="imposter", path="skip", target=None, accusers=accusers)
            return self._submit_vote_intent(belief, reason="imposter deadline: no deflection, skip")
        return Intent(kind="idle", reason="imposter waiting for a crewmate to take heat")

    def _trace_meeting_decision(
        self,
        belief: Belief,
        *,
        role: str,
        path: str,
        target: str | None,
        fabricated: bool = False,
        accusers: dict[str, int] | None = None,
    ) -> None:
        """One structured record of the deterministic meeting decision, fired once when
        we commit. The headline diagnostic for the new meeting modes: which path
        (accuse / silent_skip · proactive / bandwagon / skip), the target, real vs
        fabricated, and — for an imposter — the heat that drove it (vote tally + chat
        accusers) and the chat-NLP state, so a replay explains *why* it did what it did."""

        if self._decision_traced:
            return
        self._decision_traced = True
        data: dict[str, Any] = {
            "role": role,
            "path": path,
            "target": target,
            "fabricated": fabricated,
            "top_suspect": top_suspect(belief),
        }
        if role == "imposter":
            data["votes"] = votes_against(belief)
            data["chat_accusers"] = accusers if accusers is not None else {}
            data["nlp"] = chat_nlp.state()
        self.emit.event("meeting_decision", data)
        self.emit.counter("meeting_decision", tags={"role": role, "path": path})

    def _chat_accusers(self, belief: Belief) -> dict[str, int]:
        """Per-color count of *other players* who have accused them in chat — the
        additive bandwagon signal (empty when the chat-NLP model is off / still
        loading). The per-meeting cache avoids re-parsing the same messages each tick."""

        return chat_read.chat_accusers(belief, cache=self._chat_parse_cache)

    # --- LLM call cadence -------------------------------------------------

    def _next_llm_trigger(self, belief: Belief) -> str | None:
        """The reason to call the LLM this tick, or ``None`` to wait. Gated by the min call
        interval and the latest-safe-start deadline; never fires twice after the one-shot
        ``deadline`` prompt. In order: first call → ``meeting_start``; near deadline →
        ``deadline``; new external chat since last call → ``new_chat``; our chat cooldown just
        cleared → ``chat_cooldown_ready``."""
        tick = belief.last_tick
        if self._last_llm_call_tick is not None and tick - self._last_llm_call_tick < LLM_MIN_CALL_INTERVAL_TICKS:
            return None
        if not self._can_start_llm_call(belief):
            return None
        if self._deadline_prompted:
            return None
        if self._last_llm_call_tick is None:
            return "meeting_start"

        if self._remaining_ticks(belief) <= self._deadline_prompt_remaining_ticks():
            return "deadline"

        signature = self._external_chat_signature(belief)
        if signature != self._last_external_chat_signature:
            return "new_chat"

        if (
            self._last_chat_tick is not None
            and self._chat_cooldown_ready(belief)
            and self._last_cooldown_prompt_chat_tick != self._last_chat_tick
        ):
            return "chat_cooldown_ready"

        return None

    def _call_llm(self, context: dict[str, Any], *, trigger: str) -> Any | None:
        """Make the LLM call for ``trigger`` and return its result, or ``None`` on any
        exception (traced as ``llm_call_failed``). Records the call tick, snapshots the
        external-chat signature so ``new_chat`` only fires on genuinely new messages, latches
        the one-shot deadline prompt, and emits the latency histogram. Mutating bookkeeping
        happens before the call so a failure still advances cadence."""
        self._last_llm_call_tick = int(context["meeting"]["tick"])
        self._last_external_chat_signature = tuple(
            (event["tick"], event["speaker_color"], event["text"])
            for event in context["chat"]["messages"]
            if not event["self"]
        )
        if trigger == "deadline":
            self._deadline_prompted = True
        if trigger == "chat_cooldown_ready":
            self._last_cooldown_prompt_chat_tick = self._last_chat_tick
        try:
            result = self._llm_client.decide(context, trigger=trigger)
        except Exception as exc:
            self.emit.event(
                "meeting_llm_fallback",
                {"reason": "llm_call_failed", "trigger": trigger, "error": repr(exc)},
            )
            return None
        self.emit.histogram("meeting_llm.latency_ms", result.latency_ms, tags={"model": result.model, "trigger": trigger})
        return result

    def _validate_decision(self, belief: Belief, decision: MeetingDecision) -> MeetingDecision | None:
        """Validate/normalize an LLM ``MeetingDecision`` against the alive vote targets, our
        current tentative vote, and the deterministic fallback target. Returns the normalized
        decision, or ``None`` (traced as ``invalid_meeting_decision``) so the caller falls back."""
        try:
            return validate_meeting_decision(
                decision,
                alive_vote_targets=valid_vote_targets(belief),
                current_tentative=self._tentative_vote,
                fallback_vote=self._fallback_vote_target(belief),
            )
        except MeetingDecisionValidationError as exc:
            self.emit.event(
                "meeting_llm_fallback",
                {"reason": "invalid_meeting_decision", "error": str(exc), "decision": decision.model_dump(mode="json")},
            )
            return None

    def _trace_decision(self, trigger: str, decision: MeetingDecision, result: Any) -> None:
        """Emit the ``meeting_llm_decision`` trace (trigger, model, latency, usage, decision)
        and, when present, the raw request/response under ``meeting_llm_debug``."""
        self.emit.event(
            "meeting_llm_decision",
            {
                "trigger": trigger,
                "model": result.model,
                "latency_ms": round(result.latency_ms, 2),
                "usage": result.usage,
                "decision": decision.model_dump(mode="json"),
            },
        )
        if result.raw_request is not None or result.raw_response is not None:
            self.emit.event(
                "meeting_llm_debug",
                {"request": result.raw_request, "response": result.raw_response},
            )

    # --- decision application --------------------------------------------

    def _apply_decision(self, belief: Belief, decision: MeetingDecision) -> Intent:
        """Turn a validated ``MeetingDecision`` into an intent. Updates the tentative vote if
        the decision names one, then dispatches on ``decision.action``: ``send_chat`` (emit
        chat now if the cooldown is ready and it isn't a duplicate, else stash it as pending),
        ``submit_vote`` (commit and emit the vote), ``set_tentative_vote`` / default (idle and
        keep deliberating)."""
        if decision.vote_target is not None:
            self._tentative_vote = decision.vote_target
            self.emit.event(
                "meeting_tentative_vote",
                {"target": self._tentative_vote, "reason": decision.reason, "confidence": decision.confidence},
            )

        if decision.action == "send_chat":
            assert decision.chat_text is not None
            if decision.chat_text in self._sent_chat_texts:
                self.emit.event("meeting_llm_fallback", {"reason": "duplicate_chat_suppressed", "text": decision.chat_text})
                return Intent(kind="idle", reason="duplicate LLM chat suppressed")
            if self._chat_cooldown_ready(belief):
                return self._send_chat_intent(belief, decision.chat_text, reason=decision.reason or "LLM meeting chat")
            self._pending_chat_text = decision.chat_text[:CHAT_MAX_CHARS]
            self.emit.event(
                "meeting_llm_fallback",
                {"reason": "chat_cooldown_pending", "text": self._pending_chat_text},
            )
            return Intent(kind="idle", reason="waiting for chat cooldown")

        if decision.action == "submit_vote":
            return self._submit_vote_intent(belief, reason=decision.reason or "LLM submitted vote")

        if decision.action == "set_tentative_vote":
            return Intent(kind="idle", reason=decision.reason or "LLM set tentative vote")

        return Intent(kind="idle", reason=decision.reason or "LLM waits")

    def _send_chat_intent(self, belief: Belief, text: str, *, reason: str) -> Intent:
        """Emit a ``chat`` intent: clear any pending text, record the line as sent (dedupe) and
        stamp the chat tick (starts the chat cooldown), and trace ``meeting_chat_selected``."""
        self._pending_chat_text = None
        self._sent_chat_texts.add(text)
        self._last_chat_tick = belief.last_tick
        self.emit.event("meeting_chat_selected", {"text": text, "reason": reason})
        return Intent(kind="chat", text=text, reason=reason)

    def _submit_vote_intent(self, belief: Belief, *, reason: str) -> Intent:
        """Commit and emit the vote: resolve the target (tentative if still valid, else the
        fallback), apply the hard self-vote guard (never vote ourselves — coerce to skip),
        latch it as the active vote so later ticks re-emit it, and trace
        ``meeting_vote_selected``."""
        vote_target = self._resolved_vote_target(belief)
        # Hard guard: the agent can never vote itself out, whatever suspicion says.
        self_color = belief.self_color or belief.voting.self_marker_color
        if self_color is not None and vote_target == self_color:
            vote_target = VOTE_SKIP
        self._active_vote_target = vote_target
        self._active_vote_reason = reason
        self.emit.event("meeting_vote_selected", {"target": vote_target, "reason": reason})
        return self._vote_intent(vote_target, reason=reason)

    def _vote_intent(self, vote_target: str, *, reason: str) -> Intent:
        """Build the raw ``vote`` intent: a bare skip when ``vote_target == VOTE_SKIP``, else a
        vote for that color."""
        if vote_target == VOTE_SKIP:
            return Intent(kind="vote", reason=reason)
        return Intent(kind="vote", target_color=vote_target, reason=reason)

    def _decide_after_llm_failure(self, belief: Belief, trigger: str) -> Intent:
        """Recovery when an LLM call fails or returns an invalid decision: at the ``deadline``
        trigger force the vote out (safety); at ``meeting_start`` fall through to the
        deterministic path; otherwise idle and wait for the next trigger."""
        if trigger == "deadline":
            return self._submit_vote_intent(belief, reason=f"LLM fallback after {trigger}")
        if trigger == "meeting_start":
            return self._decide_deterministic(belief, trace_disabled=False)
        return Intent(kind="idle", reason=f"LLM fallback after {trigger}")

    # --- state helpers ----------------------------------------------------

    def _reset_for_meeting_if_needed(self, belief: Belief) -> None:
        """Clear all per-meeting state when a new meeting starts (detected by a changed
        ``phase_start_tick``). Any new per-meeting field must be reset here too."""
        meeting_id = belief.phase_start_tick
        if meeting_id == self._meeting_id:
            return
        self._meeting_id = meeting_id
        self._deterministic_chatted = False
        self._disabled_traced = False
        self._sent_chat_texts.clear()
        self._pending_chat_text = None
        self._last_chat_tick = None
        self._last_llm_call_tick = None
        self._last_external_chat_signature = self._external_chat_signature(belief)
        self._last_cooldown_prompt_chat_tick = None
        self._deadline_prompted = False
        self._tentative_vote = None
        self._active_vote_target = None
        self._active_vote_reason = ""
        self._vote_submitted = False
        self._chat_parse_cache = {}
        self._decision_traced = False

    def _external_chat_signature(self, belief: Belief) -> tuple[tuple[int, str | None, str], ...]:
        """A hashable snapshot of all *other players'* chat messages, used to detect new chat
        since our last LLM call (drives the ``new_chat`` trigger)."""
        self_color = belief.voting.self_marker_color
        return tuple(
            (event.tick, event.speaker_color, event.text)
            for event in belief.chat_log
            if self._is_external_chat(event, self_color)
        )

    def _is_external_chat(self, event: ChatEvent, self_color: str | None) -> bool:
        """Whether ``event`` came from another player (not our marker color, and not a line we
        ourselves sent — text echoed back is filtered against ``_sent_chat_texts``)."""
        if event.speaker_color is not None and event.speaker_color == self_color:
            return False
        return event.text not in self._sent_chat_texts

    def _chat_cooldown_ready(self, belief: Belief) -> bool:
        """Whether enough ticks have passed since our last chat to send another (or we haven't
        chatted yet)."""
        return self._last_chat_tick is None or belief.last_tick - self._last_chat_tick >= CHAT_COOLDOWN_TICKS

    def _remaining_ticks(self, belief: Belief) -> int:
        """Ticks left on the vote timer (``VOTE_TIMER_TICKS`` minus elapsed since the meeting
        opened), clamped to ``[0, VOTE_TIMER_TICKS]``."""
        return max(0, VOTE_TIMER_TICKS - max(0, belief.last_tick - belief.phase_start_tick))

    def _should_auto_submit(self, belief: Belief) -> bool:
        """True once we're within the auto-submit window and haven't yet voted — the deadline
        safety net that guarantees a vote lands."""
        return not self._vote_submitted and self._remaining_ticks(belief) <= AUTO_SUBMIT_REMAINING_TICKS

    def _can_start_llm_call(self, belief: Belief) -> bool:
        """Whether there is still time to start an LLM call that can finish (timeout + margin)
        before the auto-submit deadline."""
        return self._remaining_ticks(belief) > self._latest_safe_llm_start_remaining_ticks()

    def _deadline_prompt_remaining_ticks(self) -> int:
        """Remaining-ticks threshold for the final ``deadline`` LLM prompt — the larger of the
        configured ``DEADLINE_LLM_REMAINING_TICKS`` and just above the latest safe start, so
        the deadline call is never scheduled too late to finish."""
        return max(DEADLINE_LLM_REMAINING_TICKS, self._latest_safe_llm_start_remaining_ticks() + 1)

    def _latest_safe_llm_start_remaining_ticks(self) -> int:
        """The remaining-ticks floor below which starting an LLM call is unsafe: the
        auto-submit reserve plus the call's timeout (converted to ticks) plus a safety margin."""
        timeout_ticks = math.ceil(self._llm_timeout_seconds() * MEETING_TICKS_PER_SECOND)
        return AUTO_SUBMIT_REMAINING_TICKS + timeout_ticks + LLM_TIMEOUT_MARGIN_TICKS

    def _llm_timeout_seconds(self) -> float:
        """The LLM client's timeout in seconds (>= 0), or ``DEFAULT_LLM_TIMEOUT_SECONDS`` when
        the client doesn't expose one or it isn't numeric."""
        value = getattr(self._llm_client, "timeout_seconds", DEFAULT_LLM_TIMEOUT_SECONDS)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return DEFAULT_LLM_TIMEOUT_SECONDS

    def _resolved_vote_target(self, belief: Belief) -> str:
        """The color to actually vote: the tentative vote if it is still a valid target (or an
        explicit skip), otherwise the deterministic fallback."""
        tentative = self._tentative_vote
        if tentative is not None and (tentative == VOTE_SKIP or tentative in valid_vote_targets(belief)):
            return tentative
        return self._fallback_vote_target(belief)

    def _fallback_vote_target(self, belief: Belief) -> str:
        """Last-resort vote target: the current top suspect, or ``VOTE_SKIP`` if the field is
        flat."""
        return top_suspect(belief) or VOTE_SKIP
