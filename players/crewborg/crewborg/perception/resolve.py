"""Resolve the scene tables into a structured :class:`ResolvedScene` (design §4).

Joins each object to its sprite's label, converts camera-relative coordinates to
world coordinates, and classifies entities by ``(label, object-id range)``. No
pixels are read.
"""

from __future__ import annotations

import re

from typing import TYPE_CHECKING

from crewborg.perception.constants import (
    BODY_OBJECT_BASE,
    CHAT_ICON_OBJECT_BASE,
    CHAT_ICON_OBJECT_LIMIT,
    CHAT_TEXT_OBJECT_BASE,
    CHAT_TEXT_OBJECT_LIMIT,
    ENTITY_COLLISION_DX,
    ENTITY_COLLISION_DY,
    LABEL_GHOST_ICON,
    LABEL_IMPOSTER_ICON,
    LABEL_IMPOSTER_ICON_COOLDOWN,
    LABEL_TASK_ARROW,
    LABEL_TASK_BUBBLE,
    LABEL_VOTE_CURSOR,
    LABEL_VOTE_SKIP_CURSOR,
    LABEL_VOTE_TIMER,
    MAX_PLAYERS,
    PHASE_TEXTS,
    PLAYER_OBJECT_BASE,
    VOTE_SKIP_DOT_OBJECT_BASE,
    PREFIX_BODY,
    PREFIX_PLAYER,
    PREFIX_PROGRESS_BAR,
    PREFIX_TASK_COUNTER,
    PREFIX_VOTE_DOT,
    PREFIX_VOTE_SELF_MARKER,
    RESULT_ICON_OBJECT_ID,
    ROLE_ICON_OBJECT_BASE,
    SELF_OFFSET_X,
    SELF_OFFSET_Y,
    TASK_ARROW_OBJECT_BASE,
    TASK_BUBBLE_OBJECT_BASE,
    VOTE_DOT_OBJECT_BASE,
    VOTE_ICON_OBJECT_BASE,
)
from crewborg.perception.entities import (
    SKIP_VOTE_TARGET,
    CensusEntry,
    ChatLine,
    ResolvedScene,
    TaskSignal,
    VisibleBody,
    VisiblePlayer,
    VoteCandidate,
    VoteDot,
    VotingState,
)

# The MeetingCall interstitial's caller line (game 4b9297d): "<Color> reported" /
# "<Color> pressed" / "<Color> called" — display-capitalized color names, possibly
# two words ("Pale Blue"). The follow-up line ("the button", "<Color>'s body") is
# not needed: the verb already distinguishes the call kind.
MEETING_CALL_TEXT = re.compile(r"^([A-Za-z]+(?: [A-Za-z]+)?) (reported|pressed|called)$")
MEETING_CALL_KINDS = {"reported": "body", "pressed": "button", "called": "unknown"}

# A chat speaker icon is matched to the text line whose screen-y is nearest, within
# this tolerance (px). The icon is vertically centered on its (possibly multi-line)
# message, so its y can sit a few px below the text's top.
CHAT_ICON_TEXT_Y_TOLERANCE = 32

if TYPE_CHECKING:
    from crewborg.coworld.scene import SceneState


def _parse_color_and_facing(text: str) -> tuple[str, str]:
    """Split ``"<color> left|right"`` into ``(color, facing)``."""

    color, _, facing = text.rpartition(" ")
    return color, facing


def _parse_trailing_int(text: str) -> int | None:
    """Parse the trailing run of digits from a label suffix (e.g. ``"45%"``)."""

    digits = "".join(c for c in text if c.isdigit())
    return int(digits) if digits else None


