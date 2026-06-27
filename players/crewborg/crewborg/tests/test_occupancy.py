"""Perception-tape predicate tests (design §5.1)."""

from __future__ import annotations

import numpy as np

from crewborg.strategy.occupancy import (
    neighbors_within,
    players_in_rect,
    rect_visible,
)
from crewborg.types import PerceptionFrame


def _frame(players, camera=(0, 0)) -> PerceptionFrame:
    return PerceptionFrame(tick=1, camera_x=camera[0], camera_y=camera[1], players=dict(players))


def test_players_in_rect_respects_bounds_and_margin() -> None:
    frame = _frame({"red": (55, 55), "green": (48, 55), "blue": (100, 100)})
    inside = players_in_rect(frame, 50, 50, 8, 8)  # rect [50,58)x[50,58)
    assert set(inside) == {"red"}
    # green at x=48 enters once the rect is grown by a 3px walk margin.
    grown = players_in_rect(frame, 50, 50, 8, 8, margin=3)
    assert set(grown) == {"red", "green"}


def test_neighbors_within_excludes_self_and_respects_range() -> None:
    frame = _frame({"red": (100, 100), "green": (110, 100), "blue": (130, 100)})
    near = neighbors_within(frame, (100, 100), 400, exclude="red")  # 20px radius
    assert near == ["green"]  # green 10px in; blue 30px out; red excluded


def _masked(mask: np.ndarray, camera=(0, 0)) -> PerceptionFrame:
    return PerceptionFrame(tick=1, camera_x=camera[0], camera_y=camera[1], visible_mask=mask)


def test_rect_visible_uses_the_line_of_sight_mask() -> None:
    frame = _masked(np.ones((64, 64), dtype=bool))
    assert rect_visible(frame, 50, 50, 8, 8)  # fully lit
    occluded = np.ones((64, 64), dtype=bool)
    occluded[52, 53] = False  # one occluded pixel inside the rect
    assert not rect_visible(_masked(occluded), 50, 50, 8, 8)
