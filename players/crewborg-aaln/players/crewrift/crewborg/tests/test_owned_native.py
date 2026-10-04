"""Received bytes, accepted engine seats, and original owned cancellation deadlines."""

from __future__ import annotations

import asyncio
import base64
import json
from time import monotonic

import pytest

from players.crewrift.crewborg.native import (
    NativeRequest,
    NativeSession,
    PlayerRegistration,
)

pytestmark = pytest.mark.asyncio


async def test_native_started_partial_body_abort_and_owned_join(tmp_path, monkeypatch):
    request_seen = asyncio.Event()
    release = asyncio.Event()
    requests = []
    body = b'{"choices":[{"message":{"role":"assistant","content":"private-sentinel'

    async def serve(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            lengths = [
                line.split(b":", 1)[1]
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            ]
            request = await reader.readexactly(int(lengths[0]))
            requests.append((headers, json.loads(request)))
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: 10000\r\nX-Softmax-Llm-Call-Id: actual-call\r\n\r\n"
                + body
            )
            await writer.drain()
            request_seen.set()
            await release.wait()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("COWORLD_LLM_ENDPOINT", f"http://127.0.0.1:{port}")
    captures = []
    owner = NativeSession(
        PlayerRegistration(
            requested_slot=3,
            authenticated_upgrade=True,
            engine_player_index=1,
            assigned_slot=3,
        ),
        tmp_path / "native.jsonl",
        captures.append,
    )
    prepared = owner.prepare(
        NativeRequest(
            model="checkpoint/test",
            messages=[{"role": "user", "content": "exact prompt"}],
            max_tokens=20,
            temperature=0,
        ),
        phase="meeting",
        observation_tick=12,
        deadline=monotonic() + 3,
    )
    assert captures[0].raw_response is None
    assert captures[0].player_slot == 3
    task = owner.start(prepared, owner.complete(prepared))
    await request_seen.wait()
    while (
        owner.generations[str(prepared.generation.generation_id)].response_body_b64
        != base64.b64encode(body).decode()
    ):
        await asyncio.sleep(0)
    deadline = monotonic() + 0.5
    assert await owner.stop(deadline)
    assert task.cancelled()
    generation = owner.generations[str(prepared.generation.generation_id)]
    assert generation.platform_call_id == "actual-call"
    assert base64.b64decode(generation.response_body_b64) == body
    assert generation.raw_response == body.decode()
    assert generation.response_complete is False
    assert generation.response_reader_joined is True
    assert generation.transport_cleanup_joined is True
    assert b"X-Coworld-Player-Slot: 3" in requests[0][0]
    assert requests[0][1]["max_tokens"] == 20
    assert owner.shutdown_deadline == deadline
    owner.begin_stop(monotonic() + 20)
    assert owner.shutdown_deadline == deadline
    release.set()
    server.close()
    await server.wait_closed()


async def test_unadmitted_slot_and_unjoined_phase_cannot_issue_new_request(tmp_path):
    owner = NativeSession(
        PlayerRegistration(requested_slot=1),
        tmp_path / "native.jsonl",
        lambda generation: None,
    )
    request = NativeRequest(model="model", messages=[], max_tokens=1, temperature=0)
    with pytest.raises(RuntimeError, match="not authenticated"):
        owner.prepare(
            request, phase="meeting", observation_tick=1, deadline=monotonic() + 1
        )
    owner.registration.authenticated_upgrade = True
    owner.registration.assigned_slot = 1
    prepared = owner.prepare(
        request, phase="meeting", observation_tick=1, deadline=monotonic() + 1
    )
    prepared.generation.response_reader_joined = False

    async def finished():
        return None

    task = owner.start(prepared, finished())
    await task
    with pytest.raises(RuntimeError, match="not joined"):
        owner.prepare(
            request, phase="meeting", observation_tick=2, deadline=monotonic() + 1
        )
