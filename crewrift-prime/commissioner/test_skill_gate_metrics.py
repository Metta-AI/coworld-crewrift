"""Tests for skill-gate metric extraction from realistic game_results payloads."""

from __future__ import annotations

import unittest

from decision import (
    HUNT_VARIANT,
    TASK_VARIANT,
    VOTE_VARIANT,
    build_competition_report,
    build_qualifier_report,
    count_competition_wins,
    evaluate_combined_game,
    evaluate_entrant,
)
from game_results_loader import coerce_results_schema, has_results_schema_arrays, is_metadata_stub


def _combined_game(*, imposter_kills: int = 2, crew_tasks: int = 4, votes: bool = True) -> dict:
    # One 8-seat self-play game: 2 imposters (seats 0,1), 6 crew (seats 2..7).
    return {
        "imposter": [1, 1, 0, 0, 0, 0, 0, 0],
        "crew": [0, 0, 1, 1, 1, 1, 1, 1],
        "kills": [imposter_kills, 0, 0, 0, 0, 0, 0, 0],
        "tasks": [0, 0, crew_tasks, crew_tasks, crew_tasks, crew_tasks, crew_tasks, crew_tasks],
        "vote_players": [0, 1, 2, 0, 0, 0, 0, 0] if votes else [0, 0, 0, 0, 0, 0, 0, 0],
        "vote_skip": [3, 0, 0, 0, 0, 0, 0, 0] if votes else [0, 0, 0, 0, 0, 0, 0, 0],
        "vote_timeout": [0, 0, 0, 0, 0, 0, 0, 0],
        "win": [True, True, False, False, False, False, False, False],
        "scores": [100, 100, 0, 0, 0, 0, 0, 0],
    }


def _full_vote_episode() -> dict:
    # Deliberate vote actions occurred (votes landed on players) => participation.
    return {
        "imposter": [1, 0, 0, 0, 0, 0, 0, 0],
        "vote_players": [6, 1, 0, 0, 0, 0, 0, 0],
        "vote_skip": [0, 0, 0, 0, 0, 0, 0, 0],
        "vote_timeout": [0, 0, 0, 0, 0, 0, 0, 0],
        "scores": [100, 10, 10, 10, 10, 10, 10, 10],
    }


def _no_meeting_episode() -> dict:
    # No meeting ever occurred: every vote array is zero (incl. timeouts). This is
    # crewborg-aaln:v17's real scn_vote_basic shape — no vote opportunity.
    return {
        "imposter": [1, 0, 0, 0, 0, 0, 0, 0],
        "vote_players": [0, 0, 0, 0, 0, 0, 0, 0],
        "vote_skip": [0, 0, 0, 0, 0, 0, 0, 0],
        "vote_timeout": [0, 0, 0, 0, 0, 0, 0, 0],
        "scores": [10, 10, 10, 10, 10, 10, 10, 10],
    }


def _timeout_vote_episode() -> dict:
    # A meeting occurred (timeouts > 0) but the policy cast no vote/skip.
    return {
        "imposter": [1, 0, 0, 0, 0, 0, 0, 0],
        "vote_players": [0, 0, 0, 0, 0, 0, 0, 0],
        "vote_skip": [0, 0, 0, 0, 0, 0, 0, 0],
        "vote_timeout": [1, 1, 1, 1, 1, 1, 1, 1],
        "scores": [0, 0, 0, 0, 0, 0, 0, 0],
    }


def _skip_vote_episode() -> dict:
    # Explicit skip is a deliberate use of the vote system => participation.
    return {
        "imposter": [1, 0, 0, 0, 0, 0, 0, 0],
        "vote_players": [0, 0, 0, 0, 0, 0, 0, 0],
        "vote_skip": [2, 1, 0, 0, 0, 0, 0, 0],
        "vote_timeout": [0, 0, 0, 0, 0, 0, 0, 0],
        "scores": [0, 0, 0, 0, 0, 0, 0, 0],
    }


def _talk_only_episode() -> dict:
    # Forward-compat: a future game build emits a per-slot chat count. No votes,
    # but the policy spoke => participation (talk signal).
    return {
        "imposter": [1, 0, 0, 0, 0, 0, 0, 0],
        "vote_players": [0, 0, 0, 0, 0, 0, 0, 0],
        "vote_skip": [0, 0, 0, 0, 0, 0, 0, 0],
        "vote_timeout": [1, 1, 1, 1, 1, 1, 1, 1],
        "chat_messages": [3, 2, 1, 0, 0, 0, 0, 0],
        "scores": [0, 0, 0, 0, 0, 0, 0, 0],
    }


