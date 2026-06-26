# Crewrift Prime — advanced-skill commissioner

A custom Coworld commissioner for the **Crewrift Prime** league that replaces the
stock score-only Qualifiers→Competition gate with an **event-driven, replay-evaluated
three-skill gate**, plus first-class **decision observability** (per-skill scores,
verdicts, and a human-readable reason for every promotion decision).

## Why a custom image

The stock config-driven `ruleset_strategy` commissioner's transition vocabulary
(`TransitionCriteriaConfig`, `extra="forbid"`) only allows `completed_episodes_*`
/ `score_*`, discarding every other field of the per-slot `results_schema`. Gating
on advanced skills requires reading the game's results ourselves. We go further:
this image owns the **xp-request client** (`xp_request_client.py`) and the
**replay parser** (`replay_parser.py`) so the whole "submit → run an experience
request → evaluate the replay → promote" loop lives in the commissioner. The
Competition division's win-count scheduling/scoring/ranking is reused.

## Qualification — event-driven, replay-evaluated ("one game and we're in")

There is **no Qualifiers staging division**. When a new policy is submitted, the
commissioner runs the qualification loop itself (`migrate_league` →
`qualify_submission`):

1. **Create + poll** a one-game self-play *experience request* for the policy
   (`POST /v2/experience-requests`, then poll `GET .../{xreq}` / `.../episodes`).
2. **Download + parse** the completed episode's `.bitreplay`. A Crewrift
   `.bitreplay` is only per-tick input masks, so deriving metrics requires
   **re-simulating** it: `replay_parser.py` runs the repo's Nim expander
   (`tools/expand_replay.nim --format jsonl`, overridable via
   `CREWRIFT_PRIME_EXPAND_REPLAY_CMD`) to produce the structured `{ts, player,
   key, value}` event log, then folds it into a per-slot `results_schema` dict.
3. **Evaluate** the strict three-skill AND gate (`decision.evaluate_combined_game`)
   over that one self-play game, with the voting check augmented by an **LLM content
   grade** of the meeting speech (the commissioner calls `chat_grader` and feeds the
   boolean into the pure decision). Self-play fills all 8 seats with the entrant, so
   a single game exercises both roles:

| Skill | Metric | Threshold (default, env-overridable) | Computed from the one game |
|---|---|---|---|
| voting | `meeting_participation` | pass if it votes & talks genuinely / no-meeting (`CREWRIFT_PRIME_MEETING_PARTICIPATION_MIN=0.0`) | meeting-aware participation + talk gate: pass if the entrant cast a vote/skip (or spoke) when a meeting occurred AND, when content grading is on, an LLM judged its meeting speech genuine; no penalty if no meeting occurred; fail if a meeting happened yet it never voted/talked, or it talked only gibberish/canned text. |
| hunting | `imposter_kills` | `>= 0.5` (`CREWRIFT_PRIME_HUNT_KILLS_MIN`) → ≥1 kill | total `kills` landed by the imposter seat(s) (`imposter`==1) in the game |
| tasks | `crew_tasks_mean` | `>= 1.0` (`CREWRIFT_PRIME_TASK_TASKS_MIN`) | mean `tasks` across the crew seats (`crew`==1) in the game |

4. **Promote** (→ Competition, `status=competing` / `substatus=champion`) iff ALL
   three pass. Otherwise the submission **does not qualify**: it is held
   `status=qualifying` / `substatus=skill_gate` (in place — there is no qualifier
   division to hold in) and re-evaluated on its next submission.

**Crash / infra safety** (no separate crash_check stage):
- a completed, parseable replay with results is, by definition, not a crash → evaluate the 3 skills;
- a terminal run with **no completed game** (no results, not infra) → **DQ** ("Failed to complete the qualifier game");
- an **xp-request infra failure** (HTTP 4xx/5xx, run never completes within the budget) or a **replay-expansion failure** (Nim expander unavailable/errors) → **hold & retry**, never DQ.

