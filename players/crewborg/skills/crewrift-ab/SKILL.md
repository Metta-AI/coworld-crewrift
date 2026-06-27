---
name: crewrift-ab
description: "Use to decide whether a crewborg change ACTUALLY helped — A/B the candidate against the baseline head-to-head, fresh, right now, against the same field. Triggers: 'did my change help', 'compare v71 vs v70', 'A/B test crewborg', 'is the candidate better', 'did fixing the imposter regress the crew'. Runs two MATCHED fresh experience requests, diffs role-split metrics with significance (compare.py), renders a comparison report, and guides a qualitative side-by-side. It's also the 'designed run' that crewrift-experiment hands off to."
---

# Crewrift A/B

Decide whether a change **actually helped** — by running the candidate against the baseline
**head-to-head, fresh, right now**. Two halves: a **quantitative** engine (`compare.py` — role-split
metric deltas with significance) and a **qualitative** side-by-side **you** run (read both batches'
replays/logs for the *why*). The question is targeted: *did the thing I tried to improve move, and
did anything regress?* It's both the loop's re-measure step and the **designed-run experiment**
`crewrift-experiment` reaches for when existing data can't decide a hypothesis.

## The one principle that makes it valid: fresh + matched

The league field **drifts** — others change their agents constantly. So you **cannot** compare the
candidate's fresh games against the baseline's stale history; the difference is confounded by
everyone else's changes.

> **Run both versions in the same window, against the same roster/roles/count.** Two matched
> experience requests fired together → field drift hits both equally → the delta is attributable to
> *your* change. The question is "is the candidate better **now**," not "vs last week's field."

## Workflow

1. **Frame** the **baseline** + **candidate** versions and the **target axis** — the one metric the
   change was meant to move (e.g. `kills_mean`, or shrinking `imposter_no_kills_rate`). Fix your
   qualitative lens too (an opponent you lose to, a fault you're chasing).

2. **Fire two MATCHED, fresh experience requests** (`coworld-experience-requests`), byte-identical
   except the subject version:
   - **Pin every seat with an explicit `policy_ref: name:vN`** — **never `top_n`/`random` in an A/B**:
     the champion pool drifts between requests and can seat your own entry, so the arms would face
     different fields (this confound burned the v22-vs-v24 A/B).
   - Same target, same pinned roster, same roles (**natural roles unless you're testing a specific
     role** — a pinned-role config can *mask* a gap, e.g. a pinned-slot imposter A/B once hid a 30pp
     gap), same count, same window (fire back-to-back).
   - **Testing an env-flag change?** the baseline must carry **all** of the candidate's runtime env
     *minus the one flag* — isolate exactly the change, not the whole env.

3. **Pull both batches** (`coworld-episode-artifacts`), one dir per side.

4. **Quantitative diff + report:**
   ```bash
   S=players/crewborg/skills/crewrift-ab/scripts
   uv run python "$S/compare.py" /tmp/ab/base /tmp/ab/cand \
     --baseline crewborg:v70 --candidate crewborg:v71 --target kills_mean --json /tmp/ab/diff.json
   uv run python "$S/compare_report.py" /tmp/ab/diff.json --out /tmp/ab/ab.html --finding finding.md \
     --verdict "<your one-line synthesis>"
   ```
   `compare.py` leads with the target delta, then a **role-split** table of all metrics, each marked
   **improved / regressed / noise** with a p-value, plus a **regression scan** (did fixing one role
   break the other?). It's deliberately conservative — a borderline move reads as `noise`. The report
   renders this as a comparison page (effect-size delta bars, the regression scan, your finding); it's
   a **starting point — adapt/extend the visuals** (a heat map, a plot) to what the comparison shows,
   following [`report-style.md`](../../docs/reference/report-style.md), and **look at it** before presenting.

5. **Qualitative compare — the part numbers can't give.** Read both batches **side by side** through
   your lens: expand both with the version-matched `expand_replay` / the warehouse, and read
   crewborg's own logs ([`trace-logs.md`](../../crewborg/docs/trace-logs.md)) at the moments that
   matter. Write a focused finding (e.g. *"v71 lands the 2nd kill far more — `following_interval` now
   ends in a kill 7/20 vs 2/20"*). Pass it to the report as `--finding`.

6. **Synthesize the verdict:** did the target move, did anything regress, and does the qualitative
   story explain (or contradict) the numbers? A common, important outcome: numbers say *noise* but
   behaviour visibly changed → more episodes, a sharper metric, or the change didn't do what you thought.

## Discipline (the hard-won ones)

- **Matched + fresh, every time** — re-run the baseline alongside the candidate; never diff a stale batch.
- **Same tree** — build the baseline by git-stashing the candidate change, so only the subject differs.
- **Recompute on CLEAN episodes** — connect/disconnect-timeouts (`ops_fail`) hit the arms
  **asymmetrically**; drop them before comparing or the delta is contaminated.
- **Decompose by role** — crew and imposter are different policies; a change can help one and break
  the other (that's what the regression scan is for; "crew win" is a confounded team metric).
- **Respect noise** — small batches and borderline deltas are not wins; `compare.py` errs conservative
  on purpose — believe it. Rates need a few hundred appearances/side.
- **One change at a time** upstream, or the delta isn't attributable.

## See also

- **`crewrift-experiment`** — hands a hypothesis here when the test needs a designed run (this is that run).
- **`coworld-experience-requests`** / **`coworld-episode-artifacts`** — fire the matched runs / pull them.
- **`crewrift-event-warehouse`** — the deep side-by-side for the qualitative half.
- [`report-style.md`](../../docs/reference/report-style.md) — adapting the comparison HTML.
