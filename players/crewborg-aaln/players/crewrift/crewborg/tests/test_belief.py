"""Belief-folding tests: phase machine + self-role derivation (design §5)."""

from __future__ import annotations

from players.crewrift.crewborg.perception.entities import ResolvedScene
from players.crewrift.crewborg.types import Belief, Percept, update_belief


def _fold(belief: Belief, tick: int, **resolved_fields) -> None:
    resolved = ResolvedScene(tick=tick, camera_ready=True, camera_x=0, camera_y=0, **resolved_fields)
    update_belief(belief, Percept(tick=tick, messages_applied=tick, resolved=resolved))


def test_phase_transitions_role_reveal_into_playing() -> None:
    belief = Belief()

    _fold(belief, 1, phase_texts=frozenset({"IMPS"}))
    assert belief.phase == "RoleReveal"

    # Reveal text clears; an ordinary playing scene (task counter, no meeting)
    # must advance the machine to Playing rather than sticking at RoleReveal.
    _fold(belief, 2, crew_tasks_remaining=5)
    assert belief.phase == "Playing"
    assert belief.phase_start_tick == 2


def test_alive_crewmate_role_is_derived_during_play() -> None:
    belief = Belief()
    # A plain playing scene with no imposter/ghost HUD marker.
    _fold(belief, 1, crew_tasks_remaining=5)
    assert belief.phase == "Playing"
    assert belief.self_role == "crewmate"


def test_crewmate_tracks_global_kill_cooldown_start() -> None:
    from players.crewrift.crewborg.perception.entities import VotingState

    belief = Belief()

    _fold(belief, 10, phase_texts=frozenset({"CREWMATE"}))
    _fold(belief, 11, crew_tasks_remaining=5)
    assert belief.self_role == "crewmate"
    assert belief.kill_cooldown_start_tick == 11

    meeting = ResolvedScene(
        tick=20,
        camera_ready=True,
        camera_x=0,
        camera_y=0,
        voting=VotingState(timer_present=True),
    )
    update_belief(belief, Percept(tick=20, messages_applied=20, resolved=meeting))
    _fold(belief, 21, crew_tasks_remaining=5)
    assert belief.kill_cooldown_start_tick == 21


def test_imposter_hud_sets_role_and_kill_ready() -> None:
    belief = Belief()
    _fold(belief, 1, crew_tasks_remaining=5, self_role="imposter", self_kill_ready=True)
    assert belief.self_role == "imposter"
    assert belief.self_kill_ready is True


def test_just_killed_recorded_on_kill_ready_to_cooldown_edge() -> None:
    belief = Belief()
    # Imposter, kill ready.
    _fold(belief, 5, self_role="imposter", self_kill_ready=True, crew_tasks_remaining=3)
    assert belief.last_kill_tick is None
    # Kill ready → cooldown: we just killed someone.
    _fold(belief, 6, self_role="imposter", self_kill_ready=False, crew_tasks_remaining=3)
    assert belief.last_kill_tick == 6
    # Staying on cooldown does not re-record.
    _fold(belief, 7, self_role="imposter", self_kill_ready=False, crew_tasks_remaining=3)
    assert belief.last_kill_tick == 6


def test_kill_ready_since_tick_tracks_the_cooldown_to_ready_edge() -> None:
    belief = Belief()
    # Cooldown: no ready-since stamp.
    _fold(belief, 5, self_role="imposter", self_kill_ready=False, crew_tasks_remaining=3)
    assert belief.kill_ready_since_tick is None
    # Cooldown → ready: stamp the tick we became able to kill.
    _fold(belief, 6, self_role="imposter", self_kill_ready=True, crew_tasks_remaining=3)
    assert belief.kill_ready_since_tick == 6
    # Staying ready does not re-stamp (urgency keeps climbing).
    _fold(belief, 9, self_role="imposter", self_kill_ready=True, crew_tasks_remaining=3)
    assert belief.kill_ready_since_tick == 6
    # Killing (ready → cooldown) clears it.
    _fold(belief, 10, self_role="imposter", self_kill_ready=False, crew_tasks_remaining=3)
    assert belief.kill_ready_since_tick is None
    assert belief.last_kill_tick == 10


