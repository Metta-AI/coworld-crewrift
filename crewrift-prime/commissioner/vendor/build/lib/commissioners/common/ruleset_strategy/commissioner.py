from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from commissioners.common.commissioners import BaselineCommissioner
from commissioners.common.models import (
    POLICY_MEMBERSHIP_SUBSTATUS_INACTIVE,
    DivisionCommissionerDescriptionPublic,
    DivisionConfig,
    DivisionDescriptionContext,
    DivisionLeaderboardContext,
    DivisionLeaderboardSnapshot,
    EpisodeResult,
    LeagueMigrationConfigContext,
    LeagueMigrationContext,
    LeagueMigrationResult,
    OnRoundCompletedContext,
    OnRoundCompletedResult,
    PolicyMembershipEventChange,
    PolicyMembershipEventEvidence,
    PolicyPool,
    PolicyPoolEntry,
    Round,
    RoundSpec,
    ScheduleContext,
    V2RoundConfig,
)
from commissioners.common.protocol import (
    EpisodeFailed as CommissionerProtocolEpisodeFailed,
    EpisodeRequest as CommissionerProtocolEpisodeRequest,
    EpisodeResult as CommissionerProtocolEpisodeResult,
    RoundComplete as CommissionerRoundComplete,
    RoundStart as CommissionerRoundStart,
    ScheduleEpisodes as CommissionerScheduleEpisodes,
)
from commissioners.common.utils import (
    _count_text,
    _current_schedule_slot,
    _duration_text,
    _leaderboard_rules_description,
    _plural_word,
    _rank_points_lists_by_policy,
    _round_structure_description,
    _schedule_slot_description,
)
from commissioners.common.ruleset_strategy.config import RulesetStrategyCommissionerConfig, load_image_ruleset_strategy_config
from commissioners.common.ruleset_strategy.entrants import division_entries, select_rule
from commissioners.common.ruleset_strategy.membership_events import (
    build_default_competing_substatus_events,
    build_membership_events,
    protocol_policy_membership_event,
)
from commissioners.common.ruleset_strategy.round_start import RoundStartView
from commissioners.common.ruleset_strategy.scheduling import schedule_entries


