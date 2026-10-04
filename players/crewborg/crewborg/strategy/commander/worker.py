"""Nonblocking commander admission; every request belongs to the native session."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import monotonic

import httpx
from players.player_sdk import OverwriteBuffer

from crewborg.native import ControllerInstallation, NativeSession
from crewborg.strategy.commander.llm import CommanderLLMClient, CommanderLLMResult
from crewborg.strategy.commander.trace import CommanderTrace


class CommanderWorker:
    def __init__(
        self,
        client_factory: Callable[[], CommanderLLMClient],
        *,
        session: NativeSession,
        trace: CommanderTrace,
    ):
        self.session = session
        self._client_factory = client_factory
        self._client: CommanderLLMClient | None = None
        self._pending: asyncio.Future[CommanderLLMResult] | None = None
        self._closed = False
        self._trace = trace
        self.snapshots: OverwriteBuffer[dict] = OverwriteBuffer()
        self.priorities: OverwriteBuffer[CommanderLLMResult] = OverwriteBuffer()

    @property
    def enabled(self) -> bool:
        return self._client.enabled if self._client is not None else False

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("Cannot start a closed commander worker")
        if self._client is None:
            self._client = self._client_factory()
            self._trace.record("commander_started", {"enabled": self._client.enabled})

    def poll(self) -> None:
        if self._closed:
            return
        assert self._client is not None
        if not self._client.enabled:
            return
        if self._pending is not None:
            if not self._pending.done():
                return
            pending = self._pending
            self._pending = None
            try:
                result = pending.result()
            except (httpx.HTTPError, ValueError, RuntimeError, TimeoutError) as exc:
                self._trace.record(
                    "commander_call",
                    {
                        "outcome": "error",
                        "error_kind": type(exc).__name__,
                    },
                )
                return
            self._trace.record(
                "commander_call",
                {
                    "outcome": "ok",
                    "policy_identity": result.policy_identity,
                    "origin": result.generation.origin,
                    "generation_id": str(result.generation.generation_id),
                    "latency_ms": result.latency_ms,
                },
            )
            self.priorities.publish(result)
            return
        if not self.session.phase_joined("commander"):
            return
        context = self.snapshots.take()
        if context is not None:
            self._pending = self._client.decide(context)
            self._trace.record(
                "commander_call_start", {"tick": context["observation_tick"]}
            )

    def installed(self, result: CommanderLLMResult, value: dict, tick: int) -> None:
        result.generation.controller_installations.append(
            ControllerInstallation(
                kind="commander_priorities", tick=tick, value=value, intent=None
            )
        )
        self.session.record(result.generation)

    def close(self) -> None:
        self._closed = True
        self.session.begin_stop(monotonic() + 2)
        self.snapshots.close()
        self.priorities.close()
        self._trace.record(
            "commander_stopped", {"joined": self.session.phase_joined("commander")}
        )
