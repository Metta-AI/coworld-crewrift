from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from commissioners.common.models import (
    DIVISION_TYPE_COMPETITION,
    DIVISION_TYPE_STAGING,
    DivisionConfig,
    DivisionSnapshot,
    MembershipSnapshot,
)
from commissioners.common.models import V2StageConfig
from commissioners.common.utils import (
    MEAN_ROUND_SCORE_KIND,
    MEAN_SCORE_EWMA_SCORING_MECHANICS,
    RANK_EPISODE_EWMA_SCORING_MECHANICS,
    RANK_EPISODE_ROUND_SCORE_KIND,
)

CONFIG_KEY = "ruleset_strategy"
IMAGE_CONFIG_NAME_ENV = "RULESET_STRATEGY_CONFIG_NAME"
IMAGE_CONFIG_PATH_ENV = "RULESET_STRATEGY_CONFIG_PATH"
DEFAULT_IMAGE_CONFIG_NAME = "default"
BUNDLED_CONFIG_DIR = Path(__file__).resolve().parents[2] / "ruleset_strategy_commissioner" / "configs"

SeatingStrategy = Literal["baseline_window", "rolling_window", "team_blocks", "leaderboard_neighbors"]
FillSeatsStrategy = Literal["duplicate", "fill_from_divisions", "strict"]
EntrantShortcut = Literal["qualifying", "champions"]


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DivisionMatch(_ConfigModel):
    name: str | None = None
    type: str | None = None

    def matches(self, division: DivisionSnapshot) -> bool:
        if self.name is not None and division.name != self.name:
            return False
        if self.type is not None and division.type != self.type:
            return False
        return True


class EntrantSelector(_ConfigModel):
    status: str | None = None
    substatus: str | None = None
    match_substatus: bool = False
    is_champion: bool | None = None

    def matches(self, membership: MembershipSnapshot) -> bool:
        if self.status is not None and membership.status != self.status:
            return False
        if self.match_substatus and membership.substatus != self.substatus:
            return False
        if self.is_champion is not None and membership.is_champion != self.is_champion:
            return False
        return True


class DivisionRule(_ConfigModel):
    id: str
    match: DivisionMatch = Field(default_factory=DivisionMatch)
    entrants: EntrantSelector | None = None
    minimum_entrants: int = Field(default=1, gt=0)
    stages: list[V2StageConfig] | None = None
    description: str | None = None


class FillerSource(_ConfigModel):
    match: DivisionMatch = Field(default_factory=DivisionMatch)
    entrants: EntrantSelector = Field(
        default_factory=lambda: EntrantSelector(
            status="competing",
            is_champion=True,
        )
    )


class InsufficientPlayersConfig(_ConfigModel):
    strategy: FillSeatsStrategy = "duplicate"
    sources: list[FillerSource] = Field(default_factory=list)
    duplicate_after_fill: bool = True


class RankingConfig(_ConfigModel):
    result_metadata: dict[str, Any] = Field(default_factory=dict)
    filter_metadata: dict[str, Any] = Field(default_factory=dict)
    ewma_halflife_hours: float = Field(default=2.0, gt=0)


class ChangeMatch(_ConfigModel):
    division: DivisionMatch = Field(default_factory=DivisionMatch)
    membership: EntrantSelector | None = None

    def matches(self, division: DivisionSnapshot, membership: MembershipSnapshot) -> bool:
        if not self.division.matches(division):
            return False
        if self.membership is not None and not self.membership.matches(membership):
            return False
        return True


class TransitionCriteria(_ConfigModel):
    completed_episodes_gt: int | None = None
    completed_episodes_lte: int | None = None
    score_gt: float | None = None
    score_lte: float | None = None
    otherwise: bool = False

    @model_validator(mode="after")
    def require_single_condition(self) -> TransitionCriteria:
        conditions = [
            self.completed_episodes_gt is not None,
            self.completed_episodes_lte is not None,
            self.score_gt is not None,
            self.score_lte is not None,
            self.otherwise,
        ]
        if sum(conditions) != 1:
            raise ValueError("transition criteria must specify exactly one condition")
        return self


class TransitionTarget(_ConfigModel):
    to_division_name: str | None = None
    to_division_match: DivisionMatch | None = None
    status: str | None = None
    substatus: str | None = None
    reason: str | None = None


class Transition(_ConfigModel):
    id: str | None = None
    name: str | None = None
    criteria: TransitionCriteria
    to: TransitionTarget


