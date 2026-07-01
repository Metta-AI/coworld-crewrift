"""Sprite-v1 binary decoder: fold incoming messages into the scene tables.

Ported from the reference decoder (``notsus`` ``applySpritePacket``). One binary
websocket message may concatenate **many** sub-messages, so we loop an offset over
the whole packet, dispatching each message in turn (design §3.1). All multi-byte
fields are little-endian.

The only pixels decoded are two sprites' alpha channels (both snappy raw-block
compressed): the static ``walkability map`` and the dynamic ``shadow`` vision
overlay (line of sight). Every other sprite's pixels are skipped — crewborg reads
structured state from labels and coordinates, not vision.
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
    (``docs/reference/crewrift-protocol.md`` §Error Handling); the bridge lets this propagate.
    """


def _u16(buf: bytes, offset: int) -> int:
    return int.from_bytes(buf[offset : offset + 2], "little")


def _i16(buf: bytes, offset: int) -> int:
    return int.from_bytes(buf[offset : offset + 2], "little", signed=True)


def _u32(buf: bytes, offset: int) -> int:
    return int.from_bytes(buf[offset : offset + 4], "little")


def apply_message(scene: SceneState, message: bytes) -> None:
    """Apply every sub-message in ``message`` to ``scene`` in order.

    Raises :class:`SpriteProtocolError` on malformed input.
    """

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
    if offset + 2 > len(message):
        raise SpriteProtocolError("truncated delete-object")
    object_id = _u16(message, offset)
    offset += 2
    scene.objects.pop(object_id, None)  # deleting an unknown id is a no-op
    if object_id == MAP_OBJECT_ID:
        scene.camera_ready = False
    return offset


def _apply_define_layer(scene: SceneState, message: bytes, offset: int) -> int:
    if offset + 3 > len(message):
        raise SpriteProtocolError("truncated define-layer")
    layer_id = message[offset]
    scene.layers[layer_id] = LayerDef(layer_type=message[offset + 1], flags=message[offset + 2])
    offset += 3
    return offset
