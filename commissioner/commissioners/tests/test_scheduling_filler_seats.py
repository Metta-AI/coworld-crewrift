from __future__ import annotations

from uuid import uuid4

from commissioners.common.models import PolicyPool, PolicyPoolEntry
from commissioners.common.ruleset_strategy.config import RulesetStrategyCommissionerConfig
from commissioners.common.ruleset_strategy.scheduling import episode_entries, schedule_entries


def _config(
    *,
    seating: str = "baseline_window",
    fill_seats: str = "duplicate",
    duplicate_after_fill: bool = True,
) -> RulesetStrategyCommissionerConfig:
    return RulesetStrategyCommissionerConfig.model_validate(
        {
            "defaults": {
                "seating": seating,
                "fill_seats": fill_seats,
                "duplicate_after_fill": duplicate_after_fill,
                "stage": {"episodes": 1},
            },
            "divisions": {"main": {}},
        }
    )


def _entry(seed_order: int, pool_id) -> PolicyPoolEntry:
    return PolicyPoolEntry(pool_id=pool_id, policy_version_id=uuid4(), seed_order=seed_order)


def test_episode_entries_marks_no_fillers_when_roster_is_full() -> None:
    pool_id = uuid4()
    primary = [_entry(i, pool_id) for i in range(4)]

    seats, filler_positions = episode_entries(
        0,
        primary_entries=primary,
        filler_entries=[],
        num_agents=4,
        config=_config(),
    )

    assert len(seats) == 4
    assert filler_positions == set()


def test_episode_entries_marks_duplicate_topup_seats_as_fillers() -> None:
    pool_id = uuid4()
    primary = [_entry(0, pool_id)]  # one real entrant, game needs 4 seats

    seats, filler_positions = episode_entries(
        0,
        primary_entries=primary,
        filler_entries=[],
        num_agents=4,
        config=_config(fill_seats="duplicate"),
    )

    assert len(seats) == 4
    # Seat 0 is the real entrant; the duplicated top-up seats 1..3 are non-scoring fillers.
    assert filler_positions == {1, 2, 3}


def test_episode_entries_marks_division_fillers_then_duplicates() -> None:
    pool_id = uuid4()
    primary = [_entry(0, pool_id), _entry(1, pool_id)]
    fillers = [_entry(2, pool_id)]  # one cross-division filler available

    seats, filler_positions = episode_entries(
        0,
        primary_entries=primary,
        filler_entries=fillers,
        num_agents=4,
        config=_config(fill_seats="fill_from_divisions"),
    )

    assert len(seats) == 4
    # Seats 0,1 are real entrants; seat 2 is the division filler, seat 3 a duplicate top-up.
    assert filler_positions == {2, 3}


def test_schedule_entries_emits_filler_seats_tag() -> None:
    # The Observatory matchup-fairness grid and scoring read `filler_seats` to exclude
    # topped-up ("zombie") seats. A one-entrant division in a 4-seat game must tag
    # seats 1..3 so the fairness grid does not count a filler as a real entrant.
    pool = PolicyPool(id=uuid4())
    primary = [_entry(0, pool.id)]

    schedule = schedule_entries(
        pool=pool,
        primary_entries=primary,
        filler_entries=[],
        num_agents=4,
        variant_id="v",
        game_config=None,
        config=_config(fill_seats="duplicate"),
    )

    assert len(schedule.episodes) == 1
    episode = schedule.episodes[0]
    assert episode.tags["filler_seats"] == "1,2,3"
    assert episode.tags["pool_id"] == str(pool.id)


def test_schedule_entries_omits_filler_seats_tag_when_full() -> None:
    pool = PolicyPool(id=uuid4())
    primary = [_entry(i, pool.id) for i in range(4)]

    schedule = schedule_entries(
        pool=pool,
        primary_entries=primary,
        filler_entries=[],
        num_agents=4,
        variant_id="v",
        game_config=None,
        config=_config(),
    )

    assert len(schedule.episodes) >= 1
    assert all("filler_seats" not in episode.tags for episode in schedule.episodes)


def test_schedule_entries_rolling_window_tags_duplicate_fillers() -> None:
    # rolling_window is the seating used by proxywar/cogs_vs_clips/among_them; the same
    # duplicate-fill top-up path must tag fillers there too.
    pool = PolicyPool(id=uuid4())
    primary = [_entry(0, pool.id), _entry(1, pool.id)]

    schedule = schedule_entries(
        pool=pool,
        primary_entries=primary,
        filler_entries=[],
        num_agents=4,
        variant_id="v",
        game_config=None,
        config=_config(seating="rolling_window", fill_seats="duplicate"),
    )

    assert len(schedule.episodes) >= 1
    for episode in schedule.episodes:
        # Two real entrants + two duplicated top-up seats -> seats 2,3 are fillers.
        assert episode.tags["filler_seats"] == "2,3"
