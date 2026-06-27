from __future__ import annotations

import pyarrow as pa

from crewrift_event_reporter.events import EventRow, value_json

from .identity import EpisodePlayerRow, SlotIdentity

WAREHOUSE_SCHEMA_VERSION = "crewrift-event-warehouse/v1"

EVENTS_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("episode_id", pa.string()),
        ("slot", pa.int32()),
        ("policy_version", pa.string()),
        ("policy_name", pa.string()),
        ("role", pa.string()),
        ("key", pa.string()),
        ("value", pa.string()),
    ]
)

EPISODE_PLAYERS_SCHEMA = pa.schema(
    [
        ("episode_id", pa.string()),
        ("slot", pa.int32()),
        ("policy_version", pa.string()),
        ("policy_name", pa.string()),
        ("role", pa.string()),
        ("score", pa.float64()),
        ("win", pa.bool_()),
        ("tasks", pa.int32()),
        ("kills", pa.int32()),
        ("identity_source", pa.string()),
    ]
)


def enriched_events_table(
    rows: list[EventRow],
    *,
    episode_id: str,
    identities: dict[int, SlotIdentity],
) -> pa.Table:
    """Build the events fact table for one episode, stamping each row's actor
    slot with its resolved policy identity + role.

    Global rows (``slot < 0``) get null identity columns. Rows are sorted by
    ``(ts, slot)`` so Parquet row-group statistics prune on ``ts`` within a
    partition.
    """
    ordered = sorted(rows, key=lambda row: (row.ts, row.player))
    ts: list[int] = []
    slots: list[int] = []
    policy_versions: list[str | None] = []
    policy_names: list[str | None] = []
    roles: list[str | None] = []
    keys: list[str] = []
    values: list[str] = []
    for row in ordered:
        identity = identities.get(row.player) if row.player >= 0 else None
        ts.append(row.ts)
        slots.append(row.player)
        policy_versions.append(identity.policy_version if identity else None)
        policy_names.append(identity.policy_name if identity else None)
        roles.append(identity.role if identity else None)
        keys.append(row.key)
        values.append(value_json(row.value))
    return pa.table(
        {
            "ts": ts,
            "episode_id": [episode_id] * len(ordered),
            "slot": slots,
            "policy_version": policy_versions,
            "policy_name": policy_names,
            "role": roles,
            "key": keys,
            "value": values,
        },
        schema=EVENTS_SCHEMA,
    )


def episode_players_table(rows: list[EpisodePlayerRow]) -> pa.Table:
    return pa.table(
        {
            "episode_id": [r.episode_id for r in rows],
            "slot": [r.slot for r in rows],
            "policy_version": [r.policy_version for r in rows],
            "policy_name": [r.policy_name for r in rows],
            "role": [r.role for r in rows],
            "score": [r.score for r in rows],
            "win": [r.win for r in rows],
            "tasks": [r.tasks for r in rows],
            "kills": [r.kills for r in rows],
            "identity_source": [r.identity_source for r in rows],
        },
        schema=EPISODE_PLAYERS_SCHEMA,
    )
