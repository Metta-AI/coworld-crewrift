# Cyborg Agent Framework

Generated: 2026-05-11

The importable implementation is `players.player_sdk` (the Coworld
Player SDK). The framework described here is called the Cyborg
framework.

The Python implementation lives in
[`players/player_sdk`](../..).
For a shorter usage guide, see [`PYTHON_FRAMEWORK.md`](PYTHON_FRAMEWORK.md).

## Executive Summary

The Cyborg framework is a two-level control architecture for game agents:

```text
fast symbolic inner loop:
  raw observation -> percept -> belief update -> mode decision -> action command

slower strategy loop:
  belief snapshot -> strategy/LLM reasoning -> typed mode directive
```

The "cyborg" property is not that every agent must call an LLM. It is that the
fast control loop is deterministic and symbolic, while the slower strategy loop
is replaceable by a human-authored policy, an LLM, or a hybrid. The strategy
loop selects the current mode and parameters. The inner loop owns perception,
belief mutation, safety, action timing, and per-tick responsiveness.

This split is useful because game agents need two different kinds of
competence:

- Low-latency reliability for movement, UI timing, collision handling, voting
  cursors, menu interactions, and multi-tick action execution.
- High-level strategic judgment over partial information, social evidence,
  team coordination, deception, resource allocation, and long-horizon goals.

The general rule is:

> The outer loop decides what kind of work should be done. The inner loop
> decides exactly how to do it safely this tick.

## General Architecture

The framework has four durable layers.

### Perception Layer

Perception converts raw observations into symbolic percepts. The input may be
pixels, object tokens, text messages, UI state, game-state packets, or a custom
transport. The output should be typed, bounded, and easy to merge into memory.

Guidance:

- Keep raw frame buffers, token streams, and pixel atlases out of belief.
- Make percepts additive: new detector output should add fields without
  changing the meaning of existing fields.
- Preserve uncertainty when detectors are probabilistic.
- Keep enough provenance to distinguish hard evidence from weak inference.

### Belief Layer

Belief is the persistent symbolic model of the world. It is the strategy
interface and should be the only thing the outer loop needs to inspect.

Common belief contents:

- Self state: role, team, life state, location, cooldowns, resources.
- World state: phase, room, territory, task/resource state, objective state.
- Entity memory: visible entities, remembered entities, last-seen positions,
  roles, gear, cargo, threat, trust, and evidence fields.
- Social memory: chat, talk, votes, claims, alibis, commitments.
- Current directive metadata: active mode, source, issue tick, TTL, reason.
- Wake flags: phase transition, body seen, mode stalled, new chat, resource
  threshold crossed, urgent hazard.

Belief should separate:

- World truth: durable facts and evidence.
- Mode scratch: transient state owned by one active mode.
- Action state: route progress, button pulses, cursor position, task holds,
  pending chat buffers, movement-failure memory.
- Strategy inferences: summaries or rationale produced by the outer loop.

The recommended ownership rule is:

> Perception owns evidence ingestion. The inner loop owns belief mutation.
> Modes may write only their own scratch or explicit mode-status facts. The
> outer loop may write strategic inferences only through a clearly named
> inference namespace.

### Mode Layer

A mode is a deterministic symbolic local policy. It reads belief, owns
mode-local scratch, and emits a symbolic task or action intent. Examples:
`idle`, `navigate_to`, `complete_task`, `gather_resource`, `escort`,
`meeting`, `hunt`, `retreat`, `capture`, `patrol`, and `repair`.

Useful mode properties:

- Exactly one active mode exists per agent.
- Mode activation has an `on_enter` lifecycle hook.
- Mode deactivation has an `on_exit` lifecycle hook.
- Parameter equality prevents needless re-instantiation.
- Mode-local scratch survives reaffirmed directives and resets on true mode
  switches.
- A mode emits symbolic intent, not transport commands.
- Legality is checked before or during mode execution.

### Strategy Layer

The strategy loop runs more slowly than the game tick. It consumes belief
snapshots and emits typed mode directives. It may be a deterministic heuristic,
an LLM, or a hybrid.

Canonical sequence:

