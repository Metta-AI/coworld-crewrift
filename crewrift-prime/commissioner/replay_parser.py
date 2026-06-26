"""Derive skill metrics from a Crewrift ``.bitreplay`` for the qualifier gate.

A Crewrift ``.bitreplay`` is **not** a stored event log: its body is only per-tick
player INPUT MASKS (plus join/leave/chat/hash records) that the Nim game server
**re-simulates** to reconstruct what happened (kills, tasks, votes, scores). There
is no pure-Python ``.bitreplay`` decoder in this repo — recovering structured
events requires re-running the real simulator. We therefore split this module into
two layers:

  1. :func:`expand_replay_to_events` — the I/O / engine boundary. It writes the
     downloaded replay bytes to a temp file and invokes the repo's canonical Nim
     expander (``tools/expand_replay.nim --format jsonl``), which re-simulates the
     replay and emits the structured ``{ts, player, key, value}`` event log (the
     same schema the hosted ``crewrift-eventlog-reporter`` produces). The expander
     command is configurable via ``CREWRIFT_PRIME_EXPAND_REPLAY_CMD`` so the image
     can point at a prebuilt binary instead of ``nim r``. If no expander/engine is
     available it raises :class:`ReplayParseError` (an infra hold, not a DQ).

  2. :func:`game_results_from_events` — PURE Python (no I/O). It folds the event
     rows into the seat-indexed ``results_schema`` dict that ``decision.py``
     already understands (``imposter``/``crew``/``kills``/``tasks``/``vote_players``
     /``vote_skip``/``vote_timeout``/``win``/``scores``), so the existing
     :func:`decision.evaluate_combined_game` gate is reused UNCHANGED.

:func:`parse_replay_metrics` chains the two: bytes -> events -> game_results.

Event-log schema (from ``tools/expand_replay.nim`` / ``reporters/eventlog``):
rows are ``{"ts": int, "player": slot, "key": str, "value": obj}`` with keys
``player_joined``, ``entered_room``, ``left_room``, ``phase``,
``vote_called_body``, ``vote_called_button``, ``kill``, ``body``, ``died``,
``revived``, ``started_task``, ``completed_task``, ``vote_cast``, ``chat``,
``score``. ``player`` is the stable join slot; ``vote_cast.value`` is either
``{"target": "skip"}`` or ``{"target_slot": int, "target_label": str}``;
``score.value`` is ``{"amount": int, "reason": str}`` where reason is one of
``killing`` / ``completing task`` / ``winning`` / ``failing to vote or skip`` /
``standing still``.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

# Default seat count for a Crewrift qualifier game (8-seat self-play).
DEFAULT_NUM_SEATS = 8

# How to expand a replay into the structured event log. Default re-runs the
# repo's Nim expander; the image can override with a prebuilt binary path, e.g.
#   CREWRIFT_PRIME_EXPAND_REPLAY_CMD="/usr/local/bin/crewrift-expand-replay"
# The replay file path is appended as the final argument. ``--format jsonl`` is
# added automatically unless already present in the override.
_DEFAULT_EXPAND_CMD = "nim r tools/expand_replay.nim --format jsonl"
_EXPAND_CMD_ENV = "CREWRIFT_PRIME_EXPAND_REPLAY_CMD"
_EXPAND_CWD_ENV = "CREWRIFT_PRIME_GAME_DIR"  # cwd for `nim r tools/expand_replay.nim`
_EXPAND_TIMEOUT_ENV = "CREWRIFT_PRIME_EXPAND_TIMEOUT_SECONDS"

# Score reasons emitted by expand_replay.nim (see printScoreLine callers).
_REASON_KILLING = "killing"
_REASON_WINNING = "winning"


class ReplayParseError(Exception):
    """The replay could not be expanded/parsed (an infra hold, never a DQ).

    Raised when the Nim expander is unavailable, errors, times out, or produces
    no usable rows. The commissioner treats this like any other dispatch/infra
    failure: hold the entrant for retry rather than disqualifying it.
    """


def _expand_command() -> list[str]:
    raw = os.getenv(_EXPAND_CMD_ENV, _DEFAULT_EXPAND_CMD)
    parts = shlex.split(raw)
    if "--format" not in parts:
        parts += ["--format", "jsonl"]
    return parts


def expand_replay_to_events(replay_bytes: bytes) -> list[dict[str, Any]]:
    """Re-simulate a ``.bitreplay`` into structured event-log rows (engine I/O).

    Writes ``replay_bytes`` to a temp ``.bitreplay`` and runs the configured
    expander, parsing its JSONL stdout into a list of ``{ts, player, key, value}``
    rows. Raises :class:`ReplayParseError` on any failure (missing engine,
    non-zero exit, timeout, no rows) so the caller holds-for-retry.
    """
    if not replay_bytes:
        raise ReplayParseError("empty replay bytes")
    command = _expand_command()
    cwd = os.getenv(_EXPAND_CWD_ENV) or None
    try:
        timeout = float(os.getenv(_EXPAND_TIMEOUT_ENV, "300"))
    except ValueError:
        timeout = 300.0

    with tempfile.TemporaryDirectory(prefix="crewrift-prime-replay-") as tmp:
        replay_path = Path(tmp) / "replay.bitreplay"
        replay_path.write_bytes(replay_bytes)
        try:
            completed = subprocess.run(
                [*command, str(replay_path)],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:  # the expander binary/`nim` is absent.
            raise ReplayParseError(
                f"replay expander not available ({command[0]!r}); "
                f"set {_EXPAND_CMD_ENV} to a prebuilt expander"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ReplayParseError(f"replay expander timed out after {timeout:g}s") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:300]
        raise ReplayParseError(f"replay expander failed (exit {completed.returncode}): {detail}")

    rows = list(_iter_event_rows(completed.stdout))
    if not rows:
        raise ReplayParseError("replay expander produced no event rows")
    return rows


def _iter_event_rows(stdout: str) -> Iterable[dict[str, Any]]:
    """Yield ``{ts, player, key, value}`` event rows from the expander's JSONL.

    The JSONL stream also carries non-event trace/metadata rows (``map_geometry``,
    ``episode_metadata``, ``trace_complete``, ...) — we keep only rows that have
    the four event fields with an integer ``player`` slot.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if "key" not in row or "value" not in row or "player" not in row:
            continue
        if not isinstance(row.get("player"), int):
            continue
        yield row


