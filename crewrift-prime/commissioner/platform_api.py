"""Typed client for the league-scoped platform commissioner API."""

from __future__ import annotations

import urllib.parse
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from commissioners.common.models import DivisionCommissionerDescriptionPublic
from commissioners.common.protocol import (
    CommissionerRoundReport,
    DivisionLeaderboard,
)

from xp_request_client import XpRequestClient, XpRequestInfraError, _prefixed_league_id


class PlatformApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LeagueSummary(PlatformApiModel):
    id: str
    name: str
    commissioner_key: str
    rounds_paused_at: datetime | None


class DivisionRef(PlatformApiModel):
    id: str
    name: str
    level: int
    type: str
    hidden: bool = False


class PolicyVersionRef(PlatformApiModel):
    id: UUID


class PlayerRef(PlatformApiModel):
    id: str
    name: str


class MembershipSummary(PlatformApiModel):
    id: str
    status: str
    substatus: str | None = None
    is_champion: bool
    division: DivisionRef
    policy_version: PolicyVersionRef
    player: PlayerRef | None = None


class RoundSummary(PlatformApiModel):
    id: str
    round_number: int
    commissioner_key: str
    status: str
    division: DivisionRef
    round_config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RoundResultSummary(PlatformApiModel):
    rank: int
    score: float
    result_metadata: dict[str, Any] | None = None
    policy_version: PolicyVersionRef
    player: PlayerRef | None = None


class RoundDetail(RoundSummary):
    results: list[RoundResultSummary]


class RoundList(PlatformApiModel):
    entries: list[RoundSummary]
    total_count: int
    limit: int
    offset: int


class DivisionDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    level: int
    type: str
    hidden: bool = False


class DivisionTopologyMove(PlatformApiModel):
    id: str
    name: str
    action: Literal["created", "archived", "restored", "updated"]
    from_level: int | None = None
    to_level: int | None = None


class DivisionTopologyResponse(PlatformApiModel):
    divisions: list[DivisionRef]
    moves: list[DivisionTopologyMove]


class LeaderboardColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value_type: Literal["integer", "number", "percent", "string", "boolean"] = "number"
    sort: Literal["asc", "desc"] | None = None


class LeaderboardView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    title: str
    description: str | None = None
    score_key: str = "score"
    score_axis_label: str | None = None
    default: bool = False
    axis_values: dict[str, str] = Field(default_factory=dict)
    columns: list[LeaderboardColumn] = Field(default_factory=list)
    half_life_hours: float | None = None
    window_hours: float | None = None


class LeaderboardSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    half_life_hours: float = 6
    views: list[LeaderboardView] = Field(default_factory=list)


class LeagueSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episodes_per_round: int | None = None
    round_interval_minutes: int | None = None
    episode_player_pod_llm_spend_limit_usd: float | None = None
    leaderboard: LeaderboardSettings | None = None


class LeagueSettingsDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episodes_per_round: int | None
    round_interval_minutes: int


class LeagueSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: LeagueSettings
    defaults: LeagueSettingsDefaults


class CommissionerState(RootModel[JsonValue]):
    pass


class CommissionerStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    state: CommissionerState


class MembershipEventEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MembershipEventChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    league_policy_membership_id: str
    from_division_id: str | None = None
    to_division_id: str | None = None
    status: str
    substatus: str | None = None
    reason: str
    notes: str | None = None
    evidence: list[MembershipEventEvidence] = Field(default_factory=list)


class MembershipEventItemResult(PlatformApiModel):
    league_policy_membership_id: str
    accepted: bool
    error: str | None = None


class MembershipEventBatchResult(PlatformApiModel):
    applied: bool
    replayed: bool = False
    results: list[MembershipEventItemResult]


class MembershipAdmission(PlatformApiModel):
    league_policy_membership_id: str
    policy_version_id: UUID
    status: str
    substatus: str | None
    is_champion: bool


RoundCreateResponse = RoundSummary


class RoundStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = "Round"
    num_episodes: int = Field(gt=0)
    min_episodes_per_entrant: int | None = Field(default=None, gt=0)
    self_play: bool = False


