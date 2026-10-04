"""Every live feature requires the native endpoint; legacy provider keys cannot activate it."""

from __future__ import annotations

import pytest

from crewborg.native import NativeProfile
from crewborg.strategy.commander.llm import build_commander_client_from_env
from crewborg.strategy.meeting.llm import build_meeting_llm_client_from_env


@pytest.mark.parametrize(
    "build,flag",
    [
        (build_meeting_llm_client_from_env, "CREWBORG_LLM_MEETINGS"),
        (build_commander_client_from_env, "CREWBORG_LLM_COMMANDER"),
    ],
)
def test_native_endpoint_and_selected_checkpoint_decoder_are_required(
    native_session, build, flag
):
    assert not build(
        native_session,
        {"ANTHROPIC_API_KEY": "unused", "USE_BEDROCK": "1"},
        policy_profile=NativeProfile(origin="native"),
    ).enabled
    with pytest.raises(KeyError, match="COWORLD_LLM_ENDPOINT"):
        build(
            native_session, {flag: "1"}, policy_profile=NativeProfile(origin="native")
        )
    client = build(
        native_session,
        {
            flag: "1",
            "COWORLD_LLM_ENDPOINT": "http://trusted",
            "COWORLD_LLM_MODEL": "checkpoint/exact",
            "COWORLD_LLM_TEMPERATURE": "0",
        },
        policy_profile=NativeProfile(origin="native"),
    )
    assert client.enabled
    assert client.config.model == "checkpoint/exact"
    assert client.config.temperature == 0
