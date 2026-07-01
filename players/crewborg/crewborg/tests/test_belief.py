"""Belief-folding tests: phase machine + self-role derivation (design §5)."""

from __future__ import annotations

from crewborg.perception.entities import ResolvedScene
from crewborg.types import Belief, Percept, update_belief


def _fold(belief: Belief, tick: int, **resolved_fields) -> None:
    resolved = ResolvedScene(tick=tick, camera_ready=True, camera_x=0, camera_y=0, **resolved_fields)
    update_belief(belief, Percept(tick=tick, messages_applied=tick, resolved=resolved))


def test_self_color_learned_from_the_camera_center_sprite() -> None:
    # The player at self_world (the centered self-sprite) is us; nearby others aren't.
    from crewborg.perception.entities import VisiblePlayer

    belief = Belief()
    _fold(
        belief, 1, self_world_x=60, self_world_y=66,
        visible_players=(
            VisiblePlayer(object_id=1000, color="red", facing="left", world_x=60, world_y=66),  # us
            VisiblePlayer(object_id=1001, color="blue", facing="left", world_x=120, world_y=120),
        ),
    )
    assert belief.self_color == "red"


def test_self_color_learned_from_the_voting_marker() -> None:
    from crewborg.perception.entities import VotingState

    belief = Belief()
    _fold(belief, 1, voting=VotingState(self_marker_color="green"))
    assert belief.self_color == "green"


def test_phase_transitions_role_reveal_into_playing() -> None:
    belief = Belief()

    _fold(belief, 1, phase_texts=frozenset({"IMPS"}))
    assert belief.phase == "RoleReveal"

    # Reveal text clears; an ordinary playing scene (task counter, no meeting)
    # must advance the machine to Playing rather than sticking at RoleReveal.
    _fold(belief, 2, crew_tasks_remaining=5)
    assert belief.phase == "Playing"
    assert belief.phase_start_tick == 2


def test_crew_role_is_latched_positively_from_the_crewmate_reveal() -> None:
    # Crew is a positively-detected role, exactly like imposter: the "CREWMATE" reveal
    # text sets it. With no reveal seen, the role stays None ("not yet known") — we do
    # NOT default to crew from the absence of a HUD marker.
    unknown = Belief()
    _fold(unknown, 1, crew_tasks_remaining=5)  # a plain playing scene, no reveal
    assert unknown.phase == "Playing"
    assert unknown.self_role is None

    crew = Belief()
    _fold(crew, 1, phase_texts=frozenset({"CREWMATE"}))
    assert crew.phase == "RoleReveal"
    assert crew.self_role == "crewmate"


def test_hud_kill_icon_sets_only_kill_ready_not_role() -> None:
    belief = Belief()
    # The HUD kill icon reports kill-ready state; it no longer sets role.
    _fold(belief, 1, crew_tasks_remaining=5, self_kill_ready=True)
    assert belief.self_role is None
    assert belief.self_kill_ready is True


def test_just_killed_recorded_on_kill_ready_to_cooldown_edge() -> None:
    belief = Belief()
    _fold(belief, 1, phase_texts=frozenset({"IMPS"}))  # latch imposter from the reveal
    assert belief.self_role == "imposter"
    # Enter play, kill ready.
    _fold(belief, 5, self_kill_ready=True, crew_tasks_remaining=3)
    assert belief.last_kill_tick is None
    # Kill ready → cooldown during continuous Playing: we just killed someone.
    _fold(belief, 6, self_kill_ready=False, crew_tasks_remaining=3)
    assert belief.last_kill_tick == 6
    # Staying on cooldown does not re-record.
    _fold(belief, 7, self_kill_ready=False, crew_tasks_remaining=3)
    assert belief.last_kill_tick == 6


