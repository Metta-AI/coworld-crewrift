"""Attend Meeting / Report Body / Flee mode tests (design §7.1)."""

from __future__ import annotations

from players.crewrift.crewborg.modes import AttendMeetingMode, FleeMode, ReportBodyMode
from players.crewrift.crewborg.modes.attend_meeting import (
    DETERMINISTIC_TALLY_WAIT_TICKS,
    MEETING_CHAT,
)
from players.crewrift.crewborg.perception.entities import VoteCandidate, VoteDot, VotingState
from players.crewrift.crewborg.strategy.meeting import (
    MeetingDecision,
    MeetingLLMResult,
    MeetingParams,
    read_meeting_params_from_env,
)
from players.crewrift.crewborg.strategy.meeting.llm import (
    DEFAULT_BEDROCK_MODEL,
    DEFAULT_MEETING_MODEL,
    DEFAULT_OPENROUTER_MODEL,
    MeetingLLMConfig,
    OpenRouterMeetingClient,
    build_meeting_client,
)
from players.crewrift.crewborg.types import Accusation, ActionState, Belief, BodyEntry, ChatEvent, PlayerRecord
from players.player_sdk import EventEmitter, ListTraceSink


class _FakeMeetingClient:
    enabled = True
    disabled_reason = None

    def __init__(self, decisions: list[MeetingDecision]) -> None:
        self.decisions = list(decisions)
        self.calls: list[tuple[str, dict]] = []

    def decide(self, context: dict, *, trigger: str) -> MeetingLLMResult:
        self.calls.append((trigger, context))
        return MeetingLLMResult(
            decision=self.decisions.pop(0),
            model="fake-haiku",
            latency_ms=1.5,
        )


class _FailingMeetingClient:
    """An ``enabled`` client whose every call raises, like an ungated/404 model."""

    enabled = True
    disabled_reason = None

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def decide(self, context: dict, *, trigger: str) -> MeetingLLMResult:
        self.calls += 1
        raise self.exc


class _NotFoundError(Exception):
    status_code = 404


def _meeting_belief(*, tick: int = 0, start_tick: int = 0) -> Belief:
    belief = Belief(phase="Voting", phase_start_tick=start_tick, last_tick=tick, total_player_count=2)
    belief.voting = VotingState(
        timer_present=True,
        self_marker_color="blue",
        candidates=(
            VoteCandidate(slot=0, color="red", alive=True),
            VoteCandidate(slot=1, color="blue", alive=True),
        ),
        cursor_slot=0,
    )
    belief.roster["red"] = PlayerRecord(color="red", life_status="alive", last_seen_tick=1)
    belief.roster["blue"] = PlayerRecord(color="blue", life_status="alive", last_seen_tick=1)
    belief.suspicion = {"red": 0.95}
    return belief


def test_attend_meeting_chats_once_then_waits_then_votes_at_deadline() -> None:
    # With no read, the deterministic path chats, then *waits* for the live
    # tally (skip pile-on) instead of locking in an early skip; the deadline
    # auto-submit still guarantees the vote.
    mode = AttendMeetingMode()
    first = mode.decide(Belief(phase="Voting"), ActionState())
    assert first.kind == "chat" and first.text

    second = mode.decide(Belief(phase="Voting"), ActionState())
    assert second.kind == "idle"

    at_deadline = mode.decide(Belief(phase="Voting", last_tick=193), ActionState())
    assert at_deadline.kind == "vote"
    assert mode.decide(Belief(phase="Voting", last_tick=194), ActionState()).kind == "vote"


def test_attend_meeting_votes_the_top_suspect_when_confident() -> None:
    mode = AttendMeetingMode()
    belief = Belief(phase="Voting")
    belief.suspicion = {"red": 0.95, "blue": 0.2}  # red over the vote bar
    mode.decide(belief, ActionState())  # chat opener
    vote = mode.decide(belief, ActionState())
    assert vote.kind == "vote" and vote.target_color == "red"


def test_attend_meeting_skips_when_no_one_is_suspicious_enough() -> None:
    mode = AttendMeetingMode()
    belief = Belief(phase="Voting")
    belief.suspicion = {"red": 0.4, "blue": 0.2}  # nobody over the vote bar
    mode.decide(belief, ActionState())  # chat opener
    assert mode.decide(belief, ActionState()).kind == "idle"  # wait, don't lock a skip early
    belief.last_tick = 193  # deadline auto-submit with no corroborated tally -> skip
    vote = mode.decide(belief, ActionState())
    assert vote.kind == "vote" and vote.target_color is None


