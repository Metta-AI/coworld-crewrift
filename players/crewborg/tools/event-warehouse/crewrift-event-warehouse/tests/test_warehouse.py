from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest

from conftest import write_episode, write_request

from crewrift_event_warehouse.identity import build_episode_players, resolve_slot_identity
from crewrift_event_warehouse.inputs import load_batch
from crewrift_event_warehouse.results import CrewriftResults
from crewrift_event_warehouse.warehouse import build_warehouse

from crewrift_event_reporter.protocol import PlayerIdentity, ReporterEpisodeInput


# Two episodes where the same policy version plays different roles across games,
# so cross-episode, by-policy, by-role queries are exercised end to end.
def _two_episode_batch(root: Path) -> list[dict]:
    # episode one: policy "polA:v1" (slot 0) is crew and enters Bridge then Storage.
    ep1 = write_episode(
        root,
        ereq_id="ereq-1",
        results={
            "scores": [10, 5],
            "names": ["A-name", "B-name"],
            "win": [True, False],
            "tasks": [2, 0],
            "kills": [0, 1],
            "crew": [1, 0],
            "imposter": [0, 1],
        },
        replay_rows=[
            {"ts": 0, "player": 0, "key": "entered_room", "value": {"room": "Bridge", "phase": "Playing"}},
            {"ts": 5, "player": 0, "key": "left_room", "value": {"room": "Bridge", "phase": "Playing"}},
            {"ts": 9, "player": 0, "key": "entered_room", "value": {"room": "Storage", "phase": "Playing"}},
            {"ts": 9, "player": 1, "key": "entered_room", "value": {"room": "Storage", "phase": "Playing"}},
        ],
        players=[
            {"slot": 0, "player_id": "polA-v1", "display_name": "polA:v1"},
            {"slot": 1, "player_id": "polB-v1", "display_name": "polB:v1"},
        ],
    )
    # episode two: same polA:v1 (now slot 1) is imposter and enters Bridge again.
    ep2 = write_episode(
        root,
        ereq_id="ereq-2",
        results={
            "scores": [3, 12],
            "names": ["C-name", "A-name"],
            "win": [False, True],
            "tasks": [1, 0],
            "kills": [0, 2],
            "crew": [1, 0],
            "imposter": [0, 1],
        },
        replay_rows=[
            {"ts": 0, "player": 1, "key": "entered_room", "value": {"room": "Bridge", "phase": "Playing"}},
        ],
        players=[
            {"slot": 0, "player_id": "polC-v1", "display_name": "polC:v1"},
            {"slot": 1, "player_id": "polA-v1", "display_name": "polA:v1"},
        ],
    )
    return [ep1, ep2]


def test_resolve_identity_prefers_player_id_and_reads_role_from_results() -> None:
    results = CrewriftResults(scores=[1, 1], crew=[1, 0], imposter=[0, 1], names=["x", "y"])
    crew = resolve_slot_identity(0, PlayerIdentity(slot=0, player_id="p1", display_name="P:v1"), results)
    assert crew.policy_version == "p1"
    assert crew.role == "crew"
    assert crew.identity_source == "request.player_id"

    imposter = resolve_slot_identity(1, PlayerIdentity(slot=1, player_id="p2", display_name="Q:v1"), results)
    assert imposter.role == "imposter"


def test_resolve_identity_fallbacks_leave_policy_version_null() -> None:
    results = CrewriftResults(scores=[1], names=["from-results"], crew=[1], imposter=[0])
    name_only = resolve_slot_identity(0, PlayerIdentity(slot=0, display_name="D"), results)
    assert name_only.policy_version is None
    assert name_only.policy_name == "D"
    assert name_only.identity_source == "request.display_name"

    no_request = resolve_slot_identity(0, None, results)
    assert no_request.policy_version is None
    assert no_request.policy_name == "from-results"
    assert no_request.identity_source == "results.names"


def test_load_batch_dedupes_by_episode_id(tmp_path: Path) -> None:
    episodes = _two_episode_batch(tmp_path)
    write_request(tmp_path, episodes, name="report_request.json")
    # a second request that overlaps on ereq-1 must not double-count.
    overlap_dir = tmp_path / "round2"
    overlap_dir.mkdir()
    write_request(overlap_dir, [episodes[0]], name="report_request.json")

    loaded = load_batch([tmp_path])
    assert sorted(e.episode_request_id for e in loaded) == ["ereq-1", "ereq-2"]


