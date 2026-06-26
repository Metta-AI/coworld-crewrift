from __future__ import annotations

import logging
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class TraceEvent(BaseModel):
    """One framework boundary event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tick: int
    name: str
    data: dict[str, Any] = Field(default_factory=dict)


class TraceSink(Protocol):
    """Trace sink protocol used by the runtime and strategy runners."""

    def record(self, event: TraceEvent) -> None: ...


class MetricSample(BaseModel):
    """One counter, histogram, or gauge observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["counter", "histogram", "gauge"]
    name: str
    value: float
    tags: dict[str, Any] = Field(default_factory=dict)


class MetricsSink(Protocol):
    """Metrics sink protocol for production monitoring adapters."""

    def counter(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None: ...

    def histogram(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None: ...

    def gauge(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None: ...


DOMAIN_EVENT_PREFIX = "domain."


class EventEmitter:
    """Domain-event emitter bound to the runtime's current tick.

    Emitters are intended for use on the runtime's single-threaded inner loop.
    Unqualified names are prefixed with ``domain.`` so game-specific events stay
    distinguishable from framework events without changing the canonical
    framework event names.
    """

    def __init__(
        self,
        trace_sink: TraceSink | None = None,
        metrics_sink: MetricsSink | None = None,
        *,
        tick: int = 0,
    ) -> None:
        self.trace_sink = trace_sink if trace_sink is not None else NullTraceSink()
        self.metrics_sink = metrics_sink if metrics_sink is not None else NullMetricsSink()
        self.tick = tick

    def event(self, name: str, data: dict[str, Any] | None = None) -> None:
        """Emit a domain trace event at the current runtime tick."""

        self.trace_sink.record(TraceEvent(tick=self.tick, name=self._name(name), data=dict(data or {})))

    def counter(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        """Emit a domain counter sample."""

        self.metrics_sink.counter(self._name(name), value, self._tags(tags))

    def gauge(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        """Emit a domain gauge sample."""

        self.metrics_sink.gauge(self._name(name), value, self._tags(tags))

    def histogram(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        """Emit a domain histogram sample."""

        self.metrics_sink.histogram(self._name(name), value, self._tags(tags))

    def _name(self, name: str) -> str:
        if name.startswith(DOMAIN_EVENT_PREFIX):
            return name
        return f"{DOMAIN_EVENT_PREFIX}{name}"

    def _tags(self, tags: dict[str, Any] | None) -> dict[str, Any]:
        return dict(tags or {})


class NullTraceSink:
    """Trace sink that drops events."""

    def record(self, event: TraceEvent) -> None:
        del event


class ListTraceSink:
    """In-memory trace sink for tests and small examples."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def record(self, event: TraceEvent) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [event.name for event in self.events]


class LoggingTraceSink:
    """Structured trace sink backed by ``logging``."""

    def __init__(self, logger: logging.Logger | None = None, *, level: int = logging.INFO) -> None:
        self.logger = logger if logger is not None else logging.getLogger("players.player_sdk")
        self.level = level

    def record(self, event: TraceEvent) -> None:
        self.logger.log(
            self.level,
            "cyborg_trace",
            extra={"cyborg_trace": event.model_dump(mode="json")},
        )


class NullMetricsSink:
    """Metrics sink that drops samples."""

    def counter(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        del name, value, tags

    def histogram(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        del name, value, tags

    def gauge(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        del name, value, tags


class ListMetricsSink:
    """In-memory metrics sink for tests and small examples."""

    def __init__(self) -> None:
        self.samples: list[MetricSample] = []

    def counter(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        self.samples.append(MetricSample(kind="counter", name=name, value=value, tags=dict(tags or {})))

    def histogram(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self.samples.append(MetricSample(kind="histogram", name=name, value=value, tags=dict(tags or {})))

    def gauge(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self.samples.append(MetricSample(kind="gauge", name=name, value=value, tags=dict(tags or {})))


class LoggingMetricsSink:
    """Structured metrics sink backed by ``logging``."""

    def __init__(self, logger: logging.Logger | None = None, *, level: int = logging.INFO) -> None:
        self.logger = logger if logger is not None else logging.getLogger("players.player_sdk.metrics")
        self.level = level

    def counter(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        self._record(MetricSample(kind="counter", name=name, value=value, tags=dict(tags or {})))

    def histogram(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self._record(MetricSample(kind="histogram", name=name, value=value, tags=dict(tags or {})))

    def gauge(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self._record(MetricSample(kind="gauge", name=name, value=value, tags=dict(tags or {})))

    def _record(self, sample: MetricSample) -> None:
        self.logger.log(
            self.level,
            "cyborg_metric",
            extra={"cyborg_metric": sample.model_dump(mode="json")},
        )


class WandbMetricsSink:
    """Metrics sink adapter for a W&B run-like object with ``log``."""

    def __init__(self, run: Any) -> None:
        self.run = run

    def counter(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        self._log(MetricSample(kind="counter", name=name, value=value, tags=dict(tags or {})))

    def histogram(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self._log(MetricSample(kind="histogram", name=name, value=value, tags=dict(tags or {})))

    def gauge(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self._log(MetricSample(kind="gauge", name=name, value=value, tags=dict(tags or {})))

    def _log(self, sample: MetricSample) -> None:
        payload = {sample.name: sample.value}
        payload.update({f"{sample.name}.{key}": value for key, value in sample.tags.items()})
        self.run.log(payload)
