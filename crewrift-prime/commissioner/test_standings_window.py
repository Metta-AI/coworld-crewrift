"""Standings recency window: only recent gameplay counts toward the main board.

The Competition "Standings" board can OPTIONALLY grade players on RECENT merit —
when enabled (``CREWRIFT_PRIME_STANDINGS_WINDOW_HOURS`` > 0) only rounds whose
gameplay completed within the last N hours count toward the win rate and
cumulative score. It is OFF by default (all-time board). These tests assert, for
BOTH publishing paths that share ``_win_total_board``:

* ``rank_division`` drops rounds older than the window (by ``completed_at`` /
  ``started_at`` / ``created_at``) and keeps rounds inside it.
* The round-complete board (``_competition_win_leaderboards``) applies the SAME
  window over its persisted per-round win history via the ``recorded_at`` stamp,
  so the two writers stay in lockstep.
* The window is configurable and reverts to all-time when set to 0.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import crewrift_prime_skill_commissioner as comm
from commissioners.common.models import (
    DivisionLeaderboardContext,
    DivisionSnapshot,
    LeaderboardRoundResultSnapshot,
    LeagueSnapshot,
    RoundSnapshot,
)
from commissioners.common.ruleset_strategy.config import load_ruleset_strategy_config_file
from commissioners.common.utils import (
    COMPLETED_EPISODE_COUNT_METADATA_KEY,
    RANKED_SCORE_COUNT_METADATA_KEY,
)

from crewrift_prime_skill_commissioner import (
    _WIN_HISTORY_RECORDED_AT_KEY,
    _WIN_HISTORY_STATE_KEY,
    CrewriftPrimeSkillCommissioner,
)
from test_observability import _CONFIG_PATH, _COMPETITION_DIV

UTC = timezone.utc


def _commissioner() -> CrewriftPrimeSkillCommissioner:
    return CrewriftPrimeSkillCommissioner(load_ruleset_strategy_config_file(_CONFIG_PATH))


def _round(round_id: UUID, round_number: int, completed_at: datetime | None) -> RoundSnapshot:
    return RoundSnapshot(
        id=round_id,
        public_id=str(round_id),
        division_id=_COMPETITION_DIV,
        round_number=round_number,
        status="completed",
        round_config={},
        completed_at=completed_at,
    )


def _result(round_id: UUID, player_id: str, wins: float, played: int) -> LeaderboardRoundResultSnapshot:
    return LeaderboardRoundResultSnapshot(
        round_id=round_id,
        policy_version_id=uuid4(),
        rank=1,
        score=float(wins),
        player_id=player_id,
        player_name=player_id,
        result_metadata={
            RANKED_SCORE_COUNT_METADATA_KEY: played,
            COMPLETED_EPISODE_COUNT_METADATA_KEY: played,
        },
    )


def _ctx(completed_rounds, round_results) -> DivisionLeaderboardContext:
    league_id = uuid4()
    return DivisionLeaderboardContext(
        league=LeagueSnapshot(id=league_id, commissioner_key="container", commissioner_config=None),
        division=DivisionSnapshot(
            id=_COMPETITION_DIV, name="Competition", level=1, league_id=league_id, type="competition"
        ),
        completed_rounds=completed_rounds,
        recent_rounds=[],
        round_results=round_results,
    )


class RankDivisionWindowTest(unittest.TestCase):
    def test_default_window_is_disabled(self) -> None:
        """The recency window is OFF by default (all-time board)."""
        self.assertEqual(comm.STANDINGS_WINDOW_HOURS, 0.0)

    def test_stale_rounds_are_dropped_from_standings(self) -> None:
        """A player whose only wins are older than the window drops to a 0 win rate."""
        commissioner = _commissioner()
        now = datetime.now(UTC)
        recent_round, stale_round = uuid4(), uuid4()
        # ply_recent won 1/1 in a round 1h ago (in-window).
        # ply_stale won 1/1 in a round 10h ago (outside a 6h window).
        round_results = [
            _result(recent_round, "ply_recent", 1, 1),
            _result(stale_round, "ply_stale", 1, 1),
        ]
        completed = [
            _round(recent_round, 2, now - timedelta(hours=1)),
            _round(stale_round, 1, now - timedelta(hours=10)),
        ]
        original = comm.STANDINGS_WINDOW_HOURS
        try:
            comm.STANDINGS_WINDOW_HOURS = 6.0
            snapshots = commissioner.rank_division(_ctx(completed, round_results))
        finally:
            comm.STANDINGS_WINDOW_HOURS = original
        by_player = {str(s.player_id): s for s in snapshots}
        # Only the recent round's player appears; the stale-only player is dropped
        # (its round is outside the window, so it has no in-window participation).
        self.assertIn("ply_recent", by_player)
        self.assertNotIn("ply_stale", by_player)
        self.assertEqual(by_player["ply_recent"].episode_wins, 1.0)
        self.assertEqual(by_player["ply_recent"].episodes_played, 1)

    def test_only_recent_rounds_feed_win_rate(self) -> None:
        """A single player's stale wins do not inflate the recent win rate."""
        commissioner = _commissioner()
        now = datetime.now(UTC)
        stale, recent = uuid4(), uuid4()
        # Same player: won 5/5 long ago, then 1/5 recently. Only the recent round
        # counts -> win rate 1/5 = 0.2, not (5+1)/(5+5).
        round_results = [
            _result(stale, "ply", 5, 5),
            _result(recent, "ply", 1, 5),
        ]
        completed = [
            _round(stale, 1, now - timedelta(hours=8)),
            _round(recent, 2, now - timedelta(hours=2)),
        ]
        original = comm.STANDINGS_WINDOW_HOURS
        try:
            comm.STANDINGS_WINDOW_HOURS = 6.0
            snapshots = commissioner.rank_division(_ctx(completed, round_results))
        finally:
            comm.STANDINGS_WINDOW_HOURS = original
        by_player = {str(s.player_id): s for s in snapshots}
        self.assertEqual(by_player["ply"].episode_wins, 1.0)
        self.assertEqual(by_player["ply"].episodes_played, 5)
        self.assertEqual(by_player["ply"].rounds_played, 1)

    def test_window_disabled_by_default_keeps_all_rounds(self) -> None:
        """With no env override the board is all-time (the default)."""
        commissioner = _commissioner()
        now = datetime.now(UTC)
        stale, recent = uuid4(), uuid4()
        round_results = [
            _result(stale, "ply", 5, 5),
            _result(recent, "ply", 1, 5),
        ]
        completed = [
            _round(stale, 1, now - timedelta(hours=48)),
            _round(recent, 2, now - timedelta(hours=1)),
        ]
        # STANDINGS_WINDOW_HOURS defaults to 0 (disabled) — do not override it.
        self.assertEqual(comm.STANDINGS_WINDOW_HOURS, 0.0)
        snapshots = commissioner.rank_division(_ctx(completed, round_results))
        by_player = {str(s.player_id): s for s in snapshots}
        # All-time: both rounds count -> 6 wins over 10 played.
        self.assertEqual(by_player["ply"].episode_wins, 6.0)
        self.assertEqual(by_player["ply"].episodes_played, 10)
        self.assertEqual(by_player["ply"].rounds_played, 2)

    def test_window_falls_back_to_latest_round_when_all_stale(self) -> None:
        """An enabled window that would drop every round keeps the latest round,
        never an empty board (which would let the platform fabricate a flipping
        single-round board)."""
        commissioner = _commissioner()
        now = datetime.now(UTC)
        older, newest = uuid4(), uuid4()
        round_results = [
            _result(older, "ply", 2, 4),
            _result(newest, "ply", 3, 5),
        ]
        completed = [
            _round(older, 1, now - timedelta(hours=12)),
            _round(newest, 2, now - timedelta(hours=8)),  # still outside 6h
        ]
        original = comm.STANDINGS_WINDOW_HOURS
        try:
            comm.STANDINGS_WINDOW_HOURS = 6.0
            snapshots = commissioner.rank_division(_ctx(completed, round_results))
        finally:
            comm.STANDINGS_WINDOW_HOURS = original
        by_player = {str(s.player_id): s for s in snapshots}
        # Non-empty board: the single most-recent round survives the fallback.
        self.assertIn("ply", by_player)
        self.assertEqual(by_player["ply"].episode_wins, 3.0)
        self.assertEqual(by_player["ply"].episodes_played, 5)
        self.assertEqual(by_player["ply"].rounds_played, 1)

    def test_missing_timestamps_are_kept(self) -> None:
        """A round with no resolvable timestamp is never silently dropped.

        RoundSnapshot defaults ``created_at`` to now, so a round with all
        timestamps effectively 'now' stays in-window; this guards the fallback.
        """
        commissioner = _commissioner()
        rid = uuid4()
        round_results = [_result(rid, "ply", 1, 1)]
        completed = [_round(rid, 1, None)]  # completed_at None -> created_at ~ now
        snapshots = commissioner.rank_division(_ctx(completed, round_results))
        by_player = {str(s.player_id): s for s in snapshots}
        self.assertIn("ply", by_player)
        self.assertEqual(by_player["ply"].episode_wins, 1.0)