class TransitionRule(_ConfigModel):
    type: Literal["transition"]
    match: ChangeMatch = Field(default_factory=ChangeMatch)
    transitions: list[Transition] = Field(min_length=1)


MembershipChangeRule = TransitionRule


class LeaderboardScoringConfig(_ConfigModel):
    type: Literal["ewma"] = "ewma"
    half_life_hours: float = Field(default=2.0, gt=0)


class ScoringConfig(_ConfigModel):
    # "mean": round score is the mean of a policy's per-episode scores.
    # "rank": round score is the mean of a policy's per-episode rank points (placement within
    #         each episode, N..1), so margins of victory are discarded and only placement counts.
    round_score: Literal["mean", "rank"] = "mean"
    leaderboard: LeaderboardScoringConfig = Field(default_factory=LeaderboardScoringConfig)
    mechanics: str | None = None


class DispatchThrottleConfig(_ConfigModel):
    enabled: bool = False
    min_in_flight: int = Field(default=1, gt=0)
    max_in_flight: int = Field(default=64, gt=0)
    startup_buffer_seconds: float = Field(default=60.0, ge=0)
    target_load: float = Field(default=0.80, gt=0)
    worker_seconds_per_episode: float = Field(default=25.0, gt=0)
    stagger_seconds: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_ordered_in_flight_bounds(self) -> DispatchThrottleConfig:
        if self.max_in_flight < self.min_in_flight:
            raise ValueError("dispatch throttle max_in_flight must be >= min_in_flight")
        return self

    def max_concurrent_episodes(self, game_timeout_seconds: float | None) -> int:
        if game_timeout_seconds is None:
            return self.min_in_flight
        usable_window_seconds = max(1.0, game_timeout_seconds - self.startup_buffer_seconds)
        duty_cycle = self.worker_seconds_per_episode / usable_window_seconds
        allowed = self.max_in_flight if duty_cycle <= 0 else int(self.target_load / duty_cycle)
        return max(self.min_in_flight, min(self.max_in_flight, allowed))

    def episode_stagger_seconds(self, game_timeout_seconds: float | None) -> float:
        if self.stagger_seconds is not None:
            return self.stagger_seconds
        if game_timeout_seconds is None:
            return 0.0
        usable_window_seconds = max(1.0, game_timeout_seconds - self.startup_buffer_seconds)
        return usable_window_seconds / self.max_concurrent_episodes(game_timeout_seconds)


class StageScheduleConfig(_ConfigModel):
    label: str = "Round"
    episodes: int | None = Field(default=None, gt=0)
    attempts: int | None = Field(default=None, gt=0)
    min_episodes_per_entrant: int | None = Field(default=None, gt=0)
    self_play: bool = False

    @model_validator(mode="after")
    def require_single_count(self) -> StageScheduleConfig:
        if self.episodes is not None and self.attempts is not None:
            raise ValueError("stage schedule may use either episodes or attempts, not both")
        return self

    def to_stage_config(self) -> V2StageConfig:
        return V2StageConfig(
            label=self.label,
            num_episodes=self.attempts or self.episodes or 1,
            min_episodes_per_entrant=self.min_episodes_per_entrant,
            self_play=self.self_play,
        )


class RulesetDefaults(_ConfigModel):
    seating: SeatingStrategy = "baseline_window"
    team_count: int = Field(default=4, gt=0)
    fill_seats: FillSeatsStrategy = "duplicate"
    fill_from: list[FillerSource] = Field(default_factory=list)
    duplicate_after_fill: bool = True
    min_entries_to_start: int = Field(default=1, gt=0)
    stage: StageScheduleConfig = Field(default_factory=StageScheduleConfig)

    def insufficient_players(self) -> InsufficientPlayersConfig:
        return InsufficientPlayersConfig(
            strategy=self.fill_seats,
            sources=self.fill_from,
            duplicate_after_fill=self.fill_seats != "strict" and self.duplicate_after_fill,
        )


class TransitionCriteriaConfig(_ConfigModel):
    completed_episodes_gt: int | None = None
    completed_episodes_lte: int | None = None
    score_gt: float | None = None
    score_lte: float | None = None

    @model_validator(mode="after")
    def require_single_condition(self) -> TransitionCriteriaConfig:
        conditions = [
            self.completed_episodes_gt is not None,
            self.completed_episodes_lte is not None,
            self.score_gt is not None,
            self.score_lte is not None,
        ]
        if sum(conditions) != 1:
            raise ValueError("transition criteria must specify exactly one condition")
        return self


