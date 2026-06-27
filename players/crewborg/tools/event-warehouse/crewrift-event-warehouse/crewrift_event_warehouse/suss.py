"""Suss extraction: label every meeting chat message with *who it accuses*.

"Who is sussing who" is the social-deduction signal the raw warehouse doesn't capture
— chat events carry only the speaker + text. This module runs an LLM (Bedrock Haiku)
over each distinct chat ``text`` to extract the **suss target color** (the player the
speaker is casting suspicion on, or ``none``), then resolves that color to the target's
slot / role / policy *per episode* and writes a native ``events/key=chat_suss``
partition. Each row is keyed by the **speaker** (like other attributed events); its
``value`` JSON carries the resolved target, so a query can ask e.g. "how often does
policy X (as crew) suss an imposter" or "how often is Aaron sussed".

Design:
- The suss *target color* depends only on the message text, so we classify **distinct
  texts** once (most chat is templated and repeats heavily) and **cache** the result —
  thousands of events collapse to ~hundreds of LLM calls, batched.
- Color→slot is per-episode (from ``player_joined`` labels like ``red(Name)``); a pure
  data join, no LLM.

Idempotent: re-running overwrites the partition and reuses ``chat_suss_cache.json``.
Run via ``crewrift-event-warehouse suss --out <warehouse>`` (needs AWS creds + boto3).
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .schema import EVENTS_SCHEMA

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
REGION = "us-east-1"
BATCH = 30  # messages per LLM call

_LABEL_COLOR = re.compile(r"^\s*([A-Za-z]+)\s*\(")


def _color_of_label(label: str) -> str | None:
    m = _LABEL_COLOR.match(label or "")
    return m.group(1).lower() if m else None


def _events_glob(warehouse: Path, key: str) -> str:
    return str(warehouse / "events" / f"key={key}" / "*.parquet")


def distinct_texts(con: duckdb.DuckDBPyConnection, warehouse: Path) -> list[str]:
    fs = glob.glob(_events_glob(warehouse, "chat"))
    if not fs:
        return []
    rows = con.execute(
        f"SELECT DISTINCT json_extract_string(value,'$.text') AS t "
        f"FROM read_parquet('{_events_glob(warehouse, 'chat')}') WHERE t IS NOT NULL"
    ).fetchall()
    return [r[0] for r in rows]


def classify_texts(texts: list[str]) -> dict[str, str | None]:
    """Map each distinct chat text → the suss-target color (lowercase) or None.

    Batched Bedrock Haiku calls; deterministic (temperature 0). Anything the model
    can't resolve to a concrete color is None (no suss)."""

    import boto3

    br = boto3.client("bedrock-runtime", region_name=REGION)
    out: dict[str, str | None] = {}
    instruction = (
        "You label Among Us / social-deduction MEETING chat. For each numbered message, "
        "output the single player COLOR the speaker is accusing or casting suspicion on "
        "(their 'sus' target). Player colors are words like red, blue, green, yellow, "
        "orange, pink, purple, cyan, white, black, lime, brown. If the message names no "
        "specific suspect — e.g. 'no read, skipping', a generic question, a body report "
        "with no accusation, or self-defense — output \"none\". Reply with ONLY a JSON "
        "array, one object per message: [{\"i\": <index>, \"target\": \"<color|none>\"}]."
    )
    for start in range(0, len(texts), BATCH):
        chunk = texts[start : start + BATCH]
        listing = "\n".join(f"{i}: {t!r}" for i, t in enumerate(chunk))
        resp = br.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": [{"text": instruction + "\n\n" + listing}]}],
            inferenceConfig={"maxTokens": 2000, "temperature": 0},
        )
        text = resp["output"]["message"]["content"][0]["text"]
        parsed = _parse_json_array(text)
        for obj in parsed:
            try:
                i = int(obj["i"])
                tgt = str(obj["target"]).strip().lower()
            except (KeyError, ValueError, TypeError):
                continue
            if 0 <= i < len(chunk):
                out[chunk[i]] = None if tgt in ("none", "", "null") else tgt
    for t in texts:  # anything the model skipped → no suss
        out.setdefault(t, None)
    return out


