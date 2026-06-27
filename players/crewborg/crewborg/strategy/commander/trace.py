"""Thread-safe telemetry handoff for the gameplay commander.

The commander LLM runs on a background daemon thread, while crewborg domain trace
events must be emitted from the inner-loop thread through ``EventEmitter``. This
buffer is the narrow handoff between those threads: the worker records cheap,
bounded telemetry here, and ``CrewborgEventTracer`` drains it on tick completion.

The buffer is bounded and drops *oldest* records on overflow, so a fast worker can
never grow memory without bound or stall the inner loop — telemetry is best-effort,
and a dropped-count synthetic event preserves the fact that loss occurred.

Collaborators
-------------
Relies on:
  - ``threading.Lock`` + ``collections.deque`` only — no domain imports, so it stays a
    pure cross-thread primitive.
Used by:
  - ``worker.CommanderWorker`` — calls ``record`` from the daemon thread for every
    ``commander_*`` event (started/call/error/stopped).
  - ``events.CrewborgEventTracer`` — calls ``drain`` on each tick and re-emits the records
    through the inner-loop ``EventEmitter``.
  - ``__init__.build_runtime`` — owns the single shared instance wired to both ends.

Modifying this file: ``record`` runs on the worker thread and ``drain`` on the inner
loop, so every access to ``_records`` / ``_dropped`` must stay under ``_lock``. ``record``
must never block on I/O (that would stall the LLM thread); keep it a pure in-memory append.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class CommanderTrace:
    """Bounded cross-thread FIFO of ``(event_name, data)`` telemetry records.

    State (all guarded by ``_lock``): ``_records`` is the pending queue, ``_capacity`` its
    max length, and ``_dropped`` counts records evicted since the last drain (surfaced as a
    synthetic ``commander_trace_dropped`` event so loss is visible in the trace)."""

    def __init__(self, *, capacity: int = 256) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._records: deque[tuple[str, dict[str, Any]]] = deque()
        self._dropped = 0
        self._lock = threading.Lock()

    def record(self, event: str, data: dict[str, Any]) -> None:
        """Record one event from the worker thread without blocking on I/O.

        ``data`` is shallow-copied so later mutation by the caller can't change a queued
        record. When the buffer is full the oldest record is evicted (FIFO drop-oldest) and
        the dropped counter is bumped — recent telemetry is favored over old."""

        with self._lock:
            if len(self._records) >= self._capacity:
                self._records.popleft()
                self._dropped += 1
            self._records.append((event, dict(data)))

    def drain(self) -> list[tuple[str, dict[str, Any]]]:
        """Return all buffered records (oldest first) and clear the queue.

        If records were dropped since the last drain, a synthetic
        ``("commander_trace_dropped", {"dropped": N})`` record is prepended so the loss is
        visible downstream. Resets the dropped counter."""

        with self._lock:
            records = list(self._records)
            self._records.clear()
            dropped = self._dropped
            self._dropped = 0
        if dropped:
            return [("commander_trace_dropped", {"dropped": dropped}), *records]
        return records
