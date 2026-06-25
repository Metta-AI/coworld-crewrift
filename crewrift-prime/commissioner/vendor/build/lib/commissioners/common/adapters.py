from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from commissioners.common.models import (
    DivisionDescriptionContext,
    DivisionLeaderboardContext,
    DivisionSnapshot,
    EpisodeResult,
    LeaderboardRoundResultSnapshot,
    LeagueMigrationConfigContext,
    LeagueMigrationContext,
    LeagueMigrationResult,
    LeagueSnapshot,
    MembershipSnapshot,
    OnRoundCompletedContext,
    PolicyPool,
    PolicyPoolEntry,
    Round,
    RoundPolicyScore,
    RoundResultSnapshot,
    RoundSnapshot,
    RoundSpec,
    ScheduleContext,
    V2RoundConfig,
    V2StageConfig,
)
from commissioners.common.protocol import (
    DescribeDivisionRequest,
    DescribeDivisionResponse,
    DivisionDescription as CommissionerDivisionDescription,
    DivisionConfig as CommissionerDivisionConfig,
    DivisionLeaderboardEntry as CommissionerDivisionLeaderboardEntry,
)
from commissioners.common.protocol import (
    MembershipChange as CommissionerMembershipChange,
)
from commissioners.common.protocol import (
    PolicyMembershipEventChange as CommissionerPolicyMembershipEventChange,
)
from commissioners.common.protocol import (
    LeagueMigrationConfigRequest,
    LeagueMigrationConfigResponse,
    LeagueMigrationRequest,
    LeagueMigrationResponse,
)
from commissioners.common.protocol import (
    RankDivisionRequest,
    RankDivisionResponse,
)
from commissioners.common.protocol import (
    RoundCompletedRequest,
    RoundCompletedResponse,
)
from commissioners.common.protocol import (
    RoundInfo,
)
from commissioners.common.protocol import (
    RoundSpec as CommissionerRoundSpec,
)
from commissioners.common.protocol import (
    RoundStart as CommissionerRoundStart,
)
from commissioners.common.protocol import (
    ScheduleRoundsRequest,
    ScheduleRoundsResponse,
)
from commissioners.common.protocol import (
    ScheduleEpisodes as CommissionerScheduleEpisodes,
)
from commissioners.common.protocol import (
    EpisodeFailed as CommissionerProtocolEpisodeFailed,
    EpisodeRequest as CommissionerProtocolEpisodeRequest,
    EpisodeResult as CommissionerProtocolEpisodeResult,
)
from commissioners.common.protocol import (
    RoundComplete as CommissionerRoundComplete,
)
from commissioners.common.utils import division_entrants, select_qualifier_division

if TYPE_CHECKING:
    from commissioners.common.commissioners import Commissioner


# ---------------------------------------------------------------------------
# Container protocol adapter
# ---------------------------------------------------------------------------


def _round_start_config(round_start: CommissionerRoundStart) -> dict[str, Any]:
    config = round_start.league.commissioner_config or {}
    state = round_start.state if isinstance(round_start.state, dict) else {}
    round_config = state.get("round_config") if isinstance(state.get("round_config"), dict) else {}
    return {**config, **round_config}


def _current_division(round_start: CommissionerRoundStart) -> DivisionSnapshot:
    config = _round_start_config(round_start)
    configured_division_id = config.get("division_id") or config.get("current_division_id")
    if configured_division_id is not None:
        configured = UUID(str(configured_division_id))
        match = next((division for division in round_start.divisions if division.id == configured), None)
        if match is not None:
            return DivisionSnapshot(
                id=match.id,
                name=match.name,
                level=match.level,
                league_id=round_start.league.id,
                type=match.type,
            )

    membership_division_ids = {membership.division_id for membership in round_start.memberships}
    if len(membership_division_ids) == 1:
        division_id = next(iter(membership_division_ids))
        match = next((division for division in round_start.divisions if division.id == division_id), None)
        if match is not None:
            return DivisionSnapshot(
                id=match.id,
                name=match.name,
                level=match.level,
                league_id=round_start.league.id,
                type=match.type,
            )

    if not round_start.divisions:
        raise ValueError("round_start must include at least one division")
    division = min(round_start.divisions, key=lambda candidate: (candidate.level, candidate.name, str(candidate.id)))
    return DivisionSnapshot(
        id=division.id,
        name=division.name,
        level=division.level,
        league_id=round_start.league.id,
        type=division.type,
    )


