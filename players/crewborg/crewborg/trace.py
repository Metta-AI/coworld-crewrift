"""Crewborg trace selection helpers.

The SDK's ``TraceOutputs`` owns output formats and destinations (stderr/stdout/
files/the player artifact zip — see ``players.player_sdk.trace_outputs``); this
module owns Crewborg's event families and the environment-derived filtering
rules that decide *which* events flow to those outputs.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase

from players.player_sdk.trace import TraceEvent

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
        "domain.commander_started",
        "domain.commander_call_start",
        "domain.commander_call",
        "domain.commander_stopped",
        "domain.commander_applied",
        "domain.commander_trace_dropped",
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
    "commander": ("domain.commander_*",),
    "debug": (
        "domain.decision_snapshot",
        "domain.suspicion_tick",
        "domain.kill_state",
        "domain.occupancy_snapshot",
        "domain.commander_*",
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
