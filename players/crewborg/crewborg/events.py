"""Crewborg domain trace events (design §11).

The SDK runtime emits canonical *framework* boundary events (``perception``,
``belief_updated``, ``action_intent``, ``act_command``, ``mode_*``, …). Those
describe the loop; they can't name game-level happenings. This module derives
Crewrift events — phase transitions, sightings, and the objective/kill/vote
*outcomes* plus the *attempts* behind them — and emits them through the SDK's
domain-event seam (``EventEmitter``, so names are ``domain.``-prefixed and carry
the runtime tick).

``CrewborgEventTracer`` is wired into :func:`...build_runtime` as the runtime's
``on_step_complete`` hook. The runtime calls that hook once per ``step`` after
``perceive`` → ``update_belief`` → ``mode.decide`` → ``resolve_action``, so the
:class:`~players.player_sdk.StepContext` it receives is the single point where
this tick's finalized belief, the mode's chosen intent, and the produced command
all coexist. That matters because:

- *Attempt* events (kill/report/vent/chat) key on the wire ``command`` — the
  actual button edge — which modes never see (they run before the action layer).
- ``task_completed`` is concluded inside Normal mode's ``decide`` (see
  :mod:`...modes.normal`), so it is only visible after the mode has run.

The tracer keeps the previous tick's salient state and emits a trace event (and,
when a metrics sink is wired, a counter for countable outcomes) on each
transition. It only observes — it never mutates belief.

**Knowledge-layer tracing** (the per-player event log + Bayesian suspicion, the
reasoning *behind* the actions): this is the single most useful thing to see when
a live game goes weird ("why did it vote X / never flee the obvious imposter").
The tracer reads it off the finalized belief — keeping ``strategy/`` itself pure —
in two tiers:

- **Always on (deltas + meeting snapshots), lean enough for hosted log caps:**
  ``player_event`` when a new observation interval opens on someone's log,
  ``player_died`` on an alive→dead transition, ``imposter_confirmed`` /
  ``believed_changed`` when the suspicion sets move, a full ``suspicion_snapshot``
  (ranked posteriors + each suspect's event log + the would-be vote and the bar)
  at the start of every meeting, and ``meeting_decision`` (emitted by Attend Meeting)
  when the deterministic path commits — the headline meeting diagnostic: role, path,
  target, real-vs-fabricated, and the imposter's heat (vote tally + chat accusers) and
  chat-NLP state. For the imposter, ``kill_ready_changed`` fires on
  every kill cooldown→ready / ready→cooldown edge (with ``ready_since_tick``,
  ``urgency_ticks``, and whether a victim is trackable) — so kill-window utilization
  (how promptly the strike follows the cooldown clearing, and whether the gap is
  cooldown vs. no-victim) is readable without the debug stream.
- **Debug only (``CREWBORG_TRACE=debug``):** ``decision_snapshot`` (a compact
  per-tick audit tying visible players/bodies, believed threats, last-seen ages,
  flee gates, task/flee geometry, mode, intent, and command together), the entire
  live ``P(imposter)`` vector every tick (``suspicion_tick``) plus
  ``suspicion.top_p`` / ``believed_count`` gauges, and (imposter) a per-tick
  ``kill_state`` snapshot + ``kill.ready`` / ``kill.urgency_ticks`` gauges —
  heavy, for deep single-game forensics.
- **Trace replay viewer (``CREWBORG_TRACE=viewer`` or ``debug``):** browser-ready
  map/grid bootstraps plus one ``viewer_frame`` per tick with mode params, intent,
  nav target/route, and live belief overlays. Heavy, opt-in, and intended for
  single-game inspection in ``viewer/index.html``.

The same heavy families can also be enabled narrowly via ``CREWBORG_TRACE_GROUPS``
or ``CREWBORG_TRACE_INCLUDE``; the stderr sink applies the matching output filter.

This is an *observer* hanging off the side of the cognitive stack — it reads the
finalized belief / intent / command after a tick and renders telemetry; it is not
itself a stage in perception → belief → strategy → modes → action.

Collaborators
-------------
Relies on:
  - ``players.player_sdk`` — ``StepContext`` (the finalized tick), ``EventEmitter``
    (the ``domain.``-prefixed emit/counter/gauge seam).
  - ``types`` — reads ``Belief`` (phase/role/roster/bodies/suspicion/kill timing/
    agent_tracking), ``ActionState``, ``Intent``, ``Command``, ``CommanderPriorities``,
    ``PlayerRecord``.
  - ``strategy.suspicion`` — ``witnessed_imposters`` / ``top_suspect`` /
    ``_prior_imposter_p`` and the ``VOTE_PROBABILITY`` / ``ACCUSE_TAIL_RECENCY_TICKS``
    bars, so the snapshot mirrors what Attend Meeting / Accuse actually use.
  - ``strategy.opportunity`` — ``kill_urgency_ticks`` / ``has_trackable_victim`` for
    the kill-state traces.
  - ``strategy.commander.trace.CommanderTrace`` — background-thread telemetry it drains
    onto the loop thread.
  - ``action`` (``BTN_A`` / ``BTN_B``), ``perception.constants``, ``trace.TraceConfig``
    (the env-derived include/exclude/group filter).
Used by:
  - ``__init__.build_runtime`` wires ``CrewborgEventTracer`` as ``on_step_complete``.
Emits / touches: emits ``domain.*`` trace events, counters, and gauges only. It is
  read-only over belief **except** for draining two transient queues
  (``belief.commander_danger_events`` — and ``CommanderTrace`` — are cleared as they
  are flushed); it never changes any decision-bearing state.

Modifying this file: this tracer must stay a pure observer of game state — keep the
strategy modules themselves free of tracing by reading their results off belief here,
and never let an emit path mutate decision state. New always-on events must stay lean
(hosted logs are capped); put heavy/per-tick dumps behind the debug/viewer gates.
"""

