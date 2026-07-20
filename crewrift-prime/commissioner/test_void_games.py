"""Void/disconnected games are excluded from the Competition win rate.

Many Competition episodes disconnect mid-game; in those broken episodes the game
emits no winner and EVERY player policy scores 0. Counting them as "played" only
dilutes players' win rate (episodes won / episodes played) through no fault of
their own, so ``_complete_competition_round`` drops an episode in which no real
(non-filler) player seat won from BOTH the win-rate numerator (wins) AND
denominator (episodes played).

These tests assert, for BOTH publishing paths that share ``_win_total_board``:

* an all-zero (void) episode is excluded from both wins and episodes played;
* a normal (won) episode still counts;
* an episode won only by FILLER seats is still void for the real players;
* the round-complete board and ``rank_division`` agree after the exclusion;
* the pure ``episode_is_void`` detector and the ``EXCLUDE_VOID_GAMES`` toggle.
"""

from __future__ import annotations

import unittest
from typing import Any
from uuid import UUID, uuid4

from commissioners.common.protocol import (
    EpisodeRequest,
    EpisodeResult,
    EpisodeScore,
    LeagueInfo,
    MembershipInfo,
    RoundStart,
    VariantInfo,
)
from commissioners.common.ruleset_strategy.config import load_ruleset_strategy_config_file
from commissioners.common.utils import COMPLETED_EPISODE_COUNT_METADATA_KEY

import decision
from crewrift_prime_skill_commissioner import (
    _FILLER_SEATS_TAG,
    CrewriftPrimeSkillCommissioner,
)
from decision import episode_is_void
from test_leaderboard_flip import _rank_division_board, _round_result_snapshots, _round_snapshots
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


def _episode(seat_policies: list[UUID], win: list[int], request_id: str | None = None) -> EpisodeResult:
    """One episode with an explicit per-seat ``win`` array (all-crew roles)."""
    return EpisodeResult(
        request_id=request_id or str(uuid4()),
        scores=[EpisodeScore(policy_version_id=pid, score=0.0) for pid in seat_policies],
        game_results={
            "win": win,
            "imposter": [0] * len(seat_policies),
            "crew": [1] * len(seat_policies),
        },
    )


class EpisodeIsVoidTest(unittest.TestCase):
    """The pure detector: void iff no player seat won."""

    def test_all_zero_is_void(self) -> None:
        gr = {"win": [0, 0, 0]}
        self.assertTrue(episode_is_void(gr, [0, 1, 2]))

    def test_any_player_win_is_not_void(self) -> None:
        gr = {"win": [0, 1, 0]}
        self.assertFalse(episode_is_void(gr, [0, 1, 2]))

    def test_only_filler_seat_won_is_void_for_players(self) -> None:
        # Seat 2 won, but seat 2 is a filler (not in the player_seats set), so no
        # REAL player won -> the episode is void for the players.
        gr = {"win": [0, 0, 1]}
        self.assertTrue(episode_is_void(gr, [0, 1]))

    def test_no_player_seats_is_void(self) -> None:
        gr = {"win": [1, 1]}
        self.assertTrue(episode_is_void(gr, []))


