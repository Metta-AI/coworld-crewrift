"""Action layer: resolve symbolic intents into wire payloads (design §9, §12).

All Sprite-v1 transport mechanics and movement control live here.
``resolve_action`` is **stateful across ticks** via ``ActionState``: it diffs the
incoming intent against the stored one, discarding in-progress execution (the nav
route, button state) when the intent changes and continuing it when unchanged.

Movement controller (design §12 default): **bang-bang** d-pad toward the target
with a **release-near-target deadband** and a **predictive stop** — release an
axis when the remaining distance is within the estimated momentum stopping
distance, so the agent coasts to rest on the target instead of overshooting.

Composite intents sequence navigate-then-interact over one "move toward a world
point" routine that follows the baked nav route (design §9):

- ``navigate_to`` → follow the route to the point.
- ``complete_task`` → navigate to the task rect, then hold A with no d-pad
  (movement suppressed — any d-pad input resets the 72-tick task progress).

This is the final stage of the cognitive stack (… → modes → **action**): modes emit
a symbolic ``Intent``; this layer turns it into the button bitmask the bridge sends.

Collaborators
-------------
Relies on:
  - ``nav.plan_route`` / ``nav.plan_route_via_vents`` — the A* routes the follower
    walks (the vent-aware variant is imposter-flee only).
  - ``types`` — ``Intent`` (input), ``Belief`` (read: ``nav`` / ``map`` / ``roster`` /
    ``bodies`` / ``voting`` / self position), ``ActionState`` (the cross-tick execution
    state it mutates), ``Command`` (output wire payload).
Used by:
  - ``__init__.build_runtime`` passes ``resolve_action`` as the runtime's action stage.
  - ``coworld.policy_player`` (the bridge) consumes the ``Command`` / encoders.
  - ``events.py`` reads ``KILL_RANGE_SQ`` and the ``BTN_*`` bits to derive attempt
    traces from the produced command; ``modes.hunt`` reads ``KILL_RANGE_SQ``.
Emits / touches: produces a per-tick ``Command`` (held button mask + optional chat)
  and mutates ``ActionState`` (current intent, nav route/cursor/goal, teleport map,
  last self position, vote/chat latches). Never mutates ``Belief``.

Modifying this file: this layer is *mechanism, not policy* — it executes the intent it
is handed and must not make strategic choices (target/victim/vote selection live in
modes + strategy). Honor the edge-triggered vs level-triggered button distinction
(``_edge_press`` for A presses that must re-fire, B held for vents) and keep the
range gates (``*_RANGE_SQ``) matched to ``sim.nim``.
"""

from __future__ import annotations

from crewborg.nav import plan_route, plan_route_via_vents
from crewborg.types import ActionState, Belief, Command, Intent

INPUT_HEADER = 0x84
CHAT_HEADER = 0x81
MASK_BITS = 0x7F

# Button bit assignments (design §3.3).
BTN_UP = 0x01
BTN_DOWN = 0x02
BTN_LEFT = 0x04
BTN_RIGHT = 0x08
BTN_A = 0x20
BTN_B = 0x40

# Movement-controller tuning (design §12). Distances are world pixels.
ARRIVE_RADIUS = 4  # within this of an axis target ⇒ that axis has arrived
WAYPOINT_RADIUS = 8  # within this of a route waypoint ⇒ advance to the next
# Momentum stopping distance ≈ v·fr/(1-fr) with fr = 144/256; ≈ 1.29·v. Release
# the axis a bit before that so friction brings us to rest on the target.
STOP_FACTOR = 1.3

# Re-root the nav route at the agent's live position at least this often (ticks), so
# the follower never commits to a stale route after drifting off the planned line.
# A* is ~0.2ms (design §12), so frequent replanning is effectively free.
REPLAN_INTERVAL = 8

# Report fires when within ReportRange = 20px (dist² ≤ 400) of a body (sim.nim).
REPORT_RANGE_SQ = 400
# Kill fires within KillRange = 20px (dist² ≤ 400); vent within VentRange = 16px
# (dist² ≤ 256) (sim.nim).
KILL_RANGE_SQ = 400
VENT_RANGE_SQ = 256


