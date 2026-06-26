"""Attend Meeting mode: conversational chat plus deadline-safe voting."""

from __future__ import annotations

from typing import Any

from players.crewrift.crewborg.strategy.meeting import (
    CHAT_MAX_CHARS,
    VOTE_SKIP,
    MeetingDecision,
    MeetingDecisionValidationError,
    MeetingLLMClient,
    MeetingParams,
    build_meeting_client,
    serialize_meeting_context,
    valid_vote_targets,
    validate_meeting_decision,
)
from players.crewrift.crewborg.strategy.meeting.context import (
    CHAT_COOLDOWN_TICKS,
    effective_vote_timer_ticks,
)
from players.crewrift.crewborg.strategy.meeting.vote_policy import (
    anti_split_swap,
    deadline_posterior_gate,
    fallback_vote,
    should_announce,
    skip_pileon_swap,
)
from players.crewrift.crewborg.types import ActionState, Belief, ChatEvent, Intent
from players.player_sdk import Mode

# Deterministic fallback: preserve the pre-LLM behavior unless explicitly enabled.
MEETING_CHAT = "no read, skipping"

LLM_MIN_CALL_INTERVAL_TICKS = 12
DEADLINE_LLM_REMAINING_TICKS = 96
# The server's vote timer starts when the meeting OPENS — at the meeting-call
# interstitial, ~72 ticks before our Voting phase begins — so the window we can
# actually vote in is the configured timer minus this head start (measured on
# crewrift 0.1.51: Voting->VoteResult 1129-1142 ticks at voteTimerTicks=1200).
# Without this correction the deadline auto-submit fired ~12 ticks before the
# meeting closed and 29% of votes timed out in the 2026-06-11 v2 eval.
VOTE_TIMER_HEADSTART_TICKS = 96
# Submit by this many (corrected) ticks remaining no matter what state we are
# in: margin for the worst-case cursor walk (~20 edge-presses = ~40 ticks).
AUTO_SUBMIT_REMAINING_TICKS = 120
# With no read of our own, wait this long into the meeting before submitting:
# long enough for confident accusers' chats + vote dots to land (they submit at
# meeting start; a cursor walk is ~40 ticks) so the skip pile-on has a live
# tally to read — without dragging every meeting out to the full vote timer.
DETERMINISTIC_TALLY_WAIT_TICKS = 300

# When the meeting client reports ``enabled`` but its calls keep failing (an
# ungated/404 model, a bad API key, a network outage), we must not let that cost
# us our vote: the LLM path otherwise bypasses the deterministic chat->vote
# fallback entirely. After a permanent error (auth/forbidden/not-found) or this
# many failures in an episode, we latch onto the deterministic fallback for the
# rest of the episode so voting stays reliable. See ``_note_llm_failure``.
LLM_FAILURE_DISABLE_THRESHOLD = 2
PERMANENT_LLM_STATUS_CODES = frozenset({401, 403, 404})


