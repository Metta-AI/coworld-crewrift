from __future__ import annotations

import asyncio
import threading
from time import perf_counter
from typing import Protocol, TypeVar

from players.player_sdk.buffers import OverwriteBuffer
from players.player_sdk.trace import MetricsSink, NullMetricsSink, NullTraceSink, TraceEvent, TraceSink
from players.player_sdk.types import BeliefSnapshot, ModeDirective, StrategyResult

BeliefT = TypeVar("BeliefT")
ActionStateT = TypeVar("ActionStateT")


class Strategy(Protocol[BeliefT, ActionStateT]):
    """Strategy object that maps belief snapshots to directives."""

    def decide(self, snapshot: BeliefSnapshot[BeliefT, ActionStateT]) -> StrategyResult | ModeDirective | None: ...


class AsyncStrategy(Protocol[BeliefT, ActionStateT]):
    """Async strategy object that maps shared memory snapshots to directives."""

    async def decide(
        self, snapshot: BeliefSnapshot[BeliefT, ActionStateT]
    ) -> StrategyResult | ModeDirective | None: ...


class StrategyRunner(Protocol[BeliefT, ActionStateT]):
    """Runtime-facing wrapper around a strategy loop."""

    def observe(self, snapshot: BeliefSnapshot[BeliefT, ActionStateT]) -> None: ...

    def poll(self) -> StrategyResult | None: ...

    def close(self) -> None: ...


def normalize_strategy_result(
    result: StrategyResult | ModeDirective | None,
) -> StrategyResult | None:
    """Normalize strategy return values into ``StrategyResult``."""

    if result is None:
        return None
    if isinstance(result, StrategyResult):
        return result
    return StrategyResult(directive=result)


def _elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000.0


def _record_strategy_evaluation(
    *,
    runner: str,
    snapshot: BeliefSnapshot[BeliefT, ActionStateT],
    result: StrategyResult | None,
    started: float,
    trace_sink: TraceSink,
    metrics_sink: MetricsSink,
) -> None:
    tags = {
        "runner": runner,
        "wake_reason": snapshot.wake_reason,
        "has_directive": result is not None and result.directive is not None,
    }
    metrics_sink.histogram("cyborg.strategy.decide_ms", _elapsed_ms(started), tags)
    trace_sink.record(
        TraceEvent(
            tick=snapshot.tick,
            name="strategy_evaluated",
            data={
                "runner": runner,
                "wake_reason": snapshot.wake_reason,
                "has_result": result is not None,
                "has_directive": result is not None and result.directive is not None,
            },
        )
    )


class ManualStrategyRunner(StrategyRunner[BeliefT, ActionStateT]):
    """Runner whose directives are manually published by tests or callers."""

    def __init__(self) -> None:
        self._buffer: OverwriteBuffer[StrategyResult] = OverwriteBuffer()

    def publish(self, result: StrategyResult | ModeDirective) -> None:
        normalized = normalize_strategy_result(result)
        if normalized is not None:
            self._buffer.publish(normalized)

    def observe(self, snapshot: BeliefSnapshot[BeliefT, ActionStateT]) -> None:
        del snapshot

    def poll(self) -> StrategyResult | None:
        return self._buffer.take()

    def close(self) -> None:
        self._buffer.close()


class SynchronousStrategyRunner(StrategyRunner[BeliefT, ActionStateT]):
    """Cadence-limited strategy runner evaluated on the inner-loop thread."""

    def __init__(
        self,
        strategy: Strategy[BeliefT, ActionStateT],
        *,
        cadence_ticks: int = 1,
        trace_sink: TraceSink | None = None,
        metrics_sink: MetricsSink | None = None,
    ) -> None:
        self._strategy = strategy
        self._cadence_ticks = max(cadence_ticks, 1)
        self._last_eval_tick = -1
        self._pending: StrategyResult | None = None
        self._trace_sink = trace_sink if trace_sink is not None else NullTraceSink()
        self._metrics_sink = metrics_sink if metrics_sink is not None else NullMetricsSink()

    def observe(self, snapshot: BeliefSnapshot[BeliefT, ActionStateT]) -> None:
        if self._last_eval_tick < 0 or snapshot.tick - self._last_eval_tick >= self._cadence_ticks:
            self._last_eval_tick = snapshot.tick
            started = perf_counter()
            self._pending = normalize_strategy_result(self._strategy.decide(snapshot))
            _record_strategy_evaluation(
                runner="sync",
                snapshot=snapshot,
                result=self._pending,
                started=started,
                trace_sink=self._trace_sink,
                metrics_sink=self._metrics_sink,
            )

    def poll(self) -> StrategyResult | None:
        result = self._pending
        self._pending = None
        return result

    def close(self) -> None:
        self._pending = None


