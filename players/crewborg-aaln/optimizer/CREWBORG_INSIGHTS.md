# Crewborg Tournament Insights

Policy-specific, hard-won knowledge for optimizing the **crewborg-aaln** Crewrift
policy. This complements the generic optimizer skills (`optimizer/skills/`) and
the game playbook (`optimizer/games/crewrift/skills/crewrift-optimization/`) —
those cover *how to run the loop*; this file covers *what crewborg already knows*
about the opponents, its own tunable surface, and the eval traps it has hit.

**Provenance.** Distilled from the source repo
`softmax/players/players/crewrift/crewborg/` (vendored as
`players/crewrift/crewborg/` here). Cited files:
- `design.md` — architecture, modes, strategy selectors, §12 tuning table.
- `docs/designs/suspicion.md` — the Bayesian P(imposter) model, §3.5 social cues,
  §6 offline LR-fitting loop, §7 provenance log (the measured opponent tells).
- `docs/designs/agent-tracking.md` — probabilistic occupancy / imposter search.
- `README.md` / `AGENTS.md` — capabilities, env flags, protocol facts.
- `skills/optimization/*` — the optimizer-loop grounding (refs FINDINGS_v4 +
  the v3_vs_v8 eval, not vendored here because `episode_data/` is gitignored).

> Crewrift is in active development; verify any constant against the cited source
> before relying on it. The numbers below are crewborg's *current defaults and
> measured findings*, not immutable game rules.

---

## 1. The scoring objective drives everything

From `AGENTS.md` §2 (verified game constants) and `design.md` §2:

| Reward | Value | Implication for the optimizer |
|---|---|---|
| win (each winner) | **+100** | Wins dominate. Optimize win rate, not activity. |
| kill | **+10** | Imposter aggression is worth ~10 tasks. |
| task complete | +1 | Cheap; a "task throughput" win rarely moves league score. |
| vote timeout (per non-voter) | **−10** | **Never miss a vote.** A missed vote is −10 *and* −1 effective for the crew. |
| stuck (idle crewmate) | −1/interval | A wedged crewmate bleeds points silently. |

**The points asymmetry (state it before choosing an eval field).** +10/kill and
+100/win swamp +1/task, so a "win the league" goal weights **imposter performance
and wins**, not task counts. Beating a *weak, frequent* opponent can yield more
total points than beating the *single strongest, rare* one — decide which the
goal rewards before locking the opponent field (`skills/optimization/eval-set-design`).

**Two structural penalty bugs masquerade as bad strategy** (`crewrift-eval-design`):
a consistently negative mean is almost always (a) missed meeting votes (−10 each —
check the vote/cursor path) or (b) stuck-idle crewmates (−1 — check pathing/wedge),
**not** strategy variance. Diagnose the penalty before interpreting strategy.

---

## 2. Known opponent strategies & measured tells

The single most valuable hard-won asset. From `suspicion.md` §3.5 + §7 provenance
(measured against **truecrew:v14** over **88 games**, 2026-06-11):

- **The "plain-sus" disinfo tell.** Bare `"<color> sus"` chat lines with *no
  evidence wording* were **0/185 accurate** at naming a real imposter. The format
  itself is a signal:
  - the **named** color is likely *framed crew* → crewborg applies `log-LR = −ln 3`
    to the target (`PLAIN_SUS_TARGET_LOG_LR`), suppressed if any evidence-backed
    accusation also names them.
  - the **speaker** is likely steering the meeting (imposters frame crew this way;
    **11/16 ejections followed a bare-sus line**) → `log-LR = +ln 2`
    (`PLAIN_SUS_SPEAKER_LOG_LR`).
  - Crowd accusations now count **evidence-backed lines only** (body/vent/saw/
    follow/kill/report keywords); a bare-sus chorus is disinfo, not corroboration.
- **Magnitudes kept conservative** because the measurement was against *one*
  opponent engine. If you re-eval against a different field, re-measure these (the
  fitting loop is `suspicion.md` §6) and log it in the provenance table.
- **Button-call chatter as a kill-priority signal** (`design.md` §10, future
  refinement): current opponents tend to chat after pressing the emergency button
  (e.g. *"just resetting imposter cooldowns"*), a noisy-but-actionable tell that
  their one `ButtonCalls` charge is spent. A smarter imposter Search/Hunt would
  *deprioritize* colors that can no longer stop the next kill cooldown and keep
  pressure on crew who still can. **Not yet implemented — a concrete hypothesis.**

