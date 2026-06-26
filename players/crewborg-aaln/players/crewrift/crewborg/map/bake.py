"""Classify resource rects into the baked static map (port of ``sim.nim``).

Tasks (file order = stream index), vents (group = trailing alphanumeric char),
rooms, and the derived emergency button (28×34 centered on the ``bridge`` room).
See design §6.
"""

from __future__ import annotations

from importlib import resources

from players.crewrift.crewborg.map.parser import ResourceRect, load_resource_rects
from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation, Vent

# Croatoan map dimensions (sim.nim:25-26). Only croatoan exists today.
DEFAULT_MAP_WIDTH = 1235
DEFAULT_MAP_HEIGHT = 659

# Derived emergency-button size (sim.nim:789).
BUTTON_WIDTH = 28
BUTTON_HEIGHT = 34


def _name_key(name: str) -> str:
    return name.strip().lower()


def _is_task(name: str) -> bool:
    return _name_key(name) == "task"


def _is_vent(name: str) -> bool:
    key = _name_key(name)
    return key.startswith("vent") and key != "vents"


def _is_room(name: str) -> bool:
    key = _name_key(name)
    return bool(key) and key not in ("vents", "tasks", "rooms") and not key.startswith("vent") and key != "task"


def _vent_group_char(name: str) -> str:
    key = _name_key(name)
    for ch in reversed(key):
        if ch.isalnum() and ch.isascii():
            return ch
    return "v"


def _center(rect: ResourceRect) -> MapPoint:
    return MapPoint(x=rect.x + rect.w // 2, y=rect.y + rect.h // 2)


def _room_distance_squared(room: Room, x: int, y: int) -> int:
    if x < room.x:
        dx = room.x - x
    elif x >= room.x + room.w:
        dx = x - (room.x + room.w - 1)
    else:
        dx = 0
    if y < room.y:
        dy = room.y - y
    elif y >= room.y + room.h:
        dy = y - (room.y + room.h - 1)
    else:
        dy = 0
    return dx * dx + dy * dy


def _nearest_room_name(rooms: list[Room], x: int, y: int) -> str | None:
    best_distance = None
    best_name: str | None = None
    for room in rooms:
        distance = _room_distance_squared(room, x, y)
        if distance == 0:
            return room.name
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_name = room.name
    return best_name


def _task_name(rooms: list[Room], rect: ResourceRect, index: int) -> str:
    center = _center(rect)
    room_name = _nearest_room_name(rooms, center.x, center.y)
    return f"Task near {room_name}" if room_name is not None else f"Task {index + 1}"


def _centered_rect(center: MapPoint, w: int, h: int, map_w: int, map_h: int) -> MapRect:
    x = max(0, min(center.x - w // 2, max(0, map_w - w)))
    y = max(0, min(center.y - h // 2, max(0, map_h - h)))
    return MapRect(x=x, y=y, w=w, h=h)


def bake_map(
    rects: list[ResourceRect],
    width: int = DEFAULT_MAP_WIDTH,
    height: int = DEFAULT_MAP_HEIGHT,
) -> MapData:
    """Classify parsed rects into a :class:`MapData` (design §6)."""

    task_rects: list[ResourceRect] = []
    vents: list[Vent] = []
    rooms: list[Room] = []
    vent_counts: dict[str, int] = {}

    for rect in rects:
        if _is_task(rect.name):
            task_rects.append(rect)
        elif _is_vent(rect.name):
            key = _name_key(rect.name)
            vent_counts[key] = vent_counts.get(key, 0) + 1
            vents.append(
                Vent(
                    x=rect.x,
                    y=rect.y,
                    w=rect.w,
                    h=rect.h,
                    group=_vent_group_char(rect.name),
                    group_index=vent_counts[key],
                )
            )
        elif _is_room(rect.name):
            rooms.append(Room(name=rect.name, x=rect.x, y=rect.y, w=rect.w, h=rect.h))

    tasks = tuple(
        TaskStation(name=_task_name(rooms, rect, i), x=rect.x, y=rect.y, w=rect.w, h=rect.h)
        for i, rect in enumerate(task_rects)
    )

    home = next((room.center for room in rooms if _name_key(room.name) == "bridge"), None)
    if home is None:
        home = rooms[0].center if rooms else MapPoint(x=width // 2, y=height // 2)
    button = _centered_rect(home, BUTTON_WIDTH, BUTTON_HEIGHT, width, height)

    return MapData(
        width=width,
        height=height,
        tasks=tasks,
        vents=tuple(vents),
        rooms=tuple(rooms),
        button=button,
        home=home,
    )


def walkability_matches(map_data: MapData, width: int, height: int) -> bool:
    """Whether a decoded walkability mask's size matches the baked map.

    The walkability mask (from the stream) is sized to the full map; a mismatch
    means the server is running a different map than the bake (design §6).
    """

    return (map_data.width, map_data.height) == (width, height)


def load_croatoan_map() -> MapData:
    """Parse and bake the vendored ``croatoan.resources`` (design §6)."""

    text = resources.files("players.crewrift.crewborg.map").joinpath("croatoan.resources").read_text()
    return bake_map(load_resource_rects(text))