def _round_start_stage_config(
    round_start: CommissionerRoundStart,
    commissioner: Commissioner | None = None,
) -> dict[str, Any]:
    config = _round_start_config(round_start)
    stages = config.get("stages")
    if isinstance(stages, list) and stages:
        stage = V2StageConfig.model_validate(stages[0])
        stage_config = stage.model_dump(mode="json")
    else:
        stage_config = {
            "num_episodes": config.get("num_episodes", 1),
            "min_episodes_per_entrant": config.get("min_episodes_per_entrant"),
            "mock_scores": config.get("mock_scores"),
            "self_play": config.get("self_play", False),
        }
    scheduling_config_fn = getattr(commissioner, "_scheduling_config", None)
    if scheduling_config_fn is not None:
        qualifier_division = select_qualifier_division(
            round_start.league.commissioner_config,
            _round_start_divisions(round_start),
        )
        if qualifier_division is not None and _current_division(round_start).id == qualifier_division.id:
            scheduling_config = scheduling_config_fn(round_start.league.commissioner_config)
            qualifier_stages = scheduling_config.qualifier_stages
            if qualifier_stages:
                stage_config["self_play"] = qualifier_stages[0].self_play
    return stage_config


def _round_start_pool(
    round_start: CommissionerRoundStart,
    commissioner: Commissioner | None = None,
) -> PolicyPool:
    stage_config = _round_start_stage_config(round_start, commissioner)
    return PolicyPool(
        id=round_start.round_id,
        label=str(stage_config.get("label") or "Round"),
        pool_type="round",
        config=stage_config,
    )


def _round_start_entries(round_start: CommissionerRoundStart) -> list[PolicyPoolEntry]:
    division = _current_division(round_start)
    qualifier_division = select_qualifier_division(
        round_start.league.commissioner_config,
        _round_start_divisions(round_start),
    )
    is_qualifier = qualifier_division is not None and division.id == qualifier_division.id
    entrants = division_entrants(
        _round_start_memberships(round_start),
        division,
        is_qualifier=is_qualifier,
    )
    config = _round_start_config(round_start)
    if isinstance(config.get("entrant_policy_version_ids"), list):
        entrant_order = {
            UUID(str(policy_version_id)): index
            for index, policy_version_id in enumerate(config["entrant_policy_version_ids"])
        }
        entrants = [entrant for entrant in entrants if entrant.policy_version_id in entrant_order]
        entrants = sorted(entrants, key=lambda entrant: entrant_order[entrant.policy_version_id])
    entries: list[PolicyPoolEntry] = []
    seen: set[UUID] = set()
    for membership in entrants:
        if membership.policy_version_id in seen:
            continue
        seen.add(membership.policy_version_id)
        entries.append(
            PolicyPoolEntry(
                pool_id=round_start.round_id,
                policy_version_id=membership.policy_version_id,
                player_id=membership.player_id,
                seed_order=len(entries),
            )
        )
    return entries


def _round_start_variant(round_start: CommissionerRoundStart) -> tuple[str, int]:
    config = _round_start_config(round_start)
    variant_id = str(config.get("variant_id") or (round_start.variants[0].id if round_start.variants else "default"))
    variant = next((candidate for candidate in round_start.variants if candidate.id == variant_id), None)
    if variant is None and round_start.variants:
        variant = round_start.variants[0]
        variant_id = variant.id
    if variant is None:
        return variant_id, len(_round_start_entries(round_start))
    num_agents = variant.game_config.get("num_agents")
    if not isinstance(num_agents, int):
        return variant_id, len(_round_start_entries(round_start))
    return variant_id, num_agents


def _round_start_round(round_start: CommissionerRoundStart) -> Round:
    division = _current_division(round_start)
    return Round(
        id=round_start.round_id,
        public_id=str(round_start.round_id),
        division_id=division.id,
        round_number=round_start.round_number,
        commissioner_key=round_start.league.commissioner_key or "container",
        round_config=_round_start_config(round_start),
    )


def _round_start_memberships(round_start: CommissionerRoundStart) -> list[MembershipSnapshot]:
    return [
        MembershipSnapshot(
            id=membership.id,
            league_id=round_start.league.id,
            division_id=membership.division_id,
            policy_version_id=membership.policy_version_id,
            player_id=membership.player_id,
            status=membership.status,
            substatus=membership.substatus,
            is_champion=membership.is_champion,
        )
        for membership in round_start.memberships
    ]


