"""Episode artifact tests: SQLite recording, zip assembly, and best-effort upload."""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta

import pytest

from players.crewrift.crewborg import artifact as artifact_module
from players.crewrift.crewborg.artifact import (
    ARTIFACT_URL_ENV,
    SqliteEpisodeRecorder,
    upload_episode_artifact,
)
from players.player_sdk.trace import TraceEvent


def _read_back(database_bytes: bytes) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.deserialize(database_bytes)
    return connection


def _parse_summary_json_line(stderr: str) -> dict:
    """Pull the compact ``summary.json {...}`` payload out of the stderr block."""

    marker = "crewborg artifact: summary.json "
    for line in stderr.splitlines():
        if line.startswith(marker):
            return json.loads(line[len(marker) :])
    raise AssertionError(f"no summary.json line in stderr:\n{stderr}")


def test_recorder_persists_traces_and_metrics_to_sqlite() -> None:
    recorder = SqliteEpisodeRecorder()
    recorder.record(TraceEvent(tick=1, name="mode_entered", data={"mode": "idle"}))
    recorder.record(TraceEvent(tick=7, name="domain.vote_cast", data={"target": "red"}))
    recorder.counter("cyborg.mode.ran", tags={"mode": "idle"})
    recorder.histogram("cyborg.step.latency_ms", 1.5)
    recorder.gauge("cyborg.directive.age_ticks", 3)

    connection = _read_back(recorder.database_bytes())
    traces = connection.execute("SELECT tick, event, data FROM traces ORDER BY seq").fetchall()
    assert [(tick, event) for tick, event, _ in traces] == [(1, "mode_entered"), (7, "domain.vote_cast")]
    assert json.loads(traces[1][2]) == {"target": "red"}

    metrics = connection.execute("SELECT kind, name, value, tags FROM metrics ORDER BY seq").fetchall()
    assert [(kind, name) for kind, name, _, _ in metrics] == [
        ("counter", "cyborg.mode.ran"),
        ("histogram", "cyborg.step.latency_ms"),
        ("gauge", "cyborg.directive.age_ticks"),
    ]
    assert json.loads(metrics[0][3]) == {"mode": "idle"}
    assert metrics[1][2] == 1.5

    summary = recorder.summary()
    assert summary["trace_rows"] == 2
    assert summary["metric_rows"] == 3
    assert summary["first_tick"] == 1
    assert summary["last_tick"] == 7
    assert summary["event_counts"] == {"mode_entered": 1, "domain.vote_cast": 1}
    recorder.close()


def test_recorder_zip_contains_database_summary_and_readme() -> None:
    recorder = SqliteEpisodeRecorder()
    recorder.record(TraceEvent(tick=1, name="domain.phase_change", data={"to": "Playing"}))

    with zipfile.ZipFile(io.BytesIO(recorder.zip_bytes())) as archive:
        assert sorted(archive.namelist()) == ["README.md", "report.html", "summary.json", "trace.db"]
        summary = json.loads(archive.read("summary.json"))
        connection = _read_back(archive.read("trace.db"))
        readme = archive.read("README.md").decode("utf-8")

    assert summary["trace_rows"] == 1
    assert connection.execute("SELECT COUNT(*) FROM traces").fetchone() == (1,)

    # The README documents the artifact for someone unzipping it cold.
    assert "trace.db" in readme
    assert "summary.json" in readme
    assert "README.md" in readme
    assert "sqlite3 trace.db" in readme
    assert "CREATE TABLE traces" in readme
    assert "CREATE TABLE metrics" in readme
    assert "domain.vote_cast" in readme
    assert "policy_agent_{slot}.log" in readme
    recorder.close()


def test_summary_includes_schema_version_and_timestamp() -> None:
    recorder = SqliteEpisodeRecorder()
    summary = recorder.summary()
    assert summary["schema_version"] == artifact_module.SCHEMA_VERSION
    # ISO-8601 UTC timestamp, parseable and timezone-aware.
    generated = datetime.fromisoformat(summary["artifact_generated_at"])
    assert generated.tzinfo is not None
    assert generated.utcoffset() == timedelta(0)
    # No episode section when nothing has been populated.
    assert "episode" not in summary
    recorder.close()


