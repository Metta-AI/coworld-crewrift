"""Meeting LLM backend selection and prompt routing tests."""

from __future__ import annotations

from io import BytesIO
import json
from typing import Any, NamedTuple

import pytest

from crewborg.strategy.meeting import llm as meeting_llm
from crewborg.strategy.meeting.prompts import system_prompt_for_context


class _Call(NamedTuple):
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


def test_factory_selects_jev_sidecar_without_player_slot() -> None:
    client = meeting_llm.build_meeting_llm_client_from_env(
        {
            "CREWBORG_LLM_MEETINGS": "1",
            "CREWBORG_MEETING_BACKEND": "jev",
            "AWS_ENDPOINT_URL_BEDROCK_RUNTIME": "http://127.0.0.1:9000/",
        }
    )

    assert isinstance(client, meeting_llm.JevMeetingClient)
    assert client.endpoint == "http://127.0.0.1:9000"
    assert client.headers == {}


def test_factory_disables_jev_when_no_backend_is_configured() -> None:
    client = meeting_llm.build_meeting_llm_client_from_env(
        {"CREWBORG_LLM_MEETINGS": "1", "CREWBORG_MEETING_BACKEND": "jev"}
    )

    assert not client.enabled
    assert client.disabled_reason == "no Jev backend configured"


def test_factory_selects_direct_typesafe_jev() -> None:
    client = meeting_llm.build_meeting_llm_client_from_env(
        {
            "CREWBORG_LLM_MEETINGS": "1",
            "CREWBORG_MEETING_BACKEND": "jev",
            "TYPESAFE_API_KEY": "private-test-key",
            "TYPESAFE_BASE_URL": "https://example.test/",
        }
    )

    assert isinstance(client, meeting_llm.JevMeetingClient)
    assert client.endpoint == "https://example.test"
    assert client.model == "jev-latest"
    assert client.headers == {"Authorization": "Bearer private-test-key"}


def test_jev_meeting_ranks_legal_votes_and_reports_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []

    def urlopen(request: Any, *, timeout: float) -> BytesIO:
        sent.append({"url": request.full_url, "body": json.loads(request.data), "timeout": timeout})
        return BytesIO(
            json.dumps(
                {
                    "answers": {
                        "vote": {
                            "type": "choice",
                            "choice": "skip",
                            "confidence": 0.8,
                            "probabilities": {"red": 0.7, "skip": 0.3},
                        }
                    },
                    "usage": {"cost": 0.00002},
                }
            ).encode()
        )

    monkeypatch.setattr(meeting_llm, "urlopen", urlopen)
    client = meeting_llm.JevMeetingClient(endpoint="http://sidecar", headers={})
    context = {
        "self": {"role": "crewmate", "color": "blue"},
        "meeting": {"id": 42},
        "constraints": {"valid_vote_targets": ["red", "skip"]},
    }

    early = client.decide(context, trigger="meeting_start")
    late = client.decide(context, trigger="deadline")

    assert early.decision.action == "set_tentative_vote"
    assert late.decision.action == "submit_vote"
    assert early.decision.vote_target == late.decision.vote_target == "red"
    assert early.usage == {"cost_usd": 0.00002}
    assert early.raw_request == sent[0]["body"]
    assert early.inference_mode == "typed_choice"
    assert early.raw_response is not None and '"answers"' in early.raw_response
    assert sent[0]["url"] == "http://sidecar/v1/systemone"
    assert sent[0]["body"]["questions"]["vote"]["criteria"] == {"red": "Vote out red", "skip": "Skip the vote"}


def test_jev_meeting_accepts_typesafe_usage_without_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request: Any, *, timeout: float) -> BytesIO:
        del request, timeout
        return BytesIO(
            b'{"answers":{"vote":{"type":"choice","choice":"skip","confidence":0.8,"probabilities":{"red":0.2,"skip":0.8}}},"usage":{"input_tokens":100,"output_tokens":10}}'
        )

    monkeypatch.setattr(meeting_llm, "urlopen", urlopen)
    client = meeting_llm.JevMeetingClient(endpoint="https://api.typesafe.ai", headers={"Authorization": "Bearer key"})
    result = client.decide({"constraints": {"valid_vote_targets": ["red", "skip"]}}, trigger="deadline")

    assert result.decision.vote_target == "skip"
    assert result.usage == {"input_tokens": 100, "output_tokens": 10}


def test_jev_meeting_rejects_incomplete_probability_map(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request: Any, *, timeout: float) -> BytesIO:
        del request, timeout
        return BytesIO(
            b'{"answers":{"vote":{"type":"choice","choice":"red","confidence":0.8,"probabilities":{"red":1.0}}},"usage":{"cost":0.00002}}'
        )

    monkeypatch.setattr(meeting_llm, "urlopen", urlopen)
    client = meeting_llm.JevMeetingClient(endpoint="http://sidecar", headers={})
    with pytest.raises(ValueError, match="wrong vote target set"):
        client.decide({"constraints": {"valid_vote_targets": ["red", "skip"]}}, trigger="meeting_start")


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
    assert result.inference_mode == "native_language"
    assert result.raw_request == {key: value for key, value in calls[0].items() if key != "client"}
    assert result.raw_response == '{"schema_version":1,"action":"wait"}'


def test_prompt_loader_uses_files_and_missing_file_fallback(tmp_path) -> None:
    (tmp_path / "crewmate.md").write_text("CREWMATE FILE", encoding="utf-8")

    crewmate = system_prompt_for_context({"self": {"role": "crewmate"}}, prompt_dir=str(tmp_path))
    imposter = system_prompt_for_context({"self": {"role": "imposter"}}, prompt_dir=str(tmp_path))

    assert "CREWMATE FILE" in crewmate
    assert "Imposter doctrine" in imposter
