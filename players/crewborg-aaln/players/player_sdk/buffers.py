from __future__ import annotations

import threading
from typing import Generic, TypeVar

T = TypeVar("T")


class OverwriteBuffer(Generic[T]):
    """Thread-safe latest-value buffer.

    Writers overwrite any unread value. Readers consume at most one value and
    clear the slot. This is the right default for strategy directives because a
    stale plan should not execute after a newer one exists.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._value: T | None = None
        self._closed = False

    def publish(self, value: T) -> None:
        """Publish ``value``, replacing any unread value."""

        with self._condition:
            if self._closed:
                return
            self._value = value
            self._condition.notify()

    def take(self) -> T | None:
        """Consume and return the latest value without blocking."""

        with self._condition:
            value = self._value
            self._value = None
            return value

    def wait_take(self, timeout: float | None = None) -> T | None:
        """Wait for and consume a value, returning ``None`` on timeout/close."""

        with self._condition:
            if self._value is None and not self._closed:
                self._condition.wait(timeout=timeout)
            value = self._value
            self._value = None
            return value

    def close(self) -> None:
        """Wake blocked readers and reject future writes."""

        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed
