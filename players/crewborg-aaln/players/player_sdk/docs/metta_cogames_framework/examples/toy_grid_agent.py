"""Minimal grid-world cyborg agent using ``players.player_sdk``.

Run from the repo root with:

    python players/player_sdk/docs/metta_cogames_framework/examples/toy_grid_agent.py
"""

from __future__ import annotations

from dataclasses import dataclass

from players.player_sdk import (
    ActionCommand,
    ActionIntent,
    AgentRuntime,
    EmptyModeParams,
    ListTraceSink,
    Mode,
    ModeDirective,
    ModeParams,
    ModeRegistry,
    StrategyResult,
    SynchronousStrategyRunner,
)
from players.player_sdk.types import BeliefSnapshot


@dataclass
class Observation:
    position: int
    target: int


@dataclass
class Percept:
    position: int
    target: int
    tick: int


@dataclass
class Belief:
    position: int = 0
    target: int = 0


@dataclass
class ActionState:
    last_action: str = "noop"


class MoveParams(ModeParams):
    target: int


class IdleMode(Mode[Belief, ActionState, ActionIntent]):
    name = "idle"
    params_type = EmptyModeParams

    def decide(self, belief: Belief, action_state: ActionState) -> ActionIntent:
        del belief, action_state
        return ActionIntent(reason="idle")


class MoveToMode(Mode[Belief, ActionState, ActionIntent]):
    name = "move_to"
    params_type = MoveParams

    def decide(self, belief: Belief, action_state: ActionState) -> ActionIntent:
        del action_state
        params = self.params
        assert isinstance(params, MoveParams)
        self.emit.event(
            "move_target_chosen",
            {"position": belief.position, "target": params.target},
        )
        return ActionIntent(
            semantic="move_to",
            target=(params.target, 0),
            reason="closing distance to target",
        )

    def is_legal(self, belief: Belief) -> bool:
        return belief.position != belief.target


class ToyStrategy:
    """Slow strategy: choose move mode until position reaches target."""

    def decide(self, snapshot: BeliefSnapshot[Belief, ActionState]) -> StrategyResult | ModeDirective | None:
        with snapshot.read() as memory:
            belief = memory.belief
            position = belief.position
            target = belief.target
        if position == target:
            return ModeDirective(mode="idle", source="strategy", reason="arrived")
        return ModeDirective(
            mode="move_to",
            params=MoveParams(target=target),
            source="strategy",
            ttl_ticks=3,
            reason="target is not reached",
        )


def perceive(observation: Observation, tick: int) -> Percept:
    return Percept(position=observation.position, target=observation.target, tick=tick)


def update_belief(belief: Belief, percept: Percept) -> None:
    belief.position = percept.position
    belief.target = percept.target


def resolve_action(intent: ActionIntent, belief: Belief, action_state: ActionState) -> ActionCommand:
    if intent.semantic != "move_to" or intent.target is None:
        action_state.last_action = "noop"
        return ActionCommand()

    target = intent.target[0]
    if belief.position < target:
        action = "right"
    elif belief.position > target:
        action = "left"
    else:
        action = "noop"
    action_state.last_action = action
    return ActionCommand(action=action)


def build_runtime(
    trace: ListTraceSink,
) -> AgentRuntime[Observation, Percept, Belief, ActionState, ActionIntent, ActionCommand]:
    registry: ModeRegistry[Belief, ActionState, ActionIntent] = ModeRegistry()
    registry.register(IdleMode)
    registry.register(MoveToMode)
    return AgentRuntime(
        belief=Belief(),
        action_state=ActionState(),
        perceive=perceive,
        update_belief=update_belief,
        resolve_action=resolve_action,
        mode_registry=registry,
        default_directive=ModeDirective(mode="idle", source="default"),
        strategy_runner=SynchronousStrategyRunner(ToyStrategy()),
        trace_sink=trace,
    )


def main() -> None:
    trace = ListTraceSink()
    runtime = build_runtime(trace)
    position = 0
    target = 3
    for _ in range(6):
        command = runtime.step(Observation(position=position, target=target))
        print(f"pos={position} command={command.action} mode={runtime.active_mode_name}")
        if command.action == "right":
            position += 1
        elif command.action == "left":
            position -= 1
    runtime.close()
    print("trace:", ", ".join(trace.names()))


if __name__ == "__main__":
    main()
