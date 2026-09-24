"""LLM client seam for meeting chat/vote decisions."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, NamedTuple, Protocol
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict

from crewborg.strategy.meeting.prompts import PROMPT_DIR_ENV, system_prompt_for_context
from crewborg.strategy.meeting.schema import VOTE_SKIP, MeetingDecision

DEFAULT_MEETING_MODEL = "claude-haiku-4-5-20251001"
JEV_MEETING_MODEL = "typesafe/jev-1.13"


@dataclass(frozen=True)
class MeetingLLMConfig:
    model: str = DEFAULT_MEETING_MODEL
    use_bedrock: bool = False
    max_tokens: int = 512
    timeout_seconds: float = 3.0
    trace_raw: bool = False
    prompt_dir: str | None = None


class MeetingLLMResult(BaseModel):
    """A parsed LLM decision plus call metadata for tracing."""

    model_config = ConfigDict(extra="forbid")

    decision: MeetingDecision
    model: str
    latency_ms: float
    usage: dict[str, Any] | None = None
    raw_request: dict[str, Any] | None = None
    raw_response: str | None = None


class JevAnswer(BaseModel):
    type: str
    choice: str
    confidence: float
    probabilities: dict[str, float]


class JevUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    cost: float | None = None


class JevResponse(BaseModel):
    answers: dict[str, JevAnswer]
    usage: JevUsage


class MeetingLLMClient(Protocol):
    enabled: bool
    disabled_reason: str | None

    def decide(self, context: dict[str, Any], *, trigger: str) -> MeetingLLMResult: ...


@dataclass(frozen=True)
class DisabledMeetingClient:
    disabled_reason: str = "disabled"
    enabled: bool = False

    def decide(self, context: dict[str, Any], *, trigger: str) -> MeetingLLMResult:
        del context, trigger
        raise RuntimeError(self.disabled_reason)


class AnthropicMeetingClient:
    """Anthropic Messages API adapter, kept behind the meeting-client protocol."""

    enabled = True
    disabled_reason = None

    def __init__(
        self,
        config: MeetingLLMConfig,
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

    def decide(self, context: dict[str, Any], *, trigger: str) -> MeetingLLMResult:
        request = {
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
        user_content = json.dumps(request, sort_keys=True, separators=(",", ":"))
        call = self._call_json(
            self._client,
            model=self.config.model,
            system=system_prompt_for_context(context, prompt_dir=self.config.prompt_dir),
            user=user_content,
            max_tokens=self.config.max_tokens,
        )
        decision = MeetingDecision.model_validate_json(self._extract_json_object(call.text))
        return MeetingLLMResult(
            decision=decision,
            model=call.model,
            latency_ms=call.latency_ms,
            usage=call.usage,
            raw_request=request if self.config.trace_raw else None,
            raw_response=call.text if self.config.trace_raw else None,
        )


class JevMeetingClient:
    """Rank legal meeting votes with Jev; Crewborg keeps movement and vote execution."""

    enabled = True
    disabled_reason = None

    def __init__(
        self,
        *,
        endpoint: str,
        headers: dict[str, str],
        model: str = JEV_MEETING_MODEL,
        timeout_seconds: float = 3.0,
        trace_raw: bool = False,
    ) -> None:
        self.endpoint = endpoint
        self.headers = headers
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.trace_raw = trace_raw

    def decide(self, context: dict[str, Any], *, trigger: str) -> MeetingLLMResult:
        targets = context["constraints"]["valid_vote_targets"]
        criteria = {target: "Skip the vote" if target == VOTE_SKIP else f"Vote out {target}" for target in targets}
        request = {
            "model": self.model,
            "state": context,
            "questions": {
                "vote": {
                    "type": "choice",
                    "instructions": (
                        "Choose the legal vote that maximizes your own team's chance to win. "
                        "Use your private role and teammate information, public discussion, and observed evidence. "
                        "As crew, avoid ejecting a teammate without evidence. As an imposter, protect your teammates."
                    ),
                    "criteria": criteria,
                }
            },
        }
        started = time.monotonic()
        http_request = Request(
            f"{self.endpoint}/v1/systemone",
            data=json.dumps(request).encode(),
            headers={**self.headers, "Content-Type": "application/json"},
        )
        with urlopen(http_request, timeout=self.timeout_seconds) as response:
            raw_response = response.read()
        payload = JevResponse.model_validate_json(raw_response)
        answer = payload.answers["vote"]
        if answer.type != "choice" or answer.choice not in criteria or set(answer.probabilities) != set(criteria):
            raise ValueError("Jev returned the wrong vote target set")
        if not 0 <= answer.confidence <= 1 or any(not 0 <= value <= 1 for value in answer.probabilities.values()):
            raise ValueError("Jev returned a probability outside [0, 1]")
        if abs(sum(answer.probabilities.values()) - 1) > len(criteria) * 0.005 + 1e-6:
            raise ValueError("Jev probabilities do not sum to one")
        choice = max(criteria, key=answer.probabilities.__getitem__)
        if answer.probabilities[answer.choice] == answer.probabilities[choice]:
            choice = answer.choice
        usage = payload.usage.model_dump(exclude_none=True)
        if "cost" in usage:
            usage["cost_usd"] = usage.pop("cost")
        return MeetingLLMResult(
            decision=MeetingDecision(
                action="submit_vote" if trigger == "deadline" else "set_tentative_vote",
                vote_target=choice,
                reason="Jev meeting vote",
                confidence=answer.confidence,
            ),
            model=self.model,
            latency_ms=(time.monotonic() - started) * 1000,
            usage=usage,
            raw_request=request if self.trace_raw else None,
            raw_response=raw_response.decode() if self.trace_raw else None,
        )


def build_meeting_llm_client_from_env(env: dict[str, str] | None = None) -> MeetingLLMClient:
    env = env or os.environ
    if env.get("CREWBORG_LLM_MEETINGS", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return DisabledMeetingClient("CREWBORG_LLM_MEETINGS is not enabled")
    if env.get("CREWBORG_MEETING_BACKEND") == "jev":
        sidecar = env.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME")
        capture = env.get("METTA_CAPTURE_URL")
        if sidecar:
            endpoint = sidecar.rstrip("/")
            headers = {}
            default_model = JEV_MEETING_MODEL
        elif capture:
            if not env.get("METTA_CAPTURE_KEY"):
                return DisabledMeetingClient("METTA_CAPTURE_KEY is required for Jev capture")
            endpoint = capture.rstrip("/")
            headers = {
                "Authorization": f"Bearer {env['METTA_CAPTURE_KEY']}",
                "X-Metta-Trajectory-Id": "crewrift-jev-meeting",
            }
            default_model = JEV_MEETING_MODEL
        elif env.get("TYPESAFE_API_KEY"):
            endpoint = env.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
            headers = {"Authorization": f"Bearer {env['TYPESAFE_API_KEY']}"}
            default_model = "jev-latest"
        else:
            if not env.get("OPENROUTER_API_KEY"):
                return DisabledMeetingClient("no Jev backend configured")
            endpoint = "https://openrouter.ai/api"
            headers = {"Authorization": f"Bearer {env['OPENROUTER_API_KEY']}"}
            default_model = JEV_MEETING_MODEL
        return JevMeetingClient(
            endpoint=endpoint,
            headers=headers,
            model=env.get("CREWBORG_LLM_MODEL", default_model),
            timeout_seconds=_env_float(env, "CREWBORG_LLM_TIMEOUT_SECONDS", 3.0),
            trace_raw=env.get("CREWBORG_LLM_TRACE_RAW", "").strip().lower() in {"1", "true", "yes", "on"},
        )
    try:
        helpers = _load_sdk_helpers()
        # Sidecar mode strips USE_BEDROCK from the player container and injects
        # AWS_ENDPOINT_URL_BEDROCK_RUNTIME instead, so the SDK's bedrock_enabled() (which only
        # checks USE_BEDROCK/CLAUDE_CODE_USE_BEDROCK) reports no backend in-pod. Gate on what we
        # actually receive: treat the sidecar endpoint as a Bedrock signal. See
        # docs/reference/coworld-platform.md.
        use_bedrock = helpers.bedrock_enabled(env) or _sidecar_bedrock(env)
        if not use_bedrock and not env.get("ANTHROPIC_API_KEY"):
            return DisabledMeetingClient("no LLM backend configured")
        trace_raw = env.get("CREWBORG_LLM_TRACE_RAW", "").strip().lower() in {"1", "true", "yes", "on"}
        trace_raw = trace_raw or env.get("CREWBORG_TRACE", "").strip().lower() == "debug"
        timeout_seconds = _env_float(env, "CREWBORG_LLM_TIMEOUT_SECONDS", 3.0)
        config = MeetingLLMConfig(
            model=helpers.resolve_model(
                use_bedrock=use_bedrock,
                direct_model=helpers.default_direct_model,
                bedrock_model=helpers.default_bedrock_model,
                explicit=env.get("CREWBORG_LLM_MODEL"),
            ),
            use_bedrock=use_bedrock,
            max_tokens=_env_int(env, "CREWBORG_LLM_MAX_TOKENS", 512),
            timeout_seconds=timeout_seconds,
            trace_raw=trace_raw,
            prompt_dir=env.get(PROMPT_DIR_ENV) or None,
        )
        client = helpers.select_client(use_bedrock=use_bedrock, timeout=timeout_seconds)
        return AnthropicMeetingClient(
            config,
            client=client,
            call_json=helpers.call_json,
            extract_json_object=helpers.extract_json_object,
        )
    except Exception as exc:
        return DisabledMeetingClient(f"meeting LLM client construction failed: {exc!r}")


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


#: Loopback Bedrock proxy endpoint the runner injects in sidecar mode (it strips USE_BEDROCK).
BEDROCK_SIDECAR_ENDPOINT_ENV = "AWS_ENDPOINT_URL_BEDROCK_RUNTIME"


def _sidecar_bedrock(env: dict[str, str]) -> bool:
    """Whether the Bedrock sidecar endpoint is present (the in-pod Bedrock signal)."""

    return bool(env.get(BEDROCK_SIDECAR_ENDPOINT_ENV, "").strip())


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