def test_set_episode_info_surfaces_in_summary_and_omits_none() -> None:
    recorder = SqliteEpisodeRecorder()
    recorder.set_episode_info(slot=3, role="imposter", token=None, outcome=None)
    summary = recorder.summary()
    assert summary["episode"] == {"slot": 3, "role": "imposter"}
    # None-valued fields are dropped rather than stored.
    assert "token" not in summary["episode"]
    assert "outcome" not in summary["episode"]
    recorder.close()


def test_episode_info_from_env_parses_slot_and_drops_token(monkeypatch) -> None:
    monkeypatch.delenv("COWORLD_PLAYER_WS_URL", raising=False)
    monkeypatch.setenv("COGAMES_ENGINE_WS_URL", "ws://svc:8080/player?slot=5&token=SECRETTOK")
    info = artifact_module.episode_info_from_env()
    assert info == {"slot": 5}
    # The token is never carried into the metadata.
    assert "SECRETTOK" not in json.dumps(info)
    assert "token" not in info


def test_episode_info_from_ws_url_parses_slot_and_drops_token() -> None:
    info = artifact_module.episode_info_from_ws_url("ws://svc:8080/player?slot=7&token=SECRETTOK")
    assert info == {"slot": 7}
    assert "SECRETTOK" not in json.dumps(info)
    # Blank/None URLs yield nothing, never raise.
    assert artifact_module.episode_info_from_ws_url(None) == {}
    assert artifact_module.episode_info_from_ws_url("   ") == {}
    assert artifact_module.episode_info_from_ws_url("ws://svc:8080/player?token=t") == {}


def test_summary_with_env_slot_excludes_token_anywhere(monkeypatch) -> None:
    monkeypatch.delenv("COWORLD_PLAYER_WS_URL", raising=False)
    monkeypatch.setenv("COGAMES_ENGINE_WS_URL", "ws://svc:8080/player?slot=2&token=SECRETTOK")
    recorder = SqliteEpisodeRecorder()
    recorder.set_episode_info(**artifact_module.episode_info_from_env())
    summary = recorder.summary()
    assert summary["episode"]["slot"] == 2
    # The auth token must not appear anywhere in the serialized summary.
    serialized = json.dumps(summary)
    assert "SECRETTOK" not in serialized
    assert "token" not in serialized
    recorder.close()


def test_episode_info_from_env_empty_when_no_ws_url(monkeypatch) -> None:
    monkeypatch.delenv("COWORLD_PLAYER_WS_URL", raising=False)
    monkeypatch.delenv("COGAMES_ENGINE_WS_URL", raising=False)
    assert artifact_module.episode_info_from_env() == {}
    # And a summary with no info populated cleanly omits the episode key.
    recorder = SqliteEpisodeRecorder()
    assert "episode" not in recorder.summary()
    recorder.close()


def test_recorder_drops_writes_after_close() -> None:
    recorder = SqliteEpisodeRecorder()
    recorder.close()
    # Must not raise even though the connection is gone.
    recorder.record(TraceEvent(tick=1, name="perception", data={}))
    recorder.counter("cyborg.mode.ran")
    recorder.record_position(tick=1)
    recorder.close()


