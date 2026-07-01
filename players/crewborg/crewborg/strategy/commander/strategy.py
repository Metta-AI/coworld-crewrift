"""Strategy wrapper that feeds gameplay-commander priorities into belief."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol

from crewborg.strategy.commander.context import (
    legal_players,
    legal_rooms,
    serialize_commander_context,
)
from crewborg.strategy.commander.schema import sanitize_priorities
from crewborg.strategy.rule_based import RuleBasedStrategy
from crewborg.types import ActionState, Belief, CommanderPriorities
from players.player_sdk import StrategyResult
from players.player_sdk.types import BeliefSnapshot

FORCE_ENV = "CREWBORG_COMMANDER_FORCE"
_LOG = logging.getLogger(__name__)


class _CommanderWorker(Protocol):
    snapshots: Any
    priorities: Any

    def start(self) -> None: ...

    def close(self) -> None: ...


class CommanderStrategy:
    """Delegate mode selection to rules while asynchronously refreshing priorities."""

    def __init__(self, rules: RuleBasedStrategy, worker: _CommanderWorker, *, feature_enabled: bool) -> None:
        self._rules = rules
        self._worker = worker
        self._feature_enabled = feature_enabled
        self._forced_priorities = _parse_forced_priorities(os.environ.get(FORCE_ENV)) if feature_enabled else None
        self._last: CommanderPriorities | None = None
        self._started = False

    def decide(self, snapshot: BeliefSnapshot[Belief, ActionState]) -> StrategyResult:
        if not self._feature_enabled:
            with snapshot.read() as memory:
                return StrategyResult(directive=self._rules.select(memory.belief))

        if self._forced_priorities is None and not self._started:
            self._worker.start()
            self._started = True

        with snapshot.read() as memory:
            belief = memory.belief
            directive = self._rules.select(belief)
            rooms = set(legal_rooms(belief))
            players = set(legal_players(belief))
            tick = snapshot.tick
            context = None if self._forced_priorities is not None else serialize_commander_context(
                belief,
                active_mode=memory.active_directive.mode,
            )

        if self._forced_priorities is not None:
            self._last = sanitize_priorities(self._forced_priorities, rooms, players, as_of_tick=tick)
            inferences: dict[str, Any] = {"commander": self._last.model_dump()}
            return StrategyResult(directive=directive, inferences=inferences)

        self._worker.snapshots.publish(context)
        raw = self._worker.priorities.take()
        if raw is not None:
            self._last = sanitize_priorities(raw, rooms, players, as_of_tick=tick)

        inferences: dict[str, Any] = {}
        if self._last is not None:
            inferences["commander"] = self._last.model_dump()
        return StrategyResult(directive=directive, inferences=inferences)

    def close(self) -> None:
        self._worker.close()


def apply_commander_inferences(belief: Belief, inferences: dict[str, Any]) -> None:
    payload = inferences.get("commander")
    if payload is not None:
        belief.commander = CommanderPriorities(**payload)


def _parse_forced_priorities(raw: str | None) -> dict[str, Any] | None:
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _LOG.warning("Ignoring invalid %s JSON: %s", FORCE_ENV, exc)
        return None
    if not isinstance(parsed, dict):
        _LOG.warning("Ignoring %s because it is not a JSON object", FORCE_ENV)
        return None
    return parsed
