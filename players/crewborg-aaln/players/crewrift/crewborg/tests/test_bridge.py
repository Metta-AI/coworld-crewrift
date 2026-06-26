"""In-process bridge smoke (design §3).

Stands up a real websocket server, streams a few binary "scene" frames, and
asserts the bridge connects, drives the idle runtime, sends the neutral input
packet exactly once (send-only-on-change), and exits cleanly when the server
closes the socket.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from players.crewrift.crewborg.action import INPUT_HEADER, encode_chat
from players.crewrift.crewborg.coworld import policy_player
from players.crewrift.crewborg.coworld.policy_player import run_bridge
from players.crewrift.crewborg.tests import sprite_wire as w
from players.crewrift.crewborg.types import Command
from players.player_sdk import TraceEvent

pytestmark = pytest.mark.asyncio


async def test_bridge_defaults_to_sqlite_recorder_without_stderr_stream(monkeypatch) -> None:
    """Default (no trace env): traces land in the artifact recorder only, not stderr."""

    class FakeRuntime:
        def __init__(self, trace_sink) -> None:
            self._trace_sink = trace_sink

        def step(self, _observation) -> Command:
            self._trace_sink.record(TraceEvent(tick=1, name="perception", data={}))
            self._trace_sink.record(TraceEvent(tick=2, name="domain.meeting_vote_selected", data={}))
            return Command(held_mask=0)

        def close(self) -> None:
            pass

    captured: dict[str, object] = {}
    stderr = io.StringIO()
    monkeypatch.delenv("CREWBORG_TRACE", raising=False)
    monkeypatch.delenv("CREWBORG_METRICS", raising=False)
    monkeypatch.delenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", raising=False)
    monkeypatch.setattr(sys, "stderr", stderr)

    summaries: list[dict[str, object]] = []
    real_upload = policy_player.upload_episode_artifact

    def capturing_upload(recorder):
        summaries.append(recorder.summary())
        return real_upload(recorder)

    monkeypatch.setattr(policy_player, "upload_episode_artifact", capturing_upload)

    def build(**kwargs):
        captured.update(kwargs)
        return FakeRuntime(kwargs["trace_sink"])

    async def handler(websocket) -> None:
        await websocket.send(w.clear_objects())
        try:
            await asyncio.wait_for(websocket.recv(), timeout=0.25)
        except (asyncio.TimeoutError, ConnectionClosed):
            return

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await asyncio.wait_for(
            run_bridge(f"ws://localhost:{port}/player?slot=0&token=", build=build),
            timeout=5.0,
        )

    # The full unfiltered stream is recorded in the artifact recorder…
    assert summaries and summaries[0]["event_counts"] == {
        "perception": 1,
        "domain.meeting_vote_selected": 1,
    }
    # …and nothing is JSON-streamed to stderr by default.
    json_lines = [line for line in stderr.getvalue().splitlines() if line.startswith("{")]
    assert json_lines == []


async def test_bridge_streams_stderr_when_trace_env_set(monkeypatch) -> None:
    class FakeRuntime:
        def close(self) -> None:
            pass

    captured: dict[str, object] = {}
    stderr = io.StringIO()
    monkeypatch.setenv("CREWBORG_TRACE", "debug")
    monkeypatch.delenv("CREWBORG_METRICS", raising=False)
    monkeypatch.setattr(sys, "stderr", stderr)

    def build(**kwargs):
        captured.update(kwargs)
        return FakeRuntime()

    def failing_connect(*_args, **_kwargs):
        raise RuntimeError("connect failed")

    with pytest.raises(RuntimeError, match="connect failed"):
        await run_bridge("ws://unused", connect=failing_connect, build=build)

    trace_sink = captured["trace_sink"]
    trace_sink.record(TraceEvent(tick=1, name="perception", data={}))
    records = [json.loads(line) for line in stderr.getvalue().splitlines() if line.startswith("{")]
    assert [record["event"] for record in records] == ["perception"]


async def test_bridge_enables_metrics_when_requested(monkeypatch) -> None:
    class FakeRuntime:
        def close(self) -> None:
            pass

    captured: dict[str, object] = {}
    stderr = io.StringIO()
    monkeypatch.setenv("CREWBORG_METRICS", "1")
    monkeypatch.setattr(sys, "stderr", stderr)

    def build(**kwargs):
        captured.update(kwargs)
        return FakeRuntime()

    def failing_connect(*_args, **_kwargs):
        raise RuntimeError("connect failed")

    with pytest.raises(RuntimeError, match="connect failed"):
        await run_bridge("ws://unused", connect=failing_connect, build=build)

    metrics_sink = captured["metrics_sink"]
    metrics_sink.counter("cyborg.mode.ran")
    records = [json.loads(line) for line in stderr.getvalue().splitlines() if line.startswith("{")]
    assert [record["name"] for record in records] == ["cyborg.mode.ran"]


async def test_bridge_uploads_artifact_to_file_url_at_episode_end(monkeypatch, tmp_path) -> None:
    artifact_path = tmp_path / "artifacts" / "crewborg.zip"
    monkeypatch.setenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", artifact_path.as_uri())
    monkeypatch.delenv("CREWBORG_TRACE", raising=False)

    async def handler(websocket) -> None:
        await websocket.send(w.clear_objects())
        try:
            await asyncio.wait_for(websocket.recv(), timeout=0.25)
        except (asyncio.TimeoutError, ConnectionClosed):
            return

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await asyncio.wait_for(
            run_bridge(f"ws://localhost:{port}/player?slot=0&token="),
            timeout=5.0,
        )

    assert artifact_path.exists()
    with zipfile.ZipFile(artifact_path) as archive:
        assert sorted(archive.namelist()) == ["README.md", "report.html", "summary.json", "trace.db"]
        summary = json.loads(archive.read("summary.json"))
    assert summary["trace_rows"] > 0
    # The slot is parsed from the WS URL into the episode metadata; the token is not.
    assert summary["episode"]["slot"] == 0
    assert "token" not in json.dumps(summary)


async def test_bridge_records_positions_table_in_artifact(monkeypatch, tmp_path) -> None:
    """End-to-end through the REAL runtime: streamed frames produce one positions
    row per tick in the artifact, carrying the server tick from the "tick <N>"
    marker sprite (the .bitreplay join key)."""

    import sqlite3

    artifact_path = tmp_path / "artifacts" / "crewborg.zip"
    monkeypatch.setenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", artifact_path.as_uri())
    monkeypatch.delenv("CREWBORG_TRACE", raising=False)

    def frame(tick: int) -> bytes:
        # An interstitial lobby frame plus the per-tick server tick marker.
        return (
            w.define_sprite(50, 1, 1, "STARTING")
            + w.define_object(9000, 10, 10, 0, 0, 50)
            + w.define_sprite(5016, 1, 1, f"tick {tick}")
            + w.define_object(5016, 0, 0, 0, 0, 5016)
        )

    async def handler(websocket) -> None:
        await websocket.send(w.clear_objects())
        for tick in (100, 101, 102):
            await websocket.send(frame(tick))
        try:
            await asyncio.wait_for(websocket.recv(), timeout=0.25)
        except (asyncio.TimeoutError, ConnectionClosed):
            return

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await asyncio.wait_for(
            run_bridge(f"ws://localhost:{port}/player?slot=0&token="),
            timeout=5.0,
        )

    with zipfile.ZipFile(artifact_path) as archive:
        summary = json.loads(archive.read("summary.json"))
        connection = sqlite3.connect(":memory:")
        connection.deserialize(archive.read("trace.db"))

    rows = connection.execute(
        "SELECT tick, server_tick, phase, mode, intent_kind, held_mask, visible"
        " FROM positions ORDER BY seq"
    ).fetchall()
    assert len(rows) == 4  # one per streamed frame (incl. the init clear)
    assert summary["position_rows"] == 4
    # The marker frames carry the server tick; phases derived from the labels.
    by_tick = {row[0]: row for row in rows}
    assert by_tick[2][1] == 100  # bridge tick 2 = first marker frame
    assert by_tick[4][1] == 102
    assert by_tick[4][2] == "Lobby"
    assert all(row[6] == "[]" for row in rows)  # no players in view


async def test_bridge_emits_metadata_to_stderr_when_no_upload_url(monkeypatch) -> None:
    """Today's hosted reality: the player pod gets NO artifact upload URL, only its
    stderr is captured as policy_agent_{slot}.log. The bridge must still surface the
    artifact's value — a clearly-marked summary block (incl. a parseable summary.json
    line with non-zero trace_rows) — to stderr at episode end."""

    monkeypatch.delenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", raising=False)
    monkeypatch.delenv("CREWBORG_TRACE", raising=False)
    monkeypatch.delenv("CREWBORG_METRICS", raising=False)
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)

    async def handler(websocket) -> None:
        for _ in range(3):
            await websocket.send(w.clear_objects())
        try:
            await asyncio.wait_for(websocket.recv(), timeout=0.25)
        except (asyncio.TimeoutError, ConnectionClosed):
            return

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await asyncio.wait_for(
            run_bridge(f"ws://localhost:{port}/player?slot=0&token="),
            timeout=5.0,
        )

    err = stderr.getvalue()
    assert "crewborg artifact: ===== episode artifact summary (begin) =====" in err
    assert "crewborg artifact: no upload URL set" in err

    marker = "crewborg artifact: summary.json "
    summary_line = next(line for line in err.splitlines() if line.startswith(marker))
    summary = json.loads(summary_line[len(marker):])
    assert summary["trace_rows"] > 0
    assert summary["zip_bytes"] > 0


async def test_bridge_runs_idle_loop_and_exits_cleanly() -> None:
    bridge_packets: list[bytes] = []

    async def handler(websocket) -> None:
        # Stream three valid scene frames, then drain whatever the bridge replies
        # with and close (returning from the handler closes the connection).
        for _ in range(3):
            await websocket.send(w.clear_objects())
        try:
            while True:
                bridge_packets.append(await asyncio.wait_for(websocket.recv(), timeout=0.25))
        except (asyncio.TimeoutError, ConnectionClosed):
            return

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://localhost:{port}/player?slot=0&token="
        # The bridge must return on its own when the server closes the socket.
        await asyncio.wait_for(run_bridge(url), timeout=5.0)

    # Idle holds mask 0; the bridge sends the neutral packet once and nothing
    # after, since the held mask never changes.
    assert bridge_packets == [bytes([INPUT_HEADER, 0x00])]


async def test_bridge_treats_unclean_close_as_game_end() -> None:
    """The Crewrift Nim server drops the ``/player`` socket without a close
    handshake (code 1006, "no close frame received or sent") at game end. The
    bridge must treat that unclean close as normal termination — return without
    raising so the container exits 0 — and still close the runtime. (The
    websockets async iterator swallows a *clean* close but re-raises
    ``ConnectionClosedError`` on an unclean one, which is what this guards.)"""

    class FakeRuntime:
        def __init__(self) -> None:
            self.closed = False

        def step(self, _observation) -> Command:
            return Command(held_mask=0)

        def close(self) -> None:
            self.closed = True

    fake_runtime = FakeRuntime()

    class UncleanConnection:
        """Async context manager + iterator: yields one scene frame, then raises
        ``ConnectionClosedError`` exactly as the real server's abrupt close does."""

        def __init__(self) -> None:
            self._frame_sent = False

        async def __aenter__(self) -> UncleanConnection:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        def __aiter__(self) -> UncleanConnection:
            return self

        async def __anext__(self) -> bytes:
            if not self._frame_sent:
                self._frame_sent = True
                return w.clear_objects()
            raise ConnectionClosedError(None, None)

        async def send(self, _data: bytes) -> None:
            pass

    def fake_connect(*_args: object, **_kwargs: object) -> UncleanConnection:
        return UncleanConnection()

    # Must return (not raise) despite the unclean close, and still close the runtime.
    await asyncio.wait_for(
        run_bridge("ws://unused", connect=fake_connect, build=lambda **_: fake_runtime),
        timeout=5.0,
    )
    assert fake_runtime.closed


