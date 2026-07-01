"""Unit tests for the crewborg domain-event tracer (events.py).

The tracer is the runtime's ``on_step_complete`` hook; here we drive it directly
with fabricated :class:`StepContext` values and assert the ``domain.*`` events and
counters it emits through an :class:`EventEmitter` bound to list sinks.
"""

from __future__ import annotations

from crewborg.action import BTN_A, BTN_B, BTN_LEFT
from crewborg.events import CrewborgEventTracer
from crewborg.strategy.commander.trace import CommanderTrace
from crewborg.strategy.suspicion import VOTE_PROBABILITY
from crewborg.types import (
    ActionState,
    Belief,
    BodyEntry,
    ChatEvent,
    Command,
    CommanderPriorities,
    Intent,
    PlayerEvent,
    PlayerRecord,
)
from players.player_sdk import EventEmitter, ListMetricsSink, ListTraceSink, ModeDirective, StepContext


class _Harness:
    """A tracer plus list sinks and a tick-advancing StepContext builder."""

    def __init__(
        self,
        *,
        debug: bool | None = None,
        viewer: bool = False,
        commander_trace: CommanderTrace | None = None,
    ) -> None:
        self.trace = ListTraceSink()
        self.metrics = ListMetricsSink()
        self.emit = EventEmitter(self.trace, self.metrics, tick=0)
        # Pin debug explicitly (default off) so an ambient CREWBORG_TRACE=debug in the
        # test environment can't perturb the lean-mode assertions.
        self.tracer = CrewborgEventTracer(debug=bool(debug), viewer=viewer, commander_trace=commander_trace)

    def step(
        self,
        *,
        belief: Belief | None = None,
        action_state: ActionState | None = None,
        intent: Intent | None = None,
        command: Command | None = None,
        active_directive: ModeDirective | None = None,
    ) -> None:
        self.emit.tick += 1
        directive = active_directive or ModeDirective(mode="test", source="test", reason="unit test")
        context: StepContext[Belief, ActionState, Intent, Command] = StepContext(
            tick=self.emit.tick,
            belief=belief if belief is not None else Belief(),
            action_state=action_state if action_state is not None else ActionState(),
            intent=intent if intent is not None else Intent(kind="idle"),
            command=command if command is not None else Command(),
            active_mode_name=directive.mode,
            active_directive=directive,
            emit=self.emit,
        )
        self.tracer(context)

    def events(self, name: str) -> list:
        return [event for event in self.trace.events if event.name == name]

    def counters(self, name: str) -> list:
        return [s for s in self.metrics.samples if s.name == name and s.kind == "counter"]

    def gauges(self, name: str) -> list:
        return [s for s in self.metrics.samples if s.name == name and s.kind == "gauge"]


def test_events_are_domain_prefixed_and_carry_runtime_tick() -> None:
    h = _Harness()
    belief = Belief(phase="Playing")
    h.step(belief=belief)  # tick 1: unknown -> Playing

    [event] = h.events("domain.phase_change")
    assert event.tick == 1
    assert event.data == {"from": "unknown", "to": "Playing"}


def test_phase_change_fires_once_per_transition() -> None:
    h = _Harness()
    h.step(belief=Belief(phase="Playing"))
    h.step(belief=Belief(phase="Playing"))  # no change
    h.step(belief=Belief(phase="Voting"))

    changes = h.events("domain.phase_change")
    assert [e.data["to"] for e in changes] == ["Playing", "Voting"]


def test_role_resolved_emitted_once() -> None:
    h = _Harness()
    h.step(belief=Belief(self_role=None))
    h.step(belief=Belief(self_role="imposter"))
    h.step(belief=Belief(self_role="imposter"))

    [event] = h.events("domain.role_resolved")
    assert event.data == {"role": "imposter"}


def test_body_sighted_once_per_body_with_counter() -> None:
    h = _Harness()
    belief = Belief()
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=110, world_y=100, first_seen_tick=1)
    h.step(belief=belief)
    h.step(belief=belief)  # same body still present: no re-emit

    [event] = h.events("domain.body_sighted")
    assert event.data == {"body_id": 2003, "color": "green", "world_x": 110, "world_y": 100}
    assert len(h.counters("domain.body_sighted")) == 1