def encode_chat(text: str) -> bytes:
    """Encode meeting chat into a Sprite-v1 input-text packet (Voting only).

    ``0x81`` + little-endian ``u16`` length + printable ASCII (non-ASCII dropped).
    """

    payload = text.encode("ascii", errors="ignore")
    return bytes([CHAT_HEADER]) + len(payload).to_bytes(2, "little") + payload


def encode_input(held_mask: int) -> bytes:
    """Encode a held-button bitmask into a Sprite-v1 input packet."""

    return bytes([INPUT_HEADER, held_mask & MASK_BITS])


def _axis_input(delta: int, velocity: int) -> int:
    """Return -1/0/+1 d-pad input for one axis (bang-bang + predictive stop)."""

    if abs(delta) <= ARRIVE_RADIUS:
        return 0
    # If already coasting toward the target and within stopping distance, release
    # so momentum carries us the rest of the way without overshooting.
    if velocity != 0 and (velocity > 0) == (delta > 0) and abs(delta) <= STOP_FACTOR * abs(velocity):
        return 0
    return 1 if delta > 0 else -1


def _movement_mask(self_xy: tuple[int, int], target_xy: tuple[int, int], velocity: tuple[int, int]) -> int:
    """Held d-pad mask to drive from ``self_xy`` toward ``target_xy``."""

    ix = _axis_input(target_xy[0] - self_xy[0], velocity[0])
    iy = _axis_input(target_xy[1] - self_xy[1], velocity[1])
    mask = 0
    if ix < 0:
        mask |= BTN_LEFT
    elif ix > 0:
        mask |= BTN_RIGHT
    if iy < 0:
        mask |= BTN_UP
    elif iy > 0:
        mask |= BTN_DOWN
    return mask


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Squared world-pixel distance between two points (compared against ``*_RANGE_SQ``)."""

    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _self_xy(belief: Belief) -> tuple[int, int] | None:
    """Our current world position, or ``None`` before the camera/self fix exists."""

    if belief.self_world_x is None or belief.self_world_y is None:
        return None
    return belief.self_world_x, belief.self_world_y


def _velocity(action_state: ActionState, self_xy: tuple[int, int]) -> tuple[int, int]:
    """Per-axis world-px displacement since last tick (0,0 on the first observed tick);
    the momentum estimate the predictive-stop deadband uses."""

    if action_state.last_self_x is None or action_state.last_self_y is None:
        return 0, 0
    return self_xy[0] - action_state.last_self_x, self_xy[1] - action_state.last_self_y


def _reset_execution(action_state: ActionState, intent: Intent) -> None:
    """Adopt a new intent and discard all in-progress execution (route, button/vote/chat
    latches), called when the incoming intent differs from the stored one."""

    action_state.current_intent = intent
    action_state.route = []
    action_state.route_cursor = 0
    action_state.route_goal = None
    action_state.route_teleports = {}
    action_state.ticks_since_plan = 0
    action_state.vote_confirmed = False
    action_state.chat_sent = False


def _edge_press(action_state: ActionState, bit: int) -> int:
    """Fire an edge-triggered button once: 0→bit registers; if we held it last
    tick, release first (return 0) so the next tick re-presses (sim.nim freshA)."""

    return 0 if action_state.held_mask & bit else bit


def _navigate_mask(
    belief: Belief,
    action_state: ActionState,
    self_xy: tuple[int, int],
    goal: tuple[int, int],
    *,
    via_vents: bool = False,
) -> int:
    """Follow (replanning if needed) the nav route toward ``goal``; return d-pad mask.

    With ``via_vents`` the route may include vent teleport legs (imposter flee): on
    such a leg the agent walks onto the entry vent's anchor and presses B to vanish
    to the exit, then resumes walking.
    """

    velocity = _velocity(action_state, self_xy)

    # (Re)plan when the goal changes, and also **periodically** (every
    # REPLAN_INTERVAL ticks) re-rooting the route at the agent's live position. A* is
    # ~0.2ms, so this is nearly free, and it keeps the follower from committing to a
    # stale route after it has drifted off the planned line (the residual cause of
    # task-approach wedging — a fresh route from where it actually is routes around
    # the wall it was mashing into).
    action_state.ticks_since_plan += 1
    if action_state.route_goal != goal or action_state.ticks_since_plan >= REPLAN_INTERVAL:
        action_state.route_goal = goal
        action_state.route_cursor = 0
        action_state.route_teleports = {}
        action_state.ticks_since_plan = 0
        if belief.nav is None:
            # No nav graph yet: steer straight at the goal.
            action_state.route = [goal]
        elif via_vents:
            route, teleports = plan_route_via_vents(belief.nav, self_xy, goal)
            action_state.route = list(route)
            action_state.route_teleports = dict(teleports)
        else:
            # nav present: an empty route means genuinely unreachable — hold
            # still (a stall the mode can react to) rather than steering at a wall.
            action_state.route = list(plan_route(belief.nav, self_xy, goal))

    if not action_state.route:
        return 0  # unreachable under the nav graph: hold still

    # Advance past any waypoints we have already reached — including a teleport
    # target once the hop has dropped us next to it (so we resume walking onward
    # instead of trying to vent back). A teleport target is unreachable on foot, so
    # before the hop fires we are never within range of it and the cursor halts on
    # it, which is exactly when we press B below.
    while (
        action_state.route_cursor < len(action_state.route) - 1
        and _dist2(self_xy, action_state.route[action_state.route_cursor]) <= WAYPOINT_RADIUS**2
    ):
        action_state.route_cursor += 1

    cursor = action_state.route_cursor
    if cursor in action_state.route_teleports and _dist2(self_xy, action_state.route[cursor]) > WAYPOINT_RADIUS**2:
        return _teleport_mask(belief, action_state, self_xy)

    waypoint = action_state.route[min(cursor, len(action_state.route) - 1)]
    return _movement_mask(self_xy, waypoint, velocity)


def _teleport_mask(belief: Belief, action_state: ActionState, self_xy: tuple[int, int]) -> int:
    """Drive the vent hop at the current cursor: press B in range, else close in.

    The cursor sits on a teleport-target waypoint; the leg before it walked us onto
    the entry vent's anchor. Press B (level-triggered) once we are actually within
    VentRange of that vent's center — otherwise keep steering onto the anchor so the
    press lands. Once the server teleports us next to the exit waypoint, the cursor
    advances and ordinary walking resumes.
    """

    vent_index = action_state.route_teleports[action_state.route_cursor]
    if belief.map is None or not (0 <= vent_index < len(belief.map.vents)):
        return 0
    center = belief.map.vents[vent_index].center
    if _dist2(self_xy, (center.x, center.y)) <= VENT_RANGE_SQ:
        return BTN_B
    entry = action_state.route[action_state.route_cursor - 1]
    return _movement_mask(self_xy, entry, _velocity(action_state, self_xy))


def resolve_action(intent: Intent, belief: Belief, action_state: ActionState) -> Command:
    """Execute an intent into this tick's wire command (design §9)."""

    # Diff against the stored intent; a change discards in-progress execution.
    if intent != action_state.current_intent:
        _reset_execution(action_state, intent)

    self_xy = _self_xy(belief)
    command = _resolve(intent, belief, action_state, self_xy)

    # Record self position for next tick's velocity estimate, and the held mask.
    if self_xy is not None:
        action_state.last_self_x, action_state.last_self_y = self_xy
    action_state.held_mask = command.held_mask
    return command


