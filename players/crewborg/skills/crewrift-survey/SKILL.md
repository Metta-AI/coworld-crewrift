---
name: crewrift-survey
description: "Use to turn a SET of Crewrift episodes (an experience request, a policy's recent league games, a tournament batch) into a fast, polished HTML survey: a per-policy stats table, a policy×policy win heat map, and a short list of interesting episodes (with replay links). Triggers: 'survey these episodes', 'how is crewborg doing in this batch', 'who beats whom', 'give me the report on this XP request', 'which games should I watch'. Fast + lightweight (results.json + episode.json only). Pair with coworld-episode-artifacts (to pull the episodes); the DEEP per-episode dissection is a separate skill."
---

# Crewrift Survey — fast batch overview

Turn a directory of episode artifacts into a **self-contained HTML report** a human can read at a
glance: every policy's role-split stats, a policy×policy win heat map, and the handful of episodes
worth opening (each with a one-click replay). It reads **only `results.json` + `episode.json`** — no
replay parsing, no logs — so it's instant even over hundreds of episodes.

**What it deliberately does NOT do:** death / ejection / chat counts and per-episode behaviour. Those
aren't in the artifact JSONs (`game_stats` is empty; agent metrics are just `reward`) — they need the
replay. That's the separate **deep survey** (event-warehouse). Keep this one fast.

**Announce:** "Running a fast batch survey over these episodes → an HTML report; then I'll write the
reasons for the interesting episodes and link their replays."

## Workflow

1. **Get the episodes** into one directory with **`coworld-episode-artifacts`** (you don't need
   replays or logs for the survey):
   ```bash
   F=players/crewborg/skills/coworld-episode-artifacts/scripts/fetch_artifacts.py
   uv run python "$F" --policy crewborg -n 100 --no-replay --no-logs --out /tmp/eps   # or --xreq / --pool / …
   ```

2. **Run the survey** (mint replay links for the flagged episodes):
   ```bash
   S=players/crewborg/skills/crewrift-survey/scripts/survey.py
   uv run python "$S" /tmp/eps --out /tmp/survey.html --mint-replays --title "crewborg — <what this batch is>"
   ```
   It writes the HTML **and** a `survey.interesting.json` sidecar listing the flagged episodes (their
   flags + minted replay viewer URLs).

3. **Write the reasons — your job, the part the script can't do.** The flagged episodes ship with a
   generic placeholder sentence. **Replace each with a real, specific, one-sentence reason a human can
   act on** — read the `survey.interesting.json` entry (and, if useful, glance at that episode's
   `results.json`) and say *why it's worth watching* in plain language: which policy, which role, what
   actually went wrong/right, the number that makes it stand out. Write them to a file:
   ```jsonc
   // reasons.json  —  { "<episode_dir>": "one specific sentence" }
   {
     "20260627T0729_b62c1bbe": "crewborg:v70 as crew finished all 8 tasks but the imposters still won — the clearest 'tasks done, game lost' case, watch how the votes went.",
     "20260627T0742_8322cafa": "An operational disconnect (−100) for all three crewborg versions mid-game — an infra crash to rule out, not a behaviour fix."
   }
   ```
   Make each reason **distinct and concrete** — "a should-have-won game" repeated ten times is noise;
   the whole point is interpretation a tag can't give.

4. **Re-render with your reasons + present:**
   ```bash
   uv run python "$S" /tmp/eps --out /tmp/survey.html --mint-replays --reasons reasons.json --title "…"
   open /tmp/survey.html
   ```
   Relay the headline findings to the human (role-split outcome, who beats whom, the interesting
   episodes) and point them at the report.

## What's in the report

- **① Per-policy table** (rows = `policy:vN`, aggregated, role-split): Games (crew·imp), Win% overall
  / crew / imp, Score, Kills/g (imposter), Tasks/g (crew, of 8), Voted/g, Skip/g, NoVote/g
  (abstentions, −10 each), Ops% (connect/disconnect-timeout rate). Win% is coloured sage (≥50%) /
  terracotta (<30%); your policy is highlighted.
- **② Win heat map**: cell = % of opposite-team games the **row** policy beat the **column** policy
  (hover → raw count), sage→terracotta.
- **③ Interesting episodes**: a focused, de-duplicated shortlist (rarer flags first; capped, with a
  flag-count summary), each with your reason + a replay link.

The HTML follows the **Ink & Print** house style — [`report-style.md`](../../docs/reference/report-style.md).
`survey.py` is a starting point: when a batch needs a different cut (an extra column, a second
matrix), **adapt the HTML** to fit, conform to that style, and **look at the rendered page** before
presenting.

## How to extend the table

The survey reads exactly these fields — add a column by aggregating one of them in `RoleAgg` and
adding a `<td>` in `render_html`:

| `results.json` (per slot) | already used for |
|---|---|
| `win` (bool) | win rate (and the heat map) |
| `imposter` / `crew` (1/0) | the **authoritative** role split — no inference |
| `scores` (int) | Score (mean; you could add median/quartiles) |
| `kills` (int) | Kills/g (imposter) |
| `tasks` (int) | Tasks/g (crew, of 8; you could add completion %) |
| `vote_players` / `vote_skip` | Voted/g, Skip/g |
| `vote_timeout` | NoVote/g (the −10 abstention) |
| `connect_timeout` / `disconnect_timeout` | Ops% (a crash, kept separate from behaviour) |
| `names` | display only |

`episode.json` gives the **slot→policy** map (`policy_results` for league episodes, `participants`
for XP-request episodes — `slot_policy_map` handles both), the episode `id`, `replay_url`, and
`tags.coworld_id` (the last two feed the replay link). **`game_stats` is empty** and agent metrics
are just `reward` — anything richer (deaths, ejections, chats, positions) is **not here**; it needs
the replay → the deep survey.

## Replay links

Each interesting episode links to the Observatory hosted replay viewer, minted at survey time:
`POST /v2/coworlds/replays/session {coworld_id, replay_uri}` → `{viewer_url}` (works for any episode,
league or XP). That's what `--mint-replays` does; without it the links read "not minted".

## Discipline

- **Decompose by role** — crewmate and imposter are effectively different policies; the table splits
  every rate, and the heat map is opponent-aware. Read them apart.
- **Separate ops from behaviour** — `Ops%` (the −100 connect/disconnect timeouts) is a *crash*, not a
  strategy flaw; it has its own column and its own flag.
- **Reasons must be specific** — the script flags; *you* explain. Distinct, concrete, human.
- **This surveys one batch descriptively.** "Did v2 beat v1?" is a matched fresh A/B
  (`crewrift-ab`); *why* a weakness happens is `crewrift-diagnose`.

## See also

- **`coworld-episode-artifacts`** — pulls the episodes this reads.
- **`coworld-experience-requests`** — creates the batches.
- [`best_practices.md`](../../docs/best_practices.md) — the measurement discipline (decompose by role,
  ops vs behaviour, mind small n).
