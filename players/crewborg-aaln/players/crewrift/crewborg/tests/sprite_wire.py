"""Helpers to build synthetic Sprite-v1 messages for decoder/resolve tests.

These mirror the wire format in ``docs/sprite_v1.md`` (all little-endian) so tests
can assert the decoder against hand-built message sequences rather than pixels.
"""

from __future__ import annotations

import cramjam


def _u16(value: int) -> bytes:
    return int(value).to_bytes(2, "little")


def _i16(value: int) -> bytes:
    return int(value).to_bytes(2, "little", signed=True)


def _u32(value: int) -> bytes:
    return int(value).to_bytes(4, "little")


def define_sprite(sprite_id: int, width: int, height: int, label: str, *, rgba: bytes = b"") -> bytes:
    """Build a define-sprite (0x01). ``rgba`` is raw uncompressed pixels; empty
    means no pixel payload (fine for non-walkability sprites the decoder skips)."""

    compressed = bytes(cramjam.snappy.compress_raw(rgba)) if rgba else b""
    label_bytes = label.encode("utf-8")
    return (
        bytes([0x01])
        + _u16(sprite_id)
        + _u16(width)
        + _u16(height)
        + _u32(len(compressed))
        + compressed
        + _u16(len(label_bytes))
        + label_bytes
    )


def walkability_sprite(sprite_id: int, mask: list[list[bool]]) -> bytes:
    """Build a "walkability map" define-sprite from a 2D walkable bool grid."""

    height = len(mask)
    width = len(mask[0]) if height else 0
    rgba = bytearray()
    for row in mask:
        for walkable in row:
            rgba += bytes([0, 0, 0, 255 if walkable else 0])
    return define_sprite(sprite_id, width, height, "walkability map", rgba=bytes(rgba))


def define_object(object_id: int, x: int, y: int, z: int, layer: int, sprite_id: int) -> bytes:
    """Build a define-object (0x02)."""

    return (
        bytes([0x02])
        + _u16(object_id)
        + _i16(x)
        + _i16(y)
        + _i16(z)
        + bytes([layer])
        + _u16(sprite_id)
    )


def delete_object(object_id: int) -> bytes:
    return bytes([0x03]) + _u16(object_id)


def clear_objects() -> bytes:
    return bytes([0x04])


def set_viewport(layer: int, width: int, height: int) -> bytes:
    return bytes([0x05, layer]) + _u16(width) + _u16(height)


def define_layer(layer: int, layer_type: int, flags: int) -> bytes:
    return bytes([0x06, layer, layer_type, flags])