def _resolve(
    intent: Intent, belief: Belief, action_state: ActionState, self_xy: tuple[int, int] | None
) -> Command:
    """Dispatch one intent to its handler. Idle/loiter hold still; vote/chat are
    position-free; every world-relative intent first requires ``self_xy`` (else hold
    still until the camera is up). Unknown kinds fall through to a held mask of 0."""

    if intent.kind in ("idle", "loiter"):
        return Command(held_mask=0)

    # Meeting intents don't depend on world position.
    if intent.kind == "vote":
        return _resolve_vote(intent, belief, action_state)
    if intent.kind == "chat":
        return _resolve_chat(intent, action_state)

    # World-relative intents need our position; hold still until the camera is up.
    if self_xy is None:
        return Command(held_mask=0)

    if intent.kind == "navigate_to":
        if intent.point is None:
            return Command(held_mask=0)
        return Command(held_mask=_navigate_mask(belief, action_state, self_xy, intent.point))

    if intent.kind == "escape":
        if intent.point is None:
            return Command(held_mask=0)
        # Flee toward the point, vanishing through a vent when one lies on the route.
        return Command(held_mask=_navigate_mask(belief, action_state, self_xy, intent.point, via_vents=True))

    if intent.kind == "complete_task":
        return _resolve_complete_task(intent, belief, action_state, self_xy)

    if intent.kind == "report":
        return _resolve_report(intent, belief, action_state, self_xy)

    if intent.kind == "call_meeting":
        return _resolve_call_meeting(belief, action_state, self_xy)

    if intent.kind == "kill":
        return _resolve_kill(intent, belief, action_state, self_xy)

    if intent.kind == "vent":
        return _resolve_vent(intent, belief, action_state, self_xy)

    return Command(held_mask=0)


