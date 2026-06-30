# Episode Results (`results.json`) in the Replay Viewer — Observatory UI Handoff

## Context

In the Observatory v2 episodes view, an episode detail page
(`/observatory/v2#tab=episodes&detail=episode-request:ereq_...`) already renders
`EPISODE SETUP`, `EPISODE SCORES`, `REPLAY`, a `DEBUG` panel with an
`Episode Config` / `Game Logs` tab pair, and `GAME STATS`. What it does **not**
surface is the episode's own end-of-game output — the per-slot **`results.json`**
artifact the game writes (`scores` / `win` / `imposter` / `crew` / `tasks` /
`kills` / `names` / …). That artifact is the source of truth for what actually
happened in the game (it is the same artifact the qualifier reads, and the same
one `coworld episode-results` downloads), yet a human inspecting an episode in the
viewer currently can't see it without a CLI round-trip.

**Goal:** show `results.json` in the episode replay viewer **when available**, as a
new `Results` view in the existing `DEBUG` panel — sitting alongside
`Episode Config` and `Game Logs` — gracefully degrading to a "not available"
state when the artifact is missing (a running/failed episode, or a metadata-stub
round; see the data note below).

> The Observatory web app (`web/softmax.com/src/app/(observatory)/observatory/v2/`)
> is in a **separate repo** and is NOT present here — same caveat as
> `round-scoring-explainer-handoff.md` and `filler-players-platform-handoff.md`.
> This doc is the precise, copy-pasteable spec of what that app must add. All
> `file ~lines` references below are into the sibling `../metta` repo unless the
> path is explicitly under `crewrift-prime/` or `players/`.

---

## Where the data comes from (already live — no platform change needed)

Every episode in both populations (league/tournament and experience-request)
carries a `job_id`, and the job is the universal artifact handle. The results
artifact is served directly:

```
GET /jobs/{job_id}/artifacts/results   -> results.json  (the per-slot game output)
```

(Verified live; see `players/crewborg/skills/coworld-episode-artifacts/references/endpoint-map.md`
for the authoritative route map, and `.../scripts/fetch_artifacts.py` step 2 for a
reference fetch. The same route backs `uv run coworld episode-results ereq_... --output results.json`.)

Resolving the `job_id` from what the episode page already has:

- **Experience-request episode** (`ereq_...`, this screenshot's case): the row from
  `GET /v2/episode-requests/{ereq}` has `job_id` as a **top-level field**.
- **League / tournament episode** (bare uuid): the record from `GET /episodes/{id}`
  carries it at `tags.job_id`.

The episode detail page already fetches one of these records to render
`EPISODE SETUP` / `EPISODE SCORES` (it already knows `replay_url`, `scores`,
`status`). It therefore already has the `job_id` in hand — adding the Results view
is **one extra artifact GET against an id the page already holds**, not new
discovery.

### Important: results are not the EPISODE SCORES, and not the round SCORE

Three distinct numbers exist; keep them distinct (this mirrors
`round-scoring-explainer-handoff.md`):

- **`results.json` `scores[]`** — the **raw per-slot game scores** the game wrote.
  This is exactly what the new Results view shows (verbatim, per slot).
- **EPISODE SCORES panel** (e.g. `5.8750`) — the platform's per-player episode
  score, derived from those raw scores.
- **Round `SCORE`** (RANKINGS panel) — the commissioner's win-point total; **not**
  derived from `results.json` `scores`. Do not conflate.

---

## `results.json` shape (the fields to render)

The game writes a per-slot, seat-indexed object. The fields the viewer should be
prepared to render (every array is indexed by slot `0..N-1`, `N` = seat count):

| Field | Type | Meaning |
|---|---|---|
| `scores` | `number[]` | Raw per-slot game score (**always present**, non-empty). |
| `names` | `string[]` | Per-slot display name. |
| `win` | `bool[]` | Whether the slot won. |
| `imposter` | `int[]` (0/1) | Slot played imposter. |
| `crew` | `int[]` (0/1) | Slot played crew. |
| `tasks` | `int[]` | Tasks completed by the slot. |
| `kills` | `int[]` | Kills by the slot. |
| `vote_players` / `vote_skip` / `vote_timeout` | `int[]` | Per-slot meeting-vote tallies (present on Crewrift). |
| `connect_timeout` / `disconnect_timeout` | `int[]` | Per-slot connection events. |

> The seat-indexed model the qualifier/warehouse use is mirrored in this repo at
> `players/crewborg/tools/event-warehouse/.../results.py` (`CrewriftResults`:
> `scores`, `names`, `win`, `tasks`, `kills`, `imposter`, `crew`). `scores` is the
> only guaranteed-non-empty field; treat every other array as optional and tolerate
> length skew (render `—` for a slot index past an array's end). **The viewer must
> be game-agnostic** — render whatever scalar/array keys the artifact contains;
> do not hardcode the Crewrift field list. Crewrift fields above are illustrative
> of what a row looks like, not a required schema.

`role_at(slot)` (for a friendly role column): `imposter[slot]==1 → "imposter"`,
else `crew[slot]==1 → "crew"`, else `"unknown"`.

---

## How the viewer should render it (mirror the existing DEBUG tabs)

The `DEBUG` panel already renders `Episode Config` and `Game Logs` as toggled
views with a copy-the-CLI affordance (`Game Logs` shows
`uv run coworld episode-logs ereq_... COPY`). Add `Results` the same way:

1. **Add a `Results` tab** to the `DEBUG` panel's tab group, next to
   `Episode Config` and `Game Logs` (same control style).

2. **Fetch lazily** when the `Results` tab is first opened (don't block the page):
   `GET /jobs/{job_id}/artifacts/results`. Treat a `4xx`/empty body as
   "not available" (same best-effort posture as `fetch_artifacts.py`, which logs a
   missing artifact and moves on rather than erroring).

3. **Copy affordance.** Surface the CLI equivalent inline, exactly like the
   `Game Logs` tab does, e.g.:

   ```
   uv run coworld episode-results ereq_679b4fd2-8aa4-454e-… --output results.json   COPY
   ```

4. **Available state — render two parts:**
   - A **per-slot table** (one row per slot), columns driven by whichever arrays
     are present: `slot`, `name` (`names[]`), `role` (derived), `score`
     (`scores[]`), `win`, then any remaining per-slot arrays (`tasks`, `kills`,
     `votes`, …) as additional columns. Show `—` for missing/short entries.
   - The **raw JSON** below the table (collapsible, monospace, pretty-printed) so
     the exact artifact is always inspectable — this is the literal "episode
     output" the request asks for.

5. **Not-available state.** If the artifact is missing, render a neutral empty
   state mirroring the existing `Game logs are not available for this episode.`
   copy — e.g. `Results are not available for this episode.` Optionally include the
   reason when known:
   - episode `status` is not a terminal/completed state ⇒ "Episode hasn't produced
     results yet.";
   - an `error_info` artifact exists (`GET /jobs/{job_id}/artifacts/error_info`
     returns 200) ⇒ "Episode failed — see error info.";
   - otherwise the generic line.

   A missing results artifact must **never** be rendered as a real `0` outcome
   (same trap called out in `round-scoring-explainer-handoff.md`'s
   `results_available: false` note).

6. **Filler seats (forward-compat).** Once the game writes a per-slot `is_filler`
   flag (see `filler-players-platform-handoff.md` #1), the Results table must honor
   it: dim filler rows, render their `name` as `filler policy <name>`, and exclude
   them from any per-player aggregation — exactly as specified for the EPISODES /
   replay views. Until the flag exists, render all slots plainly. Key off
   `is_filler` / `round_display["filler_policy_version_ids"]`, never a
   Crewrift-specific name list.

---

## Acceptance

- On a **completed** episode with a results artifact, the `DEBUG` panel shows a
  `Results` tab with a per-slot table **and** the raw `results.json`, plus a
  working `coworld episode-results … COPY` affordance.
- On a **running / failed / metadata-stub** episode, the `Results` tab renders the
  neutral not-available state (never a fake `0` outcome) and, when derivable, the
  reason.
- No Crewrift-specific keys are hardcoded: the table is built from whichever
  per-slot arrays the artifact contains, so a different game's `results.json`
  renders without code changes.
- When `is_filler` later lands in `results.json`, filler slots render as
  `filler policy <name>`, dimmed, and excluded from aggregation.

---

## Summary of the contract

| Layer | Owner | Status | Action |
|---|---|---|---|
| `results.json` artifact + `GET /jobs/{job_id}/artifacts/results` | Game + platform (`../metta`) | **Done / live** | none — already served |
| `job_id` on the episode record/row | Platform (`../metta`) | **Done / live** | none — page already holds it |
| `Results` tab in the episode DEBUG panel | Web app (separate repo) | **TODO** | add tab + lazy fetch + table + raw JSON + not-available state (this doc) |
| Filler-seat labeling in the Results table | Web app (separate repo) | **TODO (follow-up)** | honor per-slot `is_filler` once the game writes it (`filler-players-platform-handoff.md` #1) |

**Single rule:** the `Results` view shows the game's own `results.json` verbatim
(table + raw) when the artifact is present, degrades to a neutral not-available
state when it is not, and stays game-agnostic — no per-game field list baked into
the web app.
