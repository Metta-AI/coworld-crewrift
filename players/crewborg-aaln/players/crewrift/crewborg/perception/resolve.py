"""Resolve the scene tables into a structured :class:`ResolvedScene` (design §4).

Joins each object to its sprite's label, converts camera-relative coordinates to
world coordinates, and classifies entities by ``(label, object-id range)``. No
pixels are read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from players.crewrift.crewborg.perception.constants import (
    BODY_OBJECT_BASE,
    CHAT_ICON_OBJECT_BASE,
    CHAT_ICON_OBJECT_LIMIT,
    CHAT_TEXT_OBJECT_BASE,
    CHAT_TEXT_OBJECT_LIMIT,
    ENTITY_COLLISION_DX,
    ENTITY_COLLISION_DY,
    GAME_INFO_PREFIX_GAME_TIMER,
    GAME_INFO_PREFIX_KILL_COOLDOWN,
    GAME_INFO_PREFIX_TASKS,
    GAME_INFO_PREFIX_VOTE_TIMER,
    GAMEOVER_ICON_OBJECT_BASE,
    GAMEOVER_TEXT_CREW,
    GAMEOVER_TEXT_IMP,
    LABEL_GHOST_ICON,
    LABEL_IMPOSTER_ICON,
    LABEL_IMPOSTER_ICON_COOLDOWN,
    LABEL_MEETING_BUTTON,
    LABEL_TASK_ARROW,
    LABEL_TASK_BUBBLE,
    LABEL_VOTE_CURSOR,
    LABEL_VOTE_SKIP_CURSOR,
    LABEL_VOTE_TIMER,
    MAX_PLAYERS,
    MEETING_ICON_OBJECT_BASE,
    MEETING_TEXT_SUFFIX_CALLED,
    MEETING_TEXT_SUFFIX_PRESSED,
    MEETING_TEXT_SUFFIX_REPORTED,
    PHASE_TEXT_GAME_INFO,
    PHASE_TEXTS,
    PLAYER_COLOR_NAMES,
    PLAYER_OBJECT_BASE,
    PREFIX_SERVER_TICK,
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
from players.crewrift.crewborg.perception.entities import (
    SKIP_VOTE_TARGET,
    CensusEntry,
    ChatLine,
    GameInfo,
    MeetingCall,
    ResolvedScene,
    TaskSignal,
    VisibleBody,
    VisiblePlayer,
    VoteCandidate,
    VoteDot,
    VotingState,
)

# A chat speaker icon is matched to the text line whose screen-y is nearest, within
# this tolerance (px). The icon is vertically centered on its (possibly multi-line)
# message, so its y can sit a few px below the text's top.
CHAT_ICON_TEXT_Y_TOLERANCE = 32

if TYPE_CHECKING:
    from players.crewrift.crewborg.coworld.scene import SceneState


def _parse_color_and_facing(text: str) -> tuple[str, str]:
    """Split ``"<color> left|right"`` into ``(color, facing)``."""

    color, _, facing = text.rpartition(" ")
    return color, facing


def _parse_trailing_int(text: str) -> int | None:
    """Parse the trailing run of digits from a label suffix (e.g. ``"45%"``)."""

    digits = "".join(c for c in text if c.isdigit())
    return int(digits) if digits else None


def _parse_bounded_int(label: str, prefix: str, suffix: str) -> int | None:
    """Parse ``label`` as ``<prefix><digits><suffix>``, or ``None`` (strict)."""

    if not label.startswith(prefix) or not label.endswith(suffix):
        return None
    body = label[len(prefix) : len(label) - len(suffix)]
    return int(body) if body.isdigit() else None


def _meeting_text_caller(label: str, suffix: str) -> str | None:
    """The caller color from a meeting-call text line ("Light blue reported").

    The game capitalizes the color's first letter; "Someone" means the caller has
    left. Returns the lowercase color when it is a known player color, else None.
    """

    candidate = label[: len(label) - len(suffix)].lower()
    return candidate if candidate in PLAYER_COLOR_NAMES else None


def resolve_scene(scene: SceneState, tick: int) -> ResolvedScene:
    """Build the resolved view for this tick from the current scene tables."""

    camera_x = scene.camera_x
    camera_y = scene.camera_y

    self_role: str | None = None
    self_kill_ready: bool | None = None
    players: list[VisiblePlayer] = []
    bodies: list[VisibleBody] = []
    tasks: list[TaskSignal] = []
    dots: list[VoteDot] = []
    active_progress: int | None = None
    crew_remaining: int | None = None
    phase_texts: set[str] = set()
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
    # Server tick marker ("tick <N>"); take the max in case of stale duplicates.
    server_tick: int | None = None
    # Game-info interstitial settings (only attached when the title is showing).
    game_info_present = False
    info_kill_cooldown: int | None = None
    info_tasks: int | None = None
    info_vote_timer: int | None = None
    info_max_ticks: int | None = None
    # Meeting-call interstitial: icon signals (authoritative) + text fallback.
    meeting_caller_color: str | None = None
    meeting_icon_trigger: str | None = None
    meeting_body_color: str | None = None
    meeting_text_seen = False
    meeting_text_trigger: str | None = None
    meeting_text_caller: str | None = None
    # Game-over roster: icons (x, y, color) and IMP/CREW texts (x, y, role).
    gameover_icon_cells: list[tuple[int, int, str]] = []
    gameover_role_texts: list[tuple[int, int, str]] = []

    for object_id, obj in scene.objects.items():
        sprite = scene.sprites.get(obj.sprite_id)
        if sprite is None:
            continue
        label = sprite.label
        world_x = obj.x + camera_x
        world_y = obj.y + camera_y

        # HUD self-role icons (their object ids are not in the entity ranges).
        if label == LABEL_IMPOSTER_ICON:
            self_role, self_kill_ready = "imposter", True
            continue
        if label == LABEL_IMPOSTER_ICON_COOLDOWN:
            self_role, self_kill_ready = "imposter", False
            continue
        if label == LABEL_GHOST_ICON:
            self_role = "dead"
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
        elif GAMEOVER_ICON_OBJECT_BASE <= object_id < GAMEOVER_ICON_OBJECT_BASE + MAX_PLAYERS:
            # Game-over roster icons: row-paired with the IMP/CREW texts below.
            if label.startswith(PREFIX_PLAYER):
                color, _ = _parse_color_and_facing(label[len(PREFIX_PLAYER) :])
                gameover_icon_cells.append((obj.x, obj.y, color))
            continue
        elif object_id == MEETING_ICON_OBJECT_BASE:
            # Meeting-call interstitial: the caller's player icon.
            if label.startswith(PREFIX_PLAYER):
                meeting_caller_color, _ = _parse_color_and_facing(label[len(PREFIX_PLAYER) :])
            continue
        elif object_id == MEETING_ICON_OBJECT_BASE + 1:
            # Meeting-call interstitial: the reported body, or the button icon.
            if label.startswith(PREFIX_BODY):
                meeting_icon_trigger = "report"
                meeting_body_color = label[len(PREFIX_BODY) :]
            elif label == LABEL_MEETING_BUTTON:
                meeting_icon_trigger = "button"
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
        elif label.startswith(PREFIX_SERVER_TICK):
            tick_value = _parse_bounded_int(label, PREFIX_SERVER_TICK, "")
            if tick_value is not None:
                server_tick = tick_value if server_tick is None else max(server_tick, tick_value)
        elif label in PHASE_TEXTS:
            phase_texts.add(label)
        elif (value := _parse_bounded_int(label, GAME_INFO_PREFIX_KILL_COOLDOWN, "T")) is not None:
            info_kill_cooldown = value
        elif (value := _parse_bounded_int(label, GAME_INFO_PREFIX_TASKS, " EACH")) is not None:
            info_tasks = value
        elif (value := _parse_bounded_int(label, GAME_INFO_PREFIX_VOTE_TIMER, "T")) is not None:
            info_vote_timer = value
        elif (value := _parse_bounded_int(label, GAME_INFO_PREFIX_GAME_TIMER, "T")) is not None:
            info_max_ticks = value
        elif label in (GAMEOVER_TEXT_IMP, GAMEOVER_TEXT_CREW):
            gameover_role_texts.append((obj.x, obj.y, label))
        elif label.endswith(MEETING_TEXT_SUFFIX_REPORTED):
            meeting_text_seen = True
            meeting_text_trigger = "report"
            meeting_text_caller = _meeting_text_caller(label, MEETING_TEXT_SUFFIX_REPORTED)
        elif label.endswith(MEETING_TEXT_SUFFIX_PRESSED):
            meeting_text_seen = True
            meeting_text_trigger = "button"
            meeting_text_caller = _meeting_text_caller(label, MEETING_TEXT_SUFFIX_PRESSED)
        elif label.endswith(MEETING_TEXT_SUFFIX_CALLED):
            meeting_text_seen = True
            meeting_text_caller = _meeting_text_caller(label, MEETING_TEXT_SUFFIX_CALLED)

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

    game_info_present = PHASE_TEXT_GAME_INFO in phase_texts
    game_info = (
        GameInfo(
            kill_cooldown_ticks=info_kill_cooldown,
            tasks_per_player=info_tasks,
            vote_timer_ticks=info_vote_timer,
            max_ticks=info_max_ticks,
        )
        if game_info_present
        else None
    )

    # Meeting call: the 9800/9801 icons are authoritative (they exist only on the
    # meeting-call interstitial). The text lines are the fallback for the icon-less
    # "Someone ..." case — gated on no voting UI / chat being up, so a literal chat
    # message that happens to end in " reported" can never fake the phase.
    voting_ui_active = cursor or skip_cursor or timer or bool(dots)
    meeting_call: MeetingCall | None = None
    if meeting_caller_color is not None or meeting_icon_trigger is not None:
        meeting_call = MeetingCall(
            caller_color=meeting_caller_color,
            trigger=meeting_icon_trigger or meeting_text_trigger,
            body_color=meeting_body_color,
        )
    elif meeting_text_seen and not voting_ui_active and not chat_icon_rows:
        meeting_call = MeetingCall(
            caller_color=meeting_text_caller,
            trigger=meeting_text_trigger,
            body_color=None,
        )

    game_over_roles = _pair_gameover_roles(gameover_icon_cells, gameover_role_texts)

    return ResolvedScene(
        tick=tick,
        camera_ready=scene.camera_ready,
        camera_x=camera_x,
        camera_y=camera_y,
        self_role=self_role,
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
        reveal_player_colors=frozenset(reveal_colors),
        chat_lines=chat_lines,
        census=census,
        ejected_color=ejected_color,
        server_tick=server_tick,
        game_info=game_info,
        meeting_call=meeting_call,
        game_over_roles=game_over_roles,
    )


# A game-over IMP/CREW text pairs with the roster icon on the same row: the icon
# is drawn ~16 px left of the text at nearly the same y (global.nim GameOver
# layout: icon x = baseX+4, text x = baseX+19, both within the 14 px row).
GAMEOVER_PAIR_MAX_DY = 7
GAMEOVER_PAIR_MAX_DX = 32


def _pair_gameover_roles(
    icon_cells: list[tuple[int, int, str]], role_texts: list[tuple[int, int, str]]
) -> dict[str, str]:
    """Pair each game-over roster icon with its row's IMP/CREW text → role map."""

    roles: dict[str, str] = {}
    used: set[int] = set()
    for icon_x, icon_y, color in icon_cells:
        best: int | None = None
        best_key: tuple[int, int] | None = None
        for i, (text_x, text_y, _role) in enumerate(role_texts):
            if i in used:
                continue
            dy = abs(text_y - icon_y)
            dx = text_x - icon_x  # the text sits to the icon's right
            if dy > GAMEOVER_PAIR_MAX_DY or not (0 <= dx <= GAMEOVER_PAIR_MAX_DX):
                continue
            key = (dy, dx)
            if best_key is None or key < best_key:
                best, best_key = i, key
        if best is None:
            continue
        used.add(best)
        roles[color] = "imposter" if role_texts[best][2] == GAMEOVER_TEXT_IMP else "crewmate"
    return roles


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
