"""Crewborg's SDK type parameters and the three pure perception functions.

Crewborg supplies the six ``AgentRuntime`` type parameters
(``Observation``/``Percept``/``Belief``/``ActionState``/``Intent``/``Command``)
plus ``perceive``/``update_belief`` here, and ``resolve_action`` in
:mod:`players.crewrift.crewborg.action`. See ``design.md`` §2.

Style (design §2): SDK-facing types are pydantic models — frozen where the value
is immutable (``Percept``/``Intent``/``Command``), mutable where the loop folds
state in place (``Belief``/``ActionState``). ``SceneState`` is the lone exception:
a plain dataclass owned by the bridge holding raw buffers that never reach the
strategy.

``perceive`` resolves the scene into a structured
:class:`~.perception.entities.ResolvedScene` (including self world position), and
``update_belief`` folds it into belief's self / roster / bodies / tasks / phase /
voting / evidence sections and builds the nav graph once from the walkability mask
(design §4-§6). The modes + action layer drive both roles: crewmate tasks,
meetings, voting, reporting, and fleeing, plus imposter hunting, venting, and
blending in.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from players.crewrift.crewborg.agent_tracking import AgentTrackingState
from players.crewrift.crewborg.coworld.scene import SceneState
from players.crewrift.crewborg.map.types import MapData
from players.crewrift.crewborg.nav import NavGraph, build_nav_graph
from players.crewrift.crewborg.perception.constants import PLAYER_OBJECT_BASE
from players.crewrift.crewborg.perception.entities import ResolvedScene, VotingState
from players.crewrift.crewborg.perception.resolve import resolve_scene

# The shared intent vocabulary (design §8). One vocabulary serves both roles;
# modes differ only in which they emit.
IntentKind = Literal[
    "idle",
    "loiter",
    "navigate_to",
    "navigate_to_noclip",
    "flee_from",
    "complete_task",
    "report",
    "call_meeting",
    "vote",
    "chat",
    "kill",
    "vent",
    "escape",
]

# Game phases (sim.nim phase machine). ``unknown`` until the first phase signal.
# ``GameInfo`` (pre-game settings screen, before RoleReveal) and ``MeetingCall``
# (the 3 s "who called this meeting" screen between Playing and Voting) were added
# upstream 2026-06-10; older servers never produce them.
Phase = Literal[
    "unknown",
    "Lobby",
    "GameInfo",
    "RoleReveal",
    "Playing",
    "MeetingCall",
    "Voting",
    "VoteResult",
    "GameOver",
]


class Observation(BaseModel):
    """Thin frozen wrapper holding a reference to the bridge's live scene + tick.

    Byte-level decoding happens in the bridge; ``perceive`` does interpretation
    only (design §3).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    scene: SceneState
    tick: int