async def test_bridge_closes_runtime_when_connect_raises() -> None:
    """A failure anywhere in connect/loop/send must still close the runtime
    (the strategy runner may own background threads/tasks)."""

    class FakeRuntime:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake = FakeRuntime()

    def failing_connect(*args, **kwargs):
        raise RuntimeError("connect failed")

    with pytest.raises(RuntimeError, match="connect failed"):
        await run_bridge("ws://unused", connect=failing_connect, build=lambda **_: fake)

    assert fake.closed


async def test_bridge_sends_chat_packet() -> None:
    received: list[bytes] = []

    class ChattyRuntime:
        def __init__(self) -> None:
            self.steps = 0

        def step(self, _observation) -> Command:
            self.steps += 1
            return Command(held_mask=0, chat="gg") if self.steps == 1 else Command(held_mask=0)

        def close(self) -> None:
            pass

    async def handler(websocket) -> None:
        await websocket.send(w.clear_objects())
        try:
            while True:
                received.append(await asyncio.wait_for(websocket.recv(), timeout=0.25))
        except (asyncio.TimeoutError, ConnectionClosed):
            return

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://localhost:{port}/player?slot=0&token="
        await asyncio.wait_for(run_bridge(url, build=lambda **_: ChattyRuntime()), timeout=5.0)

    assert encode_chat("gg") in received