> **Submission seam.** The stock platform↔commissioner protocol carries no
> per-submission message, so the commissioner reacts on `migrate_league` — the only
> entrypoint that sees every membership with its status and returns membership
> changes. The platform must invoke `migrate_league` (or an equivalent submission
> hook) when a policy is submitted for qualification to fire promptly. See the
> repo-root `crewrift-prime/README.md` "Qualifier" section.

## Competition division — score = WINNING PLAYERS (per round), ranked by OpenSkill MMR

Once promoted, a policy competes in the **Competition** division. Each round's
**score** counts **every winning player (seat)**: **1 point for each seat that won
as imposter, plus 1 point for each seat that won as crew** (score =
`imposter_wins + crew_wins`). A seat scores if its per-slot `game_results.win` is
True; the role of that winning seat (imposter vs crew) comes from the per-slot
`imposter`/`crew` arrays. An entrant occupying several winning seats in one game
scores once **per winning seat** (not once per winning episode). That per-round
score/finishing-rank is the *match outcome* fed to the rating — it is no longer
summed directly into the leaderboard.

- `_complete_competition_round` (subclass override) sets each entrant's per-round
  score = winning players that round, with `imposter_wins`/`crew_wins` in
  `result_metadata` and a `competition_wins` breakdown in `round_display`. A
  `COMMISSIONER_DECISION {"decision":"COMPETITION_WINS", ...}` line is logged per
  entrant.