from __future__ import annotations

import math
import os
from typing import Any

from crewborg.action import BTN_A, BTN_B
from crewborg.perception.constants import SCREEN_HEIGHT, SCREEN_WIDTH
from crewborg.strategy.commander.trace import CommanderTrace
from crewborg.strategy.opportunity import has_trackable_victim, kill_urgency_ticks
from crewborg.strategy.suspicion import (
    ACCUSE_TAIL_RECENCY_TICKS,
    VOTE_PROBABILITY,
    _prior_imposter_p,
    top_suspect,
    witnessed_imposters,
)
from crewborg.trace import TraceConfig
from crewborg.types import ActionState, Belief, Command, CommanderPriorities, Intent, PlayerRecord
from players.player_sdk import EventEmitter, StepContext


class CrewborgEventTracer:
    """Derive crewborg domain events from each tick's :class:`StepContext`.

    Usable directly as an ``on_step_complete`` hook: ``on_step_complete=tracer``.
    """

    def __init__(
        self,
        *,
        debug: bool | None = None,
        viewer: bool | None = None,
        trace_config: TraceConfig | None = None,
        commander_trace: CommanderTrace | None = None,
    ) -> None:
        # Previous-tick state for edge/delta detection. ``phase`` starts at the
        # Belief default so the first real transition (unknown → …) is reported.
        self._phase: str = "unknown"
        self._role: str | None = None
        self._seen_body_ids: set[int] = set()
        self._completed_task_indices: set[int] = set()
        self._last_kill_tick: int | None = None
        self._vote_confirmed: bool = False
        self._started_task_index: int | None = None

        # Knowledge-layer delta state (per color where noted).
        self._event_counts: dict[str, int] = {}  # color → events logged so far (emit the new tail)
        self._life: dict[str, str] = {}  # color → last-seen life_status (alive→dead edge)
        self._confirmed: set[str] = set()  # last witnessed_imposters set (kill/vent_use catches)
        self._believed: set[str] = set()  # last believed_imposters (over the flee bar)
        self._meeting_snapshotted: bool = False  # one suspicion snapshot per meeting
        self._chat_meeting_id: int | None = None
        self._chat_seen: set[tuple[int, str | None, str]] = set()
        self._kill_ready: bool | None = None  # last self_kill_ready (imposter cooldown edges)
        self._occupancy_substrate_seen: bool = False
        self._occupancy_reacquisition_count: int = 0
        self._occupancy_seek_cell: int | None = None
        self._viewer_map_seen: bool = False
        self._viewer_grid_seen: bool = False
        self._commander: CommanderPriorities | None = None
        self._commander_trace = commander_trace
        self._trace_config = trace_config if trace_config is not None else TraceConfig.from_env()
        # Full per-tick suspicion dump is opt-in: heavy, for single-game forensics.
        trace_level = os.environ.get("CREWBORG_TRACE", "").strip().lower()
        self._debug: bool = trace_level == "debug" if debug is None else debug
        # Viewer frames are also heavy, but are the clean input for the trace replay UI.
        self._viewer: bool = trace_level in {"viewer", "debug"} if viewer is None else viewer
        self._decision_fields = self._trace_config.decision_fields
        self._emit_decision_snapshot = self._optional_event_enabled("domain.decision_snapshot", self._debug)
        self._emit_viewer = self._optional_event_enabled("domain.viewer_frame", self._viewer)
        self._emit_suspicion_debug = self._optional_event_enabled("domain.suspicion_tick", self._debug)
        self._emit_kill_debug = self._optional_event_enabled("domain.kill_state", self._debug)
        self._emit_occupancy_debug = self._optional_event_enabled("domain.occupancy_snapshot", self._debug)
        self._emit_commander = self._optional_event_enabled("domain.commander_call", self._debug)

    def __call__(self, context: StepContext[Belief, ActionState, Intent, Command]) -> None:
        """The ``on_step_complete`` hook: run every observer for this finalized tick.

        Order matters only for the gated/heavy families (debug/viewer) running after
        the always-on deltas; each ``_observe_*`` is an independent edge/delta detector
        that compares the tick's state against the previous-tick state it caches and
        emits on change. Pure observation — no belief mutation beyond draining the two
        transient telemetry queues.
        """

        belief = context.belief
        emit = context.emit
        if self._emit_commander:
            self._observe_commander_trace(emit)
        self._observe_phase(belief, emit)
        self._observe_role(belief, emit)
        self._observe_bodies(belief, emit)
        self._observe_completed_tasks(belief, emit)
        self._observe_kill_landed(belief, emit)
        self._observe_vote(context.action_state, emit)
        self._observe_action(context.intent, context.command, emit)
        self._observe_chat_received(belief, emit)
        if self._emit_decision_snapshot:
            self._observe_decision_snapshot(context)
        # Knowledge layer: the event log + suspicion reasoning behind the actions.
        self._observe_player_events(belief, emit)
        self._observe_deaths(belief, emit)
        self._observe_suspicion_deltas(belief, emit)
        self._observe_meeting_suspicion(belief, emit)
        self._observe_kill_readiness(belief, emit, context.active_mode_name)
        self._observe_occupancy(belief, emit)
        if self._emit_viewer:
            self._observe_viewer(context)
        if self._emit_suspicion_debug:
            self._observe_debug_tick(belief, emit)
        if self._emit_kill_debug:
            self._observe_kill_debug(belief, emit, context.active_mode_name)
        if self._emit_occupancy_debug:
            self._observe_occupancy_debug(belief, emit)
        if self._emit_commander:
            self._observe_commander_applied(belief, emit)
        self._observe_commander_danger(belief, emit)

    def _optional_event_enabled(self, event_name: str, mode_enabled: bool) -> bool:
        """Whether a heavy/optional event family should be emitted: on if its mode
        (debug/viewer) is active and not explicitly excluded, or if the env trace
        config narrowly targets it (groups/include) regardless of mode."""

        enabled_by_mode = mode_enabled and not self._trace_config.excludes_event(event_name)
        return enabled_by_mode or self._trace_config.targets_event(event_name)

    # --- gameplay commander telemetry -------------------------------------

    def _observe_commander_trace(self, emit: EventEmitter) -> None:
        """Drain background-thread commander telemetry on the inner-loop thread."""

        if self._commander_trace is None:
            return
        for event, data in self._commander_trace.drain():
            emit.event(event, data)

    def _observe_commander_applied(self, belief: Belief, emit: EventEmitter) -> None:
        """Trace the sanitized priorities currently installed in belief."""

        if belief.commander == self._commander:
            return
        self._commander = belief.commander
        if belief.commander is None:
            return
        payload = belief.commander.model_dump()
        emit.event(
            "commander_applied",
            {
                "priorities": payload,
                "as_of_tick": belief.commander.as_of_tick,
            },
        )

    def _observe_commander_danger(self, belief: Belief, emit: EventEmitter) -> None:
        """Drain transient danger events produced outside a Mode emitter."""

        if not belief.commander_danger_events:
            return
        events = list(belief.commander_danger_events)
        belief.commander_danger_events.clear()
        if not self._emit_commander:
            return
        for event in events:
            emit.event("commander_danger", event)

    # --- state-transition / outcome events (belief & action-state deltas) ---

    def _observe_phase(self, belief: Belief, emit: EventEmitter) -> None:
        if belief.phase != self._phase:
            emit.event("phase_change", {"from": self._phase, "to": belief.phase})
            self._phase = belief.phase

    def _observe_role(self, belief: Belief, emit: EventEmitter) -> None:
        if self._role is None and belief.self_role is not None:
            self._role = belief.self_role
            emit.event("role_resolved", {"role": belief.self_role})

    def _observe_bodies(self, belief: Belief, emit: EventEmitter) -> None:
        for body_id in sorted(belief.bodies.keys() - self._seen_body_ids):
            body = belief.bodies[body_id]
            self._seen_body_ids.add(body_id)
            emit.event(
                "body_sighted",
                {"body_id": body_id, "color": body.color, "world_x": body.world_x, "world_y": body.world_y},
            )
            emit.counter("body_sighted")

    def _observe_completed_tasks(self, belief: Belief, emit: EventEmitter) -> None:
        for index in sorted(belief.completed_task_indices - self._completed_task_indices):
            emit.event("task_completed", {"task_index": index, "crew_tasks_remaining": belief.crew_tasks_remaining})
            emit.counter("task_completed")
        self._completed_task_indices = set(belief.completed_task_indices)

    def _observe_kill_landed(self, belief: Belief, emit: EventEmitter) -> None:
        # ``last_kill_tick`` advances on the kill-ready → cooldown edge that
        # update_belief records when our own kill lands (imposter only).
        if belief.last_kill_tick is not None and belief.last_kill_tick != self._last_kill_tick:
            self._last_kill_tick = belief.last_kill_tick
            emit.event("kill_landed", {"world_x": belief.self_world_x, "world_y": belief.self_world_y})
            emit.counter("kill_landed")

    def _observe_vote(self, action_state: ActionState, emit: EventEmitter) -> None:
        # vote_confirmed flips False→True the tick the vote is cast, and the action
        # layer resets it when the intent changes — so this fires once per meeting.
        if action_state.vote_confirmed and not self._vote_confirmed:
            emit.event("vote_cast", {})
            emit.counter("vote_cast")
        self._vote_confirmed = action_state.vote_confirmed

    # --- attempt events (intent + the wire command it produced) -------------

    def _observe_action(self, intent: Intent, command: Command, emit: EventEmitter) -> None:
        kind = intent.kind

        # task_started fires when we commit to a new task, and again if we resume
        # one after an interruption (any non-task intent clears the latch).
        if kind == "complete_task":
            if intent.task_index != self._started_task_index:
                self._started_task_index = intent.task_index
                emit.event("task_started", {"task_index": intent.task_index})
        else:
            self._started_task_index = None

        # The remaining events key on the actual button edge in the command, which
        # only the action layer produces (so they cannot live in a mode emitter).
        if kind == "chat" and command.chat is not None:
            emit.event("chat_sent", {"text": command.chat})
            emit.counter("chat_sent")
        elif kind == "kill" and command.held_mask & BTN_A:
            emit.event("kill_attempted", {"target_id": intent.target_id})
            emit.counter("kill_attempted")
        elif kind == "report" and command.held_mask & BTN_A:
            emit.event("report_attempted", {"body_id": intent.target_id})
            emit.counter("report_attempted")
        elif kind in ("vent", "escape") and command.held_mask & BTN_B:
            # ``escape`` presses B only on a vent teleport leg, so a B edge here is a
            # vent use just like the dedicated ``vent`` intent.
            emit.event("vent_attempted", {})
            emit.counter("vent_attempted")

    def _observe_chat_received(self, belief: Belief, emit: EventEmitter) -> None:
        """Emit each newly heard meeting chat line once per meeting."""

        if belief.phase != "Voting":
            self._chat_meeting_id = None
            self._chat_seen.clear()
            return
        if belief.phase_start_tick != self._chat_meeting_id:
            self._chat_meeting_id = belief.phase_start_tick
            self._chat_seen.clear()
        for event in belief.chat_log:
            key = (event.tick, event.speaker_color, event.text)
            if key in self._chat_seen:
                continue
            self._chat_seen.add(key)
            emit.event(
                "chat_received",
                {
                    "meeting_id": belief.phase_start_tick,
                    "speaker_color": event.speaker_color,
                    "text": event.text,
                    "chat_tick": event.tick,
                },
            )
            emit.counter("chat_received")

    # --- per-tick decision audit -----------------------------------------

    def _observe_decision_snapshot(self, context: StepContext[Belief, ActionState, Intent, Command]) -> None:
        """Emit a compact per-tick record linking perception, belief gates, and action.

        The SDK boundary traces show the selected mode/intent and final command,
        while viewer frames carry the full heavy state only when explicitly enabled.
        This lean snapshot is the default log-only bridge between them: enough to
        explain why a tick held a movement mask without replay decoding.
        """

        payload = _filter_decision_payload(_decision_snapshot_payload(context), self._decision_fields)
        context.emit.event("decision_snapshot", payload)

    # --- knowledge layer: per-player event log + suspicion reasoning --------

    def _observe_player_events(self, belief: Belief, emit: EventEmitter) -> None:
        """Emit each newly opened observation interval on any player's event log.

        A player's ``events`` list only grows (the open interval is extended in
        place), so the tail past the count we last saw is exactly the intervals
        opened since — the live "started seeing X doing Y" stream.
        """

        for color, record in belief.roster.items():
            seen = self._event_counts.get(color, 0)
            for event in record.events[seen:]:
                emit.event(
                    "player_event",
                    {
                        "color": color,
                        "kind": event.kind,
                        "start_tick": event.start_tick,
                        "target_color": event.target_color,
                        "region_index": event.region_index,
                        "min_dist": event.min_dist,
                    },
                )
                emit.counter("player_event", tags={"kind": event.kind})
            self._event_counts[color] = len(record.events)

    def _observe_deaths(self, belief: Belief, emit: EventEmitter) -> None:
        """Emit an alive/unknown → dead transition for any player (role-agnostic)."""

        for color, record in belief.roster.items():
            if record.life_status == "dead" and self._life.get(color) != "dead":
                emit.event(
                    "player_died",
                    {
                        "color": color,
                        "source": record.death_source,
                        "death_tick": record.death_seen_tick,
                        "body_xy": list(record.body_xy) if record.body_xy is not None else None,
                    },
                )
                emit.counter("player_died", tags={"source": record.death_source or "unknown"})
            self._life[color] = record.life_status

    def _observe_suspicion_deltas(self, belief: Belief, emit: EventEmitter) -> None:
        """Emit moves in the confirmed (witnessed) and believed (over flee bar) sets."""

        witnessed = witnessed_imposters(belief)
        for color in sorted(witnessed - self._confirmed):
            emit.event("imposter_confirmed", {"color": color, "p": round(belief.suspicion.get(color, 1.0), 4)})
            emit.counter("imposter_confirmed")
        self._confirmed = witnessed

        if belief.believed_imposters != self._believed:
            emit.event(
                "believed_changed",
                {
                    "added": sorted(belief.believed_imposters - self._believed),
                    "removed": sorted(self._believed - belief.believed_imposters),
                    "believed": sorted(belief.believed_imposters),
                },
            )
            self._believed = set(belief.believed_imposters)

    def _observe_meeting_suspicion(self, belief: Belief, emit: EventEmitter) -> None:
        """Snapshot the full suspicion picture once at the start of each meeting.

        The ranked posteriors, each suspect's event log, and the would-be vote
        (``top_suspect`` against the same vote bar Attend Meeting uses) — the one
        record that explains a meeting's vote after the fact.
        """

        if belief.phase != "Voting":
            self._meeting_snapshotted = False
            return
        if self._meeting_snapshotted:
            return
        self._meeting_snapshotted = True
        # Held by both live roles now: a crewmate's genuine belief, or an imposter's
        # deflection view over non-teammates (§10.4). A ghost has none ⇒ nothing to show.
        if not belief.suspicion:
            return
        target = top_suspect(belief)
        witnessed = witnessed_imposters(belief)
        ranking = [
            {
                "color": color,
                "p": round(p, 4),
                "confirmed": color in witnessed,
                "events": _event_summary(belief.roster.get(color)),
            }
            for color, p in sorted(belief.suspicion.items(), key=lambda kv: kv[1], reverse=True)
        ]
        emit.event(
            "suspicion_snapshot",
            {
                "role": belief.self_role,  # crewmate belief vs imposter deflection view
                "prior": round(_prior_imposter_p(belief), 4),
                "ranking": ranking,
                "confirmed": sorted(witnessed),
                "believed": sorted(belief.believed_imposters),
                "would_vote": target,
                "would_vote_p": round(belief.suspicion[target], 4) if target is not None else None,
                "vote_bar": VOTE_PROBABILITY,
            },
        )

    def _observe_kill_readiness(self, belief: Belief, emit: EventEmitter, mode: str) -> None:
        """Emit a kill cooldown→ready / ready→cooldown edge for the imposter.

        Fires on every transition of ``self_kill_ready`` (and once on first sight),
        carrying the cooldown context so kill-window utilization — how promptly the
        strike follows the cooldown clearing, and whether the gap is cooldown vs.
        no-victim — is readable from the lean stream alone.
        """

        if belief.self_role != "imposter":
            return
        ready = bool(belief.self_kill_ready)
        if ready == self._kill_ready:
            return
        self._kill_ready = ready
        data = _kill_state(belief)
        data["mode"] = mode
        emit.event("kill_ready_changed", data)
        emit.counter("kill_ready_changed", tags={"ready": str(ready)})

    def _observe_debug_tick(self, belief: Belief, emit: EventEmitter) -> None:
        """Debug-only: the entire live P(imposter) vector + summary gauges, per tick."""

        if not belief.suspicion:
            return
        emit.event("suspicion_tick", {"p": {c: round(p, 4) for c, p in belief.suspicion.items()}})
        emit.gauge("suspicion.top_p", max(belief.suspicion.values()))
        emit.gauge("suspicion.believed_count", float(len(belief.believed_imposters)))

    def _observe_kill_debug(self, belief: Belief, emit: EventEmitter, mode: str) -> None:
        """Debug-only (imposter): the full kill state every tick + ready/urgency gauges."""

        if belief.self_role != "imposter":
            return
        data = _kill_state(belief)
        data["mode"] = mode
        emit.event("kill_state", data)
        emit.gauge("kill.ready", 1.0 if data["ready"] else 0.0)
        emit.gauge("kill.urgency_ticks", float(data["urgency_ticks"]))

    def _observe_occupancy(self, belief: Belief, emit: EventEmitter) -> None:
        """Lean occupancy traces: substrate summary, reacquisition, and seek target."""

        tracking = belief.agent_tracking
        substrate = tracking.substrate
        if substrate is not None and not self._occupancy_substrate_seen:
            self._occupancy_substrate_seen = True
            emit.event(
                "occupancy_substrate",
                {
                    "anchors": len(substrate.anchors),
                    "polylines": len(substrate.polylines),
                    "grid_cells": len(substrate.cells),
                    "cell_size": substrate.cell_size,
                },
            )

        for event in tracking.reacquisitions[self._occupancy_reacquisition_count :]:
            emit.event(
                "occupancy_reacquired",
                {
                    "color": event.color,
                    "predicted_cell": event.predicted_cell,
                    "actual_cell": event.actual_cell,
                    "predicted_point": list(event.predicted_point) if event.predicted_point is not None else None,
                    "actual_point": list(event.actual_point),
                    "top_probability": round(event.top_probability, 4),
                    "distance_error": round(event.distance_error, 2) if event.distance_error is not None else None,
                    "disc_radius": round(event.disc_radius, 2),
                },
            )
            emit.counter("occupancy_reacquired")
        self._occupancy_reacquisition_count = len(tracking.reacquisitions)

        snapshot = tracking.snapshot
        if belief.self_role != "imposter" or snapshot is None:
            self._occupancy_seek_cell = None
            return
        if snapshot.top_cell is None or snapshot.top_cell == self._occupancy_seek_cell:
            return
        self._occupancy_seek_cell = snapshot.top_cell
        emit.event(
            "occupancy_seek_target",
            {
                "cell": snapshot.top_cell,
                "point": list(snapshot.top_point) if snapshot.top_point is not None else None,
                "expected": round(snapshot.top_expected, 4),
                "tracked": snapshot.tracked_count,
                "support_cells": snapshot.support_cell_count,
            },
        )

    def _observe_occupancy_debug(self, belief: Belief, emit: EventEmitter) -> None:
        """Debug-only: top occupancy cells and current per-agent support sizes."""

        snapshot = belief.agent_tracking.snapshot
        substrate = belief.agent_tracking.substrate
        if snapshot is None or substrate is None:
            return
        top = [
            {
                "cell": cell_id,
                "expected": round(expected, 4),
                "point": list(substrate.cells[cell_id].center),
                "label": substrate.cells[cell_id].label,
            }
            for cell_id, expected in sorted(snapshot.expected_by_cell.items(), key=lambda item: item[1], reverse=True)[
                :5
            ]
            if cell_id in substrate.cells
        ]
        emit.event(
            "occupancy_snapshot",
            {
                "tracked": snapshot.tracked_count,
                "support_cells": snapshot.support_cell_count,
                "top": top,
                "agents": {
                    color: {
                        "age_ticks": estimate.age_ticks,
                        "support_cells": estimate.support_cell_count,
                        "top_cell": estimate.top_cell,
                        "top_probability": round(estimate.top_probability, 4),
                    }
                    for color, estimate in belief.agent_tracking.estimates.items()
                },
            },
        )

    # --- viewer snapshots --------------------------------------------------

    def _observe_viewer(self, context: StepContext[Belief, ActionState, Intent, Command]) -> None:
        """Emit trace records consumed by the browser replay viewer.

        ``viewer_map`` and ``viewer_occupancy_grid`` are static bootstraps; the
        per-tick ``viewer_frame`` carries only live belief/action state.
        """

        belief = context.belief
        emit = context.emit
        if not self._viewer_map_seen and belief.map is not None:
            self._viewer_map_seen = True
            emit.event("viewer_map", _viewer_map_payload(belief))

        substrate = belief.agent_tracking.substrate
        if not self._viewer_grid_seen and substrate is not None:
            self._viewer_grid_seen = True
            emit.event(
                "viewer_occupancy_grid",
                {
                    "schema_version": 1,
                    "cell_size": substrate.cell_size,
                    "rows": substrate.rows,
                    "cols": substrate.cols,
                    "cells": [
                        {
                            "index": cell.index,
                            "row": cell.row,
                            "col": cell.col,
                            "center": list(cell.center),
                            "label": cell.label,
                        }
                        for cell in sorted(substrate.cells.values(), key=lambda item: item.index)
                    ],
                },
            )

        emit.event("viewer_frame", _viewer_frame_payload(context))


