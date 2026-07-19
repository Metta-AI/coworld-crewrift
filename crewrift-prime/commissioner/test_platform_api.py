from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

from commissioners.common.models import (
    DivisionCommissionerDescriptionPublic,
    LeagueMigrationResult,
    PolicyMembershipEventChange as CommissionerMembershipEventChange,
)
from commissioners.common.protocol import (
    CommissionerRoundReport,
    DivisionLeaderboard,
    DivisionLeaderboardColumn,
    DivisionLeaderboardRow,
    DivisionLeaderboardView,
)
from platform_api import (
    AbortRoundResult,
    AuthoredRoundScoreEntry,
    CommissionerState,
    CommissionerStateResponse,
    DivisionDeclaration,
    DivisionRef,
    DivisionTopologyResponse,
    ExplicitRoundEpisode,
    LeagueSettings,
    LeagueSettingsDefaults,
    LeagueSettingsResponse,
    LeagueSummary,
    MembershipEventBatchResult,
    MembershipSummary,
    PlatformCommissionerClient,
    PlayerRef,
    PolicyVersionRef,
    RoundList,
    RoundDetail,
    RoundEpisodePlan,
    RoundEpisodeResult,
    RoundEpisodeRuntime,
    RoundEpisodeScore,
    RoundResultSummary,
    RoundSummary,
)
from platform_manager import (
    CREWRIFT_PRIME_DIVISIONS,
    PLATFORM_CAPABILITY_GAPS,
    CrewriftPrimePlatformManager,
    _leaderboard_for_live_roster,
    _manager_from_env,
)


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class PlatformCommissionerClientTest(unittest.TestCase):
    def test_uses_bearer_auth_and_prefixed_league_id(self) -> None:
        payload = {
            "id": "league_00000000-0000-0000-0000-000000000001",
            "name": "Crewrift Prime",
            "commissioner_key": "container",
            "rounds_paused_at": None,
        }
        with patch.object(
            urllib.request, "urlopen", return_value=_Response(payload)
        ) as urlopen:
            client = PlatformCommissionerClient(
                base="https://example.test/api/observatory", token="cmr_secret"
            )
            league = client.get_league("00000000-0000-0000-0000-000000000001")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer cmr_secret")
        self.assertEqual(
            request.full_url,
            "https://example.test/api/observatory/v2/leagues/league_00000000-0000-0000-0000-000000000001",
        )
        self.assertEqual(league.name, "Crewrift Prime")

    def test_declares_typed_division_topology(self) -> None:
        payload = {
            "divisions": [
                {
                    "id": "div_00000000-0000-0000-0000-000000000010",
                    "name": "Competition",
                    "level": 1,
                    "type": "competition",
                    "hidden": False,
                }
            ],
            "moves": [],
        }
        with patch.object(
            urllib.request, "urlopen", return_value=_Response(payload)
        ) as urlopen:
            client = PlatformCommissionerClient(
                base="https://example.test", token="cmr_secret"
            )
            result = client.declare_divisions(
                "league_00000000-0000-0000-0000-000000000001",
                [DivisionDeclaration(name="Competition", level=1, type="competition")],
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "PUT")
        self.assertEqual(
            json.loads(request.data),
            {
                "divisions": [
                    {
                        "name": "Competition",
                        "level": 1,
                        "type": "competition",
                        "hidden": False,
                    }
                ]
            },
        )
        self.assertEqual(result.divisions[0].name, "Competition")

    def test_commissioner_state_preserves_non_object_json(self) -> None:
        with patch.object(
            urllib.request,
            "urlopen",
            return_value=_Response({"version": 8, "state": False}),
        ):
            client = PlatformCommissionerClient(
                base="https://example.test", token="cmr_secret"
            )
            state = client.get_commissioner_state(
                "league_00000000-0000-0000-0000-000000000001"
            )

        self.assertIs(state.state.root, False)

    def test_admits_membership_with_idempotency_key(self) -> None:
        payload = {
            "league_policy_membership_id": "lpm_00000000-0000-0000-0000-000000000020",
            "policy_version_id": "00000000-0000-0000-0000-000000000021",
            "status": "competing",
            "substatus": "active",
            "is_champion": True,
        }
        with patch.object(
            urllib.request, "urlopen", return_value=_Response(payload)
        ) as urlopen:
            client = PlatformCommissionerClient(
                base="https://example.test", token="cmr_secret"
            )
            membership = client.admit_membership(
                "league_00000000-0000-0000-0000-000000000001",
                policy_version_id=UUID("00000000-0000-0000-0000-000000000021"),
                division_id="div_00000000-0000-0000-0000-000000000010",
                idempotency_key="submission-21",
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data)["idempotency_key"], "submission-21")
        self.assertEqual(membership.status, "competing")

    def test_creates_round_with_json_policy_version_ids(self) -> None:
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        payload = {
            "id": "round_00000000-0000-0000-0000-000000000061",
            "round_number": 1,
            "commissioner_key": "platform",
            "status": "pending",
            "division": {
                "id": "div_00000000-0000-0000-0000-000000000010",
                "name": "Competition",
                "level": 1,
                "type": "competition",
            },
            "round_config": {"entrant_policy_version_ids": [str(policy_id)]},
        }
        with patch.object(
            urllib.request, "urlopen", return_value=_Response(payload)
        ) as urlopen:
            client = PlatformCommissionerClient(
                base="https://example.test", token="cmr_secret"
            )
            client.create_round(
                division_id=payload["division"]["id"],
                idempotency_key="round-21",
                entrant_policy_version_ids=[policy_id],
            )

        request_body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(
            request_body["round_config"]["entrant_policy_version_ids"],
            [str(policy_id)],
        )

    def test_reads_typed_round_results_for_commissioner_history(self) -> None:
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        payload = {
            "id": "round_00000000-0000-0000-0000-000000000061",
            "round_number": 4,
            "commissioner_key": "container",
            "status": "completed",
            "division": {
                "id": "div_00000000-0000-0000-0000-000000000001",
                "name": "Competition",
                "level": 1,
                "type": "competition",
            },
            "round_config": {"entrant_policy_version_ids": [str(policy_id)]},
            "results": [
                {
                    "id": "rr_00000000-0000-0000-0000-000000000081",
                    "rank": 1,
                    "score": 3,
                    "result_metadata": {"points": 3},
                    "policy_version": {"id": str(policy_id)},
                    "player": {"id": "ply_1", "name": "Prime Player"},
                    "created_at": "2026-07-16T00:00:00Z",
                }
            ],
        }
        with patch.object(urllib.request, "urlopen", return_value=_Response(payload)):
            client = PlatformCommissionerClient(
                base="https://example.test", token="cmr_secret"
            )
            round_detail = client.get_round(payload["id"])

        self.assertEqual(round_detail.results[0].policy_version.id, policy_id)
        self.assertEqual(round_detail.results[0].player.name, "Prime Player")
        self.assertEqual(round_detail.commissioner_key, "container")

    def test_plans_explicit_role_pinned_round(self) -> None:
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        payload = {
            "strategy": "explicit",
            "params": {},
            "coworld_id": "cow_00000000-0000-0000-0000-000000000030",
            "variant_id": None,
            "seat_count": None,
            "entrant_policy_version_ids": [str(policy_id)],
            "episodes": [
                {
                    "job_index": 0,
                    "variant_id": "default",
                    "seed": 7,
                    "policy_version_ids": [str(policy_id)],
                    "filler_seats": [],
                    "game_config": {"slots": [{"role": "imposter"}]},
                }
            ],
        }
        with patch.object(
            urllib.request, "urlopen", return_value=_Response(payload)
        ) as urlopen:
            client = PlatformCommissionerClient(
                base="https://example.test", token="cmr_secret"
            )
            plan = client.plan_explicit_round(
                "round_1",
                [
                    ExplicitRoundEpisode(
                        variant_id="default",
                        seed=7,
                        policy_version_ids=[policy_id],
                        game_config_overrides={"slots": [{"role": "imposter"}]},
                    )
                ],
            )

        self.assertEqual(
            json.loads(urlopen.call_args.args[0].data)["strategy"], "explicit"
        )
        self.assertEqual(plan.episodes[0].game_config["slots"][0]["role"], "imposter")

    def test_uses_execution_specific_round_episode_routes(self) -> None:
        round_id = "round_00000000-0000-0000-0000-000000000061"
        client = PlatformCommissionerClient(
            base="https://example.test", token="cmr_secret"
        )
        with patch.object(
            client,
            "_post",
            return_value={
                "episode_request_ids": [],
                "dispatched": 0,
                "replayed": False,
            },
        ) as post:
            client.dispatch_round(round_id)
        post.assert_called_once_with(
            f"/v2/rounds/{round_id}/episodes:dispatch", {}
        )
        with patch.object(client, "_get", return_value=[]) as get:
            client.get_round_episodes(round_id)
        get.assert_called_once_with(f"/v2/rounds/{round_id}/episode-executions")

    def test_persists_authored_score_report_and_round_display(self) -> None:
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        payload = {
            "trace": {
                "rule": "crewrift-prime-role-weighted-wins",
                "scored_at": "2026-07-15T00:00:00Z",
                "entries": [
                    {
                        "policy_version_id": str(policy_id),
                        "rank": 1,
                        "score": 3,
                        "episodes_scored": 1,
                        "episodes_excluded": 0,
                        "result_metadata": {"imposter_wins": 1},
                    }
                ],
            },
            "replayed": False,
        }
        report = CommissionerRoundReport(
            rule_id="crewrift-prime-role-weighted-wins",
            rule_description="Three points for an imposter win.",
        )
        with patch.object(
            urllib.request, "urlopen", return_value=_Response(payload)
        ) as urlopen:
            client = PlatformCommissionerClient(
                base="https://example.test", token="cmr_secret"
            )
            result = client.score_authored_round(
                "round_1",
                rule_id=report.rule_id,
                entries=[
                    AuthoredRoundScoreEntry(
                        policy_version_id=policy_id,
                        rank=1,
                        score=3,
                        episodes_scored=1,
                        result_metadata={"imposter_wins": 1},
                    )
                ],
                round_display={"winner": str(policy_id)},
                commissioner_report=report,
            )

        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["mode"], "authored")
        self.assertEqual(body["commissioner_report"]["rule_id"], report.rule_id)
        self.assertEqual(result.trace.entries[0].score, 3)

    def test_aborts_round_with_typed_durable_reason(self) -> None:
        round_id = "round_00000000-0000-0000-0000-000000000061"
        reason = "cancelled episode requests at job indexes: 0, 2"
        with patch.object(
            urllib.request,
            "urlopen",
            return_value=_Response(
                {
                    "status": "failed",
                    "reason": reason,
                    "replayed": False,
                    "jobs_failed": 1,
                    "episode_requests_deleted": 1,
                }
            ),
        ) as urlopen:
            client = PlatformCommissionerClient(
                base="https://example.test", token="cmr_secret"
            )
            result = client.abort_round(round_id, reason=reason)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(
            request.full_url,
            f"https://example.test/v2/rounds/{round_id}/abort",
        )
        self.assertEqual(json.loads(request.data), {"reason": reason})
        self.assertEqual(
            result,
            AbortRoundResult(
                status="failed",
                reason=reason,
                jobs_failed=1,
                episode_requests_deleted=1,
            ),
        )

    def test_publishes_typed_leaderboard_and_description(self) -> None:
        division_uuid = UUID("00000000-0000-0000-0000-000000000010")
        division_id = f"div_{division_uuid}"
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        leaderboard = DivisionLeaderboard(
            division_id=division_uuid,
            views=[
                DivisionLeaderboardView(
                    key="win_rate",
                    columns=[
                        DivisionLeaderboardColumn(key="win_rate", value_type="percent")
                    ],
                    rows=[
                        DivisionLeaderboardRow(
                            subject_id="ply_00000000-0000-0000-0000-000000000031",
                            values={"win_rate": 1.0},
                            policy_version_ids={policy_id},
                        )
                    ],
                )
            ],
        )
        description = DivisionCommissionerDescriptionPublic(
            round_schedule="Every ten minutes",
            round_structure="Eight seats",
            leaderboard_rules="Ranked by win rate",
        )
        responses = [
            _Response(leaderboard.model_dump(mode="json", exclude={"division_id"})),
            _Response(description.model_dump(mode="json")),
        ]
        with patch.object(urllib.request, "urlopen", side_effect=responses) as urlopen:
            client = PlatformCommissionerClient(
                base="https://example.test", token="cmr_secret"
            )
            stored_board = client.publish_division_leaderboard(division_id, leaderboard)
            stored_description = client.publish_division_description(
                division_id, description
            )

        board_body = json.loads(urlopen.call_args_list[0].args[0].data)
        self.assertNotIn("division_id", board_body["leaderboards"])
        self.assertEqual(stored_board.views[0].key, "win_rate")
        self.assertEqual(stored_description.leaderboard_rules, "Ranked by win rate")