def _full_hunt_episode(kills_at_seat0: int = 2) -> dict:
    return {
        "imposter": [1, 0, 0, 0, 0, 0, 0, 0],
        "kills": [kills_at_seat0, 0, 0, 0, 0, 0, 0, 0],
        "scores": [120, 0, 0, 0, 0, 0, 0, 0],
    }


def _full_task_episode(per_seat: int = 5) -> dict:
    seats = [per_seat] * 8
    return {
        "tasks": seats,
        "scores": [112] * 8,
        "imposter": [0, 0, 0, 0, 0, 0, 0, 1],
    }


class GameResultsLoaderTest(unittest.TestCase):
    def test_detects_metadata_stub(self) -> None:
        stub = {
            "episode_id": "ed06d8ca-b12d-4279-b0d2-de35242bdc91",
            "job_id": "2f57b797-8ca4-4476-a7f6-a6b35f95a9b2",
            "replay_url": "https://example.test/replay.z",
        }
        self.assertTrue(is_metadata_stub(stub))
        self.assertFalse(has_results_schema_arrays(stub))

    def test_accepts_full_results_schema(self) -> None:
        full = _full_hunt_episode()
        self.assertTrue(has_results_schema_arrays(full))
        self.assertFalse(is_metadata_stub(full))
        self.assertIs(coerce_results_schema(full), full)

    def test_unwraps_nested_coworld_results(self) -> None:
        nested = {
            "job_id": "abc",
            "coworld": {"results": _full_task_episode(4)},
        }
        coerced = coerce_results_schema(nested)
        assert coerced is not None
        self.assertTrue(has_results_schema_arrays(coerced))


class SkillGateMetricsTest(unittest.TestCase):
    def test_stub_game_results_yield_zero_skill_metrics(self) -> None:
        """Documents round-9 failure mode: metadata stub counts episodes but not metrics."""
        stub = {
            "episode_id": "ed06d8ca-b12d-4279-b0d2-de35242bdc91",
            "job_id": "2f57b797-8ca4-4476-a7f6-a6b35f95a9b2",
            "replay_url": "https://example.test/replay.z",
        }
        by_variant = {
            VOTE_VARIANT: [stub] * 4,
            HUNT_VARIANT: [stub] * 4,
            TASK_VARIANT: [stub] * 4,
        }
        record = evaluate_entrant(by_variant)
        voting = record.verdicts[0]
        hunting = record.verdicts[1]
        tasks = record.verdicts[2]
        # Stub has no vote arrays => no meeting occurred => voting is a no-opportunity PASS.
        self.assertEqual(voting.episodes_counted, 4)
        self.assertEqual(voting.raw_inputs["meetings_occurred"], 0)
        self.assertTrue(voting.passed)
        self.assertIn("no meeting occurred", voting.detail)
        self.assertEqual(hunting.episodes_counted, 4)
        self.assertEqual(hunting.metric_value, 0.0)
        self.assertEqual(tasks.episodes_counted, 0)

    def test_full_game_results_compute_nonzero_metrics(self) -> None:
        """Round-9-like payload after platform forwards results.json arrays."""
        by_variant = {
            VOTE_VARIANT: [_full_vote_episode()] * 4,
            HUNT_VARIANT: [_full_hunt_episode(2), _full_hunt_episode(1), _full_hunt_episode(1), _full_hunt_episode(0)],
            TASK_VARIANT: [_full_task_episode(10)] * 4,
        }
        record = evaluate_entrant(by_variant)
        voting, hunting, tasks = record.verdicts
        # Voting is now a participation rate: voted in 4/4 meetings => 1.0.
        self.assertEqual(voting.metric_name, "meeting_participation")
        self.assertAlmostEqual(voting.metric_value, 1.0, places=4)
        self.assertEqual(voting.episodes_counted, 4)
        self.assertAlmostEqual(hunting.metric_value, 1.0, places=4)
        self.assertEqual(hunting.episodes_counted, 4)
        self.assertAlmostEqual(tasks.metric_value, 10.0, places=4)
        self.assertEqual(tasks.episodes_counted, 4)
        self.assertTrue(hunting.passed)
        self.assertTrue(tasks.passed)
        self.assertTrue(voting.passed)