def _resolve_complete_task(
    intent: Intent, belief: Belief, action_state: ActionState, self_xy: tuple[int, int]
) -> Command:
    """Drive onto the task station's anchor, then (once inside the rect) hold A with no
    d-pad so progress accrues without resetting. No-op for an out-of-range index."""

    if intent.task_index is None or belief.map is None or intent.task_index >= len(belief.map.tasks):
        return Command(held_mask=0)
    task = belief.map.tasks[intent.task_index]
    inside = task.x <= self_xy[0] < task.x + task.w and task.y <= self_xy[1] < task.y + task.h
    if inside:
        # On the station: hold A with no d-pad (any d-pad resets task progress);
        # residual momentum settles via friction while progress accrues.
        return Command(held_mask=BTN_A)
    # Otherwise drive onto the station's baked anchor (a reachable pixel inside the
    # rect), falling back to the geometric center before the nav graph exists.
    anchor = belief.nav.task_anchor(intent.task_index) if belief.nav is not None else None
    goal = anchor if anchor is not None else (task.center.x, task.center.y)
    return Command(held_mask=_navigate_mask(belief, action_state, self_xy, goal))


def _resolve_report(
    intent: Intent, belief: Belief, action_state: ActionState, self_xy: tuple[int, int]
) -> Command:
    """Navigate to the target body; once within ReportRange, a fresh A press reports it.
    No-op if the body id is unknown."""

    body = belief.bodies.get(intent.target_id) if intent.target_id is not None else None
    if body is None:
        return Command(held_mask=0)
    body_xy = (body.world_x, body.world_y)
    if _dist2(self_xy, body_xy) <= REPORT_RANGE_SQ:
        # In range: a fresh A press reports the body (sim.nim tryReport).
        return Command(held_mask=_edge_press(action_state, BTN_A))
    return Command(held_mask=_navigate_mask(belief, action_state, self_xy, body_xy))


def _resolve_vote(intent: Intent, belief: Belief, action_state: ActionState) -> Command:
    """Drive the vote cursor onto the chosen cell and confirm it, exactly once.

    ``intent.target_color`` is the player to eject (design §7.1); ``None`` ⇒ skip.
    A targeted vote whose target can't be resolved (grid not up yet, or the target
    has died) falls back to skip so we still vote and avoid the no-vote penalty
    (design §12). The cursor steps with edge-triggered presses; stepping DOWN cycles
    through every alive cell + skip, so it always reaches the goal.
    """

    if action_state.vote_confirmed:
        return Command(held_mask=0)
    voting = belief.voting

    if intent.target_color is not None:
        target_slot = next(
            (c.slot for c in voting.candidates if c.color == intent.target_color and c.alive), None
        )
        if target_slot is not None:
            if voting.cursor_slot == target_slot:
                return _confirm_vote(action_state)
            return Command(held_mask=_edge_press(action_state, BTN_DOWN))  # step toward the target

    # Skip policy (default, or target unresolvable): step onto the skip cell, confirm.
    if voting.skip_cursor_present:
        return _confirm_vote(action_state)
    return Command(held_mask=_edge_press(action_state, BTN_DOWN))