def test_build_warehouse_partitioned_output_and_manifest(tmp_path: Path, fake_helper: Path) -> None:
    episodes = [ReporterEpisodeInput.model_validate(e) for e in _two_episode_batch(tmp_path)]
    out = tmp_path / "warehouse"
    summary = build_warehouse(episodes, out, workers=1)

    assert summary.episodes_ok == 2
    assert summary.distinct_policies == 3  # polA, polB, polC

    # events are hive-partitioned by key
    assert (out / "events" / "key=entered_room").is_dir()
    assert (out / "episode_players.parquet").exists()

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["episodes_ok"] == 2
    assert "entered_room" in manifest["event_keys"]


def test_failed_episode_is_skipped_not_fatal(tmp_path: Path, fake_helper: Path) -> None:
    episodes = _two_episode_batch(tmp_path)
    episodes[0]["status"] = "failed"
    parsed = [ReporterEpisodeInput.model_validate(e) for e in episodes]
    out = tmp_path / "warehouse"
    summary = build_warehouse(parsed, out, workers=1)

    assert summary.episodes_skipped == 1
    assert summary.episodes_ok == 1
    manifest = json.loads((out / "manifest.json").read_text())
    statuses = {e["episode_id"]: e["status"] for e in manifest["episodes"]}
    assert statuses["ereq-1"] == "skipped"
    assert statuses["ereq-2"] == "ok"


def test_duckdb_queries_answer_by_policy_and_by_role(tmp_path: Path, fake_helper: Path) -> None:
    episodes = [ReporterEpisodeInput.model_validate(e) for e in _two_episode_batch(tmp_path)]
    out = tmp_path / "warehouse"
    build_warehouse(episodes, out, workers=1)

    con = duckdb.connect()
    events_glob = str(out / "events" / "**" / "*.parquet")
    con.execute(
        f"CREATE VIEW events AS SELECT * FROM read_parquet('{events_glob}', hive_partitioning = true)"
    )
    con.execute(
        f"CREATE VIEW episode_players AS SELECT * FROM read_parquet('{out / 'episode_players.parquet'}')"
    )

    # Room visits by policy: polA entered a room 3 times (Bridge+Storage in ep1, Bridge in ep2).
    room_counts = con.execute(
        """
        SELECT policy_name, json_extract_string(value, '$.room') AS room, count(*) AS visits
        FROM events WHERE key = 'entered_room' AND slot >= 0
        GROUP BY policy_name, room ORDER BY policy_name, room
        """
    ).fetchall()
    counts = {(name, room): visits for name, room, visits in room_counts}
    assert counts[("polA:v1", "Bridge")] == 2
    assert counts[("polA:v1", "Storage")] == 1

    # By role: the same policy version held both roles across the two episodes.
    roles = con.execute(
        """
        SELECT policy_version, list_sort(array_agg(DISTINCT role)) AS roles
        FROM episode_players WHERE policy_version = 'polA-v1' GROUP BY policy_version
        """
    ).fetchone()
    assert roles[1] == ["crew", "imposter"]

    # Star-schema join: resolve a co-present slot's role via episode_players.
    joined = con.execute(
        """
        SELECT ep.role
        FROM events e
        JOIN episode_players ep ON ep.episode_id = e.episode_id AND ep.slot = e.slot
        WHERE e.key = 'entered_room' AND e.episode_id = 'ereq-1' AND e.slot = 1
        """
    ).fetchall()
    assert joined == [("imposter",)]


def test_global_rows_have_null_policy(tmp_path: Path, fake_helper: Path) -> None:
    episodes = [ReporterEpisodeInput.model_validate(e) for e in _two_episode_batch(tmp_path)]
    out = tmp_path / "warehouse"
    build_warehouse(episodes, out, workers=1)

    # episode_metadata is a global (slot=-1) row seeded by the reporter.
    meta = pq.read_table(out / "events" / "key=episode_metadata")
    pydict = meta.to_pydict()
    assert all(slot == -1 for slot in pydict["slot"])
    assert all(pv is None for pv in pydict["policy_version"])
