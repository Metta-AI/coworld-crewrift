from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from crewrift_event_reporter.analysis import AnalysisConfig, derive_events
from crewrift_event_reporter.app import app
from crewrift_event_reporter.events import EventRow, event_zip_bytes, parse_event_jsonl, read_event_zip
from crewrift_event_reporter.protocol import ReportRequest
from crewrift_event_reporter.replay import expand_replay_event_rows
from crewrift_event_reporter.service import build_and_write_report


DEFAULT_RESULTS = {
    "names": ["Alpha", "Bravo"],
    "scores": [10, 5],
    "win": [True, False],
    "tasks": [1, 0],
    "kills": [0, 1],
}


def write_episode_artifacts(
    tmp_path: Path,
    *,
    name: str = "ep",
    results: dict[str, Any] | None = None,
    replay_bytes: bytes = b"fake-replay",
) -> dict[str, str]:
    """Write per-artifact files (as the eval bucket would hold them) and
    return file:// URIs the reporter fetches directly — the post-#15877
    presigned-ref model, not a relayed zip."""
    ep_dir = tmp_path / name
    ep_dir.mkdir(parents=True, exist_ok=True)
    results_path = ep_dir / "results.json"
    results_path.write_text(json.dumps(results or DEFAULT_RESULTS))
    replay_path = ep_dir / "replay.bitreplay"
    replay_path.write_bytes(replay_bytes)
    return {"results": results_path.as_uri(), "replay": replay_path.as_uri()}


def make_episode_input(
    tmp_path: Path,
    *,
    name: str = "ep",
    ereq_id: str = "ereq-test",
    status: str = "success",
    results: dict[str, Any] | None = None,
    players: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    uris = write_episode_artifacts(tmp_path, name=name, results=results)
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
            "results": {"uri": uris["results"], "media_type": "application/json"},
            "replay": {"uri": uris["replay"], "media_type": "application/octet-stream"},
        },
        "players": players or [{"slot": 0, "player_id": "alpha", "display_name": "Alpha"}],
    }


def test_report_request_requires_exactly_one_episode(tmp_path: Path) -> None:
    request = ReportRequest(
        request_id="req",
        report_uri="file:///tmp/events.zip",
        episodes=[make_episode_input(tmp_path)],
    )
    assert request.episode().episode_request_id == "ereq-test"

    with pytest.raises(ValidationError):
        ReportRequest(request_id="req", report_uri="file:///tmp/events.zip", episodes=[])

    with pytest.raises(ValidationError):
        ReportRequest(
            request_id="req",
            report_uri="file:///tmp/events.zip",
            episodes=[make_episode_input(tmp_path, name="a"), make_episode_input(tmp_path, name="b")],
        )


def test_parse_event_jsonl_and_write_single_parquet_zip() -> None:
    rows = parse_event_jsonl(
        '{"ts":2,"player":0,"key":"player_state","value":{"x":1,"y":2}}\n'
        '{"ts":1,"player":-1,"key":"episode_metadata","value":{"source":"test"}}\n'
    )

    payload = event_zip_bytes(rows)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        assert zf.namelist() == ["events.parquet"]

    table = read_event_zip(payload)
    assert table.schema.names == ["ts", "player", "key", "value"]
    assert table.column("ts").to_pylist() == [1, 2]
    assert table.column("key").to_pylist() == ["episode_metadata", "player_state"]


def test_expand_replay_invokes_current_jsonl_contract(tmp_path: Path) -> None:
    args_path = tmp_path / "args.json"
    helper = tmp_path / "fake-expand-replay"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"Path({str(args_path)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'ts': 0, 'player': -1, 'key': 'trace_complete', 'value': {'complete': True}}))\n"
    )
    helper.chmod(0o755)

    rows = expand_replay_event_rows(b"fake-replay", helper_path=str(helper), snapshot_every=7)

    assert [row.key for row in rows] == ["trace_complete"]
    args = json.loads(args_path.read_text())
    assert args[:4] == ["--format", "jsonl", "--snapshot-every", "7"]
    assert args[-1].endswith(".bitreplay")


