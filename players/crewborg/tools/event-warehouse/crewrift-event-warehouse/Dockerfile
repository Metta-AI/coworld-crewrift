# Self-contained image for the batch event warehouse.
#
# This builds FROM the event-reporter image, which already bundles the compiled
# CrewRift expand_replay helper (/usr/local/bin/crewrift-expand-replay), the
# /workspace/crewrift tree, and the installed crewrift_event_reporter package.
# The warehouse reuses the reporter's extraction code IN-PROCESS (it imports
# extract_episode_rows and runs it across a process pool), so the helper binary
# must be present in this image — but it is inherited from the base, never
# recompiled here.
#
# Build the base first, then this:
#   docker build --platform linux/amd64 \
#     --build-context crewrift=/Users/jamesboggs/coding/coworlds/coworld-crewrift \
#     -t crewrift-event-reporter:local ../crewrift-event-reporter
#   docker build -t crewrift-event-warehouse:local .

ARG EVENT_REPORTER_IMAGE=crewrift-event-reporter:local
FROM ${EVENT_REPORTER_IMAGE}

WORKDIR /app

# The base image already has crewrift_event_reporter, pyarrow, and pydantic
# installed — those are the warehouse's only runtime dependencies. Install just
# the warehouse package with --no-deps so pip does not try to re-resolve the
# path-based crewrift-event-reporter source (absent from this build context).
COPY pyproject.toml README.md ./
COPY crewrift_event_warehouse ./crewrift_event_warehouse
RUN pip install --no-cache-dir --no-deps .

ENTRYPOINT ["crewrift-event-warehouse"]
CMD ["--help"]
