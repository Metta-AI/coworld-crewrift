"""Object-resolution tests: labels + id ranges -> entities (design §4)."""

from __future__ import annotations

from players.crewrift.crewborg.coworld.scene import SceneState
from players.crewrift.crewborg.perception.resolve import resolve_scene
from players.crewrift.crewborg.tests import sprite_wire as w


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


def test_self_role_from_hud_icons() -> None:
    scene = SceneState()
    scene.apply(w.define_sprite(900, 8, 8, "imposter icon cooldown") + w.define_object(900, 4, 4, 9, 0, 900))
    resolved = resolve_scene(scene, tick=1)
    assert resolved.self_role == "imposter" and resolved.self_kill_ready is False


def test_progress_counter_and_voting_resolved() -> None:
    scene = SceneState()
    scene.apply(
        w.define_sprite(910, 8, 8, "progress bar 45%")
        + w.define_object(910, 1, 1, 9, 0, 910)
        + w.define_sprite(911, 8, 8, "task counter 7")
        + w.define_object(911, 1, 1, 9, 0, 911)
        + w.define_sprite(920, 4, 4, "vote timer")
        + w.define_object(920, 1, 1, 9, 0, 920)
        + w.define_sprite(921, 4, 4, "vote dot red")
        # id 10100 + target(3)*16 + voter(2) = 10150
        + w.define_object(10150, 1, 1, 9, 0, 921)
    )
    resolved = resolve_scene(scene, tick=1)
    assert resolved.active_task_progress_pct == 45
    assert resolved.crew_tasks_remaining == 7
    assert resolved.voting.timer_present and resolved.voting.active
    assert resolved.voting.dots == (resolved.voting.dots[0],)
    dot = resolved.voting.dots[0]
    assert (dot.target, dot.voter) == (3, 2)


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


def test_chat_lines_pair_speaker_icons_to_text_by_screen_y() -> None:
    scene = SceneState()
    scene.apply(
        # A phase text in the shared 9000 range with no icon beside it: must NOT be
        # mistaken for chat.
        w.define_sprite(800, 8, 8, "SKIP")
        + w.define_object(9001, 60, 8, 9, 0, 800)
        # Two stacked chat messages: text in [9000,9200), speaker icon in [9200,9300).
        + w.define_sprite(810, 8, 8, "red did electrical")
        + w.define_object(9002, 12, 50, 9, 0, 810)
        + w.define_sprite(811, 8, 8, "player blue right")
        + w.define_object(9200, 1, 50, 9, 0, 811)  # blue speaks at y=50
        + w.define_sprite(812, 8, 8, "where was green")
        + w.define_object(9003, 12, 70, 9, 0, 812)
        + w.define_sprite(813, 8, 8, "player pink right")
        + w.define_object(9201, 1, 70, 9, 0, 813)  # pink speaks at y=70
    )
    resolved = resolve_scene(scene, tick=1)

    by_speaker = {line.speaker_color: line.text for line in resolved.chat_lines}
    assert by_speaker == {"blue": "red did electrical", "pink": "where was green"}
    assert "SKIP" in resolved.phase_texts  # the phase text still resolves as such


def test_candidate_grid_resolves_an_alive_dead_census_by_color() -> None:
    scene = SceneState()
    scene.apply(
        # Candidate-grid cells live at VOTE_ICON_OBJECT_BASE (9300) + seq index.
        w.define_sprite(820, 8, 8, "player orange right")
        + w.define_object(9300, 10, 20, 9, 0, 820)  # orange: alive
        + w.define_sprite(821, 8, 8, "body white")
        + w.define_object(9301, 30, 20, 9, 0, 821)  # white: dead
    )
    resolved = resolve_scene(scene, tick=1)

    census = {entry.color: entry.alive for entry in resolved.census}
    assert census == {"orange": True, "white": False}


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


def test_vote_result_ejected_color_resolves() -> None:
    scene = SceneState()
    scene.apply(
        w.define_sprite(830, 8, 8, "player lime right")
        + w.define_object(9600, 60, 60, 9, 0, 830)  # RESULT_ICON_OBJECT_ID
    )
    resolved = resolve_scene(scene, tick=1)
    assert resolved.ejected_color == "lime"


