#!/usr/bin/env bash
# Crewborg container entrypoint: launch the Sprite-v1 websocket bridge.
# Reads COGAMES_ENGINE_WS_URL (filled in by the Coworld runner).
set -euo pipefail

exec python -m players.crewrift.crewborg.coworld.policy_player
