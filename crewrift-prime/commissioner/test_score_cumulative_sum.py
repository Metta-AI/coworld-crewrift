"""Leaderboard ``score`` = floored cumulative sum of per-round role-weighted points.

The displayed leaderboard ``score`` column is the ABSOLUTE CUMULATIVE SUM of
a player's per-round role-weighted points (3 per imposter win, 1 per crew win)
across ALL completed rounds, floored at 0 (never negative). The win-rate metric
and the win-rate RANKING for the Competition division are unchanged.

These tests assert, for BOTH publishing paths:

* Competition (``CrewriftPrimeSkillCommissioner.rank_division`` over a competition
  division) publishes ``score = max(0, sum of per-round points)``, that the
  RANKING still follows descending win rate (so the cumulative score does NOT
  change the order), and that a raw-negative total floors to 0.
* The stock/non-competition path (``rank_division`` deferring to
  ``BaselineCommissioner``) emits the same floored cumulative sum.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from commissioners.common.models import (
    DivisionLeaderboardContext,
    DivisionSnapshot,
    LeaderboardRoundResultSnapshot,
    LeagueSnapshot,
    RoundSnapshot,
)
from commissioners.common.utils import (
    COMPLETED_EPISODE_COUNT_METADATA_KEY,
    MEAN_ROUND_SCORE_KIND,
    RANKED_SCORE_COUNT_METADATA_KEY,
)
from commissioners.common.ruleset_strategy.config import load_ruleset_strategy_config_file

from crewrift_prime_skill_commissioner import CrewriftPrimeSkillCommissioner
from test_observability import _CONFIG_PATH, _COMPETITION_DIV


def _commissioner() -> CrewriftPrimeSkillCommissioner:
    return CrewriftPrimeSkillCommissioner(load_ruleset_strategy_config_file(_CONFIG_PATH))


def _round(
    round_id: UUID,
    round_number: int,
    completed_at,
) -> RoundSnapshot:
    return RoundSnapshot(
        id=round_id,
        public_id=str(round_id),
        division_id=_COMPETITION_DIV,
        round_number=round_number,
        status="completed",
        round_config={},
        completed_at=completed_at,
    )


def _result(
    round_id: UUID,
    player_id: str,
    score: float,
    *,
    played: int = 1,
    ranked_score_count: int = 1,
    policy_version_id: UUID | None = None,
    score_kind: str | None = None,
    episode_wins: float | None = None,
    points: float | None = None,
) -> LeaderboardRoundResultSnapshot:
    metadata = {
        RANKED_SCORE_COUNT_METADATA_KEY: ranked_score_count,
        COMPLETED_EPISODE_COUNT_METADATA_KEY: played,
    }
    if score_kind is not None:
        metadata["score_kind"] = score_kind
    if episode_wins is not None:
        metadata["episode_wins"] = episode_wins
    if points is not None:
        metadata["points"] = points
    return LeaderboardRoundResultSnapshot(
        round_id=round_id,
        policy_version_id=policy_version_id or uuid4(),
        rank=1,
        score=score,
        player_id=player_id,
        player_name=player_id,
        result_metadata=metadata,
    )


def _ctx(division_type: str, completed_rounds, round_results) -> DivisionLeaderboardContext:
    league_id = uuid4()
    return DivisionLeaderboardContext(
        league=LeagueSnapshot(id=league_id, commissioner_key="container", commissioner_config=None),
        division=DivisionSnapshot(
            id=_COMPETITION_DIV,
            name="Competition" if division_type == "competition" else "Practice",
            level=1,
            league_id=league_id,
            type=division_type,
        ),
        completed_rounds=completed_rounds,
        recent_rounds=[],
        round_results=round_results,
    )


class CompetitionScoreIsFlooredCumulativeSumTest(unittest.TestCase):
    def test_score_is_cumulative_sum_ranking_stays_win_rate(self) -> None:
        """Competition ``score`` = cumulative per-round win sum; rank = win rate.

        Two players over two rounds. ``ply_low`` wins MORE episodes in total but
        plays many more, so its win RATE is lower; ``ply_hi`` wins fewer episodes
        but at a higher rate. Ranking must follow the win rate (``ply_hi`` first),
        while the displayed score is each player's cumulative won-episode sum
        (``ply_low`` higher). This proves the cumulative score does NOT drive the
        order.
        """
        commissioner = _commissioner()
        r1, r2 = uuid4(), uuid4()
        # ply_hi: round1 won 2/2, round2 won 1/2 => wins 3, played 4, rate 0.75, sum 3.
        # ply_low: round1 won 2/8, round2 won 2/8 => wins 4, played 16, rate 0.25, sum 4.
        round_results = [
            _result(r1, "ply_hi", 2.0, played=2),
            _result(r2, "ply_hi", 1.0, played=2),
            _result(r1, "ply_low", 2.0, played=8),
            _result(r2, "ply_low", 2.0, played=8),
        ]
        completed = [_round(r1, 1, None), _round(r2, 2, None)]
        snapshots = commissioner.rank_division(_ctx("competition", completed, round_results))
        by_player = {str(s.player_id): s for s in snapshots}

        # SCORE = floored cumulative sum of per-round points (here 1 pt/win legacy).
        self.assertEqual(by_player["ply_hi"].score, 3.0)
        self.assertEqual(by_player["ply_low"].score, 4.0)
        # RANKING is by WIN RATE: ply_hi (0.75) outranks ply_low (0.25) even though
        # ply_low's cumulative SCORE is higher.
        self.assertEqual(by_player["ply_hi"].rank, 1)
        self.assertEqual(by_player["ply_low"].rank, 2)
        self.assertLess(by_player["ply_hi"].score, by_player["ply_low"].score)

    def test_negative_cumulative_total_floors_to_zero(self) -> None:
        """A player whose raw per-round score sum is negative floors to 0.0.

        Per-round win scores are normally non-negative, but the floor must hold
        regardless: a player whose summed per-round scores are negative shows 0.0.
        """
        commissioner = _commissioner()
        r1, r2 = uuid4(), uuid4()
        round_results = [
            _result(r1, "ply_neg", -3.0, played=2),
            _result(r2, "ply_neg", -1.5, played=2),
            _result(r1, "ply_pos", 1.0, played=2),
        ]
        completed = [_round(r1, 1, None), _round(r2, 2, None)]
        snapshots = commissioner.rank_division(_ctx("competition", completed, round_results))
        by_player = {str(s.player_id): s for s in snapshots}
        # Raw sum -4.5 -> floored at 0.0.
        self.assertEqual(by_player["ply_neg"].score, 0.0)
        self.assertEqual(by_player["ply_pos"].score, 1.0)

    def test_tainted_rounds_excluded_from_sum(self) -> None:
        """Tainted rounds (ranked_score_count <= 0) are excluded from the sum.

        The cumulative sum is over the SAME per-round scores the win-rate path
        counts; tainted/unranked rounds (the -100 lobby taint) never contribute.
        """
        commissioner = _commissioner()
        r1, r2 = uuid4(), uuid4()
        round_results = [
            _result(r1, "ply", 2.0, played=2, ranked_score_count=2),
            # Tainted: a -100 lobby-taint row must NOT drag the cumulative sum down.
            _result(r2, "ply", -100.0, played=0, ranked_score_count=0),
        ]
        completed = [_round(r1, 1, None), _round(r2, 2, None)]
        snapshots = commissioner.rank_division(_ctx("competition", completed, round_results))
        by_player = {str(s.player_id): s for s in snapshots}
        # Only the ranked round (2.0) counts; the tainted row is skipped.
        self.assertEqual(by_player["ply"].score, 2.0)

    def test_score_sums_role_weighted_points_not_episode_wins(self) -> None:
        """Standings score aggregates role-weighted points; win rate uses episode wins."""
        commissioner = _commissioner()
        r1, r2 = uuid4(), uuid4()
        # ply_imp: 2 imposter wins in r1 -> 6 pts, 1 crew win in r2 -> 1 pt => 7 pts, 3 wins.
        # ply_crew: 3 crew wins across both rounds -> 3 pts, 3 wins (same win rate).
        round_results = [
            _result(r1, "ply_imp", 6.0, played=4, episode_wins=2.0, points=6.0),
            _result(r2, "ply_imp", 1.0, played=4, episode_wins=1.0, points=1.0),
            _result(r1, "ply_crew", 2.0, played=4, episode_wins=2.0, points=2.0),
            _result(r2, "ply_crew", 1.0, played=4, episode_wins=1.0, points=1.0),
        ]
        completed = [_round(r1, 1, None), _round(r2, 2, None)]
        snapshots = commissioner.rank_division(_ctx("competition", completed, round_results))
        by_player = {str(s.player_id): s for s in snapshots}

        self.assertEqual(by_player["ply_imp"].score, 7.0)
        self.assertEqual(by_player["ply_crew"].score, 3.0)
        self.assertEqual(by_player["ply_imp"].episode_wins, 3.0)
        self.assertEqual(by_player["ply_crew"].episode_wins, 3.0)
        # Same win rate; imposter-heavy player has higher cumulative points.
        self.assertGreater(by_player["ply_imp"].score, by_player["ply_crew"].score)


class NonCompetitionScoreIsFlooredCumulativeSumTest(unittest.TestCase):
    def test_stock_path_emits_floored_cumulative_sum(self) -> None:
        """Non-competition divisions defer to the stock path, which now emits the
        floored cumulative sum (not the decayed-mean EWMA)."""
        commissioner = _commissioner()
        r1, r2 = uuid4(), uuid4()
        # Same player across two rounds: scores 4.0 and 2.5 -> cumulative 6.5
        # (the EWMA decayed MEAN would have been ~3.25, not 6.5).
        pid = uuid4()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        round_results = [
            _result(r1, "ply_a", 4.0, policy_version_id=pid, score_kind=MEAN_ROUND_SCORE_KIND),
            _result(r2, "ply_a", 2.5, policy_version_id=pid, score_kind=MEAN_ROUND_SCORE_KIND),
            _result(r1, "ply_b", -5.0, score_kind=MEAN_ROUND_SCORE_KIND),
        ]
        # The stock path takes completed_rounds[0] as the latest; order newest-first
        # and give each round a real completed_at (the path asserts it is set).
        completed = [
            _round(r2, 2, now),
            _round(r1, 1, now - timedelta(hours=1)),
        ]
        snapshots = commissioner.rank_division(_ctx("practice", completed, round_results))
        by_player = {str(s.player_id): s for s in snapshots}
        # Cumulative sum, NOT a decayed mean.
        self.assertAlmostEqual(by_player["ply_a"].score, 6.5, places=9)
        # Negative raw total floors to 0.
        self.assertEqual(by_player["ply_b"].score, 0.0)
        # Ranking is by the (floored) cumulative score for non-competition: ply_a
        # (6.5) ranks above ply_b (0.0).
        self.assertEqual(by_player["ply_a"].rank, 1)
        self.assertEqual(by_player["ply_b"].rank, 2)


if __name__ == "__main__":
    unittest.main()
