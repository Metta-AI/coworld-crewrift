# crewborg-aaln:v2 — Aaron's Crewrift league policy (deterministic dumb baseline).
#
# Self-contained build: vendored players/player_sdk + players/crewrift/crewborg.
# Env flags match the Observatory upload (crewborg-aaln:v2, 2026-06-11): scripted
# baseline with LLM meetings off and no hunter fork (that became sussybuster-aaln).
FROM --platform=linux/amd64 python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/srv/crewborg-aaln \
    CREWBORG_POLICY_VARIANT=dumb \
    BE_DUMB=1 \
    CREWBORG_DICK_MODE=0 \
    CREWBORG_LLM_MEETINGS=0 \
    CREWBORG_DEBUG_SPRITES=1

WORKDIR /srv/crewborg-aaln

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY players ./players

CMD ["python", "-m", "players.crewrift.crewborg.coworld.policy_player"]
