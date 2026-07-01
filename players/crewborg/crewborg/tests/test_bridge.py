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

from crewborg.action import INPUT_HEADER, encode_chat
from crewborg.coworld.policy_player import (
    MIDGAME_RECONNECT_ATTEMPTS,
    build_trace_outputs,
    run_bridge,
)
from crewborg.tests import sprite_wire as w
from crewborg.types import Command
from players.player_sdk import NullMetricsSink, TraceEvent

pytestmark = pytest.mark.asyncio


async def _no_sleep(_seconds: float) -> None:
    """Stub for asyncio.sleep so reconnect-backoff tests don't wait in real time."""


def _json_records(raw: str) -> list[dict]:
    """Parse the JSON lines from a stream, skipping plain-text warnings."""

    records = []
    for line in raw.splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


async def test_bridge_defaults_to_lean_trace_and_no_metrics(monkeypatch) -> None:
    class FakeRuntime:
        def close(self) -> None:
            pass

    captured: dict[str, object] = {}
    stderr = io.StringIO()
    monkeypatch.delenv("CREWBORG_TRACE", raising=False)
    monkeypatch.delenv("CREWBORG_METRICS", raising=False)
    monkeypatch.delenv("CREWBORG_TRACE_OUTPUTS", raising=False)
    monkeypatch.delenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", raising=False)
    monkeypatch.setattr(sys, "stderr", stderr)

    def build(**kwargs):
        captured.update(kwargs)
        return FakeRuntime()

    def failing_connect(*_args, **_kwargs):
        raise RuntimeError("connect failed")

    with pytest.raises(RuntimeError, match="connect failed"):
        await run_bridge("ws://unused", connect=failing_connect, build=build)

    assert isinstance(captured["metrics_sink"], NullMetricsSink)
    trace_sink = captured["trace_sink"]
    trace_sink.record(TraceEvent(tick=1, name="perception", data={}))
    trace_sink.record(TraceEvent(tick=2, name="domain.meeting_vote_selected", data={}))
    records = _json_records(stderr.getvalue())
    assert [record["event"] for record in records] == ["domain.meeting_vote_selected"]


async def test_bridge_enables_metrics_when_requested(monkeypatch) -> None:
    class FakeRuntime:
        def close(self) -> None:
            pass

    captured: dict[str, object] = {}
    monkeypatch.setenv("CREWBORG_METRICS", "1")
    monkeypatch.delenv("CREWBORG_TRACE_OUTPUTS", raising=False)
    monkeypatch.delenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", raising=False)
    monkeypatch.setattr(sys, "stderr", io.StringIO())

    def build(**kwargs):
        captured.update(kwargs)
        return FakeRuntime()

    def failing_connect(*_args, **_kwargs):
        raise RuntimeError("connect failed")

    with pytest.raises(RuntimeError, match="connect failed"):
        await run_bridge("ws://unused", connect=failing_connect, build=build)

    assert not isinstance(captured["metrics_sink"], NullMetricsSink)


async def test_trace_outputs_default_to_artifact_zip(tmp_path, monkeypatch) -> None:
    """With the runner-provided upload URL present, the default output is the
    player artifact zip: traces land in telemetry.jsonl inside the uploaded
    zip, not on stderr (design §11; metta PLAYER_ARTIFACT contract)."""

    destination = tmp_path / "policy_artifact_0.zip"
    stderr = io.StringIO()
    monkeypatch.delenv("CREWBORG_TRACE", raising=False)
    monkeypatch.delenv("CREWBORG_METRICS", raising=False)
    monkeypatch.delenv("CREWBORG_TRACE_OUTPUTS", raising=False)
    monkeypatch.setenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", f"file://{destination}")
    monkeypatch.setattr(sys, "stderr", stderr)

    outputs = build_trace_outputs()
    outputs.trace_sink.record(TraceEvent(tick=1, name="domain.vote_cast", data={}))
    outputs.close()

    assert not _json_records(stderr.getvalue())
    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        telemetry = next(name for name in names if name != "manifest.json")
        records = _json_records(archive.read(telemetry).decode("utf-8"))
    assert [record["event"] for record in records] == ["domain.vote_cast"]


async def test_trace_outputs_fall_back_to_stderr_without_upload_url(monkeypatch) -> None:
    """Without an upload URL (bridge running outside a runner) the artifact
    default must degrade to stderr JSONL instead of raising — a crash here
    would happen before connect and fail the episode."""

    stderr = io.StringIO()
    monkeypatch.delenv("CREWBORG_TRACE", raising=False)
    monkeypatch.delenv("CREWBORG_METRICS", raising=False)
    monkeypatch.delenv("CREWBORG_TRACE_OUTPUTS", raising=False)
    monkeypatch.delenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", raising=False)
    monkeypatch.setattr(sys, "stderr", stderr)

    outputs = build_trace_outputs()
    outputs.trace_sink.record(TraceEvent(tick=1, name="domain.vote_cast", data={}))
    outputs.close()

    raw = stderr.getvalue()
    assert "falling back" in raw
    records = _json_records(raw)
    assert [record["event"] for record in records] == ["domain.vote_cast"]


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


