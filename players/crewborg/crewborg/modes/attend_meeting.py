"""Attend Meeting mode: conversational chat plus deadline-safe voting."""

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
from crewborg.strategy.meeting.imposter import (
    bandwagon_target,
    parity_closing_vote_target,
    votes_against,
)
from crewborg.strategy.meeting import chat_nlp, chat_read
from crewborg.strategy.suspicion import chat_suspect, top_suspect
from crewborg.types import ActionState, Belief, ChatEvent, Intent
from players.player_sdk import EmptyModeParams, Mode

LLM_MIN_CALL_INTERVAL_TICKS = 12
DEADLINE_LLM_REMAINING_TICKS = 96
AUTO_SUBMIT_REMAINING_TICKS = 48
MEETING_TICKS_PER_SECOND = VOTE_TIMER_TICKS // 10
LLM_TIMEOUT_MARGIN_TICKS = LLM_MIN_CALL_INTERVAL_TICKS
DEFAULT_LLM_TIMEOUT_SECONDS = 3.0


class AttendMeetingMode(Mode[Belief, ActionState, Intent]):
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
        return belief.phase == "Voting"

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
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
        """Accuse + vote a clear leading suspect; else SHARE a read on a softer suspect
        (chat only, no vote) rather than going silent — vote restraint is unchanged."""

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
                # No clear suspect to VOTE — but voice an evidence-cited read instead of
                # going silent (chat only; we still skip the vote on a thin field).
                soft = chat_suspect(belief)
                read = build_accusation(belief, soft) if soft is not None else None
                if read is not None:
                    self._trace_meeting_decision(belief, role="crewmate", path="share_read", target=soft)
                    return self._send_chat_intent(belief, read, reason="sharing read (no vote)")
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

        # 3. Parity-closing push — one removal from a win and no crewmate is taking
        #    heat on their own, so MANUFACTURE the pile instead of skipping it away
        #    (the dominant imposter loss is stalling at 3-crew/2-imp; design §10.4).
        parity_target = parity_closing_vote_target(belief, accusers)
        if parity_target is not None:
            self._tentative_vote = parity_target
            self._deterministic_chatted = True
            fabricated = fabricate_accusation(belief, parity_target)
            self._trace_meeting_decision(
                belief, role="imposter", path="parity_push", target=parity_target,
                fabricated=fabricated is not None, accusers=accusers,
            )
            if fabricated is not None:
                return self._send_chat_intent(belief, fabricated, reason="imposter parity push: fabricated")
            return self._submit_vote_intent(belief, reason="imposter parity push vote")

        # 4. No one to deflect onto yet — wait, then skip at the deadline.
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
        self._pending_chat_text = None
        self._sent_chat_texts.add(text)
        self._last_chat_tick = belief.last_tick
        self.emit.event("meeting_chat_selected", {"text": text, "reason": reason})
        return Intent(kind="chat", text=text, reason=reason)

    def _submit_vote_intent(self, belief: Belief, *, reason: str) -> Intent:
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
        if vote_target == VOTE_SKIP:
            return Intent(kind="vote", reason=reason)
        return Intent(kind="vote", target_color=vote_target, reason=reason)

    def _decide_after_llm_failure(self, belief: Belief, trigger: str) -> Intent:
        if trigger == "deadline":
            return self._submit_vote_intent(belief, reason=f"LLM fallback after {trigger}")
        if trigger == "meeting_start":
            return self._decide_deterministic(belief, trace_disabled=False)
        return Intent(kind="idle", reason=f"LLM fallback after {trigger}")

    # --- state helpers ----------------------------------------------------

    def _reset_for_meeting_if_needed(self, belief: Belief) -> None:
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
        self_color = belief.voting.self_marker_color
        return tuple(
            (event.tick, event.speaker_color, event.text)
            for event in belief.chat_log
            if self._is_external_chat(event, self_color)
        )

    def _is_external_chat(self, event: ChatEvent, self_color: str | None) -> bool:
        if event.speaker_color is not None and event.speaker_color == self_color:
            return False
        return event.text not in self._sent_chat_texts

    def _chat_cooldown_ready(self, belief: Belief) -> bool:
        return self._last_chat_tick is None or belief.last_tick - self._last_chat_tick >= CHAT_COOLDOWN_TICKS

    def _remaining_ticks(self, belief: Belief) -> int:
        return max(0, VOTE_TIMER_TICKS - max(0, belief.last_tick - belief.phase_start_tick))

    def _should_auto_submit(self, belief: Belief) -> bool:
        return not self._vote_submitted and self._remaining_ticks(belief) <= AUTO_SUBMIT_REMAINING_TICKS

    def _can_start_llm_call(self, belief: Belief) -> bool:
        return self._remaining_ticks(belief) > self._latest_safe_llm_start_remaining_ticks()

    def _deadline_prompt_remaining_ticks(self) -> int:
        return max(DEADLINE_LLM_REMAINING_TICKS, self._latest_safe_llm_start_remaining_ticks() + 1)

    def _latest_safe_llm_start_remaining_ticks(self) -> int:
        timeout_ticks = math.ceil(self._llm_timeout_seconds() * MEETING_TICKS_PER_SECOND)
        return AUTO_SUBMIT_REMAINING_TICKS + timeout_ticks + LLM_TIMEOUT_MARGIN_TICKS

    def _llm_timeout_seconds(self) -> float:
        value = getattr(self._llm_client, "timeout_seconds", DEFAULT_LLM_TIMEOUT_SECONDS)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return DEFAULT_LLM_TIMEOUT_SECONDS

    def _resolved_vote_target(self, belief: Belief) -> str:
        tentative = self._tentative_vote
        if tentative is not None and (tentative == VOTE_SKIP or tentative in valid_vote_targets(belief)):
            return tentative
        return self._fallback_vote_target(belief)

    def _fallback_vote_target(self, belief: Belief) -> str:
        return top_suspect(belief) or VOTE_SKIP