def _kill_state(belief: Belief) -> dict[str, object]:
    """The imposter's current kill-cooldown context (shared by the edge + debug traces)."""

    return {
        "ready": bool(belief.self_kill_ready),
        "ready_since_tick": belief.kill_ready_since_tick,
        "last_kill_tick": belief.last_kill_tick,
        "urgency_ticks": kill_urgency_ticks(belief),
        "has_trackable_victim": has_trackable_victim(belief),
    }


def _event_summary(record: PlayerRecord | None) -> list[dict[str, object]]:
    """Compact per-player event log for a suspicion snapshot (durations, not spans)."""

    if record is None:
        return []
    return [
        {
            "kind": event.kind,
            "dur": event.duration_ticks,
            "target": event.target_color,
            "region": event.region_index,
            "min_dist": event.min_dist,
        }
        for event in record.events
    ]


def _decision_snapshot_payload(context: StepContext[Belief, ActionState, Intent, Command]) -> dict[str, Any]:
    """Compact log payload for explaining one tick's mode/action decision."""

    belief = context.belief
    action_state = context.action_state
    visible_colors = _visible_colors(belief)
    return {
        "schema_version": 1,
        "phase": belief.phase,
        "role": belief.self_role,
        "mode": context.active_mode_name,
        "intent": _decision_intent_payload(context.intent),
        "command": _decision_command_payload(context.command),
        "self": _viewer_point(belief.self_world_x, belief.self_world_y),
        "visible_players": _decision_visible_players(belief, visible_colors),
        "visible_bodies": _decision_visible_bodies(belief),
        "threats": _decision_threats(belief, visible_colors),
        "task": _decision_task_payload(context),
        "accuse": _decision_accuse_payload(context, visible_colors),
        "nav": {
            "route_goal": _point_list(action_state.route_goal),
            "route_cursor": action_state.route_cursor,
            "route_len": len(action_state.route),
            "next_waypoint": _next_waypoint(action_state),
        },
        "voting": _decision_voting_payload(belief, action_state),
    }


