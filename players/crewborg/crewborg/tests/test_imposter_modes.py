"""Hunt / Evade imposter mode tests (design §7.2).

Covers the Hunt strike gate (when the imposter fires vs. shadows) and Evade's
post-kill re-approach toward the densest expected-crew area. The retired
Pretend/Search occupancy-seeking modes (cold-stored at ``modes/_deprecated/``)
are gone; their FSM successor is exercised in ``test_search_mode.py``.
"""

from __future__ import annotations

import numpy as np

from crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation, Vent
from crewborg.agent_tracking import OccupancySnapshot, update_agent_tracking
from crewborg.modes import EvadeMode, HuntMode
from crewborg.nav import build_nav_graph
from crewborg.types import ActionState, Belief, PlayerRecord


def _visible(belief: Belief, object_id: int, xy: tuple[int, int], color: str = "red", tick: int | None = None) -> None:
    belief.roster[color] = PlayerRecord(
        object_id=object_id, color=color, facing="left", world_x=xy[0], world_y=xy[1],
        last_seen_tick=belief.last_tick if tick is None else tick, life_status="alive",
    )


# --------------------------------------------------------------------------- #
# Hunt — strike gate: fire only when in-range + kill-ready + (unwitnessed      #
# OR already-killed OR danger); otherwise close in / shadow / idle.            #
# --------------------------------------------------------------------------- #


def test_hunt_strikes_a_victim_in_range_and_unwitnessed() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=True)
    _visible(belief, 1004, (108, 100), color="green")  # 8px away (<KillRange), alone
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "kill" and intent.target_color == "green"


def test_hunt_shadows_in_range_until_the_cooldown_clears() -> None:
    # In range + unwitnessed but NOT yet kill-ready (entered Hunt in the lead window):
    # lie in wait, don't fire, so the strike lands the instant the cooldown clears.
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=False)
    _visible(belief, 1004, (108, 100), color="green")
    assert HuntMode().decide(belief, ActionState()).kind == "navigate_to"


def test_hunt_stalks_a_distant_victim() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    _visible(belief, 1004, (300, 100), color="green")  # far ⇒ close in, don't kill
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to" and intent.point[0] > 100  # heading toward the victim


def test_hunt_ignores_a_recently_seen_victim() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5)
    _visible(belief, 1004, (120, 100), tick=1)  # Search may track this; Hunt requires visibility
    assert HuntMode().decide(belief, ActionState()).kind == "idle"


def test_hunt_skips_teammates() -> None:
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=True)
    belief.teammate_colors = {"red"}
    _visible(belief, 1004, (108, 100), color="red")  # teammate — never a victim
    assert HuntMode().decide(belief, ActionState()).kind == "idle"

    _visible(belief, 1007, (108, 100), color="green")  # an in-range crewmate is killable
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "kill" and intent.target_color == "green"


def test_hunt_lies_in_wait_when_a_witness_is_near() -> None:
    # Victim in range but a witness beside it (zero urgency) ⇒ shadow, don't fire.
    belief = Belief(self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=True)
    _visible(belief, 1004, (108, 100), color="green")
    _visible(belief, 1005, (110, 100), color="blue")  # witness next to the victim
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"  # lying in wait, not killing


def test_hunt_strikes_a_witnessed_victim_after_the_first_kill() -> None:
    # After our FIRST kill (last_kill_tick set), the unwitnessed requirement is dropped:
    # getting the SECOND kill is the imposter's core job, worth more than stealth. Same
    # witness setup as the lie-in-wait test, but with a kill already banked ⇒ strike.
    belief = Belief(
        self_world_x=100, self_world_y=100, last_tick=5, self_kill_ready=True, last_kill_tick=3,
    )
    _visible(belief, 1004, (108, 100), color="green")
    _visible(belief, 1005, (110, 100), color="blue")  # witness ignored once we've killed once
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "kill" and intent.target_color == "green"


def test_hunt_strikes_a_witnessed_victim_under_full_urgency() -> None:
    belief = Belief(
        self_world_x=100, self_world_y=100, last_tick=300, self_kill_ready=True, kill_ready_since_tick=0,
    )
    _visible(belief, 1004, (108, 100), color="green")
    _visible(belief, 1005, (110, 100), color="blue")  # witness ignored at full urgency
    intent = HuntMode().decide(belief, ActionState())
    assert intent.kind == "kill" and intent.target_color == "green"


# --------------------------------------------------------------------------- #
# Evade — re-approach the crew after a kill (rewritten 2026-06-26)             #
# Old flee behavior (vent away / walk off the body) is gone: Evade now beelines #
# toward the densest expected-crew area so a victim cluster is nearby when it    #
# hands back to Search/Recon.                                                    #
# --------------------------------------------------------------------------- #


def _evade_belief_with_occupancy(target_room: str) -> Belief:
    """An imposter in the Left room with expected-crew occupancy massed in `target_room`.
    A vent is present specifically to prove Evade no longer uses it."""
    map_data = MapData(
        width=128, height=64,
        tasks=(TaskStation(name="left", x=16, y=16, w=8, h=8),
               TaskStation(name="right", x=96, y=16, w=8, h=8)),
        vents=(Vent(x=8, y=8, w=14, h=14, group="1", group_index=1),),
        rooms=(Room(name="Left", x=0, y=0, w=64, h=64),
               Room(name="Right", x=64, y=0, w=64, h=64)),
        button=MapRect(x=4, y=48, w=8, h=8), home=MapPoint(x=8, y=8),
    )
    nav = build_nav_graph(np.ones((map_data.height, map_data.width), dtype=bool), map_data=map_data)
    belief = Belief(map=map_data, nav=nav, self_role="imposter", self_world_x=8, self_world_y=8)
    update_agent_tracking(belief)
    cells = [c for c in belief.agent_tracking.substrate.cells.values() if c.label == target_room]
    belief.agent_tracking.snapshot = OccupancySnapshot(
        tick=1, expected_by_cell={c.index: 0.5 for c in cells},
        top_cell=cells[0].index, top_point=cells[0].center, top_expected=0.5,
        tracked_count=1, support_cell_count=len(cells),
    )
    return belief


def test_evade_beelines_to_densest_crew_area_not_a_vent() -> None:
    belief = _evade_belief_with_occupancy("Right")  # crew massed across the map in Right
    intent = EvadeMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"  # no longer vents or flees the body
    assert intent.point[0] > 64          # heads INTO the crew-dense Right room


def test_evade_falls_back_to_last_seen_crew_without_occupancy() -> None:
    # Cold start: no occupancy grid yet, but we have seen a crewmate -> close on them.
    map_data = MapData(
        width=1000, height=1000, tasks=(), vents=(), rooms=(),
        button=MapRect(x=0, y=0, w=28, h=34), home=MapPoint(x=0, y=0),
    )
    belief = Belief(map=map_data, self_role="imposter", self_world_x=100, self_world_y=100)
    _visible(belief, 1007, (400, 300), color="green", tick=5)
    intent = EvadeMode().decide(belief, ActionState())
    assert intent.kind == "navigate_to"
    assert intent.point == (400, 300)
