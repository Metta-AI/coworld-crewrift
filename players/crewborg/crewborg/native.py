"""Native requests and the owned lifetime of one hosted player's model work."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal, TypeVar
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue

T = TypeVar("T")


class NativeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, strict=True)


class PlayerRegistration(NativeModel):
    requested_slot: int | None = Field(ge=0, le=15)
    authenticated_upgrade: bool = False
    engine_player_index: int | None = Field(default=None, ge=0)
    assigned_slot: int | None = Field(default=None, ge=0, le=15)

    @classmethod
    def from_url(cls, url: str) -> PlayerRegistration:
        query = parse_qs(urlsplit(url).query, strict_parsing=True)
        values = query.get("slot", [])
        if len(values) > 1:
            raise ValueError("Player URL contains multiple requested slots")
        return cls(requested_slot=int(values[0]) if values else None)


class NativeHttpError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Native inference failed: HTTP {status_code}")


class NativeRequest(NativeModel):
    model: str = Field(min_length=1)
    messages: list[dict[str, str]]
    max_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0, le=2)


def decision_json(text: str) -> str:
    """Preserve the ordinary first-to-last object parser without exposing its input."""
    first, last = text.find("{"), text.rfind("}")
    if first < 0 or last < first:
        raise ValueError("Native decision did not contain a JSON object")
    return text[first : last + 1]


class ControllerInstallation(NativeModel):
    kind: Literal["meeting_decision", "commander_priorities"]
    tick: int = Field(ge=0)
    value: JsonValue
    intent: JsonValue | None


class NativeGeneration(NativeModel):
    origin: Literal["native"]
    generation_id: UUID = Field(default_factory=uuid4)
    phase: Literal["meeting", "commander"]
    observation_tick: int = Field(ge=0)
    player_slot: int | None
    request: NativeRequest
    prompt: list[dict[str, str]]
    decoder: dict[str, JsonValue]
    platform_call_id: str | None = None
    provider_request_id: str | None = None
    http_status: int | None = None
    response_headers: dict[str, str] | None = None
    response_header_pairs_b64: list[tuple[str, str]] | None = None
    response_headers_b64: str | None = None
    response_body_b64: str | None = None
    raw_response: str | None = None
    response_complete: bool | None = None
    response_reader_joined: bool | None = None
    transport_cleanup_joined: bool | None = None
    latency_ms: int | None = None
    response: JsonValue | None = None
    completion_text: str | None = None
    parsed_action: JsonValue | None = None
    controller_installations: list[ControllerInstallation] = Field(default_factory=list)
    model_identity: str | None = None
    tokenizer_identity: str | None = None
    chat_template_sha256: str | None = None
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ScriptedGeneration(NativeModel):
    origin: Literal["teacher"]
    generation_id: UUID = Field(default_factory=uuid4)
    teacher_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    phase: Literal["meeting", "commander"]
    observation_tick: int = Field(ge=0)
    player_slot: int
    prompt: list[dict[str, str]]
    completion_text: str
    parsed_action: JsonValue
    duration_ms: int = Field(ge=0)
    controller_installations: list[ControllerInstallation] = Field(default_factory=list)


PolicyGeneration = Annotated[
    NativeGeneration | ScriptedGeneration, Field(discriminator="origin")
]


class NativeProfile(NativeModel):
    origin: Literal["native"]


class ScriptedProfile(NativeModel):
    origin: Literal["teacher"]
    teacher_authority_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


PolicyProfile = Annotated[
    NativeProfile | ScriptedProfile, Field(discriminator="origin")
]


class NativeMessage(NativeModel):
    content: str
    role: Literal["assistant"] = "assistant"


class NativeChoice(NativeModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    message: NativeMessage
    finish_reason: str | None = None


class NativeUsage(NativeModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class NativeResponse(NativeModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    choices: list[NativeChoice] = Field(min_length=1)
    usage: NativeUsage | None = None


@dataclass
class CleanupBudget:
    deadline: float | None = None

    def begin(self, deadline: float) -> float:
        self.deadline = (
            deadline if self.deadline is None else min(self.deadline, deadline)
        )
        return self.deadline


@dataclass(frozen=True)
class PreparedCall:
    generation: NativeGeneration
    deadline: float
    cleanup: CleanupBudget


class NativeSession:
    """All model tasks share STOP admission, private evidence, and one shutdown deadline."""

    def __init__(
        self,
        registration: PlayerRegistration,
        path: Path,
        record_generation: Callable[[PolicyGeneration], None],
    ):
        self.registration = registration
        self.record_generation = record_generation
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        self.tasks: dict[asyncio.Task[object], PreparedCall] = {}
        self.generations: dict[str, PolicyGeneration] = {}
        self.shutdown_deadline: float | None = None
        self.sealed = False

    def record(self, generation: PolicyGeneration) -> None:
        if self.sealed:
            raise RuntimeError("Cannot mutate sealed native player evidence")
        frozen = generation.model_copy(deep=True)
        self.generations[str(frozen.generation_id)] = frozen
        self.record_generation(frozen)
        with self.path.open("a") as stream:
            stream.write(frozen.model_dump_json() + "\n")

    def start(
        self, prepared: PreparedCall, operation: Coroutine[object, object, T]
    ) -> asyncio.Task[T]:
        if self.shutdown_deadline is not None or self.sealed:
            operation.close()
            raise RuntimeError("Cannot start model work after player STOP")
        task = asyncio.create_task(operation)
        self.tasks[task] = prepared
        return task

    def cancel_phase(
        self, phase: Literal["meeting", "commander"], deadline: float
    ) -> None:
        for task, prepared in self.tasks.items():
            if prepared.generation.phase == phase and not task.done():
                prepared.cleanup.begin(deadline)
                task.cancel()

    def phase_joined(self, phase: Literal["meeting", "commander"]) -> bool:
        return all(
            task.done()
            and prepared.generation.response_reader_joined is not False
            and prepared.generation.transport_cleanup_joined is not False
            for task, prepared in self.tasks.items()
            if prepared.generation.phase == phase
        )

    def begin_stop(self, cleanup_deadline: float) -> float:
        if self.shutdown_deadline is None:
            self.shutdown_deadline = cleanup_deadline
        else:
            self.shutdown_deadline = min(self.shutdown_deadline, cleanup_deadline)
        for task, prepared in self.tasks.items():
            if not task.done():
                prepared.cleanup.begin(self.shutdown_deadline)
                task.cancel()
        return self.shutdown_deadline

    async def stop(self, cleanup_deadline: float) -> bool:
        self.begin_stop(cleanup_deadline)
        assert self.shutdown_deadline is not None
        pending = {task for task in self.tasks if not task.done()}
        if pending:
            _, unresolved = await asyncio.wait(
                pending, timeout=max(0, self.shutdown_deadline - monotonic())
            )
            joined = not unresolved
        else:
            joined = True
        for task in pending:
            if task.done() and not task.cancelled():
                task.exception()
        joined = joined and all(
            generation.response_reader_joined is not False
            and generation.transport_cleanup_joined is not False
            for generation in self.generations.values()
            if generation.origin == "native"
        )
        self.sealed = True
        return joined

    def prepare(
        self,
        request: NativeRequest,
        *,
        phase: Literal["meeting", "commander"],
        observation_tick: int,
        deadline: float,
    ) -> PreparedCall:
        if self.shutdown_deadline is not None or self.sealed:
            raise RuntimeError("Cannot prepare inference after player STOP")
        if (
            self.registration.requested_slot is not None
            and not self.registration.authenticated_upgrade
        ):
            raise RuntimeError(
                "Native player seat is not authenticated by an accepted engine upgrade"
            )
        slot = (
            self.registration.assigned_slot
            if self.registration.authenticated_upgrade
            else None
        )
        if self.registration.authenticated_upgrade:
            assert slot is not None
        if not self.phase_joined(phase):
            raise RuntimeError("Previous phase generation is not joined")
        request = request.model_copy(deep=True)
        generation = NativeGeneration(
            origin="native",
            phase=phase,
            observation_tick=observation_tick,
            player_slot=slot,
            request=request,
            prompt=request.messages,
            decoder={
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
            },
        )
        self.record(generation)
        return PreparedCall(
            generation=generation, deadline=deadline, cleanup=CleanupBudget()
        )

    async def complete(self, prepared: PreparedCall) -> NativeGeneration:
        generation = prepared.generation
        request = generation.request
        deadline = prepared.deadline
        slot = generation.player_slot
        if self.shutdown_deadline is not None or self.sealed:
            raise RuntimeError("Cannot issue inference after player STOP")
        started = monotonic()
        client = httpx.AsyncClient(timeout=max(0.001, deadline - monotonic()))
        response: httpx.Response | None = None
        body = bytearray()
        try:
            async with asyncio.timeout_at(deadline):
                headers = {"Accept-Encoding": "identity"}
                if slot is not None:
                    headers["X-Coworld-Player-Slot"] = str(slot)
                response = await client.send(
                    client.build_request(
                        "POST",
                        os.environ["COWORLD_LLM_ENDPOINT"].rstrip("/")
                        + "/v1/chat/completions",
                        json=request.model_dump(),
                        headers=headers,
                    ),
                    stream=True,
                )
                generation.http_status = response.status_code
                generation.response_headers = dict(response.headers)
                generation.response_header_pairs_b64 = [
                    (base64.b64encode(k).decode(), base64.b64encode(v).decode())
                    for k, v in response.headers.raw
                ]
                generation.provider_request_id = response.headers.get(
                    "request-id"
                ) or response.headers.get("x-request-id")
                generation.model_identity = response.headers.get(
                    "x-coworld-checkpoint-sha256"
                )
                generation.tokenizer_identity = response.headers.get(
                    "x-coworld-tokenizer-sha256"
                )
                generation.chat_template_sha256 = response.headers.get(
                    "x-coworld-chat-template-sha256"
                )
                generation.response_body_b64 = ""
                generation.response_complete = False
                generation.response_reader_joined = False
                self.record(generation)
                ids = response.headers.get_list("x-softmax-llm-call-id")
                if len(ids) > 1:
                    raise ValueError(
                        "Native response contains duplicate platform call-ID headers"
                    )
                generation.platform_call_id = ids[0] if ids else None
                self.record(generation)
                if (
                    response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    raise ValueError(
                        "Native response uses unsupported content encoding"
                    )
                async for chunk in response.aiter_raw():
                    body.extend(chunk)
                    generation.response_body_b64 = base64.b64encode(body).decode()
                    text = body.decode("utf-8", errors="surrogateescape")
                    generation.raw_response = (
                        None if any(0xDC80 <= ord(c) <= 0xDCFF for c in text) else text
                    )
                    self.record(generation)
                generation.response_complete = True
                self.record(generation)
                if not response.is_success:
                    raise RuntimeError(
                        f"Native inference failed: HTTP {response.status_code}"
                    )
                parsed = NativeResponse.model_validate_json(bytes(body))
                generation.response = json.loads(body)
                generation.completion_text = parsed.choices[0].message.content
                generation.stop_reason = parsed.choices[0].finish_reason
                if parsed.usage is not None:
                    generation.input_tokens = parsed.usage.prompt_tokens
                    generation.output_tokens = parsed.usage.completion_tokens
        finally:
            original_error = sys.exception()
            cleanup_deadline = prepared.cleanup.begin(
                self.shutdown_deadline
                if self.shutdown_deadline is not None
                else monotonic() + 2
            )
            if response is not None:
                generation.response_reader_joined = await self.settle(
                    response.aclose(), cleanup_deadline
                )
            generation.transport_cleanup_joined = await self.settle(
                client.aclose(), cleanup_deadline
            )
            generation.latency_ms = round((monotonic() - started) * 1000)
            self.record(generation)
        if original_error is None and (
            generation.response_reader_joined is False
            or generation.transport_cleanup_joined is False
        ):
            raise RuntimeError("Native inference cleanup did not join")
        return generation

    @staticmethod
    async def settle(
        operation: Coroutine[object, object, None], deadline: float
    ) -> bool:
        task = asyncio.create_task(operation)
        done, pending = await asyncio.wait(
            [task], timeout=max(0, deadline - monotonic())
        )
        if pending:
            task.cancel()
            return False
        for result in done:
            if result.cancelled() or result.exception() is not None:
                return False
        return True
