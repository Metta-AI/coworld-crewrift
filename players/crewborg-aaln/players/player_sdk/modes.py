from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Generic, TypeVar

from players.player_sdk.trace import EventEmitter
from players.player_sdk.types import EmptyModeParams, ModeDecision, ModeDirective, ModeParams

BeliefT = TypeVar("BeliefT")
ActionStateT = TypeVar("ActionStateT")
IntentT = TypeVar("IntentT")


class DirectiveValidationError(ValueError):
    """Raised when a directive cannot be installed by a mode registry."""


class Mode(ABC, Generic[BeliefT, ActionStateT, IntentT]):
    """Base class for deterministic symbolic modes.

    Mode instances are the Python equivalent of mode scratch: they hold
    transient local state and are replaced only when the active directive
    genuinely changes.
    """

    name: ClassVar[str]
    params_type: ClassVar[type[ModeParams]] = EmptyModeParams

    def __init__(self, params: ModeParams | None = None) -> None:
        self.params = params if params is not None else EmptyModeParams()
        self.emit = EventEmitter()
        expected = self.params_type
        if not isinstance(self.params, expected):
            raise DirectiveValidationError(
                f"{type(self).__name__} expected params {expected.__name__}, got {type(self.params).__name__}"
            )

    def matches_directive(self, directive: ModeDirective) -> bool:
        """Return whether this live mode already satisfies ``directive``."""

        return directive.mode == self.name and directive.params == self.params

    def directive_params(self) -> ModeParams:
        """Return stable params that identify this mode instance."""

        return self.params

    def on_enter(self, belief: BeliefT, action_state: ActionStateT) -> None:
        """Run once after the mode becomes active."""

    def on_exit(
        self,
        belief: BeliefT,
        action_state: ActionStateT,
        next_directive: ModeDirective,
    ) -> None:
        """Run once before the mode is replaced."""

    def is_legal(self, belief: BeliefT) -> bool:
        """Return whether the mode may keep running in the current belief state."""

        return True

    @abstractmethod
    def decide(self, belief: BeliefT, action_state: ActionStateT) -> IntentT | ModeDecision[IntentT]:
        """Return this tick's symbolic intent or a status-bearing decision."""


class ModeRegistry(Generic[BeliefT, ActionStateT, IntentT]):
    """Registry mapping directive mode names to mode classes."""

    def __init__(self) -> None:
        self._modes: dict[str, type[Mode[BeliefT, ActionStateT, IntentT]]] = {}

    def register(
        self, mode_cls: type[Mode[BeliefT, ActionStateT, IntentT]]
    ) -> type[Mode[BeliefT, ActionStateT, IntentT]]:
        """Register and return ``mode_cls`` for decorator-style use."""

        name = getattr(mode_cls, "name", "")
        if not name:
            raise DirectiveValidationError(f"{mode_cls.__name__} has no mode name")
        self._modes[name] = mode_cls
        return mode_cls

    def validation_error(self, directive: ModeDirective) -> str | None:
        """Return a validation error string, or ``None`` when valid."""

        mode_cls = self._modes.get(directive.mode)
        if mode_cls is None:
            return f"unknown mode {directive.mode!r}"
        if not isinstance(directive.params, mode_cls.params_type):
            return (
                f"mode {directive.mode!r} expected params "
                f"{mode_cls.params_type.__name__}, got "
                f"{type(directive.params).__name__}"
            )
        return None

    def validate(self, directive: ModeDirective) -> ModeDirective:
        """Validate ``directive`` or raise ``DirectiveValidationError``."""

        error = self.validation_error(directive)
        if error is not None:
            raise DirectiveValidationError(error)
        return directive

    def create(self, directive: ModeDirective) -> Mode[BeliefT, ActionStateT, IntentT]:
        """Instantiate the mode targeted by ``directive``."""

        self.validate(directive)
        mode_cls = self._modes[directive.mode]
        return mode_cls(directive.params)

    def __contains__(self, mode_name: str) -> bool:
        return mode_name in self._modes

    def __len__(self) -> int:
        return len(self._modes)
