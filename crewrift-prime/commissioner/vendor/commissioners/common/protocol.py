from __future__ import annotations

import json
from secrets import randbelow
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

_STATE_MAX_BYTES = 10 * 1024 * 1024
EPISODE_SEED_MAX = 2**31 - 1


def random_episode_seed() -> int:
    return randbelow(EPISODE_SEED_MAX + 1)


class LeagueInfo(BaseModel):
    id: UUID
    commissioner_key: str | None = None
    commissioner_config: dict[str, Any] | None = None


class DivisionInfo(BaseModel):
    id: UUID
    name: str
    level: int
    type: str = "competition"


class DivisionConfig(BaseModel):
    name: str
    level: int
    type: str = "competition"
    description: str | None = None
    previous_name: str | None = None


class MembershipInfo(BaseModel):
    id: UUID
    league_id: UUID
    division_id: UUID
    policy_version_id: UUID
    player_id: str | None = None
    status: str = "competing"
    substatus: str | None = None
    is_champion: bool = False


class RecentResult(BaseModel):
    round_id: UUID
    division_id: UUID
    round_number: int
    policy_version_id: UUID
    rank: int
    score: float


class VariantInfo(BaseModel):
    id: str
    name: str
    game_config: dict[str, Any]


class EpisodeRequest(BaseModel):
    request_id: str
    variant_id: str
    policy_version_ids: list[UUID]
    seed: int = Field(default_factory=random_episode_seed)
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("seed", mode="before")
    @classmethod
    def default_seed(cls, value: Any) -> Any:
        if value is None:
            return random_episode_seed()
        return value


class EpisodeScore(BaseModel):
    policy_version_id: UUID
    player_id: str | None = None
    score: float


class RankingEntry(BaseModel):
    policy_version_id: UUID
    player_id: str | None = None
    rank: int
    score: float
    result_metadata: dict[str, Any] = Field(default_factory=dict)


class DivisionRanking(BaseModel):
    division_id: UUID
    rankings: list[RankingEntry]


class MembershipChange(BaseModel):
    membership_id: UUID
    from_division_id: UUID
    to_division_id: UUID | None = None
    is_active: bool = True
    reason: str


class PolicyMembershipEventEvidence(BaseModel):
    type: str
    public_id: str | None = None
    title: str
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyMembershipEventChange(BaseModel):
    league_policy_membership_id: UUID
    from_division_id: UUID | None = None
    to_division_id: UUID | None = None
    status: str
    substatus: str | None = None
    reason: str
    end_time: str | None = None
    notes: str | None = None
    evidence: list[PolicyMembershipEventEvidence] = Field(default_factory=list)


class StageConfig(BaseModel):
    label: str = "Round"
    num_episodes: int = Field(default=1, gt=0)
    min_episodes_per_entrant: int | None = Field(default=None, gt=0)


class RoundConfig(BaseModel):
    mock_scores: dict[UUID, float] | None = None
    stages: list[StageConfig] | None = None
    entrant_policy_version_ids: list[UUID] | None = None


class RoundInfo(BaseModel):
    id: UUID
    public_id: str | None = None
    division_id: UUID
    round_number: int
    status: str
    round_config: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class RoundResultInfo(BaseModel):
    round_id: UUID
    policy_version_id: UUID
    rank: int
    score: float
    result_metadata: dict[str, Any] = Field(default_factory=dict)


class LeaderboardRoundResultInfo(RoundResultInfo):
    player_id: str
    player_name: str | None = None


class RoundSpec(BaseModel):
    division_id: UUID
    round_config: RoundConfig
    execution_backend: str = "mock"
    notes: str | None = None


class DivisionLeaderboardEntry(BaseModel):
    player_id: str
    player_name: str | None = None
    rank: int
    score: float
    rounds_played: int
    policy_version_ids: set[UUID] = Field(default_factory=set)
    recent_rounds: list[dict[str, Any]] | None = None


LeaderboardValue = str | int | float | bool | None


class DivisionLeaderboardAxis(BaseModel):
    key: str
    label: str | None = None


class DivisionLeaderboardColumn(BaseModel):
    key: str
    label: str | None = None
    value_type: str = "number"
    sort: str | None = None


class DivisionLeaderboardRow(BaseModel):
    subject_type: str = "player"
    subject_id: str
    subject_name: str | None = None
    values: dict[str, LeaderboardValue] = Field(default_factory=dict)
    policy_version_ids: set[UUID] = Field(default_factory=set)
    recent_rounds: list[dict[str, Any]] | None = None


class DivisionLeaderboardView(BaseModel):
    key: str = "score"
    title: str | None = None
    description: str | None = None
    axis_values: dict[str, str] = Field(default_factory=dict)
    columns: list[DivisionLeaderboardColumn] = Field(default_factory=list)
    rows: list[DivisionLeaderboardRow] = Field(default_factory=list)


class DivisionLeaderboard(BaseModel):
    division_id: UUID
    default_view_key: str = "score"
    axes: list[DivisionLeaderboardAxis] = Field(default_factory=list)
    views: list[DivisionLeaderboardView] = Field(default_factory=list)