def test_kill_ready_since_tick_tracks_the_cooldown_to_ready_edge() -> None:
    belief = Belief()
    _fold(belief, 1, phase_texts=frozenset({"IMPS"}))  # latch imposter from the reveal
    # Cooldown: no ready-since stamp.
    _fold(belief, 5, self_kill_ready=False, crew_tasks_remaining=3)
    assert belief.kill_ready_since_tick is None
    # Cooldown → ready: stamp the tick we became able to kill.
    _fold(belief, 6, self_kill_ready=True, crew_tasks_remaining=3)
    assert belief.kill_ready_since_tick == 6
    # Staying ready does not re-stamp (urgency keeps climbing).
    _fold(belief, 9, self_kill_ready=True, crew_tasks_remaining=3)
    assert belief.kill_ready_since_tick == 6
    # Killing (ready → cooldown) clears it.
    _fold(belief, 10, self_kill_ready=False, crew_tasks_remaining=3)
    assert belief.kill_ready_since_tick is None
    assert belief.last_kill_tick == 10


def test_roster_accumulates_a_sighting_trail() -> None:
    from crewborg.perception.entities import VisiblePlayer

    belief = Belief()
    _fold(belief, 1, crew_tasks_remaining=5, visible_players=(VisiblePlayer(object_id=1001, color="green", facing="left", world_x=10, world_y=10),))
    _fold(belief, 2, crew_tasks_remaining=5, visible_players=(VisiblePlayer(object_id=1001, color="green", facing="right", world_x=14, world_y=12),))

    entry = belief.roster["green"]
    # Last-known fix is the freshest sighting; history is the ordered trail.
    assert (entry.world_x, entry.world_y, entry.last_seen_tick, entry.facing) == (14, 12, 2, "right")
    assert entry.history == [(1, 10, 10), (2, 14, 12)]
    # A live sighting is proof of life, and the object id is recovered.
    assert entry.life_status == "alive" and entry.object_id == 1001


def test_roster_history_is_bounded() -> None:
    from crewborg.types import ROSTER_HISTORY_MAX, PlayerRecord

    entry = PlayerRecord(object_id=1, color="red", facing="left", world_x=0, world_y=0, last_seen_tick=0)
    for t in range(ROSTER_HISTORY_MAX + 20):
        entry.record(t, t, t, "left", 1)
    assert len(entry.history) == ROSTER_HISTORY_MAX
    assert entry.history[-1] == (ROSTER_HISTORY_MAX + 19,) * 3  # newest kept
    assert entry.history[0][0] == 20  # oldest dropped


def test_teammates_recorded_from_imps_role_reveal() -> None:
    belief = Belief()
    resolved = ResolvedScene(
        tick=1, camera_ready=True, camera_x=0, camera_y=0,
        phase_texts=frozenset({"IMPS"}), reveal_player_colors=frozenset({"red", "blue"}),
    )
    update_belief(belief, Percept(tick=1, messages_applied=1, resolved=resolved))
    assert belief.phase == "RoleReveal"
    assert belief.self_role == "imposter"
    assert belief.teammate_colors == {"red", "blue"}


def test_crew_reveal_icons_do_not_falsely_latch_imposter() -> None:
    # REGRESSION (the crew-plays-imposter collapse, 2026-06-30): the CREW role-reveal
    # also renders player icons in the 9500+ range, so reveal_player_colors is non-empty
    # for crew too. Role must therefore come from the interstitial TEXT ("CREWMATE" vs
    # "IMPS"), never from the mere presence of reveal icons — otherwise every crew latches
    # imposter and plays the whole game as one (0 tasks). A crew reveal is crewmate, with
    # no teammates recorded.
    crew = Belief()
    _fold(crew, 1, phase_texts=frozenset({"CREWMATE"}), reveal_player_colors=frozenset({"green"}))
    assert crew.phase == "RoleReveal"
    assert crew.self_role == "crewmate"
    assert crew.teammate_colors == set()

    # And reveal icons with NEITHER text yet (a pre-text frame) latch nothing — we wait.
    early = Belief()
    _fold(early, 1, reveal_player_colors=frozenset({"green"}))
    assert early.self_role is None
    assert early.teammate_colors == set()


