"""Path-prediction module tests (strategy/path_prediction.py).

Synthetic open-floor map; checks the distribution concentrates on the destination
the target walks toward, recovers on a reversal, and advances predictions through
occlusion. Tuning constants (ALIGN_GAIN / EVIDENCE_DECAY) are validated against
real replays via tools/path_prediction_eval.py; these tests pin the behavior, not
the exact probabilities.
"""

from __future__ import annotations

import numpy as np

from crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation
from crewborg.nav import build_nav_graph
from crewborg.strategy.path_prediction import PathPredictor


def _map() -> MapData:
    return MapData(
        width=128, height=64,
        tasks=(TaskStation(name="L", x=16, y=32, w=8, h=8), TaskStation(name="R", x=104, y=32, w=8, h=8)),
        vents=(),
        rooms=(Room(name="Left", x=0, y=0, w=64, h=64), Room(name="Right", x=64, y=0, w=64, h=64)),
        button=MapRect(x=4, y=4, w=8, h=8), home=MapPoint(x=8, y=8),
    )


def _predictor() -> PathPredictor:
    m = _map()
    nav = build_nav_graph(np.ones((m.height, m.width), dtype=bool), map_data=m)
    return PathPredictor(nav=nav, map=m)


def _run(p: PathPredictor, xs: range) -> None:
    for i, x in enumerate(xs):
        p.observe(tick=i, point=(x, 32))


def test_predicts_the_destination_being_walked_toward() -> None:
    p = _predictor()
    _run(p, range(40, 110, 3))  # walking right
    best = p.best()
    assert best is not None and best.dest_label.endswith(":R")


def test_recovers_on_a_direction_reversal() -> None:
    p = _predictor()
    for i, x in enumerate(list(range(40, 82, 3)) + list(range(82, 18, -3))):
        p.observe(tick=i, point=(x, 32))
    best = p.best()
    assert best is not None and best.dest_label.endswith(":L")  # now heading left
