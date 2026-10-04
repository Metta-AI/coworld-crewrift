"""Meeting requests use the native endpoint and player-owned request lifetime."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from pydantic import Field, JsonValue

from players.crewrift.crewborg.native import (
    ControllerInstallation,
    NativeGeneration,
    NativeModel,
    NativeRequest,
    NativeSession,
    decision_json,
)
from players.crewrift.crewborg.strategy.meeting.prompts import build_system_prompt
from players.crewrift.crewborg.strategy.meeting.schema import VOTE_SKIP, MeetingDecision
from players.crewrift.crewborg.types import Intent
from players.player_sdk import ModeParams

DEFAULT_MEETING_MODEL = "anthropic/claude-haiku-4.5"


class MeetingParams(ModeParams):
    model_config = NativeModel.model_config
    use_llm: bool = False
    model: str = DEFAULT_MEETING_MODEL
    max_tokens: int = Field(default=512, gt=0)
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: float = Field(default=3.0, gt=0)


class MeetingLLMResult(NativeModel):
    decision: MeetingDecision
    generation: NativeGeneration

    @property
    def model(self) -> str:
        return self.generation.request.model

    @property
    def latency_ms(self) -> int:
        assert self.generation.latency_ms is not None
        return self.generation.latency_ms


class MeetingLLMClient(Protocol):
    @property
    def enabled(self) -> bool: ...

    @property
    def disabled_reason(self) -> str | None: ...

    def decide(
        self, context: dict, *, trigger: str
    ) -> asyncio.Task[MeetingLLMResult]: ...

    def cancel(self, cleanup_deadline: float) -> None: ...

    def joined(self) -> bool: ...

    def installed(
        self,
        result: MeetingLLMResult,
        decision: MeetingDecision,
        intent: Intent,
        tick: int,
    ) -> None: ...


@dataclass(frozen=True)
class DisabledMeetingClient:
    disabled_reason: str | None = "disabled"
    enabled: bool = False

    def decide(self, context: dict, *, trigger: str) -> asyncio.Task[MeetingLLMResult]:
        raise RuntimeError(self.disabled_reason)

    def cancel(self, cleanup_deadline: float) -> None:
        pass

    def joined(self) -> bool:
        return True

    def installed(
        self,
        result: MeetingLLMResult,
        decision: MeetingDecision,
        intent: Intent,
        tick: int,
    ) -> None:
        raise RuntimeError("Disabled meeting policy cannot install model decisions")


class NativeMeetingClient:
    enabled = True
    disabled_reason = None

    def __init__(self, config: MeetingParams, session: NativeSession):
        self.config = config
        self.session = session

    def cancel(self, cleanup_deadline: float) -> None:
        self.session.cancel_phase("meeting", cleanup_deadline)

    def joined(self) -> bool:
        return self.session.phase_joined("meeting")

    def installed(
        self,
        result: MeetingLLMResult,
        decision: MeetingDecision,
        intent: Intent,
        tick: int,
    ) -> None:
        result.generation.controller_installations.append(
            ControllerInstallation(
                kind="meeting_decision",
                tick=tick,
                value=decision.model_dump(mode="json"),
                intent=intent.model_dump(mode="json"),
            )
        )
        self.session.record(result.generation)

    @property
    def timeout_seconds(self) -> float:
        return self.config.timeout_seconds

    def decide(self, context: dict, *, trigger: str) -> asyncio.Task[MeetingLLMResult]:
        prompt: dict[str, JsonValue] = {
            "trigger": trigger,
            "context": context,
            "response_schema": {
                "schema_version": 1,
                "action": "send_chat | set_tentative_vote | submit_vote | wait",
                "chat_text": "string or null",
                "vote_target": f"player color, {VOTE_SKIP}, or null",
                "reason": "short rationale",
                "confidence": "0.0 to 1.0 or null",
            },
        }
        request = NativeRequest(
            model=self.config.model,
            messages=[
                {
                    "role": "system",
                    "content": build_system_prompt(context["self"]["role"]),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        prompt, sort_keys=True, separators=(",", ":")
                    ),
                },
            ],
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )
        prepared = self.session.prepare(
            request,
            phase="meeting",
            observation_tick=int(context["meeting"]["tick"]),
            deadline=monotonic() + self.config.timeout_seconds,
        )

        async def finish() -> MeetingLLMResult:
            generation = await self.session.complete(prepared)
            assert generation.completion_text is not None
            decision = MeetingDecision.model_validate_json(
                decision_json(generation.completion_text)
            )
            generation.parsed_action = decision.model_dump(mode="json")
            self.session.record(generation)
            return MeetingLLMResult(decision=decision, generation=generation)

        return self.session.start(prepared, finish())


def read_meeting_params_from_env(env: Mapping[str, str] | None = None) -> MeetingParams:
    env = os.environ if env is None else env
    enabled = bool(env.get("COWORLD_LLM_ENDPOINT")) or env.get(
        "CREWBORG_LLM_MEETINGS", ""
    ).lower() in {"1", "true", "yes", "on"}
    if enabled and not env["COWORLD_LLM_ENDPOINT"]:
        raise ValueError("Native meeting endpoint must be nonempty")
    return MeetingParams(
        use_llm=enabled,
        model=env.get(
            "COWORLD_LLM_MODEL", env.get("CREWBORG_LLM_MODEL", DEFAULT_MEETING_MODEL)
        ),
        max_tokens=int(env.get("CREWBORG_LLM_MAX_TOKENS", "512")),
        temperature=float(
            env.get(
                "COWORLD_LLM_TEMPERATURE", env.get("CREWBORG_LLM_TEMPERATURE", "0.2")
            )
        ),
        timeout_seconds=float(env.get("CREWBORG_LLM_TIMEOUT_SECONDS", "3")),
    )


def build_meeting_client(
    params: MeetingParams, session: NativeSession
) -> MeetingLLMClient:
    if not params.use_llm:
        return DisabledMeetingClient("meeting LLM disabled")
    return NativeMeetingClient(params, session)
