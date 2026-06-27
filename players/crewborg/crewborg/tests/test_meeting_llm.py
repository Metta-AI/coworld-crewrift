"""Meeting LLM backend selection and totality (always-falls-back) tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from crewborg.strategy.meeting import llm as meeting_llm


@dataclass(frozen=True)
class _Call:
    text: str
    usage: dict[str, Any] | None = None
    latency_ms: float = 12.5
    model: str = "fake-model"


def _helpers(*, use_bedrock: bool, selected: list[dict[str, Any]]) -> meeting_llm._SDKHelpers:
    def bedrock_enabled(env: dict[str, str]) -> bool:
        return use_bedrock

    def select_client(*, use_bedrock: bool, timeout: float) -> object:
        selected.append({"use_bedrock": use_bedrock, "timeout": timeout})
        return object()

    def resolve_model(*, use_bedrock: bool, direct_model: str, bedrock_model: str, explicit: str | None = None) -> str:
        if explicit:
            return explicit
        return bedrock_model if use_bedrock else direct_model

    return meeting_llm._SDKHelpers(
        bedrock_enabled=bedrock_enabled,
        select_client=select_client,
        resolve_model=resolve_model,
        call_json=lambda *args, **kwargs: _Call(text='{"schema_version":1,"action":"wait"}'),
        extract_json_object=lambda text: text,
        default_bedrock_model="bedrock-default",
        default_direct_model="direct-default",
    )


def test_factory_disabled_when_flag_is_off() -> None:
    client = meeting_llm.build_meeting_llm_client_from_env({})

    assert not client.enabled
    assert client.disabled_reason == "CREWBORG_LLM_MEETINGS is not enabled"


def test_factory_selects_bedrock_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    selected: list[dict[str, Any]] = []
    monkeypatch.setattr(meeting_llm, "_load_sdk_helpers", lambda: _helpers(use_bedrock=True, selected=selected))

    client = meeting_llm.build_meeting_llm_client_from_env({"CREWBORG_LLM_MEETINGS": "1", "USE_BEDROCK": "1"})

    assert client.enabled
    assert client.config.model == "bedrock-default"
    assert client.config.use_bedrock is True
    assert selected == [{"use_bedrock": True, "timeout": 3.0}]


def test_factory_construction_failure_disables_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> meeting_llm._SDKHelpers:
        raise RuntimeError("sdk unavailable")

    monkeypatch.setattr(meeting_llm, "_load_sdk_helpers", fail)

    client = meeting_llm.build_meeting_llm_client_from_env({"CREWBORG_LLM_MEETINGS": "1", "USE_BEDROCK": "1"})

    assert not client.enabled
    assert "sdk unavailable" in (client.disabled_reason or "")
