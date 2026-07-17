# Crewrift Prime REST commissioner worker image.
#
# Builds the vendored stock `commissioners` package (vendor/), then overlays the
# CrewriftPrimeSkillCommissioner subclass, its ruleset YAML, and the durable
# platform API worker. This image does not serve the legacy `/round` WebSocket.
#
# Wiring:
#   RULESET_STRATEGY_CONFIG_PATH=/config/...yaml -> the skill-gate ruleset config
#   CREWRIFT_PRIME_INTERVIEW_ENABLED=0           -> interview hard gate OFF (the
#     platform interview-mode launcher is not wired yet, so an enabled gate would
#     INFRA-HOLD every candidate forever; interview support will move into the game
#     itself later). Qualification runs on the three-skill gate only until then.
#   CREWRIFT_PRIME_MAX_SPEND_PER_POD_USD=10      -> $10 LLM spend cap per player
#     pod per episode. Enforced via the platform's Bedrock-sidecar mechanism:
#     the commissioner syncs this into the league's
#     episode_player_pod_llm_spend_limit_usd setting (POST /v2/leagues/{id}/settings),
#     which the dispatcher injects into each player pod's Bedrock sidecar as
#     BEDROCK_SIDECAR_SPEND_LIMIT_USD; also stamped as the max_spend_per_pod_usd
#     episode tag for observability.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RULESET_STRATEGY_CONFIG_PATH=/app/crewrift_prime.yaml \
    CREWRIFT_PRIME_INTERVIEW_ENABLED=0 \
    CREWRIFT_PRIME_MAX_SPEND_PER_POD_USD=10

WORKDIR /app

# 1) Install the vendored stock commissioners package (unmodified upstream).
#    NOTE: the Competition leaderboard now ranks by WIN RATE (all-time episodes
#    won / all-time episodes played; see rank_division / _win_total_board), NOT
#    OpenSkill MMR. The OpenSkill install and mmr.py are retained only as an
#    unused, test-covered ranking option and are no longer on the live scoring path.
COPY vendor /app/vendor
RUN pip install --no-cache-dir /app/vendor "openskill>=6.0.0"

# 2) Overlay the Crewrift Prime decision logic, subclass, REST worker, debug
#    script, and ruleset config.
#    NOTE: the qualifier reads the episode's per-slot RESULTS JSON artifact
#    (/jobs/{job_id}/artifacts/results) directly, so the Nim replay expander +
#    replay_parser.py are NO LONGER part of the image (Dependency A retired).
COPY decision.py /app/decision.py
COPY mmr.py /app/mmr.py
COPY game_results_loader.py /app/game_results_loader.py
COPY xp_request_client.py /app/xp_request_client.py
COPY platform_api.py /app/platform_api.py
COPY platform_manager.py /app/platform_manager.py
COPY interview.py /app/interview.py
COPY crewrift_prime_skill_commissioner.py /app/crewrift_prime_skill_commissioner.py
COPY debug_decision.py /app/debug_decision.py
COPY crewrift_prime.yaml /app/crewrift_prime.yaml

# Reconcile once, then advance the complete league lifecycle through REST APIs.
CMD ["python", "/app/platform_manager.py", "run"]
