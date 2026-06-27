"""The three retained Sprite-v1 scene tables (design §3.1).

The in-memory row types for the perception layer's first stage: plain mutable
dataclasses held by ``SceneState`` (the bridge-owned scene), keyed by their
protocol ids. Sprite pixels are intentionally *not* retained — the decoder keeps
only label/size, plus the decoded ``walkability map`` alpha mask, which lives on
``SceneState``.

Collaborators
-------------
Relies on: stdlib ``dataclasses`` only — an import-leaf with no crewborg deps.
Used by:
  - ``perception.decoder`` — constructs and stores these as it parses messages.
  - ``coworld.scene.SceneState`` — holds the ``sprites``/``objects``/``layers``
    dicts of these.
  - ``perception.resolve`` — reads ``ObjectState`` / ``SpriteDef`` to build entities.
Emits / touches: nothing — pure data containers (mutated by the decoder).

Modifying this file: these are mutable by design (the decoder overwrites entries
in place each frame) — do not freeze them. Field names/types mirror the wire layout
the decoder fills in; keep them in sync with ``perception.decoder`` and the
``perception.constants`` field meanings.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SpriteDef:
    """A defined sprite: dimensions + label. Pixels are not retained."""

    width: int
    height: int
    label: str


@dataclass
class ObjectState:
    """A placed object. ``x``/``y`` are camera-relative (screen) coordinates."""

    x: int
    y: int
    z: int
    layer: int
    sprite_id: int


@dataclass
class LayerDef:
    """A layer's kind and flags (viewport tracked separately if needed)."""

    layer_type: int
    flags: int