# --- upstream 2026-06-10 additions: tick marker, game info, meeting call, ----
# --- game-over roles. All tolerant: absent labels resolve to None/empty. -----


def test_server_tick_marker_resolves() -> None:
    scene = SceneState()
    scene.apply(
        w.define_sprite(5016, 1, 1, "tick 4807") + w.define_object(5016, 0, 0, -32768, 0, 5016)
    )
    assert resolve_scene(scene, tick=1).server_tick == 4807


def test_server_tick_marker_label_updates_each_tick() -> None:
    scene = SceneState()
    scene.apply(w.define_sprite(5016, 1, 1, "tick 10") + w.define_object(5016, 0, 0, 0, 0, 5016))
    assert resolve_scene(scene, tick=1).server_tick == 10
    # The server redefines the same sprite id with a new label every tick.
    scene.apply(w.define_sprite(5016, 1, 1, "tick 11") + w.define_object(5016, 0, 0, 0, 0, 5016))
    assert resolve_scene(scene, tick=2).server_tick == 11


def test_malformed_tick_label_is_ignored() -> None:
    scene = SceneState()
    scene.apply(w.define_sprite(5016, 1, 1, "tick ") + w.define_object(5016, 0, 0, 0, 0, 5016))
    assert resolve_scene(scene, tick=1).server_tick is None
    scene.apply(w.define_sprite(5017, 1, 1, "tick 12a") + w.define_object(5017, 0, 0, 0, 0, 5017))
    assert resolve_scene(scene, tick=2).server_tick is None


def test_game_info_interstitial_resolves_settings() -> None:
    scene = SceneState()
    scene.apply(
        w.define_sprite(9000, 40, 6, "GAME INFO")
        + w.define_object(9000, 44, 49, 50, 0, 9000)
        + w.define_sprite(9001, 60, 6, "KILL COOLDOWN 500T")
        + w.define_object(9001, 34, 63, 50, 0, 9001)
        + w.define_sprite(9002, 50, 6, "TASKS 8 EACH")
        + w.define_object(9002, 40, 77, 50, 0, 9002)
        + w.define_sprite(9003, 56, 6, "VOTE TIMER 1200T")
        + w.define_object(9003, 36, 91, 50, 0, 9003)
        + w.define_sprite(9004, 60, 6, "GAME TIMER 10000T")
        + w.define_object(9004, 34, 105, 50, 0, 9004)
    )
    resolved = resolve_scene(scene, tick=1)
    assert "GAME INFO" in resolved.phase_texts
    info = resolved.game_info
    assert info is not None
    assert info.kill_cooldown_ticks == 500
    assert info.tasks_per_player == 8
    assert info.vote_timer_ticks == 1200
    assert info.max_ticks == 10000


def test_game_info_with_no_game_timer_resolves_none_max_ticks() -> None:
    scene = SceneState()
    scene.apply(
        w.define_sprite(9000, 40, 6, "GAME INFO")
        + w.define_object(9000, 44, 49, 50, 0, 9000)
        + w.define_sprite(9001, 60, 6, "GAME TIMER NONE")
        + w.define_object(9001, 34, 105, 50, 0, 9001)
    )
    info = resolve_scene(scene, tick=1).game_info
    assert info is not None
    assert info.max_ticks is None
    assert info.kill_cooldown_ticks is None  # absent line ⇒ None, never a crash


def test_game_info_absent_resolves_none() -> None:
    scene = _scene_with_camera()
    assert resolve_scene(scene, tick=1).game_info is None


def test_meeting_call_report_resolves_caller_trigger_and_body() -> None:
    scene = SceneState()
    scene.apply(
        # Caller icon (object 9800) + reported body icon (object 9801).
        w.define_sprite(860, 8, 8, "player light blue right")
        + w.define_object(9800, 29, 73, 40, 0, 860)
        + w.define_sprite(861, 8, 8, "body green")
        + w.define_object(9801, 79, 73, 40, 0, 861)
        # The interstitial text lines.
        + w.define_sprite(9000, 60, 6, "Light blue reported")
        + w.define_object(9000, 30, 36, 50, 0, 9000)
        + w.define_sprite(9001, 50, 6, "Green's body")
        + w.define_object(9001, 36, 44, 50, 0, 9001)
    )
    resolved = resolve_scene(scene, tick=1)
    call = resolved.meeting_call
    assert call is not None
    assert call.caller_color == "light blue"
    assert call.trigger == "report"
    assert call.body_color == "green"


