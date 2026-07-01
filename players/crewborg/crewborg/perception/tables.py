"""The three retained Sprite-v1 scene tables (design §3.1).

Plain mutable dataclasses held by ``SceneState`` (the bridge-owned scene). Sprite
pixels are intentionally *not* retained — the decoder keeps only label/size, plus
the decoded ``walkability map`` alpha mask, which lives on ``SceneState``.
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
