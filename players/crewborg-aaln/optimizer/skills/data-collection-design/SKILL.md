---
name: data-collection-design
description: Decide what a policy should log to validate a specific hypothesis, then instrument it so the next eval's artifacts carry the signal — pick the minimum signals, the cheapest artifact home (event/position/metric), keep it joinable to the ground-truth clock, choose a cost-aware trace level, emit at the right seam, and smoke-verify before the big eval. Use when the current artifacts cannot distinguish a hypothesis from the null.
---

# Data-Collection Design (instrument for the hypothesis)

A pre-registered hypothesis usually needs data the policy does not yet emit. This
skill decides **what to log** so the *next* eval's artifacts can confirm or kill
the hypothesis, then instruments the policy. Instrumentation lands **together
with** the behavior change so the very next eval is diagnostic — never change
behavior blind and hope the existing logs suffice. This is hypothesis-first data
collection, not generic logging: collect the fields that answer the current
question, not everything.

**Precondition:** a hypothesis from `policy-hypothesis-loop` with a prediction and
a regression tripwire — those define the minimum signal to capture.

## Step 1 — From hypothesis to required signal

For the hypothesis's prediction *and* its regression tripwire, list the **minimum**
signals that distinguish it from the null. For each, pick the cheapest artifact
home:

| You need to know… | Log it as… | Where |
|---|---|---|
| an outcome / state change (an event, a vote, a phase flip) | a domain event with a JSON payload | events/traces table |
| a per-tick spatial/visibility fact (where, who-seen, which mode) | a row already in the position track — extend the row only if the field is missing | positions table |
| a rate / duration / count (latency, dwell, cooldown age) | a counter / gauge / histogram | metrics table |
| a one-shot decision rationale | a field on the existing decision event (e.g. a `reason` code) | events/traces |

Prefer **adding a field to an existing event** over a new event; prefer a
**derived query** over logging a new kind at all (raw observations, derived
interpretations). A new heuristic should be a function over the log, not a schema
change.

## Step 2 — Make it joinable to ground truth

If the hypothesis is checked against the replay (most are), the new signal must be
locatable on the **shared ground-truth clock** (the tick/round/phase key the
replay reconstruction joins on) so `replay-artifact-analysis` can align it. Domain
events carry the tick; per-tick facts go in the position track which already
records the join key. **Do not invent a parallel clock.** Carry the spatial
annotation (position/region) the events already attach, so a pattern is both
*when* and *where* (feeds `spatial-temporal-analysis`).

## Step 3 — Choose the trace level / scope (cost-aware)

More logging = bigger artifacts and, on hosted runs, risk of hitting capped logs.
Turn on the **narrowest** thing that captures the signal:

- a family already in the lean default → nothing to enable;
- needs counters/gauges → the metrics level;
- needs per-tick decision/state detail → the debug level;
- needs the spatial replay view → the viewer level;
- targeted → an event-group / include-glob filter.

**Record which level the eval runs at — it must match across the baseline and
variant arms** or the comparison is confounded (`eval-variance-design` Step 4).

## Step 4 — Emit at the right seam

Emit new events where the finalized belief + chosen action + produced command are
all observable for the tick (the per-step completion hook), not from inside the
decision code — keep the decision layer pure and log off the finalized state. Add
position-track columns at the recorder (bump the schema version, update the
artifact README); populate them from the transport/bridge layer that has the live
position.

## Step 5 — Verify the instrument before the big eval

Run a 1–3 episode smoke and confirm the new event/field/metric **actually appears
with non-trivial values and joins to the replay**. A hypothesis that is silently
un-instrumented produces a fake "no effect." Only then launch the full eval.

## Output

The instrumentation diff (events / fields / metrics + the trace level), a note of
the ground-truth join path, and confirmation from the smoke that the signal lands.
Hand to `eval-variance-design` to run the comparison.

## Integration

- **Consumes:** `policy-hypothesis-loop` (the change + validation-data list).
- **Feeds:** `eval-variance-design` (run it), then `replay-artifact-analysis` /
  `spatial-temporal-analysis` on the next pull.
- **Game-specific grounding:** the artifact schema, trace levels, and emission
  seam are game/policy specific — see `games/<game>/skills/...`.
