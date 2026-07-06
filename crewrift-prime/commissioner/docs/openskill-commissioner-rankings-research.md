# OpenSkill for Crewrift Prime — Commissioner Rankings & Placements (research)

**Status:** Research report (no code changes).
**Scope:** How to adopt the OpenSkill (Plackett–Luce) skill rating from upstream
PR [Metta-AI/metta#16527](https://github.com/Metta-AI/metta/pull/16527) for the
**Crewrift Prime** commissioner's **Competition ranking** and for a real
**placement** notion, replacing the current cumulative win-count leaderboard.

---

## 0. TL;DR

- **PR #16527 is a read-path change** in `app_backend` (the platform-owned
  "Live"/MMR board for divisions whose ranking is **not** overridden). It swaps
  the opponent-blind *mean-of-round-scores* `rank_division_by_window_score` for
  `rank_division_by_mmr` (OpenSkill Plackett–Luce), **per policy version**, with a
  **player-prior init** and a **5-game placement gate**.
- **Crewrift Prime does NOT use the platform ranker for its Competition
  division.** Its commissioner *overrides* `rank_division` (see
  `crewrift_prime_skill_commissioner.py:601`) to rank the Competition leaderboard
  by **cumulative winning-player points** (`imposter_wins + crew_wins` summed over
  rounds, per *player*). So PR #16527 does **not** reach Crewrift Prime
  automatically — adopting OpenSkill here is a **commissioner-side change** that
  mirrors the PR's algorithm inside `rank_division`.
- **Two distinct surfaces** map onto the PR's two ideas:
  1. **Commissioner rankings** → the **Competition** leaderboard ordering
     (`rank_division`). Today: cumulative wins. Proposed: OpenSkill ordinal
     `mu − 3σ`.
  2. **Commissioner placements** → "a policy is *rated but unranked* until it has
     played N rated games" (`in_placement`). Crewrift Prime already has a
     *qualifier* skill gate (Qualifiers→Competition); OpenSkill **placement** is a
     *second, lighter* gate **inside** Competition (don't show a numeric rank until
     5 Competition games), orthogonal to the qualifier gate.
- **The hard part is the unit of rating.** The PR rates **policy versions**;
  Crewrift Prime's Competition leaderboard is **player-keyed** (it aggregates a
  player's `policy_version_ids`). And a Crewrift "round" is **multi-episode
  self-mixed 8-seat free-for-all**, not the PR's clean "one round = one 4-seat
  match by finishing rank". The finishing-rank signal must be **derived** from
  `game_results` (wins/role), which is the real design work (§4).
- **Dependency reality:** the commissioner image vendors its own package
  (`vendor/`) and is built/shipped via `coworld patch-commissioner` — so adding
  `openskill` is a **Dockerfile/vendor requirement add**, independent of the
  upstream `app_backend` `uv.lock` edit in the PR.

---

## 1. What PR #16527 actually does (verified from the diff)

Files changed are all in `app_backend` (backend read path) + `web/softmax.com`
(UI) + `uv.lock`/`pyproject.toml`. Key mechanics in
`app_backend/src/metta/app_backend/v2/commissioners.py`:

- **Library:** `from openskill.models import PlackettLuce`; `model = PlackettLuce()`.
  Patent-free TrueSkill-equivalent, multiplayer-native (right fit for free-for-all
  with a full finishing order). Added as `openskill>=6.0.0`.
- **Rating unit = policy version.** Per-policy-version mutable `(mu, sigma)`; the
  displayed **MMR = conservative ordinal `mu − 3σ`**.
- **One round = one match.** It replays a division's **completed rounds
  oldest-first**; each round's participating policy versions are fed to the rater
  **ordered by finishing `rank`** (`model.rate([[r1],[r2],...], ranks=[...])`),
  each as a 1-member "team". Rounds with `< 2` policies are skipped (no
  comparative signal).
- **Best-result-per-round dedup.** Keeps each policy's best result per round
  (highest `score`, ties broken by lower `rank`) so multi-episode rounds
  contribute one finishing position per policy.
- **Player-prior init.** A brand-new policy version from a player who already has
  a rated policy starts at that player's **best established `mu`** (with the wide
  default `sigma`) — `player_prior_mu[player_id]`, updated once a policy clears
  placement. So a new version's first ranks aren't insane; placement then tightens
  `sigma`.
- **Placement gate.** `MMR_PLACEMENT_MIN_GAMES = 5`. A policy is **rated but
  `rank=None` ("in placement")** until it has played 5 rated games. Prevents one
  lucky win rocketing a new policy to the top.
