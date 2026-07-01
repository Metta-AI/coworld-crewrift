# LLM gameplay commander

The **gameplay commander** is crewborg's opt-in, gated-off LLM layer for the **Playing**
phase. It is a background daemon that periodically asks an LLM to read the live game state
and write a small set of sticky *priorities* into `belief.commander`. The deterministic
modes read those priorities and change *how* they execute — which room to do tasks in, which
room to hunt, which player to chase, how to break ties — without ever changing *which* mode
runs. It is the realization of the "future LLM seam" that [`../design.md`](../design.md)
anticipates, scoped to live play.

The commander is **disabled by default**: when off, no worker thread runs, no context is
serialized, `belief.commander` stays `None`, and play is byte-identical to deterministic
crewborg. This document is the cross-cutting reference for the layer. For the modes' base
behavior see [`./imposter-play.md`](./imposter-play.md) and
[`./crewmate-play.md`](./crewmate-play.md); for the separate meeting/chat LLM (a different
system) see [`./meetings.md`](./meetings.md); for trace event mechanics see
[`./trace-logs.md`](./trace-logs.md). Orientation lives in [`../README.md`](../README.md).

---

## 1. Scope and boundaries

What the commander does:

- **Steers positioning and targeting for both roles** by setting priorities the modes
  consume — crewmate `target_room` / `target_task` / `posture`; imposter `hunt_room` /
  `target_player` / `avoid_room`.
- **Exposes two opt-in imposter risk levers** (`allow_witnessed_kill`, `skip_evade`), each
  gated on a `danger_reason` (§6).
- **Runs continuously off the per-tick path** on a background daemon thread, refreshing
  priorities as fast as the LLM returns; the inner loop reads the latest published value.
- **Emits `domain.commander_*` traces** so what the LLM proposed, its latency, and whether it
  changed behavior are observable (§8).

What it does **not** do:

- **It does not select modes.** `RuleBasedStrategy.select` still picks the mode every tick and
  owns every reactive transition (Voting→Attend Meeting, body→Report, tail→Accuse,
  just-killed→Evade, kill-ready+victim→Hunt). The commander only attaches a `commander`
  inference; the directive's mode is never overridden. The single exception is the
  `skip_evade` danger lever, which suppresses the just-killed→Evade transition (§6).
- **It does not issue actions, movement, or paths.** It only sets belief priorities; the modes
  and action layer produce every intent.
- **It does not block or pace the game loop.** An LLM call runs on its own thread; a slow or
  dead worker just means no fresh priorities that tick, which the consumer's TTL ages out.
- **It does not override safety/correctness gates** except the two opt-in danger levers: kill
  range, kill-readiness, the witness test, the post-kill Evade, victim commitment, and the
  self/teammate guards all stay intact.
- **It does not touch the meeting/chat LLM.** Meetings are a separate brain
  ([`./meetings.md`](./meetings.md)); the only shared code is the Bedrock-enablement detection
  (§7). The commander never chats or votes, and its prompt says so explicitly.

## 2. Architecture — two loops, one shared belief

Two producers write at two cadences; the inner loop only ever *reads* the latest priorities.

```
  INNER LOOP  (every tick, never blocks)
  perceive → fold_belief → RuleBasedStrategy.select → mode.decide → resolve_action
                 ▲                  (picks mode, unchanged)   │ reads belief.commander
                 │ apply_commander_inferences                 │ to bias HOW it executes
                 │  → belief.commander (latest priorities)    ▼
  ─ ─ ─ ─ ─ ─ ─ ─┼─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─
                 │ lock-protected latest-value OverwriteBuffers (no shared mutable belief)
  OUTER LOOP  (LLM cadence, background daemon thread)
  take latest serialized context → build prompt (+ role doctrine) → LLM → parse JSON
       → publish raw priorities ──────────────────────────────────────────────────┘
```

`CommanderStrategy` (in `strategy/commander/strategy.py`) wraps the existing
`RuleBasedStrategy` and is installed by `crewrift/crewborg/__init__.py:build_runtime` as the runtime's
strategy, with `strategy/commander/strategy.py:apply_commander_inferences` registered as the
runtime's `apply_inferences` hook. Mode selection therefore stays per-tick and unchanged.

The master gate is read once at construction: `build_runtime` calls
`strategy/commander/llm.py:commander_feature_enabled` and passes the result as
`feature_enabled`. Each tick, `strategy/commander/strategy.py:CommanderStrategy.decide`:

1. delegates to `RuleBasedStrategy.select(belief)` for the mode directive (the reactive ladder
   is intact);