def test_recorder_persists_positions_table() -> None:
    recorder = SqliteEpisodeRecorder()
    recorder.record_position(
        tick=7,
        server_tick=4807,
        self_x=120,
        self_y=90,
        room_id=3,
        mode="normal",
        intent_kind="complete_task",
        held_mask=0x20,
        phase="Playing",
        visible='[{"c":"green","x":140,"y":95}]',
    )
    recorder.record_position(tick=8, phase="Voting")  # camera down: nullable fields

    connection = _read_back(recorder.database_bytes())
    rows = connection.execute(
        "SELECT tick, server_tick, self_x, self_y, room_id, mode, intent_kind,"
        " held_mask, phase, visible FROM positions ORDER BY seq"
    ).fetchall()
    assert rows[0] == (7, 4807, 120, 90, 3, "normal", "complete_task", 0x20, "Playing", '[{"c":"green","x":140,"y":95}]')
    assert rows[1] == (8, None, None, None, None, None, None, None, "Voting", "[]")
    assert json.loads(rows[0][9]) == [{"c": "green", "x": 140, "y": 95}]

    summary = recorder.summary()
    assert summary["position_rows"] == 2
    assert summary["dropped_position_rows"] == 0
    recorder.close()


def test_recorder_caps_position_rows_and_counts_drops(monkeypatch) -> None:
    monkeypatch.setattr(artifact_module, "MAX_ROWS_PER_TABLE", 2)
    recorder = SqliteEpisodeRecorder()
    for tick in range(4):
        recorder.record_position(tick=tick)

    summary = recorder.summary()
    assert summary["position_rows"] == 2
    assert summary["dropped_position_rows"] == 2
    connection = _read_back(recorder.database_bytes())
    assert connection.execute("SELECT COUNT(*) FROM positions").fetchone() == (2,)
    recorder.close()


def test_readme_documents_the_positions_table() -> None:
    readme = artifact_module.ARTIFACT_README
    assert "CREATE TABLE positions" in readme
    assert "server_tick" in readme
    assert "FROM positions" in readme  # a runnable example query
    # The pre-existing documentation is preserved (extend, don't regress).
    assert "CREATE TABLE traces" in readme
    assert "CREATE TABLE metrics" in readme
    assert "policy_agent_{slot}.log" in readme
    # The new player-specific report is documented for someone unzipping cold.
    assert "report.html" in readme


def _build_episode_recorder() -> SqliteEpisodeRecorder:
    """A recorder with a representative crewmate game: positions + domain events + info."""

    recorder = SqliteEpisodeRecorder()
    recorder.set_episode_info(slot=2, role="crewmate", color="green", outcome="crew_wins")
    for tick in range(0, 200, 4):
        recorder.record_position(
            tick=tick,
            self_x=100 + tick,
            self_y=80 + (tick % 60),
            room_id=tick % 4,
            mode="normal" if tick < 120 else "flee",
            intent_kind="complete_task",
            phase="Playing",
            visible="[]",
        )
    recorder.record(TraceEvent(tick=40, name="domain.task_completed", data={"task_index": 0}))
    recorder.record(TraceEvent(tick=90, name="domain.player_died", data={"color": "red", "source": "kill"}))
    recorder.record(
        TraceEvent(tick=100, name="domain.meeting_called", data={"by": "blue", "trigger": "report", "body_color": "red"})
    )
    recorder.record(
        TraceEvent(
            tick=110,
            name="domain.suspicion_snapshot",
            data={"ranking": [{"color": "blue", "p": 0.82}, {"color": "pink", "p": 0.30}]},
        )
    )
    recorder.record(
        TraceEvent(tick=112, name="domain.meeting_vote_selected", data={"target": "blue", "reason": "near body"})
    )
    recorder.record(TraceEvent(tick=115, name="domain.vote_cast", data={"meeting_id": 100}))
    recorder.record(TraceEvent(tick=199, name="domain.game_over", data={"outcome": "crew_wins"}))
    return recorder


def test_zip_contains_self_contained_report_html() -> None:
    recorder = _build_episode_recorder()
    with zipfile.ZipFile(io.BytesIO(recorder.zip_bytes())) as archive:
        assert "report.html" in archive.namelist()
        report = archive.read("report.html").decode("utf-8")

    # Non-empty, an actual HTML document, with the data inlined for offline open.
    assert len(report) > 0
    assert "<html" in report
    assert "const DATA = {" in report
    assert "cdn.jsdelivr.net/npm/chart.js" in report
    # The player's episode context (role/outcome/color) is present when known.
    assert "crewmate" in report
    assert "crew_wins" in report
    assert "green" in report
    recorder.close()