```text
while running:
  snapshot = latest_snapshot_buffer.consume_blocking_or_poll()
  with snapshot.read() as memory:
    context = summarize(memory.belief)
  directive, inferences = strategy.decide(context)
  validate(directive)
  directive_buffer.publish_latest(directive, inferences)
```

Outer-loop outputs should use latest-value semantics by default. A stale
strategic directive is usually worse than a dropped directive.

## Framework Reference

This section is the durable API-level reference for the reusable Python
framework.

### What The Package Provides

The public package is
[`players/player_sdk`](../..).
It provides:

- Typed mode directives and mode parameters.
- Shared, lock-protected agent memory for inner/outer-loop access.
- A mode base class and mode registry.
- A generic per-tick runtime.
- Synchronous, threaded, async, and manual strategy runners.
- Latest-value buffers for async strategy communication.
- Priority-ordered reflex hooks for urgent local overrides.
- Trace events, metrics sinks, domain-event emitters, logging adapters, and W&B
  metric adapter hooks.
- Generic intent/command models for examples and simple agents.

### Core Runtime

[`AgentRuntime`](../../runtime.py)
is the inner-loop orchestrator. A game supplies:

- `belief`: the live mutable belief object.
- `action_state`: mutable state for transport mechanics and multi-tick action
  execution.
- `perceive(observation, tick) -> percept`.
- `update_belief(belief, percept) -> None`.
- `resolve_action(intent, belief, action_state) -> command`.
- `mode_registry`: registered symbolic modes.
- `default_directive`: a fallback `ModeDirective`, or a function that builds
  one from belief.
- Optionally: `strategy_runner`, `reflexes`, `trace_sink`, `metrics_sink`,
  `apply_inferences`, and `on_step_complete`.

Each call to `step(observation)` performs:

```text
tick += 1
percept = perceive(observation, tick)
update_belief(belief, percept)
submit BeliefSnapshot pointing at shared locked memory
consume latest StrategyResult, if any
apply strategy inferences, if any
install validated strategy directive, if any
run reflexes
fall back on expired or illegal modes
decision = active_mode.decide(belief, action_state)
intent = decision.intent
command = resolve_action(intent, belief, action_state)
if decision marks complete/stalled, trace status, fall back, and wake strategy
call on_step_complete with intent, command, belief, action state, mode, and emitter
return command
```

The runtime does not know any game rules. Game-specific behavior lives in
perception, belief update, modes, reflexes, strategy, and action resolution.
Game-specific trace and metric events live in mode emitters or
`on_step_complete` observers.

### Typed Directives

The strategy layer talks to the runtime through models in
[`types.py`](../../types.py):

- `ModeParams`: Pydantic base class for typed, frozen, extra-forbidden mode
  parameters.
- `EmptyModeParams`: parameter object for modes with no arguments.
- `ModeDirective`: mode name, typed params, source, issue tick, TTL, reason,
  and metadata.
- `StrategyResult`: optional directive plus optional structured inferences.
- `SharedMemory`: lock-protected live belief/action-state handle.
- `BeliefSnapshot`: immutable envelope with `read()` and `write()` accessors for
  the shared memory.
- `ModeDecision`: optional status envelope returned by modes to mark running,
  complete, or stalled.
- `StepContext`: frozen end-of-tick observer context containing tick, belief,
  action state, intent, command, active mode name, and an event emitter.
- `ActionIntent` and `ActionCommand`: generic base shapes for small agents and
  examples.

`ModeDirective` is strategic intent, not a raw action. The inner loop validates
the directive, preserves or resets mode scratch correctly, and executes the
chosen mode through deterministic action code.

### Modes

[`Mode`](../../modes.py)
is the symbolic local-policy base class. Each mode defines:

- `name`: stable directive name.
- `params_type`: expected `ModeParams` subclass.
- `on_enter(belief, action_state)`: optional lifecycle hook.
- `on_exit(belief, action_state, next_directive)`: optional cleanup hook.
- `is_legal(belief) -> bool`: optional legality check.
- `decide(belief, action_state) -> intent | ModeDecision`: required per-tick
  symbolic decision, optionally with completion or stalling status.

