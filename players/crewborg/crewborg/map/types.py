"""Baked static-map types (design §6). Frozen pydantic models for belief.map.

The output schema of the map bake (:mod:`crewborg.map.bake`): geometry the
Sprite-v1 stream never sends (vent/button/room/task locations) parsed from the game's
resource file into immutable values. All coordinates are world (map) pixels, the same
space the bake's walkability mask validates against. Models are ``frozen`` (hashable,
shareable across belief snapshots) and ``extra="forbid"`` (an unexpected field is a
bake/schema mismatch, not a silently-dropped key).

Collaborators
-------------
Built by: ``map.bake`` (``bake_map`` / ``load_croatoan_map``).
Used by: ``belief.map`` (a :class:`MapData`), and nav/strategy code that reads station,
  vent, room, and button geometry off it.

Modifying this file: these are the data contract between the bake and every consumer of
``belief.map``. Adding/renaming a field means re-baking and updating ``map.bake``; the
``extra="forbid"`` config means a stale prebaked asset will fail to load loudly.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MapPoint(BaseModel):
    """An immutable world-pixel point ``(x, y)``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: int
    y: int


class MapRect(BaseModel):
    """An immutable world-pixel rectangle: top-left ``(x, y)`` plus size ``(w, h)``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> MapPoint:
        """The rectangle's center, floor-divided (matches the bake's integer geometry)."""
        return MapPoint(x=self.x + self.w // 2, y=self.y + self.h // 2)


class TaskStation(BaseModel):
    """A task rectangle. Its position in :attr:`MapData.tasks` is the stream's
    ``3000/7000 + idx`` task index (design §6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> MapPoint:
        """The task tile's center — the nav goal for completing this task."""
        return MapPoint(x=self.x + self.w // 2, y=self.y + self.h // 2)


class Vent(BaseModel):
    """A vent rectangle. Vents sharing a ``group`` teleport together; ``group_index`` is
    its 1-based ordinal within that group (assignment order in the bake)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: int
    y: int
    w: int
    h: int
    group: str
    group_index: int

    @property
    def center(self) -> MapPoint:
        """The vent tile's center — where the agent stands to enter/exit it."""
        return MapPoint(x=self.x + self.w // 2, y=self.y + self.h // 2)


class Room(BaseModel):
    """A named room rectangle (region label; used to name nearby tasks and for context)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> MapPoint:
        """The room's center point."""
        return MapPoint(x=self.x + self.w // 2, y=self.y + self.h // 2)


class MapData(BaseModel):
    """The complete baked static map — the value carried on ``belief.map``.

    Attributes:
      ``width`` / ``height`` — map size in world pixels; the streamed walkability mask
        must match these (validated by ``bake.walkability_matches``).
      ``tasks`` — task stations in **stream order**: index ``i`` is the ``3000/7000 + i``
        task index the perception stream refers to (design §6).
      ``vents`` — vent rectangles (grouped teleport pairs/sets).
      ``rooms`` — named region rectangles.
      ``button`` — the derived emergency-button rectangle (centered on ``home``).
      ``home`` — the bridge room's center: the meeting/emergency-button anchor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    width: int
    height: int
    tasks: tuple[TaskStation, ...]
    vents: tuple[Vent, ...]
    rooms: tuple[Room, ...]
    button: MapRect
    home: MapPoint
