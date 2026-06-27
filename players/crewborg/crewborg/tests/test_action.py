"""Action-layer tests: input encoding and idle resolution (design §3.3, §9)."""

from __future__ import annotations

import numpy as np

from crewborg.action import (
    BTN_A,
    BTN_B,
    BTN_DOWN,
    BTN_LEFT,
    BTN_RIGHT,
    BTN_UP,
    INPUT_HEADER,
    encode_input,
    resolve_action,
)
from crewborg.map.types import MapData, MapPoint, MapRect, TaskStation, Vent
from crewborg.nav import build_nav_graph
from crewborg.perception.entities import VoteCandidate, VotingState
from crewborg.types import ActionState, Belief, BodyEntry, Intent, PlayerRecord


def _one_task_map() -> MapData:
    return MapData(
        width=200,
        height=200,
        tasks=(TaskStation(name="t", x=100, y=100, w=20, h=20),),  # center (110, 110)
        vents=(),
        rooms=(),
        button=MapRect(x=0, y=0, w=28, h=34),
        home=MapPoint(x=0, y=0),
    )


def _detour_map_with_linking_vents() -> MapData:
    return MapData(
        width=200, height=120, tasks=(),
        vents=(
            Vent(x=86, y=56, w=8, h=8, group="g", group_index=1),  # center (90, 60), left
            Vent(x=106, y=56, w=8, h=8, group="g", group_index=2),  # center (110, 60), right
        ),
        rooms=(), button=MapRect(x=0, y=0, w=4, h=4), home=MapPoint(x=10, y=10),
    )


def test_escape_presses_b_to_vent_when_a_teleport_leg_is_next() -> None:
    mask = np.ones((120, 200), dtype=bool)
    mask[20:120, 98:102] = False  # wall splitting the map (gap along the top)
    map_data = _detour_map_with_linking_vents()
    nav = build_nav_graph(mask, map_data=map_data)

    # Stand on the left vent's anchor; the cheapest escape to the far side teleports.
    entry = nav.vent_anchor(0)
    belief = Belief(map=map_data, nav=nav, self_world_x=entry[0], self_world_y=entry[1])
    action_state = ActionState()
    command = resolve_action(Intent(kind="escape", point=(190, 110)), belief, action_state)

    assert action_state.route_teleports, "escape route should include a vent hop"
    assert command.held_mask == BTN_B  # at the vent entry: press B to vanish


def test_encode_input_emits_header_and_masked_byte() -> None:
    assert encode_input(0) == bytes([INPUT_HEADER, 0x00])
    assert encode_input(BTN_UP | BTN_A) == bytes([INPUT_HEADER, 0x21])
    assert encode_input(BTN_DOWN | BTN_LEFT | BTN_RIGHT) == bytes([INPUT_HEADER, 0x0E])


def test_navigate_predictive_stop_coasts_when_close_and_moving() -> None:
    belief = Belief(self_world_x=0, self_world_y=0)
    intent = Intent(kind="navigate_to", point=(5, 0))
    # Same intent as last tick (no reset) and a +5px/tick velocity toward target.
    action_state = ActionState(current_intent=intent, route=[(5, 0)], route_goal=(5, 0))
    action_state.last_self_x, action_state.last_self_y = -5, 0
    command = resolve_action(intent, belief, action_state)
    # Remaining 5px is within ~1.3*5 stopping distance, so release and coast.
    assert command.held_mask == 0


def test_navigate_holds_still_when_nav_route_unreachable() -> None:
    # A full-height wall splits the map; the goal across it is unreachable.
    mask = np.ones((24, 48), dtype=bool)
    mask[:, 24:32] = False
    belief = Belief(self_world_x=8, self_world_y=12)  # left of the wall
    belief.nav = build_nav_graph(mask, cell_size=8)
    command = resolve_action(Intent(kind="navigate_to", point=(40, 12)), belief, ActionState())
    # nav present + no path ⇒ hold still rather than steer into the wall.
    assert command.held_mask == 0


def test_complete_task_holds_a_inside_rect_and_navigates_outside() -> None:
    belief_inside = Belief(map=_one_task_map(), self_world_x=105, self_world_y=105)
    command = resolve_action(Intent(kind="complete_task", task_index=0), belief_inside, ActionState())
    assert command.held_mask == BTN_A  # on the station: hold A, no d-pad

    belief_outside = Belief(map=_one_task_map(), self_world_x=0, self_world_y=0)
    command = resolve_action(Intent(kind="complete_task", task_index=0), belief_outside, ActionState())
    assert command.held_mask == BTN_RIGHT | BTN_DOWN  # drive toward center (110, 110)


def _belief_with_body(self_xy: tuple[int, int], body_xy: tuple[int, int]) -> Belief:
    belief = Belief(self_world_x=self_xy[0], self_world_y=self_xy[1])
    belief.bodies[2003] = BodyEntry(
        object_id=2003, color="green", world_x=body_xy[0], world_y=body_xy[1], first_seen_tick=1
    )
    return belief


def test_report_in_range_edge_presses_a_refiring_requires_release() -> None:
    belief = _belief_with_body((10, 10), (10, 10))  # on top of the body
    action_state = ActionState()
    intent = Intent(kind="report", target_id=2003)

    assert resolve_action(intent, belief, action_state).held_mask == BTN_A  # fresh press
    assert resolve_action(intent, belief, action_state).held_mask == 0  # release to reset edge
    assert resolve_action(intent, belief, action_state).held_mask == BTN_A  # re-press


