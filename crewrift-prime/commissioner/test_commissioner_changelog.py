"""Commissioner changelog: the Prime commissioner self-reports how it works.

The commissioner is a black box to the platform, so the only way the Observatory
can show HOW the commissioner works and WHAT functionality changed is if the
commissioner publishes it. ``describe_division`` attaches
``PRIME_COMMISSIONER_CHANGELOG`` to every division description, and the change
survives the ``describe_division`` -> wire adapter round-trip the platform reads.
"""

from __future__ import annotations

import unittest
from uuid import uuid4

import crewrift_prime_skill_commissioner as comm
from commissioners.common.adapters import describe_division_for_request
from commissioners.common.models import (
    DivisionDescriptionContext,
    DivisionSnapshot,
    LeagueSnapshot,
)
from commissioners.common.protocol import (
    DescribeDivisionRequest,
    DivisionInfo,
    LeagueInfo,
)
from commissioners.common.ruleset_strategy.config import load_ruleset_strategy_config_file

from crewrift_prime_skill_commissioner import CrewriftPrimeSkillCommissioner
from test_observability import _COMPETITION_DIV, _CONFIG_PATH


def _commissioner() -> CrewriftPrimeSkillCommissioner:
    return CrewriftPrimeSkillCommissioner(load_ruleset_strategy_config_file(_CONFIG_PATH))


class CommissionerChangelogTest(unittest.TestCase):
    def test_changelog_is_nonempty_and_newest_first(self) -> None:
        self.assertGreater(len(comm.PRIME_COMMISSIONER_CHANGELOG), 0)
        dates = [entry.date for entry in comm.PRIME_COMMISSIONER_CHANGELOG]
        self.assertEqual(dates, sorted(dates, reverse=True), "changelog must be newest-first")

    def test_describe_division_publishes_changelog(self) -> None:
        commissioner = _commissioner()
        league_id = uuid4()
        ctx = DivisionDescriptionContext(
            league=LeagueSnapshot(id=league_id, commissioner_key="container", commissioner_config=None),
            division=DivisionSnapshot(
                id=_COMPETITION_DIV, name="Competition", level=1, league_id=league_id, type="competition"
            ),
            active_memberships=[],
            recent_rounds=[],
        )
        description = commissioner.describe_division(ctx)
        self.assertEqual(len(description.changelog), len(comm.PRIME_COMMISSIONER_CHANGELOG))
        self.assertEqual(description.changelog[0].title, comm.PRIME_COMMISSIONER_CHANGELOG[0].title)

    def test_changelog_survives_wire_adapter(self) -> None:
        commissioner = _commissioner()
        league_id = uuid4()
        request = DescribeDivisionRequest(
            league=LeagueInfo(id=league_id, commissioner_key="container", commissioner_config=None),
            division=DivisionInfo(id=_COMPETITION_DIV, name="Competition", level=1, type="competition"),
            active_memberships=[],
            recent_rounds=[],
        )
        response = describe_division_for_request(commissioner, request)
        self.assertEqual(len(response.description.changelog), len(comm.PRIME_COMMISSIONER_CHANGELOG))
        entry = response.description.changelog[0]
        self.assertTrue(entry.date)
        self.assertTrue(entry.title)


if __name__ == "__main__":
    unittest.main()
