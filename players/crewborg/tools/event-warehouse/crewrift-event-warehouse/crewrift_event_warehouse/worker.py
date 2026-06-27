from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from crewrift_event_reporter.bundles import BundleReader
from crewrift_event_reporter.protocol import ReporterEpisodeInput
from crewrift_event_reporter.service import extract_episode_rows

from .identity import EpisodePlayerRow, build_episode_players
from .results import CrewriftResults
from .schema import enriched_events_table


@dataclass
class EpisodeResult:
    """Small payload returned by a worker. Event rows are streamed to disk, not
    returned, so batch memory stays bounded by one episode per worker."""

    episode_id: str
    status: str  # "ok" | "skipped" | "failed"
    player_rows: list[EpisodePlayerRow] = field(default_factory=list)
    event_count: int = 0
    keys: list[str] = field(default_factory=list)
    trace_warning: bool = False
    message: str | None = None


def process_episode(episode: ReporterEpisodeInput, events_dir: Path) -> EpisodeResult:
    """Extract, enrich, and write one episode's events. Never raises for an
    expected per-episode failure — it returns a status the orchestrator records
    in the manifest so one bad episode cannot sink the batch."""
    episode_id = episode.episode_request_id
    if episode.status != "success":
        return EpisodeResult(episode_id, "skipped", message=f"episode status={episode.status!r}")
    try:
        with BundleReader(episode) as bundle:
            bundle.require_success()
            results = CrewriftResults.model_validate(bundle.read_json("results"))
        rows = extract_episode_rows(episode, request_id=episode_id, report_uri="warehouse://batch")
    except Exception as exc:  # noqa: BLE001 - per-episode isolation is the contract
        return EpisodeResult(episode_id, "failed", message=f"{type(exc).__name__}: {exc}")

    player_rows, identities = build_episode_players(episode, results, episode_id)
    table = enriched_events_table(rows, episode_id=episode_id, identities=identities)
    keys = _write_partitioned(table, events_dir, episode_id)
    trace_warning = any(row.key == "trace_warning" for row in rows)
    return EpisodeResult(
        episode_id,
        "ok",
        player_rows=player_rows,
        event_count=table.num_rows,
        keys=keys,
        trace_warning=trace_warning,
    )


def _write_partitioned(table: pa.Table, events_dir: Path, episode_id: str) -> list[str]:
    """Write one ``events/key=<k>/<ereq>.parquet`` shard per distinct key.

    Per-episode shard filenames mean parallel workers never write the same file,
    so no cross-process coordination is needed.
    """
    keys = pc.unique(table.column("key")).to_pylist()
    written: list[str] = []
    for key in sorted(keys):
        shard = table.filter(pc.equal(table.column("key"), key))
        partition_dir = events_dir / f"key={key}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(shard, partition_dir / f"{episode_id}.parquet")
        written.append(key)
    return written
