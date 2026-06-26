# Crewrift Prime advanced-skill commissioner image.
#
# Builds the vendored stock `commissioners` package (vendor/), then overlays the
# CrewriftPrimeSkillCommissioner subclass + its ruleset YAML + a custom ASGI
# entrypoint that registers the new commissioner key before the app resolves it.
# It ALSO bundles the repo's Nim replay expander (`tools/expand_replay.nim`,
# compiled to `crewrift-expand-replay`) so `replay_parser.py` can re-simulate a
# qualifier's `.bitreplay` into skill metrics at runtime — without it, every
# qualifier becomes an infra-hold.
#
# BUILD CONTEXT: the **repo root** (NOT this directory), so the builder stage can
# reach the game source (`src/`, `tools/`, `data/`, `nimby.lock`). Build with:
#
#   docker build --platform=linux/amd64 \
#     -f crewrift-prime/commissioner/Dockerfile \
#     -t crewrift-prime-commissioner:vN .
#
# (run from the repo root). See README.md "Build / wire".
#
# Wiring:
#   COMMISSIONER_KEY=crewrift_prime_skill        -> resolves CrewriftPrimeSkillCommissioner
#   RULESET_STRATEGY_CONFIG_PATH=/config/...yaml -> the skill-gate ruleset config
#   CREWRIFT_PRIME_EXPAND_REPLAY_CMD/_GAME_DIR   -> point replay_parser.py at the bundled expander
# The custom app module (app.py) imports the subclass (registering the key) then
# builds commissioner_app(), which reads COMMISSIONER_KEY from the env.

# ---------------------------------------------------------------------------
# Stage 1: compile the Nim replay expander against the full game source tree.
#
# `tools/expand_replay.nim` imports `../src/crewrift/{replays,sim}` and derives
# its game directory from `currentSourcePath()` at COMPILE time
# (GameDir = <repo>/.. of tools/) — at runtime it chdir's there to load map/data
# assets (`data/croatoan.resources`, ...). So it can NOT be compiled standalone:
# it needs the whole src tree + the pinned Nim deps. We mirror the game image's
# builder (debian + nimby + `nimby sync nimby.lock`), compile the binary at a
# FIXED path (/opt/crewrift-game) so its baked-in GameDir matches the runtime
# layout, and copy only the binary + `data/` into the lean final stage.
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS expander-build

RUN apt-get update && \
  apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git && \
  rm -rf /var/lib/apt/lists/*

# Pinned Nim toolchain via nimby (same release the game/eventlog images use).
RUN curl -fsSL \
      -o /usr/local/bin/nimby \
      https://github.com/treeform/nimby/releases/download/0.1.26/nimby-Linux-X64 && \
  chmod +x /usr/local/bin/nimby && \
  nimby use 2.2.4

ENV PATH="/root/.nimby/nim/bin:$PATH"

# Compile in the fixed game dir so the binary's baked-in GameDir == runtime dir.
WORKDIR /opt/crewrift-game

# Install the pinned Nim deps first (cached unless nimby.lock changes).
COPY nimby.lock .
RUN nimby --global sync nimby.lock

# Bring in the game source (src/, tools/, data/, crewrift.nimble, ...). The repo
# `nim.cfg` is host-specific (absolute nimby paths) and is .dockerignore'd; the
# global nimby config resolves the synced deps instead.
COPY . .

ARG NimFlags="-d:release -d:useMalloc --opt:speed --stackTrace:on"
RUN nim c \
  $NimFlags \
  --nimcache:/tmp/crewrift-expand-replay-nimcache \
  --out:/opt/crewrift-game/crewrift-expand-replay \
  tools/expand_replay.nim

# ---------------------------------------------------------------------------
# Stage 2: lean Python runtime image (the commissioner ASGI app), with only the
# compiled expander binary + the game `data/` assets copied from stage 1 — no
# Nim toolchain, no game source.
# ---------------------------------------------------------------------------
FROM python:3.13-slim

# Game directory the expander chdir's to (must match its compile-time GameDir).
ENV CREWRIFT_PRIME_GAME_DIR=/opt/crewrift-game

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COMMISSIONER_KEY=crewrift_prime_skill \
    RULESET_STRATEGY_CONFIG_PATH=/config/crewrift_prime.yaml \
    CREWRIFT_PRIME_EXPAND_REPLAY_CMD="/usr/local/bin/crewrift-expand-replay --format jsonl"

# Runtime libs the compiled Nim binary links against (curl for bitworld/curly).
RUN apt-get update && \
  apt-get install -y --no-install-recommends ca-certificates libcurl4 && \
  rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bundle the compiled expander (on PATH) + the game data assets it loads at the
# baked-in GameDir. `replay_parser.py` runs the expander in CREWRIFT_PRIME_GAME_DIR
# and the sim resolves `data/...` relative to that dir.
COPY --from=expander-build /opt/crewrift-game/crewrift-expand-replay /usr/local/bin/crewrift-expand-replay
COPY --from=expander-build /opt/crewrift-game/data /opt/crewrift-game/data

# 1) Install the vendored stock commissioners package (unmodified upstream),
#    plus OpenSkill (patent-free Bayesian skill rating) that powers the
#    per-policy MMR Competition leaderboard (see mmr.py / rank_division).
COPY crewrift-prime/commissioner/vendor /app/vendor
RUN pip install --no-cache-dir /app/vendor "openskill>=6.0.0"

# 2) Overlay the Crewrift Prime decision logic, subclass, entrypoint, debug
#    script, and ruleset config.
COPY crewrift-prime/commissioner/decision.py /app/decision.py
COPY crewrift-prime/commissioner/mmr.py /app/mmr.py
COPY crewrift-prime/commissioner/game_results_loader.py /app/game_results_loader.py
COPY crewrift-prime/commissioner/xp_request_client.py /app/xp_request_client.py
COPY crewrift-prime/commissioner/replay_parser.py /app/replay_parser.py
COPY crewrift-prime/commissioner/crewrift_prime_skill_commissioner.py /app/crewrift_prime_skill_commissioner.py
COPY crewrift-prime/commissioner/app.py /app/app.py
COPY crewrift-prime/commissioner/debug_decision.py /app/debug_decision.py
COPY crewrift-prime/commissioner/crewrift_prime.yaml /config/crewrift_prime.yaml

EXPOSE 8080
# Listen on 0.0.0.0:8080 and serve /healthz + /round per the commissioner contract.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
