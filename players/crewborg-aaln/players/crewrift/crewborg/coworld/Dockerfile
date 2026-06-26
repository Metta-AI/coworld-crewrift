# Crewborg player image.
#
# Built via the leaf ``build.sh`` (which uses tools/players_build/build_lib.sh).
# The build context is the repo root so the image can install the local
# ``players`` package. Unlike the cogsguard images, crewborg needs no mettagrid /
# cogames stack — it speaks Crewrift's binary Sprite-v1 protocol over its own
# websocket bridge, so the base ``players`` deps (numpy/pydantic/websockets)
# suffice.

FROM --platform=linux/amd64 python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CREWBORG_POLICY_VARIANT=dumb \
    BE_DUMB=1 \
    CREWBORG_DICK_MODE=0 \
    CREWBORG_LLM_MEETINGS=0

WORKDIR /srv/players

COPY pyproject.toml README.md ./
COPY players ./players
# The `bedrock` extra pulls boto3 so the meeting LLM can route through AWS
# Bedrock when the policy is uploaded with `--use-bedrock` (see README §"LLM
# meetings"). It is inert unless a Bedrock flag is set at runtime.
RUN pip install --upgrade pip \
 && pip install -e ".[bedrock]"

ENTRYPOINT ["/srv/players/players/crewrift/crewborg/coworld/entrypoint.sh"]
