"""Trace/metrics sink tests: newline-delimited JSON to a stream (design §11)."""

from __future__ import annotations

import io
import json

from players.crewrift.crewborg.trace import (
    StderrJsonMetricsSink,
    StderrJsonTraceSink,
    TraceConfig,
    lean_trace_filter,
)
from players.player_sdk.trace import TraceEvent


def test_trace_sink_writes_one_json_line_per_event() -> None:
    stream = io.StringIO()
    sink = StderrJsonTraceSink(stream)

    sink.record(TraceEvent(tick=1, name="mode_entered", data={"mode": "idle"}))
    sink.record(TraceEvent(tick=2, name="act_command", data={}))

    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first == {
        "kind": "trace",
        "tick": 1,
        "event": "mode_entered",
        "data": {"mode": "idle"},
    }


def test_lean_trace_filter_keeps_durable_events_and_drops_tick_noise() -> None:
    assert not lean_trace_filter(TraceEvent(tick=1, name="perception", data={}))
    assert not lean_trace_filter(TraceEvent(tick=1, name="directive_reaffirmed", data={}))
    assert not lean_trace_filter(TraceEvent(tick=1, name="domain.decision_snapshot", data={}))
    assert not lean_trace_filter(TraceEvent(tick=1, name="domain.viewer_frame", data={}))

    assert lean_trace_filter(TraceEvent(tick=1, name="mode_entered", data={}))
    assert lean_trace_filter(TraceEvent(tick=1, name="domain.phase_change", data={}))
    assert lean_trace_filter(TraceEvent(tick=1, name="domain.meeting_vote_selected", data={}))
    assert lean_trace_filter(TraceEvent(tick=1, name="domain.vote_cast", data={}))


def test_trace_sink_can_filter_events() -> None:
    stream = io.StringIO()
    sink = StderrJsonTraceSink(stream, event_filter=lean_trace_filter)

    sink.record(TraceEvent(tick=1, name="perception", data={}))
    sink.record(TraceEvent(tick=2, name="domain.phase_change", data={"to": "Voting"}))

    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [record["event"] for record in records] == ["domain.phase_change"]


def test_trace_sink_env_factory_defaults_to_lean_stream(monkeypatch) -> None:
    monkeypatch.delenv("CREWBORG_TRACE", raising=False)
    stream = io.StringIO()
    sink = StderrJsonTraceSink.from_env(stream)

    sink.record(TraceEvent(tick=1, name="perception", data={}))
    sink.record(TraceEvent(tick=2, name="domain.decision_snapshot", data={}))
    sink.record(TraceEvent(tick=3, name="domain.meeting_vote_selected", data={}))

    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [record["event"] for record in records] == ["domain.meeting_vote_selected"]


def test_trace_sink_env_factory_debug_keeps_full_stream(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_TRACE", "debug")
    stream = io.StringIO()
    sink = StderrJsonTraceSink.from_env(stream)

    sink.record(TraceEvent(tick=1, name="perception", data={}))
    sink.record(TraceEvent(tick=2, name="domain.decision_snapshot", data={}))

    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [record["event"] for record in records] == ["perception", "domain.decision_snapshot"]


def test_trace_group_targets_action_events_and_framework_action_boundaries() -> None:
    config = TraceConfig.from_env({"CREWBORG_TRACE_GROUPS": "action"})

    assert config.allows(TraceEvent(tick=1, name="action_intent", data={}))
    assert config.allows(TraceEvent(tick=1, name="act_command", data={}))
    assert config.allows(TraceEvent(tick=1, name="domain.kill_attempted", data={}))
    assert config.allows(TraceEvent(tick=1, name="domain.vote_cast", data={}))
    assert not config.allows(TraceEvent(tick=1, name="domain.phase_change", data={}))


def test_trace_include_and_exclude_patterns_accept_domain_shorthand() -> None:
    config = TraceConfig.from_env(
        {
            "CREWBORG_TRACE_INCLUDE": "meeting_*, vote_cast",
            "CREWBORG_TRACE_EXCLUDE": "meeting_context_serialized",
        }
    )

    assert config.allows(TraceEvent(tick=1, name="domain.meeting_vote_selected", data={}))
    assert config.allows(TraceEvent(tick=1, name="domain.vote_cast", data={}))
    assert not config.allows(TraceEvent(tick=1, name="domain.meeting_context_serialized", data={}))
    assert not config.allows(TraceEvent(tick=1, name="domain.phase_change", data={}))


def test_debug_trace_can_still_exclude_noisy_families(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_TRACE", "debug")
    monkeypatch.setenv("CREWBORG_TRACE_EXCLUDE", "domain.viewer_*,domain.decision_snapshot")
    stream = io.StringIO()
    sink = StderrJsonTraceSink.from_env(stream)

    sink.record(TraceEvent(tick=1, name="perception", data={}))
    sink.record(TraceEvent(tick=2, name="domain.viewer_frame", data={}))
    sink.record(TraceEvent(tick=3, name="domain.decision_snapshot", data={}))
    sink.record(TraceEvent(tick=4, name="domain.meeting_vote_selected", data={}))

    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [record["event"] for record in records] == ["perception", "domain.meeting_vote_selected"]


def test_metrics_sink_records_each_sample_kind() -> None:
    stream = io.StringIO()
    sink = StderrJsonMetricsSink(stream)

    sink.counter("cyborg.mode.ran", tags={"mode": "idle"})
    sink.histogram("cyborg.step.latency_ms", 1.5)
    sink.gauge("cyborg.directive.age_ticks", 3)

    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [r["metric_kind"] for r in records] == ["counter", "histogram", "gauge"]
    assert records[0]["name"] == "cyborg.mode.ran"
    assert records[0]["tags"] == {"mode": "idle"}
    assert records[1]["value"] == 1.5
