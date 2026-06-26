"""Unit tests for the crewborg domain-event tracer (events.py).

The tracer is the runtime's ``on_step_complete`` hook; here we drive it directly
with fabricated :class:`StepContext` values and assert the ``domain.*`` events and
counters it emits through an :class:`EventEmitter` bound to list sinks.
"""

from __future__ import annotations

from players.crewrift.crewborg.action import BTN_A, BTN_B, BTN_LEFT
from players.crewrift.crewborg.events import CrewborgEventTracer
from players.crewrift.crewborg.strategy.meeting.vote_policy import vote_bar
from players.crewrift.crewborg.types import ActionState, Belief, BodyEntry, ChatEvent, Command, Intent, PlayerRecord
from players.player_sdk import EventEmitter, ListMetricsSink, ListTraceSink, ModeDirective, StepContext


def _spatial(**fields):
    """Expected payload plus the spatial annotation every domain event carries."""

    return {"self_x": None, "self_y": None, "room_id": None, **fields}


class _Harness:
    """A tracer plus list sinks and a tick-advancing StepContext builder."""

    def __init__(self, *, debug: bool | None = None, viewer: bool = False) -> None:
        self.trace = ListTraceSink()
        self.metrics = ListMetricsSink()
        self.emit = EventEmitter(self.trace, self.metrics, tick=0)
        # Pin debug explicitly (default off) so an ambient CREWBORG_TRACE=debug in the
        # test environment can't perturb the lean-mode assertions.
        self.tracer = CrewborgEventTracer(debug=bool(debug), viewer=viewer)

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
    assert event.data == _spatial(**{"from": "unknown", "to": "Playing"})


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
    assert event.data == _spatial(role="imposter")


def test_body_sighted_once_per_body_with_counter() -> None:
    h = _Harness()
    belief = Belief()
    belief.bodies[2003] = BodyEntry(object_id=2003, color="green", world_x=110, world_y=100, first_seen_tick=1)
    h.step(belief=belief)
    h.step(belief=belief)  # same body still present: no re-emit

    [event] = h.events("domain.body_sighted")
    assert event.data == _spatial(body_id=2003, color="green", world_x=110, world_y=100)
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
    # Spatially annotated with the (tracked) self fix; no prior kill attempt was
    # observed in this harness, so no strike geometry is folded in.
    assert event.data == {"world_x": 300, "world_y": 200, "self_x": 300, "self_y": 200, "room_id": None}
    assert len(h.counters("domain.kill_landed")) == 1


def test_vote_cast_fires_once_per_meeting() -> None:
    """One vote_cast per meeting, even though the action layer's ``vote_confirmed``
    latch flaps every time the meeting mode cycles its intents (vote → idle →
    vote re-resets it). A vote is final once cast, so re-confirms within the same
    meeting are not new casts."""

    h = _Harness()
    meeting = Belief(phase="Voting", phase_start_tick=10)
    h.step(belief=meeting, action_state=ActionState(vote_confirmed=False))
    h.step(belief=meeting, action_state=ActionState(vote_confirmed=True))  # cast
    h.step(belief=meeting, action_state=ActionState(vote_confirmed=True))  # held: no re-emit
    # The intent-change reset + re-confirm flap (the production ~64×/episode bug):
    # still the same meeting, so no new event.
    h.step(belief=meeting, action_state=ActionState(vote_confirmed=False))
    h.step(belief=meeting, action_state=ActionState(vote_confirmed=True))
    h.step(belief=meeting, action_state=ActionState(vote_confirmed=False))
    h.step(belief=meeting, action_state=ActionState(vote_confirmed=True))

    [event] = h.events("domain.vote_cast")
    assert event.data["meeting_id"] == 10
    assert len(h.counters("domain.vote_cast")) == 1

    # A NEW meeting (fresh phase_start_tick) casts again.
    h.step(belief=Belief(phase="Playing"), action_state=ActionState(vote_confirmed=False))
    next_meeting = Belief(phase="Voting", phase_start_tick=99)
    h.step(belief=next_meeting, action_state=ActionState(vote_confirmed=True))
    assert [e.data["meeting_id"] for e in h.events("domain.vote_cast")] == [10, 99]
    assert len(h.counters("domain.vote_cast")) == 2

    # vote_confirmed flapping outside Voting (no meeting) never emits.
    h.step(belief=Belief(phase="Playing"), action_state=ActionState(vote_confirmed=False))
    h.step(belief=Belief(phase="Playing"), action_state=ActionState(vote_confirmed=True))
    assert len(h.events("domain.vote_cast")) == 2


