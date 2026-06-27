# crewborg — agent operating guide

How a coding agent works in this package: orient, run the optimization loop, and use the skills +
tools. This is the *procedure*; the *map* of what's here is [`README.md`](README.md).

## On arrival — orient yourself

1. **Read [`README.md`](README.md)** — it lays out the player, the docs, the skills, the tools, and
   the suspicion lab, and where each lives.
2. **Look around and follow the links.** Build real contextual understanding before you act — skim
   [`crewborg/README.md`](crewborg/README.md) + [`crewborg/design.md`](crewborg/design.md) for the
   player's shape, [`docs/reference/`](docs/reference/README.md) for the game/platform ground truth,
   and the SKILL.md of any skill you're about to use. You don't need every detail, but you must know
   *where* things are and *how they connect* before changing code or running an eval.
3. **Read these on startup:** [`docs/best_practices.md`](docs/best_practices.md) (your defaults —
   warn the human if a request would contravene one), [`docs/user_preferences.md`](docs/user_preferences.md)
   (the human's standing preferences), and [`docs/WORKING_CONTEXT.md`](docs/WORKING_CONTEXT.md) (the
   live state — what's being worked on now; a recorded objective there means resume the loop).
4. **Check [`docs/TODO.md`](docs/TODO.md)** for parked work at the start of focused work.

## Your role

The **human originates the strategic jumps and judges gameplay quality; you implement them, build the
observability that reveals where a jump is possible, measure rigorously, and hold the correctness
gate.** Your highest-leverage work is making the human's judgment cheap and well-informed — clear
options, visible behavior, trustworthy numbers — not replacing his decisions. **Talk with the human
plenty:** surface decision-ready forks, present hypotheses and experiment designs as options, and
**propose-and-pause** when a thread finishes rather than auto-chaining into unrequested work
(especially gameplay changes). Widen what you do *between* his decisions.

## The optimization loop

**evaluate → diagnose → experiment → improve → re-measure → (gated) submit.** Concretely:

1. **See where crewborg stands.** Run experience requests on a flat/representative field
   (**`coworld-experience-requests`**) *or* pull the latest tournament round's games
   (**`coworld-episode-artifacts`**), then turn the batch into a fast overview with **`crewrift-survey`**
   (per-policy role-split table + win heat map + flagged episodes). Decompose by role — crewmate and
   imposter are effectively two policies.
2. **Diagnose.** Turn the signals into a few *varied, mechanistic* hypotheses for where it falls short
   with **`crewrift-diagnose`** (explain the signals → hypotheses pinned to code, presented as
   options). **Consult the human.**
3. **Experiment.** Test whether a diagnosis is actually right with **`crewrift-experiment`**: design a
   *falsifiable* test (if-true vs if-false must differ), criticize it, run it. The **cheapest** test is
   usually a **`crewrift-event-warehouse`** query over data you already have; otherwise a designed run
   (**`crewrift-ab`**) or added tracing. Don't change code on a hunch — confirm the mechanism first.
4. **Implement.** Change **one** component (so the next eval is attributable); keep tunable knobs in
   config, not logic.
5. **Gate 1 + ship the artifact (routine).** Smoke it locally (**`coworld-local-run`** — did the change
   take, does it connect→play→exit cleanly; *not* a matchup), then **build & upload a new version**
   (**`build-and-upload`**). Uploading is inert — it just gives you a testable artifact. Record the
   version → change in [`crewborg/version_log.md`](crewborg/version_log.md).
6. **Re-measure.** Evaluate the new version vs the baseline — **`crewrift-ab`** (matched + fresh) — and
   run more experiments until it's **demonstrably better**.
7. **Gate 2 — submit (gated, the human's call).** Only once it's clearly better **and the human gives
   explicit go-ahead**, submit to the league and watch it **qualify** with **`coworld-policy-lifecycle`**.

**The two gates.** Gate 1 (yours, every iteration) is smoke + correctness — *never* a comparative
test; uploading is routine and ungated. Gate 2 (the human's, rare) is **league submission** — public,
likely to become champion once it qualifies, hard to roll back. You avoid rollback by uploading freely
but **not submitting** until the player is better and the human approves.

## Skills — the toolkit (read a skill's `SKILL.md` before using it)

*Eval & ship machinery:*
- **`coworld-experience-requests`** — create + monitor hosted episode batches (the primary eval
  instrument) → [skill](skills/coworld-experience-requests/SKILL.md) (+ `references/api.md`).
- **`coworld-episode-artifacts`** — download episodes' replays / results / per-agent logs →
  [skill](skills/coworld-episode-artifacts/SKILL.md) (+ `references/endpoint-map.md`).
- **`coworld-local-run`** — Gate-1 local smoke test of a built image →
  [skill](skills/coworld-local-run/SKILL.md) (+ `references/cli.md`).
- **`build-and-upload`** — build the crewborg amd64 image → upload it as a new version (the routine,
  every-iteration action; incl. the LLM upload recipe) → [skill](skills/build-and-upload/SKILL.md).
- **`coworld-policy-lifecycle`** — **submit & monitor**: the gated submit → watch it **qualify** →
  champion (assumes already uploaded) → [skill](skills/coworld-policy-lifecycle/SKILL.md).

*Analysis:*
- **`crewrift-survey`** — fast batch overview → a polished HTML report (per-policy table, win heat
  map, interesting episodes w/ replay links). Reads results+episode JSON only →
  [skill](skills/crewrift-survey/SKILL.md).
- **`crewrift-diagnose`** — explain the signals → a few varied mechanistic hypotheses (presented as
  options); renders an HTML report → [skill](skills/crewrift-diagnose/SKILL.md).
- **`crewrift-experiment`** — test ONE hypothesis: design ↔ adversarial-critique ↔ run; renders an
  HTML design report and gates on the human before running → [skill](skills/crewrift-experiment/SKILL.md).
- **`crewrift-ab`** — "did v2 beat v1, now": matched-fresh A/B with role-split significance + a
  comparison report → [skill](skills/crewrift-ab/SKILL.md).
- **`crewrift-event-warehouse`** — build + query a policy-indexed DuckDB/Parquet **event** dataset
  (the deep dig; the default cheap experiment) → [skill](skills/crewrift-event-warehouse/SKILL.md)
  (+ `references/event-catalog.md`, `references/recipes.md`).
- **`lessons-review`** — cluster recurring lessons across session buffers → graduate keepers to
  best_practices → [skill](skills/lessons-review/SKILL.md).

## Tools — the scripts behind the skills (each is self-documented)

- **`tools/build/`** — building the player: `build_player.sh` (crewborg amd64 image), `nav_bake.py`
  (bake the nav asset), `versions.env` (central SDK + deployed-game pins).
- **`tools/build_expand_replay.sh`** — build the **version-matched** `expand_replay` binary that reads
  Crewrift replays (must match the deployed game commit — the #1 gotcha; documented in the file).
- **`tools/behavior_compare.py`** — per-game behavioural head-to-head of *any* policies, either role,
  from a built event-warehouse (proximity/isolation/follow/chase/rooms/ended-dead).
- **`tools/positioning_viz/`** — a Flask + canvas viewer for *where* players sit at key moments.
- **`tools/event-warehouse/`** — the vendored event-warehouse package (the `crewrift-event-warehouse`
  skill drives it; `build` / `serve` / `suss`).
- **`tools/rotate_lessons.sh` · `lessons_stop_nudge.sh`** — the SessionStart/Stop hooks that drive the
  lessons lifecycle.
- **`suspicion_lab/`** — the data-science pipeline that fits `crewborg/data/suspicion_weights.json`
  (its own [README](suspicion_lab/README.md); **its data dirs start empty — regenerate them**).

## Session state & lessons — keep them current

- **[`docs/WORKING_CONTEXT.md`](docs/WORKING_CONTEXT.md)** — the live one-screen state. Update it as
  you learn; clear/reseed it on a pivot.
- **[`docs/TENTATIVE_LESSONS.md`](docs/TENTATIVE_LESSONS.md)** — write candidate lessons here **eagerly,
  as you go** (most are noise; the value is the occasional gem). A SessionStart hook archives the
  buffer and a Stop hook nudges if substantive work ended with it untouched; `/lessons-review`
  graduates lessons that **recur across sessions**.
- When the human states a durable preference, record it in
  [`docs/user_preferences.md`](docs/user_preferences.md); when you defer something, add it to
  [`docs/TODO.md`](docs/TODO.md).

**On wrap-up of a thread:** capture every tentative lesson, reconcile WORKING_CONTEXT (prune stale
detail, update the active version), and propose the next step — don't auto-chain.

## Disciplines (full set in `docs/best_practices.md` — read it)

- **Decompose by role** — crew and imposter are different policies; an aggregate hides a broken role.
- **Measure on batches, ops-filter** the connect/disconnect-timeouts (a crash, not strategy).
- **A/B matched + fresh** (the field drifts); **one change at a time**; respect noise.
- **Hypotheses are mechanisms pinned to code, tested falsifiably** — plausibility ≠ evidence; a
  mechanism can be backwards.
- **Upload freely; submit rarely** — Gate 2 is the human's.
