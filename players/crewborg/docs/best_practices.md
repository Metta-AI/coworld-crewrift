# Best practices — optimizing crewborg

Battle-tested disciplines for the **evaluate → diagnose → improve** loop, distilled from many
optimization campaigns. **Part 1** is game-agnostic (true of any Coworld player); **Part 2** is
Crewrift-specific (the failure modes of *this* game). Treat them as your defaults, and **warn the
human if a request would contravene one** before proceeding (then do what they decide).

> New here? Read these once, then keep them as a reference. The 🚩-marked items are the ones that
> get violated most — re-read those before you write a "why" or interpret a result.

---

## The non-negotiables (the ones that actually bite)

1. **🚩 No causal claim without the falsifying query.** Every "because / since / due to" in your
   own draft is an un-run query, not a conclusion — and it's wrong about half the time, often
   *backwards*. Before writing a "why": (a) is the effect even real (effect size + a significance
   test)? (b) name what your mechanism *and the competing one* would make observable; (c) run the
   query that separates them and report it, refutations included.
2. **🚩 Decompose by role before judging.** Crewmate and imposter are two different policies; the
   aggregate headline routinely hides one role being broken.
3. **🚩 Meeting ticks are NOT idle time.** A report or button starts a ~1272-tick meeting that
   teleports everyone home (a body/unknown meeting — **not** the emergency button — resets imposter
   kill cooldowns) — exclude it from every idle/latency/ready/gap metric (see Part 2).
4. **🚩 `-100` means DISCONNECT/CRASH, not ejection.** It's an ops failure; filter it out before
   any rate. Getting voted out carries *no* score signal at all — you must read the logs for it.
5. **Upload freely; submit rarely.** Uploading a version is routine and touches no league. Submitting
   is the irreversible, champion-making action — only on a demonstrably-better player + human go-ahead.
   *Not* submitting is your rollback.

## Working with the human

- **If you're re-instructed to do the same thing more than once, record it as a preference.** A
  standing instruction — or a correction the human keeps making — belongs in
  [`user_preferences.md`](user_preferences.md); write it there so they never have to say it a third
  time and it persists across sessions. Don't make the human be your memory.

---

# Part 1 — General (game-agnostic)

## Measurement — know whether a change actually helped

- **Evaluate on a batch, never a single game.** Within-game variance (std can exceed the mean),
  role/seat asymmetry, and opponent dependence each swamp one game.
- **Decompose before judging; the aggregate headline is a trap.** Cut by **role** (the most
  important cut), by **opponent/matchup** (pairwise), and by **behavioral sub-metrics** (wins, mean
  *and* median, action counters). Their disagreement localizes *why*.
- **Apply statistical rigor.** Report effect sizes (not just means), run a mean-based *and* a
  rank-based test, apply multiple-comparison correction, and pool matched batches for power. A
  leaderboard that looks cleanly ranked is mostly noise until corrected.
- **🚩 No causal claim without the falsifying query** (see the non-negotiables). This is the
  single most-violated discipline. Observable preconditions exist for *every* mechanism, and the
  query to check them is almost always one cheap join away — it repeatedly overturns the story.
  Watch your own language: borrowed metaphors ("snowball", "momentum") smuggle in a model the game
  doesn't have.
- **Normalize every stat by seat-holding.** When a policy occupies a different number of roster
  seats than others, report **per-seat-game rates, never raw totals** (4 of 8 seats ⇒ ~4× the
  totals for the same skill). Two traps: (1) counting non-events as events (an abstain logged as
  "chat") inflates volume — exclude first; (2) **team-outcome metrics carry a composition confound
  that per-seat normalization does NOT remove** — isolating individual contribution to a team
  result needs a controlled design (vary one seat, hold the rest fixed). Individual stats
  (kills, tasks) are clean per-seat; team stats (win) are not.
- **Experience requests are your primary eval — they aren't scarce.** They run many episodes in
  parallel on Softmax infra and are currently free; use them liberally, just **target them to the
  question** (matched roles, the specific opponents you struggle against) and harvest async.
- **Local testing is smoke/correctness only — never comparative.** You generally can't run other
  users' policies locally, so local play proves only that your artifact runs, speaks the protocol,
  and that your change took. All competitive judgment comes from experience requests.

## Diagnosis — from "it lost" to "it does X in situation Y because Z"

