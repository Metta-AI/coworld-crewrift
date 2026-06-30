"""Tests for the event-driven qualifier + Competition win-count commissioner.

Covers (event-driven rework):
  - a new submission is qualified by an xp-request self-play game whose
    ``.bitreplay`` is parsed into metrics, then promoted to Competition on pass,
  - crash/infra safety WITHOUT a qualifier division: a passing replay promotes,
    a failing replay holds (status qualifying, in place), a genuine non-completion
    (terminal run, no completed episode) DQs, and an xp-request/replay infra
    failure holds (no DQ),
  - ``migrate_league`` qualifies every submitted/qualifying membership and leaves
    competing memberships untouched,
  - the Competition division scores by WON EPISODES (1 pt per episode won, capped
    at 1 per episode, role-agnostic) and ranks by all-time WIN RATE.

The xp-request client is MOCKED — no network calls. We inject a fake
:class:`XpRequestClient` whose ``get_episode_results`` returns a scripted per-slot
results JSON, so the gate is exercised end to end in-process WITHOUT any replay
download or Nim engine.
"""

from __future__ import annotations

import io
import json
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path
from uuid import UUID, uuid4

from commissioners.common.models import (
    DivisionSnapshot,
    LeagueMigrationContext,
    LeagueSnapshot,
    MembershipSnapshot,
    PolicyMembershipStatus,
)
from commissioners.common.protocol import (
    DivisionInfo,
    EpisodeRequest,
    EpisodeResult,
    EpisodeScore,
    LeagueInfo,
    MembershipInfo,
    RoundStart,
    VariantInfo,
)
from commissioners.common.ruleset_strategy.config import load_ruleset_strategy_config_file

from decision import DECISION_LOG_TAG
from crewrift_prime_skill_commissioner import (
    NUM_SEATS,
    CrewriftPrimeSkillCommissioner,
    _emit_decision_log,
    _looks_like_dispatch_failure,
)
from xp_request_client import EpisodeRow, XpRequestInfraError, XpRequestRun

_CONFIG_PATH = Path(__file__).resolve().parent / "crewrift_prime.yaml"
_COMPETITION_DIV = UUID("ac000000-0000-0000-0000-000000000002")


def _commissioner() -> CrewriftPrimeSkillCommissioner:
    return CrewriftPrimeSkillCommissioner(load_ruleset_strategy_config_file(_CONFIG_PATH))


def _divisions() -> list[DivisionInfo]:
    return [DivisionInfo(id=_COMPETITION_DIV, name="Competition", level=1, type="competition")]


def _division_snapshots(league_id: UUID) -> list[DivisionSnapshot]:
    return [
        DivisionSnapshot(
            id=_COMPETITION_DIV, name="Competition", level=1, league_id=league_id, type="competition"
        )
    ]


def _good_combined_game() -> dict:
    # Passes all three skills: imposter kills, crew tasks, a vote cast.
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


def _failing_combined_game() -> dict:
    # No kills => hunting fails.
    game = _good_combined_game()
    game["kills"] = [0, 0, 0, 0, 0, 0, 0, 0]
    return game


class _FakeXpClient:
    """A stand-in for XpRequestClient that returns scripted runs (no network).

    Configure either ``run`` (an XpRequestRun to return) or ``run_error`` (an
    exception to raise from ``run_qualifier``). ``get_episode_results`` returns the
    configured per-slot results JSON (the ``results_schema`` payload the qualifier
    reads from ``/jobs/{job_id}/artifacts/results``) or raises ``results_error`` to
    exercise the infra-hold path.
    """

    def __init__(self, *, run: XpRequestRun | None = None, run_error: Exception | None = None,
                 results: dict | None = None, results_error: Exception | None = None,
                 filler_ids: list[str] | None = None, filler_error: Exception | None = None) -> None:
        self._run = run
        self._run_error = run_error
        self._results = results
        self._results_error = results_error
        self._filler_ids = filler_ids or []
        self._filler_error = filler_error
        self.created: list[tuple[str, str]] = []
        self.filler_lookups: list[str] = []
        self.results_lookups: list[str] = []

    def run_qualifier(self, *, division_id: str, policy_version_id: str, **_kw) -> XpRequestRun:
        self.created.append((division_id, policy_version_id))
        if self._run_error is not None:
            raise self._run_error
        assert self._run is not None
        return self._run

    def get_filler_policy_versions(self, league_id: str) -> list[str]:
        self.filler_lookups.append(str(league_id))
        if self._filler_error is not None:
            raise self._filler_error
        return list(self._filler_ids)

    def get_episode_results(self, job_id: str) -> dict:
        self.results_lookups.append(str(job_id))
        if self._results_error is not None:
            raise self._results_error
        assert self._results is not None
        return self._results


