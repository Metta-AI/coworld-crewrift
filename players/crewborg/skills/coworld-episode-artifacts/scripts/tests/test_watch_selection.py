"""Unit tests for the --watch selection logic (pure function, no network)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetch_artifacts import EpisodeRef, episode_dirname, select_watch_fetches


def _ref(ref_id: str, status: str) -> EpisodeRef:
    return EpisodeRef(
        ref_id=ref_id,
        created_at="2026-07-01T12:00:00",
        job_id="job-1",
        replay_url=None,
        label=status,
        record={"id": ref_id, "status": status},
    )


def _complete_dir(root: Path, ref: EpisodeRef) -> Path:
    d = root / episode_dirname(ref)
    (d / "logs").mkdir(parents=True)
    (d / "episode.json").write_text("{}")
    (d / "replay.json").write_bytes(b"")
    return d


def test_selection_partitions_done_waiting_exhausted_and_fetchable(tmp_path: Path) -> None:
    done_ref = _ref("ereq_done00000000000", "completed")
    _complete_dir(tmp_path, done_ref)
    running = _ref("ereq_running0000000", "running")
    fresh = _ref("ereq_fresh000000000", "completed")
    failed_terminal = _ref("ereq_failed00000000", "failed")
    tired = _ref("ereq_tired000000000", "failed")

    to_fetch, waiting, exhausted, done = select_watch_fetches(
        [done_ref, running, fresh, failed_terminal, tired],
        tmp_path,
        {"ereq_tired000000000": 3},
        want_replay=True,
        want_logs=True,
        max_attempts=3,
        xreq_drained=False,
    )
    assert [r.ref_id for r in to_fetch] == ["ereq_fresh000000000", "ereq_failed00000000"]
    assert [r.ref_id for r in waiting] == ["ereq_running0000000"]
    assert [r.ref_id for r in exhausted] == ["ereq_tired000000000"]
    assert [r.ref_id for r in done] == ["ereq_done00000000000"]


def test_drained_xreq_sweeps_episodes_with_nonterminal_row_status(tmp_path: Path) -> None:
    # When the xreq itself reports drained, stale per-row statuses must not
    # strand an episode: everything unfetched becomes fetchable.
    running = _ref("ereq_running0000000", "running")
    to_fetch, waiting, exhausted, done = select_watch_fetches(
        [running], tmp_path, {},
        want_replay=True, want_logs=True, max_attempts=3, xreq_drained=True,
    )
    assert [r.ref_id for r in to_fetch] == ["ereq_running0000000"]
    assert waiting == [] and exhausted == [] and done == []


def test_partial_dir_is_retried_not_done(tmp_path: Path) -> None:
    # An episode dir missing its replay fails episode_is_complete -> refetch.
    ref = _ref("ereq_partial0000000", "completed")
    d = tmp_path / episode_dirname(ref)
    d.mkdir(parents=True)
    (d / "episode.json").write_text("{}")   # no replay.json, no logs/
    to_fetch, _, _, done = select_watch_fetches(
        [ref], tmp_path, {},
        want_replay=True, want_logs=True, max_attempts=3, xreq_drained=False,
    )
    assert [r.ref_id for r in to_fetch] == ["ereq_partial0000000"]
    assert done == []