- **You can't debug an outcome, only a trace.** Pivot immediately from the result to the player's
  internal reasoning stream (see [`../crewborg/docs/trace-logs.md`](../crewborg/docs/trace-logs.md)).
- **Observability is something you build, not something you're given** — reason traces (mode /
  options / choice + a *why*), belief snapshots, tick-keyed lines, tiered verbosity, replays. If you
  can't see the behavior you can't improve it; building the instrument often precedes the fix.
- **Triage by failure class; chase the surprise.** Aggregate, then sample the worst case per class.
  The most informative game is the one that "should have been a win."
- **Variance carries the mechanism — and the lucky wins are findings too.** Look at *which* episodes
  moved and what they share; a change that helps one cluster and hurts another points at the gating
  fix, and positive outliers are a hypothesis source — find the mechanism and make it fire on purpose.
- **Don't optimize the obvious intermediate metric.** Confirm it maps to the objective first;
  counterintuitive correlations (dying *more* while scoring *more*) are signal, not noise.
- **Name the layer first** — perception / belief / strategy / execution — because the layer
  determines where the fix goes. Keep **operations** failures (can't connect/build) strictly
  separate from **behavior** failures (plays badly).
- **Ground truth beats inference.** When the player's view or the tooling could be lying, verify
  against the game's authoritative source/logs before building on it.

## Hypothesis discipline — make a diagnosis actionable

- **Name a specific mechanism and predict an observable effect.** Pin it to a rule/timer/threshold
  and the trace line that proves it; propose a scoped change to *that* mechanism only.
- **Plausibility is not evidence.** "This should obviously help" is a reason to *test*, never to
  assert or ship — roughly half of "obviously good" ideas regress.
- **Pre-register the expected effect**, ideally as a test written *before* the run — the test is the
  hypothesis made falsifiable.
- **"Capability exists" ≠ "capability is used."** A signal the policy never consults is a silent
  no-op; verify it's consumed, not just emitted.
- **Validate from the trace, not the scoreboard.** A win can be noise; confirm the intended
  mechanism actually fired.

## Provenance — never trust an unverified green result

- **Change one component at a time** so the next evaluation is attributable.
- **Rebuild after every change** — a stale artifact reads as "the change did nothing."
- **Upload freely; submit rarely** (see the non-negotiables). *Not* submitting is your rollback.
- **Keep a version log** (`../crewborg/version_log.md`) mapping each uploaded version to the changes
  it carries, so you always know what each version is testing.
- **Use explicit positive/negative controls** — a silent fallback can run a reference player, not
  yours; a verified A/B beats a source review.
- Stay alert to **local↔live drift**, **stale rotating IDs / docs**, **over-reading a small batch**,
  and **position-based score joins** — the classic looked-like-success failures.

---

# Part 2 — Crewrift-specific

These layer on Part 1; they're the failure modes of *this* game. Add to this part as we learn more.

## Scoring (read before interpreting results)

- **🚩 `-100` means DISCONNECT/CRASH only — NOT ejection.** It's an ops failure (the container
  disconnected or crashed). Filter these out before computing any rate. Do **not** read `-100` as
  "got voted out."
- **Getting EJECTED (voted out) carries NO points penalty — there is no score signal for it at
  all.** A loss after ejection looks identical in `results.json` to any other loss, so you
  **cannot** infer the ejection rate from scores. To know whether/when crewborg was ejected you
  **must read the logs/replay** (the meeting outcome / `player_died`-by-vote / `expand_replay`
  ejected-by-vote). Always check the logs, not the score, for ejection.
- **Cleanest ejection signal for an IMPOSTER: it ended dead.** An imposter cannot be killed (only
  crew can), so an imposter dead at game end was **necessarily ejected by vote** — so *imposter
  ejection rate = fraction of imposter games the policy ended dead*. (Crew deaths are ambiguous —
  killed vs ejected — and need the vote events.)
