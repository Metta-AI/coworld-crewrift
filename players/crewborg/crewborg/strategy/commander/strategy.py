"""Strategy wrapper that feeds gameplay-commander priorities into belief.

The WRITE side of the commander seam, and the top-level gate for the whole feature.
``CommanderStrategy`` wraps the deterministic ``RuleBasedStrategy``: mode selection is
*always* delegated to the rules, while the commander only attaches a sanitized
``commander`` inference. The inner loop never blocks on the LLM — priorities are pulled
from the worker's latest-value buffer (whatever is ready *this* tick, possibly nothing),
sanitized, and emitted; ``apply_commander_inferences`` later installs them into
``belief.commander`` for the modes to read via ``bias.commander_of``.

Gating / "off = inert": when ``feature_enabled`` is false, ``decide`` returns the pure
rule directive with no inferences and never starts the worker, so belief stays untouched
and play is byte-identical to the deterministic agent. A ``CREWBORG_COMMANDER_FORCE`` env
override (a JSON priorities object) is a deterministic test path that bypasses the worker
and LLM entirely while still flowing through the same sanitizer.

Collaborators
-------------
Relies on:
  - ``strategy.rule_based.RuleBasedStrategy`` — the real mode selector (always authoritative).
  - ``context`` (``serialize_commander_context`` + the ``legal_rooms`` / ``legal_players``
    validity sets) and ``schema.sanitize_priorities``.
  - the injected ``_CommanderWorker`` — latest-value ``snapshots`` (out) / ``priorities`` (in).
  - the player SDK ``BeliefSnapshot`` (read lock) / ``StrategyResult``.
Used by:
  - ``__init__.build_runtime`` — constructs this as the runtime's strategy and registers
    ``apply_commander_inferences`` as the runtime's ``apply_inferences`` hook.

Modifying this file: the ``feature_enabled`` short-circuit is the load-bearing
"off = inert" guarantee — keep the disabled path free of worker start-up and inference
emission. All priorities (worker or forced) must go through ``sanitize_priorities``; never
install raw LLM JSON into belief.
"""

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
    """Structural type of the background worker: two latest-value buffers + lifecycle.

    ``snapshots`` is the out-channel (context the inner loop publishes for the LLM thread)
    and ``priorities`` the in-channel (raw LLM output the inner loop takes). Decouples this
    module from the concrete ``worker.CommanderWorker`` (and lets tests inject a fake)."""

    snapshots: Any
    priorities: Any

    def start(self) -> None: ...

    def close(self) -> None: ...


class CommanderStrategy:
    """Delegate mode selection to rules while asynchronously refreshing priorities.

    Wraps a ``RuleBasedStrategy`` (which alone decides the mode each tick) and a background
    ``_CommanderWorker``. State:
      - ``_feature_enabled`` — the master gate; when false ``decide`` is pure rule selection.
      - ``_forced_priorities`` — parsed ``CREWBORG_COMMANDER_FORCE`` JSON (test override); when
        set, the worker/LLM are bypassed and these priorities are used every tick.
      - ``_last`` — the most recently sanitized priorities, re-emitted on ticks where the
        worker has nothing new (so the inference persists across the LLM's call latency).
      - ``_started`` — guards one-time lazy worker start (skipped on the forced path)."""

    def __init__(self, rules: RuleBasedStrategy, worker: _CommanderWorker, *, feature_enabled: bool) -> None:
        self._rules = rules
        self._worker = worker
        self._feature_enabled = feature_enabled
        self._forced_priorities = _parse_forced_priorities(os.environ.get(FORCE_ENV)) if feature_enabled else None
        self._last: CommanderPriorities | None = None
        self._started = False

    def decide(self, snapshot: BeliefSnapshot[Belief, ActionState]) -> StrategyResult:
        """Return this tick's directive (always the rule selection) plus the commander inference.

        Disabled path (``not feature_enabled``): pure rule directive, no inferences, no worker.
        Forced path (``CREWBORG_COMMANDER_FORCE`` set): sanitize the forced JSON each tick and
        emit it; no worker/LLM. Live path: publish the freshly serialized context to the worker,
        take whatever raw priorities are ready (may be ``None``), sanitize+cache them in
        ``_last``, and emit ``_last`` if present. Belief is only read under ``snapshot.read()``;
        the directive's mode is never overridden here."""
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
        """Stop the background worker (joins its thread); safe to call when never started."""
        self._worker.close()


def apply_commander_inferences(belief: Belief, inferences: dict[str, Any]) -> None:
    """Install the sanitized ``commander`` inference into ``belief.commander``.

    Registered as the runtime's ``apply_inferences`` hook: it carries the priorities emitted by
    ``CommanderStrategy.decide`` into the live belief the modes read. A missing ``"commander"``
    key is a no-op (leaves any existing value untouched), so a tick with no fresh inference does
    not clear belief. The payload is a ``model_dump`` of an already-sanitized ``CommanderPriorities``."""
    payload = inferences.get("commander")
    if payload is not None:
        belief.commander = CommanderPriorities(**payload)


def _parse_forced_priorities(raw: str | None) -> dict[str, Any] | None:
    """Parse the ``CREWBORG_COMMANDER_FORCE`` env value into a priorities dict, or ``None``.

    Returns ``None`` for empty/unset input, invalid JSON, or non-object JSON (logging a warning
    in the latter two cases) — i.e. a malformed override silently disables forcing rather than
    crashing. The returned dict is still passed through ``sanitize_priorities`` before use."""
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
