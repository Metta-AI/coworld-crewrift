# Crewrift Prime Filler Players — Platform / Game Wiring Handoff

## Context

A Competition episode is a **closed-roster 8-seat** crewrift game: the game cannot
dispatch with empty seats. When fewer than 8 real entrants are competing (e.g. only
4 real policies), the commissioner **tops up** the remaining seats with **filler
policies** so the game can run.

Filler policies are **seat-fillers ONLY**. They must never:

- be counted in the scoring of an episode or round,
- be ranked, or appear on the leaderboard, or
- be represented as a real player anywhere a human reads results — they may be
  referenced **only** as a *filler policy* (e.g. `filler policy <policy_name>`).

The **commissioner side is complete and tested** (see
`crewrift-prime/commissioner/crewrift_prime_skill_commissioner.py` and
`test_observability.py`). This doc covers the **game/platform-side** work still
required — in the sibling repo `../metta` (game image + Observatory web app) — so
fillers are also hidden/labeled **inside the game artifacts and UI**, not just in
the commissioner's own scoring.

> `file ~lines` references below are into `../metta` unless explicitly under
> `crewrift-prime/`. The platform contract is owned by metta; re-derive from the
> cited source before relying on fine detail (see
> `players/crewborg/docs/reference/coworld-platform.md`).

---

## What the commissioner already guarantees (no platform change needed)

Per Competition episode, the commissioner tags **both** the filler seat indices and
the filler policy ids on the scheduled episode request, and excludes them from
scoring with defense-in-depth:

- `tags["filler_seats"]` — comma-separated 0-based seat indices that are top-up
  (filler or duplicated-real-entrant) seats.
- `tags["filler_policy_version_ids"]` — comma-separated `policy_version_id`s placed
  purely as fillers (a duplicated **real** entrant is NOT tagged here, since it
  still legitimately scores at its own seat).

At round completion (`_complete_competition_round`):

- a seat is excluded if it is a filler seat **OR** holds a filler policy id;
- a pure-filler policy is dropped from the scored entrants entirely (never ranked,
  never represented as a real entrant);
- the filler policy ids are surfaced explicitly so any consumer can LABEL them:
  - decision log line `COMMISSIONER_DECISION {"decision":"FILLER_POLICIES_EXCLUDED", "filler_policy_version_ids":[...], ...}`,
  - `RoundComplete.round_display["filler_policy_version_ids"]` (list of ids),
  - an observability note: `Filler policies are seat-fillers only and are EXCLUDED from scoring: filler policy <id>, …`.

**Implication for the platform/game:** the commissioner already knows which seats
and policies are fillers. The remaining work is making the GAME artifact and the
Observatory UI honor that — because those layers render per-slot data (e.g. the
`names` array, replays, meeting transcripts) that the commissioner does not author.

---

## Gaps to close (game / Observatory side)

### 1. Game `results.json` `names[]` still names filler seats as players

**Where.** The crewrift game writes a per-slot `results.json` validated by
`game.results_schema`; it includes a per-slot `names` array alongside `scores`,
`win`, `imposter`, `crew`, … (`coworld-crewrift/coworld_manifest.json`
`game.results_schema`; metta `docs/COWORLD_MANIFEST.md` "Results Schema";
`docs/roles/GAME.md` "Player slots").

**Problem.** The game has no concept of "filler" — every seat, including a
seat-filler, gets a normal player name in `names[]`. Anything that renders that
array (replays, the Observatory EPISODES panel, debugging tools) shows fillers as
real players.

**Fix (pick one; the runner has the info the commissioner tagged):**

- **Preferred — propagate filler seats to the runner/game.** The round-runner
  already receives the scheduled episode request (with `tags["filler_seats"]` /
  `tags["filler_policy_version_ids"]`). Forward a per-slot `is_filler: bool[]`
  (and/or a `filler_seats: int[]`) into the game launch config so the game can
  write a per-slot `is_filler` flag into `results.json` and label those slots'
  `names[]` as `filler policy <name>` (never a real handle).
- **Minimum — add `is_filler` to `results_schema`.** Even without renaming, add a
  per-slot boolean `is_filler` to the results schema so every downstream consumer
  can detect and exclude/label filler seats deterministically rather than guessing.

**Acceptance:** for a topped-up episode, `results.json` marks the filler slots, and
no filler slot's `names[]` entry reads as a real player name.

### 2. Observatory EPISODES / replay views render filler seats

**Where.** Observatory v2 leagues view (the web app is a **separate repo**, not
present here — see `crewrift-prime/commissioner/docs/round-scoring-explainer-handoff.md`
for the same "web app is elsewhere" caveat).

**Problem.** The EPISODES panel shows per-slot raw game scores (e.g. `63.0000`) and
replays show per-seat avatars/names. Filler seats currently appear identical to real
players.

**Fix.** Consume the per-slot `is_filler` flag from #1 (and/or join the round's
`round_display["filler_policy_version_ids"]`) to:

- visibly tag filler seats as `filler policy <name>` (dimmed / "FILLER" badge),
- exclude filler seats from any per-player aggregation in the EPISODES panel, and
- never link a filler seat to a real player row in the standings.

Do **not** hardcode crewrift-specific strings: key off `is_filler` /
`filler_policy_version_ids`, exactly like the existing `round_scoring` and
`skill_gate` explainers (commissioner owns the strings; the web app renders
generically).

### 3. Meeting transcripts / chat reference fillers by name

**Where.** In-game meetings produce chat/vote transcripts (player-authored;
crewborg's meeting layer is in `players/crewborg/crewborg/strategy/meeting/`, but the
transcript is assembled and surfaced by the game/Observatory).

**Problem.** A filler bot speaking/voting in a meeting appears under a normal player
name in the transcript, implying a real participant.

**Fix.** Wherever a transcript/vote line is attributed to a seat, resolve the seat
through the per-slot `is_filler` flag (#1) and render the speaker as
`filler policy <name>`. Filler votes/lines must not be attributed to, or counted
against, any real player.

---

## Summary of the contract

| Layer | Owner | Status | Action |
|---|---|---|---|
| Round scoring / ranking / leaderboard | Commissioner (this repo) | **Done** | none — fillers excluded + labeled |
| `round_display` / observability labeling | Commissioner (this repo) | **Done** | `filler_policy_version_ids` emitted |
| Game `results.json` per-slot `is_filler` + `names[]` | Game (`../metta` game image) | **TODO** | mark/label filler slots (#1) |
| Observatory EPISODES / replay rendering | Web app (separate repo) | **TODO** | tag + exclude filler seats (#2) |
| Meeting/vote transcript attribution | Game / web app | **TODO** | render fillers as `filler policy <name>` (#3) |

**Single rule for every TODO layer:** a filler seat is referenced **only** as
`filler policy <name>`, is never attributed to or aggregated into a real player, and
is never counted in scoring of an episode in any way.
