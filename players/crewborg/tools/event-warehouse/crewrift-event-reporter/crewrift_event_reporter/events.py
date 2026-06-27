from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, Field, field_validator

EVENT_SCHEMA_VERSION = "crewrift-events/v1"
PARQUET_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("player", pa.int32()),
        ("key", pa.string()),
        ("value", pa.string()),
    ]
)
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class EventRow(BaseModel):
    ts: int
    player: int
    key: str
    value: dict[str, Any] = Field(default_factory=dict)

    @field_validator("key")
    @classmethod
    def key_must_not_be_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("event key must not be empty")
        return value


def common_value(
    *,
    source: str,
    confidence: float = 1.0,
    episode_id: str | None = None,
    phase: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "source": source,
        "confidence": confidence,
    }
    if episode_id is not None:
        value["episode_id"] = episode_id
    if phase is not None:
        value["phase"] = phase
    value.update({key: item for key, item in fields.items() if item is not None})
    return value


def parse_event_jsonl(text: str) -> list[EventRow]:
    rows: list[EventRow] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid event JSONL at line {line_number}: {exc}") from exc
        rows.append(EventRow.model_validate(payload))
    return rows


def value_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def sort_events(rows: list[EventRow]) -> list[EventRow]:
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda item: (item[1].ts, item[1].player, item[1].key, item[0]))
    return [row for _, row in indexed]


def parquet_bytes(rows: list[EventRow]) -> bytes:
    ordered = sort_events(rows)
    table = pa.Table.from_pydict(
        {
            "ts": [row.ts for row in ordered],
            "player": [row.player for row in ordered],
            "key": [row.key for row in ordered],
            "value": [value_json(row.value) for row in ordered],
        },
        schema=PARQUET_SCHEMA,
    )
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def event_zip_bytes(rows: list[EventRow]) -> bytes:
    payload = parquet_bytes(rows)
    buffer = io.BytesIO()
    info = zipfile.ZipInfo("events.parquet", ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(info, payload)
    return buffer.getvalue()


def read_event_zip(payload: bytes) -> pa.Table:
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = zf.namelist()
        if names != ["events.parquet"]:
            raise ValueError(f"expected zip to contain only events.parquet, got {names}")
        return pq.read_table(io.BytesIO(zf.read("events.parquet")))


def reporter_metadata_row(
    *,
    request_id: str,
    episode_id: str,
    report_uri: str,
) -> EventRow:
    return EventRow(
        ts=0,
        player=-1,
        key="episode_metadata",
        value=common_value(
            source="reporter",
            episode_id=episode_id,
            request_id=request_id,
            report_uri=report_uri,
            generated_at=datetime.now().astimezone().isoformat(),
        ),
    )


def request_player_manifest_rows(
    *,
    episode_id: str,
    players: list[Any],
) -> list[EventRow]:
    rows: list[EventRow] = []
    for player in players:
        rows.append(
            EventRow(
                ts=0,
                player=player.slot,
                key="player_manifest",
                value=common_value(
                    source="reporter",
                    episode_id=episode_id,
                    player_id=player.player_id,
                    display_name=player.display_name,
                ),
            )
        )
    return rows
