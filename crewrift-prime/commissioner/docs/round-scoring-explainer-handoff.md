# Crewrift Prime — Round Scoring Explainer (Observatory UI Handoff)

## Context

In the Observatory v2 leagues view, a completed Competition round shows a
`RANKINGS` panel (per-entrant round `SCORE`) and an `EPISODES` panel (per-player
per-episode scores like `63.0000`). Two distinct numbers were being shown with
**no commissioner-authored explanation of how either was computed**, which made
the round look unexplained/inconsistent (e.g. three entrants all showing the
same round `SCORE` of `4.00`, sitting next to MMR standings).

To fix this, the **commissioner is now the single source of truth** for a
human-readable, click-to-expand explanation of how each entrant's round score
was calculated — exactly the same philosophy as the existing `skill_gate`
explainer (`SKILL_GATE_EVIDENCE_TYPE` / `SKILL_PRESENTATION` in `decision.py`):
**the commissioner owns the strings; the web app renders them generically and
holds no Crewrift-specific copy.**

> The Observatory web app (`web/softmax.com/src/app/(observatory)/observatory/v2/`)
> is in a **separate repo** and is NOT present here. This doc is the precise,
> copy-pasteable spec of what that app must render.

### Which numbers the commissioner owns (important)

- **Per-ROUND `SCORE` (RANKINGS panel):** the commissioner computes this. It is
  the flat number of **games the entrant won** this round — 1 point for an
  imposter win, 1 point for a crew win, with **no per-seat multiplier** (winning a
  game scores once no matter how many seats the entrant held). This is what the
  new explainer describes. `imposter_wins`/`crew_wins` are an informational split
  of those won games by the winning seat's role.
- **Per-EPISODE scores (EPISODES panel, e.g. `63.0000`):** these are the **raw
  game scores** (`EpisodeResult.scores[].score`) produced by the GAME and passed
  through by the platform. The commissioner does **not** compute or rank by them;
  they are unrelated to the round `SCORE`. The explainer makes this explicit so
  the two panels stop looking contradictory.
