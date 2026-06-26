"""Hunter profile tests (``strategy.hunter`` — the anti-sussyboi fork).

Covers the three behaviors behind ``CREWBORG_HUNTER=1``: the timed crew button
(jam), the imposter button stakeout, and the early-button-caller suspicion
evidence — plus the shared one-ButtonCalls budget across FSMs.
"""

from __future__ import annotations

from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect
from players.crewrift.crewborg.modes.jam_button import JamButtonMode
from players.crewrift.crewborg.modes.stakeout import StakeoutMode
from players.crewrift.crewborg.strategy import RuleBasedStrategy
from players.crewrift.crewborg.strategy.hunter import (
    EARLY_BUTTON_CALL_MAX_ELAPSED_TICKS,
    JAM_MAX_STAGING_TICKS,
    JAM_PRESS_LEAD_TICKS,
    JAM_START_MARGIN_TICKS,
    STAKEOUT_LEAD_TICKS,
    STAKEOUT_MAX_ELAPSED_TICKS,
    estimate_travel_ticks,
    stakeout_window_active,
)
from players.crewrift.crewborg.strategy.opportunity import select_victim
from players.crewrift.crewborg.strategy.suspicion import update_suspicion
from players.crewrift.crewborg.types import ActionState, Belief, MeetingRecord, PlayerRecord
from players.player_sdk.types import BeliefSnapshot, ModeDirective, SharedMemory

KILL_COOLDOWN = 500


def _map() -> MapData:
    return MapData(
        width=1000, height=1000, tasks=(), vents=(), rooms=(),
        button=MapRect(x=500, y=500, w=28, h=34), home=MapPoint(x=0, y=0),
    )


def _select_with(
    strategy: RuleBasedStrategy, belief: Belief, action_state: ActionState | None = None
) -> str:
    memory = SharedMemory(
        belief=belief, action_state=action_state or ActionState(), active_directive=ModeDirective(mode="idle")
    )
    return strategy.decide(BeliefSnapshot(tick=belief.last_tick, memory=memory)).mode


def _crewmate(*, tick: int) -> Belief:
    return Belief(
        phase="Playing", self_role="crewmate", last_tick=tick,
        self_world_x=480, self_world_y=480, map=_map(),
        kill_cooldown_start_tick=0, kill_cooldown_estimate=KILL_COOLDOWN,
    )


def _jam_trigger_tick(belief: Belief) -> int:
    """The first tick at which the jam window opens for ``belief``'s position."""

    travel = estimate_travel_ticks(belief, (belief.self_world_x, belief.self_world_y), (514, 517))
    return KILL_COOLDOWN - travel - JAM_START_MARGIN_TICKS


# --- jam (timed crew button) -----------------------------------------------------


def test_jam_inactive_without_hunter_flag() -> None:
    belief = _crewmate(tick=KILL_COOLDOWN - JAM_PRESS_LEAD_TICKS)
    assert _select_with(RuleBasedStrategy(hunter=False), belief) == "normal"


def test_jam_triggers_inside_travel_window_and_spends_once() -> None:
    strategy = RuleBasedStrategy(hunter=True)
    action_state = ActionState()
    belief = _crewmate(tick=1)
    trigger = _jam_trigger_tick(belief)

    belief.last_tick = trigger - 1
    assert _select_with(strategy, belief, action_state) == "normal"

    belief.last_tick = trigger
    assert _select_with(strategy, belief, action_state) == "jam_button"
    belief.last_tick += 10
    assert _select_with(strategy, belief, action_state) == "jam_button"

    # We press; the meeting opens. The budget is spent.
    action_state.last_call_meeting_attempt_tick = belief.last_tick + 5
    belief.phase = "Voting"
    belief.last_tick = action_state.last_call_meeting_attempt_tick + 1
    assert _select_with(strategy, belief, action_state) == "attend_meeting"

    # Next segment: cooldown re-anchored, but the one ButtonCalls budget is gone.
    belief.phase = "Playing"
    belief.kill_cooldown_start_tick = belief.last_tick
    belief.last_tick = belief.kill_cooldown_start_tick + KILL_COOLDOWN - JAM_PRESS_LEAD_TICKS
    assert _select_with(strategy, belief, action_state) == "normal"