class UpdateMembershipAction(_ConfigModel):
    type: Literal["update_membership"] = "update_membership"
    division: str | None = None
    status: str | None = None
    substatus: str | None = None
    reason: str | None = None


class EpisodeCompleteTransition(_ConfigModel):
    id: str | None = None
    name: str | None = None
    criteria: Literal["otherwise"] | TransitionCriteriaConfig
    actions: list[UpdateMembershipAction] = Field(min_length=1, max_length=1)


class DivisionStageConfig(_ConfigModel):
    id: str
    entrants: EntrantSelector | EntrantShortcut | None = None
    schedule: StageScheduleConfig = Field(default_factory=StageScheduleConfig)
    on_round_complete: list[EpisodeCompleteTransition] = Field(default_factory=list)
    on_episode_complete: list[EpisodeCompleteTransition] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_single_transition_hook(self) -> DivisionStageConfig:
        if self.on_round_complete and self.on_episode_complete:
            raise ValueError(
                "stage config may use either on_round_complete or legacy on_episode_complete, not both"
            )
        return self


class RulesetDivisionConfig(_ConfigModel):
    name: str | None = None
    previous_name: str | None = None
    level: int | None = None
    description: str | None = None
    match: DivisionMatch = Field(default_factory=DivisionMatch)
    entrants: EntrantSelector | EntrantShortcut | None = None
    min_entries_to_start: int | None = Field(default=None, gt=0)
    stage: StageScheduleConfig | None = None
    stages: list[DivisionStageConfig] = Field(default_factory=list)
    on_round_complete: list[EpisodeCompleteTransition] = Field(default_factory=list)
    on_episode_complete: list[EpisodeCompleteTransition] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_single_stage_shape(self) -> RulesetDivisionConfig:
        if self.stage is not None and self.stages:
            raise ValueError("division config may use either stage or stages, not both")
        if self.on_round_complete and self.on_episode_complete:
            raise ValueError(
                "division config may use either on_round_complete or legacy on_episode_complete, not both"
            )
        if self.round_complete_transitions and self.stages:
            raise ValueError("division-level on_round_complete is only valid with a single stage")
        return self

    @property
    def round_complete_transitions(self) -> list[EpisodeCompleteTransition]:
        return self.on_round_complete or self.on_episode_complete


