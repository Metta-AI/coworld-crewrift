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
  - the Competition division scores by WINNING PLAYERS (1 pt per winning seat, by
    role) and ranks cumulatively.

The xp-request client and the replay parser are MOCKED — no network calls and no
Nim engine are used. We inject a fake :class:`XpRequestClient` and monkeypatch the
module's ``parse_replay_metrics`` so the gate is exercised end to end in-process.
"""

from __future__ import annotations

import io
import json
import os
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
import crewrift_prime_skill_commissioner as commissioner_module
from crewrift_prime_skill_commissioner import (
    NUM_SEATS,
    CrewriftPrimeSkillCommissioner,
    _emit_decision_log,
    _looks_like_dispatch_failure,
)
from xp_request_client import EpisodeRow, XpRequestInfraError, XpRequestRun
from replay_parser import ReplayParseError

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
    # Passes all three skills: imposter kills, crew tasks, a vote cast, and the
    # policy talks in the meeting (the in-replay talk gate).
    return {
        "imposter": [1, 1, 0, 0, 0, 0, 0, 0],
        "crew": [0, 0, 1, 1, 1, 1, 1, 1],
        "kills": [2, 0, 0, 0, 0, 0, 0, 0],
        "tasks": [0, 0, 4, 4, 4, 4, 4, 4],
        "vote_players": [0, 1, 2, 0, 0, 0, 0, 0],
        "vote_skip": [3, 0, 0, 0, 0, 0, 0, 0],
        "vote_timeout": [0, 0, 0, 0, 0, 0, 0, 0],
        "chat_messages": [1, 1, 2, 0, 0, 0, 0, 0],
        "win": [True, True, False, False, False, False, False, False],
        "scores": [100, 100, 0, 0, 0, 0, 0, 0],
    }


def _failing_combined_game() -> dict:
    # No kills => hunting fails.
    game = _good_combined_game()
    game["kills"] = [0, 0, 0, 0, 0, 0, 0, 0]
    return game


def _silent_combined_game() -> dict:
    # A meeting occurred (votes cast) but NO seat talked -> the in-replay talk
    # gate fails the voting verdict (the policy is not LLM-enabled).
    game = _good_combined_game()
    game["chat_messages"] = [0, 0, 0, 0, 0, 0, 0, 0]
    return game


class _FakeXpClient:
    """A stand-in for XpRequestClient that returns scripted runs (no network).

    Configure either ``run`` (an XpRequestRun to return) or ``run_error`` (an
    exception to raise from ``run_qualifier``). ``download_replay`` returns the
    configured bytes (used only as an opaque token; the parser is mocked).
    """

    def __init__(self, *, run: XpRequestRun | None = None, run_error: Exception | None = None,
                 replay_bytes: bytes = b"replay", download_error: Exception | None = None,
                 filler_ids: list[str] | None = None, filler_error: Exception | None = None) -> None:
        self._run = run
        self._run_error = run_error
        self._replay_bytes = replay_bytes
        self._download_error = download_error
        self._filler_ids = filler_ids or []
        self._filler_error = filler_error
        self.created: list[tuple[str, str]] = []
        self.filler_lookups: list[str] = []

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

    def download_replay(self, replay_url: str, *, timeout=None) -> bytes:
        if self._download_error is not None:
            raise self._download_error
        return self._replay_bytes


def _completed_run(*, replay_url: str = "https://example.test/replay.z") -> XpRequestRun:
    return XpRequestRun(
        xreq_id="xreq_test",
        status="completed",
        episodes=[EpisodeRow(id="ereq_1", status="completed", episode_id="ep1", replay_url=replay_url)],
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
        # Mock the replay parser so no Nim engine / network is touched.
        original = commissioner_module.parse_replay_metrics
        commissioner_module.parse_replay_metrics = lambda _bytes, num_seats=NUM_SEATS: monkey_game
        try:
            return commissioner.qualify_submission(membership, _COMPETITION_DIV)
        finally:
            commissioner_module.parse_replay_metrics = original

    def test_passing_replay_promotes_to_competition(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
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
        # No separate interview skill any more — the talk check lives in voting.
        self.assertNotIn("interview", skills)
        self.assertTrue(skills["voting"]["passed"])
        self.assertTrue(skills["voting"]["raw_inputs"]["talk_signal_available"])

    def test_failing_replay_holds_qualifying(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
        membership = _membership("submitted")
        event = self._run_qualify(commissioner, membership, _failing_combined_game())
        self.assertEqual(str(event.status), "qualifying")
        self.assertEqual(event.to_division_id, membership.division_id)  # held in place
        self.assertNotEqual(str(event.status), "disqualified")

    def test_meeting_without_talk_holds_even_when_other_skills_pass(self) -> None:
        # The in-replay talk gate: a meeting occurred but the policy never talked
        # -> voting fails ("not LLM-enabled") -> does not qualify.
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
        membership = _membership("submitted")
        event = self._run_qualify(commissioner, membership, _silent_combined_game())
        self.assertEqual(str(event.status), "qualifying")
        self.assertNotEqual(str(event.status), "competing")
        skills = event.evidence[0].metadata["skills"]
        self.assertFalse(skills["voting"]["passed"])
        self.assertIn("never talked", skills["voting"]["detail"])

    def test_xp_request_infra_failure_holds_not_dq(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run_error=XpRequestInfraError("400 from /jobs/batch"))
        membership = _membership("submitted")
        event = commissioner.qualify_submission(membership, _COMPETITION_DIV)
        self.assertEqual(str(event.status), "qualifying")
        self.assertNotEqual(str(event.status), "disqualified")
        self.assertEqual(event.evidence[0].type, "crewrift_prime_dispatch_failure")

    def test_replay_parse_failure_holds_not_dq(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
        membership = _membership("submitted")
        original = commissioner_module.parse_replay_metrics

        def _boom(_bytes, num_seats=NUM_SEATS):
            raise ReplayParseError("expander unavailable")

        commissioner_module.parse_replay_metrics = _boom
        try:
            event = commissioner.qualify_submission(membership, _COMPETITION_DIV)
        finally:
            commissioner_module.parse_replay_metrics = original
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
        commissioner._xp_client = _FakeXpClient(run=_completed_run())
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
        original = commissioner_module.parse_replay_metrics
        commissioner_module.parse_replay_metrics = lambda _b, num_seats=NUM_SEATS: _good_combined_game()
        try:
            result = commissioner.migrate_league(ctx)
        finally:
            commissioner_module.parse_replay_metrics = original
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
                tags={"competition": "1", "filler_seats": "1,2,3,4,5,6,7"},
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
        finally:
            if prev is None:
                os.environ.pop("CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS", None)
            else:
                os.environ["CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS"] = prev


class FillerPolicyResolutionTest(unittest.TestCase):
    """Filler set precedence (env override > league-config API > empty) AND the
    bare-UUID -> ``league_<uuid>`` path normalization the live v2 route requires.

    The commissioner protocol hands ``round_start.league.id`` as a BARE
    ``uuid.UUID``, but ``GET /v2/leagues/{league_id}/filler-policies`` validates
    its path param as a ``PrefixedId`` (``^league_<uuid>$``) and returns HTTP 422
    *before the handler* for a bare UUID. That 422 was previously caught and
    silently degraded to "no fillers" — the exact reason a configured filler
    (e.g. ``crewborg-aaln:v16``) was never seated. These tests pin the fix.
    """

    def setUp(self) -> None:
        self._prev_env = os.environ.get("CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS")
        os.environ.pop("CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS", None)

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop("CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS", None)
        else:
            os.environ["CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS"] = self._prev_env

    def test_api_served_fillers_used_when_env_unset(self) -> None:
        api_a, api_b = uuid4(), uuid4()
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(filler_ids=[str(api_a), str(api_b)])
        league_id = uuid4()
        resolved = commissioner._filler_policy_version_ids(league_id)
        self.assertEqual(resolved, [api_a, api_b])
        # The API was queried with the league_id.
        self.assertEqual(commissioner._xp_client.filler_lookups, [str(league_id)])

    def test_env_override_wins_over_api(self) -> None:
        env_a = uuid4()
        api_a = uuid4()
        os.environ["CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS"] = str(env_a)
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(filler_ids=[str(api_a)])
        resolved = commissioner._filler_policy_version_ids(uuid4())
        self.assertEqual(resolved, [env_a])
        # Env override short-circuits: the API is never consulted.
        self.assertEqual(commissioner._xp_client.filler_lookups, [])

    def test_api_failure_falls_back_gracefully(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(
            filler_error=XpRequestInfraError("GET filler-policies -> HTTP 503")
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            resolved = commissioner._filler_policy_version_ids(uuid4())
        self.assertEqual(resolved, [])
        self.assertIn("WARNING", buffer.getvalue())

    def test_empty_api_list_falls_back_to_no_filler(self) -> None:
        commissioner = _commissioner()
        commissioner._xp_client = _FakeXpClient(filler_ids=[])
        self.assertEqual(commissioner._filler_policy_version_ids(uuid4()), [])

    def test_real_client_seats_fillers_through_prefixed_league_route(self) -> None:
        # REGRESSION (full chain, REAL XpRequestClient with mocked transport, no
        # network): round_start.league.id is a BARE uuid.UUID, but the v2
        # filler-policies path param is the prefixed LeagueId (^league_<uuid>$).
        # Before the fix the client sent the bare UUID -> HTTP 422 *before the
        # handler* -> caught -> empty -> NO fillers seated (the exact symptom).
        # Assert the real client builds the league_<uuid> URL and the served
        # filler is seated into the empty seats of the closed 8-seat roster.
        from xp_request_client import LEAGUE_ID_PREFIX, XpRequestClient

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

        served_filler = uuid4()
        body = {"filler_policy_versions": [{"policy_version_id": str(served_filler)}]}

        def _fake_urlopen(req, timeout=None):  # noqa: ARG001 - signature parity
            captured["url"] = req.full_url
            return _FakeResponse(json.dumps(body).encode("utf-8"))

        commissioner = _commissioner()
        commissioner._xp_client = XpRequestClient(
            base="https://example.test/observatory", token="tok-abc"
        )
        entrants = [uuid4(), uuid4(), uuid4()]
        rs = CompetitionSchedulingTest()._competition_round_start(entrants)
        # round_start.league.id is a bare UUID (LeagueInfo.id: UUID).
        self.assertNotIn(LEAGUE_ID_PREFIX, str(rs.league.id))

        with unittest.mock.patch("urllib.request.urlopen", _fake_urlopen):
            schedule = commissioner.schedule_episodes_for_round_start(rs)

        # The real client hit the PREFIXED league route, not the bare UUID.
        url = str(captured.get("url", ""))
        self.assertTrue(
            url.endswith(f"/v2/leagues/{LEAGUE_ID_PREFIX}{rs.league.id}/filler-policies"),
            f"expected prefixed league route, got: {url}",
        )
        # And the served filler actually fills the empty seats 3..7.
        self.assertTrue(schedule.episodes)
        for ep in schedule.episodes:
            self.assertEqual(ep.tags["filler_seats"], "3,4,5,6,7")
            self.assertEqual(set(ep.policy_version_ids[3:]), {served_filler})


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
                division_id="div-1",
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
        # Target is still {division_id: ...}.
        self.assertEqual(body["target"], {"division_id": "div-1"})
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

    def _capture_filler_get(self, league_id: str, *, body: dict | None = None):
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

        resp_body = body if body is not None else {"filler_policy_versions": []}

        def _fake_urlopen(req, timeout=None):  # noqa: ARG001 - signature parity
            captured["method"] = req.get_method()
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            return _FakeResponse(json.dumps(resp_body).encode("utf-8"))

        client = XpRequestClient(base="https://example.test/observatory", token="tok-abc")
        with unittest.mock.patch("urllib.request.urlopen", _fake_urlopen):
            ids = client.get_filler_policy_versions(league_id)
        return ids, captured

    def test_get_filler_policy_versions_parses_payload(self) -> None:
        # The league-config GET reuses the same authenticated client/transport and
        # returns the ordered policy_version_id strings from filler_policy_versions.
        body = {
            "filler_policy_versions": [
                {"policy_version_id": "pv-1", "policy_id": "p-1", "policy_name": "notsus", "version": 3},
                {"policy_version_id": "pv-2", "policy_id": "p-2", "policy_name": "notsus", "version": 4},
            ]
        }
        ids, captured = self._capture_filler_get("league_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", body=body)
        self.assertEqual(ids, ["pv-1", "pv-2"])
        self.assertEqual(captured["method"], "GET")
        self.assertTrue(
            str(captured["url"]).endswith(
                "/v2/leagues/league_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/filler-policies"
            )
        )
        # Same auth header as the rest of the client.
        self.assertEqual(dict(captured["headers"]).get("X-auth-token"), "tok-abc")

    def test_get_filler_policy_versions_prefixes_bare_uuid_league_id(self) -> None:
        # REGRESSION: the commissioner protocol hands a BARE uuid.UUID
        # (round_start.league.id), but the v2 path param is a PrefixedId validated
        # against ^league_<uuid>$ — a bare UUID is rejected with HTTP 422 *before*
        # the handler, which previously degraded silently to "no fillers". The
        # client must prepend the ``league_`` prefix so the real endpoint matches.
        from xp_request_client import LEAGUE_ID_PREFIX

        league_uuid = str(uuid4())  # bare UUID, exactly what LeagueInfo.id carries
        pv = str(uuid4())
        body = {"filler_policy_versions": [{"policy_version_id": pv}]}
        ids, captured = self._capture_filler_get(league_uuid, body=body)
        self.assertEqual(ids, [pv])
        url = str(captured["url"])
        self.assertTrue(
            url.endswith(f"/v2/leagues/{LEAGUE_ID_PREFIX}{league_uuid}/filler-policies"),
            f"bare UUID must be normalized to league_<uuid>, got URL: {url}",
        )
        self.assertNotIn(f"/v2/leagues/{league_uuid}/filler-policies", url)

    def test_get_filler_policy_versions_leaves_already_prefixed_id(self) -> None:
        # An already-prefixed league_<uuid> passes through untouched (no double prefix).
        from xp_request_client import LEAGUE_ID_PREFIX

        prefixed = f"{LEAGUE_ID_PREFIX}{uuid4()}"
        _ids, captured = self._capture_filler_get(prefixed)
        url = str(captured["url"])
        self.assertTrue(url.endswith(f"/v2/leagues/{prefixed}/filler-policies"))
        self.assertNotIn(f"{LEAGUE_ID_PREFIX}{LEAGUE_ID_PREFIX}", url)


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
