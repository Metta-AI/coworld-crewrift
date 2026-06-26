"""Pure spatial predicates over a perception-tape frame (design §5.1).

Occupancy and adjacency are never stored — they are derived on demand from the
raw ``PerceptionFrame`` (positions + camera viewport). Keeping these as pure
functions means new region/predicate questions cost a function, not a belief
schema change. A frame's ``players``/``bodies`` are the entities we actually saw,
and ``rect_observed`` answers whether a region was inside the viewport — i.e.
whether an *absence* from ``players`` is meaningful ("clear") or just unobserved.
"""

from __future__ import annotations

from players.crewrift.crewborg.perception.constants import SCREEN_HEIGHT, SCREEN_WIDTH
from players.crewrift.crewborg.types import PerceptionFrame


def players_in_rect(
    frame: PerceptionFrame, x: int, y: int, w: int, h: int, *, margin: int = 0
) -> dict[str, tuple[int, int]]:
    """Colors whose collision point is inside the rect, optionally grown by ``margin``."""

    x0, y0 = x - margin, y - margin
    x1, y1 = x + w + margin, y + h + margin
    return {c: p for c, p in frame.players.items() if x0 <= p[0] < x1 and y0 <= p[1] < y1}


def rect_observed(frame: PerceptionFrame, x: int, y: int, w: int, h: int, *, margin: int = 0) -> bool:
    """Whether the (optionally grown) rect lies wholly within the frame's viewport.

    Viewport containment is a *proxy* for visibility — it ignores occlusion. Prefer
    :func:`rect_visible`, which uses the real line-of-sight mask when present and
    falls back to this only when no mask is available.
    """

    x0, y0 = x - margin, y - margin
    x1, y1 = x + w + margin, y + h + margin
    return (
        frame.camera_x <= x0
        and x1 <= frame.camera_x + SCREEN_WIDTH
        and frame.camera_y <= y0
        and y1 <= frame.camera_y + SCREEN_HEIGHT
    )


def rect_visible(frame: PerceptionFrame, x: int, y: int, w: int, h: int, *, margin: int = 0) -> bool:
    """Whether the (optionally grown) rect was actually in line of sight this frame.

    Uses the decoded ``shadow`` line-of-sight mask: the rect must lie wholly within
    the viewport *and* every one of its screen pixels must be visible (unoccluded).
    Falls back to plain viewport containment (:func:`rect_observed`) when no mask is
    available (e.g. before the overlay arrives, or in tests without one).
    """

    if frame.visible_mask is None:
        return rect_observed(frame, x, y, w, h, margin=margin)
    x0, y0 = x - margin, y - margin
    x1, y1 = x + w + margin, y + h + margin
    sx0, sy0 = x0 - frame.camera_x, y0 - frame.camera_y
    sx1, sy1 = x1 - frame.camera_x, y1 - frame.camera_y
    height, width = frame.visible_mask.shape
    if sx0 < 0 or sy0 < 0 or sx1 > width or sy1 > height:
        return False  # spills off-screen ⇒ not wholly observed
    return bool(frame.visible_mask[sy0:sy1, sx0:sx1].all())


def neighbors_within(
    frame: PerceptionFrame, point: tuple[int, int], range_sq: int, *, exclude: str | None = None
) -> list[str]:
    """Colors whose collision point is within ``range_sq`` of ``point`` this frame."""

    return [
        c
        for c, p in frame.players.items()
        if c != exclude and (p[0] - point[0]) ** 2 + (p[1] - point[1]) ** 2 <= range_sq
    ]