def test_report_payload_summarizes_player_specific_data() -> None:
    recorder = _build_episode_recorder()
    summary = recorder.summary()
    connection = _read_back(recorder.database_bytes())
    payload = artifact_module.build_report_payload(summary, connection)

    assert payload["episode"]["role"] == "crewmate"
    assert payload["stats"]["tasks_completed"] == 1
    assert payload["stats"]["meetings"] == 1
    assert payload["stats"]["votes_cast"] == 1
    # Heatmap binned this game's non-NULL self positions.
    assert payload["heatmap"]["samples"] == 50
    assert payload["heatmap"]["peak"] >= 1
    # Suspicion peaks ranked desc; blue (0.82) leads pink (0.30).
    assert payload["suspicion"]["colors"][0] == "blue"
    assert payload["suspicion"]["peak"][0] == 0.82
    # The vote target + reason come from meeting_vote_selected.
    assert payload["votes"] == [{"tick": 112, "target": "blue", "reason": "near body"}]
    # Meetings list the caller + trigger.
    assert payload["meetings"][0]["by"] == "blue"
    # Markers include the salient domain events for the timeline.
    marker_events = {m["event"] for m in payload["markers"]}
    assert "domain.kill_landed" not in marker_events  # not present this game
    assert {"domain.meeting_called", "domain.player_died", "domain.vote_cast", "domain.game_over"} <= marker_events
    recorder.close()


def test_report_degrades_without_episode_info_or_suspicion() -> None:
    """An imposter-style game (no suspicion, no episode block) still builds a report."""

    recorder = SqliteEpisodeRecorder()
    recorder.record(TraceEvent(tick=1, name="domain.phase_change", data={"to": "Playing"}))
    recorder.record(TraceEvent(tick=5, name="domain.kill_landed", data={"target_color": "pink"}))
    summary = recorder.summary()
    connection = _read_back(recorder.database_bytes())
    payload = artifact_module.build_report_payload(summary, connection)

    assert payload["episode"] == {}
    assert payload["suspicion"]["colors"] == []
    assert payload["stats"]["kills_landed"] == 1
    # Still renders to valid HTML.
    html = artifact_module.build_report_html(summary, connection)
    assert "<html" in html
    assert "const DATA = {" in html
    recorder.close()