def _decision_voting_payload(belief: Belief, action_state: ActionState) -> dict[str, Any] | None:
    """Ballot-actuation state, present only during Voting.

    Captures the action->effect chain for one vote: where the perceived cursor
    sits (`cursor_slot` / `cursor_on_skip`) versus whether we've confirmed
    (`vote_confirmed`). A cursor that doesn't advance across many ticks of
    `down` presses is the vote-timeout failure signature.
    """

    if belief.phase != "Voting":
        return None
    voting = belief.voting
    return {
        "cursor_slot": voting.cursor_slot,
        "cursor_on_skip": voting.skip_cursor_present,
        "candidates": len(voting.candidates),
        "vote_confirmed": action_state.vote_confirmed,
    }


def _filter_decision_payload(payload: dict[str, Any], fields: tuple[str, ...] | None) -> dict[str, Any]:
    if fields is None:
        return payload
    selected: dict[str, Any] = {"schema_version": payload["schema_version"]}
    for field in fields:
        if field in payload:
            selected[field] = payload[field]
    return selected


def _decision_intent_payload(intent: Intent) -> dict[str, Any]:
    return {
        "kind": intent.kind,
        "point": _point_list(intent.point),
        "target_color": intent.target_color,
        "target_id": intent.target_id,
        "task_index": intent.task_index,
        "reason": intent.reason,
    }


