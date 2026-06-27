"""Crewborg trace selection helpers.

The SDK's ``TraceOutputs`` owns output formats and destinations (stderr/stdout/
files/the player artifact zip — see ``players.player_sdk.trace_outputs``); this
module owns Crewborg's event families and the environment-derived filtering
rules that decide *which* events flow to those outputs.

The vocabulary here mirrors what ``events.py`` emits: this module names the groups
(``TRACE_GROUP_PATTERNS``) and the lean/noisy partitions, and ``TraceConfig`` turns
the ``CREWBORG_TRACE*`` env vars into a per-event allow decision.

Collaborators
-------------
Relies on:
  - ``players.player_sdk.trace.TraceEvent`` — the event whose ``.name`` is matched.
  - stdlib ``fnmatch`` (glob patterns) and ``os.environ`` (the ``CREWBORG_TRACE*`` vars).
Used by:
  - ``events.CrewborgEventTracer`` builds a ``TraceConfig.from_env`` to gate its
    optional/heavy event families (``targets_event`` / ``excludes_event``).
  - ``coworld.policy_player`` (the bridge) applies ``allows`` as the stderr sink filter.
Emits / touches: pure, stateless configuration — no belief, no events, no I/O beyond
  reading env at construction. ``allows`` returns a bool; nothing is mutated.

Modifying this file: keep it a pure filter (env → which event names pass). The
default (no targets, non-debug) must stay *lean* — durable game events only — because
hosted policy logs are capped; the patterns/group names must track the actual
``domain.*`` events emitted by ``events.py``, or filtering silently drops/admits the
wrong families.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase

from players.player_sdk.trace import TraceEvent

# The CREWBORG_TRACE* environment knobs (read once via TraceConfig.from_env):
TRACE_LEVEL_ENV = "CREWBORG_TRACE"  # "" | "debug" | "viewer" — global verbosity level
TRACE_GROUPS_ENV = "CREWBORG_TRACE_GROUPS"  # comma/space list of group names to admit
TRACE_INCLUDE_ENV = "CREWBORG_TRACE_INCLUDE"  # extra event-name globs to admit
TRACE_EXCLUDE_ENV = "CREWBORG_TRACE_EXCLUDE"  # event-name globs to suppress (wins over admit)
TRACE_DECISION_FIELDS_ENV = "CREWBORG_TRACE_DECISION_FIELDS"  # subset of decision_snapshot fields

# Framework boundary events cheap enough to keep in the default lean stream (one per
# mode/strategy transition, not per tick).
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

# Per-tick / heavy ``domain.*`` events excluded from the lean default — admitted only
# under debug/viewer or an explicit group/include target.
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

# Named event families a user can request via CREWBORG_TRACE_GROUPS. Each maps to a
# tuple of fnmatch globs over event names; "all" is everything, and the synthetic
# "lean" group (handled in _group_matches) is the default-stream partition.
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
    """Environment-derived trace targeting configuration (immutable, built once).

    ``level`` is the global verbosity ("" / "debug" / "viewer"); ``groups`` the
    requested family names; ``include_patterns`` / ``exclude_patterns`` extra
    admit/suppress globs (exclude wins); ``decision_fields`` an optional whitelist of
    ``decision_snapshot`` fields. ``has_targets`` distinguishes "user asked for
    specific families" from the lean default.
    """

    level: str = ""
    groups: frozenset[str] = frozenset()
    include_patterns: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    decision_fields: tuple[str, ...] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TraceConfig:
        """Parse the ``CREWBORG_TRACE*`` vars (defaulting to ``os.environ``) into a
        config. Tokens are comma/space/semicolon-separated and lower-cased."""

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
        """True when the user explicitly requested families (groups or include globs),
        which switches ``allows`` from the lean default to the requested set."""

        return bool(self.groups or self.include_patterns)

    def allows(self, event: TraceEvent) -> bool:
        """The sink filter: whether ``event`` should be written.

        With explicit targets, admit events matching a requested group or include glob;
        otherwise admit everything at debug/viewer level, or the lean default set. In
        all cases an exclude-glob match suppresses the event (exclude always wins).
        """

        name = event.name.lower()
        if self.has_targets:
            allowed = self._matches_group(name) or _matches_any(name, self.include_patterns)
        elif self.level in {"debug", "viewer"}:
            allowed = True
        else:
            allowed = lean_trace_filter(event)
        return allowed and not self.excludes_event(name)

    def targets_event(self, event_name: str) -> bool:
        """Whether a specific event is *explicitly* requested (group or include glob)
        and not excluded — how ``events.py`` decides to turn on an optional family even
        without full debug mode."""

        name = event_name.lower()
        return (self._matches_group(name) or _matches_any(name, self.include_patterns)) and not self.excludes_event(name)

    def excludes_event(self, event_name: str) -> bool:
        """Whether ``event_name`` matches an exclude glob (suppression wins over admit)."""

        return _matches_any(event_name.lower(), self.exclude_patterns)

    def _matches_group(self, event_name: str) -> bool:
        """Whether any requested group's patterns match ``event_name``."""

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
    """Whether ``event_name`` belongs to ``group``. The synthetic "lean" group is the
    default-stream partition (low-volume framework + non-noisy domain events); every
    other group is a glob tuple in ``TRACE_GROUP_PATTERNS``."""

    if group == "lean":
        return event_name in LOW_VOLUME_FRAMEWORK_EVENTS or (
            event_name.startswith("domain.") and event_name not in NOISY_DOMAIN_EVENTS
        )
    patterns = TRACE_GROUP_PATTERNS.get(group, ())
    return _matches_any(event_name, patterns)


def _matches_any(event_name: str, patterns: tuple[str, ...]) -> bool:
    """Whether ``event_name`` matches any of the fnmatch globs in ``patterns``."""

    return any(fnmatchcase(event_name, pattern) for pattern in patterns)


def _parse_patterns(raw: str) -> tuple[str, ...]:
    """Tokenize a glob list, also adding a ``domain.``-prefixed alias for any bare
    token — so ``CREWBORG_TRACE_INCLUDE=kill_landed`` matches ``domain.kill_landed``."""

    patterns: list[str] = []
    for token in _split_tokens(raw):
        patterns.append(token)
        if "." not in token:
            patterns.append(f"domain.{token}")
    return tuple(patterns)


def _split_tokens(raw: str) -> tuple[str, ...]:
    """Split a raw env value into lower-cased tokens on commas, semicolons, and spaces."""

    return tuple(part for chunk in raw.replace(";", ",").split(",") for part in chunk.lower().split() if part)