def _row_from_division_leaderboard_entry(entry: DivisionLeaderboardEntry) -> DivisionLeaderboardRow:
    return DivisionLeaderboardRow(
        subject_type="player",
        subject_id=entry.player_id,
        subject_name=entry.player_name,
        values={"rank": entry.rank, "score": entry.score, "rounds_played": entry.rounds_played},
        policy_version_ids=entry.policy_version_ids,
        recent_rounds=entry.recent_rounds,
    )


def division_leaderboard_from_entries(
    *,
    division_id: UUID,
    entries: list[DivisionLeaderboardEntry],
    title: str = "Score",
) -> DivisionLeaderboard:
    """Build the platform's generic-view leaderboard from player-keyed entries.

    Mirrors the platform's own ``rankings -> views/rows`` shape so a leaderboard
    the commissioner publishes on ``RoundComplete`` is persisted verbatim instead
    of being re-synthesized by the platform's win-count compatibility shim.
    """
    view = DivisionLeaderboardView(
        key="score",
        title=title,
        axis_values={"metric": "score", "timeframe": "legacy"},
        columns=[
            DivisionLeaderboardColumn(key="rank", label="Rank", value_type="integer", sort="asc"),
            DivisionLeaderboardColumn(key="score", label="Score", value_type="number", sort="desc"),
            DivisionLeaderboardColumn(key="rounds_played", label="Rounds Played", value_type="integer"),
        ],
        rows=[_row_from_division_leaderboard_entry(entry) for entry in entries],
    )
    return DivisionLeaderboard(
        division_id=division_id,
        default_view_key="score",
        axes=[
            DivisionLeaderboardAxis(key="metric", label="Metric"),
            DivisionLeaderboardAxis(key="timeframe", label="Timeframe"),
        ],
        views=[view],
    )



class DivisionDescription(BaseModel):
    round_schedule: str | None = None
    next_round: str | None = None
    round_structure: str | None = None
    leaderboard_rules: str | None = None
    scoring_mechanics: str | None = None


class RoundStart(BaseModel):
    round_id: UUID
    round_number: int
    league: LeagueInfo
    divisions: list[DivisionInfo]
    memberships: list[MembershipInfo]
    recent_results: list[RecentResult]
    variants: list[VariantInfo]
    state: Any = None

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "round_start"
        return data


class EpisodeAccepted(BaseModel):
    request_ids: list[str]

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "episodes_accepted"
        return data


class EpisodesRejected(BaseModel):
    request_ids: list[str]
    errors: dict[str, str]

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "episodes_rejected"
        return data


class EpisodeResult(BaseModel):
    request_id: str
    scores: list[EpisodeScore]
    game_results: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "episode_result"
        return data


class EpisodeFailed(BaseModel):
    request_id: str
    error: str

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "episode_failed"
        return data


class EpisodeCancel(BaseModel):
    request_id: str
    reason: str

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "episode_cancel"
        return data


class RoundAbort(BaseModel):
    reason: str

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "round_abort"
        return data


class ScheduleEpisodes(BaseModel):
    episodes: list[EpisodeRequest]

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "schedule_episodes"
        return data


class CommissionerCalcStep(BaseModel):
    """One step in deriving an entrant's outcome: a human label + the value it produced."""

    label: str
    value: Any = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    passed: bool | None = None


class CommissionerEntrantReport(BaseModel):
    """How one entrant's round outcome was calculated, end to end (the scoring trace)."""

    policy_version_id: UUID
    player_id: str | None = None
    outcome: str
    score: float | None = None
    passed: bool | None = None
    steps: list[CommissionerCalcStep] = Field(default_factory=list)
    summary: str | None = None


class CommissionerRoundReport(BaseModel):
    """Structured, game-agnostic explanation of how a commissioner scored a round.

    Mirrors ``coworld.commissioner.protocol.CommissionerRoundReport`` so this
    commissioner's wire output is parsed by the platform's observability layer.
    """

    rule_id: str
    rule_description: str
    division_id: UUID | None = None
    entrants: list[CommissionerEntrantReport] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    # Self-contained, safe HTML the commissioner authors to render its own view of
    # the round. Served under the platform's safe-render CSP in a sandboxed iframe.
    render_html: str | None = None


class RoundComplete(BaseModel):
    results: list[DivisionRanking] = Field(default_factory=list)
    leaderboards: list[DivisionLeaderboard] = Field(default_factory=list)
    policy_membership_events: list[PolicyMembershipEventChange] = Field(default_factory=list)
    membership_changes: list[MembershipChange] = Field(default_factory=list)
    round_display: dict[str, Any] | None = None
    state: Any = None
    # Structured observability report (how this round was scored). Parsed by the
    # platform into rounds.commissioner_report and rendered in the Observatory.
    observability: CommissionerRoundReport | None = None

    @field_validator("state")
    @classmethod
    def validate_state_size(cls, value: Any) -> Any:
        if value is None:
            return value
        serialized = json.dumps(value)
        size_bytes = len(serialized.encode())
        if size_bytes > _STATE_MAX_BYTES:
            raise ValueError(f"state must not exceed 10 MB (got {size_bytes} bytes)")
        return value

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "round_complete"
        return data