`ModeRegistry` maps directive names to mode classes. It validates that a
directive names a known mode and carries the correct params type before the
runtime installs it.

When a new directive matches the current mode name and params, the runtime
reaffirms the directive and preserves the live mode instance. When the mode or
params change, the runtime calls `on_exit`, creates a new mode instance, and
calls `on_enter`.

### Strategy Runners

[`strategy.py`](../../strategy.py)
defines the runtime-facing strategy contract:

```python
class Strategy:
    def decide(self, snapshot) -> StrategyResult | ModeDirective | None:
        ...
```

The package includes four runners:

- `SynchronousStrategyRunner`: cadence-limited strategy evaluation on the
  inner-loop thread. Use it for deterministic strategies, tests, and simple
  adapters.
- `ThreadedStrategyRunner`: background strategy evaluation connected by
  latest-value buffers. Use it for blocking clients or expensive reasoning.
- `AsyncStrategyRunner`: event-loop strategy evaluation for `async def`
  strategies. Use it for async-first LLM clients when the game/application
  already owns the event loop. Construct it inside that running loop or pass the
  loop explicitly; it does not create a private loop.
- `ManualStrategyRunner`: test harness runner where callers publish directives
  manually.

The threaded and async runners use newest-snapshot semantics. The threaded
runner uses
[`OverwriteBuffer`](../../buffers.py),
which keeps only the newest unread value. That prevents old snapshots and stale
directives from building up behind the live game state.

### Reflexes And Fallbacks

Reflexes are urgent symbolic overrides. A `ReflexRule` has a `name`, numeric
`priority`, and callback receiving `RuntimeContext`. Higher priority rules run
first; equal priorities preserve registration order. The runtime traces which
rules were checked, which rule won, and which lower-priority rules were skipped.
Use reflexes for phase transitions, hazards, deadlines, or other events that
cannot wait for a slower strategy pass.

The runtime also has two built-in fallback paths:

- TTL fallback: if the active directive expires, install the default directive.
- Legality fallback: if `active_mode.is_legal(belief)` is false, install the
  default directive.

Fallbacks should preserve liveness, but they should not hide design bugs. The
runtime traces fallback reasons.

### Observability

[`trace.py`](../../trace.py)
defines:

- `TraceEvent`: one boundary event with tick, name, and data.
- `TraceSink`: protocol for event consumers.
- `NullTraceSink`: default sink that drops events.
- `ListTraceSink`: in-memory sink for tests and small examples.
- `LoggingTraceSink`: structured logging adapter.
- `MetricsSink`: protocol for counters, histograms, and gauges.
- `ListMetricsSink`: in-memory metric sink for tests.
- `LoggingMetricsSink`: structured logging metric adapter.
- `WandbMetricsSink`: W&B run adapter without adding a package dependency.
- `EventEmitter`: domain-event handle that writes to the runtime's configured
  trace and metrics sinks.

The runtime emits events for mode entry and exit, perception, belief update,
snapshot submission, directive rejection, directive reaffirmation, strategy
inferences, reflex evaluation, mode completion/stalling, action intent, and
concrete command emission.

The runtime and strategy runners emit metrics for mode runs, mode duration,
directive age, fallback rate, strategy observe/decide latency, and step latency.
These traces and metrics should let a developer answer whether bad behavior came
from perception, belief update, strategy, mode logic, action lowering, or slow
outer-loop reasoning.

Game-specific code can emit its own events through `EventEmitter` without
threading sinks through the pure `perceive`, `update_belief`, or
`resolve_action` functions. Every `Mode` has `self.emit`, which defaults to a
no-op emitter when the mode is constructed outside a runtime and is rebound to
the runtime emitter when the mode becomes active. Unqualified event and metric
names are prefixed with `domain.` so they are easy to separate from framework
events in a shared sink. Names that already start with `domain.` are left as-is:

```python
class CaptureMode(Mode[Belief, ActionState, ActionIntent]):
    name = "capture"

    def decide(self, belief: Belief, action_state: ActionState) -> ActionIntent:
        self.emit.event("objective_committed", {"objective_id": belief.target_id})
        self.emit.counter("objective_attempts", tags={"objective": belief.target_id})
        return ActionIntent(semantic="capture")
```