2. publishes a freshly serialized game-state snapshot to the worker (non-blocking
   `OverwriteBuffer.publish`);
3. takes the worker's latest raw priorities (non-blocking `take`, possibly `None`), sanitizes
   them against the current legal rooms/players, caches them in `_last`, and returns
   `StrategyResult(directive=<mode>, inferences={"commander": …})`.

The runtime folds `inferences` into `belief.commander` via `apply_commander_inferences`. A
tick with no fresh inference leaves `belief.commander` untouched (the previous value persists),
so priorities are **sticky** across the LLM's call latency.

**Cross-thread safety.** The worker never touches live belief. The inner loop serializes a
read-only state dict out through `worker.snapshots` and the worker hands a raw priorities dict
back through `worker.priorities`, both being the SDK's lock-protected latest-value
`OverwriteBuffer`. The only belief mutation is on the inner-loop thread.

## 3. The worker

`strategy/commander/worker.py:CommanderWorker` owns the daemon thread and the two buffers. Its
loop (`_run`):

- **Builds the LLM client lazily, on its own thread, from live `os.environ`**, via the injected
  `build_commander_client_from_env` factory. `_build_client` retries up to `build_attempts`
  (20) times at `retry_interval` (0.5 s) **only while the failure is specifically "no LLM
  backend configured"** — the sidecar endpoint and credentials can land slightly after
  start-up. A disable for any other reason (e.g. the flag is off) returns immediately, since a
  retry would never help.
- **Exits immediately if the built client is disabled** (`_run` returns before the loop), so a
  disabled or unconfigured commander spins up no work.
- Each iteration `wait_take`s the latest context (skipping when none arrives within
  `wait_timeout` = 0.1 s), calls `client.decide`, traces the outcome, and publishes the raw
  priorities. Only one call is ever in flight, so the synchronous SDK path (`call_json`, the
  same one the meeting LLM uses) suffices; an async client buys nothing.
- **Never raises out of the loop.** A failed call is traced as a `commander_call` error and the
  loop continues.

`close` is idempotent and bounded (a 1 s thread join), so shutdown can't hang the agent.

## 4. The priorities contract (`CommanderPriorities`)

`belief.commander: CommanderPriorities | None`, default `None`, defined in `types.py`. A frozen
(`extra="forbid"`) pydantic model carrying `as_of_tick` for the staleness guard. The LLM sets
only the fields it has an opinion about; every other field keeps its behavior-neutral default.

| Field | Role | Meaning |
|---|---|---|
| `target_room: str \| None` | crew | prefer doing tasks in this room |
| `target_task: int \| None` | crew | prefer this specific task index (if still signalled + reachable) |
| `posture: "stick"\|"isolate"\|"neutral"` | crew | break task ties toward most-crew / fewest-crew rooms (default `neutral`) |
| `hunt_room: str \| None` | imposter | seek a kill in this room |
| `target_player: str \| None` | imposter | prefer hunting / following / closing on this color |
| `avoid_room: str \| None` | imposter | skip this room when sweeping |
| `strength: "soft"\|"hard"` | both | how hard to honor the positioning levers (§5); default `soft` |
| `allow_witnessed_kill: bool` | imposter · **danger** | strike even when witnessed (§6) |
| `skip_evade: bool` | imposter · **danger** | suppress the post-kill Evade transition (§6) |
| `danger_reason: str \| None` | imposter · **danger** | required when a danger lever is set; traced |
| `reason: str \| None` | both | LLM rationale; traced, never gates behavior |
| `as_of_tick: int` | — | freshness clock the TTL reads; stamped by the sanitizer |

## 5. Consumption — "bias, don't force" and the strength dial

Every discretionary mode already picks from a candidate set by a score. The read-side helpers
in `strategy/commander/bias.py` centralize the rule:

- `bias.py:commander_of` — the **single accessor**. Returns the priorities only if
  `belief.commander` is set *and* not stale; otherwise `None`, which means "behave exactly as
  the deterministic agent would." Staleness horizon is `COMMANDER_TTL_TICKS` = 240 ticks
  (~10 s); a payload whose `last_tick - as_of_tick` exceeds it is treated as absent. A
  stalled/slow worker therefore degrades to default behavior, never a stale fixation.
- `bias.py:filter_or_fallback` — keeps candidates matching a predicate but **returns the full
  list if none match**, so a preference narrows the option set without ever emptying it.
- `bias.py:room_crew_count` — counts visible, live, non-teammate crew currently inside a room
  (for posture scoring).