class AttendMeetingMode(Mode[Belief, ActionState, Intent]):
    name = "attend_meeting"
    params_type = MeetingParams

    def __init__(self, params: MeetingParams | None = None, *, llm_client: MeetingLLMClient | None = None) -> None:
        super().__init__(params or MeetingParams())
        self._llm_client = llm_client if llm_client is not None else build_meeting_client(self.params)
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
        self._vote_submitted = False
        # The vote actually submitted: latched on the first submit so (a) the
        # vote intent stays stable across the multi-tick cursor walk and (b)
        # ``meeting_vote_selected`` fires exactly once per meeting (it used to
        # re-emit every Voting tick).
        self._submitted_vote_target: str | None = None
        self._submitted_vote_reason: str | None = None
        # Episode-level LLM health. A failing-but-"enabled" client must not
        # silently cost us our vote, so these latch the mode onto the
        # deterministic fallback and intentionally persist across meetings —
        # they are NOT cleared in ``_reset_for_meeting_if_needed``.
        self._llm_failure_count = 0
        self._llm_disabled_for_episode = False

    def is_legal(self, belief: Belief) -> bool:
        return belief.phase == "Voting"

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        self._reset_for_meeting_if_needed(belief)
        if action_state.vote_confirmed:
            self._vote_submitted = True
            return Intent(kind="idle", reason="vote already confirmed")

        # A submitted vote must keep returning the same vote intent until the
        # action layer confirms it: an intent change mid-cursor-walk resets the
        # walk, and an idle here would drop the vote entirely (the -10 penalty).
        if self._vote_submitted:
            return self._submit_vote_intent(belief, reason="continuing submitted vote")

        # The deadline backstop applies to every path — LLM, deterministic, or a
        # failing-but-enabled client — so a pending chat or an exception can never
        # delay the vote past the timer (the no-vote penalty is -10).
        if self._should_auto_submit(belief):
            return self._submit_vote_intent(
                belief, reason="meeting deadline: auto-submit tentative vote", deadline=True
            )

        if not self._llm_client.enabled:
            return self._decide_deterministic(belief, trace_disabled=True)

        if self._llm_disabled_for_episode:
            # The client claims to be enabled but its calls are failing; fall
            # back to the deterministic chat->vote path so we still vote.
            return self._decide_deterministic(belief, trace_disabled=False)

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
        if trace_disabled and not self._disabled_traced:
            self._disabled_traced = True
            self.emit.event(
                "meeting_llm_fallback",
                {"reason": "llm_disabled", "detail": self._llm_client.disabled_reason},
        )
        vote = fallback_vote(belief)
        announce = should_announce(belief, vote)
        if not self._deterministic_chatted:
            self._deterministic_chatted = True
            return self._send_chat_intent(
                belief,
                self._deterministic_chat_text(belief, vote, announce=announce),
                reason="meeting opener",
            )
        # A confirmed-witness-level read (≥ ANNOUNCE_MIN_PROBABILITY or a
        # witnessed-caught imposter): submit immediately and lead the charge —
        # our early dot + accusation chat is what skipping crew can corroborate
        # and pile onto. Anything below that bar votes SILENTLY after the tally
        # window (2026-06-11 evals: led accusations ran 42% accurate vs truecrew
        # and announcing preceded 2 of our 4 ejections vs the champion field —
        # the lobby turns on the loudest accuser). The post-wait submit
        # re-resolves against the live tally (anti-split + skip pile-on), so a
        # forming credible conviction can still recruit us.
        if announce:
            return self._submit_vote_intent(belief, reason="deterministic meeting vote")
        # The imposter holds its vote to the deadline auto-submit: its whole
        # vote policy is joining the crew plurality, and at the +300 tally wait
        # the plurality has usually not formed yet — across 87 v6-eval meetings
        # the +300 join NEVER fired, forfeiting the free-parity ejections the
        # same join converted 5× in 22 v3 imposter episodes. The deadline
        # submit resolves against the meeting's final tally.
        if belief.self_role == "imposter":
            return Intent(kind="idle", reason="imposter: holding vote for the final tally")
        if self._tally_wait_elapsed(belief):
            if vote != VOTE_SKIP:
                return self._submit_vote_intent(belief, reason="silent vote after tally wait")
            return self._submit_vote_intent(belief, reason="deterministic vote after tally wait")
        return Intent(kind="idle", reason="waiting for the tally before voting")

    def _deterministic_chat_text(self, belief: Belief, vote: str, *, announce: bool) -> str:
        """The opener: announce only a confirmed-witness-level read.

        The accusation text is parsed by every crewborg's social layer
        (``strategy.meeting.social``) into its accusation graph — the
        corroboration the skip pile-on requires. It deliberately carries
        evidence wording ("saw"), honest at this bar (a witnessed kill/vent or
        a ≥0.9 posterior), so credibility-gated peers can follow it. Imposters
        and crewmates below the announce bar keep the neutral opener.
        """

        del belief
        if announce:
            return f"saw {vote}, {vote} sus, vote {vote}"[:CHAT_MAX_CHARS]
        return MEETING_CHAT

    # --- LLM call cadence -------------------------------------------------

    def _next_llm_trigger(self, belief: Belief) -> str | None:
        tick = belief.last_tick
        if self._last_llm_call_tick is not None and tick - self._last_llm_call_tick < LLM_MIN_CALL_INTERVAL_TICKS:
            return None
        if self._last_llm_call_tick is None:
            return "meeting_start"

        signature = self._external_chat_signature(belief)
        if signature != self._last_external_chat_signature:
            return "new_chat"

        if (
            self._last_chat_tick is not None
            and self._chat_cooldown_ready(belief)
            and self._last_cooldown_prompt_chat_tick != self._last_chat_tick
        ):
            return "chat_cooldown_ready"

        if self._remaining_ticks(belief) <= DEADLINE_LLM_REMAINING_TICKS and not self._deadline_prompted:
            return "deadline"
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
            self._note_llm_failure(exc, trigger=trigger)
            self.emit.event(
                "meeting_llm_fallback",
                {"reason": "llm_call_failed", "trigger": trigger, "error": repr(exc)},
            )
            return None
        self.emit.histogram("meeting_llm.latency_ms", result.latency_ms, tags={"model": result.model, "trigger": trigger})
        return result

    def _note_llm_failure(self, exc: Exception, *, trigger: str) -> None:
        """Latch onto the deterministic fallback when the meeting LLM keeps failing.

        A client that is ``enabled`` but whose calls fail (an ungated model
        returning 404, a bad key, a network outage) would otherwise bypass the
        deterministic chat->vote path and cost us our vote every meeting. A
        permanent error (auth/forbidden/not-found) disables the client for the
        rest of the episode immediately; transient errors do so after
        ``LLM_FAILURE_DISABLE_THRESHOLD`` failures.
        """
        if self._llm_disabled_for_episode:
            return
        self._llm_failure_count += 1
        status = getattr(exc, "status_code", None)
        permanent = status in PERMANENT_LLM_STATUS_CODES
        if permanent or self._llm_failure_count >= LLM_FAILURE_DISABLE_THRESHOLD:
            self._llm_disabled_for_episode = True
            self.emit.event(
                "meeting_llm_disabled",
                {
                    "reason": "permanent_error" if permanent else "repeated_failures",
                    "status_code": status,
                    "failures": self._llm_failure_count,
                    "trigger": trigger,
                },
            )

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

    def _submit_vote_intent(self, belief: Belief, *, reason: str, deadline: bool = False) -> Intent:
        # Resolve and latch once per meeting: the same vote intent (same target,
        # same reason) must be returned every tick of the cursor walk (an intent
        # change resets the walk), and ``meeting_vote_selected`` fires exactly
        # once — the same latch pattern as the ``vote_cast`` fix in events.py.
        if self._submitted_vote_target is None:
            self._submitted_vote_target = self._resolved_vote_target(belief, deadline=deadline)
            self._submitted_vote_reason = reason
            self.emit.event("meeting_vote_selected", {"target": self._submitted_vote_target, "reason": reason})
        self._vote_submitted = True
        vote_target = self._submitted_vote_target
        vote_reason = self._submitted_vote_reason or reason
        if vote_target == VOTE_SKIP:
            return Intent(kind="vote", reason=vote_reason)
        return Intent(kind="vote", target_color=vote_target, reason=vote_reason)

    def _decide_after_llm_failure(self, belief: Belief, trigger: str) -> Intent:
        if trigger == "deadline":
            return self._submit_vote_intent(belief, reason=f"LLM fallback after {trigger}", deadline=True)
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
        self._vote_submitted = False
        self._submitted_vote_target = None
        self._submitted_vote_reason = None

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
        # The meeting length is learned from the GAME INFO interstitial when
        # available (currently 1200 ticks by default), with a conservative
        # 240-tick fallback so an unknown timer can never miss the vote deadline.
        # The server's timer started at the meeting-call interstitial, before our
        # Voting phase clock — subtract that head start (VOTE_TIMER_HEADSTART_TICKS).
        timer = effective_vote_timer_ticks(belief) - VOTE_TIMER_HEADSTART_TICKS
        return max(0, timer - max(0, belief.last_tick - belief.phase_start_tick))

    def _should_auto_submit(self, belief: Belief) -> bool:
        return not self._vote_submitted and self._remaining_ticks(belief) <= AUTO_SUBMIT_REMAINING_TICKS

    def _tally_wait_elapsed(self, belief: Belief) -> bool:
        return belief.last_tick - belief.phase_start_tick >= DETERMINISTIC_TALLY_WAIT_TICKS

    def _resolved_vote_target(self, belief: Belief, *, deadline: bool = False) -> str:
        tentative = self._tentative_vote
        if tentative is not None and (tentative == VOTE_SKIP or tentative in valid_vote_targets(belief)):
            target = tentative
        else:
            target = self._fallback_vote_target(belief)
        # Near the deadline a trailing vote is wasted (ties eject no one): join
        # the plurality when it lands on a plausible target (design §10.2). A
        # deterministic submit after the tally wait is final — treat it as the
        # deadline so the swaps apply (the live tally is as formed as it gets).
        remaining = 0 if self._tally_wait_elapsed(belief) else self._remaining_ticks(belief)
        swapped = anti_split_swap(belief, target, remaining)
        if swapped != target:
            self.emit.event("meeting_anti_split_swap", {"from": target, "to": swapped})
        # The deadline auto-submit is a backstop, not a read: a crewmate's
        # forced vote below the posterior bar becomes a skip (wrong ejections
        # help the imposters; design §10.2). Must-eject, confirmed imposters,
        # and the imposter plurality-join pass through unchanged. Applied
        # BEFORE the skip pile-on so a corroborated accusation (which has its
        # own credibility machinery) can still recruit the gated skip — the
        # lone-witness conviction channel survives.
        if deadline:
            gated = deadline_posterior_gate(belief, swapped)
            if gated != swapped:
                self.emit.event(
                    "meeting_deadline_posterior_gate",
                    {"from": swapped, "to": gated, "posterior": belief.suspicion.get(swapped, 0.0)},
                )
            swapped = gated
        # A skip near the deadline joins a corroborated accusation (a voter who
        # also chat-accused the target): the skip pile-on that lets a lone
        # correct witness actually convict (design §10.2).
        piled = skip_pileon_swap(belief, swapped, remaining)
        if piled != swapped:
            self.emit.event("meeting_skip_pileon", {"from": swapped, "to": piled})
        return piled

    def _fallback_vote_target(self, belief: Belief) -> str:
        return fallback_vote(belief)
