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


@pytest.mark.parametrize("upload_status", [200, 503])
async def test_assigned_slot_frames_and_authoritative_terminal_upload_one_joined_zip(
    tmp_path, monkeypatch, upload_status
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
            body = b"private-upload-sentinel" if upload_status == 503 else b""
            writer.write(
                f"HTTP/1.1 {upload_status} Fixture\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
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
        operation = run_bridge(
            f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/player?slot=3&token=private-token",
            build=build,
        )
        if upload_status == 200:
            await operation
        else:
            with pytest.raises(
                RuntimeError, match="Private artifact upload failed: HTTP 503"
            ) as failure:
                await operation
            assert "private-upload-sentinel" not in str(failure.value)
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


@pytest.mark.parametrize(
    "termination", ["terminal", "signal", "eof", "terminal_unjoined"]
)
async def test_terminal_owns_backlogged_frames_before_stale_controller_send(
    tmp_path, monkeypatch, termination
):
    from time import monotonic

    from websockets.asyncio.client import connect

    monkeypatch.setattr(
        "crewborg.coworld.policy_player.tempfile.mkdtemp",
        lambda **kwargs: str(tmp_path),
    )
    monkeypatch.delenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", raising=False)
    blocked_send = asyncio.Event()
    controller_canceled = asyncio.Event()
    runtime = Runtime()
    sessions = []
    terminal_ack = []
    late_release = asyncio.Event()
    owned_sockets = []
    handlers = {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "add_signal_handler",
        lambda signum, callback: handlers.__setitem__(signum, callback),
    )
    monkeypatch.setattr(
        loop, "remove_signal_handler", lambda signum: handlers.pop(signum)
    )

    class OwnedSocket:
        def __init__(self, socket):
            self.socket = socket
            self.blocked = False

        def __aiter__(self):
            return self.socket.__aiter__()

        async def send(self, packet):
            if isinstance(packet, bytes) and not self.blocked:
                self.blocked = True
                blocked_send.set()
                try:
                    if termination == "terminal_unjoined":
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            # Deliberately uncooperative owner: admission must stay closed.
                            await late_release.wait()
                    else:
                        await asyncio.Event().wait()
                finally:
                    controller_canceled.set()
            else:
                await self.socket.send(packet)

        async def close(self):
            await self.socket.close()

    async def owned_connect(*args, **kwargs):
        wrapped = OwnedSocket(await connect(*args, **kwargs))
        owned_sockets.append(wrapped)
        return wrapped

    def build(**kwargs):
        sessions.append(kwargs["native_session"])
        return runtime

    async def engine(socket):
        await socket.send(welcome())
        await socket.send(b"")
        await blocked_send.wait()
        for _ in range(200):
            await socket.send(b"")
        began = monotonic()
        if termination == "signal":
            import signal

            while not sessions[0].registration.authenticated_upgrade:
                await asyncio.sleep(0)
            handlers[signal.SIGTERM]()
            await socket.wait_closed()
            return
        if termination == "eof":
            await socket.close()
            return
        await socket.send(
            json.dumps(
                {
                    "kind": "native_terminal",
                    "protocol": "crewrift.native-evidence.v1",
                    "tick": 201,
                    "results": [1],
                }
            )
        )
        packet = await asyncio.wait_for(socket.recv(), 2)
        terminal_ack.append((json.loads(packet), monotonic() - began))
        await socket.wait_closed()

    async with serve(engine, "127.0.0.1", 0) as server:
        owner = asyncio.create_task(
            run_bridge(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/player?slot=3&token=secret",
                connect=owned_connect,
                build=build,
            )
        )
        if termination == "terminal":
            await owner
        elif termination == "terminal_unjoined":
            with pytest.raises(RuntimeError, match="frame owners did not join"):
                await owner
            late_release.set()
            await controller_canceled.wait()
            await owned_sockets[0].close()
            await asyncio.sleep(0)
        else:
            with pytest.raises(
                asyncio.CancelledError if termination == "signal" else RuntimeError
            ):
                await owner
    if termination in {"terminal", "terminal_unjoined"}:
        assert terminal_ack[0][0] == {"kind": "native_terminal_ack", "tick": 201}
        assert terminal_ack[0][1] < 2
    assert controller_canceled.is_set() and runtime.closed
    assert runtime.ticks == 1
    assert sessions[0].sealed and sessions[0].shutdown_deadline is not None
    with zipfile.ZipFile(tmp_path / "player.zip") as archive:
        records = [
            json.loads(line) for line in archive.read("records.jsonl").splitlines()
        ]
        assert archive.testzip() is None
    outcome = next(
        row["outcome"] for row in records if row["kind"] == "private_outcome"
    )
    assert outcome["status"] == (
        "completed" if termination == "terminal" else "truncated"
    )
    assert outcome["frame_owners_joined"] is (termination != "terminal_unjoined")
    assert outcome["native_work_joined"] is True
    frames = [row for row in records if row["kind"] == "engine_frame"]
    if termination != "signal":
        assert len(frames) == 201
    assert [row["frame_sequence"] for row in frames] == list(range(len(frames)))
    arrivals = [row["received_monotonic"] for row in frames]
    assert all(isinstance(value, float) for value in arrivals)
    assert arrivals == sorted(arrivals)
    ledger = next(row for row in records if row["kind"] == "controller_frame_outcome")
    assert ledger == {
        "kind": "controller_frame_outcome",
        "received_frames": len(frames),
        "controller_applied_frames": 1,
        "unapplied_frames": len(frames) - 1,
        "owners_joined": termination != "terminal_unjoined",
    }


async def test_invalid_received_frame_is_private_and_cannot_complete(
    tmp_path, monkeypatch
):
    from crewborg.perception.decoder import SpriteProtocolError

    monkeypatch.setattr(
        "crewborg.coworld.policy_player.tempfile.mkdtemp",
        lambda **kwargs: str(tmp_path),
    )
    monkeypatch.delenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", raising=False)
    runtime = Runtime()

    async def engine(socket):
        await socket.send(welcome())
        await socket.send(b"\xffprivate-body-sentinel")
        await socket.wait_closed()

    async with serve(engine, "127.0.0.1", 0) as server:
        with pytest.raises(SpriteProtocolError) as failure:
            await run_bridge(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/player?slot=3&token=secret",
                build=lambda **kwargs: runtime,
            )
    assert "private-body-sentinel" not in str(failure.value)
    assert runtime.closed and runtime.ticks == 0
    with zipfile.ZipFile(tmp_path / "player.zip") as archive:
        assert archive.testzip() is None
        records = [
            json.loads(line) for line in archive.read("records.jsonl").splitlines()
        ]
    ledger = next(row for row in records if row["kind"] == "controller_frame_outcome")
    assert ledger["received_frames"] == 1 and ledger["controller_applied_frames"] == 0
    assert ledger["owners_joined"] is True


async def test_unjoined_reader_cannot_append_after_private_seal(tmp_path, monkeypatch):
    import hashlib

    from websockets.asyncio.client import connect

    from crewborg.perception.decoder import SpriteProtocolError

    monkeypatch.setattr(
        "crewborg.coworld.policy_player.tempfile.mkdtemp",
        lambda **kwargs: str(tmp_path),
    )
    monkeypatch.delenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", raising=False)
    late_release = asyncio.Event()
    reader_returned = asyncio.Event()
    wrapped_sockets = []
    runtime = Runtime()

    class LateReader:
        def __init__(self, socket):
            self.socket = socket

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return await self.socket.recv()
            except asyncio.CancelledError:
                # Deliberately ignore cancellation until after the original budget.
                await late_release.wait()
                packet = await self.socket.recv()
                reader_returned.set()
                return packet

        async def send(self, packet):
            await self.socket.send(packet)

        async def close(self):
            await late_release.wait()
            await self.socket.close()

    async def owned_connect(*args, **kwargs):
        wrapped = LateReader(await connect(*args, **kwargs))
        wrapped_sockets.append(wrapped)
        return wrapped

    async def engine(socket):
        await socket.send(welcome())
        await socket.send(b"\xffprivate-original-frame")
        await late_release.wait()
        await socket.send(b"late-private-frame")
        await socket.wait_closed()

    async with serve(engine, "127.0.0.1", 0) as server:
        with pytest.raises(SpriteProtocolError):
            await run_bridge(
                f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/player?slot=3&token=secret",
                connect=owned_connect,
                build=lambda **kwargs: runtime,
            )
        sealed = hashlib.sha256((tmp_path / "player.zip").read_bytes()).hexdigest()
        late_release.set()
        await asyncio.wait_for(reader_returned.wait(), 1)
        await asyncio.sleep(0)
        await wrapped_sockets[0].socket.close()
        assert (
            hashlib.sha256((tmp_path / "player.zip").read_bytes()).hexdigest() == sealed
        )
    with zipfile.ZipFile(tmp_path / "player.zip") as archive:
        records = [
            json.loads(line) for line in archive.read("records.jsonl").splitlines()
        ]
        outcome = next(
            row["outcome"] for row in records if row["kind"] == "private_outcome"
        )
    assert outcome["status"] == "truncated"
    assert outcome["frame_owners_joined"] is False
    assert outcome["terminal_engine_evidence"] is False
    frames = [row for row in records if row["kind"] == "engine_frame"]
    assert len(frames) == 1
    assert runtime.closed
