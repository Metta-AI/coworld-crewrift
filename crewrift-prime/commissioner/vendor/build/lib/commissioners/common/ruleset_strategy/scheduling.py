from __future__ import annotations

from typing import Any

from commissioners.common.models import PolicyPool, PolicyPoolEntry, PoolConfig
from commissioners.common.protocol import EpisodeRequest as CommissionerEpisodeRequest
from commissioners.common.protocol import ScheduleEpisodes as CommissionerScheduleEpisodes
from commissioners.common.utils import (
    _build_entry_indices,
    _build_rolling_window_entry_indices,
    _entry_index_offset,
    _pool_episode_count,
)
from commissioners.common.ruleset_strategy.config import RulesetStrategyCommissionerConfig


def schedule_entries(
    *,
    pool: PolicyPool,
    primary_entries: list[PolicyPoolEntry],
    filler_entries: list[PolicyPoolEntry],
    num_agents: int,
    variant_id: str,
    config: RulesetStrategyCommissionerConfig,
    recent_results: list[Any] | None = None,
) -> CommissionerScheduleEpisodes:
    if not primary_entries:
        raise ValueError("pool must have at least one primary entry")
    pool_config = PoolConfig.model_validate(pool.config)
    if pool_config.self_play:
        episodes_per_entrant = pool_config.min_episodes_per_entrant or pool_config.num_episodes
        return CommissionerScheduleEpisodes(
            episodes=[
                CommissionerEpisodeRequest(
                    request_id=str(entry_index * episodes_per_entrant + episode_index),
                    variant_id=variant_id,
                    policy_version_ids=[entry.policy_version_id] * num_agents,
                    tags={"pool_id": str(pool.id)},
                )
                for entry_index, entry in enumerate(primary_entries)
                for episode_index in range(episodes_per_entrant)
            ]
        )

    if config.seating == "team_blocks":
        team_count = config.defaults.team_count
        if len(primary_entries) < team_count:
            raise ValueError(f"team_blocks seating requires at least {team_count} primary entries")
        if num_agents % team_count != 0:
            raise ValueError(f"team_blocks seating requires num_agents divisible by {team_count}")

        team_size = num_agents // team_count
        num_episodes = _pool_episode_count(
            config=pool_config,
            num_entries=len(primary_entries),
            num_agents=team_count,
        )
        episodes: list[CommissionerEpisodeRequest] = []
        for job_index in range(num_episodes):
            entry_indices = [
                (job_index * team_count + team_index) % len(primary_entries) for team_index in range(team_count)
            ]
            rotation = job_index % team_count
            entry_indices = entry_indices[rotation:] + entry_indices[:rotation]
            episodes.append(
                CommissionerEpisodeRequest(
                    request_id=str(job_index),
                    variant_id=variant_id,
                    policy_version_ids=[
                        primary_entries[entry_index].policy_version_id
                        for entry_index in entry_indices
                        for _slot in range(team_size)
                    ],
                    tags={"pool_id": str(pool.id)},
                )
            )
        return CommissionerScheduleEpisodes(episodes=episodes)

    if config.seating == "leaderboard_neighbors":
        return _schedule_leaderboard_neighbors(
            pool=pool,
            pool_config=pool_config,
            primary_entries=primary_entries,
            filler_entries=filler_entries,
            num_agents=num_agents,
            variant_id=variant_id,
            recent_results=recent_results or [],
        )

    num_episodes = _pool_episode_count(
        config=pool_config,
        num_entries=len(primary_entries),
        num_agents=num_agents,
    )
    return CommissionerScheduleEpisodes(
        episodes=[
            CommissionerEpisodeRequest(
                request_id=str(job_index),
                variant_id=variant_id,
                policy_version_ids=[
                    entry.policy_version_id
                    for entry in episode_entries(
                        job_index,
                        primary_entries=primary_entries,
                        filler_entries=filler_entries,
                        num_agents=num_agents,
                        config=config,
                    )
                ],
                tags={"pool_id": str(pool.id)},
            )
            for job_index in range(num_episodes)
        ]
    )


def episode_entries(
    job_index: int,
    *,
    primary_entries: list[PolicyPoolEntry],
    filler_entries: list[PolicyPoolEntry],
    num_agents: int,
    config: RulesetStrategyCommissionerConfig,
) -> list[PolicyPoolEntry]:
    if len(primary_entries) >= num_agents:
        if config.seating == "baseline_window":
            indices = _build_entry_indices(
                num_entries=len(primary_entries),
                num_agents=num_agents,
                offset=_entry_index_offset(
                    job_index=job_index,
                    num_entries=len(primary_entries),
                    num_agents=num_agents,
                ),
            )
        else:
            indices = _build_rolling_window_entry_indices(
                job_index=job_index,
                num_entries=len(primary_entries),
                num_agents=num_agents,
            )
        return [primary_entries[index] for index in indices]

    seats = list(primary_entries)
    if config.insufficient_players.strategy == "strict":
        raise ValueError(f"not enough primary entries to fill {num_agents} seats")
    if config.insufficient_players.strategy == "fill_from_divisions":
        seats.extend(cycled(filler_entries, num_agents - len(seats), offset=job_index))
    if len(seats) < num_agents and config.insufficient_players.duplicate_after_fill:
        seats.extend(cycled(seats or primary_entries, num_agents - len(seats), offset=job_index))
    if len(seats) < num_agents:
        raise ValueError(f"not enough entries to fill {num_agents} seats")
    return seats[:num_agents]


