from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from commissioners.common.models import DivisionSnapshot, MembershipSnapshot, PolicyPoolEntry
from commissioners.common.ruleset_strategy.config import RulesetStrategyCommissionerConfig, DivisionRule, default_entrant_selector


def division_entries(
    division: DivisionSnapshot,
    memberships: Iterable[MembershipSnapshot],
    rule: DivisionRule | None,
) -> list[PolicyPoolEntry]:
    selector = rule.entrants if rule is not None and rule.entrants is not None else default_entrant_selector(division)
    entries: list[PolicyPoolEntry] = []
    seen: set[UUID] = set()
    for membership in memberships:
        if membership.division_id != division.id or not selector.matches(membership):
            continue
        if membership.policy_version_id in seen:
            continue
        seen.add(membership.policy_version_id)
        entries.append(
            PolicyPoolEntry(
                pool_id=division.id,
                policy_version_id=membership.policy_version_id,
                player_id=membership.player_id,
                seed_order=len(entries),
            )
        )
    return entries


def select_rule(
    config: RulesetStrategyCommissionerConfig,
    division: DivisionSnapshot,
    memberships: Iterable[MembershipSnapshot],
    *,
    require_minimum: bool = False,
) -> DivisionRule | None:
    fallback_rule: DivisionRule | None = None
    for rule in config.division_rules:
        if not rule.match.matches(division):
            continue
        entrants = division_entries(division, memberships, rule)
        if require_minimum and len(entrants) < rule.minimum_entrants:
            continue
        if entrants or rule.entrants is None:
            return rule
        if fallback_rule is None:
            fallback_rule = rule
    if fallback_rule is not None and not require_minimum:
        return fallback_rule
    if require_minimum:
        entrants = division_entries(division, memberships, None)
        if len(entrants) < 1:
            return None
    return None
