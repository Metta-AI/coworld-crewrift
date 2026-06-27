from __future__ import annotations

import os
import subprocess
import tempfile

from .events import EventRow, common_value, parse_event_jsonl


def expand_replay_event_rows(
    replay_bytes: bytes,
    *,
    helper_path: str | None = None,
    snapshot_every: int = 1,
) -> list[EventRow]:
    helper = helper_path or os.environ.get("CREWRIFT_EXPAND_REPLAY", "crewrift-expand-replay")
    with tempfile.NamedTemporaryFile(suffix=".bitreplay") as replay_file:
        replay_file.write(replay_bytes)
        replay_file.flush()
        completed = subprocess.run(
            [
                helper,
                "--format",
                "jsonl",
                "--snapshot-every",
                str(snapshot_every),
                replay_file.name,
            ],
            capture_output=True,
            text=True,
        )
    rows = parse_event_jsonl(completed.stdout)
    if completed.returncode == 0:
        return rows
    if rows:
        if not any(row.key == "trace_warning" for row in rows):
            rows.append(
                EventRow(
                    ts=max((row.ts for row in rows), default=0),
                    player=-1,
                    key="trace_warning",
                    value=common_value(
                        source="reporter",
                        message="expand_replay exited nonzero after emitting partial rows",
                        returncode=completed.returncode,
                        stderr=completed.stderr.strip()[-300:] or None,
                    ),
                )
            )
        return rows
    raise subprocess.CalledProcessError(
        completed.returncode,
        completed.args,
        output=completed.stdout,
        stderr=completed.stderr,
    )
