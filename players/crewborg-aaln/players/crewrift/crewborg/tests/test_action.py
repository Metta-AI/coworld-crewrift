"""Action-layer tests: input encoding and idle resolution (design §3.3, §9)."""

from __future__ import annotations

import numpy as np

from players.crewrift.crewborg.action import (
    BTN_A,
    BTN_B,
    BTN_DOWN,
    BTN_LEFT,
    BTN_RIGHT,
    BTN_UP,
    CHAT_HEADER,
    INPUT_HEADER,
    encode_chat,
    encode_input,
    resolve_action,
)
from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect, TaskStation, Vent
from players.crewrift.crewborg.nav import build_nav_graph
from players.crewrift.crewborg.perception.entities import VoteCandidate, VotingState
from players.crewrift.crewborg.types import ActionState, Belief, BodyEntry, Intent, PlayerRecord


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


def test_periodic_replan_reroots_the_route_on_interval() -> None:
    from players.crewrift.crewborg.action import REPLAN_INTERVAL

    nav = build_nav_graph(np.ones((40, 40), dtype=bool), cell_size=8)
    belief = Belief(nav=nav, self_world_x=4, self_world_y=4)
    action_state = ActionState()
    intent = Intent(kind="navigate_to", point=(36, 36))  # fixed goal throughout

    resolve_action(intent, belief, action_state)  # initial plan
    assert action_state.ticks_since_plan == 0
    # A few more ticks with the goal unchanged: the counter climbs...
    for _ in range(REPLAN_INTERVAL - 1):
        resolve_action(intent, belief, action_state)
    assert action_state.ticks_since_plan == REPLAN_INTERVAL - 1
    # ...and the next tick triggers a periodic re-plan (counter resets), with no goal change.
    resolve_action(intent, belief, action_state)
    assert action_state.ticks_since_plan == 0


def test_escape_resumes_walking_after_the_teleport() -> None:
    # Mid-route: cursor sits on the teleport target and the hop has dropped us next
    # to it. We must advance past it and walk onward, not vent back to the entry.
    goal = (190, 110)
    intent = Intent(kind="escape", point=goal)
    action_state = ActionState(
        current_intent=intent,
        route=[(90, 60), (110, 60), goal],  # entry anchor, exit anchor, goal
        route_cursor=1,
        route_goal=goal,
        route_teleports={1: 0},
    )
    # Standing on the exit anchor (just teleported there).
    belief = Belief(map=_detour_map_with_linking_vents(), self_world_x=110, self_world_y=60)
    command = resolve_action(intent, belief, action_state)
    assert action_state.route_cursor == 2  # advanced past the teleport target
    assert not (command.held_mask & BTN_B)  # walking onward, not venting back


def test_escape_walks_before_reaching_the_vent() -> None:
    mask = np.ones((120, 200), dtype=bool)
    mask[20:120, 98:102] = False
    map_data = _detour_map_with_linking_vents()
    nav = build_nav_graph(mask, map_data=map_data)

    # Far from any vent: the first move is a walk toward the route, not a B press.
    belief = Belief(map=map_data, nav=nav, self_world_x=10, self_world_y=110)
    command = resolve_action(Intent(kind="escape", point=(190, 110)), belief, ActionState())
    assert command.held_mask != 0 and not (command.held_mask & BTN_B)


def test_encode_input_emits_header_and_masked_byte() -> None:
    assert encode_input(0) == bytes([INPUT_HEADER, 0x00])
    assert encode_input(BTN_UP | BTN_A) == bytes([INPUT_HEADER, 0x21])
    assert encode_input(BTN_DOWN | BTN_LEFT | BTN_RIGHT) == bytes([INPUT_HEADER, 0x0E])


def test_encode_input_masks_reserved_bit_7() -> None:
    # Bit 7 is reserved and must never reach the wire.
    assert encode_input(0xFF) == bytes([INPUT_HEADER, 0x7F])


def test_resolve_idle_holds_nothing() -> None:
    action_state = ActionState(held_mask=BTN_UP)
    command = resolve_action(Intent(kind="idle"), Belief(), action_state)
    assert command.held_mask == 0
    assert command.chat is None
    assert action_state.held_mask == 0


def test_navigate_presses_dpad_toward_target() -> None:
    belief = Belief(self_world_x=0, self_world_y=0)
    command = resolve_action(Intent(kind="navigate_to", point=(100, 0)), belief, ActionState())
    assert command.held_mask == BTN_RIGHT

    belief = Belief(self_world_x=0, self_world_y=0)
    command = resolve_action(Intent(kind="navigate_to", point=(0, 100)), belief, ActionState())
    assert command.held_mask == BTN_DOWN


