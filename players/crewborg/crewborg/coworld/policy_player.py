"""Crewborg's Sprite-v1 websocket bridge (design §3).

The bridge connects to the Crewrift engine, maintains a :class:`SceneState` as
binary messages arrive, drives ``runtime.step`` once per tick, and sends an input
packet only when the held button mask changes. It exits cleanly when the server
closes the socket (= game over).

Each incoming binary message is decoded into the ``SceneState`` and drives one
``runtime.step``; the held button mask is sent only when it changes, and meeting
chat is sent during Voting.

Environment:

- ``COWORLD_PLAYER_WS_URL`` — websocket URL including ``?slot=…&token=…``
  (the runner fills these in; token validation is at HTTP upgrade). The legacy
  ``COGAMES_ENGINE_WS_URL`` alias (same value) is accepted as a fallback.
- ``CREWBORG_TRACE_OUTPUTS`` — SDK trace output specs (``format@destination``,
  comma-separated; see ``players.player_sdk.trace_outputs``). Defaults to
  ``jsonl@artifact``: traces/metrics stream to a temp file and are zipped and
  uploaded to ``COWORLD_PLAYER_ARTIFACT_UPLOAD_URL`` at exit, keeping stderr
  under Observatory's policy-log line cap. When no upload URL is present (the
  bridge is running outside a Coworld runner), the bridge falls back to
  ``jsonl@stderr`` instead of crashing.
- ``CREWBORG_METRICS`` / ``CREWBORG_TRACE`` — metric fan-out and trace
  verbosity/filtering (see ``crewborg.trace``).
- ``CREWBORG_RECONNECT_DEADLINE`` / ``CREWBORG_RECONNECT_INTERVAL`` /
  ``CREWBORG_MIDGAME_RECONNECTS`` / ``CREWBORG_MIDGAME_RECONNECT_INTERVAL`` — tune the
  connect-retry and mid-game-reconnect behavior (see the constants below).
- ``CREWBORG_CAPTURE_WALKABILITY`` — when set, emit the streamed walkability mask once
  as a single base64 JSON stderr line for ``tools/nav_bake.py capture`` (off by default).

Collaborators
-------------
Relies on:
  - ``coworld.scene.SceneState`` — the mutable scene the bridge folds frames into
    (``apply`` / ``server_tick`` / ``walkability``).
  - ``__init__.build_runtime`` — builds the SDK ``runtime`` the bridge drives
    (``runtime.step`` per tick, ``runtime.tick`` seeding, ``runtime.belief.map``,
    ``runtime.close``).
  - ``action.encode_input`` / ``encode_chat`` — the only two outbound Sprite-v1 packets.
  - ``map.walkability_matches`` — validates the baked map against the streamed mask.
  - ``trace.TraceConfig`` and ``players.player_sdk`` (``TraceOutputs`` / trace-spec parse).
  - ``types.Observation`` — wraps ``(scene, tick)`` handed to ``runtime.step``.
Used by:
  - ``main`` (the container entrypoint) → ``asyncio.run(run_bridge(...))``.
Emits / touches: outbound input/chat packets on the websocket; ``bridge.loop_gap_ms`` /
  ``bridge.step_ms`` / ``bridge.tick_drift`` metrics; stderr warnings (wrong-map,
  connect failure, game-over) and the optional walkability capture line.

Modifying this file: this owns the *transport lifecycle only* — connect/retry, frame
decode hand-off, per-tick step, send-on-change, and clean exit. It contains no strategy.
The load-bearing invariant is the reconnect discriminator: ``state.frames_seen`` decides
whether a closed socket is a startup race (retry) or a real game-over (stop) — never
reconnect after a legitimate game end. Change the retry logic deliberately.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import websockets

from crewborg import build_runtime
from crewborg.action import encode_chat, encode_input
from crewborg.coworld.scene import SceneState
from crewborg.map import walkability_matches
from crewborg.trace import TraceConfig
from crewborg.types import Observation
from players.player_sdk import TraceOutputs, parse_trace_output_specs

METRICS_ENV = "CREWBORG_METRICS"

DEFAULT_TRACE_OUTPUTS = "jsonl@artifact"
FALLBACK_TRACE_OUTPUTS = "jsonl@stderr"

# --- aggressive initial-connect reconnect (2026-06-24) ----------------------------
# Hosted episodes were dying at a high rate with a -100 "connect_timeout": the player
# produced 0-1 telemetry lines and no stderr (it NEVER received a frame), and the
# episode never reached "running". That is an INITIAL-connect failure — the player
# container races the engine's /player websocket coming up, the single connect()
# throws (or closes with no frames), and the process exits, failing the episode.
#
# Fix: retry establishing the connection until the FIRST frame arrives, on a short
# FLAT interval (no exponential backoff — this is a startup race against the engine
# binding its socket, so we want to be aggressive and catch it the instant it comes
# up, not back off to multi-second waits), bounded by a wall-clock deadline (so we
# never hang past the runner's episode timeout). Once frames flow we hand off to the
# normal loop, where an abrupt close still means "game over" (the engine ends
# episodes that way) — we must NOT reconnect after a legitimate game end, so the
# discriminator is strictly "did we ever receive a frame on this connection."
#
# A refused connect returns almost instantly (the engine isn't listening yet), so a
# 0.1s interval probes ~10x/sec — aggressive, but not a pure busy-loop hammering the
# host. Over the default deadline that's ~1000+ attempts before we give up.
RECONNECT_DEADLINE_SECONDS = float(os.environ.get("CREWBORG_RECONNECT_DEADLINE", "120"))
RECONNECT_INTERVAL_SECONDS = float(os.environ.get("CREWBORG_RECONNECT_INTERVAL", "0.1"))
CONNECT_OPEN_TIMEOUT = 10.0     # per-attempt handshake timeout (don't hang one attempt)

# After the game has started, a dropped socket is AMBIGUOUS: it is how the Crewrift engine
# signals game-over (an unclean 1006 close), but it is also exactly what a transient mid-game
# network blip looks like. So don't give up on the first drop — try to reconnect a few times.
# If frames resume, the game was still live and we recover; if a run of reconnects delivers no
# new frames, the game really ended and we stop. A reconnect that DOES deliver new frames is
# progress and refreshes the idle budget (so an hour-long game survives several independent
# blips). The overall RECONNECT_DEADLINE is the ultimate backstop.
MIDGAME_RECONNECT_ATTEMPTS = int(os.environ.get("CREWBORG_MIDGAME_RECONNECTS", "5"))
MIDGAME_RECONNECT_INTERVAL_SECONDS = float(os.environ.get("CREWBORG_MIDGAME_RECONNECT_INTERVAL", "0.25"))

# Connection-establishment failures worth retrying (vs a real game-over close, which
# only counts once a frame has been seen). OSError covers ECONNREFUSED while the
# engine is still binding; the websockets handshake/timeout errors cover races.
_RETRYABLE_CONNECT_ERRORS = (
    OSError,
    asyncio.TimeoutError,
    websockets.exceptions.WebSocketException,
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
    tick_offset: int | None = None  # (server_tick - scene.tick) when the marker first appears

# The engine pushes one frame per game tick at ~24 Hz and does NOT wait for the
# player (docs/crewrift-player.md). At the hosted 250m-CPU budget that gives
# runtime.step() ~42 ms per tick; exceeding it makes frames queue and inputs
# land late.
#
# `scene.tick` is a local received-message counter; the engine also streams its
# authoritative tick as a sprite (`scene.server_tick()`). We drive the SDK runtime
# from the server tick so perception, belief, and ALL tracing/metrics carry the
# engine's true tick — and `bridge.tick_drift` reports exactly how many frames we've
# fallen behind (server tick minus frames we've processed), not a wall-clock estimate.


def build_trace_outputs() -> TraceOutputs:
    """Build the SDK trace outputs, defaulting to the player artifact zip.

    The artifact destination needs the runner-provided
    ``COWORLD_PLAYER_ARTIFACT_UPLOAD_URL``; the SDK raises when it is missing
    rather than skipping. Crashing here would happen before connect and fail
    the episode (a -100 connect timeout), so fall back to plain stderr JSONL
    — same content, just subject to the hosted log cap.
    """

    trace_config = TraceConfig.from_env()
    try:
        return TraceOutputs.from_env(
            prefix="CREWBORG",
            event_filter=trace_config.allows,
            metrics_enabled=_metrics_enabled(),
            default_outputs=DEFAULT_TRACE_OUTPUTS,
        )
    except ValueError as exc:
        print(
            f"WARNING: trace outputs unavailable ({exc}); falling back to {FALLBACK_TRACE_OUTPUTS}",
            file=sys.stderr,
            flush=True,
        )
        return TraceOutputs.from_specs(
            parse_trace_output_specs(FALLBACK_TRACE_OUTPUTS),
            event_filter=trace_config.allows,
            metrics_enabled=_metrics_enabled(),
        )


async def run_bridge(
    engine_ws_url: str,
    *,
    connect: Callable[..., Any] = websockets.connect,
    build: Callable[..., Any] = build_runtime,
) -> None:
    """Connect (retrying the initial connection) and run the per-tick loop.

    Returns when the game ends (the engine closes the socket after we've received
    frames) or when the reconnect deadline passes without ever connecting.
    """

    scene = SceneState()
    # The with-block guarantees outputs.close() runs at exit — that close is what
    # zips and uploads the artifact (when configured), so it must happen before
    # the container exits and the runner tears the pod down.
    with build_trace_outputs() as outputs:
        runtime = build(trace_sink=outputs.trace_sink, metrics_sink=outputs.metrics_sink)
        metrics = outputs.metrics_sink
        # Session state lives out here so a reconnect resumes cleanly rather than
        # rebuilding the runtime/belief (which would discard everything learned).
        state = _BridgeState()

        # Guarantee runtime cleanup (the strategy runner may own background
        # threads/tasks) even if connect, a step, or a shutdown-race send raises.
        try:
            await _connect_with_retry(
                engine_ws_url, connect=connect, scene=scene, runtime=runtime,
                metrics=metrics, state=state,
            )
        finally:
            runtime.close()


async def _connect_with_retry(
    engine_ws_url: str,
    *,
    connect: Callable[..., Any],
    scene: SceneState,
    runtime: Any,
    metrics: Any,
    state: _BridgeState,
) -> None:
    """Establish the websocket, retrying the INITIAL connect on a short flat interval
    until the first frame arrives, then run the session. A connection that closes
    *after* frames were seen is a normal game-over (stop); a failure or close
    *before* any frame is a connect race (retry until the deadline)."""

    deadline = time.monotonic() + RECONNECT_DEADLINE_SECONDS
    attempt = 0
    midgame_idle = 0  # consecutive post-game-start reconnects that delivered no new frames
    last_error: BaseException | None = None  # closed-with-no-frames also retries
    while True:
        attempt += 1
        frames_before = state.frames_seen
        try:
            async with connect(engine_ws_url, max_size=None, open_timeout=CONNECT_OPEN_TIMEOUT) as websocket:
                await _run_session(websocket, scene=scene, runtime=runtime, metrics=metrics, state=state)
            # Session returned without raising: a clean close. After the game has started a
            # clean close is a normal game-over (the engine only does an *unclean* 1006 drop
            # mid-stream; a graceful close means it's done).
            if state.frames_seen:
                return  # the game ran and ended normally
            # Closed with no frames — treat as a connect race and retry.
        except _RETRYABLE_CONNECT_ERRORS as exc:
            if state.frames_seen:
                # Mid-game abrupt drop — ambiguous between game-over (1006) and a transient
                # blip. A reconnect that delivered new frames is progress (reset the budget);
                # otherwise count it. Conclude game-over only after a run of idle reconnects
                # (or the overall deadline), so a real network blip gets a chance to recover.
                midgame_idle = 0 if state.frames_seen > frames_before else midgame_idle + 1
                if midgame_idle >= MIDGAME_RECONNECT_ATTEMPTS or time.monotonic() >= deadline:
                    print("game over: server closed the connection", file=sys.stderr, flush=True)
                    return
                print(
                    f"mid-game disconnect — reconnecting ({midgame_idle}/{MIDGAME_RECONNECT_ATTEMPTS} idle)",
                    file=sys.stderr, flush=True,
                )
                await asyncio.sleep(min(MIDGAME_RECONNECT_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
                continue
            last_error = exc  # pre-first-frame failure: retry below

        if time.monotonic() >= deadline:
            print(
                f"ERROR: could not connect to engine after {attempt} attempt(s) / "
                f"{RECONNECT_DEADLINE_SECONDS:.0f}s ({type(last_error).__name__}: {last_error}); giving up.",
                file=sys.stderr, flush=True,
            )
            return
        # Flat, short interval — stay aggressive so we catch the engine the instant it
        # binds, rather than backing off to multi-second waits. Log only the first few
        # so a slow startup doesn't spew thousands of lines into the policy log.
        if attempt <= 3:
            print(f"connect attempt {attempt} failed (no frames yet); retrying", file=sys.stderr, flush=True)
        await asyncio.sleep(min(RECONNECT_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))


async def _run_session(
    websocket: Any,
    *,
    scene: SceneState,
    runtime: Any,
    metrics: Any,
    state: _BridgeState,
) -> None:
    """Drive the per-tick loop on an established connection until it closes.

    Raises the websockets close/connection errors to the caller, which decides —
    based on whether any frame was seen — whether it's a game-over or a connect
    race to retry. A clean ``ConnectionClosed`` from the async iterator returns
    normally (the caller still inspects ``state.frames_seen``)."""

    async for message in websocket:
        if isinstance(message, str):
            # The /player stream is binary Sprite-v1; ignore stray text.
            continue
        state.frames_seen += 1
        # loop_gap_ms: wall-clock between consecutive frame arrivals
        # — sustained gaps *below* the ~42 ms frame interval mean queued
        # frames are being drained, i.e. we had fallen behind the engine.
        # (Measured here; emitted below, tagged with the server tick.)
        arrival = time.perf_counter()
        loop_gap_ms = (
            round((arrival - state.previous_arrival) * 1000.0, 3)
            if state.previous_arrival is not None
            else None
        )
        scene.apply(message)
        scene.tick += 1

        # Ground-truth tick: prefer the engine's streamed tick-marker
        # over our local frame counter, and drive the SDK runtime from it
        # so perception, belief.last_tick, and ALL tracing/metrics carry
        # the engine's true tick. step() does `self.tick += 1`, so seed it
        # one below the server tick to land exactly on it. Before the
        # marker arrives (first frames) fall back to the local counter.
        server_tick = scene.server_tick()
        tick = server_tick if server_tick >= 0 else scene.tick
        if server_tick >= 0:
            runtime.tick = server_tick - 1
            if state.tick_offset is None:
                state.tick_offset = server_tick - scene.tick

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
            # Optional capture: emit the streamed walkability mask once
            # so tools/nav_bake.py can re-bake the offline nav asset when
            # the map changes. Inert unless CREWBORG_CAPTURE_WALKABILITY
            # is set; the mask is the authoritative input crewborg sees.
            if _capture_walkability_enabled():
                _emit_walkability_capture(scene.walkability)

        # step_ms: the per-tick compute budget check (~42 ms at 24 Hz).
        step_start = time.perf_counter()
        command = runtime.step(Observation(scene=scene, tick=tick))
        step_end = time.perf_counter()
        if loop_gap_ms is not None:
            metrics.histogram("bridge.loop_gap_ms", loop_gap_ms, tags={"tick": tick})
        metrics.histogram(
            "bridge.step_ms",
            round((step_end - step_start) * 1000.0, 3),
            tags={"tick": tick},
        )
        # tick_drift: frames we've fallen behind the engine since the
        # marker first appeared (ground truth: server tick minus frames
        # processed). 0 means we're keeping up; growth means falling behind.
        if server_tick >= 0:
            metrics.gauge(
                "bridge.tick_drift",
                server_tick - scene.tick - state.tick_offset,
                tags={"tick": tick},
            )

        # Send only when the held mask changes (design §3.3). The first
        # tick sends the neutral mask once, establishing "all released".
        if command.held_mask != state.last_sent_mask:
            await websocket.send(encode_input(command.held_mask))
            state.last_sent_mask = command.held_mask

        # Meeting chat (accepted only during Voting); sent as it appears.
        if command.chat is not None:
            await websocket.send(encode_chat(command.chat))
        state.previous_arrival = arrival


def main() -> None:
    """Container entrypoint: resolve the player websocket URL from the environment and
    run the bridge to completion. Exits non-zero (``SystemExit``) if no URL is set."""

    # Canonical player-contract var is COWORLD_PLAYER_WS_URL; COGAMES_ENGINE_WS_URL is
    # a legacy alias the runner also sets to the same value. Prefer the canonical one,
    # fall back to the alias (see metta docs/roles/PLAYER.md, ../../player-build.md).
    engine_ws_url = os.environ.get("COWORLD_PLAYER_WS_URL") or os.environ.get("COGAMES_ENGINE_WS_URL")
    if not engine_ws_url:
        raise SystemExit("no player websocket URL: set COWORLD_PLAYER_WS_URL "
                         "(or the legacy COGAMES_ENGINE_WS_URL)")
    asyncio.run(run_bridge(engine_ws_url))


def _metrics_enabled() -> bool:
    """Whether to emit metrics: on if ``CREWBORG_TRACE=debug`` or ``CREWBORG_METRICS`` is
    a truthy flag (``1``/``true``/``yes``/``on``). Off by default to keep the log lean."""

    trace_level = os.environ.get("CREWBORG_TRACE", "").strip().lower()
    metrics_flag = os.environ.get(METRICS_ENV, "").strip().lower()
    return trace_level == "debug" or metrics_flag in {"1", "true", "yes", "on"}


def _capture_walkability_enabled() -> bool:
    return os.environ.get("CREWBORG_CAPTURE_WALKABILITY", "").strip().lower() in {"1", "true", "yes", "on"}


def _emit_walkability_capture(walkability: Any) -> None:
    """Print the walkability mask to stderr as one bit-packed, base64 JSON line.

    A line, not a file: the player container's filesystem isn't collected on local
    runs, but its stderr is (the policy log). ``tools/nav_bake.py capture`` reads
    this line back. ~100 KB packed for the croatoan mask — fine as a single line.
    """

    import base64
    import json

    import numpy as np

    mask = np.ascontiguousarray(walkability, dtype=bool)
    packed = np.packbits(mask)
    print(
        json.dumps(
            {
                "event": "walkability_capture",
                "shape": list(mask.shape),
                "packbits_b64": base64.b64encode(packed.tobytes()).decode("ascii"),
            }
        ),
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
