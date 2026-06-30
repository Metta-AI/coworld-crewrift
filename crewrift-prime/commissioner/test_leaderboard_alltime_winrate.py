"""All-time episode win/played totals on the published Competition board.

Competition Win % is ``episode_wins / episodes_played`` over ALL of a player's
completed rounds. The platform only persists a SHORT recent-rounds strip
(``_RECENT_ROUNDS_FOR_DISPLAY = 20``) on each leaderboard row, so a client that
recomputes Win % from the strip alone shows 0% for anyone whose wins all fell
OUTSIDE the last 20 rounds. The fix surfaces the all-time totals
(``episode_wins`` / ``episodes_played``) directly onto each published row's
``values`` so the client can compute the true all-rounds win rate.

These tests assert, for the COMPETITION publishing paths:

* ``rank_division`` snapshots carry all-time ``episode_wins`` / ``episodes_played``.
* The round-complete published board (``_competition_win_leaderboards`` /
  ``complete_round_for_round_start``) carries those totals in each row's
  ``values`` (the dict the platform persists verbatim to leaderboard_config).
* A player who won episodes ONLY in early rounds (far outside the last-20
  recent-rounds window) still has ``episode_wins > 0`` in the published totals —
  the bug this fix exists to kill.
"""

from __future__ import annotations

import unittest
from typing import Any
from uuid import UUID, uuid4

from commissioners.common.models import (
    DivisionLeaderboardContext,
    DivisionSnapshot,
    LeaderboardRoundResultSnapshot,
    LeagueSnapshot,
    RoundSnapshot,
)
from commissioners.common.protocol import (
    MembershipInfo,
)
from commissioners.common.ruleset_strategy.config import load_ruleset_strategy_config_file
from commissioners.common.utils import (
    COMPLETED_EPISODE_COUNT_METADATA_KEY,
    RANKED_SCORE_COUNT_METADATA_KEY,
)

from crewrift_prime_skill_commissioner import (
    _WIN_HISTORY_STATE_KEY,
    CrewriftPrimeSkillCommissioner,
)
from test_observability import _CONFIG_PATH, _COMPETITION_DIV


def _commissioner() -> CrewriftPrimeSkillCommissioner:
    return CrewriftPrimeSkillCommissioner(load_ruleset_strategy_config_file(_CONFIG_PATH))


def _memberships(policies: list[tuple[UUID, str]]) -> list[MembershipInfo]:
    league_id = uuid4()
    return [
        MembershipInfo(
            id=uuid4(),
            league_id=league_id,
            division_id=_COMPETITION_DIV,
            policy_version_id=pid,
            player_id=player_id,
            status="competing",
            substatus="champion",
            is_champion=True,
        )
        for pid, player_id in policies
    ]


def _result(
    round_id: UUID,
    player_id: str,
    wins: float,
    played: int,
    *,
    ranked_score_count: int | None = None,
) -> LeaderboardRoundResultSnapshot:
    """A round result where the per-round score IS the episode-win count."""
    return LeaderboardRoundResultSnapshot(
        round_id=round_id,
        policy_version_id=uuid4(),
        rank=1,
        score=float(wins),
        player_id=player_id,
        player_name=player_id,
        result_metadata={
            RANKED_SCORE_COUNT_METADATA_KEY: played if ranked_score_count is None else ranked_score_count,
            COMPLETED_EPISODE_COUNT_METADATA_KEY: played,
        },
    )


def _ctx(completed_rounds, round_results, league_id: UUID) -> DivisionLeaderboardContext:
    return DivisionLeaderboardContext(
        league=LeagueSnapshot(id=league_id, commissioner_key="container", commissioner_config=None),
        division=DivisionSnapshot(
            id=_COMPETITION_DIV, name="Competition", level=1, league_id=league_id, type="competition"
        ),
        completed_rounds=completed_rounds,
        recent_rounds=[],
        round_results=round_results,
    )


class RankDivisionCarriesAllTimeTotalsTest(unittest.TestCase):
    def test_snapshot_carries_episode_wins_and_played(self) -> None:
        """``rank_division`` snapshots carry all-time wins/played per player."""
        commissioner = _commissioner()
        league_id = uuid4()
        r1, r2 = uuid4(), uuid4()
        # ply_a: won 2/2 then 1/2 => wins 3, played 4.
        # ply_b: won 0/2 then 2/2 => wins 2, played 4.
        round_results = [
            _result(r1, "ply_a", 2, 2),
            _result(r2, "ply_a", 1, 2),
            _result(r1, "ply_b", 0, 2),
            _result(r2, "ply_b", 2, 2),
        ]
        completed = [
            RoundSnapshot(
                id=rid,
                public_id=str(rid),
                division_id=_COMPETITION_DIV,
                round_number=n,
                status="completed",
                round_config={},
            )
            for n, rid in enumerate((r1, r2), start=1)
        ]
        snapshots = commissioner.rank_division(_ctx(completed, round_results, league_id))
        by_player = {str(s.player_id): s for s in snapshots}
        self.assertEqual(by_player["ply_a"].episode_wins, 3.0)
        self.assertEqual(by_player["ply_a"].episodes_played, 4)
        self.assertEqual(by_player["ply_b"].episode_wins, 2.0)
        self.assertEqual(by_player["ply_b"].episodes_played, 4)
        # Win rate is episode_wins / episodes_played: ply_a 0.75 outranks ply_b 0.5.
        self.assertEqual(by_player["ply_a"].rank, 1)
        self.assertEqual(by_player["ply_b"].rank, 2)