def test_meeting_call_button_resolves_trigger() -> None:
    scene = SceneState()
    scene.apply(
        w.define_sprite(860, 8, 8, "player red right")
        + w.define_object(9800, 29, 73, 40, 0, 860)
        + w.define_sprite(5017, 12, 12, "meeting button")
        + w.define_object(9801, 81, 75, 40, 0, 5017)
        + w.define_sprite(9000, 40, 6, "Red pressed")
        + w.define_object(9000, 40, 36, 50, 0, 9000)
        + w.define_sprite(9001, 40, 6, "the button")
        + w.define_object(9001, 42, 44, 50, 0, 9001)
    )
    call = resolve_scene(scene, tick=1).meeting_call
    assert call is not None
    assert call.caller_color == "red"
    assert call.trigger == "button"
    assert call.body_color is None


def test_meeting_call_text_fallback_when_caller_left() -> None:
    # "Someone reported" + "a body": no caller icon exists, only the text.
    scene = SceneState()
    scene.apply(
        w.define_sprite(9000, 60, 6, "Someone reported")
        + w.define_object(9000, 32, 36, 50, 0, 9000)
        + w.define_sprite(9001, 30, 6, "a body")
        + w.define_object(9001, 48, 44, 50, 0, 9001)
    )
    call = resolve_scene(scene, tick=1).meeting_call
    assert call is not None
    assert call.caller_color is None  # "Someone" is not a player color
    assert call.trigger == "report"


def test_meeting_call_text_does_not_misfire_during_voting_chat() -> None:
    # A literal chat line ending in " reported" during Voting (vote UI + a chat
    # icon present) must NOT read as a meeting-call interstitial.
    scene = SceneState()
    scene.apply(
        w.define_sprite(9050, 60, 6, "red reported")  # chat text
        + w.define_object(9000, 6, 30, 50, 0, 9050)
        + w.define_sprite(9051, 8, 8, "player blue right")  # chat speaker icon
        + w.define_object(9200, 2, 30, 50, 0, 9051)
        + w.define_sprite(920, 4, 4, "vote timer")
        + w.define_object(920, 1, 1, 9, 0, 920)
    )
    resolved = resolve_scene(scene, tick=1)
    assert resolved.meeting_call is None
    assert resolved.voting.active


def test_game_over_roles_pair_icons_with_imp_crew_texts() -> None:
    scene = SceneState()
    scene.apply(
        # Row 0 (left column): red is CREW. Icon at x=3,y=18; text at x=19,y=20.
        w.define_sprite(870, 8, 8, "player red right")
        + w.define_object(9700, 3, 18, 40, 0, 870)
        + w.define_sprite(9001, 20, 6, "CREW")
        + w.define_object(9001, 19, 20, 50, 0, 9001)
        # Row 1: green is IMP. Icon at x=3,y=32; text at x=19,y=34.
        + w.define_sprite(871, 8, 8, "player green right")
        + w.define_object(9701, 3, 32, 40, 0, 871)
        + w.define_sprite(9002, 16, 6, "IMP")
        + w.define_object(9002, 19, 34, 50, 0, 9002)
        # The outcome title.
        + w.define_sprite(9000, 40, 6, "CREW WINS")
        + w.define_object(9000, 40, 2, 50, 0, 9000)
    )
    resolved = resolve_scene(scene, tick=1)
    assert "CREW WINS" in resolved.phase_texts
    assert resolved.game_over_roles == {"red": "crewmate", "green": "imposter"}


def test_new_labels_absent_resolve_to_defaults() -> None:
    """Older servers (no tick marker / interstitials): everything stays None/empty."""

    scene = _scene_with_camera()
    resolved = resolve_scene(scene, tick=1)
    assert resolved.server_tick is None
    assert resolved.game_info is None
    assert resolved.meeting_call is None
    assert resolved.game_over_roles == {}