**Opponents to expect on the board** (named in the source skill grounding):
`truecrew:v14` (the head-to-head reference threat), plus the internal version
chain `crewborg v3 / v6 / v8` used as A/B baselines. Pull the live leaderboard
(`coworld memberships`/`leagues`) each run — the field drifts.

---

## 3. What scored well vs poorly (eval lessons)

From `FINDINGS_v4.md` + the `eval_2026-06-11_v3_vs_v8` eval, as cited across the
optimizer skills. **These are the traps; internalize them before trusting a number.**

- **The false-win trap (v4).** The v4 package looked fine on the **top-ranked
  field** (42% vs 34%) but **regressed head-to-head** (24% vs 39%). Only
  **per-matchup AND per-role** aggregation surfaced it. A change that lifts the
  aggregate by helping crewmates while quietly tanking imposters is a false win.
  → Always report imposter win-rate and crewmate win-rate **separately**, per
  opponent field (`skills/optimization/eval-aggregation` Step 3).
- **The −100 disconnect taint.** A disconnect / no-show scores the **whole lobby
  −100** — usually a cold-node image-pull timeout, *not* gameplay. Including those
  craters the mean and fabricates a regression. Filter any episode with a slot at
  −100 or an artifact showing 0 ticks **first**, and **report the disconnect rate**
  (historical background ≈5–12%; a spike is a deploy bug, not a policy result).
- **Concurrency causes fake failures.** Large concurrent XP-request batches
  overload shared Bedrock quota and produce −100 timeouts that look like policy
  failures. Submit **small (1–3 episode) sequential** requests and aggregate them
  into one logical eval set (`crewrift-eval-design`).
- **The ecosystem-interaction regression (v4 kill tempo).** A change can help in
  isolation and *regress in the field* through tempo/ecosystem coupling. Predict
  the **second-order** effect (how the change shifts meeting/kill tempo for
  everyone), not just the local one (`skills/optimization/hypothesis-generation`).
- **N discipline.** Crewrift is high-variance and role-asymmetric. <40 completed
  games is a liveness check only — a "decisive" 3-game result is variance or a
  structural bug. Gate decisions want ~40–80 (or ~100/matchup) clean games, often
  on **two** matchups (named threat + broad field).

---

## 4. Variant flags & their tradeoffs

crewborg ships several gated experiments (`design.md` §10/§12, `README.md`). They
are the cheapest things to A/B because they're env-flag toggles, no code change.
**Check which is live in the shipped image first** — a hypothesis about an inert
param shows "no effect" for the wrong reason.

| Flag | Effect | Tradeoff / when to test |
|---|---|---|
| `CREWBORG_BE_DUMB=1` | Imposter runs **only Search/Hunt** — skips Pretend, Evade, and body reports; always preparing to kill. | Isolates "does always-hunting beat blend-in?" Under this flag, `SEARCH_LEAD_TICKS`/`EVADE_TICKS` are **inert** — don't form hypotheses about them here. |
| `CREWBORG_DICK_MODE=1` | Crewmate one-shot: rush the emergency button before the **first** kill cooldown clears, taunt + skip-vote, then resume. | Disrupts imposter timing once/game. `ButtonCalls=1`, so it never re-arms. Worst-case button-walk budget is hardcoded (`DICK_MAX_BUTTON_TRAVEL_TICKS=600`). |
| `CREWBORG_LLM_MEETINGS=1` (+ key) / `USE_BEDROCK=1` | Replace deterministic meeting chat/vote with a Haiku-class LLM call on the meeting fast path. | **Latency risk:** a 3s per-call timeout; on permanent error (401/403/404) or 2 failures it latches to the deterministic fallback for the episode so a broken backend never costs the vote. Bedrock concurrency contends for shared quota → −100s. The deterministic fallback is the Bayesian top-suspect vote. |

**Imposter target priority (future, §2 above):** deprioritize spent-button crew.
**Possible refinements** worth hypotheses (`design.md` §7.4): travelling-salesman
task ordering, safety-in-numbers task routing, strategic flee targets, richer
imposter coordination (shared claims beyond the current local teammate-pressure).

---

## 5. The tunable surface (the knobs, with current defaults)

