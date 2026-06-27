from __future__ import annotations

from dataclasses import dataclass

from crewrift_event_reporter.protocol import PlayerIdentity, ReporterEpisodeInput

from .results import CrewriftResults


@dataclass(frozen=True)
class SlotIdentity:
    """Resolved identity + role for one slot within one episode."""

    policy_version: str | None
    policy_name: str | None
    role: str
    identity_source: str


@dataclass(frozen=True)
class EpisodePlayerRow:
    """One row of the ``episode_players`` dimension table."""

    episode_id: str
    slot: int
    policy_version: str | None
    policy_name: str | None
    role: str
    score: float
    win: bool
    tasks: int
    kills: int
    identity_source: str


def resolve_slot_identity(slot: int, identity: PlayerIdentity | None, results: CrewriftResults) -> SlotIdentity:
    """Resolve a slot to policy identity + role using the standard fallback
    chain: request.player_id -> request.display_name -> results.names -> slot:N.

    ``policy_version`` is only ever the request's ``player_id`` (the stable
    cross-episode key); a name-only or results-only fallback leaves it null so
    that grouping by ``policy_version`` never silently mixes a real version id
    with a display string.
    """
    role = results.role_at(slot)
    if identity is not None and identity.player_id:
        return SlotIdentity(
            policy_version=identity.player_id,
            policy_name=identity.display_name or results.name_at(slot),
            role=role,
            identity_source="request.player_id",
        )
    if identity is not None and identity.display_name:
        return SlotIdentity(
            policy_version=None,
            policy_name=identity.display_name,
            role=role,
            identity_source="request.display_name",
        )
    results_name = results.name_at(slot)
    if results_name is not None:
        return SlotIdentity(
            policy_version=None,
            policy_name=results_name,
            role=role,
            identity_source="results.names",
        )
    return SlotIdentity(policy_version=None, policy_name=None, role=role, identity_source="slot")


def build_episode_players(
    episode: ReporterEpisodeInput,
    results: CrewriftResults,
    episode_id: str,
) -> tuple[list[EpisodePlayerRow], dict[int, SlotIdentity]]:
    """Build the dimension rows for one episode and a slot->identity lookup
    the event enricher uses to stamp each event row.

    Slots come from the union of results (authoritative slot count) and any
    request identities, so a slot present in the request but missing from
    results still resolves.
    """
    identities_by_slot = {p.slot: p for p in episode.players}
    slots = sorted(set(range(results.slot_count())) | set(identities_by_slot))

    rows: list[EpisodePlayerRow] = []
    lookup: dict[int, SlotIdentity] = {}
    for slot in slots:
        identity = resolve_slot_identity(slot, identities_by_slot.get(slot), results)
        lookup[slot] = identity
        rows.append(
            EpisodePlayerRow(
                episode_id=episode_id,
                slot=slot,
                policy_version=identity.policy_version,
                policy_name=identity.policy_name,
                role=identity.role,
                score=results.score_at(slot),
                win=results.win_at(slot),
                tasks=results.int_at(results.tasks, slot),
                kills=results.int_at(results.kills, slot),
                identity_source=identity.identity_source,
            )
        )
    return rows, lookup