def test_no_false_kill_after_a_meeting() -> None:
    from crewborg.perception.entities import VotingState

    belief = Belief()
    _fold(belief, 1, phase_texts=frozenset({"IMPS"}))  # latch imposter from the reveal
    _fold(belief, 2, self_kill_ready=True, crew_tasks_remaining=3)  # Playing, kill ready

    # Meeting: voting active; the HUD kill icon is absent so kill_ready is carried.
    meeting = ResolvedScene(
        tick=3, camera_ready=True, camera_x=0, camera_y=0, voting=VotingState(timer_present=True)
    )
    update_belief(belief, Percept(tick=3, messages_applied=3, resolved=meeting))
    assert belief.phase == "Voting"

    # Back to Playing with cooldown reset by the meeting — NOT a kill.
    _fold(belief, 4, self_kill_ready=False, crew_tasks_remaining=3)
    assert belief.phase == "Playing"
    assert belief.last_kill_tick is None


def test_death_sets_the_alive_flag_and_preserves_role() -> None:
    # The ghost icon is our own death — a STATE (a flag), not a role. Role is kept, so a
    # dead agent still knows whether it was crew or imposter.
    belief = Belief()
    _fold(belief, 1, phase_texts=frozenset({"CREWMATE"}))
    assert belief.self_role == "crewmate" and belief.self_alive is True
    _fold(belief, 5, self_dead=True, crew_tasks_remaining=3)
    assert belief.self_alive is False
    assert belief.self_role == "crewmate"  # role survives death


def test_phase_stays_unknown_before_any_signal() -> None:
    belief = Belief()
    # Camera not yet ready and no signals: phase remains unknown, role unset.
    resolved = ResolvedScene(tick=1, camera_ready=False, camera_x=0, camera_y=0)
    update_belief(belief, Percept(tick=1, messages_applied=1, resolved=resolved))
    assert belief.phase == "unknown"
    assert belief.self_role is None


# --- perception tape (design §5.1) ------------------------------------------


def test_tape_records_camera_ready_frames_with_viewport_and_entities() -> None:
    from crewborg.perception.entities import VisibleBody, VisiblePlayer

    belief = Belief()
    resolved = ResolvedScene(
        tick=3, camera_ready=True, camera_x=40, camera_y=70,
        visible_players=(VisiblePlayer(object_id=1001, color="green", facing="left", world_x=50, world_y=80),),
        visible_bodies=(VisibleBody(object_id=2002, color="red", world_x=44, world_y=72),),
    )
    update_belief(belief, Percept(tick=3, messages_applied=3, resolved=resolved))

    assert len(belief.recent_frames) == 1
    frame = belief.recent_frames[-1]
    assert (frame.tick, frame.camera_x, frame.camera_y) == (3, 40, 70)
    assert frame.players == {"green": (50, 80)} and frame.bodies == {"red": (44, 72)}


def test_tape_frame_carries_the_visibility_mask() -> None:
    import numpy as np

    belief = Belief()
    mask = np.ones((4, 4), dtype=bool)
    resolved = ResolvedScene(tick=2, camera_ready=True, camera_x=0, camera_y=0)
    update_belief(belief, Percept(tick=2, messages_applied=2, resolved=resolved, visible_mask=mask))
    assert belief.recent_frames[-1].visible_mask is mask  # held by reference


def test_tape_skips_frames_without_a_camera() -> None:
    belief = Belief()
    resolved = ResolvedScene(tick=1, camera_ready=False, camera_x=0, camera_y=0)
    update_belief(belief, Percept(tick=1, messages_applied=1, resolved=resolved))
    assert belief.recent_frames == []  # nothing to anchor a transition on


def test_tape_is_bounded() -> None:
    from crewborg.types import RECENT_FRAMES_MAX

    belief = Belief()
    for tick in range(RECENT_FRAMES_MAX + 10):
        _fold(belief, tick)
    assert len(belief.recent_frames) == RECENT_FRAMES_MAX
    assert belief.recent_frames[-1].tick == RECENT_FRAMES_MAX + 9  # newest kept
    assert belief.recent_frames[0].tick == 10  # oldest dropped


