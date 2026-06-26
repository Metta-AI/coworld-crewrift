"""Resolved per-tick entities produced by :mod:`.resolve` (design §4).

Frozen pydantic models. These are the structured, vision-free view of the scene:
each carries a stable object id, world coordinates (valid when ``camera_ready``),
and the classified label fields. ``Percept`` (in ``types.py``) embeds these.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Facing = Literal["left", "right"]
SelfRole = Literal["crewmate", "imposter", "dead"]


class VisiblePlayer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    object_id: int
    color: str
    facing: Facing
    world_x: int
    world_y: int


class VisibleBody(BaseModel):
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


class GameInfo(BaseModel):
    """Live game-config values read off the pre-game GAME INFO interstitial.

    Each field is ``None`` when its line was absent/unparseable; ``max_ticks`` is
    ``None`` for "GAME TIMER NONE" (no limit). Only attached to the resolved scene
    when the "GAME INFO" title itself is on screen.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kill_cooldown_ticks: int | None = None
    tasks_per_player: int | None = None
    vote_timer_ticks: int | None = None
    max_ticks: int | None = None


MeetingTrigger = Literal["report", "button"]


class MeetingCall(BaseModel):
    """The meeting-call interstitial: who opened this meeting, and how.

    ``caller_color`` comes from the caller icon (or the text line); ``None`` when
    the caller has left the game ("Someone ..."). ``trigger`` is ``report`` /
    ``button`` (``None`` when the cause is unknown); ``body_color`` is the
    reported body's color for report-triggered meetings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    caller_color: str | None = None
    trigger: MeetingTrigger | None = None
    body_color: str | None = None


# A vote dot whose ``target`` is this sentinel is a skip vote (the game's vote
# value −2 for skip; sim.nim).
SKIP_VOTE_TARGET = -2


class VoteDot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    voter: int
    target: int  # a player slot, or SKIP_VOTE_TARGET (−2) for a skip vote

    @property
    def is_skip(self) -> bool:
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
    # Player colors shown in the role-reveal icons — the imposter team when the
    # viewer is an imposter (design §7.2 teammate identification).
    reveal_player_colors: frozenset[str] = frozenset()

    # Social signals from the voting / vote-result screens (design §4.3).
    chat_lines: tuple[ChatLine, ...] = ()
    census: tuple[CensusEntry, ...] = ()
    # The color ejected by the just-finished vote, or ``None`` (vote skipped / not
    # a VoteResult frame).
    ejected_color: str | None = None

    # The server's authoritative tick from the per-tick "tick <N>" marker sprite
    # (upstream 2026-06-10), or ``None`` on older servers / before it arrives.
    server_tick: int | None = None
    # Live game config from the pre-game GAME INFO interstitial, or ``None`` when
    # that screen is not showing (including on older servers without it).
    game_info: GameInfo | None = None
    # The meeting-call interstitial (who opened the meeting and how), or ``None``
    # outside that screen (including on older servers without it).
    meeting_call: MeetingCall | None = None
    # Game-over roster role census, paired from the GameOver icons + IMP/CREW
    # texts: color → "imposter" | "crewmate". Empty outside GameOver.
    game_over_roles: dict[str, str] = Field(default_factory=dict)
