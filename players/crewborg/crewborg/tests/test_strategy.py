"""Mode selector tests (design §10)."""

from __future__ import annotations

from crewborg.map.types import MapData, MapPoint, MapRect
from crewborg.strategy import RuleBasedStrategy
from crewborg.types import ActionState, Belief, PlayerEvent, PlayerRecord
from players.player_sdk.types import BeliefSnapshot, ModeDirective, SharedMemory


def _select(belief: Belief) -> str:
    return _select_with(RuleBasedStrategy(), belief)


def _select_with(strategy: RuleBasedStrategy, belief: Belief, tick: int = 1) -> str:
    memory = SharedMemory(
        belief=belief, action_state=ActionState(), active_directive=ModeDirective(mode="idle")
    )
    directive = strategy.decide(BeliefSnapshot(tick=tick, memory=memory))
    return directive.mode


def _crewmate_being_tailed(
    *, tick: int, p: float = 0.7, tail_end: int | None = None, color: str = "red", alive: bool = True
) -> Belief:
    """A live crewmate being shadowed by ``color``: an (optionally lapsed) tailing_self
    interval plus a manually set posterior ``p`` (the selector reads belief.suspicion)."""

    belief = Belief(phase="Playing", self_role="crewmate", last_tick=tick, self_world_x=100, self_world_y=100)
    # A reachable button away from self (no nav graph ⇒ reachable), so Accuse can fire
    # and walk to it without immediately being "inside" it.
    belief.map = MapData(
        width=200, height=200, tasks=(), vents=(), rooms=(),
        button=MapRect(x=10, y=10, w=8, h=8), home=MapPoint(x=10, y=10),
    )
    belief.roster[color] = PlayerRecord(
        color=color,
        world_x=110,
        world_y=100,
        last_seen_tick=tick,
        life_status="alive" if alive else "dead",
        events=[
            PlayerEvent(
                kind="tailing_self", start_tick=1, end_tick=tick if tail_end is None else tail_end, target_color=None
            )
        ],
    )
    belief.suspicion = {color: p}
    return belief


def _map_with_button_around_self() -> MapData:
    # A button rect covering self at (100, 100), so "inside the button rect" is true.
    return MapData(
        width=200, height=200, tasks=(), vents=(), rooms=(),
        button=MapRect(x=96, y=96, w=8, h=8), home=MapPoint(x=10, y=10),
    )


def test_playing_crewmate_selects_normal() -> None:
    assert _select(Belief(phase="Playing", self_role="crewmate")) == "normal"
    # Role not yet known during early Playing still does tasks.
    assert _select(Belief(phase="Playing", self_role=None)) == "normal"
    # A crewmate ghost keeps doing its own tasks (design §7.3).
    assert _select(Belief(phase="Playing", self_role="dead")) == "normal"


def test_voting_selects_attend_meeting() -> None:
    assert _select(Belief(phase="Voting")) == "attend_meeting"


def test_body_in_view_selects_report_body() -> None:
    from crewborg.types import BodyEntry

    belief = Belief(phase="Playing", self_role="crewmate", visible_body_ids={2003})
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=10, world_y=10, first_seen_tick=1)
    assert _select(belief) == "report_body"


def test_ghost_does_tasks_not_report() -> None:
    from crewborg.types import BodyEntry

    # A dead crewmate (ghost) can't report; it goes straight to Normal even with a
    # body in view, so it keeps finishing its own tasks (design §7.3).
    belief = Belief(phase="Playing", self_role="dead", visible_body_ids={2003})
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=10, world_y=10, first_seen_tick=1)
    assert _select(belief) == "normal"


def test_active_tail_by_a_suspect_selects_accuse() -> None:
    assert _select(_crewmate_being_tailed(tick=40, p=0.7)) == "accuse"


def test_a_new_game_restores_the_button_call_budget() -> None:
    strategy = RuleBasedStrategy()
    at_button = _crewmate_being_tailed(tick=40, p=0.7)
    at_button.map = _map_with_button_around_self()
    _select_with(strategy, at_button, tick=40)  # spends the call
    assert _select_with(strategy, _crewmate_being_tailed(tick=41, p=0.7), tick=41) == "normal"
    # A fresh game (RoleReveal) resets the budget; accusing is available again.
    assert _select_with(strategy, Belief(phase="RoleReveal"), tick=42) == "idle"
    assert _select_with(strategy, _crewmate_being_tailed(tick=43, p=0.7), tick=43) == "accuse"


def _imposter_with_visible_target(**kwargs) -> Belief:
    belief = Belief(phase="Playing", self_role="imposter", last_tick=10, self_world_x=100, self_world_y=100, **kwargs)
    # A lone, isolated, reachable (no nav graph) crewmate — a valid kill opportunity.
    belief.roster["red"] = PlayerRecord(
        object_id=1004, color="red", facing="left", world_x=50, world_y=50, last_seen_tick=10,
        life_status="alive",
    )
    return belief


def test_imposter_searches_by_default() -> None:
    # No kill opportunity ⇒ Search (the always-on seeking stance; Pretend retired
    # 2026-06-24). Search keeps us near crew so a kill window opens.
    assert _select(Belief(phase="Playing", self_role="imposter", last_tick=10)) == "search"


def test_imposter_hunts_when_kill_ready_with_opportunity() -> None:
    assert _select(_imposter_with_visible_target(self_kill_ready=True)) == "hunt"
    # Kill ready but no target in view ⇒ Search owns target acquisition.
    no_target = Belief(
        phase="Playing", self_role="imposter", self_kill_ready=True, last_tick=10,
        self_world_x=100, self_world_y=100,
    )
    assert _select(no_target) == "search"


def test_imposter_evades_before_reporting_a_fresh_kill_body() -> None:
    from crewborg.types import BodyEntry

    # A fresh self-kill body in view -> evade first, outranking the old
    # report-first path even if the kill is otherwise ready.
    belief = _imposter_with_visible_target(self_kill_ready=True, last_kill_tick=9, visible_body_ids={2003})
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=60, world_y=60, first_seen_tick=10)
    assert _select(belief) == "evade"


def test_imposter_recons_within_the_recon_window_before_ready() -> None:
    # Not yet kill-ready, the cooldown clears in ~50 ticks (≤ recon_window 100), and a
    # crewmate has been seen ⇒ Recon (beeline to that crewmate so a victim is in hand
    # the instant the kill comes ready), not Search.
    belief = _imposter_with_visible_target(self_kill_ready=False)
    belief.kill_cooldown_start_tick = belief.last_tick
    belief.kill_cooldown_estimate = 50  # ticks_until_ready = start + 50 − now = 50
    assert _select(belief) == "recon"


def test_be_dumb_imposter_searches_instead_of_pretending(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_BE_DUMB", "1")

    belief = Belief(phase="Playing", self_role="imposter", self_kill_ready=False, last_tick=10)
    assert _select(belief) == "search"


def test_imposter_pretends_when_only_a_teammate_is_visible() -> None:
    # Kill ready but the only visible player is a teammate ⇒ no kill target, so Search.
    belief = _imposter_with_visible_target(self_kill_ready=True)
    belief.teammate_colors = {"red"}  # the visible target is red (see helper)
    assert _select(belief) == "search"