def test_roster_accumulates_a_sighting_trail() -> None:
    from players.crewrift.crewborg.perception.entities import VisiblePlayer

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
    from players.crewrift.crewborg.types import ROSTER_HISTORY_MAX, PlayerRecord

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


def test_no_false_kill_after_a_meeting() -> None:
    from players.crewrift.crewborg.perception.entities import VotingState

    belief = Belief()
    _fold(belief, 1, self_role="imposter", self_kill_ready=True, crew_tasks_remaining=3)  # Playing, ready

    # Meeting: voting active; the HUD role icon is absent so kill_ready is carried.
    meeting = ResolvedScene(
        tick=2, camera_ready=True, camera_x=0, camera_y=0, voting=VotingState(timer_present=True)
    )
    update_belief(belief, Percept(tick=2, messages_applied=2, resolved=meeting))
    assert belief.phase == "Voting"

    # Back to Playing with cooldown reset by the meeting — NOT a kill.
    _fold(belief, 3, self_role="imposter", self_kill_ready=False, crew_tasks_remaining=3)
    assert belief.phase == "Playing"
    assert belief.last_kill_tick is None


def test_phase_stays_unknown_before_any_signal() -> None:
    belief = Belief()
    # Camera not yet ready and no signals: phase remains unknown, role unset.
    resolved = ResolvedScene(tick=1, camera_ready=False, camera_x=0, camera_y=0)
    update_belief(belief, Percept(tick=1, messages_applied=1, resolved=resolved))
    assert belief.phase == "unknown"
    assert belief.self_role is None


# --- perception tape (design §5.1) ------------------------------------------


def test_tape_records_camera_ready_frames_with_viewport_and_entities() -> None:
    from players.crewrift.crewborg.perception.entities import VisibleBody, VisiblePlayer

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
    from players.crewrift.crewborg.types import RECENT_FRAMES_MAX

    belief = Belief()
    for tick in range(RECENT_FRAMES_MAX + 10):
        _fold(belief, tick)
    assert len(belief.recent_frames) == RECENT_FRAMES_MAX
    assert belief.recent_frames[-1].tick == RECENT_FRAMES_MAX + 9  # newest kept
    assert belief.recent_frames[0].tick == 10  # oldest dropped


# --- life-status linkage (design §5) ----------------------------------------


def test_body_sighting_connects_last_seen_alive_to_the_death() -> None:
    from players.crewrift.crewborg.perception.entities import VisibleBody, VisiblePlayer

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
    from players.crewrift.crewborg.perception.entities import VisibleBody, VotingState

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
    from players.crewrift.crewborg.perception.entities import CensusEntry

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
    from players.crewrift.crewborg.perception.entities import ChatLine, VotingState

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


# --- upstream 2026-06-10: GameInfo / MeetingCall phases, tick marker, ---------
# --- game config, meeting attribution, game outcome. --------------------------


def _fold_interstitial(belief: Belief, tick: int, **resolved_fields) -> None:
    """Fold a camera-down frame (interstitials tear the map object down)."""

    resolved = ResolvedScene(tick=tick, camera_ready=False, camera_x=0, camera_y=0, **resolved_fields)
    update_belief(belief, Percept(tick=tick, messages_applied=tick, resolved=resolved))


def test_game_info_phase_and_config_learning() -> None:
    from players.crewrift.crewborg.perception.entities import GameInfo

    belief = Belief()
    _fold_interstitial(
        belief,
        1,
        phase_texts=frozenset({"GAME INFO"}),
        game_info=GameInfo(
            kill_cooldown_ticks=500, tasks_per_player=8, vote_timer_ticks=1200, max_ticks=10000
        ),
    )
    assert belief.phase == "GameInfo"
    assert belief.kill_cooldown_config_ticks == 500
    assert belief.tasks_per_player == 8
    assert belief.vote_timer_ticks == 1200
    assert belief.game_max_ticks == 10000

    # GameInfo → RoleReveal → Playing proceeds as before.
    _fold_interstitial(belief, 73, phase_texts=frozenset({"CREWMATE"}))
    assert belief.phase == "RoleReveal"
    _fold(belief, 120, crew_tasks_remaining=5)
    assert belief.phase == "Playing"