class RulesetStrategyCommissioner(BaselineCommissioner):
    """Commissioner whose scheduling, seating, ranking metadata, and membership changes come from config."""

    def __init__(self, config: RulesetStrategyCommissionerConfig | dict[str, Any] | None = None) -> None:
        if config is None:
            self._ruleset_config = load_image_ruleset_strategy_config()
        elif isinstance(config, RulesetStrategyCommissionerConfig):
            self._ruleset_config = config
        else:
            self._ruleset_config = RulesetStrategyCommissionerConfig.from_mapping(config)

    def _config(self) -> RulesetStrategyCommissionerConfig:
        return self._ruleset_config

    def dispatch_throttle_config(self) -> Any:
        return self._config().dispatch_throttle

    def league_migration_config(self, ctx: LeagueMigrationConfigContext) -> list[DivisionConfig]:
        return self._config().migration_divisions

    def migrate_league(self, ctx: LeagueMigrationContext) -> LeagueMigrationResult:
        configured_names = {division.name for division in self._config().migration_divisions}
        competition = next((division for division in ctx.divisions if division.name == "Competition"), None)
        divisions_by_id = {division.id: division for division in ctx.divisions}
        events: list[PolicyMembershipEventChange] = []

        for membership in ctx.memberships:
            division = divisions_by_id.get(membership.division_id)
            if division is None or division.name in configured_names:
                continue
            if division.name == "Dirt" and membership.status != "disqualified":
                events.append(
                    PolicyMembershipEventChange(
                        league_policy_membership_id=membership.id,
                        from_division_id=membership.division_id,
                        to_division_id=None,
                        status="disqualified",
                        substatus=POLICY_MEMBERSHIP_SUBSTATUS_INACTIVE,
                        reason="Tournament restructure Dirt->Disqualified",
                        end_time=datetime.now(UTC),
                        evidence=[_legacy_division_migration_evidence(division.name, "Disqualified")],
                    )
                )
            elif division.name == "Wood" and competition is not None:
                events.append(
                    PolicyMembershipEventChange(
                        league_policy_membership_id=membership.id,
                        from_division_id=membership.division_id,
                        to_division_id=competition.id,
                        status=_membership_status(membership.status),
                        substatus=membership.substatus,
                        reason=f"Tournament restructure Wood->{competition.name}",
                        evidence=[_legacy_division_migration_evidence(division.name, competition.name)],
                    )
                )
        return LeagueMigrationResult(policy_membership_events=events)

    def rank_division(self, ctx: DivisionLeaderboardContext) -> list[DivisionLeaderboardSnapshot]:
        config = self._config()
        if config.ranking.filter_metadata:
            filtered = [
                result
                for result in ctx.round_results
                if all(result.result_metadata.get(key) == value for key, value in config.ranking.filter_metadata.items())
            ]
            ctx = ctx.model_copy(update={"round_results": filtered})
        return super().rank_division(ctx)

    def _leaderboard_ewma_halflife(self, ctx: DivisionLeaderboardContext) -> timedelta:
        config = self._config()
        return timedelta(hours=config.ranking.ewma_halflife_hours)

    def describe_division(self, ctx: DivisionDescriptionContext) -> DivisionCommissionerDescriptionPublic:
        config = self._config()
        memberships = list(ctx.active_memberships)
        rule = select_rule(config, ctx.division, memberships)
        stages = rule.stages if rule and rule.stages is not None else config.stages
        minimum_entrants = rule.minimum_entrants if rule is not None else 1
        entrants = division_entries(ctx.division, memberships, rule)
        active_round = next((r for r in ctx.recent_rounds if r.status in ("pending", "claimed", "running")), None)
        next_round = None
        if len(entrants) < minimum_entrants:
            needed = minimum_entrants - len(entrants)
            next_round = f"Add {needed} more {_plural_word(needed, 'entrant')} before scheduling can continue."
        elif active_round is not None:
            next_round = f"The next round waits for round #{active_round.round_number} to finish."
        return DivisionCommissionerDescriptionPublic(
            round_schedule=(
                f"Rounds start every {_duration_text(config.schedule_interval_minutes)}"
                f"{_schedule_slot_description(config)} if there are at least "
                f"{_count_text(minimum_entrants)} {_plural_word(minimum_entrants, 'entrant')} in the division."
            ),
            next_round=next_round,
            round_structure=_round_structure_description(stages),
            leaderboard_rules=_leaderboard_rules_description(),
            scoring_mechanics=config.scoring_mechanics,
        )

    def schedule_rounds(self, ctx: ScheduleContext) -> list[RoundSpec]:
        config = self._config()
        current_slot = _current_schedule_slot(datetime.now(UTC), config)
        specs: list[RoundSpec] = []
        for division in ctx.divisions:
            division_rounds = [r for r in ctx.recent_rounds if r.division_id == division.id]
            if any(r.status in ("pending", "claimed", "running") for r in division_rounds):
                continue
            latest_round = max(division_rounds, key=lambda r: r.created_at, default=None)
            if latest_round is not None and latest_round.created_at >= current_slot:
                continue

            rule = select_rule(config, division, ctx.active_memberships, require_minimum=True)
            if rule is None:
                continue
            entrants = division_entries(division, ctx.active_memberships, rule)
            specs.append(
                RoundSpec(
                    division_id=division.id,
                    round_config=V2RoundConfig(
                        stages=rule.stages if rule.stages is not None else config.stages,
                        entrant_policy_version_ids=[entry.policy_version_id for entry in entrants],
                    ),
                    execution_backend=config.default_execution_backend,
                    notes=f"auto-scheduled by {type(self).__name__}:{rule.id}",
                )
            )
        return specs

    def schedule_episodes_for_round_start(self, round_start: CommissionerRoundStart) -> CommissionerScheduleEpisodes:
        config = self._config()
        view = RoundStartView(round_start, config)
        rule = select_rule(config, view.current_division, view.memberships)
        variant_id, num_agents = view.variant()
        entries = view.entries(rule)
        return schedule_entries(
            pool=view.pool(rule),
            primary_entries=entries,
            filler_entries=view.filler_entries(entries),
            num_agents=num_agents,
            variant_id=variant_id,
            config=config,
            recent_results=round_start.recent_results,
        )

    def schedule_episodes(
        self,
        *,
        pool: PolicyPool,
        entries: list[PolicyPoolEntry],
        num_agents: int,
        variant_id: str,
    ) -> CommissionerScheduleEpisodes:
        config = self._config()
        return schedule_entries(
            pool=pool,
            primary_entries=entries,
            filler_entries=[],
            num_agents=num_agents,
            variant_id=variant_id,
            config=config,
        )

    def complete_round_for_round_start(
        self,
        round_start: CommissionerRoundStart,
        episode_results: list[CommissionerProtocolEpisodeResult],
        scheduled_episodes: list[CommissionerProtocolEpisodeRequest] | None = None,
        failed_episodes: list[CommissionerProtocolEpisodeFailed] | None = None,
    ) -> CommissionerRoundComplete:
        config = self._config()
        view = RoundStartView(round_start, config)
        rule = select_rule(config, view.current_division, view.memberships)
        entries = view.entries(rule)
        local_episode_results = view.episode_results(episode_results)
        complete = self.complete_round(
            round_row=view.round_row(),
            pool=view.pool(rule),
            entries=entries,
            episode_results=local_episode_results,
        )
        hook = self.on_round_completed(
            view.on_round_completed_context(
                complete,
                episode_results=local_episode_results,
                scheduled_episodes=scheduled_episodes,
                failed_episodes=failed_episodes,
            )
        )
        complete.policy_membership_events = [
            protocol_policy_membership_event(change) for change in hook.policy_membership_events
        ]
        return complete

    def _round_scores_by_policy(
        self,
        entries: list[PolicyPoolEntry],
        episode_results: list[EpisodeResult],
    ) -> tuple[dict[UUID, float], dict[UUID, int]]:
        scoring = self._config().scoring
        if scoring is None or scoring.round_score != "rank":
            return super()._round_scores_by_policy(entries, episode_results)
        points_lists = _rank_points_lists_by_policy(episode_results)
        scores = {
            entry.policy_version_id: (
                sum(points_lists.get(entry.policy_version_id, []))
                / len(points_lists.get(entry.policy_version_id, []))
                if points_lists.get(entry.policy_version_id)
                else 0.0
            )
            for entry in entries
        }
        ranked_counts = {
            entry.policy_version_id: len(points_lists.get(entry.policy_version_id, [])) for entry in entries
        }
        return scores, ranked_counts

    def complete_round(
        self,
        *,
        round_row: Round,
        pool: PolicyPool,
        entries: list[PolicyPoolEntry],
        episode_results: list[EpisodeResult],
    ) -> CommissionerRoundComplete:
        complete = super().complete_round(
            round_row=round_row,
            pool=pool,
            entries=entries,
            episode_results=episode_results,
        )
        config = self._config()
        if config.ranking.result_metadata:
            for division_ranking in complete.results:
                for ranking in division_ranking.rankings:
                    ranking.result_metadata = dict(ranking.result_metadata) | dict(config.ranking.result_metadata)
        return complete

    def on_round_completed(self, ctx: OnRoundCompletedContext) -> OnRoundCompletedResult:
        config = self._config()
        if not config.membership_changes:
            return super().on_round_completed(ctx)
        events = build_membership_events(ctx, config)
        default_events = build_default_competing_substatus_events(
            ctx,
            exclude_membership_ids={event.league_policy_membership_id for event in events},
        )
        return OnRoundCompletedResult(policy_membership_events=[*events, *default_events])


def _membership_status(status: Any) -> str:
    return status.value if hasattr(status, "value") else str(status)


def _legacy_division_migration_evidence(from_division: str, to_division: str) -> PolicyMembershipEventEvidence:
    return PolicyMembershipEventEvidence(
        type="tournament_restructure",
        title="Tournament restructure",
        summary=f"Tournament restructure {from_division}->{to_division}",
        metadata={
            "from_division": from_division,
            "to_division": to_division,
        },
    )