def test_navigate_releases_within_arrive_deadband() -> None:
    belief = Belief(self_world_x=0, self_world_y=0)
    command = resolve_action(Intent(kind="navigate_to", point=(3, 0)), belief, ActionState())
    assert command.held_mask == 0


def test_navigate_predictive_stop_coasts_when_close_and_moving() -> None:
    belief = Belief(self_world_x=0, self_world_y=0)
    intent = Intent(kind="navigate_to", point=(5, 0))
    # Same intent as last tick (no reset) and a +5px/tick velocity toward target.
    action_state = ActionState(current_intent=intent, route=[(5, 0)], route_goal=(5, 0))
    action_state.last_self_x, action_state.last_self_y = -5, 0
    command = resolve_action(intent, belief, action_state)
    # Remaining 5px is within ~1.3*5 stopping distance, so release and coast.
    assert command.held_mask == 0


def test_navigate_without_self_position_holds_still() -> None:
    command = resolve_action(Intent(kind="navigate_to", point=(100, 0)), Belief(), ActionState())
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


def test_navigate_noclip_steers_directly_even_when_nav_route_unreachable() -> None:
    mask = np.ones((24, 48), dtype=bool)
    mask[:, 24:32] = False
    belief = Belief(self_world_x=8, self_world_y=12)
    belief.nav = build_nav_graph(mask, cell_size=8)
    command = resolve_action(Intent(kind="navigate_to_noclip", point=(40, 12)), belief, ActionState())
    assert command.held_mask == BTN_RIGHT


def test_intent_change_resets_the_route() -> None:
    belief = Belief(self_world_x=0, self_world_y=0)
    action_state = ActionState()
    resolve_action(Intent(kind="navigate_to", point=(100, 0)), belief, action_state)
    assert action_state.route_goal == (100, 0)
    resolve_action(Intent(kind="navigate_to", point=(0, 100)), belief, action_state)
    assert action_state.route_goal == (0, 100)


def test_complete_task_holds_a_inside_rect_and_navigates_outside() -> None:
    belief_inside = Belief(map=_one_task_map(), self_world_x=105, self_world_y=105)
    command = resolve_action(Intent(kind="complete_task", task_index=0), belief_inside, ActionState())
    assert command.held_mask == BTN_A  # on the station: hold A, no d-pad

    belief_outside = Belief(map=_one_task_map(), self_world_x=0, self_world_y=0)
    command = resolve_action(Intent(kind="complete_task", task_index=0), belief_outside, ActionState())
    assert command.held_mask == BTN_RIGHT | BTN_DOWN  # drive toward center (110, 110)


def test_encode_chat_wire_format() -> None:
    assert encode_chat("hi") == bytes([CHAT_HEADER, 0x02, 0x00]) + b"hi"
    # Non-ASCII is dropped; length is the ASCII byte count.
    packet = encode_chat("héllo")
    assert packet == bytes([CHAT_HEADER, 0x04, 0x00]) + b"hllo"


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


def test_report_out_of_range_navigates_to_body() -> None:
    belief = _belief_with_body((200, 200), (10, 10))
    command = resolve_action(Intent(kind="report", target_id=2003), belief, ActionState())
    assert command.held_mask == BTN_UP | BTN_LEFT  # toward (10, 10) from (200, 200)


def test_call_meeting_navigates_then_edge_presses_a_on_button() -> None:
    far = Belief(map=_one_task_map(), self_world_x=100, self_world_y=100)
    command = resolve_action(Intent(kind="call_meeting"), far, ActionState())
    assert command.held_mask == BTN_UP | BTN_LEFT  # toward button center (14, 17)

    on_button = Belief(map=_one_task_map(), self_world_x=1, self_world_y=1, last_tick=42)
    action_state = ActionState()
    intent = Intent(kind="call_meeting")
    assert resolve_action(intent, on_button, action_state).held_mask == BTN_A
    assert action_state.last_call_meeting_attempt_tick == 42
    assert resolve_action(intent, on_button, action_state).held_mask == 0  # release to reset edge


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


def test_targeted_vote_confirms_immediately_when_already_on_target() -> None:
    belief = Belief()
    belief.voting = _vote_grid()  # cursor already on slot 0 = red
    command = resolve_action(Intent(kind="vote", target_color="red"), belief, ActionState())
    assert command.held_mask == BTN_A