From `design.md` §12. Prefer hypotheses that map to **one** knob — cheap to test,
cheap to revert. Pin every build to a policy-version id and record the knob value
in the findings doc.

**Suspicion / voting** (the prime social-deduction lever — see §6 below):
- `FLEE_PROBABILITY = 0.9` — flee a player at/above this P(imposter).
- Vote bar is **state-dependent** (`vote_policy.vote_bar`): 0.75 at margin ≥4, 0.8
  at margin 3, **0.9** at margin ≤2, and **0 in a must-eject endgame** (a skip
  loses to the next kill). Anti-split swap onto the plurality within
  `ANTI_SPLIT_REMAINING_TICKS = 96` if plausibly guilty (`≥ 0.3`).
- Always cast *something* before the timer (−10 otherwise): auto-submit at ≤72
  ticks left, plus an action-layer last-resort cursor confirm.

**Imposter kill/search:**
- `BASE_ISOLATION_RADIUS = 48 px`, `WITNESS_WINDOW_TICKS = 72` — relaxed to **zero**
  by urgency `URGENCY_FULL_TICKS = 240` (a perpetually-shadowed kill eventually fires).
- `SEARCH_LEAD_TICKS = 100` — enter Search this far before kill-ready.
- `KillCooldownTicks` default **500** (GameInfo-advertised; the old 900/240 are
  stale — crewborg learns live values from the `GAME INFO` interstitial, §7).
- `MAX_LEAD_TICKS = 24` trajectory lead; `TEAMMATE_CLAIM_RADIUS = 80 px`.

**Pretend (imposter blend-in):**
- Fake-task hold = `TASK_TICKS = 72`. Room score = expected crew density −
  teammate pressure (`TEAMMATE_ROOM_PENALTY = 3.0`). Starting room never anchors a
  fake task.

**Movement:** bang-bang + predictive stop, `CLEARANCE_RADIUS = 2 px`,
`REPLAN_INTERVAL = 8` ticks (route re-roots at live position; A* ≈0.2ms).

---

## 6. Suspicion is the prime crewmate knob — and it's *fittable*

`suspicion.py` maintains `belief.suspicion[color] = P(imposter)` via log-odds
Bayes: `logit(P) = logit(prior) + Σ logLR(evidence)` (`suspicion.md` §2). This is
the load-bearing crewmate-side surface and the offline LR-fitting loop
(`suspicion.md` §6) is a **worked instance of the whole optimizer loop** —
pre-registered weight claims, validated by re-fitting from replays, every change
in a provenance table.

- **Prior** redistributes the imposter budget as players are confirmed/die — so
  catching one of K=2 roughly halves everyone else's prior.
