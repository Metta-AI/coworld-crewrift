#!/usr/bin/env bash
# Crewbot3000 INTERVIEW-MODE entrypoint: launch the out-of-band interview
# websocket SERVER (NOT the game bridge). The league commissioner connects to
# this server to interview the policy about Crewrift voting strategy as a hard
# qualification gate. See coworld/interview_server.py for the
# `coworld.interview.v1` protocol.
#
# The platform launches the container with this command (instead of the default
# game entrypoint) when it needs to interview the policy, and reaches it on
# CREWRIFT_INTERVIEW_PORT (default 8770). The game entrypoint (entrypoint.sh) is
# untouched.
set -euo pipefail

exec python -m players.crewrift.crewbot3000.coworld.interview_server
