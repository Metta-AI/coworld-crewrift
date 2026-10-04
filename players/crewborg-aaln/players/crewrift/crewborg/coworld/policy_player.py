"""Sprite-v1 bridge with engine-assigned native seats and joined private artifacts.

The ordinary renderer/controller still receives each binary engine frame. The
opt-in native-evidence protocol supplies the accepted slot/index and final engine
results. Connection closure without that terminal evidence remains truncated.
Native requests, received bodies, controller packets, and frames stay in the
private ZIP uploaded to COWORLD_PLAYER_ARTIFACT_UPLOAD_URL. SIGTERM/SIGINT share
one two-second deadline with native work, socket closure, and artifact upload.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import struct
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets
from pydantic import ConfigDict, Field, JsonValue, TypeAdapter
from websockets.exceptions import WebSocketException

from players.crewrift.crewborg import build_runtime
from players.crewrift.crewborg.action import encode_chat, encode_input
from players.crewrift.crewborg.artifact import ARTIFACT_README, SqliteEpisodeRecorder
from players.crewrift.crewborg.coworld.private_artifact import (
    PrivateArtifact,
    PrivateOutcome,
)
from players.crewrift.crewborg.coworld.scene import SceneState
from players.crewrift.crewborg.debug_overlay import build_overlay, encode_debug_sprites
from players.crewrift.crewborg.map import walkability_matches
from players.crewrift.crewborg.native import (
    NativeModel,
    NativeSession,
    PlayerRegistration,
)
from players.crewrift.crewborg.scripted import policy_profile_from_env
from players.crewrift.crewborg.types import Observation

METRICS_ENV = "CREWBORG_METRICS"

CONNECT_OPEN_TIMEOUT = 10.0
RECONNECT_DEADLINE_SECONDS = float(os.environ.get("CREWBORG_RECONNECT_DEADLINE", "120"))
RECONNECT_INTERVAL_SECONDS = 0.1
_RETRYABLE_CONNECT_ERRORS = (
    OSError,
    asyncio.TimeoutError,
    WebSocketException,
)


class EngineWelcome(NativeModel):
    kind: Literal["native_welcome"]
    protocol: Literal["crewrift.native-evidence.v1"]
    player_slot: int = Field(ge=0, le=15)
    engine_player_index: int = Field(ge=0)
    configured_seat: bool
    tick: int = Field(ge=0)


class EngineTerminal(NativeModel):
    kind: Literal["native_terminal"]
    protocol: Literal["crewrift.native-evidence.v1"]
    tick: int = Field(ge=0)
    results: JsonValue


ENGINE_MESSAGE = TypeAdapter(
    EngineWelcome | EngineTerminal, config=ConfigDict(hide_input_in_errors=True)
)


@dataclass
class _BridgeState:
    """Per-bridge session state that must survive a reconnect.

    Keeping this outside the connection means a retried connect resumes against the
    same scene/runtime rather than rebuilding belief. ``frames_seen`` is the
    discriminator the retry loop uses: >0 means the game has started, so a close is
    a real game-over (stop); 0 means we never connected (a race — keep retrying).
    """

    frames_seen: int = 0
    last_sent_mask: int | None = None
    walkability_checked: bool = False
    previous_arrival: float | None = None
    terminal: EngineTerminal | None = None
    socket: Any = None
    last_overlay: bytes | None = None
    frame_owners_joined: bool = True


class _ReceivedFrames:
    """Own ordered immutable frames on disk while the controller falls behind."""

    def __init__(self):
        self.stream = tempfile.TemporaryFile()  # noqa: SIM115 - held until both owned frame tasks join; an unjoined reader retains its spool.
        self.read_offset = 0
        self.received = 0
        self.applied = 0
        self.ready = asyncio.Event()

    def append(self, frame: bytes) -> tuple[int, float]:
        arrival = time.perf_counter()
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() + 16 + len(frame) > 200 * 1024 * 1024:
            raise ValueError("Private received-frame spool exceeds 200 MiB")
        self.stream.write(struct.pack("!Qd", len(frame), arrival))
        self.stream.write(frame)
        sequence = self.received
        self.received += 1
        self.ready.set()
        return sequence, arrival

    async def next(self) -> tuple[int, bytes, float]:
        while self.applied == self.received:
            self.ready.clear()
            await self.ready.wait()
        self.stream.seek(self.read_offset)
        size, arrival = struct.unpack("!Qd", self.stream.read(16))
        frame = self.stream.read(size)
        self.read_offset = self.stream.tell()
        return self.applied, frame, arrival


# The engine pushes one frame per game tick at ~24 Hz and does NOT wait for the
# player (docs/crewrift-protocol.md). At the hosted 250m-CPU budget that gives
# runtime.step() ~42 ms per tick; exceeding it makes frames queue and inputs
# land late.
#
# `scene.tick` is a local received-message counter; the engine also streams its
# authoritative tick as a sprite (`scene.server_tick()`). We drive the SDK runtime
# from the server tick so perception, belief, and ALL tracing/metrics carry the
# engine's true tick — and `bridge.tick_drift` reports exactly how many frames we've
# fallen behind (server tick minus frames we've processed), not a wall-clock estimate.


async def run_bridge(
    engine_ws_url: str,
    *,
    connect: Callable[..., Any] = websockets.connect,
    build: Callable[..., Any] = build_runtime,
) -> None:
    """Own native calls, engine transport, and private upload under one STOP deadline."""
    directory = Path(tempfile.mkdtemp(prefix="crewborg-private-"))
    artifact = PrivateArtifact(directory / "player.zip")
    registration = PlayerRegistration.from_url(engine_ws_url)
    native = NativeSession(
        registration, directory / "native.jsonl", artifact.record_policy
    )
    recorder = SqliteEpisodeRecorder()
    recorder.set_episode_info(player_slot=registration.requested_slot)
    runtime = build(
        trace_sink=recorder,
        metrics_sink=recorder,
        native_session=native,
        policy_profile=policy_profile_from_env(),
        episode_recorder=recorder,
    )
    scene = SceneState()
    state = _BridgeState()
    owner = asyncio.current_task()
    assert owner is not None
    loop = asyncio.get_running_loop()

    def stop() -> None:
        first = native.shutdown_deadline is None
        native.begin_stop(time.monotonic() + 2)
        if first:
            owner.cancel()

    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop)
    split = urlsplit(engine_ws_url)
    query = [
        (key, value)
        for key, value in parse_qsl(split.query)
        if key != "native_evidence"
    ]
    url = urlunsplit(
        split._replace(query=urlencode([*query, ("native_evidence", "1")]))
    )
    try:
        await _connect_with_retry(
            url,
            connect=connect,
            scene=scene,
            runtime=runtime,
            metrics=recorder,
            state=state,
            native=native,
            artifact=artifact,
        )
    finally:
        failure = sys.exception()
        deadline = native.begin_stop(time.monotonic() + 2)
        runtime.close()
        native_joined = await native.stop(deadline)
        # This controller has no background NLP loader.
        nlp_joined = True
        socket_joined = state.socket is None or await NativeSession.settle(
            state.socket.close(), deadline
        )
        summary = recorder.summary()
        database = recorder.database_bytes()
        stored_members = {
            "trace.db": database,
            "summary.json": json.dumps(summary, indent=2).encode(),
            "README.md": ARTIFACT_README.encode(),
            "report.html": recorder._report_html(summary, database).encode(),
        }
        recorder.close()
        stored_writers_joined = time.monotonic() < deadline
        terminal = state.terminal is not None
        complete = (
            terminal
            and native_joined
            and state.frame_owners_joined
            and nlp_joined
            and socket_joined
            and stored_writers_joined
            and failure is None
        )
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)
        outcome = PrivateOutcome(
            status="completed" if complete else "truncated",
            native_work_joined=native_joined,
            frame_owners_joined=state.frame_owners_joined,
            nlp_work_joined=nlp_joined,
            socket_joined=socket_joined,
            stored_writers_joined=stored_writers_joined,
            terminal_engine_evidence=terminal,
            requested_player_slot=registration.requested_slot,
            engine_player_index=registration.engine_player_index,
            source_revision=os.environ.get("COWORLD_SOURCE_REVISION"),
            image_digest=os.environ.get("COWORLD_GAME_IMAGE_DIGEST"),
            failure_kind=type(failure).__name__
            if failure is not None
            else (None if complete else "MissingTerminalOrUnjoinedWork"),
        )
        await artifact.finish(
            outcome,
            os.environ.get("COWORLD_PLAYER_ARTIFACT_UPLOAD_URL"),
            deadline,
            stored_members,
        )


async def _connect_with_retry(
    engine_ws_url: str,
    *,
    connect: Callable[..., Any],
    scene: SceneState,
    runtime: Any,
    metrics: Any,
    state: _BridgeState,
    native: NativeSession,
    artifact: PrivateArtifact,
) -> None:
    deadline = time.monotonic() + RECONNECT_DEADLINE_SECONDS
    while state.socket is None:
        try:
            state.socket = await connect(
                engine_ws_url,
                max_size=None,
                open_timeout=min(
                    CONNECT_OPEN_TIMEOUT, max(0.001, deadline - time.monotonic())
                ),
                close_timeout=1,
            )
        except _RETRYABLE_CONNECT_ERRORS:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Engine connection admission deadline exceeded"
                ) from None
            await asyncio.sleep(
                min(RECONNECT_INTERVAL_SECONDS, max(0, deadline - time.monotonic()))
            )
    await _run_session(
        state.socket,
        scene=scene,
        runtime=runtime,
        metrics=metrics,
        state=state,
        native=native,
        artifact=artifact,
    )
    if state.terminal is None:
        raise RuntimeError(
            "Engine stream ended without authoritative terminal evidence"
        )


async def _run_session(
    websocket: Any,
    *,
    scene: SceneState,
    runtime: Any,
    metrics: Any,
    state: _BridgeState,
    native: NativeSession,
    artifact: PrivateArtifact,
) -> None:
    """Receive terminal independently of the ordered ordinary controller loop."""
    frames = _ReceivedFrames()

    async def receive() -> None:
        async for message in websocket:
            if native.shutdown_deadline is not None:
                return
            if isinstance(message, str):
                event = ENGINE_MESSAGE.validate_json(message)
                artifact.write_record(
                    {"kind": "engine_event", "event": event.model_dump(mode="json")}
                )
                if isinstance(event, EngineWelcome):
                    registration = native.registration
                    if (
                        registration.requested_slot is not None
                        and event.player_slot != registration.requested_slot
                    ):
                        raise ValueError(
                            "Engine assigned a different requested player slot"
                        )
                    registration.engine_player_index = event.engine_player_index
                    registration.authenticated_upgrade = event.configured_seat
                    registration.assigned_slot = (
                        event.player_slot if event.configured_seat else None
                    )
                    if (
                        registration.requested_slot is not None
                        and not event.configured_seat
                    ):
                        raise ValueError(
                            "Hosted native seat lacks configured engine admission"
                        )
                else:
                    state.terminal = event
                    deadline = native.begin_stop(time.monotonic() + 2)
                    async with asyncio.timeout_at(deadline):
                        await websocket.send(
                            json.dumps(
                                {"kind": "native_terminal_ack", "tick": event.tick},
                                separators=(",", ":"),
                            )
                        )
                    return
                continue
            if native.registration.engine_player_index is None:
                raise RuntimeError(
                    "Engine sent a player frame before assigned-seat evidence"
                )
            sequence, received_at = frames.append(message)
            artifact.write_record(
                {
                    "kind": "engine_frame",
                    "frame_sequence": sequence,
                    "received_monotonic": received_at,
                    "bytes_b64": base64.b64encode(message).decode(),
                    "engine_player_index": native.registration.engine_player_index,
                }
            )
            state.frames_seen += 1
        if state.terminal is None:
            raise RuntimeError(
                "Engine stream ended without authoritative terminal evidence"
            )

    async def control() -> None:
        while state.terminal is None:
            sequence, message, arrival = await frames.next()
            # loop_gap_ms: wall-clock between consecutive frame arrivals
            # — sustained gaps *below* the ~42 ms frame interval mean queued
            # frames are being drained, i.e. we had fallen behind the engine.
            # (Measured here; emitted below, tagged with the server tick.)
            loop_gap_ms = (
                round((arrival - state.previous_arrival) * 1000.0, 3)
                if state.previous_arrival is not None
                else None
            )
            scene.apply(message)
            scene.tick += 1

            # This caller observes local frame ticks; it has no authoritative tick sprite.
            tick = scene.tick
            # Validate the baked map against the streamed walkability mask
            # once it arrives (design §6); a size mismatch means a different
            # map than croatoan. Warn loudly rather than misnavigate later.
            if not state.walkability_checked and scene.walkability is not None:
                state.walkability_checked = True
                map_data = runtime.belief.map
                if map_data is not None and not walkability_matches(
                    map_data, scene.walkability_width, scene.walkability_height
                ):
                    print(
                        "WARNING: walkability map "
                        f"{scene.walkability_width}x{scene.walkability_height} does not match "
                        f"baked map {map_data.width}x{map_data.height}; server may be running "
                        "a different map than croatoan.",
                        file=sys.stderr,
                        flush=True,
                    )
            # step_ms: the per-tick compute budget check (~42 ms at 24 Hz).
            step_start = time.perf_counter()
            command = runtime.step(Observation(scene=scene, tick=tick))
            if native.shutdown_deadline is not None:
                return
            frames.applied += 1
            artifact.write_record(
                {
                    "kind": "controller_frame_applied",
                    "frame_sequence": sequence,
                    "tick": tick,
                }
            )
            step_end = time.perf_counter()
            if loop_gap_ms is not None:
                metrics.histogram(
                    "bridge.loop_gap_ms", loop_gap_ms, tags={"tick": tick}
                )
            metrics.histogram(
                "bridge.step_ms",
                round((step_end - step_start) * 1000.0, 3),
                tags={"tick": tick},
            )
            # Send only when the held mask changes (design §3.3). The first
            # tick sends the neutral mask once, establishing "all released".
            if command.held_mask != state.last_sent_mask:
                packet = encode_input(command.held_mask)
                artifact.write_record(
                    {
                        "kind": "controller_packet",
                        "tick": tick,
                        "phase": "input",
                        "bytes_b64": base64.b64encode(packet).decode(),
                    }
                )
                await websocket.send(packet)
                if native.shutdown_deadline is not None:
                    return
                state.last_sent_mask = command.held_mask

            # Meeting chat (accepted only during Voting); sent as it appears.
            if command.chat is not None:
                packet = encode_chat(command.chat)
                artifact.write_record(
                    {
                        "kind": "controller_packet",
                        "tick": tick,
                        "phase": "chat",
                        "bytes_b64": base64.b64encode(packet).decode(),
                    }
                )
                await websocket.send(packet)
                if native.shutdown_deadline is not None:
                    return
            if os.environ.get("CREWBORG_DEBUG_SPRITES", "").lower() in {
                "1",
                "true",
                "yes",
                "on",
            }:
                overlay = build_overlay(runtime.belief, runtime.action_state)
                if overlay is not None and overlay != state.last_overlay:
                    await websocket.send(encode_debug_sprites(overlay))
                    state.last_overlay = overlay
            if native.shutdown_deadline is not None:
                return
            await websocket.send(bytes([0x85]))
            state.previous_arrival = arrival
            await asyncio.sleep(0)

    receiver = asyncio.create_task(receive())
    controller = asyncio.create_task(control())
    owners = {receiver, controller}
    try:
        done, _ = await asyncio.wait(owners, return_when=asyncio.FIRST_COMPLETED)
        if state.terminal is not None and not receiver.done():
            await receiver
    finally:
        deadline = native.begin_stop(time.monotonic() + 2)
        for task in owners:
            if not task.done():
                task.cancel()
        settled, pending = await asyncio.wait(
            owners, timeout=max(0, deadline - time.monotonic())
        )
        state.frame_owners_joined = not pending
        artifact.write_record(
            {
                "kind": "controller_frame_outcome",
                "received_frames": frames.received,
                "controller_applied_frames": frames.applied,
                "unapplied_frames": frames.received - frames.applied,
                "owners_joined": state.frame_owners_joined,
            }
        )
        if not pending:
            frames.stream.close()
        else:

            def release_joined_spool(_: asyncio.Task[None]) -> None:
                if all(task.done() for task in owners):
                    frames.stream.close()

            for task in pending:
                task.add_done_callback(release_joined_spool)
        for task in settled:
            if not task.cancelled():
                task.exception()
    for task in done:
        task.result()
    if not state.frame_owners_joined:
        raise RuntimeError(
            "Engine frame owners did not join before the original deadline"
        )


def main() -> None:
    # Canonical player-contract var is COWORLD_PLAYER_WS_URL; COGAMES_ENGINE_WS_URL is
    # a legacy alias the runner also sets to the same value. Prefer the canonical one,
    # fall back to the alias (see docs/reference/coworld-platform.md).
    engine_ws_url = os.environ.get("COWORLD_PLAYER_WS_URL") or os.environ.get(
        "COGAMES_ENGINE_WS_URL"
    )
    if not engine_ws_url:
        raise SystemExit(
            "no player websocket URL: set COWORLD_PLAYER_WS_URL "
            "(or the legacy COGAMES_ENGINE_WS_URL)"
        )
    asyncio.run(run_bridge(engine_ws_url))


def _metrics_enabled() -> bool:
    trace_level = os.environ.get("CREWBORG_TRACE", "").strip().lower()
    metrics_flag = os.environ.get(METRICS_ENV, "").strip().lower()
    return trace_level == "debug" or metrics_flag in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    main()
