# Crewrift Prime advanced-skill commissioner image.
#
# Builds the vendored stock `commissioners` package (vendor/), then overlays the
# CrewriftPrimeSkillCommissioner subclass + its ruleset YAML + a custom ASGI
# entrypoint that registers the new commissioner key before the app resolves it.
#
# Wiring:
#   COMMISSIONER_KEY=crewrift_prime_skill        -> resolves CrewriftPrimeSkillCommissioner
#   RULESET_STRATEGY_CONFIG_PATH=/config/...yaml -> the skill-gate ruleset config
# The custom app module (app.py) imports the subclass (registering the key) then
# builds commissioner_app(), which reads COMMISSIONER_KEY from the env.
FROM --platform=linux/amd64 python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COMMISSIONER_KEY=crewrift_prime_skill \
    RULESET_STRATEGY_CONFIG_PATH=/config/crewrift_prime.yaml

WORKDIR /app

# 1) Install the vendored stock commissioners package (unmodified upstream).
COPY vendor /app/vendor
RUN pip install --no-cache-dir /app/vendor

# 2) Overlay the Crewrift Prime decision logic, subclass, entrypoint, debug
#    script, and ruleset config.
COPY decision.py /app/decision.py
COPY game_results_loader.py /app/game_results_loader.py
COPY crewrift_prime_skill_commissioner.py /app/crewrift_prime_skill_commissioner.py
COPY app.py /app/app.py
COPY debug_decision.py /app/debug_decision.py
COPY crewrift_prime.yaml /config/crewrift_prime.yaml

EXPOSE 8080
# Listen on 0.0.0.0:8080 and serve /healthz + /round per the commissioner contract.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
