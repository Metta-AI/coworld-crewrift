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


def test_phase_transitions_role_reveal_into_playing() -> None:
    belief = Belief()

    _fold(belief, 1, phase_texts=frozenset({"IMPS"}))
    assert belief.phase == "RoleReveal"

    # Reveal text clears; an ordinary playing scene (task counter, no meeting)
    # must advance the machine to Playing rather than sticking at RoleReveal.
    _fold(belief, 2, crew_tasks_remaining=5)
    assert belief.phase == "Playing"
    assert belief.phase_start_tick == 2


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
    from crewborg.perception.entities import VotingState

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
