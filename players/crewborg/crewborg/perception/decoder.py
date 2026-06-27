"""Sprite-v1 binary decoder: fold incoming messages into the scene tables.

First stage of the perception layer (perception → belief → suspicion → strategy →
modes → action): this is the raw *byte* ingest. It mutates the three retained
scene tables (Layers/Sprites/Objects) and the camera in place; the later
:mod:`.resolve` stage turns those tables into structured entities. No strategy or
belief logic lives here — only protocol parsing.

Ported from the reference decoder (``notsus`` ``applySpritePacket``). One binary
websocket message may concatenate **many** sub-messages, so we loop an offset over
the whole packet, dispatching each message in turn (design §3.1). All multi-byte
fields are little-endian.

The only pixels decoded are two sprites' alpha channels (both snappy raw-block
compressed): the static ``walkability map`` and the dynamic ``shadow`` vision
overlay (line of sight). Every other sprite's pixels are skipped — crewborg reads
structured state from labels and coordinates, not vision.

Collaborators
-------------
Relies on:
  - ``perception.constants`` — message-type bytes (``MSG_*``), the two decoded
    sprite labels (``LABEL_WALKABILITY`` / ``LABEL_SHADOW``), and the world-map
    object/sprite ids (``MAP_OBJECT_ID`` / ``MAP_SPRITE_ID``) that recover the camera.
  - ``perception.tables`` — ``LayerDef`` / ``ObjectState`` / ``SpriteDef``, the
    dataclasses stored in the scene tables.
  - ``cramjam`` (snappy raw decompress) and ``numpy`` (RGBA → alpha-mask reshape).
Used by:
  - ``coworld.scene.SceneState.apply`` — the bridge's sole entry point; it calls
    ``apply_message(self, message)`` for every incoming frame.
Emits / touches: mutates the passed ``SceneState`` in place — ``objects`` /
  ``sprites`` / ``layers`` tables, ``camera_ready`` / ``camera_x`` / ``camera_y``,
  and the ``walkability`` / ``visible_mask`` alpha grids. Raises
  :class:`SpriteProtocolError` on malformed input.

Modifying this file: this is a faithful port of the wire protocol — the byte
layouts and id-range meanings are the contract (verified against the game source;
see ``perception.constants``). Preserve the invariant that every branch advances
``offset`` past exactly the bytes it consumed and returns the new offset, or the
sub-message loop in ``apply_message`` desyncs. Do not add belief/strategy logic
here; only decode bytes into the scene tables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cramjam
import numpy as np

from crewborg.perception.constants import (
    LABEL_SHADOW,
    LABEL_WALKABILITY,
    MAP_OBJECT_ID,
    MAP_SPRITE_ID,
    MSG_CLEAR_OBJECTS,
    MSG_DEFINE_LAYER,
    MSG_DEFINE_OBJECT,
    MSG_DEFINE_SPRITE,
    MSG_DELETE_OBJECT,
    MSG_SET_VIEWPORT,
)
from crewborg.perception.tables import LayerDef, ObjectState, SpriteDef

if TYPE_CHECKING:
    from crewborg.coworld.scene import SceneState


class SpriteProtocolError(ValueError):
    """Raised on a malformed Sprite-v1 message (truncation, unknown type, …).

    The protocol mandates closing the connection on malformed input
    (``docs/sprite_v1.md`` §Error Handling); the bridge lets this propagate.
    """


def _u16(buf: bytes, offset: int) -> int:
    """Read a little-endian unsigned 16-bit int at ``offset``."""
    return int.from_bytes(buf[offset : offset + 2], "little")


def _i16(buf: bytes, offset: int) -> int:
    """Read a little-endian signed 16-bit int at ``offset`` (used for screen coords)."""
    return int.from_bytes(buf[offset : offset + 2], "little", signed=True)


def _u32(buf: bytes, offset: int) -> int:
    """Read a little-endian unsigned 32-bit int at ``offset``."""
    return int.from_bytes(buf[offset : offset + 4], "little")


def apply_message(scene: SceneState, message: bytes) -> None:
    """Apply every sub-message in ``message`` to ``scene`` in order.

    Raises :class:`SpriteProtocolError` on malformed input.
    """

    # Walk the packet one sub-message at a time: read the 1-byte type, dispatch
    # to the per-type decoder, and advance ``offset`` to whatever it consumed.
    offset = 0
    length = len(message)
    while offset < length:
        message_type = message[offset]
        offset += 1
        if message_type == MSG_DEFINE_SPRITE:
            offset = _apply_define_sprite(scene, message, offset)
        elif message_type == MSG_DEFINE_OBJECT:
            offset = _apply_define_object(scene, message, offset)
        elif message_type == MSG_DELETE_OBJECT:
            offset = _apply_delete_object(scene, message, offset)
        elif message_type == MSG_CLEAR_OBJECTS:
            scene.objects.clear()
            scene.camera_ready = False
        elif message_type == MSG_SET_VIEWPORT:
            if offset + 5 > length:
                raise SpriteProtocolError("truncated set-viewport")
            offset += 5
        elif message_type == MSG_DEFINE_LAYER:
            offset = _apply_define_layer(scene, message, offset)
        else:
            raise SpriteProtocolError(f"unknown message type 0x{message_type:02x}")


def _apply_define_sprite(scene: SceneState, message: bytes, offset: int) -> int:
    """Decode one define-sprite message and store the sprite's dimensions + label.

    Wire layout (from ``offset``): u16 sprite_id, u16 width, u16 height, u32
    compressed pixel length, the compressed RGBA payload, u16 label length, then the
    UTF-8 label. Pixels are decoded only for the two retained masks (walkability /
    shadow) and otherwise discarded — the table keeps just size + label. Returns the
    offset just past the label.
    """

    if offset + 10 > len(message):
        raise SpriteProtocolError("truncated define-sprite header")
    sprite_id = _u16(message, offset)
    width = _u16(message, offset + 2)
    height = _u16(message, offset + 4)
    compressed_len = _u32(message, offset + 6)
    offset += 10
    compressed_start = offset
    offset += compressed_len
    if offset + 2 > len(message):
        raise SpriteProtocolError("truncated define-sprite payload")
    label_len = _u16(message, offset)
    offset += 2
    if offset + label_len > len(message):
        raise SpriteProtocolError("truncated define-sprite label")
    label = message[offset : offset + label_len].decode("utf-8", errors="replace")
    offset += label_len

    if label == LABEL_WALKABILITY:
        compressed = message[compressed_start : compressed_start + compressed_len]
        _decode_walkability(scene, width, height, bytes(compressed))
    elif label == LABEL_SHADOW:
        compressed = message[compressed_start : compressed_start + compressed_len]
        _decode_shadow(scene, width, height, bytes(compressed))

    scene.sprites[sprite_id] = SpriteDef(width=width, height=height, label=label)
    return offset


def _decode_walkability(scene: SceneState, width: int, height: int, compressed: bytes) -> None:
    """Decode the static ``walkability map`` sprite into a world-space bool grid.

    Snappy-raw-decompresses the RGBA payload and keeps alpha > 0 as walkable,
    reshaped to ``(height, width)``. Stored on ``scene.walkability`` (+ its
    dimensions) and consumed once by nav-graph construction. Raises
    :class:`SpriteProtocolError` on bad dimensions, a failed decode, or a payload
    whose length is not ``width*height*4``.
    """

    if width <= 0 or height <= 0:
        raise SpriteProtocolError("invalid walkability dimensions")
    try:
        raw = bytes(cramjam.snappy.decompress_raw(compressed))
    except Exception as exc:  # cramjam raises a variety of error types
        raise SpriteProtocolError("walkability snappy decode failed") from exc
    if len(raw) != width * height * 4:
        raise SpriteProtocolError("walkability payload size mismatch")
    # Walkable where alpha > 0; reshape RGBA into (height, width) bool grid.
    alpha = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[:, :, 3]
    scene.walkability = alpha > 0
    scene.walkability_width = width
    scene.walkability_height = height


def _decode_shadow(scene: SceneState, width: int, height: int, compressed: bytes) -> None:
    """Decode the ``shadow`` vision overlay into a screen-space visibility mask.

    The overlay paints occluded pixels opaque and leaves visible pixels transparent
    (sim.nim: ``shadowBuf`` true ⇒ occluded), so ``visible = alpha == 0``. Overwrites
    the prior mask — it is resent on every camera move, so it always matches the
    current camera.
    """

    if width <= 0 or height <= 0:
        raise SpriteProtocolError("invalid shadow dimensions")
    try:
        raw = bytes(cramjam.snappy.decompress_raw(compressed))
    except Exception as exc:  # cramjam raises a variety of error types
        raise SpriteProtocolError("shadow snappy decode failed") from exc
    if len(raw) != width * height * 4:
        raise SpriteProtocolError("shadow payload size mismatch")
    alpha = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)[:, :, 3]
    scene.visible_mask = alpha == 0


def _apply_define_object(scene: SceneState, message: bytes, offset: int) -> int:
    """Decode one define-object message: place/replace an object in the scene table.

    Wire layout (from ``offset``): u16 object_id, i16 x, i16 y, i16 z, u8 layer,
    u16 sprite_id (x/y are camera-relative screen coords). Stores the
    ``ObjectState`` under ``object_id``. The world-map object (``MAP_OBJECT_ID`` on
    ``MAP_SPRITE_ID``) is special-cased to recover the camera: it is drawn at
    ``(-camX, -camY)``, so ``camera = -obj.x/-obj.y`` and ``camera_ready`` flips
    true. Returns the offset just past this message.
    """

    if offset + 11 > len(message):
        raise SpriteProtocolError("truncated define-object")
    object_id = _u16(message, offset)
    obj = ObjectState(
        x=_i16(message, offset + 2),
        y=_i16(message, offset + 4),
        z=_i16(message, offset + 6),
        layer=message[offset + 8],
        sprite_id=_u16(message, offset + 9),
    )
    offset += 11
    scene.objects[object_id] = obj
    if object_id == MAP_OBJECT_ID and obj.sprite_id == MAP_SPRITE_ID:
        scene.camera_ready = True
        scene.camera_x = -obj.x
        scene.camera_y = -obj.y
    return offset


def _apply_delete_object(scene: SceneState, message: bytes, offset: int) -> int:
    """Decode one delete-object message (u16 object_id) and drop it from the table.

    Deleting an unknown id is a no-op. Deleting the world-map object clears
    ``camera_ready`` (the camera is no longer valid). Returns the offset just past
    the id.
    """

    if offset + 2 > len(message):
        raise SpriteProtocolError("truncated delete-object")
    object_id = _u16(message, offset)
    offset += 2
    scene.objects.pop(object_id, None)  # deleting an unknown id is a no-op
    if object_id == MAP_OBJECT_ID:
        scene.camera_ready = False
    return offset


def _apply_define_layer(scene: SceneState, message: bytes, offset: int) -> int:
    """Decode one define-layer message (u8 layer_id, u8 layer_type, u8 flags).

    Stores the ``LayerDef`` under ``layer_id``. Returns the offset just past it.
    """

    if offset + 3 > len(message):
        raise SpriteProtocolError("truncated define-layer")
    layer_id = message[offset]
    scene.layers[layer_id] = LayerDef(layer_type=message[offset + 1], flags=message[offset + 2])
    offset += 3
    return offset
