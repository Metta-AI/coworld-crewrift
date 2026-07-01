from __future__ import annotations

import time
from typing import Any

from crewborg import build_runtime
from crewborg.coworld.scene import SceneState
from crewborg.strategy.commander.strategy import CommanderStrategy, apply_commander_inferences
from crewborg.strategy.rule_based import RuleBasedStrategy
from crewborg.tests import sprite_wire as w
from crewborg.types import ActionState, Belief, BodyEntry, CommanderPriorities, Observation, PlayerRecord
from players.player_sdk import OverwriteBuffer
from players.player_sdk.trace import ListTraceSink
from players.player_sdk.types import BeliefSnapshot, ModeDirective, SharedMemory


class _DisabledWorker:
    enabled = False

    def __init__(self) -> None:
        self.snapshots: OverwriteBuffer[dict[str, Any]] = OverwriteBuffer()
        self.priorities: OverwriteBuffer[dict[str, Any]] = OverwriteBuffer()
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True
        self.snapshots.close()
        self.priorities.close()


class _ManualWorker(_DisabledWorker):
    enabled = True


def test_commander_strategy_matches_rules_when_disabled() -> None:
    cases = [
        Belief(phase="Playing", self_role="crewmate"),
        Belief(phase="Voting", self_role="crewmate"),
        _crewmate_with_visible_body(),
        Belief(phase="Playing", self_role="imposter", last_tick=10),
        _imposter_with_visible_target(self_kill_ready=True),
        _imposter_with_visible_target(self_kill_ready=True, last_kill_tick=9),
        _imposter_with_visible_target(
            self_kill_ready=False,
            kill_cooldown_start_tick=10,
            kill_cooldown_estimate=50,
        ),
    ]

    for belief in cases:
        expected = RuleBasedStrategy().decide(_snapshot(belief)).mode
        worker = _DisabledWorker()
        result = CommanderStrategy(RuleBasedStrategy(), worker, feature_enabled=False).decide(_snapshot(belief))

        assert result.directive is not None
        assert result.directive.mode == expected
        assert result.inferences == {}
        assert worker.started is False


def test_commander_strategy_sanitizes_and_returns_latest_worker_priorities(monkeypatch) -> None:
    monkeypatch.delenv("CREWBORG_COMMANDER_FORCE", raising=False)
    belief = _imposter_with_visible_target(self_kill_ready=False)
    worker = _ManualWorker()
    worker.priorities.publish(
        {
            "hunt_room": "electrical",
            "target_player": "red",
            "allow_witnessed_kill": True,
            "reason": "fake",
        }
    )
    strategy = CommanderStrategy(RuleBasedStrategy(), worker, feature_enabled=True)

    result = strategy.decide(_snapshot(belief, active_mode="search", tick=12))

    assert result.directive is not None
    assert result.inferences["commander"]["hunt_room"] == "electrical"
    assert result.inferences["commander"]["target_player"] == "red"
    assert result.inferences["commander"]["allow_witnessed_kill"] is False
    assert result.inferences["commander"]["as_of_tick"] == 12
    assert worker.snapshots.take()["active_mode"] == "search"