def game_results_from_events(
    events: list[dict[str, Any]],
    *,
    num_seats: int = DEFAULT_NUM_SEATS,
) -> dict[str, Any]:
    """Fold event-log rows into a seat-indexed ``results_schema`` dict (PURE).

    The returned dict matches the per-slot arrays ``decision.py`` reads:
      - ``imposter`` / ``crew`` : 1/0 role flags per seat,
      - ``kills``               : kills landed by each seat (``kill`` events),
      - ``tasks``               : tasks completed by each seat (``completed_task``),
      - ``vote_players``        : votes the seat cast for a player,
      - ``vote_skip``           : explicit skip votes the seat cast,
      - ``vote_timeout``        : meetings the seat failed to vote in
                                  (``score`` reason "failing to vote or skip"),
      - ``win``                 : True if the seat received a "winning" score,
      - ``scores``              : net score per seat.

    Roles are derived without trusting any single signal: a seat is an imposter
    if it landed a kill OR scored for "killing"; every other seat that joined the
    game is crew. ``num_seats`` bounds the arrays (extra-high slots are ignored;
    fewer-seat games are padded with zeros) so a malformed replay can't crash the
    gate.
    """
    n = max(int(num_seats), 0)

    def zeros() -> list[float]:
        return [0.0] * n

    kills = zeros()
    tasks = zeros()
    vote_players = zeros()
    vote_skip = zeros()
    vote_timeout = zeros()
    scores = zeros()
    joined = [False] * n
    is_imposter = [False] * n
    is_winner = [False] * n

    def in_range(slot: Any) -> bool:
        return isinstance(slot, int) and 0 <= slot < n

    for row in events:
        slot = row.get("player")
        key = row.get("key")
        value = row.get("value") if isinstance(row.get("value"), dict) else {}
        if not in_range(slot):
            continue

        if key == "player_joined":
            joined[slot] = True
        elif key == "kill":
            kills[slot] += 1.0
            is_imposter[slot] = True
            joined[slot] = True
        elif key == "completed_task":
            tasks[slot] += 1.0
            joined[slot] = True
        elif key == "vote_cast":
            joined[slot] = True
            if value.get("target") == "skip" or value.get("target_label") == "skip":
                vote_skip[slot] += 1.0
            else:
                vote_players[slot] += 1.0
        elif key == "score":
            joined[slot] = True
            amount = value.get("amount")
            reason = value.get("reason")
            if isinstance(amount, (int, float)):
                scores[slot] += float(amount)
            if reason == _REASON_KILLING:
                is_imposter[slot] = True
            elif reason == _REASON_WINNING:
                is_winner[slot] = True
            elif reason == "failing to vote or skip":
                vote_timeout[slot] += 1.0
        elif key in ("died", "revived", "body", "started_task", "entered_room", "left_room", "chat"):
            joined[slot] = True

    imposter = [1 if is_imposter[i] else 0 for i in range(n)]
    crew = [1 if (joined[i] and not is_imposter[i]) else 0 for i in range(n)]
    win = [bool(is_winner[i]) for i in range(n)]

    return {
        "imposter": imposter,
        "crew": crew,
        "kills": kills,
        "tasks": tasks,
        "vote_players": vote_players,
        "vote_skip": vote_skip,
        "vote_timeout": vote_timeout,
        "win": win,
        "scores": scores,
    }


def parse_replay_metrics(
    replay_bytes: bytes,
    *,
    num_seats: int = DEFAULT_NUM_SEATS,
) -> dict[str, Any]:
    """Expand a ``.bitreplay`` and fold it into a ``game_results`` dict.

    Bytes -> Nim event expansion -> pure metric fold. Raises
    :class:`ReplayParseError` if expansion fails (infra hold).
    """
    events = expand_replay_to_events(replay_bytes)
    return game_results_from_events(events, num_seats=num_seats)
