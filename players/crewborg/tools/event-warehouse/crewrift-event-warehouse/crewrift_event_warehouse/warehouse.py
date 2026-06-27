from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

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


def build_warehouse(
    episodes: list[ReporterEpisodeInput],
    out_dir: Path,
    *,
    workers: int | None = None,
) -> BuildSummary:
    """Fan extraction out across the batch, collate the dimension table, and
    write the partitioned dataset + manifest. Synchronous and process-parallel.
    """
    out_dir = Path(out_dir)
    events_dir = out_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    results = _run_episodes(episodes, events_dir, workers=workers)

    player_rows: list[EpisodePlayerRow] = [row for r in results for row in r.player_rows]
    if player_rows:
        pq.write_table(episode_players_table(player_rows), out_dir / "episode_players.parquet")

    summary = _summarize(results, player_rows, out_dir)
    _write_manifest(out_dir, results, summary)
    return summary


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
    results: list[EpisodeResult],
    player_rows: list[EpisodePlayerRow],
    out_dir: Path,
) -> BuildSummary:
    policies = {row.policy_version for row in player_rows if row.policy_version}
    return BuildSummary(
        out_dir=out_dir,
        episodes_total=len(results),
        episodes_ok=sum(1 for r in results if r.status == "ok"),
        episodes_skipped=sum(1 for r in results if r.status == "skipped"),
        episodes_failed=sum(1 for r in results if r.status == "failed"),
        events_written=sum(r.event_count for r in results),
        distinct_policies=len(policies),
    )


def _write_manifest(out_dir: Path, results: list[EpisodeResult], summary: BuildSummary) -> None:
    all_keys = sorted({key for r in results for key in r.keys})
    manifest = {
        "schema_version": WAREHOUSE_SCHEMA_VERSION,
        "episodes_total": summary.episodes_total,
        "episodes_ok": summary.episodes_ok,
        "episodes_skipped": summary.episodes_skipped,
        "episodes_failed": summary.episodes_failed,
        "events_written": summary.events_written,
        "distinct_policies": summary.distinct_policies,
        "event_keys": all_keys,
        "episodes": [
            {
                "episode_id": r.episode_id,
                "status": r.status,
                "event_count": r.event_count,
                "trace_warning": r.trace_warning,
                "message": r.message,
            }
            for r in results
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
