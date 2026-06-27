"""Resolved per-tick entities produced by :mod:`.resolve` (design §4).

The output data shapes of the perception layer (perception → belief → suspicion →
strategy → modes → action): the structured, vision-free view of one scene tick that
the belief fold reads. Pure data — no logic. Frozen pydantic models, each carrying
a stable object id, world coordinates (valid when ``camera_ready``), and the
classified label fields. ``ResolvedScene`` is the aggregate; ``Percept`` (in
``types.py``) embeds it.

Collaborators
-------------
Relies on: ``pydantic`` only (frozen ``BaseModel``); no other crewborg modules,
  so this stays an import-leaf.
Used by:
  - ``perception.resolve.resolve_scene`` — constructs every model here.
  - ``types.py`` — embeds ``ResolvedScene`` / ``VotingState`` in ``Percept`` and
    re-exports the entity types the belief fold and modes consume.
Emits / touches: nothing — these are immutable value objects.

Modifying this file: every model is ``frozen=True, extra="forbid"`` on purpose —
the resolved scene is an immutable snapshot, and ``extra="forbid"`` makes a
field-name drift between here and ``resolve.py`` fail loudly instead of silently
dropping data. Keep both invariants. Coordinate units: ``world_*`` are world pixels
(collision point for players/bodies); ``screen`` is camera-relative screen pixels.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Facing = Literal["left", "right"]
SelfRole = Literal["crewmate", "imposter", "dead"]


class VisiblePlayer(BaseModel):
    """A live (non-body) player sprite on screen: color, facing, world collision point.

    ``world_x``/``world_y`` are the recovered collision point (world pixels), so
    they match the server's report/kill range checks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: int
    color: str
    facing: Facing
    world_x: int
    world_y: int


class VisibleBody(BaseModel):
    """A killed player's body sprite on screen: color + world collision point (reportable)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: int
    color: str
    world_x: int
    world_y: int


class ChatLine(BaseModel):
    """One chat message visible on the voting screen (design §4.3).

    ``speaker_color`` is recovered from the speaker icon rendered alongside the
    text; ``None`` if no icon could be matched to the line.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    speaker_color: str | None
    text: str


class CensusEntry(BaseModel):
    """One player's alive/dead state from the voting candidate grid (design §4.3).

    The grid renders every player as a crew sprite (alive) or body sprite (dead),
    tagged by color — an authoritative per-meeting alive/dead census.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    color: str
    alive: bool


class TaskSignal(BaseModel):
    """One incomplete assigned task's signal (crewmate-only; design §4.2).

    A ``bubble`` is on/near-screen and gives an exact ``world`` position; an
    ``arrow`` is off-screen and gives bearing only via its screen-edge pixel
    (``screen``), with no world position.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_index: int
    kind: Literal["bubble", "arrow"]
    world: tuple[int, int] | None = None
    screen: tuple[int, int]


# A vote dot whose ``target`` is this sentinel is a skip vote (the game's vote
# value −2 for skip; sim.nim).
SKIP_VOTE_TARGET = -2


class VoteDot(BaseModel):
    """One cast vote on the voting screen: ``voter`` slot → ``target`` slot.

    ``target`` is a player slot index, or ``SKIP_VOTE_TARGET`` (−2) for a skip vote.
    Slots are the candidate-grid order, not colors — callers map via the candidate
    grid. Resolved from the vote-dot object-id ranges (normal vs. skip).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    voter: int
    target: int  # a player slot, or SKIP_VOTE_TARGET (−2) for a skip vote

    @property
    def is_skip(self) -> bool:
        """True if this dot is a skip vote (``target == SKIP_VOTE_TARGET``)."""
        return self.target == SKIP_VOTE_TARGET


class VoteCandidate(BaseModel):
    """One cell of the voting candidate grid (design §4.3): a player's vote slot.

    ``slot`` is the cursor index for this player (the candidate-grid order); the
    cursor reaches them by stepping to it. Used for targeted voting.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: int
    color: str
    alive: bool


class VotingState(BaseModel):
    """Voting-UI presence and tally (design §4.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor_present: bool = False
    skip_cursor_present: bool = False
    timer_present: bool = False
    self_marker_color: str | None = None
    dots: tuple[VoteDot, ...] = ()
    # The candidate grid (one cell per player) and which player slot the cursor is
    # currently on (``None`` if not on a player cell — e.g. on skip). These drive
    # targeted voting: map a target color → its slot, then step the cursor to it.
    candidates: tuple[VoteCandidate, ...] = ()
    cursor_slot: int | None = None

    @property
    def active(self) -> bool:
        """True when a voting UI is on screen — any cursor, the timer, or cast dots."""
        return self.cursor_present or self.skip_cursor_present or self.timer_present or bool(self.dots)


class ResolvedScene(BaseModel):
    """The fully resolved per-tick view assembled from the scene tables."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tick: int
    camera_ready: bool
    camera_x: int
    camera_y: int

    self_role: SelfRole | None = None
    self_kill_ready: bool | None = None
    # Self is the camera center, not an object; world position is the camera plus
    # a fixed offset (design §3.2). Valid only when ``camera_ready``.
    self_world_x: int | None = None
    self_world_y: int | None = None

    visible_players: tuple[VisiblePlayer, ...] = ()
    visible_bodies: tuple[VisibleBody, ...] = ()
    task_signals: tuple[TaskSignal, ...] = ()

    active_task_progress_pct: int | None = None
    crew_tasks_remaining: int | None = None

    voting: VotingState = Field(default_factory=VotingState)
    phase_texts: frozenset[str] = frozenset()
    # The MeetingCall interstitial (game 4b9297d): "<Color> reported|pressed|called"
    # text names the meeting caller in the player view. ``kind`` is "body" (a
    # report), "button" (the emergency button), or "unknown".
    meeting_caller_color: str | None = None
    meeting_call_kind: str | None = None
    # Player colors shown in the role-reveal icons — the imposter team when the
    # viewer is an imposter (design §7.2 teammate identification).
    reveal_player_colors: frozenset[str] = frozenset()

    # Social signals from the voting / vote-result screens (design §4.3).
    chat_lines: tuple[ChatLine, ...] = ()
    census: tuple[CensusEntry, ...] = ()
    # The color ejected by the just-finished vote, or ``None`` (vote skipped / not
    # a VoteResult frame).
    ejected_color: str | None = None
