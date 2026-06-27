"""CommanderStrategy off-inert guard (strategy/commander/strategy.py).

When the gameplay-commander feature is disabled, the strategy decorator must be
inert: it must produce exactly the bare ``RuleBasedStrategy`` mode, emit no
inferences, and never start the background worker. A silent regression here would
let a gated-off feature perturb the deterministic line the agent actually plays.
"""

from __future__ import annotations

from typing import Any

from crewborg.strategy.commander.strategy import CommanderStrategy
from crewborg.strategy.rule_based import RuleBasedStrategy
from crewborg.types import ActionState, Belief, BodyEntry, PlayerRecord
from players.player_sdk import OverwriteBuffer
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
