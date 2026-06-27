from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# A fake expand_replay helper: it reads the temp .bitreplay file (whose bytes are
# whatever the fixture wrote) as JSON and echoes each entry as a JSONL event row.
# This lets each episode emit a fully controlled synthetic event stream without
# the real Nim helper, mirroring the event-reporter's own test approach.
FAKE_HELPER_SOURCE = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

replay_path = sys.argv[-1]
spec = json.loads(Path(replay_path).read_text())
for row in spec.get("rows", []):
    print(json.dumps(row))
"""


@pytest.fixture
def fake_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    helper = tmp_path / "fake-expand-replay"
    helper.write_text(FAKE_HELPER_SOURCE)
    helper.chmod(0o755)
    monkeypatch.setenv("CREWRIFT_EXPAND_REPLAY", str(helper))
    return helper


def write_episode(
    root: Path,
    *,
    ereq_id: str,
    results: dict[str, Any],
    replay_rows: list[dict[str, Any]],
    players: list[dict[str, Any]],
    status: str = "success",
) -> dict[str, Any]:
    """Write one episode's artifacts and return its ReporterEpisodeInput dict."""
    ep_dir = root / ereq_id
    ep_dir.mkdir(parents=True, exist_ok=True)
    (ep_dir / "results.json").write_text(json.dumps(results))
    (ep_dir / "replay.bitreplay").write_text(json.dumps({"rows": replay_rows}))
    return {
        "episode_request_id": ereq_id,
        "status": status,
        "manifest": {
            "ereq_id": ereq_id,
            "status": status,
            "include": ["results", "replay"],
            "files": {"results": "results.json", "replay": "replay.bitreplay"},
        },
        "artifacts": {
            "results": {"uri": (ep_dir / "results.json").as_uri(), "media_type": "application/json"},
            "replay": {"uri": (ep_dir / "replay.bitreplay").as_uri(), "media_type": "application/octet-stream"},
        },
        "players": players,
    }


def write_request(root: Path, episodes: list[dict[str, Any]], name: str = "report_request.json") -> Path:
    request = {
        "type": "report_request",
        "request_id": "batch-test",
        "report_uri": (root / "PLACEHOLDER.zip").as_uri(),
        "episodes": episodes,
    }
    path = root / name
    path.write_text(json.dumps(request, indent=2))
    return path