def _decision_command_payload(command: Command) -> dict[str, Any]:
    return {
        "held_mask": command.held_mask,
        "buttons": _button_names(command.held_mask),
        "chat": command.chat is not None,
    }


def _decision_visible_players(belief: Belief, visible_colors: set[str]) -> list[dict[str, Any]]:
    witnessed = witnessed_imposters(belief)
    players: list[dict[str, Any]] = []
    for color in sorted(visible_colors):
        record = belief.roster.get(color)
        if record is None:
            continue
        players.append(
            {
                "color": color,
                "xy": [record.world_x, record.world_y],
                "life_status": record.life_status,
                "suspicion": _rounded_p(belief.suspicion.get(color)),
                "believed_imposter": color in belief.believed_imposters,
                "confirmed_imposter": color in witnessed,
            }
        )
    return players


def _decision_visible_bodies(belief: Belief) -> list[dict[str, Any]]:
    return [
        {"id": body.object_id, "color": body.color, "xy": [body.world_x, body.world_y]}
        for body in sorted(
            (belief.bodies[body_id] for body_id in belief.visible_body_ids if body_id in belief.bodies),
            key=lambda item: item.object_id,
        )
    ]


def _decision_threats(belief: Belief, visible_colors: set[str]) -> list[dict[str, Any]]:
    witnessed = witnessed_imposters(belief)
    threat_colors = belief.believed_imposters | witnessed
    return [
        _decision_player_threat_payload(belief, color, visible_colors, witnessed)
        for color in sorted(threat_colors)
    ]


