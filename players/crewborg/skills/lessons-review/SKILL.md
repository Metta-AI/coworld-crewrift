---
name: lessons-review
description: "Periodic (≈weekly) review of crewborg's tentative-lessons archive: cluster the lessons that keep REAPPEARING across independent session buffers, propose promote/keep/cull with recurrence counts, and — on the human's call — graduate the keepers into best_practices.md and retire the reviewed buffers. Triggers: '/lessons-review', 'review the lessons archive', 'which lessons keep reappearing', 'graduate lessons'."
---

# Lessons review

Mine the tentative-lessons archive for the one signal it exists to surface: **lessons that keep
reappearing across independent sessions.** Recurrence across session buffers — not anyone's
in-session conviction — is the graduation evidence (a boring lesson seen in 3 sessions outranks a
brilliant one seen once).

**Announce:** "Reviewing the lessons archive — clustering recurring lessons across N session buffers."

## Inputs

- [`docs/lessons_archive/*.md`](../../docs/lessons_archive/) — one buffer per past session (rotated in
  automatically by the SessionStart hook). `lessons_archive/reviewed/` holds already-reviewed
  buffers — **exclude them from the candidate set**, but still count them when judging a fresh lesson's
  recurrence.
- [`docs/TENTATIVE_LESSONS.md`](../../docs/TENTATIVE_LESSONS.md) — the live buffer; include it
  **read-only** (it stays in place; this review never retires it).
- [`docs/best_practices.md`](../../docs/best_practices.md) — the graduation target; also check a
  candidate isn't already there.

## Workflow

1. **Collect** every `### ` lesson from the unreviewed archives (+ the live buffer), keyed by
   (file, title, evidence).
2. **Cluster semantically** — the same underlying lesson worded differently counts as recurrence.
   Cite which sessions each cluster appeared in.
3. **Propose**, as a table for the human: **promote** (recurred in ≥2–3 sessions, or single-occurrence
   but high-stakes *and* verified), **keep waiting** (plausible, 1 occurrence), **cull** (contradicted,
   superseded, or noise). Give the one-line lesson, recurrence count + dates, and a recommendation
   with a reason. **The human decides — do not graduate without their call.**
4. **Apply the decisions:** graduated lessons → [`docs/best_practices.md`](../../docs/best_practices.md),
   **rewritten as durable practice prose** (not buffer-entry format), in the right part (general vs
   Crewrift-specific). Culled lessons just retire with their buffer.
5. **Retire reviewed buffers** → `git mv` them into `docs/lessons_archive/reviewed/`. Waiting lessons
   stay discoverable there (future reviews count recurrence against `reviewed/` too).
6. **Commit** with a summary: N buffers reviewed, promoted / waiting / culled counts.

## Discipline

- **Recurrence beats eloquence** — graduate what *keeps coming back*, not what reads well once.
- **Check the target first** — don't re-promote something already in `best_practices.md`.
- **Negative results are findings** — a lesson contradicted by later evidence gets culled *with a
  note* in the commit message, not silently dropped.

## See also

- [`docs/TENTATIVE_LESSONS.md`](../../docs/TENTATIVE_LESSONS.md) + [`lessons_archive/README.md`](../../docs/lessons_archive/) — the lifecycle this closes.
- [`docs/best_practices.md`](../../docs/best_practices.md) — where keepers land.