Two strengths:

- **`soft` (default)** — filter-then-rank or score-nudge, and **always fall back** to the
  deterministic choice when the priority would select nothing valid. Cannot make play worse
  than the rules when the priority is impossible.
- **`hard`** — override the default even when suboptimal (still never violating a safety gate).

| Mode | Discretionary step | `soft` | `hard` |
|---|---|---|---|
| **NormalMode** (`modes/normal.py:_pick_target`, `:decide`) | nearest signalled+reachable task | honor `target_task` if it is among candidates; else filter tasks to `target_room` (fallback to all if none); `posture` breaks ties via `room_crew_count` (stick = most crew, isolate = fewest) | if `target_room` exists but holds no task for me → `_hard_target_room_intent` issues a `navigate_to` toward that room's center (loiter there) instead of tasking elsewhere |
| **SearchMode** (`modes/search.py:_pick_room`, `:_candidate_rooms`, follow) | random among the nearest `NEARBY_ROOMS` (4) task rooms | pick `hunt_room` if it is among those nearest rooms; drop `avoid_room` via `filter_or_fallback`; prefer `target_player` among leavers to follow | inject `hunt_room` as a candidate even when far (target it regardless of distance, if it has a task); extend the follow-lost window `FOLLOW_LOST_TICKS` (120) → `COMMANDER_FOLLOW_LOST_TICKS` (240) for a hard-named follow target |
| **ReconMode** (`modes/recon.py:decide`, `:_commander_target`) | `most_recent_victim` | close on `target_player` if it is alive, known, and not a teammate; else `most_recent_victim` | (same) |
| **HuntMode** (`modes/hunt.py:_resolve_victim`, `:_commander_victim`) | `select_victim` (most-isolated visible) | prefer `target_player` among visible victims when it is reachability-checked (a plannable route exists); else `select_victim` | (same; the strike gate is relaxed only via §6) |

Note that `target_player`, `hunt_room`, and `avoid_room` are still re-validated against live
belief at the point of use (the room exists, the player is alive and currently a visible
victim), on top of the sanitizer's legality check.

### `strength` is soft-only on the LLM path

By design, the LLM is **never asked for `strength`**:
`strategy/commander/llm.py:AnthropicCommanderClient.decide` omits it from the `response_schema`
field menu, and the prompts do not mention it. LLM-authored priorities therefore always
sanitize to `"soft"` — bias, never force. `"hard"` forcing (the override columns above) is
reachable **only** through the `CREWBORG_COMMANDER_FORCE` override (§9), a deterministic
test/QA/control path the LLM cannot trigger.

## 6. Danger mode — two opt-in imposter risk levers

The deliberate, narrow exception to "never touch the safety gates." Both default off, both
require a non-empty `danger_reason`.

| Lever | Default behavior | Danger effect | Still enforced |
|---|---|---|---|
| `allow_witnessed_kill` | A Hunt strike fires when `in_range ∧ kill_ready ∧ (unwitnessed ∨ already-banked-a-kill)` | adds `∨ allow_witnessed_kill`, relaxing the witness test even for the first kill | in-range and kill-ready unchanged |
| `skip_evade` | After a kill, `RuleBasedStrategy` forces **Evade** for `EVADE_TICKS` (72, env-tunable via `CREWBORG_EVADE_TICKS`) | skips the just-killed→Evade branch → straight back to Hunt/Search | nothing else changes |

`skip_evade` is the **one** place the commander reaches into `RuleBasedStrategy`'s reactive
ladder — a single guarded read in `strategy/rule_based.py` around the just-killed transition.

**Danger tracing.** When a lever actually changes behavior, the danger fire is recorded with
its `danger_reason`:

- `allow_witnessed_kill`: `modes/hunt.py:decide` emits a `commander_danger` event (via the mode
  emitter) at the moment it strikes a witnessed victim.
- `skip_evade`: `strategy/rule_based.py` appends a marker to the transient
  `belief.commander_danger_events` list, which `CrewborgEventTracer` drains on the next
  step-complete hook and re-emits as `commander_danger`.

## 7. The sanitize trust boundary (`schema.py`)

Raw LLM JSON (and forced-override JSON) is never installed into belief directly. It first
passes through `strategy/commander/schema.py:sanitize_priorities`, which reduces every field to
a value a mode can trust:

- `target_room` / `hunt_room` / `avoid_room` must be in `legal_rooms`; `target_player` must be
  in `legal_players`; anything else → `None`.
