# User preferences

Durable preferences the human has expressed for working on crewborg — how to communicate, what to do
or avoid, and defaults to assume (e.g. how evaluations should be set up, what they care about
measuring). **Read this on startup** and treat the entries as your defaults.

**Why this file matters.** Preferences are *standing instructions*, not one-off requests — recording
them here means you don't re-ask the same question every session or re-litigate a decision the human
has already made. A preference the human states once should change your behavior in every future
session until they change it.

## How to fill it in

When the human states a preference — **explicitly** ("always do X", "never Y", "I prefer Z"), or
**implicitly through repeated correction** (they keep steering you the same way) — record it here:

- One **short, concrete** bullet per preference, in the imperative ("Always run 2-imposter evals…").
- Add the **date** and, when useful, a one-line **why** so a future agent understands the rationale,
  not just the rule.
- Keep it tidy: **drop superseded entries** when a newer preference replaces them. This file should
  stay short and current, not accumulate history.

## What does NOT go here

- **Live state of the current work** (the active objective, the version under test) → [`WORKING_CONTEXT.md`](WORKING_CONTEXT.md).
- **Durable, game-agnostic disciplines** (measurement rigor, the loop) → [`best_practices.md`](best_practices.md).
- **Candidate learnings still being validated** → [`TENTATIVE_LESSONS.md`](TENTATIVE_LESSONS.md)
  (these graduate into `best_practices.md` once they recur, not here).
- **Deferred tasks** → [`TODO.md`](TODO.md).

This file is for the human's *durable preferences* only. When in doubt: is it a rule the human wants
applied going forward (here), the current objective (working context), or a discipline true of any
player (best practices)?

> Read on startup, alongside [`best_practices.md`](best_practices.md) and
> [`WORKING_CONTEXT.md`](WORKING_CONTEXT.md). (The player's top-level `AGENTS.md` / `README.md` will
> point here once they exist.)

## Preferences

_None recorded yet — preferences are the human's to state, not something this player inherits. Add
them here as they come up._

- **XP requests > 16 episodes: always bring up the dashboard** (James, 2026-07-01).
  Whenever you create an experience request with more than 16 episodes, start the XP
  dashboard (`players/crewborg/skills/coworld-experience-requests/scripts/xp_dashboard.py
  --port <port> xreq_...`) and give the human the `http://localhost:<port>` link in the
  same message that reports the request was created.

- **Always upload policies with ALL telemetry enabled unless told otherwise** (James, 2026-07-01).
  Every `coworld upload-policy` gets `--secret-env CREWBORG_METRICS=1 --secret-env
  CREWBORG_TRACE_GROUPS=all`. Massive logs when needed beat re-uploading the same policy
  and re-running XP requests. NB league artifacts are ephemeral (~one round's retention) —
  harvest them promptly each round.