class RulesetStrategyCommissionerConfig(_ConfigModel):
    """Resolved ruleset config.

    The accepted shape mirrors the public YAML shape. Lower-level division and
    transition rules are derived by properties below instead of being part of
    the config contract.
    """

    schedule_interval_minutes: int = Field(default=10, gt=0)
    backend: str = "dispatch"
    scoring: ScoringConfig | None = None
    dispatch_throttle: DispatchThrottleConfig = Field(default_factory=DispatchThrottleConfig)
    defaults: RulesetDefaults = Field(default_factory=RulesetDefaults)
    divisions: dict[str, RulesetDivisionConfig]

    @model_validator(mode="after")
    def require_divisions(self) -> RulesetStrategyCommissionerConfig:
        if not self.divisions:
            raise ValueError("ruleset strategy commissioner requires at least one division")
        return self

    @classmethod
    def from_mapping(cls, config: dict[str, Any]) -> RulesetStrategyCommissionerConfig:
        nested = config.get(CONFIG_KEY)
        if isinstance(nested, dict):
            return cls.model_validate(nested)
        return cls.model_validate(config)

    @property
    def default_execution_backend(self) -> str:
        return self.backend

    @property
    def stages(self) -> list[V2StageConfig]:
        return [self.defaults.stage.to_stage_config()]

    @property
    def seating(self) -> SeatingStrategy:
        return self.defaults.seating

    @property
    def insufficient_players(self) -> InsufficientPlayersConfig:
        return self.defaults.insufficient_players()

    @property
    def round_score_kind(self) -> str:
        if self.scoring is not None and self.scoring.round_score == "rank":
            return RANK_EPISODE_ROUND_SCORE_KIND
        return MEAN_ROUND_SCORE_KIND

    @property
    def ranking(self) -> RankingConfig:
        if self.scoring is None:
            return RankingConfig()
        # Tag/filter round results by score kind so that switching round_score (e.g. mean -> rank)
        # excludes the now-incomparable prior-regime results from the leaderboard instead of
        # blending different score scales.
        return RankingConfig(
            result_metadata={"score_kind": self.round_score_kind},
            filter_metadata={"score_kind": self.round_score_kind},
            ewma_halflife_hours=self.scoring.leaderboard.half_life_hours,
        )

    @property
    def scoring_mechanics(self) -> str | None:
        if self.scoring is None:
            return None
        if self.scoring.mechanics is not None:
            return self.scoring.mechanics
        half_life_hours = self.scoring.leaderboard.half_life_hours
        is_rank = self.scoring.round_score == "rank"
        if half_life_hours == 2:
            return RANK_EPISODE_EWMA_SCORING_MECHANICS if is_rank else MEAN_SCORE_EWMA_SCORING_MECHANICS
        half_life_text = int(half_life_hours) if half_life_hours.is_integer() else half_life_hours
        if is_rank:
            return (
                "Rounds rank policies by placement within each episode (N points for the episode winner of an "
                "N-policy game down to 1 for last, ties sharing the better place), averaged across the episodes "
                "each policy played. The division leaderboard combines completed rounds with a "
                f"{half_life_text}-hour half-life EWMA, so newer rounds count more than older rounds."
            )
        return (
            "Rounds rank policies by the average score reported by the game across each policy's episode slots. "
            "The division leaderboard only uses current average-score round results and combines completed rounds "
            f"with a {half_life_text}-hour half-life EWMA, so newer rounds count more than older rounds."
        )

    @property
    def migration_divisions(self) -> list[DivisionConfig]:
        configs: list[DivisionConfig] = []
        competition_level = 1
        for key, division in self.divisions.items():
            division_type = division.match.type or (
                DIVISION_TYPE_STAGING if key == "qualifiers" else DIVISION_TYPE_COMPETITION
            )
            if division.level is not None:
                level = division.level
            elif division_type == DIVISION_TYPE_STAGING:
                level = -99
            else:
                level = competition_level
                competition_level += 1
            configs.append(
                DivisionConfig(
                    name=division.name or division.match.name or key.replace("_", " ").title(),
                    level=level,
                    type=division_type,
                    description=division.description,
                    previous_name=division.previous_name,
                )
            )
        return configs

    @property
    def division_rules(self) -> list[DivisionRule]:
        rules: list[DivisionRule] = []
        for division_key, division in self.divisions.items():
            rules.extend(self._division_rules(division_key, division))
        return rules

    @property
    def membership_changes(self) -> list[TransitionRule]:
        changes: list[TransitionRule] = []
        for division in self.divisions.values():
            changes.extend(self._membership_changes(division))
        return changes

    def _division_rules(self, division_key: str, division: RulesetDivisionConfig) -> list[DivisionRule]:
        stages = self._expanded_stages(division)
        if not stages:
            return [
                DivisionRule(
                    id=division_key,
                    match=division.match,
                    entrants=self._entrant_selector(division.entrants, division.match),
                    minimum_entrants=division.min_entries_to_start or self.defaults.min_entries_to_start,
                    stages=[division.stage.to_stage_config()] if division.stage is not None else None,
                )
            ]

        return [
            DivisionRule(
                id=f"{division_key}-{stage.id}",
                match=division.match,
                entrants=self._stage_entrant_selector(division, stage, index, len(stages)),
                minimum_entrants=division.min_entries_to_start or self.defaults.min_entries_to_start,
                stages=[stage.schedule.to_stage_config()],
            )
            for index, stage in reversed(list(enumerate(stages)))
        ]

    def _membership_changes(self, division: RulesetDivisionConfig) -> list[TransitionRule]:
        changes: list[TransitionRule] = []
        stages = self._expanded_stages(division)
        for index, stage in enumerate(stages):
            transitions = self._round_complete_transitions(stage)
            if not transitions:
                continue
            match = ChangeMatch(
                division=division.match,
                membership=self._stage_entrant_selector(division, stage, index, len(stages)),
            )
            changes.append(self._transition_rule(match, transitions))
        if not stages and division.round_complete_transitions:
            match = ChangeMatch(
                division=division.match,
                membership=self._entrant_selector(division.entrants, division.match),
            )
            changes.append(self._transition_rule(match, division.round_complete_transitions))
        return changes

    def _transition_rule(
        self,
        match: ChangeMatch,
        transitions: list[EpisodeCompleteTransition],
    ) -> TransitionRule:
        return TransitionRule(
            type="transition",
            match=match,
            transitions=[self._transition(transition) for transition in transitions],
        )

    def _transition(self, transition: EpisodeCompleteTransition) -> Transition:
        action = transition.actions[0]
        return Transition(
            id=transition.id,
            name=transition.name,
            criteria=self._criteria(transition.criteria),
            to=TransitionTarget(
                to_division_match=self._move_to_match(action.division),
                status=action.status,
                substatus=action.substatus,
                reason=action.reason,
            ),
        )

    def _criteria(self, criteria: Literal["otherwise"] | TransitionCriteriaConfig) -> TransitionCriteria:
        if criteria == "otherwise":
            return TransitionCriteria(otherwise=True)
        return TransitionCriteria(
            completed_episodes_gt=criteria.completed_episodes_gt,
            completed_episodes_lte=criteria.completed_episodes_lte,
            score_gt=criteria.score_gt,
            score_lte=criteria.score_lte,
        )

    def _expanded_stages(self, division: RulesetDivisionConfig) -> list[DivisionStageConfig]:
        if division.stages:
            return division.stages
        if division.stage is None and not division.round_complete_transitions:
            return []
        return [
            DivisionStageConfig(
                id="round",
                schedule=division.stage or self.defaults.stage,
                on_round_complete=division.on_round_complete,
                on_episode_complete=division.on_episode_complete,
            )
        ]

    def _round_complete_transitions(self, stage: DivisionStageConfig) -> list[EpisodeCompleteTransition]:
        return stage.on_round_complete or stage.on_episode_complete

    def _stage_entrant_selector(
        self,
        division: RulesetDivisionConfig,
        stage: DivisionStageConfig,
        index: int,
        stage_count: int,
    ) -> EntrantSelector:
        selector = self._entrant_selector(stage.entrants or division.entrants, division.match)
        if stage.entrants is not None or stage_count == 1:
            return selector
        return EntrantSelector(
            status=selector.status,
            substatus=None if index == 0 else stage.id,
            match_substatus=True,
            is_champion=selector.is_champion,
        )

    def _entrant_selector(
        self,
        entrants: EntrantSelector | EntrantShortcut | None,
        division_match: DivisionMatch,
    ) -> EntrantSelector:
        if isinstance(entrants, EntrantSelector):
            return entrants
        if entrants == "qualifying":
            return EntrantSelector(status="qualifying")
        if entrants == "champions":
            return EntrantSelector(status="competing", is_champion=True)
        if division_match.type == DIVISION_TYPE_STAGING:
            return EntrantSelector(status="qualifying")
        if division_match.type == DIVISION_TYPE_COMPETITION:
            return EntrantSelector(status="competing", is_champion=True)
        return EntrantSelector()

    def _move_to_match(self, division_key: str | None) -> DivisionMatch | None:
        if division_key is None:
            return None
        division = self.divisions.get(division_key)
        return division.match if division is not None else DivisionMatch(name=division_key)


