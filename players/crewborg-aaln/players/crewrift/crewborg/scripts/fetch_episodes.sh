#!/usr/bin/env bash
# Download full episode data for the N most recent episodes crewborg played.
#
# Thin wrapper around scripts/fetch_episodes.py so it runs from the repo root
# under `uv` (matching play_local.sh). Auth comes from `softmax login`.
#
# Examples:
#   players/crewrift/crewborg/scripts/fetch_episodes.sh -n 10
#   players/crewrift/crewborg/scripts/fetch_episodes.sh -n 5 --version 2
#
# All flags are passed through to fetch_episodes.py (see its --help).
set -euo pipefail

SCRIPT_DIR="$( cd "$(dirname "${BASH_SOURCE[0]}")" && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/../../../.." && pwd )"
cd "$REPO_ROOT"

exec uv run python "$SCRIPT_DIR/fetch_episodes.py" "$@"