def test_task_started_on_new_target_and_resume_after_interruption() -> None:
    h = _Harness()
    h.step(intent=Intent(kind="complete_task", task_index=4))
    h.step(intent=Intent(kind="complete_task", task_index=4))  # same target: no re-emit
    h.step(intent=Intent(kind="complete_task", task_index=9))  # new target
    h.step(intent=Intent(kind="flee_from", target_id=1))  # interruption clears the latch
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
    assert event.data["target_id"] == 1007
    assert event.data["target_color"] is None
    assert len(h.counters("domain.kill_attempted")) == 1


def test_kill_attempted_carries_geometry_and_kill_landed_inherits_it() -> None:
    """The strike press records victim identity/position, distance, readiness age,
    and the witnesses in LOS; the kill_landed outcome folds the same geometry in."""

    from players.crewrift.crewborg.types import PerceptionFrame

    h = _Harness()
    belief = Belief(
        self_role="imposter",
        self_kill_ready=True,
        kill_ready_since_tick=10,
        last_tick=22,
        self_world_x=100,
        self_world_y=100,
    )
    belief.roster["green"] = PlayerRecord(
        color="green", object_id=1004, world_x=112, world_y=109, last_seen_tick=22, life_status="alive"
    )
    belief.roster["blue"] = PlayerRecord(
        color="blue", object_id=1005, world_x=140, world_y=100, last_seen_tick=22, life_status="alive"
    )
    belief.recent_frames.append(
        PerceptionFrame(
            tick=22,
            camera_x=40,
            camera_y=40,
            players={"green": (112, 109), "blue": (140, 100)},
        )
    )

    h.step(
        belief=belief,
        intent=Intent(kind="kill", target_color="green"),
        command=Command(held_mask=BTN_A),
    )
    [attempt] = h.events("domain.kill_attempted")
    assert attempt.data["target_color"] == "green"
    assert attempt.data["target_id"] == 1004  # resolved from the roster
    assert attempt.data["victim_x"] == 112
    assert attempt.data["victim_y"] == 109
    assert attempt.data["dist"] == 15.0  # sqrt(12² + 9²)
    assert attempt.data["ticks_since_ready"] == 12  # last_tick 22 − ready_since 10
    [witness] = attempt.data["witnesses"]  # the victim itself is excluded
    assert witness["color"] == "blue"
    assert witness["dist"] == 29.4  # to the victim: sqrt(28² + 9²)
    assert witness["near"] is True
    assert witness["teammate"] is False
    assert attempt.data["self_x"] == 100  # spatial annotation

    # The kill lands (ready→cooldown edge): the outcome inherits the geometry.
    landed_belief = belief.model_copy(deep=True)
    landed_belief.last_kill_tick = 23
    h.step(belief=landed_belief)
    [landed] = h.events("domain.kill_landed")
    assert landed.data["target_color"] == "green"
    assert landed.data["victim_x"] == 112
    assert landed.data["dist"] == 15.0
    assert landed.data["ticks_since_ready"] == 12
    assert [w["color"] for w in landed.data["witnesses"]] == ["blue"]


def test_report_vent_and_chat_attempts() -> None:
    h = _Harness()
    h.step(intent=Intent(kind="report", target_id=2003), command=Command(held_mask=BTN_A))
    h.step(intent=Intent(kind="vent", target_id=0), command=Command(held_mask=BTN_B))
    h.step(intent=Intent(kind="chat", text="no read, skipping"), command=Command(chat="no read, skipping"))

    assert h.events("domain.report_attempted")[0].data == _spatial(body_id=2003)
    assert h.events("domain.vent_attempted")
    assert h.events("domain.chat_sent")[0].data == _spatial(text="no read, skipping")


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
    assert received[0].data == _spatial(
        meeting_id=10,
        speaker_color="red",
        text="where",
        chat_tick=11,
    )
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