Use `AgentRuntime(on_step_complete=...)` when an event needs end-of-tick facts
that only coexist after action resolution. The hook runs once per `step()` while
the shared-memory write scope is still held, after `resolve_action` and
mode-completion handling. Its `StepContext` includes the runtime tick, live
belief, live action state, selected intent, resolved command, mode name that
made the decision, and the same emitter:

```python
def observe_step(context: StepContext[Belief, ActionState, ActionIntent, ActionCommand]) -> None:
    if context.intent.semantic == "attack":
        context.emit.event(
            "attack_attempted",
            {"command": context.command.action, "mode": context.active_mode_name},
        )
```

On completion or stall ticks, the hook sees final end-of-tick state: the
completed/stalled mode's intent and command, the mode name that made the
decision, and live belief/action state after fallback handling has run.

### Building A New Game Agent

A new game-specific agent should usually be assembled in this order:

1. Define observation and action transport types.
2. Define belief, action state, and percept types.
3. Implement `perceive`.
4. Implement `update_belief`.
5. Implement `idle` plus one useful mode.
6. Register modes in `ModeRegistry`.
7. Implement `resolve_action`.
8. Add a default rule-based strategy that emits directives.
9. Add domain events and metrics for game-specific boundaries.
10. Add reflexes and fallback directives for urgent state changes.
11. Add structured snapshots and validators for future LLM use.
12. Enable LLM strategy only after deterministic replay and trace review.

The runnable toy example is
[`examples/toy_grid_agent.py`](examples/toy_grid_agent.py).

## Inner Loop Contract

The inner loop runs every tick and must not block on model calls, network calls,
or slow strategy computation.

Canonical sequence:

```text
tick(raw_observation):
  percept = perceive(raw_observation)
  with shared_memory.write() as memory:
    memory.belief.update(percept)
  latest_directive = directive_buffer.consume_if_present()
  reconcile_directive(latest_directive, belief)
  reflex_or_safety_override_if_needed(belief)
  decision = active_mode.decide(belief, mode_scratch)
  intent = decision.intent
  command = action_layer.resolve(intent, belief, action_state)
  handle_mode_completion_or_stall(decision)
  on_step_complete(tick, belief, action_state, intent, command, mode)
  emit(command)
  trace(percept, belief, directive, intent, command)
```

Important properties:

- Single owner of live mutation: the inner loop owns live belief mutation and
  action-state mutation.
- Non-blocking strategy: the inner loop reads a latest available directive if
  one exists; otherwise it continues with the current mode or fallback.
- Deterministic local behavior: mode decisions and action resolution should be
  reproducible from belief, directive params, and scratch state.
- Fast safety overrides: reflexes, legality checks, and fallback modes run
  inside the inner loop because they cannot wait for strategy.
- Trace every boundary: perception, belief diffs, mode changes, intents,
  outgoing actions, outer-loop decisions, and game-specific phase/objective
  outcomes should be visible in logs.

## Mode Parameters

Mode params should be structured and mode-specific. For Python agents, prefer
Pydantic `ModeParams` subclasses over raw dicts. The strategy layer may still
output JSON, but the inner loop should validate it into typed parameters before
a mode sees it.

Examples:

- `navigate_to(target=(12, 4), purpose="task")`
- `probe_target(target_id="yellow", intent="gather_evidence")`
- `hunt(preferred_target="yellow", max_witnesses=0)`
- `capture(target_pos=(4, 9), max_hearts_per_trip=2)`
- `gather(resource_type="oxygen")`
- `meeting(want_to_speak_first=True)`

## Mode Completion And Stalling

The outer loop needs to know when the active mode is done, impossible, stale,
or should be reconsidered.

General pattern:

```text
mode.decide(...)
  -> can return a normal intent
  -> can return ModeDecision.complete(intent, reason=...)
  -> can return ModeDecision.stalled(intent, reason=...)
```

