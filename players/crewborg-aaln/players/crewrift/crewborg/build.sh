#!/usr/bin/env bash
# Build the Crewborg player image and emit Coworld manifest artifacts.
# Mirrors players/cogsguard/baseline/build.sh; see
# docs/coworld-player-packaging.md for the full contract.
set -euo pipefail

SCRIPT_DIR="$( cd "$(dirname "${BASH_SOURCE[0]}")" && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/../../.." && pwd )"
POLICY_DIR="$SCRIPT_DIR"
export POLICY_DIR

source "$REPO_ROOT/tools/players_build/build_lib.sh"

PLAYER_ID="crewborg"
PLAYER_NAME="Crewborg"
PLAYER_DESCRIPTION="Player-SDK scripted agent for the Crewrift social-deduction Coworld."
PLAYER_GAMES_JSON='["crewrift"]'
PLAYER_AUTHOR="players@softmax.com"
IMAGE_LOCAL_TAG="players-crewborg:dev"
IMAGE_PUBLIC_URI="ghcr.io/metta-ai/players-crewborg:latest"
DOCKERFILE="$POLICY_DIR/coworld/Dockerfile"
BUILD_CONTEXT="$REPO_ROOT"
PLAYER_ENV_JSON='{}'
PLAYER_RUN_JSON='null'

run_player_build "$@"
