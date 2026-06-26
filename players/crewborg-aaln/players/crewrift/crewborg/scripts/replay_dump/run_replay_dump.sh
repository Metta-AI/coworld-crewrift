#!/usr/bin/env bash
# Compile replay_dump.nim against a coworld-crewrift checkout and expand
# every episode's .bitreplay into per-tick multi-agent NDJSON.
#
# Usage:
#   CREWRIFT_ROOT=~/experiments/softmax/coworld-crewrift \
#     scripts/replay_dump/run_replay_dump.sh <eval_episode_dir>
#
# <eval_episode_dir> must contain ep??_*/replay.bitreplay (see
# fetch_replays.py in the eval dir, or scripts/fetch_episodes.py).
# Output: <eval_episode_dir>/replay_data/<ep>.ndjson per episode.
set -euo pipefail

CREWRIFT_ROOT="${CREWRIFT_ROOT:?Set CREWRIFT_ROOT to the coworld-crewrift checkout}"
EVAL_DIR="${1:?Usage: run_replay_dump.sh <eval_episode_dir>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN=/tmp/replay_dump

# The exporter imports ../src/crewrift/* and ./expand_replay, so it must be
# compiled from inside the checkout's tools/ dir. Copy only if missing or
# different; never overwrite uncommitted local edits silently.
if ! cmp -s "$HERE/replay_dump.nim" "$CREWRIFT_ROOT/tools/replay_dump.nim" 2>/dev/null; then
  cp "$HERE/replay_dump.nim" "$CREWRIFT_ROOT/tools/replay_dump.nim"
fi
(cd "$CREWRIFT_ROOT" && nim c -d:release --hints:off -o:"$BIN" tools/replay_dump.nim)

mkdir -p "$EVAL_DIR/replay_data"
ok=0 fail=0
for d in "$EVAL_DIR"/ep*_*/; do
  ep="$(basename "$d")"
  replay="$d/replay.bitreplay"
  out="$EVAL_DIR/replay_data/$ep.ndjson"
  [ -f "$replay" ] || continue
  [ -s "$out" ] && { ok=$((ok+1)); continue; }
  if "$BIN" "$replay" "$out" >/dev/null 2>"$EVAL_DIR/replay_data/$ep.err"; then
    rm -f "$EVAL_DIR/replay_data/$ep.err"
    ok=$((ok+1))
  else
    echo "FAIL $ep (see replay_data/$ep.err)"
    fail=$((fail+1))
  fi
done
echo "expanded ok=$ok fail=$fail -> $EVAL_DIR/replay_data/"
