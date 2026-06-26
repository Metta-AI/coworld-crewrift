"""Baked static-map types (design §6). Frozen pydantic models for belief.map."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MapPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    x: int
    y: int


class MapRect(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> MapPoint:
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
        return MapPoint(x=self.x + self.w // 2, y=self.y + self.h // 2)


class Vent(BaseModel):
    """A vent rectangle. Vents sharing a ``group`` teleport together."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: int
    y: int
    w: int
    h: int
    group: str
    group_index: int

    @property
    def center(self) -> MapPoint:
        return MapPoint(x=self.x + self.w // 2, y=self.y + self.h // 2)


class Room(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    x: int
    y: int
    w: int
    h: int

    @property
    def center(self) -> MapPoint:
        return MapPoint(x=self.x + self.w // 2, y=self.y + self.h // 2)


class MapData(BaseModel):
    """The complete baked static map."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    width: int
    height: int
    tasks: tuple[TaskStation, ...]
    vents: tuple[Vent, ...]
    rooms: tuple[Room, ...]
    button: MapRect
    home: MapPoint
