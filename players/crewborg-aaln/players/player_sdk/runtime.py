from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Generic, TypeAlias, TypeVar

from players.player_sdk.modes import Mode, ModeRegistry
from players.player_sdk.strategy import StrategyRunner
from players.player_sdk.trace import EventEmitter, MetricsSink, NullMetricsSink, NullTraceSink, TraceEvent, TraceSink
from players.player_sdk.types import (
    BeliefSnapshot,
    ModeDecision,
    ModeDecisionStatus,
    ModeDirective,
    SharedMemory,
    StrategyResult,
)

ObservationT = TypeVar("ObservationT")
PerceptT = TypeVar("PerceptT")
BeliefT = TypeVar("BeliefT")
ActionStateT = TypeVar("ActionStateT")
IntentT = TypeVar("IntentT")
CommandT = TypeVar("CommandT")


@dataclass(frozen=True)
class RuntimeContext(Generic[BeliefT, ActionStateT]):
    """Read-only context passed to reflex callbacks."""

    tick: int
    belief: BeliefT
    action_state: ActionStateT
    active_directive: ModeDirective
    active_mode_name: str


Reflex: TypeAlias = Callable[[RuntimeContext[BeliefT, ActionStateT]], ModeDirective | None]


@dataclass(frozen=True)
class StepContext(Generic[BeliefT, ActionStateT, IntentT, CommandT]):
    """Read-only end-of-tick context passed to game-specific observers."""

    tick: int
    belief: BeliefT
    action_state: ActionStateT
    intent: IntentT
    command: CommandT
    active_mode_name: str
    active_directive: ModeDirective
    emit: EventEmitter


StepCompleteHook: TypeAlias = Callable[[StepContext[BeliefT, ActionStateT, IntentT, CommandT]], None]


@dataclass(frozen=True)
class ReflexRule(Generic[BeliefT, ActionStateT]):
    """Priority-ordered reflex callback."""

    name: str
    priority: int
    callback: Reflex[BeliefT, ActionStateT]