class _FakeRuntime:
    def __init__(self) -> None:
        self.closed = False

    def step(self, _observation) -> Command:
        return Command(held_mask=0)

    def close(self) -> None:
        self.closed = True


class _FrameThenUncleanClose:
    """One scene frame, then an unclean ``ConnectionClosedError`` (the engine's 1006 drop)."""

    def __init__(self) -> None:
        self._frame_sent = False

    async def __aenter__(self) -> _FrameThenUncleanClose:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __aiter__(self) -> _FrameThenUncleanClose:
        return self

    async def __anext__(self) -> bytes:
        if not self._frame_sent:
            self._frame_sent = True
            return w.clear_objects()
        raise ConnectionClosedError(None, None)

    async def send(self, _data: bytes) -> None:
        pass


async def test_bridge_retries_then_concludes_game_over_on_unclean_close(monkeypatch) -> None:
    """A mid-game unclean close (1006) is ambiguous — game-over vs a transient blip — so the
    bridge now retries a few times before concluding the game ended. When the reconnects keep
    failing (the engine is really gone), it still returns cleanly and closes the runtime."""

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    fake_runtime = _FakeRuntime()
    connects = {"n": 0}

    class _DeadConnection:
        """The engine is gone: connecting refuses before any frame arrives."""

        async def __aenter__(self) -> _DeadConnection:
            raise ConnectionClosedError(None, None)

        async def __aexit__(self, *exc: object) -> bool:
            return False

    def fake_connect(*_args: object, **_kwargs: object):
        connects["n"] += 1
        return _FrameThenUncleanClose() if connects["n"] == 1 else _DeadConnection()

    await asyncio.wait_for(
        run_bridge("ws://unused", connect=fake_connect, build=lambda **_: fake_runtime),
        timeout=5.0,
    )
    assert fake_runtime.closed
    # One live connection, then MIDGAME_RECONNECT_ATTEMPTS idle reconnects before giving up.
    assert connects["n"] == 1 + MIDGAME_RECONNECT_ATTEMPTS


async def test_bridge_recovers_from_a_transient_midgame_drop(monkeypatch) -> None:
    """A transient blip: the socket drops mid-game, but the next connect resumes (more
    frames) and the bridge keeps playing instead of bailing on the first drop."""

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    fake_runtime = _FakeRuntime()
    connects = {"n": 0}

    class _FrameThenCleanEnd:
        """One more frame after the reconnect, then a clean end (game finishes normally)."""

        def __init__(self) -> None:
            self._frame_sent = False

        async def __aenter__(self) -> _FrameThenCleanEnd:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        def __aiter__(self) -> _FrameThenCleanEnd:
            return self

        async def __anext__(self) -> bytes:
            if not self._frame_sent:
                self._frame_sent = True
                return w.clear_objects()
            raise StopAsyncIteration

        async def send(self, _data: bytes) -> None:
            pass

    def fake_connect(*_args: object, **_kwargs: object):
        connects["n"] += 1
        return _FrameThenUncleanClose() if connects["n"] == 1 else _FrameThenCleanEnd()

    await asyncio.wait_for(
        run_bridge("ws://unused", connect=fake_connect, build=lambda **_: fake_runtime),
        timeout=5.0,
    )
    assert fake_runtime.closed
    # Reconnected exactly once after the drop, resumed, then ended cleanly — did NOT give up.
    assert connects["n"] == 2


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


