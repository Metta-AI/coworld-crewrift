"""Debug-sprite overlay encoder tests (engine PR #67).

The encoder is the mirror of the perception decoder, so round-trip assertions go
through :func:`...perception.decoder.apply_message` onto a fresh ``SceneState``:
what we encode must decode back to the same sprites/objects/coords/labels. Pixels
are asserted via raw-block ``cramjam.snappy.decompress_raw`` directly (the decoder only
decodes pixels for the walkability/shadow labels; a generic label just records
the ``SpriteDef`` with width/height/label, which is what we assert here).
"""

from __future__ import annotations

import cramjam

from players.crewrift.crewborg.coworld.scene import SceneState
from players.crewrift.crewborg.debug_overlay import (
    DEBUG_SPRITE_HEADER,
    OBJECT_BASE,
    SPRITE_GOAL_DOT,
    SPRITE_ROUTE_DOT,
    SPRITE_SELF_DOT,
    add_object,
    add_sprite,
    build_overlay,
    encode_debug_sprites,
)
from players.crewrift.crewborg.types import ActionState, Belief


def test_add_sprite_and_object_round_trip_through_decoder() -> None:
    rgba = bytes([10, 20, 30, 255]) * (3 * 3)  # 3x3 opaque
    packet = bytearray()
    add_sprite(packet, 1, 3, 3, rgba, "debug route")
    add_object(packet, 16, x=120, y=-40, z=2, layer=0, sprite_id=1)

    scene = SceneState()
    scene.apply(bytes(packet))

    assert scene.sprites[1].width == 3 and scene.sprites[1].height == 3
    assert scene.sprites[1].label == "debug route"
    obj = scene.objects[16]
    assert (obj.x, obj.y, obj.z) == (120, -40, 2)  # i16 world coords, incl. negative
    assert obj.sprite_id == 1


def test_sprite_pixels_round_trip_via_raw_snappy() -> None:
    rgba = bytes([0, 220, 255, 255]) * (3 * 3)
    packet = bytearray()
    add_sprite(packet, SPRITE_ROUTE_DOT, 3, 3, rgba, "debug route")

    # Decode the packet by hand to lift out the compressed pixel blob: skip the
    # 0x01 type + u16 id/w/h + u32 len header, then the compressed bytes.
    raw = bytes(packet)
    assert raw[0] == 0x01
    compressed_len = int.from_bytes(raw[7:11], "little")
    compressed = raw[11 : 11 + compressed_len]
    assert bytes(cramjam.snappy.decompress_raw(compressed)) == rgba


def test_encode_debug_sprites_frames_inner_packet() -> None:
    inner = bytes([0x01, 0x02, 0x03, 0xAB, 0xCD])
    framed = encode_debug_sprites(inner)

    assert framed[0] == DEBUG_SPRITE_HEADER  # 0x86
    assert framed[1:5] == len(inner).to_bytes(4, "little")
    assert framed[5:] == inner


def test_build_overlay_returns_none_without_route_or_goal() -> None:
    belief = Belief()
    action_state = ActionState()
    assert build_overlay(belief, action_state) is None


def test_build_overlay_places_route_goal_and_self_objects() -> None:
    belief = Belief(self_world_x=50, self_world_y=60)
    action_state = ActionState(
        route=[(10, 10), (20, 30), (40, 55)],
        route_goal=(80, 90),
    )
    overlay = build_overlay(belief, action_state)
    assert overlay is not None

    scene = SceneState()
    scene.apply(overlay)

    # Three sprite defs (route / goal / self), all defined once.
    assert {SPRITE_ROUTE_DOT, SPRITE_GOAL_DOT, SPRITE_SELF_DOT} <= set(scene.sprites)

    # One object per waypoint + goal + self = 3 + 1 + 1.
    assert len(scene.objects) == 5

    waypoint_objects = [
        scene.objects[OBJECT_BASE + i] for i in range(len(action_state.route))
    ]
    assert [(o.x, o.y) for o in waypoint_objects] == [(10, 10), (20, 30), (40, 55)]
    assert all(o.sprite_id == SPRITE_ROUTE_DOT for o in waypoint_objects)

    goal_object = scene.objects[OBJECT_BASE + len(action_state.route)]
    assert (goal_object.x, goal_object.y) == (80, 90)
    assert goal_object.sprite_id == SPRITE_GOAL_DOT

    self_object = scene.objects[OBJECT_BASE + len(action_state.route) + 1]
    assert (self_object.x, self_object.y) == (50, 60)
    assert self_object.sprite_id == SPRITE_SELF_DOT


def test_build_overlay_clears_objects_so_shrinking_route_leaves_no_stale_dots() -> None:
    belief = Belief()
    long_route = ActionState(route=[(i, i) for i in range(5)], route_goal=(99, 99))
    short_route = ActionState(route=[(1, 1)], route_goal=(99, 99))

    scene = SceneState()
    scene.apply(build_overlay(belief, long_route))  # type: ignore[arg-type]
    assert len(scene.objects) == 5 + 1  # 5 waypoints + goal (no self set)

    scene.apply(build_overlay(belief, short_route))  # type: ignore[arg-type]
    # The clear-objects in the second packet drops the prior waypoints first.
    assert len(scene.objects) == 1 + 1