class AgentRuntime(Generic[ObservationT, PerceptT, BeliefT, ActionStateT, IntentT, CommandT]):
    """Fast inner-loop runtime for cyborg agents.

    The runtime is intentionally game-agnostic. Game-specific code supplies
    perception, belief update, mode implementations, and action resolution.
    """

    def __init__(
        self,
        *,
        belief: BeliefT,
        action_state: ActionStateT,
        perceive: Callable[[ObservationT, int], PerceptT],
        update_belief: Callable[[BeliefT, PerceptT], None],
        resolve_action: Callable[[IntentT, BeliefT, ActionStateT], CommandT],
        mode_registry: ModeRegistry[BeliefT, ActionStateT, IntentT],
        default_directive: ModeDirective | Callable[[BeliefT], ModeDirective],
        strategy_runner: StrategyRunner[BeliefT, ActionStateT] | None = None,
        reflexes: Iterable[ReflexRule[BeliefT, ActionStateT]] = (),
        trace_sink: TraceSink | None = None,
        metrics_sink: MetricsSink | None = None,
        apply_inferences: Callable[[BeliefT, dict], None] | None = None,
        on_step_complete: StepCompleteHook[BeliefT, ActionStateT, IntentT, CommandT] | None = None,
    ) -> None:
        self.belief = belief
        self.action_state = action_state
        self.perceive = perceive
        self.update_belief = update_belief
        self.resolve_action = resolve_action
        self.mode_registry = mode_registry
        self.default_directive = default_directive
        self.strategy_runner = strategy_runner
        self.reflexes = tuple(
            rule
            for _, rule in sorted(
                enumerate(reflexes),
                key=lambda item: (-item[1].priority, item[0]),
            )
        )
        self.tick = 0
        self.trace_sink = trace_sink if trace_sink is not None else NullTraceSink()
        self.metrics_sink = metrics_sink if metrics_sink is not None else NullMetricsSink()
        self.emit = EventEmitter(self.trace_sink, self.metrics_sink, tick=self.tick)
        self.apply_inferences = apply_inferences
        self.on_step_complete = on_step_complete
        self.latest_inferences: dict = {}

        initial = self._default_directive().issued(self.tick)
        self.mode_registry.validate(initial)
        self.active_directive = initial
        self.active_mode = self.mode_registry.create(initial)
        self._attach_emitter(self.active_mode)
        self.active_mode_entered_at_tick = self.tick
        self.shared_memory = SharedMemory(
            belief=self.belief,
            action_state=self.action_state,
            active_directive=self.active_directive,
        )
        with self.shared_memory.write():
            self.active_mode.on_enter(self.belief, self.action_state)
        self._trace(
            "mode_entered",
            {
                "mode": self.active_directive.mode,
                "source": self.active_directive.source,
                "reason": "initial",
            },
        )

    @property
    def active_mode_name(self) -> str:
        return self.active_directive.mode

    def step(self, observation: ObservationT) -> CommandT:
        """Run one perception -> belief -> mode -> action tick."""

        step_started = perf_counter()
        self.tick += 1
        self.emit.tick = self.tick
        percept = self.perceive(observation, self.tick)
        self._trace("perception", {"percept_type": type(percept).__name__})

        with self.shared_memory.write():
            self.update_belief(self.belief, percept)
            self._trace("belief_updated", {"belief_type": type(self.belief).__name__})

            self._observe_strategy()
            self._consume_strategy_result()
            self._run_reflexes()
            self._reconcile_fallbacks()

            ran_mode_name = self.active_mode_name
            decision = self._normalize_mode_decision(self.active_mode.decide(self.belief, self.action_state))
            intent = decision.intent
            self._record_mode_metrics()
            self._trace(
                "action_intent",
                {
                    "mode": self.active_mode_name,
                    "intent_type": type(intent).__name__,
                    "intent": repr(intent),
                    "mode_status": decision.status,
                    "status_reason": decision.reason,
                },
            )

            command = self.resolve_action(intent, self.belief, self.action_state)
            self._trace(
                "act_command",
                {"command_type": type(command).__name__, "command": repr(command)},
            )
            self._handle_mode_status(decision)
            self._notify_step_complete(ran_mode_name, intent, command)

        self._histogram("cyborg.step.latency_ms", self._elapsed_ms(step_started), {"mode": ran_mode_name})
        return command

    def close(self) -> None:
        """Close the optional strategy runner."""

        if self.strategy_runner is not None:
            self.strategy_runner.close()

    def install_directive(self, directive: ModeDirective, *, reason: str) -> bool:
        """Validate and install a directive.

        Returns ``True`` when a directive was accepted. Reaffirming the current
        mode updates directive metadata but preserves the live mode instance.
        """

        error = self.mode_registry.validation_error(directive)
        if error is not None:
            self._trace(
                "directive_rejected",
                {"mode": directive.mode, "reason": reason, "error": error},
            )
            return False

        issued = directive.issued(self.tick)
        if self.active_mode.matches_directive(issued):
            self.active_directive = issued
            self.shared_memory.set_active_directive(issued)
            self._trace(
                "directive_reaffirmed",
                {
                    "mode": issued.mode,
                    "source": issued.source,
                    "reason": reason,
                },
            )
            return True

        old_mode = self.active_directive.mode
        self.active_mode.on_exit(self.belief, self.action_state, issued)
        self._trace(
            "mode_exited",
            {"old_mode": old_mode, "new_mode": issued.mode, "reason": reason},
        )

        self.active_directive = issued
        self.shared_memory.set_active_directive(issued)
        self.active_mode = self.mode_registry.create(issued)
        self._attach_emitter(self.active_mode)
        self.active_mode_entered_at_tick = self.tick
        self.active_mode.on_enter(self.belief, self.action_state)
        self._trace(
            "mode_entered",
            {
                "old_mode": old_mode,
                "mode": issued.mode,
                "source": issued.source,
                "reason": reason,
            },
        )
        return True

    def _observe_strategy(
        self,
        *,
        wake_reason: str = "tick",
        mode_status: ModeDecisionStatus = "running",
        mode_status_reason: str = "",
    ) -> None:
        if self.strategy_runner is None:
            return
        snapshot = BeliefSnapshot(
            tick=self.tick,
            memory=self.shared_memory,
            wake_reason=wake_reason,
            mode_status=mode_status,
            mode_status_reason=mode_status_reason,
        )
        started = perf_counter()
        self.strategy_runner.observe(snapshot)
        self._histogram(
            "cyborg.strategy.observe_ms",
            self._elapsed_ms(started),
            {"mode": self.active_mode_name, "wake_reason": wake_reason},
        )
        self._counter("cyborg.strategy.observed", tags={"mode": self.active_mode_name, "wake_reason": wake_reason})
        self._trace(
            "snapshot_submitted",
            {
                "mode": self.active_mode_name,
                "wake_reason": wake_reason,
                "mode_status": mode_status,
                "mode_status_reason": mode_status_reason,
            },
        )

    def _consume_strategy_result(self) -> None:
        if self.strategy_runner is None:
            return
        result = self.strategy_runner.poll()
        if result is None:
            return

        self._apply_strategy_inferences(result)
        self._counter("cyborg.strategy.result", tags={"has_directive": result.directive is not None})
        if result.directive is not None:
            self.install_directive(result.directive, reason="strategy")

    def _apply_strategy_inferences(self, result: StrategyResult) -> None:
        if not result.inferences:
            return
        self.latest_inferences = dict(result.inferences)
        if self.apply_inferences is not None:
            self.apply_inferences(self.belief, self.latest_inferences)
        self._trace(
            "strategy_inferences",
            {"keys": sorted(str(key) for key in self.latest_inferences)},
        )

    def _run_reflexes(self) -> None:
        if not self.reflexes:
            return
        context = RuntimeContext(
            tick=self.tick,
            belief=self.belief,
            action_state=self.action_state,
            active_directive=self.active_directive,
            active_mode_name=self.active_mode_name,
        )
        checks: list[dict[str, Any]] = []
        for reflex in self.reflexes:
            directive = reflex.callback(context)
            check: dict[str, Any] = {
                "name": reflex.name,
                "priority": reflex.priority,
                "fired": directive is not None,
            }
            checks.append(check)
            if directive is None:
                continue
            accepted = self.install_directive(directive, reason="reflex")
            check["accepted"] = accepted
            if accepted:
                self._trace(
                    "reflex_evaluated",
                    {
                        "winner": reflex.name,
                        "checks": checks,
                        "unchecked": [rule.name for rule in self.reflexes[len(checks) :]],
                    },
                )
                self._trace(
                    "reflex_fired",
                    {"name": reflex.name, "mode": directive.mode, "source": directive.source},
                )
                self._counter("cyborg.reflex.fired", tags={"name": reflex.name, "mode": directive.mode})
                return
        self._trace("reflex_evaluated", {"winner": None, "checks": checks, "unchecked": []})

    def _reconcile_fallbacks(self) -> None:
        if self.active_directive.expired_at(self.tick):
            self._counter("cyborg.fallback", tags={"reason": "ttl_expired", "mode": self.active_mode_name})
            self._trace("fallback_activated", {"reason": "ttl_expired", "mode": self.active_mode_name})
            self.install_directive(self._default_directive(), reason="ttl_expired")
            return

        if not self.active_mode.is_legal(self.belief):
            self._counter("cyborg.fallback", tags={"reason": "mode_illegal", "mode": self.active_mode_name})
            self._trace("fallback_activated", {"reason": "mode_illegal", "mode": self.active_mode_name})
            self.install_directive(self._default_directive(), reason="mode_illegal")

    def _default_directive(self) -> ModeDirective:
        if isinstance(self.default_directive, ModeDirective):
            return self.default_directive
        return self.default_directive(self.belief)

    def _trace(self, name: str, data: dict) -> None:
        self.trace_sink.record(TraceEvent(tick=self.tick, name=name, data=data))

    def _attach_emitter(self, mode: Mode[BeliefT, ActionStateT, IntentT]) -> None:
        mode.emit = self.emit

    def _normalize_mode_decision(self, result: IntentT | ModeDecision[IntentT]) -> ModeDecision[IntentT]:
        if isinstance(result, ModeDecision):
            return result
        return ModeDecision.running(result)

    def _notify_step_complete(self, mode_name: str, intent: IntentT, command: CommandT) -> None:
        if self.on_step_complete is None:
            return
        self.on_step_complete(
            StepContext(
                tick=self.tick,
                belief=self.belief,
                action_state=self.action_state,
                intent=intent,
                command=command,
                active_mode_name=mode_name,
                active_directive=self.active_directive,
                emit=self.emit,
            )
        )

    def _handle_mode_status(self, decision: ModeDecision[IntentT]) -> None:
        if decision.status == "running":
            return
        event_name = "mode_completed" if decision.status == "complete" else "mode_stalled"
        mode_name = self.active_mode_name
        self._trace(
            event_name,
            {
                "mode": mode_name,
                "reason": decision.reason,
                "metadata": decision.metadata,
            },
        )
        self._counter("cyborg.mode.status", tags={"mode": mode_name, "status": decision.status})
        self._trace("fallback_activated", {"reason": f"mode_{decision.status}", "mode": mode_name})
        self.install_directive(self._default_directive(), reason=f"mode_{decision.status}")
        self._observe_strategy(
            wake_reason=f"mode_{decision.status}",
            mode_status=decision.status,
            mode_status_reason=decision.reason,
        )

    def _record_mode_metrics(self) -> None:
        tags = {"mode": self.active_mode_name, "source": self.active_directive.source}
        self._counter("cyborg.mode.ran", tags=tags)
        self._gauge("cyborg.mode.duration_ticks", self.tick - self.active_mode_entered_at_tick, tags)
        self._gauge("cyborg.directive.age_ticks", self.tick - self.active_directive.issued_at_tick, tags)

    def _counter(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        self.metrics_sink.counter(name, value, tags)

    def _histogram(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self.metrics_sink.histogram(name, value, tags)

    def _gauge(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self.metrics_sink.gauge(name, value, tags)

    def _elapsed_ms(self, started: float) -> float:
        return (perf_counter() - started) * 1000.0
