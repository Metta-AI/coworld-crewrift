# Crewrift Prime advanced-skill commissioner image.
#
# Builds the vendored stock `commissioners` package (vendor/), then overlays the
# CrewriftPrimeSkillCommissioner subclass + its ruleset YAML + a custom ASGI
# entrypoint that registers the new commissioner key before the app resolves it.
#
# Wiring:
#   COMMISSIONER_KEY=crewrift_prime_skill        -> resolves CrewriftPrimeSkillCommissioner
#   RULESET_STRATEGY_CONFIG_PATH=/config/...yaml -> the skill-gate ruleset config
#   CREWRIFT_PRIME_INTERVIEW_ENABLED=0           -> interview hard gate OFF (the
#     platform interview-mode launcher is not wired yet, so an enabled gate would
#     INFRA-HOLD every candidate forever; interview support will move into the game
#     itself later). Qualification runs on the three-skill gate only until then.
# The custom app module (app.py) imports the subclass (registering the key) then
# builds commissioner_app(), which reads COMMISSIONER_KEY from the env.
FROM --platform=linux/amd64 python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COMMISSIONER_KEY=crewrift_prime_skill \
    RULESET_STRATEGY_CONFIG_PATH=/config/crewrift_prime.yaml \
    CREWRIFT_PRIME_INTERVIEW_ENABLED=0

WORKDIR /app

# 1) Install the vendored stock commissioners package (unmodified upstream).
#    NOTE: the Competition leaderboard now ranks by WIN RATE (all-time episodes
#    won / all-time episodes played; see rank_division / _win_total_board), NOT
#    OpenSkill MMR. The OpenSkill install and mmr.py are retained only as an
#    unused, test-covered ranking option and are no longer on the live scoring path.
COPY vendor /app/vendor
RUN pip install --no-cache-dir /app/vendor "openskill>=6.0.0"

# 2) Overlay the Crewrift Prime decision logic, subclass, entrypoint, debug
#    script, and ruleset config.
#    NOTE: the qualifier reads the episode's per-slot RESULTS JSON artifact
#    (/jobs/{job_id}/artifacts/results) directly, so the Nim replay expander +
#    replay_parser.py are NO LONGER part of the image (Dependency A retired).
COPY decision.py /app/decision.py
COPY mmr.py /app/mmr.py
COPY game_results_loader.py /app/game_results_loader.py
COPY xp_request_client.py /app/xp_request_client.py
COPY interview.py /app/interview.py
COPY crewrift_prime_skill_commissioner.py /app/crewrift_prime_skill_commissioner.py
COPY app.py /app/app.py
COPY debug_decision.py /app/debug_decision.py
COPY crewrift_prime.yaml /config/crewrift_prime.yaml

EXPOSE 8080
# Listen on 0.0.0.0:8080 and serve /healthz + /round per the commissioner contract.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