- **🚩 Meeting/voting ticks are NOT idle time — exclude them from EVERY idle / latency / ready / gap
  metric.** A report or button press starts a meeting: `MeetingCallTicks` (72) + `VoteTimerTicks`
  (1200) ≈ ~1272 ticks during which nobody moves or kills, everyone is teleported home, and a
  body/unknown meeting **resets imposter kill cooldowns**. **Two layers, both required:** (1) filter
  `phase == 'Playing'` to drop meeting *samples*; (2) **never subtract a raw tick delta across a
  Playing-filtered series** — the delta between two consecutive Playing samples still spans any
  meeting in between (~1272 ticks). Instead **count Playing+ready samples × snapshot interval**, or
  bound the window at the next meeting. A ready→kill window **ends at the next body/unknown meeting**
  (which resets the cooldown — the emergency **button** no longer does; `buttonResetsKillCooldowns=false`,
  see [`reference/crewrift-gameplay.md`](reference/crewrift-gameplay.md)). This has bitten the analysis
  repeatedly ("~2000-tick inter-kill gap" and "2077-tick wander" were both *meetings*, not hunting).

## Evaluation

- **Crewmate and imposter are two different policies — never judge them merged.** The same code in
  the two roles has different objectives, different action sets (kill/vent only exist for imposters),
  and different score structures. An aggregate win-rate routinely hides one role being broken.
  **Always decompose eval by role**, and target experience requests at matched roles when a change
  was role-specific. Force your policy's role by pinning its roster `slot` +
  `game_config_overrides.slots` (an array of `{"role": …}` objects, not bare strings — the common
  mistake; see [`reference/crewrift-gameplay.md`](reference/crewrift-gameplay.md)).

## Reading games (replays & logs)

- **Investigate the game, don't infer from the scoreboard** — pivot from the result to what
  *happened* (the objective timeline) and *why* (the policy's logs).
- **Go batch-first, then drill.** Start with the distribution across the whole batch; open
  individual episodes only once it flags the interesting ones (the should-have-been-wins). Match the
  tool to the altitude — each tool's own docs cover how to run it (see the tool library):
  - *Triage a batch* → the **`crewrift-survey`** skill (role-decomposed stats; flags interesting episodes).
  - *Cross-episode behavioral data ("all the data")* → a **`crewrift-event-warehouse`** (queryable event store, re-keyed by policy/role).
  - *One game's objective ground truth* → **`expand_replay`** (the single-game primitive the others build on).
- **Policy logs are version-independent — the primary source for hosted/league episodes** (no replay
  or version match needed; crewborg writes a rich per-tick JSON trace).
- **League episodes are a disjoint population — `coworld episodes -p crewborg` returns `[]`.**
  Discover them via the policy-versions → episodes path (the `coworld-episode-artifacts` skill);
  never read `[]` as "no episodes."
- **Confirm what's in a log before querying it** (crewborg's trace level varies — an empty `select`
  can mean "wrong level," not "didn't happen"), and **identify a slot by name *and* version** (a
  league episode can carry several crewborg versions — map slot→policy from `episode.json`, not list
  position).

## Perception & the scene contract

- **The game owns the scene vocabulary; re-derive from source when in doubt.** The Sprite-v1
  object-id ranges, labels, and camera offsets (in [`reference/crewrift-protocol.md`](reference/crewrift-protocol.md) and
  `crewborg/perception/constants.py`) are verified against `Metta-AI/coworld-crewrift`:
  `src/crewrift/{sim,global}.nim`, but they are the **game's to change**. If perception misbehaves
  after a game bump, suspect drift and check the Nim source before trusting the decoder (see
  [`../crewborg/docs/perception-and-belief.md`](../crewborg/docs/perception-and-belief.md)).

## Idling is dangerous — every idle needs an escape

- **Standing still is almost always the wrong move**, and it is where crewborg's worst bugs hide. Every
  multi-thousand-tick freeze we've found was a disguised idle with **no way out**: a WATCH parked at a
  vantage, a `pick_room` "no task rooms" dead-end, and a RECON that `navigate_to`s a stale last-known
  crew position it has already reached (navigate-onto-self ⇒ velocity 0 for thousands of ticks).
- **Rule: any mode that can emit `idle` MUST have a clear escape** — a fallback action, a timeout, or a
  transition that guarantees motion resumes. A mode that can return `idle` (or `navigate_to` its own
  current / an unreachable point) with no guaranteed exit is a latent freeze.
- Idle is legitimate only for a **narrow, deliberate** purpose (a genuine multi-crew vantage stakeout)
  and the unavoidable startup no-op (no camera/map yet). Everything else should move toward crew /
  re-search instead. Concretely: RECON with no live target → fall back to SEARCH (never idle); PICK_ROOM
  → always pick a room. When auditing the FSM, check every `idle` **and** every `navigate_to` that could
  resolve to the agent's current position.