def _decision_player_threat_payload(
    belief: Belief, color: str, visible_colors: set[str], witnessed: set[str]
) -> dict[str, Any]:
    record = belief.roster.get(color)
    dist_sq = _record_dist_sq(belief, record)
    age_ticks = None if record is None else max(0, belief.last_tick - record.last_seen_tick)
    return {
        "color": color,
        "p": _rounded_p(belief.suspicion.get(color)),
        "believed": color in belief.believed_imposters,
        "confirmed": color in witnessed,
        "visible": color in visible_colors,
        "life_status": record.life_status if record is not None else None,
        "last_seen_tick": record.last_seen_tick if record is not None else None,
        "age_ticks": age_ticks,
        "xy": [record.world_x, record.world_y] if record is not None else None,
        "dist": _rounded_dist(dist_sq),
        "dist_sq": dist_sq,
        "tailing_self": _is_actively_tailing_self(record, belief.last_tick),
    }


def _is_actively_tailing_self(record: PlayerRecord | None, tick: int) -> bool:
    """Whether this player has a live ``tailing_self`` interval (the Accuse trigger)."""

    if record is None:
        return False
    for event in reversed(record.events):
        if event.kind == "tailing_self":
            return tick - event.end_tick <= ACCUSE_TAIL_RECENCY_TICKS
    return False