class VoidGameExclusionTest(unittest.TestCase):
    def test_void_episode_excluded_from_numerator_and_denominator(self) -> None:
        """A round with one won and one all-zero episode counts only the won one."""
        commissioner = _commissioner()
        policy_a, policy_b = uuid4(), uuid4()
        memberships = _memberships([(policy_a, "ply_a"), (policy_b, "ply_b")])
        state: Any = {"round_config": {"current_division_id": str(_COMPETITION_DIV)}}
        rs = _round_start(memberships, 1, state)

        seats = [policy_a, policy_b]
        won = _episode(seats, win=[1, 0])  # policy_a wins this episode
        void = _episode(seats, win=[0, 0])  # disconnected: nobody won

        complete = commissioner.complete_round_for_round_start(
            rs, episode_results=[won, void], scheduled_episodes=[], failed_episodes=[]
        )

        # Only ONE game is scored (the void one is dropped).
        by_pid = {r.policy_version_id: r for r in complete.results[0].rankings}
        rec_a = by_pid[policy_a].result_metadata
        rec_b = by_pid[policy_b].result_metadata
        # episodes_played (the denominator) excludes the void episode.
        self.assertEqual(rec_a[COMPLETED_EPISODE_COUNT_METADATA_KEY], 1)
        self.assertEqual(rec_b[COMPLETED_EPISODE_COUNT_METADATA_KEY], 1)
        # policy_a won its 1 counted episode; policy_b won 0.
        self.assertEqual(rec_a["episode_wins"], 1)
        self.assertEqual(rec_b["episode_wins"], 0)

        # The published board reflects wins/played over the non-void episode only:
        # ply_a 1/1 = 100%, ply_b 0/1 = 0%.
        by_player = {row.subject_id: row for row in complete.leaderboards[0].views[0].rows}
        self.assertEqual(by_player["ply_a"].values["episodes_played"], 1)
        self.assertEqual(by_player["ply_a"].values["wins"], 1.0)
        self.assertAlmostEqual(float(by_player["ply_a"].values["win_rate"]), 1.0, places=9)
        self.assertEqual(by_player["ply_b"].values["episodes_played"], 1)
        self.assertAlmostEqual(float(by_player["ply_b"].values["win_rate"]), 0.0, places=9)

    def test_all_episodes_void_yields_zero_played(self) -> None:
        """A fully-disconnected round counts 0 played for everyone (not a loss)."""
        commissioner = _commissioner()
        policy_a, policy_b = uuid4(), uuid4()
        memberships = _memberships([(policy_a, "ply_a"), (policy_b, "ply_b")])
        state: Any = {"round_config": {"current_division_id": str(_COMPETITION_DIV)}}
        rs = _round_start(memberships, 1, state)
        seats = [policy_a, policy_b]
        void1 = _episode(seats, win=[0, 0])
        void2 = _episode(seats, win=[0, 0])

        complete = commissioner.complete_round_for_round_start(
            rs, episode_results=[void1, void2], scheduled_episodes=[], failed_episodes=[]
        )
        by_pid = {r.policy_version_id: r for r in complete.results[0].rankings}
        for pid in (policy_a, policy_b):
            self.assertEqual(by_pid[pid].result_metadata[COMPLETED_EPISODE_COUNT_METADATA_KEY], 0)
            self.assertEqual(by_pid[pid].result_metadata["episode_wins"], 0)
        # Both players still appear on the board (never dropped), each 0/0 -> 0%.
        by_player = {row.subject_id: row for row in complete.leaderboards[0].views[0].rows}
        for pid in ("ply_a", "ply_b"):
            self.assertEqual(by_player[pid].values["episodes_played"], 0)
            self.assertAlmostEqual(float(by_player[pid].values["win_rate"]), 0.0, places=9)

    def test_episode_won_only_by_filler_is_void_for_players(self) -> None:
        """An episode where only a FILLER seat won is void for the real players.

        Seat 2 is a filler; only seat 2's ``win`` is True. No real (non-filler)
        player won, so the episode is void and excluded from wins/played.
        """
        commissioner = _commissioner()
        policy_a, policy_b, filler = uuid4(), uuid4(), uuid4()
        memberships = _memberships([(policy_a, "ply_a"), (policy_b, "ply_b")])
        state: Any = {"round_config": {"current_division_id": str(_COMPETITION_DIV)}}
        rs = _round_start(memberships, 1, state)

        request_id = "competition:filler-win:0"
        seats = [policy_a, policy_b, filler]
        # Only the filler seat (index 2) won.
        filler_only = _episode(seats, win=[0, 0, 1], request_id=request_id)
        scheduled = [
            EpisodeRequest(
                request_id=request_id,
                variant_id="default",
                policy_version_ids=seats,
                tags={_FILLER_SEATS_TAG: "2"},
            )
        ]
        complete = commissioner.complete_round_for_round_start(
            rs, episode_results=[filler_only], scheduled_episodes=scheduled, failed_episodes=[]
        )
        by_pid = {r.policy_version_id: r for r in complete.results[0].rankings}
        # Neither real player is credited; the filler-won episode is void for them.
        for pid in (policy_a, policy_b):
            self.assertEqual(by_pid[pid].result_metadata[COMPLETED_EPISODE_COUNT_METADATA_KEY], 0)
            self.assertEqual(by_pid[pid].result_metadata["episode_wins"], 0)

    def test_both_writer_paths_agree_after_void_exclusion(self) -> None:
        """rank_division over the accumulated history matches the published board.

        Runs several rounds, some containing void episodes, and asserts the
        round-complete board equals what ``rank_division`` computes from the same
        persisted history — so the void exclusion keeps both writers in lockstep.
        """
        commissioner = _commissioner()
        policy_a, policy_b, policy_c = uuid4(), uuid4(), uuid4()
        memberships = _memberships([(policy_a, "ply_a"), (policy_b, "ply_b"), (policy_c, "ply_c")])
        state: Any = {"round_config": {"current_division_id": str(_COMPETITION_DIV)}}
        last_complete = None
        completed_round_ids: list[UUID] = []
        round_results = []
        seats = [policy_a, policy_b, policy_c]
        for round_number in range(1, 7):
            rs = _round_start(memberships, round_number, state)
            winner = round_number % 3
            win = [1 if i == winner else 0 for i in range(3)]
            won = _episode(seats, win=win)
            void = _episode(seats, win=[0, 0, 0])  # a disconnected episode each round
            last_complete = commissioner.complete_round_for_round_start(
                rs, episode_results=[won, void], scheduled_episodes=[], failed_episodes=[]
            )
            state = last_complete.state
            completed_round_ids.append(rs.round_id)
            round_results.extend(_round_result_snapshots(rs.round_id, last_complete.results[0].rankings))

        published = last_complete.leaderboards[0].views[0].rows
        rank_div_snapshots = _rank_division_board(
            commissioner, memberships, _round_snapshots(completed_round_ids), round_results
        )
        self.assertEqual(
            [row.subject_id for row in published],
            [str(s.player_id) for s in rank_div_snapshots],
        )
        for row, snap in zip(published, rank_div_snapshots, strict=True):
            self.assertEqual(row.values["rank"], snap.rank)
            self.assertAlmostEqual(float(row.values["score"]), snap.score, places=9)
            self.assertEqual(row.values["rounds_played"], snap.rounds_played)
        # Each player played exactly 1 (non-void) episode per round it participated.
        by_player = {row.subject_id: row for row in published}
        for pid in ("ply_a", "ply_b", "ply_c"):
            # 6 rounds, 1 counted episode each -> 6 played (the 6 void ones dropped).
            self.assertEqual(by_player[pid].values["episodes_played"], 6)

    def test_toggle_off_counts_void_games(self) -> None:
        """CREWRIFT_PRIME_COUNT_VOID_GAMES=1 reverts to counting all-zero games."""
        commissioner = _commissioner()
        policy_a, policy_b = uuid4(), uuid4()
        memberships = _memberships([(policy_a, "ply_a"), (policy_b, "ply_b")])
        state: Any = {"round_config": {"current_division_id": str(_COMPETITION_DIV)}}
        rs = _round_start(memberships, 1, state)
        seats = [policy_a, policy_b]
        won = _episode(seats, win=[1, 0])
        void = _episode(seats, win=[0, 0])

        original = decision.EXCLUDE_VOID_GAMES
        try:
            decision.EXCLUDE_VOID_GAMES = False
            # The commissioner imports the flag by name at module load; patch it
            # there too so the toggle takes effect for this run.
            import crewrift_prime_skill_commissioner as comm

            comm.EXCLUDE_VOID_GAMES = False
            complete = commissioner.complete_round_for_round_start(
                rs, episode_results=[won, void], scheduled_episodes=[], failed_episodes=[]
            )
        finally:
            decision.EXCLUDE_VOID_GAMES = original
            comm.EXCLUDE_VOID_GAMES = original
        by_pid = {r.policy_version_id: r for r in complete.results[0].rankings}
        # With the exclusion OFF, the void episode counts as played (2 episodes).
        self.assertEqual(by_pid[policy_a].result_metadata[COMPLETED_EPISODE_COUNT_METADATA_KEY], 2)
        self.assertEqual(by_pid[policy_b].result_metadata[COMPLETED_EPISODE_COUNT_METADATA_KEY], 2)


if __name__ == "__main__":
    unittest.main()
