"""Trace and metrics sinks: tee fan-out + newline-delimited JSON to stderr.

The Coworld contract is **stdout = protocol channel, stderr = logs/traces**
(design §11, AGENTS.md §"Packaging"). These sinks satisfy the SDK's ``TraceSink``
and ``MetricsSink`` protocols (:mod:`players.player_sdk.trace`). The stderr-JSON
sinks write one JSON object per line so a log collector can parse them without
touching the protocol stream; they are opt-in via the ``CREWBORG_TRACE*`` envs.
The primary sink is the SQLite episode recorder uploaded as the player debug
artifact (:mod:`players.crewrift.crewborg.artifact`); ``TeeTraceSink`` /
``TeeMetricsSink`` fan one stream out to both.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, TextIO

from players.player_sdk.trace import MetricSample, TraceEvent

TraceFilter = Callable[[TraceEvent], bool]

TRACE_LEVEL_ENV = "CREWBORG_TRACE"
TRACE_GROUPS_ENV = "CREWBORG_TRACE_GROUPS"
TRACE_INCLUDE_ENV = "CREWBORG_TRACE_INCLUDE"
TRACE_EXCLUDE_ENV = "CREWBORG_TRACE_EXCLUDE"
TRACE_DECISION_FIELDS_ENV = "CREWBORG_TRACE_DECISION_FIELDS"

LOW_VOLUME_FRAMEWORK_EVENTS = frozenset(
    {
        "mode_entered",
        "mode_exited",
        "mode_completed",
        "mode_stalled",
        "directive_rejected",
        "strategy_inferences",
    }
)

NOISY_DOMAIN_EVENTS = frozenset(
    {
        "domain.decision_snapshot",
        "domain.viewer_frame",
        "domain.viewer_map",
        "domain.viewer_occupancy_grid",
        "domain.suspicion_tick",
        "domain.kill_state",
        "domain.occupancy_snapshot",
    }
)

TRACE_GROUP_PATTERNS: dict[str, tuple[str, ...]] = {
    "action": (
        "action_intent",
        "act_command",
        "domain.task_started",
        "domain.kill_attempted",
        "domain.report_attempted",
        "domain.vent_attempted",
        "domain.chat_sent",
        "domain.vote_cast",
    ),
    "all": ("*",),
    "belief": (
        "domain.role_resolved",
        "domain.body_sighted",
        "domain.player_*",
        "domain.imposter_confirmed",
        "domain.believed_changed",
        "domain.suspicion_*",
        "domain.kill_ready_changed",
        "domain.kill_state",
        "domain.occupancy_*",
        "domain.decision_snapshot",
    ),
    "chat": ("domain.chat_*", "domain.meeting_chat_*"),
    "debug": (
        "domain.decision_snapshot",
        "domain.suspicion_tick",
        "domain.kill_state",
        "domain.occupancy_snapshot",
    ),
    "decision": ("domain.decision_snapshot",),
    "framework": (
        "perception",
        "belief_updated",
        "action_intent",
        "act_command",
        "snapshot_submitted",
        "strategy_evaluated",
        "directive_*",
        "fallback_activated",
        "mode_*",
    ),
    "kill": ("domain.kill_*",),
    "knowledge": (
        "domain.player_event",
        "domain.player_died",
        "domain.imposter_confirmed",
        "domain.believed_changed",
        "domain.suspicion_snapshot",
    ),
    "llm": (
        "domain.meeting_context_serialized",
        "domain.meeting_llm_*",
        "domain.meeting_tentative_*",
        "domain.meeting_chat_selected",
        "domain.meeting_vote_selected",
    ),
    "meeting": (
        "domain.vote_cast",
        "domain.chat_*",
        "domain.suspicion_snapshot",
        "domain.meeting_*",
    ),
    "mode": ("mode_*", "directive_*", "fallback_activated", "strategy_*", "snapshot_submitted"),
    "occupancy": ("domain.occupancy_*", "domain.viewer_occupancy_grid"),
    "state": (
        "domain.phase_change",
        "domain.role_resolved",
        "domain.body_sighted",
        "domain.task_completed",
        "domain.kill_landed",
        "domain.player_died",
    ),
    "suspicion": ("domain.imposter_confirmed", "domain.believed_changed", "domain.suspicion_*"),
    "task": ("domain.task_*",),
    "viewer": ("domain.viewer_*",),
    "voting": (
        "domain.vote_cast",
        "domain.chat_*",
        "domain.suspicion_snapshot",
        "domain.meeting_*",
    ),
}


@dataclass(frozen=True)
class TraceConfig:
    """Environment-derived trace targeting configuration."""

    level: str = ""
    groups: frozenset[str] = frozenset()
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    decision_fields: tuple[str, ...] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TraceConfig:
        source = os.environ if env is None else env
        decision_fields = _split_tokens(source.get(TRACE_DECISION_FIELDS_ENV, ""))
        return cls(
            level=source.get(TRACE_LEVEL_ENV, "").strip().lower(),
            groups=frozenset(_split_tokens(source.get(TRACE_GROUPS_ENV, ""))),
            include_patterns=_parse_patterns(source.get(TRACE_INCLUDE_ENV, "")),
            exclude_patterns=_parse_patterns(source.get(TRACE_EXCLUDE_ENV, "")),
            decision_fields=decision_fields or None,
        )

    @property
    def has_targets(self) -> bool:
        return bool(self.groups or self.include_patterns)

    @property
    def is_full_stream(self) -> bool:
        return self.level in {"debug", "viewer"} and not self.has_targets and not self.exclude_patterns

    def allows(self, event: TraceEvent) -> bool:
        name = event.name.lower()
        if self.has_targets:
            allowed = self._matches_group(name) or _matches_any(name, self.include_patterns)
        elif self.level in {"debug", "viewer"}:
            allowed = True
        else:
            allowed = lean_trace_filter(event)
        return allowed and not self.excludes_event(name)

    def targets_event(self, event_name: str) -> bool:
        name = event_name.lower()
        return (self._matches_group(name) or _matches_any(name, self.include_patterns)) and not self.excludes_event(name)

    def excludes_event(self, event_name: str) -> bool:
        return _matches_any(event_name.lower(), self.exclude_patterns)

    def _matches_group(self, event_name: str) -> bool:
        return any(_group_matches(group, event_name) for group in self.groups)


def lean_trace_filter(event: TraceEvent) -> bool:
    """Return whether an event belongs in the hosted default log stream.

    Hosted policy logs are capped, so the default stream must preserve durable
    game events and suppress per-tick SDK/viewer/debug payloads. Full framework
    traces remain available with ``CREWBORG_TRACE=debug``.
    """

    if event.name in LOW_VOLUME_FRAMEWORK_EVENTS:
        return True
    if not event.name.startswith("domain."):
        return False
    return event.name not in NOISY_DOMAIN_EVENTS


def _group_matches(group: str, event_name: str) -> bool:
    if group == "lean":
        return event_name in LOW_VOLUME_FRAMEWORK_EVENTS or (
            event_name.startswith("domain.") and event_name not in NOISY_DOMAIN_EVENTS
        )
    patterns = TRACE_GROUP_PATTERNS.get(group, ())
    return _matches_any(event_name, patterns)


def _matches_any(event_name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(event_name, pattern) for pattern in patterns)


def _parse_patterns(raw: str) -> tuple[str, ...]:
    patterns: list[str] = []
    for token in _split_tokens(raw):
        patterns.append(token)
        if "." not in token:
            patterns.append(f"domain.{token}")
    return tuple(patterns)


def _split_tokens(raw: str) -> tuple[str, ...]:
    return tuple(part for chunk in raw.replace(";", ",").split(",") for part in chunk.lower().split() if part)


class TeeTraceSink:
    """Fan one trace stream out to several sinks (``None`` entries are skipped)."""

    def __init__(self, *sinks: Any) -> None:
        self._sinks = tuple(sink for sink in sinks if sink is not None)

    def record(self, event: TraceEvent) -> None:
        for sink in self._sinks:
            sink.record(event)


class TeeMetricsSink:
    """Fan metric samples out to several sinks (``None`` entries are skipped)."""

    def __init__(self, *sinks: Any) -> None:
        self._sinks = tuple(sink for sink in sinks if sink is not None)

    def counter(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        for sink in self._sinks:
            sink.counter(name, value, tags)

    def histogram(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        for sink in self._sinks:
            sink.histogram(name, value, tags)

    def gauge(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        for sink in self._sinks:
            sink.gauge(name, value, tags)


class StderrJsonTraceSink:
    """Trace sink writing one JSON line per event to stderr."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        event_filter: TraceFilter | None = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._event_filter = event_filter

    def record(self, event: TraceEvent) -> None:
        if self._event_filter is not None and not self._event_filter(event):
            return
        line = json.dumps(
            {
                "kind": "trace",
                "tick": event.tick,
                "event": event.name,
                "data": event.data,
            },
            default=str,
        )
        print(line, file=self._stream, flush=True)

    @classmethod
    def from_env(cls, stream: TextIO | None = None) -> StderrJsonTraceSink:
        config = TraceConfig.from_env()
        if config.is_full_stream:
            return cls(stream)
        return cls(stream, event_filter=config.allows)


class StderrJsonMetricsSink:
    """Metrics sink writing one JSON line per sample to stderr."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr

    def _emit(self, sample: MetricSample) -> None:
        line = json.dumps(
            {
                "kind": "metric",
                "metric_kind": sample.kind,
                "name": sample.name,
                "value": sample.value,
                "tags": sample.tags,
            },
            default=str,
        )
        print(line, file=self._stream, flush=True)

    def counter(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        self._emit(MetricSample(kind="counter", name=name, value=value, tags=dict(tags or {})))

    def histogram(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self._emit(MetricSample(kind="histogram", name=name, value=value, tags=dict(tags or {})))

    def gauge(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        self._emit(MetricSample(kind="gauge", name=name, value=value, tags=dict(tags or {})))
