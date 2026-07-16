"""Typed client for the league-scoped platform commissioner API.

The Crewrift Prime commissioner still uses the container callback protocol for
game-specific round planning and scoring. This client is the pull/write boundary
for the parts the platform commissioner API can already own without changing
Prime's rules: league context, division topology, durable settings and state,
membership mutations, and the generic round lifecycle.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from xp_request_client import XpRequestClient, XpRequestInfraError, _prefixed_league_id


class PlatformApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LeagueSummary(PlatformApiModel):
    id: str
    name: str
    commissioner_key: str


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
    status: str
    division: DivisionRef


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


class RoundCreateResponse(RoundSummary):
    created_at: datetime


class RoundEpisodePlan(PlatformApiModel):
    strategy: str
    coworld_id: str
    variant_id: str
    seat_count: int
    entrant_policy_version_ids: list[UUID]
    episodes: list[dict[str, Any]]


class DispatchRoundResult(PlatformApiModel):
    episode_request_ids: list[UUID]
    dispatched: int
    replayed: bool = False


class ScoreRoundResult(PlatformApiModel):
    trace: dict[str, Any]
    replayed: bool = False


class CompleteRoundResult(PlatformApiModel):
    status: str
    replayed: bool = False
    membership_events: MembershipEventBatchResult | None = None


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
    ) -> RoundCreateResponse:
        payload = self._post(
            "/v2/rounds",
            {
                "division_id": division_id,
                "idempotency_key": idempotency_key,
                "round_config": {
                    "entrant_policy_version_ids": entrant_policy_version_ids
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

    def dispatch_round(self, round_id: str) -> DispatchRoundResult:
        payload = self._post(f"/v2/rounds/{urllib.parse.quote(round_id)}/episodes", {})
        return DispatchRoundResult.model_validate(payload)

    def score_round(self, round_id: str, *, rule: str) -> ScoreRoundResult:
        payload = self._post(
            f"/v2/rounds/{urllib.parse.quote(round_id)}/score", {"rule": rule}
        )
        return ScoreRoundResult.model_validate(payload)

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


__all__ = [
    "CommissionerStateResponse",
    "DivisionDeclaration",
    "DivisionTopologyResponse",
    "LeagueSettings",
    "MembershipAdmission",
    "MembershipEventChange",
    "PlatformCommissionerClient",
    "XpRequestInfraError",
]