def _parse_json_array(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        return []
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []


def episode_color_maps(con: duckdb.DuckDBPyConnection, warehouse: Path) -> dict[str, dict[str, int]]:
    """{episode_id: {color: slot}} from player_joined labels."""

    rows = con.execute(
        f"SELECT episode_id, slot, json_extract_string(value,'$.label') AS lbl "
        f"FROM read_parquet('{_events_glob(warehouse, 'player_joined')}')"
    ).fetchall()
    maps: dict[str, dict[str, int]] = {}
    for ep, slot, label in rows:
        color = _color_of_label(label)
        if color is not None:
            maps.setdefault(ep, {})[color] = slot
    return maps


def slot_identity(con: duckdb.DuckDBPyConnection, warehouse: Path) -> dict[tuple[str, int], dict]:
    rows = con.execute(
        f"SELECT episode_id, slot, role, policy_name FROM read_parquet('{warehouse}/episode_players.parquet')"
    ).fetchall()
    return {(ep, slot): {"role": role, "policy": pol} for ep, slot, role, pol in rows}


def build_suss_partition(warehouse: Path, *, refresh: bool = False) -> int:
    """Classify chat → write events/key=chat_suss. Returns the row count written."""

    warehouse = Path(warehouse)
    con = duckdb.connect()
    texts = distinct_texts(con, warehouse)
    if not texts:
        print("no chat events in this warehouse — nothing to do")
        return 0

    cache_path = warehouse / "chat_suss_cache.json"
    cache: dict[str, str | None] = {}
    if cache_path.exists() and not refresh:
        cache = json.loads(cache_path.read_text())
    missing = [t for t in texts if t not in cache]
    print(f"{len(texts)} distinct chat texts ({len(missing)} new to classify via {MODEL_ID})")
    if missing:
        cache.update(classify_texts(missing))
        cache_path.write_text(json.dumps(cache, indent=0))

    color_maps = episode_color_maps(con, warehouse)
    identity = slot_identity(con, warehouse)

    # join each chat event to its resolved suss target
    chat = con.execute(
        f"SELECT ts, episode_id, slot, policy_version, policy_name, role, "
        f"json_extract_string(value,'$.text') AS txt, json_extract_string(value,'$.phase') AS ph "
        f"FROM read_parquet('{_events_glob(warehouse, 'chat')}')"
    ).fetchall()

    cols = {c: [] for c in ("ts", "episode_id", "slot", "policy_version", "policy_name", "role", "key", "value")}
    for ts, ep, slot, pver, pname, role, text, phase in chat:
        target_color = cache.get(text)
        tslot = color_maps.get(ep, {}).get(target_color) if target_color else None
        tident = identity.get((ep, tslot)) if tslot is not None else None
        value = {
            "text": text,
            "phase": phase,
            "suss_target_color": target_color,
            "suss_target_slot": tslot,
            "suss_target_role": tident["role"] if tident else None,
            "suss_target_policy": tident["policy"] if tident else None,
            "is_suss": target_color is not None,
            "target_is_imposter": (tident["role"] == "imposter") if tident else None,
        }
        cols["ts"].append(ts)
        cols["episode_id"].append(ep)
        cols["slot"].append(slot)
        cols["policy_version"].append(pver)
        cols["policy_name"].append(pname)
        cols["role"].append(role)
        cols["key"].append("chat_suss")
        cols["value"].append(json.dumps(value))

    out_dir = warehouse / "events" / "key=chat_suss"
    out_dir.mkdir(parents=True, exist_ok=True)
    table = pa.table(cols, schema=EVENTS_SCHEMA)
    pq.write_table(table, out_dir / "chat_suss.parquet")
    n_suss = sum(1 for v in cols["value"] if json.loads(v)["is_suss"])
    print(f"wrote {len(cols['ts'])} chat_suss rows ({n_suss} are an actual suss) → {out_dir}")
    return len(cols["ts"])