class Percept(BaseModel):
    """Resolved per-tick view of the scene (design §4).

    ``walkability`` is the decoded mask held by reference (not copied): it is
    static for the episode, so ``update_belief`` builds the nav graph from it once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    tick: int
    messages_applied: int
    resolved: ResolvedScene
    walkability: np.ndarray | None = None
    # Screen-space line-of-sight mask (True ⇒ visible), aligned to this tick's
    # camera; held by reference. ``None`` until the ``shadow`` overlay arrives.
    visible_mask: np.ndarray | None = None


# How many recent sightings to keep per player (design §5). A trajectory tail —
# enough to read heading/velocity and to recover the crew when sight is lost,
# without unbounded growth.
ROSTER_HISTORY_MAX = 64


# A player's life status. ``unknown`` until we have seen them alive in-world or
# learned their state from the meeting census / a body / an ejection.
LifeStatus = Literal["alive", "dead", "unknown"]
# How we learned a player died — an in-world body, the meeting census, the
# vote-result ejection, or the meeting-call interstitial naming the reported body.
DeathSource = Literal["body", "census", "ejection", "report"]

# An observed interaction we logged about another player (design §5.2). All are
# **durative**: an interval of contiguous observation. ``room``/``task``/``vent``
# carry a ``region_index``; ``near_body``/``proximity`` carry a ``target_color``
# (the body's color / the other player) and a ``min_dist`` closest approach.
PlayerEventKind = Literal["room", "task", "vent", "near_body", "proximity"]


class PlayerEvent(BaseModel):
    """One observed interval about a player — the unit of their event log (§5.2).

    Raw observation, not interpretation: compound signals ("followed Y, who then
    died") are *queries* over events + life-status, not stored here. Duration is the
    *observed* span (contiguous line of sight), i.e. "seen doing X for ≥ this long".
    """

    model_config = ConfigDict(extra="forbid")

    kind: PlayerEventKind
    start_tick: int
    end_tick: int
    target_color: str | None = None  # near_body → body's color; proximity → other player
    region_index: int | None = None  # room/task/vent index into the baked map
    min_dist: int | None = None  # closest approach (world px) for near_body / proximity

    @property
    def duration_ticks(self) -> int:
        return self.end_tick - self.start_tick + 1


class PlayerRecord(BaseModel):
    """Everything known about one player, keyed by **color** (design §5).

    Color is the only identity stable across every Crewrift namespace (in-world
    sprites, bodies, chat icons, vote markers) and is unique per player, so it is
    the canonical key. ``object_id`` (``PLAYER_OBJECT_BASE + joinOrder``) is the
    live-world handle, learned the first time we see the player alive.

    The fix fields — ``world_x``/``world_y``/``facing``/``last_seen_tick`` and the
    bounded ``history`` trail — are written **only from live "player <color>"
    sightings**, so they are exactly *"the last time and place I saw this player
    alive"*. When the player dies we flip ``life_status`` and record the death but
    leave the alive-fix untouched, connecting the two halves on one record.
    """

    model_config = ConfigDict(extra="forbid")

    color: str
    object_id: int | None = None
    facing: str = "right"
    world_x: int = 0
    world_y: int = 0
    last_seen_tick: int = 0
    history: list[tuple[int, int, int]] = Field(default_factory=list)

    life_status: LifeStatus = "unknown"
    # When (and how) we learned this player is dead; ``None`` while alive/unknown.
    death_seen_tick: int | None = None
    death_source: DeathSource | None = None
    # Where the body was, if we saw it in-world (``None`` if death was learned only
    # from the census / an ejection).
    body_xy: tuple[int, int] | None = None

    # The observation event log for this player (§5.2): the full-game timeline of
    # durative interactions we saw them in, maintained by ``strategy.event_log``
    # (unbounded — a game produces few intervals). ``last_event_tick`` is that
    # logger's bookkeeping: the previous tick it processed this player, used to tell
    # a brief unobserved gap (bridge) from an observed departure (split).
    events: list[PlayerEvent] = Field(default_factory=list)
    last_event_tick: int = 0

    @property
    def join_order(self) -> int | None:
        """The player's joinOrder, recovered from the live-world object id."""

        return None if self.object_id is None else self.object_id - PLAYER_OBJECT_BASE

    def record(self, tick: int, x: int, y: int, facing: str, object_id: int) -> None:
        """Fold a fresh **live** sighting into the alive-fix and the bounded trail."""

        self.facing = facing
        self.world_x = x
        self.world_y = y
        self.last_seen_tick = tick
        self.object_id = object_id
        self.life_status = "alive"
        self.history.append((tick, x, y))
        if len(self.history) > ROSTER_HISTORY_MAX:
            del self.history[0]

    def mark_dead(
        self, tick: int, source: DeathSource, body_xy: tuple[int, int] | None = None
    ) -> None:
        """Record that this player is dead, preserving the last-seen-alive fix.

        Idempotent: the first death signal wins (the alive-fix and the original
        ``death_seen_tick``/``death_source`` are kept), but a later in-world body
        sighting fills in ``body_xy`` if we only knew of the death abstractly.
        """

        if self.life_status != "dead":
            self.life_status = "dead"
            self.death_seen_tick = tick
            self.death_source = source
        if body_xy is not None and self.body_xy is None:
            self.body_xy = body_xy


class BodyEntry(BaseModel):
    """A dead body the agent has seen (design §5 bodies)."""

    model_config = ConfigDict(extra="forbid")

    object_id: int
    color: str
    world_x: int
    world_y: int
    first_seen_tick: int


class ChatEvent(BaseModel):
    """One chat message heard during a meeting (design §5 chat).

    ``speaker_color`` is the speaker's player color (``None`` if the line could not
    be attributed); ``tick`` is when we first observed the message.
    """

    model_config = ConfigDict(extra="forbid")

    tick: int
    speaker_color: str | None
    text: str


class Accusation(BaseModel):
    """One parsed who-sus'd-who edge from meeting chat (design §5 social).

    Produced by :func:`...strategy.meeting.social.parse_stances` over each new
    chat line and accumulated for the whole episode (unlike ``chat_log``, which
    is cleared per meeting): accusation patterns across meetings are evidence —
    imposters scapegoat crew and defend each other.
    """

    model_config = ConfigDict(extra="forbid")

    meeting_id: int  # the meeting's phase_start_tick
    tick: int
    speaker_color: str | None  # None if the line could not be attributed
    target_color: str
    stance: Literal["accuse", "defend"]
    text: str
    # Whether the line carried evidence wording (a body / vent / sighting…) per
    # :func:`...strategy.meeting.social.has_evidence_context`. A bare ``"<color>
    # sus"`` assertion (``False``) is treated as disinfo by the suspicion model
    # and never qualifies for the vote policy's pile-on (2026-06-11 truecrew
    # eval: 0/185 bare-sus lines named a real imposter).
    has_evidence: bool = False


class MeetingRecord(BaseModel):
    """The outcome of one finished (or in-progress) meeting (design §5 social).

    ``votes`` maps voter color → target color (or ``"skip"``), refreshed from the
    live vote dots every Voting tick so the record naturally freezes at the final
    tally when the meeting closes. ``ejected_color`` is filled from the
    vote-result interstitial. ``called_by``/``trigger``/``reported_body_color``
    come from the meeting-call interstitial (upstream 2026-06-10) — who opened
    the meeting and how; ``None`` on older servers. Episode-persistent: who voted
    with whom — and whether that vote landed on a later-confirmed imposter — is
    evidence.
    """

    model_config = ConfigDict(extra="forbid")

    meeting_id: int  # the meeting's phase_start_tick
    votes: dict[str, str] = Field(default_factory=dict)
    ejected_color: str | None = None
    called_by: str | None = None
    trigger: Literal["report", "button"] | None = None
    reported_body_color: str | None = None
    # Playing ticks elapsed in the interrupted segment when the meeting-call
    # interstitial opened (None on older servers / when the anchor was unknown).
    # Caller timing is a behavioral fingerprint: sussyboi's imposter presses the
    # button at offsets ~150, its crew at ~500 (strategy.hunter).
    called_elapsed_ticks: int | None = None


# How many recent raw observation frames the perception tape keeps (~1 s at 24 Hz).
RECENT_FRAMES_MAX = 24


class PerceptionFrame(BaseModel):
    """One frame of raw observations — the "perception tape" (design §5.1).

    Distinct from the interpreted aggregates (``roster`` / ``bodies``): this is
    *what we saw* on a single camera-ready frame, including the camera viewport so
    consumers know **what we could see** (a region absent from ``players`` is only
    meaningfully "clear" if it was inside the viewport). Frame-to-frame transition
    detection (kills, vents) reads the tape; occupancy/adjacency are pure functions
    over it (``strategy.occupancy``), never stored.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    tick: int
    camera_x: int
    camera_y: int
    # Alive players visible this frame: color → collision (x, y).
    players: dict[str, tuple[int, int]] = Field(default_factory=dict)
    # Bodies visible this frame: color → collision (x, y).
    bodies: dict[str, tuple[int, int]] = Field(default_factory=dict)
    # Screen-space line-of-sight mask (True ⇒ visible) for this frame's camera, held
    # by reference (``None`` before the ``shadow`` overlay arrives). Lets occupancy
    # predicates use true LoS instead of mere viewport containment.
    visible_mask: np.ndarray | None = None


class Belief(BaseModel):
    """Persistent world model — the only interface the strategy and modes see."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    # Loop bookkeeping.
    last_tick: int = 0
    ticks_observed: int = 0
    messages_applied: int = 0

    # Static map, baked once at startup (design §6).
    map: MapData | None = None
    # Navigation graph over the streamed walkability mask, built once (design §6).
    nav: NavGraph | None = None

    # Self / camera (design §5 self).
    camera_ready: bool = False
    camera_x: int = 0
    camera_y: int = 0
    self_role: str | None = None
    self_kill_ready: bool | None = None
    self_world_x: int | None = None
    self_world_y: int | None = None

    # Tasks (design §5 tasks).
    assigned_task_indices: set[int] = Field(default_factory=set)
    visible_task_indices: set[int] = Field(default_factory=set)
    completed_task_indices: set[int] = Field(default_factory=set)
    crew_tasks_remaining: int | None = None
    active_task_progress_pct: int | None = None

    # Perception tape (design §5.1): a bounded ring of recent raw observation
    # frames (oldest first), appended only on camera-ready frames. The substrate
    # for frame-to-frame transition detection; aggregates below are folded from it.
    recent_frames: list[PerceptionFrame] = Field(default_factory=list)
    # Probabilistic per-agent location tracker (docs/designs/agent-tracking.md):
    # static occupancy substrate plus per-player reachability-disc beliefs.
    agent_tracking: AgentTrackingState = Field(default_factory=AgentTrackingState)

    # Roster + bodies (design §5). The roster is keyed by player **color** — the
    # one identity stable and unique across every Crewrift namespace — and carries
    # each player's live/dead status (see ``PlayerRecord``). ``bodies`` stays keyed
    # by body object id for the report path; deaths are also reflected onto the
    # roster (by color) so "last seen alive" and "now dead" live on one record.
    roster: dict[str, PlayerRecord] = Field(default_factory=dict)
    total_player_count: int = 0
    bodies: dict[int, BodyEntry] = Field(default_factory=dict)
    visible_body_ids: set[int] = Field(default_factory=set)  # bodies in view this tick

    # Phase machine (design §5 phase).
    phase: Phase = "unknown"
    phase_start_tick: int = 0

    # The server's authoritative tick, read off the per-tick "tick <N>" marker
    # sprite (upstream 2026-06-10). ``None`` on older servers. This is the same
    # tick counter the server's .bitreplay uses, so it joins crewborg's trace to
    # the replay timeline; the runtime's own ``last_tick`` (bridge message count)
    # can drift from it when frames are dropped or arrive before the game loop.
    server_tick: int | None = None

    # Live game config learned from the pre-game GAME INFO interstitial (upstream
    # 2026-06-10): the actual killCooldownTicks / tasksPerPlayer / voteTimerTicks /
    # maxTicks this episode runs with. ``None`` until seen (older servers never
    # show the screen). Consumers fall back to conservative defaults when unset.
    kill_cooldown_config_ticks: int | None = None
    tasks_per_player: int | None = None
    vote_timer_ticks: int | None = None
    game_max_ticks: int | None = None

    # Who opened the current/most recent meeting, and how — from the meeting-call
    # interstitial (upstream 2026-06-10; previously unobservable). Latched when the
    # MeetingCall screen shows and kept through Voting/VoteResult so suspicion /
    # analysis can attribute the meeting; reset when the next MeetingCall opens.
    # ``meeting_trigger`` is "report" | "button"; ``meeting_reported_body_color``
    # is the reported body's color for report-triggered meetings.
    meeting_called_by: str | None = None
    meeting_trigger: Literal["report", "button"] | None = None
    meeting_reported_body_color: str | None = None
    # Playing ticks elapsed in the interrupted segment when the current meeting's
    # call interstitial opened (the kill-cooldown anchor is reset every meeting →
    # Playing transition, so this is the caller's button/report *segment offset*).
    meeting_call_elapsed_ticks: int | None = None

    # Game outcome, read from the GameOver interstitial title text. ``None`` until
    # the game ends.
    game_outcome: Literal["draw", "crew_wins", "imps_win"] | None = None
    # End-of-game ground-truth roles by color, paired from the GameOver roster
    # icons + IMP/CREW texts: color → "imposter" | "crewmate".
    game_over_roles: dict[str, str] = Field(default_factory=dict)

    # Voting (design §5 voting).
    voting: VotingState = Field(default_factory=VotingState)

    # Chat heard during the current meeting (design §5 chat), de-duplicated across
    # ticks and cleared when a new meeting opens. The raw transcript suspicion
    # reasoning will consume.
    chat_log: list[ChatEvent] = Field(default_factory=list)

    # Episode-persistent social history (design §5 social): the who-sus'd-who
    # accusation graph parsed from every meeting's chat, and each meeting's final
    # vote tally + ejection. Never cleared — cross-meeting accusation and voting
    # patterns feed the suspicion model and the meeting LLM context.
    accusations: list[Accusation] = Field(default_factory=list)
    meeting_history: list[MeetingRecord] = Field(default_factory=list)

    # Social / evidence (design §5, §10.1). Bayesian: ``suspicion[color]`` is the
    # posterior **P(imposter)** ∈ [0, 1] for each other player (crewmate POV),
    # recomputed each tick from a combinatorial prior + likelihood-ratio updates for
    # observed evidence. ``confirmed_imposters`` are colors caught by the near-certain
    # detectors (witnessed kill/vent), contributed as overwhelming-LR evidence;
    # ``believed_imposters`` is the derived set over the flee probability (drives
    # Flee). ``imposter_count`` (K) overrides the player-count-derived default.
    confirmed_imposters: set[str] = Field(default_factory=set)
    suspicion: dict[str, float] = Field(default_factory=dict)
    believed_imposters: set[str] = Field(default_factory=set)
    imposter_count: int | None = None
    # Imposter teammates' colors, learned from the role-reveal icons (design §7.2),
    # so Hunt never targets a fellow imposter (the server's kill skips them).
    teammate_colors: set[str] = Field(default_factory=set)

    # Follower ("tail") tracking (crewmate survival): per-color shadowing streaks
    # as ``color -> (streak_start_tick, last_within_tick)`` for players recently
    # observed within tail range of us. 34/46 of our crewmate deaths in the
    # 2026-06-11 replay reconstruction were 6-12 s shadow kills with the killer
    # inside our LoS the whole approach; maintained each Playing tick by
    # ``strategy.shadow.update_tail_tracking`` and read by ``active_tail``.
    tail_streaks: dict[str, tuple[int, int]] = Field(default_factory=dict)

    # Imposter: tick of the most recent self kill (kill-ready → cooldown edge),
    # surfaced in the kill-readiness trace (events.py §11).
    last_kill_tick: int | None = None
    # Imposter: tick the kill last became ready (cooldown → kill-ready edge), reset
    # to ``None`` whenever the kill is on cooldown. The strategy reads
    # ``last_tick - kill_ready_since_tick`` as a "how long have I been able to kill
    # without doing so" urgency signal that loosens the kill-opportunity bar over
    # time (design §10).
    kill_ready_since_tick: int | None = None
    # Kill cooldown timing. Imposters use it to pre-position before a kill is ready;
    # crewmates use the same estimate for button-timing experiments. The HUD gives
    # only imposters a binary ready/cooldown icon, so ``kill_cooldown_start_tick`` is
    # also set from the globally observable first Playing tick after role-reveal or
    # a meeting, since both reset killCooldown. ``kill_cooldown_estimate`` is learned
    # only when we can watch an imposter cooldown run to ready.
    kill_cooldown_start_tick: int | None = None
    kill_cooldown_estimate: int | None = None


class Intent(BaseModel):
    """Symbolic "what to do now" (design §8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: IntentKind = "idle"
    point: tuple[int, int] | None = None
    # A player-identity target (the roster key) for ``kill`` / ``flee_from``.
    target_color: str | None = None
    # A body object id for ``report`` (bodies stay keyed by object id).
    target_id: int | None = None
    task_index: int | None = None
    text: str | None = None
    reason: str = ""


class ActionState(BaseModel):
    """Action-layer execution state, mutated in place across ticks (design §9)."""

    model_config = ConfigDict(extra="forbid")

    held_mask: int = 0
    # The intent currently being executed, for diffing against the next tick's.
    current_intent: Intent | None = None
    # Active nav route (world waypoints) + cursor to the next unreached waypoint.
    route: list[tuple[int, int]] = Field(default_factory=list)
    route_cursor: int = 0
    route_goal: tuple[int, int] | None = None
    # Ticks since the route was last (re)planned; drives periodic re-rooting so the
    # follower doesn't commit to a stale route after drifting off the planned line.
    ticks_since_plan: int = 0
    # For a vent-aware escape route: maps the index of a waypoint reached by venting
    # to the vent index to stand on and press B (design §9). Empty for walk routes.
    route_teleports: dict[int, int] = Field(default_factory=dict)
    # Last observed self world position, for estimating velocity (predictive stop).
    last_self_x: int | None = None
    last_self_y: int | None = None
    # Whether the current vote intent has been confirmed (A pressed on the choice),
    # so we don't re-press once the vote is cast.
    vote_confirmed: bool = False
    # Registered cursor-step presses for the current vote intent. Bounds the
    # cursor walk: past the budget the resolver confirms wherever the cursor is,
    # because any vote beats the no-vote penalty (design §12).
    vote_steps: int = 0
    # Whether the current chat intent's text has been emitted (sent once).
    chat_sent: bool = False
    # Tick of the most recent emergency-button A press. The strategy uses this to
    # avoid wedging in call-button experiments if the server refuses the meeting.
    last_call_meeting_attempt_tick: int | None = None


class Command(BaseModel):
    """Per-tick wire payload (design §9).

    ``held_mask`` is the button bitmask the action layer wants held this tick; the
    bridge owns the last-sent mask and the send-only-on-change comparison
    (design §3.3). ``chat`` is meeting speech, emitted only during Voting.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    held_mask: int = 0
    chat: str | None = None


def derive_phase(resolved: ResolvedScene, current: Phase) -> Phase:
    """Advance the phase machine from interstitial text + voting + scene signals.

    Explicit interstitial text and the voting UI pin the transitional phases. The
    subtlety (design §5) is ``Playing``: during ordinary play there is usually *no*
    interstitial and no voting UI, so the machine must infer ``Playing`` from a
    live scene once a reveal/meeting clears — otherwise belief stays stuck at
    ``RoleReveal`` and the Normal mode (keyed on ``Playing``) never activates.
    """

    texts = resolved.phase_texts
    if texts & {"DRAW", "CREW WINS", "IMPS WIN"}:
        return "GameOver"
    if texts & {"IMPS", "CREWMATE"}:
        return "RoleReveal"
    if "GAME INFO" in texts:
        return "GameInfo"
    if texts & {"WAITING", "NEED MORE!", "STARTING"}:
        return "Lobby"
    if texts & {"NO ONE", "WAS KILLED"}:
        return "VoteResult"
    # The meeting-call interstitial (3 s between Playing and Voting, upstream
    # 2026-06-10). Checked before Voting: its screen shows no voting UI, and the
    # resolver only reports a meeting call when the voting UI is absent.
    if resolved.meeting_call is not None:
        return "MeetingCall"
    if resolved.voting.active or "SKIP" in texts:
        return "Voting"

    # No transitional signal. Infer Playing from a live scene: when a reveal /
    # meeting has just cleared, or (for a mid-game join) when Playing-specific
    # signals — the crew task counter or task bubbles — are present. A live camera
    # (the world-map object is only streamed during Playing) is what clears the
    # interstitial phases, including the new GameInfo / MeetingCall ones.
    if not resolved.camera_ready:
        return current
    if current in ("GameInfo", "RoleReveal", "MeetingCall", "VoteResult", "Voting", "Playing"):
        return "Playing"
    if resolved.crew_tasks_remaining is not None or resolved.task_signals:
        return "Playing"
    return current


def perceive(observation: Observation, tick: int) -> Percept:
    """Resolve the live scene into a frozen per-tick percept (design §4)."""

    scene = observation.scene
    return Percept(
        tick=tick,
        messages_applied=scene.messages_applied,
        resolved=resolve_scene(scene, tick),
        walkability=scene.walkability,
        visible_mask=scene.visible_mask,
    )


def _record_death(
    belief: Belief,
    color: str,
    tick: int,
    source: DeathSource,
    body_xy: tuple[int, int] | None = None,
) -> None:
    """Mark a player (by color) dead on the roster, creating the record if needed."""

    record = belief.roster.get(color)
    if record is None:
        record = belief.roster[color] = PlayerRecord(color=color)
    record.mark_dead(tick, source, body_xy)


def update_belief(belief: Belief, percept: Percept) -> None:
    """Fold the percept into belief in place (design §5)."""

    resolved = percept.resolved
    previous_phase = belief.phase  # before this tick's phase derivation
    belief.last_tick = percept.tick
    belief.ticks_observed += 1
    belief.messages_applied = percept.messages_applied

    belief.camera_ready = resolved.camera_ready
    belief.camera_x = resolved.camera_x
    belief.camera_y = resolved.camera_y
    belief.self_world_x = resolved.self_world_x
    belief.self_world_y = resolved.self_world_y

    # Build the navigation graph once from the static walkability mask + baked map
    # (design §6): nodes/edges validated at pixel resolution, reachable flood from
    # home, and precomputed reachable anchors for every task / vent / button.
    if belief.nav is None and percept.walkability is not None:
        belief.nav = build_nav_graph(percept.walkability, map_data=belief.map)

    # Tasks. The renderer emits a signal per incomplete assigned task. We only
    # accumulate which tasks are assigned and which are visible this tick;
    # completion is concluded by Normal mode (which knows the task it is standing
    # on), since a task also leaves the visible set merely by going off-screen.
    belief.visible_task_indices = {signal.task_index for signal in resolved.task_signals}
    belief.assigned_task_indices |= belief.visible_task_indices
    if resolved.crew_tasks_remaining is not None:
        belief.crew_tasks_remaining = resolved.crew_tasks_remaining
    belief.active_task_progress_pct = resolved.active_task_progress_pct

    # Live sightings: a "player <color>" in-world proves that player is alive here,
    # now. Keyed by color (the canonical identity); the trail accumulates in place.
    for player in resolved.visible_players:
        entry = belief.roster.get(player.color)
        if entry is None:
            entry = belief.roster[player.color] = PlayerRecord(color=player.color)
        entry.record(percept.tick, player.world_x, player.world_y, player.facing, player.object_id)
    # The roster spawns co-located at the first Playing tick, so the distinct
    # colors seen so far estimate the full player count (design §5); the meeting
    # census, when present, is authoritative.
    belief.total_player_count = max(belief.total_player_count, len(belief.roster))
    if resolved.census:
        belief.total_player_count = max(belief.total_player_count, len(resolved.census))

    belief.visible_body_ids = {body.object_id for body in resolved.visible_bodies}
    for body in resolved.visible_bodies:
        if body.object_id not in belief.bodies:
            belief.bodies[body.object_id] = BodyEntry(
                object_id=body.object_id,
                color=body.color,
                world_x=body.world_x,
                world_y=body.world_y,
                first_seen_tick=percept.tick,
            )
        # Reflect the death onto the (color-keyed) roster, linking it to the last
        # time we saw that player alive.
        _record_death(belief, body.color, percept.tick, "body", (body.world_x, body.world_y))

    # Append this frame to the perception tape — only camera-ready frames, so the
    # tape holds real in-world observations (with the viewport) for transition
    # detection. Meetings (no camera) leave a tick gap, which detectors require to
    # be absent (consecutive ticks) before trusting a transition.
    if resolved.camera_ready:
        belief.recent_frames.append(
            PerceptionFrame(
                tick=percept.tick,
                camera_x=resolved.camera_x,
                camera_y=resolved.camera_y,
                players={p.color: (p.world_x, p.world_y) for p in resolved.visible_players},
                bodies={b.color: (b.world_x, b.world_y) for b in resolved.visible_bodies},
                visible_mask=percept.visible_mask,
            )
        )
        if len(belief.recent_frames) > RECENT_FRAMES_MAX:
            del belief.recent_frames[0]

    # The meeting candidate grid is an authoritative alive/dead census by color.
    for entry in resolved.census:
        if entry.alive:
            record = belief.roster.get(entry.color)
            if record is None:
                belief.roster[entry.color] = PlayerRecord(color=entry.color, life_status="alive")
            elif record.life_status == "unknown":
                record.life_status = "alive"
        else:
            _record_death(belief, entry.color, percept.tick, "census")

    # The vote-result interstitial names the player the meeting ejected; also
    # close out the meeting's record with the ejection.
    if resolved.ejected_color is not None:
        _record_death(belief, resolved.ejected_color, percept.tick, "ejection")
        if belief.meeting_history and belief.meeting_history[-1].ejected_color is None:
            belief.meeting_history[-1].ejected_color = resolved.ejected_color

    # The server's authoritative tick marker, when present (older servers: None).
    if resolved.server_tick is not None:
        belief.server_tick = resolved.server_tick

    # Live game config from the pre-game GAME INFO interstitial.
    if resolved.game_info is not None:
        info = resolved.game_info
        if info.kill_cooldown_ticks is not None:
            belief.kill_cooldown_config_ticks = info.kill_cooldown_ticks
        if info.tasks_per_player is not None:
            belief.tasks_per_player = info.tasks_per_player
        if info.vote_timer_ticks is not None:
            belief.vote_timer_ticks = info.vote_timer_ticks
        if info.max_ticks is not None:
            belief.game_max_ticks = info.max_ticks

    phase = derive_phase(resolved, belief.phase)
    if phase != belief.phase:
        if phase == "MeetingCall":
            # A fresh meeting opens: reset the previous meeting's attribution so
            # the latched fields below describe only the current meeting.
            belief.meeting_called_by = None
            belief.meeting_trigger = None
            belief.meeting_reported_body_color = None
            # How deep into the interrupted Playing segment the call landed —
            # the caller-timing fingerprint (strategy.hunter).
            belief.meeting_call_elapsed_ticks = (
                percept.tick - belief.kill_cooldown_start_tick
                if previous_phase == "Playing" and belief.kill_cooldown_start_tick is not None
                else None
            )
        if phase == "Voting":
            # A meeting clears the previous transcript and — matching the server,
            # which removes all bodies when a meeting opens — our body beliefs, so a
            # post-meeting walk past where a body lay no longer reads as a sighting.
            # (Deaths already recorded on the roster persist; this only drops the
            # in-world body objects.)
            belief.chat_log.clear()
            belief.bodies.clear()
            belief.visible_body_ids.clear()
        belief.phase = phase
        belief.phase_start_tick = percept.tick

    # Latch the meeting-call attribution (who opened the meeting, and how) while
    # the interstitial shows; it persists through Voting/VoteResult.
    if resolved.meeting_call is not None:
        call = resolved.meeting_call
        if call.caller_color is not None:
            belief.meeting_called_by = call.caller_color
        if call.trigger is not None:
            belief.meeting_trigger = call.trigger
        if call.body_color is not None:
            belief.meeting_reported_body_color = call.body_color
            # The interstitial names the reported body — that player is dead,
            # even if we never saw the body in-world.
            _record_death(belief, call.body_color, percept.tick, "report")

    # Game outcome + end-of-game ground-truth roles from the GameOver screen.
    if "CREW WINS" in resolved.phase_texts:
        belief.game_outcome = "crew_wins"
    elif "IMPS WIN" in resolved.phase_texts:
        belief.game_outcome = "imps_win"
    elif "DRAW" in resolved.phase_texts:
        belief.game_outcome = "draw"
    if resolved.game_over_roles:
        belief.game_over_roles.update(resolved.game_over_roles)

    # Game start and meeting close reset every alive imposter's killCooldown. This
    # transition is visible to every role, so crewmate strategy can time emergency
    # button calls even though only imposters see the HUD ready/cooldown icon.
    if previous_phase != "Playing" and belief.phase == "Playing":
        belief.kill_cooldown_start_tick = percept.tick

    # Chat is re-rendered every tick (the last few messages), so de-duplicate by
    # (speaker, text) and append only lines we have not logged this meeting.
    if resolved.chat_lines:
        seen = {(event.speaker_color, event.text) for event in belief.chat_log}
        for line in resolved.chat_lines:
            key = (line.speaker_color, line.text)
            if key not in seen:
                seen.add(key)
                belief.chat_log.append(
                    ChatEvent(tick=percept.tick, speaker_color=line.speaker_color, text=line.text)
                )
                _record_accusations(belief, percept.tick, line.speaker_color, line.text)

    # The role-reveal "IMPS" interstitial confirms we are an imposter and shows
    # only our teammates' icons; record their colors so Hunt never targets them.
    if belief.phase == "RoleReveal" and "IMPS" in resolved.phase_texts:
        belief.self_role = "imposter"
        belief.teammate_colors |= resolved.reveal_player_colors

    # Self role/state (design §4-§5). The HUD shows an imposter/ghost icon for
    # those roles; an alive crewmate has neither, so once we know we are Playing
    # and no such marker is present, the role is crewmate. Role is fixed for the
    # game (a crewmate only changes to "dead" on death, via the ghost icon).
    if resolved.self_role is not None:
        # A kill-ready → cooldown edge for an imposter means we just killed
        # someone (the icon flips to "imposter icon cooldown"); note it to evade.
        # Gate on continuous Playing: a meeting also resets killCooldown, which
        # would otherwise look like a kill on the first Playing frame afterward.
        if (
            resolved.self_role == "imposter"
            and belief.self_kill_ready is True
            and resolved.self_kill_ready is False
            and previous_phase == "Playing"
            and belief.phase == "Playing"
        ):
            belief.last_kill_tick = percept.tick
        # Track the cooldown → kill-ready edge (and clear it on the way down) so the
        # strategy can measure how long we have been able to kill without doing so.
        if resolved.self_role == "imposter":
            if resolved.self_kill_ready is True and belief.self_kill_ready is not True:
                belief.kill_ready_since_tick = percept.tick
                # Cooldown just ran to ready: learn its duration (for pre-positioning).
                if belief.kill_cooldown_start_tick is not None:
                    belief.kill_cooldown_estimate = percept.tick - belief.kill_cooldown_start_tick
            elif resolved.self_kill_ready is False:
                belief.kill_ready_since_tick = None
            # Mark when our own current cooldown began. The globally observable
            # Playing transition above already covered role-reveal / meeting resets.
            if belief.self_kill_ready is True and resolved.self_kill_ready is False:
                belief.kill_cooldown_start_tick = percept.tick
        belief.self_role = resolved.self_role
        belief.self_kill_ready = resolved.self_kill_ready
    elif belief.self_role is None and belief.phase == "Playing":
        belief.self_role = "crewmate"

    belief.voting = resolved.voting

    # Maintain this meeting's record (design §5 social): refresh the vote tally
    # from the live dots every Voting tick, so the record freezes at the final
    # tally when the meeting closes (the ejection is filled in above).
    if belief.phase == "Voting":
        _refresh_meeting_record(belief)


def _record_accusations(belief: Belief, tick: int, speaker_color: str | None, text: str) -> None:
    """Parse one new chat line into who-sus'd-who edges on ``belief.accusations``."""

    # Imported lazily: the meeting package's __init__ pulls modules that import
    # this one, so a module-level import here would be circular. The parser
    # itself is pure (no crewborg imports).
    from players.crewrift.crewborg.strategy.meeting.social import (
        has_evidence_context,
        parse_stances,
    )

    has_evidence = has_evidence_context(text)
    for target_color, stance in parse_stances(speaker_color, text):
        belief.accusations.append(
            Accusation(
                meeting_id=belief.phase_start_tick,
                tick=tick,
                speaker_color=speaker_color,
                target_color=target_color,
                stance=stance,
                text=text,
                has_evidence=has_evidence,
            )
        )


def _refresh_meeting_record(belief: Belief) -> None:
    """Upsert the current meeting's :class:`MeetingRecord` from the live dots."""

    voting = belief.voting
    record = belief.meeting_history[-1] if belief.meeting_history else None
    if record is None or record.meeting_id != belief.phase_start_tick:
        record = MeetingRecord(meeting_id=belief.phase_start_tick)
        belief.meeting_history.append(record)
    # Attribution latched from the meeting-call interstitial that preceded this
    # Voting phase (None on older servers without the interstitial).
    if record.called_by is None:
        record.called_by = belief.meeting_called_by
    if record.trigger is None:
        record.trigger = belief.meeting_trigger
    if record.reported_body_color is None:
        record.reported_body_color = belief.meeting_reported_body_color
    if record.called_elapsed_ticks is None:
        record.called_elapsed_ticks = belief.meeting_call_elapsed_ticks

    slot_to_color = {candidate.slot: candidate.color for candidate in voting.candidates}
    votes: dict[str, str] = {}
    for dot in voting.dots:
        voter_color = slot_to_color.get(dot.voter)
        if voter_color is None:
            continue
        votes[voter_color] = "skip" if dot.is_skip else slot_to_color.get(dot.target, str(dot.target))
    if votes:
        record.votes = votes