- `rank_division` (subclass override) ranks the Competition leaderboard by a
  **per-policy OpenSkill (Plackett–Luce) MMR** (see `mmr.py`), faithful to upstream
  PR [Metta-AI/metta#16527](https://github.com/Metta-AI/metta/pull/16527). It
  replays the division's completed rounds **oldest-first**; each round is one
  Plackett–Luce match decided by the finishing rank above. The displayed MMR is the
  conservative ordinal **`mu − 3σ`**; a player's leaderboard row is their **best
  out-of-placement policy version** (falling back to their best in-placement policy
  when none has cleared placement). Other divisions keep the stock ranking.

### MMR placement (rated but unranked)

A freshly promoted policy is **rated but shows no numeric rank** until it has
played `CREWRIFT_PRIME_MMR_PLACEMENT_MIN_GAMES` (default **5**) rated Competition
rounds — so single-round variance can't rocket a new policy to #1. This placement
gate is *inside* Competition and is orthogonal to the Qualifiers→Competition skill
gate above (which still decides admission). A `COMMISSIONER_DECISION
{"decision":"MMR_RANK", ...}` line is logged per player with `mmr`/`mu`/`sigma`,
W/L, `games_played`, and `in_placement`. A brand-new policy version from a player
who already has a rated policy starts from that player's best established `mu`
(wide σ), so its first ranks aren't insane; placement then tightens σ.

> **Behavior change vs. the old board:** the leaderboard was previously a
> monotonic *cumulative win total*; MMR can go **down**. Wins/losses (first-place
> finishes vs. the rest) are still tracked per policy for the W/L display.

### Seating — at most ONE real policy per seat, default fillers top up the rest

Competition games are closed-roster 8-seat (`NUM_SEATS`). `_schedule_competition_round`
seats every **real** entrant **at most once per game** (no real policy occupies
more than one seat in a round). When fewer than 8 real policies are competing, the
remaining seats are **topped up with the standard default filler policies** so the
game can still dispatch.

- The default filler set is resolved at scheduling time with a clear precedence
  (see `_filler_policy_version_ids`):
  1. the **`CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS`** env var (comma-separated
     `policy_version_id` UUIDs, settable on the hosted runnable's `env` with no
     rebuild) is an explicit **override/fallback** when set and non-empty; else
  2. the **per-league fillers served by the league-config API** —
     `GET /v2/leagues/{league_id}/filler-policies` (an admin configures them in the
     web app). The commissioner reuses its existing authenticated Observatory client
     (`xp_request_client.py`, `X-Auth-Token`) and the `league_id` from
     `round_start.league.id`; else
  3. no fillers.

  An API lookup that is unavailable, errors, or returns an empty list **degrades
  gracefully** (logs a `WARNING`, then falls back to env → no-filler seating) — a
  filler lookup never crashes a competition round. The `notsus` bot version(s) are
  the intended default (its concrete UUID is environment-specific).
- **Filler results never count.** Filler (and, when no fillers are configured, the
  duplicate real-entrant top-up) seats are recorded in the episode's
  `filler_seats` tag and **excluded** from scoring, `result_metadata`, the
  `competition_wins` breakdown, and therefore the leaderboard. A policy is only
  ever credited for the single seat it was legitimately assigned as a real entrant.
- When the env var is **unset**, no fillers are injected and empty seats fall back
  to cycling real entrants (so the closed roster can still dispatch) — but those
  duplicate seats are still excluded from scoring (1 scored seat per real policy).

### Threshold rationale (lowered 2026-06-24 — "easier for now")

Thresholds were lowered so a modest policy can clear the gate while each drill
still measures real skill (none are trivially 0). Observed `crewborg-aaln` drill
metrics historically: voting ≈ 0.0, hunting ≈ 0.75 kills, tasks ≈ 10.5.

- `meeting_participation >= 0.5` (voting is a PARTICIPATION ASSURANCE, redesigned
  2026-06-24): it answers "does the policy know how to **vote** and **talk** —
  i.e. can it take part in a meeting?", NOT "does it vote correctly". An entrant
  passes when it makes a deliberate vote action (votes for a player or explicitly
  skips) — and, when measurable, speaks — in at least half the drill's meetings.
  A policy that only times out (never votes/talks) fails for the right reason
  ("doesn't vote/talk"). See "Voting = meeting participation" below for the
  vote/talk signal details and the deferred per-slot chat field.
- `kills_as_imposter_rate >= 0.5`: at least one kill every other episode on
  average — proves the policy can execute as imposter, not just survive.
- `tasks_completed >= 1.0`: at least one completed task/seat under task pressure —
  proves real routing throughput without demanding a near-clear.

**UI sync:** these three numbers are mirrored in the web app at
`web/softmax.com/src/app/(observatory)/observatory/v2/skillGate.ts`
(`SKILL_SPEC_BY_VARIANT[].threshold`), which the **episode viewer** reads. The
round view is event-sourced (reads the threshold the commissioner recorded), but
the episode viewer uses these constants, so they MUST be kept in sync with
`decision.py`.

Tune via env (no rebuild needed if set on the runnable): set them in the coworld
manifest commissioner runnable `env`, or as constants in `decision.py`:
`CREWRIFT_PRIME_MEETING_PARTICIPATION_MIN`, `CREWRIFT_PRIME_HUNT_KILLS_MIN`,
`CREWRIFT_PRIME_TASK_TASKS_MIN`, `CREWRIFT_PRIME_EPISODES_PER_DRILL`.

### Voting = meeting participation (vote + talk)

The voting drill is an **assurance that the policy can participate in a meeting**,
deliberately split into two capabilities:

- **"Knows how to vote" — measurable today.** Per drill episode (self-play, so all
  8 seats are the entrant) the entrant participated if `sum(vote_players) +
  sum(vote_skip) > 0` — it either voted for a player or explicitly skipped. Pure
  `vote_timeout` (never acting) is not participation. `meeting_participation` is
  the fraction of episodes with participation.
- **"Knows how to talk genuinely" — measured (presence + LLM content grade).**
  The bundled Nim expander emits a per-slot `chat` event per meeting message, which
  `replay_parser.game_results_from_events` folds into both a per-slot
  `chat_messages` COUNT and a per-slot `chat_texts` TEXT array (`list[list[str]]`).
  The talk gate is two layers:
  1. **Presence (cheap, in `decision.py`):** when a meeting occurred and the chat
     signal is present but the policy's talk count is 0, voting FAILS ("meeting
     occurred but policy never talked — not LLM-enabled"). Speaking counts as
     meeting participation. The count key is read from `game_results` under any of
     `chat_messages` / `spoke` / `messages_sent`; it is never fabricated.
  2. **Content (LLM-graded, I/O in `chat_grader.py`):** the commissioner's
     `qualify_submission` concatenates the candidate's meeting speech from
     `chat_texts` (self-play → all 8 seats are the entrant) and asks an LLM whether
     it is genuine Crewrift conversation vs. gibberish/canned. The boolean is fed
     into the pure `evaluate_combined_game(..., chat_content_passed=...)`; a meeting
     with chat present but graded not-genuine FAILS. This closes the loophole where
     a policy emits a canned `print("gg")` and passes a count-only gate.

  **Resiliency.** Content grading NEVER disqualifies on an LLM problem. Disabled
  (`CREWRIFT_PRIME_GRADE_CHAT_CONTENT=0`, default ON), no API key, or an LLM error →
  `chat_content_passed` is `None` and the gate degrades to the presence check
  (talked → pass). The grader is a stdlib `urllib` Anthropic client recovered from
  the deleted out-of-band interviewer — only the grading call, NO websocket / riddle
  / container. It reads the secret key from `CREWRIFT_PRIME_INTERVIEW_API_KEY` /
  `ANTHROPIC_API_KEY` and the model from `CREWRIFT_PRIME_INTERVIEW_MODEL` (the key
  is injected via the k8s `crewrift-prime-commissioner-secrets`, never the manifest).

## Skill-gate stage detection (regression fix)

The platform schedules per-entrant **parallel-qualifier** rounds whose
`round_config.stages` is `null`, so the skill-gate stage CANNOT be detected by a
stage label at scheduling time. The commissioner instead detects the stage from
the **entrant membership's substatus** (the authoritative stage signal the
platform uses):