def test_debug_decision_snapshot_includes_visibility_threat_task_and_command_geometry() -> None:
    from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect, TaskStation

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
    )
    belief.believed_imposters = {"red"}
    belief.confirmed_imposters = {"red"}
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
    assert data["threats"][0]["flee_enter"] is True
    assert data["task"]["task_index"] == 0
    assert data["task"]["inside"] is True
    assert data["task"]["goal"] == [102, 100]
    assert data["task"]["dist"] == 2.0
    assert data["nav"]["next_waypoint"] == [110, 100]


def test_debug_decision_snapshot_marks_offscreen_last_known_flee_target() -> None:
    belief = Belief(
        phase="Playing",
        self_role="crewmate",
        last_tick=20,
        self_world_x=100,
        self_world_y=100,
    )
    belief.roster["red"] = PlayerRecord(
        color="red",
        world_x=150,
        world_y=100,
        last_seen_tick=18,
        life_status="alive",
    )
    belief.believed_imposters = {"red"}
    belief.suspicion = {"red": 0.99}

    h = _Harness(debug=True)
    h.step(
        belief=belief,
        intent=Intent(kind="flee_from", target_color="red", reason="fleeing believed imposter"),
        command=Command(held_mask=BTN_B),
        active_directive=ModeDirective(mode="flee", source="strategy", reason="unit"),
    )

    [event] = h.events("domain.decision_snapshot")
    data = event.data
    assert data["visible_players"] == []
    assert data["threats"][0]["visible"] is False
    assert data["threats"][0]["age_ticks"] == 2
    assert data["threats"][0]["flee_stale"] is False
    assert data["flee"] == {
        "active": True,
        "target_color": "red",
        "target_visible": False,
        "target_last_seen_tick": 18,
        "target_age_ticks": 2,
        "target_xy": [150, 100],
        "away_point": [50, 100],
        "target_dist": 50.0,
        "target_dist_sq": 2500,
    }


# --- knowledge layer: per-player event log + suspicion reasoning -----------


def _crewmate_belief(**kwargs) -> Belief:
    return Belief(self_role="crewmate", total_player_count=8, **kwargs)


def test_player_event_emitted_for_each_newly_opened_interval() -> None:
    from players.crewrift.crewborg.types import PlayerEvent, PlayerRecord

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
    from players.crewrift.crewborg.types import PlayerRecord

    h = _Harness()
    belief = Belief()
    record = belief.roster["blue"] = PlayerRecord(color="blue", life_status="alive")
    h.step(belief=belief)  # alive: nothing
    record.mark_dead(tick=40, source="body", body_xy=(120, 80))
    h.step(belief=belief)  # edge
    h.step(belief=belief)  # still dead: no re-emit

    [event] = h.events("domain.player_died")
    assert event.data == _spatial(color="blue", source="body", death_tick=40, body_xy=[120, 80])
    assert len(h.counters("domain.player_died")) == 1


def test_imposter_confirmed_and_believed_changed_on_set_moves() -> None:
    h = _Harness()
    belief = _crewmate_belief()
    h.step(belief=belief)  # empty: nothing

    belief.confirmed_imposters = {"red"}
    belief.suspicion = {"red": 0.999}
    belief.believed_imposters = {"red"}
    h.step(belief=belief)

    [confirmed] = h.events("domain.imposter_confirmed")
    assert confirmed.data["color"] == "red"
    [changed] = h.events("domain.believed_changed")
    assert changed.data == _spatial(added=["red"], removed=[], believed=["red"])

    # Believed set shrinking is reported too; confirmed (a fixed latent) is not re-emitted.
    belief.believed_imposters = set()
    h.step(belief=belief)
    assert h.events("domain.believed_changed")[-1].data["removed"] == ["red"]
    assert len(h.events("domain.imposter_confirmed")) == 1