When a mode returns `complete` or `stalled`, the runtime emits
`mode_completed` or `mode_stalled`, installs the default directive after the
current intent is lowered to an action, and submits a wake snapshot to the
strategy runner with the mode status reason. Do not let a stalled mode spin
indefinitely without making that state visible to metrics, tracing, and
strategy.

## Action Layer Contract

The action layer lowers symbolic intent into concrete transport actions.

The action layer owns:

- Pathfinding and adjacent-cell interaction rules.
- Button pulse timing.
- Cursor movement timing.
- Menu/UI open-close mechanics.
- Multi-tick holds and confirmation windows.
- Movement failure detection.
- Transport-specific details such as WebSocket packets or game `Action`
  objects.

The action layer should not own:

- Strategic priorities.
- Long-horizon target choice.
- Social reasoning.
- Durable interpretation of evidence.

## Rule-Based, LLM, And Hybrid Strategy

The strategy loop does not have to be an LLM.

Useful strategy variants:

- Pure heuristic: fast, deterministic, easy to test, limited in social
  reasoning.
- LLM shadow: model receives context and proposes decisions, but output is
  ignored except for traces.
- LLM advisory: model proposes decisions; rules decide whether to accept.
- Constrained LLM control: model may choose among validated, executor-backed
  modes or actions.
- Full strategy LLM: model selects all high-level modes, still validated by
  deterministic code.
- Hybrid strategy: rules compute candidates and hard constraints; model chooses
  among them or explains uncertainty.

The best default is hybrid: deterministic code computes affordances and hard
constraints; the LLM selects among legal options using structured evidence.

## LLM Boundary

The LLM boundary should be explicit and schema-driven.

Minimum pieces:

1. Context builder: converts belief into compact JSON-safe context.
2. Prompt: describes objectives, response schema, legal choices, evidence
   rules, and hard prohibitions.
3. Provider: calls the model and can be replaced with deterministic fakes.
4. Validator: rejects malformed JSON, illegal modes/actions, impossible
   targets, unsafe social claims, stale actions, and schema mismatches.
5. Executor mapping: converts validated model decisions into `ModeDirective`
   or executor-backed symbolic action surfaces.
6. Trace: records context hash, raw decision, validation result, accepted
   directive, rejection reasons, latency, and fallback behavior.

The standard framework has the LLM select modes, not raw actions. Direct LLM
actions are acceptable only when the action surface is small, symbolic,
view-specific, validated, and executor-backed.

Do not let an LLM directly emit per-frame movement buttons, cursor pulses, or
transport packets.

## Concurrency Model

The core concurrency rule is:

> The inner loop is real-time and authoritative. The outer loop is advisory and
> asynchronous.

Recommended patterns:

- Share belief/action state through `SharedMemory`; read and write it only under
  `snapshot.read()` or `snapshot.write()`.
- Keep shared-memory lock scopes short. Build LLM context under `snapshot.read()`,
  release the lock, then call the model.
- Use size-one buffers or newest-wins channels to prevent backlog.
- Poll directives non-blockingly from the inner loop.
- Hand lock-protected `BeliefSnapshot` handles, not deep-copied belief objects,
  to the strategy loop.
- Make strategy output idempotent and droppable.
- Track liveness through tick counters, TTLs, and watchdogs.

Avoid:

- Blocking the inner loop on an LLM.
- Holding the shared-memory lock across model calls, network calls, or slow
  summarization.
- Queueing old directives that will execute after the game phase has changed.
- Sharing mutable belief objects across threads without a clear lock or copy
  discipline.
- Resetting mode scratch every time the outer loop reaffirms the same plan.

## Evidence And Belief Semantics

Belief should preserve evidence, not only conclusions.

Bad belief shape:

```json
{"yellow": "sus"}
```

Better belief shape:

```json
{
  "yellow": {
    "incriminating": [
      {"kind": "near_body", "distance": 12, "ambiguous": true},
      {"kind": "near_vent_appearance", "probability_pct": 66}
    ],
    "exculpatory": [
      {"kind": "solo_survival_trust", "total_ticks": 180}
    ],
    "chat_mentions": [
      {"speaker": "red", "text": "yellow near vent", "interpretation": "claim"}
    ]
  }
}
```