def test_commander_strategy_force_returns_fresh_priorities_without_worker(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_COMMANDER_FORCE", '{"target_room":"electrical"}')
    belief = _imposter_with_visible_target(self_kill_ready=False)
    worker = _DisabledWorker()
    strategy = CommanderStrategy(RuleBasedStrategy(), worker, feature_enabled=True)

    result = strategy.decide(_snapshot(belief, active_mode="search", tick=33))

    assert result.directive is not None
    assert result.inferences["commander"]["target_room"] == "electrical"
    assert result.inferences["commander"]["as_of_tick"] == 33
    assert worker.started is False
    assert worker.snapshots.take() is None


def test_commander_strategy_unset_force_uses_worker_path(monkeypatch) -> None:
    monkeypatch.delenv("CREWBORG_COMMANDER_FORCE", raising=False)
    belief = _imposter_with_visible_target(self_kill_ready=False)
    worker = _DisabledWorker()
    strategy = CommanderStrategy(RuleBasedStrategy(), worker, feature_enabled=True)

    result = strategy.decide(_snapshot(belief, active_mode="search", tick=33))

    assert result.directive is not None
    assert result.inferences == {}
    assert worker.started is True
    assert worker.snapshots.take()["active_mode"] == "search"


def test_apply_commander_inferences_sets_belief() -> None:
    belief = Belief()

    apply_commander_inferences(
        belief,
        {"commander": CommanderPriorities(target_room="electrical", as_of_tick=10).model_dump()},
    )

    assert belief.commander is not None
    assert belief.commander.target_room == "electrical"
    assert belief.commander.as_of_tick == 10


def test_commander_strategy_close_closes_worker() -> None:
    worker = _DisabledWorker()
    strategy = CommanderStrategy(RuleBasedStrategy(), worker, feature_enabled=False)

    strategy.close()

    assert worker.closed is True


def test_runtime_with_commander_off_leaves_belief_unset_and_no_inference_trace(monkeypatch) -> None:
    monkeypatch.delenv("CREWBORG_LLM_COMMANDER", raising=False)
    monkeypatch.setenv("CREWBORG_TRACE_GROUPS", "commander")
    trace = ListTraceSink()
    runtime = build_runtime(trace_sink=trace)
    scene = SceneState()
    scene.apply(w.clear_objects())
    scene.tick += 1

    runtime.step(Observation(scene=scene, tick=scene.tick))
    runtime.close()

    assert runtime.belief.commander is None
    assert "strategy_inferences" not in trace.names()
    assert "domain.commander_started" not in trace.names()


def test_runtime_with_commander_trace_group_reports_backend_env_seen(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_LLM_COMMANDER", "1")
    monkeypatch.delenv("CREWBORG_COMMANDER_FORCE", raising=False)
    monkeypatch.delenv("USE_BEDROCK", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", raising=False)
    monkeypatch.setenv("CREWBORG_TRACE_GROUPS", "commander")
    trace = ListTraceSink()
    runtime = build_runtime(trace_sink=trace)
    scene = SceneState()
    scene.apply(w.clear_objects())
    scene.tick += 1

    runtime.step(Observation(scene=scene, tick=scene.tick))
    time.sleep(0.02)
    scene.tick += 1
    runtime.step(Observation(scene=scene, tick=scene.tick))
    runtime.close()

    [started] = [event for event in trace.events if event.name == "domain.commander_started"]
    assert started.data == {
        "enabled": False,
        "backend": None,
        "model": None,
        "disabled_reason": "no LLM backend configured",
        "attempt": 1,
        "env_seen": {
            "USE_BEDROCK": False,
            "CLAUDE_CODE_USE_BEDROCK": False,
            "ANTHROPIC_API_KEY": False,
            "AWS_ENDPOINT_URL_BEDROCK_RUNTIME": False,
        },
    }


def _snapshot(
    belief: Belief,
    *,
    tick: int = 1,
    active_mode: str = "idle",
) -> BeliefSnapshot[Belief, ActionState]:
    memory = SharedMemory(
        belief=belief,
        action_state=ActionState(),
        active_directive=ModeDirective(mode=active_mode),
    )
    return BeliefSnapshot(tick=tick, memory=memory)


def _crewmate_with_visible_body() -> Belief:
    belief = Belief(phase="Playing", self_role="crewmate", visible_body_ids={2003})
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=10, world_y=10, first_seen_tick=1)
    return belief


def _imposter_with_visible_target(**kwargs: Any) -> Belief:
    from crewborg.map.types import MapData, MapPoint, MapRect, Room

    belief = Belief(
        phase="Playing",
        self_role="imposter",
        last_tick=10,
        self_world_x=100,
        self_world_y=100,
        **kwargs,
    )
    belief.map = MapData(
        width=200,
        height=200,
        tasks=(),
        vents=(),
        rooms=(Room(name="electrical", x=0, y=0, w=200, h=200),),
        button=MapRect(x=0, y=0, w=8, h=8),
        home=MapPoint(x=0, y=0),
    )
    belief.roster["red"] = PlayerRecord(
        object_id=1004,
        color="red",
        facing="left",
        world_x=50,
        world_y=50,
        last_seen_tick=10,
        life_status="alive",
    )
    return belief