def test_attend_meeting_llm_sends_multiple_chats_after_new_chat_and_cooldown() -> None:
    client = _FakeMeetingClient(
        [
            MeetingDecision(action="send_chat", chat_text="red, where were you?", vote_target="red"),
            MeetingDecision(action="send_chat", chat_text="that route does not clear red"),
        ]
    )
    mode = AttendMeetingMode(llm_client=client)

    opener_belief = _meeting_belief(tick=0)
    opener_belief.vote_timer_ticks = 1200  # a long learned timer keeps the deadline far
    first = mode.decide(opener_belief, ActionState())
    assert first.kind == "chat"
    assert first.text == "red, where were you?"

    belief = _meeting_belief(tick=101)
    belief.vote_timer_ticks = 1200
    belief.chat_log = [ChatEvent(tick=20, speaker_color="red", text="i was nav")]
    second = mode.decide(belief, ActionState())
    assert second.kind == "chat"
    assert second.text == "that route does not clear red"
    assert [trigger for trigger, _ in client.calls] == ["meeting_start", "new_chat"]


def test_attend_meeting_llm_tentative_vote_auto_submits_near_deadline() -> None:
    client = _FakeMeetingClient([MeetingDecision(action="set_tentative_vote", vote_target="red")])
    mode = AttendMeetingMode(llm_client=client)

    assert mode.decide(_meeting_belief(tick=0), ActionState()).kind == "idle"

    vote = mode.decide(_meeting_belief(tick=193), ActionState())
    assert vote.kind == "vote"
    assert vote.target_color == "red"


def test_attend_meeting_llm_can_submit_vote_early() -> None:
    client = _FakeMeetingClient([MeetingDecision(action="submit_vote", vote_target="red")])
    mode = AttendMeetingMode(llm_client=client)

    vote = mode.decide(_meeting_belief(tick=0), ActionState())
    assert vote.kind == "vote"
    assert vote.target_color == "red"


def test_attend_meeting_invalid_llm_decision_falls_back_to_canned_chat() -> None:
    client = _FakeMeetingClient([MeetingDecision(action="send_chat", chat_text="vote green", vote_target="green")])
    mode = AttendMeetingMode(llm_client=client)

    intent = mode.decide(_meeting_belief(tick=0), ActionState())
    assert intent.kind == "chat"
    # The deterministic opener announces the confirmed-witness-level read (red at
    # 0.95 ≥ ANNOUNCE_MIN_PROBABILITY) with evidence wording so credibility-gated
    # crew can corroborate the accusation.
    assert intent.text == "saw red, red sus, vote red"


def test_attend_meeting_votes_when_enabled_llm_permanently_fails() -> None:
    # A 404/ungated model that reports enabled must not cost us our vote: the
    # mode latches onto the deterministic chat->vote fallback after the first
    # permanent error rather than idling out the meeting without voting.
    client = _FailingMeetingClient(_NotFoundError("model use case not submitted"))
    mode = AttendMeetingMode(llm_client=client)

    first = mode.decide(_meeting_belief(tick=0), ActionState())
    assert first.kind == "chat"  # meeting_start failed -> deterministic opener
    assert mode._llm_disabled_for_episode is True

    second = mode.decide(_meeting_belief(tick=1), ActionState())
    assert second.kind == "vote"
    assert second.target_color == "red"  # the top suspect


def test_attend_meeting_keeps_voting_in_later_meetings_after_llm_failure() -> None:
    client = _FailingMeetingClient(_NotFoundError("ungated"))
    mode = AttendMeetingMode(llm_client=client)

    mode.decide(_meeting_belief(tick=0), ActionState())  # meeting 1 opener (+ latch)
    mode.decide(_meeting_belief(tick=1), ActionState())  # meeting 1 vote

    # A new meeting (new phase_start_tick) stays on the deterministic fallback
    # without ever calling the broken client again.
    calls_after_meeting_one = client.calls
    opener = mode.decide(_meeting_belief(tick=300, start_tick=300), ActionState())
    assert opener.kind == "chat"
    vote = mode.decide(_meeting_belief(tick=301, start_tick=300), ActionState())
    assert vote.kind == "vote" and vote.target_color == "red"
    assert client.calls == calls_after_meeting_one  # no further LLM calls


