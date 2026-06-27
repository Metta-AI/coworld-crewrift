"""``SceneState`` — the bridge-owned mutable view of the Sprite-v1 stream.

Per design §3 this is the lone non-pydantic SDK-facing type: a plain dataclass
holding the three retained tables (Layers/Sprites/Objects), the decoded camera,
the walkability mask, and the ``shadow`` line-of-sight mask, which ``Observation``
references by pointer. Raw sprite pixels are otherwise discarded — only those two
alpha masks are retained (the walkability mask for nav, the line-of-sight mask
flows on into the perception tape).

Byte-level decoding lives in :mod:`crewborg.perception.decoder`;
``apply`` delegates to it. (The line-of-sight mask is stored on the ``visible_mask``
field — decoded from the ``shadow`` vision-overlay sprite, hence the name above.)

Collaborators
-------------
Relies on:
  - ``perception.decoder.apply_message`` — the byte-level decode ``apply`` delegates to;
    it mutates every field below in place.
  - ``perception.tables`` — ``LayerDef`` / ``ObjectState`` / ``SpriteDef``, the values
    held in the retained tables.
Used by:
  - ``coworld.policy_player`` — constructs one ``SceneState`` per bridge session, calls
    ``apply`` per frame, reads ``server_tick`` / ``walkability`` / ``tick``.
  - ``types.Observation`` — carries this scene by pointer into ``runtime.step``;
    perception reads the tables and the two alpha masks downstream.

Modifying this file: this is a passive container — decoding logic belongs in the decoder,
not here. ``server_tick`` is the only behavior, and it must keep matching the marker by
**label prefix** (not sprite id) so an id-offset change in the engine doesn't break tick
ground truth. Fields are mutated externally by the decoder; keep them plain and public.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from crewborg.perception.decoder import apply_message
from crewborg.perception.tables import LayerDef, ObjectState, SpriteDef

# The engine streams an invisible 1x1 sprite labeled ``"tick <N>"`` every frame
# (game object/sprite id 5016; see coworld-crewrift global.nim addSpritePlayerTickMarker),
# where N is the engine's authoritative tick. We match on the label prefix rather than
# the id so the parse survives id-offset changes (the reference notsus player does the same).
SERVER_TICK_LABEL_PREFIX = "tick "


@dataclass
class SceneState:
    """Mutable scene the bridge maintains as Sprite-v1 messages arrive."""

    # Bridge bookkeeping.
    tick: int = 0
    messages_applied: int = 0

    # Retained tables, keyed by their protocol ids.
    sprites: dict[int, SpriteDef] = field(default_factory=dict)
    objects: dict[int, ObjectState] = field(default_factory=dict)
    layers: dict[int, LayerDef] = field(default_factory=dict)

    # Camera recovered from the world-map object (id 1, sprite 1). World coords
    # are unavailable until it first arrives; degrade gracefully meanwhile.
    camera_ready: bool = False
    camera_x: int = 0
    camera_y: int = 0

    # Decoded walkability: a bool grid (alpha > 0 ⇒ walkable), or None until the
    # ``walkability map`` sprite has been defined.
    walkability: np.ndarray | None = None
    walkability_width: int = 0
    walkability_height: int = 0

    # Decoded line-of-sight: a screen-space bool grid (True ⇒ visible) from the
    # ``shadow`` vision overlay, overwritten each time that sprite is resent (on any
    # camera move), or None until it first arrives. Aligned to the current camera.
    visible_mask: np.ndarray | None = None

    def apply(self, message: bytes) -> None:
        """Decode one incoming binary message into the scene tables.

        Raises ``SpriteProtocolError`` on malformed input (the bridge lets it
        propagate, closing the connection per the protocol).
        """

        self.messages_applied += 1
        apply_message(self, message)

    def server_tick(self) -> int:
        """The engine's authoritative tick from the streamed tick-marker sprite.

        Returns the value parsed from the ``"tick <N>"`` sprite label, or ``-1``
        when the marker has not arrived yet (the first frames) so the bridge can
        fall back to its local message counter. Scans the retained sprite table and
        takes the max in case a stale definition lingers.
        """

        latest = -1
        for sprite in self.sprites.values():
            label = sprite.label
            if label.startswith(SERVER_TICK_LABEL_PREFIX):
                try:
                    value = int(label[len(SERVER_TICK_LABEL_PREFIX):])
                except ValueError:
                    continue
                if value > latest:
                    latest = value
        return latest