class VotingParticipationAssuranceTest(unittest.TestCase):
    def test_no_meeting_is_no_opportunity_pass(self) -> None:
        # crewborg-aaln:v17's real shape: all vote arrays zero (incl. timeouts) =>
        # the drill never reached a meeting => not penalized (no vote opportunity).
        verdict = evaluate_entrant({VOTE_VARIANT: [_no_meeting_episode()] * 4}).verdicts[0]
        self.assertEqual(verdict.skill, "voting")
        self.assertEqual(verdict.metric_name, "meeting_participation")
        self.assertEqual(verdict.raw_inputs["meetings_occurred"], 0)
        self.assertTrue(verdict.passed)
        self.assertIn("no meeting occurred", verdict.detail)

    def test_meeting_reached_but_never_voted_fails(self) -> None:
        # Meetings occurred (timeouts > 0) but the entrant never cast a vote/skip =>
        # fails for the right reason (given the chance, did not vote).
        verdict = evaluate_entrant({VOTE_VARIANT: [_timeout_vote_episode()] * 4}).verdicts[0]
        self.assertEqual(verdict.raw_inputs["meetings_occurred"], 4)
        self.assertEqual(verdict.metric_value, 0.0)
        self.assertFalse(verdict.passed)
        self.assertIn("did not vote", verdict.detail)

    def test_voting_for_players_counts_as_participation(self) -> None:
        verdict = evaluate_entrant({VOTE_VARIANT: [_full_vote_episode()] * 4}).verdicts[0]
        self.assertEqual(verdict.metric_value, 1.0)
        self.assertTrue(verdict.passed)
        self.assertIn("cast votes", verdict.detail)

    def test_explicit_skip_counts_as_participation(self) -> None:
        verdict = evaluate_entrant({VOTE_VARIANT: [_skip_vote_episode()] * 4}).verdicts[0]
        self.assertTrue(verdict.passed)

    def test_any_single_vote_passes_by_default(self) -> None:
        # Default MIN=0.0: voting in even 1 of 4 reached meetings PASSES ("passes
        # if the game players vote"); the other 3 meetings were timed out.
        episodes = [_full_vote_episode(), _timeout_vote_episode(), _timeout_vote_episode(), _timeout_vote_episode()]
        verdict = evaluate_entrant({VOTE_VARIANT: episodes}).verdicts[0]
        self.assertEqual(verdict.raw_inputs["meetings_occurred"], 4)
        self.assertEqual(verdict.raw_inputs["participated_episodes"], 1)
        self.assertTrue(verdict.passed)

    def test_talk_signal_forward_compat(self) -> None:
        # No votes, but the (future) chat signal is present and non-zero => participation.
        verdict = evaluate_entrant({VOTE_VARIANT: [_talk_only_episode()] * 4}).verdicts[0]
        self.assertTrue(verdict.raw_inputs["talk_signal_available"])
        self.assertTrue(verdict.passed)
        self.assertIn("spoke", verdict.detail)


class CombinedSingleGameTest(unittest.TestCase):
    def test_one_good_game_passes_all_three(self) -> None:
        record = evaluate_combined_game(_combined_game(imposter_kills=2, crew_tasks=4, votes=True))
        skills = {v.skill: v for v in record.verdicts}
        self.assertTrue(record.passed)
        self.assertEqual(skills["hunting"].metric_name, "imposter_kills")
        self.assertEqual(skills["hunting"].metric_value, 2.0)
        self.assertEqual(skills["tasks"].metric_name, "crew_tasks_mean")
        self.assertAlmostEqual(skills["tasks"].metric_value, 4.0, places=4)
        self.assertTrue(skills["voting"].passed)

    def test_no_kill_fails_hunting_only(self) -> None:
        record = evaluate_combined_game(_combined_game(imposter_kills=0, crew_tasks=4, votes=True))
        skills = {v.skill: v for v in record.verdicts}
        self.assertFalse(record.passed)
        self.assertFalse(skills["hunting"].passed)
        self.assertTrue(skills["tasks"].passed)

    def test_none_game_fails_all(self) -> None:
        record = evaluate_combined_game(None)
        self.assertFalse(record.passed)
        self.assertTrue(all(not v.passed for v in record.verdicts))


class CompetitionWinCountTest(unittest.TestCase):
    def test_winning_episodes_score_one_each(self) -> None:
        # Entrant occupies seat 0 in each episode (one seat per game). It won 3 of
        # the 5 episodes, so it scores 3 (one point per WON episode). The role
        # split still counts the winning seats for observability.
        won_imposter = {"win": [True], "imposter": [1], "crew": [0]}
        won_crew = {"win": [True], "imposter": [0], "crew": [1]}
        lost = {"win": [False], "imposter": [0], "crew": [1]}
        episodes = [
            (won_imposter, [0]),
            (won_crew, [0]),
            (lost, [0]),
            (won_crew, [0]),
            (lost, [0]),
        ]
        rec = count_competition_wins(episodes)
        self.assertEqual(rec.wins, 3)  # episodes won
        self.assertEqual(rec.episode_wins, 3)
        self.assertEqual(rec.score, 3.0)
        self.assertEqual(rec.imposter_wins, 1)
        self.assertEqual(rec.crew_wins, 2)
        self.assertEqual(rec.episodes_counted, 5)

    def test_multiple_winning_seats_score_one_per_episode(self) -> None:
        # 8-seat self-play game where the crew team (seats 2..7) won: the entrant
        # occupies all 8 seats, but winning ONE episode scores exactly 1 (capped),
        # regardless of how many of its seats won. The 6 winning crew seats are
        # tracked as crew_wins for observability only.
        gr = _combined_game()
        gr["win"] = [False, False, True, True, True, True, True, True]
        rec = count_competition_wins([(gr, list(range(8)))])
        self.assertEqual(rec.crew_wins, 6)
        self.assertEqual(rec.imposter_wins, 0)
        self.assertEqual(rec.episode_wins, 1)
        self.assertEqual(rec.wins, 1)
        self.assertEqual(rec.score, 1.0)

    def test_imposter_and_crew_players_both_score_one_episode(self) -> None:
        # Both imposter seats (0,1) and four crew seats win in one self-play game.
        # That is still ONE won episode => score 1; the role split records the
        # winning seats (2 imposter, 4 crew) for observability.
        gr = _combined_game()
        gr["win"] = [True, True, True, True, True, True, False, False]
        rec = count_competition_wins([(gr, list(range(8)))])
        self.assertEqual(rec.imposter_wins, 2)
        self.assertEqual(rec.crew_wins, 4)
        self.assertEqual(rec.episode_wins, 1)
        self.assertEqual(rec.wins, 1)


