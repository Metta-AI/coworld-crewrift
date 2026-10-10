"""Received bytes, accepted engine seats, and original owned cancellation deadlines."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from time import monotonic

import pytest
from crewborg_native import NativeRequest, NativeSession, PlayerRegistration

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
    while captures[-1].response_body_b64 != base64.b64encode(body).decode():
        await asyncio.sleep(0)
    deadline = monotonic() + 0.5
    assert await owner.stop(deadline)
    assert task.cancelled()
    generation = captures[-1]
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


async def test_policy_entrypoint_imports_in_a_cold_interpreter():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import crewborg.coworld.policy_player",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    assert stdout == b""


async def test_completed_native_calls_retire_owners_and_keep_every_private_attempt(
    tmp_path, monkeypatch
):
    body = json.dumps(
        {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
    ).encode()
    requests = []

    async def serve(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            requests.append(await reader.readexactly(length))
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    monkeypatch.setenv(
        "COWORLD_LLM_ENDPOINT", f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    )
    captured = []
    session = NativeSession(
        PlayerRegistration(
            requested_slot=1,
            assigned_slot=1,
            authenticated_upgrade=True,
            engine_player_index=2,
        ),
        tmp_path / "native.jsonl",
        captured.append,
    )
    ids = []
    try:
        for tick in range(25):
            prepared = session.prepare(
                NativeRequest(
                    model="fixture",
                    messages=[{"role": "user", "content": f"prompt-{tick}"}],
                    max_tokens=10,
                    temperature=0,
                ),
                phase="meeting",
                observation_tick=tick,
                deadline=monotonic() + 2,
            )
            task = session.start(prepared, session.complete(prepared))
            completion = await task
            ids.append(str(completion.generation_id))
            assert session.tasks == {}
            assert session.phase_joined("meeting")
        assert await session.stop(monotonic() + 2)
    finally:
        server.close()
        await server.wait_closed()
    records = [json.loads(line) for line in session.path.read_text().splitlines()]
    assert len(requests) == 25
    for identifier in ids:
        evidence = [
            record for record in records if record["generation_id"] == identifier
        ]
        assert evidence[0]["raw_response"] is None
        assert evidence[-1]["raw_response"].encode() == body
        assert evidence[-1]["response_reader_joined"] is True
        assert evidence[-1]["transport_cleanup_joined"] is True
    assert len(captured) == len(records)


@pytest.mark.parametrize(
    (
        "sampling",
        "sampled_temperature",
        "requested_temperature",
        "accepted",
        "corruption",
    ),
    [
        ("full_softmax", 0.2, 0.2, True, None),
        ("full_softmax_temperature_one", 1.0, 1.0, True, None),
        ("full_softmax", 0.7, 0.2, False, None),
        ("full_softmax_temperature_one", 1.0, 0.2, False, None),
        (None, 0.0, 0.2, True, None),
        *[
            ("full_softmax", 0.2, 0.2, False, corruption)
            for corruption in ["length", "positive", "nonfinite", "token", "response"]
        ],
    ],
)
async def test_actual_sampler_matches_request_and_keeps_private_response(
    tmp_path,
    monkeypatch,
    sampling,
    sampled_temperature,
    requested_temperature,
    accepted,
    corruption,
):
    evidence = (
        None
        if sampling is None
        else {
            "policy_revision": "checkpoint",
            "tokenizer_revision": "tokenizer",
            "chat_template": "template",
            "sampling": sampling,
            "enable_thinking": False,
            "max_new_tokens": 20,
            "max_sequence_length": 100,
            "sampling_seed": 7,
            "eos_token_ids": [2],
            "prompt_token_ids": [10],
            "completion_token_ids": [12, 2],
            "behavior_log_probs": [-0.2, -0.3],
            "stop_reason": "eos",
            "response": "private sample",
            **(
                {"temperature": sampled_temperature}
                if sampling == "full_softmax"
                else {}
            ),
        }
    )
    if corruption is not None:
        assert evidence is not None
        if corruption == "length":
            evidence["behavior_log_probs"] = [-0.2]
        elif corruption == "positive":
            evidence["behavior_log_probs"] = [0.2, -0.3]
        elif corruption == "nonfinite":
            evidence["behavior_log_probs"] = [float("-inf"), -0.3]
        elif corruption == "token":
            evidence["prompt_token_ids"] = [-1]
        elif corruption == "response":
            evidence["response"] = "private mismatched sample"
    body = (
        json.dumps(
            {
                "choices": [{"message": {"content": "private sample"}}],
                "sampling_evidence": evidence,
            }
        )
        .encode()
        .replace(b"-Infinity", b"-1e309")
    )
    requests = []

    async def serve(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in header.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            requests.append(json.loads(await reader.readexactly(length)))
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nX-Softmax-Llm-Call-Id: 20c53756-d9a5-4ed1-9e29-a366efb81919\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    monkeypatch.setenv(
        "COWORLD_LLM_ENDPOINT", f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    )
    captures = []
    session = NativeSession(
        PlayerRegistration(
            requested_slot=0,
            assigned_slot=0,
            engine_player_index=0,
            authenticated_upgrade=True,
        ),
        tmp_path / "native.jsonl",
        captures.append,
    )
    prepared = session.prepare(
        NativeRequest(
            model="checkpoint/test",
            messages=[{"role": "user", "content": "ordinary view"}],
            max_tokens=20,
            temperature=requested_temperature,
        ),
        phase="meeting",
        observation_tick=1,
        deadline=monotonic() + 2,
    )
    try:
        task = session.start(prepared, session.complete(prepared))
        if accepted:
            assert (await task).completion_text == "private sample"
        else:
            with pytest.raises(ValueError) as failure:
                await task
            assert "private sample" not in str(failure.value)
            assert "private mismatched sample" not in str(failure.value)
        assert captures[-1].raw_response == body.decode()
        if corruption == "nonfinite":
            assert captures[-1].response is None
        else:
            assert isinstance(captures[-1].response, dict)
            assert captures[-1].response["sampling_evidence"] == evidence
        assert captures[-1].response_reader_joined
        assert captures[-1].transport_cleanup_joined
        assert requests[0]["temperature"] == requested_temperature
        assert await session.stop(monotonic() + 2)
    finally:
        server.close()
        await server.wait_closed()