def test_attend_meeting_votes_after_repeated_transient_llm_failures() -> None:
    # A transient error (no status_code) latches only after the failure
    # threshold, but still ends in a vote rather than an unvoted meeting.
    client = _FailingMeetingClient(RuntimeError("timeout"))
    mode = AttendMeetingMode(llm_client=client)

    first = mode.decide(_meeting_belief(tick=0), ActionState())  # failure #1 -> chat
    assert first.kind == "chat"
    assert mode._llm_disabled_for_episode is False

    belief = _meeting_belief(tick=13)
    belief.chat_log = [ChatEvent(tick=5, speaker_color="red", text="i was nav")]
    mode.decide(belief, ActionState())  # new_chat trigger -> failure #2 -> latched
    assert mode._llm_disabled_for_episode is True

    vote = mode.decide(_meeting_belief(tick=14), ActionState())
    assert vote.kind == "vote" and vote.target_color == "red"


def test_attend_meeting_auto_submits_when_llm_always_waits() -> None:
    # An LLM that never commits (always "wait") must still end in a vote: the
    # deadline backstop submits regardless of what the model keeps choosing.
    client = _FakeMeetingClient([MeetingDecision(action="wait") for _ in range(32)])
    mode = AttendMeetingMode(llm_client=client)

    # With the 240-tick fallback timer and the 96-tick server head start, the
    # corrected window is 144 ticks; the backstop fires at remaining <= 120,
    # i.e. from tick 24 on.
    for tick in range(0, 24, 12):
        belief = _meeting_belief(tick=tick)
        assert mode.decide(belief, ActionState()).kind == "idle"

    vote = mode.decide(_meeting_belief(tick=25), ActionState())  # remaining 119 <= 120
    assert vote.kind == "vote"
    assert vote.target_color == "red"  # the top suspect


def test_attend_meeting_deadline_gates_a_low_posterior_vote_to_skip() -> None:
    # The deadline auto-submit is a backstop, not a read (v8 0.1.52 eval:
    # deadline votes ran on weaker reads): a crewmate's forced vote below the
    # posterior bar becomes a skip instead of risking a wrong ejection.
    client = _FakeMeetingClient([MeetingDecision(action="set_tentative_vote", vote_target="red")])
    mode = AttendMeetingMode(llm_client=client)

    weak = _meeting_belief(tick=0)
    weak.suspicion = {"red": 0.3}
    assert mode.decide(weak, ActionState()).kind == "idle"

    at_deadline = _meeting_belief(tick=193)
    at_deadline.suspicion = {"red": 0.3}
    vote = mode.decide(at_deadline, ActionState())
    assert vote.kind == "vote"
    assert vote.target_color is None  # gated to skip


def test_attend_meeting_imposter_deadline_join_is_not_posterior_gated() -> None:
    # The imposter's deadline plurality-join (the free-parity ejection channel)
    # is deliberately posterior-free.
    mode = AttendMeetingMode()
    belief = _meeting_belief(tick=193)
    belief.self_role = "imposter"
    belief.suspicion = {}
    belief.voting = VotingState(
        timer_present=True,
        self_marker_color="blue",
        candidates=(
            VoteCandidate(slot=0, color="red", alive=True),
            VoteCandidate(slot=1, color="blue", alive=True),
        ),
        dots=(VoteDot(voter=1, target=0),),
        cursor_slot=0,
    )
    vote = mode.decide(belief, ActionState())
    assert vote.kind == "vote"
    assert vote.target_color == "red"  # joins the plurality despite no read


def test_attend_meeting_auto_submits_with_chat_pending_at_deadline() -> None:
    # A chat stuck behind the cooldown must not delay the vote past the deadline.
    client = _FakeMeetingClient(
        [
            MeetingDecision(action="send_chat", chat_text="opener", vote_target="red"),
            MeetingDecision(action="send_chat", chat_text="second message too soon"),
        ]
    )
    mode = AttendMeetingMode(llm_client=client)

    assert mode.decide(_meeting_belief(tick=0), ActionState()).kind == "chat"

    belief = _meeting_belief(tick=20)
    belief.chat_log = [ChatEvent(tick=10, speaker_color="red", text="hello")]
    pending = mode.decide(belief, ActionState())  # cooldown not ready: chat parks
    assert pending.kind == "idle"
    assert mode._pending_chat_text == "second message too soon"

    vote = mode.decide(_meeting_belief(tick=170), ActionState())  # past the deadline
    assert vote.kind == "vote"
    assert vote.target_color == "red"


