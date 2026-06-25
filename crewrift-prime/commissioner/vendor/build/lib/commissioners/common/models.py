from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum
from os import getenv
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

PlayerId = str
RoundId = str
SubmissionId = str

DIVISION_TYPE_COMPETITION = "competition"
DIVISION_TYPE_STAGING = "staging"


class PolicyMembershipStatus(str, Enum):
    submitted = "submitted"
    qualifying = "qualifying"
    competing = "competing"
    disqualified = "disqualified"

    @classmethod
    def live(cls) -> tuple["PolicyMembershipStatus", ...]:
        return (cls.submitted, cls.qualifying, cls.competing)


POLICY_MEMBERSHIP_SUBSTATUS_ACTIVE = "active"
POLICY_MEMBERSHIP_SUBSTATUS_BENCHED = "benched"
POLICY_MEMBERSHIP_SUBSTATUS_CHAMPION = "champion"
POLICY_MEMBERSHIP_SUBSTATUS_CRASH = "crash"
POLICY_MEMBERSHIP_SUBSTATUS_INACTIVE = "inactive"


def policy_membership_is_champion(status: PolicyMembershipStatus | str, is_champion: bool) -> bool:
    return status == PolicyMembershipStatus.competing and is_champion


class RoundExecutionBackend(StrEnum):
    mock = "mock"
    dispatch = "dispatch"


class DivisionCommissionerDescriptionPublic(BaseModel):
    round_schedule: str | None = None
    next_round: str | None = None
    round_structure: str | None = None
    leaderboard_rules: str | None = None
    scoring_mechanics: str | None = None


class LeaderboardRecentRoundPublic(BaseModel):
    id: RoundId
    round_number: int
    status: str
    rank: int | None = None
    score: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class League(BaseModel):
    id: UUID
    commissioner_key: str
    commissioner_config: dict[str, Any] | None = None


class Division(BaseModel):
    id: UUID
    name: str
    level: int
    league_id: UUID
    type: str = DIVISION_TYPE_COMPETITION


class DivisionConfig(BaseModel):
    name: str
    level: int
    type: str = DIVISION_TYPE_COMPETITION
    description: str | None = None
    previous_name: str | None = None


class LeaguePolicyMembership(BaseModel):
    id: UUID
    league_id: UUID
    division_id: UUID
    policy_version_id: UUID
    player_id: PlayerId | None = None
    status: PolicyMembershipStatus = PolicyMembershipStatus.competing
    substatus: str | None = None
    is_champion: bool = False


class PolicyPool(BaseModel):
    id: UUID
    label: str = "Round"
    pool_type: str = "round"
    config: dict[str, Any] = Field(default_factory=dict)


class PolicyPoolEntry(BaseModel):
    pool_id: UUID
    policy_version_id: UUID
    player_id: PlayerId | None = None
    seed_order: int


class Round(BaseModel):
    id: UUID
    public_id: RoundId | None = None
    division_id: UUID
    round_number: int
    commissioner_key: str
    round_config: dict[str, Any] = Field(default_factory=dict)
    status: str = "running"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RoundResult(BaseModel):
    round_id: UUID
    policy_version_id: UUID
    rank: int
    score: float
    result_metadata: dict[str, Any] = Field(default_factory=dict)

PLACEMENT_DRY_RUN_POOL_TYPE = "placement_dry_run"
DIVISION_LEADERBOARD_SCORE_EWMA_HALFLIFE_HOURS = 2
DIVISION_LEADERBOARD_SCORE_EWMA_HALFLIFE = timedelta(hours=DIVISION_LEADERBOARD_SCORE_EWMA_HALFLIFE_HOURS)


# ---------------------------------------------------------------------------
# Pool / episode / round models
# ---------------------------------------------------------------------------


class PoolPlan(BaseModel):
    label: str
    pool_type: str
    config: dict[str, Any] = Field(default_factory=dict)


class PoolEntryPlan(BaseModel):
    league_policy_membership_id: UUID | None = None
    policy_version_id: UUID
    player_id: PlayerId | None = None
    seed_order: int


class RoundPolicyScore(BaseModel):
    policy_version_id: UUID
    player_id: PlayerId | None = None
    score: float


class EpisodeResult(BaseModel):
    episode_request_id: UUID
    scores: list[RoundPolicyScore]
    game_results: dict[str, Any] | None = None


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
    end_time: datetime | None = None
    notes: str | None = None
    evidence: list[PolicyMembershipEventEvidence] = Field(default_factory=list)


class V2StageConfig(BaseModel):
    label: str = "Round"
    num_episodes: int = Field(default=1, gt=0)
    min_episodes_per_entrant: int | None = Field(default=None, gt=0)
    # When true, every seat in an episode is filled by a single entrant playing
    # against copies of itself, and each entrant gets its own episodes. Used for
    # qualifiers so a policy's score reflects only its own play, not its opponents'.
    self_play: bool = False


