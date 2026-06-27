"""Runtime smoke: the assembled idle agent steps cleanly (design §1)."""

from __future__ import annotations

from crewborg import build_runtime
from crewborg.coworld.scene import SceneState
from crewborg.tests import sprite_wire as w
from crewborg.types import Observation
from players.player_sdk.trace import ListTraceSink


def test_idle_runtime_holds_neutral_mask_and_tracks_ticks() -> None:
    trace = ListTraceSink()
    runtime = build_runtime(trace_sink=trace)
    scene = SceneState()

    last = None
    for _ in range(5):
        scene.apply(w.clear_objects())
        scene.tick += 1
        last = runtime.step(Observation(scene=scene, tick=scene.tick))

    assert runtime.active_mode_name == "idle"
    assert last is not None and last.held_mask == 0
    assert runtime.belief.ticks_observed == 5
    assert runtime.belief.messages_applied == 5
    assert runtime.belief.map is not None  # baked at startup
    runtime.close()
