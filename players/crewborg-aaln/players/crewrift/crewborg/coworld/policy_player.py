"""Crewborg's Sprite-v1 websocket bridge (design §3, AGENTS.md §Transport).

The bridge connects to the Crewrift engine, maintains a :class:`SceneState` as
binary messages arrive, drives ``runtime.step`` once per tick, and sends an input
packet only when the held button mask changes. It exits cleanly when the server
closes the socket (= game over).

Each incoming binary message is decoded into the ``SceneState`` and drives one
``runtime.step``; the held button mask is sent only when it changes, and meeting
chat is sent during Voting.

Logging: the full unfiltered trace/metric stream is recorded into an in-memory
SQLite database and uploaded as the player debug artifact at episode end
(:mod:`players.crewrift.crewborg.artifact`) instead of being streamed to stderr.
The stderr JSON sinks remain available for local debugging when ``CREWBORG_TRACE``
(or the trace group/include envs) is set explicitly.

Environment:

- ``COGAMES_ENGINE_WS_URL`` — websocket URL including ``?slot=…&token=…``
  (the runner fills these in; token validation is at HTTP upgrade).
- ``COWORLD_PLAYER_ARTIFACT_UPLOAD_URL`` — optional per-slot artifact upload URL
  (presigned ``https://`` PUT, or ``file://`` on local runs). Absent ⇒ no upload.
- ``CREWBORG_DEBUG_SPRITES`` — truthy (``1``/``true``/``yes``/``on``) enables
  best-effort debug-sprite replay overlays (engine PR #67): each tick the bridge
  emits a ``0x86`` debug-sprite frame visualizing the live nav plan
  (:mod:`players.crewrift.crewborg.debug_overlay`), recorded into the
  ``.bitreplay`` and shown over crewborg's POV when a replay viewer toggles "D".
  Default OFF — normal evals send nothing extra.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from players.crewrift.crewborg import build_runtime
from players.crewrift.crewborg.action import encode_chat, encode_input
from players.crewrift.crewborg.artifact import (
    SqliteEpisodeRecorder,
    episode_info_from_ws_url,
    upload_episode_artifact,
)
from players.crewrift.crewborg.coworld.scene import SceneState
from players.crewrift.crewborg.debug_overlay import build_overlay, encode_debug_sprites
from players.crewrift.crewborg.map import walkability_matches
from players.crewrift.crewborg.trace import (
    StderrJsonMetricsSink,
    StderrJsonTraceSink,
    TeeMetricsSink,
    TeeTraceSink,
    TraceConfig,
)
from players.crewrift.crewborg.types import Observation

METRICS_ENV = "CREWBORG_METRICS"
DEBUG_SPRITES_ENV = "CREWBORG_DEBUG_SPRITES"


async def run_bridge(
    engine_ws_url: str,
    *,
    connect: Callable[..., Any] = websockets.connect,
    build: Callable[..., Any] = build_runtime,
) -> None:
    """Connect, run the per-tick loop, and return when the socket closes."""

    scene = SceneState()
    # All traces/metrics land in the SQLite episode recorder (uploaded as the
    # player artifact at episode end). Stderr JSON streaming is opt-in via the
    # CREWBORG_TRACE* / CREWBORG_METRICS envs for local debugging.
    recorder = SqliteEpisodeRecorder()
    # Populate best-effort, non-secret episode metadata (currently the player slot,
    # parsed from the connect URL; the auth token is dropped, never stored). Wrapped
    # so a missing/odd URL never fails the episode. Richer fields (resolved role,
    # game outcome) can be pushed later via recorder.set_episode_info(...) once the
    # event/belief layer holds them — the hook and summary plumbing are in place.
    try:
        recorder.set_episode_info(**episode_info_from_ws_url(engine_ws_url))
    except Exception as error:  # noqa: BLE001 — metadata is best-effort, never fatal.
        print(f"crewborg artifact: episode-info capture skipped: {error!r}", file=sys.stderr, flush=True)
    runtime = build(
        trace_sink=TeeTraceSink(
            recorder,
            StderrJsonTraceSink.from_env() if _stderr_trace_enabled() else None,
        ),
        metrics_sink=TeeMetricsSink(
            recorder,
            StderrJsonMetricsSink() if _metrics_enabled() else None,
        ),
        # Lets the event tracer stream the per-tick positions table into the
        # artifact and push role/color/outcome into summary.json.
        episode_recorder=recorder,
    )
    last_sent_mask: int | None = None
    walkability_checked = False
    # Debug-sprite replay overlays (engine PR #67): opt-in, best-effort, deduped.
    debug_sprites = _debug_sprites_enabled()
    last_overlay: bytes | None = None

    # Guarantee runtime cleanup (the strategy runner may own background
    # threads/tasks) even if connect, a step, or a shutdown-race send raises.
    try:
        async with connect(engine_ws_url, max_size=None) as websocket:
            try:
                async for message in websocket:
                    if isinstance(message, str):
                        # The /player stream is binary Sprite-v1; ignore stray text.
                        continue
                    scene.apply(message)
                    scene.tick += 1

                    # Validate the baked map against the streamed walkability mask
                    # once it arrives (design §6); a size mismatch means a different
                    # map than croatoan. Warn loudly rather than misnavigate later.
                    if not walkability_checked and scene.walkability is not None:
                        walkability_checked = True
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

                    command = runtime.step(Observation(scene=scene, tick=scene.tick))

                    # Send only when the held mask changes (design §3.3). The first
                    # tick sends the neutral mask once, establishing "all released".
                    if command.held_mask != last_sent_mask:
                        await websocket.send(encode_input(command.held_mask))
                        last_sent_mask = command.held_mask

                    # Meeting chat (accepted only during Voting); sent as it appears.
                    if command.chat is not None:
                        await websocket.send(encode_chat(command.chat))

                    # Debug-sprite replay overlay (opt-in). Best-effort and
                    # wrapped so it can NEVER crash the episode (like the
                    # artifact path); only resent when the bytes change, to keep
                    # the side-channel cheap.
                    if debug_sprites:
                        try:
                            overlay = build_overlay(runtime.belief, runtime.action_state)
                            if overlay is not None and overlay != last_overlay:
                                await websocket.send(encode_debug_sprites(overlay))
                                last_overlay = overlay
                        except Exception as error:  # noqa: BLE001 — debug-only, never fatal.
                            print(
                                f"crewborg debug sprites: overlay skipped: {error!r}",
                                file=sys.stderr,
                                flush=True,
                            )
            except ConnectionClosed:
                # Game end: the Crewrift server closes the socket to signal the
                # episode is over. It does so *abruptly* — no close handshake
                # (code 1006, "no close frame received or sent") — which the
                # websockets async iterator surfaces as ConnectionClosedError
                # rather than swallowing (as it does a clean ConnectionClosedOK).
                # Either way a close means the game is over: treat it as normal
                # termination so the process exits 0. The Coworld runner requires
                # every player container to exit 0; propagating here would fail
                # the whole episode (runner._wait_for_player_exit).
                print("game over: server closed the connection", file=sys.stderr, flush=True)
    finally:
        runtime.close()
        # Upload before the container is torn down. Best-effort: a missing URL
        # or failed upload never fails the episode (upload_episode_artifact
        # swallows and logs all errors).
        upload_episode_artifact(recorder)
        recorder.close()


def main() -> None:
    engine_ws_url = os.environ["COGAMES_ENGINE_WS_URL"]
    asyncio.run(run_bridge(engine_ws_url))


def _metrics_enabled() -> bool:
    trace_level = os.environ.get("CREWBORG_TRACE", "").strip().lower()
    metrics_flag = os.environ.get(METRICS_ENV, "").strip().lower()
    return trace_level == "debug" or metrics_flag in {"1", "true", "yes", "on"}


def _debug_sprites_enabled() -> bool:
    """Whether to emit debug-sprite replay overlays (opt-in; default OFF)."""

    return os.environ.get(DEBUG_SPRITES_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _stderr_trace_enabled() -> bool:
    """Stderr JSON trace streaming is opt-in: any explicit trace targeting enables it."""

    config = TraceConfig.from_env()
    return bool(config.level) or config.has_targets


if __name__ == "__main__":
    main()
