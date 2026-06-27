# crewborg — a Crewrift player and its optimization toolkit

> **Why crewborg?** Of the Crewrift players here, crewborg is the one built to be *optimized*, not
> just run. It's a **cyborg policy** — a fast, deterministic inner loop (perception → belief →
> suspicion → strategy → action, every tick, never blocking) under a slower **strategic outer loop**
> where **gated LLM layers steer without stalling play**: an LLM that runs **meetings** (chat + votes)
> and a tunable **LLM commander** that biases the agent's priorities from the outer loop. The policy
> is **highly modular Python** — clean, documented components you change *surgically* (one threshold,
> one mode, one cue) instead of fighting a Nim monolith — and it ships inside a **fully fleshed-out
> optimization system**: an 11-skill toolkit, a queryable event warehouse, a fitted suspicion model,
> and battle-tested disciplines, all documented end-to-end, with **first-class tracing** so every
> decision is observable. Crucially, crewborg was **not auto-researched**: every cue, threshold, and
> strategy here was reached through a **fully human-in-the-loop** process — **humans drive the
> ideation, experimentation, and analysis**, the agent builds the observability and holds the
> measurement. The optimization system below is built for *that* collaboration, not push-button
> automation. If you want to *improve* a Crewrift agent rather than start one from scratch, start here.

This directory is **two things in one**: the **`crewborg`** player policy for the Crewrift game, and
the **self-contained toolkit for optimizing it** — the skills, tools, references, and disciplines a
human + a coding agent use to evaluate crewborg, find where it falls short, improve it, and ship it.
Everything you need is here; you rarely leave this folder.

This README is the **front door** — what's here, how it's laid out, where to look. For *how the
optimization loop actually runs* (the procedure a coding agent follows), read **[`AGENTS.md`](AGENTS.md)**.

## The game, in brief

Crewrift is a Coworld **social-deduction game** (an *Among Us*-style benchmark): 8 players on a 2-D
map. Most are **crewmates** doing tasks; a couple are **imposters** who kill and blend in. Bodies are
reported, meetings are called, players chat and **vote** someone out. Crew win by finishing tasks or
voting out the imposters; imposters win by killing enough crew. A policy speaks the binary
**Sprite-v1** protocol over a websocket — it receives the rendered scene and acts with a d-pad + A/B;
there is **no semantic action API**. Full game reference: [`docs/reference/crewrift-gameplay.md`](docs/reference/crewrift-gameplay.md).

## Layout

```
players/crewborg/
  coplayer_manifest.json    the player declaration (author / name / run / games)
  crewborg/                 THE PLAYER — the Python policy package (see below)
  docs/                     knowledge & process: best practices, references, working state, lessons
  skills/                   the optimizer toolkit — 11 invocable skills (eval loop + analysis)
  tools/                    scripts behind the skills: build, replay reader, viz, the event warehouse
  suspicion_lab/            the data-science sub-project that fits crewborg's suspicion weights
  README.md                 ← you are here (orientation)
  AGENTS.md                 the operating model: how a coding agent runs the loop
```

## The player — `crewborg/`

A full Python player built as a deterministic-first pipeline with optional, gated LLM layers. The
cognitive stack: **perception → belief → suspicion → strategy → modes → action**, driven over the
wire by `coworld/`.

- **[`crewborg/README.md`](crewborg/README.md)** — the package's own front door (the 60-second model,
  the file map, the env-var table, where to make changes). **Start here to work on the player.**
- **[`crewborg/design.md`](crewborg/design.md)** — the architecture / "where things are" map.
- **[`crewborg/docs/`](crewborg/docs/README.md)** — nine deep-dives on the cross-cutting concepts
  (perception-and-belief, suspicion, meetings, imposter-play, crewmate-play, navigation, commander,
  agent-tracking, trace-logs).
- Key dirs: `perception/` (Sprite-v1 → scene), `strategy/` + `modes/` + `action.py` (the brain),
  `coworld/` (the websocket bridge + the Dockerfile), `data/suspicion_weights.json` (the fitted
  suspicion model), `tests/`, `tools/` + `viewer/` (in-package replay analysis).

## Knowledge & process — `docs/`

- **[`docs/best_practices.md`](docs/best_practices.md)** — the battle-tested disciplines for the loop
  (measurement rigor, diagnosis, hypotheses, the gates, working-with-the-human). **Read on startup;
  treat as defaults.**
- **[`docs/reference/`](docs/reference/README.md)** — the ground-truth, source-cited reference for the
  **game** and the **platform**: `crewrift-gameplay.md`, `crewrift-protocol.md` (Sprite-v1),
  `crewrift-replays.md` (reading finished games), `coworld-platform.md` (the runner + Bedrock), and
  `report-style.md` (how the HTML reports look).
- **[`docs/WORKING_CONTEXT.md`](docs/WORKING_CONTEXT.md)** — the live, one-screen state of what's being
  worked on now (the active version, the current lens, open threads). The resume signal.
- **[`docs/user_preferences.md`](docs/user_preferences.md)** — the human's durable preferences.
- **[`docs/TODO.md`](docs/TODO.md)** — parked work.
- **[`docs/TENTATIVE_LESSONS.md`](docs/TENTATIVE_LESSONS.md)** + [`lessons_archive/`](docs/lessons_archive/) —
  the per-session lesson buffer → archive → graduation lifecycle (the `lessons-review` skill).

## The optimizer toolkit — `skills/` + `tools/`

The **11 skills** are the loop made operational — the eval machinery
(`coworld-experience-requests`, `coworld-episode-artifacts`, `coworld-local-run`,
`coworld-policy-lifecycle`, `build-and-upload`) and the analysis suite (`crewrift-survey`,
`crewrift-diagnose`, `crewrift-experiment`, `crewrift-ab`, `crewrift-event-warehouse`,
`lessons-review`). The **tools** are the scripts behind them (the build, the version-matched
`expand_replay` reader, the positioning viewer, the vendored event-warehouse package). Each carries
its own docstrings/README. **The full, annotated catalog with pointers is in
[`AGENTS.md`](AGENTS.md)** — that's where to internalize what each offers.

## The suspicion lab — `suspicion_lab/`

The **data-science half** of crewborg's suspicion system: a pipeline (scrape → expand → dataset → fit
→ eval) that produces `crewborg/data/suspicion_weights.json`. crewborg ships with fitted weights, so
the player works out of the box; the lab is for **refitting/improving** them. **Its data dirs are
empty in a fresh checkout and must be regenerated** — see [`suspicion_lab/README.md`](suspicion_lab/README.md).

## Doing optimization

The short version: **evaluate → diagnose → experiment → improve → re-measure → (gated) submit.** Run
hosted games to see where crewborg stands, find the mechanism behind a weakness, test it cheaply,
change one thing, prove it helped, and submit only when it's demonstrably better and the human says
so. The **procedure, the skills/tools to use at each step, and the two gates** are in
[`AGENTS.md`](AGENTS.md); the **disciplines** are in [`docs/best_practices.md`](docs/best_practices.md).

## Building it

crewborg builds to a `linux/amd64` Docker image (the Coworld upload contract) from
`crewborg/coworld/Dockerfile` — `tools/build/build_player.sh` (or the **`build-and-upload`** skill).
The image installs the shared player SDK from the public `Metta-AI/coworld-tools` repo (pinned in
`tools/build/versions.env`) and runs `python -m crewborg.coworld.policy_player`. All inputs are
public, so a build needs only Docker — no credentials.