- **Near-certain** (LR ≈ 1e6): witnessed kill / witnessed vent (frame-transition
  detectors on the perception tape, gated by the `shadow` line-of-sight mask so
  occlusion can't fake a "clear").
- **Graded functions** (the fittable shapes): `vent_dwell` (weak, flat past a
  3-tick crossing); `body_proximity` **decreases** with dwell (a skilled imposter
  flees; a long camp = reporter = neutral); `follow_to_death` **increases** with
  dwell.
- **Social cues** = the measured opponent tells in §2.

**To improve it:** run the §6 fitting procedure (re-run the event-log + tape
detectors from an *observer's* POV on labelled replays, bin by feature, estimate
the per-bin likelihood ratio, fit a simple closed form, update §3.3 + the §7
provenance log + `suspicion.py`). The suspicion tests assert *relational*
properties so they survive re-tuning. **This is the highest-leverage place to
turn replays into a measured policy improvement.**

---

## 7. Protocol facts that matter for instrumentation

(`AGENTS.md` §2 contract-delta, `design.md` §11 — these affect what you can log
and join.)

- **`server_tick` join key.** The game emits an invisible `tick <N>` sprite
  (object/sprite id 5016) every frame; crewborg records it in
  `positions.server_tick`. It is **identical** to the `.bitreplay` timeline tick —
  *the* reason artifact↔replay joins are mechanical.
- **GameInfo interstitial** advertises the **live episode config** (`KILL COOLDOWN
  <N>T`, `TASKS <N> EACH`, `VOTE TIMER <N>T`, `GAME TIMER <N>T`). Crewborg learns
  these instead of trusting baked defaults — so don't hardcode cooldown numbers.
- **MeetingCall interstitial** exposes **who opened the meeting and how** (report
  vs button vs called) — folded into `meeting_called_by` / `meeting_trigger` and
  emitted as `domain.meeting_called`. Useful for who-pressed-the-button analysis.
- **Game clock pauses in meetings** (only `Playing` ticks count); kill/vent
  cooldowns also only decrement during Playing, and **every meeting resets imposter
  kill cooldowns**.
- **Time-based seed ⇒ NO color→role correlation.** Never bake color→role priors.
  Read the per-episode role census from `domain.game_over` (+ GameOver roster
  icons) — the cheap global truth for labelling every slot's role.

---

## 8. Tooling (the optimization loop's scripts)

All under `players/crewrift/crewborg/scripts/` (already vendored in this repo).
Run from the `players` workspace root with `uv run`.

| Script | What it does for the loop |
|---|---|
| `fetch_episodes.py` / `fetch_episodes.sh` | **Bulk-pull** the N most recent hosted episodes crewborg played: per episode dir with `replay.json` (`.bitreplay`), `episode_request.json` (roster: slot→policy/version+scores), and `logs/crewborg_slot{N}_v{V}.log`. Reads **raw JSON against current routes**, so it survives the client/server drift that 404-breaks the typed `coworld episodes`/`replays` CLI. Auth via `softmax login`. |
| `replay_analysis.py` | Re-simulate one episode's `.bitreplay` (shells to `tools/expand_replay.nim --json` in a local `coworld-crewrift` checkout — there is **no Python decoder**), join to the roster, and emit a JSON report: per-slot kills/tasks/votes + a `crewborg_opponent_correlation` matrix. With `--trace-db --slot N`, joins crewborg's per-tick mode/intent to replay events on `server_tick`. |
| `build_eval_dashboard.py` | Aggregate an eval set's `analysis_episodes.json` into a **standalone HTML dashboard** (Chart.js via CDN, data inlined): per-policy strengths, head-to-head threat matrix, field-level distributions. Each focus-slot record reconstructs *every* policy's per-episode result (positions, roles_by_color, alive_by_color, outcome, scores). Opens with a double-click — no server. |
| `replay_dump/` (`replay_dump.nim` + `run_replay_dump.sh`) | Lower-level Nim replay expansion utility — the building block under `replay_analysis.py`. |
| `play_local.sh` | Run crewborg against a local Crewrift dev server for single-game iteration. |

**One-time replay setup** (`docs/replay-analysis.md`): `coworld` CLI ≥0.1.22, Nim
**2.2.10** (`nimby use 2.2.10 && nimby sync`), `export
CREWRIFT_ROOT=<coworld-crewrift checkout>` and `PATH="$HOME/.nimby/nim/bin:$PATH"`.

**Watching a replay visually:** `coworld replay` is **broken for Crewrift** (shows
a live "waiting for players" game). Launch the game image directly with
`COGAME_LOAD_REPLAY_URI=file:///coworld-replay/replay.json` and open the
**singular** `/client/replay` (the CLI prints a dead plural URL). Full
source-verified recipe: `docs/crewrift-replays.md`.

**Hosted benchmark workflow** (`docs/experience-request-benchmark-analysis.md`):
to benchmark a policy version against the live tournament field via an
Observatory experience-request — resolve league/division/leaderboard, join
leaderboard rows to active memberships (prefer `champion` substatus, else newest),
inspect the OpenAPI schema (it drifts), POST with `requester` + `opponents` (not
`policy_version_ids`, which is for caller-owned rosters), poll
status/completed/failed separately, then summarize per-policy scores + outlier
replay URLs. Don't report "done" just because the request was created.

---

## 9. Where the deeper detail lives

| Need | Source file (vendored at `players/crewrift/crewborg/`) |
|---|---|
| Full architecture, modes, strategy selectors, tuning table | `design.md` |
| The suspicion model + fittable LR functions + measured tells + fitting loop | `docs/designs/suspicion.md` |
| Imposter occupancy/search belief (reachability disc) | `docs/designs/agent-tracking.md` |
| Protocol/contract facts, env flags, constants | `AGENTS.md`, `README.md` |
| Replay reconstruction & viewing recipes | `docs/replay-analysis.md`, `docs/crewrift-replays.md` |
| The optimizer loop grounded in this policy | `skills/optimization/*` (and the namespaced skill beside this file) |