def test_task_completed_on_set_growth() -> None:
    h = _Harness()
    belief = Belief(crew_tasks_remaining=5)
    belief.completed_task_indices = {2}
    h.step(belief=belief)
    belief.completed_task_indices = {2, 7}
    h.step(belief=belief)

    completed = h.events("domain.task_completed")
    assert [e.data["task_index"] for e in completed] == [2, 7]
    assert completed[1].data["crew_tasks_remaining"] == 5
    assert len(h.counters("domain.task_completed")) == 2


def test_kill_landed_on_cooldown_edge() -> None:
    h = _Harness()
    h.step(belief=Belief(self_role="imposter", last_kill_tick=None))
    belief = Belief(self_role="imposter", last_kill_tick=12, self_world_x=300, self_world_y=200)
    h.step(belief=belief)
    h.step(belief=belief)  # same kill tick: no re-emit

    [event] = h.events("domain.kill_landed")
    assert event.data == {"world_x": 300, "world_y": 200}
    assert len(h.counters("domain.kill_landed")) == 1


def test_vote_cast_fires_once_per_meeting() -> None:
    h = _Harness()
    h.step(action_state=ActionState(vote_confirmed=False))
    h.step(action_state=ActionState(vote_confirmed=True))  # cast
    h.step(action_state=ActionState(vote_confirmed=True))  # still held: no re-emit
    h.step(action_state=ActionState(vote_confirmed=False))  # action layer reset (intent changed)
    h.step(action_state=ActionState(vote_confirmed=True))  # next meeting cast

    assert len(h.events("domain.vote_cast")) == 2
    assert len(h.counters("domain.vote_cast")) == 2


def test_task_started_on_new_target_and_resume_after_interruption() -> None:
    h = _Harness()
    h.step(intent=Intent(kind="complete_task", task_index=4))
    h.step(intent=Intent(kind="complete_task", task_index=4))  # same target: no re-emit
    h.step(intent=Intent(kind="complete_task", task_index=9))  # new target
    h.step(intent=Intent(kind="call_meeting"))  # interruption clears the latch
    h.step(intent=Intent(kind="complete_task", task_index=9))  # resume counts as a new start

    started = h.events("domain.task_started")
    assert [e.data["task_index"] for e in started] == [4, 9, 9]


def test_kill_attempted_requires_the_a_edge_in_the_command() -> None:
    h = _Harness()
    # Navigating toward the target (d-pad held, no A) is not an attempt.
    h.step(intent=Intent(kind="kill", target_id=1007), command=Command(held_mask=BTN_LEFT))
    assert not h.events("domain.kill_attempted")

    # The fresh A press is the attempt.
    h.step(intent=Intent(kind="kill", target_id=1007), command=Command(held_mask=BTN_A))
    [event] = h.events("domain.kill_attempted")
    assert event.data == {"target_id": 1007}
    assert len(h.counters("domain.kill_attempted")) == 1


def test_report_vent_and_chat_attempts() -> None:
    h = _Harness()
    h.step(intent=Intent(kind="report", target_id=2003), command=Command(held_mask=BTN_A))
    h.step(intent=Intent(kind="vent", target_id=0), command=Command(held_mask=BTN_B))
    h.step(intent=Intent(kind="chat", text="no read, skipping"), command=Command(chat="no read, skipping"))

    assert h.events("domain.report_attempted")[0].data == {"body_id": 2003}
    assert h.events("domain.vent_attempted")
    assert h.events("domain.chat_sent")[0].data == {"text": "no read, skipping"}


def test_chat_received_emits_each_meeting_line_once_and_resets_per_meeting() -> None:
    h = _Harness()
    belief = Belief(phase="Voting", phase_start_tick=10)
    belief.chat_log = [ChatEvent(tick=11, speaker_color="red", text="where")]

    h.step(belief=belief)
    h.step(belief=belief)

    fresh_meeting = Belief(phase="Voting", phase_start_tick=99)
    fresh_meeting.chat_log = [ChatEvent(tick=100, speaker_color="red", text="where")]
    h.step(belief=fresh_meeting)

    received = h.events("domain.chat_received")
    assert [event.data["meeting_id"] for event in received] == [10, 99]
    assert received[0].data == {
        "meeting_id": 10,
        "speaker_color": "red",
        "text": "where",
        "chat_tick": 11,
    }
    assert len(h.counters("domain.chat_received")) == 2


def test_decision_snapshot_is_debug_only() -> None:
    h = _Harness()
    h.step(belief=Belief(phase="Playing"), intent=Intent(kind="idle"), command=Command())

    assert not h.events("domain.decision_snapshot")