def cycled(entries: list[PolicyPoolEntry], count: int, *, offset: int = 0) -> list[PolicyPoolEntry]:
    if count <= 0 or not entries:
        return []
    return [entries[(offset + index) % len(entries)] for index in range(count)]


def _schedule_leaderboard_neighbors(
    *,
    pool: PolicyPool,
    pool_config: PoolConfig,
    primary_entries: list[PolicyPoolEntry],
    filler_entries: list[PolicyPoolEntry],
    num_agents: int,
    variant_id: str,
    recent_results: list[Any],
) -> CommissionerScheduleEpisodes:
    if num_agents != 2:
        raise ValueError("leaderboard_neighbors seating requires a two-player variant")

    episodes_per_entrant = pool_config.min_episodes_per_entrant or pool_config.num_episodes
    ordered_primary_entries = _leaderboard_ordered_entries(primary_entries, recent_results)
    opponent_entries = _dedupe_entries([*ordered_primary_entries, *filler_entries])
    if len(opponent_entries) < 2:
        return CommissionerScheduleEpisodes(episodes=[])

    episodes: list[CommissionerEpisodeRequest] = []
    for anchor_index, anchor in enumerate(ordered_primary_entries):
        ordered_opponents = [entry for entry in opponent_entries if entry.policy_version_id != anchor.policy_version_id]
        if not ordered_opponents:
            continue
        if opponent_entries == ordered_primary_entries:
            neighbors = _leaderboard_neighbors(
                ordered_primary_entries,
                anchor_index,
                episodes_per_entrant,
            )
        else:
            neighbors = _repeat_to_count(ordered_opponents, episodes_per_entrant)
        for opponent in neighbors:
            episodes.append(
                CommissionerEpisodeRequest(
                    request_id=str(len(episodes)),
                    variant_id=variant_id,
                    policy_version_ids=[anchor.policy_version_id, opponent.policy_version_id],
                    tags={"pool_id": str(pool.id)},
                )
            )
    return CommissionerScheduleEpisodes(episodes=episodes)


def _leaderboard_ordered_entries(
    entries: list[PolicyPoolEntry],
    recent_results: list[Any],
) -> list[PolicyPoolEntry]:
    entries = _dedupe_entries(entries)
    entry_index = {entry.policy_version_id: index for index, entry in enumerate(entries)}
    scores: dict[Any, list[float]] = {entry.policy_version_id: [] for entry in entries}
    ranks: dict[Any, list[float]] = {entry.policy_version_id: [] for entry in entries}

    for result in recent_results:
        policy_version_id = getattr(result, "policy_version_id", None)
        if policy_version_id not in entry_index:
            continue
        scores[policy_version_id].append(float(getattr(result, "score")))
        ranks[policy_version_id].append(float(getattr(result, "rank")))

    def sort_key(entry: PolicyPoolEntry) -> tuple[int, float, float, int]:
        policy_scores = scores[entry.policy_version_id]
        if not policy_scores:
            return (1, 0.0, float("inf"), entry_index[entry.policy_version_id])
        mean_score = sum(policy_scores) / len(policy_scores)
        mean_rank = sum(ranks[entry.policy_version_id]) / max(1, len(ranks[entry.policy_version_id]))
        return (0, -mean_score, mean_rank, entry_index[entry.policy_version_id])

    return sorted(entries, key=sort_key)


def _leaderboard_neighbors(
    ordered_entries: list[PolicyPoolEntry],
    anchor_index: int,
    count: int,
) -> list[PolicyPoolEntry]:
    max_unique = len(ordered_entries) - 1
    if count <= 0 or max_unique <= 0:
        return []

    below_target = (count + 1) // 2
    above_target = count // 2
    selected: list[PolicyPoolEntry] = []
    selected.extend(_below(ordered_entries, anchor_index, start=1, count=below_target))
    selected.extend(_above(ordered_entries, anchor_index, start=1, count=above_target))

    if len(selected) < min(count, max_unique):
        selected.extend(_below(ordered_entries, anchor_index, start=below_target + 1, count=count))
    if len(selected) < min(count, max_unique):
        selected.extend(_above(ordered_entries, anchor_index, start=above_target + 1, count=count))

    return _repeat_to_count(_dedupe_entries(selected), count)


def _below(
    ordered_entries: list[PolicyPoolEntry],
    anchor_index: int,
    *,
    start: int,
    count: int,
) -> list[PolicyPoolEntry]:
    if count <= 0:
        return []
    first = anchor_index + start
    return ordered_entries[first : first + count]


def _above(
    ordered_entries: list[PolicyPoolEntry],
    anchor_index: int,
    *,
    start: int,
    count: int,
) -> list[PolicyPoolEntry]:
    if count <= 0:
        return []
    first = anchor_index - start
    last = max(-1, first - count)
    return [ordered_entries[index] for index in range(first, last, -1) if index >= 0]


def _repeat_to_count(entries: list[PolicyPoolEntry], count: int) -> list[PolicyPoolEntry]:
    if count <= 0 or not entries:
        return []
    return [entries[index % len(entries)] for index in range(count)]


def _dedupe_entries(entries: list[PolicyPoolEntry]) -> list[PolicyPoolEntry]:
    seen: set[Any] = set()
    deduped: list[PolicyPoolEntry] = []
    for entry in entries:
        if entry.policy_version_id in seen:
            continue
        seen.add(entry.policy_version_id)
        deduped.append(entry)
    return deduped