def _round_start_divisions(round_start: CommissionerRoundStart) -> list[DivisionSnapshot]:
    return [
        DivisionSnapshot(
            id=division.id,
            name=division.name,
            level=division.level,
            league_id=round_start.league.id,
            type=division.type,
        )
        for division in round_start.divisions
    ]


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _round_snapshot(info: RoundInfo) -> RoundSnapshot:
    return RoundSnapshot(
        id=info.id,
        public_id=info.public_id or str(info.id),
        division_id=info.division_id,
        round_number=info.round_number,
        status=info.status,
        round_config=info.round_config,
        created_at=_parse_datetime(info.created_at) or datetime.now(UTC),
        started_at=_parse_datetime(info.started_at),
        completed_at=_parse_datetime(info.completed_at),
    )


def schedule_episodes_for_round_start(
    commissioner: Commissioner,
    round_start: CommissionerRoundStart,
) -> CommissionerScheduleEpisodes:
    custom_scheduler = getattr(commissioner, "schedule_episodes_for_round_start", None)
    if callable(custom_scheduler):
        return custom_scheduler(round_start)
    variant_id, num_agents = _round_start_variant(round_start)
    return commissioner.schedule_episodes(
        pool=_round_start_pool(round_start, commissioner),
        entries=_round_start_entries(round_start),
        num_agents=num_agents,
        variant_id=variant_id,
    )


def _protocol_round_spec(spec: RoundSpec) -> CommissionerRoundSpec:
    return CommissionerRoundSpec.model_validate(spec.model_dump(mode="json"))


def _protocol_leaderboard_entry(entry: CommissionerDivisionLeaderboardEntry) -> CommissionerDivisionLeaderboardEntry:
    return CommissionerDivisionLeaderboardEntry.model_validate(entry.model_dump(mode="json"))


def _protocol_division_description(
    description: CommissionerDivisionDescription,
) -> CommissionerDivisionDescription:
    return CommissionerDivisionDescription.model_validate(description.model_dump(mode="json"))


def _protocol_division_config(config: Any) -> CommissionerDivisionConfig:
    return CommissionerDivisionConfig.model_validate(config.model_dump(mode="json"))


def _protocol_membership_change(change: Any) -> CommissionerMembershipChange:
    return CommissionerMembershipChange.model_validate(change.model_dump(mode="json"))


def _protocol_policy_membership_event(change: Any) -> CommissionerPolicyMembershipEventChange:
    return CommissionerPolicyMembershipEventChange.model_validate(change.model_dump(mode="json"))


def complete_round_for_round_start(
    commissioner: Commissioner,
    round_start: CommissionerRoundStart,
    episode_results: list[CommissionerProtocolEpisodeResult],
    scheduled_episodes: list[CommissionerProtocolEpisodeRequest] | None = None,
    failed_episodes: list[CommissionerProtocolEpisodeFailed] | None = None,
) -> CommissionerRoundComplete:
    custom_completer = getattr(commissioner, "complete_round_for_round_start", None)
    if callable(custom_completer):
        return custom_completer(round_start, episode_results, scheduled_episodes, failed_episodes)
    local_episode_results = [
        EpisodeResult(
            episode_request_id=UUID(int=index + 1),
            scores=[
                RoundPolicyScore(
                    policy_version_id=score.policy_version_id,
                    player_id=score.player_id,
                    score=score.score,
                )
                for score in result.scores
            ],
            game_results=result.game_results,
        )
        for index, result in enumerate(episode_results)
    ]
    round_row = _round_start_round(round_start)
    pool = _round_start_pool(round_start, commissioner)
    entries = _round_start_entries(round_start)
    complete = commissioner.complete_round(
        round_row=round_row,
        pool=pool,
        entries=entries,
        episode_results=local_episode_results,
    )
    hook_result = commissioner.on_round_completed(
        OnRoundCompletedContext(
            league=LeagueSnapshot(
                id=round_start.league.id,
                commissioner_key=round_start.league.commissioner_key or "container",
                commissioner_config=_round_start_config(round_start),
            ),
            division=_current_division(round_start),
            all_divisions=_round_start_divisions(round_start),
            round_config=V2RoundConfig.model_validate(_round_start_config(round_start)),
            round_results=[
                RoundResultSnapshot(
                    round_id=round_start.round_id,
                    policy_version_id=ranking.policy_version_id,
                    rank=ranking.rank,
                    score=ranking.score,
                    result_metadata=ranking.result_metadata,
                )
                for division_ranking in complete.results
                for ranking in division_ranking.rankings
            ],
            division_memberships=_round_start_memberships(round_start),
            recent_results=[],
            commissioner_config=_round_start_config(round_start),
        )
    )
    complete.membership_changes = [_protocol_membership_change(change) for change in hook_result.membership_changes]
    complete.policy_membership_events = [
        _protocol_policy_membership_event(change) for change in hook_result.policy_membership_events
    ]
    return complete