def test_attend_meeting_auto_submits_on_failing_llm_at_deadline() -> None:
    # Even before the failure latch trips, the deadline backstop runs ahead of any
    # LLM call, so a raising client cannot cost us the vote.
    client = _FailingMeetingClient(RuntimeError("timeout"))
    mode = AttendMeetingMode(llm_client=client)

    vote = mode.decide(_meeting_belief(tick=170), ActionState())
    assert vote.kind == "vote"
    assert vote.target_color == "red"
    assert client.calls == 0  # the backstop never reached the LLM


def test_attend_meeting_deterministic_path_auto_submits_at_deadline() -> None:
    # The deterministic (LLM-disabled) path also gets the backstop: joining a
    # meeting late skips the canned opener and votes immediately.
    mode = AttendMeetingMode(MeetingParams(use_llm=False))
    vote = mode.decide(_meeting_belief(tick=170), ActionState())
    assert vote.kind == "vote"
    assert vote.target_color == "red"


def test_attend_meeting_votes_with_empty_candidate_grid() -> None:
    # An undecoded candidate grid must still produce a vote intent (the action
    # resolver falls back to skip / last-resort confirm from there).
    mode = AttendMeetingMode(MeetingParams(use_llm=False))
    belief = Belief(phase="Voting", last_tick=170, phase_start_tick=0)
    vote = mode.decide(belief, ActionState())
    assert vote.kind == "vote"


def test_attend_meeting_deadline_accounts_for_the_meeting_call_head_start() -> None:
    # The server's vote timer starts at the meeting-call interstitial (~72 ticks
    # before Voting): with the learned 1200-tick timer the auto-submit must fire
    # by 1200 - 96 (head start) - 120 (walk margin) = 984 ticks into Voting —
    # NOT at 1128, which left ~12 ticks and timed the vote out.
    mode = AttendMeetingMode(MeetingParams(use_llm=False))
    belief = _meeting_belief(tick=0)
    belief.vote_timer_ticks = 1200
    belief.suspicion = {}  # no read: the deterministic path waits

    mode.decide(belief, ActionState())  # opener chat
    belief.last_tick = 299
    assert mode.decide(belief, ActionState()).kind == "idle"

    # The tally wait (300 ticks) elapses well before the corrected deadline.
    belief.last_tick = 300
    assert mode.decide(belief, ActionState()).kind == "vote"


def test_attend_meeting_no_read_submits_after_the_tally_wait_not_the_full_timer() -> None:
    # Long learned timer: an uninformed crewmate must not drag the meeting out
    # to the deadline — it submits once the tally window has passed.
    mode = AttendMeetingMode(MeetingParams(use_llm=False))
    belief = _meeting_belief(tick=0)
    belief.vote_timer_ticks = 1200
    belief.suspicion = {}

    mode.decide(belief, ActionState())  # opener
    belief.last_tick = 150
    assert mode.decide(belief, ActionState()).kind == "idle"
    belief.last_tick = 301
    vote = mode.decide(belief, ActionState())
    assert vote.kind == "vote" and vote.target_color is None  # skip: no corroborated tally


def test_attend_meeting_emits_meeting_vote_selected_once_per_meeting() -> None:
    # The vote intent must persist across the multi-tick cursor walk, but the
    # trace event fires exactly once (it used to re-emit every Voting tick).
    sink = ListTraceSink()
    mode = AttendMeetingMode(MeetingParams(use_llm=False))
    mode.emit = EventEmitter(trace_sink=sink)
    belief = _meeting_belief(tick=0)

    assert mode.decide(belief, ActionState()).kind == "chat"
    for tick in range(1, 6):
        belief.last_tick = tick
        intent = mode.decide(belief, ActionState())
        assert intent.kind == "vote" and intent.target_color == "red"
    assert sink.names().count("domain.meeting_vote_selected") == 1

    # A fresh meeting re-arms the latch.
    fresh = _meeting_belief(tick=100, start_tick=100)
    mode.decide(fresh, ActionState())  # opener chat
    fresh.last_tick = 101
    assert mode.decide(fresh, ActionState()).kind == "vote"
    assert sink.names().count("domain.meeting_vote_selected") == 2


