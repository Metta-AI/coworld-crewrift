---
name: scripted-policy-techniques
description: Techniques for building a robust scripted control path for a policy so it plays every step deterministically, never crashes, falls back instantly when an LLM is slow or fails, resumes mid-game from live state, and survives opponent message/broadcast storms. Use when a policy is fragile under provider failure or load, times out, replays stale state, over-sends actions, or needs a non-LLM path that supports every phase of the game.
---

# Scripted Policy Techniques

An LLM-first policy that calls a model for every decision is fragile: a slow or
throttled provider, a broadcast storm from an opponent, or a single malformed
response can run the agent past the game timeout and score it a floor penalty
(disqualification / `-100` / inactive). The fix is not "tune the timeout" — it
is a **scripted control path that can play the whole game on its own**, with the
LLM demoted to an optional enhancement that can fail at any time without breaking
play.

This skill is game-agnostic. It generalizes the common failure where an
LLM-per-message loop gets buried under an opponent's broadcast storm and times
out; the fix (scripted-first, drain-to-latest, quota-gated, send-and-confirm,
circuit-broken) completes games purely scripted and leverages the LLM only when
healthy. For crewborg this is why the deterministic vote/fallback path is the
always-on baseline and LLM meetings sit on top behind a circuit breaker.

## When To Reach For This

- A policy times out or gets a floor/inactive penalty under load while a
  deterministic opponent never times out (the tell: the loss is *time*, not
  *strategy*).
- The policy calls an LLM/provider on the hot path for every decision.
- The transport is async/streaming (websocket, message bus) and the server
  broadcasts state after every action by any player.
- The policy re-submits actions it already completed, or acts on stale early
  state instead of the current state.
- There is no path that plays the game when the LLM is unavailable.

## Principle: Scripted Is The Default, LLM Is The Enhancement

Invert the usual control flow. The scripted path is the **primary** decision
maker and must support **every** step/phase of the game on its own. The LLM is
consulted only when (a) it is healthy, (b) the decision is genuinely ambiguous or
semantic, and (c) there is time budget. If any of those is false, the scripted
path acts immediately. A policy that cannot finish the game without the LLM is
not done.

```text
decide(state):
    intent = scripted_decision(state)          # always produces a legal action
    if llm_healthy and time_ok and is_ambiguous(state):
        try:
            intent = llm_refine(state, intent, deadline=short)  # may improve it
        except (timeout, provider_error):
            circuit.trip(); # fall through with the scripted intent
    return intent
```

## Technique 1: Never Crash — Always Return A Legal Action

Every decision path, including all error and fallback branches, must return a
**valid, legal** action for the current phase. Validate/normalize the action
shape before returning. Wrap instrumentation (artifact writes, feature parsing)
so it can never raise into the game loop — record the instrumentation error and
continue. A policy that raises mid-game is indistinguishable from a timeout to
the scorer.

## Technique 2: Fail Fast, Not Slow (bounded LLM + circuit breaker)

A 30s-per-decision timeout with multi-attempt retry+backoff is a *liability*: a
single throttled call can burn the whole game clock and starve the worker pool.

- Give the LLM a **short** deadline (single-digit seconds), not the game timeout.
- **No long retry/backoff chains** on the hot path. One bounded attempt, then
  scripted fallback.
- Add a **circuit breaker**: after K consecutive failures (K can be 1), stop
  calling the LLM for the rest of the episode and run purely scripted. Provider
  problems are usually persistent within an episode; re-probing wastes the clock.
- Run the LLM call off the critical thread, but treat a timed-out call as
  *abandoned* — never block waiting on it and never let abandoned calls pile up
  and exhaust the executor.

## Technique 3: Drain To The Latest State Before Deciding

When the server broadcasts a fresh state after every action by any player, a
naive `for msg in stream:` loop processes oldest-first and falls behind: under an
opponent storm the inbound buffer fills faster than a slow per-message decision
can drain it, so the policy spends the whole game acting on **stale** early-game
states and never reaches the live one.

- Before each decision, **flush the buffer to the newest state** and discard the
  intermediate ones. Decide on the freshest state only.
- Treat inbound messages as state *replacements*, not a queue of work to fully
  process.

## Technique 4: Quota-Gate Every Action (no re-submitting completed work)

Track, per phase/step, what the policy has already successfully done and what the
rules allow. **Never re-submit a quota that is already met.** This eliminates
stale-backlog replay entirely: even if an old state arrives late, the gate
refuses to re-act on a step that is complete, so the policy advances instead of
looping on rejected actions.

```text
if phase.quota_met(state):   # e.g. all N asks done, proposal already sent
    return advance_or_wait()
```

## Technique 5: Send-And-Confirm (don't over-send)

After sending an action, **wait for the server to confirm it landed** (the
relevant count advanced / state reflects it) before deciding the next action.
Under a slow server response plus an opponent broadcast storm, fire-and-forget
sending produces duplicates and quota violations. Confirmation gates the next
send and keeps the policy in lockstep with the server's view.

## Technique 6: Resume Mid-Game From Live State (stateless-friendly decisions)

Derive the current phase/step from the **observed state**, not from an internal
counter that assumes the policy saw every prior message. If the policy
reconnects, drops messages, or starts mid-episode, it must pick up correctly from
whatever the latest state says. Make `scripted_decision(state)` a pure function
of the current observation plus the confirmed-quota record, so any starting point
is handled. Recompute phase from action counts in the state rather than trusting
a local "I think we're in phase X."

## Technique 7: Instrument The Scripted Path For The Loop

Record, per decision, into player artifacts (see `player-artifacts`):
`phase`, `chosen_action`, `reason_code`, `llm_used`, `llm_failed`,
`fallback_used`, `circuit_open`, and any `error`. This makes "we lost on time,
not strategy" and "the LLM path never fired" *visible* in aggregation instead of
something to reconstruct from transient logs. A timeout with `fallback_used=false`
everywhere means the scripted path was never reached — a different bug than a
scripted path that plays but plays weakly.

## Verification Checklist (before trusting the scripted rewrite)

- [ ] Runs to completion with the **LLM forced off** — every phase handled, no
      crash, well under the game timeout.
- [ ] Runs to completion under a **simulated broadcast storm** (opponent spamming
      actions) without timing out or over-sending.
- [ ] **Hang/resilience test:** inject a slow/failing LLM and confirm the circuit
      trips and play continues immediately.
- [ ] **Resume test:** start the decision function from a mid-game state and
      confirm it produces the correct next action.
- [ ] Artifacts show `fallback_used` / `circuit_open` / `llm_failed` so the path
      taken is auditable.
- [ ] Then run the normal eval ladder (smoke → diagnostic → … ) and only promote
      via the `promotion-gate`.

## Anti-Patterns

- Treating the game timeout as the per-call LLM timeout.
- Retry+backoff chains on the hot decision path.
- Processing the inbound stream oldest-first with a slow per-message decision.
- Re-deriving "what to do" from a local counter that assumes no dropped messages.
- A "fallback" that only covers some phases (so the policy still stalls on the
  uncovered one).
- Letting artifact/instrumentation code raise into the game loop.
