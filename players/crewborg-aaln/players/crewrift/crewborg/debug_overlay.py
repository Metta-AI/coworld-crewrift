"""Sprite-v1 ENCODER for debug-sprite replay overlays (engine PR #67).

The mirror image of :mod:`players.crewrift.crewborg.perception.decoder`: instead
of folding an incoming server->client sprite packet into the scene, this builds
an *outgoing* one and wraps it in the engine's ``SpriteClientDebugSprite``
(``0x86``) frame. The engine records that frame into the ``.bitreplay`` and, when
a replay viewer toggles the new "D" control, renders our sprites/objects over
crewborg's POV — a live picture of what the policy is "thinking" each tick.

This complements the SQLite trace artifact (:mod:`...artifact`, the
``player-artifacts`` skill): the artifact answers "what did belief hold and which
events fired", offline and queryable; this overlay answers "where on the map was
the plan pointing", in the replay viewer, frame by frame. Neither is bundled into
the other — the overlay is a transient wire side-channel, gated off by default.

Wire contract (verified against bitworld ``spriteprotocol.nim`` @ 87724ba and our
decoder, which parses the same message format in the opposite direction):

- The outer debug frame is ``0x86`` + ``u32`` little-endian length N + N bytes of
  an ordinary sprite packet (``encode_debug_sprites``).
- The inner packet reuses the decoder's message types/field layouts exactly
  (``perception.constants``), all multi-byte fields little-endian:
  define-sprite (0x01), define-object (0x02), delete-object (0x03),
  clear-objects (0x04).

Compression: the engine's ``addSprite`` / ``addDebugSpritePacket`` path uses
``supersnappy.compress`` / ``uncompress`` (raw block snappy), same as server→client
sprite definitions — so outgoing pixels use ``cramjam.snappy.compress_raw``.

Id namespace: the engine remaps our sprite/object ids into a debug range
(``id mod 8000``) and forces the viewport layer + a debug z-base, so our ids only
need to be internally consistent and small, and the object ``layer`` field is
ignored (we pass 0). Object x/y are world-pixel coordinates in the same space as
real player/body objects (``belief.self_world_x/y``, route waypoints); the
engine/renderer applies the camera.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import cramjam

from players.crewrift.crewborg.perception.constants import (
    MSG_CLEAR_OBJECTS,
    MSG_DEFINE_OBJECT,
    MSG_DEFINE_SPRITE,
    MSG_DELETE_OBJECT,
)

if TYPE_CHECKING:
    from players.crewrift.crewborg.types import ActionState, Belief

# The outer client->server debug-sprite message byte (bitworld
# ``SpriteClientDebugSprite``); its payload is one inner sprite packet.
DEBUG_SPRITE_HEADER = 0x86

# Internally-consistent small ids (engine takes ``id mod 8000`` into a debug
# namespace). One sprite per overlay role; objects start above the sprite ids.
SPRITE_ROUTE_DOT = 1  # waypoints along the planned nav route
SPRITE_GOAL_DOT = 2  # the route's goal / target point
SPRITE_SELF_DOT = 3  # the agent's own world position
OBJECT_BASE = 16  # first object id; waypoint/goal/self objects count up from here

# Tiny opaque dots — large enough to spot in the 128x128 POV, cheap to encode.
DOT_SIZE = 3
GOAL_SIZE = 5

# RGBA fills (opaque). Route = cyan trail, goal = magenta, self = yellow.
COLOR_ROUTE = (0, 220, 255, 255)
COLOR_GOAL = (255, 0, 200, 255)
COLOR_SELF = (255, 235, 0, 255)

# Cap the per-tick object count so a pathological route can never bloat the frame.
MAX_ROUTE_DOTS = 32


def _u16(value: int) -> bytes:
    return int(value).to_bytes(2, "little")


def _i16(value: int) -> bytes:
    return int(value).to_bytes(2, "little", signed=True)


def _u32(value: int) -> bytes:
    return int(value).to_bytes(4, "little")


def add_sprite(
    packet: bytearray,
    sprite_id: int,
    width: int,
    height: int,
    rgba_pixels: bytes,
    label: str,
) -> None:
    """Append a define-sprite (0x01) for ``width*height`` raw-block snappy RGBA pixels.

    ``rgba_pixels`` must be ``width*height*4`` bytes; the engine's debug reader
    uncompresses raw block (``supersnappy.uncompress``), so we compress raw.
    """

    compressed = bytes(cramjam.snappy.compress_raw(rgba_pixels))
    label_bytes = label.encode("utf-8")
    packet += bytes([MSG_DEFINE_SPRITE])
    packet += _u16(sprite_id)
    packet += _u16(width)
    packet += _u16(height)
    packet += _u32(len(compressed))
    packet += compressed
    packet += _u16(len(label_bytes))
    packet += label_bytes


def add_object(
    packet: bytearray,
    object_id: int,
    x: int,
    y: int,
    z: int,
    layer: int,
    sprite_id: int,
) -> None:
    """Append a define-object (0x02): place ``sprite_id`` at world ``(x, y, z)``.

    ``layer`` is carried for protocol symmetry but ignored for debug objects (the
    engine forces the viewport layer); pass 0.
    """

    packet += bytes([MSG_DEFINE_OBJECT])
    packet += _u16(object_id)
    packet += _i16(x)
    packet += _i16(y)
    packet += _i16(z)
    packet += bytes([layer & 0xFF])
    packet += _u16(sprite_id)


def add_delete_object(packet: bytearray, object_id: int) -> None:
    """Append a delete-object (0x03)."""

    packet += bytes([MSG_DELETE_OBJECT])
    packet += _u16(object_id)


def add_clear_objects(packet: bytearray) -> None:
    """Append a clear-objects (0x04): drop every previously-placed debug object."""

    packet += bytes([MSG_CLEAR_OBJECTS])


def encode_debug_sprites(packet: bytes) -> bytes:
    """Wrap an inner sprite packet in the ``0x86`` + ``u32`` length debug frame.

    The ``blobFromSpriteDebugSprites`` equivalent: the engine reads the length,
    slices off N bytes, and feeds them to its ordinary sprite-packet reader.
    """

    return bytes([DEBUG_SPRITE_HEADER]) + _u32(len(packet)) + packet


def _solid_rgba(size: int, color: tuple[int, int, int, int]) -> bytes:
    return bytes(color) * (size * size)


def _define_dots(packet: bytearray) -> None:
    """Define the three reusable dot sprites once at the head of the packet."""

    add_sprite(packet, SPRITE_ROUTE_DOT, DOT_SIZE, DOT_SIZE, _solid_rgba(DOT_SIZE, COLOR_ROUTE), "debug route")
    add_sprite(packet, SPRITE_GOAL_DOT, GOAL_SIZE, GOAL_SIZE, _solid_rgba(GOAL_SIZE, COLOR_GOAL), "debug goal")
    add_sprite(packet, SPRITE_SELF_DOT, DOT_SIZE, DOT_SIZE, _solid_rgba(DOT_SIZE, COLOR_SELF), "debug self")


def build_overlay(belief: Belief, action_state: ActionState) -> bytes | None:
    """Build the inner sprite packet visualizing crewborg's current plan.

    Draws the live nav plan in world coordinates: a cyan dot at every waypoint in
    ``action_state.route``, a magenta dot at the route goal
    (``action_state.route_goal``), and a yellow dot at the agent's own position
    (``belief.self_world_x/y``) for reference. Returns ``None`` when there is
    nothing to show (no route and no goal) so the bridge can skip the send.

    The packet clears prior debug objects each tick, then redefines the sprites
    and re-places the objects, so a shrinking route never leaves stale dots.
    """

    route = action_state.route
    goal = action_state.route_goal
    if not route and goal is None:
        return None

    packet = bytearray()
    _define_dots(packet)
    add_clear_objects(packet)

    object_id = OBJECT_BASE
    for x, y in route[:MAX_ROUTE_DOTS]:
        add_object(packet, object_id, int(x), int(y), 0, 0, SPRITE_ROUTE_DOT)
        object_id += 1

    if goal is not None:
        add_object(packet, object_id, int(goal[0]), int(goal[1]), 1, 0, SPRITE_GOAL_DOT)
        object_id += 1

    if belief.self_world_x is not None and belief.self_world_y is not None:
        add_object(packet, object_id, int(belief.self_world_x), int(belief.self_world_y), 2, 0, SPRITE_SELF_DOT)

    return bytes(packet)