def test_suspicion_snapshot_once_per_meeting_with_ranking_and_vote() -> None:
    from players.crewrift.crewborg.types import PlayerEvent, PlayerRecord

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
    # The bar is state-dependent now (vote_policy): 8 declared players but only 2
    # known alive reads as a must-eject endgame, so the bar collapses to 0.
    assert snap.data["vote_bar"] == vote_bar(belief)
    assert snap.data["ranking"][0]["events"][0] == {
        "kind": "near_body", "dur": 4, "target": "green", "region": None, "min_dist": 5,
    }

    # Leaving and re-entering Voting arms a second snapshot.
    belief.phase = "Playing"
    h.step(belief=belief)
    belief.phase = "Voting"
    h.step(belief=belief)
    assert len(h.events("domain.suspicion_snapshot")) == 2


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

    from players.crewrift.crewborg.agent_tracking import OccupancySnapshot, update_agent_tracking
    from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect
    from players.crewrift.crewborg.nav import build_nav_graph

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
    from players.crewrift.crewborg.agent_tracking import ReacquisitionEvent

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
    assert event.data == _spatial(
        color="green",
        predicted_cell=2,
        actual_cell=5,
        predicted_point=[20, 20],
        actual_point=[80, 16],
        top_probability=0.25,
        distance_error=60.13,
        disc_radius=44.0,
    )
    assert len(h.counters("domain.occupancy_reacquired")) == 1


def test_viewer_trace_emits_map_and_frame_payloads() -> None:
    from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect, Room, TaskStation, Vent

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
    from players.crewrift.crewborg.agent_tracking import OccupancyCell, OccupancySubstrate

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


def test_meeting_called_emitted_once_per_meeting_with_attribution() -> None:
    h = _Harness()
    h.step(belief=Belief(phase="Playing", self_world_x=200, self_world_y=150))

    meeting = Belief(
        phase="MeetingCall",
        phase_start_tick=50,
        meeting_called_by="red",
        meeting_trigger="report",
        meeting_reported_body_color="green",
    )
    h.step(belief=meeting)
    h.step(belief=meeting)  # same meeting: no re-emit

    [event] = h.events("domain.meeting_called")
    assert event.data["by"] == "red"
    assert event.data["trigger"] == "report"
    assert event.data["body_color"] == "green"
    # Spatial annotation carries the LAST KNOWN fix (the camera is down during
    # the interstitial, so belief's live position is None) — where we were when
    # the meeting interrupted us.
    assert event.data["self_x"] == 200
    assert event.data["self_y"] == 150
    assert h.counters("domain.meeting_called")[0].tags == {"trigger": "report"}

    # A later meeting (new phase_start_tick) emits again.
    h.step(belief=Belief(phase="Playing"))
    button_meeting = Belief(
        phase="MeetingCall", phase_start_tick=300, meeting_called_by="blue", meeting_trigger="button"
    )
    h.step(belief=button_meeting)
    assert [e.data["by"] for e in h.events("domain.meeting_called")] == ["red", "blue"]


def test_game_over_emitted_once_with_outcome_roles_and_census() -> None:
    h = _Harness()
    belief = Belief(phase="GameOver", game_outcome="crew_wins")
    belief.roster["red"] = PlayerRecord(color="red", life_status="alive")
    belief.roster["green"] = PlayerRecord(color="green", life_status="dead")
    belief.game_over_roles = {"red": "crewmate", "green": "imposter"}
    h.step(belief=belief)
    h.step(belief=belief)  # no re-emit

    [event] = h.events("domain.game_over")
    assert event.data["outcome"] == "crew_wins"
    assert event.data["alive_by_color"] == {"green": False, "red": True}
    assert event.data["roles"] == {"green": "imposter", "red": "crewmate"}
    assert h.counters("domain.game_over")[0].tags == {"outcome": "crew_wins"}


