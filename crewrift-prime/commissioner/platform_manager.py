"""One-shot Crewrift Prime reconciliation through the platform commissioner API.

Run this command with a league-scoped ``cmr_`` credential. It deliberately does
not poll: callers schedule it at the cadence they own, and every mutation is
state-idempotent. The live container callback remains responsible for Prime's
custom role-pinned seating, results-JSON scoring, and qualification until those
capabilities exist in the platform API.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict

from platform_api import (
    DivisionDeclaration,
    DivisionTopologyResponse,
    PlatformCommissionerClient,
)
from xp_request_client import DEFAULT_API_BASE, _observatory_base

COMMISSIONER_TOKEN_ENV = "CREWRIFT_PRIME_COMMISSIONER_TOKEN"
LEAGUE_ID_ENV = "CREWRIFT_PRIME_LEAGUE_ID"

CREWRIFT_PRIME_DIVISIONS = [
    DivisionDeclaration(name="Competition", level=1, type="competition"),
    DivisionDeclaration(name="Imposters", level=2, type="competition"),
    DivisionDeclaration(name="Crew", level=3, type="competition"),
]


class PlatformCapabilityGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    blocker: str
    required_api_change: str


PLATFORM_CAPABILITY_GAPS = [
    PlatformCapabilityGap(
        capability="qualification",
        blocker=(
            "Commissioner tokens cannot create/read experience requests or fetch their "
            "results artifacts."
        ),
        required_api_change=(
            "Add league-bound cmr_ access to POST/GET /v2/experience-requests, "
            "GET /v2/experience-requests/{id}/episodes, and the episode-request results artifact."
        ),
    ),
    PlatformCapabilityGap(
        capability="configured filler seating",
        blocker=(
            "GET /v2/leagues/{league_id}/filler-policies is team-only and the generic "
            "planner duplicates real entrants instead of seating configured fillers."
        ),
        required_api_change=(
            "Allow league-bound filler-policy reads and teach episodes:plan to resolve and "
            "persist configured filler policies."
        ),
    ),
    PlatformCapabilityGap(
        capability="role-pinned Competition/Imposters/Crew rounds",
        blocker=(
            "episodes:plan cannot select a variant, provide per-episode game_config/slot roles, "
            "or submit an explicit validated seating plan."
        ),
        required_api_change=(
            "Expose league Coworld variants and add typed role/slot overrides or an explicit "
            "episode-plan write validated by the platform."
        ),
    ),
    PlatformCapabilityGap(
        capability="Prime scoring and leaderboard",
        blocker=(
            "round score supports only mean/ewma scalar scores; Prime needs per-slot results JSON, "
            "void-game exclusion, 3x imposter wins, 1x crew wins, and its player-collapsed "
            "win-rate board."
        ),
        required_api_change=(
            "Add platform-owned Crewrift scoring/ranking rules and persist their typed trace and "
            "leaderboard views."
        ),
    ),
    PlatformCapabilityGap(
        capability="commissioner-authored reports and division guidance",
        blocker=(
            "The commissioner surface can read round reports/logs and division descriptions, "
            "but cannot write Prime's calculation trace, rendered report, leaderboard payload, "
            "or player-facing division description/changelog."
        ),
        required_api_change=(
            "Add typed commissioner writes for round reports/leaderboards and division "
            "description/changelog, bound to the token's league."
        ),
    ),
    PlatformCapabilityGap(
        capability="durable agent invocation and credential delivery",
        blocker=(
            "The API is request/response only: it does not notify or lease work to an external "
            "commissioner, and the Coworld runnable manifest has no private cmr_ credential channel."
        ),
        required_api_change=(
            "Provide a platform-owned commissioner worker/lease or event webhook plus secure "
            "league-token delivery, so the one-shot agent does not require an operator scheduler."
        ),
    ),
]


class PlatformLeagueSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    league_id: str
    league_name: str
    commissioner_key: str
    division_names: list[str]
    membership_count: int
    round_count: int
    commissioner_state_version: int
    spend_limit_usd: float | None


class PlatformReconcileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["inspect", "reconcile"]
    snapshot: PlatformLeagueSnapshot
    topology_moves: list[str]
    settings_updated: bool
    remaining_gaps: list[PlatformCapabilityGap]


class CrewriftPrimePlatformManager:
    def __init__(
        self,
        client: PlatformCommissionerClient,
        league_id: str,
        *,
        spend_limit_usd: float,
    ) -> None:
        self.client = client
        self.league_id = league_id
        self.spend_limit_usd = spend_limit_usd

    def inspect(self) -> PlatformReconcileResult:
        return self._result(mode="inspect", topology=None, settings_updated=False)

    def reconcile(self) -> PlatformReconcileResult:
        topology = self.client.declare_divisions(
            self.league_id, CREWRIFT_PRIME_DIVISIONS
        )
        current = self.client.get_typed_league_settings(self.league_id)
        desired_settings = current.settings.model_copy(
            update={"episode_player_pod_llm_spend_limit_usd": self.spend_limit_usd}
        )
        settings_updated = desired_settings != current.settings
        if settings_updated:
            self.client.replace_league_settings(self.league_id, desired_settings)
        return self._result(
            mode="reconcile", topology=topology, settings_updated=settings_updated
        )

    def _result(
        self,
        *,
        mode: Literal["inspect", "reconcile"],
        topology: DivisionTopologyResponse | None,
        settings_updated: bool,
    ) -> PlatformReconcileResult:
        league = self.client.get_league(self.league_id)
        divisions = self.client.list_divisions(self.league_id)
        memberships = self.client.list_memberships(self.league_id)
        rounds = self.client.list_rounds(self.league_id)
        state = self.client.get_commissioner_state(self.league_id)
        settings = self.client.get_typed_league_settings(self.league_id)
        return PlatformReconcileResult(
            mode=mode,
            snapshot=PlatformLeagueSnapshot(
                league_id=league.id,
                league_name=league.name,
                commissioner_key=league.commissioner_key,
                division_names=[
                    division.name
                    for division in sorted(divisions, key=lambda row: row.level)
                ],
                membership_count=len(memberships),
                round_count=rounds.total_count,
                commissioner_state_version=state.version,
                spend_limit_usd=settings.settings.episode_player_pod_llm_spend_limit_usd,
            ),
            topology_moves=[]
            if topology is None
            else [f"{move.action}:{move.name}" for move in topology.moves],
            settings_updated=settings_updated,
            remaining_gaps=PLATFORM_CAPABILITY_GAPS,
        )


def _manager_from_env() -> CrewriftPrimePlatformManager:
    token = os.environ[COMMISSIONER_TOKEN_ENV].strip()
    if not token.startswith("cmr_"):
        raise ValueError(f"{COMMISSIONER_TOKEN_ENV} must be a league-scoped cmr_ token")
    league_id = os.environ[LEAGUE_ID_ENV].strip()
    base = _observatory_base(DEFAULT_API_BASE, os.environ.get("OBSERVATORY_API_URL"))
    spend_limit = float(os.environ.get("CREWRIFT_PRIME_MAX_SPEND_PER_POD_USD", "10"))
    return CrewriftPrimePlatformManager(
        PlatformCommissionerClient(base=base, token=token),
        league_id,
        spend_limit_usd=spend_limit,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "reconcile"))
    args = parser.parse_args()
    manager = _manager_from_env()
    result = manager.inspect() if args.mode == "inspect" else manager.reconcile()
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