def _confirm_vote(action_state: ActionState) -> Command:
    press = _edge_press(action_state, BTN_A)
    if press:  # the fresh-press tick casts the vote
        action_state.vote_confirmed = True
    return Command(held_mask=press)


def _resolve_chat(intent: Intent, action_state: ActionState) -> Command:
    if action_state.chat_sent or not intent.text:
        return Command(held_mask=0)
    action_state.chat_sent = True
    return Command(held_mask=0, chat=intent.text)


def _resolve_call_meeting(
    belief: Belief, action_state: ActionState, self_xy: tuple[int, int]
) -> Command:
    """Navigate to the emergency button and press A inside its rect to call a meeting.

    Mirrors ``_resolve_report``/``_resolve_complete_task``: drive onto the button's
    reachable anchor, then — once standing in the button rect — a fresh A press fires
    ``tryCallButton`` (sim.nim). Holds still until the nav graph / map is available.
    """

    if belief.map is None:
        return Command(held_mask=0)
    button = belief.map.button
    inside = button.x <= self_xy[0] < button.x + button.w and button.y <= self_xy[1] < button.y + button.h
    if inside:
        return Command(held_mask=_edge_press(action_state, BTN_A))
    anchor = belief.nav.button_anchor if belief.nav is not None else None
    goal = anchor if anchor is not None else (button.center.x, button.center.y)
    return Command(held_mask=_navigate_mask(belief, action_state, self_xy, goal))


def _resolve_kill(
    intent: Intent, belief: Belief, action_state: ActionState, self_xy: tuple[int, int]
) -> Command:
    """Navigate to the victim (by color); once within KillRange, a fresh A press kills.
    Hunt owns the strike *decision* — this only executes it. No-op for an unknown color."""

    target = belief.roster.get(intent.target_color) if intent.target_color is not None else None
    if target is None:
        return Command(held_mask=0)
    target_xy = (target.world_x, target.world_y)
    if _dist2(self_xy, target_xy) <= KILL_RANGE_SQ:
        # In range: a fresh A press kills (sim.nim tryKill). Caveat: if a body is
        # adjacent, the server reports it instead — Hunt avoids that case.
        return Command(held_mask=_edge_press(action_state, BTN_A))
    return Command(held_mask=_navigate_mask(belief, action_state, self_xy, target_xy))


def _resolve_vent(
    intent: Intent, belief: Belief, action_state: ActionState, self_xy: tuple[int, int]
) -> Command:
    """Drive onto the chosen vent's anchor, then hold B (level-triggered) within
    VentRange of its center to teleport. ``target_id`` picks the vent; default is the
    nearest. No-op when the map has no vents."""

    index = _select_vent_index(belief, intent.target_id, self_xy)
    if index is None:
        return Command(held_mask=0)
    vent = belief.map.vents[index]
    center_xy = (vent.center.x, vent.center.y)
    # Vent fires within VentRange of the vent center (sim.nim tryVent), so the trigger
    # gate stays on the center; navigation aims at the baked anchor (a reachable pixel
    # within range), falling back to the center before the nav graph exists.
    if _dist2(self_xy, center_xy) <= VENT_RANGE_SQ:
        return Command(held_mask=BTN_B)  # B is level-triggered
    anchor = belief.nav.vent_anchor(index) if belief.nav is not None else None
    goal = anchor if anchor is not None else center_xy
    return Command(held_mask=_navigate_mask(belief, action_state, self_xy, goal))


def _select_vent_index(belief: Belief, target_id: int | None, self_xy: tuple[int, int]) -> int | None:
    """Pick which vent to use: the explicit ``target_id`` if in range, else the nearest
    vent by center distance; ``None`` when the map has no vents."""

    if belief.map is None or not belief.map.vents:
        return None
    vents = belief.map.vents
    if target_id is not None and 0 <= target_id < len(vents):
        return target_id
    # Default: the nearest vent by center distance.
    return min(range(len(vents)), key=lambda i: _dist2(self_xy, (vents[i].center.x, vents[i].center.y)))
