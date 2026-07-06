from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from commissioners.common.models import DivisionSnapshot, MembershipSnapshot
from commissioners.common.protocol import LeagueInfo, MembershipInfo, RoundStart, VariantInfo
from commissioners.common.ruleset_strategy.config import load_ruleset_strategy_config_file
from commissioners.common.ruleset_strategy.entrants import select_rule

from crewrift_prime_skill_commissioner import NUM_SEATS, CrewriftPrimeSkillCommissioner
from test_observability import _CONFIG_PATH, _COMPETITION_DIV, _divisions


def _commissioner() -> CrewriftPrimeSkillCommissioner:
    return CrewriftPrimeSkillCommissioner(load_ruleset_strategy_config_file(_CONFIG_PATH))


def _competition_division(league_id: UUID) -> DivisionSnapshot:
    return DivisionSnapshot(
        id=_COMPETITION_DIV,
        name="Competition",
        level=1,
        league_id=league_id,
        type="competition",
    )


def _competition_memberships(
    league_id: UUID, specs: list[tuple[UUID, str, bool]]
) -> list[MembershipSnapshot]:
    return [
        MembershipSnapshot(
            id=uuid4(),
            league_id=league_id,
            division_id=_COMPETITION_DIV,
            policy_version_id=policy_version_id,
            player_id=player_id,
            status="competing",
            substatus="champion" if is_champion else "active",
            is_champion=is_champion,
        )
        for policy_version_id, player_id, is_champion in specs
    ]


def _round_start(memberships: list[MembershipInfo], round_number: int = 1) -> RoundStart:
    league_id = memberships[0].league_id
    return RoundStart(
        round_id=uuid4(),
        round_number=round_number,
        league=LeagueInfo(id=league_id, commissioner_key="container"),
        divisions=_divisions(),
        memberships=memberships,
        recent_results=[],
        variants=[VariantInfo(id="default", name="Default", game_config={})],
        state={
            "round_config": {
                "current_division_id": str(_COMPETITION_DIV),
                "stages": [
                    {
                        "label": "Round",
                        "self_play": False,
                        "num_episodes": 12,
                        "min_episodes_per_entrant": 12,
                    }
                ],
            }
        },
    )


class CompetitionSeatingConfigTest(unittest.TestCase):
    def test_competition_rule_selects_all_competing_memberships(self) -> None:
        config = load_ruleset_strategy_config_file(_CONFIG_PATH)
        league_id = uuid4()
        division = _competition_division(league_id)
        memberships = _competition_memberships(
            league_id,
            [
                (uuid4(), "ply_a", False),
                (uuid4(), "ply_b", True),
            ],
        )

        rule = select_rule(config, division, memberships)

        self.assertIsNotNone(rule)
        self.assertEqual(rule.id, "competition")
        self.assertIsNotNone(rule.entrants)
        self.assertEqual(rule.entrants.status, "competing")
        self.assertIsNone(rule.entrants.is_champion)


class CompetitionSeatingBehaviorTest(unittest.TestCase):
    def test_competition_seats_distinct_competing_players_without_duplication(self) -> None:
        commissioner = _commissioner()
        league_id = uuid4()
        memberships = _competition_memberships(
            league_id,
            [
                (uuid4(), "ply_0", False),
                (uuid4(), "ply_1", True),
                (uuid4(), "ply_2", False),
                (uuid4(), "ply_3", False),
                (uuid4(), "ply_4", True),
                (uuid4(), "ply_5", False),
                (uuid4(), "ply_6", False),
                (uuid4(), "ply_7", False),
                (uuid4(), "ply_8", False),
                (uuid4(), "ply_9", False),
            ],
        )
        round_start = _round_start(
            [
                MembershipInfo(
                    id=membership.id,
                    league_id=membership.league_id,
                    division_id=membership.division_id,
                    policy_version_id=membership.policy_version_id,
                    player_id=membership.player_id,
                    status=membership.status,
                    substatus=membership.substatus,
                    is_champion=membership.is_champion,
                )
                for membership in memberships
            ]
        )

        schedule = commissioner.schedule_episodes_for_round_start(round_start)

        self.assertTrue(schedule.episodes)
        self.assertEqual(len(schedule.episodes), 12)
        for episode in schedule.episodes:
            self.assertEqual(len(episode.policy_version_ids), NUM_SEATS)
            self.assertEqual(episode.tags["filler_seats"], "")
            self.assertEqual(len(set(episode.policy_version_ids)), NUM_SEATS)
            seated_player_ids = {
                next(m.player_id for m in memberships if m.policy_version_id == policy_id)
                for policy_id in episode.policy_version_ids
            }
            self.assertEqual(len(seated_player_ids), NUM_SEATS)

    def test_competition_seats_non_champions(self) -> None:
        commissioner = _commissioner()
        league_id = uuid4()
        memberships = _competition_memberships(
            league_id,
            [(uuid4(), f"ply_{i}", False) for i in range(NUM_SEATS)],
        )
        round_start = _round_start(
            [
                MembershipInfo(
                    id=membership.id,
                    league_id=membership.league_id,
                    division_id=membership.division_id,
                    policy_version_id=membership.policy_version_id,
                    player_id=membership.player_id,
                    status=membership.status,
                    substatus=membership.substatus,
                    is_champion=membership.is_champion,
                )
                for membership in memberships
            ]
        )

        schedule = commissioner.schedule_episodes_for_round_start(round_start)

        self.assertTrue(schedule.episodes)
        for episode in schedule.episodes:
            self.assertEqual(len(episode.policy_version_ids), NUM_SEATS)
            self.assertEqual(episode.tags["filler_seats"], "")
            self.assertEqual(set(episode.policy_version_ids), {m.policy_version_id for m in memberships})
