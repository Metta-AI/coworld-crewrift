from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

BeliefT = TypeVar("BeliefT")
ActionStateT = TypeVar("ActionStateT")
IntentT = TypeVar("IntentT")
ModeDecisionStatus = Literal["running", "complete", "stalled"]


class ModeParams(BaseModel):
    """Base class for typed mode parameters."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EmptyModeParams(ModeParams):
    """Parameter object for modes with no parameters."""


class ModeDirective(BaseModel):
    """Instruction from the strategy layer to run a named mode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: str
    params: SerializeAsAny[ModeParams] = Field(default_factory=EmptyModeParams)
    source: str = "strategy"
    issued_at_tick: int = 0
    ttl_ticks: int = 0
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def issued(self, tick: int) -> ModeDirective:
        """Return a copy stamped with the tick where the inner loop accepted it."""

        return self.model_copy(update={"issued_at_tick": tick})

    def expired_at(self, tick: int) -> bool:
        """Return whether this directive's TTL has elapsed at ``tick``."""

        return self.ttl_ticks > 0 and self.issued_at_tick > 0 and tick - self.issued_at_tick >= self.ttl_ticks


@dataclass(frozen=True)
class SharedMemoryView(Generic[BeliefT, ActionStateT]):
    """Live memory objects exposed while the shared-memory lock is held."""

    belief: BeliefT
    action_state: ActionStateT
    active_directive: ModeDirective


class SharedMemory(Generic[BeliefT, ActionStateT]):
    """Lock-protected live memory shared by the inner loop and strategy loop."""

    def __init__(
        self,
        *,
        belief: BeliefT,
        action_state: ActionStateT,
        active_directive: ModeDirective,
    ) -> None:
        self._lock = threading.RLock()
        self._belief = belief
        self._action_state = action_state
        self._active_directive = active_directive

    @contextmanager
    def read(self) -> Iterator[SharedMemoryView[BeliefT, ActionStateT]]:
        """Read live memory while holding the shared lock."""

        with self._lock:
            yield SharedMemoryView(
                belief=self._belief,
                action_state=self._action_state,
                active_directive=self._active_directive,
            )

    @contextmanager
    def write(self) -> Iterator[SharedMemoryView[BeliefT, ActionStateT]]:
        """Mutate live memory while holding the shared lock."""

        with self._lock:
            yield SharedMemoryView(
                belief=self._belief,
                action_state=self._action_state,
                active_directive=self._active_directive,
            )

    def set_active_directive(self, directive: ModeDirective) -> None:
        """Update directive metadata visible through future memory views."""

        with self._lock:
            self._active_directive = directive


class ActionIntent(BaseModel):
    """Generic symbolic intent a mode can emit.

    Game-specific agents will usually subclass this model or replace it with
    their own intent type. The base shape is useful for examples and small
    agents.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic: str = "noop"
    target: tuple[int, int] | None = None
    text: str | None = None
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionCommand(BaseModel):
    """Generic concrete command returned by an action resolver."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = "noop"
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StrategyResult(BaseModel):
    """Result produced by a strategy loop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    directive: ModeDirective | None = None
    inferences: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ModeDecision(Generic[IntentT]):
    """Mode result with explicit completion/stalling status."""

    intent: IntentT
    status: ModeDecisionStatus = "running"
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def running(
        cls,
        intent: IntentT,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ModeDecision[IntentT]:
        return cls(intent=intent, status="running", reason=reason, metadata=dict(metadata or {}))

    @classmethod
    def complete(
        cls,
        intent: IntentT,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ModeDecision[IntentT]:
        return cls(intent=intent, status="complete", reason=reason, metadata=dict(metadata or {}))

    @classmethod
    def stalled(
        cls,
        intent: IntentT,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> ModeDecision[IntentT]:
        return cls(intent=intent, status="stalled", reason=reason, metadata=dict(metadata or {}))


@dataclass(frozen=True)
class BeliefSnapshot(Generic[BeliefT, ActionStateT]):
    """Immutable envelope that points strategy loops at shared live memory."""

    tick: int
    memory: SharedMemory[BeliefT, ActionStateT]
    wake_reason: str = "tick"
    mode_status: ModeDecisionStatus = "running"
    mode_status_reason: str = ""

    def read(self) -> AbstractContextManager[SharedMemoryView[BeliefT, ActionStateT]]:
        """Read belief, action state, and directive under the shared lock."""

        return self.memory.read()

    def write(self) -> AbstractContextManager[SharedMemoryView[BeliefT, ActionStateT]]:
        """Mutate belief, action state, or directive-scoped facts under the shared lock."""

        return self.memory.write()
