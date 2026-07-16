from __future__ import annotations

import json
import os
import unittest
import urllib.request
from uuid import UUID
from unittest.mock import MagicMock, patch

from platform_api import (
    CommissionerState,
    CommissionerStateResponse,
    DivisionDeclaration,
    DivisionRef,
    DivisionTopologyResponse,
    LeagueSettings,
    LeagueSettingsDefaults,
    LeagueSettingsResponse,
    LeagueSummary,
    PlatformCommissionerClient,
    RoundList,
)
from platform_manager import (
    CREWRIFT_PRIME_DIVISIONS,
    PLATFORM_CAPABILITY_GAPS,
    CrewriftPrimePlatformManager,
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

    def test_reconcile_does_not_rewrite_matching_settings(self) -> None:
        client = self._client(current_spend_limit=10, final_spend_limit=10)
        manager = CrewriftPrimePlatformManager(
            client, self.league_id, spend_limit_usd=10
        )

        result = manager.reconcile()

        client.replace_league_settings.assert_not_called()
        self.assertFalse(result.settings_updated)

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


if __name__ == "__main__":
    unittest.main()