- **W/L.** `wins` = first-place (`rank == 1`) finishes; everything else a loss.
- **Surface:** `GET /v2/divisions/{id}/policy-leaderboard` drops `window_minutes`,
  returns `PolicyMmrEntryPublic { rank?, mmr, wins, losses, games_played,
  in_placement }`. The per-player EWMA "All-time" board is unchanged.
- **Not in scope (explicitly):** matchmaking — "stop matching very weak policies
  once their rating is established" is left as a follow-up commissioner change.

> **The PR is exactly the thing the user wants, but it lives in the wrong layer
> for Crewrift Prime.** Crewrift Prime overrides ranking in its commissioner, so
> the PR's read-path MMR never applies to its Competition board. We re-implement
> the same algorithm inside the commissioner.

---

## 2. Where Crewrift Prime ranks today (and why the PR doesn't reach it)

`CrewriftPrimeSkillCommissioner` (`crewrift_prime_skill_commissioner.py`):

- **`rank_division` override (`:601-665`)**: for the **Competition** division
  (`type == "competition"`), it ignores the stock/PR ranker entirely and computes
  a **per-player cumulative win total**:
  - dedup best result per `(player_id, round_id)`,
  - `totals[player_id]["wins"] += result.score` (score = winning-player points),
  - order by `-wins`, emit `DivisionLeaderboardSnapshot(player_id, score=wins,
    rounds_played, policy_version_ids, recent_rounds)`.
  - Other divisions `return super().rank_division(ctx)` (the stock EWMA path; the
    PR would change *that* fallback if the vendored package were updated — but
    Crewrift Prime's Competition board never hits it).
- **`_complete_competition_round` (`:509-599`)**: sets each entrant's per-round
  `score` to the role-weighted won-episode points (3 pts per episode won as
  imposter, 1 pt per episode won as crew; each episode scored at most once),
  records `wins`, `points`, `imposter_wins`, `crew_wins` in `result_metadata`.
- **`rank_division` input** is a `DivisionLeaderboardContext` (vendored
  `models.py:449`): `completed_rounds`, `recent_rounds`, and **`round_results:
  list[LeaderboardRoundResultSnapshot]`** where each row carries `player_id`,
  `policy_version_id`, `round_id`, `rank`, `score`, `result_metadata`. **This is
  the same shape the PR's MMR ranker consumes** (the PR's
  `MmrRoundResultSnapshot` is `RoundResultSnapshot` + policy/player labels), so the
  data needed to run OpenSkill is **already delivered to the commissioner** — no
  new platform plumbing required for the Competition board.

**Implication:** porting the PR into `rank_division` is mechanically
straightforward because the context already contains `completed_rounds` (for
oldest-first replay) and per-`(policy_version, round)` `rank`/`score`. The open
question is *what counts as a finishing rank* in a Crewrift round (§4).

---

## 3. The two surfaces, precisely

### 3.1 "Commissioner rankings" = Competition leaderboard ordering

Replace the cumulative-win ordering in `rank_division` with OpenSkill ordinal
`mu − 3σ`. Decision: **rate per policy version or per player?**

| Option | Pro | Con |
|---|---|---|
| **A. Per policy version (mirror the PR), then collapse to player by best policy** | Matches the PR 1:1; reusable; placement & player-prior come for free; a player's rank = their strongest current policy | Crewrift's leaderboard is player-keyed → need a deterministic policy→player collapse (take the player's max-`mu−3σ` out-of-placement policy) |
| **B. Per player directly** | Simplest given the player-keyed board | Loses the PR's player-prior trick (it *is* the player), can't show per-policy placement, conflates a player's old/new policies into one noisy rating |

**Recommend A** — rate policy versions exactly as the PR does, then derive the
player row from the player's **best out-of-placement policy** (fall back to best
in-placement if none cleared placement). This keeps the player-keyed
`DivisionLeaderboardSnapshot` contract while preserving every PR property
(placement, player-prior init, per-policy MMR for observability).

### 3.2 "Commissioner placements" = rated-but-unranked gate

Crewrift Prime has **two** gates after this change:

1. **Qualifier gate (existing, unchanged):** Qualifiers→Competition, the strict
   three-skill single-game gate (`decision.py`). This decides *admission* to
   Competition. **Do not touch it.**
2. **MMR placement gate (new, from the PR):** *inside* Competition, a freshly
   promoted policy is **rated but shows no numeric rank** until it has played
   `MMR_PLACEMENT_MIN_GAMES` (5) Competition rounds. This decides *when a
   Competition member earns a displayed rank*.

These are orthogonal and complementary: the qualifier gate proves *capability*;
the placement gate prevents *rating-noise* from a tiny Competition sample. Surface
`in_placement`/`games_played` in the `DivisionLeaderboardSnapshot` (needs a small
model extension — §5) so the UI can show "in placement (3/5)".