def test_jam_budget_survives_a_meeting_we_did_not_press_for() -> None:
    strategy = RuleBasedStrategy(hunter=True)
    action_state = ActionState()
    belief = _crewmate(tick=1)
    belief.last_tick = _jam_trigger_tick(belief)
    assert _select_with(strategy, belief, action_state) == "jam_button"

    # Someone else's body report opens a meeting before we press.
    belief.phase = "Voting"
    belief.last_tick += 5
    assert _select_with(strategy, belief, action_state) == "attend_meeting"

    # The meeting reset the cooldown for free — the jam re-arms next segment.
    belief.phase = "Playing"
    belief.kill_cooldown_start_tick = belief.last_tick
    belief.last_tick = belief.kill_cooldown_start_tick + KILL_COOLDOWN - JAM_PRESS_LEAD_TICKS
    assert _select_with(strategy, belief, action_state) == "jam_button"


def test_jam_staging_timeout_spends_the_budget() -> None:
    strategy = RuleBasedStrategy(hunter=True)
    action_state = ActionState()
    belief = _crewmate(tick=1)
    belief.last_tick = _jam_trigger_tick(belief)
    assert _select_with(strategy, belief, action_state) == "jam_button"

    belief.last_tick += JAM_MAX_STAGING_TICKS
    assert _select_with(strategy, belief, action_state) == "normal"

    # Spent: a fresh segment does not re-arm.
    belief.kill_cooldown_start_tick = belief.last_tick
    belief.last_tick = belief.kill_cooldown_start_tick + KILL_COOLDOWN - JAM_PRESS_LEAD_TICKS
    assert _select_with(strategy, belief, action_state) == "normal"


def test_jam_mode_stages_then_presses_at_cooldown_expiry() -> None:
    mode = JamButtonMode()
    belief = _crewmate(tick=KILL_COOLDOWN - JAM_PRESS_LEAD_TICKS - 100)

    staged = mode.decide(belief, ActionState())
    assert staged.kind == "navigate_to"

    belief.last_tick = KILL_COOLDOWN - JAM_PRESS_LEAD_TICKS
    pressed = mode.decide(belief, ActionState())
    assert pressed.kind == "call_meeting"


def test_jam_spends_the_shared_button_budget_for_the_evidence_call() -> None:
    strategy = RuleBasedStrategy(hunter=True)
    action_state = ActionState()
    belief = _crewmate(tick=1)
    belief.last_tick = _jam_trigger_tick(belief)
    assert _select_with(strategy, belief, action_state) == "jam_button"
    belief.last_tick += JAM_MAX_STAGING_TICKS  # timeout: budget spent
    assert _select_with(strategy, belief, action_state) == "normal"

    # A believed imposter appears (far away, so Flee does not preempt): the
    # evidence call would fire, but the shared budget is gone.
    belief.roster["red"] = PlayerRecord(
        object_id=1004, color="red", facing="left", world_x=900, world_y=900,
        last_seen_tick=belief.last_tick, life_status="alive",
    )
    belief.believed_imposters = {"red"}
    assert _select_with(strategy, belief, action_state) == "normal"


# --- stakeout (imposter button lurk) ----------------------------------------------


def _imposter(*, tick: int, start_tick: int = 0) -> Belief:
    return Belief(
        phase="Playing", self_role="imposter", last_tick=tick,
        self_world_x=300, self_world_y=300, map=_map(),
        kill_cooldown_start_tick=start_tick, kill_cooldown_estimate=KILL_COOLDOWN,
        self_kill_ready=False,
    )


def test_stakeout_window_opens_near_kill_ready_and_closes_deep_in_segment() -> None:
    early = _imposter(tick=KILL_COOLDOWN - STAKEOUT_LEAD_TICKS - 1)
    assert not stakeout_window_active(early)

    in_window = _imposter(tick=KILL_COOLDOWN - STAKEOUT_LEAD_TICKS)
    assert stakeout_window_active(in_window)

    ready = _imposter(tick=KILL_COOLDOWN + 50)
    ready.self_kill_ready = True
    assert stakeout_window_active(ready)

    stale = _imposter(tick=STAKEOUT_MAX_ELAPSED_TICKS + 1)
    stale.self_kill_ready = True
    assert not stakeout_window_active(stale)