def test_derive_events_adds_proximity_following_routes_tasks_and_body_context() -> None:
    rows = [
        EventRow(ts=0, player=0, key="player_state", value={"x": 0, "y": 0, "alive": True, "phase": "Playing"}),
        EventRow(ts=0, player=1, key="player_state", value={"x": 20, "y": 0, "alive": True, "phase": "Playing"}),
        EventRow(ts=8, player=0, key="player_state", value={"x": 8, "y": 0, "alive": True, "phase": "Playing"}),
        EventRow(ts=8, player=1, key="player_state", value={"x": 28, "y": 0, "alive": True, "phase": "Playing"}),
        EventRow(ts=16, player=0, key="player_state", value={"x": 16, "y": 0, "alive": True, "phase": "Playing"}),
        EventRow(ts=16, player=1, key="player_state", value={"x": 36, "y": 0, "alive": True, "phase": "Playing"}),
        EventRow(ts=24, player=0, key="player_state", value={"x": 24, "y": 0, "alive": True, "phase": "Playing"}),
        EventRow(ts=24, player=1, key="player_state", value={"x": 44, "y": 0, "alive": True, "phase": "Playing"}),
        EventRow(ts=0, player=0, key="left_room", value={"room": "Bridge", "phase": "Playing"}),
        EventRow(ts=24, player=0, key="entered_room", value={"room": "Storage", "phase": "Playing"}),
        EventRow(ts=4, player=0, key="started_task", value={"task": 2, "phase": "Playing"}),
        EventRow(ts=20, player=0, key="completed_task", value={"task": 2, "phase": "Playing"}),
        EventRow(ts=8, player=2, key="body_state", value={"victim_slot": 2, "x": 40, "y": 0, "room": "Storage"}),
        EventRow(ts=16, player=2, key="body_state", value={"victim_slot": 2, "x": 40, "y": 0, "room": "Storage"}),
        EventRow(ts=24, player=2, key="body_state", value={"victim_slot": 2, "x": 40, "y": 0, "room": "Storage"}),
    ]

    derived = derive_events(
        rows,
        episode_id="ereq-test",
        config=AnalysisConfig(near_distance=24, body_distance=20, group_distance=30, min_interval_ticks=8),
    )
    keys = {row.key for row in derived}

    assert "proximity_interval" in keys
    assert "following_interval" in keys
    assert "headed_to" in keys
    assert "arrived_at" in keys
    assert "task_attempt" in keys
    assert "near_body_interval" in keys


def test_derive_events_uses_measured_interval_boundaries() -> None:
    rows: list[EventRow] = []
    for ts in range(5):
        player_1_x = 3 if ts < 4 else 20
        rows.extend(
            [
                EventRow(
                    ts=ts,
                    player=0,
                    key="player_state",
                    value={"x": 0, "y": 0, "alive": True, "phase": "Playing"},
                ),
                EventRow(
                    ts=ts,
                    player=1,
                    key="player_state",
                    value={"x": player_1_x, "y": 0, "alive": True, "phase": "Playing"},
                ),
                EventRow(
                    ts=ts,
                    player=2,
                    key="body_state",
                    value={"victim_slot": 2, "x": 1, "y": 0, "room": "Storage"},
                ),
            ]
        )

    derived = derive_events(
        rows,
        episode_id="ereq-test",
        config=AnalysisConfig(near_distance=5, body_distance=5, group_distance=5, min_interval_ticks=1),
    )

    proximity = next(row for row in derived if row.key == "proximity_interval")
    assert proximity.value["tick_start"] == 0
    assert proximity.value["tick_end"] == 4
    assert proximity.value["last_observed_tick"] == 3
    assert proximity.value["duration_ticks"] == 4
    assert proximity.value["boundary_precision"] == "exact"
    assert proximity.value["ended_by"] == "separation"

    near_body = next(row for row in derived if row.key == "near_body_interval" and row.player == 1)
    assert near_body.value["tick_start"] == 0
    assert near_body.value["tick_end"] == 4
    assert near_body.value["last_observed_tick"] == 3
    assert near_body.value["duration_ticks"] == 4
    assert near_body.value["boundary_precision"] == "exact"
    assert near_body.value["ended_by"] == "body_or_player_left_range"


def test_build_and_write_report_outputs_parquet_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    episode = make_episode_input(tmp_path, ereq_id="episode-one")
    output = tmp_path / "events.zip"

    def fake_expand(replay_bytes: bytes, *, helper_path: str | None = None, snapshot_every: int = 1) -> list[EventRow]:
        assert replay_bytes == b"fake-replay"
        assert snapshot_every == 1
        return [
            EventRow(ts=0, player=-1, key="episode_metadata", value={"source": "replay"}),
            EventRow(ts=1, player=0, key="player_state", value={"x": 0, "y": 0, "alive": True}),
            EventRow(ts=1, player=1, key="player_state", value={"x": 10, "y": 0, "alive": True}),
        ]

    monkeypatch.setattr("crewrift_event_reporter.service.expand_replay_event_rows", fake_expand)

    result = build_and_write_report(
        ReportRequest(request_id="req", report_uri=output.as_uri(), episodes=[episode])
    )

    assert result["players"] == 2
    table = read_event_zip(output.read_bytes())
    assert table.schema.names == ["ts", "player", "key", "value"]
    payload = table.to_pydict()
    assert "episode_metadata" in payload["key"]
    assert "player_manifest" in payload["key"]
    player_state_index = payload["key"].index("player_state")
    player_state_value = json.loads(payload["value"][player_state_index])
    assert player_state_value["episode_id"] == "episode-one"