def resolve_scene(scene: SceneState, tick: int) -> ResolvedScene:
    """Build the resolved view for this tick from the current scene tables."""

    camera_x = scene.camera_x
    camera_y = scene.camera_y

    self_dead: bool = False
    self_kill_ready: bool | None = None
    players: list[VisiblePlayer] = []
    bodies: list[VisibleBody] = []
    tasks: list[TaskSignal] = []
    dots: list[VoteDot] = []
    active_progress: int | None = None
    crew_remaining: int | None = None
    phase_texts: set[str] = set()
    meeting_caller_color: str | None = None
    meeting_call_kind: str | None = None
    cursor = skip_cursor = timer = False
    cursor_xy: tuple[int, int] | None = None
    self_marker_color: str | None = None
    reveal_colors: set[str] = set()
    # Candidate-grid cells: (slot, color, alive, screen_x, screen_y). The slot is the
    # cursor index; positions let us map the cursor to a slot after the loop.
    candidate_cells: list[tuple[int, str, bool, int, int]] = []
    ejected_color: str | None = None
    # Chat is paired after the loop: collect candidate text lines (screen-y → text)
    # and speaker icons (screen-y → color), then match each icon to its line by y.
    chat_text_rows: list[tuple[int, str]] = []
    chat_icon_rows: list[tuple[int, str]] = []

    for object_id, obj in scene.objects.items():
        sprite = scene.sprites.get(obj.sprite_id)
        if sprite is None:
            continue
        label = sprite.label
        world_x = obj.x + camera_x
        world_y = obj.y + camera_y

        # HUD icons (their object ids are not in the entity ranges). We read kill
        # *state* and death here — NOT role. Imposter-vs-crew is established
        # positively from the RoleReveal interstitial text (see derive_phase /
        # update_belief in types.py); the kill/cooldown icon only tells us whether a
        # kill is currently ready, and the ghost icon tells us we have died (a state,
        # which overrides role once it happens).
        if label == LABEL_IMPOSTER_ICON:
            self_kill_ready = True
            continue
        if label == LABEL_IMPOSTER_ICON_COOLDOWN:
            self_kill_ready = False
            continue
        if label == LABEL_GHOST_ICON:
            self_dead = True
            continue

        # Social UI on the voting / vote-result screens, dispatched by id range.
        if CHAT_TEXT_OBJECT_BASE <= object_id < CHAT_TEXT_OBJECT_LIMIT:
            # Shared with phase/HUD text: keep as a chat-text candidate but fall
            # through so genuine phase texts still reach the PHASE_TEXTS branch.
            chat_text_rows.append((obj.y, label))
        elif CHAT_ICON_OBJECT_BASE <= object_id < CHAT_ICON_OBJECT_LIMIT:
            if label.startswith(PREFIX_PLAYER):
                color, _ = _parse_color_and_facing(label[len(PREFIX_PLAYER) :])
                chat_icon_rows.append((obj.y, color))
            continue
        elif VOTE_ICON_OBJECT_BASE <= object_id < VOTE_ICON_OBJECT_BASE + MAX_PLAYERS:
            slot = object_id - VOTE_ICON_OBJECT_BASE
            if label.startswith(PREFIX_PLAYER):
                color, _ = _parse_color_and_facing(label[len(PREFIX_PLAYER) :])
                candidate_cells.append((slot, color, True, obj.x, obj.y))
            elif label.startswith(PREFIX_BODY):
                candidate_cells.append((slot, label[len(PREFIX_BODY) :], False, obj.x, obj.y))
            continue
        elif object_id == RESULT_ICON_OBJECT_ID:
            if label.startswith(PREFIX_PLAYER):
                ejected_color, _ = _parse_color_and_facing(label[len(PREFIX_PLAYER) :])
            continue

        if label == LABEL_VOTE_CURSOR:
            cursor = True
            cursor_xy = (obj.x, obj.y)
        elif label == LABEL_VOTE_SKIP_CURSOR:
            skip_cursor = True
        elif label == LABEL_VOTE_TIMER:
            timer = True
        elif label.startswith(PREFIX_VOTE_SELF_MARKER):
            self_marker_color = label[len(PREFIX_VOTE_SELF_MARKER) :]
        elif label.startswith(PREFIX_PROGRESS_BAR):
            active_progress = _parse_trailing_int(label[len(PREFIX_PROGRESS_BAR) :])
        elif label.startswith(PREFIX_TASK_COUNTER):
            crew_remaining = _parse_trailing_int(label[len(PREFIX_TASK_COUNTER) :])
        elif label in PHASE_TEXTS:
            phase_texts.add(label)
        else:
            call = MEETING_CALL_TEXT.match(label)
            if call is not None:
                meeting_caller_color = call.group(1).lower()
                meeting_call_kind = MEETING_CALL_KINDS[call.group(2)]

        # Player/body objects are drawn offset from their collision point; recover
        # the collision point so range checks (report, kill) match the server.
        coll_x = world_x + ENTITY_COLLISION_DX
        coll_y = world_y + ENTITY_COLLISION_DY

        # Entities, classified by both id range and label.
        if PLAYER_OBJECT_BASE <= object_id < BODY_OBJECT_BASE and label.startswith(PREFIX_PLAYER):
            color, facing = _parse_color_and_facing(label[len(PREFIX_PLAYER) :])
            if facing in ("left", "right"):
                players.append(
                    VisiblePlayer(
                        object_id=object_id, color=color, facing=facing, world_x=coll_x, world_y=coll_y
                    )
                )
        elif BODY_OBJECT_BASE <= object_id < TASK_BUBBLE_OBJECT_BASE and label.startswith(PREFIX_BODY):
            bodies.append(
                VisibleBody(
                    object_id=object_id, color=label[len(PREFIX_BODY) :], world_x=coll_x, world_y=coll_y
                )
            )
        elif TASK_BUBBLE_OBJECT_BASE <= object_id < TASK_ARROW_OBJECT_BASE and label == LABEL_TASK_BUBBLE:
            tasks.append(
                TaskSignal(
                    task_index=object_id - TASK_BUBBLE_OBJECT_BASE,
                    kind="bubble",
                    world=(world_x, world_y),
                    screen=(obj.x, obj.y),
                )
            )
        elif TASK_ARROW_OBJECT_BASE <= object_id < VOTE_DOT_OBJECT_BASE and label == LABEL_TASK_ARROW:
            tasks.append(
                TaskSignal(
                    task_index=object_id - TASK_ARROW_OBJECT_BASE,
                    kind="arrow",
                    world=None,
                    screen=(obj.x, obj.y),
                )
            )
        elif (
            VOTE_DOT_OBJECT_BASE <= object_id < VOTE_DOT_OBJECT_BASE + MAX_PLAYERS * MAX_PLAYERS
            and label.startswith(PREFIX_VOTE_DOT)
        ):
            rel = object_id - VOTE_DOT_OBJECT_BASE
            dots.append(VoteDot(target=rel // MAX_PLAYERS, voter=rel % MAX_PLAYERS))
        elif (
            VOTE_SKIP_DOT_OBJECT_BASE <= object_id < VOTE_SKIP_DOT_OBJECT_BASE + MAX_PLAYERS
            and label.startswith(PREFIX_VOTE_DOT)
        ):
            # Skip votes share the "vote dot" sprite but a separate id range.
            dots.append(VoteDot(target=SKIP_VOTE_TARGET, voter=object_id - VOTE_SKIP_DOT_OBJECT_BASE))
        elif (
            ROLE_ICON_OBJECT_BASE <= object_id < ROLE_ICON_OBJECT_BASE + MAX_PLAYERS
            and label.startswith(PREFIX_PLAYER)
        ):
            # Role-reveal icons: for an imposter viewer these are the teammates.
            color, _ = _parse_color_and_facing(label[len(PREFIX_PLAYER) :])
            reveal_colors.add(color)

    self_world_x = camera_x + SELF_OFFSET_X if scene.camera_ready else None
    self_world_y = camera_y + SELF_OFFSET_Y if scene.camera_ready else None
    chat_lines = _pair_chat(chat_icon_rows, chat_text_rows)
    census = tuple(CensusEntry(color=c, alive=alive) for _slot, c, alive, _x, _y in candidate_cells)
    candidates = tuple(VoteCandidate(slot=s, color=c, alive=alive) for s, c, alive, _x, _y in candidate_cells)
    cursor_slot = _cursor_slot(cursor_xy, candidate_cells) if cursor else None

    return ResolvedScene(
        tick=tick,
        camera_ready=scene.camera_ready,
        camera_x=camera_x,
        camera_y=camera_y,
        self_dead=self_dead,
        self_kill_ready=self_kill_ready,
        self_world_x=self_world_x,
        self_world_y=self_world_y,
        visible_players=tuple(players),
        visible_bodies=tuple(bodies),
        task_signals=tuple(tasks),
        active_task_progress_pct=active_progress,
        crew_tasks_remaining=crew_remaining,
        voting=VotingState(
            cursor_present=cursor,
            skip_cursor_present=skip_cursor,
            timer_present=timer,
            self_marker_color=self_marker_color,
            dots=tuple(dots),
            candidates=candidates,
            cursor_slot=cursor_slot,
        ),
        phase_texts=frozenset(phase_texts),
        meeting_caller_color=meeting_caller_color,
        meeting_call_kind=meeting_call_kind,
        reveal_player_colors=frozenset(reveal_colors),
        chat_lines=chat_lines,
        census=census,
        ejected_color=ejected_color,
    )


def _cursor_slot(
    cursor_xy: tuple[int, int] | None, cells: list[tuple[int, str, bool, int, int]]
) -> int | None:
    """The candidate-grid slot the cursor sits on, by nearest cell position.

    The cursor on slot ``s`` is drawn at the same grid position as candidate cell
    ``s`` (global.nim), so the nearest cell is the one it's on. Matching by position
    avoids hardcoding the grid layout constants.
    """

    if cursor_xy is None or not cells:
        return None
    cx, cy = cursor_xy
    slot, _color, _alive, _x, _y = min(cells, key=lambda c: (c[3] - cx) ** 2 + (c[4] - cy) ** 2)
    return slot


def _pair_chat(
    icon_rows: list[tuple[int, str]], text_rows: list[tuple[int, str]]
) -> tuple[ChatLine, ...]:
    """Match each chat speaker icon to the text line at the nearest screen-y.

    Anchoring on the icon range (exclusively chat) keeps phase/HUD text in the
    shared 9000 range from being mistaken for chat: a text line is only emitted
    when an icon sits within ``CHAT_ICON_TEXT_Y_TOLERANCE`` of it. Each text line is
    consumed at most once, so stacked messages map one-to-one to their speakers.
    """

    lines: list[ChatLine] = []
    used: set[int] = set()
    for icon_y, color in sorted(icon_rows):
        best: int | None = None
        best_dy = CHAT_ICON_TEXT_Y_TOLERANCE + 1
        for i, (text_y, _text) in enumerate(text_rows):
            if i in used:
                continue
            dy = abs(text_y - icon_y)
            if dy < best_dy:
                best, best_dy = i, dy
        if best is None:
            continue
        used.add(best)
        lines.append(ChatLine(speaker_color=color, text=text_rows[best][1]))
    return tuple(lines)