---

## 4. The core design problem: finishing rank in a Crewrift round

The PR feeds `model.rate(teams, ranks=[finishing positions])`. In Agricola each
round is one 4-seat FFA with a natural full finishing order. **Crewrift Prime
Competition rounds are different:**

- A round = **`num_episodes` 8-seat self-mixed games**
  (`_schedule_competition_round`), seats round-robin-assigned across the N
  entrants, so an entrant occupies a **subset of seats across several episodes**.
- Per-round `score` = **winning-player points** (`imposter_wins + crew_wins`),
  already computed in `_complete_competition_round` and stored as the round
  `rank`/`score` in `result_metadata`.

So the round already produces a per-entrant `(rank, score)` — **that ranking is
exactly the finishing order OpenSkill needs.** Three ways to derive the match
ranks fed to `model.rate`, cheapest first:

- **(R1) Use the round's existing per-entrant rank (recommended first cut).**
  Each Competition round already ranks its entrants by winning-player points
  (`_complete_competition_round` sorts and assigns `rank`). Feed *that* round-level
  finishing order as one OpenSkill match (ties when scores tie). **Zero new
  signal needed** — `rank_division` already receives these per-`(policy,round)`
  ranks in `round_results`. This is the smallest, safest port: one round = one
  match, ranked by the points the commissioner already computes.
- **(R2) Per-episode matches (finer, more matches).** Treat **each episode** as a
  match: rank the seats/entrants within an episode by per-seat outcome (winning
  seats beat losing seats; tie within a class). Gives ~`num_episodes`× more rating
  updates per round (faster `sigma` convergence, better placement) but requires
  `rank_division` to see per-episode results — which it does **not** today (it
  only gets round-aggregated `round_results`). Would need either per-episode rows
  threaded into the context or the rating computed in `_complete_competition_round`
  (where episode results *are* available) and persisted to `state`/`result_metadata`.
- **(R3) Role-split ratings (richest, future).** Crewrift is role-asymmetric
  (imposter vs crew). Rate two sub-skills per policy (imposter-rank, crew-rank)
  from the per-role winning-seat outcomes, combine for the displayed MMR. Matches
  the repo's non-negotiable **role disaggregation** rule
  (`crewrift-eval-design`). Heaviest; defer to a later phase.

**Recommend R1 for v1** (pure `rank_division` change, no new plumbing), with R2 as
the immediate follow-up once per-episode signal is available, and R3 as the
role-aware end state.

---

## 5. Concrete integration plan (commissioner-side, phased)

### Phase 1 — OpenSkill ordinal ranking in `rank_division` (R1), per-policy with player collapse

1. **Dep:** add `openskill>=6.0.0` to `crewrift-prime/commissioner/requirements`/
   `vendor` install in the `Dockerfile` (the commissioner image is independent of
   the upstream `uv.lock`).
