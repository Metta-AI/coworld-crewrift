"""Sprite-v1 decoder tests against synthesized message sequences (design §3)."""

from __future__ import annotations

import numpy as np

from crewborg.coworld.scene import SceneState
from crewborg.tests import sprite_wire as w


def test_one_message_carries_many_submessages() -> None:
    scene = SceneState()
    # An init-burst-style message: clear, layer, viewport, two sprites, one object.
    scene.apply(
        w.clear_objects()
        + w.define_layer(0, 0x00, 0x01)
        + w.set_viewport(0, 128, 128)
        + w.define_sprite(1, 1235, 659, "map")
        + w.define_sprite(1042, 10, 10, "player red right")
        + w.define_object(1042, 30, 40, 5, 0, 1042)
    )
    assert set(scene.sprites) == {1, 1042}
    assert scene.sprites[1042].label == "player red right"
    assert scene.objects[1042].x == 30 and scene.objects[1042].y == 40
    assert 0 in scene.layers


def test_camera_recovered_from_map_object() -> None:
    scene = SceneState()
    assert not scene.camera_ready
    scene.apply(w.define_sprite(1, 1235, 659, "map") + w.define_object(1, -100, -250, 0, 0, 1))
    assert scene.camera_ready
    assert (scene.camera_x, scene.camera_y) == (100, 250)


def test_delete_and_clear_drop_objects_and_camera() -> None:
    scene = SceneState()
    scene.apply(w.define_sprite(1, 4, 4, "map") + w.define_object(1, -10, -20, 0, 0, 1))
    scene.apply(w.define_object(1042, 1, 2, 0, 0, 1))
    assert scene.camera_ready and 1042 in scene.objects

    scene.apply(w.delete_object(1))  # deleting the map object clears the camera
    assert not scene.camera_ready

    scene.apply(w.clear_objects())
    assert scene.objects == {} and not scene.camera_ready
    # Clear keeps sprite defs.
    assert 1 in scene.sprites


def test_walkability_alpha_decoded_to_bool_grid() -> None:
    scene = SceneState()
    mask = [[True, False, True], [False, True, False]]
    scene.apply(w.walkability_sprite(7, mask))
    assert scene.walkability is not None
    assert scene.walkability_width == 3 and scene.walkability_height == 2
    np.testing.assert_array_equal(scene.walkability, np.array(mask))
