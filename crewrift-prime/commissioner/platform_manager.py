"""One-shot Crewrift Prime reconciliation through the platform commissioner API.

Run this command with a league-scoped ``cmr_`` credential. It deliberately does
not poll: callers schedule it at the cadence they own, and every mutation is
state-idempotent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from commissioners.common.models import (
    DivisionDescriptionContext,
    DivisionSnapshot,
    LeagueMigrationContext,
    LeagueSnapshot,
    MembershipSnapshot,
    PolicyMembershipStatus,
    RoundSnapshot,
)
from commissioners.common.protocol import (
    DivisionInfo,
    EpisodeRequest,
    EpisodeResult,
    EpisodeScore,
    LeagueInfo,
    MembershipInfo,
    RecentResult,
    RoundStart,
    VariantInfo,
)
from commissioners.common.ruleset_strategy.config import (
    load_ruleset_strategy_config_file,
)
from commissioners.common.ruleset_strategy.entrants import division_entries, select_rule
from crewrift_prime_skill_commissioner import CrewriftPrimeSkillCommissioner
from platform_api import (
    AuthoredRoundScoreEntry,
    DivisionDeclaration,
    DivisionTopologyResponse,
    ExplicitRoundEpisode,
    MembershipEventEvidence,
    MembershipEventChange,
    PlatformCommissionerClient,
    RoundStage,
    RoundSummary,
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

    kind: Literal["api", "cross-surface", "hosting"]
    capability: str
    blocker: str
    required_change: str


PLATFORM_CAPABILITY_GAPS = [
    PlatformCapabilityGap(
        kind="cross-surface",
        capability="candidate interview launch",
        blocker=(
            "Prime's qualification gate needs a candidate websocket interview server, but "
            "uploaded policy versions declare only their normal game runnable and therefore "
            "have no typed alternate interview entrypoint for the platform to launch."
        ),
        required_change=(
            "Define an uploaded-policy interview-runnable contract, then add a league-bound "
            "commissioner endpoint that creates a short-lived interview session and proxies "
            "the typed question/answer exchange."
        ),
    ),
    PlatformCapabilityGap(
        kind="hosting",
        capability="durable agent invocation and credential delivery",
        blocker=(
            "The API is request/response only: it does not notify or lease work to an external "
            "commissioner, and the Coworld runnable manifest has no private cmr_ credential channel."
        ),
        required_change=(
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
    rounds_paused: bool


class PlatformReconcileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["inspect", "reconcile"]
    snapshot: PlatformLeagueSnapshot
    topology_moves: list[str]
    settings_updated: bool
    remaining_gaps: list[PlatformCapabilityGap]


class PlatformRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qualifications_applied: int
    rounds_created: int
    rounds_dispatched: int
    rounds_aborted: int
    rounds_completed: int
    reconcile: PlatformReconcileResult


def _uuid(public_id: str) -> UUID:
    return UUID(public_id.rsplit("_", 1)[-1])


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
        self.commissioner = CrewriftPrimeSkillCommissioner(
            load_ruleset_strategy_config_file(
                Path(__file__).with_name("crewrift_prime.yaml")
            )
        )
        self.commissioner._xp_client = client

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
        league = self.client.get_league(self.league_id)
        divisions = self.client.list_divisions(self.league_id)
        memberships = self.client.list_memberships(self.league_id)
        rounds = self.client.list_rounds(self.league_id).entries
        league_snapshot = LeagueSnapshot(
            id=_uuid(league.id),
            commissioner_key=league.commissioner_key,
            commissioner_config=None,
        )
        membership_snapshots = [
            MembershipSnapshot(
                id=_uuid(membership.id),
                league_id=_uuid(league.id),
                division_id=_uuid(membership.division.id),
                policy_version_id=membership.policy_version.id,
                player_id=None if membership.player is None else membership.player.id,
                status=PolicyMembershipStatus(membership.status),
                substatus=membership.substatus,
                is_champion=membership.is_champion,
            )
            for membership in memberships
        ]
        round_snapshots = [self._round_snapshot(round_) for round_ in rounds]
        for division in divisions:
            description = self.commissioner.describe_division(
                DivisionDescriptionContext(
                    league=league_snapshot,
                    division=DivisionSnapshot(
                        id=_uuid(division.id),
                        league_id=_uuid(league.id),
                        name=division.name,
                        level=division.level,
                        type=division.type,
                    ),
                    active_memberships=membership_snapshots,
                    recent_rounds=round_snapshots,
                )
            )
            self.client.publish_division_description(division.id, description)
        return self._result(
            mode="reconcile", topology=topology, settings_updated=settings_updated
        )

    def run_once(self) -> PlatformRunResult:
        reconcile = self.reconcile()
        league = self.client.get_league(self.league_id)
        rounds_paused = league.rounds_paused_at is not None
        divisions = self.client.list_divisions(self.league_id)
        memberships = self.client.list_memberships(self.league_id)
        league_uuid = _uuid(league.id)
        division_snapshots = [
            DivisionSnapshot(
                id=_uuid(division.id),
                league_id=league_uuid,
                name=division.name,
                level=division.level,
                type=division.type,
            )
            for division in divisions
        ]
        membership_snapshots = [
            MembershipSnapshot(
                id=_uuid(membership.id),
                league_id=league_uuid,
                division_id=_uuid(membership.division.id),
                policy_version_id=membership.policy_version.id,
                player_id=None if membership.player is None else membership.player.id,
                status=PolicyMembershipStatus(membership.status),
                substatus=membership.substatus,
                is_champion=membership.is_champion,
            )
            for membership in memberships
        ]
        migration_events = []
        if not rounds_paused:
            migration = self.commissioner.migrate_league(
                LeagueMigrationContext(
                    league=LeagueSnapshot(
                        id=league_uuid,
                        commissioner_key=league.commissioner_key,
                        commissioner_config=None,
                    ),
                    divisions=division_snapshots,
                    memberships=membership_snapshots,
                )
            )
            migration_events = migration.policy_membership_events
        membership_public_id = {
            _uuid(membership.id): membership.id for membership in memberships
        }
        if migration_events:
            changes = [
                MembershipEventChange(
                    league_policy_membership_id=membership_public_id[
                        event.league_policy_membership_id
                    ],
                    from_division_id=None
                    if event.from_division_id is None
                    else f"div_{event.from_division_id}",
                    to_division_id=None
                    if event.to_division_id is None
                    else f"div_{event.to_division_id}",
                    status=event.status,
                    substatus=event.substatus,
                    reason=event.reason,
                    notes=event.notes,
                    evidence=[
                        MembershipEventEvidence(
                            type=evidence.type,
                            title=evidence.title,
                            summary=evidence.summary or "",
                            metadata=evidence.metadata,
                        )
                        for evidence in event.evidence
                    ],
                )
                for event in migration_events
            ]
            change_digest = hashlib.sha256(
                json.dumps(
                    [change.model_dump(mode="json") for change in changes],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            membership_result = self.client.apply_membership_events(
                self.league_id,
                changes,
                idempotency_key=f"crewrift-prime-migration-{change_digest}",
            )
            if not membership_result.applied:
                raise RuntimeError(
                    f"Platform rejected Crewrift Prime membership events: {membership_result.results}"
                )
            memberships = self.client.list_memberships(self.league_id)
            membership_snapshots = [
                MembershipSnapshot(
                    id=_uuid(membership.id),
                    league_id=league_uuid,
                    division_id=_uuid(membership.division.id),
                    policy_version_id=membership.policy_version.id,
                    player_id=None
                    if membership.player is None
                    else membership.player.id,
                    status=PolicyMembershipStatus(membership.status),
                    substatus=membership.substatus,
                    is_champion=membership.is_champion,
                )
                for membership in memberships
            ]

        competition = next(
            division for division in divisions if division.name == "Competition"
        )
        competition_snapshot = next(
            division
            for division in division_snapshots
            if division.id == _uuid(competition.id)
        )
        competition_rule = select_rule(
            self.commissioner._config(),
            competition_snapshot,
            membership_snapshots,
        )
        entrant_ids = [
            entry.policy_version_id
            for entry in division_entries(
                competition_snapshot,
                membership_snapshots,
                competition_rule,
            )
        ]
        rounds = self.client.list_rounds(self.league_id).entries
        existing_round_ids = {round_.id for round_ in rounds}
        recent_results = self._recent_results(rounds)
        active_division_ids = {
            round_.division.id
            for round_ in rounds
            if round_.status in {"pending", "claimed", "running"}
        }
        settings = self.client.get_typed_league_settings(self.league_id)
        interval = (
            settings.settings.round_interval_minutes
            or settings.defaults.round_interval_minutes
        )
        episodes_per_round = (
            settings.settings.episodes_per_round
            or settings.defaults.episodes_per_round
            or max(len(entrant_ids), 1)
        )
        slot = int(datetime.now(UTC).timestamp() // (interval * 60))
        created = 0
        if entrant_ids and not rounds_paused:
            for division in divisions:
                if division.id in active_division_ids:
                    continue
                created_round = self.client.create_round(
                    division_id=division.id,
                    idempotency_key=f"crewrift-prime-{division.id}-{slot}",
                    entrant_policy_version_ids=entrant_ids,
                    stages=[RoundStage(num_episodes=episodes_per_round)],
                )
                if created_round.id not in existing_round_ids:
                    created += 1

        state = self.client.get_commissioner_state(self.league_id)
        dispatched = 0
        aborted = 0
        completed = 0
        for round_ in self.client.list_rounds(self.league_id).entries:
            if round_.commissioner_key != "platform":
                continue
            if round_.status not in {"pending", "claimed", "running"}:
                continue
            episodes = self.client.get_round_episodes(round_.id)
            if not episodes:
                round_start = self._round_start(
                    league=league,
                    divisions=divisions,
                    memberships=memberships,
                    round_=round_,
                    state=state.state.root,
                    recent_results=recent_results,
                    entrant_policy_version_ids=[
                        UUID(str(policy_version_id))
                        for policy_version_id in round_.round_config[
                            "entrant_policy_version_ids"
                        ]
                    ],
                )
                schedule = self.commissioner.schedule_episodes_for_round_start(
                    round_start
                )
                self.client.plan_explicit_round(
                    round_.id,
                    [
                        ExplicitRoundEpisode(
                            variant_id=episode.variant_id,
                            seed=episode.seed,
                            policy_version_ids=episode.policy_version_ids,
                            filler_seats=[
                                int(seat)
                                for seat in episode.tags.get("filler_seats", "").split(
                                    ","
                                )
                                if seat
                            ],
                            game_config_overrides=episode.game_config or {},
                        )
                        for episode in schedule.episodes
                    ],
                )
                self.client.dispatch_round(round_.id)
                dispatched += 1
                continue
            cancelled_job_indexes = sorted(
                episode.job_index
                for episode in episodes
                if episode.runtime.status == "cancelled"
            )
            if cancelled_job_indexes:
                reason = "cancelled episode requests at job indexes: " + ", ".join(
                    str(job_index) for job_index in cancelled_job_indexes
                )
                self.client.abort_round(round_.id, reason=reason)
                aborted += 1
                continue
            if any(
                episode.runtime.status not in {"completed", "failed"}
                for episode in episodes
            ):
                continue

            plan = self.client.plan_round(round_.id)
            round_start = self._round_start(
                league=league,
                divisions=divisions,
                memberships=memberships,
                round_=round_,
                state=state.state.root,
                recent_results=recent_results,
                entrant_policy_version_ids=plan.entrant_policy_version_ids,
            )
            scheduled = [
                EpisodeRequest(
                    request_id=str(episode.job_index),
                    variant_id=episode.variant_id,
                    policy_version_ids=episode.runtime.policy_version_ids,
                    game_config=episode.game_config,
                    seed=episode.seed,
                    tags={
                        "filler_seats": ",".join(
                            str(seat) for seat in episode.filler_seats
                        )
                    },
                )
                for episode in episodes
            ]
            results = [
                EpisodeResult(
                    request_id=str(episode.job_index),
                    scores=[
                        EpisodeScore(
                            policy_version_id=policy_version_id,
                            player_id=next(
                                (
                                    membership.player.id
                                    for membership in memberships
                                    if membership.policy_version.id == policy_version_id
                                    and membership.player is not None
                                ),
                                None,
                            ),
                            score=next(
                                (
                                    score.score
                                    for score in episode.runtime.scores
                                    if score.policy_version_id == policy_version_id
                                ),
                                0.0,
                            ),
                        )
                        for policy_version_id in episode.runtime.policy_version_ids
                    ],
                    game_results=episode.game_results,
                )
                for episode in episodes
                if episode.runtime.status == "completed"
            ]
            output = self.commissioner.complete_round_for_round_start(
                round_start,
                results,
                scheduled,
            )
            assert output.observability is not None
            rankings = output.results[0].rankings
            self.client.score_authored_round(
                round_.id,
                rule_id=output.observability.rule_id,
                entries=[
                    AuthoredRoundScoreEntry(
                        policy_version_id=entry.policy_version_id,
                        rank=entry.rank,
                        score=entry.score,
                        episodes_scored=int(
                            entry.result_metadata.get("completed_episode_count", 0)
                        ),
                        result_metadata=entry.result_metadata,
                    )
                    for entry in rankings
                ],
                excluded_filler_seats=sum(
                    len(episode.filler_seats) for episode in episodes
                ),
                round_display=output.round_display,
                commissioner_report=output.observability,
            )
            for leaderboard in output.leaderboards:
                self.client.publish_division_leaderboard(
                    round_.division.id, leaderboard
                )
            next_state = dict(output.state) if isinstance(output.state, dict) else {}
            next_state.pop("round_config", None)
            state = self.client.update_commissioner_state(
                self.league_id,
                version=state.version,
                state=next_state,
            )
            self.client.complete_round(round_.id)
            completed += 1

        return PlatformRunResult(
            qualifications_applied=len(migration_events),
            rounds_created=created,
            rounds_dispatched=dispatched,
            rounds_aborted=aborted,
            rounds_completed=completed,
            reconcile=reconcile,
        )

    @staticmethod
    def _round_snapshot(round_: RoundSummary) -> RoundSnapshot:
        return RoundSnapshot(
            id=_uuid(round_.id),
            public_id=round_.id,
            division_id=_uuid(round_.division.id),
            round_number=round_.round_number,
            status=round_.status,
            round_config=round_.round_config,
            created_at=round_.created_at or datetime.now(UTC),
            started_at=round_.started_at,
            completed_at=round_.completed_at,
        )

    def _recent_results(self, rounds: list[RoundSummary]) -> list[RecentResult]:
        recent_results: list[RecentResult] = []
        for round_ in rounds:
            if round_.status != "completed":
                continue
            detail = self.client.get_round(round_.id)
            for result in detail.results:
                recent_results.append(
                    RecentResult(
                        round_id=_uuid(round_.id),
                        division_id=_uuid(round_.division.id),
                        round_number=round_.round_number,
                        policy_version_id=result.policy_version.id,
                        rank=result.rank,
                        score=result.score,
                    )
                )
                if len(recent_results) == 50:
                    return recent_results
        return recent_results

    @staticmethod
    def _round_start(
        *,
        league,
        divisions,
        memberships,
        round_,
        state,
        recent_results,
        entrant_policy_version_ids,
    ) -> RoundStart:
        durable_state = dict(state) if isinstance(state, dict) else {}
        durable_state["round_config"] = {
            **round_.round_config,
            "current_division_id": str(_uuid(round_.division.id)),
            "entrant_policy_version_ids": [
                str(policy_version_id)
                for policy_version_id in entrant_policy_version_ids
            ],
        }
        membership_by_policy_version_id = {}
        for membership in memberships:
            membership_by_policy_version_id.setdefault(
                membership.policy_version.id, membership
            )
        competition = next(
            division for division in divisions if division.name == "Competition"
        )
        membership_division_id = (
            competition.id
            if round_.division.name in {"Imposters", "Crew"}
            else round_.division.id
        )
        frozen_memberships = [
            membership_by_policy_version_id[policy_version_id]
            for policy_version_id in entrant_policy_version_ids
        ]
        return RoundStart(
            round_id=_uuid(round_.id),
            round_number=round_.round_number,
            league=LeagueInfo(
                id=_uuid(league.id), commissioner_key=league.commissioner_key
            ),
            divisions=[
                DivisionInfo(
                    id=_uuid(division.id),
                    name=division.name,
                    level=division.level,
                    type=division.type,
                )
                for division in divisions
            ],
            memberships=[
                MembershipInfo(
                    id=_uuid(membership.id),
                    league_id=_uuid(league.id),
                    division_id=_uuid(membership_division_id),
                    policy_version_id=membership.policy_version.id,
                    player_id=None
                    if membership.player is None
                    else membership.player.id,
                    status="competing",
                    substatus="active",
                    is_champion=membership.is_champion,
                )
                for membership in frozen_memberships
            ],
            recent_results=recent_results,
            variants=[VariantInfo(id="default", name="default", game_config={})],
            state=durable_state,
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
                rounds_paused=league.rounds_paused_at is not None,
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
    parser.add_argument("mode", choices=("inspect", "reconcile", "run"))
    args = parser.parse_args()
    manager = _manager_from_env()
    if args.mode == "inspect":
        result = manager.inspect()
    elif args.mode == "reconcile":
        result = manager.reconcile()
    else:
        result = manager.run_once()
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