def test_attend_meeting_keeps_the_submitted_vote_stable_until_confirmed() -> None:
    # Once submitted, the same vote intent (same target, same reason) returns
    # every tick — an intent change would reset the action layer's cursor walk.
    mode = AttendMeetingMode(MeetingParams(use_llm=False))
    belief = _meeting_belief(tick=0)
    mode.decide(belief, ActionState())  # opener chat
    first = mode.decide(belief, ActionState())
    second = mode.decide(belief, ActionState())
    assert first.kind == second.kind == "vote"
    assert first == second

    confirmed = ActionState(vote_confirmed=True)
    assert mode.decide(belief, confirmed).kind == "idle"


def test_attend_meeting_votes_a_sub_announce_read_silently_after_the_tally_wait() -> None:
    # Over the vote bar but under the announce bar: neutral opener (no
    # accusation chat — announce-then-die is real), wait the tally window, then
    # vote the read silently.
    mode = AttendMeetingMode(MeetingParams(use_llm=False))
    belief = _meeting_belief(tick=0)
    belief.total_player_count = 8
    belief.imposter_count = 2
    for color in ("green", "yellow", "pink", "lime", "orange", "white"):
        belief.roster[color] = PlayerRecord(color=color, life_status="alive", last_seen_tick=1)
    belief.vote_timer_ticks = 1200  # keep the deadline auto-submit far away
    belief.suspicion = {"red": 0.8}  # ≥ comfortable vote bar (0.75), < announce (0.9)

    opener = mode.decide(belief, ActionState())
    assert opener.kind == "chat"
    assert opener.text == MEETING_CHAT  # neutral: the read is not announced

    belief.last_tick = 1
    assert mode.decide(belief, ActionState()).kind == "idle"  # waiting on the tally

    belief.last_tick = DETERMINISTIC_TALLY_WAIT_TICKS
    vote = mode.decide(belief, ActionState())
    assert vote.kind == "vote" and vote.target_color == "red"
    assert vote.reason == "silent vote after tally wait"


def test_attend_meeting_skip_piles_onto_a_corroborated_accusation_at_deadline() -> None:
    # We (blue) have no read; red (slot 0) was voted AND chat-accused by green
    # (slot 2) this meeting. The deadline auto-submit joins the conviction
    # instead of skipping.
    mode = AttendMeetingMode(MeetingParams(use_llm=False))
    belief = _meeting_belief(tick=0)
    belief.voting = belief.voting.model_copy(
        update={
            "candidates": (
                VoteCandidate(slot=0, color="red", alive=True),
                VoteCandidate(slot=1, color="blue", alive=True),
                VoteCandidate(slot=2, color="green", alive=True),
            ),
            "dots": (VoteDot(voter=2, target=0),),
        }
    )
    belief.roster["green"] = PlayerRecord(color="green", life_status="alive", last_seen_tick=1)
    belief.suspicion = {"red": 0.28, "green": 0.28}  # nobody over the vote bar
    belief.accusations.append(
        Accusation(
            meeting_id=belief.phase_start_tick,
            tick=0,
            speaker_color="green",
            target_color="red",
            stance="accuse",
            text="body in storage, red sus, vote red",
            has_evidence=True,  # credibility-gated: bare assertions never recruit us
        )
    )

    assert mode.decide(belief, ActionState()).kind == "chat"
    assert mode.decide(belief, ActionState()).kind == "idle"  # waiting on the tally

    belief.last_tick = 193  # deadline auto-submit
    vote = mode.decide(belief, ActionState())
    assert vote.kind == "vote" and vote.target_color == "red"