- `target_task` must be a real `int` (`type(x) is int` rejects `True`/`False`); else `None`.
- `posture` and `strength` fall back to their neutral defaults (`"neutral"`, `"soft"`) on any
  unknown value.
- `allow_witnessed_kill` and `skip_evade` are forced `False` unless a non-empty `danger_reason`
  string is supplied; `danger_reason` itself is kept only when a danger lever survives.
- `reason` is kept only when it is a string.
- `as_of_tick` stamps the freshness clock the TTL reads.

The legality sets come from `strategy/commander/context.py:legal_rooms` /
`:legal_players` — the same allow-lists sent to the LLM as the only valid targets it may name.
Every field defaults to the **behavior-preserving** value, so garbage in is inert.

`strategy/commander/context.py:serialize_commander_context` builds the compact JSON state the
LLM reasons over: phase, self pose/role/kill-readiness, the room and player allow-lists, the
roster (alive flag, position, room, last-seen tick), known bodies, and the currently selected
`active_mode` (situational awareness only; it does not constrain output). `context["self"]
["role"]` selects the system prompt.

## 8. Gating and the LLM backend

Two conditions must both hold for the commander to do anything:

1. **The feature flag.** `commander_feature_enabled` requires `CREWBORG_LLM_COMMANDER` truthy
   (`1`/`true`/`yes`/`on`). When unset, `CommanderStrategy.decide` returns the pure rule
   directive, never starts the worker, and never serializes context — the byte-identical
   disabled path.
2. **A configured backend.** `strategy/commander/llm.py:build_commander_client_from_env`
   returns a `DisabledCommanderClient` unless the flag is on **and** a usable backend exists
   (AWS Bedrock, the Bedrock sidecar, or `ANTHROPIC_API_KEY`). Any construction error also
   degrades to disabled rather than raising.

### Bedrock sidecar-endpoint gating

The SDK's `bedrock_enabled(env)` checks `USE_BEDROCK` / `CLAUDE_CODE_USE_BEDROCK`. In sidecar
mode the Coworld runner **strips `USE_BEDROCK`** from the player container and injects the
loopback Bedrock proxy endpoint `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` (plus dummy credentials)
instead, so gating on `USE_BEDROCK` alone reports "no LLM backend configured" in-pod even
though Bedrock is available.

`llm.py:build_commander_client_from_env` therefore also treats the presence of that endpoint as
a Bedrock signal:

```
use_bedrock = helpers.bedrock_enabled(env) or _sidecar_bedrock(env)
```

where `llm.py:_sidecar_bedrock` checks `AWS_ENDPOINT_URL_BEDROCK_RUNTIME`
(`BEDROCK_SIDECAR_ENDPOINT_ENV`). This routes `select_client(use_bedrock=True)` to the SDK's
sidecar path. The meeting LLM applies the same fix (see [`./meetings.md`](./meetings.md)).

### Model resolution

`build_commander_client_from_env` resolves the model via the SDK's `resolve_model`, passing the
SDK's default direct/Bedrock models and an optional explicit override from `CREWBORG_LLM_MODEL`
— a small, fast Claude (Haiku-class) model, since the commander runs every few seconds and only
emits a tiny JSON object. (`DEFAULT_COMMANDER_MODEL` in `llm.py` is the config dataclass default
and is superseded by the resolved value.) Calls use `temperature` 0.2 and `max_tokens` 512 by
default.

## 9. Forced-priority override (`CREWBORG_COMMANDER_FORCE`)

A deterministic test/QA/control knob. When the feature is on and `CREWBORG_COMMANDER_FORCE`
holds a JSON object, `CommanderStrategy` parses it once at construction
(`strategy.py:_parse_forced_priorities`) and stamps it — sanitized, with a fresh `as_of_tick`
— into `belief.commander` **every tick**, bypassing the worker and the LLM entirely (so it
works with no backend, and the worker is never started). This makes control deterministic
(e.g. `'{"hunt_room":"Observatory","strength":"hard"}'`) and is the only way to exercise the
`"hard"` strength dial. A malformed or non-object value is logged and ignored, behaving as
unset.

## 10. Observability

`strategy/commander/trace.py:CommanderTrace` is a bounded (capacity 256), drop-oldest,
lock-protected ring buffer. The worker records into it on the daemon thread; `CrewborgEventTracer`
drains it on the inner-loop thread and re-emits each record through the domain `EventEmitter` as
a `domain.commander_*` event. A drop is surfaced as a synthetic `commander_trace_dropped`
record so loss is visible. These events are gated behind `CREWBORG_TRACE=debug` or
`CREWBORG_TRACE_GROUPS=commander` (off by default — see [`./trace-logs.md`](./trace-logs.md)).