def test_game_info_to_playing_when_role_reveal_disabled() -> None:
    from players.crewrift.crewborg.perception.entities import GameInfo

    belief = Belief()
    _fold_interstitial(belief, 1, phase_texts=frozenset({"GAME INFO"}), game_info=GameInfo())
    assert belief.phase == "GameInfo"
    # roleRevealTicks=0 servers jump straight to Playing: a live camera frame.
    _fold(belief, 73)
    assert belief.phase == "Playing"


def test_meeting_call_pins_phase_and_latches_attribution() -> None:
    from players.crewrift.crewborg.perception.entities import MeetingCall, VotingState

    belief = Belief()
    _fold(belief, 1, crew_tasks_remaining=5)
    assert belief.phase == "Playing"

    call = MeetingCall(caller_color="red", trigger="report", body_color="green")
    _fold_interstitial(belief, 10, meeting_call=call)
    assert belief.phase == "MeetingCall"
    assert belief.phase_start_tick == 10
    assert belief.meeting_called_by == "red"
    assert belief.meeting_trigger == "report"
    assert belief.meeting_reported_body_color == "green"
    # The named body is a death we now know about, even unseen in-world.
    assert belief.roster["green"].life_status == "dead"
    assert belief.roster["green"].death_source == "report"

    # The interstitial holds for ~72 ticks, then Voting opens; the attribution
    # persists through the meeting and lands on the meeting record.
    _fold_interstitial(belief, 82, voting=VotingState(timer_present=True))
    assert belief.phase == "Voting"
    assert belief.meeting_called_by == "red"
    assert belief.meeting_history[-1].called_by == "red"
    assert belief.meeting_history[-1].trigger == "report"
    assert belief.meeting_history[-1].reported_body_color == "green"

    # A later meeting resets the latch before refilling it.
    _fold(belief, 200, crew_tasks_remaining=4)
    assert belief.phase == "Playing"
    _fold_interstitial(belief, 300, meeting_call=MeetingCall(caller_color="blue", trigger="button"))
    assert belief.meeting_called_by == "blue"
    assert belief.meeting_trigger == "button"
    assert belief.meeting_reported_body_color is None


def test_meeting_call_with_unknown_caller_keeps_none() -> None:
    from players.crewrift.crewborg.perception.entities import MeetingCall

    belief = Belief()
    _fold(belief, 1, crew_tasks_remaining=5)
    _fold_interstitial(belief, 10, meeting_call=MeetingCall(trigger="report"))
    assert belief.phase == "MeetingCall"
    assert belief.meeting_called_by is None
    assert belief.meeting_trigger == "report"


def test_server_tick_folds_into_belief() -> None:
    belief = Belief()
    _fold(belief, 1, server_tick=4807)
    assert belief.server_tick == 4807
    # Absent marker (older server / dropped frame) keeps the last value.
    _fold(belief, 2)
    assert belief.server_tick == 4807


def test_game_over_outcome_and_roles_fold() -> None:
    belief = Belief()
    _fold_interstitial(
        belief,
        500,
        phase_texts=frozenset({"CREW WINS"}),
        game_over_roles={"red": "crewmate", "green": "imposter"},
    )
    assert belief.phase == "GameOver"
    assert belief.game_outcome == "crew_wins"
    assert belief.game_over_roles == {"red": "crewmate", "green": "imposter"}


def test_meeting_call_to_playing_recovery() -> None:
    """If the interstitial clears straight back to a live scene (e.g. a replayed
    or aborted meeting), a camera-ready frame recovers Playing."""

    from players.crewrift.crewborg.perception.entities import MeetingCall

    belief = Belief()
    _fold(belief, 1, crew_tasks_remaining=5)
    _fold_interstitial(belief, 10, meeting_call=MeetingCall(caller_color="red"))
    assert belief.phase == "MeetingCall"
    _fold(belief, 90)
    assert belief.phase == "Playing"
