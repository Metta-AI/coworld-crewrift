"""Domain-event emission CONTRACT tests (events.py).

These guard the ``domain.*`` event families that report/analysis tooling reads
to reconstruct an episode: phase segmentation, kills, deaths, and votes. We drive
the tracer directly with fabricated :class:`StepContext` values and assert each
family fires once on the right edge, carrying the keys consumers depend on. The
exhaustive per-event/observability plumbing is intentionally not covered here.
"""

from __future__ import annotations

from crewborg.events import CrewborgEventTracer
from crewborg.strategy.commander.trace import CommanderTrace
from crewborg.types import ActionState, Belief, Command, Intent
from players.player_sdk import EventEmitter, ListMetricsSink, ListTraceSink, ModeDirective, StepContext


class _Harness:
    """A tracer plus list sinks and a tick-advancing StepContext builder."""

    def __init__(
        self,
        *,
        debug: bool | None = None,
        viewer: bool = False,
        commander_trace: CommanderTrace | None = None,
    ) -> None:
        self.trace = ListTraceSink()
        self.metrics = ListMetricsSink()
        self.emit = EventEmitter(self.trace, self.metrics, tick=0)
        # Pin debug explicitly (default off) so an ambient CREWBORG_TRACE=debug in the
        # test environment can't perturb the lean-mode assertions.
        self.tracer = CrewborgEventTracer(debug=bool(debug), viewer=viewer, commander_trace=commander_trace)

    def step(
        self,
        *,
        belief: Belief | None = None,
        action_state: ActionState | None = None,
        intent: Intent | None = None,
        command: Command | None = None,
        active_directive: ModeDirective | None = None,
    ) -> None:
        self.emit.tick += 1
        directive = active_directive or ModeDirective(mode="test", source="test", reason="unit test")
        context: StepContext[Belief, ActionState, Intent, Command] = StepContext(
            tick=self.emit.tick,
            belief=belief if belief is not None else Belief(),
            action_state=action_state if action_state is not None else ActionState(),
            intent=intent if intent is not None else Intent(kind="idle"),
            command=command if command is not None else Command(),
            active_mode_name=directive.mode,
            active_directive=directive,
            emit=self.emit,
        )
        self.tracer(context)

    def events(self, name: str) -> list:
        return [event for event in self.trace.events if event.name == name]

    def counters(self, name: str) -> list:
        return [s for s in self.metrics.samples if s.name == name and s.kind == "counter"]

    def gauges(self, name: str) -> list:
        return [s for s in self.metrics.samples if s.name == name and s.kind == "gauge"]


def test_events_are_domain_prefixed_and_carry_runtime_tick() -> None:
    h = _Harness()
    belief = Belief(phase="Playing")
    h.step(belief=belief)  # tick 1: unknown -> Playing

    [event] = h.events("domain.phase_change")
    assert event.tick == 1
    assert event.data == {"from": "unknown", "to": "Playing"}


def test_kill_landed_on_cooldown_edge() -> None:
    h = _Harness()
    h.step(belief=Belief(self_role="imposter", last_kill_tick=None))
    belief = Belief(self_role="imposter", last_kill_tick=12, self_world_x=300, self_world_y=200)
    h.step(belief=belief)
    h.step(belief=belief)  # same kill tick: no re-emit

    [event] = h.events("domain.kill_landed")
    assert event.data == {"world_x": 300, "world_y": 200}
    assert len(h.counters("domain.kill_landed")) == 1


def test_vote_cast_fires_once_per_meeting() -> None:
    h = _Harness()
    h.step(action_state=ActionState(vote_confirmed=False))
    h.step(action_state=ActionState(vote_confirmed=True))  # cast
    h.step(action_state=ActionState(vote_confirmed=True))  # still held: no re-emit
    h.step(action_state=ActionState(vote_confirmed=False))  # action layer reset (intent changed)
    h.step(action_state=ActionState(vote_confirmed=True))  # next meeting cast

    assert len(h.events("domain.vote_cast")) == 2
    assert len(h.counters("domain.vote_cast")) == 2


def test_player_died_fires_once_on_the_alive_to_dead_edge() -> None:
    from crewborg.types import PlayerRecord

    h = _Harness()
    belief = Belief()
    record = belief.roster["blue"] = PlayerRecord(color="blue", life_status="alive")
    h.step(belief=belief)  # alive: nothing
    record.mark_dead(tick=40, source="body", body_xy=(120, 80))
    h.step(belief=belief)  # edge
    h.step(belief=belief)  # still dead: no re-emit

    [event] = h.events("domain.player_died")
    assert event.data == {"color": "blue", "source": "body", "death_tick": 40, "body_xy": [120, 80]}
    assert len(h.counters("domain.player_died")) == 1