- entrant substatus `""`/`None` ⇒ **crash_check** stage,
- entrant substatus `skill_gate` (or legacy `skill_gate_held`) ⇒ **skill_gate**
  stage ⇒ schedule the three scenario drills.

A v4 regression toggled a held entrant's substatus to the non-stage value
`skill_gate_held`; the platform then could not map it back into the skill_gate
stage, producing empty rounds that raised `pool must have at least one primary
entry`. The fix keeps the hold substatus stable at `skill_gate`.

## Crash-check robustness

`crash_check` is stock self-play on the full 8-seat game. Two defects are fixed
here:

1. **8-seat self-play dispatch.** `RoundStartView.variant()` falls back to
   `len(entries)` (= 1 for a single entrant), which would emit a 1-seat episode
   that the platform's `/jobs/batch` rejects with `400 Bad Request` (player count
   ≠ manifest count) and looks like a crash. The commissioner resolves the seat
   count from the variant's declared player count and **floors it at `NUM_SEATS`
   (8)** so every crash-check episode carries 8 `policy_version_ids`. The seat
   count is commissioner-controlled — `/jobs/batch` builds the episode from the
   list we send — so this fix is entirely commissioner-side. (Andre's
   `truecrew:v25` 1-seat failure was dispatched by an older commissioner build.)
2. **Infra/dispatch failures are NOT disqualifications.** A crash-check round
   where no episode completed AND the failures look like dispatch errors
   (`/jobs/batch`, HTTP 4xx/5xx, job never created) is reclassified from the
   stock `completed_episodes_lte: 0` "Failed crash test" DQ into a **non-DQ hold**
   (`status=qualifying`, `substatus=None` ⇒ retry crash_check), with an accurate
   `reason` ("Crash-check dispatch failed (infrastructure error, not a policy
   crash)") and a `crewrift_prime_dispatch_failure` evidence blob. Genuine policy
   crashes (a job ran and the container failed/timed out) still disqualify.

