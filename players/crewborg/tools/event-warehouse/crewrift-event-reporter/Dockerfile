FROM debian:bookworm-slim AS crewrift-helper-build

RUN apt-get update && \
  apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git && \
  rm -rf /var/lib/apt/lists/*

RUN if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
    curl -fsSL \
      -o /usr/local/bin/nimby \
      https://github.com/treeform/nimby/releases/download/0.1.26/nimby-Linux-X64; \
  elif [ "$(dpkg --print-architecture)" = "arm64" ]; then \
    curl -fsSL \
      -o /usr/local/bin/nimby \
      https://github.com/treeform/nimby/releases/download/0.1.26/nimby-Linux-ARM64; \
  else \
    echo "unsupported arch: $(dpkg --print-architecture)" && exit 1; \
  fi && \
  chmod +x /usr/local/bin/nimby && \
  nimby use 2.2.4

ENV PATH="/root/.nimby/nim/bin:$PATH"

WORKDIR /workspace/crewrift
COPY --from=crewrift nimby.lock .
RUN nimby --global sync nimby.lock

COPY --from=crewrift . .
RUN nim c \
  -d:release \
  -d:useMalloc \
  --opt:speed \
  --stackTrace:on \
  --nimcache:/tmp/crewrift-helper-nimcache \
  --out:/usr/local/bin/crewrift-expand-replay \
  tools/expand_replay.nim

FROM python:3.13-slim

RUN apt-get update && \
  apt-get install -y --no-install-recommends ca-certificates libcurl4 && \
  rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY crewrift_event_reporter ./crewrift_event_reporter
COPY README.md ./
RUN pip install --no-cache-dir .

COPY --from=crewrift-helper-build /usr/local/bin/crewrift-expand-replay /usr/local/bin/crewrift-expand-replay
COPY --from=crewrift-helper-build /workspace/crewrift /workspace/crewrift

ENV CREWRIFT_EXPAND_REPLAY=/usr/local/bin/crewrift-expand-replay
EXPOSE 8080

CMD ["uvicorn", "crewrift_event_reporter.app:app", "--host", "0.0.0.0", "--port", "8080"]
