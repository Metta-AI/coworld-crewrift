# Crewborg

A [Player-SDK](../../player_sdk/) agent that plays **Crewrift**, a Coworld
social-deduction game (Among Us–style). Crewborg plugs Crewrift-specific
perception, belief, modes, and strategy into the SDK's two-loop runtime and ships
as a Docker image the Coworld runner launches.

- **Design spec:** [`design.md`](./design.md) — the settled architecture.
- **Orientation:** [`AGENTS.md`](./AGENTS.md) — codebases, protocol, source pointers.
- **Design docs:** [`docs/designs/`](./docs/designs/) — living deep-dives, e.g.
  [`suspicion.md`](./docs/designs/suspicion.md) (the Bayesian model + likelihood-ratio
  table + how we learn/improve the weights) and
  [`agent-tracking.md`](./docs/designs/agent-tracking.md) (probabilistic location
  tracking for imposter search).
- **Benchmark workflow:** [`docs/experience-request-benchmark-analysis.md`](./docs/experience-request-benchmark-analysis.md)
  captures the Observatory experience-request process used to run and analyze
  hosted Crewrift policy matchups.

## What it does

Crewborg plays **both roles** end-to-end. As a live crewmate it does tasks,
attends meetings, reports bodies, flees believed imposters, and **votes out the
most-likely imposter**; as a crewmate ghost it keeps finishing tasks with
wall-ignoring navigation and skips body reports / imposter avoidance. A
**Bayesian suspicion model** (`strategy/suspicion.py`) maintains a
posterior `P(imposter)` per player (a remaining-imposter-budget prior updated by
likelihood ratios for witnessed kills/vents, graded event-log cues, and social
who-sus'd-who cues parsed from meeting chat); it flees anyone over a probability
threshold, and at meetings a **game-theory vote policy**
(`strategy/meeting/vote_policy.py`) votes the highest-`P` player above a
state-dependent bar (zero in a must-eject endgame, tighter as the crew nears
parity), swaps a trailing vote onto the plurality near the deadline to avoid
splits, and — as an imposter — joins the crew plurality on a non-teammate. A vote
(player or skip) is always cast before the timer: a deadline auto-submit plus an
action-layer last-resort confirm guarantee it. Reporting a visible body takes
priority over fleeing. As an
imposter the
role-aware selector runs a priority order during `Playing`: **Evade** immediately
after its own kill (vent if possible, else move away from the body), **Report Body**
for non-fresh visible bodies, **Hunt** (kill ready *and* a victim visible → commit
to the most-isolated visible crewmate, close via a trajectory-led intercept, and
strike when in range and unwitnessed),
**Search** (within the kill lead window, walk ranked occupancy hot spots until a
victim is visible, then follow that target), and **Pretend** (the default — pick a
real task station in the highest-scoring occupancy room, penalizing rooms another
imposter is likely occupying, then fake the task for one task duration). Meetings
reuse **Attend Meeting**. When the meeting LLM is enabled (see
[LLM meetings](#llm-meetings)), Attend Meeting uses a fast Haiku-class LLM call on
the meeting fast path to chat, respond to other players, keep a tentative vote, and
submit early when requested; otherwise it preserves the deterministic
canned-chat + suspicion-vote fallback.
Hunt is gated on a visible kill opportunity whose isolation bar relaxes with
urgency, not merely on the cooldown ending. The action layer covers `kill` (edge-A
in KillRange), `vent` (level-B in VentRange), and emergency-button calls. With
`CREWBORG_DICK_MODE=1` (or `DICK_MODE=1`), live crewmates interrupt normal tasking
once before the first kill cooldown can clear, rush the emergency button, chat
`haha, fuck you imposters` only if their own button press opened the meeting,
skip-vote, and resume normal operation once that meeting closes.

## Layout

```
crewborg/
  __init__.py        build_runtime(): assemble the AgentRuntime + bake the map
  agent_tracking.py  reachability-disc location beliefs + coarse occupancy grid search
  types.py           the six SDK types + perceive/update_belief + phase machine
  action.py          action layer: stateful resolve_action + movement/edge FSMs
  nav.py             baked nav graph: pixel-validated A* + reachability + anchors + vent-teleport routing
  trace.py           trace & metrics sinks: tee fan-out + opt-in stderr-JSON streaming
  artifact.py        SQLite episode recorder + end-of-episode debug artifact upload
  events.py          CrewborgEventTracer: on_step_complete hook → domain.* events
  modes/             idle/normal/crewmate_ghost/dick_mode/attend_meeting/report_body/flee + evade/pretend/search/hunt (+ imposter_common helpers)
  strategy/          rule_based.py: mode selector + suspicion.py: Bayesian P(imposter) → believed_imposters + event_log.py: per-player observation log + occupancy.py: perception-tape predicates + opportunity.py: victim/witness logic + trajectory.py: intercept prediction
  perception/        Sprite-v1 decoder (decoder/tables) + resolution (resolve/entities)
  map/               vendored croatoan.resources + ported parser/bake (§6)
  coworld/           policy_player.py (bridge), scene.py, Dockerfile, entrypoint.sh
  viewer/            browser trace replay UI for agent-perspective forensics
  scripts/play_local.sh      run crewborg against a local Crewrift server
  scripts/fetch_episodes.py  download full data for the N most recent hosted episodes
  build.sh
  tests/
```

## Develop

From the workspace root (`~/coding/players_checkouts/players`):

```sh
uv sync --extra test
uv run pytest players/crewrift/crewborg/tests
uv run ruff check players/crewrift/crewborg
```

## Run locally

Start a Crewrift dev server (see `AGENTS.md` §"Connecting / running locally"),
then:

```sh
players/crewrift/crewborg/scripts/play_local.sh
```

`COGAMES_ENGINE_WS_URL` defaults to `ws://localhost:2000/player?slot=0&token=`;
override it to point elsewhere.

Set `CREWBORG_BE_DUMB=1` (or `BE_DUMB=1`) for the aggressive imposter experiment:
during `Playing`, imposters skip Pretend/Evade/body reports and stay in Search
unless kill-ready with a visible victim, then Hunt.

Set `CREWBORG_DICK_MODE=1` (or `DICK_MODE=1`) for the crewmate emergency-button
experiment: once the first kill cooldown gets within the hardcoded worst-case
button-walk budget plus a 10-tick buffer, the bot calls a meeting, sends
`haha, fuck you imposters` only if that call opened the meeting, skip-votes, then
resumes tasking. Crewrift's default config allows one emergency button call per
player, so the strategy intentionally treats this as a one-shot interruption.

## Logging & the episode artifact

Crewborg records its full, unfiltered trace/metric stream into an in-memory
SQLite database (`artifact.py`) instead of streaming JSON to stderr. At episode
end the bridge zips `trace.db` + `summary.json` and uploads them to the per-slot
`COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` the Coworld runner injects (presigned
`https://` PUT hosted, `file://` path on local runs; absent ⇒ skipped). The
upload is best-effort: a missing or failed artifact never fails the episode.
Inspect an artifact with any sqlite client:

```sh
unzip crewborg.zip && sqlite3 trace.db \
  'SELECT tick, event, data FROM traces WHERE event = "domain.vote_cast"'
```

Tables: `traces(seq, wall_time, tick, event, data)` and
`metrics(seq, wall_time, kind, name, value, tags)` — `data`/`tags` are JSON text
(use SQLite's `json_extract`). `summary.json` carries per-event counts, the tick
range, and dropped-row counts (rows are capped to bound memory).

Stderr JSON streaming remains available for local debugging but is now opt-in:
setting any `CREWBORG_TRACE*` env enables it (in addition to the artifact).
The lean stderr stream (durable domain events, action attempts, meeting
chat/vote decisions, per-player event deltas, occupancy seek changes, and a
ranked `suspicion_snapshot` at every meeting — see `design.md` §11) comes back
with e.g. `CREWBORG_TRACE_GROUPS=lean`. Per-tick `decision_snapshot`, viewer
frames, suspicion ticks, kill-state dumps, and occupancy snapshots are still
only *generated* at the debug levels: set `CREWBORG_METRICS=1` to include
metrics on stderr, `CREWBORG_TRACE=viewer` for the per-tick replay view model
consumed by the browser UI, or `CREWBORG_TRACE=debug` for the full framework
trace plus viewer frames and heavier suspicion / kill / occupancy debug dump.
Set `CREWBORG_LLM_TRACE_RAW=1` (or `CREWBORG_TRACE=debug`) to include raw LLM
request/response text.

For targeted traces without full debug volume, set `CREWBORG_TRACE_GROUPS` to a
comma-separated list. Useful groups include `voting`/`meeting`, `action`,
`decision`, `suspicion`, `kill`, `occupancy`, `knowledge`, `chat`, `llm`,
`viewer`, `framework`, `mode`, `task`, `state`, `belief`, `lean`, and `all`.
`CREWBORG_TRACE_INCLUDE` and `CREWBORG_TRACE_EXCLUDE` accept comma-separated glob
patterns; unqualified domain names like `meeting_*` or `vote_cast` also match
`domain.meeting_*` / `domain.vote_cast`. For compact per-tick decision traces,
combine `CREWBORG_TRACE_GROUPS=decision` with
`CREWBORG_TRACE_DECISION_FIELDS=mode,intent,command,threats` (or any top-level
`decision_snapshot` fields).

## View trace replays

Open [`viewer/index.html`](./viewer/index.html) in a browser and load a
`logs/crewborg_slot{N}_v{V}.log` file from `scripts/fetch_episodes.sh`, or any
local stderr trace captured from `scripts/play_local.sh`. Logs generated with
`CREWBORG_TRACE=viewer` or `CREWBORG_TRACE=debug` include:

- `domain.viewer_map`: static rooms, task stations, vents, button, and home.
- `domain.viewer_occupancy_grid`: the reachable coarse grid used by the tracker.
- `domain.viewer_frame`: one per tick, with active mode + directive params,
  current intent, self/camera, nav route and target, roster/body beliefs, task
  state, and the live occupancy belief grid.

The viewer can still load older lean logs, but without `domain.viewer_frame` it
falls back to a sparse event timeline and cannot draw full map-space belief
overlays.

## Fetch hosted episode data

Download the full data for the most recent episodes crewborg played in the
hosted Crewrift league (auth via `softmax login`):

```sh
players/crewrift/crewborg/scripts/fetch_episodes.sh -n 10
players/crewrift/crewborg/scripts/fetch_episodes.sh -n 5 --version 2 --out /tmp/eps
```

Writes one directory per episode (default `episode_data/`, gitignored) plus an
`index.json` summary. Each episode dir holds `episode.json` +
`episode_request.json` (metadata, participants, scores, game_config), the
binary `replay.json` (the whole game — load it with the
[`COGAME_LOAD_REPLAY_URI`](docs/crewrift-replays.md) viewer recipe) and its raw
compressed `replay.json.z`, and `logs/crewborg_slot{N}_v{V}.log` — crewborg's
own per-tick stderr trace for each slot it controlled. The run is idempotent
(`--force` to re-download); see `--help` for `--no-replay` / `--no-logs`.

The official `coworld episodes` / `coworld replays` / `coworld episode-logs`
commands *would* cover similar ground, but as of 2026-06-02 they are **broken
against the live server**: the server renamed its episode-request API
(`/v2/episode-requests*` → `/v2/experience-request*`) and even the latest CLI
(coworld 0.1.13) still calls the old paths, so those commands 404. This script
calls the current routes directly (and reads raw JSON), so it keeps working
across that kind of client/server drift — prefer it. (If you need the official
CLI, check `<api>/observatory/openapi.json` for the live route names first.)

## LLM meetings

During meetings, **Attend Meeting** can call a fast Haiku-class LLM to chat,
react to other players, hold a tentative vote, and submit early. The feature is
off by default; when it is disabled — or the call times out, returns late, or
returns no legal vote target — the mode falls back to the deterministic
canned-chat (`"no read, skipping"`) plus the Bayesian suspicion vote.

If the LLM is enabled but its calls keep failing — a permanent error
(HTTP 401/403/404, e.g. an ungated model or a bad key) on the first call, or two
failures otherwise — the mode latches onto that deterministic fallback for the
rest of the episode (tracing `meeting_llm_disabled`) so a broken backend can
never cost crewborg its vote.

Configuration is owned by the strategy, never the mode:
`read_meeting_params_from_env` resolves the environment once at construction and
stamps a `MeetingParams` onto the Attend Meeting directive, and the mode builds
its client from those params (`build_meeting_client`) without reading the
environment itself. The implementation lives in
[`strategy/meeting/llm.py`](./strategy/meeting/llm.py).

The system prompt is **role-specialized** and lives in
[`strategy/meeting/prompts.py`](./strategy/meeting/prompts.py), assembled from
three independently tunable tiers: `SHARED_BOILERPLATE` (the role-independent
output contract — edit rarely), `ROLE_GOALS` (per-role objective), and
`ROLE_STRATEGY` (per-role tactics — **the knob to tune**). Crewmate and imposter
tactics are edited separately; the imposter prompt is told never to vote or
accuse a teammate and to deflect toward a plausible crewmate. The client selects
the prompt from the player's role at call time, and unknown / not-yet-revealed /
ghost roles fall back to the crewmate prompt (never disclosing imposter tactics).

The teammate rule is also **enforced**, not just prompted: `valid_vote_targets`
(the single source of legal vote targets, feeding the LLM menu, the validator,
and the submit-time re-check) drops teammate colors when we are the imposter, so
crewborg cannot vote out a teammate regardless of what the model returns. If only
teammates remain alive it safely skips.

Three backends are supported. Pick one by setting the env vars below — in code
(local runs, your own Dockerfile `ENV`) or at upload time (next subsection). The
backend is chosen by `CREWBORG_LLM_PROVIDER` (`anthropic` | `bedrock` |
`openrouter`); if unset, a Bedrock flag selects Bedrock, a present
`OPENROUTER_API_KEY` selects OpenRouter, otherwise it defaults to the direct
Anthropic API.

**Direct Anthropic API.** Set `CREWBORG_LLM_MEETINGS=1` and provide
`ANTHROPIC_API_KEY`. Calls go straight to the Anthropic Messages API. Default
model `claude-haiku-4-5-20251001`.

**OpenRouter (recommended).** Set `OPENROUTER_API_KEY` (and optionally
`CREWBORG_LLM_PROVIDER=openrouter`). Calls route through OpenRouter's
OpenAI-compatible Chat Completions API (`https://openrouter.ai/api/v1`) via the
`openai` SDK, so any OpenRouter-hosted model works without depending on Bedrock
or a single vendor SDK. A present key implies `CREWBORG_LLM_MEETINGS`, so it
alone turns the feature on. Default model `anthropic/claude-haiku-4.5`; set any
`vendor/model` slug with `CREWBORG_LLM_MODEL`. The key is supplied at runtime
from the secrets manager (see below) and never baked into the image.

**AWS Bedrock.** Set any one of `USE_BEDROCK=1`, `CREWBORG_USE_BEDROCK=1`, or
`CLAUDE_CODE_USE_BEDROCK=1`. A Bedrock flag also implies `CREWBORG_LLM_MEETINGS`,
so it alone turns the feature on — no second flag and no `ANTHROPIC_API_KEY`
required. Calls route through the Anthropic SDK's `AnthropicBedrock` client,
which authenticates via the standard AWS environment (`AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, and `AWS_REGION` /
`AWS_DEFAULT_REGION`) — the same way the direct client reads
`ANTHROPIC_API_KEY`. The default model is the Bedrock inference-profile ID
`us.anthropic.claude-haiku-4-5-20251001-v1:0`. The image installs the `bedrock`
extra (`boto3`) so this path needs no extra setup; the dependency is inert
unless a Bedrock flag is set.

Shared tuning knobs (all backends):

| Env var | Default | Meaning |
|---|---|---|
| `CREWBORG_LLM_PROVIDER` | inferred | Force the backend: `anthropic`, `bedrock`, or `openrouter`. |
| `CREWBORG_LLM_MODEL` | backend default | Override the model (Anthropic model name, Bedrock inference-profile ID, or OpenRouter `vendor/model` slug). |
| `CREWBORG_LLM_MAX_TOKENS` | `512` | Response token cap. |
| `CREWBORG_LLM_TEMPERATURE` | `0.2` | Sampling temperature. |
| `CREWBORG_LLM_TIMEOUT_SECONDS` | `3.0` | Per-call client timeout. |
| `CREWBORG_LLM_TRACE_RAW` | off | Include raw request/response text in the trace (also on with `CREWBORG_TRACE=debug`). |

### Enabling a backend at upload time (secrets manager)

The deployed image bakes in `CREWBORG_LLM_MEETINGS=1` but ships **no
credentials** — secrets never live in the image. Provider keys are attached to a
policy version at upload time with `--secret-env KEY=VALUE`, which stores the
value in **AWS Secrets Manager** and injects it as an environment variable into
the policy's pods at runtime:

```sh
# OpenRouter (recommended): the key is stored in Secrets Manager and injected
# as OPENROUTER_API_KEY at runtime. The present key auto-selects the provider.
coworld upload-policy <image> --name crewborg --secret-env OPENROUTER_API_KEY=sk-or-...

# Direct Anthropic API instead:
coworld upload-policy <image> --name crewborg --secret-env ANTHROPIC_API_KEY=sk-ant-...

# AWS Bedrock instead (no key; uses the pod's IRSA role):
coworld upload-policy <image> --name crewborg --use-bedrock
```

`--use-bedrock` is shorthand for `--secret-env USE_BEDROCK=true`. It both sets
the flag the code reads and routes this policy version's pods to a
Bedrock-enabled service account that supplies the AWS credentials on the hosted
runner, so no AWS keys need to be passed explicitly. The exact `--secret-env` /
secrets-manager mechanics are documented in the optimizer
`coworld-operations` skill (`optimizer/skills/coworld-operations/SKILL.md`).

## Build the image

```sh
players/crewrift/crewborg/build.sh            # build + emit manifest snippet
players/crewrift/crewborg/build.sh --no-build # only render manifests
```

The build context is the repo root; the image installs the local `players`
package (no mettagrid/cogames stack needed). **stdout = protocol channel,
stderr = logs/traces.**
