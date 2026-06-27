from __future__ import annotations

import os
from typing import Any

from .analysis import derive_events
from .bundles import BundleReader
from .events import EventRow, event_zip_bytes, reporter_metadata_row, request_player_manifest_rows
from .protocol import ReportRequest, ReporterEpisodeInput
from .replay import expand_replay_event_rows
from .uri_io import write_uri


def extract_episode_rows(
    episode: ReporterEpisodeInput,
    *,
    request_id: str,
    report_uri: str,
) -> list[EventRow]:
    """Extract the full direct + derived event stream for one episode.

    This is the per-episode core of report building, factored out of
    ``build_and_write_report`` so batch consumers (e.g. the event warehouse)
    can fan it out across many episodes without the zip/write step. The
    ``request_id``/``report_uri`` only seed the ``episode_metadata`` row.
    """
    snapshot_every = int(os.environ.get("CREWRIFT_EVENT_SNAPSHOT_EVERY", "1"))
    helper_path = os.environ.get("CREWRIFT_EXPAND_REPLAY")

    with BundleReader(episode) as bundle:
        bundle.require_success()
        replay_bytes = bundle.read_bytes("replay")

    episode_id = episode.episode_request_id or request_id
    rows = [
        reporter_metadata_row(
            request_id=request_id,
            episode_id=episode_id,
            report_uri=report_uri,
        )
    ]
    rows.extend(request_player_manifest_rows(episode_id=episode_id, players=episode.players))
    replay_rows = expand_replay_event_rows(replay_bytes, helper_path=helper_path, snapshot_every=snapshot_every)
    for row in replay_rows:
        row.value.setdefault("episode_id", episode_id)
    rows.extend(replay_rows)
    rows.extend(derive_events(rows, episode_id=episode_id))
    return rows


def build_and_write_report(request: ReportRequest) -> dict[str, Any]:
    episode = request.episode()
    with BundleReader(episode) as bundle:
        bundle.require_success()
        results = bundle.read_json("results")

    rows = extract_episode_rows(
        episode,
        request_id=request.request_id,
        report_uri=request.report_uri,
    )

    write_uri(request.report_uri, event_zip_bytes(rows), content_type="application/zip")
    return {"players": player_count(results, rows), "events": len(rows)}


def player_count(results: Any, rows: list[Any]) -> int:
    if isinstance(results, dict) and isinstance(results.get("scores"), list):
        return len(results["scores"])
    players = {row.player for row in rows if row.player >= 0}
    return len(players)
