"""Meeting LLM backend selection and prompt routing tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from crewborg.strategy.meeting import llm as meeting_llm
from crewborg.strategy.meeting.prompts import system_prompt_for_context


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


def test_factory_disabled_when_no_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    selected: list[dict[str, Any]] = []
    monkeypatch.setattr(meeting_llm, "_load_sdk_helpers", lambda: _helpers(use_bedrock=False, selected=selected))

    client = meeting_llm.build_meeting_llm_client_from_env({"CREWBORG_LLM_MEETINGS": "1"})

    assert not client.enabled
    assert client.disabled_reason == "no LLM backend configured"
    assert selected == []


def test_factory_selects_direct_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    selected: list[dict[str, Any]] = []
    monkeypatch.setattr(meeting_llm, "_load_sdk_helpers", lambda: _helpers(use_bedrock=False, selected=selected))

    client = meeting_llm.build_meeting_llm_client_from_env(
        {"CREWBORG_LLM_MEETINGS": "1", "ANTHROPIC_API_KEY": "sk-test", "CREWBORG_LLM_TIMEOUT_SECONDS": "2.5"}
    )

    assert client.enabled
    assert client.config.model == "direct-default"
    assert client.config.use_bedrock is False
    assert client.timeout_seconds == 2.5
    assert selected == [{"use_bedrock": False, "timeout": 2.5}]


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


def test_client_uses_call_json_and_role_prompt_from_context(tmp_path) -> None:
    (tmp_path / "crewmate.md").write_text("CREW ONLY", encoding="utf-8")
    (tmp_path / "imposter.md").write_text("IMPOSTER ONLY", encoding="utf-8")
    calls: list[dict[str, Any]] = []

    def call_json(client: object, **kwargs: Any) -> _Call:
        calls.append({"client": client, **kwargs})
        return _Call(text='{"schema_version":1,"action":"wait"}', model=kwargs["model"])

    client = object()
    meeting_client = meeting_llm.AnthropicMeetingClient(
        meeting_llm.MeetingLLMConfig(model="fake-haiku", prompt_dir=str(tmp_path)),
        client=client,
        call_json=call_json,
        extract_json_object=lambda text: text,
    )

    result = meeting_client.decide({"self": {"role": "imposter"}}, trigger="meeting_start")

    assert result.decision.action == "wait"
    assert calls[0]["client"] is client
    assert calls[0]["model"] == "fake-haiku"
    assert "IMPOSTER ONLY" in calls[0]["system"]
    assert "CREW ONLY" not in calls[0]["system"]


def test_prompt_loader_uses_files_and_missing_file_fallback(tmp_path) -> None:
    (tmp_path / "crewmate.md").write_text("CREWMATE FILE", encoding="utf-8")

    crewmate = system_prompt_for_context({"self": {"role": "crewmate"}}, prompt_dir=str(tmp_path))
    imposter = system_prompt_for_context({"self": {"role": "imposter"}}, prompt_dir=str(tmp_path))

    assert "CREWMATE FILE" in crewmate
    assert "Imposter doctrine" in imposter