def test_report_generation_failure_does_not_break_zip(monkeypatch) -> None:
    """If report generation raises, the zip still assembles with the other 3 entries."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("report builder exploded")

    monkeypatch.setattr(artifact_module, "build_report_html", boom)
    recorder = SqliteEpisodeRecorder()
    recorder.record(TraceEvent(tick=1, name="domain.phase_change", data={"to": "Playing"}))

    with zipfile.ZipFile(io.BytesIO(recorder.zip_bytes())) as archive:
        names = sorted(archive.namelist())
    # report.html is dropped, but the durable artifact survives intact.
    assert names == ["README.md", "summary.json", "trace.db"]
    recorder.close()


def test_upload_skips_when_env_unset_but_still_emits_metadata(monkeypatch, capsys) -> None:
    """No upload URL (today's hosted reality): still emit the metadata to stderr.

    This is the production guarantee — the player pod receives no per-player upload
    URL, so the captured policy-log is the only channel and must carry the value.
    """

    monkeypatch.delenv(ARTIFACT_URL_ENV, raising=False)
    recorder = SqliteEpisodeRecorder()
    recorder.record(TraceEvent(tick=2, name="domain.phase_change", data={"to": "Playing"}))
    recorder.record(TraceEvent(tick=9, name="perception", data={}))

    # No binary artifact was written, so it returns False…
    assert upload_episode_artifact(recorder) is False

    err = capsys.readouterr().err
    # …but the metadata block IS captured, clearly marked and greppable.
    assert "crewborg artifact: ===== episode artifact summary (begin) =====" in err
    assert "crewborg artifact: ===== episode artifact summary (end) =====" in err
    assert "trace_rows=2" in err
    assert "crewborg artifact: no upload URL set" in err
    assert ARTIFACT_URL_ENV in err

    # The compact summary.json line fully reconstructs the artifact metadata.
    summary = _parse_summary_json_line(err)
    assert summary["trace_rows"] == 2
    assert summary["metric_rows"] == 0
    assert summary["first_tick"] == 2
    assert summary["last_tick"] == 9
    assert summary["event_counts"] == {"domain.phase_change": 1, "perception": 1}
    assert summary["zip_bytes"] > 0
    recorder.close()


def test_upload_writes_file_url(monkeypatch, tmp_path, capsys) -> None:
    target = tmp_path / "nested" / "artifact.zip"
    monkeypatch.setenv(ARTIFACT_URL_ENV, target.as_uri())
    recorder = SqliteEpisodeRecorder()
    recorder.record(TraceEvent(tick=3, name="domain.kill_landed", data={}))

    assert upload_episode_artifact(recorder) is True
    with zipfile.ZipFile(target) as archive:
        summary = json.loads(archive.read("summary.json"))
    assert summary["event_counts"] == {"domain.kill_landed": 1}

    err = capsys.readouterr().err
    # Metadata preview before the write: shows the displayed path + contents.
    assert str(target) in err
    assert "trace_rows=" in err
    assert "domain.kill_landed=1" in err
    # file:// writes are confirmed with "wrote" (not "upload OK").
    assert f"crewborg artifact: wrote -> {target}" in err
    assert "bytes in" in err
    recorder.close()


def test_upload_puts_zip_to_https_url(monkeypatch, capsys) -> None:
    monkeypatch.setenv(ARTIFACT_URL_ENV, "https://example.invalid/upload?sig=SECRETSIG123")
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def read(self) -> bytes:
            return b""

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["content_type"] = request.get_header("Content-type")
        captured["body"] = request.data
        return FakeResponse()

    monkeypatch.setattr(artifact_module.urllib.request, "urlopen", fake_urlopen)
    recorder = SqliteEpisodeRecorder()
    recorder.record(TraceEvent(tick=1, name="perception", data={}))

    assert upload_episode_artifact(recorder) is True
    # The actual PUT still uses the full presigned URL (signature intact).
    assert captured["url"] == "https://example.invalid/upload?sig=SECRETSIG123"
    assert captured["method"] == "PUT"
    assert captured["content_type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(captured["body"])) as archive:
        assert "trace.db" in archive.namelist()

    err = capsys.readouterr().err
    # Success line names the host/path…
    assert "crewborg artifact: upload OK -> https://example.invalid/upload" in err
    # …but the presigned signature is redacted, never leaked to the logs.
    assert "SECRETSIG123" not in err
    assert "sig=" not in err
    assert "<redacted>" in err
    recorder.close()


def test_upload_failure_is_swallowed(monkeypatch, capsys) -> None:
    monkeypatch.setenv(ARTIFACT_URL_ENV, "https://example.invalid/upload?sig=SECRETSIG123")

    def failing_urlopen(*_args, **_kwargs):
        raise OSError("network down")

    monkeypatch.setattr(artifact_module.urllib.request, "urlopen", failing_urlopen)
    recorder = SqliteEpisodeRecorder()

    assert upload_episode_artifact(recorder) is False
    err = capsys.readouterr().err
    assert "crewborg artifact: upload FAILED -> https://example.invalid/upload" in err
    assert "network down" in err
    # The signature is redacted even on the failure path.
    assert "SECRETSIG123" not in err
    recorder.close()


def test_upload_skips_oversized_payload(monkeypatch, capsys) -> None:
    monkeypatch.setenv(ARTIFACT_URL_ENV, "https://example.invalid/upload?sig=SECRETSIG123")
    monkeypatch.setattr(artifact_module, "MAX_ARTIFACT_BYTES", 8)

    def must_not_be_called(*_args, **_kwargs):  # pragma: no cover - guard
        raise AssertionError("urlopen must not be called for oversized payloads")

    monkeypatch.setattr(artifact_module.urllib.request, "urlopen", must_not_be_called)
    recorder = SqliteEpisodeRecorder()
    assert upload_episode_artifact(recorder) is False
    err = capsys.readouterr().err
    # Oversize skip states the size vs cap and the masked URL.
    assert "crewborg artifact: upload skipped" in err
    assert "> 8 max" in err
    assert "https://example.invalid/upload" in err
    assert "SECRETSIG123" not in err
    recorder.close()


def test_recorder_caps_rows_and_counts_drops(monkeypatch) -> None:
    monkeypatch.setattr(artifact_module, "MAX_ROWS_PER_TABLE", 2)
    recorder = SqliteEpisodeRecorder()
    for tick in range(4):
        recorder.record(TraceEvent(tick=tick, name="perception", data={}))
        recorder.counter("cyborg.mode.ran")

    summary = recorder.summary()
    assert summary["trace_rows"] == 2
    assert summary["dropped_trace_rows"] == 2
    assert summary["metric_rows"] == 2
    assert summary["dropped_metric_rows"] == 2
    # Tick range still spans dropped events.
    assert summary["last_tick"] == 3
    recorder.close()


def test_tee_sinks_fan_out() -> None:
    from players.crewrift.crewborg.trace import TeeMetricsSink, TeeTraceSink

    recorder_a = SqliteEpisodeRecorder()
    recorder_b = SqliteEpisodeRecorder()
    trace_tee = TeeTraceSink(recorder_a, None, recorder_b)
    metrics_tee = TeeMetricsSink(recorder_a, None, recorder_b)

    trace_tee.record(TraceEvent(tick=1, name="perception", data={}))
    metrics_tee.gauge("cyborg.directive.age_ticks", 2)

    for recorder in (recorder_a, recorder_b):
        summary = recorder.summary()
        assert summary["trace_rows"] == 1
        assert summary["metric_rows"] == 1
        recorder.close()


@pytest.mark.parametrize("invalid", ["", "   "])
def test_upload_treats_blank_url_as_disabled(monkeypatch, capsys, invalid) -> None:
    monkeypatch.setenv(ARTIFACT_URL_ENV, invalid)
    recorder = SqliteEpisodeRecorder()
    assert upload_episode_artifact(recorder) is False
    err = capsys.readouterr().err
    # Blank is treated as no URL: no binary upload, but metadata is still captured.
    assert "crewborg artifact: no upload URL set" in err
    assert "crewborg artifact: summary.json " in err
    recorder.close()


def test_resolve_upload_url_uses_candidate_order(monkeypatch) -> None:
    # Primary env var wins; blank/whitespace is treated as unset.
    monkeypatch.setenv(ARTIFACT_URL_ENV, "  https://host/p?sig=x  ")
    assert artifact_module._resolve_upload_url() == "https://host/p?sig=x"

    monkeypatch.setenv(ARTIFACT_URL_ENV, "   ")
    assert artifact_module._resolve_upload_url() is None

    monkeypatch.delenv(ARTIFACT_URL_ENV, raising=False)
    assert artifact_module._resolve_upload_url() is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://host.example/bucket/slot-0.zip?sig=abc&exp=123", "https://host.example/bucket/slot-0.zip?<redacted>"),
        ("https://host.example/bucket/slot-0.zip", "https://host.example/bucket/slot-0.zip"),
        ("http://host.example:8080/up?x=1", "http://host.example:8080/up?<redacted>"),
        ("file:///tmp/run/artifact.zip", "/tmp/run/artifact.zip"),
    ],
)
def test_display_url_masks_query_signature(url, expected) -> None:
    assert artifact_module._display_url(url) == expected