def _decision_task_payload(context: StepContext[Belief, ActionState, Intent, Command]) -> dict[str, Any] | None:
    intent = context.intent
    belief = context.belief
    if intent.kind != "complete_task" or intent.task_index is None:
        return None
    if belief.map is None or not (0 <= intent.task_index < len(belief.map.tasks)):
        return {"task_index": intent.task_index, "valid": False}

    task = belief.map.tasks[intent.task_index]
    self_xy = _belief_self_xy(belief)
    inside = (
        self_xy is not None
        and task.x <= self_xy[0] < task.x + task.w
        and task.y <= self_xy[1] < task.y + task.h
    )
    anchor = belief.nav.task_anchor(intent.task_index) if belief.nav is not None else None
    goal = anchor if anchor is not None else (task.center.x, task.center.y)
    return {
        "task_index": intent.task_index,
        "valid": True,
        "visible": intent.task_index in belief.visible_task_indices,
        "completed": intent.task_index in belief.completed_task_indices,
        "active_progress_pct": belief.active_task_progress_pct,
        "rect": {"x": task.x, "y": task.y, "w": task.w, "h": task.h},
        "inside": inside,
        "goal": list(goal),
        "anchor": _point_list(anchor),
        "dist": _rounded_dist(_dist_sq(self_xy, goal) if self_xy is not None else None),
    }


def _decision_accuse_payload(
    context: StepContext[Belief, ActionState, Intent, Command], visible_colors: set[str]
) -> dict[str, Any] | None:
    """Why/where we're calling a meeting: the tail we mean to accuse and the button run.

    Emitted while Accuse is active (a ``call_meeting`` intent). ``target_color`` is the
    suspect (best-effort — the meeting re-derives the vote from suspicion)."""

    intent = context.intent
    belief = context.belief
    if intent.kind != "call_meeting":
        return None

    target_color = intent.target_color
    target = belief.roster.get(target_color) if target_color is not None else None
    self_xy = _belief_self_xy(belief)
    button = belief.map.button if belief.map is not None else None
    button_anchor = belief.nav.button_anchor if belief.nav is not None else None
    button_xy = button_anchor if button_anchor is not None else (button.center.x, button.center.y) if button else None
    dist_sq = _dist_sq(self_xy, button_xy) if self_xy is not None and button_xy is not None else None
    return {
        "active": True,
        "target_color": target_color,
        "target_p": _rounded_p(belief.suspicion.get(target_color)) if target_color is not None else None,
        "target_visible": target_color in visible_colors if target_color is not None else None,
        "target_last_seen_tick": target.last_seen_tick if target is not None else None,
        "button_xy": list(button_xy) if button_xy is not None else None,
        "button_dist": _rounded_dist(dist_sq),
        "button_dist_sq": dist_sq,
    }


def _visible_colors(belief: Belief) -> set[str]:
    return {
        color
        for color, record in belief.roster.items()
        if record.life_status != "dead" and record.last_seen_tick == belief.last_tick
    }


def _record_dist_sq(belief: Belief, record: PlayerRecord | None) -> int | None:
    self_xy = _belief_self_xy(belief)
    if self_xy is None or record is None:
        return None
    return _dist_sq(self_xy, (record.world_x, record.world_y))


def _belief_self_xy(belief: Belief) -> tuple[int, int] | None:
    if belief.self_world_x is None or belief.self_world_y is None:
        return None
    return belief.self_world_x, belief.self_world_y


def _dist_sq(a: tuple[int, int] | None, b: tuple[int, int] | None) -> int | None:
    if a is None or b is None:
        return None
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _rounded_dist(dist_sq: int | None) -> float | None:
    return None if dist_sq is None else round(math.sqrt(dist_sq), 1)