def test_vote_skip_steps_to_skip_then_confirms_once() -> None:
    belief = Belief()
    belief.voting = VotingState(cursor_present=True)  # on a player cell, not skip
    action_state = ActionState()
    intent = Intent(kind="vote")

    assert resolve_action(intent, belief, action_state).held_mask == BTN_DOWN  # step toward skip
    assert resolve_action(intent, belief, action_state).held_mask == 0  # release (edge)

    belief.voting = VotingState(skip_cursor_present=True)  # cursor now on skip
    confirm = resolve_action(intent, belief, action_state)
    assert confirm.held_mask == BTN_A and action_state.vote_confirmed
    # Vote is cast: no further input.
    assert resolve_action(intent, belief, action_state).held_mask == 0


def _vote_grid() -> VotingState:
    return VotingState(
        cursor_present=True,
        cursor_slot=0,  # currently on red's cell
        candidates=(
            VoteCandidate(slot=0, color="red", alive=True),
            VoteCandidate(slot=1, color="blue", alive=True),
        ),
    )


def test_targeted_vote_steps_to_the_target_then_confirms() -> None:
    belief = Belief()
    belief.voting = _vote_grid()  # cursor on slot 0, target is blue (slot 1)
    action_state = ActionState()
    intent = Intent(kind="vote", target_color="blue")

    assert resolve_action(intent, belief, action_state).held_mask == BTN_DOWN  # step toward blue
    assert resolve_action(intent, belief, action_state).held_mask == 0  # release (edge)

    belief.voting = belief.voting.model_copy(update={"cursor_slot": 1})  # cursor reached blue
    confirm = resolve_action(intent, belief, action_state)
    assert confirm.held_mask == BTN_A and action_state.vote_confirmed
    assert resolve_action(intent, belief, action_state).held_mask == 0  # cast: no further input


def test_unresolvable_target_falls_back_to_skip() -> None:
    belief = Belief()
    # Target not among the live candidates (gone / grid not up) ⇒ skip instead.
    belief.voting = VotingState(skip_cursor_present=True, candidates=(VoteCandidate(slot=0, color="red", alive=True),))
    command = resolve_action(Intent(kind="vote", target_color="purple"), belief, ActionState())
    assert command.held_mask == BTN_A  # confirms the skip, not a spin


def _map_with_button(x: int, y: int, w: int = 8, h: int = 8) -> MapData:
    return MapData(
        width=400, height=400, tasks=(), vents=(), rooms=(),
        button=MapRect(x=x, y=y, w=w, h=h), home=MapPoint(x=10, y=10),
    )


def test_call_meeting_presses_a_inside_the_button_rect() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, map=_map_with_button(96, 96))  # self inside [96,104)
    command = resolve_action(Intent(kind="call_meeting"), belief, ActionState())
    assert command.held_mask == BTN_A  # standing on the button ⇒ a fresh A press calls a meeting


def _belief_with_target(self_xy: tuple[int, int], target_xy: tuple[int, int]) -> Belief:
    belief = Belief(self_world_x=self_xy[0], self_world_y=self_xy[1])
    belief.roster["red"] = PlayerRecord(
        object_id=1004, color="red", facing="left", world_x=target_xy[0], world_y=target_xy[1],
        last_seen_tick=1, life_status="alive",
    )
    return belief


def test_kill_navigates_then_edge_presses_a_in_range() -> None:
    on_target = _belief_with_target((50, 50), (50, 50))
    action_state = ActionState()
    intent = Intent(kind="kill", target_color="red")
    assert resolve_action(intent, on_target, action_state).held_mask == BTN_A  # fresh press
    assert resolve_action(intent, on_target, action_state).held_mask == 0  # release (edge)

    far = _belief_with_target((300, 300), (50, 50))
    assert resolve_action(Intent(kind="kill", target_color="red"), far, ActionState()).held_mask == BTN_UP | BTN_LEFT


def _belief_with_vent(self_xy: tuple[int, int], vent_xy: tuple[int, int]) -> Belief:
    vent = Vent(x=vent_xy[0] - 7, y=vent_xy[1] - 7, w=14, h=14, group="1", group_index=1)  # center vent_xy
    map_data = MapData(
        width=1235, height=659, tasks=(), vents=(vent,), rooms=(), button=MapRect(x=0, y=0, w=28, h=34),
        home=MapPoint(x=0, y=0),
    )
    return Belief(map=map_data, self_world_x=self_xy[0], self_world_y=self_xy[1])


def test_vent_navigates_then_holds_b_level_in_range() -> None:
    on_vent = _belief_with_vent((100, 100), (100, 100))
    action_state = ActionState()
    intent = Intent(kind="vent")
    # B is level-triggered: held every tick in range (no edge release).
    assert resolve_action(intent, on_vent, action_state).held_mask == BTN_B
    assert resolve_action(intent, on_vent, action_state).held_mask == BTN_B

    far = _belief_with_vent((300, 300), (100, 100))
    assert resolve_action(Intent(kind="vent"), far, ActionState()).held_mask == BTN_UP | BTN_LEFT
