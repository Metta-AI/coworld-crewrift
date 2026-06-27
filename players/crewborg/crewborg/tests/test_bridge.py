"""In-process bridge smoke (design §3).

Stands up a real websocket server, streams a few binary "scene" frames, and
asserts the bridge connects, drives the idle runtime, sends the neutral input
packet exactly once (send-only-on-change), and exits cleanly when the server
closes the socket.
"""

from __future__ import annotations

import asyncio

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, ConnectionClosedError

from crewborg.action import INPUT_HEADER
from crewborg.coworld.policy_player import (
    MIDGAME_RECONNECT_ATTEMPTS,
    run_bridge,
)
from crewborg.tests import sprite_wire as w
from crewborg.types import Command

pytestmark = pytest.mark.asyncio


async def _no_sleep(_seconds: float) -> None:
    """Stub for asyncio.sleep so reconnect-backoff tests don't wait in real time."""


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
