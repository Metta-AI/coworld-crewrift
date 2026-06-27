"""Crewborg static map: the resource-file bake (design §6).

Vent, emergency-button, room, and task-station locations are not in the Sprite-v1
stream — they live in the game's map resource file, which crewborg vendors
(``croatoan.resources``) and parses at startup. The decoded walkability mask
(from the stream) validates the bake.
"""

from crewborg.map.bake import (
    DEFAULT_MAP_HEIGHT,
    DEFAULT_MAP_WIDTH,
    bake_map,
    load_croatoan_map,
    walkability_matches,
)
from crewborg.map.types import (
    MapData,
    MapPoint,
    MapRect,
    Room,
    TaskStation,
    Vent,
)

__all__ = [
    "DEFAULT_MAP_HEIGHT",
    "DEFAULT_MAP_WIDTH",
    "MapData",
    "MapPoint",
    "MapRect",
    "Room",
    "TaskStation",
    "Vent",
    "bake_map",
    "load_croatoan_map",
    "walkability_matches",
]
