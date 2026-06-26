---
name: data-collection-design
description: "Use to decide what crewborg should log to validate a specific hypothesis, then instrument the policy so the next eval's artifacts carry the signal. Trigger on 'what data do I need', 'how do I instrument this', 'what should I log to test this', or 'add tracing to validate the hypothesis'."
---

# Data-Collection Design (instrument for the hypothesis)

## Purpose

A pre-registered hypothesis usually needs data crewborg does not yet emit. This
skill decides **what to log** so the *next* eval's artifacts can confirm or kill
the hypothesis, then instruments the policy. Two sister questions, both answered
here:

1. **What kind of data do we need** from the player to find the behavior pattern
   that bears on the goal?
2. **How do we reconstruct/match it** — i.e. log it in a shape that the artifact
   tables + replay join (`server_tick`) can validate against ground truth?

Instrumentation lands **together with** the behavior change so the very next eval
is diagnostic — never change behavior blind and hope the existing logs suffice.

**Announce at start:** "Designing the data collection for this hypothesis: which
events/metrics/positions to log, in what shape, so the next eval's artifacts
prove or disprove it — then wiring it in."

## Step 1 — From hypothesis to required signal

For the hypothesis's prediction and regression tripwire, list the *minimum*
signals that distinguish it from the null. For each, pick the cheapest artifact
home:

| You need to know… | Log it as… | Where |
|---|---|---|
| an outcome/state change (a kill, a vote, a phase flip) | a `domain.*` event with a JSON payload | `traces` table |
| a per-tick spatial/visibility fact (where, who-seen, which mode) | already in `positions` (`self_x/y`, `room_id`, `mode`, `intent_kind`, `visible`) — extend the row only if missing | `positions` table |
| a rate/duration/count (latency, dwell, cooldown age) | a counter/gauge/histogram | `metrics` table |
| a one-shot decision rationale | a payload field on the existing decision event (e.g. vote `reason`) | `traces` |

Prefer **adding a field to an existing event** over a new event; prefer a
**derived query** over logging a new kind at all (raw observations, derived
interpretations — `design.md` §5.2).

## Step 2 — Make it join-able to ground truth

If the hypothesis is checked against the replay (most are), the new signal must be
locatable on the **`server_tick`** timeline so `replay-reconstruction` can join
it. Domain events carry `tick`; per-tick facts go in `positions` which already
records `server_tick`. Don't invent a parallel clock. Carry the spatial annotation
(`self_x/self_y/room_id`) the domain events already attach, so a pattern is both
*when* and *where*.

## Step 3 — Choose the trace level / scope (cost-aware)

More logging = bigger artifacts and, on hosted runs, risk of hitting capped logs.
Turn on the **narrowest** thing that captures the signal (see `artifact-capture`
for the env matrix):

- a domain event family already in the lean default → nothing to enable.
- needs metrics → `CREWBORG_METRICS=1`.
- needs per-tick decision/suspicion/occupancy detail → `CREWBORG_TRACE=debug`.
- needs the spatial replay view → `CREWBORG_TRACE=viewer`.
- targeted → `CREWBORG_TRACE_GROUPS=...` / `CREWBORG_TRACE_INCLUDE=glob`.

Record which level the eval will run at — it must match across baseline and
variant arms or the comparison is confounded.

## Step 4 — Add the emission at the right seam

crewborg's own game events are emitted in `events.py`
(`CrewborgEventTracer.on_step_complete`), which sees the finalized belief +
chosen intent + produced command each tick — the only place attempt-events
(keyed on the wire command) and post-mode conclusions (e.g. `task_completed`) are
observable. Add new `domain.*` emissions there; add `positions` columns in
`artifact.py` (bump `SCHEMA_VERSION`, update the embedded README) and populate
from the bridge. Keep `strategy/` pure — read off finalized belief, don't log from
inside decision code.

## Step 5 — Verify the instrument before the big eval

Run a 1–3 episode smoke (`artifact-capture`) and confirm the new event/field/
metric actually appears with non-trivial values and joins to the replay. A
hypothesis silently un-instrumented produces a fake "no effect." Only then launch
the full eval (`eval-set-design`).

## Output

The instrumentation diff (events/fields/metrics + trace level), a note of the
`server_tick` join path, and confirmation from the smoke that the signal lands.
Hand to `eval-set-design` to run the comparison.

## Integration

- **Consumes:** `hypothesis-generation` (the change + validation-data list).
- **Feeds:** `eval-set-design`, then `artifact-capture` / `replay-reconstruction`
  on the next pull.
- **Grounded in:** `artifact.py` (schema, `record_position`, trace levels),
  `events.py` (the `on_step_complete` emission seam), `design.md` §5.2/§11.