class RoundEpisodePlan(PlatformApiModel):
    strategy: str
    coworld_id: str
    variant_id: str | None
    seat_count: int | None
    entrant_policy_version_ids: list[UUID]
    episodes: list["PlannedRoundEpisode"]


class ExplicitRoundEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str
    seed: int = 0
    policy_version_ids: list[UUID]
    filler_seats: list[int] = Field(default_factory=list)
    game_config_overrides: dict[str, Any] = Field(default_factory=dict)


class PlannedRoundEpisode(PlatformApiModel):
    job_index: int
    variant_id: str
    seed: int
    policy_version_ids: list[UUID]
    filler_seats: list[int]
    game_config: dict[str, Any]


class RoundEpisodeRuntime(PlatformApiModel):
    status: str
    policy_version_ids: list[UUID]
    job_id: UUID | None = None
    scores: list["RoundEpisodeScore"] = Field(default_factory=list)


class RoundEpisodeScore(PlatformApiModel):
    policy_version_id: UUID
    score: float


class RoundEpisodeResult(PlatformApiModel):
    id: str
    job_index: int
    variant_id: str
    seed: int
    game_config: dict[str, Any]
    filler_seats: list[int]
    runtime: RoundEpisodeRuntime
    game_results: dict[str, Any] | None = None


class DispatchRoundResult(PlatformApiModel):
    episode_request_ids: list[UUID]
    dispatched: int
    replayed: bool = False


class RoundScoreEntry(PlatformApiModel):
    policy_version_id: UUID
    rank: int
    score: float
    episodes_scored: int
    episodes_excluded: int
    result_metadata: dict[str, Any]


class ScoreTrace(PlatformApiModel):
    rule: str
    scored_at: str
    entries: list[RoundScoreEntry]
    infrastructure_failures: dict[str, str] = Field(default_factory=dict)
    excluded_filler_seats: int = 0
    notes: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ScoreRoundResult(PlatformApiModel):
    trace: ScoreTrace
    replayed: bool = False


class AuthoredRoundScoreEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version_id: UUID
    rank: int = Field(ge=1)
    score: float
    episodes_scored: int = Field(ge=0)
    episodes_excluded: int = Field(default=0, ge=0)
    result_metadata: dict[str, Any] = Field(default_factory=dict)


class CompleteRoundResult(PlatformApiModel):
    status: str
    replayed: bool = False
    membership_events: MembershipEventBatchResult | None = None


class AbortRoundResult(PlatformApiModel):
    status: str
    reason: str
    replayed: bool = False
    jobs_failed: int = 0
    episode_requests_deleted: int = 0