def test_zlib_encoded_replay_ref_is_decompressed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replays are zlib-at-rest in the eval bucket; the reporter must
    decompress refs marked encoding=zlib before handing bytes to the helper
    (PR #15877). A regression here silently feeds compressed bytes to the
    replay expander."""
    import zlib

    ep_dir = tmp_path / "ep"
    ep_dir.mkdir()
    (ep_dir / "results.json").write_text(json.dumps(DEFAULT_RESULTS))
    raw_replay = b"raw-replay-payload"
    (ep_dir / "replay.bitreplay.z").write_bytes(zlib.compress(raw_replay))
    output = tmp_path / "events.zip"

    episode = {
        "episode_request_id": "ereq-z",
        "status": "success",
        "manifest": {
            "ereq_id": "ereq-z",
            "status": "success",
            "include": ["results", "replay"],
            "files": {"results": "results.json", "replay": "replay.bitreplay.z"},
        },
        "artifacts": {
            "results": {"uri": (ep_dir / "results.json").as_uri(), "media_type": "application/json"},
            "replay": {
                "uri": (ep_dir / "replay.bitreplay.z").as_uri(),
                "media_type": "application/octet-stream",
                "encoding": "zlib",
            },
        },
        "players": [],
    }

    seen: dict[str, bytes] = {}

    def fake_expand(replay_bytes: bytes, *, helper_path: str | None = None, snapshot_every: int = 1) -> list[EventRow]:
        seen["replay"] = replay_bytes
        return [EventRow(ts=1, player=0, key="player_state", value={"x": 0, "y": 0, "alive": True})]

    monkeypatch.setattr("crewrift_event_reporter.service.expand_replay_event_rows", fake_expand)
    build_and_write_report(ReportRequest(request_id="req", report_uri=output.as_uri(), episodes=[episode]))
    assert seen["replay"] == raw_replay


def test_failed_episode_is_rejected(tmp_path: Path) -> None:
    """A failed episode must not be turned into a report — require_success
    raises, the websocket surfaces report_failed, and no output is written."""
    episode = make_episode_input(tmp_path, status="failed")
    output = tmp_path / "events.zip"
    with pytest.raises(RuntimeError, match="status='failed'"):
        build_and_write_report(ReportRequest(request_id="req", report_uri=output.as_uri(), episodes=[episode]))
    assert not output.exists()


def test_websocket_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    episode = make_episode_input(tmp_path)
    output = tmp_path / "events.zip"

    monkeypatch.setattr(
        "crewrift_event_reporter.service.expand_replay_event_rows",
        lambda replay_bytes, *, helper_path=None, snapshot_every=1: [
            EventRow(ts=1, player=0, key="player_state", value={"x": 0, "y": 0, "alive": True})
        ],
    )

    client = TestClient(app)
    with client.websocket_connect("/reporter") as websocket:
        assert websocket.receive_json()["type"] == "reporter_ready"
        websocket.send_json(
            {
                "type": "report_request",
                "request_id": "req",
                "episodes": [episode],
                "report_uri": output.as_uri(),
            }
        )
        assert websocket.receive_json()["type"] == "report_started"
        finished = websocket.receive_json()

    assert finished["type"] == "report_finished"
    assert output.exists()


def test_websocket_rejects_legacy_bundle_uris_shape(tmp_path: Path) -> None:
    """Guard the breaking change: a backend still speaking the old
    episode_bundle_uris wire shape must be cleanly rejected (report_failed),
    not silently mishandled."""
    output = tmp_path / "events.zip"
    client = TestClient(app)
    with client.websocket_connect("/reporter") as websocket:
        assert websocket.receive_json()["type"] == "reporter_ready"
        websocket.send_json(
            {
                "type": "report_request",
                "request_id": "req",
                "episode_bundle_uris": ["file:///tmp/old.zip"],
                "report_uri": output.as_uri(),
            }
        )
        failed = websocket.receive_json()
    assert failed["type"] == "report_failed"
    assert not output.exists()
