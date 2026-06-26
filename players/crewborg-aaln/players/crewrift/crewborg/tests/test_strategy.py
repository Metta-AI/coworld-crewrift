"""Mode selector tests (design §10)."""

from __future__ import annotations

from players.crewrift.crewborg.modes import AttendMeetingMode
from players.crewrift.crewborg.modes.seek_crowd import SeekCrowdParams
from players.crewrift.crewborg.strategy import RuleBasedStrategy
from players.crewrift.crewborg.strategy.meeting import MeetingParams
from players.crewrift.crewborg.strategy.rule_based import (
    DICK_CALL_NO_MEETING_GRACE_TICKS,
    DICK_KILL_COOLDOWN_BUFFER_TICKS,
    DICK_MAX_BUTTON_TRAVEL_TICKS,
    EVIDENCE_CALL_MAX_TRAVEL_TICKS,
    EVIDENCE_CALL_NO_MEETING_GRACE_TICKS,
    FLEE_STALE_TICKS,
    TAIL_RESPONSE_MAX_TICKS,
    TAIL_RESPONSE_REFRACTORY_TICKS,
)
from players.crewrift.crewborg.strategy.shadow import TAIL_MIN_TICKS
from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect
from players.crewrift.crewborg.types import ActionState, Belief, PlayerRecord
from players.player_sdk import ModeRegistry
from players.player_sdk.types import BeliefSnapshot, ModeDirective, SharedMemory


def _select(belief: Belief) -> str:
    return _select_with(RuleBasedStrategy(), belief)


def _select_with(
    strategy: RuleBasedStrategy, belief: Belief, tick: int = 1, action_state: ActionState | None = None
) -> str:
    return _directive_with(strategy, belief, tick=tick, action_state=action_state).mode


def _directive_with(
    strategy: RuleBasedStrategy, belief: Belief, tick: int = 1, action_state: ActionState | None = None
) -> ModeDirective:
    memory = SharedMemory(
        belief=belief, action_state=action_state or ActionState(), active_directive=ModeDirective(mode="idle")
    )
    return strategy.decide(BeliefSnapshot(tick=tick, memory=memory))


def _crewmate_with_threat(*, tick: int, threat_x: int, threat_y: int = 100, last_seen_tick: int | None = None) -> Belief:
    belief = Belief(
        phase="Playing",
        self_role="crewmate",
        last_tick=tick,
        self_world_x=100,
        self_world_y=100,
    )
    belief.roster["red"] = PlayerRecord(
        object_id=1004,
        color="red",
        facing="left",
        world_x=threat_x,
        world_y=threat_y,
        last_seen_tick=tick if last_seen_tick is None else last_seen_tick,
        life_status="alive",
    )
    belief.believed_imposters = {"red"}
    return belief


def test_playing_crewmate_selects_normal() -> None:
    assert _select(Belief(phase="Playing", self_role="crewmate")) == "normal"
    # Role not yet known during early Playing still does tasks.
    assert _select(Belief(phase="Playing", self_role=None)) == "normal"
    # A crewmate ghost keeps doing its own tasks, but with noclip navigation.
    assert _select(Belief(phase="Playing", self_role="dead")) == "crewmate_ghost"


def test_voting_selects_attend_meeting() -> None:
    assert _select(Belief(phase="Voting")) == "attend_meeting"


def test_voting_attend_meeting_directive_carries_strategy_params() -> None:
    meeting_params = MeetingParams(use_llm=True, model="claude-test")
    directive = _directive_with(RuleBasedStrategy(meeting_params=meeting_params), Belief(phase="Voting"))

    assert directive.mode == "attend_meeting"
    assert directive.params == meeting_params

    registry: ModeRegistry[Belief, ActionState, object] = ModeRegistry()
    registry.register(AttendMeetingMode)
    assert registry.validation_error(directive) is None


def test_body_in_view_selects_report_body() -> None:
    from players.crewrift.crewborg.types import BodyEntry

    belief = Belief(phase="Playing", self_role="crewmate", visible_body_ids={2003})
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=10, world_y=10, first_seen_tick=1)
    assert _select(belief) == "report_body"


def test_ghost_does_tasks_not_report() -> None:
    from players.crewrift.crewborg.types import BodyEntry

    # A dead crewmate (ghost) can't report; it goes straight to ghost tasking even
    # with a body in view, so it keeps finishing its own tasks (design §7.3).
    belief = Belief(phase="Playing", self_role="dead", visible_body_ids={2003})
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=10, world_y=10, first_seen_tick=1)
    assert _select(belief) == "crewmate_ghost"