class PlatformCommissionerClient(XpRequestClient):
    """Synchronous, bearer-authenticated client for one commissioner league."""

    def get_league(self, league_id: str) -> LeagueSummary:
        league_id = _prefixed_league_id(league_id)
        return LeagueSummary.model_validate(
            self._get(f"/v2/leagues/{urllib.parse.quote(league_id)}")
        )

    def list_divisions(self, league_id: str) -> list[DivisionRef]:
        league_id = _prefixed_league_id(league_id)
        payload = self._get(f"/v2/divisions?league_id={urllib.parse.quote(league_id)}")
        return [DivisionRef.model_validate(row) for row in payload]

    def declare_divisions(
        self, league_id: str, divisions: list[DivisionDeclaration]
    ) -> DivisionTopologyResponse:
        league_id = _prefixed_league_id(league_id)
        payload = self._request(
            "PUT",
            f"/v2/leagues/{urllib.parse.quote(league_id)}/divisions",
            body={
                "divisions": [
                    division.model_dump(mode="json") for division in divisions
                ]
            },
        )
        return DivisionTopologyResponse.model_validate(payload)

    def list_memberships(self, league_id: str) -> list[MembershipSummary]:
        league_id = _prefixed_league_id(league_id)
        payload = self._get(
            f"/v2/league-policy-memberships?league_id={urllib.parse.quote(league_id)}"
        )
        return [MembershipSummary.model_validate(row) for row in payload]

    def list_rounds(self, league_id: str, *, limit: int = 200) -> RoundList:
        league_id = _prefixed_league_id(league_id)
        payload = self._get(
            f"/v2/rounds?league_id={urllib.parse.quote(league_id)}&limit={limit}"
        )
        return RoundList.model_validate(payload)

    def get_round(self, round_id: str) -> RoundDetail:
        return RoundDetail.model_validate(
            self._get(f"/v2/rounds/{urllib.parse.quote(round_id)}")
        )

    def get_commissioner_state(self, league_id: str) -> CommissionerStateResponse:
        league_id = _prefixed_league_id(league_id)
        payload = self._get(
            f"/v2/leagues/{urllib.parse.quote(league_id)}/commissioner-state"
        )
        return CommissionerStateResponse.model_validate(payload)

    def update_commissioner_state(
        self, league_id: str, *, version: int, state: JsonValue
    ) -> CommissionerStateResponse:
        league_id = _prefixed_league_id(league_id)
        payload = self._request(
            "PUT",
            f"/v2/leagues/{urllib.parse.quote(league_id)}/commissioner-state",
            body={"version": version, "state": state},
        )
        return CommissionerStateResponse.model_validate(payload)

    def get_typed_league_settings(self, league_id: str) -> LeagueSettingsResponse:
        league_id = _prefixed_league_id(league_id)
        payload = self._get(f"/v2/leagues/{urllib.parse.quote(league_id)}/settings")
        return LeagueSettingsResponse.model_validate(payload)

    def replace_league_settings(
        self, league_id: str, settings: LeagueSettings
    ) -> LeagueSettingsResponse:
        league_id = _prefixed_league_id(league_id)
        payload = self._post(
            f"/v2/leagues/{urllib.parse.quote(league_id)}/settings",
            settings.model_dump(mode="json", exclude_none=True),
        )
        return LeagueSettingsResponse.model_validate(payload)

    def apply_membership_events(
        self,
        league_id: str,
        changes: list[MembershipEventChange],
        *,
        idempotency_key: str,
    ) -> MembershipEventBatchResult:
        league_id = _prefixed_league_id(league_id)
        payload = self._post(
            "/v2/policy-membership-events",
            {
                "league_id": league_id,
                "changes": [change.model_dump(mode="json") for change in changes],
                "idempotency_key": idempotency_key,
            },
        )
        return MembershipEventBatchResult.model_validate(payload)

    def admit_membership(
        self,
        league_id: str,
        *,
        policy_version_id: UUID,
        division_id: str,
        idempotency_key: str,
        make_champion: bool = True,
        notes: str | None = None,
    ) -> MembershipAdmission:
        league_id = _prefixed_league_id(league_id)
        payload = self._post(
            f"/v2/leagues/{urllib.parse.quote(league_id)}/memberships",
            {
                "policy_version_id": str(policy_version_id),
                "division_id": division_id,
                "make_champion": make_champion,
                "notes": notes,
                "idempotency_key": idempotency_key,
            },
        )
        return MembershipAdmission.model_validate(payload)

    def create_round(
        self,
        *,
        division_id: str,
        idempotency_key: str,
        entrant_policy_version_ids: list[UUID] | None = None,
        stages: list[RoundStage] | None = None,
    ) -> RoundCreateResponse:
        payload = self._post(
            "/v2/rounds",
            {
                "division_id": division_id,
                "idempotency_key": idempotency_key,
                "round_config": {
                    "entrant_policy_version_ids": entrant_policy_version_ids,
                    "stages": None
                    if stages is None
                    else [stage.model_dump(mode="json") for stage in stages],
                },
            },
        )
        return RoundCreateResponse.model_validate(payload)

    def plan_round(
        self,
        round_id: str,
        *,
        strategy: Literal[
            "round_robin", "swiss_neighbor", "random_fill"
        ] = "round_robin",
        params: dict[str, Any] | None = None,
    ) -> RoundEpisodePlan:
        payload = self._post(
            f"/v2/rounds/{urllib.parse.quote(round_id)}/episodes:plan",
            {"strategy": strategy, "params": params or {}},
        )
        return RoundEpisodePlan.model_validate(payload)

    def plan_explicit_round(
        self,
        round_id: str,
        episodes: list[ExplicitRoundEpisode],
    ) -> RoundEpisodePlan:
        payload = self._post(
            f"/v2/rounds/{urllib.parse.quote(round_id)}/episodes:plan",
            {
                "strategy": "explicit",
                "episodes": [episode.model_dump(mode="json") for episode in episodes],
            },
        )
        return RoundEpisodePlan.model_validate(payload)

    def dispatch_round(self, round_id: str) -> DispatchRoundResult:
        payload = self._post(f"/v2/rounds/{urllib.parse.quote(round_id)}/episodes", {})
        return DispatchRoundResult.model_validate(payload)

    def get_round_episodes(self, round_id: str) -> list[RoundEpisodeResult]:
        payload = self._get(f"/v2/rounds/{urllib.parse.quote(round_id)}/episodes")
        return [RoundEpisodeResult.model_validate(row) for row in payload]

    def score_round(self, round_id: str, *, rule: str) -> ScoreRoundResult:
        payload = self._post(
            f"/v2/rounds/{urllib.parse.quote(round_id)}/score",
            {"mode": "generated", "rule": rule},
        )
        return ScoreRoundResult.model_validate(payload)

    def score_authored_round(
        self,
        round_id: str,
        *,
        rule_id: str,
        entries: list[AuthoredRoundScoreEntry],
        infrastructure_failures: dict[str, str] | None = None,
        excluded_filler_seats: int = 0,
        notes: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        round_display: dict[str, Any] | None = None,
        commissioner_report: CommissionerRoundReport | None = None,
    ) -> ScoreRoundResult:
        payload = self._post(
            f"/v2/rounds/{urllib.parse.quote(round_id)}/score",
            {
                "mode": "authored",
                "rule_id": rule_id,
                "entries": [entry.model_dump(mode="json") for entry in entries],
                "infrastructure_failures": infrastructure_failures or {},
                "excluded_filler_seats": excluded_filler_seats,
                "notes": notes or [],
                "extra": extra or {},
                "round_display": round_display,
                "commissioner_report": None
                if commissioner_report is None
                else commissioner_report.model_dump(mode="json"),
            },
        )
        return ScoreRoundResult.model_validate(payload)

    def publish_division_leaderboard(
        self,
        division_id: str,
        leaderboard: DivisionLeaderboard,
    ) -> DivisionLeaderboard:
        if division_id != f"div_{leaderboard.division_id}":
            raise ValueError("leaderboard division_id must match the target division")
        body = leaderboard.model_dump(mode="json")
        del body["division_id"]
        payload = self._request(
            "PUT",
            f"/v2/divisions/{urllib.parse.quote(division_id)}/leaderboards",
            body={"leaderboards": body},
        )
        return DivisionLeaderboard(division_id=leaderboard.division_id, **payload)

    def publish_division_description(
        self,
        division_id: str,
        description: DivisionCommissionerDescriptionPublic,
    ) -> DivisionCommissionerDescriptionPublic:
        payload = self._request(
            "PUT",
            f"/v2/divisions/{urllib.parse.quote(division_id)}/commissioner-description",
            body=description.model_dump(mode="json"),
        )
        return DivisionCommissionerDescriptionPublic.model_validate(payload)

    def complete_round(
        self,
        round_id: str,
        *,
        membership_events: list[MembershipEventChange] | None = None,
        membership_idempotency_key: str | None = None,
    ) -> CompleteRoundResult:
        payload = self._post(
            f"/v2/rounds/{urllib.parse.quote(round_id)}/complete",
            {
                "membership_events": [
                    event.model_dump(mode="json") for event in membership_events or []
                ],
                "membership_idempotency_key": membership_idempotency_key,
            },
        )
        return CompleteRoundResult.model_validate(payload)

    def abort_round(self, round_id: str, *, reason: str) -> AbortRoundResult:
        payload = self._post(
            f"/v2/rounds/{urllib.parse.quote(round_id)}/abort",
            {"reason": reason},
        )
        return AbortRoundResult.model_validate(payload)


__all__ = [
    "AbortRoundResult",
    "CommissionerStateResponse",
    "DivisionDeclaration",
    "DivisionTopologyResponse",
    "ExplicitRoundEpisode",
    "LeagueSettings",
    "MembershipAdmission",
    "MembershipEventChange",
    "PlatformCommissionerClient",
    "RoundStage",
    "XpRequestInfraError",
]