def test_spatial_annotation_includes_room_and_survives_meetings() -> None:
    from players.crewrift.crewborg.map.types import MapData, MapPoint, MapRect, Room

    map_data = MapData(
        width=400,
        height=300,
        tasks=(),
        vents=(),
        rooms=(Room(name="Engine", x=0, y=0, w=100, h=100), Room(name="Bridge", x=100, y=0, w=100, h=100)),
        button=MapRect(x=10, y=10, w=8, h=8),
        home=MapPoint(x=5, y=5),
    )
    h = _Harness()
    h.step(belief=Belief(phase="Playing", map=map_data, self_world_x=150, self_world_y=50))
    [event] = h.events("domain.phase_change")
    assert event.data["self_x"] == 150
    assert event.data["room_id"] == 1  # Bridge

    # Camera down (meeting): the annotation keeps the last fix.
    h.step(belief=Belief(phase="Voting", map=map_data))
    voting_change = h.events("domain.phase_change")[-1]
    assert voting_change.data["to"] == "Voting"
    assert voting_change.data["self_x"] == 150
    assert voting_change.data["room_id"] == 1


class _FakeRecorder:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.info: dict = {}

    def record_position(self, **row) -> None:
        self.rows.append(row)

    def set_episode_info(self, **fields) -> None:
        self.info.update(fields)


def test_tracer_streams_positions_and_episode_info_to_recorder() -> None:
    from players.crewrift.crewborg.types import PerceptionFrame

    recorder = _FakeRecorder()
    trace = ListTraceSink()
    metrics = ListMetricsSink()
    emit = EventEmitter(trace, metrics, tick=0)
    tracer = CrewborgEventTracer(debug=False, viewer=False, episode_recorder=recorder)

    belief = Belief(
        phase="Playing",
        last_tick=1,
        server_tick=4242,
        self_role="imposter",
        self_world_x=120,
        self_world_y=90,
    )
    belief.voting = belief.voting.model_copy(update={"self_marker_color": "red"})
    belief.recent_frames.append(
        PerceptionFrame(tick=1, camera_x=60, camera_y=30, players={"green": (140, 95)})
    )
    emit.tick = 1
    context = StepContext(
        tick=1,
        belief=belief,
        action_state=ActionState(),
        intent=Intent(kind="navigate_to", point=(150, 90)),
        command=Command(held_mask=BTN_LEFT),
        active_mode_name="search",
        active_directive=ModeDirective(mode="search", source="strategy", reason="unit"),
        emit=emit,
    )
    tracer(context)

    [row] = recorder.rows
    assert row["tick"] == 1
    assert row["server_tick"] == 4242
    assert row["self_x"] == 120
    assert row["self_y"] == 90
    assert row["mode"] == "search"
    assert row["intent_kind"] == "navigate_to"
    assert row["held_mask"] == BTN_LEFT
    assert row["phase"] == "Playing"
    assert row["visible"] == '[{"c":"green","x":140,"y":95}]'
    # Role + color pushed into the artifact's episode metadata.
    assert recorder.info["role"] == "imposter"
    assert recorder.info["color"] == "red"

    # Game over pushes the outcome too.
    over = Belief(phase="GameOver", game_outcome="imps_win", last_tick=2)
    emit.tick = 2
    tracer(
        StepContext(
            tick=2,
            belief=over,
            action_state=ActionState(),
            intent=Intent(kind="idle"),
            command=Command(),
            active_mode_name="idle",
            active_directive=ModeDirective(mode="idle", source="default", reason="unit"),
            emit=emit,
        )
    )
    assert recorder.info["outcome"] == "imps_win"
    assert recorder.rows[-1]["phase"] == "GameOver"
    assert recorder.rows[-1]["visible"] == "[]"


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
    from players.crewrift.crewborg import build_runtime

    runtime = build_runtime()
    assert isinstance(runtime.on_step_complete, CrewborgEventTracer)


def test_domain_event_flows_through_a_real_runtime_step() -> None:
    """End-to-end: a real step drives the hook and routes through the trace sink."""

    from players.crewrift.crewborg import build_runtime
    from players.crewrift.crewborg.coworld.scene import SceneState
    from players.crewrift.crewborg.tests import sprite_wire as w
    from players.crewrift.crewborg.types import Observation

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
    assert phase_events[0].data == _spatial(**{"from": "unknown", "to": "Lobby"})
    assert phase_events[0].tick == 1
