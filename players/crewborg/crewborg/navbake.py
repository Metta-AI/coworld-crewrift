"""Offline-baked nav graph + occupancy substrate for the static croatoan map.

The nav graph (:mod:`.nav`) and occupancy substrate (:mod:`.agent_tracking`) are
*pure functions* of the static croatoan walkability mask + baked map, but building
them is a heavy pure-Python pass: a pixel flood over the ~1235×659 mask, per-pixel
node/edge construction, and an O(anchors²) A* sweep for the substrate polylines.
At the hosted 250m-CPU budget that first-tick build costs ~14s — freezing the agent
at spawn while the real-time 24 Hz engine streams ahead (the agent then drains a
stale backlog). See WORKING_CONTEXT / design §6.

There is only one, static map in the game, so we bake this **once offline**
(``tools/build/nav_bake.py``) into a vendored asset and load it at runtime.
Loading validates that the streamed walkability mask still matches what we baked
against; on any mismatch, missing asset, or load error we **fall back to the live
build** — correctness never depends on the asset, only startup latency does. A
mismatch is the signal to re-run ``nav_bake`` (the map changed).

Collaborators
-------------
Relies on:
  - ``nav.NavGraph`` and ``agent_tracking.OccupancySubstrate`` (TYPE_CHECKING) — the
    two heavy artifacts pickled together; the ``NavGraph.walkability`` mask is the
    validation key.
  - stdlib ``gzip`` / ``pickle`` / ``importlib.resources``, ``numpy`` (mask compare).
Used by:
  - ``types.update_belief`` calls ``load_navbake`` on the first tick with a walkability
    mask; the bake tool (``tools/build/nav_bake.py``) calls ``serialize_navbake``.
Emits / touches: pure load/serialize — returns the (nav, substrate) pair or ``None``;
  no belief, no events, no mutation. The only side effect is reading the vendored file.

Modifying this file: the load path must be **fail-safe** — every error mode collapses
to ``None`` so the runtime falls back to the live build and never crashes on a bad/
absent/stale asset. Bump ``NAVBAKE_FORMAT`` whenever the serialized payload shape
changes (old assets are then ignored, not mis-loaded), and keep the walkability-mask
equality check as the freshness guard.
"""

from __future__ import annotations

import gzip
import pickle
from importlib import resources
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from crewborg.agent_tracking import OccupancySubstrate
    from crewborg.nav import NavGraph

# Vendored next to croatoan.resources; bumped only by re-running the bake tool.
NAVBAKE_PACKAGE = "crewborg.map"
NAVBAKE_RESOURCE = "croatoan_navbake.pkl.gz"

# Bumped when the serialized payload shape changes (forces a re-bake; old assets
# are ignored rather than mis-loaded).
NAVBAKE_FORMAT = 1


def serialize_navbake(nav: "NavGraph", substrate: "OccupancySubstrate") -> bytes:
    """Gzip-pickle the (nav, substrate) pair for vendoring as the bake asset."""

    payload = {"format": NAVBAKE_FORMAT, "nav": nav, "substrate": substrate}
    return gzip.compress(pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL))


def _read_payload() -> dict[str, Any] | None:
    """Load and validate the vendored bake payload, or ``None`` if unusable.

    Every failure mode (missing file, gzip/pickle error, wrong format, version
    skew that breaks unpickling) collapses to ``None`` so the caller falls back
    to the live build instead of crashing.
    """

    try:
        resource = resources.files(NAVBAKE_PACKAGE).joinpath(NAVBAKE_RESOURCE)
        if not resource.is_file():
            return None
        payload = pickle.loads(gzip.decompress(resource.read_bytes()))
    except Exception:  # noqa: BLE001 - any load failure must degrade to the live build.
        return None
    if not isinstance(payload, dict) or payload.get("format") != NAVBAKE_FORMAT:
        return None
    return payload


def load_navbake(walkability: np.ndarray) -> "tuple[NavGraph, OccupancySubstrate] | None":
    """Return the baked (nav, substrate) iff it was built against ``walkability``.

    The baked ``NavGraph`` carries the exact walkability mask it was built from, so
    we validate by direct comparison: same shape and same pixels. Any difference
    (a redeployed/different map) returns ``None`` → live rebuild + a re-bake signal.
    """

    payload = _read_payload()
    if payload is None:
        return None
    nav = payload.get("nav")
    substrate = payload.get("substrate")
    if nav is None or substrate is None:
        return None
    baked = getattr(nav, "walkability", None)
    if not isinstance(baked, np.ndarray):
        return None
    if baked.shape != walkability.shape or not np.array_equal(baked, walkability):
        return None
    return nav, substrate