def default_entrant_selector(division: DivisionSnapshot) -> EntrantSelector:
    if division.type == DIVISION_TYPE_STAGING:
        return EntrantSelector(status="qualifying")
    return EntrantSelector(status="competing", is_champion=True)


def load_ruleset_strategy_config_file(path: Path) -> RulesetStrategyCommissionerConfig:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"ruleset strategy config file must contain a mapping: {path}")
    return RulesetStrategyCommissionerConfig.from_mapping(data)


@lru_cache(maxsize=1)
def load_image_ruleset_strategy_config() -> RulesetStrategyCommissionerConfig:
    path_env = os.getenv(IMAGE_CONFIG_PATH_ENV)
    if path_env:
        return load_ruleset_strategy_config_file(Path(path_env))

    config_name = os.getenv(IMAGE_CONFIG_NAME_ENV, DEFAULT_IMAGE_CONFIG_NAME).strip() or DEFAULT_IMAGE_CONFIG_NAME
    if "/" in config_name or "\\" in config_name or config_name in {".", ".."}:
        raise ValueError(f"{IMAGE_CONFIG_NAME_ENV} must be a bundled config name, got {config_name!r}")
    return load_ruleset_strategy_config_file(BUNDLED_CONFIG_DIR / f"{config_name}.yaml")