class CrewriftPrimePlatformManagerTest(unittest.TestCase):
    league_id = "league_00000000-0000-0000-0000-000000000001"

    def _client(
        self, *, current_spend_limit: float | None, final_spend_limit: float | None
    ) -> MagicMock:
        client = MagicMock(spec=PlatformCommissionerClient)
        client.get_league.return_value = LeagueSummary(
            id=self.league_id,
            name="Crewrift Prime",
            commissioner_key="container",
            rounds_paused_at=None,
        )
        client.list_divisions.return_value = [
            DivisionRef(
                id=f"div_00000000-0000-0000-0000-00000000000{index}",
                name=declaration.name,
                level=declaration.level,
                type=declaration.type,
            )
            for index, declaration in enumerate(CREWRIFT_PRIME_DIVISIONS, start=1)
        ]
        client.list_memberships.return_value = []
        client.list_rounds.return_value = RoundList(
            entries=[], total_count=12, limit=200, offset=0
        )
        client.get_commissioner_state.return_value = CommissionerStateResponse(
            version=4,
            state=CommissionerState(root={"history": []}),
        )
        client.apply_membership_events.return_value = MembershipEventBatchResult(
            applied=True,
            results=[],
        )
        current = LeagueSettingsResponse(
            settings=LeagueSettings(
                episode_player_pod_llm_spend_limit_usd=current_spend_limit
            ),
            defaults=LeagueSettingsDefaults(
                episodes_per_round=36, round_interval_minutes=10
            ),
        )
        final = LeagueSettingsResponse(
            settings=LeagueSettings(
                episode_player_pod_llm_spend_limit_usd=final_spend_limit
            ),
            defaults=current.defaults,
        )
        client.get_typed_league_settings.side_effect = [current, final]
        client.declare_divisions.return_value = DivisionTopologyResponse(
            divisions=client.list_divisions.return_value,
            moves=[],
        )
        return client

    def test_leaderboard_rebinds_historical_rows_to_live_roster(self) -> None:
        division = DivisionRef(
            id="div_00000000-0000-0000-0000-000000000001",
            name="Competition",
            level=1,
            type="competition",
        )
        live_player_id = "ply_00000000-0000-0000-0000-000000000051"
        departed_player_id = "ply_00000000-0000-0000-0000-000000000052"
        historical_policy_id = UUID("00000000-0000-0000-0000-000000000021")
        live_policy_id = UUID("00000000-0000-0000-0000-000000000022")
        departed_policy_id = UUID("00000000-0000-0000-0000-000000000023")
        memberships = [
            MembershipSummary(
                id="lpm_00000000-0000-0000-0000-000000000041",
                status="disqualified",
                is_champion=False,
                division=division,
                policy_version=PolicyVersionRef(id=historical_policy_id),
                player=PlayerRef(id=live_player_id, name="Live Player"),
            ),
            MembershipSummary(
                id="lpm_00000000-0000-0000-0000-000000000042",
                status="competing",
                is_champion=True,
                division=division,
                policy_version=PolicyVersionRef(id=live_policy_id),
                player=PlayerRef(id=live_player_id, name="Live Player"),
            ),
            MembershipSummary(
                id="lpm_00000000-0000-0000-0000-000000000043",
                status="disqualified",
                is_champion=False,
                division=division,
                policy_version=PolicyVersionRef(id=departed_policy_id),
                player=PlayerRef(id=departed_player_id, name="Departed Player"),
            ),
        ]
        leaderboard = DivisionLeaderboard(
            division_id=UUID("00000000-0000-0000-0000-000000000001"),
            views=[
                DivisionLeaderboardView(
                    rows=[
                        DivisionLeaderboardRow(
                            subject_id=departed_player_id,
                            values={"rank": 1, "wins": 9},
                            policy_version_ids={departed_policy_id},
                        ),
                        DivisionLeaderboardRow(
                            subject_id=live_player_id,
                            values={"rank": 2, "wins": 7},
                            policy_version_ids={historical_policy_id},
                        ),
                    ]
                )
            ],
        )

        published = _leaderboard_for_live_roster(leaderboard, memberships)

        self.assertEqual(len(published.views[0].rows), 1)
        row = published.views[0].rows[0]
        self.assertEqual(row.subject_id, live_player_id)
        self.assertEqual(row.policy_version_ids, {live_policy_id})
        self.assertEqual(row.values, {"rank": 1, "wins": 7})

    def test_reconcile_declares_topology_and_updates_spend_limit(self) -> None:
        client = self._client(current_spend_limit=5, final_spend_limit=10)
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )

        result = manager.reconcile()

        client.declare_divisions.assert_called_once_with(
            self.league_id, CREWRIFT_PRIME_DIVISIONS
        )
        settings = client.replace_league_settings.call_args.args[1]
        self.assertEqual(settings.episode_player_pod_llm_spend_limit_usd, 10)
        self.assertTrue(result.settings_updated)
        self.assertEqual(
            result.snapshot.division_names, ["Competition", "Imposters", "Crew"]
        )
        self.assertEqual(result.snapshot.round_count, 12)
        self.assertEqual(result.remaining_gaps, PLATFORM_CAPABILITY_GAPS)
        self.assertEqual(
            [gap.kind for gap in result.remaining_gaps],
            ["cross-surface"],
        )

    def test_reconcile_does_not_rewrite_matching_settings(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )

        result = manager.reconcile()

        client.replace_league_settings.assert_not_called()
        self.assertFalse(result.settings_updated)

    def test_run_plans_and_dispatches_pending_round_through_platform_api(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        competition = client.list_divisions.return_value[0]
        membership = MembershipSummary(
            id="lpm_00000000-0000-0000-0000-000000000041",
            status="competing",
            is_champion=True,
            division=competition,
            policy_version=PolicyVersionRef(id=policy_id),
            player=PlayerRef(
                id="ply_00000000-0000-0000-0000-000000000051", name="Prime Player"
            ),
        )
        pending = RoundSummary(
            id="round_00000000-0000-0000-0000-000000000061",
            round_number=1,
            commissioner_key="platform",
            status="pending",
            division=competition,
            round_config={"entrant_policy_version_ids": [str(policy_id)]},
        )
        client.list_memberships.return_value = [membership]
        client.list_rounds.return_value = RoundList(
            entries=[pending], total_count=1, limit=200, offset=0
        )
        client.get_typed_league_settings.side_effect = None
        client.get_typed_league_settings.return_value = LeagueSettingsResponse(
            settings=LeagueSettings(
                round_interval_minutes=10,
                episode_player_pod_llm_spend_limit_usd=10,
            ),
            defaults=LeagueSettingsDefaults(
                episodes_per_round=36, round_interval_minutes=10
            ),
        )
        client.get_round_episodes.return_value = []
        client.get_filler_policy_versions.return_value = []
        client.get_league_settings.return_value = {
            "episode_player_pod_llm_spend_limit_usd": 10
        }
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )

        result = manager.run_once()

        client.plan_explicit_round.assert_called_once()
        planned = client.plan_explicit_round.call_args.args[1]
        self.assertEqual(len(planned), 1)
        self.assertEqual(len(planned[0].policy_version_ids), 8)
        client.dispatch_round.assert_called_once_with(pending.id)
        self.assertEqual(result.rounds_dispatched, 1)

    def test_run_does_not_claim_legacy_container_round(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        competition = client.list_divisions.return_value[0]
        client.list_memberships.return_value = [
            MembershipSummary(
                id="lpm_00000000-0000-0000-0000-000000000041",
                status="competing",
                is_champion=True,
                division=competition,
                policy_version=PolicyVersionRef(id=policy_id),
                player=PlayerRef(id="ply_1", name="Prime Player"),
            )
        ]
        legacy_round = RoundSummary(
            id="round_00000000-0000-0000-0000-000000000061",
            round_number=1,
            commissioner_key="container",
            status="pending",
            division=competition,
            round_config={"entrant_policy_version_ids": [str(policy_id)]},
        )
        client.list_rounds.return_value = RoundList(
            entries=[legacy_round], total_count=1, limit=200, offset=0
        )
        client.get_typed_league_settings.side_effect = None
        client.get_typed_league_settings.return_value = LeagueSettingsResponse(
            settings=LeagueSettings(
                round_interval_minutes=10,
                episode_player_pod_llm_spend_limit_usd=10,
            ),
            defaults=LeagueSettingsDefaults(
                episodes_per_round=36, round_interval_minutes=10
            ),
        )
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )

        result = manager.run_once()

        self.assertEqual(
            [call.kwargs["division_id"] for call in client.create_round.call_args_list],
            [division.id for division in client.list_divisions.return_value[1:]],
        )
        client.get_round_episodes.assert_not_called()
        client.plan_explicit_round.assert_not_called()
        client.dispatch_round.assert_not_called()
        self.assertEqual(result.rounds_created, 2)
        self.assertEqual(result.rounds_dispatched, 0)

    def test_run_schedules_non_champion_competing_membership(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        competition = client.list_divisions.return_value[0]
        client.list_memberships.return_value = [
            MembershipSummary(
                id="lpm_00000000-0000-0000-0000-000000000041",
                status="competing",
                is_champion=False,
                division=competition,
                policy_version=PolicyVersionRef(id=policy_id),
                player=PlayerRef(
                    id="ply_00000000-0000-0000-0000-000000000051",
                    name="Prime Player",
                ),
            )
        ]
        client.get_typed_league_settings.side_effect = None
        client.get_typed_league_settings.return_value = LeagueSettingsResponse(
            settings=LeagueSettings(
                round_interval_minutes=10,
                episode_player_pod_llm_spend_limit_usd=10,
            ),
            defaults=LeagueSettingsDefaults(
                episodes_per_round=36, round_interval_minutes=10
            ),
        )
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )

        result = manager.run_once()

        self.assertEqual(result.rounds_created, 3)
        self.assertEqual(client.create_round.call_count, 3)
        for call in client.create_round.call_args_list:
            self.assertEqual(call.kwargs["entrant_policy_version_ids"], [policy_id])

    def test_run_does_not_count_idempotent_round_replay_as_created(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        competition = client.list_divisions.return_value[0]
        client.list_divisions.return_value = [competition]
        client.list_memberships.return_value = [
            MembershipSummary(
                id="lpm_00000000-0000-0000-0000-000000000041",
                status="competing",
                is_champion=True,
                division=competition,
                policy_version=PolicyVersionRef(id=policy_id),
                player=PlayerRef(id="ply_1", name="Prime Player"),
            )
        ]
        completed = RoundSummary(
            id="round_00000000-0000-0000-0000-000000000061",
            round_number=1,
            commissioner_key="platform",
            status="completed",
            division=competition,
            round_config={"entrant_policy_version_ids": [str(policy_id)]},
        )
        client.list_rounds.return_value = RoundList(
            entries=[completed], total_count=1, limit=200, offset=0
        )
        client.get_round.return_value = RoundDetail(
            **completed.model_dump(), results=[]
        )
        client.create_round.return_value = completed
        client.get_typed_league_settings.side_effect = None
        client.get_typed_league_settings.return_value = LeagueSettingsResponse(
            settings=LeagueSettings(
                round_interval_minutes=10,
                episode_player_pod_llm_spend_limit_usd=10,
            ),
            defaults=LeagueSettingsDefaults(
                episodes_per_round=36, round_interval_minutes=10
            ),
        )
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )

        result = manager.run_once()

        client.create_round.assert_called_once()
        self.assertEqual(result.rounds_created, 0)

    def test_migration_event_replay_uses_stable_idempotency_key(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        competition = client.list_divisions.return_value[0]
        membership = MembershipSummary(
            id="lpm_00000000-0000-0000-0000-000000000041",
            status="submitted",
            is_champion=False,
            division=competition,
            policy_version=PolicyVersionRef(
                id=UUID("00000000-0000-0000-0000-000000000021")
            ),
            player=PlayerRef(
                id="ply_00000000-0000-0000-0000-000000000051", name="Prime Player"
            ),
        )
        client.list_memberships.return_value = [membership]
        client.get_typed_league_settings.side_effect = None
        client.get_typed_league_settings.return_value = LeagueSettingsResponse(
            settings=LeagueSettings(
                round_interval_minutes=10,
                episode_player_pod_llm_spend_limit_usd=10,
            ),
            defaults=LeagueSettingsDefaults(
                episodes_per_round=36, round_interval_minutes=10
            ),
        )
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )
        manager.commissioner.migrate_league = MagicMock(
            return_value=LeagueMigrationResult(
                policy_membership_events=[
                    CommissionerMembershipEventChange(
                        league_policy_membership_id=UUID(
                            "00000000-0000-0000-0000-000000000041"
                        ),
                        to_division_id=UUID("00000000-0000-0000-0000-000000000001"),
                        status="competing",
                        reason="skill gate passed",
                    )
                ]
            )
        )

        manager.run_once()
        manager.run_once()

        keys = [
            call.kwargs["idempotency_key"]
            for call in client.apply_membership_events.call_args_list
        ]
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        self.assertTrue(keys[0].startswith("crewrift-prime-migration-"))

    def test_run_stops_when_platform_rejects_membership_events(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        competition = client.list_divisions.return_value[0]
        membership = MembershipSummary(
            id="lpm_00000000-0000-0000-0000-000000000041",
            status="submitted",
            is_champion=False,
            division=competition,
            policy_version=PolicyVersionRef(
                id=UUID("00000000-0000-0000-0000-000000000021")
            ),
            player=PlayerRef(
                id="ply_00000000-0000-0000-0000-000000000051", name="Prime Player"
            ),
        )
        client.list_memberships.return_value = [membership]
        client.apply_membership_events.return_value = MembershipEventBatchResult(
            applied=False,
            results=[],
        )
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )
        manager.commissioner.migrate_league = MagicMock(
            return_value=LeagueMigrationResult(
                policy_membership_events=[
                    CommissionerMembershipEventChange(
                        league_policy_membership_id=UUID(
                            "00000000-0000-0000-0000-000000000041"
                        ),
                        to_division_id=UUID("00000000-0000-0000-0000-000000000001"),
                        status="competing",
                        reason="skill gate passed",
                    )
                ]
            )
        )

        with self.assertRaisesRegex(
            RuntimeError, "Platform rejected Crewrift Prime membership events"
        ):
            manager.run_once()

        client.create_round.assert_not_called()

    def test_run_scores_publishes_and_completes_terminal_round(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        competition = client.list_divisions.return_value[0]
        membership = MembershipSummary(
            id="lpm_00000000-0000-0000-0000-000000000041",
            status="competing",
            is_champion=True,
            division=competition,
            policy_version=PolicyVersionRef(id=policy_id),
            player=PlayerRef(
                id="ply_00000000-0000-0000-0000-000000000051", name="Prime Player"
            ),
        )
        running = RoundSummary(
            id="round_00000000-0000-0000-0000-000000000061",
            round_number=1,
            commissioner_key="platform",
            status="running",
            division=competition,
            round_config={"entrant_policy_version_ids": [str(policy_id)]},
        )
        client.list_memberships.return_value = [membership]
        client.list_rounds.return_value = RoundList(
            entries=[running], total_count=1, limit=200, offset=0
        )
        client.get_typed_league_settings.side_effect = None
        client.get_typed_league_settings.return_value = LeagueSettingsResponse(
            settings=LeagueSettings(
                round_interval_minutes=10,
                episode_player_pod_llm_spend_limit_usd=10,
            ),
            defaults=LeagueSettingsDefaults(
                episodes_per_round=36, round_interval_minutes=10
            ),
        )
        client.get_round_episodes.return_value = [
            RoundEpisodeResult(
                id="ereq_00000000-0000-0000-0000-000000000071",
                job_index=0,
                variant_id="default",
                seed=7,
                game_config={},
                filler_seats=list(range(1, 8)),
                runtime=RoundEpisodeRuntime(
                    status="completed",
                    policy_version_ids=[policy_id] * 8,
                    scores=[RoundEpisodeScore(policy_version_id=policy_id, score=1)],
                ),
                game_results={
                    "imposter": [1, 1, 0, 0, 0, 0, 0, 0],
                    "crew": [0, 0, 1, 1, 1, 1, 1, 1],
                    "win": [True, True, False, False, False, False, False, False],
                    "scores": [100, 100, 0, 0, 0, 0, 0, 0],
                },
            )
        ]
        client.plan_round.return_value = RoundEpisodePlan(
            strategy="explicit",
            coworld_id="cw_1",
            variant_id="default",
            seat_count=8,
            entrant_policy_version_ids=[policy_id],
            episodes=[],
        )
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )

        result = manager.run_once()

        authored = client.score_authored_round.call_args.kwargs
        self.assertEqual(authored["entries"][0].score, 3)
        self.assertEqual(authored["rule_id"], authored["commissioner_report"].rule_id)
        client.publish_division_leaderboard.assert_called_once()
        client.update_commissioner_state.assert_called_once()
        client.complete_round.assert_called_once_with(running.id)
        self.assertEqual(result.rounds_completed, 1)

    def test_run_scores_frozen_role_entrant_after_membership_drift(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        planned_policy_id = UUID("00000000-0000-0000-0000-000000000021")
        replacement_policy_id = UUID("00000000-0000-0000-0000-000000000022")
        competition, imposters, crew = client.list_divisions.return_value
        client.list_memberships.return_value = [
            MembershipSummary(
                id="lpm_00000000-0000-0000-0000-000000000041",
                status="disqualified",
                substatus="inactive",
                is_champion=False,
                division=crew,
                policy_version=PolicyVersionRef(id=planned_policy_id),
                player=PlayerRef(id="ply_planned", name="Planned Player"),
            ),
            MembershipSummary(
                id="lpm_00000000-0000-0000-0000-000000000042",
                status="competing",
                substatus="active",
                is_champion=True,
                division=competition,
                policy_version=PolicyVersionRef(id=replacement_policy_id),
                player=PlayerRef(id="ply_replacement", name="Replacement Player"),
            ),
        ]
        running = RoundSummary(
            id="round_00000000-0000-0000-0000-000000000061",
            round_number=1,
            commissioner_key="platform",
            status="running",
            division=imposters,
            round_config={"entrant_policy_version_ids": [str(replacement_policy_id)]},
        )
        client.list_rounds.return_value = RoundList(
            entries=[running], total_count=1, limit=200, offset=0
        )
        client.get_typed_league_settings.side_effect = None
        client.get_typed_league_settings.return_value = LeagueSettingsResponse(
            settings=LeagueSettings(
                round_interval_minutes=10,
                episode_player_pod_llm_spend_limit_usd=10,
            ),
            defaults=LeagueSettingsDefaults(
                episodes_per_round=36, round_interval_minutes=10
            ),
        )
        client.get_round_episodes.return_value = [
            RoundEpisodeResult(
                id="ereq_00000000-0000-0000-0000-000000000071",
                job_index=0,
                variant_id="default",
                seed=7,
                game_config={},
                filler_seats=list(range(1, 8)),
                runtime=RoundEpisodeRuntime(
                    status="completed",
                    policy_version_ids=[planned_policy_id] * 8,
                    scores=[
                        RoundEpisodeScore(policy_version_id=planned_policy_id, score=1)
                    ],
                ),
                game_results={
                    "imposter": [1, 1, 0, 0, 0, 0, 0, 0],
                    "crew": [0, 0, 1, 1, 1, 1, 1, 1],
                    "win": [True, True, False, False, False, False, False, False],
                    "scores": [100, 100, 0, 0, 0, 0, 0, 0],
                },
            ),
        ]
        client.plan_round.return_value = RoundEpisodePlan(
            strategy="explicit",
            coworld_id="cw_1",
            variant_id="default",
            seat_count=8,
            entrant_policy_version_ids=[planned_policy_id],
            episodes=[],
        )
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )
        manager.commissioner.migrate_league = MagicMock(
            return_value=LeagueMigrationResult()
        )

        manager.run_once()

        entries = client.score_authored_round.call_args.kwargs["entries"]
        self.assertEqual(
            [entry.policy_version_id for entry in entries], [planned_policy_id]
        )
        self.assertNotIn(
            replacement_policy_id,
            [entry.policy_version_id for entry in entries],
        )

    def test_run_honors_league_pause_for_qualification_and_scheduling(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        client.get_league.return_value = client.get_league.return_value.model_copy(
            update={"rounds_paused_at": datetime.now(UTC)}
        )
        client.get_typed_league_settings.side_effect = None
        client.get_typed_league_settings.return_value = LeagueSettingsResponse(
            settings=LeagueSettings(
                round_interval_minutes=10,
                episode_player_pod_llm_spend_limit_usd=10,
            ),
            defaults=LeagueSettingsDefaults(
                episodes_per_round=36, round_interval_minutes=10
            ),
        )
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )
        manager.commissioner.migrate_league = MagicMock()

        result = manager.run_once()

        manager.commissioner.migrate_league.assert_not_called()
        client.create_round.assert_not_called()
        self.assertEqual(result.qualifications_applied, 0)
        self.assertTrue(result.reconcile.snapshot.rounds_paused)

    def test_run_aborts_round_with_cancelled_episode(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        competition = client.list_divisions.return_value[0]
        client.list_memberships.return_value = [
            MembershipSummary(
                id="lpm_00000000-0000-0000-0000-000000000041",
                status="competing",
                is_champion=True,
                division=competition,
                policy_version=PolicyVersionRef(id=policy_id),
                player=PlayerRef(id="ply_1", name="Prime Player"),
            )
        ]
        running = RoundSummary(
            id="round_00000000-0000-0000-0000-000000000061",
            round_number=1,
            commissioner_key="platform",
            status="running",
            division=competition,
            round_config={"entrant_policy_version_ids": [str(policy_id)]},
        )
        client.list_rounds.return_value = RoundList(
            entries=[running], total_count=1, limit=200, offset=0
        )
        client.get_typed_league_settings.side_effect = None
        client.get_typed_league_settings.return_value = LeagueSettingsResponse(
            settings=LeagueSettings(
                round_interval_minutes=10,
                episode_player_pod_llm_spend_limit_usd=10,
            ),
            defaults=LeagueSettingsDefaults(
                episodes_per_round=36, round_interval_minutes=10
            ),
        )
        client.get_round_episodes.return_value = [
            RoundEpisodeResult(
                id="ereq_00000000-0000-0000-0000-000000000072",
                job_index=2,
                variant_id="default",
                seed=8,
                game_config={},
                filler_seats=[],
                runtime=RoundEpisodeRuntime(
                    status="cancelled",
                    policy_version_ids=[policy_id],
                ),
            ),
            RoundEpisodeResult(
                id="ereq_00000000-0000-0000-0000-000000000071",
                job_index=0,
                variant_id="default",
                seed=7,
                game_config={},
                filler_seats=[],
                runtime=RoundEpisodeRuntime(
                    status="cancelled",
                    policy_version_ids=[policy_id],
                ),
            ),
        ]
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )
        manager.commissioner.migrate_league = MagicMock(
            return_value=LeagueMigrationResult()
        )

        result = manager.run_once()

        client.score_authored_round.assert_not_called()
        client.complete_round.assert_not_called()
        client.abort_round.assert_called_once_with(
            running.id,
            reason="cancelled episode requests at job indexes: 0, 2",
        )
        self.assertEqual(result.rounds_aborted, 1)
        self.assertEqual(result.rounds_completed, 0)

    def test_recent_results_are_loaded_from_completed_round_details(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        policy_id = UUID("00000000-0000-0000-0000-000000000021")
        competition = client.list_divisions.return_value[0]
        completed = RoundSummary(
            id="round_00000000-0000-0000-0000-000000000061",
            round_number=4,
            commissioner_key="container",
            status="completed",
            division=competition,
            round_config={"entrant_policy_version_ids": [str(policy_id)]},
        )
        client.get_round.return_value = RoundDetail(
            **completed.model_dump(),
            results=[
                RoundResultSummary(
                    rank=1,
                    score=3,
                    result_metadata={"points": 3},
                    policy_version=PolicyVersionRef(id=policy_id),
                    player=PlayerRef(id="ply_1", name="Prime Player"),
                )
            ],
        )
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )

        recent_results = manager._recent_results([completed])

        self.assertEqual(len(recent_results), 1)
        self.assertEqual(recent_results[0].division_id, UUID(competition.id[4:]))
        self.assertEqual(recent_results[0].score, 3)

    @patch.dict(
        os.environ,
        {
            "CREWRIFT_PRIME_COMMISSIONER_TOKEN": "cmr_secret",
            "CREWRIFT_PRIME_LEAGUE_ID": "league_00000000-0000-0000-0000-000000000001",
        },
        clear=True,
    )
    def test_default_api_base_includes_observatory_prefix(self) -> None:
        manager = _manager_from_env()

        self.assertEqual(manager.client._base, "https://softmax.com/api/observatory")

    @patch.dict(
        os.environ,
        {
            "CREWRIFT_PRIME_COMMISSIONER_TOKEN": "cmr_secret",
            "CREWRIFT_PRIME_LEAGUE_ID": "league_00000000-0000-0000-0000-000000000001",
            "OBSERVATORY_API_URL": "http://127.0.0.1:8200",
        },
        clear=True,
    )
    def test_explicit_api_base_is_used_verbatim(self) -> None:
        manager = _manager_from_env()

        self.assertEqual(manager.client._base, "http://127.0.0.1:8200")

    def test_durable_worker_reconciles_once_then_runs_rest_cycles(self) -> None:
        manager = MagicMock(spec=CrewriftPrimePlatformManager)
        reconcile = MagicMock()
        cycle = MagicMock()
        cycle.model_dump.return_value = {"rounds_completed": 1}
        manager.reconcile.return_value = reconcile
        manager._run_cycle.return_value = cycle

        with tempfile.TemporaryDirectory() as directory:
            ready_file = Path(directory) / "ready"
            with (
                patch.dict(
                    os.environ,
                    {"CREWRIFT_PRIME_READY_FILE": str(ready_file)},
                ),
                patch(
                    "platform_manager.time.sleep", side_effect=RuntimeError("stop")
                ) as sleep,
                patch("builtins.print") as print_result,
                self.assertRaisesRegex(RuntimeError, "stop"),
            ):
                CrewriftPrimePlatformManager.run_forever(
                    manager, poll_interval_seconds=5
                )

            self.assertTrue(ready_file.exists())

        manager.reconcile.assert_called_once_with()
        manager._run_cycle.assert_called_once_with(reconcile)
        print_result.assert_called_once_with('{"rounds_completed": 1}', flush=True)
        sleep.assert_called_once_with(5)

    def test_prime_image_has_no_websocket_entrypoint(self) -> None:
        dockerfile = Path(__file__).with_name("Dockerfile").read_text()

        self.assertNotIn("app.py", dockerfile)
        self.assertNotIn("uvicorn", dockerfile)
        self.assertNotIn("EXPOSE 8080", dockerfile)
        self.assertIn('CMD ["python", "/app/platform_manager.py", "run"]', dockerfile)


if __name__ == "__main__":
    unittest.main()