2. **New pure module** `commissioner/mmr.py` (mirror the PR's `rank_division_by_mmr`
   so it's unit-testable offline like `decision.py`): input the same
   `(completed_rounds, round_results)` already in `DivisionLeaderboardContext`;
   replay rounds oldest-first; `model.rate` per round by the existing per-entrant
   `rank`; track per-policy-version `(mu, sigma, wins, losses, games_played)`,
   `player_prior_mu`, and `MMR_PLACEMENT_MIN_GAMES`. Keep the PR's exact
   semantics (best-result-per-round dedup, `< 2` skip, `mu − 3σ`).
3. **`rank_division` override:** for the Competition division, call `mmr.py`, then
   **collapse policy versions → player** (player row = the player's best
   out-of-placement policy `mu−3σ`; fall back to best in-placement). Order players
   by that MMR. Emit the existing `DivisionLeaderboardSnapshot` (now `score =
   player_best_mmr`, plus the per-policy MMR/placement in a metadata side-channel).
4. **Observability (reuse the existing pattern):** log a
   `COMMISSIONER_DECISION {"decision":"MMR_RANK", policy, mu, sigma, mmr, wins,
   losses, games_played, in_placement}` line per policy (matches the README's
   greppable-log convention), and stash per-policy ratings in
   `RoundComplete.state["crewrift_prime_skill"]` so the rating history is auditable.

### Phase 2 — Placement surfacing + UI

5. Extend `DivisionLeaderboardSnapshot` consumption so the player row exposes
   `in_placement` + `games_played` (the vendored model lacks these fields; either
   carry them in `result_metadata` or propose adding them to the vendored
   `DivisionLeaderboardSnapshot`, paralleling the PR's `PolicyMmrEntryPublic`).
   Mirror the PR's `web/softmax.com` MMR-board columns (rank · policy · player ·
   MMR · W/L · games) — but note Crewrift Prime's board is **player-keyed**, so
   the column set is the player-collapsed view.
6. Keep the **qualifier gate** exactly as-is; "placement" here is the *Competition*
   rated-but-unranked window, distinct from Qualifiers→Competition admission.

### Phase 3 — Finer + role-aware rating

7. **R2:** compute the rating in `_complete_competition_round` (per-episode signal
   available there) and persist `(mu,sigma)` deltas into `state`, so `rank_division`
   reads accumulated ratings instead of re-deriving from round ranks. More updates
   → better placement convergence.
8. **R3:** split imposter/crew sub-ratings (role disaggregation) and combine.
9. **(PR follow-up) matchmaking:** once ratings are established, bias
   `_schedule_competition_round` seat assignment away from rating-mismatched games
   (the PR explicitly defers this; it's a *scheduling* change, not a ranking one).

---

## 6. Risks / open questions

- **Rating unit mismatch.** The PR rates policy versions; Crewrift's Competition
  board is player-keyed. The policy→player collapse (best out-of-placement policy)
  is a *taste* decision — alternative is "most-recent policy" or "rating-weighted
  blend". Recommend best-policy for legibility; revisit if it punishes players who
  iterate often.
- **Round-as-match coarseness (R1).** One round = one match discards within-round
  episode variance. Given Crewrift's **high variance / 8 seats / 40–80-game floor**
  (`crewrift-eval-design`), R1 ratings converge slowly; the 5-game placement gate
  is measured in *rounds*, which may be many episodes. Consider lowering the
  Competition placement-min or moving to R2 sooner.
- **`−100` lobby taint.** Disconnect/no-show taints a whole lobby `−100`
  (`crewrift-optimization` taint rule). A tainted round must be **excluded** from
  the rating replay (don't let an infra `−100` crush `mu`), exactly as the
  optimizer excludes tainted episodes. Filter before feeding `model.rate`.
- **Cumulative-wins → MMR is a visible behavior change.** The current leaderboard
  is a "running win total" (monotonic, easy to read); MMR can go **down**. Decide
  whether to (a) replace, (b) show both columns, or (c) keep wins as the W/L column
  beside MMR (the PR does the latter). Recommend (c) — matches the PR UI.
- **Determinism / auditability.** OpenSkill replay must be deterministic given the
  same round history. Keep `mmr.py` pure (like `decision.py`) and unit-test the
  PR's scenarios (consistent-winner ordering, placement gating, best-per-round
  dedup, player-prior inheritance, empty board) against the Crewrift round shape.
- **Vendored-package drift.** If/when the upstream `commissioners` package adopts
  PR #16527's ranker in its *stock* `rank_division`, re-vendoring would change the
  **non-Competition** fallback path; the Competition override stays ours. Track the
  PR's merge so the vendored base and our override don't diverge in surprising ways.
- **Schema authority.** Adding `in_placement`/`games_played`/per-policy MMR to the
  player-keyed `DivisionLeaderboardSnapshot` touches the **vendored** model. Prefer
  carrying them in `result_metadata` first (no model edit); only propose a vendored
  model field if the UI needs first-class typing (parallels the PR's
  `PolicyMmrEntryPublic`).

---

## 7. Minimal first PR (recommended)

A single self-contained commissioner change, no platform/upstream dependency:

1. `openskill>=6.0.0` into the commissioner image deps.
2. `commissioner/mmr.py` — pure `rank_competition_by_mmr(completed_rounds,
   round_results)` mirroring the PR algorithm (PlackettLuce, oldest-first replay,
   per-round rank as the match order, `mu−3σ`, player-prior, 5-game placement),
   per **policy version**.
3. `rank_division` Competition branch → call it, collapse to player by best
   out-of-placement policy, keep emitting `DivisionLeaderboardSnapshot` (W/L from
   OpenSkill wins/losses, `score = MMR`, placement in `result_metadata`).
4. `COMMISSIONER_DECISION {"decision":"MMR_RANK",...}` logs + `state` audit blob.
5. Unit tests porting the PR's `test_leaderboards.py` cases onto the Crewrift
   round shape (`test_mmr.py`), run like the existing `test_skill_gate_metrics`.

This delivers the user's ask — OpenSkill **commissioner rankings** (Competition
ordinal MMR) and **commissioner placements** (rated-but-unranked gate) — entirely
within the Crewrift Prime commissioner, faithful to PR #16527, shippable via the
existing `coworld patch-commissioner` flow.
