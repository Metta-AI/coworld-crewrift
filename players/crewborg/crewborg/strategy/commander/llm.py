"""Native commander requests are owned by the player's asynchronous session."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from pydantic import Field, JsonValue

from crewborg.native import (
    NativeGeneration,
    NativeModel,
    NativeRequest,
    NativeSession,
    decision_json,
)
from crewborg.strategy.commander.prompts import PROMPT_DIR_ENV, system_prompt_for_role

DEFAULT_COMMANDER_MODEL = "anthropic/claude-haiku-4.5"


class CommanderLLMConfig(NativeModel):
    model: str = DEFAULT_COMMANDER_MODEL
    max_tokens: int = Field(default=512, gt=0)
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: float = Field(default=3, gt=0)
    prompt_dir: str | None = None


class CommanderLLMResult(NativeModel):
    priorities: dict[str, JsonValue]
    generation: NativeGeneration

    @property
    def model(self) -> str:
        return self.generation.request.model

    @property
    def latency_ms(self) -> int:
        assert self.generation.latency_ms is not None
        return self.generation.latency_ms


class CommanderLLMClient(Protocol):
    @property
    def enabled(self) -> bool: ...

    @property
    def disabled_reason(self) -> str | None: ...

    def decide(self, context: dict) -> asyncio.Task[CommanderLLMResult]: ...


@dataclass(frozen=True)
class DisabledCommanderClient:
    disabled_reason: str | None = "disabled"
    enabled: bool = False

    def decide(self, context: dict) -> asyncio.Task[CommanderLLMResult]:
        raise RuntimeError(self.disabled_reason)


class NativeCommanderClient:
    enabled = True
    disabled_reason = None

    def __init__(self, config: CommanderLLMConfig, session: NativeSession):
        self.config = config
        self.session = session

    def decide(self, context: dict) -> asyncio.Task[CommanderLLMResult]:
        prompt = {
            "context": context,
            "response_schema": {
                "schema_version": 1,
                "target_room": "legal room name or null",
                "target_task": "integer task index or null",
                "posture": "stick | isolate | neutral",
                "hunt_room": "legal room name or null",
                "target_player": "legal player color or null",
                "avoid_room": "legal room name or null",
                "allow_witnessed_kill": "boolean, DANGER",
                "skip_evade": "boolean, DANGER",
                "danger_reason": "required string when any DANGER field is true; otherwise null",
                "reason": "short rationale",
            },
        }
        request = NativeRequest(
            model=self.config.model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt_for_role(
                        context["self"]["role"], prompt_dir=self.config.prompt_dir
                    ),
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
            phase="commander",
            observation_tick=context["observation_tick"],
            deadline=monotonic() + self.config.timeout_seconds,
        )

        async def finish() -> CommanderLLMResult:
            generation = await self.session.complete(prepared)
            assert generation.completion_text is not None
            priorities = json.loads(decision_json(generation.completion_text))
            generation.parsed_action = priorities
            self.session.record(generation)
            return CommanderLLMResult(priorities=priorities, generation=generation)

        return self.session.start(prepared, finish())


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def commander_feature_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return _truthy(env.get("CREWBORG_LLM_COMMANDER", ""))


def build_commander_client_from_env(
    session: NativeSession, env: Mapping[str, str] | None = None
) -> CommanderLLMClient:
    env = os.environ if env is None else env
    if not commander_feature_enabled(env):
        return DisabledCommanderClient("CREWBORG_LLM_COMMANDER is not enabled")
    if not env["COWORLD_LLM_ENDPOINT"]:
        raise ValueError("Native commander endpoint must be nonempty")
    config = CommanderLLMConfig(
        model=env.get(
            "COWORLD_LLM_MODEL", env.get("CREWBORG_LLM_MODEL", DEFAULT_COMMANDER_MODEL)
        ),
        max_tokens=int(env.get("CREWBORG_LLM_MAX_TOKENS", "512")),
        temperature=float(
            env.get(
                "COWORLD_LLM_TEMPERATURE", env.get("CREWBORG_LLM_TEMPERATURE", "0.2")
            )
        ),
        timeout_seconds=float(env.get("CREWBORG_LLM_TIMEOUT_SECONDS", "3")),
        prompt_dir=env.get(PROMPT_DIR_ENV),
    )
    return NativeCommanderClient(config, session)
