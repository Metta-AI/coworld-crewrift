"""Thread-safe telemetry handoff for the gameplay commander.

The commander LLM runs on a background daemon thread, while crewborg domain trace
events must be emitted from the inner-loop thread through ``EventEmitter``. This
buffer is the narrow handoff between those threads: the worker records cheap,
bounded telemetry here, and ``CrewborgEventTracer`` drains it on tick completion.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class CommanderTrace:
    """Bounded cross-thread queue for commander telemetry records."""

    def __init__(self, *, capacity: int = 256) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._records: deque[tuple[str, dict[str, Any]]] = deque()
        self._dropped = 0
        self._lock = threading.Lock()

    def record(self, event: str, data: dict[str, Any]) -> None:
        """Record one event from the worker thread without blocking on I/O."""

        with self._lock:
            if len(self._records) >= self._capacity:
                self._records.popleft()
                self._dropped += 1
            self._records.append((event, dict(data)))

    def drain(self) -> list[tuple[str, dict[str, Any]]]:
        """Return all buffered records and clear the queue."""

        with self._lock:
            records = list(self._records)
            self._records.clear()
            dropped = self._dropped
            self._dropped = 0
        if dropped:
            return [("commander_trace_dropped", {"dropped": dropped}), *records]
        return records
