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
  ``believed_changed`` when the suspicion sets move, and a full ``suspicion_snapshot``
  (ranked posteriors + each suspect's event log + the would-be vote and the bar)
  at the start of every meeting. For the imposter, ``kill_ready_changed`` fires on
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
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from players.crewrift.crewborg.action import BTN_A, BTN_B
from players.crewrift.crewborg.perception.constants import SCREEN_HEIGHT, SCREEN_WIDTH
from players.crewrift.crewborg.strategy.opportunity import has_trackable_victim, kill_urgency_ticks
from players.crewrift.crewborg.strategy.rule_based import FLEE_ENTER_SQ, FLEE_EXIT_SQ, FLEE_STALE_TICKS
from players.crewrift.crewborg.strategy.meeting.schema import VOTE_SKIP
from players.crewrift.crewborg.strategy.meeting.vote_policy import fallback_vote, vote_bar
from players.crewrift.crewborg.strategy.suspicion import _prior_imposter_p
from players.crewrift.crewborg.trace import TraceConfig
from players.crewrift.crewborg.types import ActionState, Belief, Command, Intent, PlayerRecord
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
        episode_recorder: Any | None = None,
    ) -> None:
        # Optional artifact recorder (duck-typed ``SqliteEpisodeRecorder``): when
        # present, the tracer streams one per-tick row into the artifact's
        # ``positions`` table and pushes episode metadata (role / outcome) into
        # ``summary.json``. Best-effort: the recorder is never required.
        self._episode_recorder = episode_recorder
        # Previous-tick state for edge/delta detection. ``phase`` starts at the
        # Belief default so the first real transition (unknown → …) is reported.
        self._phase: str = "unknown"
        self._role: str | None = None
        self._seen_body_ids: set[int] = set()
        self._completed_task_indices: set[int] = set()
        self._last_kill_tick: int | None = None
        self._vote_confirmed: bool = False
        self._vote_cast_meeting_id: int | None = None  # one vote_cast per meeting
        self._started_task_index: int | None = None
        # Last known self world fix (belief's own copy goes None during meetings,
        # when the camera is torn down): the spatial annotation for every domain
        # event + the meeting-call/kill geometry payloads.
        self._self_x: int | None = None
        self._self_y: int | None = None
        self._room_id: int | None = None
        # The most recent kill attempt's geometry, folded into kill_landed.
        self._last_kill_attempt: dict[str, Any] | None = None
        self._meeting_call_id: int | None = None  # one meeting_called per meeting
        self._game_over_emitted: bool = False
        self._episode_color: str | None = None

        # Knowledge-layer delta state (per color where noted).
        self._event_counts: dict[str, int] = {}  # color → events logged so far (emit the new tail)
        self._life: dict[str, str] = {}  # color → last-seen life_status (alive→dead edge)
        self._confirmed: set[str] = set()  # last confirmed_imposters (witnessed catches)
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

    def __call__(self, context: StepContext[Belief, ActionState, Intent, Command]) -> None:
        belief = context.belief
        self._update_self_fix(belief)
        # Every domain event is spatially annotated (self_x / self_y / room_id)
        # via this wrapper, so analysis can place any event on the map without
        # joining against the positions table.
        emit = _SpatialEmitter(context.emit, self._self_x, self._self_y, self._room_id)
        self._observe_phase(belief, emit)
        self._observe_role(belief, emit)
        self._observe_bodies(belief, emit)
        self._observe_completed_tasks(belief, emit)
        self._observe_kill_landed(belief, emit)
        self._observe_vote(belief, context.action_state, emit)
        self._observe_action(belief, context.intent, context.command, emit)
        self._observe_meeting_called(belief, emit)
        self._observe_game_over(belief, emit)
        self._observe_chat_received(belief, emit)
        self._record_position(context)
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

    def _optional_event_enabled(self, event_name: str, mode_enabled: bool) -> bool:
        enabled_by_mode = mode_enabled and not self._trace_config.excludes_event(event_name)
        return enabled_by_mode or self._trace_config.targets_event(event_name)

    # --- state-transition / outcome events (belief & action-state deltas) ---

    def _observe_phase(self, belief: Belief, emit: EventEmitter) -> None:
        if belief.phase != self._phase:
            emit.event("phase_change", {"from": self._phase, "to": belief.phase})
            self._phase = belief.phase

    def _observe_role(self, belief: Belief, emit: EventEmitter) -> None:
        if self._role is None and belief.self_role is not None:
            self._role = belief.self_role
            emit.event("role_resolved", {"role": belief.self_role})
            self._set_episode_info(role=belief.self_role)
        if self._episode_color is None and belief.voting.self_marker_color is not None:
            self._episode_color = belief.voting.self_marker_color
            self._set_episode_info(color=self._episode_color)

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
            payload: dict[str, Any] = {
                "world_x": belief.self_world_x,
                "world_y": belief.self_world_y,
            }
            # Fold in the geometry captured at the most recent kill attempt (the A
            # press that landed this kill): victim identity/position, the strike
            # distance, how long the kill had been ready, and witnesses in LOS.
            if self._last_kill_attempt is not None:
                payload.update(self._last_kill_attempt)
            emit.event("kill_landed", payload)
            emit.counter("kill_landed")

    def _observe_vote(self, belief: Belief, action_state: ActionState, emit: EventEmitter) -> None:
        # vote_confirmed flips False→True the tick the vote is cast. The action
        # layer resets it whenever the intent changes — and meeting modes cycle
        # vote→idle→vote intents — so a raw edge detector fires dozens of times
        # per meeting. A vote is final once cast (the server ignores re-presses),
        # so latch on the meeting id and report exactly one cast per meeting.
        meeting_id = belief.phase_start_tick if belief.phase == "Voting" else None
        if meeting_id is None:
            self._vote_confirmed = action_state.vote_confirmed
            return
        if (
            action_state.vote_confirmed
            and not self._vote_confirmed
            and self._vote_cast_meeting_id != meeting_id
        ):
            self._vote_cast_meeting_id = meeting_id
            emit.event("vote_cast", {"meeting_id": meeting_id})
            emit.counter("vote_cast")
        self._vote_confirmed = action_state.vote_confirmed

    # --- attempt events (intent + the wire command it produced) -------------

    def _observe_action(
        self, belief: Belief, intent: Intent, command: Command, emit: EventEmitter
    ) -> None:
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
            # Kill intents target by color (the roster key); the object id is
            # resolved from the roster. Geometry is captured here — at the actual
            # strike press — and reused to enrich the kill_landed outcome.
            payload = self._kill_attempt_payload(belief, intent)
            self._last_kill_attempt = dict(payload)
            emit.event("kill_attempted", payload)
            emit.counter("kill_attempted")
        elif kind == "report" and command.held_mask & BTN_A:
            emit.event("report_attempted", {"body_id": intent.target_id})
            emit.counter("report_attempted")
        elif kind in ("vent", "escape") and command.held_mask & BTN_B:
            # ``escape`` presses B only on a vent teleport leg, so a B edge here is a
            # vent use just like the dedicated ``vent`` intent.
            emit.event("vent_attempted", {})
            emit.counter("vent_attempted")

    def _kill_attempt_payload(self, belief: Belief, intent: Intent) -> dict[str, Any]:
        """The strike's geometry: victim fix, distance, readiness age, witnesses."""

        victim = belief.roster.get(intent.target_color) if intent.target_color is not None else None
        victim_xy = (victim.world_x, victim.world_y) if victim is not None else None
        self_xy = _belief_self_xy(belief)
        return {
            "target_color": intent.target_color,
            "target_id": victim.object_id if victim is not None else intent.target_id,
            "victim_x": victim_xy[0] if victim_xy is not None else None,
            "victim_y": victim_xy[1] if victim_xy is not None else None,
            "dist": _rounded_dist(_dist_sq(self_xy, victim_xy)),
            "ticks_since_ready": kill_urgency_ticks(belief),
            "witnesses": _witnesses_in_los(belief, intent.target_color, victim_xy),
        }

    def _observe_meeting_called(self, belief: Belief, emit: EventEmitter) -> None:
        """Emit who opened each meeting (and how), once per meeting.

        Sourced from the meeting-call interstitial (upstream 2026-06-10): the
        caller's identity and the report/button trigger were previously
        unobservable. The spatial annotation carries our last Playing-phase
        position, i.e. where we were when the meeting interrupted us.
        """

        if belief.phase != "MeetingCall":
            return
        if belief.phase_start_tick == self._meeting_call_id:
            return
        self._meeting_call_id = belief.phase_start_tick
        emit.event(
            "meeting_called",
            {
                "by": belief.meeting_called_by,
                "trigger": belief.meeting_trigger,
                "body_color": belief.meeting_reported_body_color,
            },
        )
        emit.counter("meeting_called", tags={"trigger": belief.meeting_trigger or "unknown"})

    def _observe_game_over(self, belief: Belief, emit: EventEmitter) -> None:
        """Emit the episode outcome once, when the GameOver screen names it.

        ``alive_by_color`` is the roster's final alive/dead view; ``roles`` is the
        end-of-game ground-truth role census paired off the GameOver screen
        (color → imposter/crewmate), when the server provides it.
        """

        if self._game_over_emitted or belief.game_outcome is None:
            return
        self._game_over_emitted = True
        emit.event(
            "game_over",
            {
                "outcome": belief.game_outcome,
                "alive_by_color": {
                    color: record.life_status != "dead"
                    for color, record in sorted(belief.roster.items())
                },
                "roles": dict(sorted(belief.game_over_roles.items())),
            },
        )
        emit.counter("game_over", tags={"outcome": belief.game_outcome})
        self._set_episode_info(outcome=belief.game_outcome)

    def _update_self_fix(self, belief: Belief) -> None:
        """Track the last known self position + room (belief's copy goes None
        whenever the camera is down, e.g. during meetings)."""

        if belief.self_world_x is None or belief.self_world_y is None:
            return
        self._self_x = belief.self_world_x
        self._self_y = belief.self_world_y
        self._room_id = _room_index(belief, self._self_x, self._self_y)

    def _set_episode_info(self, **fields: Any) -> None:
        recorder = self._episode_recorder
        if recorder is None:
            return
        set_info = getattr(recorder, "set_episode_info", None)
        if set_info is not None:
            set_info(**fields)

    def _record_position(self, context: StepContext[Belief, ActionState, Intent, Command]) -> None:
        """Stream one per-tick row into the artifact's ``positions`` table.

        The compact ``visible`` column is this tick's seen players from the
        perception tape (empty when the camera is down, e.g. meetings).
        """

        recorder = self._episode_recorder
        if recorder is None:
            return
        record_position = getattr(recorder, "record_position", None)
        if record_position is None:
            return
        belief = context.belief
        frame = belief.recent_frames[-1] if belief.recent_frames else None
        if frame is not None and frame.tick == belief.last_tick:
            visible = [{"c": c, "x": p[0], "y": p[1]} for c, p in sorted(frame.players.items())]
        else:
            visible = []
        record_position(
            tick=context.tick,
            server_tick=belief.server_tick,
            self_x=belief.self_world_x,
            self_y=belief.self_world_y,
            room_id=self._room_id if belief.self_world_x is not None else None,
            mode=context.active_mode_name,
            intent_kind=context.intent.kind,
            held_mask=context.command.held_mask,
            phase=belief.phase,
            visible=json.dumps(visible, separators=(",", ":")),
        )

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

        for color in sorted(belief.confirmed_imposters - self._confirmed):
            emit.event("imposter_confirmed", {"color": color, "p": round(belief.suspicion.get(color, 1.0), 4)})
            emit.counter("imposter_confirmed")
        self._confirmed = set(belief.confirmed_imposters)

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
        (``fallback_vote`` against the same state-dependent vote bar Attend
        Meeting uses) — the one record that explains a meeting's vote after the
        fact.
        """

        if belief.phase != "Voting":
            self._meeting_snapshotted = False
            return
        if self._meeting_snapshotted:
            return
        self._meeting_snapshotted = True
        # Suspicion is crewmate-only (cleared for imposter/ghost), so nothing to show otherwise.
        if not belief.suspicion:
            return
        fallback = fallback_vote(belief)
        target = fallback if fallback != VOTE_SKIP else None
        ranking = [
            {
                "color": color,
                "p": round(p, 4),
                "confirmed": color in belief.confirmed_imposters,
                "events": _event_summary(belief.roster.get(color)),
            }
            for color, p in sorted(belief.suspicion.items(), key=lambda kv: kv[1], reverse=True)
        ]
        emit.event(
            "suspicion_snapshot",
            {
                "prior": round(_prior_imposter_p(belief), 4),
                "ranking": ranking,
                "confirmed": sorted(belief.confirmed_imposters),
                "believed": sorted(belief.believed_imposters),
                "would_vote": target,
                "would_vote_p": (
                    round(belief.suspicion[target], 4)
                    if target is not None and target in belief.suspicion
                    else None
                ),
                "vote_bar": vote_bar(belief),
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


class _SpatialEmitter:
    """EventEmitter wrapper that spatially annotates every domain event payload.

    Adds ``self_x`` / ``self_y`` / ``room_id`` (the tracer's last known self fix —
    kept through meetings, when belief's live copy is None) to each ``event``
    payload without overwriting fields the event already carries. Counters /
    gauges / histograms pass through untouched.
    """

    def __init__(self, inner: EventEmitter, self_x: int | None, self_y: int | None, room_id: int | None) -> None:
        self._inner = inner
        self._fields = {"self_x": self_x, "self_y": self_y, "room_id": room_id}

    def event(self, name: str, data: dict[str, Any] | None = None) -> None:
        payload = dict(data or {})
        for key, value in self._fields.items():
            payload.setdefault(key, value)
        self._inner.event(name, payload)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _room_index(belief: Belief, x: int, y: int) -> int | None:
    """The baked-map room index containing ``(x, y)``, or ``None``."""

    if belief.map is None:
        return None
    for index, room in enumerate(belief.map.rooms):
        if room.x <= x < room.x + room.w and room.y <= y < room.y + room.h:
            return index
    return None


# Another player this close to the victim (world px) is considered able to see a
# kill — matches the imposter's own zero-urgency isolation radius (opportunity.py).
WITNESS_LOS_RADIUS_SQ = 48**2


def _witnesses_in_los(
    belief: Belief, victim_color: str | None, victim_xy: tuple[int, int] | None
) -> list[dict[str, Any]]:
    """Players visible in our LOS this tick, with their distance to the victim.

    Sourced from the latest perception-tape frame (players are only rendered in
    line of sight, so presence in the frame implies LOS). ``near`` flags the ones
    within witness range of the victim.
    """

    frame = belief.recent_frames[-1] if belief.recent_frames else None
    if frame is None or frame.tick != belief.last_tick:
        return []
    witnesses: list[dict[str, Any]] = []
    for color, (x, y) in sorted(frame.players.items()):
        if color == victim_color:
            continue
        dist_sq = _dist_sq((x, y), victim_xy)
        witnesses.append(
            {
                "color": color,
                "dist": _rounded_dist(dist_sq),
                "near": dist_sq is not None and dist_sq <= WITNESS_LOS_RADIUS_SQ,
                "teammate": color in belief.teammate_colors,
            }
        )
    return witnesses


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
        "flee": _decision_flee_payload(context, visible_colors),
        "nav": {
            "route_goal": _point_list(action_state.route_goal),
            "route_cursor": action_state.route_cursor,
            "route_len": len(action_state.route),
            "next_waypoint": _next_waypoint(action_state),
        },
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
                "confirmed_imposter": color in belief.confirmed_imposters,
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
    threat_colors = belief.believed_imposters | belief.confirmed_imposters
    return [
        _decision_player_threat_payload(belief, color, visible_colors)
        for color in sorted(threat_colors)
    ]


def _decision_player_threat_payload(
    belief: Belief, color: str, visible_colors: set[str]
) -> dict[str, Any]:
    record = belief.roster.get(color)
    dist_sq = _record_dist_sq(belief, record)
    age_ticks = None if record is None else max(0, belief.last_tick - record.last_seen_tick)
    return {
        "color": color,
        "p": _rounded_p(belief.suspicion.get(color)),
        "believed": color in belief.believed_imposters,
        "confirmed": color in belief.confirmed_imposters,
        "visible": color in visible_colors,
        "life_status": record.life_status if record is not None else None,
        "last_seen_tick": record.last_seen_tick if record is not None else None,
        "age_ticks": age_ticks,
        "xy": [record.world_x, record.world_y] if record is not None else None,
        "dist": _rounded_dist(dist_sq),
        "dist_sq": dist_sq,
        "flee_enter": dist_sq is not None and dist_sq <= FLEE_ENTER_SQ,
        "flee_continue": dist_sq is not None and dist_sq <= FLEE_EXIT_SQ,
        "flee_stale": age_ticks is not None and age_ticks > FLEE_STALE_TICKS,
        "thresholds": {
            "enter_sq": FLEE_ENTER_SQ,
            "exit_sq": FLEE_EXIT_SQ,
            "stale_ticks": FLEE_STALE_TICKS,
        },
    }


def _decision_task_payload(context: StepContext[Belief, ActionState, Intent, Command]) -> dict[str, Any] | None:
    intent = context.intent
    belief = context.belief
    if intent.kind not in {"complete_task", "navigate_to_noclip"} or intent.task_index is None:
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
    if intent.kind == "navigate_to_noclip" and intent.point is not None:
        goal = intent.point
    else:
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


def _decision_flee_payload(
    context: StepContext[Belief, ActionState, Intent, Command], visible_colors: set[str]
) -> dict[str, Any] | None:
    intent = context.intent
    belief = context.belief
    target_color = intent.target_color if intent.kind == "flee_from" else None
    if target_color is None and not belief.believed_imposters:
        return None

    target = belief.roster.get(target_color) if target_color is not None else None
    self_xy = _belief_self_xy(belief)
    target_xy = (target.world_x, target.world_y) if target is not None else None
    away = (
        (2 * self_xy[0] - target_xy[0], 2 * self_xy[1] - target_xy[1])
        if self_xy is not None and target_xy is not None
        else None
    )
    dist_sq = _dist_sq(self_xy, target_xy) if self_xy is not None and target_xy is not None else None
    return {
        "active": intent.kind == "flee_from",
        "target_color": target_color,
        "target_visible": target_color in visible_colors if target_color is not None else None,
        "target_last_seen_tick": target.last_seen_tick if target is not None else None,
        "target_age_ticks": (
            max(0, belief.last_tick - target.last_seen_tick) if target is not None else None
        ),
        "target_xy": list(target_xy) if target_xy is not None else None,
        "away_point": _point_list(away),
        "target_dist": _rounded_dist(dist_sq),
        "target_dist_sq": dist_sq,
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
                "confirmed_imposter": color in belief.confirmed_imposters,
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