def schedule_rounds_for_request(
    commissioner: Commissioner,
    request: ScheduleRoundsRequest,
) -> ScheduleRoundsResponse:
    specs = commissioner.schedule_rounds(
        ScheduleContext(
            league=LeagueSnapshot(
                id=request.league.id,
                commissioner_key=request.league.commissioner_key or "container",
                commissioner_config=request.league.commissioner_config,
            ),
            divisions=[
                DivisionSnapshot(
                    id=division.id,
                    name=division.name,
                    level=division.level,
                    league_id=request.league.id,
                    type=division.type,
                )
                for division in request.divisions
            ],
            active_memberships=[
                MembershipSnapshot(
                    id=membership.id,
                    league_id=request.league.id,
                    division_id=membership.division_id,
                    policy_version_id=membership.policy_version_id,
                    player_id=membership.player_id,
                    status=membership.status,
                    substatus=membership.substatus,
                    is_champion=membership.is_champion,
                )
                for membership in request.active_memberships
            ],
            recent_rounds=[_round_snapshot(round_info) for round_info in request.recent_rounds],
        )
    )
    return ScheduleRoundsResponse(rounds=[_protocol_round_spec(spec) for spec in specs])


def league_migration_config_for_request(
    commissioner: Commissioner,
    request: LeagueMigrationConfigRequest,
) -> LeagueMigrationConfigResponse:
    configs = commissioner.league_migration_config(
        LeagueMigrationConfigContext(
            league=LeagueSnapshot(
                id=request.league.id,
                commissioner_key=request.league.commissioner_key or "container",
                commissioner_config=request.league.commissioner_config,
            ),
            divisions=[
                DivisionSnapshot(
                    id=division.id,
                    name=division.name,
                    level=division.level,
                    league_id=request.league.id,
                    type=division.type,
                )
                for division in request.divisions
            ],
        )
    )
    return LeagueMigrationConfigResponse(divisions=[_protocol_division_config(config) for config in configs])


def migrate_league_for_request(
    commissioner: Commissioner,
    request: LeagueMigrationRequest,
) -> LeagueMigrationResponse:
    result = commissioner.migrate_league(
        LeagueMigrationContext(
            league=LeagueSnapshot(
                id=request.league.id,
                commissioner_key=request.league.commissioner_key or "container",
                commissioner_config=request.league.commissioner_config,
            ),
            divisions=[
                DivisionSnapshot(
                    id=division.id,
                    name=division.name,
                    level=division.level,
                    league_id=request.league.id,
                    type=division.type,
                )
                for division in request.divisions
            ],
            memberships=[
                MembershipSnapshot(
                    id=membership.id,
                    league_id=request.league.id,
                    division_id=membership.division_id,
                    policy_version_id=membership.policy_version_id,
                    player_id=membership.player_id,
                    status=membership.status,
                    substatus=membership.substatus,
                    is_champion=membership.is_champion,
                )
                for membership in request.memberships
            ],
        )
    )
    if not isinstance(result, LeagueMigrationResult):
        result = LeagueMigrationResult.model_validate(result)
    return LeagueMigrationResponse(
        policy_membership_events=[
            _protocol_policy_membership_event(change) for change in result.policy_membership_events
        ]
    )