Events:

- `commander_started` — worker connect: `{enabled, backend, model, disabled_reason, attempt,
  env_seen}`, where `env_seen` reports which of `USE_BEDROCK` / `CLAUDE_CODE_USE_BEDROCK` /
  `ANTHROPIC_API_KEY` / `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` are present (the in-pod-enablement
  diagnostic).
- `commander_call_start` — per call: `{phase, role}` digest.
- `commander_call` — per call result: `{outcome: ok|error, latency_ms, model, priorities, usage}`
  on success (plus `raw_request` / `raw_response` when `trace_raw` is on), or
  `{outcome: error, error_type, error, latency_ms}` on failure.
- `commander_danger` — when a danger lever actually fires, with its `danger_reason` (§6).
- `commander_stopped` — worker close (emitted once).
- `commander_trace_dropped` — synthetic, when telemetry was evicted under load.

## 11. Configuration (env)

| Var | Effect |
|---|---|
| `CREWBORG_LLM_COMMANDER` | master feature flag; enable the gameplay commander |
| `USE_BEDROCK` / `CLAUDE_CODE_USE_BEDROCK` / `ANTHROPIC_API_KEY` / (in-pod) `AWS_ENDPOINT_URL_BEDROCK_RUNTIME` | a backend; without one the worker disables (§8) |
| `CREWBORG_LLM_MODEL` | explicit model override (else the SDK default Haiku-class model) |
| `CREWBORG_LLM_PROMPT_DIR` | override the role-doctrine prompt dir (default: the `memory/` sibling dir) |
| `CREWBORG_LLM_TIMEOUT_SECONDS` | per-call timeout (default 3.0) |
| `CREWBORG_LLM_MAX_TOKENS` | per-call max tokens (default 512) |
| `CREWBORG_LLM_TEMPERATURE` | per-call temperature (default 0.2) |
| `CREWBORG_LLM_TRACE_RAW` | include raw request/response in `commander_call` traces (also implied by `CREWBORG_TRACE=debug`) |
| `CREWBORG_TRACE_GROUPS=commander` / `CREWBORG_TRACE=debug` | surface `domain.commander_*` traces |
| `CREWBORG_COMMANDER_FORCE='{…}'` | force a fixed priority, bypassing the LLM (§9) |
| `CREWBORG_EVADE_TICKS` | tunes the post-kill Evade window the `skip_evade` lever suppresses (default 72) |

## 12. Disabled / fallback guarantee

With `CREWBORG_LLM_COMMANDER` unset — or no backend, or a stale/`None` commander — behavior is
byte-identical to deterministic crewborg: `CommanderStrategy` returns just the rule directive,
no worker runs, no context is serialized, `belief.commander` stays `None`, and every mode and
branch takes its current path. The same holds at every downgrade boundary inside the live path:
a dead or slow worker, malformed JSON, an illegal target, or a priority older than
`COMMANDER_TTL_TICKS` all resolve through the consumer's `commander_of` / `filter_or_fallback`
fallback to the deterministic default.

## 13. Module layout (`strategy/commander/`)

| File | Responsibility |
|---|---|
| `strategy.py` | `CommanderStrategy` (write side + master gate + forced override) and `apply_commander_inferences` |
| `worker.py` | `CommanderWorker` daemon (lazy client build, bounded retry, the call loop) |
| `llm.py` | client protocol, `DisabledCommanderClient` / `AnthropicCommanderClient`, `build_commander_client_from_env`, `commander_feature_enabled`, `_sidecar_bedrock` |
| `context.py` | `serialize_commander_context` + the `legal_rooms` / `legal_players` allow-lists |
| `schema.py` | `sanitize_priorities` — the trust boundary |
| `prompts.py` | `system_prompt_for_role` = common framing + role doctrine |
| `bias.py` | read side: `commander_of` (TTL), `filter_or_fallback`, `room_crew_count` |
| `trace.py` | `CommanderTrace` cross-thread telemetry buffer |
| `memory/{crewmate,imposter}.md` | editable role doctrine loaded by `prompts.py` |

`CommanderPriorities` and `belief.commander` / `belief.commander_danger_events` live in
`types.py`. The priorities are consumed in `modes/{normal,search,recon,hunt}.py` and
`strategy/rule_based.py` (the `skip_evade` lever), and the whole layer is wired together in
`crewrift/crewborg/__init__.py:build_runtime`.
