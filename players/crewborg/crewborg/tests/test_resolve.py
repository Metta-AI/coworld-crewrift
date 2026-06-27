"""Object-resolution tests: labels + id ranges -> entities (design §4)."""

from __future__ import annotations

from crewborg.coworld.scene import SceneState
from crewborg.perception.resolve import resolve_scene
from crewborg.tests import sprite_wire as w


def _scene_with_camera() -> SceneState:
    scene = SceneState()
    # Map object at (-1000, -500) => camera (1000, 500).
    scene.apply(w.define_sprite(1, 1235, 659, "map") + w.define_object(1, -1000, -500, 0, 0, 1))
    return scene


def test_players_and_bodies_resolve_with_world_coords() -> None:
    scene = _scene_with_camera()
    scene.apply(
        w.define_sprite(1042, 10, 10, "player light blue right")
        + w.define_object(1042, 30, 40, 5, 0, 1042)  # world (1030, 540)
        + w.define_sprite(2003, 10, 10, "body green")
        + w.define_object(2003, -5, 8, 5, 0, 2003)  # world (995, 508)
    )
    resolved = resolve_scene(scene, tick=7)

    assert resolved.camera_ready
    assert len(resolved.visible_players) == 1
    player = resolved.visible_players[0]
    assert (player.color, player.facing) == ("light blue", "right")
    # Draw pos (1030, 540) + collision offset (3, 9) = the server's collision point.
    assert (player.world_x, player.world_y) == (1033, 549)

    assert len(resolved.visible_bodies) == 1
    body = resolved.visible_bodies[0]
    assert body.color == "green" and (body.world_x, body.world_y) == (998, 517)


def test_task_bubble_and_arrow_distinguished() -> None:
    scene = _scene_with_camera()
    scene.apply(
        w.define_sprite(500, 8, 8, "task bubble")
        + w.define_object(3002, 12, 12, 1, 0, 500)  # task index 2, on-screen bubble
        + w.define_sprite(501, 1, 1, "task arrow")
        + w.define_object(7005, 0, 64, 1, 0, 501)  # task index 5, off-screen arrow
    )
    resolved = resolve_scene(scene, tick=1)
    by_index = {t.task_index: t for t in resolved.task_signals}

    assert by_index[2].kind == "bubble" and by_index[2].world == (1012, 512)
    assert by_index[5].kind == "arrow" and by_index[5].world is None
    assert by_index[5].screen == (0, 64)


def test_skip_vote_dots_decode_as_skip_not_a_player_target() -> None:
    scene = SceneState()
    scene.apply(
        w.define_sprite(921, 4, 4, "vote dot red")
        # Skip vote from voter 2 uses the separate base 10400 + voter.
        + w.define_object(10402, 1, 1, 9, 0, 921)
        # A normal vote: voter 1 -> target 0 at 10100 + 0*16 + 1 = 10101.
        + w.define_object(10101, 1, 1, 9, 0, 921)
    )
    resolved = resolve_scene(scene, tick=1)
    by_voter = {d.voter: d for d in resolved.voting.dots}

    assert by_voter[2].is_skip and by_voter[2].target == -2
    assert not by_voter[1].is_skip and by_voter[1].target == 0


def test_candidate_grid_with_cursor_resolves_slots_and_cursor_position() -> None:
    scene = SceneState()
    scene.apply(
        # Candidate grid: slots 0/1 alive, slot 2 dead. (VOTE_ICON_OBJECT_BASE = 9300.)
        w.define_sprite(840, 8, 8, "player red right")
        + w.define_object(9300, 10, 20, 9, 0, 840)
        + w.define_sprite(841, 8, 8, "player blue right")
        + w.define_object(9301, 30, 20, 9, 0, 841)
        + w.define_sprite(842, 8, 8, "body green")
        + w.define_object(9302, 50, 20, 9, 0, 842)
        # The cursor sits on slot 1 (drawn at that cell's position, y offset by 1).
        + w.define_sprite(843, 8, 8, "vote cursor")
        + w.define_object(700, 30, 19, 9, 0, 843)
    )
    resolved = resolve_scene(scene, tick=1)

    by_slot = {c.slot: (c.color, c.alive) for c in resolved.voting.candidates}
    assert by_slot == {0: ("red", True), 1: ("blue", True), 2: ("green", False)}
    assert resolved.voting.cursor_present and resolved.voting.cursor_slot == 1