class RoundCompleteWindowTest(unittest.TestCase):
    """The round-complete board applies the SAME window via ``recorded_at``."""

    def test_stale_history_rows_excluded_from_published_board(self) -> None:
        commissioner = _commissioner()
        now = datetime.now(UTC)        # Seed persisted history: a stale row (10h ago) and a recent row (1h ago)
        # for two different players; then complete a fresh round.
        stale_round = str(uuid4())
        recent_round = str(uuid4())
        state = {
            "round_config": {"current_division_id": str(_COMPETITION_DIV)},
            _WIN_HISTORY_STATE_KEY: [
                {
                    "round_id": stale_round,
                    "policy_version_id": str(uuid4()),
                    "player_id": "ply_stale",
                    "rank": 1,
                    "score": 3.0,
                    "episodes_played": 3,
                    "tainted": False,
                    _WIN_HISTORY_RECORDED_AT_KEY: (now - timedelta(hours=10)).isoformat(),
                },
                {
                    "round_id": recent_round,
                    "policy_version_id": str(uuid4()),
                    "player_id": "ply_recent",
                    "rank": 1,
                    "score": 1.0,
                    "episodes_played": 2,
                    "tainted": False,
                    _WIN_HISTORY_RECORDED_AT_KEY: (now - timedelta(hours=1)).isoformat(),
                },
            ],
        }
        from commissioners.common.protocol import RankingEntry as CommissionerRankingEntry

        rankings = [
            CommissionerRankingEntry(
                policy_version_id=uuid4(),
                player_id="ply_now",
                rank=1,
                score=2.0,
                result_metadata={
                    RANKED_SCORE_COUNT_METADATA_KEY: 2,
                    COMPLETED_EPISODE_COUNT_METADATA_KEY: 2,
                },
            )
        ]
        original = comm.STANDINGS_WINDOW_HOURS
        try:
            comm.STANDINGS_WINDOW_HOURS = 6.0
            leaderboards, next_state = commissioner._competition_win_leaderboards(
                incoming_state=state,
                division_id=_COMPETITION_DIV,
                round_id=uuid4(),
                rankings=rankings,
            )
        finally:
            comm.STANDINGS_WINDOW_HOURS = original
        rows = leaderboards[0].views[0].rows
        subjects = {row.subject_id for row in rows}
        # The stale player is excluded from the published board; the recent and
        # freshly-scored players remain.
        self.assertNotIn("ply_stale", subjects)
        self.assertIn("ply_recent", subjects)
        self.assertIn("ply_now", subjects)
        # State still retains the full append-only history (the window is applied to
        # the PUBLISHED board only, not by pruning persisted state).
        history_players = {row["player_id"] for row in next_state[_WIN_HISTORY_STATE_KEY]}
        self.assertIn("ply_stale", history_players)


if __name__ == "__main__":
    unittest.main()
