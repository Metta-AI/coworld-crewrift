"""LLM client seam for meeting chat/vote decisions: backend select + Bedrock sidecar gating.

This is the boundary between the meeting layer and an actual model call. ``build_meeting_llm_client_from_env``
reads the environment and returns a client implementing the ``MeetingLLMClient`` protocol —
either a live ``AnthropicMeetingClient`` or, whenever the LLM can't or shouldn't run, a
``DisabledMeetingClient``. The factory is **total**: it never raises. Anything that goes
wrong (flag off, no backend, SDK import failure, bad config) becomes a disabled client with
a ``disabled_reason``, and the caller (``modes.attend_meeting``) takes the deterministic
fallback path. This is one half of the "deterministic fallback is never bypassed" invariant —
the LLM is strictly opt-in (``CREWBORG_LLM_MEETINGS=1``) and strictly best-effort.

Backend selection (gating logic lives in ``build_meeting_llm_client_from_env``):
  - The opt-in flag must be set, else ``DisabledMeetingClient``.
  - Bedrock is used if the SDK's ``bedrock_enabled(env)`` says so **or** the loopback
    sidecar endpoint is present. The sidecar case matters: the hosted runner strips
    ``USE_BEDROCK`` from the player container and injects ``AWS_ENDPOINT_URL_BEDROCK_RUNTIME``
    instead, so the SDK's own check reports no backend in-pod; ``_sidecar_bedrock`` treats
    that endpoint as the real Bedrock signal (see the issue doc referenced inline).
  - With no Bedrock and no ``ANTHROPIC_API_KEY``, there is no backend → disabled.
  - Model id, max tokens, temperature, timeout, trace flags, and prompt dir are read from
    ``CREWBORG_LLM_*`` env, with the actual model resolved by the SDK's ``resolve_model``.

Collaborators
-------------
Relies on:
  - ``players.player_sdk`` (lazy-imported in ``_load_sdk_helpers``) — ``bedrock_enabled``,
    ``select_client``, ``resolve_model``, ``call_json``, ``extract_json_object``, and the
    default model ids. The SDK is what actually talks to Anthropic/Bedrock; this module
    only wires it behind the protocol.
  - ``schema.MeetingDecision`` — parses the model's JSON into the validated decision.
  - ``prompts.system_prompt_for_context`` — role-specific system prompt for each call.
Used by: ``modes.attend_meeting`` (constructs a client in ``__init__``, calls ``.decide``).

Modifying this file: preserve totality — ``build_meeting_llm_client_from_env`` must return a
client, never raise, so the fallback path is always reachable. Keep the sidecar-endpoint
gate; without it, hosted Bedrock games silently run LLM-off (the exact bug the inline doc
links). The chat/vote shape the model is asked for must stay in sync with ``schema.py``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, NamedTuple, Protocol

from pydantic import BaseModel, ConfigDict

from crewborg.strategy.meeting.prompts import PROMPT_DIR_ENV, system_prompt_for_context
from crewborg.strategy.meeting.schema import VOTE_SKIP, MeetingDecision

#: Fallback model id for the dataclass default only. In practice the live config's model is
#: chosen by the SDK's ``resolve_model`` (direct vs Bedrock id, or a ``CREWBORG_LLM_MODEL``
#: override), so this is rarely the id actually used.
DEFAULT_MEETING_MODEL = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class MeetingLLMConfig:
    """Immutable per-build LLM settings, assembled from ``CREWBORG_LLM_*`` env.

    Attributes:
      - ``model``: resolved model id passed to the SDK call.
      - ``use_bedrock``: whether to route via Bedrock (vs the direct Anthropic API).
      - ``max_tokens`` / ``temperature``: generation knobs (low temperature for steadier
        meeting behavior).
      - ``timeout_seconds``: per-call wall-clock budget; the mode also uses it to decide how
        early it must stop calling so a late call can't miss the vote deadline.
      - ``trace_raw``: when set, the result carries the raw request/response for debugging.
      - ``prompt_dir``: optional override directory for the role prompt files.
    """

    model: str = DEFAULT_MEETING_MODEL
    use_bedrock: bool = False
    max_tokens: int = 512
    temperature: float = 0.2
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


class MeetingLLMClient(Protocol):
    """Structural type for a meeting client. ``enabled`` lets the caller branch to the
    deterministic path without a model call; ``disabled_reason`` explains why (for tracing).
    ``decide`` maps serialized context → a parsed ``MeetingLLMResult`` for one tick. Both the
    live and disabled clients below conform; tests supply their own fakes."""

    enabled: bool
    disabled_reason: str | None

    def decide(self, context: dict[str, Any], *, trigger: str) -> MeetingLLMResult: ...


@dataclass(frozen=True)
class DisabledMeetingClient:
    """The "no LLM" client returned whenever the model is off or unavailable. ``enabled`` is
    ``False`` so the caller never calls ``decide``; if something does call it, it raises
    (a programming error, not a runtime path). ``disabled_reason`` records why it's off."""

    disabled_reason: str = "disabled"
    enabled: bool = False

    def decide(self, context: dict[str, Any], *, trigger: str) -> MeetingLLMResult:
        del context, trigger
        raise RuntimeError(self.disabled_reason)