class PublishedBoardCarriesAllTimeTotalsTest(unittest.TestCase):
    def test_round_complete_rows_carry_alltime_totals(self) -> None:
        """The published board's rows carry all-time episode totals in `values`."""
        commissioner = _commissioner()
        policy_a, policy_b = uuid4(), uuid4()
        memberships = _memberships([(policy_a, "ply_a"), (policy_b, "ply_b")])
        state: Any = {"round_config": {"current_division_id": str(_COMPETITION_DIV)}}
        last_complete = None
        from test_leaderboard_flip import _round_start, _two_seat_episode

        for round_number in range(1, 9):
            rs = _round_start(memberships, round_number, state)
            episode = _two_seat_episode([policy_a, policy_b], winner_seat=0)
            last_complete = commissioner.complete_round_for_round_start(
                rs, episode_results=[episode], scheduled_episodes=[], failed_episodes=[]
            )
            state = last_complete.state

        board = last_complete.leaderboards[0]
        view = board.views[0]
        column_keys = {column.key for column in view.columns}
        # The all-time episode columns are declared on the published view.
        self.assertIn("episode_wins", column_keys)
        self.assertIn("episodes_played", column_keys)
        by_player = {row.subject_id: row for row in view.rows}
        # ply_a won 1 episode in each of its 8 rounds; played 8.
        self.assertEqual(by_player["ply_a"].values["episode_wins"], 8.0)
        self.assertEqual(by_player["ply_a"].values["episodes_played"], 8)
        # ply_a's win rate over the published totals is 1.0 (> 0).
        self.assertGreater(
            by_player["ply_a"].values["episode_wins"] / by_player["ply_a"].values["episodes_played"],
            0.0,
        )
        # ply_b never won an episode: 0 wins over 8 played.
        self.assertEqual(by_player["ply_b"].values["episode_wins"], 0.0)
        self.assertEqual(by_player["ply_b"].values["episodes_played"], 8)


class WinsOutsideRecentWindowStillCountTest(unittest.TestCase):
    """The bug this fix kills: a win older than the last-20 rounds shows as 0%.

    A player wins episodes ONLY in their earliest rounds, then plays (and loses)
    far more than 20 subsequent rounds. The recent-rounds strip the platform
    persists (<= 20 rounds) would contain NONE of the winning rounds, so a client
    recomputing Win % from the strip alone would show 0%. The all-time totals the
    commissioner now publishes must still report ``episode_wins > 0``.
    """

    def test_early_only_winner_has_nonzero_alltime_wins(self) -> None:
        commissioner = _commissioner()
        league_id = uuid4()
        round_results: list[LeaderboardRoundResultSnapshot] = []
        completed: list[RoundSnapshot] = []
        total_rounds = 30  # comfortably more than _RECENT_ROUNDS_FOR_DISPLAY (20)
        early_wins = 5  # episodes ply_early won, all in rounds 1..5
        for n in range(1, total_rounds + 1):
            rid = uuid4()
            completed.append(
                RoundSnapshot(
                    id=rid,
                    public_id=str(rid),
                    division_id=_COMPETITION_DIV,
                    round_number=n,
                    status="completed",
                    round_config={},
                )
            )
            # ply_early wins exactly 1 episode in each of the first `early_wins`
            # rounds, then 0 for the remaining (>20) rounds — every round is 1 ep.
            won = 1 if n <= early_wins else 0
            round_results.append(_result(rid, "ply_early", won, 1))
            # A foil who always wins, so the board is non-trivial.
            round_results.append(_result(rid, "ply_always", 1, 1))

        snapshots = commissioner.rank_division(_ctx(completed, round_results, league_id))
        by_player = {str(s.player_id): s for s in snapshots}
        # All-time totals reflect ALL 30 rounds, not the last 20.
        self.assertEqual(by_player["ply_early"].episode_wins, float(early_wins))
        self.assertEqual(by_player["ply_early"].episodes_played, total_rounds)
        # A nonzero, small win rate: 5 / 30 ~= 0.1667 — strictly > 0.
        rate = by_player["ply_early"].episode_wins / by_player["ply_early"].episodes_played
        self.assertGreater(rate, 0.0)
        self.assertAlmostEqual(rate, early_wins / total_rounds, places=9)


if __name__ == "__main__":
    unittest.main()
