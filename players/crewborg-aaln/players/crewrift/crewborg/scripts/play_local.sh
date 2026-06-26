#!/usr/bin/env bash
# Run crewborg against a local Crewrift server for a smoke test.
#
# Start a Crewrift dev server first (see AGENTS.md §"Connecting / running
# locally"), e.g. the smallest smoke episode:
#
#   cd ~/coding/games/coworld-crewrift
#   nim r src/crewrift.nim --address:0.0.0.0 --port:2000 \
#     --config:'{"minPlayers":1,"imposterCount":0,"tasksPerPlayer":1}'
#
# then run this script. Override COGAMES_ENGINE_WS_URL to point elsewhere.
set -euo pipefail

: "${COGAMES_ENGINE_WS_URL:=ws://localhost:2000/player?slot=0&token=}"
export COGAMES_ENGINE_WS_URL

REPO_ROOT="$( cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd )"
cd "$REPO_ROOT"

exec uv run python -m players.crewrift.crewborg.coworld.policy_player