class V2RoundConfig(BaseModel):
    mock_scores: dict[UUID, float] | None = None
    stages: list[V2StageConfig] | None = None
    entrant_policy_version_ids: list[UUID] | None = None

    @model_validator(mode="after")
    def require_single_stage(self) -> V2RoundConfig:
        if self.stages is not None and len(self.stages) != 1:
            raise ValueError("V2RoundConfig.stages must have exactly one stage")
        return self


class PoolConfig(BaseModel):
    num_episodes: int = Field(default=1, gt=0)
    min_episodes_per_entrant: int | None = Field(default=None, gt=0)
    mock_scores: dict[UUID, float] | None = None
    self_play: bool = False


class RoundSchedulingConfig(BaseModel):
    schedule_interval_minutes: int = Field(default=10, gt=0)
    default_execution_backend: str = "mock"
    minimum_champions: int = Field(default=2, gt=0)
    qualifiers_minimum_champions: int = Field(default=1, gt=0)
    stages: list[V2StageConfig] | None = None
    qualifier_stages: list[V2StageConfig] | None = None

    @model_validator(mode="after")
    def require_single_stage(self) -> RoundSchedulingConfig:
        if self.stages is not None and len(self.stages) != 1:
            raise ValueError("RoundSchedulingConfig.stages must have exactly one stage")
        if self.qualifier_stages is not None and len(self.qualifier_stages) != 1:
            raise ValueError("RoundSchedulingConfig.qualifier_stages must have exactly one stage")
        return self

    def effective_execution_backend(self) -> RoundExecutionBackend:
        backend = RoundExecutionBackend(self.default_execution_backend)
        if backend == RoundExecutionBackend.mock and getenv("LOCAL_DEV", "").lower() not in {"1", "true", "yes"}:
            return RoundExecutionBackend.dispatch
        return backend


DEFAULT_STAGES = [V2StageConfig(label="Round", num_episodes=1)]
AMONG_THEM_DEFAULT_STAGE = V2StageConfig(
    label="Round",
    num_episodes=100,
    min_episodes_per_entrant=100,
)
AMONG_THEM_DIRT_STAGE = V2StageConfig(
    label="Round",
    num_episodes=8,
    min_episodes_per_entrant=8,
)
# Qualifiers gate entry into the competition; they only need enough games to judge
# pass/fail, not the full ranking power of a competition round. These are self-play,
# so a single episode already yields one seat of signal per agent for that policy;
# a few episodes per entrant is plenty. Keeping this small bounds qualifier round
# size, which for self-play is num_entrants * min_episodes_per_entrant.
AMONG_THEM_QUALIFIER_STAGE = V2StageConfig(
    label="Round",
    num_episodes=2,
    min_episodes_per_entrant=2,
    self_play=True,
)


class AmongThemSchedulingConfig(RoundSchedulingConfig):
    schedule_interval_minutes: int = Field(default=10, gt=0)
    default_execution_backend: str = "dispatch"
    stages: list[V2StageConfig] | None = Field(default_factory=lambda: [AMONG_THEM_DEFAULT_STAGE])
    dirt_stages: list[V2StageConfig] | None = Field(default_factory=lambda: [AMONG_THEM_DIRT_STAGE])
    qualifier_stages: list[V2StageConfig] | None = Field(default_factory=lambda: [AMONG_THEM_QUALIFIER_STAGE])
    # Dirt exists to evaluate unproven policies, so it needs to run with very few entrants.
    dirt_minimum_champions: int = Field(default=2, gt=0)
    dirt_division_name: str = "Dirt"
    wood_division_name: str = "Wood"


# ---------------------------------------------------------------------------
# Snapshot models — lightweight copies of ORM objects for commissioner methods.
# ---------------------------------------------------------------------------


class LeagueSnapshot(BaseModel):
    id: UUID
    commissioner_key: str
    commissioner_config: dict[str, Any] | None

    @staticmethod
    def from_orm(league: League) -> LeagueSnapshot:
        return LeagueSnapshot(
            id=league.id,
            commissioner_key=league.commissioner_key,
            commissioner_config=league.commissioner_config,
        )


class DivisionSnapshot(BaseModel):
    id: UUID
    name: str
    level: int
    league_id: UUID
    type: str = DIVISION_TYPE_COMPETITION

    @staticmethod
    def from_orm(division: Division) -> DivisionSnapshot:
        return DivisionSnapshot(
            id=division.id,
            name=division.name,
            level=division.level,
            league_id=division.league_id,
            type=division.type,
        )


