"""Tests for the OpenSkill (Plackett-Luce) MMR ranking (mmr.py).

Ports PR Metta-AI/metta#16527's leaderboard scenarios onto the Crewrift round
shape and the per-policy-version rater: consistent-winner ordering, placement
gating, best-result-per-round dedup, player-prior inheritance, and empty board.

Requires ``openskill`` (the commissioner image installs it; run these in a venv
with ``pip install ./vendor openskill``).
"""

from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from mmr import MMR_PLACEMENT_MIN_GAMES, RatedRoundResult, rank_by_mmr


def _pid() -> UUID:
    return uuid4()


def _match(round_id: UUID, finishers: list[tuple[UUID, object | None]]) -> list[RatedRoundResult]:
    """One round's results from a finishing order (best-first).

    ``finishers`` is (policy_version_id, player_id) ordered 1st, 2nd, ...; the
    rank is the position and the score mirrors the order (1st highest) so the
    best-per-round dedup is well-defined.
    """
    n = len(finishers)
    return [
        RatedRoundResult(
            round_id=round_id,
            policy_version_id=pvid,
            player_id=player_id,
            rank=i + 1,
            score=float(n - i),
        )
        for i, (pvid, player_id) in enumerate(finishers)
    ]


class TestMmrRanking(unittest.TestCase):
    def test_empty_board(self) -> None:
        self.assertEqual(rank_by_mmr(completed_round_ids_oldest_first=[], round_results=[]).by_policy, [])
        rid = _pid()
        # Rounds but no results, or results but no completed rounds -> empty.
        self.assertEqual(rank_by_mmr(completed_round_ids_oldest_first=[rid], round_results=[]).by_policy, [])

    def test_single_policy_match_is_skipped(self) -> None:
        rid = _pid()
        a = _pid()
        results = [RatedRoundResult(round_id=rid, policy_version_id=a, player_id=_pid(), rank=1, score=1.0)]
        ranking = rank_by_mmr(completed_round_ids_oldest_first=[rid], round_results=results)
        # A 1-policy "match" carries no comparative signal -> no rating updates.
        self.assertEqual(ranking.by_policy, [])

    def test_consistent_winner_ranks_first(self) -> None:
        winner, loser = _pid(), _pid()
        wp, lp = _pid(), _pid()
        rounds = [_pid() for _ in range(MMR_PLACEMENT_MIN_GAMES)]
        results: list[RatedRoundResult] = []
        for rid in rounds:
            results += _match(rid, [(winner, wp), (loser, lp)])
        ranking = rank_by_mmr(completed_round_ids_oldest_first=rounds, round_results=results)
        by_id = {p.policy_version_id: p for p in ranking.by_policy}
        self.assertGreater(by_id[winner].mmr, by_id[loser].mmr)
        self.assertEqual(by_id[winner].rank, 1)
        self.assertEqual(by_id[winner].wins, MMR_PLACEMENT_MIN_GAMES)
        self.assertEqual(by_id[winner].losses, 0)
        self.assertEqual(by_id[loser].wins, 0)
        self.assertEqual(by_id[loser].losses, MMR_PLACEMENT_MIN_GAMES)

    def test_placement_gating(self) -> None:
        winner, loser = _pid(), _pid()
        wp, lp = _pid(), _pid()
        # Play one fewer than the placement minimum -> both still in placement.
        rounds = [_pid() for _ in range(MMR_PLACEMENT_MIN_GAMES - 1)]
        results: list[RatedRoundResult] = []
        for rid in rounds:
            results += _match(rid, [(winner, wp), (loser, lp)])
        ranking = rank_by_mmr(completed_round_ids_oldest_first=rounds, round_results=results)
        for p in ranking.by_policy:
            self.assertTrue(p.in_placement)
            self.assertIsNone(p.rank, "in-placement policies must not get a numeric rank")
            self.assertEqual(p.games_played, MMR_PLACEMENT_MIN_GAMES - 1)

    def test_best_result_per_round_dedup(self) -> None:
        winner, loser = _pid(), _pid()
        wp, lp = _pid(), _pid()
        rounds = [_pid() for _ in range(MMR_PLACEMENT_MIN_GAMES)]
        results: list[RatedRoundResult] = []
        for rid in rounds:
            results += _match(rid, [(winner, wp), (loser, lp)])
            # Add a WORSE duplicate result for the winner in the same round (lower
            # score); dedup must keep the better (rank-1) row, so winner stays 1st.
            results.append(
                RatedRoundResult(round_id=rid, policy_version_id=winner, player_id=wp, rank=2, score=0.0)
            )
        ranking = rank_by_mmr(completed_round_ids_oldest_first=rounds, round_results=results)
        by_id = {p.policy_version_id: p for p in ranking.by_policy}
        # Despite the planted worse duplicate, the winner kept its rank-1 wins.
        self.assertEqual(by_id[winner].wins, MMR_PLACEMENT_MIN_GAMES)
        self.assertGreater(by_id[winner].mmr, by_id[loser].mmr)

    def test_player_prior_inheritance(self) -> None:
        """A player's NEW policy starts from their best established mu, so its
        first rating is far above the model default (which a debutant would get).
        """
        from openskill.models import PlackettLuce

        model = PlackettLuce()
        default_mu = model.mu

        player = _pid()
        strong = _pid()
        opponents = [_pid() for _ in range(MMR_PLACEMENT_MIN_GAMES)]
        rounds = [_pid() for _ in range(MMR_PLACEMENT_MIN_GAMES)]
        results: list[RatedRoundResult] = []
        # The player's first policy wins every placement game -> clears placement
        # with mu well above default; that becomes the player's prior.
        for rid, opp in zip(rounds, opponents, strict=True):
            results += _match(rid, [(strong, player), (opp, _pid())])

        # Now the player debuts a SECOND policy in a new round vs a fresh opponent.
        debut_round = _pid()
        new_policy = _pid()
        results += _match(debut_round, [(new_policy, player), (_pid(), _pid())])

        ranking = rank_by_mmr(
            completed_round_ids_oldest_first=[*rounds, debut_round],
            round_results=results,
        )
        by_id = {p.policy_version_id: p for p in ranking.by_policy}
        # The debut policy played exactly one game; its mu reflects the inherited
        # prior (strong) plus one win, so it must exceed the model default mu.
        self.assertGreater(by_id[new_policy].mu, default_mu)
        self.assertTrue(by_id[new_policy].in_placement)

    def test_mmr_is_conservative_ordinal(self) -> None:
        a, b = _pid(), _pid()
        rounds = [_pid() for _ in range(MMR_PLACEMENT_MIN_GAMES)]
        results: list[RatedRoundResult] = []
        for rid in rounds:
            results += _match(rid, [(a, _pid()), (b, _pid())])
        ranking = rank_by_mmr(completed_round_ids_oldest_first=rounds, round_results=results)
        for p in ranking.by_policy:
            self.assertAlmostEqual(p.mmr, p.mu - 3.0 * p.sigma, places=6)


if __name__ == "__main__":
    unittest.main()
