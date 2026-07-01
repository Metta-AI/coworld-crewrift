"""LLM client seam for gameplay commander priority decisions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, NamedTuple, Protocol

from pydantic import BaseModel, ConfigDict

from crewborg.strategy.commander.prompts import PROMPT_DIR_ENV, system_prompt_for_role

DEFAULT_COMMANDER_MODEL = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class CommanderLLMConfig:
    model: str = DEFAULT_COMMANDER_MODEL
    use_bedrock: bool = False
    max_tokens: int = 512
    temperature: float = 0.2
    timeout_seconds: float = 3.0
    trace_raw: bool = False
    prompt_dir: str | None = None


class CommanderLLMResult(BaseModel):
    """Raw commander priorities plus call metadata for tracing."""

    model_config = ConfigDict(extra="forbid")

    priorities: dict[str, Any]
    model: str
    latency_ms: float
    usage: dict[str, Any] | None = None
    raw_request: dict[str, Any] | None = None
    raw_response: str | None = None


class CommanderLLMClient(Protocol):
    enabled: bool
    disabled_reason: str | None

    def decide(self, context: dict[str, Any]) -> CommanderLLMResult: ...


@dataclass(frozen=True)
class DisabledCommanderClient:
    disabled_reason: str = "disabled"
    enabled: bool = False

    def decide(self, context: dict[str, Any]) -> CommanderLLMResult:
        del context
        raise RuntimeError(self.disabled_reason)


class AnthropicCommanderClient:
    """Anthropic Messages API adapter, kept behind the commander-client protocol."""

    enabled = True
    disabled_reason = None

    def __init__(
        self,
        config: CommanderLLMConfig,
        *,
        client: Any,
        call_json: Callable[..., Any],
        extract_json_object: Callable[[str], str],
    ) -> None:
        self.config = config
        self._client = client
        self._call_json = call_json
        self._extract_json_object = extract_json_object

    @property
    def timeout_seconds(self) -> float:
        return self.config.timeout_seconds

    def decide(self, context: dict[str, Any]) -> CommanderLLMResult:
        request = {
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
        user_content = json.dumps(request, sort_keys=True, separators=(",", ":"))
        call = self._call_json(
            self._client,
            model=self.config.model,
            system=system_prompt_for_role(context.get("self", {}).get("role"), prompt_dir=self.config.prompt_dir),
            user=user_content,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )
        priorities = json.loads(self._extract_json_object(call.text))
        return CommanderLLMResult(
            priorities=priorities,
            model=call.model,
            latency_ms=call.latency_ms,
            usage=call.usage,
            raw_request=request if self.config.trace_raw else None,
            raw_response=call.text if self.config.trace_raw else None,
        )


def build_commander_client_from_env(env: dict[str, str] | None = None) -> CommanderLLMClient:
    env = os.environ if env is None else env
    if not _truthy(env.get("CREWBORG_LLM_COMMANDER", "")):
        return DisabledCommanderClient("CREWBORG_LLM_COMMANDER is not enabled")
    try:
        helpers = _load_sdk_helpers()
        # Sidecar mode (BEDROCK_SIDECAR_ENABLED) strips USE_BEDROCK / direct AWS identity
        # from the player container and instead injects the loopback Bedrock proxy endpoint
        # AWS_ENDPOINT_URL_BEDROCK_RUNTIME (+ dummy creds). The SDK's bedrock_enabled() only
        # checks USE_BEDROCK / CLAUDE_CODE_USE_BEDROCK, so it wrongly reports no backend in-pod.
        # Gate on what we actually receive: treat the sidecar endpoint as a Bedrock signal, so
        # select_client(use_bedrock=True) reaches the SDK's sidecar-routing path. See
        # docs/reference/coworld-platform.md (platform/SDK fix tracked there).
        use_bedrock = helpers.bedrock_enabled(env) or _sidecar_bedrock(env)
        if not use_bedrock and not env.get("ANTHROPIC_API_KEY"):
            return DisabledCommanderClient("no LLM backend configured")
        trace_raw = _truthy(env.get("CREWBORG_LLM_TRACE_RAW", ""))
        trace_raw = trace_raw or env.get("CREWBORG_TRACE", "").strip().lower() == "debug"
        timeout_seconds = _env_float(env, "CREWBORG_LLM_TIMEOUT_SECONDS", 3.0)
        config = CommanderLLMConfig(
            model=helpers.resolve_model(
                use_bedrock=use_bedrock,
                direct_model=helpers.default_direct_model,
                bedrock_model=helpers.default_bedrock_model,
                explicit=env.get("CREWBORG_LLM_MODEL"),
            ),
            use_bedrock=use_bedrock,
            max_tokens=_env_int(env, "CREWBORG_LLM_MAX_TOKENS", 512),
            temperature=_env_float(env, "CREWBORG_LLM_TEMPERATURE", 0.2),
            timeout_seconds=timeout_seconds,
            trace_raw=trace_raw,
            prompt_dir=env.get(PROMPT_DIR_ENV) or None,
        )
        client = helpers.select_client(use_bedrock=use_bedrock, timeout=timeout_seconds)
        return AnthropicCommanderClient(
            config,
            client=client,
            call_json=helpers.call_json,
            extract_json_object=helpers.extract_json_object,
        )
    except Exception as exc:
        return DisabledCommanderClient(f"commander LLM client construction failed: {exc!r}")


build_commander_client = build_commander_client_from_env


def commander_feature_enabled(env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return _truthy(env.get("CREWBORG_LLM_COMMANDER", ""))


class _SDKHelpers(NamedTuple):
    bedrock_enabled: Callable[..., bool]
    select_client: Callable[..., Any]
    resolve_model: Callable[..., str]
    call_json: Callable[..., Any]
    extract_json_object: Callable[[str], str]
    default_bedrock_model: str
    default_direct_model: str


def _load_sdk_helpers() -> _SDKHelpers:
    from players.player_sdk import (
        DEFAULT_BEDROCK_MODEL,
        DEFAULT_DIRECT_MODEL,
        bedrock_enabled,
        call_json,
        extract_json_object,
        resolve_model,
        select_client,
    )

    return _SDKHelpers(
        bedrock_enabled=bedrock_enabled,
        select_client=select_client,
        resolve_model=resolve_model,
        call_json=call_json,
        extract_json_object=extract_json_object,
        default_bedrock_model=DEFAULT_BEDROCK_MODEL,
        default_direct_model=DEFAULT_DIRECT_MODEL,
    )


def _env_int(env: dict[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(env: dict[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, default))
    except (TypeError, ValueError):
        return default


#: The loopback Bedrock proxy endpoint the runner injects in sidecar mode (it strips
#: USE_BEDROCK). Its presence means Bedrock IS available via the sidecar.
BEDROCK_SIDECAR_ENDPOINT_ENV = "AWS_ENDPOINT_URL_BEDROCK_RUNTIME"


def _sidecar_bedrock(env: dict[str, str]) -> bool:
    """Whether the Bedrock sidecar endpoint is present (the in-pod Bedrock signal)."""

    return bool(env.get(BEDROCK_SIDECAR_ENDPOINT_ENV, "").strip())


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