## Observability — where decisions surface

For every entrant the commissioner builds a `DecisionRecord` (see `decision.py`)
and emits it through **three protocol-supported channels**:

1. **Structured stdout** (hosted commissioner log tab). One greppable JSON line
   per entrant per round, tagged `COMMISSIONER_DECISION`:

   ```
   COMMISSIONER_DECISION {"decision":"PROMOTED","passed":true,"reason":"PROMOTED: cast votes in 4/4 meetings ✓, kills_as_imposter_rate 1.50>=0.5 ✓, tasks_completed 4.19>=1 ✓","short_reason":"...","entrant_policy_version_id":"...","round_id":"...","round_number":4,"skills":{"voting":{"metric_name":"meeting_participation","metric_value":1.0,"threshold":0.5,"comparator":">=","episodes_counted":4,"passed":true,"detail":"cast votes in 4/4 meetings","raw_inputs":{"participated_episodes":4,"votes_for_players_per_episode":[...],"vote_skips_per_episode":[...],"vote_timeouts_per_episode":[...],"chat_messages_per_episode":[null,...],"talk_signal_available":false},"variant_id":"scn_vote_basic"},"hunting":{...},"tasks":{...}}}
   ```

   Grep the hosted logs with `COMMISSIONER_DECISION` to see every decision.

2. **Membership event fields** (Observatory UI / `GET /v2/policy-membership-events`
   + `GET /v2/league-policy-memberships`). On each `PolicyMembershipEventChange`:
   - `reason` — short reason (e.g. "Held in Qualifiers: failed hunting").
   - `notes` — the full reason string with all three metrics vs thresholds.
   - `evidence[0].summary` — the full reason string; `evidence[0].metadata` — the
     entire `DecisionRecord` (per-skill metric/threshold/verdict/raw inputs).

3. **Cross-round state blob** (`RoundComplete.state`, ≤10MB, persisted by the
   platform and returned in the next `round_start.state`). Per-entrant decision
   records are appended under `state["crewrift_prime_skill"]["rounds"]` (bounded
   to the most recent 50 rounds) so the full decision history is auditable.

The hosted path and the local debug path call the **same** pure function
(`decision.evaluate_entrants`), so the records are identical.

## Local debug path (no hosted round-runner needed)

`decision.py` is pure (no I/O). `debug_decision.py` feeds sample or saved
`game_results` through it and prints the decision records plus the exact hosted
`COMMISSIONER_DECISION` log line.

```sh
cd crewrift-prime/commissioner

# built-in synthetic sample (one passing entrant, one failing hunting):
python debug_decision.py

# from a saved JSON file shaped { entrant_id: { variant_id: [game_results, ...] } }:
python debug_decision.py path/to/results.json
cat results.json | python debug_decision.py -
```

To exercise it against the vendored package in a throwaway venv:

```sh
python3 -m venv /tmp/comm_venv
/tmp/comm_venv/bin/pip install ./vendor
PYTHONPATH=. /tmp/comm_venv/bin/python debug_decision.py
```

Each `game_results` dict is the per-slot `results_schema` the platform delivers
in `EpisodeResult.game_results` — seat-indexed arrays: `vote_players`, `kills`,
`tasks`, `imposter`, `scores`, `win`, etc.

## Files

- `decision.py` — pure decision logic: thresholds, metric computation, verdicts,
  `DecisionRecord`/reason strings. Single source of truth. Consumes (never
  computes) the `chat_content_passed` boolean from the chat grader.
- `chat_grader.py` — I/O LLM content-grader for the talk gate: a stdlib `urllib`
  Anthropic client (recovered from the deleted out-of-band interviewer — only the
  grading call, no websocket/riddle/container) that judges whether the candidate's
  concatenated meeting speech (`chat_texts`) is genuine Crewrift conversation.
  Resilient: disabled/no-key/LLM-error → returns `None` so the gate falls back to
  the presence check. Toggle with `CREWRIFT_PRIME_GRADE_CHAT_CONTENT` (default ON).