def test_attend_meeting_imposter_waits_then_joins_the_crew_plurality() -> None:
    # The imposter's blend-in vote: neutral opener, wait, then join the crew
    # plurality on a non-teammate at the deadline (never its own early accusation).
    mode = AttendMeetingMode(MeetingParams(use_llm=False))
    belief = _meeting_belief(tick=0)
    belief.self_role = "imposter"
    belief.voting = belief.voting.model_copy(
        update={
            "candidates": (
                VoteCandidate(slot=0, color="red", alive=True),
                VoteCandidate(slot=1, color="blue", alive=True),
                VoteCandidate(slot=2, color="green", alive=True),
            ),
        }
    )
    belief.roster["green"] = PlayerRecord(color="green", life_status="alive", last_seen_tick=1)

    belief.vote_timer_ticks = 1200  # a learned long timer keeps the deadline far

    opener = mode.decide(belief, ActionState())
    assert opener.kind == "chat" and opener.text == "no read, skipping"
    assert mode.decide(belief, ActionState()).kind == "idle"

    # The imposter holds past the +300 tally wait: across 87 hosted v6-eval
    # meetings the +300 join never saw a formed plurality (pluralities form
    # late), so the join resolves only at the deadline auto-submit.
    belief.last_tick = DETERMINISTIC_TALLY_WAIT_TICKS + 1
    assert mode.decide(belief, ActionState()).kind == "idle"

    # Deadline (1200 - 96 headstart - 120 margin): the formed plurality is joined.
    belief.last_tick = 1000
    belief.voting = belief.voting.model_copy(
        update={"dots": (VoteDot(voter=2, target=0), VoteDot(voter=0, target=0))}
    )
    vote = mode.decide(belief, ActionState())
    assert vote.kind == "vote" and vote.target_color == "red"


def test_read_meeting_params_from_env_enables_llm_only_with_key() -> None:
    enabled = read_meeting_params_from_env({"CREWBORG_LLM_MEETINGS": "1", "ANTHROPIC_API_KEY": "secret"})
    assert enabled.use_llm is True

    missing_key = read_meeting_params_from_env({"CREWBORG_LLM_MEETINGS": "1"})
    assert missing_key.use_llm is False


def test_read_meeting_params_from_env_parses_tuning_and_trace() -> None:
    params = read_meeting_params_from_env(
        {
            "CREWBORG_LLM_MEETINGS": "yes",
            "ANTHROPIC_API_KEY": "secret",
            "CREWBORG_LLM_MODEL": "claude-test",
            "CREWBORG_LLM_MAX_TOKENS": "123",
            "CREWBORG_LLM_TEMPERATURE": "0.7",
            "CREWBORG_LLM_TIMEOUT_SECONDS": "9.5",
            "CREWBORG_TRACE": "debug",
        }
    )

    assert params == MeetingParams(
        use_llm=True,
        model="claude-test",
        max_tokens=123,
        temperature=0.7,
        timeout_seconds=9.5,
        trace_raw=True,
        api_key="secret",
    )


def test_attend_meeting_builds_client_from_params() -> None:
    disabled = AttendMeetingMode(MeetingParams(use_llm=False))
    assert disabled._llm_client.enabled is False

    enabled = AttendMeetingMode(MeetingParams(use_llm=True, model="claude-test"))
    assert enabled._llm_client.enabled is True
    assert enabled._llm_client.config.model == "claude-test"


def test_bedrock_flag_enables_llm_without_anthropic_key() -> None:
    # Bedrock authenticates through AWS, so no ANTHROPIC_API_KEY is required, and
    # the flag implies meetings are on without a separate CREWBORG_LLM_MEETINGS.
    params = read_meeting_params_from_env({"USE_BEDROCK": "1"})
    assert params.use_llm is True
    assert params.use_bedrock is True
    assert params.model == DEFAULT_BEDROCK_MODEL


def test_bedrock_flag_aliases_are_accepted() -> None:
    for flag in ("USE_BEDROCK", "CREWBORG_USE_BEDROCK", "CLAUDE_CODE_USE_BEDROCK"):
        params = read_meeting_params_from_env({flag: "true"})
        assert params.use_bedrock is True, flag


def test_explicit_model_overrides_bedrock_default() -> None:
    params = read_meeting_params_from_env({"USE_BEDROCK": "1", "CREWBORG_LLM_MODEL": "custom-profile"})
    assert params.model == "custom-profile"


def test_direct_path_keeps_anthropic_key_requirement() -> None:
    # Without Bedrock, the direct Anthropic backend still needs a key, and the
    # direct model default is used.
    params = read_meeting_params_from_env({"CREWBORG_LLM_MEETINGS": "1", "ANTHROPIC_API_KEY": "secret"})
    assert params.use_bedrock is False
    assert params.model == DEFAULT_MEETING_MODEL