# --- life-status linkage (design §5) ----------------------------------------


def test_body_sighting_connects_last_seen_alive_to_the_death() -> None:
    from crewborg.perception.entities import VisibleBody, VisiblePlayer

    belief = Belief()
    # Seen alive at (10, 10) on tick 1...
    _fold(belief, 1, visible_players=(VisiblePlayer(object_id=1003, color="red", facing="left", world_x=10, world_y=10),))
    # ...then we find red's body across the map on tick 9.
    _fold(belief, 9, visible_bodies=(VisibleBody(object_id=2003, color="red", world_x=300, world_y=80),))

    red = belief.roster["red"]
    assert red.life_status == "dead"
    assert red.death_source == "body" and red.death_seen_tick == 9
    assert red.body_xy == (300, 80)
    # The last-seen-alive fix is preserved (not overwritten by the body position).
    assert (red.world_x, red.world_y, red.last_seen_tick) == (10, 10, 1)


def test_meeting_clears_in_world_bodies_but_keeps_the_death() -> None:
    from crewborg.perception.entities import VisibleBody, VotingState

    belief = Belief()
    _fold(belief, 1, visible_bodies=(VisibleBody(object_id=2003, color="red", world_x=10, world_y=10),))
    assert belief.bodies and "red" in belief.roster and belief.roster["red"].life_status == "dead"

    # A meeting opens: the server removes bodies, so we drop our body beliefs...
    meeting = ResolvedScene(tick=2, camera_ready=True, camera_x=0, camera_y=0, voting=VotingState(timer_present=True))
    update_belief(belief, Percept(tick=2, messages_applied=2, resolved=meeting))
    assert belief.phase == "Voting"
    assert belief.bodies == {} and belief.visible_body_ids == set()
    # ...but the death stays recorded on the roster.
    assert belief.roster["red"].life_status == "dead"


def test_census_records_alive_and_dead_players_by_color() -> None:
    from crewborg.perception.entities import CensusEntry

    belief = Belief()
    _fold(
        belief, 4,
        census=(CensusEntry(color="blue", alive=True), CensusEntry(color="green", alive=False)),
    )
    assert belief.roster["blue"].life_status == "alive"
    assert belief.roster["green"].life_status == "dead"
    assert belief.roster["green"].death_source == "census"
    # The census is authoritative for the player count, even for never-seen players.
    assert belief.total_player_count == 2


def test_ejection_marks_the_voted_out_player_dead() -> None:
    belief = Belief()
    _fold(belief, 7, ejected_color="white")
    assert belief.roster["white"].life_status == "dead"
    assert belief.roster["white"].death_source == "ejection"


def test_chat_log_accumulates_dedups_and_resets_each_meeting() -> None:
    from crewborg.perception.entities import ChatLine, VotingState

    belief = Belief()
    voting = VotingState(timer_present=True)
    # First meeting: one line, re-rendered next tick (must not duplicate), then a new line.
    _fold(belief, 1, voting=voting, chat_lines=(ChatLine(speaker_color="red", text="i was in nav"),))
    _fold(belief, 2, voting=voting, chat_lines=(ChatLine(speaker_color="red", text="i was in nav"),))
    _fold(
        belief, 3, voting=voting,
        chat_lines=(
            ChatLine(speaker_color="red", text="i was in nav"),
            ChatLine(speaker_color="blue", text="sus"),
        ),
    )
    assert [(e.speaker_color, e.text) for e in belief.chat_log] == [("red", "i was in nav"), ("blue", "sus")]
    assert belief.chat_log[0].tick == 1 and belief.chat_log[1].tick == 3

    # Back to Playing, then a NEW meeting clears the previous transcript.
    _fold(belief, 10, crew_tasks_remaining=3)
    _fold(belief, 11, voting=voting, chat_lines=(ChatLine(speaker_color="green", text="fresh"),))
    assert [(e.speaker_color, e.text) for e in belief.chat_log] == [("green", "fresh")]
