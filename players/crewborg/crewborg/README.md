# Crewborg

Crewborg is a **Player-SDK agent that plays Crewrift** — the Coworld social-deduction
game (Among Us–style: crewmates do tasks and vote out imposters; imposters kill, vent,
and blend in). It plays **both roles** end-to-end. Decision-making is
**deterministic-first** — a rule-based mode selector over a Bayesian world model — with
**optional LLM layers** (meeting chat/vote, a gameplay commander) that are **gated off by
default** and byte-identical to deterministic play when disabled.

This README is the **front door for a coding agent**: enough to start working — the model,
where everything is, how it plays, how it's built — then pointers to depth. The settled
architecture lives in **[`design.md`](./design.md)** (which opens with a Contents index —
jump straight to the section you need).

| | |
|---|---|
| **Package** | `crewborg` (this directory is `crewborg/`) |
| **Plays** | `crewrift` |
| **Entrypoint** | `python -m crewborg.coworld.policy_player` |
| **Imports** | `players.player_sdk` only (the shared Player SDK) — full install set in [Dependencies](#dependencies) |

---

## The 60-second model

Crewborg plugs game-specific code into the Player SDK's per-tick runtime as **three tiers**:

```
STRATEGY (mode selector)   rules over belief → which MODE is active     strategy/
   │  ModeDirective
   ▼
MODE (behavioral stance)   one Intent per tick, read from belief        modes/
   │  Intent
   ▼
ACTION LAYER (executor)    Intent → wire Command, stateful over ticks   action.py
```

Every tick the SDK runs one fold (`build_runtime` in `__init__.py` wires it):

```
perceive → update_belief (+ agent_tracking + event_log + social_evidence + suspicion)
        → strategy picks mode → mode.decide → resolve_action → wire Command
```

**Three invariants (from the SDK — do not violate):**

- **Belief is the only interface** the strategy and modes see. Raw scene data — especially
  sprite pixels — never enters belief.
- **Modes emit symbolic intents, never wire actions.** All movement, button/cursor timing,
  chat buffering, and momentum live in the **action layer** (`action.py`).
- **The agent stays live under strategy stall** via a default directive + directive TTLs;
  the inner loop never blocks on the strategy.

Full detail: [`design.md` §1](./design.md#1-architecture).

---

## Where everything is

```
crewborg/              package crewborg
  __init__.py          build_runtime(): assembles the AgentRuntime + bakes the static map
  types.py             the six SDK types + perceive/update_belief + the phase machine
  action.py            action layer: stateful resolve_action — the ONLY transport code
  agent_tracking.py    reachability-disc location beliefs + coarse expected-crew occupancy grid
  events.py            CrewborgEventTracer: on_step_complete hook → domain.* trace events
  trace.py             trace selection: event families + env-derived filtering
  nav.py / navbake.py  pixel-validated A* nav graph over the walkability mask (+ vent-teleport edges)
  perception/          Sprite-v1 scene decoder → resolved (label, world-x/y) entities
  map/                 vendored croatoan.resources + ported parser/bake + the prebaked nav asset
  modes/               behavioral stances —
                         crew:     normal · attend_meeting · report_body · accuse
                         imposter: search · recon · hunt · evade   (+ idle, imposter_common)
                         _deprecated/  cold-stored (retired pretend mode) — DO NOT USE
  strategy/            rule_based.py (mode selector) · suspicion.py (Bayesian P(imposter))
                         · social_evidence.py (vote/chat evidence) · event_log.py (per-player log)
                         · occupancy.py · opportunity.py · trajectory.py · path_prediction.py
  strategy/meeting/    the LLM meeting chat/vote path (+ spaCy chat parsing) — GATED, off by default
  strategy/commander/  the LLM gameplay commander (biases belief priorities) — GATED, off by default
  coworld/             policy_player.py (the websocket bridge) · scene.py (Sprite-v1 decode) · Dockerfile
  data/                suspicion_weights.json — fitted evidence weights loaded at runtime
  docs/                cross-cutting deep-dives (how each subsystem works) — see docs/README.md index
  viewer/              browser trace-replay UI for agent-perspective forensics
  tests/               ~37 modules: decoder, belief, suspicion, nav, modes, runtime, bridge smoke
  design.md            THE architecture spec (has a Contents index)
  version_log.md       each uploaded policy version → code + runtime config + result
```

---

## How it plays (the strategy, in brief)

The **mode selector** (`strategy/rule_based.py`) runs every tick — pure rules over belief —
and picks one mode per role. Depth in [`design.md` §7 (modes)](./design.md#7-modes) and
[§10 (selector + suspicion)](./design.md#10-strategy-mode-selector).

**As a crewmate.** Do tasks (**Normal**) while a **Bayesian suspicion model**
(`strategy/suspicion.py`) maintains a posterior `P(imposter)` per player — a combinatorial
prior updated by likelihood ratios for witnessed kills/vents, being tailed, and graded
event-log cues (see [`docs/suspicion.md`](./docs/suspicion.md)). A body in
view → **Report Body**. Being actively tailed by a player it's grown suspicious of →
**Accuse** (drop tasks, slam the one-shot emergency button). At meetings (**Attend
Meeting**): with a *clear leading suspect*, **accuse then vote** them, chatting
`"<color> sus: <reasons>"` from the ranked event log; on a flat field, stay **silent and
skip**.

**As an imposter** (priority order during play):

1. **Evade** — for a window right after its own kill: *re-approach* the densest expected-crew
   area (not flee), so a victim cluster is nearby when the window ends.
2. **Hunt** — kill ready **and** a victim visible: commit to the most-isolated visible
   crewmate, close via a trajectory-led intercept, strike when in range and **unwitnessed**
   (the witness check is dropped after the *first* kill — the bottleneck is *converting* the
   2nd kill, not stealth).
3. **Recon** — kill nearly ready and a crewmate has been seen: beeline to the most-recently-seen
   crewmate so a victim is in hand the instant the kill comes ready.
4. **Search** — the always-on seeking stance: watch a task room, and when a crewmate leaves,
   follow them down the hallway (predicting their path) to stay *near crew* until a kill
   window opens.

Imposters **never report bodies** (self-reporting reset the kill cooldown and killed snowball
kills). At meetings they **deflect onto crewmates** (never a teammate) or **bandwagon** with
fabricated safe cues, in the *identical* chat format a crewmate uses (so it isn't a tell).

---

## How it's implemented (in general)

- **Player SDK two-loop runtime.** The SDK (`players.player_sdk`) is a generic, transport-
  and game-agnostic framework: a fast symbolic inner loop + a slower strategy loop.
  `build_runtime()` (`__init__.py`) composes crewborg's six types, three pure functions
  (`perceive` / `update_belief` / `resolve_action`), its modes, and the rule-based strategy
  into an `AgentRuntime`; the bridge then calls `runtime.step(scene)` each tick.
- **Deterministic-first.** v1 selection is pure rules with no reflexes — every transition
  (body→Report, Voting→Attend Meeting, kill-ready→Hunt) is re-evaluated each tick. The
  suspicion posterior's evidence weights are fitted offline and loaded from
  `data/suspicion_weights.json`.
- **LLM layers are gated and inert by default.** `strategy/meeting/`
  (`CREWBORG_LLM_MEETINGS=1`) and `strategy/commander/` (`CREWBORG_LLM_COMMANDER=1`) sit on
  top of the deterministic path behind circuit breakers. With the flags off, play is
  byte-identical and no model is contacted. The deterministic path must always cast a legal
  action — the LLM is never on the correctness path.
- **Crewborg owns its transport.** The SDK's JSON bridge doesn't fit a binary game, so
  `coworld/policy_player.py` is crewborg's own websocket bridge: connect to
  `COWORLD_PLAYER_WS_URL`, decode Sprite-v1 → `SceneState`, drive the runtime, retry the
  connect on a startup race, and exit when the server closes the socket after frames were seen.

---

## Where to make changes

The cognitive stack is one-responsibility-per-file. Common edits and where they live:

| To change… | Start in | Validate with |
|---|---|---|
| Imposter **victim choice / kill timing** | `modes/hunt.py`, `strategy/opportunity.py`, `strategy/trajectory.py` | `tests/test_imposter_modes.py`, `tests/test_opportunity.py` |
| Imposter **seeking** (room watch / follow) | `modes/search.py`, `modes/recon.py`, `strategy/path_prediction.py` | `tests/test_search_mode.py`, `tests/test_recon_mode.py`, `tests/test_path_prediction.py` |
| Crewmate **who we suspect / vote / accuse** | `strategy/suspicion.py`, `strategy/social_evidence.py`, `modes/accuse.py`, `modes/attend_meeting.py` | `tests/test_suspicion.py`, `tests/test_accusation.py` |
| **Meeting chat / LLM votes** | `strategy/meeting/` | `tests/test_meeting_llm.py`, `tests/test_imposter_meeting.py` |
| **Which mode is selected** (the rules) | `strategy/rule_based.py` | `tests/test_strategy.py` |
| **How movement / buttons execute** | `action.py` | `tests/test_action.py` |
| **Perception / sprite decoding** | `perception/`, `coworld/scene.py` | `tests/test_decoder.py`, `tests/test_resolve.py` |
| **Navigation / pathing** | `nav.py`, `navbake.py` | `tests/test_nav.py`, `tests/test_navbake.py` |

To trace *why* a decision happened, run with `CREWBORG_TRACE=debug` and read the `domain.*`
events (format: [`docs/trace-logs.md`](./docs/trace-logs.md); replay them in [`viewer/`](./viewer/)).

---

## The Player SDK

`players.player_sdk` is the generic two-loop agent framework crewborg builds on. It is
**imported, not vendored**: installed from the public **`Metta-AI/coworld-tools`** monorepo
(`players/` subdirectory) via a **pinned archive tarball** — see
[`coworld/Dockerfile`](./coworld/Dockerfile) and the build pin. Crewborg imports **only**
`players.player_sdk` from it.

The SDK is generic over six type parameters —
`AgentRuntime[Observation, Percept, Belief, ActionState, Intent, Command]` — which crewborg
defines (`types.py`). Crewborg supplies the three pure functions + modes + strategy; the SDK
owns the loop, mode validation, strategy runners, and tracing. The SDK-facing contract is in
[`design.md` §2 (types)](./design.md#2-types) and [§3 (transport & bridge)](./design.md#3-transport--bridge).

---

## Dependencies

Crewborg runs in a **Python 3.12** environment. The complete set it installs — authoritative
source is [`coworld/Dockerfile`](./coworld/Dockerfile):

| Dependency | Source | Why |
|---|---|---|
| **`players[bedrock]`** — the Player SDK | the public **`Metta-AI/coworld-tools`** monorepo (`players/` subdir), as a **pinned archive tarball**: `https://github.com/Metta-AI/coworld-tools/archive/<ref>.tar.gz#subdirectory=players` | the framework crewborg builds on; crewborg imports only `players.player_sdk`. The `[bedrock]` extra adds **`boto3`** for Bedrock routing (inert unless an LLM flag + Bedrock are set). |
| `numpy`, `pydantic`, `websockets`, `cramjam` | the SDK's base deps (pulled in transitively) | masks/arrays · typed models · the websocket transport · Snappy decode |
| **`spacy>=3.8,<3.9`** + **`click`** | PyPI | dependency-parse negation scope for the imposter chat-bandwagon signal (`strategy/meeting/chat_nlp.py`); `click` is a spaCy CLI import the slim base omits, so it's installed explicitly |
| **`en_core_web_sm` 3.8.0** | pinned wheel from `explosion/spacy-models` | the spaCy English model the chat NLP loads (lazily, in a background thread) |

> **SDK source — note the move.** The Player SDK now lives in **`Metta-AI/coworld-tools`** (the
> old `Metta-AI/players` repo is archived). It's installed from a **tarball**, not a git source,
> to sidestep coworld-tools' broken `co-gas` submodule that breaks recursive clones (coworld-tools
> issue #13). The `<ref>` is **pinned** for reproducible builds — bump it deliberately, never to a
> floating `main`.

---

## Build, run, test

```sh
# Run one local episode (point at a running Crewrift server; the runner fills slot/token):
COWORLD_PLAYER_WS_URL='ws://127.0.0.1:2000/player?slot=0&token=' \
  python -m crewborg.coworld.policy_player

# Tests (from this package directory; needs the Player SDK installed — see Dependencies):
python -m pytest tests/
```

**A local game needs a running Crewrift server and ≥1 other bot to reach the player count.**
This repo's **top-level `README.md`** (its *"Run the game locally"* section) has the server +
`notsus` commands; point `COWORLD_PLAYER_WS_URL` at that server. Tests need no server.

The container image is built from [`coworld/Dockerfile`](./coworld/Dockerfile) (Python
3.12-slim; installs `players[bedrock]` from the pinned tarball + spaCy; `CMD` is the
entrypoint above). The image bakes **no** behavior/experiment env — variant flags
(`CREWBORG_*`) are set at upload time so the image stays behavior-neutral. The full
build → upload → submit workflow lives in the player-directory orientation (top-level
`AGENTS.md`).

---

## Environment variables

Crewborg reads all configuration from the environment; the image bakes **none** of these (set
them at upload/run time). **Only the LLM and behavior groups change play** — tracing, metrics, and
transport tuning never do.

**Runner-provided** (set by the Coworld runner — you don't):

| Variable | Default | Effect |
|---|---|---|
| `COWORLD_PLAYER_WS_URL` | — *(required)* | the player websocket `ws://…/player?slot=N&token=…`; the bridge connects to it exactly. Alias: `COGAMES_ENGINE_WS_URL`. |
| `COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` | — | presigned URL the bridge zips + uploads traces to at exit. |

**LLM layers — meetings & gameplay commander** (both OFF by default → deterministic):

| Variable | Default | Effect |
|---|---|---|
| `CREWBORG_LLM_MEETINGS` | off | `1` enables the meeting chat/vote LLM (`strategy/meeting/`); off → deterministic accuse-and-vote / silent-skip. |
| `CREWBORG_LLM_COMMANDER` | off | `1` enables the gameplay commander (`strategy/commander/`); off → byte-identical deterministic play. |
| `CREWBORG_COMMANDER_FORCE` | — | JSON priority stamped into belief each tick, bypassing the LLM (deterministic QA/control). |
| `CREWBORG_LLM_MODEL` | Haiku-class | override the model id. |
| `CREWBORG_LLM_TIMEOUT_SECONDS` | `3.0` | per-call timeout (meetings are time-boxed). |
| `CREWBORG_LLM_MAX_TOKENS` | `512` | LLM max output tokens. |
| `CREWBORG_LLM_TEMPERATURE` | `0.2` | LLM temperature. |
| `CREWBORG_LLM_PROMPT_DIR` | bundled | override the prompt directory. |
| `CREWBORG_LLM_TRACE_RAW` | off | `1` traces raw LLM payloads (also on under `CREWBORG_TRACE=debug`). |

**Bedrock / API routing** (only consulted when an LLM flag is on):

| Variable | Default | Effect |
|---|---|---|
| `USE_BEDROCK` | off | `1` routes LLM calls through AWS Bedrock (via `boto3`). |
| `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` | — | in-pod Bedrock endpoint. **In sidecar mode the runner strips `USE_BEDROCK` and injects this — the meeting + commander factories gate on this endpoint's presence, not on `USE_BEDROCK`.** |
| `ANTHROPIC_API_KEY` | — | direct Anthropic API key (alternative to Bedrock). |

**Behavior toggles** (change play):

| Variable | Default | Effect |
|---|---|---|
| `CREWBORG_BE_DUMB` (alias `BE_DUMB`) | off | `1` = aggressive imposter selector: Search/Hunt only (skip Evade + body reports). An experiment. |
| `CREWBORG_CHAT_NLP` | **on** | `0` kills the spaCy chat NLP (never imports spaCy); the imposter chat-bandwagon then degrades to vote-only. |
| `CREWBORG_RECON_WINDOW` | `100` | Recon lead window (ticks before kill-ready) to pre-position on a victim. |
| `CREWBORG_EVADE_TICKS` | `72` | Evade window (ticks) after our own kill before returning to the kill loop. |
| `CREWBORG_SUSPICION_WEIGHTS` | bundled `data/suspicion_weights.json` | path to a fitted weights file; `0` forces the legacy hand-tuned model. Fitting workflow: [`docs/suspicion.md`](./docs/suspicion.md). |
| `CREWBORG_WEIGHTS_VOTE_P` | `0.9` | vote-probability threshold for the fitted suspicion model. |

**Tracing & metrics** (no effect on play):

| Variable | Default | Effect |
|---|---|---|
| `CREWBORG_TRACE` | off | trace verbosity; `debug` adds the full per-tick dump on top of the `domain.*` events. |
| `CREWBORG_TRACE_GROUPS` | all | restrict tracing to named event families (e.g. `commander`, `meeting`). |
| `CREWBORG_TRACE_INCLUDE` / `CREWBORG_TRACE_EXCLUDE` | — | add / remove specific event families. |
| `CREWBORG_TRACE_DECISION_FIELDS` | — | include extra decision fields in traces. |
| `CREWBORG_TRACE_OUTPUTS` | `jsonl@artifact` | trace sink(s); default zips + uploads at exit, falling back to `jsonl@stderr` when no upload URL. |
| `CREWBORG_METRICS` | off | metrics sink spec. |

**Transport tuning** (rarely set):

| Variable | Default | Effect |
|---|---|---|
| `CREWBORG_RECONNECT_INTERVAL` | `0.1` | initial-connect retry interval (s) while the engine socket comes up. |
| `CREWBORG_RECONNECT_DEADLINE` | `120` | initial-connect deadline (s) before giving up. |
| `CREWBORG_MIDGAME_RECONNECTS` | `5` | mid-game reconnect attempts after a dropped socket. |
| `CREWBORG_MIDGAME_RECONNECT_INTERVAL` | `0.25` | mid-game reconnect interval (s). |

*(The source also carries a couple of dev-capture toggles — `CREWBORG_CAPTURE_WALKABILITY`, and
`PRED_UI_PORT` for the path-prediction UI — not used in normal play.)*

---

## Going deeper

- **[`design.md`](./design.md)** — the architecture spec (start at its Contents index).
- **[`docs/`](./docs/README.md)** — the cross-cutting deep-dives: *how each subsystem works
  end-to-end across files*. Start at the **[docs index](./docs/README.md)**; it covers
  [perception & belief](./docs/perception-and-belief.md), [navigation](./docs/navigation.md),
  [imposter play](./docs/imposter-play.md) & [crewmate play](./docs/crewmate-play.md),
  [suspicion](./docs/suspicion.md), [agent tracking](./docs/agent-tracking.md),
  [meetings](./docs/meetings.md), the [LLM commander](./docs/commander.md), and
  [trace logs](./docs/trace-logs.md).
- **[`docs/trace-logs.md`](./docs/trace-logs.md)** — the `domain.*` trace-log format + how to
  read a game; **[`viewer/`](./viewer/)** — the trace-replay viewer.
- **[`version_log.md`](./version_log.md)** — what each uploaded version changed and how it scored.
- **Crewrift rules + the Sprite-v1 wire protocol + source pointers** — this repo
  (`Metta-AI/coworld-crewrift`): `README.md`, `docs/sprite_v1.md`, `src/crewrift/sim.nim`, and
  the reference bot `players/notsus/`. The consolidated game + SDK orientation is the
  player-directory **top-level `AGENTS.md`**.
