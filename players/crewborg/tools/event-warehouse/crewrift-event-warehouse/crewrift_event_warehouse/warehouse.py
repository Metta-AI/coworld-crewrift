from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from crewrift_event_reporter.protocol import ReporterEpisodeInput

from .identity import EpisodePlayerRow
from .schema import WAREHOUSE_SCHEMA_VERSION, episode_players_table
from .worker import EpisodeResult, process_episode


@dataclass
class BuildSummary:
    out_dir: Path
    episodes_total: int
    episodes_ok: int
    episodes_skipped: int
    episodes_failed: int
    events_written: int
    distinct_policies: int
    episodes_cached: int = 0


def build_warehouse(
    episodes: list[ReporterEpisodeInput],
    out_dir: Path,
    *,
    workers: int | None = None,
) -> BuildSummary:
    """Fan extraction out across the batch, collate the dimension table, and
    write the partitioned dataset + manifest. Synchronous and process-parallel.

    Incremental: episodes already in the output manifest with status "ok" and
    no trace_warning are NOT reprocessed (no replay re-expansion); the manifest
    and episode_players.parquet are merged with the prior build rather than
    overwritten, so repeated builds over a growing episode set only pay for
    the new episodes. Prior "failed"/trace-warned episodes are re-attempted.
    """
    out_dir = Path(out_dir)
    events_dir = out_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    prior = _load_prior_manifest(out_dir)
    prior_entries: dict[str, dict] = {
        e["episode_id"]: e for e in (prior.get("episodes", []) if prior else [])
    }
    cached_ids = {
        eid
        for eid, entry in prior_entries.items()
        if entry.get("status") == "ok" and not entry.get("trace_warning")
    }
    to_process = [ep for ep in episodes if ep.episode_request_id not in cached_ids]
    episodes_cached = len(episodes) - len(to_process)

    # A re-attempted episode's old shards must not survive alongside new ones.
    reprocessed_ids = {ep.episode_request_id for ep in to_process}
    for episode_id in reprocessed_ids & set(prior_entries):
        _remove_episode_shards(events_dir, episode_id)

    results = _run_episodes(to_process, events_dir, workers=workers)

    players_table = _merged_players_table(
        out_dir,
        [row for r in results for row in r.player_rows],
        reprocessed_ids=reprocessed_ids,
    )
    if players_table.num_rows:
        pq.write_table(players_table, out_dir / "episode_players.parquet")

    entries = dict(prior_entries)
    for r in results:
        entries[r.episode_id] = {
            "episode_id": r.episode_id,
            "status": r.status,
            "event_count": r.event_count,
            "trace_warning": r.trace_warning,
            "message": r.message,
        }
    event_keys = sorted(
        set((prior or {}).get("event_keys", [])) | {k for r in results for k in r.keys}
    )

    summary = _summarize(entries, players_table, out_dir, episodes_cached)
    _write_manifest(out_dir, entries, event_keys, summary)
    return summary


def _load_prior_manifest(out_dir: Path) -> dict | None:
    """The prior build's manifest, or None (also on schema mismatch -> full rebuild)."""
    path = out_dir / "manifest.json"
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if manifest.get("schema_version") != WAREHOUSE_SCHEMA_VERSION:
        return None
    return manifest


def _remove_episode_shards(events_dir: Path, episode_id: str) -> None:
    for shard in events_dir.glob(f"key=*/{episode_id}.parquet"):
        shard.unlink()


def _merged_players_table(
    out_dir: Path,
    new_rows: list[EpisodePlayerRow],
    *,
    reprocessed_ids: set[str],
) -> pa.Table:
    """Prior episode_players rows (minus re-attempted episodes) + new rows."""
    tables: list[pa.Table] = []
    path = out_dir / "episode_players.parquet"
    if path.exists():
        prior = pq.read_table(path)
        if reprocessed_ids:
            keep = pc.invert(
                pc.is_in(
                    prior.column("episode_id"),
                    value_set=pa.array(sorted(reprocessed_ids), type=pa.string()),
                )
            )
            prior = prior.filter(keep)
        tables.append(prior)
    if new_rows:
        tables.append(episode_players_table(new_rows))
    if not tables:
        return episode_players_table([])
    return pa.concat_tables(tables)


def _run_episodes(
    episodes: list[ReporterEpisodeInput],
    events_dir: Path,
    *,
    workers: int | None,
) -> list[EpisodeResult]:
    worker_count = workers or os.cpu_count() or 1
    task = partial(process_episode, events_dir=events_dir)
    if worker_count == 1 or len(episodes) <= 1:
        return [task(episode) for episode in episodes]
    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        return list(pool.map(task, episodes))


def _summarize(
    entries: dict[str, dict],
    players_table: pa.Table,
    out_dir: Path,
    episodes_cached: int,
) -> BuildSummary:
    statuses = [e.get("status") for e in entries.values()]
    policies = (
        {v for v in players_table.column("policy_version").to_pylist() if v}
        if players_table.num_rows
        else set()
    )
    return BuildSummary(
        out_dir=out_dir,
        episodes_total=len(entries),
        episodes_ok=statuses.count("ok"),
        episodes_skipped=statuses.count("skipped"),
        episodes_failed=statuses.count("failed"),
        events_written=sum(e.get("event_count") or 0 for e in entries.values()),
        distinct_policies=len(policies),
        episodes_cached=episodes_cached,
    )


def _write_manifest(
    out_dir: Path, entries: dict[str, dict], event_keys: list[str], summary: BuildSummary
) -> None:
    manifest = {
        "schema_version": WAREHOUSE_SCHEMA_VERSION,
        "episodes_total": summary.episodes_total,
        "episodes_ok": summary.episodes_ok,
        "episodes_skipped": summary.episodes_skipped,
        "episodes_failed": summary.episodes_failed,
        "episodes_cached": summary.episodes_cached,
        "events_written": summary.events_written,
        "distinct_policies": summary.distinct_policies,
        "event_keys": event_keys,
        "episodes": sorted(entries.values(), key=lambda e: e["episode_id"]),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
