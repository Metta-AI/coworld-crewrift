"""Offline nav-bake: serialization round-trips and the load-time mask validation.

The bake must produce a nav graph + occupancy substrate identical to the live
build (so play is unchanged), and ``load_navbake`` must accept only the exact mask
it was baked against — any difference falls back to the live build.
"""

from __future__ import annotations

import gzip
import pickle

import numpy as np

from crewborg.agent_tracking import build_occupancy_substrate
from crewborg.map.types import MapData, MapPoint, MapRect, TaskStation
from crewborg.nav import build_nav_graph
from crewborg.navbake import NAVBAKE_FORMAT, load_navbake, serialize_navbake


def _small_mask() -> np.ndarray:
    """A tiny walkable rectangle inside walls — enough for a real nav + substrate."""

    mask = np.zeros((64, 96), dtype=bool)
    mask[8:56, 8:88] = True
    return mask


def _small_map() -> MapData:
    """A minimal map whose home + two tasks sit inside ``_small_mask`` (fast bake)."""

    return MapData(
        width=96,
        height=64,
        tasks=(TaskStation(name="a", x=16, y=16, w=4, h=4), TaskStation(name="b", x=72, y=44, w=4, h=4)),
        vents=(),
        rooms=(),
        button=MapRect(x=40, y=28, w=8, h=8),
        home=MapPoint(x=12, y=12),
    )


def test_serialize_navbake_round_trips_nav_and_substrate() -> None:
    mask = _small_mask()
    map_data = _small_map()
    nav = build_nav_graph(mask, map_data=map_data)
    substrate = build_occupancy_substrate(nav, map_data)

    payload = pickle.loads(gzip.decompress(serialize_navbake(nav, substrate)))
    assert payload["format"] == NAVBAKE_FORMAT

    loaded_nav = payload["nav"]
    assert np.array_equal(loaded_nav.walkability, nav.walkability)
    assert loaded_nav.node_point == nav.node_point
    assert loaded_nav.adjacency == nav.adjacency
    assert loaded_nav.reachable == nav.reachable
    assert loaded_nav.task_anchors == nav.task_anchors

    loaded_sub = payload["substrate"]
    assert loaded_sub.anchors == substrate.anchors
    assert loaded_sub.polylines.keys() == substrate.polylines.keys()


def test_load_navbake_accepts_exact_mask_and_rejects_changes() -> None:
    """Against the committed croatoan asset: the embedded mask loads, anything else
    falls back. Skips cleanly if the asset hasn't been baked (CI without capture)."""

    from importlib import resources

    from crewborg.navbake import NAVBAKE_PACKAGE, NAVBAKE_RESOURCE

    resource = resources.files(NAVBAKE_PACKAGE).joinpath(NAVBAKE_RESOURCE)
    if not resource.is_file():
        import pytest

        pytest.skip("no vendored navbake asset (run tools/nav_bake.py to create it)")

    payload = pickle.loads(gzip.decompress(resource.read_bytes()))
    baked_mask = payload["nav"].walkability

    loaded = load_navbake(baked_mask)
    assert loaded is not None
    nav, substrate = loaded
    assert np.array_equal(nav.walkability, baked_mask)
    assert len(substrate.polylines) > 0

    tampered = baked_mask.copy()
    tampered[0, 0] = not tampered[0, 0]
    assert load_navbake(tampered) is None
    assert load_navbake(np.ones((10, 10), dtype=bool)) is None
