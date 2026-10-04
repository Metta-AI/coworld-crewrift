"""Both native learner phases retain exact actual HTTP request/response evidence."""

from __future__ import annotations

import asyncio
import json

import pytest

from crewborg.native import NativeProfile
from crewborg.strategy.commander.llm import build_commander_client_from_env
from crewborg.strategy.meeting.llm import build_meeting_llm_client_from_env


@pytest.mark.asyncio
async def test_native_sidecar_drives_meeting_and_commander(monkeypatch, native_session):
    requests = []
    handlers = set()

    async def handler(reader, writer):
        handlers.add(asyncio.current_task())
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            request = json.loads(await reader.readexactly(length))
            requests.append((headers, request))
            prompt = json.loads(request["messages"][1]["content"])
            decision = (
                {"schema_version": 1, "action": "wait"}
                if "action" in prompt["response_schema"]
                else {"schema_version": 1, "target_room": None, "reason": "hold"}
            )
            body = json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "```json\n" + json.dumps(decision) + "\n```",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                }
            ).encode()
            writer.write(
                f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\nX-Softmax-Llm-Call-Id: received-{len(requests)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    env = {
        "COWORLD_LLM_ENDPOINT": f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        "COWORLD_LLM_MODEL": "checkpoint/source",
        "COWORLD_LLM_TEMPERATURE": "0",
        "CREWBORG_LLM_MODEL": "retired-model",
        "CREWBORG_LLM_MEETINGS": "1",
        "CREWBORG_LLM_COMMANDER": "1",
        "ANTHROPIC_API_KEY": "private-key-must-not-be-sent",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    meeting = build_meeting_llm_client_from_env(
        native_session, policy_profile=NativeProfile(origin="native")
    )
    commander = build_commander_client_from_env(
        native_session, policy_profile=NativeProfile(origin="native")
    )
    try:
        task = meeting.decide(
            {"self": {"role": "crewmate"}, "meeting": {"tick": 12}},
            trigger="meeting_start",
        )
        assert len(native_session.generations) == 1
        assert (await task).decision.action == "wait"
        task = commander.decide({"self": {"role": "crewmate"}, "observation_tick": 13})
        assert len(native_session.generations) == 2
        assert (await task).priorities["reason"] == "hold"
        for generation in native_session.generations.values():
            assert generation.request.model == "checkpoint/source"
            assert generation.decoder["temperature"] == 0
            assert (
                generation.response_complete
                and generation.response_reader_joined
                and generation.transport_cleanup_joined
            )
            assert (
                generation.raw_response is not None
                and "choices" in generation.raw_response
            )
            assert generation.platform_call_id.startswith("received-")
            assert generation.input_tokens == 10 and generation.output_tokens == 3
            assert generation.stop_reason == "stop"
    finally:
        server.close()
        await server.wait_closed()
        await asyncio.gather(*handlers)
    assert len(requests) == 2
    assert all(
        b"private-key-must-not-be-sent" not in headers for headers, request in requests
    )
    assert all(
        request["max_tokens"] > 0 and "max_completion_tokens" not in request
        for headers, request in requests
    )
