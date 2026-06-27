---
name: crewrift-diagnose
description: "Use to turn crewborg's signals (a survey, a warehouse, league results) into an explanation and a small set of VARIED, mechanistic improvement hypotheses — what is going wrong, why (pinned to a code location), and which directions are worth testing. Triggers: 'why is crewborg weak at X', 'explain these signals', 'where are we weakest', 'what should we try', 'form hypotheses', 'turn this report into directions'. Explanatory + generative: it presents hypotheses + suggests experiments; the actual testing is the crewrift-experiment skill."
---

# Crewrift Diagnose

Turn the signals into **understanding + directions**: find where crewborg is weakest, **explain what
the signals mean**, and produce a few **varied, mechanistic hypotheses** for *why* — each a claim
about what the policy is doing (or failing to do) and the code that drives it. This is the
**explanatory** half of improvement; it hands a chosen hypothesis to **`crewrift-experiment`** to
actually test.

It does **not** decide for the human, and it does **not** run experiments. It offers *"here's what's
going on, here's what I think is causing it, here's what we could test."* The human (or you) picks.

**Announce:** "Diagnosing — I'll locate the weakness, explain the signals, and propose a few
mechanistic hypotheses with experiments to test them."

## 1. Locate the weakness (quick triage — the on-ramp, not the point)

From the survey (`crewrift-survey`) / warehouse / league standings, name **where crewborg is weakest**.
There's a small, recurring set of ways it fails — check these, pick the highest-leverage one:

| Role | Common failure | Where to read it |
|---|---|---|
| Imposter | behind in **kills** / **under-converting** (near crew but no kill) / **caught** after a kill | survey kills/g · warehouse `following_interval`/`isolation_interval` vs `kill` |
| Crew | behind in **task completion** / **killed early** / **voting wrong** (ejecting crew, missing imposters) | survey tasks/g, win% · warehouse `task_attempt`, `vote_cast`, `chat_suss` |
| Either | getting **evicted** / **always alone** (isolation) / **stuck on the map** (nav) / otherwise blocked from the goal | survey ops% · warehouse `isolation_interval`, `entered_room`/`player_state` |

Keep this short — it's the entry. The value is the explanation + hypotheses below.

> **Known standing lever (2026-06, re-verify):** the imposter is strong but **under-converts kills to
> wins** — more kills haven't moved win%, so kill→win conversion (esp. landing the 2nd kill) is the
> durable gap. A good default place to look.

## 2. Explain the signals (what do they actually mean?)

Translate the flags and numbers into **what is happening in the games**. The survey's flag symbols
(`crew_lost_nearly_won`, `imposter_no_kills`, …) and the warehouse rates are *symptoms*; say what
they imply about behaviour. Read the evidence at the relevant tier:

- the survey/warehouse **distributions** (role-split — crew and imposter are different policies);
- the **objective timeline** (`expand_replay` / the warehouse events) at the flagged moments — what
  actually happened (kills, bodies, votes, positions, true roles);
- the policy's **own logs** ([`trace-logs.md`](../../crewborg/docs/trace-logs.md)) at the same ticks —
  what it *perceived, believed, and decided*. **The gap between what was true and what it chose is
  usually where the mechanism lives.**

Present this as the explanation: "the signal says X; in the games that looks like Y; here's the
moment it goes wrong."

## 3. Generate varied mechanistic hypotheses (2–4)

For the weakness, propose a few **different, independent** mechanisms — not variations of one. A good
hypothesis is:

- **a mechanism, not a tweak** — *"X happens because Y in the code, causing Z"*, not "lower the
  threshold";
- **grounded in evidence** — cites what you actually saw (episodes, log lines, warehouse rows), never
  "this should obviously help" (~half of "obviously good" ideas regressed, and a mechanism can be
  flat **backwards** — which is exactly why a hypothesis is a claim to *test*, not assert);
- **pinned to a code location** — the mode/strategy/threshold that drives it (crewborg:
  [`design.md`](../../crewborg/design.md) + the package `AGENTS.md` "where things are"). If you can't
  point at the code, keep investigating — it's a vibe, not a hypothesis;
- **with a predicted, observable effect, per role** — what should move, and roughly how much. This is
  what `crewrift-experiment` will turn into an if-true/if-false test.

Include the **positive** outliers too — "we did unusually well here" is a mechanism to find and make
reliable, as much as "we lost badly here." The three shapes: **stop** a bad behaviour that fires,
**enable** an absent good one, **amplify/engineer the luck** of a working one.

**Tracing-escalation (autonomous):** if the logs are too thin to find the mechanism, turn up tracing
(`CREWBORG_TRACE_*`) → re-run the experience request → re-pull → re-examine. That's mechanical; don't
stop to ask. Only flag the human if getting the signal needs a *code* change to the player's tracing.

## 4. Present a readable report → hand off

Render the diagnosis as a clean HTML report for the human with `scripts/diagnose_report.py` (fill the
JSON: the weakness, the *signals-mean* explanation, and each hypothesis = evidence → mechanism [code
location] → change → predicted effect → confidence → suggested test). Each hypothesis is laid out
clearly separated.

**Adapt the HTML where the content needs it — it's a starting point, not a form to fill.** If a
hypothesis wants a small table, a diagram, or its own emphasis, author it; follow
[`report-style.md`](../../docs/reference/report-style.md) and **look at the rendered page** (serve +
screenshot, or run `ux.ify`) before you present it.

Offer the hypotheses **as options, not directives**. Then hand the chosen one to
**`crewrift-experiment`**, which designs a falsifiable test and runs it. (Diagnose suggests the
experiment; the experiment skill makes it rigorous and executes it.)

## Discipline

- **Mechanism, not tweak; code location, not vibe; predicted effect, not hand-waving.**
- **Varied, not redundant** — a spread of independent mechanisms beats three versions of one.
- **Plausibility ≠ evidence** — "obviously helps" is a reason to *test* (→ experiment), never to assert.
- **Decompose by role** — a fix can help one role and break the other.
- **Don't thrash** — investigate one signal to a grounded mechanism before spawning the next.
- **Present, don't assert; don't auto-implement.** The only autonomous action is the tracing re-run.

## See also

- **`crewrift-survey`** / **`crewrift-event-warehouse`** — the signals this explains.
- **`crewrift-experiment`** — tests a hypothesis this generates (design ↔ criticize ↔ run).
- **`crewrift-ab`** — measures whether a fix that came out of all this actually helped.
- [`crewrift-replays.md`](../../docs/reference/crewrift-replays.md) · [`trace-logs.md`](../../crewborg/docs/trace-logs.md) — the investigation surfaces.