def test_build_meeting_client_propagates_bedrock_flag() -> None:
    client = build_meeting_client(MeetingParams(use_llm=True, provider="bedrock", model="profile"))
    assert client.enabled is True
    assert client.config.use_bedrock is True
    assert client.config.model == "profile"


def test_openrouter_provider_enables_llm_with_key() -> None:
    # A present OPENROUTER_API_KEY selects the OpenRouter backend and implies
    # meetings are on (no separate CREWBORG_LLM_MEETINGS flag needed).
    params = read_meeting_params_from_env({"OPENROUTER_API_KEY": "sk-or-secret"})
    assert params.use_llm is True
    assert params.provider == "openrouter"
    assert params.model == DEFAULT_OPENROUTER_MODEL
    assert params.api_key == "sk-or-secret"


def test_openrouter_requires_api_key() -> None:
    # The provider flag alone, without a key, has no viable backend.
    params = read_meeting_params_from_env({"CREWBORG_LLM_PROVIDER": "openrouter"})
    assert params.provider == "openrouter"
    assert params.use_llm is False


def test_explicit_provider_overrides_implicit_signals() -> None:
    # An explicit provider wins even when a Bedrock flag is also set.
    params = read_meeting_params_from_env(
        {"CREWBORG_LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "k", "USE_BEDROCK": "1"}
    )
    assert params.provider == "openrouter"


def test_build_meeting_client_selects_openrouter() -> None:
    client = build_meeting_client(
        MeetingParams(use_llm=True, provider="openrouter", model="anthropic/claude-haiku-4.5", api_key="k")
    )
    assert isinstance(client, OpenRouterMeetingClient)
    assert client.config.api_key == "k"
    assert client.config.model == "anthropic/claude-haiku-4.5"


def test_openrouter_client_decides_via_openai_compatible_api() -> None:
    # A stubbed OpenAI-compatible client: the OpenRouter adapter must build a
    # chat.completions request and parse the JSON decision out of the message.
    captured: dict = {}

    class _FakeChoiceMessage:
        content = '{"schema_version": 1, "action": "submit_vote", "vote_target": "red", "reason": "sus"}'

    class _FakeChoice:
        message = _FakeChoiceMessage()

    class _FakeUsage:
        def model_dump(self, mode: str = "json") -> dict:
            return {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    class _FakeResponse:
        choices = [_FakeChoice()]
        usage = _FakeUsage()

    class _FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeOpenAIClient:
        chat = _FakeChat()

    config = MeetingLLMConfig(provider="openrouter", model="anthropic/claude-haiku-4.5", api_key="k")
    client = OpenRouterMeetingClient(config, client=_FakeOpenAIClient())
    result = client.decide({"self": {"role": "crewmate"}}, trigger="meeting_start")

    assert result.decision.action == "submit_vote"
    assert result.decision.vote_target == "red"
    assert captured["model"] == "anthropic/claude-haiku-4.5"
    # System + user messages are sent (OpenAI chat format).
    assert [m["role"] for m in captured["messages"]] == ["system", "user"]


def test_report_body_targets_nearest_visible_body() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, visible_body_ids={2001, 2005})
    belief.bodies[2001] = BodyEntry(object_id=2001, color="red", world_x=400, world_y=400, first_seen_tick=1)
    belief.bodies[2005] = BodyEntry(object_id=2005, color="blue", world_x=110, world_y=100, first_seen_tick=1)
    intent = ReportBodyMode().decide(belief, ActionState())
    assert intent.kind == "report" and intent.target_id == 2005  # the nearer body


def test_report_body_idles_with_no_body_in_view() -> None:
    assert ReportBodyMode().decide(Belief(), ActionState()).kind == "idle"


def test_flee_targets_believed_imposter_and_is_dormant_when_empty() -> None:
    belief = Belief(self_world_x=100, self_world_y=100)
    belief.roster["red"] = PlayerRecord(
        object_id=1004, color="red", facing="left", world_x=120, world_y=100, last_seen_tick=1,
        life_status="alive",
    )
    # Empty evidence stub ⇒ dormant.
    assert FleeMode().decide(belief, ActionState()).kind == "idle"
    # Once a believed imposter exists, flee from it.
    belief.believed_imposters = {"red"}
    intent = FleeMode().decide(belief, ActionState())
    assert intent.kind == "flee_from" and intent.target_color == "red"
