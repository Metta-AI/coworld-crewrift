"""Trace selection tests: Crewborg's event families and env-derived filtering.

Output formats/destinations (stderr/files/artifact zip) are the SDK's
``TraceOutputs`` and are covered by the players repo; these tests cover only the
Crewborg-owned selection rules that feed it (design §11).
"""

from __future__ import annotations

from crewborg.trace import TraceConfig, lean_trace_filter
from players.player_sdk.trace import TraceEvent


def test_lean_trace_filter_keeps_durable_events_and_drops_tick_noise() -> None:
    assert not lean_trace_filter(TraceEvent(tick=1, name="perception", data={}))
    assert not lean_trace_filter(TraceEvent(tick=1, name="directive_reaffirmed", data={}))
    assert not lean_trace_filter(TraceEvent(tick=1, name="domain.decision_snapshot", data={}))
    assert not lean_trace_filter(TraceEvent(tick=1, name="domain.viewer_frame", data={}))

    assert lean_trace_filter(TraceEvent(tick=1, name="mode_entered", data={}))
    assert lean_trace_filter(TraceEvent(tick=1, name="domain.phase_change", data={}))
    assert lean_trace_filter(TraceEvent(tick=1, name="domain.meeting_vote_selected", data={}))
    assert lean_trace_filter(TraceEvent(tick=1, name="domain.vote_cast", data={}))


def test_trace_config_defaults_to_lean_stream() -> None:
    config = TraceConfig.from_env({})

    assert not config.allows(TraceEvent(tick=1, name="perception", data={}))
    assert not config.allows(TraceEvent(tick=2, name="domain.decision_snapshot", data={}))
    assert config.allows(TraceEvent(tick=3, name="domain.meeting_vote_selected", data={}))


def test_trace_config_debug_keeps_full_stream() -> None:
    config = TraceConfig.from_env({"CREWBORG_TRACE": "debug"})

    assert config.allows(TraceEvent(tick=1, name="perception", data={}))
    assert config.allows(TraceEvent(tick=2, name="domain.decision_snapshot", data={}))


def test_trace_group_targets_action_events_and_framework_action_boundaries() -> None:
    config = TraceConfig.from_env({"CREWBORG_TRACE_GROUPS": "action"})

    assert config.allows(TraceEvent(tick=1, name="action_intent", data={}))
    assert config.allows(TraceEvent(tick=1, name="act_command", data={}))
    assert config.allows(TraceEvent(tick=1, name="domain.kill_attempted", data={}))
    assert config.allows(TraceEvent(tick=1, name="domain.vote_cast", data={}))
    assert not config.allows(TraceEvent(tick=1, name="domain.phase_change", data={}))


def test_trace_group_targets_commander_events() -> None:
    config = TraceConfig.from_env({"CREWBORG_TRACE_GROUPS": "commander"})

    assert config.allows(TraceEvent(tick=1, name="domain.commander_started", data={}))
    assert config.allows(TraceEvent(tick=1, name="domain.commander_call_start", data={}))
    assert config.allows(TraceEvent(tick=1, name="domain.commander_call", data={}))
    assert config.allows(TraceEvent(tick=1, name="domain.commander_stopped", data={}))
    assert config.allows(TraceEvent(tick=1, name="domain.commander_applied", data={}))
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


def test_debug_trace_can_still_exclude_noisy_families() -> None:
    config = TraceConfig.from_env(
        {
            "CREWBORG_TRACE": "debug",
            "CREWBORG_TRACE_EXCLUDE": "domain.viewer_*,domain.decision_snapshot",
        }
    )

    assert config.allows(TraceEvent(tick=1, name="perception", data={}))
    assert not config.allows(TraceEvent(tick=2, name="domain.viewer_frame", data={}))
    assert not config.allows(TraceEvent(tick=3, name="domain.decision_snapshot", data={}))
    assert config.allows(TraceEvent(tick=4, name="domain.meeting_vote_selected", data={}))