class ObservabilityReportTest(unittest.TestCase):
    """The structured report + HTML render are well-formed and embed-safe."""

    def _combined_game(self) -> dict:
        return {
            "imposter": [1, 1, 0, 0, 0, 0, 0, 0],
            "crew": [0, 0, 1, 1, 1, 1, 1, 1],
            "kills": [2, 0, 0, 0, 0, 0, 0, 0],
            "tasks": [0, 0, 4, 4, 4, 4, 4, 4],
            "vote_players": [0, 1, 2, 0, 0, 0, 0, 0],
            "vote_skip": [3, 0, 0, 0, 0, 0, 0, 0],
            "vote_timeout": [0, 0, 0, 0, 0, 0, 0, 0],
            "win": [1, 1, 0, 0, 0, 0, 0, 0],
            "scores": [100, 100, 0, 0, 0, 0, 0, 0],
        }

    def test_qualifier_report_carries_html_not_duplicate_entrants(self) -> None:
        record = evaluate_combined_game(self._combined_game())
        report = build_qualifier_report({"pv-1": record}, player_id_by_entrant={"pv-1": "ply_a"})
        self.assertEqual(report["rule_id"], "skill_gate")
        # The per-entrant scoring trace is NOT duplicated in the report (it lives
        # on PolicyMembershipEvent evidence); only the HTML view is carried.
        self.assertNotIn("entrants", report)
        self.assertIn("<html", report["render_html"])
        self.assertIn("PROMOTED", report["render_html"])

    def test_competition_report_html_reflects_win_ranking(self) -> None:
        report = build_competition_report(
            [
                {"policy_version_id": "a", "wins": 1, "imposter_wins": 1, "crew_wins": 0, "episodes_counted": 4},
                {"policy_version_id": "b", "wins": 3, "imposter_wins": 1, "crew_wins": 2, "episodes_counted": 4},
            ]
        )
        self.assertEqual(report["rule_id"], "competition_wins")
        self.assertNotIn("entrants", report)
        html = report["render_html"]
        # Compact scoreboard: winner b (3 wins) rendered before runner-up a (1 win),
        # shown as a big win count + role breakdown (no raw UUIDs).
        self.assertIn('<span class="big">3</span>', html)
        self.assertIn('<span class="big">1</span>', html)
        self.assertLess(
            html.index('<span class="big">3</span>'),
            html.index('<span class="big">1</span>'),
        )
        self.assertIn("as imposter", html)

    def test_render_html_passes_platform_safe_render_check(self) -> None:
        # Load the platform's producer-side safety gate directly (avoid importing
        # the heavy coworld package __init__). Skip if the source tree isn't present.
        import importlib.util
        import os

        report_path = os.environ.get(
            "COWORLD_REPORT_PY",
            "/Users/aaln/experiments/softmax/metta/packages/coworld/src/coworld/report.py",
        )
        if not os.path.exists(report_path):
            self.skipTest("coworld.report source not available in this environment")
        spec = importlib.util.spec_from_file_location("cw_report", report_path)
        assert spec and spec.loader
        rmod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rmod)

        record = evaluate_combined_game(self._combined_game())
        qual = build_qualifier_report({"pv-1": record}, notes=["a note"])
        comp = build_competition_report(
            [{"policy_version_id": "a", "wins": 2, "imposter_wins": 1, "crew_wins": 1, "episodes_counted": 4}]
        )
        rmod.assert_safe_render_html(qual["render_html"], source="qualifier")
        rmod.assert_safe_render_html(comp["render_html"], source="competition")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
