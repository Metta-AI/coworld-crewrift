"""Perception-tape predicate tests (design §5.1)."""

from __future__ import annotations

import numpy as np

from crewborg.strategy.occupancy import (
    neighbors_within,
    players_in_rect,
    rect_observed,
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


def test_rect_observed_is_viewport_containment() -> None:
    frame = _frame({}, camera=(0, 0))  # viewport [0,128)x[0,128)
    assert rect_observed(frame, 50, 50, 8, 8)
    assert not rect_observed(frame, 124, 50, 8, 8)  # spills past the right edge
    # The margin is included in the containment test.
    assert not rect_observed(frame, 1, 50, 8, 8, margin=3)  # 1-3 = -2 < 0


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


def test_rect_visible_requires_the_rect_fully_on_screen() -> None:
    frame = _masked(np.ones((64, 64), dtype=bool))
    assert not rect_visible(frame, 60, 60, 8, 8)  # 60+8 = 68 spills past the mask edge


def test_rect_visible_falls_back_to_viewport_without_a_mask() -> None:
    frame = PerceptionFrame(tick=1, camera_x=0, camera_y=0)  # no mask yet
    assert rect_visible(frame, 50, 50, 8, 8)  # inside the 128px viewport
    assert not rect_visible(frame, 124, 50, 8, 8)  # spills past the right edge
