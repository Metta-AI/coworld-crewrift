# Crewrift gameplay: rules, mechanics, and scoring

Reference for coding agents: the **rules of Crewrift** — roles, win conditions,
the phase machine, every core mechanic (tasks, kill, vent, report, button,
meetings/voting), and the score every action pays. This doc is **policy-agnostic**
(it describes the game, not crewborg). For *why* a policy should do anything with
these rules, see crewborg's own docs (cross-links at the bottom).

Crewrift is a social-deduction game in the *Among Us* lineage: a crew of
**crewmates** races to finish tasks while a hidden minority of **imposters** kills
them off, and meetings let the living vote someone out. Crewmates win by tasks or
by eliminating imposters; imposters win by reaching parity.

---

## How to use / re-verify this doc

**Verified at crewrift commit `a3e2859`** (`coworld-crewrift`, `src/crewrift` at
upstream-current; `sim.nim:GameVersion` = `"1"`). The authoritative source is the
Nim simulation in `src/crewrift/` — **`sim.nim`** (constants, mechanics, scoring,
win conditions, phase machine, `applyInput`, vote logic), **`tasks.nim`** (task
assignment), and the deployed config in this repo's **`coworld_manifest.json`**
(its `variants[].game_config` carries the actual game settings the platform runs).

Every claim below is cited to **`file:Symbol`** — a proc/const/type *name*, not a
line number (names survive line drift). Each constant in the table carries a tiny
**re-check** grep. Run it if a value looks off; the source moves.

- Re-check one symbol fast, e.g.: `grep -n "KillCooldownTicks" src/crewrift/sim.nim`
- Re-check the deployed config:
  `python3 -c "import json;print(json.load(open('coworld_manifest.json'))['variants'][0]['game_config'])"`

