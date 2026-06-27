from __future__ import annotations

import json
from pathlib import Path

from crewrift_event_reporter.protocol import ReporterEpisodeInput

REQUEST_FILENAME = "report_request.json"


def load_batch(inputs: list[Path]) -> list[ReporterEpisodeInput]:
    """Flatten one or more report_request.json files (or directories scanned
    for them) into a single batch of episodes, de-duplicated by
    ``episode_request_id`` so overlapping rounds do not double-count.

    The report_request.json shape is exactly what tmp/round-loop/fetch_round.py
    emits: a top-level ``episodes`` list of presigned/file:// artifact refs with
    per-slot ``players`` identities.
    """
    episodes: dict[str, ReporterEpisodeInput] = {}
    for request_path in _discover_request_files(inputs):
        payload = json.loads(request_path.read_text())
        for raw_episode in payload.get("episodes", []):
            episode = ReporterEpisodeInput.model_validate(raw_episode)
            episodes.setdefault(episode.episode_request_id, episode)
    return list(episodes.values())


def _discover_request_files(inputs: list[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for item in inputs:
        resolved = item.resolve()
        if resolved.is_dir():
            candidates = sorted(resolved.rglob(REQUEST_FILENAME))
        elif resolved.is_file():
            candidates = [resolved]
        else:
            raise FileNotFoundError(f"input path does not exist: {item}")
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                found.append(candidate)
    if not found:
        raise FileNotFoundError(f"no {REQUEST_FILENAME} found under: {[str(i) for i in inputs]}")
    return found