class _FakeInterviewTransport:
    """A stand-in InterviewTransport that returns a scripted answer frame."""

    def __init__(self, answer: str = "Vote out players with vent evidence; skip when unsure.", degraded: bool = False) -> None:
        self._answer = answer
        self._degraded = degraded

    def ask(self, question: str, *, context=None) -> dict:
        return {"type": "interview_answer", "answer": self._answer, "degraded": self._degraded}


class _FakeInterviewLLM:
    """A stand-in AnthropicRestClient: fixed question + fixed score (no network).

    ``score_error``/``generate_error`` simulate interviewer-LLM failures to
    exercise the resiliency paths (riddle fallback / scorer auto-pass).
    """

    model = "fake-interviewer"

    def __init__(self, score: float = 0.9, reason: str = "solid", *,
                 score_error: Exception | None = None, generate_error: Exception | None = None) -> None:
        self._score = score
        self._reason = reason
        self._score_error = score_error
        self._generate_error = generate_error

    def generate_question(self) -> str:
        if self._generate_error is not None:
            raise self._generate_error
        return "When should a crewmate skip a vote?"

    def score_answer(self, question: str, answer: str) -> tuple[float, str]:
        if self._score_error is not None:
            raise self._score_error
        return self._score, self._reason


def _wire_interview(commissioner, *, score: float = 0.9, answer: str = "Vote out players with vent evidence; skip when genuinely unsure.", degraded: bool = False, transport_error: Exception | None = None, llm: object | None = None) -> None:
    """Inject a passing (or configurable) interview onto a commissioner — no network."""
    if transport_error is not None:
        def _provider(_membership):
            raise transport_error
        commissioner._interview_transport_provider = _provider
    else:
        commissioner._interview_transport_provider = lambda _m: _FakeInterviewTransport(answer=answer, degraded=degraded)
    commissioner._interview_llm = llm if llm is not None else _FakeInterviewLLM(score=score)


def _completed_run(*, job_id: str = "job-1", replay_url: str = "https://example.test/replay.z") -> XpRequestRun:
    return XpRequestRun(
        xreq_id="xreq_test",
        status="completed",
        episodes=[EpisodeRow(id="ereq_1", status="completed", episode_id="ep1", replay_url=replay_url, job_id=job_id)],
    )


def _membership(status: str = "submitted") -> MembershipSnapshot:
    return MembershipSnapshot(
        id=uuid4(),
        league_id=uuid4(),
        division_id=_COMPETITION_DIV,
        policy_version_id=uuid4(),
        player_id="ply_test",
        status=PolicyMembershipStatus(status),
        substatus=None,
    )


class DispatchFailureClassificationTest(unittest.TestCase):
    def test_jobs_batch_400_is_dispatch_failure(self) -> None:
        self.assertTrue(_looks_like_dispatch_failure("400 Bad Request from /jobs/batch"))
        self.assertTrue(_looks_like_dispatch_failure("HTTP 503 Service Unavailable"))

    def test_real_policy_crash_is_not_dispatch_failure(self) -> None:
        self.assertFalse(_looks_like_dispatch_failure("RuntimeError: policy crashed in step()"))
        self.assertFalse(_looks_like_dispatch_failure(None))


