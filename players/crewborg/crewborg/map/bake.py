"""Classify resource rects into the baked static map (port of ``sim.nim``).

Stage 2 of the static-map bake (``parser`` parse → classify here → ``types.MapData``).
Sorts the flat :class:`ResourceRect` list into tasks (kept in **file order**, which is
the perception stream's task index), vents (grouped by their trailing alphanumeric char),
rooms, and the derived emergency button (28×34 centered on the ``bridge`` room, or the
first room / map center as fallback). See design §6.

Rect classification is by name: exactly ``task`` → a task tile; ``vent*`` (but not the
plural ``vents`` legend label) → a vent; any other non-empty name that isn't a section
legend (``vents``/``tasks``/``rooms``) → a room. Tasks are named for their nearest room
so logs read meaningfully.

Collaborators
-------------
Relies on: ``map.parser`` (``load_resource_rects`` + :class:`ResourceRect`) and
  ``map.types`` (the output models). ``load_croatoan_map`` reads the vendored
  ``croatoan.resources`` via ``importlib.resources``.
Used by: ``__init__.build_runtime`` bakes ``belief.map`` at startup; the bridge calls
  ``walkability_matches`` once the streamed mask arrives to detect a wrong-map server.

Modifying this file: the name-classification rules and the file-order = task-index
invariant are a port of the engine's ``sim.nim`` and a contract with the perception
stream. Changing them silently shifts task indices — keep them aligned to the source.
"""

from __future__ import annotations

from importlib import resources

from crewborg.map.parser import ResourceRect, load_resource_rects
from crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation, Vent

# Croatoan map dimensions (sim.nim:25-26). Only croatoan exists today.
DEFAULT_MAP_WIDTH = 1235
DEFAULT_MAP_HEIGHT = 659

# Derived emergency-button size (sim.nim:789).
BUTTON_WIDTH = 28
BUTTON_HEIGHT = 34


def _name_key(name: str) -> str:
    """Canonical comparison key for a rect name: trimmed and lowercased."""
    return name.strip().lower()


def _is_task(name: str) -> bool:
    """A task tile is named exactly ``task`` (case-insensitive)."""
    return _name_key(name) == "task"


def _is_vent(name: str) -> bool:
    """A vent name starts with ``vent`` but is not the plural ``vents`` legend label."""
    key = _name_key(name)
    return key.startswith("vent") and key != "vents"


def _is_room(name: str) -> bool:
    """A room is any non-empty name that is neither a task, a vent, nor a section legend
    (``vents``/``tasks``/``rooms``) — i.e. the catch-all named region."""
    key = _name_key(name)
    return bool(key) and key not in ("vents", "tasks", "rooms") and not key.startswith("vent") and key != "task"


def _vent_group_char(name: str) -> str:
    """The vent's teleport group: its last ASCII-alphanumeric char (e.g. ``vent a`` → ``a``).
    Vents that share this char teleport together. Falls back to ``"v"`` if none is found."""
    key = _name_key(name)
    for ch in reversed(key):
        if ch.isalnum() and ch.isascii():
            return ch
    return "v"


def _center(rect: ResourceRect) -> MapPoint:
    """The integer center of a parsed rect (floor-divided, matching the engine)."""
    return MapPoint(x=rect.x + rect.w // 2, y=rect.y + rect.h // 2)


def _room_distance_squared(room: Room, x: int, y: int) -> int:
    """Squared distance from point ``(x, y)`` to ``room``'s rectangle (0 if inside).

    Per-axis clamp distance — the standard point-to-AABB metric — kept squared to avoid
    a sqrt, since it is only used to rank rooms by nearness in ``_nearest_room_name``.
    """
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
    """Name of the room nearest ``(x, y)`` (a containing room short-circuits), or ``None``
    if there are no rooms. Used to give each task a human-readable ``Task near <room>`` name."""

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
    """A display name for a task: ``Task near <nearest room>``, or ``Task <n>`` (1-based)
    when there are no rooms to anchor it. Cosmetic only — the task's identity is its index."""

    center = _center(rect)
    room_name = _nearest_room_name(rooms, center.x, center.y)
    return f"Task near {room_name}" if room_name is not None else f"Task {index + 1}"


def _centered_rect(center: MapPoint, w: int, h: int, map_w: int, map_h: int) -> MapRect:
    """A ``w×h`` rect centered on ``center``, clamped fully inside the ``map_w×map_h`` map.
    Used to derive the emergency-button rectangle from the bridge-room center."""

    x = max(0, min(center.x - w // 2, max(0, map_w - w)))
    y = max(0, min(center.y - h // 2, max(0, map_h - h)))
    return MapRect(x=x, y=y, w=w, h=h)


def bake_map(
    rects: list[ResourceRect],
    width: int = DEFAULT_MAP_WIDTH,
    height: int = DEFAULT_MAP_HEIGHT,
) -> MapData:
    """Classify parsed rects into a :class:`MapData` (design §6).

    Buckets each rect by name (task / vent / room), preserving file order for tasks so a
    task's position equals its stream index; assigns each vent a 1-based ``group_index``
    within its group char; names tasks for their nearest room; derives ``home`` from the
    ``bridge`` room center (falling back to the first room, then map center) and the
    emergency ``button`` as a fixed-size rect centered there. ``width``/``height`` default
    to the croatoan dimensions and define the coordinate space consumers validate against.
    """

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

    text = resources.files("crewborg.map").joinpath("croatoan.resources").read_text()
    return bake_map(load_resource_rects(text))