The strategy loop, especially when LLM-backed, needs evidence categories and
provenance:

- Hard evidence vs probabilistic evidence.
- Direct observation vs inference.
- Public knowledge vs private knowledge.
- Stale context vs current evidence.
- Exculpatory vs incriminating evidence.
- Ambiguous evidence with alternative explanations.

## Team Coordination

The framework must distinguish actual shared state from environment-visible
coordination.

General rule:

> Do not put coordination facts in memory unless the agent could have observed
> them through the game or an allowed communication channel.

For LLM use, make observability visible in the context:

- observed teammate claim;
- trusted same-policy talk;
- visible gear or cargo;
- inferred objective;
- private teammate knowledge;
- public accusation.

This prevents models from leaking private information into public actions.

## Suggested Project Layout

A new game-specific Cyborg agent can use this layout:

```text
agent/
  README.md
  DESIGN.md
  types.py
  perception/
    parse_observation.py
    fixtures/
  belief.py
  modes/
    __init__.py
    idle.py
    <mode>.py
  action.py
  strategy/
    snapshot.py
    strategy.py
    prompts.py
    provider.py
    validator.py
  trace.py
  tests/
```

## Suggested Core Types

The exact game-specific types can vary. This is the stable conceptual model:

```python
class ModeDirective:
    mode: str
    params: object
    ttl_ticks: int = 0
    reason: str = ""
    source: str = "strategy"


class Belief:
    tick: int
    phase: str
    self: object
    world: object
    entities: object
    social: object
    tasks: object
    directive: ModeDirective
    wake_reasons: set[str]
    inferences: dict


class ModeDecision:
    intent: ActionIntent
    status: str = "running"  # running | complete | stalled
    reason: str = ""


class ActionIntent:
    target: tuple[int, int] | None = None
    semantic_action: str | None = None
    chat: str = ""
    reason: str = ""


class Mode:
    name: str
    params_type: type

    def on_enter(self, belief: Belief, action_state: object) -> None:
        ...

    def on_exit(self, belief: Belief, action_state: object) -> None:
        ...

    def decide(self, belief: Belief, action_state: object) -> ActionIntent | ModeDecision:
        ...
```

For production Python code, use the concrete classes in
`players.player_sdk` rather than copying this sketch.

## Design Invariants

These invariants should hold across implementations:

1. One active mode exists per agent.
2. Mode directives select modes and parameters. They are not raw controller
   input.
3. The inner loop never waits for the outer loop.
4. Lock-protected `BeliefSnapshot` handles, not raw frames or unguarded mutable
   state, are the strategy interface.
5. Important decisions are backed by durable evidence fields.
6. View legality, action legality, target legality, guards, resource
   constraints, and stale-response checks belong in code.
7. Mode-local planning state does not pollute global belief.
8. Movement, pathing, button timing, cursor navigation, and interaction
   mechanics are code-owned.
9. Reaffirming an existing directive does not reset mode-local progress.
10. Defaults, watchdogs, TTLs, reflexes, and safety-net actions are observable
    and traceable.
11. Agents may reason from communication only if the game exposes that
    communication.
12. LLM control is enabled one bounded surface at a time.

## Anti-Patterns

Avoid these patterns:

- Calling an LLM every tick.
- Using LLM output as raw button masks or movement commands.
- Letting unvalidated JSON construct modes.
- Applying stale directives long after phase changes.
- Storing mode scratch in global belief without ownership.
- Treating current action state as durable world truth.
- Falling back silently with no trace reason.
- Collapsing evidence into unexplained trust or suspicion scores.
- Relying on a team blackboard unavailable in the game environment.
- Re-instantiating a mode whenever the same directive is reissued.
- Asking a model to infer legal actions from prose instead of providing
  explicit affordances.

## Validation Strategy

Validation should cover each boundary independently.

### Perception

- Fixture tests for raw observations.
- Snapshot tests for parsed percepts.
- Performance checks when perception runs every frame.
- Regression fixtures for UI screens, OCR, role reveal, voting, map
  localization, or equivalent game-specific surfaces.

### Belief Update

