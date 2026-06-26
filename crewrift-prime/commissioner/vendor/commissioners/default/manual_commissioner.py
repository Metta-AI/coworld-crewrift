from __future__ import annotations

from commissioners.common.commissioners import BaselineCommissioner
from commissioners.common.models import (
    DivisionCommissionerDescriptionPublic,
    DivisionDescriptionContext,
    RoundSpec,
    ScheduleContext,
)
from commissioners.common.utils import (
    _leaderboard_rules_description,
    _round_structure_description,
)


class ManualCommissioner(BaselineCommissioner):
    """Same scoring as the baseline but never auto-schedules rounds."""

    def schedule_rounds(self, ctx: ScheduleContext) -> list[RoundSpec]:
        return []

    def describe_division(self, ctx: DivisionDescriptionContext) -> DivisionCommissionerDescriptionPublic:
        config = self._scheduling_config(ctx.league.commissioner_config)
        return DivisionCommissionerDescriptionPublic(
            round_schedule="Rounds are created manually.",
            round_structure=_round_structure_description(config.stages),
            leaderboard_rules=_leaderboard_rules_description(),
        )
