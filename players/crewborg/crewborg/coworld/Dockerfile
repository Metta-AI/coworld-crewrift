# Crewborg player image — self-contained build for coworld-crewrift.
#
# Build context = the player directory `players/crewborg/`, which holds the `crewborg/`
# package (the policy source). Only the package is copied into the image; the
# surrounding optimizer toolkit stays out.
#
#   docker build -f crewborg/coworld/Dockerfile -t crewborg players/crewborg
#   (add --platform=linux/amd64 via buildx; the cluster is amd64)
#
# crewborg is the `crewborg` package and imports only `players.player_sdk`
# from the shared SDK. We install that SDK from the public Metta-AI/coworld-tools
# monorepo (pinned via PLAYERS_SDK_REF — pass a fixed ref for a reproducible build;
# `main` is only a fallback) and put the package on PYTHONPATH. We install NO lab
# tooling, and bake NO behavior/experiment env (e.g. CREWBORG_BE_DUMB) — those are
# set at upload time so the image stays behavior-neutral and reproducible.

# Platform is set by the build invocation (buildx --platform=linux/amd64), not here.
FROM python:3.12-slim

ARG PLAYERS_SDK_REF=main

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# Shared SDK + crewborg's runtime deps (numpy/pydantic/websockets/cramjam are
# players' base deps). The players repo moved into the coworld-tools monorepo as the
# `players/` subdirectory; install from its GitHub source tarball at the pinned ref
# (tarball = no git/submodules or local checkout needed in the build).
# `[bedrock]` adds boto3 so the meeting LLM can route through AWS Bedrock when the
# policy is uploaded with `--use-bedrock`; inert otherwise.
RUN pip install --upgrade pip \
 && pip install "players[bedrock] @ https://github.com/Metta-AI/coworld-tools/archive/${PLAYERS_SDK_REF}.tar.gz#subdirectory=players"

# Meeting chat NLP (imposter bandwagon, design §10.5): spaCy dependency-parse negation
# scope over opponents' chat. Loaded lazily in a background thread and gated by
# CREWBORG_CHAT_NLP, so it never touches the gameplay hot path; unset the flag to skip
# it entirely. The en_core_web_sm model is pinned by URL to match spaCy's minor version.
# `click` is pulled in explicitly: `import spacy` imports `spacy.cli` → `click`, and the
# slim base + players deps leave it out (typer lands without it), so without this the
# model load throws ModuleNotFoundError at runtime and chat NLP silently degrades to
# vote-only (caught in the v20 eval via the meeting_decision `nlp=failed` trace).
RUN pip install "spacy>=3.8.0,<3.9.0" click \
 && pip install "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

# Copy the policy package into place as `crewborg`. The build-context root is the player
# directory, so the `crewborg/` package is copied directly onto PYTHONPATH (/app).
# (A `.dockerignore` could later trim tests/docs/viewer from the image.)
COPY crewborg /app/crewborg

# Reads COWORLD_PLAYER_WS_URL (canonical) / COGAMES_ENGINE_WS_URL (alias), connects,
# plays one slot, and exits when the episode ends.
CMD ["python", "-m", "crewborg.coworld.policy_player"]