class MembershipSnapshot(BaseModel):
    id: UUID
    league_id: UUID
    division_id: UUID
    policy_version_id: UUID
    player_id: PlayerId | None
    status: PolicyMembershipStatus = PolicyMembershipStatus.competing
    substatus: str | None = None
    is_champion: bool = False

    @property
    def is_active_champion(self) -> bool:
        return policy_membership_is_champion(self.status, self.is_champion)


class RoundSnapshot(BaseModel):
    id: UUID
    public_id: RoundId
    division_id: UUID
    round_number: int
    status: str
    round_config: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @staticmethod
    def from_orm(r: Round) -> RoundSnapshot:
        return RoundSnapshot(
            id=r.id,
            public_id=r.round_id,
            division_id=r.division_id,
            round_number=r.round_number,
            status=r.status,
            round_config=r.round_config,
            created_at=r.created_at,
            started_at=r.started_at,
            completed_at=r.completed_at,
        )


class RoundResultSnapshot(BaseModel):
    round_id: UUID
    policy_version_id: UUID
    rank: int
    score: float
    result_metadata: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def from_orm(r: RoundResult) -> RoundResultSnapshot:
        return RoundResultSnapshot(
            round_id=r.round_id,
            policy_version_id=r.policy_version_id,
            rank=r.rank,
            score=r.score,
            result_metadata=r.result_metadata or {},
        )


class PolicyTransitionObservation(BaseModel):
    scheduled_episodes: int = 0
    completed_episodes: int = 0
    score: float = 0.0
    failed_episodes: int = 0
    failed_request_ids: list[str] = Field(default_factory=list)
    failure_error_samples: list[str] = Field(default_factory=list)


class DivisionLeaderboardSnapshot(BaseModel):
    player_id: PlayerId
    player_name: str | None = None
    rank: int
    score: float
    rounds_played: int
    policy_version_ids: set[UUID] = Field(default_factory=set)
    recent_rounds: list[LeaderboardRecentRoundPublic] | None = None


class _LeaderboardAgg(BaseModel):
    player_id: PlayerId
    player_name: str | None = None
    policy_version_ids: set[UUID] = Field(default_factory=set)
    weighted_score_sum: float = 0.0
    weight_sum: float = 0.0

    def score(self) -> float:
        return self.weighted_score_sum / self.weight_sum


class LeaderboardRoundResultSnapshot(RoundResultSnapshot):
    player_id: PlayerId
    player_name: str | None = None


# ---------------------------------------------------------------------------
# Scheduling context and result models
# ---------------------------------------------------------------------------


class RoundSpec(BaseModel):
    """A round the commissioner wants the pipeline to create."""

    division_id: UUID
    round_config: V2RoundConfig
    execution_backend: str = "mock"
    notes: str | None = None


class ScheduleContext(BaseModel):
    league: LeagueSnapshot
    divisions: list[DivisionSnapshot]
    active_memberships: list[MembershipSnapshot]
    recent_rounds: list[RoundSnapshot]


class LeagueMigrationConfigContext(BaseModel):
    league: LeagueSnapshot
    divisions: list[DivisionSnapshot]


class LeagueMigrationContext(BaseModel):
    league: LeagueSnapshot
    divisions: list[DivisionSnapshot]
    memberships: list[MembershipSnapshot]


class LeagueMigrationResult(BaseModel):
    policy_membership_events: list[PolicyMembershipEventChange] = Field(default_factory=list)


class DivisionLeaderboardContext(BaseModel):
    league: LeagueSnapshot
    division: DivisionSnapshot
    completed_rounds: list[RoundSnapshot]
    recent_rounds: list[RoundSnapshot]
    round_results: list[LeaderboardRoundResultSnapshot]


class SubmissionPlacementContext(BaseModel):
    league: LeagueSnapshot
    submission_public_id: SubmissionId
    policy_version_id: UUID
    player_id: PlayerId
    num_agents: int


class OnRoundCompletedContext(BaseModel):
    league: LeagueSnapshot
    division: DivisionSnapshot
    all_divisions: list[DivisionSnapshot]
    round_config: V2RoundConfig
    round_results: list[RoundResultSnapshot]
    transition_observations: dict[UUID, PolicyTransitionObservation] | None = None
    division_memberships: list[MembershipSnapshot]
    recent_results: list[RoundResultSnapshot]
    commissioner_config: dict[str, Any] | None


class OnRoundCompletedResult(BaseModel):
    policy_membership_events: list[PolicyMembershipEventChange] = Field(default_factory=list)
    membership_changes: list[MembershipChange] = Field(default_factory=list)
    follow_up_rounds: list[RoundSpec] = Field(default_factory=list)


class DivisionDescriptionContext(BaseModel):
    league: LeagueSnapshot
    division: DivisionSnapshot
    active_memberships: list[MembershipSnapshot]
    recent_rounds: list[RoundSnapshot]
