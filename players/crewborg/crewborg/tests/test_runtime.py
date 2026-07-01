"""Runtime smoke: the assembled idle agent steps cleanly (design §1)."""

from __future__ import annotations

from crewborg import build_runtime
from crewborg.coworld.scene import SceneState
from crewborg.tests import sprite_wire as w
from crewborg.types import Observation
from players.player_sdk.trace import ListMetricsSink, ListTraceSink


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


def test_idle_runtime_emits_canonical_trace_events() -> None:
    trace = ListTraceSink()
    metrics = ListMetricsSink()
    runtime = build_runtime(trace_sink=trace, metrics_sink=metrics)
    scene = SceneState()
    scene.apply(w.clear_objects())
    scene.tick += 1
    runtime.step(Observation(scene=scene, tick=scene.tick))
    runtime.close()

    names = set(trace.names())
    # Every per-tick boundary the SDK traces should appear for a healthy loop,
    # including the strategy seam: build_runtime must thread the sinks into the
    # SynchronousStrategyRunner so its telemetry is not silently dropped.
    assert {
        "perception",
        "belief_updated",
        "action_intent",
        "act_command",
        "strategy_evaluated",
    } <= names
    assert any(sample.name == "cyborg.strategy.decide_ms" for sample in metrics.samples)
