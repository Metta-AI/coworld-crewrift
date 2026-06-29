"""Regression guard for the leaderboard-score-flip bug.

The Competition board kept flip-flopping because two platform writers persisted
two DIFFERENT boards to ``division.leaderboard_config``: the scheduling tick's
``rank_division`` and every round completion (whose ``RoundComplete`` lacked a
``leaderboards`` payload, so the platform's compatibility shim fabricated its own
board from the per-round ``results`` and overwrote the commissioner's).

The fix makes ``_complete_competition_round`` publish the SAME win-rate board
``rank_division`` computes, by aggregating the division's per-round win history
(carried in commissioner ``state``) through the shared ``_win_total_board``.
These tests assert round-complete publishes that win-rate board, that it equals
what ``rank_division`` produces over the same history, and that the history
accumulation is idempotent on a retried round-complete.
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
    EpisodeResult,
    EpisodeScore,
    LeagueInfo,
    MembershipInfo,
    RoundStart,
    VariantInfo,
)
from commissioners.common.ruleset_strategy.config import load_ruleset_strategy_config_file
from commissioners.common.utils import (
    COMPLETED_EPISODE_COUNT_METADATA_KEY,
    RANKED_SCORE_COUNT_METADATA_KEY,
)

from crewrift_prime_skill_commissioner import (
    _COMPETITION_SCORE_KIND,
    _WIN_HISTORY_STATE_KEY,
    CrewriftPrimeSkillCommissioner,
)
from test_observability import _CONFIG_PATH, _COMPETITION_DIV, _divisions


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


def _round_start(memberships: list[MembershipInfo], round_number: int, state: Any) -> RoundStart:
    return RoundStart(
        round_id=uuid4(),
        round_number=round_number,
        league=LeagueInfo(id=memberships[0].league_id, commissioner_key="container"),
        divisions=_divisions(),
        memberships=memberships,
        recent_results=[],
        variants=[VariantInfo(id="default", name="Default", game_config={})],
        state=state,
    )


def _two_seat_episode(seat_policies: list[UUID], winner_seat: int) -> EpisodeResult:
    """One 2-seat game where ``winner_seat`` wins as crew."""
    win = [i == winner_seat for i in range(len(seat_policies))]
    imposter = [0] * len(seat_policies)
    crew = [1] * len(seat_policies)
    return EpisodeResult(
        request_id=str(uuid4()),
        scores=[EpisodeScore(policy_version_id=pid, score=0.0) for pid in seat_policies],
        game_results={"win": win, "imposter": imposter, "crew": crew},
    )


def _rank_division_board(commissioner, memberships, history_rows):
    """The board ``rank_division`` produces for the same accumulated history."""
    completed_ids = [UUID(rid) for rid in dict.fromkeys(row["round_id"] for row in history_rows)]
    round_results = [
        LeaderboardRoundResultSnapshot(
            round_id=UUID(row["round_id"]),
            policy_version_id=UUID(row["policy_version_id"]),
            rank=row["rank"],
            score=row["score"],
            player_id=row["player_id"],
            player_name=None,
            result_metadata={
                COMPLETED_EPISODE_COUNT_METADATA_KEY: row.get("episodes_played", 0),
            },
        )
        for row in history_rows
    ]
    completed_rounds = [
        RoundSnapshot(
            id=rid,
            public_id=str(rid),
            division_id=_COMPETITION_DIV,
            round_number=0,
            status="completed",
            round_config={},
        )
        for rid in completed_ids
    ]
    ctx = DivisionLeaderboardContext(
        league=LeagueSnapshot(id=memberships[0].league_id, commissioner_key="container", commissioner_config=None),
        division=DivisionSnapshot(
            id=_COMPETITION_DIV, name="Competition", level=1, league_id=memberships[0].league_id, type="competition"
        ),
        completed_rounds=completed_rounds,
        recent_rounds=[],
        round_results=round_results,
    )
    return commissioner.rank_division(ctx)


class LeaderboardFlipRegressionTest(unittest.TestCase):
    def test_round_complete_publishes_win_total_leaderboards(self) -> None:
        commissioner = _commissioner()
        policy_a, policy_b = uuid4(), uuid4()
        memberships = _memberships([(policy_a, "ply_a"), (policy_b, "ply_b")])

        state: Any = {"round_config": {"current_division_id": str(_COMPETITION_DIV)}}
        last_complete = None
        for round_number in range(1, 9):
            rs = _round_start(memberships, round_number, state)
            # policy_a wins every round; policy_b loses every round.
            episode = _two_seat_episode([policy_a, policy_b], winner_seat=0)
            last_complete = commissioner.complete_round_for_round_start(
                rs, episode_results=[episode], scheduled_episodes=[], failed_episodes=[]
            )
            state = last_complete.state

        # The round-complete response carries an explicit win-rate leaderboard
        # so the platform never synthesizes its own competing board.
        self.assertEqual(len(last_complete.leaderboards), 1)
        board = last_complete.leaderboards[0]
        self.assertEqual(board.division_id, _COMPETITION_DIV)
        rows = board.views[0].rows
        # Both players appear, keyed by player id.
        self.assertEqual({row.subject_id for row in rows}, {"ply_a", "ply_b"})
        by_player = {row.subject_id: row for row in rows}
        # The consistent winner is rank 1; its score is the all-time WIN RATE (won
        # all 8 of the 8 episodes it played => 1.0), and the loser scored 0.0.
        self.assertEqual(by_player["ply_a"].values["rank"], 1)
        self.assertEqual(by_player["ply_a"].values["score"], 1.0)
        self.assertEqual(by_player["ply_b"].values["score"], 0.0)
        # rounds_played is tracked.
        self.assertEqual(by_player["ply_a"].values["rounds_played"], 8)

        # The per-round results carry the win-count score kind.
        self.assertEqual(
            last_complete.results[0].rankings[0].result_metadata["score_kind"], _COMPETITION_SCORE_KIND
        )

    def test_round_complete_board_matches_rank_division(self) -> None:
        commissioner = _commissioner()
        policy_a, policy_b, policy_c = uuid4(), uuid4(), uuid4()
        memberships = _memberships([(policy_a, "ply_a"), (policy_b, "ply_b"), (policy_c, "ply_c")])

        state: Any = {"round_config": {"current_division_id": str(_COMPETITION_DIV)}}
        last_complete = None
        for round_number in range(1, 9):
            rs = _round_start(memberships, round_number, state)
            seats = [policy_a, policy_b, policy_c]
            # Rotate the winner so the ranking is non-trivial.
            episode = _two_seat_episode(seats, winner_seat=round_number % 3)
            last_complete = commissioner.complete_round_for_round_start(
                rs, episode_results=[episode], scheduled_episodes=[], failed_episodes=[]
            )
            state = last_complete.state

        history = state[_WIN_HISTORY_STATE_KEY]
        published = last_complete.leaderboards[0].views[0].rows

        # rank_division over the SAME history must yield the SAME ordering + scores.
        rank_div_snapshots = _rank_division_board(commissioner, memberships, history)
        self.assertEqual(
            [row.subject_id for row in published],
            [str(s.player_id) for s in rank_div_snapshots],
        )
        for row, snap in zip(published, rank_div_snapshots, strict=True):
            self.assertEqual(row.values["rank"], snap.rank)
            self.assertAlmostEqual(float(row.values["score"]), snap.score, places=9)
            self.assertEqual(row.values["rounds_played"], snap.rounds_played)

    def test_history_accumulation_is_idempotent(self) -> None:
        commissioner = _commissioner()
        policy_a, policy_b = uuid4(), uuid4()
        memberships = _memberships([(policy_a, "ply_a"), (policy_b, "ply_b")])
        state: Any = {"round_config": {"current_division_id": str(_COMPETITION_DIV)}}
        rs = _round_start(memberships, 1, state)
        episode = _two_seat_episode([policy_a, policy_b], winner_seat=0)

        first = commissioner.complete_round_for_round_start(
            rs, episode_results=[episode], scheduled_episodes=[], failed_episodes=[]
        )
        history_after_first = list(first.state[_WIN_HISTORY_STATE_KEY])

        # Re-run the SAME round (same round_id via the same RoundStart) with the
        # already-updated state: a retried round-complete must not double-count.
        retried = commissioner.complete_round_for_round_start(
            rs, episode_results=[episode], scheduled_episodes=[], failed_episodes=[]
        )
        self.assertEqual(retried.state[_WIN_HISTORY_STATE_KEY], history_after_first)


class EveryParticipantVisibleTest(unittest.TestCase):
    """No active participant is ever dropped from the standings.

    Regression guard for the "6 active players, only 5 rows" bug: a player whose
    only round results were tainted/unranked (``ranked_score_count <= 0``) must
    still appear on the board with a 0 win rate, not vanish.
    """

    def test_rank_division_includes_tainted_only_player(self) -> None:
        commissioner = _commissioner()
        winner, taint_only = uuid4(), uuid4()
        memberships = _memberships([(winner, "ply_win"), (taint_only, "ply_taint")])
        round_id = uuid4()
        round_results = [
            LeaderboardRoundResultSnapshot(
                round_id=round_id,
                policy_version_id=winner,
                rank=1,
                score=2.0,
                player_id="ply_win",
                player_name="Winner",
                result_metadata={
                    RANKED_SCORE_COUNT_METADATA_KEY: 4,
                    COMPLETED_EPISODE_COUNT_METADATA_KEY: 4,
                },
            ),
            # ply_taint only ever has a tainted row (ranked_score_count <= 0).
            LeaderboardRoundResultSnapshot(
                round_id=round_id,
                policy_version_id=taint_only,
                rank=2,
                score=-100.0,
                player_id="ply_taint",
                player_name="Tainted",
                result_metadata={
                    RANKED_SCORE_COUNT_METADATA_KEY: 0,
                    COMPLETED_EPISODE_COUNT_METADATA_KEY: 0,
                },
            ),
        ]
        ctx = DivisionLeaderboardContext(
            league=LeagueSnapshot(
                id=memberships[0].league_id, commissioner_key="container", commissioner_config=None
            ),
            division=DivisionSnapshot(
                id=_COMPETITION_DIV,
                name="Competition",
                level=1,
                league_id=memberships[0].league_id,
                type="competition",
            ),
            completed_rounds=[
                RoundSnapshot(
                    id=round_id,
                    public_id=str(round_id),
                    division_id=_COMPETITION_DIV,
                    round_number=1,
                    status="completed",
                    round_config={},
                )
            ],
            recent_rounds=[],
            round_results=round_results,
        )
        snapshots = commissioner.rank_division(ctx)
        by_player = {str(s.player_id): s for s in snapshots}
        # BOTH players are shown — the tainted-only player is not dropped.
        self.assertEqual(set(by_player), {"ply_win", "ply_taint"})
        # The tainted-only player has a 0 win rate and 0 rounds played.
        self.assertEqual(by_player["ply_taint"].score, 0.0)
        self.assertEqual(by_player["ply_taint"].rounds_played, 0)
        # The real winner ranks first with a positive win rate.
        self.assertEqual(by_player["ply_win"].rank, 1)
        self.assertGreater(by_player["ply_win"].score, 0.0)


if __name__ == "__main__":
    unittest.main()
