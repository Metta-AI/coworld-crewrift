"""Crewborg — a Player-SDK agent that plays Crewrift.

``build_runtime`` assembles the ``AgentRuntime`` from crewborg's six type
parameters, three pure functions, modes, and the rule-based strategy. See
``design.md`` for the full architecture and ``AGENTS.md`` for orientation.
"""

from __future__ import annotations

import os
from typing import Protocol

from crewborg.agent_tracking import update_agent_tracking
from crewborg.action import resolve_action
from crewborg.events import CrewborgEventTracer
from crewborg.map import MapData, load_croatoan_map
from crewborg.modes import (
    AccuseMode,
    AttendMeetingMode,
    EvadeMode,
    HuntMode,
    IdleMode,
    NormalMode,
    ReconMode,
    ReportBodyMode,
    SearchMode,
)
from crewborg.strategy import (
    RuleBasedStrategy,
    update_event_log,
    update_social_evidence,
    update_suspicion,
)
from crewborg.strategy.commander.llm import build_commander_client_from_env, commander_feature_enabled
from crewborg.strategy.commander.strategy import CommanderStrategy, apply_commander_inferences
from crewborg.strategy.commander.trace import CommanderTrace
from crewborg.strategy.commander.worker import CommanderWorker
from crewborg.strategy.meeting import chat_nlp
from crewborg.types import (
    ActionState,
    Belief,
    Command,
    Intent,
    Observation,
    Percept,
    perceive,
    update_belief,
)
from players.player_sdk import (
    AgentRuntime,
    MetricsSink,
    ModeDirective,
    ModeRegistry,
    SynchronousStrategyRunner,
    TraceSink,
)

__all__ = ["build_runtime"]


class _CloseableStrategy(Protocol):
    def close(self) -> None: ...


class CloseAwareSynchronousStrategyRunner(SynchronousStrategyRunner[Belief, ActionState]):
    """Sync runner that also closes the wrapped strategy if it owns resources."""

    def __init__(self, strategy: _CloseableStrategy, **kwargs) -> None:
        super().__init__(strategy, **kwargs)
        self._closeable_strategy = strategy

    def close(self) -> None:
        self._closeable_strategy.close()
        super().close()


def build_runtime(
    *,
    trace_sink: TraceSink | None = None,
    metrics_sink: MetricsSink | None = None,
    map_data: MapData | None = None,
) -> AgentRuntime[Observation, Percept, Belief, ActionState, Intent, Command]:
    """Assemble the crewborg ``AgentRuntime``.

    The inner loop runs ``perceive -> update_belief (+ agent tracking + event log
    + suspicion) -> mode.decide -> resolve_action`` each tick; the rule-based
    strategy publishes mode directives via ``SynchronousStrategyRunner``. The
    per-agent location tracker, per-player event log (design §5.2), and suspicion
    scoring (§10.1) are folded into belief right after perception so the strategy
    snapshot sees current search and ``believed_imposters`` state. The static map
    is baked once here (design §6) — ``map_data`` overrides the vendored
    ``croatoan`` bake (tests).
    Registers all modes: idle / normal / attend_meeting / report_body / accuse
    (crewmate) and evade / pretend / search / hunt (imposter). A ``CrewborgEventTracer``
    is wired as the runtime's ``on_step_complete`` hook so crewborg emits its
    ``domain.*`` trace events through the configured sinks (design §11): the
    phase / sighting / objective / kill / vote outcomes *and* the knowledge layer
    behind them (per-player event log + suspicion posteriors, with a
    ``suspicion_snapshot`` each meeting). ``CREWBORG_TRACE=debug`` adds the full
    per-tick dump; ``CREWBORG_TRACE_GROUPS`` / ``CREWBORG_TRACE_INCLUDE`` can
    target narrower event families without full debug volume.
    """

    # Kick off the spaCy chat-NLP model load in the background now (gated by
    # CREWBORG_CHAT_NLP), so the ~1.5-2s load overlaps the pre-game idle phases and is
    # ready before the first meeting — never on the gameplay hot path (design §10.5).
    chat_nlp.ensure_loading()

    registry: ModeRegistry[Belief, ActionState, Intent] = ModeRegistry()
    registry.register(IdleMode)
    registry.register(NormalMode)
    registry.register(AttendMeetingMode)
    registry.register(ReportBodyMode)
    registry.register(AccuseMode)
    registry.register(EvadeMode)
    registry.register(HuntMode)
    registry.register(ReconMode)
    registry.register(SearchMode)

    if map_data is None:
        map_data = load_croatoan_map()

    def fold_belief(belief: Belief, percept: Percept) -> None:
        """Fast-loop belief update: perception, tracking, event log, social evidence, suspicion."""

        update_belief(belief, percept)
        update_agent_tracking(belief)
        update_event_log(belief)
        update_social_evidence(belief)
        update_suspicion(belief)

    commander_trace = CommanderTrace()
    feature_on = commander_feature_enabled(dict(os.environ))
    commander_strategy = CommanderStrategy(
        RuleBasedStrategy(),
        CommanderWorker(build_commander_client_from_env, trace=commander_trace),
        feature_enabled=feature_on,
    )

    return AgentRuntime(
        belief=Belief(map=map_data),
        action_state=ActionState(),
        perceive=perceive,
        update_belief=fold_belief,
        resolve_action=resolve_action,
        mode_registry=registry,
        default_directive=ModeDirective(mode="idle", source="default", reason="default idle"),
        strategy_runner=CloseAwareSynchronousStrategyRunner(
            commander_strategy,
            trace_sink=trace_sink,
            metrics_sink=metrics_sink,
        ),
        apply_inferences=apply_commander_inferences,
        on_step_complete=CrewborgEventTracer(commander_trace=commander_trace),
        trace_sink=trace_sink,
        metrics_sink=metrics_sink,
    )