def _crewmate_near_kill_cooldown_ready() -> Belief:
    trigger_window = DICK_MAX_BUTTON_TRAVEL_TICKS + DICK_KILL_COOLDOWN_BUFFER_TICKS
    return Belief(
        phase="Playing",
        self_role="crewmate",
        last_tick=900 - trigger_window,
        kill_cooldown_start_tick=0,
        kill_cooldown_estimate=900,
    )


def test_dick_mode_disabled_by_default_near_kill_cooldown() -> None:
    belief = _crewmate_near_kill_cooldown_ready()

    assert _select(belief) == "normal"


def test_dick_mode_triggers_once_before_kill_cooldown_ready(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_DICK_MODE", "1")
    strategy = RuleBasedStrategy()
    action_state = ActionState()
    trigger_window = DICK_MAX_BUTTON_TRAVEL_TICKS + DICK_KILL_COOLDOWN_BUFFER_TICKS
    belief = Belief(
        phase="Playing",
        self_role="crewmate",
        last_tick=900 - trigger_window - 1,
        kill_cooldown_start_tick=0,
        kill_cooldown_estimate=900,
    )

    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "normal"

    belief.last_tick += 1
    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "dick_mode"

    action_state.last_call_meeting_attempt_tick = belief.last_tick + 5
    belief.phase = "Voting"
    belief.phase_start_tick = action_state.last_call_meeting_attempt_tick + 1
    belief.last_tick = belief.phase_start_tick
    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "dick_mode"

    belief.phase = "Playing"
    belief.last_tick += 1
    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "normal"

    # A later cooldown window in the same game does not re-arm Dick Mode; Crewrift's
    # default ButtonCalls is one per player.
    belief.kill_cooldown_start_tick = belief.last_tick
    belief.last_tick = belief.kill_cooldown_start_tick + 900 - trigger_window
    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "normal"


def test_dick_mode_alias_triggers_crewmate_path(monkeypatch) -> None:
    monkeypatch.setenv("DICK_MODE", "true")
    belief = _crewmate_near_kill_cooldown_ready()

    assert _select(belief) == "dick_mode"


def test_dick_mode_does_not_override_dead_or_imposter_roles(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_DICK_MODE", "1")
    crewmate = _crewmate_near_kill_cooldown_ready()

    assert _select(crewmate.model_copy(update={"self_role": "dead"})) == "crewmate_ghost"
    assert _select(crewmate.model_copy(update={"self_role": "imposter"})) == "pretend"


def test_dick_mode_button_refusal_timeout_does_not_retry(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_DICK_MODE", "1")
    strategy = RuleBasedStrategy()
    action_state = ActionState()
    belief = _crewmate_near_kill_cooldown_ready()

    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "dick_mode"

    action_state.last_call_meeting_attempt_tick = belief.last_tick + 1
    belief.last_tick = action_state.last_call_meeting_attempt_tick + DICK_CALL_NO_MEETING_GRACE_TICKS
    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "normal"

    belief.kill_cooldown_start_tick = belief.last_tick
    belief.last_tick += 1
    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "normal"


def test_dick_mode_does_not_taunt_meetings_we_did_not_call(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_DICK_MODE", "1")
    strategy = RuleBasedStrategy()
    action_state = ActionState()
    belief = _crewmate_near_kill_cooldown_ready()

    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "dick_mode"

    belief.phase = "Voting"
    belief.phase_start_tick = belief.last_tick + 1
    belief.last_tick = belief.phase_start_tick
    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "attend_meeting"


def _empty_map() -> MapData:
    return MapData(
        width=1000, height=1000, tasks=(), vents=(), rooms=(),
        button=MapRect(x=500, y=500, w=28, h=34), home=MapPoint(x=0, y=0),
    )


def _crewmate_with_evidence(*, tick: int = 100) -> Belief:
    # A believed imposter (over the flee bar) who is NOT nearby — so Flee does
    # not preempt and the evidence-call trigger is what's under test.
    belief = Belief(
        phase="Playing", self_role="crewmate", last_tick=tick,
        self_world_x=100, self_world_y=100, map=_empty_map(),
    )
    belief.roster["red"] = PlayerRecord(
        object_id=1004, color="red", facing="left", world_x=900, world_y=900,
        last_seen_tick=tick, life_status="alive",
    )
    belief.believed_imposters = {"red"}
    return belief


def test_evidence_call_triggers_on_a_believed_imposter() -> None:
    assert _select(_crewmate_with_evidence()) == "call_button"


def test_evidence_call_requires_evidence_and_a_crewmate() -> None:
    no_evidence = _crewmate_with_evidence()
    no_evidence.believed_imposters = set()
    assert _select(no_evidence) == "normal"

    ghost = _crewmate_with_evidence()
    ghost.self_role = "dead"
    assert _select(ghost) == "crewmate_ghost"

    imposter = _crewmate_with_evidence()
    imposter.self_role = "imposter"
    assert _select(imposter) != "call_button"


def test_evidence_call_is_outranked_by_report_and_flee() -> None:
    from players.crewrift.crewborg.types import BodyEntry

    body = _crewmate_with_evidence()
    body.visible_body_ids = {2003}
    body.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=10, world_y=10, first_seen_tick=1)
    assert _select(body) == "report_body"

    near_threat = _crewmate_with_evidence()
    near_threat.roster["red"].world_x = 110
    near_threat.roster["red"].world_y = 100
    assert _select(near_threat) == "flee"


def test_evidence_call_spends_the_button_once_a_meeting_opens() -> None:
    strategy = RuleBasedStrategy()
    action_state = ActionState()
    belief = _crewmate_with_evidence(tick=100)

    assert _select_with(strategy, belief, tick=100, action_state=action_state) == "call_button"

    # The walk lands and we press A; the meeting opens.
    action_state.last_call_meeting_attempt_tick = 120
    belief.phase = "Voting"
    belief.phase_start_tick = 121
    belief.last_tick = 121
    assert _select_with(strategy, belief, tick=121, action_state=action_state) == "attend_meeting"

    # Back in Playing with the evidence still live: the budget is spent, task on.
    belief.phase = "Playing"
    belief.last_tick = 200
    assert _select_with(strategy, belief, tick=200, action_state=action_state) == "normal"


def test_evidence_call_refused_press_does_not_retry() -> None:
    strategy = RuleBasedStrategy()
    action_state = ActionState()
    belief = _crewmate_with_evidence(tick=100)

    assert _select_with(strategy, belief, tick=100, action_state=action_state) == "call_button"

    # Pressed, but the server refused (no meeting within the grace window).
    action_state.last_call_meeting_attempt_tick = 120
    belief.last_tick = 120 + EVIDENCE_CALL_NO_MEETING_GRACE_TICKS
    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "normal"
    belief.last_tick += 1
    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "normal"


def test_evidence_call_walk_timeout_does_not_retry() -> None:
    strategy = RuleBasedStrategy()
    action_state = ActionState()
    belief = _crewmate_with_evidence(tick=100)

    assert _select_with(strategy, belief, tick=100, action_state=action_state) == "call_button"

    belief.last_tick = 100 + EVIDENCE_CALL_MAX_TRAVEL_TICKS
    assert _select_with(strategy, belief, tick=belief.last_tick, action_state=action_state) == "normal"


def test_evidence_call_releases_the_budget_when_evidence_dissolves() -> None:
    strategy = RuleBasedStrategy()
    action_state = ActionState()
    belief = _crewmate_with_evidence(tick=100)

    assert _select_with(strategy, belief, tick=100, action_state=action_state) == "call_button"

    # The believed imposter dies mid-walk: abort without spending the budget.
    belief.believed_imposters = set()
    belief.last_tick = 110
    assert _select_with(strategy, belief, tick=110, action_state=action_state) == "normal"

    # Fresh evidence later re-arms the call.
    belief.believed_imposters = {"red"}
    belief.last_tick = 120
    assert _select_with(strategy, belief, tick=120, action_state=action_state) == "call_button"


def test_approaching_believed_imposter_selects_flee() -> None:
    assert _select(_crewmate_with_threat(tick=1, threat_x=110)) == "flee"


def test_flee_stays_active_until_threat_is_clearly_clear() -> None:
    strategy = RuleBasedStrategy()

    assert _select_with(strategy, _crewmate_with_threat(tick=1, threat_x=150), tick=1) == "flee"
    # Outside the 60px enter radius but inside the wider exit radius: stay in Flee
    # instead of returning to tasking and bouncing at the threshold.
    assert _select_with(strategy, _crewmate_with_threat(tick=2, threat_x=175), tick=2) == "flee"
    assert _select_with(strategy, _crewmate_with_threat(tick=3, threat_x=205), tick=3) == "normal"


def test_flee_exits_when_last_known_threat_position_is_stale() -> None:
    strategy = RuleBasedStrategy()

    assert _select_with(strategy, _crewmate_with_threat(tick=10, threat_x=150), tick=10) == "flee"
    stale = _crewmate_with_threat(
        tick=10 + FLEE_STALE_TICKS + 1,
        threat_x=150,
        last_seen_tick=10,
    )
    assert _select_with(strategy, stale, tick=stale.last_tick) == "normal"


def _tailed_belief(tick: int = TAIL_MIN_TICKS + 5) -> Belief:
    belief = _crewmate_with_threat(tick=tick, threat_x=150)
    belief.believed_imposters = set()  # a tail, not a flee-level threat
    belief.tail_streaks = {"red": (0, tick)}
    # The imposters' kill clock (globally observable) is ready: the tail could
    # strike, so the crowd-seek gate is open.
    belief.kill_cooldown_start_tick = tick - 500
    return belief


def test_tailed_crewmate_seeks_crowd() -> None:
    belief = _tailed_belief()
    directive = _directive_with(RuleBasedStrategy(), belief, tick=belief.last_tick)
    assert directive.mode == "seek_crowd"
    assert directive.params == SeekCrowdParams(avoid_color="red")


def test_tail_response_waits_out_a_fresh_kill_cooldown() -> None:
    # A shadow during a fresh cooldown is harmless (a shadow kill needs the
    # cooldown ready): keep tasking instead of abandoning the leg.
    belief = _tailed_belief(tick=600)
    belief.kill_cooldown_start_tick = belief.last_tick - 10  # ~490 ticks to ready
    assert _select_with(RuleBasedStrategy(), belief, tick=belief.last_tick) == "normal"


def test_tail_response_runs_in_bounded_bursts_with_a_refractory() -> None:
    strategy = RuleBasedStrategy()
    start = TAIL_MIN_TICKS + 5

    def at(tick: int) -> str:
        belief = _tailed_belief(tick=tick)
        return _select_with(strategy, belief, tick=tick)

    assert at(start) == "seek_crowd"
    assert at(start + TAIL_RESPONSE_MAX_TICKS - 1) == "seek_crowd"
    # Burst cap reached: back to tasking despite the persistent tail…
    assert at(start + TAIL_RESPONSE_MAX_TICKS) == "normal"
    # …and the refractory holds before a new burst may start.
    assert at(start + TAIL_RESPONSE_MAX_TICKS + 10) == "normal"
    assert at(start + TAIL_RESPONSE_MAX_TICKS + TAIL_RESPONSE_REFRACTORY_TICKS + 1) == "seek_crowd"


def test_flee_outranks_the_tail_response() -> None:
    # A believed imposter at close range is a *now* threat: Flee wins.
    belief = _crewmate_with_threat(tick=TAIL_MIN_TICKS + 5, threat_x=110)
    belief.tail_streaks = {"red": (0, TAIL_MIN_TICKS + 5)}
    assert _select_with(RuleBasedStrategy(), belief, tick=belief.last_tick) == "flee"


def test_a_tail_with_a_witness_nearby_keeps_tasking() -> None:
    belief = _tailed_belief()  # gate open: the kill clock is ready
    belief.roster["green"] = PlayerRecord(
        color="green", world_x=130, world_y=100, last_seen_tick=belief.last_tick, life_status="alive"
    )
    assert _select_with(RuleBasedStrategy(), belief, tick=belief.last_tick) == "normal"


def test_non_playing_phases_idle() -> None:
    assert _select(Belief(phase="Lobby")) == "idle"
    assert _select(Belief(phase="RoleReveal")) == "idle"
    assert _select(Belief(phase="GameOver")) == "idle"


def _imposter_with_visible_target(**kwargs) -> Belief:
    from players.crewrift.crewborg.types import PlayerRecord

    belief = Belief(phase="Playing", self_role="imposter", last_tick=10, self_world_x=100, self_world_y=100, **kwargs)
    # A lone, isolated, reachable (no nav graph) crewmate — a valid kill opportunity.
    belief.roster["red"] = PlayerRecord(
        object_id=1004, color="red", facing="left", world_x=50, world_y=50, last_seen_tick=10,
        life_status="alive",
    )
    return belief


def test_imposter_pretends_by_default() -> None:
    # No kill opportunity ⇒ Pretend (which itself follows crew / wanders rooms).
    assert _select(Belief(phase="Playing", self_role="imposter", last_tick=10)) == "pretend"


def test_imposter_hunts_when_kill_ready_with_opportunity() -> None:
    assert _select(_imposter_with_visible_target(self_kill_ready=True)) == "hunt"
    # Kill ready but no target in view ⇒ Search owns target acquisition.
    no_target = Belief(
        phase="Playing", self_role="imposter", self_kill_ready=True, last_tick=10,
        self_world_x=100, self_world_y=100,
    )
    assert _select(no_target) == "search"


def test_imposter_hunts_to_stalk_even_when_targets_are_clustered() -> None:
    from players.crewrift.crewborg.types import PlayerRecord

    # Kill ready with crewmates in sight (even clustered) ⇒ Hunt and stalk; Hunt
    # itself holds off the actual kill until the victim is isolated.
    belief = Belief(
        phase="Playing", self_role="imposter", self_kill_ready=True, last_tick=10,
        self_world_x=100, self_world_y=100,
    )
    belief.roster["green"] = PlayerRecord(
        object_id=1004, color="green", facing="left", world_x=50, world_y=50, last_seen_tick=10,
        life_status="alive",
    )
    belief.roster["blue"] = PlayerRecord(
        object_id=1005, color="blue", facing="left", world_x=58, world_y=50, last_seen_tick=10,
        life_status="alive",
    )
    assert _select(belief) == "hunt"


def test_imposter_evades_before_reporting_a_fresh_kill_body() -> None:
    from players.crewrift.crewborg.types import BodyEntry

    # A fresh self-kill body in view -> evade first, outranking the old
    # report-first path even if the kill is otherwise ready.
    belief = _imposter_with_visible_target(self_kill_ready=True, last_kill_tick=9, visible_body_ids={2003})
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=60, world_y=60, first_seen_tick=10)
    assert _select(belief) == "evade"


def test_imposter_can_report_a_non_fresh_visible_body() -> None:
    from players.crewrift.crewborg.types import BodyEntry

    belief = _imposter_with_visible_target(self_kill_ready=True, last_kill_tick=1, visible_body_ids={2003})
    belief.last_tick = 100
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=60, world_y=60, first_seen_tick=10)
    assert _select(belief) == "report_body"


def test_imposter_searches_within_the_lead_window_before_ready() -> None:
    # Not yet kill-ready, but the cooldown clears in ~50 ticks (≤ SEARCH_LEAD_TICKS)
    # ⇒ enter Search, not Hunt. Search follows visible targets until Hunt activates.
    belief = _imposter_with_visible_target(self_kill_ready=False)
    belief.kill_cooldown_start_tick = belief.last_tick
    belief.kill_cooldown_estimate = 50  # ticks_until_ready = start + 50 − now = 50
    assert _select(belief) == "search"


def test_be_dumb_imposter_searches_instead_of_pretending(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_BE_DUMB", "1")

    belief = Belief(phase="Playing", self_role="imposter", self_kill_ready=False, last_tick=10)
    assert _select(belief) == "search"


def test_be_dumb_imposter_hunts_when_kill_ready_with_visible_victim(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_BE_DUMB", "1")

    assert _select(_imposter_with_visible_target(self_kill_ready=True)) == "hunt"


def test_be_dumb_alias_enables_the_aggressive_imposter_path(monkeypatch) -> None:
    monkeypatch.setenv("BE_DUMB", "true")

    assert _select(_imposter_with_visible_target(self_kill_ready=True)) == "hunt"


def test_be_dumb_imposter_skips_evade_and_report_body(monkeypatch) -> None:
    from players.crewrift.crewborg.types import BodyEntry

    monkeypatch.setenv("CREWBORG_BE_DUMB", "1")

    fresh_kill = _imposter_with_visible_target(self_kill_ready=True, last_kill_tick=9, visible_body_ids={2003})
    fresh_kill.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=60, world_y=60, first_seen_tick=10)
    assert _select(fresh_kill) == "hunt"

    body_only = Belief(phase="Playing", self_role="imposter", self_kill_ready=False, last_tick=10, visible_body_ids={2003})
    body_only.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=60, world_y=60, first_seen_tick=10)
    assert _select(body_only) == "search"


def test_be_dumb_does_not_override_voting(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_BE_DUMB", "1")

    assert _select(Belief(phase="Voting", self_role="imposter")) == "attend_meeting"


def test_imposter_pretends_when_kill_is_far_off_cooldown() -> None:
    # A victim is in view but the kill is a long way off ⇒ blend (Pretend), don't tail.
    belief = _imposter_with_visible_target(self_kill_ready=False)
    belief.kill_cooldown_start_tick = belief.last_tick
    belief.kill_cooldown_estimate = 900
    assert _select(belief) == "pretend"


def test_imposter_pretends_when_only_a_teammate_is_visible() -> None:
    # Kill ready but the only visible player is a teammate ⇒ no kill target, so Search.
    belief = _imposter_with_visible_target(self_kill_ready=True)
    belief.teammate_colors = {"red"}  # the visible target is red (see helper)
    assert _select(belief) == "search"
