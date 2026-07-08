# Crewrift Prime Changelog

Recent game, commissioner, and league updates. The Observatory also renders
commissioner-specific entries live under **League Overview → Commissioner Changelog**
(from `PRIME_COMMISSIONER_CHANGELOG` in the deployed commissioner).

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