> ### ⚠️ VERSION / VARIANT WARNING — these numbers are not universal
>
> **Constants differ across Crewrift versions and variants, and every value here
> is config-overridable per episode.** Do not hard-code any number from this doc
> as a law of the game. Three things can change a value out from under you:
>
> 1. **Build/version drift.** The `sim.nim` const block changes between crewrift
>    builds. Confirmed since the lab's older `d9f6b30` snapshot: `KillCooldownTicks`
>    went **500 → 800**, `VoteTimerTicks` went **240 → 1200**, and the emergency
>    button **stopped resetting kill cooldowns** (see [§ Meetings](#meetings--voting)).
> 2. **Per-variant config.** `coworld_manifest.json` `variants[].game_config`
>    overrides the source defaults. crewborg's own
>    [`opportunity.py`](../../crewborg/strategy/opportunity.py) notes:
>    *"Crewrift Prime (the deployed target league) uses `KillCooldownTicks`=500;
>    regular Crewrift uses 800"* (`DEFAULT_KILL_COOLDOWN_TICKS = 500`) — i.e. the
>    league a submission actually plays may differ from this repo's manifest.
> 3. **Per-episode config.** Each episode/replay bakes its own `game_config` JSON
>    (`sim.nim:defaultGameConfig` is only the fallback; `sim.nim:update` —
>    `update*(config, jsonText)` — applies the per-episode overrides). **The
>    authoritative value for a game you are playing is that
>    build's `sim.nim` const block and that episode's baked `game_config` — not
>    this doc.**
>
> The lab toolkit pins the deployed game build as **`CREWRIFT_REF`**
> (`tools/versions.env`; currently `d9f6b30` there, while this repo's
> `src/crewrift` is newer at `a3e2859`). To re-derive for the deployed league,
> read that ref's `sim.nim` and the episode's own config. **When in doubt, read
> the episode's `game_config`.**

---

## Constants (@ `a3e2859`)

All in `src/crewrift/sim.nim` unless noted. **Ticks → seconds at
`TargetFps`=24** (`sim.nim:TargetFps`). Times shown are at the source defaults;
the **deployed** column flags where `coworld_manifest.json`'s `variants[0]`
(`game_config`) overrides the default.

| Constant | Value @`a3e2859` | Deployed (manifest) | Source symbol | Re-check |
|----------|------------------|---------------------|---------------|----------|
| `TargetFps` | 24 | — | `sim.nim:TargetFps` | `grep -n 'TargetFps\* =' src/crewrift/sim.nim` |
| `MaxPlayers` | 16 | — | `sim.nim:MaxPlayers` | `grep -n 'MaxPlayers\* =' src/crewrift/sim.nim` |
| `MinPlayers` | 8 | 8 | `sim.nim:MinPlayers` | `grep -n 'MinPlayers\* =' src/crewrift/sim.nim` |
| `ImposterCount` | 2 | 2 | `sim.nim:ImposterCount` | `grep -n 'ImposterCount\* =' src/crewrift/sim.nim` |
| `AutoImposterCount` | true | **false** | `sim.nim:AutoImposterCount` | `grep -n 'AutoImposterCount\* =' src/crewrift/sim.nim` |
| `TasksPerPlayer` | 8 | 8 | `sim.nim:TasksPerPlayer` | `grep -n 'TasksPerPlayer\* =' src/crewrift/sim.nim` |
| `TaskCompleteTicks` | 72 (3 s) | — | `sim.nim:TaskCompleteTicks` | `grep -n 'TaskCompleteTicks\* =' src/crewrift/sim.nim` |
| `KillRange` | 20 (world px) | — | `sim.nim:KillRange` | `grep -n 'KillRange\* =' src/crewrift/sim.nim` |
| `KillCooldownTicks` | **800** (≈33.3 s) | 800 | `sim.nim:KillCooldownTicks` | `grep -n 'KillCooldownTicks\* =' src/crewrift/sim.nim` |
| `ButtonResetsKillCooldowns` | **false** | false | `sim.nim:ButtonResetsKillCooldowns` | `grep -n 'ButtonResetsKillCooldowns\* =' src/crewrift/sim.nim` |
| `VentRange` | 16 (world px) | — | `sim.nim:VentRange` | `grep -n 'VentRange\* =' src/crewrift/sim.nim` |
| vent cooldown | 30 (≈1.25 s) *(hard-coded)* | — | `sim.nim:tryVent` (literal `30`) | `grep -n 'ventCooldown = 30' src/crewrift/sim.nim` |
| `ReportRange` | 20 (world px) | — | `sim.nim:ReportRange` | `grep -n 'ReportRange\* =' src/crewrift/sim.nim` |
| `ButtonCalls` | 1 (per player/game) | — | `sim.nim:ButtonCalls` | `grep -n 'ButtonCalls\* =' src/crewrift/sim.nim` |
| `MeetingCallTicks` | 72 (3 s) | — | `sim.nim:MeetingCallTicks` | `grep -n 'MeetingCallTicks\* =' src/crewrift/sim.nim` |
| `VoteTimerTicks` | **1200** (50 s) | 1200 | `sim.nim:VoteTimerTicks` | `grep -n 'VoteTimerTicks\* =' src/crewrift/sim.nim` |
| `VoteFinalizeTicks` | 48 (2 s) | — | `sim.nim:VoteFinalizeTicks` | `grep -n 'VoteFinalizeTicks\* =' src/crewrift/sim.nim` |
| `VoteResultTicks` | 72 (3 s) | — | `sim.nim:VoteResultTicks` | `grep -n 'VoteResultTicks\* =' src/crewrift/sim.nim` |
| `RoleRevealTicks` | 120 (5 s) | 120 | `sim.nim:RoleRevealTicks` | `grep -n 'RoleRevealTicks\* =' src/crewrift/sim.nim` |
| `GameInfoTicks` | 72 (3 s) | — | `sim.nim:GameInfoTicks` | `grep -n 'GameInfoTicks\* =' src/crewrift/sim.nim` |
| `StartWaitTicks` | 120 (5 s) | 120 | `sim.nim:StartWaitTicks` | `grep -n 'StartWaitTicks\* =' src/crewrift/sim.nim` |
| `GameOverTicks` | 360 (15 s) | 360 | `sim.nim:GameOverTicks` | `grep -n 'GameOverTicks\* =' src/crewrift/sim.nim` |
| `MaxTicks` | 10000 (≈6.9 min) | 10000 | `sim.nim:MaxTicks` | `grep -n 'MaxTicks\* =' src/crewrift/sim.nim` |
| `MessageCooldownTicks` | 100 (≈4.2 s) | — | `sim.nim:MessageCooldownTicks` | `grep -n 'MessageCooldownTicks\* =' src/crewrift/sim.nim` |
| `StuckPenaltyTicks` | 480 (20 s) | — | `sim.nim:StuckPenaltyTicks` | `grep -n 'StuckPenaltyTicks\* =' src/crewrift/sim.nim` |
| `ConnectTimeoutTicks` | 2880 (120 s) | 2880 | `sim.nim:ConnectTimeoutTicks` | `grep -n 'ConnectTimeoutTicks\* =' src/crewrift/sim.nim` |
| `DisconnectTimeoutTicks` | 720 (30 s) | 720 | `sim.nim:DisconnectTimeoutTicks` | `grep -n 'DisconnectTimeoutTicks\* =' src/crewrift/sim.nim` |

> The two values that **changed since the lab's `d9f6b30` doc**: `KillCooldownTicks`
> (500 → **800**) and `VoteTimerTicks` (240 → **1200**). Both are confirmed at 800
> / 1200 in *both* `sim.nim` and the deployed manifest @`a3e2859`.

---

## Roles

Two roles (`sim.nim:PlayerRole` = `Crewmate` | `Imposter`), assigned once at game
start and fixed for the game (`sim.nim:startGame`).

- **Crewmates** — the majority. Do tasks, find bodies, report, vote out imposters.
  Cannot kill or vent. A crewmate killed or voted out becomes a **ghost** (`alive
  = false`) that can still move and finish tasks but cannot report/vote/be seen by
  the living (see [Ghosts](#ghosts)).
- **Imposters** — the hidden minority. Can **kill** (in range, off cooldown) and
  **vent** (teleport between grouped vents); cannot do tasks. They blend in,
  sabotage votes, and reach parity.

### Imposter count (scaling)

Set by `sim.nim:effectiveImposterCount(config, playerCount)`:

- If `config.autoImposterCount` is true, it uses `sim.nim:ratioImposterCount` =
  `0` for `playerCount < 5`, else `(playerCount - 3) div 2` (so 5–6 players → 1
  imposter, 7–8 → 2, 9–10 → 3, …).
- If false, it uses the fixed `config.imposterCount`.
- Either way it is capped at `max(0, playerCount - 1)`.

**In the deployed game** (`coworld_manifest.json` `variants[0].game_config`)
`autoImposterCount` is **false** and `imposterCount` is **2**, on a closed
8-player roster — so the live game is **2 imposters vs 6 crewmates**. (Note this
overrides the source default `AutoImposterCount=true`.) Per-slot role pins are
also possible via `sim.nim:slotConfig` (`hasRole`), with the remaining imposter
seats filled randomly (`sim.nim:startGame`).

---

## Objective and win conditions

Checked every Playing tick and after each vote result by
`sim.nim:checkWinCondition` (called from `sim.nim:step` and after
`sim.nim:applyVoteResult`). With imposters present and ≥1 player, in order:

1. **Crewmates win** if `aliveImposters == 0` (all imposters dead).
2. **Imposters win** if `aliveImposters >= aliveCrewmates` (**parity** — imposters
   are at least as many as living crewmates).
3. **Crewmates win** if `allTasksDone()` — every crewmate's assigned tasks are
   complete (`sim.nim:allTasksDone` → `sim.nim:totalTasksRemaining`).

The winner is finalized in `sim.nim:finishGame` (moves to `GameOver`, pays
`WinReward`). **Time limit:** if `gameTicksElapsed >= MaxTicks` while in a
playing-family phase, the game ends as a **Crewmate win** (`sim.nim:checkMaxTicks`
→ `finishGame(Crewmate)`) — crew outlasted the imposters, so `WinReward` and win
points are paid to crew. Only infrastructure endings (connect/disconnect
timeouts, roster aborts) finish with `timeLimitReached = true`, which records a
**draw**: `finishGame` returns early, pays no `WinReward`, and every slot scores 0.

---

## The phase machine

Phases are `sim.nim:GamePhase` (enum order: `Lobby`, `Playing`, `Voting`,
`VoteResult`, `GameOver`, `RoleReveal`, `GameInfo`, `MeetingCall`). The per-tick
dispatch is `sim.nim:step` (a chain of `if sim.phase == …` early-returns), and
roles/tasks are assigned in `sim.nim:startGame`.

| Phase | What happens | Exit / timer |
|-------|--------------|--------------|
| `Lobby` | Wait for the roster; countdown once enough players are present | `StartWaitTicks`; `sim.nim:lobbyIsStarting` |
| `GameInfo` | Optional pre-game info screen | `GameInfoTicks` (skipped if 0) |
| `RoleReveal` | Each player is shown its role | `RoleRevealTicks` → `enterPlaying` |
| `Playing` | **The main game.** Move, do tasks, kill, vent, report, press button | until a meeting starts, a win condition, or `MaxTicks` |
| `MeetingCall` | Short interstitial after a report/button before voting | `MeetingCallTicks` → `startVoting` |
| `Voting` | Discuss (chat) + move cursor + cast a vote | `VoteTimerTicks`, or all votes in → `VoteFinalizeTicks` → `tallyVotes` |
| `VoteResult` | Show who (if anyone) was ejected | `VoteResultTicks` → `applyVoteResult` → back to `Playing` |
| `GameOver` | Winner shown, rewards paid | `GameOverTicks` |

**Critical:** player **inputs only act in `Playing` and `Voting`.** In `Playing`,
`sim.nim:step` calls `sim.nim:applyInput` (movement, tasks, kill, vent, report,
button). In `Voting`, `step` reads input directly to move the vote cursor and cast
votes. All other phases ignore player input and just tick their timer
(`MeetingCall`/`VoteResult`/`RoleReveal`/`GameInfo`/`GameOver` each `dec` a timer
and `return`). A meeting also **teleports everyone home** (see below), so
in-world position is reset across every meeting.

Typical flow: `Lobby → RoleReveal → Playing → (report/button) → MeetingCall →
Voting → VoteResult → Playing → … → GameOver`.

---

## Core mechanics

### Movement

Continuous, momentum-based, in world pixels. `sim.nim:applyInput` (alive) and
`sim.nim:applyGhostMovement` (dead) integrate velocity from the four direction
inputs with acceleration `Accel`, friction `FrictionNum/FrictionDen`, top speed
`MaxSpeed`, and sub-pixel carry at `MotionScale` (`sim.nim:defaultGameConfig`).
The map is `MapWidth`×`MapHeight` = 1235×659 (`sim.nim:MapWidth`/`MapHeight`);
walls block movement (`sim.nim:applyMomentumAxis`, `sim.nim:isWall`).

### Tasks (crewmates)

- Each crewmate is assigned `TasksPerPlayer` = **8** tasks at start
  (`sim.nim:startGame` using `tasks.nim` route-cost assignment; routes are tuned to
  `tasks.nim:TaskRouteGoal` ≈ 1500). Tasks are **per-player**: a task station's
  `completed[]` is indexed by player, so two crewmates can have the "same" station
  and each must do it (`sim.nim:TaskStation.completed`).
- To do a task, a crewmate **stands inside the station rectangle and holds the
  action input (`attack` / "A") while not moving.** Each held tick with no movement
  increments `taskProgress`; at `TaskCompleteTicks` = **72 (3 s)** the task
  completes (`sim.nim:applyInput`, crewmate branch → `sim.nim:completeTask`).
- **Movement resets progress.** The hold only counts when `inputX == 0 and inputY
  == 0`; any direction input (or releasing `attack`, or switching to a different
  station) sets `activeTask = -1` and `taskProgress = 0` (`sim.nim:applyInput`).
  Progress does **not** carry over — you must complete it in one stationary hold.
- Completing a task pays `TaskReward` once (`sim.nim:completeTask`; idempotent —
  re-completing a done task is a no-op). All crewmate tasks done → **crewmates win**.

> **Stuck penalty:** a crewmate that stops moving while it still has unfinished
> tasks and is *not* actively doing one is docked `StuckPenalty` every
> `StuckPenaltyTicks` (20 s) of standing still (`sim.nim:applyStuckPenalty`). This
> discourages idling.

### Kill (imposters)

`sim.nim:tryKill`, fired on a fresh `attack` press by an imposter in
`sim.nim:applyInput`:

- Requires the killer is an **alive imposter** with `killCooldown == 0`.
- Targets the **nearest alive crewmate** within `KillRange` = **20** world px
  (squared-distance compare; imposters can't kill imposters).
- On a kill: the victim becomes a ghost, a `Body` is dropped at the victim's
  position (`sim.nim:Body`, carrying `killerSlot` and `killTick`), the killer is
  paid `KillReward`, and the killer's `killCooldown` is set to
  `KillCooldownTicks` = **800** (≈33.3 s). Cooldown counts down each Playing tick
  (`sim.nim:step`).

### Vent (imposters)

`sim.nim:tryVent`, fired on a fresh `b` press by an imposter in
`sim.nim:applyInput`:

- Requires an **alive imposter** with `ventCooldown == 0`, standing within
  `VentRange` = **16** world px of a vent.
- **Teleports** the imposter to the **next vent in the same group** (vents are
  grouped by `Vent.group` with a 1-based `groupIndex`; it advances to
  `groupIndex + 1`, wrapping back to index 1 — a ring), zeroing velocity/carry
  (`sim.nim:tryVent`).
- Sets `ventCooldown` to a **hard-coded 30 ticks** (≈1.25 s) — note this is a
  literal in `tryVent`, *not* a config field, so it does not scale with config.

### Report a body

`sim.nim:tryReport`, fired on a fresh `attack` press in `Playing` by an alive
player near a body:

- Only in `Playing`; the reporter must be **alive** and within `ReportRange` =
  **20** world px of a `Body` (`sim.bodies`, scanned up to `bodiesBeforeTick` so a
  body dropped this same tick isn't instantly reportable).
- Starts a **body meeting**: `startVote(VoteCalledBody, …)` → `MeetingCall` →
  `Voting`. Either crewmates *or* imposters can technically report (it's gated only
  on `alive`), but reporting reveals a body and (for body meetings) **resets
  imposter kill cooldowns** — see the rule below.

### Emergency button (one-shot)

`sim.nim:tryCallButton`, also on a fresh `attack` press in `Playing`:

- The caller must be **alive**, standing inside the map's button rectangle
  (`CrewriftMap.button`), and must not have exhausted `ButtonCalls` = **1**
  (`buttonCallsUsed >= config.buttonCalls`). So **each player gets one button press
  per game.**
- Starts a **button meeting**: `startVote(VoteCalledButton, …)`. Unlike a body
  meeting, a button meeting **does not require a body** and — in the deployed game —
  **does not reset kill cooldowns** (see below).

Within one `attack` press the order in `applyInput` is: **report first, then
button, then kill** (each step early-returns if it left `Playing`). So an imposter
standing on a body *and* on the button will report rather than kill on that press.

### Meetings & voting

When a meeting starts (`sim.nim:startVote`), phase goes `Playing → MeetingCall`
(`MeetingCallTicks` interstitial) → `Voting` (`sim.nim:startVoting`). The
`VoteState` records the **call kind** (`sim.nim:VoteCallKind` =
`VoteCalledUnknown` | `VoteCalledButton` | `VoteCalledBody`).

During `Voting` (`sim.nim:step`, Voting branch):

- The timer is `VoteTimerTicks` = **1200 (50 s)**. Each living player has a
  **cursor** over the roster + a **Skip** cell. `up`/`left` move the cursor
  backward, `down`/`right` forward (skipping dead players); `sim.nim:moveCursor`.
- A fresh `attack` **casts the vote** at the cursor: a player index, or **Skip**
  (cursor on the `players.len` cell → vote `-2`). A cast vote is locked in (no
  re-vote). Chat is `sim.nim:addVotingChat`, throttled by `MessageCooldownTicks`.
- When **all living players have voted** (`sim.nim:allVotesCast`), a short
  `VoteFinalizeTicks` (2 s) "dots visible" delay runs, then `sim.nim:tallyVotes`.
  If the `VoteTimerTicks` clock runs out first, `tallyVotes(timedOut = true)` runs
  and players who never voted are each docked `VoteTimeoutPenalty`.
- **Tally** (`sim.nim:tallyVotes`): the ejection threshold starts at
  `skipVotes + timeoutVotes`; a player is ejected only with **strictly more** votes
  than that baseline. **Ties → no ejection**, and an all-skip/all-timeout vote →
  no ejection. The ejected player (if any) is recorded in `voteState.ejectedPlayer`.
- Phase → `VoteResult` for `VoteResultTicks` (3 s), then `sim.nim:applyVoteResult`:
  the ejected player (if any) dies, **all bodies and chat are cleared, every player
  is teleported home** (`sim.nim:resetPlayerToHome`), phase returns to `Playing`,
  and `sim.nim:checkWinCondition` runs.

> **🚨 The kill-cooldown reset rule (this changed — read carefully).**
>
> Whether a vote result **resets every alive imposter's kill cooldown** back to
> `KillCooldownTicks` is decided by `sim.nim:voteResultResetsKillCooldowns` and
> applied in `sim.nim:applyVoteResult`:
>
> - **Body meetings** (`VoteCalledBody`) and **unknown-cause meetings**
>   (`VoteCalledUnknown`) → **ALWAYS reset** imposter kill cooldowns.
> - **Button / emergency meetings** (`VoteCalledButton`) → reset **only if**
>   `config.buttonResetsKillCooldowns` is true.
>
> **In the deployed game that flag is `false`** (`coworld_manifest.json`
> `variants[0].game_config.buttonResetsKillCooldowns = false`; source default
> `sim.nim:ButtonResetsKillCooldowns` is also `false`). **So in the live game the
> emergency button does NOT reset kill cooldowns — only a body-report or
> unknown-cause meeting does.** This is config-gated and overridable per episode,
> so always read the episode's own `game_config` to be sure.
>
> Practical consequence: a body-report meeting hands every imposter a fresh kill on
> the very next Playing tick; an emergency button does not. (crewborg encodes this:
> [`crewrift/crewborg/types.py`](../../crewborg/types.py) only treats a non-`"button"`
> meeting as a cooldown reset — *"Emergency-button meetings do not reset
> killCooldown"* — and its imposter play deliberately avoids self-reporting kills,
> which would open a cooldown-resetting body meeting.)

### Ghosts

A player whose `alive` is false (killed or voted out) is a **ghost**. Ghosts are
routed to `sim.nim:applyGhostMovement` (not `applyInput`): they **move freely
(ignoring walls, clamped to the map by `sim.nim:containGhost`) and crewmate ghosts
can still complete their remaining tasks** (the ghost-movement proc runs the same
task-hold logic). Ghosts cannot kill, report, press the button, or vote, and the
living cannot see them. Their finished tasks still count toward the crewmate
task-win (and `finishGame` settles their task rewards via
`sim.nim:settleAllCompletedTaskRewards`).

---

## Scoring

Per-action rewards, all in `sim.nim` and applied via `sim.nim:addReward`. These
are the **simulation rewards** the game emits; how the league aggregates them into
standings is the platform's concern (see `./coworld-platform.md`).

| Reward | Value | When | Source symbol |
|--------|-------|------|---------------|
| `TaskReward` | **+1** | Each task completed (paid once per task) | `sim.nim:TaskReward` → `sim.nim:completeTask` |
| `KillReward` | **+10** | Imposter kills a crewmate | `sim.nim:KillReward` → `sim.nim:tryKill` |
| `WinReward` | **+100** | Each player on the winning role at `finishGame` (not paid on a time-limit draw) | `sim.nim:WinReward` → `sim.nim:finishGame` |
| `VoteTimeoutPenalty` | **−10** | A living player who never cast a vote when the vote timer ran out | `sim.nim:VoteTimeoutPenalty` → `sim.nim:tallyVotes` |
| `StuckPenalty` | **−1** | A crewmate idle with unfinished tasks, per `StuckPenaltyTicks` (20 s) | `sim.nim:StuckPenalty` → `sim.nim:applyStuckPenalty` |
| `ConnectionTimeoutPenalty` | **−100** | A player that fails to connect / times out | `sim.nim:ConnectionTimeoutPenalty` |

Re-check the reward block: `grep -nE 'TaskReward|KillReward|WinReward|VoteTimeoutPenalty|StuckPenalty|ConnectionTimeoutPenalty' src/crewrift/sim.nim`.

**Reading these for strategy:** winning dominates the score (+100), a single kill
(+10) is worth ten tasks, and tasks are the crewmate's only steady income. The
penalties punish disengagement (idling crewmates, non-voters). For how crewborg
turns this into behavior, defer to its strategy docs below.

---

## Related docs

- [`./crewrift-protocol.md`](./crewrift-protocol.md) — the wire protocol: the bytes
  a player sends/receives to *act* on these mechanics.
- [`./crewrift-replays.md`](./crewrift-replays.md) — reading a *finished* game
  (visual replay, `expand_replay` event timeline, per-policy logs).
- [`./coworld-platform.md`](./coworld-platform.md) — the game-agnostic
  image/build/ship/host contract and how scores become standings.
- [`./README.md`](./README.md) — reference-doc index.
- [`../best_practices.md`](../best_practices.md) — crewborg engineering practices.
- crewborg internals — strategy over these rules:
  [`../../crewborg/docs/imposter-play.md`](../../crewborg/docs/imposter-play.md),
  [`../../crewborg/docs/crewmate-play.md`](../../crewborg/docs/crewmate-play.md),
  [`../../crewborg/docs/meetings.md`](../../crewborg/docs/meetings.md),
  [`../../crewborg/docs/perception-and-belief.md`](../../crewborg/docs/perception-and-belief.md).
