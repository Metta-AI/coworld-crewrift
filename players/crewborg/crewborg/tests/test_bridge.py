"""Real websocket/ZIP lifecycle; closure cannot substitute for engine authority."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from websockets.asyncio.server import serve

from crewborg.action import encode_input
from crewborg.coworld.policy_player import run_bridge
from crewborg.types import Command

pytestmark = pytest.mark.asyncio


def welcome(slot=3):
    return json.dumps(
        {
            "kind": "native_welcome",
            "protocol": "crewrift.native-evidence.v1",
            "player_slot": slot,
            "engine_player_index": 1,
            "configured_seat": True,
            "tick": 0,
        }
    )


class Runtime:
    def __init__(self):
        self.belief = SimpleNamespace(map=None)
        self.ticks = 0
        self.closed = False

    def step(self, observation):
        self.ticks += 1
        return Command()

    def close(self):
        self.closed = True


async def test_assigned_slot_frames_and_authoritative_terminal_upload_one_joined_zip(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "crewborg.coworld.policy_player.tempfile.mkdtemp",
        lambda **kwargs: str(tmp_path),
    )
    uploads = []
    upload_finished = asyncio.Event()

    async def upload(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            uploads.append(await reader.readexactly(length))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            upload_finished.set()

    packets = []

    async def engine(socket):
        assert "native_evidence=1" in socket.request.path
        await socket.send(welcome())
        await socket.send(b"")
        packets.append(await socket.recv())
        packets.append(await socket.recv())
        await socket.send(b"")
        packets.append(await socket.recv())
        await socket.send(
            json.dumps(
                {
                    "kind": "native_terminal",
                    "protocol": "crewrift.native-evidence.v1",
                    "tick": 2,
                    "results": [1],
                }
            )
        )
        assert json.loads(await socket.recv()) == {
            "kind": "native_terminal_ack",
            "tick": 2,
        }
        await socket.wait_closed()

    runtime = Runtime()
    sessions = []

    def build(**kwargs):
        sessions.append(kwargs["native_session"])
        return runtime

    upload_server = await asyncio.start_server(upload, "127.0.0.1", 0)
    monkeypatch.setenv(
        "COWORLD_PLAYER_ARTIFACT_UPLOAD_URL",
        f"http://127.0.0.1:{upload_server.sockets[0].getsockname()[1]}/private",
    )
    async with serve(engine, "127.0.0.1", 0) as server:
        await run_bridge(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/player?slot=3&token=private-token",
            build=build,
        )
    await upload_finished.wait()
    upload_server.close()
    await upload_server.wait_closed()
    assert packets == [encode_input(0), bytes([0x85]), bytes([0x85])]
    assert runtime.ticks == 2 and runtime.closed
    assert sessions[0].registration.assigned_slot == 3
    assert sessions[0].registration.engine_player_index == 1
    with zipfile.ZipFile(io.BytesIO(uploads[0])) as archive:
        assert archive.testzip() is None
        records = [
            json.loads(line) for line in archive.read("records.jsonl").splitlines()
        ]
    assert records[-1]["outcome"]["status"] == "completed"
    assert records[-1]["outcome"]["native_work_joined"] is True
    assert records[-1]["outcome"]["socket_joined"] is True
    assert b"private-token" not in uploads[0]


@pytest.mark.parametrize(
    "event,error",
    [
        (welcome(), "without authoritative terminal"),
        (welcome(4), "different requested player slot"),
    ],
)
async def test_closed_or_misassigned_engine_remains_truncated(
    tmp_path, monkeypatch, event, error
):
    monkeypatch.setattr(
        "crewborg.coworld.policy_player.tempfile.mkdtemp",
        lambda **kwargs: str(tmp_path),
    )
    monkeypatch.delenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", raising=False)
    runtime = Runtime()

    async def engine(socket):
        await socket.send(event)
        await socket.close()

    async with serve(engine, "127.0.0.1", 0) as server:
        with pytest.raises((RuntimeError, ValueError), match=error):
            await run_bridge(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/player?slot=3&token=secret",
                build=lambda **kwargs: runtime,
            )
    with zipfile.ZipFile(tmp_path / "player.zip") as archive:
        outcome = json.loads(archive.read("records.jsonl").splitlines()[-1])["outcome"]
    assert outcome["status"] == "truncated"
    assert outcome["terminal_engine_evidence"] is False
    assert runtime.closed


async def test_local_volume_artifact_is_sealed_after_engine_terminal(
    tmp_path, monkeypatch
):
    directory = tmp_path / "private"
    directory.mkdir()
    monkeypatch.setattr(
        "crewborg.coworld.policy_player.tempfile.mkdtemp",
        lambda **kwargs: str(directory),
    )
    destination = tmp_path / "policy_artifact_3.zip"
    monkeypatch.setenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", destination.as_uri())
    runtime = Runtime()

    async def engine(socket):
        await socket.send(welcome())
        await socket.send(b"")
        assert await socket.recv() == encode_input(0)
        assert await socket.recv() == bytes([0x85])
        await socket.send(
            json.dumps(
                {
                    "kind": "native_terminal",
                    "protocol": "crewrift.native-evidence.v1",
                    "tick": 1,
                    "results": [1],
                }
            )
        )
        await socket.wait_closed()

    async with serve(engine, "127.0.0.1", 0) as server:
        await run_bridge(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/player?slot=3&token=private-token",
            build=lambda **kwargs: runtime,
        )
    assert destination.read_bytes() == (directory / "player.zip").read_bytes()
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        outcome = json.loads(archive.read("records.jsonl").splitlines()[-1])["outcome"]
    assert outcome["status"] == "completed" and outcome["socket_joined"]
