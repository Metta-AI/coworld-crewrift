"""Tests for the single-game qualifier + Competition win-count commissioner.

Covers (v9):
  - the Qualifiers division schedules exactly ONE 8-seat self-play `scn_qualifier`
    game and reads all three skills from it,
  - crash safety folded into that one game: completed-with-results promotes/holds,
    a genuine non-completion DQs, and an infra/dispatch failure holds (no DQ),
  - the Competition division scores by WINNING PLAYERS (1 pt per winning seat,
    by role) with an imposter/crew breakdown, and ranks cumulatively.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from uuid import UUID, uuid4

from commissioners.common.protocol import (
    DivisionInfo,
    EpisodeFailed,
    EpisodeResult,
    EpisodeScore,
    LeagueInfo,
    MembershipInfo,
    RoundStart,
    VariantInfo,
)
from commissioners.common.ruleset_strategy.config import load_ruleset_strategy_config_file

from decision import DECISION_LOG_TAG, QUALIFIER_VARIANT
from crewrift_prime_skill_commissioner import (
    NUM_SEATS,
    CrewriftPrimeSkillCommissioner,
    _emit_decision_log,
    _looks_like_dispatch_failure,
    _round_is_dispatch_failure,
)

_CONFIG_PATH = Path(__file__).resolve().parent / "crewrift_prime.yaml"
_QUALIFIERS_DIV = UUID("60480000-0000-0000-0000-000000000001")
_COMPETITION_DIV = UUID("ac000000-0000-0000-0000-000000000002")


def _commissioner() -> CrewriftPrimeSkillCommissioner:
    return CrewriftPrimeSkillCommissioner(load_ruleset_strategy_config_file(_CONFIG_PATH))


def _divisions() -> list[DivisionInfo]:
    return [
        DivisionInfo(id=_QUALIFIERS_DIV, name="Qualifiers", level=-99, type="staging"),
        DivisionInfo(id=_COMPETITION_DIV, name="Competition", level=1, type="competition"),
    ]


def _qualifier_round_start(*, entrant: UUID | None = None) -> RoundStart:
    entrant = entrant or uuid4()
    membership = MembershipInfo(
        id=uuid4(),
        league_id=uuid4(),
        division_id=_QUALIFIERS_DIV,
        policy_version_id=entrant,
        player_id="ply_test",
        status="qualifying",
        substatus=None,
    )
    return RoundStart(
        round_id=uuid4(),
        round_number=42,
        league=LeagueInfo(id=membership.league_id, commissioner_key="container"),
        divisions=_divisions(),
        memberships=[membership],
        recent_results=[],
        variants=[
            VariantInfo(id="default", name="Default", game_config={}),
            VariantInfo(id=QUALIFIER_VARIANT, name="Qualifier", game_config={}),
        ],
        state={"round_config": {
            "stages": None,
            "entrant_policy_version_ids": [str(entrant)],
            "current_division_id": str(_QUALIFIERS_DIV),
        }},
    )


def _good_combined_game() -> dict:
    return {
        "imposter": [1, 1, 0, 0, 0, 0, 0, 0],
        "crew": [0, 0, 1, 1, 1, 1, 1, 1],
        "kills": [2, 0, 0, 0, 0, 0, 0, 0],
        "tasks": [0, 0, 4, 4, 4, 4, 4, 4],
        "vote_players": [0, 1, 2, 0, 0, 0, 0, 0],
        "vote_skip": [3, 0, 0, 0, 0, 0, 0, 0],
        "vote_timeout": [0, 0, 0, 0, 0, 0, 0, 0],
        "win": [True, True, False, False, False, False, False, False],
        "scores": [100, 100, 0, 0, 0, 0, 0, 0],
    }


class DispatchFailureClassificationTest(unittest.TestCase):
    def test_jobs_batch_400_is_dispatch_failure(self) -> None:
        self.assertTrue(_looks_like_dispatch_failure("400 Bad Request from /jobs/batch"))
        self.assertTrue(_looks_like_dispatch_failure("HTTP 503 Service Unavailable"))

    def test_real_policy_crash_is_not_dispatch_failure(self) -> None:
        self.assertFalse(_looks_like_dispatch_failure("RuntimeError: policy crashed in step()"))
        self.assertFalse(_looks_like_dispatch_failure(None))


class SingleGameQualifierSchedulingTest(unittest.TestCase):
    def test_schedules_exactly_one_eight_seat_combined_game(self) -> None:
        commissioner = _commissioner()
        schedule = commissioner.schedule_episodes_for_round_start(_qualifier_round_start())
        self.assertEqual(len(schedule.episodes), 1, "qualifier must schedule exactly ONE game")
        episode = schedule.episodes[0]
        self.assertEqual(episode.variant_id, QUALIFIER_VARIANT)
        self.assertEqual(len(episode.policy_version_ids), NUM_SEATS, "single game must fill all 8 seats")


class SingleGameQualifierCompletionTest(unittest.TestCase):
    def test_passing_game_promotes_to_competition(self) -> None:
        commissioner = _commissioner()
        rs = _qualifier_round_start()
        scheduled = commissioner.schedule_episodes_for_round_start(rs).episodes
        entrant = rs.memberships[0].policy_version_id
        results = [
            EpisodeResult(
                request_id=scheduled[0].request_id,
                scores=[EpisodeScore(policy_version_id=entrant, score=1.0) for _ in range(NUM_SEATS)],
                game_results=_good_combined_game(),
            )
        ]
        complete = commissioner.complete_round_for_round_start(
            rs, episode_results=results, scheduled_episodes=scheduled, failed_episodes=[]
        )
        self.assertEqual(len(complete.policy_membership_events), 1)
        event = complete.policy_membership_events[0]
        self.assertEqual(str(event.status), "competing")
        self.assertEqual(str(event.substatus), "champion")
        self.assertEqual(event.to_division_id, _COMPETITION_DIV)
        # Evidence is the generic, game-agnostic skill_gate type (not a crewrift-
        # specific string) and carries self-describing presentation metadata so the
        # UI renders it without any game knowledge.
        evidence = event.evidence[0]
        self.assertEqual(evidence.type, "skill_gate")
        skills = evidence.metadata["skills"]
        for verdict in skills.values():
            self.assertIn("label", verdict)
            self.assertIn("blurb", verdict)
        self.assertEqual(skills["voting"]["threshold_label"], "pass if it votes")

    def test_infra_failure_holds_not_dq(self) -> None:
        commissioner = _commissioner()
        rs = _qualifier_round_start()
        scheduled = commissioner.schedule_episodes_for_round_start(rs).episodes
        failed = [EpisodeFailed(request_id=scheduled[0].request_id, error="400 Bad Request from /jobs/batch")]
        complete = commissioner.complete_round_for_round_start(
            rs, episode_results=[], scheduled_episodes=scheduled, failed_episodes=failed
        )
        statuses = {str(e.status) for e in complete.policy_membership_events}
        self.assertNotIn("disqualified", statuses)
        self.assertEqual(str(complete.policy_membership_events[0].status), "qualifying")

    def test_non_completion_disqualifies(self) -> None:
        commissioner = _commissioner()
        rs = _qualifier_round_start()
        scheduled = commissioner.schedule_episodes_for_round_start(rs).episodes
        failed = [EpisodeFailed(request_id=scheduled[0].request_id, error="container exited with code 139 (segfault)")]
        complete = commissioner.complete_round_for_round_start(
            rs, episode_results=[], scheduled_episodes=scheduled, failed_episodes=failed
        )
        self.assertEqual(str(complete.policy_membership_events[0].status), "disqualified")


class CompetitionWinScoringTest(unittest.TestCase):
    def test_competition_round_scores_by_wins(self) -> None:
        commissioner = _commissioner()
        policy_a = uuid4()
        policy_b = uuid4()
        memberships = [
            MembershipInfo(
                id=uuid4(), league_id=uuid4(), division_id=_COMPETITION_DIV,
                policy_version_id=pid, player_id=f"ply_{i}", status="competing", substatus="champion", is_champion=True,
            )
            for i, pid in enumerate((policy_a, policy_b))
        ]
        rs = RoundStart(
            round_id=uuid4(),
            round_number=7,
            league=LeagueInfo(id=memberships[0].league_id, commissioner_key="container"),
            divisions=_divisions(),
            memberships=memberships,
            recent_results=[],
            variants=[VariantInfo(id="default", name="Default", game_config={})],
            state={"round_config": {"current_division_id": str(_COMPETITION_DIV)}},
        )
        # 2 episodes, 2 seats each (A at seat 0, B at seat 1). A wins both as imposter.
        def episode(winner_seat: int, imposter_seat: int) -> EpisodeResult:
            win = [False, False]
            win[winner_seat] = True
            imposter = [0, 0]
            imposter[imposter_seat] = 1
            crew = [1 - imposter[0], 1 - imposter[1]]
            return EpisodeResult(
                request_id=str(uuid4()),
                scores=[
                    EpisodeScore(policy_version_id=policy_a, score=0.0),
                    EpisodeScore(policy_version_id=policy_b, score=0.0),
                ],
                game_results={"win": win, "imposter": imposter, "crew": crew},
            )
        results = [episode(winner_seat=0, imposter_seat=0), episode(winner_seat=0, imposter_seat=0)]
        complete = commissioner.complete_round_for_round_start(
            rs, episode_results=results, scheduled_episodes=[], failed_episodes=[]
        )
        rankings = complete.results[0].rankings
        by_policy = {str(r.policy_version_id): r for r in rankings}
        self.assertEqual(by_policy[str(policy_a)].score, 2.0)  # 1 pt/winning player, 2 imposter wins
        self.assertEqual(by_policy[str(policy_a)].result_metadata["imposter_wins"], 2)
        self.assertEqual(by_policy[str(policy_b)].score, 0.0)
        self.assertEqual(by_policy[str(policy_a)].rank, 1)
        self.assertIn("competition_wins", complete.round_display)


class CompetitionSchedulingTest(unittest.TestCase):
    def _competition_round_start(self, entrants: list[UUID]) -> RoundStart:
        memberships = [
            MembershipInfo(
                id=uuid4(), league_id=uuid4(), division_id=_COMPETITION_DIV,
                policy_version_id=pid, player_id=f"ply_{i}", status="competing",
                substatus="champion", is_champion=True,
            )
            for i, pid in enumerate(entrants)
        ]
        return RoundStart(
            round_id=uuid4(),
            round_number=70,
            league=LeagueInfo(id=memberships[0].league_id, commissioner_key="container"),
            divisions=_divisions(),
            memberships=memberships,
            recent_results=[],
            variants=[VariantInfo(id="default", name="Default", game_config={})],
            state={"round_config": {
                "current_division_id": str(_COMPETITION_DIV),
                "stages": [{"label": "Round", "self_play": False, "num_episodes": 12,
                            "min_episodes_per_entrant": 12}],
                "entrant_policy_version_ids": [str(p) for p in entrants],
            }},
        )

    def test_competition_schedules_eight_seat_episodes(self) -> None:
        commissioner = _commissioner()
        entrants = [uuid4(), uuid4(), uuid4()]
        rs = self._competition_round_start(entrants)
        schedule = commissioner.schedule_episodes_for_round_start(rs)
        self.assertEqual(len(schedule.episodes), 12, "stage num_episodes must be honored")
        for ep in schedule.episodes:
            self.assertEqual(
                len(ep.policy_version_ids), NUM_SEATS,
                "every Competition episode must fill all 8 seats (closed-roster game)",
            )
            # only the real entrants occupy seats
            self.assertTrue(set(ep.policy_version_ids) <= set(entrants))
        # all three entrants are seated across the round
        seated = {pid for ep in schedule.episodes for pid in ep.policy_version_ids}
        self.assertEqual(seated, set(entrants))

    def test_competition_single_entrant_fills_all_seats(self) -> None:
        commissioner = _commissioner()
        entrant = uuid4()
        rs = self._competition_round_start([entrant])
        schedule = commissioner.schedule_episodes_for_round_start(rs)
        self.assertTrue(schedule.episodes)
        for ep in schedule.episodes:
            self.assertEqual(ep.policy_version_ids, [entrant] * NUM_SEATS)


class ObservabilityHelpersTest(unittest.TestCase):
    def test_emit_decision_log_writes_greppable_stdout(self) -> None:
        entrant = str(uuid4())
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _emit_decision_log({"entrant_policy_version_id": entrant, "decision": "X", "passed": False})
        line = buffer.getvalue().strip()
        self.assertTrue(line.startswith(f"{DECISION_LOG_TAG} "))
        self.assertEqual(json.loads(line[len(DECISION_LOG_TAG) + 1 :])["entrant_policy_version_id"], entrant)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