def test_imposter_selects_stakeout_in_window_only_with_hunter() -> None:
    belief = _imposter(tick=KILL_COOLDOWN - STAKEOUT_LEAD_TICKS)
    assert _select_with(RuleBasedStrategy(hunter=True, be_dumb=False), belief) == "stakeout"
    assert _select_with(RuleBasedStrategy(hunter=True, be_dumb=True), belief) == "stakeout"
    assert _select_with(RuleBasedStrategy(hunter=False, be_dumb=False), belief) == "search"


def test_hunt_outranks_stakeout_with_a_visible_victim() -> None:
    belief = _imposter(tick=KILL_COOLDOWN + 10)
    belief.self_kill_ready = True
    belief.roster["green"] = PlayerRecord(
        object_id=1002, color="green", facing="left", world_x=310, world_y=300,
        last_seen_tick=belief.last_tick, life_status="alive",
    )
    assert _select_with(RuleBasedStrategy(hunter=True, be_dumb=False), belief) == "hunt"


def test_stakeout_mode_holds_the_lurk_point_when_nobody_is_visible() -> None:
    belief = _imposter(tick=KILL_COOLDOWN - 50)
    intent = StakeoutMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point is not None and intent.point[1] > 534  # below the button rect

    belief.self_world_x, belief.self_world_y = intent.point
    holding = StakeoutMode().decide(belief, ActionState())
    assert holding.kind == "idle"


def test_select_victim_prefers_button_approacher_in_stakeout_window(monkeypatch) -> None:
    belief = _imposter(tick=KILL_COOLDOWN - 50)
    # "blue" is far more isolated (the default pick); "red" approaches the button.
    belief.roster["blue"] = PlayerRecord(
        object_id=1003, color="blue", facing="left", world_x=100, world_y=900,
        last_seen_tick=belief.last_tick, life_status="alive",
    )
    belief.roster["red"] = PlayerRecord(
        object_id=1004, color="red", facing="left", world_x=520, world_y=490,
        last_seen_tick=belief.last_tick, life_status="alive",
    )
    belief.roster["green"] = PlayerRecord(
        object_id=1002, color="green", facing="left", world_x=540, world_y=470,
        last_seen_tick=belief.last_tick, life_status="alive",
    )

    monkeypatch.delenv("CREWBORG_HUNTER", raising=False)
    victim = select_victim(belief)
    assert victim is not None and victim.color == "blue"

    monkeypatch.setenv("CREWBORG_HUNTER", "1")
    victim = select_victim(belief)
    assert victim is not None and victim.color in {"red", "green"}


# --- early-button-caller suspicion -------------------------------------------------


def _belief_with_early_button_meeting(elapsed: int) -> Belief:
    belief = Belief(phase="Playing", self_role="crewmate", last_tick=600, map=_map())
    for color, oid in (("red", 1004), ("blue", 1003)):
        belief.roster[color] = PlayerRecord(
            object_id=oid, color=color, facing="left", world_x=100, world_y=100,
            last_seen_tick=600, life_status="alive",
        )
    belief.meeting_history.append(
        MeetingRecord(meeting_id=200, trigger="button", called_by="red", called_elapsed_ticks=elapsed)
    )
    return belief


def test_early_button_caller_raises_suspicion_only_with_hunter(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_HUNTER", "1")
    belief = _belief_with_early_button_meeting(EARLY_BUTTON_CALL_MAX_ELAPSED_TICKS)
    update_suspicion(belief)
    assert belief.suspicion["red"] > belief.suspicion["blue"]

    monkeypatch.delenv("CREWBORG_HUNTER", raising=False)
    update_suspicion(belief)
    assert belief.suspicion["red"] == belief.suspicion["blue"]


def test_late_button_caller_is_not_suspicious(monkeypatch) -> None:
    # A press at segment offset ~500 is the *crew* jam timing — no evidence.
    monkeypatch.setenv("CREWBORG_HUNTER", "1")
    belief = _belief_with_early_button_meeting(EARLY_BUTTON_CALL_MAX_ELAPSED_TICKS + 250)
    update_suspicion(belief)
    assert belief.suspicion["red"] == belief.suspicion["blue"]