- Unit tests for each percept-to-belief transition.
- Tests for reset behavior and phase transitions.
- Evidence provenance tests, especially hard vs probabilistic evidence.
- Tests that stale observations do not masquerade as current evidence.

### Modes

- Unit tests for mode legality and default params.
- Tests for mode lifecycle and scratch reset/preservation.
- Scenario tests for mode completion and stalling.
- Tests that modes emit symbolic intents only.

### Action Layer

- Pathfinding and interaction tests.
- Cursor/button pulse tests.
- Multi-tick task hold tests.
- Movement-failure recovery tests.

### Strategy

- Snapshot shape and schema tests.
- Deterministic strategy tests.
- Directive validation tests.
- Reaffirmation/idempotence tests.
- TTL/watchdog/fallback tests.

### LLM

- Deterministic fake provider tests.
- Malformed output rejection tests.
- Legal-action affordance tests.
- Shadow evaluation on saved contexts.
- Live trace review before enabling control.

### End-To-End

- Short local smoke runs for compile/import/transport.
- Fixture replay or recorded trace replay.
- Full episode runs for behavior quality.
- Trace review for mode transitions, directive age, fallback rate, and action
  legality.
- Metrics review for mode counts, fallback rate, strategy latency, and current
  mode duration.

## Trace Schema Recommendations

Every implementation should produce enough trace data to answer:

- What did the agent observe this tick?
- How did belief change?
- Which directive was active, and where did it come from?
- Did a reflex, TTL expiry, watchdog, or legality fallback override it?
- Which mode ran?
- Which symbolic intent did the mode produce?
- What concrete action was emitted?
- If the LLM was called, what context hash, raw decision, validation result,
  and accepted directive/action resulted?
- If behavior looked bad, was the problem perception, belief update, strategy,
  mode logic, or action lowering?

Recommended event names:

- `perception`
- `belief_diff`
- `snapshot_submitted`
- `strategy_evaluated`
- `llm_response`
- `directive_published`
- `directive_consumed`
- `mode_entered`
- `mode_exited`
- `mode_stalled`
- `mode_completed`
- `reflex_fired`
- `reflex_evaluated`
- `action_intent`
- `act_command`
- `fallback_activated`
- `validation_rejected`

## What To Standardize Across Future Agents

These pieces are worth standardizing:

- `ModeDirective` shape and structural equality.
- `Mode` lifecycle names.
- Snapshot schema sections: self, phase, current mode, visible now, memory,
  social/communication, legal actions, hard constraints, wake reasons.
- LLM decision schema: action/mode, params, confidence, rationale, schema
  version.
- Trace event names for directive and mode lifecycle.
- Metric names for mode counts, fallback rate, strategy latency, and directive
  age.
- Fallback semantics: TTL, default mode, watchdog, mode stalled.
- Source-map documentation for each game-specific agent: framework, inner
  loop, belief, modes, strategy, LLM boundary, and tests.

## Open Design Questions

These are unresolved at the generalized-framework level:

- Should mode params be required to be static typed models everywhere, or is a
  raw dict acceptable for fast iteration?
- Should strategy facts be written back into belief, or kept in a separate
  inference namespace?
- How much direct LLM action control is acceptable outside social/UI phases?
- What is the standard trace format across languages?
- Should a shared library provide all mode/directive/buffer contracts, or
  should each game keep local copies until the contract stabilizes?
- How should team coordination schemas represent trust when some teammates use
  the same policy and others do not?

Conservative defaults: typed params, separate inference namespaces, no raw LLM
action control, explicit trace schemas, local wrappers around the shared Python
framework until contracts settle, and communication facts that distinguish
observed claims from inferred coordination.

## Bottom Line

The reusable Cyborg framework is:

```text
fast symbolic inner loop
  perception -> belief -> mode policy -> deterministic action

slow strategic outer loop
  belief snapshot -> rule/LLM/hybrid strategy -> validated mode directive
```

The framework succeeds when the inner loop is boring, fast, and safe, while the
outer loop is allowed to be smarter, slower, and more flexible. The bridge
between them stays narrow: structured belief in, validated directive out.
