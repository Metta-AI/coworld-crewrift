"""Background daemon worker that runs the gameplay-commander LLM off the inner loop.

The thread that decouples LLM latency from gameplay. It owns the daemon thread and the two
latest-value ``OverwriteBuffer``s that bridge it to ``CommanderStrategy``: it ``wait_take``s the
newest serialized context, calls the (lazily built) LLM client, and ``publish``es the raw
priorities — never touching live belief itself. Because both buffers keep only the latest
value, the inner loop never blocks on a call and stale snapshots are simply overwritten; a
slow or failed call just means no fresh priorities that tick, which ``bias.commander_of`` then
ages out via its TTL.

Gating / "off = inert": ``start`` is only called by ``CommanderStrategy`` when the feature is
enabled, and ``_run`` exits immediately if the built client is disabled — so when off, no
thread does anything and nothing is ever published.

Collaborators
-------------
Relies on:
  - ``llm`` — ``CommanderLLMClient`` protocol, ``DisabledCommanderClient`` (build-failure
    fallback), and the injected ``client_factory`` (normally ``build_commander_client_from_env``).
  - ``players.player_sdk.OverwriteBuffer`` — the latest-value cross-thread channels.
  - ``trace.CommanderTrace`` (optional) — where all ``commander_*`` telemetry is recorded.
Used by:
  - ``strategy.CommanderStrategy`` — drives it via ``start`` / ``snapshots.publish`` /
    ``priorities.take`` / ``close`` (through the ``_CommanderWorker`` protocol).
  - ``__init__.build_runtime`` — constructs it with the client factory and shared trace.

Modifying this file: ``_run`` is the only code on the daemon thread — it must never raise out
of the loop (every ``decide`` is wrapped) and must never touch ``Belief``. All telemetry goes
through ``_record`` so it is a no-op when no trace is wired. Keep ``close`` idempotent and
bounded (the ``join`` timeout) so shutdown can't hang the agent.
"""

from __future__ import annotations

import os
import threading
from time import perf_counter
from typing import Any, Callable

from crewborg.strategy.commander.llm import CommanderLLMClient, DisabledCommanderClient, _truthy
from crewborg.strategy.commander.trace import CommanderTrace
from players.player_sdk import OverwriteBuffer