- **Standings (leaderboard):** ranked by **all-time WIN RATE** (total episodes
  won / total episodes played, a fraction in `[0, 1]`), NOT by a raw sum of round
  scores and NOT by OpenSkill MMR (`mmr.py` is dead code). Tied win rates break
  deterministically by player id so the scheduling tick and round-complete writers
  publish the same order. **The published board now carries the per-player
  `win_rate`/`wins`/`episodes_played` columns the UI must render as `WIN %` — see
  ["Leaderboard `WIN %` column"](#leaderboard-win--column) below. Do NOT derive
  `WIN %` from `score` (that produces a share that sums to 100%).**

## What the commissioner now emits

Per Competition round, `_complete_competition_round`
(`crewrift_prime_skill_commissioner.py`) builds a structured scoring-explanation
payload via the pure `decision.build_round_scoring_explanation(...)` and surfaces
it in **two** places (identical payload):

1. `RoundComplete.round_display["round_scoring"]`
2. `RoundComplete.observability.extra["round_scoring"]` (persisted by the
   platform into `rounds.commissioner_report.extra`, alongside the existing
   `render_html`)

It also logs one greppable line: `COMMISSIONER_DECISION {"decision":"ROUND_SCORING_EXPLANATION", "type":"round_scoring", ...}`.

### Exact evidence `type`

```
round_scoring
```

(constant `ROUND_SCORING_EVIDENCE_TYPE` in `decision.py`. Game-agnostic, exactly
like `skill_gate` — the UI keys off this string, never a Crewrift-specific one.)

### Exact JSON shape

```json
{
  "type": "round_scoring",
  "method": "competition_games_won",
  "method_label": "Games won (1 point per won game — a win is a win)",
  "summary": "This round's score for each entrant is the number of games it won — one point for an imposter win and one point for a crew win, with no per-seat multiplier (winning a game scores once no matter how many seats the entrant held). Filler / duplicate top-up seats are excluded, so an entrant is only credited for the games it legitimately won as a real entrant. Entrants are ordered by this round score; that finishing position is then fed to the OpenSkill (mu - 3 sigma) rating that ranks the standings, so the leaderboard reflects skill over time rather than a raw all-time win sum.",
  "per_episode_note": "Each completed game this round contributes one point if the entrant won it; the round score is the sum across all of the round's games.",
  "score_formula": "round_score = number of games won (summed over the round's games)",
  "games_scored": 3,
  "results_available": true,
  "notes": [],
  "entrants": [
    {
      "policy_version_id": "pv-a",
      "player_id": "ply_a",
      "player_name": "crewborg-aaln",
      "policy_label": null,
      "rank": 1,
      "score": 2.0,
      "imposter_wins": 1,
      "crew_wins": 1,
      "episodes_counted": 3,
      "explanation": "Won 2 of 3 games this round (1 as imposter, 1 as crew).",
      "per_episode": [
        { "request_id": "competition:r212:0", "points": 1, "imposter_wins": 1, "crew_wins": 0, "had_results": true },
        { "request_id": "competition:r212:1", "points": 1, "imposter_wins": 0, "crew_wins": 1, "had_results": true },
        { "request_id": "competition:r212:2", "points": 0, "imposter_wins": 0, "crew_wins": 0, "had_results": true }
      ]
    }
  ]
}
```

### Field reference

| Field | Type | Meaning |
|---|---|---|
| `type` | string | Always `"round_scoring"`. The UI's render key. |
| `method` | string | Scoring-method id (`"competition_games_won"`). |
| `method_label` | string | Short human label for the method (render as the panel heading). |
| `summary` | string | The "how this round was scored" prose. Render verbatim — no UI copy. |
| `per_episode_note` | string | One line explaining the per-game breakdown. Render above the per-episode rows. |
| `score_formula` | string | The literal formula, for a monospace/secondary line. |
| `games_scored` | int | Completed games scored this round. |
| `results_available` | bool | `false` ⇒ the platform forwarded only metadata stubs and win points could **not** be computed (see data blocker below). When `false`, render `notes` prominently. |
| `notes` | string[] | Round-level caveats. Currently used only for the no-results warning. |
| `entrants[]` | object[] | One per ranked entrant, in finishing order (rank 1 first). |
| `entrants[].policy_version_id` | string | Entrant's policy version id. |
| `entrants[].player_id` | string \| null | Player id (for joining to the standings row). |
| `entrants[].player_name` / `policy_label` | string \| null | Display name when the commissioner had one; else fall back to `player_id`/short id. |
| `entrants[].rank` | int | Finishing rank this round. |
| `entrants[].score` | number | The round `SCORE` shown in the RANKINGS panel (= number of games won). |
| `entrants[].imposter_wins` / `crew_wins` | int | Informational role split of the won games (a game won across both roles increments both; they do NOT sum to `score`). |
| `entrants[].episodes_counted` | int | Scored games the entrant had a real (non-filler) seat in. |
| `entrants[].explanation` | string | One-sentence human derivation. Render verbatim as the expanded explainer body. |
| `entrants[].per_episode[]` | object[] | One row per scored game: `request_id`, `points` (0 or 1), `imposter_wins`, `crew_wins`, `had_results`. |

## How the round view should render it (mirror the `skill_gate` explainer)

The web app already renders the `skill_gate` evidence generically (it reads
`evidence.metadata.explainer` / `skills[]` and shows a "how qualification works"
panel without any game-specific strings). Render `round_scoring` the same way:

1. **Locate the payload.** On the completed-round object, read
   `commissioner_report.extra["round_scoring"]` (preferred) or
   `round_display["round_scoring"]`. If absent, render nothing (older rounds).
2. **Round-level explainer (click-to-expand).** Next to the RANKINGS panel
   header, add a small "How this round was scored" affordance (same control style
   as the skill-gate explainer toggle). Expanded, show `method_label` as the
   heading, then `summary`, then `score_formula` (monospace/secondary), then
   `per_episode_note`. All strings come straight from the payload.
3. **If `results_available === false`:** show `notes[0]` as a warning banner in
   the expander (and optionally a small "data unavailable" badge on the round),
   so a `0` score reads as "no results artifact", not a real loss.
4. **Per-entrant explainer.** In the RANKINGS panel, make each entrant row's
   `SCORE` click-to-expand. Join the row to `entrants[]` by `policy_version_id`
   (or `player_id`). Expanded, show `entrants[].explanation` (verbatim) and a
   small table of `per_episode[]` rows: game (`request_id`), `points`, and the
   `imposter_wins`/`crew_wins` split. Dim rows where `had_results === false`.
5. **Clarify the EPISODES panel.** Optionally surface `per_episode_note` (or a UI
   tooltip) near the EPISODES panel noting those per-player numbers are the raw
   game scores, distinct from the round `SCORE` (which is the win-point total
   above). The commissioner does not rank by the per-episode raw scores.

**Do not** hardcode any of the scoring prose in the web app — every string is
supplied by the commissioner so a different game's commissioner can emit its own
`round_scoring` payload (different `method`/`summary`) and the same renderer works.

## Data-availability blocker (platform-side, may force `results_available: false`)

Win points are only computable when the platform forwards the full
per-slot `results_schema` arrays (`win` / `imposter` / `crew`) in
`EpisodeResult.game_results`. Per `game_results_loader.py`, the container
round-runner **currently forwards a metadata stub** (`episode_id` / `job_id` /
`replay_url`) instead of the game-written results artifact for at least some
rounds. When that happens:

- `is_metadata_stub(...)` is true, the per-seat `win`/`imposter`/`crew` arrays are
  absent, every entrant scores `0`, and the explanation sets
  `results_available: false` with the no-results note (rather than silently
  implying a real 0-0 round).
- **Required platform change:** the round-runner must forward the game's written
  `results_schema` artifact (the seat-indexed arrays) as
  `EpisodeResult.game_results`, not the metadata stub. Once it does, the
  commissioner computes real win points and `results_available` becomes `true`
  with no further change. (Same artifact the qualifier path already re-derives by
  re-simulating the `.bitreplay`; for Competition rounds the platform should
  forward it directly from the game results.)

## Leaderboard `WIN %` column

> **TL;DR for the Observatory app:** render each row's `WIN %` from the published
> per-player `win_rate` column (equivalently `wins / episodes_played`). **Stop**
> computing it as a share of total wins (`row.score / Σ score`, or any
> `wins / total_wins`). A share normalizes to 100% across players and is wrong.

### The bug this fixes

The live leaderboard showed `WIN %` values of `30%, 20%, 20%, 10%, 10%, 5%, 5%`
for 7 players — they sum to exactly **100%**. That is a normalized **share of
total wins**, not a win rate. It happened because the commissioner previously
published only `rank` / `score` / `rounds_played`, where `score` is the
**cumulative count of won episodes** (an absolute total, not a rate). With only a
count to work from, the UI's only way to make a "percentage" was to divide each
player's count by the sum of all players' counts — which always sums to 100% and
has nothing to do with how often a player actually wins.

`WIN %` is supposed to be each player's **own** rate, independent of everyone
else's:

```
WIN % = episodes_won / episodes_played      (per player, clamped to [0, 1])
```

Two strong players can both be at 80% — these rates do **not** sum to 100%.

### What the commissioner now publishes (use these — no derivation needed)

The Competition board (`RoundComplete.leaderboards[0]`, built by
`_win_rate_leaderboard` in `crewrift_prime_skill_commissioner.py`) now declares
these columns and per-row `values`:

| Column key | `value_type` | Meaning | Use for `WIN %`? |
|---|---|---|---|
| `rank` | integer | Standings position (1 = best win rate). | — |
| `win_rate` | number | **`episodes_won / episodes_played`, clamped `[0, 1]`. THE per-player rate.** | **YES — render this as `WIN %` (× 100).** |
| `wins` | number | All-time episodes the player won (capped 1/episode). | (derivation source) |
| `episodes_played` | integer | All-time episodes the player played (win-rate denominator). | (derivation source) |
| `score` | number | DISPLAY-ONLY cumulative won-episode **count**, floored at 0. **Not a rate.** | **NO — never `score / Σ score`.** |
| `rounds_played` | integer | Rounds the player appears in. | — |

Render `WIN %` as `win_rate * 100` (it is already `[0, 1]`). If you prefer to
recompute, use `wins / episodes_played` per row (guard `episodes_played == 0` ⇒
`0`). **Never** divide by a grand total across players.

### Worked example (values do NOT sum to 100%)

Three players over 4 completed rounds; seats 0 and 1 are crew and win every round
together (a crew win credits **every** crew seat the same episode), seat 2 always
loses:

| Player | `wins` | `episodes_played` | `win_rate` | Rendered `WIN %` |
|---|---|---|---|---|
| ply_a | 4 | 4 | `4/4 = 1.0` | **100%** |
| ply_b | 4 | 4 | `4/4 = 1.0` | **100%** |
| ply_c | 0 | 4 | `0/4 = 0.0` | **0%** |

Sum of the `win_rate` column = `1.0 + 1.0 + 0.0 = 2.0` (i.e. 200%), **not** 1.0.
The old share-based UI would have shown `50% / 50% / 0%` for the same data — both
true winners diluted to 50% and forced to sum to 100%. The rate is the correct,
unambiguous number. (This exact case is asserted by
`PublishedBoardExposesTrueWinRateTest.test_win_rate_column_is_per_player_rate_not_a_share`
in `test_leaderboard_flip.py`.)

### Observatory app change (the remaining external work)

The commissioner side is done — the payload now carries `win_rate` (and the
`wins` / `episodes_played` it derives from) on every Competition leaderboard row.
The web app in the separate repo
(`web/softmax.com/src/app/(observatory)/observatory/v2/`) must:

1. **Read `win_rate`** (or `wins` / `episodes_played`) from each leaderboard row's
   `values` and render it as the `WIN %` column (`win_rate * 100`, one decimal as
   desired).
2. **Delete the share/normalization** path — any code that computes a row's
   percentage as `row.score / Σ rows.score`, `wins / total_wins`, `/ sum(...)`,
   or otherwise divides by a grand total across players. That is the source of the
   "sums to 100%" bug.
3. **Keep `score` labeled `Score`** (it is the cumulative won-episode count, a
   display total) — do not relabel it `WIN %`.
4. **Ranking is unchanged:** rows already arrive sorted by descending `win_rate`
   (`rank` ascending). Render in `rank` order; do not re-sort by `score`.

If the board predates this change (older persisted rounds without the `win_rate`
column), fall back to `wins / episodes_played` when both are present, else hide
the column rather than showing a share.