class QualifySubmissionTest(unittest.TestCase):
    """The core event-driven loop: xp request -> parse replay -> evaluate -> event."""

    def _run_qualify(self, commissioner, membership, monkey_game) -> object:
        # Feed the scripted results JSON through the fake client's
        # get_episode_results — no replay download or Nim engine is touched.
        commissioner._xp_client._results = monkey_game
        return commissioner.qualify_submission(membership, _COMPETITION_DIV)

    def test_passing_replay_promotes_to_competition(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
        _wire_interview(commissioner)
        membership = _membership("submitted")
        event = self._run_qualify(commissioner, membership, _good_combined_game())
        self.assertEqual(str(event.status), "competing")
        self.assertEqual(str(event.substatus), "champion")
        self.assertEqual(event.to_division_id, _COMPETITION_DIV)
        evidence = event.evidence[0]
        self.assertEqual(evidence.type, "skill_gate")
        self.assertEqual(evidence.metadata["xreq_id"], "xreq_test")
        skills = evidence.metadata["skills"]
        for verdict in skills.values():
            self.assertIn("label", verdict)
            self.assertIn("blurb", verdict)
        # The interview is now a recorded fourth skill.
        self.assertIn("interview", skills)
        self.assertTrue(skills["interview"]["passed"])

    def test_failing_replay_holds_qualifying(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
        _wire_interview(commissioner)
        membership = _membership("submitted")
        event = self._run_qualify(commissioner, membership, _failing_combined_game())
        self.assertEqual(str(event.status), "qualifying")
        self.assertEqual(event.to_division_id, membership.division_id)  # held in place
        self.assertNotEqual(str(event.status), "disqualified")

    def test_failing_interview_holds_even_when_skills_pass(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
        # Skills pass, but the interview scores below threshold -> does not qualify.
        _wire_interview(commissioner, score=0.1)
        membership = _membership("submitted")
        event = self._run_qualify(commissioner, membership, _good_combined_game())
        self.assertEqual(str(event.status), "qualifying")
        self.assertNotEqual(str(event.status), "competing")
        skills = event.evidence[0].metadata["skills"]
        self.assertFalse(skills["interview"]["passed"])

    def test_degraded_interview_holds_even_when_skills_pass(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
        # A degraded player answer never passes regardless of score.
        _wire_interview(commissioner, score=0.95, degraded=True)
        membership = _membership("submitted")
        event = self._run_qualify(commissioner, membership, _good_combined_game())
        self.assertNotEqual(str(event.status), "competing")
        self.assertFalse(event.evidence[0].metadata["skills"]["interview"]["passed"])

    def test_interview_infra_failure_holds_not_dq(self) -> None:
        from interview import InterviewInfraError as _InfraErr

        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
        _wire_interview(commissioner, transport_error=_InfraErr("player interview server unreachable"))
        membership = _membership("submitted")
        event = self._run_qualify(commissioner, membership, _good_combined_game())
        self.assertEqual(str(event.status), "qualifying")
        self.assertNotEqual(str(event.status), "disqualified")
        self.assertEqual(event.evidence[0].type, "crewrift_prime_interview_failure")

    def test_scorer_llm_failure_auto_passes_and_promotes(self) -> None:
        # An answer WAS received but the SCORER LLM fails -> auto-pass: the
        # interview qualifies (given skills pass) and the policy is promoted.
        from interview import InterviewInfraError as _InfraErr

        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
        _wire_interview(commissioner, llm=_FakeInterviewLLM(score_error=_InfraErr("grader HTTP 500")))
        membership = _membership("submitted")
        event = self._run_qualify(commissioner, membership, _good_combined_game())
        self.assertEqual(str(event.status), "competing")
        skills = event.evidence[0].metadata["skills"]
        self.assertTrue(skills["interview"]["passed"])
        interview_meta = event.evidence[0].metadata["interview"]
        self.assertTrue(interview_meta["auto_passed"])
        self.assertIn("auto-passed", skills["interview"]["detail"])

    def test_riddle_gen_llm_failure_uses_fallback_and_still_gates(self) -> None:
        # Riddle generation fails -> fallback question used, answer scored
        # normally; a strong score still passes the interview and promotes.
        from interview import InterviewInfraError as _InfraErr

        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
        _wire_interview(commissioner, llm=_FakeInterviewLLM(score=0.9, generate_error=_InfraErr("no gen key")))
        membership = _membership("submitted")
        event = self._run_qualify(commissioner, membership, _good_combined_game())
        self.assertEqual(str(event.status), "competing")
        interview_meta = event.evidence[0].metadata["interview"]
        self.assertTrue(interview_meta["fallback_question"])
        self.assertFalse(interview_meta["auto_passed"])

    def test_xp_request_infra_failure_holds_not_dq(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run_error=XpRequestInfraError("400 from /jobs/batch"))
        membership = _membership("submitted")
        event = commissioner.qualify_submission(membership, _COMPETITION_DIV)
        self.assertEqual(str(event.status), "qualifying")
        self.assertNotEqual(str(event.status), "disqualified")
        self.assertEqual(event.evidence[0].type, "crewrift_prime_dispatch_failure")

    def test_results_fetch_failure_holds_not_dq(self) -> None:
        commissioner = _commissioner()
        # A completed episode whose results JSON can't be fetched -> infra hold.
        commissioner._xp_client = _FakeXpClient(
            run=_completed_run(),
            results_error=XpRequestInfraError("artifact 503"),
        )
        membership = _membership("submitted")
        event = commissioner.qualify_submission(membership, _COMPETITION_DIV)
        self.assertEqual(str(event.status), "qualifying")
        self.assertNotEqual(str(event.status), "disqualified")

    def test_results_missing_per_slot_arrays_holds_not_dq(self) -> None:
        commissioner = _commissioner()
        # A completed episode whose results JSON lacks the per-slot skill arrays
        # (e.g. only scores) -> infra hold, never DQ.
        commissioner._xp_client = _FakeXpClient(
            run=_completed_run(),
            results={"scores": [1.0] * NUM_SEATS},
        )
        membership = _membership("submitted")
        event = commissioner.qualify_submission(membership, _COMPETITION_DIV)
        self.assertEqual(str(event.status), "qualifying")
        self.assertNotEqual(str(event.status), "disqualified")

    def test_completed_episode_without_job_id_holds_not_dq(self) -> None:
        commissioner = _commissioner()
        run = XpRequestRun(
            xreq_id="xreq_test",
            status="completed",
            episodes=[EpisodeRow(id="ereq_1", status="completed", episode_id="ep1", replay_url=None, job_id=None)],
        )
        commissioner._xp_client = _FakeXpClient(run=run, results=_good_combined_game())
        membership = _membership("submitted")
        event = commissioner.qualify_submission(membership, _COMPETITION_DIV)
        self.assertEqual(str(event.status), "qualifying")
        self.assertNotEqual(str(event.status), "disqualified")

    def test_non_completion_disqualifies(self) -> None:
        commissioner = _commissioner()
        # Terminal run with NO completed episode -> genuine non-completion.
        empty_run = XpRequestRun(xreq_id="xreq_empty", status="completed", episodes=[])
        commissioner._xp_client = _FakeXpClient(run=empty_run)
        membership = _membership("submitted")
        event = commissioner.qualify_submission(membership, _COMPETITION_DIV)
        self.assertEqual(str(event.status), "disqualified")
        self.assertEqual(event.evidence[0].type, "crewrift_prime_qualifier_crash")


class MigrateLeagueQualificationTest(unittest.TestCase):
    def test_migrate_league_qualifies_submitted_only(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run(), results=_good_combined_game())
        _wire_interview(commissioner)
        league_id = uuid4()
        submitted = _membership("submitted")
        qualifying = _membership("qualifying")
        competing = MembershipSnapshot(
            id=uuid4(), league_id=league_id, division_id=_COMPETITION_DIV,
            policy_version_id=uuid4(), player_id="ply_c",
            status=PolicyMembershipStatus.competing, substatus="champion", is_champion=True,
        )
        ctx = LeagueMigrationContext(
            league=LeagueSnapshot(id=league_id, commissioner_key="container", commissioner_config=None),
            divisions=_division_snapshots(league_id),
            memberships=[submitted, qualifying, competing],
        )
        result = commissioner.migrate_league(ctx)
        qualified_ids = {e.league_policy_membership_id for e in result.policy_membership_events}
        self.assertIn(submitted.id, qualified_ids)
        self.assertIn(qualifying.id, qualified_ids)
        self.assertNotIn(competing.id, qualified_ids)
        # both submitted/qualifying got an xp request created
        self.assertEqual(len(commissioner._xp_client.created), 2)


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
        self.assertEqual(by_policy[str(policy_a)].score, 2.0)
        self.assertEqual(by_policy[str(policy_a)].result_metadata["imposter_wins"], 2)
        self.assertEqual(by_policy[str(policy_b)].score, 0.0)
        self.assertEqual(by_policy[str(policy_a)].rank, 1)
        self.assertIn("competition_wins", complete.round_display)

    def test_filler_seat_wins_are_excluded_from_scoring(self) -> None:
        # An 8-seat round with 1 real entrant + 7 filler/duplicate top-up seats.
        # The real entrant is at seat 0 (a winning crew seat); seats 1..7 are
        # filler seats that ALSO win — but they must NOT score.
        commissioner = _commissioner()
        real = uuid4()
        filler = uuid4()
        rs = RoundStart(
            round_id=uuid4(),
            round_number=9,
            league=LeagueInfo(id=uuid4(), commissioner_key="container"),
            divisions=_divisions(),
            memberships=[
                MembershipInfo(
                    id=uuid4(), league_id=uuid4(), division_id=_COMPETITION_DIV,
                    policy_version_id=real, player_id="ply_real", status="competing",
                    substatus="champion", is_champion=True,
                )
            ],
            recent_results=[],
            variants=[VariantInfo(id="default", name="Default", game_config={})],
            state={"round_config": {"current_division_id": str(_COMPETITION_DIV)}},
        )
        request_id = "competition:r9:0"
        scheduled = [
            EpisodeRequest(
                request_id=request_id,
                variant_id="default",
                policy_version_ids=[real] + [filler] * 7,
                tags={
                    "competition": "1",
                    "filler_seats": "1,2,3,4,5,6,7",
                    "filler_policy_version_ids": str(filler),
                },
            )
        ]
        # Every seat wins as crew, but only seat 0 (the real entrant) should count.
        result = EpisodeResult(
            request_id=request_id,
            scores=[EpisodeScore(policy_version_id=real, score=0.0)]
            + [EpisodeScore(policy_version_id=filler, score=0.0) for _ in range(7)],
            game_results={
                "win": [True] * 8,
                "imposter": [0, 0, 0, 0, 0, 0, 0, 0],
                "crew": [1, 1, 1, 1, 1, 1, 1, 1],
            },
        )
        complete = commissioner.complete_round_for_round_start(
            rs, episode_results=[result], scheduled_episodes=scheduled, failed_episodes=[]
        )
        rankings = complete.results[0].rankings
        by_policy = {str(r.policy_version_id): r for r in rankings}
        # Only the real entrant is ranked, and it scores exactly ONE crew win
        # (its single real seat), not 8 — filler seats are excluded.
        self.assertEqual(set(by_policy), {str(real)})
        self.assertEqual(by_policy[str(real)].score, 1.0)
        self.assertEqual(by_policy[str(real)].result_metadata["crew_wins"], 1)
        self.assertEqual(by_policy[str(real)].result_metadata["imposter_wins"], 0)
        # The filler is NEVER represented as a real entrant...
        self.assertNotIn(str(filler), by_policy)
        # ...and is explicitly labeled as a filler in the round display.
        self.assertEqual(
            complete.round_display.get("filler_policy_version_ids"), [str(filler)]
        )

    def test_filler_policy_excluded_even_when_seat_not_tagged(self) -> None:
        # Defense-in-depth: a CONFIGURED filler policy must never score, even if the
        # per-seat ``filler_seats`` tag failed to mark its seat. Here the filler is
        # at seat 7 but ``filler_seats`` omits seat 7; the policy-id tag still drops
        # it (and it is never ranked as a real entrant).
        commissioner = _commissioner()
        real = uuid4()
        filler = uuid4()
        rs = RoundStart(
            round_id=uuid4(),
            round_number=11,
            league=LeagueInfo(id=uuid4(), commissioner_key="container"),
            divisions=_divisions(),
            memberships=[
                MembershipInfo(
                    id=uuid4(), league_id=uuid4(), division_id=_COMPETITION_DIV,
                    policy_version_id=real, player_id="ply_real", status="competing",
                    substatus="champion", is_champion=True,
                )
            ],
            recent_results=[],
            variants=[VariantInfo(id="default", name="Default", game_config={})],
            state={"round_config": {"current_division_id": str(_COMPETITION_DIV)}},
        )
        request_id = "competition:r11:0"
        scheduled = [
            EpisodeRequest(
                request_id=request_id,
                variant_id="default",
                policy_version_ids=[real] + [filler] * 7,
                # Seat 7 is intentionally NOT in filler_seats, but the policy id tag
                # marks the filler so it is still excluded by policy.
                tags={
                    "competition": "1",
                    "filler_seats": "1,2,3,4,5,6",
                    "filler_policy_version_ids": str(filler),
                },
            )
        ]
        result = EpisodeResult(
            request_id=request_id,
            scores=[EpisodeScore(policy_version_id=real, score=0.0)]
            + [EpisodeScore(policy_version_id=filler, score=0.0) for _ in range(7)],
            game_results={
                "win": [True] * 8,
                "imposter": [0, 0, 0, 0, 0, 0, 0, 0],
                "crew": [1, 1, 1, 1, 1, 1, 1, 1],
            },
        )
        complete = commissioner.complete_round_for_round_start(
            rs, episode_results=[result], scheduled_episodes=scheduled, failed_episodes=[]
        )
        by_policy = {str(r.policy_version_id): r for r in complete.results[0].rankings}
        # The filler never appears even though seat 7 was untagged, and the real
        # entrant still only scores its single legitimate seat.
        self.assertEqual(set(by_policy), {str(real)})
        self.assertEqual(by_policy[str(real)].score, 1.0)
        self.assertEqual(
            complete.round_display.get("filler_policy_version_ids"), [str(filler)]
        )


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
            # No filler ids configured -> empty seats cycle real entrants, but each
            # real entrant is still seated AT MOST ONCE as a non-filler real seat.
            filler_seats = {
                int(s) for s in ep.tags["filler_seats"].split(",") if s.strip()
            }
            real_seats = [
                pid for i, pid in enumerate(ep.policy_version_ids) if i not in filler_seats
            ]
            self.assertEqual(
                len(real_seats), len(set(real_seats)),
                "no real policy may occupy more than one scored seat in a round",
            )
            self.assertTrue(set(real_seats) <= set(entrants))
        seated = {
            ep.policy_version_ids[i]
            for ep in schedule.episodes
            for i in range(NUM_SEATS)
            if str(i) not in ep.tags["filler_seats"].split(",")
        }
        self.assertEqual(seated, set(entrants))

    def test_competition_single_entrant_fills_all_seats(self) -> None:
        commissioner = _commissioner()
        entrant = uuid4()
        rs = self._competition_round_start([entrant])
        schedule = commissioner.schedule_episodes_for_round_start(rs)
        self.assertTrue(schedule.episodes)
        for ep in schedule.episodes:
            # The closed roster still dispatches 8 seats, but only seat 0 is the
            # real entrant; seats 1..7 are filler/duplicate top-up (excluded).
            self.assertEqual(len(ep.policy_version_ids), NUM_SEATS)
            self.assertEqual(ep.tags["filler_seats"], "1,2,3,4,5,6,7")
            self.assertEqual(ep.policy_version_ids[0], entrant)
            # No CONFIGURED fillers: the top-up duplicates the real entrant, so the
            # filler-policy tag is empty (a duplicated real policy is not a filler).
            self.assertEqual(ep.tags["filler_policy_version_ids"], "")

    def test_real_policies_equal_seats_no_filler_no_duplication(self) -> None:
        # When real entrants == NUM_SEATS, every seat is a distinct real policy and
        # there are NO filler seats at all.
        commissioner = _commissioner()
        entrants = [uuid4() for _ in range(NUM_SEATS)]
        rs = self._competition_round_start(entrants)
        schedule = commissioner.schedule_episodes_for_round_start(rs)
        self.assertTrue(schedule.episodes)
        for ep in schedule.episodes:
            self.assertEqual(ep.tags["filler_seats"], "", "no filler when real >= seats")
            self.assertEqual(ep.tags["filler_policy_version_ids"], "")
            self.assertEqual(
                sorted(ep.policy_version_ids), sorted(entrants),
                "each real policy occupies exactly one seat",
            )

    def test_configured_fillers_top_up_remaining_seats(self) -> None:
        # With fewer real entrants than seats AND fillers configured, the empty
        # seats are filled with the configured filler policies (not duplicated
        # real entrants), and each real policy still appears at most once.
        filler_a, filler_b = uuid4(), uuid4()
        import os

        prev = os.environ.get("CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS")
        os.environ["CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS"] = f"{filler_a}, {filler_b}"
        try:
            commissioner = _commissioner()
            entrants = [uuid4(), uuid4(), uuid4()]
            rs = self._competition_round_start(entrants)
            schedule = commissioner.schedule_episodes_for_round_start(rs)
            self.assertTrue(schedule.episodes)
            for ep in schedule.episodes:
                self.assertEqual(len(ep.policy_version_ids), NUM_SEATS)
                self.assertEqual(ep.tags["filler_seats"], "3,4,5,6,7")
                real_seats = ep.policy_version_ids[:3]
                self.assertEqual(
                    len(real_seats), len(set(real_seats)),
                    "each real policy seated at most once",
                )
                self.assertTrue(set(real_seats) <= set(entrants))
                filler_seats = ep.policy_version_ids[3:]
                self.assertTrue(set(filler_seats) <= {filler_a, filler_b})
                # Configured fillers are recorded by policy id so scoring can drop
                # them explicitly and label them as fillers.
                tagged_filler_ids = {
                    s for s in ep.tags["filler_policy_version_ids"].split(",") if s
                }
                self.assertEqual(tagged_filler_ids, {str(p) for p in filler_seats})
        finally:
            if prev is None:
                os.environ.pop("CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS", None)
            else:
                os.environ["CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS"] = prev


class XpRequestPayloadTest(unittest.TestCase):
    """The qualifier POST body must match the live V2CreateExperienceRequestRequest.

    The platform schema (``V2CreateExperienceRequestRequest``) forbids extra keys
    and requires a ``roster`` of exactly ``player_count`` participants
    (``len(roster) == player_count``). For Crewrift's closed-roster 8-seat self-play
    qualifier that means 8 participants, each pinning the candidate via
    ``player.policy_ref``. The legacy ``requester``/``opponents``/``backfill`` keys
    must be gone. No real network is used: ``urllib.request.urlopen`` is patched to
    capture the request and return a canned ``{"id": ...}`` body.
    """

    def _capture_post(self, *, seat_count=NUM_SEATS, num_episodes=1):
        from xp_request_client import XpRequestClient

        captured: dict[str, object] = {}

        class _FakeResponse:
            def __init__(self, payload: bytes) -> None:
                self._payload = payload

            def read(self) -> bytes:
                return self._payload

            def __enter__(self):
                return self

            def __exit__(self, *_exc) -> bool:
                return False

        def _fake_urlopen(req, timeout=None):  # noqa: ARG001 - signature parity
            captured["method"] = req.get_method()
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode("utf-8")) if req.data else None
            return _FakeResponse(json.dumps({"id": "xreq_test123"}).encode("utf-8"))

        client = XpRequestClient(base="https://example.test/observatory", token="tok-abc")
        with unittest.mock.patch("urllib.request.urlopen", _fake_urlopen):
            xreq_id = client.create_experience_request(
                division_id="acbde92a-df21-4489-859c-4510bd4445f2",
                policy_version_id="pv-candidate",
                seat_count=seat_count,
                num_episodes=num_episodes,
                notes="crewrift-prime qualifier",
            )
        return xreq_id, captured

    def test_create_posts_roster_based_self_play_body(self) -> None:
        xreq_id, captured = self._capture_post()
        self.assertEqual(xreq_id, "xreq_test123")
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(str(captured["url"]).endswith("/v2/experience-requests"))

        body = captured["body"]
        assert isinstance(body, dict)
        # Target is {division_id: ...}, normalized to the platform's prefixed
        # ``div_<uuid>`` form (a bare UUID is rejected by the DivisionId type, 422).
        self.assertEqual(
            body["target"], {"division_id": "div_acbde92a-df21-4489-859c-4510bd4445f2"}
        )
        # num_episodes / execution_backend live at the top level.
        self.assertEqual(body["num_episodes"], 1)
        self.assertEqual(body["execution_backend"], "k8s")

        # Roster: exactly NUM_SEATS self-play participants, all pinning the candidate
        # via player.policy_ref with the schema-default round-robin slot (-1).
        roster = body["roster"]
        self.assertIsInstance(roster, list)
        self.assertEqual(len(roster), NUM_SEATS)
        for participant in roster:
            self.assertEqual(participant, {"player": {"policy_ref": "pv-candidate"}, "slot": -1})

        # The legacy shape's keys must be gone (schema is extra="forbid").
        for legacy_key in ("requester", "opponents", "backfill"):
            self.assertNotIn(legacy_key, body)

    def test_num_episodes_and_seat_count_are_honored(self) -> None:
        _xreq_id, captured = self._capture_post(seat_count=8, num_episodes=3)
        body = captured["body"]
        assert isinstance(body, dict)
        self.assertEqual(body["num_episodes"], 3)
        self.assertEqual(len(body["roster"]), 8)

    def test_auth_header_is_x_auth_token(self) -> None:
        _xreq_id, captured = self._capture_post()
        headers = captured["headers"]
        assert isinstance(headers, dict)
        # urllib title-cases header keys; X-Auth-Token -> X-auth-token.
        self.assertEqual(headers.get("X-auth-token"), "tok-abc")

    def test_prefixed_division_id_is_idempotent(self) -> None:
        from xp_request_client import _prefixed_division_id, _prefixed_league_id

        bare = "acbde92a-df21-4489-859c-4510bd4445f2"
        prefixed = f"div_{bare}"
        # A bare UUID gains the div_ prefix; an already-prefixed id is unchanged.
        self.assertEqual(_prefixed_division_id(bare), prefixed)
        self.assertEqual(_prefixed_division_id(prefixed), prefixed)
        # Surrounding whitespace is trimmed before the check.
        self.assertEqual(_prefixed_division_id(f"  {bare} "), prefixed)

        # Same contract for league ids (filler-policy lookup path param).
        league_prefixed = f"league_{bare}"
        self.assertEqual(_prefixed_league_id(bare), league_prefixed)
        self.assertEqual(_prefixed_league_id(league_prefixed), league_prefixed)


class ObservabilityHelpersTest(unittest.TestCase):
    def test_emit_decision_log_writes_greppable_stdout(self) -> None:
        entrant = str(uuid4())
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            _emit_decision_log({"policy_version_id": entrant, "decision": "X", "passed": False})
        line = buffer.getvalue().strip()
        self.assertTrue(line.startswith(f"{DECISION_LOG_TAG} "))
        self.assertEqual(json.loads(line[len(DECISION_LOG_TAG) + 1 :])["policy_version_id"], entrant)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