class CommanderWorker:
    """Take latest serialized context, call the LLM client, publish raw priorities.

    The worker never touches live belief. Both directions use latest-value buffers
    so the inner loop cannot block on an LLM call and stale snapshots are overwritten.

    State:
      - ``snapshots`` — in-channel: latest serialized context published by the strategy.
      - ``priorities`` — out-channel: latest raw LLM priorities for the strategy to take.
      - ``_client`` — the LLM client, built lazily on the thread (``None`` until ``_run``).
      - ``_build_attempts`` / ``_retry_interval`` — bounded retry while only the *backend* is
        missing (e.g. the sidecar endpoint appears slightly after start-up).
      - ``_wait_timeout`` — how long each loop blocks waiting for a new context.
      - ``_stop`` — set by ``close`` to end the loop and short-circuit retry waits.
      - ``_trace`` / ``_stopped_recorded`` — optional telemetry sink and its one-shot guard.
    """

    def __init__(
        self,
        client_factory: Callable[[], CommanderLLMClient],
        *,
        build_attempts: int = 20,
        retry_interval: float = 0.5,
        wait_timeout: float = 0.1,
        trace: CommanderTrace | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._client: CommanderLLMClient | None = None
        self._build_attempts = build_attempts
        self._retry_interval = retry_interval
        self._wait_timeout = wait_timeout
        self._trace = trace
        self.snapshots: OverwriteBuffer[dict[str, Any]] = OverwriteBuffer()
        self.priorities: OverwriteBuffer[dict[str, Any]] = OverwriteBuffer()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopped_recorded = False

    @property
    def enabled(self) -> bool:
        """Whether a client has been built and is enabled (``False`` before the thread builds one)."""
        return self._client.enabled if self._client is not None else False

    def start(self) -> None:
        """Start the background daemon thread (idempotent: a second call is a no-op)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="crewborg-commander")
        self._thread.start()

    def close(self) -> None:
        """Signal stop, close both buffers, and join the thread (bounded 1s).

        Records the one-shot ``commander_stopped`` event, then unblocks any pending buffer waits so
        the loop can exit. Safe to call when never started; the bounded join keeps shutdown from
        hanging the agent even if a call is mid-flight."""
        self._record_stopped()
        self._stop.set()
        self.snapshots.close()
        self.priorities.close()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        """Daemon-thread main loop: build the client, then context→decide→publish until stopped.

        Builds the client once (with bounded retry); exits immediately if it is disabled. Each
        iteration takes the latest context (skipping when none arrives within ``_wait_timeout``),
        calls ``decide``, traces the outcome, and publishes the raw priorities on success. Every
        ``decide`` is wrapped so a call failure is traced as a ``commander_call`` error and the loop
        continues rather than killing the thread. Never touches ``Belief``."""
        self._client = self._build_client()
        if not self._client.enabled:
            return
        while not self._stop.is_set():
            context = self.snapshots.wait_take(timeout=self._wait_timeout)
            if context is None:
                continue
            self._record("commander_call_start", _context_summary(context))
            started = perf_counter()
            try:
                result = self._client.decide(context)
            except Exception as exc:
                self._record(
                    "commander_call",
                    {
                        "outcome": "error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "latency_ms": (perf_counter() - started) * 1000,
                    },
                )
                continue
            data: dict[str, Any] = {
                "outcome": "ok",
                "latency_ms": result.latency_ms,
                "model": result.model,
                "priorities": result.priorities,
            }
            if result.usage is not None:
                data["usage"] = result.usage
            if result.raw_request is not None:
                data["raw_request"] = result.raw_request
            if result.raw_response is not None:
                data["raw_response"] = result.raw_response
            self._record("commander_call", data)
            self.priorities.publish(result.priorities)

    def _build_client(self) -> CommanderLLMClient:
        """Build the client, retrying only while the *backend* is what's missing.

        Returns immediately once a client is enabled, or once it is disabled for a non-backend
        reason (e.g. flag off — retrying would never help). A "no LLM backend" disable is treated as
        possibly-transient (the sidecar endpoint / creds can land just after start-up), so it retries
        up to ``_build_attempts`` times, sleeping ``_retry_interval`` between tries and bailing early
        if ``_stop`` is set. Each successful or first build is traced via ``_record_started``."""
        client = self._call_client_factory()
        self._record_started(client, attempt=1)
        if client.enabled or not _missing_backend(client):
            return client

        for attempt in range(2, self._build_attempts + 1):
            if self._stop.wait(self._retry_interval):
                return client
            client = self._call_client_factory()
            if client.enabled:
                self._record_started(client, attempt=attempt)
                return client
            if not _missing_backend(client):
                return client
        return client

    def _call_client_factory(self) -> CommanderLLMClient:
        """Invoke the injected factory, degrading any construction error to a disabled client."""
        try:
            return self._client_factory()
        except Exception as exc:
            return DisabledCommanderClient(f"commander LLM client construction failed: {exc!r}")

    def _record_started(self, client: CommanderLLMClient, *, attempt: int) -> None:
        """Trace a ``commander_started`` event with the client's enabled/backend/model state.

        Includes ``env_seen`` (which backend-selecting env vars were present) and the build
        ``attempt`` number, so a disabled commander can be diagnosed from the trace alone."""
        self._record(
            "commander_started",
            {
                "enabled": client.enabled,
                "backend": _client_backend(client),
                "model": _client_model(client),
                "disabled_reason": client.disabled_reason,
                "attempt": attempt,
                "env_seen": _env_seen(),
            },
        )

    def _record_stopped(self) -> None:
        """Emit the ``commander_stopped`` event exactly once, even if ``close`` is called twice."""
        if self._stopped_recorded:
            return
        self._stopped_recorded = True
        self._record("commander_stopped", {})

    def _record(self, event: str, data: dict[str, Any]) -> None:
        """Forward one telemetry record to the trace buffer; no-op when no trace is wired."""
        if self._trace is not None:
            self._trace.record(event, data)


def _context_summary(context: dict[str, Any]) -> dict[str, Any]:
    """Cheap, low-cardinality digest (phase + role) of a context, for the call-start trace."""
    self_context = context.get("self")
    return {
        "phase": context.get("phase"),
        "role": self_context.get("role") if isinstance(self_context, dict) else None,
    }


def _missing_backend(client: CommanderLLMClient) -> bool:
    """Whether a disabled client failed specifically for lack of a backend (a retry-worthy cause)."""
    return "no LLM backend" in (client.disabled_reason or "")


def _env_seen() -> dict[str, bool]:
    """Snapshot which backend-selecting env vars are present, for the ``commander_started`` trace."""
    return {
        "USE_BEDROCK": _truthy(os.environ.get("USE_BEDROCK", "")),
        "CLAUDE_CODE_USE_BEDROCK": _truthy(os.environ.get("CLAUDE_CODE_USE_BEDROCK", "")),
        "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY" in os.environ,
        # The sidecar endpoint the runner injects in sidecar mode (it strips USE_BEDROCK);
        # its presence is the real in-pod Bedrock signal the commander now gates on.
        "AWS_ENDPOINT_URL_BEDROCK_RUNTIME": bool(
            os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "").strip()
        ),
    }


def _client_backend(client: CommanderLLMClient) -> str | None:
    """Backend label (``"bedrock"`` / ``"anthropic"``) of an enabled client, else ``None``.

    Uses ``getattr`` so it works against the protocol without assuming a concrete type (a disabled
    client has no ``config``)."""
    config = getattr(client, "config", None)
    if not client.enabled or config is None:
        return None
    return "bedrock" if bool(getattr(config, "use_bedrock", False)) else "anthropic"


def _client_model(client: CommanderLLMClient) -> str | None:
    """Resolved model id of an enabled client, or ``None`` when disabled/unset (defensive getattr)."""
    config = getattr(client, "config", None)
    if not client.enabled or config is None:
        return None
    model = getattr(config, "model", None)
    return model if isinstance(model, str) else None
