from __future__ import annotations

from collections import defaultdict
from functools import cached_property
from typing import Any
from uuid import UUID

from commissioners.common.models import (
    DivisionSnapshot,
    EpisodeResult,
    LeagueSnapshot,
    MembershipSnapshot,
    OnRoundCompletedContext,
    PolicyPool,
    PolicyPoolEntry,
    PolicyTransitionObservation,
    Round,
    RoundPolicyScore,
    RoundResultSnapshot,
    V2RoundConfig,
    V2StageConfig,
)
from commissioners.common.protocol import EpisodeRequest as CommissionerProtocolEpisodeRequest
from commissioners.common.protocol import EpisodeFailed as CommissionerProtocolEpisodeFailed
from commissioners.common.protocol import EpisodeResult as CommissionerProtocolEpisodeResult
from commissioners.common.protocol import RoundComplete as CommissionerRoundComplete
from commissioners.common.protocol import RoundStart as CommissionerRoundStart
from commissioners.common.ruleset_strategy.config import CONFIG_KEY, RulesetStrategyCommissionerConfig, DivisionRule
from commissioners.common.ruleset_strategy.entrants import division_entries


class RoundStartView:
    def __init__(self, round_start: CommissionerRoundStart, config: RulesetStrategyCommissionerConfig) -> None:
        self.round_start = round_start
        self.config = config

    @cached_property
    def round_config(self) -> dict[str, Any]:
        state = self.round_start.state if isinstance(self.round_start.state, dict) else {}
        round_config = state.get("round_config") if isinstance(state.get("round_config"), dict) else {}
        return dict(round_config)

    @cached_property
    def current_division(self) -> DivisionSnapshot:
        configured_id = self.round_config.get("current_division_id") or self.round_config.get("division_id")
        if configured_id is not None:
            division_id = UUID(str(configured_id))
            match = next((division for division in self.round_start.divisions if division.id == division_id), None)
            if match is not None:
                return self._division_snapshot(match)

        membership_division_ids = {membership.division_id for membership in self.round_start.memberships}
        if len(membership_division_ids) == 1:
            division_id = next(iter(membership_division_ids))
            match = next((division for division in self.round_start.divisions if division.id == division_id), None)
            if match is not None:
                return self._division_snapshot(match)

        if not self.round_start.divisions:
            raise ValueError("round_start must include at least one division")
        return self._division_snapshot(
            min(self.round_start.divisions, key=lambda division: (division.level, division.name, str(division.id)))
        )

    @cached_property
    def memberships(self) -> list[MembershipSnapshot]:
        return [
            MembershipSnapshot(
                id=membership.id,
                league_id=self.round_start.league.id,
                division_id=membership.division_id,
                policy_version_id=membership.policy_version_id,
                player_id=membership.player_id,
                status=membership.status,
                substatus=membership.substatus,
                is_champion=membership.is_champion,
            )
            for membership in self.round_start.memberships
        ]

    @cached_property
    def divisions(self) -> list[DivisionSnapshot]:
        return [self._division_snapshot(division) for division in self.round_start.divisions]

    def variant(self) -> tuple[str, int]:
        default_variant_id = self.round_start.variants[0].id if self.round_start.variants else "default"
        variant_id = str(self.round_config.get("variant_id") or default_variant_id)
        variant = next((candidate for candidate in self.round_start.variants if candidate.id == variant_id), None)
        if variant is None and self.round_start.variants:
            variant = self.round_start.variants[0]
            variant_id = variant.id
        if variant is None:
            return variant_id, 1
        num_agents = variant.game_config.get("num_agents")
        if isinstance(num_agents, int):
            return variant_id, num_agents
        return variant_id, len(self.entries(None)) or 1

    def entries(self, rule: DivisionRule | None) -> list[PolicyPoolEntry]:
        entries = division_entries(self.current_division, self.memberships, rule)
        configured_order = self.round_config.get("entrant_policy_version_ids")
        if isinstance(configured_order, list):
            order = {UUID(str(policy_id)): index for index, policy_id in enumerate(configured_order)}
            entries = [entry for entry in entries if entry.policy_version_id in order]
            entries.sort(key=lambda entry: order[entry.policy_version_id])
            for index, entry in enumerate(entries):
                entry.seed_order = index
        for entry in entries:
            entry.pool_id = self.round_start.round_id
        return entries

    def filler_entries(self, primary_entries: list[PolicyPoolEntry]) -> list[PolicyPoolEntry]:
        if self.config.insufficient_players.strategy != "fill_from_divisions":
            return []
        primary_policy_ids = {entry.policy_version_id for entry in primary_entries}
        division_by_id = {division.id: division for division in self.divisions}
        entries: list[PolicyPoolEntry] = []
        seen: set[UUID] = set(primary_policy_ids)
        for source in self.config.insufficient_players.sources:
            for membership in self.memberships:
                division = division_by_id.get(membership.division_id)
                if division is None or not source.match.matches(division) or not source.entrants.matches(membership):
                    continue
                if membership.policy_version_id in seen:
                    continue
                seen.add(membership.policy_version_id)
                entries.append(
                    PolicyPoolEntry(
                        pool_id=self.round_start.round_id,
                        policy_version_id=membership.policy_version_id,
                        player_id=membership.player_id,
                        seed_order=len(entries),
                    )
                )
        return entries

    def pool(self, rule: DivisionRule | None) -> PolicyPool:
        base_stage = (rule.stages if rule and rule.stages is not None else self.config.stages)[0]
        round_config = V2RoundConfig.model_validate(self.round_config)
        stage = base_stage
        if round_config.stages is not None:
            override_stage = round_config.stages[0].model_dump(mode="json", exclude_none=True, exclude_unset=True)
            stage = V2StageConfig.model_validate({**base_stage.model_dump(mode="json"), **override_stage})
        pool_config = stage.model_dump(mode="json")
        pool_config[CONFIG_KEY] = self.config.model_dump(mode="json")
        return PolicyPool(id=self.round_start.round_id, label=stage.label, pool_type="round", config=pool_config)

    def round_row(self) -> Round:
        return Round(
            id=self.round_start.round_id,
            public_id=str(self.round_start.round_id),
            division_id=self.current_division.id,
            round_number=self.round_start.round_number,
            commissioner_key=self.round_start.league.commissioner_key or "ruleset_strategy",
            round_config=self.round_config,
        )

    def episode_results(
        self,
        episode_results: list[CommissionerProtocolEpisodeResult],
    ) -> list[EpisodeResult]:
        return [
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

    def transition_observations(
        self,
        episode_results: list[EpisodeResult],
        scheduled_episodes: list[CommissionerProtocolEpisodeRequest] | None,
        failed_episodes: list[CommissionerProtocolEpisodeFailed] | None = None,
    ) -> dict[UUID, PolicyTransitionObservation]:
        policies_by_request_id: dict[str, set[UUID]] = {}
        scheduled_episode_counts: dict[UUID, int] = defaultdict(int)
        if scheduled_episodes is None:
            for result in episode_results:
                for policy_version_id in {score.policy_version_id for score in result.scores}:
                    scheduled_episode_counts[policy_version_id] += 1
        else:
            for episode in scheduled_episodes:
                episode_policy_ids = set(episode.policy_version_ids)
                policies_by_request_id[episode.request_id] = episode_policy_ids
                for policy_version_id in episode_policy_ids:
                    scheduled_episode_counts[policy_version_id] += 1

        score_lists: dict[UUID, list[float]] = defaultdict(list)
        completed_episode_counts: dict[UUID, int] = defaultdict(int)
        for result in episode_results:
            episode_policy_ids: set[UUID] = set()
            for score in result.scores:
                score_lists[score.policy_version_id].append(score.score)
                episode_policy_ids.add(score.policy_version_id)
            for policy_version_id in episode_policy_ids:
                completed_episode_counts[policy_version_id] += 1

        failed_episode_counts: dict[UUID, int] = defaultdict(int)
        failed_request_ids: dict[UUID, list[str]] = defaultdict(list)
        failure_error_samples: dict[UUID, list[str]] = defaultdict(list)
        for failed in failed_episodes or []:
            for policy_version_id in policies_by_request_id.get(failed.request_id, set()):
                failed_episode_counts[policy_version_id] += 1
                failed_request_ids[policy_version_id].append(failed.request_id)
                error_sample = failed.error[:500]
                if error_sample not in failure_error_samples[policy_version_id]:
                    if len(failure_error_samples[policy_version_id]) < 3:
                        failure_error_samples[policy_version_id].append(error_sample)

        return {
            policy_version_id: PolicyTransitionObservation(
                scheduled_episodes=scheduled_episode_counts[policy_version_id],
                completed_episodes=completed_episode_counts[policy_version_id],
                score=(
                    sum(score_lists[policy_version_id]) / len(score_lists[policy_version_id])
                    if score_lists[policy_version_id]
                    else 0.0
                ),
                failed_episodes=failed_episode_counts[policy_version_id],
                failed_request_ids=failed_request_ids[policy_version_id],
                failure_error_samples=failure_error_samples[policy_version_id],
            )
            for policy_version_id in scheduled_episode_counts
        }

    def on_round_completed_context(
        self,
        complete: CommissionerRoundComplete,
        *,
        episode_results: list[EpisodeResult],
        scheduled_episodes: list[CommissionerProtocolEpisodeRequest] | None = None,
        failed_episodes: list[CommissionerProtocolEpisodeFailed] | None = None,
    ) -> OnRoundCompletedContext:
        return OnRoundCompletedContext(
            league=LeagueSnapshot(
                id=self.round_start.league.id,
                commissioner_key=self.round_start.league.commissioner_key or "ruleset_strategy",
                commissioner_config={CONFIG_KEY: self.config.model_dump(mode="json")},
            ),
            division=self.current_division,
            all_divisions=self.divisions,
            round_config=V2RoundConfig.model_validate(self.round_config),
            round_results=[
                RoundResultSnapshot(
                    round_id=self.round_start.round_id,
                    policy_version_id=ranking.policy_version_id,
                    rank=ranking.rank,
                    score=ranking.score,
                    result_metadata=ranking.result_metadata,
                )
                for division_ranking in complete.results
                for ranking in division_ranking.rankings
            ],
            transition_observations=self.transition_observations(
                episode_results,
                scheduled_episodes,
                failed_episodes,
            ),
            division_memberships=[
                membership for membership in self.memberships if membership.division_id == self.current_division.id
            ],
            recent_results=[],
            commissioner_config={CONFIG_KEY: self.config.model_dump(mode="json")},
        )

    def _division_snapshot(self, division: Any) -> DivisionSnapshot:
        return DivisionSnapshot(
            id=division.id,
            name=division.name,
            level=division.level,
            league_id=self.round_start.league.id,
            type=division.type,
        )