def rank_division_for_request(
    commissioner: Commissioner,
    request: RankDivisionRequest,
) -> RankDivisionResponse:
    rankings = commissioner.rank_division(
        DivisionLeaderboardContext(
            league=LeagueSnapshot(
                id=request.league.id,
                commissioner_key=request.league.commissioner_key or "container",
                commissioner_config=request.league.commissioner_config,
            ),
            division=DivisionSnapshot(
                id=request.division.id,
                name=request.division.name,
                level=request.division.level,
                league_id=request.league.id,
                type=request.division.type,
            ),
            completed_rounds=[_round_snapshot(round_info) for round_info in request.completed_rounds],
            recent_rounds=[_round_snapshot(round_info) for round_info in request.recent_rounds],
            round_results=[
                LeaderboardRoundResultSnapshot(
                    round_id=result.round_id,
                    policy_version_id=result.policy_version_id,
                    rank=result.rank,
                    score=result.score,
                    result_metadata=result.result_metadata,
                    player_id=result.player_id,
                    player_name=result.player_name,
                )
                for result in request.round_results
            ],
        )
    )
    return RankDivisionResponse(rankings=[_protocol_leaderboard_entry(ranking) for ranking in rankings])


def describe_division_for_request(
    commissioner: Commissioner,
    request: DescribeDivisionRequest,
) -> DescribeDivisionResponse:
    description = commissioner.describe_division(
        DivisionDescriptionContext(
            league=LeagueSnapshot(
                id=request.league.id,
                commissioner_key=request.league.commissioner_key or "container",
                commissioner_config=request.league.commissioner_config,
            ),
            division=DivisionSnapshot(
                id=request.division.id,
                name=request.division.name,
                level=request.division.level,
                league_id=request.league.id,
                type=request.division.type,
            ),
            active_memberships=[
                MembershipSnapshot(
                    id=membership.id,
                    league_id=request.league.id,
                    division_id=membership.division_id,
                    policy_version_id=membership.policy_version_id,
                    player_id=membership.player_id,
                    status=membership.status,
                    substatus=membership.substatus,
                    is_champion=membership.is_champion,
                )
                for membership in request.active_memberships
            ],
            recent_rounds=[_round_snapshot(round_info) for round_info in request.recent_rounds],
        )
    )
    return DescribeDivisionResponse(description=_protocol_division_description(description))


def round_completed_for_request(
    commissioner: Commissioner,
    request: RoundCompletedRequest,
) -> RoundCompletedResponse:
    result = commissioner.on_round_completed(
        OnRoundCompletedContext(
            league=LeagueSnapshot(
                id=request.league.id,
                commissioner_key=request.league.commissioner_key or "container",
                commissioner_config=request.league.commissioner_config,
            ),
            division=DivisionSnapshot(
                id=request.division.id,
                name=request.division.name,
                level=request.division.level,
                league_id=request.league.id,
                type=request.division.type,
            ),
            all_divisions=[
                DivisionSnapshot(
                    id=division.id,
                    name=division.name,
                    level=division.level,
                    league_id=request.league.id,
                    type=division.type,
                )
                for division in request.all_divisions
            ],
            round_config=V2RoundConfig.model_validate(request.round_config.model_dump(mode="json")),
            round_results=[
                RoundResultSnapshot(
                    round_id=round_result.round_id,
                    policy_version_id=round_result.policy_version_id,
                    rank=round_result.rank,
                    score=round_result.score,
                    result_metadata=round_result.result_metadata,
                )
                for round_result in request.round_results
            ],
            division_memberships=[
                MembershipSnapshot(
                    id=membership.id,
                    league_id=request.league.id,
                    division_id=membership.division_id,
                    policy_version_id=membership.policy_version_id,
                    player_id=membership.player_id,
                    status=membership.status,
                    substatus=membership.substatus,
                    is_champion=membership.is_champion,
                )
                for membership in request.division_memberships
            ],
            recent_results=[
                RoundResultSnapshot(
                    round_id=round_result.round_id,
                    policy_version_id=round_result.policy_version_id,
                    rank=round_result.rank,
                    score=round_result.score,
                    result_metadata=round_result.result_metadata,
                )
                for round_result in request.recent_results
            ],
            commissioner_config=request.commissioner_config,
        )
    )
    return RoundCompletedResponse(
        policy_membership_events=[
            _protocol_policy_membership_event(change) for change in result.policy_membership_events
        ],
        membership_changes=[_protocol_membership_change(change) for change in result.membership_changes],
        follow_up_rounds=[_protocol_round_spec(round_spec) for round_spec in result.follow_up_rounds],
    )
