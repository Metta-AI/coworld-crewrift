#!/usr/bin/env python3
"""One-off MANUAL promotion of a Crewrift Prime policy membership.

WHY THIS EXISTS
---------------
Crewrift Prime promotes a submitted policy from ``qualifying`` -> ``competing``
(Competition division) only via the commissioner's ``migrate_league`` /
``qualify_submission`` event loop. When the league's round/qualify pipeline is
stalled (e.g. the scheduler is frozen, or the per-submission qualify pass is
throttled / not firing), a membership can sit in ``qualifying`` indefinitely and
never get evaluated, so it never promotes.

There is NO public API/CLI route for the ``qualifying`` -> ``competing``
transition (``POST .../champion`` requires an ALREADY-competing membership).
This script performs that transition through the platform's own supported write
boundary, :func:`apply_policy_membership_event`, so the membership row and the
audit ``PolicyMembershipEvent`` row stay in sync (no raw SQL, no bypassing the
event log). Setting champion auto-demotes the player's prior champion.

CAVEATS (read before running)
------------------------------
- This BYPASSES the three-skill qualification gate. Only use it to manually
  promote a policy you have independently decided should compete (e.g. when the
  pipeline is stuck and you accept the policy without the gate).
- It writes to the production Observatory DB. It must run in an environment with
  ``STATS_DB_URI`` set to the Observatory database (the app_backend settings).
  Run it from the metta ``app_backend`` venv so imports resolve.
- It demotes the player's current champion in the same league (one champion per
  player per league).

USAGE
-----
    cd <metta>/app_backend
    # STATS_DB_URI must point at the Observatory DB (same env app_backend uses)
    uv run python <path>/manual_promote_membership.py \
        --membership-id lpm_2ba9e7c2-d1a2-407f-9910-e73b611d1441 \
        --champion \
        --reason "Manual promotion: pipeline stalled; interview gate disabled (0.4.15+)"

    # dry run (no write):
    uv run python ... --membership-id lpm_... --dry-run

Find the membership id with:
    coworld memberships --league <league_id> --json   # the lpm_... for your policy
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from sqlmodel import select

from metta.app_backend.database import db_session
from metta.app_backend.v2.models import (
    DIVISION_TYPE_COMPETITION,
    Division,
    LeaguePolicyMembership,
    PolicyMembershipStatus,
)
from metta.app_backend.v2.policy_membership_events import (
    PolicyMembershipEventChange,
    apply_policy_membership_event,
)


def _parse_membership_id(raw: str) -> str:
    """Accept either a bare UUID or the public ``lpm_<uuid>`` form."""
    return raw[len("lpm_"):] if raw.startswith("lpm_") else raw


async def _competition_division_id(session, league_id: UUID) -> UUID:
    """Lowest-level live Competition division for the league (the promote target)."""
    rows = (
        await session.execute(
            select(Division)
            .where(Division.league_id == league_id)
            .where(Division.type == DIVISION_TYPE_COMPETITION)
            .where(Division.archived_at.is_(None))
            .order_by(Division.level)
        )
    ).scalars().all()
    if not rows:
        raise SystemExit(f"No live Competition division for league {league_id}")
    return rows[0].id


async def promote(membership_public_id: str, *, champion: bool, reason: str, dry_run: bool) -> None:
    membership_uuid = UUID(_parse_membership_id(membership_public_id))
    async with db_session() as session:
        membership = (
            await session.execute(
                select(LeaguePolicyMembership)
                .where(LeaguePolicyMembership.id == membership_uuid)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if membership is None:
            raise SystemExit(f"Membership {membership_public_id} not found")

        print(
            f"membership {membership_public_id}: status={membership.status} "
            f"is_champion={membership.is_champion} division={membership.division_id}"
        )

        if membership.status == PolicyMembershipStatus.competing and (membership.is_champion or not champion):
            print("Already in the desired state; nothing to do.")
            return

        competition_division_id = await _competition_division_id(session, membership.league_id)
        moving_division = membership.division_id != competition_division_id

        change = PolicyMembershipEventChange(
            league_policy_membership_id=membership.id,
            from_division_id=membership.division_id,
            to_division_id=competition_division_id if moving_division else None,
            status=PolicyMembershipStatus.competing,
            substatus="champion" if champion else None,
            reason=reason,
            notes=reason,
        )

        print(
            f"-> promote to competing in division {competition_division_id} "
            f"(champion={champion}, move_division={moving_division})"
        )
        if dry_run:
            print("DRY RUN: no write performed.")
            return

        await apply_policy_membership_event(
            session=session,
            membership=membership,
            change=change,
            set_is_champion=True if champion else None,
        )
        await session.commit()
        await session.refresh(membership)
        print(
            f"DONE: status={membership.status} is_champion={membership.is_champion} "
            f"division={membership.division_id}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manually promote a Crewrift Prime membership to competing/champion.")
    parser.add_argument("--membership-id", required=True, help="lpm_<uuid> or bare uuid of the membership to promote")
    parser.add_argument("--champion", action="store_true", help="Also set as champion (demotes the player's prior champion)")
    parser.add_argument("--reason", default="Manual promotion (pipeline stalled).", help="Audit reason recorded on the event")
    parser.add_argument("--dry-run", action="store_true", help="Print the intended change without writing")
    args = parser.parse_args()
    asyncio.run(
        promote(args.membership_id, champion=args.champion, reason=args.reason, dry_run=args.dry_run)
    )


if __name__ == "__main__":
    main()
