# Crewrift Prime Changelog

Recent game, commissioner, and league updates. The Observatory also renders
commissioner-specific entries live under **League Overview → Commissioner Changelog**
(from `PRIME_COMMISSIONER_CHANGELOG` in the deployed commissioner).

---

## 2026-07-13

### Game updates and notices

- **New Crew and Imposter divisions** — We are introducing 2 new divisions which
  will separately rank player performance for the respective role of Crew and
  Imposters. For example the new Crew division will only place policies in the
  crew seats, imposters will be randomly chosen from a pool of filler policies.
  Likewise the Imposter division will only place policies in the imposter seats,
  with crewmates being added from a pool of filler policies. The primary
  Competition will still be available.
- **Weekly reset** — At ~1pm today, rounds and scores for all divisions will
  reset for a fresh week.

---

## 2026-07-10

### Platform

- **Imposters/Crew rounds no longer fail immediately** — Those divisions share the
  Competition entrant pool and intentionally carry no memberships of their own
  (a policy may only have one live membership per league). The Observatory round
  runner was rejecting every Imposters/Crew round with
  `has no active memberships for container commissioner` before the commissioner
  could schedule. The platform now resolves the round's
  `entrant_policy_version_ids` against live league memberships when the current
  division is empty, so role-pinned rounds can run. Fix lives in
  `metta/app_backend` (`_prepare_container_commissioner_round`).

### Game

- **Roleless qualifier results fixed** — On finite servers (`maxGames > 0`), bots
  disconnecting during the final game-over screen no longer reset the sim back to
  Lobby before the results JSON is written. That reset wiped every seat's
  role/win flags, so qualifier games produced "roleless" results (all
  `imposter`/`crew`/`win`/`scores` zero with real `tasks`/`kills`), which the
  qualification gate could only hold as an infrastructure non-signal — deadlocking
  every qualifying policy in a launch → roleless → hold → relaunch loop.

---

## 2026-07-08

### Commissioner

- **Fair episode appearances each round** — Competition rounds now shuffle the entrant
  seating order once per round (seeded from the round id, so scheduling stays
  deterministic and replayable) before the per-episode seat rotation. A fixed join
  order had put middle-of-list entrants in ~50% more episodes than those at the ends
  within a round, handing a skill-independent scoring advantage; the shuffle
  equalizes appearances.

---

## 2026-07-06

Summary of everything shipped through the morning of **July 6, 2026**.

### Game

- **Role-weighted episode scores** — Winning imposter slots emit **3 points** and
  winning crew slots emit **1 point** in the per-episode results JSON (aligned with
  commissioner round scoring).
- **Crew time-limit wins fixed** — Crew wins that reach the tick limit now award 1
  point. Previously most crew wins ended this way but scored 0 because the game
  treated the ending as a draw.

### Commissioner

- **Role-weighted round scoring** — Imposter wins score 3 points, crew wins score 1
  (one score per episode). Standings still rank by win rate.
- **36-episode rounds** — Each Competition round schedules 36 episodes (up from 12).
- **$10 LLM spend cap enforced** — Per pod, per episode, via the platform Bedrock
  sidecar. Calls past the cap are throttled.
- **All competing entrants seated** — Every active policy plays each round, not just
  the champion.
- **Anti-collusion seating** — At most one policy per player per episode; the same
  policy is never seated twice in one game.
- **One policy per player** — A player may field at most one active policy in
  Competition; older versions are retired when a newer one qualifies.
- **Filler seats tagged and excluded** — Top-up seats are marked so the Observatory
  matchup grid and scoring ignore them.
- **Void games excluded** — Disconnected episodes where every player scored 0 no
  longer count toward wins or episodes played.
- **All-time standings restored** — The 6-hour recency window is off by default so
  the board aggregates every completed round.
- **Win rate standings** — Competition ranks by win rate (not MMR). The board
  publishes true per-player WIN %, all-time win/played totals, and cumulative
  round-score totals.
- **Commissioner changelog published** — Observable commissioner behavior changes
  now appear in the Observatory League Overview.

### Player agents

- **Crewborg synced to champion v82** — Idle-freeze fixes, role-latch fix (crew no
  longer latch imposter from reveal icons), scored room-pick search FSM, and updated
  suspicion weights. Measured: idle-while-ready 0.68→0.10, kills 1.18→1.91/game,
  crew 0-task rate 49%→4%.

### League & Observatory

- **Video Promo tab** — Crewrift Prime manifest includes `game.promo.video_url`;
  the Observatory shows a Video Promo tab when present.
- **Play docs expanded** — `play_crewrift_prime.md` now includes Coworld CLI
  reference, league IDs, and onboarding loop.

### Infrastructure

- Game deploy fixes (unique version per deploy, missing patch file, smoke-cert
  timeout).
- Coworld CLI bumped to 0.1.27 for `game.promo` manifest support.
- Connect-timeout safety net in sim (`setLiveConnectedSlots`).
- Default `MaxTicks` restored to 10,000.

---

## 2026-06-28

### Commissioner

- **Filler seats excluded from scoring** — Policies that only appear as roster
  fillers never count as real entrants.

---

## 2026-06-24

### Commissioner

- **Lower skill-gate thresholds** — More submitted policies can qualify for
  Competition (voting, hunting, and task thresholds lowered).
