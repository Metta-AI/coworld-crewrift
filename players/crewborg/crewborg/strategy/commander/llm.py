"""LLM client seam for gameplay-commander priority decisions (backend selection + call).

Builds the client the worker uses to turn serialized belief into raw priority JSON, and
decides *which* backend that client talks to. Two backends are possible — Anthropic direct
(``ANTHROPIC_API_KEY``) or AWS Bedrock — both reached through the player SDK helpers behind
the ``CommanderLLMClient`` protocol so the worker is backend-agnostic.

Gating / "off = inert": ``build_commander_client_from_env`` returns a
``DisabledCommanderClient`` (which raises if ``decide`` is ever called) unless
``CREWBORG_LLM_COMMANDER`` is truthy AND a usable backend is configured. Any construction
error also degrades to disabled rather than raising — so a missing key, missing SDK, or bad
config can never crash the agent; it just falls back to deterministic play.

Bedrock sidecar gotcha (see ``_sidecar_bedrock`` and the inline comment in
``build_commander_client_from_env``): in sidecar mode the runner strips ``USE_BEDROCK`` and
injects a loopback proxy endpoint instead, so the SDK's ``bedrock_enabled()`` under-reports.
We additionally treat the presence of ``AWS_ENDPOINT_URL_BEDROCK_RUNTIME`` as a Bedrock
signal so ``select_client(use_bedrock=True)`` reaches the SDK's sidecar-routing path.

Collaborators
-------------
Relies on:
  - ``players.player_sdk`` helpers (lazily imported in ``_load_sdk_helpers``): ``bedrock_enabled``,
    ``select_client``, ``resolve_model``, ``call_json``, ``extract_json_object``, and the default
    model constants — the SDK owns the actual transport, retries, and JSON extraction.
  - ``prompts.system_prompt_for_role`` — role-specific system prompt for each call.
Used by:
  - ``worker.CommanderWorker`` — calls ``build_commander_client_from_env`` (its client factory)
    and invokes ``client.decide(context)`` on the daemon thread.
  - ``__init__.build_runtime`` / ``CommanderStrategy`` — ``commander_feature_enabled`` is the
    master on/off check.

Modifying this file: the disabled-by-default contract is load-bearing — every error and
every "no flag / no backend" case must return a ``DisabledCommanderClient``, never raise.
``decide`` reads untrusted model text; it stays a thin wrapper and leaves validation to
``schema.sanitize_priorities`` downstream.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, NamedTuple, Protocol

from pydantic import BaseModel, ConfigDict

from crewborg.strategy.commander.prompts import PROMPT_DIR_ENV, system_prompt_for_role

#: Fallback model id if the SDK's ``resolve_model`` is not given an explicit override.
#: A small, fast Claude model — the commander runs every few seconds and only needs to
#: emit a tiny JSON object, so latency matters more than peak reasoning.
DEFAULT_COMMANDER_MODEL = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class CommanderLLMConfig:
    """Immutable per-call LLM settings, resolved once from env at client-build time.

    ``use_bedrock`` selects the backend (incl. the sidecar path); ``temperature`` is kept low
    for stable, near-deterministic priority choices; ``timeout_seconds`` bounds how long the
    daemon waits on a call; ``trace_raw`` opts the raw request/response into the trace (verbose
    debugging only); ``prompt_dir`` overrides where role doctrine Markdown is loaded from."""

    model: str = DEFAULT_COMMANDER_MODEL
    use_bedrock: bool = False
    max_tokens: int = 512
    temperature: float = 0.2
    timeout_seconds: float = 3.0
    trace_raw: bool = False
    prompt_dir: str | None = None


class CommanderLLMResult(BaseModel):
    """Raw commander priorities plus call metadata for tracing.

    ``priorities`` is the *unvalidated* JSON object from the model (sanitized later by
    ``schema.py``); the rest is telemetry — ``model``/``latency_ms``/``usage`` always present,
    and ``raw_request``/``raw_response`` populated only when ``trace_raw`` is on."""

    model_config = ConfigDict(extra="forbid")

    priorities: dict[str, Any]
    model: str
    latency_ms: float
    usage: dict[str, Any] | None = None
    raw_request: dict[str, Any] | None = None
    raw_response: str | None = None


class CommanderLLMClient(Protocol):
    """Structural contract every commander client satisfies.

    ``enabled`` tells the worker whether ``decide`` may be called; ``disabled_reason`` carries a
    human-readable cause when not (also used by the worker to decide whether a build is worth
    retrying — see ``_missing_backend``). ``decide`` maps a serialized context to a result."""

    enabled: bool
    disabled_reason: str | None

    def decide(self, context: dict[str, Any]) -> CommanderLLMResult: ...


@dataclass(frozen=True)
class DisabledCommanderClient:
    """The inert client returned whenever the commander is off or unconfigured.

    ``enabled`` is ``False`` so the worker never calls ``decide``; if something does, it raises
    with ``disabled_reason`` rather than silently producing priorities."""

    disabled_reason: str = "disabled"
    enabled: bool = False

    def decide(self, context: dict[str, Any]) -> CommanderLLMResult:
        del context
        raise RuntimeError(self.disabled_reason)


class AnthropicCommanderClient:
    """Live client: builds the request, calls the SDK, parses model text into raw priorities.

    Holds the resolved ``config`` and the SDK callables injected at construction (``client``
    transport, ``call_json`` request helper, ``extract_json_object`` text→JSON extractor) — so
    this class stays a thin, testable adapter with no direct SDK import. Always ``enabled``."""

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
        """Per-call timeout (seconds) from config; exposed for the worker/transport."""
        return self.config.timeout_seconds

    def decide(self, context: dict[str, Any]) -> CommanderLLMResult:
        """Call the model once and return the parsed (still-unvalidated) priorities + telemetry.

        Wraps ``context`` and an inline ``response_schema`` (the field menu the model fills in)
        into the user message, selects the system prompt from ``context["self"]["role"]``, calls
        the SDK, and parses the first JSON object out of the reply text. The returned
        ``priorities`` are NOT validated here — ``schema.sanitize_priorities`` enforces legality
        downstream. Side effects: one network/LLM round-trip. Raises if the call or JSON parse
        fails (the worker catches and traces it as a ``commander_call`` error)."""
        # The field menu shown to the model. By design ``strength`` (soft/hard) is NOT listed
        # here, so the LLM cannot set it — LLM priorities are always "soft" (bias only); "hard"
        # forcing is a test/QA path reachable only via CREWBORG_COMMANDER_FORCE (see schema.py).
        # DANGER fields require a ``danger_reason``.
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
    """Construct the commander client from environment, or a ``DisabledCommanderClient``.

    The single gated entry point (defaults to ``os.environ``; ``env`` injectable for tests).
    Returns a disabled client when the feature flag is off, when no backend (Bedrock/sidecar or
    ``ANTHROPIC_API_KEY``) is configured, or when construction raises — so callers can rely on a
    client object always coming back and never crashing the agent. On success, resolves the
    model/backend, builds an SDK client, and wraps it in an ``AnthropicCommanderClient``."""
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
        # docs/issues/2026-06-26-bedrock-disabled-crewrift-prime-xp.md (platform/SDK fix tracked there).
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


#: Backward-compatible alias for the env-based builder (the worker's client factory).
build_commander_client = build_commander_client_from_env


def commander_feature_enabled(env: dict[str, str] | None = None) -> bool:
    """Whether the commander master flag (``CREWBORG_LLM_COMMANDER``) is truthy.

    The cheap top-level on/off check used by ``CommanderStrategy`` before any worker is started;
    distinct from ``build_commander_client_from_env``, which also requires a configured backend."""
    env = os.environ if env is None else env
    return _truthy(env.get("CREWBORG_LLM_COMMANDER", ""))


class _SDKHelpers(NamedTuple):
    """Bundle of the player-SDK callables/constants the client needs, captured at import time.

    Lets ``build_commander_client_from_env`` depend on a single struct instead of importing the
    SDK at module load — so importing this module never hard-requires the SDK to be installed."""

    bedrock_enabled: Callable[..., bool]
    select_client: Callable[..., Any]
    resolve_model: Callable[..., str]
    call_json: Callable[..., Any]
    extract_json_object: Callable[[str], str]
    default_bedrock_model: str
    default_direct_model: str


def _load_sdk_helpers() -> _SDKHelpers:
    """Lazily import the player-SDK helpers/constants and bundle them into an ``_SDKHelpers``.

    Deferred to call time so a missing SDK only fails the (already error-trapped) client build,
    not module import."""
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
    """Read an int env var, falling back to ``default`` on absence or non-integer text."""
    try:
        return int(env.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(env: dict[str, str], name: str, default: float) -> float:
    """Read a float env var, falling back to ``default`` on absence or non-numeric text."""
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
    """Parse a flag-style string as boolean: true for 1/true/yes/on (case/space-insensitive)."""
    return value.strip().lower() in {"1", "true", "yes", "on"}