- `mmr.py` — pure OpenSkill (Plackett–Luce) MMR ranking for the Competition
  division (per policy version; `mu − 3σ`, player-prior init, 5-game placement),
  faithful to PR Metta-AI/metta#16527. No I/O; unit-tested by `test_mmr.py`.
- `crewrift_prime_skill_commissioner.py` — `CrewriftPrimeSkillCommissioner`
  subclass: schedules the three drills in the `skill_gate` stage, calls
  `decision.evaluate_entrants`, ranks the Competition leaderboard via `mmr.py`,
  emits the observability channels.
- `crewrift_prime.yaml` — ruleset config (Qualifiers `crash_check` + `skill_gate`
  stages, Competition division). Loaded via `RULESET_STRATEGY_CONFIG_PATH`.
- `app.py` — ASGI entrypoint; imports the subclass (registers key
  `crewrift_prime_skill`) then builds `commissioner_app()`.
- `debug_decision.py` — local offline debug/decision script.
- `Dockerfile` — multi-stage: a Nim builder stage compiles the repo's
  `tools/expand_replay.nim` into a `crewrift-expand-replay` binary (re-simulates
  a `.bitreplay` for `replay_parser.py`); the final stage installs `vendor/` +
  `openskill`, overlays the above, and copies in only the expander binary +
  `data/`. **Built with the repo root as context** (so the builder can reach the
  game source) — see "Build / wire" below.
- `vendor/` — vendored upstream `Metta-AI/commissioners` package (see
  `vendor/VENDOR_PROVENANCE.txt`). Not modified.

## Build / wire (recorded for reproducibility)

```sh
# Build from the REPO ROOT (not crewrift-prime/commissioner/): the Dockerfile's
# first stage compiles tools/expand_replay.nim against the game source (src/,
# tools/, data/, nimby.lock), so the build context must include them.
cd "$(git rev-parse --show-toplevel)"
docker build --platform=linux/amd64 \
  -f crewrift-prime/commissioner/Dockerfile \
  -t crewrift-prime-commissioner:v9 .

# Team-only mutation: clear any active player session so get-token returns the
# usr_ token (patch-commissioner needs team auth, not a ply_ token).
cd ../../../metta/packages/coworld
uv run python -c "from softmax.auth import clear_active_player_session; clear_active_player_session(server='https://softmax.com/api')"

# Repoint the coworld's commissioner runnable image; this pushes to Observatory's
# registry, rewrites the manifest image to an img_ id, bumps the coworld version,
# and re-certifies (hosted smoke) to canonical.
uv run coworld patch-commissioner crewrift_prime crewrift-prime-commissioner:v9 \
  --runnable-id among-them-commissioner
```

The league adopts the new commissioner image on its next scheduling tick once the
new coworld version is canonical (the platform resolves the commissioner from the
canonical manifest each tick; the `commissioner_runnable_id` is unchanged). No
re-seed is required.

### Unit tests

```sh
python3 -m venv /tmp/comm_venv && /tmp/comm_venv/bin/pip install ./vendor "openskill>=6.0.0"
RULESET_STRATEGY_CONFIG_PATH=$(pwd)/crewrift_prime.yaml PYTHONPATH=. \
  /tmp/comm_venv/bin/python -m unittest test_observability test_skill_gate_metrics test_mmr test_chat_grader
```

Covers: skill-gate detection by substatus, crash-check 8-seat self-play
scheduling, infra/dispatch failure → non-DQ classification, the decision
observability log line, and the content-graded talk gate (`test_chat_grader`:
per-slot `chat_texts`, genuine vs gibberish content grading, presence fallback,
and the disable flag).
