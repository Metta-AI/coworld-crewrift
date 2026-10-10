#!/usr/bin/env bash
# Build the current Aaln player from both owned source inputs. Pass Docker build flags.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAYERS_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
docker build --platform linux/amd64 -t crewborg-aaln:dev \
  -f "$PLAYERS_ROOT/crewborg-aaln/Dockerfile" "$@" "$PLAYERS_ROOT"