class ScheduleRoundsRequest(BaseModel):
    league: LeagueInfo
    divisions: list[DivisionInfo]
    active_memberships: list[MembershipInfo]
    recent_rounds: list[RoundInfo]

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "schedule_rounds_request"
        return data


class ScheduleRoundsResponse(BaseModel):
    rounds: list[RoundSpec] = Field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "schedule_rounds_response"
        return data


class LeagueMigrationConfigRequest(BaseModel):
    league: LeagueInfo
    divisions: list[DivisionInfo]

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "league_migration_config_request"
        return data


class LeagueMigrationConfigResponse(BaseModel):
    divisions: list[DivisionConfig] = Field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "league_migration_config_response"
        return data


class LeagueMigrationRequest(BaseModel):
    league: LeagueInfo
    divisions: list[DivisionInfo]
    memberships: list[MembershipInfo]

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "league_migration_request"
        return data


class LeagueMigrationResponse(BaseModel):
    policy_membership_events: list[PolicyMembershipEventChange] = Field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "league_migration_response"
        return data


class RankDivisionRequest(BaseModel):
    league: LeagueInfo
    division: DivisionInfo
    completed_rounds: list[RoundInfo]
    recent_rounds: list[RoundInfo]
    round_results: list[LeaderboardRoundResultInfo]

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "rank_division_request"
        return data


class RankDivisionResponse(BaseModel):
    rankings: list[DivisionLeaderboardEntry] = Field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "rank_division_response"
        return data


class DescribeDivisionRequest(BaseModel):
    league: LeagueInfo
    division: DivisionInfo
    active_memberships: list[MembershipInfo]
    recent_rounds: list[RoundInfo]

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "describe_division_request"
        return data


class DescribeDivisionResponse(BaseModel):
    description: DivisionDescription

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "describe_division_response"
        return data


class RoundCompletedRequest(BaseModel):
    league: LeagueInfo
    division: DivisionInfo
    all_divisions: list[DivisionInfo]
    round_config: RoundConfig
    round_results: list[RoundResultInfo]
    division_memberships: list[MembershipInfo]
    recent_results: list[RoundResultInfo]
    commissioner_config: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "round_completed_request"
        return data


class EpisodeCompletedRequest(BaseModel):
    round_start: RoundStart
    episode_result: EpisodeResult | None = None
    episode_failed: EpisodeFailed | None = None
    completed_episode_results: list[EpisodeResult] = Field(default_factory=list)
    failed_episodes: list[EpisodeFailed] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_completed_event(self) -> EpisodeCompletedRequest:
        if (self.episode_result is None) == (self.episode_failed is None):
            raise ValueError("exactly one of episode_result or episode_failed must be set")
        return self

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "episode_completed_request"
        return data


class RoundCompletedResponse(BaseModel):
    policy_membership_events: list[PolicyMembershipEventChange] = Field(default_factory=list)
    membership_changes: list[MembershipChange] = Field(default_factory=list)
    follow_up_rounds: list[RoundSpec] = Field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "round_completed_response"
        return data


class EpisodeCompletedResponse(BaseModel):
    episodes: list[EpisodeRequest] = Field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["type"] = "episode_completed_response"
        return data


PlatformMessage = (
    RoundStart
    | EpisodeAccepted
    | EpisodesRejected
    | EpisodeResult
    | EpisodeFailed
    | RoundAbort
    | ScheduleRoundsRequest
    | LeagueMigrationConfigRequest
    | LeagueMigrationRequest
    | RankDivisionRequest
    | DescribeDivisionRequest
    | RoundCompletedRequest
    | EpisodeCompletedRequest
)

CommissionerMessageType = (
    ScheduleEpisodes
    | EpisodeCancel
    | RoundComplete
    | ScheduleRoundsResponse
    | LeagueMigrationConfigResponse
    | LeagueMigrationResponse
    | RankDivisionResponse
    | DescribeDivisionResponse
    | RoundCompletedResponse
    | EpisodeCompletedResponse
)

_COMMISSIONER_MESSAGE_TYPES: dict[str, type[CommissionerMessageType]] = {
    "schedule_episodes": ScheduleEpisodes,
    "episode_cancel": EpisodeCancel,
    "round_complete": RoundComplete,
    "schedule_rounds_response": ScheduleRoundsResponse,
    "league_migration_config_response": LeagueMigrationConfigResponse,
    "league_migration_response": LeagueMigrationResponse,
    "rank_division_response": RankDivisionResponse,
    "describe_division_response": DescribeDivisionResponse,
    "round_completed_response": RoundCompletedResponse,
    "episode_completed_response": EpisodeCompletedResponse,
}


class CommissionerMessage:
    @staticmethod
    def from_json(data: dict[str, Any]) -> CommissionerMessageType:
        msg_type = data["type"]
        cls = _COMMISSIONER_MESSAGE_TYPES.get(msg_type)
        if cls is None:
            raise ValueError(f"Unknown commissioner message type: {msg_type!r}")
        return cls.model_validate({key: value for key, value in data.items() if key != "type"})