def test_decision_trace_group_enables_compact_decision_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_TRACE_GROUPS", "decision")
    monkeypatch.setenv("CREWBORG_TRACE_DECISION_FIELDS", "mode,intent,command")
    h = _Harness()

    h.step(
        belief=Belief(phase="Voting"),
        intent=Intent(kind="chat", text="no read, skipping"),
        command=Command(chat="no read, skipping"),
        active_directive=ModeDirective(mode="attend_meeting", source="strategy", reason="unit"),
    )

    [event] = h.events("domain.decision_snapshot")
    assert list(event.data.keys()) == ["schema_version", "mode", "intent", "command"]
    assert event.data["mode"] == "attend_meeting"
    assert event.data["intent"]["kind"] == "chat"
    assert event.data["command"] == {"held_mask": 0, "buttons": [], "chat": True}


def test_commander_trace_is_not_emitted_by_default() -> None:
    commander_trace = CommanderTrace()
    commander_trace.record("commander_started", {"enabled": True})
    h = _Harness(commander_trace=commander_trace)

    h.step()

    assert not h.events("domain.commander_started")
    assert commander_trace.drain() == [("commander_started", {"enabled": True})]


def test_commander_trace_group_drains_and_emits_records(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_TRACE_GROUPS", "commander")
    commander_trace = CommanderTrace()
    commander_trace.record("commander_started", {"enabled": True})
    commander_trace.record("commander_call", {"outcome": "ok", "latency_ms": 12.5})
    h = _Harness(commander_trace=commander_trace)

    h.step()

    assert h.events("domain.commander_started")[0].data == {"enabled": True}
    assert h.events("domain.commander_call")[0].data == {"outcome": "ok", "latency_ms": 12.5}
    assert commander_trace.drain() == []


def test_debug_trace_drains_commander_records() -> None:
    commander_trace = CommanderTrace()
    commander_trace.record("commander_call_start", {"phase": "Playing", "role": "imposter"})
    h = _Harness(debug=True, commander_trace=commander_trace)

    h.step()

    assert h.events("domain.commander_call_start")[0].data == {"phase": "Playing", "role": "imposter"}


def test_commander_applied_emits_on_belief_commander_change(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_TRACE_GROUPS", "commander")
    h = _Harness()

    h.step(belief=Belief(commander=None))
    h.step(belief=Belief(commander=CommanderPriorities(hunt_room="electrical", as_of_tick=10)))
    h.step(belief=Belief(commander=CommanderPriorities(hunt_room="electrical", as_of_tick=10)))
    h.step(belief=Belief(commander=CommanderPriorities(hunt_room="bridge", as_of_tick=14)))

    applied = h.events("domain.commander_applied")
    assert [event.data["as_of_tick"] for event in applied] == [10, 14]
    assert applied[0].data["priorities"]["hunt_room"] == "electrical"
    assert applied[1].data["priorities"]["hunt_room"] == "bridge"


def test_commander_danger_marker_emits_and_clears(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_TRACE_GROUPS", "commander")
    h = _Harness()
    belief = Belief(
        commander_danger_events=[
            {"lever": "skip_evade", "danger_reason": "chain pressure before crew groups"},
        ]
    )

    h.step(belief=belief)
    h.step(belief=belief)

    danger = h.events("domain.commander_danger")
    assert len(danger) == 1
    assert danger[0].data == {"lever": "skip_evade", "danger_reason": "chain pressure before crew groups"}
    assert belief.commander_danger_events == []


def test_debug_decision_snapshot_includes_visibility_threat_task_and_command_geometry() -> None:
    from crewborg.map.types import MapData, MapPoint, MapRect, TaskStation

    map_data = MapData(
        width=200,
        height=200,
        tasks=(TaskStation(name="wires", x=96, y=96, w=12, h=8),),
        vents=(),
        rooms=(),
        button=MapRect(x=10, y=10, w=8, h=8),
        home=MapPoint(x=20, y=20),
    )
    belief = Belief(
        phase="Playing",
        self_role="crewmate",
        last_tick=10,
        self_world_x=100,
        self_world_y=100,
        map=map_data,
        visible_task_indices={0},
        active_task_progress_pct=12,
    )
    belief.roster["red"] = PlayerRecord(
        color="red",
        world_x=150,
        world_y=100,
        last_seen_tick=10,
        life_status="alive",
        events=[PlayerEvent(kind="vent_use", start_tick=9, end_tick=9)],  # a witnessed catch
    )
    belief.believed_imposters = {"red"}
    belief.suspicion = {"red": 0.99991}

    h = _Harness(debug=True)
    h.step(
        belief=belief,
        action_state=ActionState(route=[(100, 100), (110, 100)], route_cursor=1, route_goal=(102, 100)),
        intent=Intent(kind="complete_task", task_index=0, reason="completing assigned task"),
        command=Command(held_mask=BTN_LEFT),
        active_directive=ModeDirective(mode="normal", source="strategy", reason="unit"),
    )

    [event] = h.events("domain.decision_snapshot")
    data = event.data
    assert data["mode"] == "normal"
    assert data["intent"]["kind"] == "complete_task"
    assert data["command"] == {"held_mask": BTN_LEFT, "buttons": ["left"], "chat": False}
    assert data["self"] == {"x": 100, "y": 100}
    assert data["visible_players"][0]["color"] == "red"
    assert data["visible_players"][0]["believed_imposter"] is True
    assert data["threats"][0]["color"] == "red"
    assert data["threats"][0]["visible"] is True
    assert data["threats"][0]["age_ticks"] == 0
    assert data["threats"][0]["dist_sq"] == 2500
    assert data["threats"][0]["tailing_self"] is False  # this suspect was caught venting, not tailing
    assert data["task"]["task_index"] == 0
    assert data["task"]["inside"] is True
    assert data["task"]["goal"] == [102, 100]
    assert data["task"]["dist"] == 2.0
    assert data["nav"]["next_waypoint"] == [110, 100]


def test_debug_decision_snapshot_records_an_accuse_button_run() -> None:
    from crewborg.map.types import MapData, MapPoint, MapRect

    belief = Belief(
        phase="Playing",
        self_role="crewmate",
        last_tick=20,
        self_world_x=100,
        self_world_y=100,
        map=MapData(
            width=400, height=400, tasks=(), vents=(), rooms=(),
            button=MapRect(x=196, y=96, w=8, h=8), home=MapPoint(x=10, y=10),  # center (200, 100)
        ),
    )
    belief.roster["red"] = PlayerRecord(
        color="red",
        world_x=150,
        world_y=100,
        last_seen_tick=18,
        life_status="alive",
        events=[PlayerEvent(kind="tailing_self", start_tick=1, end_tick=20, target_color=None)],
    )
    belief.believed_imposters = {"red"}
    belief.suspicion = {"red": 0.99}

    h = _Harness(debug=True)
    h.step(
        belief=belief,
        intent=Intent(kind="call_meeting", target_color="red", reason="being tailed: call a meeting"),
        command=Command(held_mask=BTN_B),
        active_directive=ModeDirective(mode="accuse", source="strategy", reason="unit"),
    )

    [event] = h.events("domain.decision_snapshot")
    data = event.data
    assert data["visible_players"] == []
    assert data["threats"][0]["visible"] is False
    assert data["threats"][0]["age_ticks"] == 2
    assert data["threats"][0]["tailing_self"] is True
    assert data["accuse"] == {
        "active": True,
        "target_color": "red",
        "target_p": 0.99,
        "target_visible": False,
        "target_last_seen_tick": 18,
        "button_xy": [200, 100],
        "button_dist": 100.0,
        "button_dist_sq": 10000,
    }


# --- knowledge layer: per-player event log + suspicion reasoning -----------


def _crewmate_belief(**kwargs) -> Belief:
    return Belief(self_role="crewmate", total_player_count=8, **kwargs)


def test_player_event_emitted_for_each_newly_opened_interval() -> None:
    from crewborg.types import PlayerEvent, PlayerRecord

    h = _Harness()
    belief = Belief()
    record = belief.roster["red"] = PlayerRecord(color="red")
    record.events.append(PlayerEvent(kind="vent", start_tick=5, end_tick=5, region_index=2))
    h.step(belief=belief)
    # Extending the open interval (same list length) emits nothing new...
    record.events[0].end_tick = 9
    h.step(belief=belief)
    # ...a freshly opened interval does.
    record.events.append(PlayerEvent(kind="near_body", start_tick=10, end_tick=10, target_color="green", min_dist=7))
    h.step(belief=belief)

    events = h.events("domain.player_event")
    assert [(e.data["kind"], e.data["color"]) for e in events] == [("vent", "red"), ("near_body", "red")]
    assert events[1].data["min_dist"] == 7
    assert [s.tags["kind"] for s in h.counters("domain.player_event")] == ["vent", "near_body"]


def test_player_died_fires_once_on_the_alive_to_dead_edge() -> None:
    from crewborg.types import PlayerRecord

    h = _Harness()
    belief = Belief()
    record = belief.roster["blue"] = PlayerRecord(color="blue", life_status="alive")
    h.step(belief=belief)  # alive: nothing
    record.mark_dead(tick=40, source="body", body_xy=(120, 80))
    h.step(belief=belief)  # edge
    h.step(belief=belief)  # still dead: no re-emit

    [event] = h.events("domain.player_died")
    assert event.data == {"color": "blue", "source": "body", "death_tick": 40, "body_xy": [120, 80]}
    assert len(h.counters("domain.player_died")) == 1


def test_imposter_confirmed_and_believed_changed_on_set_moves() -> None:
    h = _Harness()
    belief = _crewmate_belief()
    h.step(belief=belief)  # empty: nothing

    # A witnessed catch (kill/vent_use event) is what witnessed_imposters reads.
    belief.roster["red"] = PlayerRecord(
        color="red", life_status="alive", events=[PlayerEvent(kind="kill", start_tick=4, end_tick=4, target_color="green")]
    )
    belief.suspicion = {"red": 0.999}
    belief.believed_imposters = {"red"}
    h.step(belief=belief)

    [confirmed] = h.events("domain.imposter_confirmed")
    assert confirmed.data["color"] == "red"
    [changed] = h.events("domain.believed_changed")
    assert changed.data == {"added": ["red"], "removed": [], "believed": ["red"]}

    # Believed set shrinking is reported too; confirmed (a fixed latent) is not re-emitted.
    belief.believed_imposters = set()
    h.step(belief=belief)
    assert h.events("domain.believed_changed")[-1].data["removed"] == ["red"]
    assert len(h.events("domain.imposter_confirmed")) == 1


def test_suspicion_snapshot_once_per_meeting_with_ranking_and_vote() -> None:
    from crewborg.types import PlayerEvent, PlayerRecord

    h = _Harness()
    belief = _crewmate_belief(phase="Playing")
    red = belief.roster["red"] = PlayerRecord(color="red", life_status="alive")
    red.events.append(PlayerEvent(kind="near_body", start_tick=3, end_tick=6, target_color="green", min_dist=5))
    belief.roster["blue"] = PlayerRecord(color="blue", life_status="alive")
    belief.suspicion = {"red": 0.91, "blue": 0.12}
    belief.believed_imposters = {"red"}
    h.step(belief=belief)  # Playing: no snapshot

    belief.phase = "Voting"
    h.step(belief=belief)  # meeting opens: snapshot
    h.step(belief=belief)  # still Voting: no re-emit

    [snap] = h.events("domain.suspicion_snapshot")
    assert [r["color"] for r in snap.data["ranking"]] == ["red", "blue"]  # sorted desc by P
    assert snap.data["would_vote"] == "red"
    assert snap.data["would_vote_p"] == 0.91
    assert snap.data["vote_bar"] == VOTE_PROBABILITY
    assert snap.data["ranking"][0]["events"][0] == {
        "kind": "near_body", "dur": 4, "target": "green", "region": None, "min_dist": 5,
    }

    # Leaving and re-entering Voting arms a second snapshot.
    belief.phase = "Playing"
    h.step(belief=belief)
    belief.phase = "Voting"
    h.step(belief=belief)
    assert len(h.events("domain.suspicion_snapshot")) == 2


def test_suspicion_snapshot_emits_feature_vector_and_raw_inputs_when_flag_set(monkeypatch) -> None:
    # Training capture: CREWBORG_TRACE_SUSPICION_FEATURES adds, per suspect, the exact
    # runtime feature vector + the raw inputs (seen_ticks, per-event end_tick) needed to
    # refit the model on what crewborg actually computes live.
    from crewborg.strategy.suspicion import _fitted_features
    from crewborg.types import PlayerEvent, PlayerRecord

    monkeypatch.setenv("CREWBORG_TRACE_SUSPICION_FEATURES", "1")
    h = _Harness()
    belief = _crewmate_belief(phase="Playing")
    red = belief.roster["red"] = PlayerRecord(color="red", life_status="alive")
    red.seen_ticks = 120
    red.events.append(PlayerEvent(kind="near_body", start_tick=3, end_tick=6, target_color="green", min_dist=5))
    belief.suspicion = {"red": 0.5}
    h.step(belief=belief)
    belief.phase = "Voting"
    h.step(belief=belief)

    entry = h.events("domain.suspicion_snapshot")[0].data["ranking"][0]
    assert entry["features"] == _fitted_features(belief, red)  # the exact model input
    assert entry["features"]["near_body_bodies"] == 1.0 and "tasks_completed_watched" in entry["features"]
    assert entry["seen_ticks"] == 120  # raw input for observed_samples
    assert entry["events"][0]["end_tick"] == 6  # raw input for follow_death_samples


def test_suspicion_snapshot_omits_features_by_default() -> None:
    from crewborg.types import PlayerEvent, PlayerRecord

    h = _Harness()
    belief = _crewmate_belief(phase="Playing")
    red = belief.roster["red"] = PlayerRecord(color="red", life_status="alive")
    red.events.append(PlayerEvent(kind="near_body", start_tick=3, end_tick=6, target_color="green", min_dist=5))
    belief.suspicion = {"red": 0.5}
    h.step(belief=belief)
    belief.phase = "Voting"
    h.step(belief=belief)

    entry = h.events("domain.suspicion_snapshot")[0].data["ranking"][0]
    assert "features" not in entry and "seen_ticks" not in entry
    assert "end_tick" not in entry["events"][0]


def test_suspicion_snapshot_skipped_when_no_suspicion() -> None:
    # An imposter / ghost has its suspicion cleared, so a meeting yields no snapshot.
    h = _Harness()
    belief = Belief(self_role="imposter", phase="Voting")
    h.step(belief=belief)
    assert not h.events("domain.suspicion_snapshot")


def test_debug_tick_dump_is_gated() -> None:
    off = _Harness(debug=False)
    belief = _crewmate_belief(phase="Playing")
    belief.suspicion = {"red": 0.4, "blue": 0.2}
    belief.believed_imposters = set()
    off.step(belief=belief)
    assert not off.events("domain.suspicion_tick")
    assert not off.gauges("domain.suspicion.top_p")

    on = _Harness(debug=True)
    on.step(belief=belief)
    [tick] = on.events("domain.suspicion_tick")
    assert tick.data["p"] == {"red": 0.4, "blue": 0.2}
    assert on.gauges("domain.suspicion.top_p")[0].value == 0.4
    assert on.gauges("domain.suspicion.believed_count")[0].value == 0.0


def test_suspicion_trace_group_enables_per_tick_suspicion_dump(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_TRACE_GROUPS", "suspicion")
    belief = _crewmate_belief(phase="Playing")
    belief.suspicion = {"red": 0.4, "blue": 0.2}

    h = _Harness()
    h.step(belief=belief)

    [tick] = h.events("domain.suspicion_tick")
    assert tick.data["p"] == {"red": 0.4, "blue": 0.2}
    assert h.gauges("domain.suspicion.top_p")[0].value == 0.4


def test_kill_ready_changed_on_cooldown_edges_imposter_only() -> None:
    h = _Harness()
    # A crewmate never emits kill-readiness, even though self_kill_ready may be set.
    h.step(belief=Belief(self_role="crewmate", self_kill_ready=True))
    assert not h.events("domain.kill_ready_changed")

    # Imposter: first sight (cooldown) → ready → cooldown each emit one edge.
    imp = Belief(self_role="imposter", self_kill_ready=False)
    h.step(belief=imp)  # first sight: not ready
    imp2 = Belief(self_role="imposter", self_kill_ready=True, kill_ready_since_tick=10, last_tick=15)
    h.step(belief=imp2)  # edge → ready
    h.step(belief=imp2)  # still ready: no re-emit
    imp3 = Belief(self_role="imposter", self_kill_ready=False, last_kill_tick=20)
    h.step(belief=imp3)  # edge → cooldown (killed)

    edges = h.events("domain.kill_ready_changed")
    assert [e.data["ready"] for e in edges] == [False, True, False]
    assert edges[1].data["ready_since_tick"] == 10
    assert edges[1].data["urgency_ticks"] == 5  # last_tick 15 − ready_since 10
    assert edges[2].data["last_kill_tick"] == 20
    assert {s.tags["ready"] for s in h.counters("domain.kill_ready_changed")} == {"True", "False"}


def test_kill_state_debug_tick_imposter_only() -> None:
    off = _Harness(debug=False)
    off.step(belief=Belief(self_role="imposter", self_kill_ready=True))
    assert not off.events("domain.kill_state")

    on = _Harness(debug=True)
    on.step(belief=Belief(self_role="crewmate", self_kill_ready=True))
    assert not on.events("domain.kill_state")  # crewmate: nothing
    on.step(belief=Belief(self_role="imposter", self_kill_ready=True, kill_ready_since_tick=3, last_tick=8))
    [state] = on.events("domain.kill_state")
    assert state.data["ready"] is True
    assert state.data["urgency_ticks"] == 5
    assert on.gauges("domain.kill.ready")[0].value == 1.0
    assert on.gauges("domain.kill.urgency_ticks")[0].value == 5.0


def test_occupancy_substrate_and_seek_target_events_are_lean() -> None:
    import numpy as np

    from crewborg.agent_tracking import OccupancySnapshot, update_agent_tracking
    from crewborg.map.types import MapData, MapPoint, MapRect
    from crewborg.nav import build_nav_graph

    map_data = MapData(
        width=64,
        height=64,
        tasks=(),
        vents=(),
        rooms=(),
        button=MapRect(x=8, y=8, w=8, h=8),
        home=MapPoint(x=4, y=4),
    )
    belief = Belief(
        map=map_data,
        nav=build_nav_graph(np.ones((64, 64), dtype=bool), map_data=map_data),
        self_role="imposter",
    )
    update_agent_tracking(belief)
    substrate = belief.agent_tracking.substrate
    assert substrate is not None
    cell = next(iter(substrate.cells.values()))
    belief.agent_tracking.snapshot = OccupancySnapshot(
        tick=1,
        expected_by_cell={cell.index: 1.0},
        top_cell=cell.index,
        top_point=cell.center,
        top_expected=1.0,
        tracked_count=1,
        support_cell_count=1,
    )

    h = _Harness()
    h.step(belief=belief)
    [substrate_event] = h.events("domain.occupancy_substrate")
    assert substrate_event.data["anchors"] == 2
    assert substrate_event.data["grid_cells"] == len(substrate.cells)
    [seek_event] = h.events("domain.occupancy_seek_target")
    assert seek_event.data["cell"] == cell.index
    assert seek_event.data["tracked"] == 1


def test_occupancy_reacquisition_events_are_emitted_once() -> None:
    from crewborg.agent_tracking import ReacquisitionEvent

    belief = Belief()
    belief.agent_tracking.reacquisitions.append(
        ReacquisitionEvent(
            tick=20,
            color="green",
            predicted_cell=2,
            actual_cell=5,
            predicted_point=(20, 20),
            actual_point=(80, 16),
            top_probability=0.25,
            distance_error=60.13,
            disc_radius=44.0,
        )
    )

    h = _Harness()
    h.step(belief=belief)
    h.step(belief=belief)

    [event] = h.events("domain.occupancy_reacquired")
    assert event.data == {
        "color": "green",
        "predicted_cell": 2,
        "actual_cell": 5,
        "predicted_point": [20, 20],
        "actual_point": [80, 16],
        "top_probability": 0.25,
        "distance_error": 60.13,
        "disc_radius": 44.0,
    }
    assert len(h.counters("domain.occupancy_reacquired")) == 1


def test_viewer_trace_emits_map_and_frame_payloads() -> None:
    from crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation, Vent

    map_data = MapData(
        width=320,
        height=180,
        tasks=(TaskStation(name="wires", x=40, y=50, w=10, h=12),),
        vents=(Vent(x=100, y=80, w=12, h=12, group="a", group_index=0),),
        rooms=(Room(name="Engine", x=20, y=30, w=120, h=70),),
        button=MapRect(x=150, y=40, w=16, h=16),
        home=MapPoint(x=12, y=14),
    )
    belief = Belief(
        map=map_data,
        phase="Playing",
        self_role="imposter",
        camera_ready=True,
        camera_x=8,
        camera_y=16,
        self_world_x=72,
        self_world_y=88,
    )
    action_state = ActionState(
        route=[(72, 88), (160, 100)],
        route_cursor=1,
        route_goal=(160, 100),
    )
    intent = Intent(kind="navigate_to", point=(160, 100), reason="inspect target")
    h = _Harness(viewer=True)

    h.step(belief=belief, action_state=action_state, intent=intent)

    [viewer_map] = h.events("domain.viewer_map")
    assert viewer_map.data["width"] == 320
    assert viewer_map.data["tasks"][0]["name"] == "wires"

    [frame] = h.events("domain.viewer_frame")
    assert frame.data["mode"]["name"] == "test"
    assert frame.data["intent"]["kind"] == "navigate_to"
    assert frame.data["nav"]["target"] == [160, 100]
    assert frame.data["nav"]["next_waypoint"] == [160, 100]
    assert frame.data["self"] == {"x": 72, "y": 88}


def test_viewer_trace_emits_occupancy_grid_once() -> None:
    from crewborg.agent_tracking import OccupancyCell, OccupancySubstrate

    belief = Belief()
    belief.agent_tracking.substrate = OccupancySubstrate(
        anchors=(),
        polylines={},
        cells={
            7: OccupancyCell(index=7, row=1, col=2, center=(80, 48), label="Engine"),
        },
        cell_size=32,
        rows=4,
        cols=5,
    )
    h = _Harness(viewer=True)

    h.step(belief=belief)
    h.step(belief=belief)

    grid_events = h.events("domain.viewer_occupancy_grid")
    assert len(grid_events) == 1
    assert grid_events[0].data["cells"] == [
        {"index": 7, "row": 1, "col": 2, "center": [80, 48], "label": "Engine"}
    ]


def test_env_flag_enables_debug_dump(monkeypatch) -> None:
    monkeypatch.setenv("CREWBORG_TRACE", "debug")
    tracer = CrewborgEventTracer()
    assert tracer._debug is True
    assert tracer._viewer is True
    monkeypatch.setenv("CREWBORG_TRACE", "")
    assert CrewborgEventTracer()._debug is False
    monkeypatch.setenv("CREWBORG_TRACE", "viewer")
    tracer = CrewborgEventTracer()
    assert tracer._viewer is True
    assert tracer._debug is False


def test_build_runtime_wires_the_tracer_as_on_step_complete() -> None:
    from crewborg import build_runtime

    runtime = build_runtime()
    assert isinstance(runtime.on_step_complete, CrewborgEventTracer)


def test_domain_event_flows_through_a_real_runtime_step() -> None:
    """End-to-end: a real step drives the hook and routes through the trace sink."""

    from crewborg import build_runtime
    from crewborg.coworld.scene import SceneState
    from crewborg.tests import sprite_wire as w
    from crewborg.types import Observation

    trace = ListTraceSink()
    runtime = build_runtime(trace_sink=trace)
    scene = SceneState()
    scene.apply(w.clear_objects())
    scene.apply(w.define_sprite(50, 1, 1, "STARTING"))  # interstitial text => Lobby
    scene.apply(w.define_object(9000, 10, 10, 0, 0, 50))
    scene.tick += 1
    runtime.step(Observation(scene=scene, tick=scene.tick))
    runtime.close()

    phase_events = [e for e in trace.events if e.name == "domain.phase_change"]
    assert phase_events
    assert phase_events[0].data == {"from": "unknown", "to": "Lobby"}
    assert phase_events[0].tick == 1


def test_debug_decision_snapshot_captures_voting_actuation_state() -> None:
    """During Voting the snapshot must tie perceived cursor position to vote
    progress — the action->effect record for diagnosing slow/failed votes."""

    from crewborg.perception.entities import VoteCandidate, VotingState

    belief = Belief(phase="Voting")
    belief.voting = VotingState(
        cursor_present=True,
        skip_cursor_present=False,
        cursor_slot=1,
        candidates=(
            VoteCandidate(slot=0, color="red", alive=True),
            VoteCandidate(slot=1, color="blue", alive=True),
        ),
    )

    h = _Harness(debug=True)
    h.step(
        belief=belief,
        action_state=ActionState(),
        intent=Intent(kind="vote", reason="unit"),
        command=Command(held_mask=BTN_A),
        active_directive=ModeDirective(mode="attend_meeting", source="strategy", reason="unit"),
    )

    [event] = h.events("domain.decision_snapshot")
    assert event.data["voting"] == {
        "cursor_slot": 1,
        "cursor_on_skip": False,
        "candidates": 2,
        "vote_confirmed": False,
    }


def test_debug_decision_snapshot_voting_state_absent_outside_meetings() -> None:
    h = _Harness(debug=True)
    h.step(belief=Belief(phase="Playing"), intent=Intent(kind="idle"), command=Command())

    [event] = h.events("domain.decision_snapshot")
    assert event.data["voting"] is None