def test_unresolvable_target_falls_back_to_skip() -> None:
    belief = Belief()
    # Target not among the live candidates (gone / grid not up) ⇒ skip instead.
    belief.voting = VotingState(skip_cursor_present=True, candidates=(VoteCandidate(slot=0, color="red", alive=True),))
    command = resolve_action(Intent(kind="vote", target_color="purple"), belief, ActionState())
    assert command.held_mask == BTN_A  # confirms the skip, not a spin


def test_vote_last_resort_confirms_after_step_budget() -> None:
    # A cursor/grid that never decodes (no skip cell, no cursor movement) must not
    # spin DOWN forever: past the step budget the resolver confirms wherever the
    # cursor is — any vote beats the no-vote penalty.
    belief = Belief()
    belief.voting = VotingState(cursor_present=True)  # never reaches skip
    action_state = ActionState()
    intent = Intent(kind="vote")

    for _ in range(200):  # far beyond any sane cursor walk
        command = resolve_action(intent, belief, action_state)
        if action_state.vote_confirmed:
            break
    assert action_state.vote_confirmed
    assert command.held_mask == BTN_A


def test_vote_last_resort_confirms_when_cursor_never_reaches_target() -> None:
    # A targeted vote whose cursor the server never moves onto the target slot
    # still ends in a confirmed vote within the budget.
    belief = Belief()
    belief.voting = _vote_grid()  # cursor stuck on slot 0; target blue is slot 1
    action_state = ActionState()
    intent = Intent(kind="vote", target_color="blue")

    for _ in range(200):
        resolve_action(intent, belief, action_state)
        if action_state.vote_confirmed:
            break
    assert action_state.vote_confirmed


def test_chat_emitted_once() -> None:
    action_state = ActionState()
    intent = Intent(kind="chat", text="gg")
    first = resolve_action(intent, Belief(), action_state)
    assert first.chat == "gg" and first.held_mask == 0
    assert resolve_action(intent, Belief(), action_state).chat is None  # not resent


def test_distinct_chat_intents_can_emit_after_an_intent_change() -> None:
    action_state = ActionState()
    assert resolve_action(Intent(kind="chat", text="first"), Belief(), action_state).chat == "first"
    assert resolve_action(Intent(kind="idle"), Belief(), action_state).chat is None
    assert resolve_action(Intent(kind="chat", text="second"), Belief(), action_state).chat == "second"


def test_flee_moves_away_from_threat() -> None:
    belief = Belief(self_world_x=100, self_world_y=100)
    belief.roster["red"] = PlayerRecord(
        object_id=1004, color="red", facing="left", world_x=110, world_y=100, last_seen_tick=1,
        life_status="alive",
    )
    command = resolve_action(Intent(kind="flee_from", target_color="red"), belief, ActionState())
    assert command.held_mask == BTN_LEFT  # threat is to our right ⇒ flee left


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


def _belief_with_button(
    self_xy: tuple[int, int], target_xy: tuple[int, int], button_xy: tuple[int, int]
) -> Belief:
    belief = _belief_with_target(self_xy, target_xy)
    belief.map = MapData(
        width=1235, height=659, tasks=(), vents=(), rooms=(),
        button=MapRect(x=button_xy[0], y=button_xy[1], w=28, h=34),
        home=MapPoint(x=0, y=0),
    )
    return belief


def test_kill_press_suppressed_inside_button_zone() -> None:
    # The server's A order is report -> button -> kill: a kill press from the
    # emergency-button zone opens a meeting instead of killing (observed 3x in
    # the v8 0.1.52 eval). In the zone: keep closing on the victim, never press.
    in_zone = _belief_with_button((110, 110), (120, 110), button_xy=(100, 100))
    command = resolve_action(Intent(kind="kill", target_color="red"), in_zone, ActionState())
    assert command.held_mask & BTN_A == 0
    assert command.held_mask == BTN_RIGHT  # closing on the victim instead

    # Within the inflated margin just outside the rect: still suppressed.
    margin = _belief_with_button((97, 110), (105, 110), button_xy=(100, 100))
    assert resolve_action(Intent(kind="kill", target_color="red"), margin, ActionState()).held_mask & BTN_A == 0

    # Clearly outside the zone: the normal fresh A press kills.
    outside = _belief_with_button((300, 300), (300, 300), button_xy=(100, 100))
    assert resolve_action(Intent(kind="kill", target_color="red"), outside, ActionState()).held_mask == BTN_A


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