class ThreadedStrategyRunner(StrategyRunner[BeliefT, ActionStateT]):
    """Background strategy runner connected with latest-value buffers."""

    def __init__(
        self,
        strategy: Strategy[BeliefT, ActionStateT],
        *,
        name: str = "cyborg-strategy",
        wait_timeout: float = 0.05,
        trace_sink: TraceSink | None = None,
        metrics_sink: MetricsSink | None = None,
    ) -> None:
        self._strategy = strategy
        self._wait_timeout = wait_timeout
        self._snapshots: OverwriteBuffer[BeliefSnapshot[BeliefT, ActionStateT]] = OverwriteBuffer()
        self._results: OverwriteBuffer[StrategyResult] = OverwriteBuffer()
        self._trace_sink = trace_sink if trace_sink is not None else NullTraceSink()
        self._metrics_sink = metrics_sink if metrics_sink is not None else NullMetricsSink()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name=name)
        self._thread.start()

    def observe(self, snapshot: BeliefSnapshot[BeliefT, ActionStateT]) -> None:
        self._snapshots.publish(snapshot)

    def poll(self) -> StrategyResult | None:
        return self._results.take()

    def close(self) -> None:
        self._stop.set()
        self._snapshots.close()
        self._results.close()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            snapshot = self._snapshots.wait_take(timeout=self._wait_timeout)
            if snapshot is None:
                continue
            started = perf_counter()
            result = normalize_strategy_result(self._strategy.decide(snapshot))
            _record_strategy_evaluation(
                runner="threaded",
                snapshot=snapshot,
                result=result,
                started=started,
                trace_sink=self._trace_sink,
                metrics_sink=self._metrics_sink,
            )
            if result is not None:
                self._results.publish(result)


class AsyncStrategyRunner(StrategyRunner[BeliefT, ActionStateT]):
    """Event-loop strategy runner for async LLM-backed strategies."""

    def __init__(
        self,
        strategy: AsyncStrategy[BeliefT, ActionStateT],
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        cadence_ticks: int = 1,
        trace_sink: TraceSink | None = None,
        metrics_sink: MetricsSink | None = None,
    ) -> None:
        self._strategy = strategy
        self._loop = loop if loop is not None else asyncio.get_running_loop()
        self._cadence_ticks = max(cadence_ticks, 1)
        self._last_eval_tick = -1
        self._latest_snapshot: BeliefSnapshot[BeliefT, ActionStateT] | None = None
        self._pending: StrategyResult | None = None
        self._task: asyncio.Task[StrategyResult | None] | None = None
        self._closed = False
        self._error: BaseException | None = None
        self._trace_sink = trace_sink if trace_sink is not None else NullTraceSink()
        self._metrics_sink = metrics_sink if metrics_sink is not None else NullMetricsSink()

    def observe(self, snapshot: BeliefSnapshot[BeliefT, ActionStateT]) -> None:
        if self._closed:
            return
        if self._last_eval_tick >= 0 and snapshot.tick - self._last_eval_tick < self._cadence_ticks:
            return
        self._last_eval_tick = snapshot.tick
        self._latest_snapshot = snapshot
        if self._task is None or self._task.done():
            self._start_latest()

    def poll(self) -> StrategyResult | None:
        if self._error is not None:
            raise self._error
        result = self._pending
        self._pending = None
        return result

    def close(self) -> None:
        self._closed = True
        self._latest_snapshot = None
        self._pending = None
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _start_latest(self) -> None:
        snapshot = self._latest_snapshot
        if snapshot is None:
            return
        self._latest_snapshot = None
        self._task = self._loop.create_task(self._run_strategy(snapshot))
        self._task.add_done_callback(self._on_done)

    async def _run_strategy(self, snapshot: BeliefSnapshot[BeliefT, ActionStateT]) -> StrategyResult | None:
        started = perf_counter()
        result = normalize_strategy_result(await self._strategy.decide(snapshot))
        _record_strategy_evaluation(
            runner="async",
            snapshot=snapshot,
            result=result,
            started=started,
            trace_sink=self._trace_sink,
            metrics_sink=self._metrics_sink,
        )
        return result

    def _on_done(self, task: asyncio.Task[StrategyResult | None]) -> None:
        if self._closed or task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            self._error = exception
            return
        result = task.result()
        if result is not None:
            self._pending = result
        self._task = None
        self._start_latest()