class AnthropicMeetingClient:
    """Anthropic Messages API adapter, kept behind the meeting-client protocol.

    Holds the resolved ``config`` plus the SDK callables injected at build time (the raw
    ``client``, ``call_json`` to do one structured request, ``extract_json_object`` to pull
    the JSON body out of the model's text). Stateless per call; ``decide`` does one request
    and parses the response into a ``MeetingDecision``."""

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
        """Run one model call for the given meeting context and return the parsed decision
        plus call metadata (model, latency, token usage). ``context`` is the serialized
        belief (``context.serialize_meeting_context``); ``trigger`` names why we're calling
        this tick (``meeting_start`` / ``new_chat`` / ``deadline`` / …) and is echoed into
        the prompt. The user message is the context + the expected response schema as compact
        JSON; the system prompt is role-selected. Raises if the model output won't parse into
        a ``MeetingDecision`` (the caller catches it and falls back)."""

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
            temperature=self.config.temperature,
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


def build_meeting_llm_client_from_env(env: dict[str, str] | None = None) -> MeetingLLMClient:
    """Construct the meeting client from the environment. **Never raises**: returns a live
    ``AnthropicMeetingClient`` when the LLM is enabled and a backend is reachable, otherwise
    a ``DisabledMeetingClient`` carrying the reason. ``env`` defaults to ``os.environ`` and
    is injectable for tests. See the module docstring for the full gating order (opt-in flag
    → Bedrock-or-key backend → config from ``CREWBORG_LLM_*``). Any exception during build
    (e.g. the SDK import failing) is swallowed into a disabled client."""

    env = env or os.environ
    if env.get("CREWBORG_LLM_MEETINGS", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return DisabledMeetingClient("CREWBORG_LLM_MEETINGS is not enabled")
    try:
        helpers = _load_sdk_helpers()
        # Sidecar mode strips USE_BEDROCK from the player container and injects
        # AWS_ENDPOINT_URL_BEDROCK_RUNTIME instead, so the SDK's bedrock_enabled() (which only
        # checks USE_BEDROCK/CLAUDE_CODE_USE_BEDROCK) reports no backend in-pod. Gate on what we
        # actually receive: treat the sidecar endpoint as a Bedrock signal. See
        # docs/reference/coworld-platform.md (Bedrock section).
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
            temperature=_env_float(env, "CREWBORG_LLM_TEMPERATURE", 0.2),
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
    """The bundle of ``players.player_sdk`` callables/defaults this module needs, grouped so
    they're imported once and passed around as one value (keeps the SDK import lazy)."""

    bedrock_enabled: Callable[..., bool]
    select_client: Callable[..., Any]
    resolve_model: Callable[..., str]
    call_json: Callable[..., Any]
    extract_json_object: Callable[[str], str]
    default_bedrock_model: str
    default_direct_model: str


def _load_sdk_helpers() -> _SDKHelpers:
    """Lazily import the player SDK and bundle the helpers we use. Imported inside the
    function (not at module load) so importing this module never hard-depends on the SDK,
    and a missing SDK degrades to a disabled client via the caller's ``try``."""

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
    """Read ``env[name]`` as an int, falling back to ``default`` when unset or unparseable."""

    try:
        return int(env.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(env: dict[str, str], name: str, default: float) -> float:
    """Read ``env[name]`` as a float, falling back to ``default`` when unset or unparseable."""

    try:
        return float(env.get(name, default))
    except (TypeError, ValueError):
        return default
