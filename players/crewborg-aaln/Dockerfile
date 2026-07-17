# crewborg-rohit:v5 — Crewrift policy with hosted Bedrock meetings enabled.
#
# Self-contained build: vendored players/player_sdk + players/crewrift/crewborg.
# Scripted baseline with LLM meetings on and no hunter fork.
FROM --platform=linux/amd64 python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/srv/crewborg-aaln \
    CREWBORG_POLICY_VARIANT=dumb \
    BE_DUMB=1 \
    CREWBORG_DICK_MODE=0 \
    CREWBORG_LLM_MEETINGS=1 \
    CREWBORG_DEBUG_SPRITES=1

WORKDIR /srv/crewborg-aaln

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY players ./players

CMD ["python", "-m", "players.crewrift.crewborg.coworld.policy_player"]