def _rounded_p(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _button_names(mask: int) -> list[str]:
    buttons: list[str] = []
    for bit, name in (
        (0x01, "up"),
        (0x02, "down"),
        (0x04, "left"),
        (0x08, "right"),
        (BTN_A, "a"),
        (BTN_B, "b"),
    ):
        if mask & bit:
            buttons.append(name)
    return buttons


def _next_waypoint(action_state: ActionState) -> list[int] | None:
    if 0 <= action_state.route_cursor < len(action_state.route):
        return list(action_state.route[action_state.route_cursor])
    return None


def _viewer_map_payload(belief: Belief) -> dict[str, Any]:
    """Static map geometry for the browser viewer."""

    assert belief.map is not None
    return {
        "schema_version": 1,
        "width": belief.map.width,
        "height": belief.map.height,
        "home": belief.map.home.model_dump(mode="json"),
        "button": belief.map.button.model_dump(mode="json"),
        "rooms": [room.model_dump(mode="json") for room in belief.map.rooms],
        "tasks": [
            {"index": index, **task.model_dump(mode="json")}
            for index, task in enumerate(belief.map.tasks)
        ],
        "vents": [
            {"index": index, **vent.model_dump(mode="json")}
            for index, vent in enumerate(belief.map.vents)
        ],
    }


def _viewer_frame_payload(context: StepContext[Belief, ActionState, Intent, Command]) -> dict[str, Any]:
    """Live per-tick view model for replay inspection."""

    belief = context.belief
    witnessed = witnessed_imposters(belief)
    action_state = context.action_state
    route = [list(point) for point in action_state.route]
    next_waypoint = (
        list(action_state.route[action_state.route_cursor])
        if 0 <= action_state.route_cursor < len(action_state.route)
        else None
    )
    return {
        "schema_version": 1,
        "tick": context.tick,
        "phase": belief.phase,
        "role": belief.self_role,
        "camera": {
            "ready": belief.camera_ready,
            "x": belief.camera_x,
            "y": belief.camera_y,
            "width": SCREEN_WIDTH,
            "height": SCREEN_HEIGHT,
        },
        "self": _viewer_point(belief.self_world_x, belief.self_world_y),
        "mode": _viewer_mode_payload(context),
        "intent": _json_model(context.intent),
        "command": _json_model(context.command),
        "nav": {
            "target": _viewer_nav_target(context.intent, action_state),
            "route_goal": _point_list(action_state.route_goal),
            "route_cursor": action_state.route_cursor,
            "next_waypoint": next_waypoint,
            "route": route,
            "teleports": {str(key): value for key, value in action_state.route_teleports.items()},
        },
        "tasks": {
            "assigned": sorted(belief.assigned_task_indices),
            "visible": sorted(belief.visible_task_indices),
            "completed": sorted(belief.completed_task_indices),
            "crew_remaining": belief.crew_tasks_remaining,
            "active_progress_pct": belief.active_task_progress_pct,
        },
        "players": [
            {
                "color": color,
                "x": record.world_x,
                "y": record.world_y,
                "last_seen_tick": record.last_seen_tick,
                "life_status": record.life_status,
                "body_xy": _point_list(record.body_xy),
                "suspicion": round(belief.suspicion[color], 4) if color in belief.suspicion else None,
                "believed_imposter": color in belief.believed_imposters,
                "confirmed_imposter": color in witnessed,
                "teammate": color in belief.teammate_colors,
            }
            for color, record in sorted(belief.roster.items())
        ],
        "bodies": [
            {
                "id": body.object_id,
                "color": body.color,
                "x": body.world_x,
                "y": body.world_y,
                "visible": body.object_id in belief.visible_body_ids,
                "first_seen_tick": body.first_seen_tick,
            }
            for body in sorted(belief.bodies.values(), key=lambda item: item.object_id)
        ],
        "occupancy": _viewer_occupancy_payload(belief),
    }


def _viewer_mode_payload(context: StepContext[Belief, ActionState, Intent, Command]) -> dict[str, Any]:
    directive = context.active_directive
    return {
        "name": context.active_mode_name,
        "source": directive.source,
        "reason": directive.reason,
        "params_type": type(directive.params).__name__,
        "params": directive.params.model_dump(mode="json"),
        "issued_at_tick": directive.issued_at_tick,
        "ttl_ticks": directive.ttl_ticks,
        "age_ticks": max(0, context.tick - directive.issued_at_tick),
        "metadata": dict(directive.metadata),
    }


def _viewer_occupancy_payload(belief: Belief) -> dict[str, Any] | None:
    snapshot = belief.agent_tracking.snapshot
    if snapshot is None:
        return None
    teammate_snapshot = belief.agent_tracking.teammate_snapshot
    return {
        "tick": snapshot.tick,
        "top_cell": snapshot.top_cell,
        "top_point": _point_list(snapshot.top_point),
        "top_expected": round(snapshot.top_expected, 4),
        "tracked": snapshot.tracked_count,
        "support_cells": snapshot.support_cell_count,
        "cells": [
            [cell, round(expected, 4)]
            for cell, expected in sorted(snapshot.expected_by_cell.items())
            if expected > 0
        ],
        "teammate_cells": [
            [cell, round(expected, 4)]
            for cell, expected in sorted((teammate_snapshot.expected_by_cell if teammate_snapshot else {}).items())
            if expected > 0
        ],
        "agents": {
            color: {
                "age_ticks": estimate.age_ticks,
                "support_cells": estimate.support_cell_count,
                "top_cell": estimate.top_cell,
                "top_point": _point_list(estimate.top_point),
                "top_probability": round(estimate.top_probability, 4),
            }
            for color, estimate in sorted(belief.agent_tracking.estimates.items())
        },
    }


def _viewer_nav_target(intent: Intent, action_state: ActionState) -> list[int] | None:
    if action_state.route_goal is not None:
        return _point_list(action_state.route_goal)
    if intent.point is not None:
        return _point_list(intent.point)
    return None


def _viewer_point(x: int | None, y: int | None) -> dict[str, int] | None:
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def _point_list(point: tuple[int, int] | None) -> list[int] | None:
    return list(point) if point is not None else None


def _json_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value
