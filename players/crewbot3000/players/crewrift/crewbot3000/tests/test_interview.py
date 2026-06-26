"""Interview-mode websocket server + LLM client tests (no real network/LLM).

Stands up the real ``interview_server`` against an injected fake
:class:`InterviewLLMClient`, drives the ``coworld.interview.v1`` protocol over a
real client socket, and asserts the ready/answer handshake. Also covers the
degraded path (LLM disabled -> a clear non-crashing answer the commissioner
scores as a fail).
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from players.crewrift.crewbot3000.coworld.interview_server import (
    PROTOCOL_VERSION,
    run_interview_server,
)
from players.crewrift.crewbot3000.strategy.meeting.interview import (
    DEGRADED_ANSWER_PREFIX,
    InterviewAnswer,
    InterviewLLMClient,
)


class _FakeInterviewLLM:
    enabled = True
    disabled_reason = None

    def __init__(self, answer: str = "Skip when there is no real evidence; vote on vent sightings.") -> None:
        self._answer = answer
        self.questions: list[str] = []

    def answer(self, question: str, context=None) -> InterviewAnswer:
        self.questions.append(question)
        return InterviewAnswer(answer=self._answer, degraded=False, model="fake-haiku")


async def _serve_once(client) -> tuple[asyncio.Task, asyncio.Event, int, list]:
    ready = asyncio.Event()
    stop_future: asyncio.Future = asyncio.get_event_loop().create_future()
    bound: list[int] = []

    from websockets.asyncio.server import serve as real_serve

    def serve_factory(handler, host, port):
        # Force an ephemeral port and capture it once bound.
        cm = real_serve(handler, host, 0)
        return _CaptureServer(cm, bound)

    task = asyncio.ensure_future(
        run_interview_server(
            host="127.0.0.1",
            port=0,
            client=client,
            serve_factory=serve_factory,
            ready_event=ready,
            stop=stop_future,
        )
    )
    await asyncio.wait_for(ready.wait(), timeout=5.0)
    return task, stop_future, bound[0], []


class _CaptureServer:
    """Wrap the websockets serve context manager to expose the bound port."""

    def __init__(self, cm, bound: list[int]) -> None:
        self._cm = cm
        self._bound = bound
        self._server = None

    async def __aenter__(self):
        self._server = await self._cm.__aenter__()
        self._bound.append(self._server.sockets[0].getsockname()[1])
        return self._server

    async def __aexit__(self, *exc):
        return await self._cm.__aexit__(*exc)


@pytest.mark.asyncio
async def test_interview_server_answers_question() -> None:
    fake = _FakeInterviewLLM()
    task, stop_future, port, _ = await _serve_once(fake)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
            ready = json.loads(await ws.recv())
            assert ready["type"] == "interview_ready"
            assert ready["protocol"] == PROTOCOL_VERSION
            assert ready["llm_enabled"] is True

            await ws.send(json.dumps({"type": "interview_question", "question": "When to skip?", "id": "q1"}))
            answer = json.loads(await ws.recv())
            assert answer["type"] == "interview_answer"
            assert answer["answer"].startswith("Skip when")
            assert answer["degraded"] is False
            assert answer["id"] == "q1"
            assert answer["model"] == "fake-haiku"

            await ws.send(json.dumps({"type": "done"}))
    finally:
        if not stop_future.done():
            stop_future.set_result(None)
        await asyncio.wait_for(task, timeout=5.0)
    assert fake.questions == ["When to skip?"]


@pytest.mark.asyncio
async def test_interview_server_rejects_malformed_frame() -> None:
    fake = _FakeInterviewLLM()
    task, stop_future, port, _ = await _serve_once(fake)
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/") as ws:
            await ws.recv()  # ready
            await ws.send("not json")
            err = json.loads(await ws.recv())
            assert err["type"] == "error"
            # The loop continues; a valid question still works afterward.
            await ws.send(json.dumps({"type": "interview_question", "question": "hi"}))
            answer = json.loads(await ws.recv())
            assert answer["type"] == "interview_answer"
            await ws.send(json.dumps({"type": "done"}))
    finally:
        if not stop_future.done():
            stop_future.set_result(None)
        await asyncio.wait_for(task, timeout=5.0)


def test_disabled_client_returns_degraded_answer() -> None:
    # No LLM backend configured -> disabled client, clear degraded answer (no crash).
    client = InterviewLLMClient.from_env(env={})
    assert client.enabled is False
    result = client.answer("When to skip?")
    assert result.degraded is True
    assert result.answer.startswith(DEGRADED_ANSWER_PREFIX)


def test_enabled_client_uses_injected_anthropic() -> None:
    # Inject a fake Anthropic client so no network call happens.
    class _Msg:
        def __init__(self, text: str) -> None:
            self.content = [{"type": "text", "text": text}]

    class _Messages:
        def create(self, **_kw):
            return _Msg("Vote out players seen venting; skip when there is no evidence.")

    class _FakeAnthropic:
        messages = _Messages()

    env = {"CREWBOT3000_LLM_MEETINGS": "1", "ANTHROPIC_API_KEY": "test-key"}
    client = InterviewLLMClient.from_env(env=env, client=_FakeAnthropic())
    assert client.enabled is True
    result = client.answer("How do you vote as an imposter?")
    assert result.degraded is False
    assert "skip" in result.answer.lower()