async def test_bridge_retries_initial_connect_until_it_succeeds(monkeypatch) -> None:
    """The hosted -100 connect_timeout failures were INITIAL-connect races: the
    player container starts before the engine's /player socket accepts, the single
    connect() threw, and the process died. The bridge must retry the initial connect
    (with backoff) until frames flow, not give up on the first failure."""

    import crewborg.coworld.policy_player as pp
    monkeypatch.setattr(pp.asyncio, "sleep", _no_sleep)  # don't actually wait out backoff

    class FakeRuntime:
        def step(self, _observation) -> Command:
            return Command(held_mask=0)

        def close(self) -> None:
            pass

    attempts = {"n": 0}

    class OneFrameThenCleanEnd:
        # Ends the game with a CLEAN close (StopAsyncIteration) so this test stays focused
        # on the initial-connect retry; mid-game unclean-drop recovery is covered separately.
        def __init__(self) -> None:
            self._sent = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            if not self._sent:
                self._sent = True
                return w.clear_objects()
            raise StopAsyncIteration

        async def send(self, _data: bytes) -> None:
            pass

    def flaky_connect(*_args, **_kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionRefusedError("engine not up yet")  # an OSError subclass
        return OneFrameThenCleanEnd()

    await asyncio.wait_for(
        run_bridge("ws://unused", connect=flaky_connect, build=lambda **_: FakeRuntime()),
        timeout=5.0,
    )
    assert attempts["n"] == 3  # two refusals, third connects and runs the game


async def test_bridge_gives_up_after_reconnect_deadline(monkeypatch) -> None:
    """If the engine never comes up, the bridge must give up at the deadline (exit
    0 — never connected is still a clean process exit) rather than retry forever."""

    import crewborg.coworld.policy_player as pp
    monkeypatch.setattr(pp.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(pp, "RECONNECT_DEADLINE_SECONDS", 0.0)  # give up immediately

    class FakeRuntime:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake = FakeRuntime()

    def always_refuse(*_args, **_kwargs):
        raise ConnectionRefusedError("engine never came up")

    await asyncio.wait_for(
        run_bridge("ws://unused", connect=always_refuse, build=lambda **_: fake),
        timeout=5.0,
    )
    assert fake.closed  # runtime still cleaned up on give-up


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


async def test_scene_server_tick_parses_marker() -> None:
    """SceneState reads the engine's authoritative tick from the ``"tick <N>"``
    marker sprite (id 5016), and re-definitions update it; -1 before it arrives."""

    from crewborg.coworld.scene import SceneState

    scene = SceneState()
    assert scene.server_tick() == -1
    scene.apply(w.define_sprite(5016, 1, 1, "tick 4242"))
    assert scene.server_tick() == 4242
    scene.apply(w.define_sprite(5016, 1, 1, "tick 4243"))
    assert scene.server_tick() == 4243


def _latency_records(destination) -> dict[str, list[dict]]:
    with zipfile.ZipFile(destination) as archive:
        telemetry = next(name for name in archive.namelist() if name != "manifest.json")
        records = _json_records(archive.read(telemetry).decode("utf-8"))
    by_name: dict[str, list[dict]] = {}
    for record in records:
        if record.get("kind") == "metric":
            by_name.setdefault(record["name"], []).append(record)
    return by_name


async def _run_bridge_with_tick_frames(destination, tick_values, monkeypatch) -> dict[str, list[dict]]:
    monkeypatch.setenv("CREWBORG_METRICS", "1")
    monkeypatch.delenv("CREWBORG_TRACE", raising=False)
    monkeypatch.delenv("CREWBORG_TRACE_OUTPUTS", raising=False)
    monkeypatch.setenv("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL", f"file://{destination}")

    class FakeRuntime:
        tick = 0

        def step(self, _observation) -> Command:
            return Command(held_mask=0)

        def close(self) -> None:
            pass

    async def handler(websocket) -> None:
        for value in tick_values:
            await websocket.send(w.define_sprite(5016, 1, 1, f"tick {value}"))
        try:
            while True:
                await asyncio.wait_for(websocket.recv(), timeout=0.25)
        except (asyncio.TimeoutError, ConnectionClosed):
            return

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://localhost:{port}/player?slot=0&token="
        await asyncio.wait_for(run_bridge(url, build=lambda **_: FakeRuntime()), timeout=5.0)
    return _latency_records(destination)


async def test_bridge_metrics_use_server_tick_not_local_counter(tmp_path, monkeypatch) -> None:
    """All bridge metrics must be tagged with the engine's server tick (from the
    marker sprite), not the local received-message counter; keeping up 1:1 means
    tick_drift is 0."""

    by_name = await _run_bridge_with_tick_frames(tmp_path / "a.zip", [100, 101, 102], monkeypatch)
    assert len(by_name["bridge.step_ms"]) == 3
    assert len(by_name["bridge.loop_gap_ms"]) == 2
    assert len(by_name["bridge.tick_drift"]) == 3
    # SERVER ticks (100..102), NOT the local frame counter (1..3).
    assert by_name["bridge.step_ms"][0]["tags"] == {"tick": 100}
    assert by_name["bridge.step_ms"][-1]["tags"] == {"tick": 102}
    assert all(r["value"] == 0 for r in by_name["bridge.tick_drift"])


async def test_bridge_tick_drift_grows_when_frames_skip(tmp_path, monkeypatch) -> None:
    """When the engine's tick jumps faster than frames we process (we fell behind),
    tick_drift grows by the number of skipped frames."""

    # Server advances 100 -> 102 -> 103: between frames 1 and 2 the engine ran 2
    # ticks while we processed 1, so from frame 2 on we are 1 frame behind.
    by_name = await _run_bridge_with_tick_frames(tmp_path / "b.zip", [100, 102, 103], monkeypatch)
    drift = [r["value"] for r in by_name["bridge.tick_drift"]]
    assert drift == [0, 1, 1]
