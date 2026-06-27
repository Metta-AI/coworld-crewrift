# Reading a finished Crewrift game: replays, event timeline, logs

Reference for coding agents: how to read what happened in a *completed* Crewrift
game — the visual replay, the `expand_replay` event timeline, and per-policy logs.
This doc is **policy-agnostic** (it is about reading any finished Crewrift game,
not about crewborg specifically).

---

## How to use / re-verify this doc

**Verified at crewrift commit `a3e2859`** (`coworld-crewrift`), against
`bitworld` (the replay codec, installed at `~/.nimby/pkgs/bitworld`). Every claim
below is cited to **`file:Symbol`** — a proc/const/type name, *not* a line number
(names survive line drift). Each fact carries a tiny **re-check** recipe; run it
if something looks off, because the source moves.

- Game source of truth: `src/crewrift/replays.nim`, `tools/expand_replay.nim`,
  `src/crewrift/sim.nim`, `src/crewrift/server.nim`, `src/crewrift/global.nim`.
- Codec source of truth: `bitworld/replays.nim` (re-exported by
  `replays.nim:export replayCodec`).
- Re-check a symbol fast, e.g.:
  `grep -n "CrewriftReplayFormatVersion" src/crewrift/replays.nim`.

> ### ⚠️ VERSION WARNING — `expand_replay` is version-coupled
>
> `expand_replay` does **not** read pre-computed events. It **re-simulates** the
> recorded inputs through a *compiled-in* crewrift `sim` and validates a per-tick
> hash (`tools/expand_replay.nim:expandReplayTimeline` →
> `replays.nim:stepReplay` → `replays.nim:checkReplayHash`). A given binary can
> therefore only expand a replay that was recorded by the **same crewrift build**
> it was compiled from. Built from a different version, its sim diverges within a
> few hundred ticks, the hash check fails, and it emits almost nothing
> (`hash failed`). The embedded `gameVersion` is the coarse constant `"1"`
> (`sim.nim:GameVersion`) and does **not** catch this.
>
> So the *deployed ref matters*: league/Observatory replays are recorded by the
> current crewrift upload, so you must build `expand_replay` from that same ref.
> The lab toolkit pins it as **`CREWRIFT_REF`** (`tools/build/versions.env`);
> keep that pin tracking the deployed upload, and pass `--ref <sha>` for any other
> recording build. See [§B](#b-the-replay-as-events-expand_replay-objective-ground-truth).

---

## The three ways to read a finished game

| Way | Audience | What you get | Policy-independent? | Version-coupled? |
|-----|----------|--------------|---------------------|------------------|
| **Visual replay** ([§A](#a-the-visual-replay-for-humans)) | human | re-simulated video in the game image | yes | no (game image re-sims itself) |
| **`expand_replay` timeline** ([§B](#b-the-replay-as-events-expand_replay-objective-ground-truth)) | agent | objective tick-by-tick text/JSONL events: every player, **true roles**, kills/bodies/votes/tasks/chat/score | yes | **yes** (must match the recording build) |
| **Per-policy logs** ([§C](#c-a-players-own-logs-subjective)) | agent | one policy's **subjective** view: what it perceived, believed, decided | no (one slot) | no (recorded output) |

Use the **timeline** to find *what happened* and ground truth a policy couldn't
see; use a **policy log** to see *why that policy did what it did*; **align by
tick**. The visual replay is for a human to watch — an agent can't "see" it (but
[§F](#f-verifying-playback-when-viewing-not-via-the-cli) shows how to confirm it loaded).

---

## Getting episode data

To download an episode's replay + per-slot logs + metadata, use the
**`coworld-episode-artifacts`** skill — do not hand-roll the discovery. **Defer
the how-to to that skill's own docs.** The one fact to internalize here:

> **League and experience-request episodes are disjoint populations.** League
> episodes (what a submitted league player like crewborg actually plays) are
> **NOT** in the `/v2/episode-requests` table, so `coworld episodes -p crewborg`
> returns `[]`. The skill discovers them via `/stats/policy-versions` →
> `/episodes` instead. Experience-request episodes (the ones *you* create) *are*
> in `/v2/episode-requests`.

What the download contains, per episode directory:

- `episode.json` — metadata incl. the **slot↔policy** map, in one of two shapes:
  - **league** episodes: **`policy_results[]`** = `[{position, policy:{name,version}}]`;
  - **experience-request** episodes: **`participants[]`** = `[{position, policy_name, version}]`.
- `results.json` — outcome arrays (`win`, `kills`, `names`, `scores`, `vote_*`).
  **Does not carry the slot↔policy map** — use `episode.json` for that.
- `replay.json` (+ raw `replay.json.z`) — the `.bitreplay` (magic `CREWRIFT`).
- `logs/policy_agent_{N}.log` — one per slot, each policy's own stderr.

`coworld run-episode` also writes a `replay.json` for a locally-run game.

---

## A. The visual replay (for humans)

A Crewrift replay is **not stored frames** — it is per-tick player input masks the
game **re-simulates** on playback (see [§E](#e-the-bitreplay-format-verified)). It plays
back inside the Crewrift *game image*, either Observatory-hosted (you get a URL) or
launched locally with Docker.

**Hosted (no local Docker), easiest:**

```sh
coworld replay-open <episode_request_id> --hosted   # prints/opens an Observatory viewer URL
```

`coworld replay-open` takes an **`<episode_request_id>`** (one game), not a
batch/experience-request id. Caveat: that route resolves **experience-request**
episodes only; raw league `/episodes` ids are a different population and won't
resolve (see "disjoint populations" above).

**Local (needs Docker):**

```sh
coworld replay-open <episode_request_id>                       # resolve + launch viewer locally
# or, given a manifest + replay you already have:
coworld download crewrift -o /tmp/crewrift
coworld replay /tmp/crewrift/coworld_manifest.json /path/to/replay.json
```

Both set `COGAME_LOAD_REPLAY_URI` on a local container and open `/client/replay`
(singular). Loading is wired in `bitworld/runtime.nim:readRuntimeConfig`:
`COGAME_LOAD_REPLAY_URI` reads the bytes and sets `replayMode = true`; the binary
also accepts `--load-replay-uri:<uri>` / `--load-replay:<path>`. `file://` and
`http(s)://` are read directly (`runtime.nim:readCogameUri`). Re-check:
`grep -n "COGAME_LOAD_REPLAY_URI\|readCogameUri" ~/.nimby/pkgs/bitworld/src/bitworld/runtime.nim`.

`coworld replay` does **not** confirm the replay loaded — if the viewer looks
empty, use [§F](#f-verifying-playback-when-viewing-not-via-the-cli). It also leaves a
`tmp/coworld-replay-*` workspace under the **current directory** and a running
container — run from a scratch dir or clean up (`docker rm -f crewrift-replay`).

---

## B. The replay as events: `expand_replay` (objective ground truth)

`tools/expand_replay.nim` parses a `.bitreplay` into a tick-by-tick **event
timeline**. Because it reads the recorded game, it knows the **true roles** — kills
are attributed to the real imposters — so it is the fastest objective view of what
actually happened. The repo recommends it as the agent's starting point
(`README.md §"Inspect replay timelines"`).

### It re-simulates → the binary must match the recording build

The driver `tools/expand_replay.nim:expandReplayTimeline` builds a fresh
`initSimServer` from the replay's embedded config, then drives
`replays.nim:stepReplay` once per tick. `stepReplay` applies the tick's
joins/leaves/chats/inputs, calls `sim.step`, then `replays.nim:checkReplayHash`
validates `sim.gameHash()` against the recorded `TickHash`. `expandReplayTimeline`
sets `replay.mismatchQuit = true`, so the **first** mismatch raises `ReplayError`,
which it catches and records as `hashFailed`/`failTick`. The text printer then
emits `  hash failed` at that tick and exits non-zero
(`tools/expand_replay.nim:printText`, `when isMainModule`). Re-check:
`grep -n "mismatchQuit = true\|hashFailed = true\|hash failed" tools/expand_replay.nim`.

This is why a usable binary must be built from the **same crewrift version that
recorded the replay** (see the version warning up top). For **league/Observatory**
replays — recorded by the current upload at `CREWRIFT_REF` — a binary built at that
ref expands them fully.

**Build it with the lab toolkit (defer the how-to to that tool's docs):**

```sh
tools/build_expand_replay.sh            # builds version-matched, host-native; uses CREWRIFT_REF
tools/build_expand_replay.sh --run /tmp/eps/<episode>/replay.json   # build (if needed) + expand
tools/build_expand_replay.sh --ref <sha>                            # a different recording build
```

**When it still won't expand** (an older league season, someone else's build, an
unknown ref): fall back to the **visual viewer** ([§A](#a-the-visual-replay-for-humans) —
it loads the replay into the *episode's own* game image, so it always re-sims
correctly) and the **policy logs** ([§C](#c-a-players-own-logs-subjective) —
version-independent). Don't trust the bundled `tests/replays/notsus.bitreplay` as an
oracle — it can be stale relative to the committed sim and hash-fail under a current
build; use a real downloaded replay.

### Output: the text event vocabulary

Default `--format text` prints `replay <path>`, then `tick N` lines with indented
events under the tick they occur, then `done` (`tools/expand_replay.nim:printText`).
Players render as `color(name)` (`expand_replay.nim:player` →
`playerColorText`); rooms are named; tasks are indexed. The **complete** event
vocabulary is exactly `tools/expand_replay.nim:text` (one branch per
`ReplayEventKind`):

```
phase <Phase>                                          # Lobby|Playing|Voting|VoteResult|GameOver|RoleReveal|GameInfo|MeetingCall
  player orange(notsus1) joined
  player orange(notsus1) entered room Bridge
  player orange(notsus1) left room Bridge
  player blue(notsus7) started task 6
  player blue(notsus7) completed task 6
  player orange(notsus1) completed task 8 while dead   # dead crew still complete tasks
  player pale blue(notsus8) killed orange(notsus1)     # <- true imposter revealed
  body orange(notsus1) room Hydroponics                # a body is now present there
  player red(notsus2) reported body orange(notsus1) room Hydroponics   # body report opens a vote
  player blue(notsus7) called emergency button
  player orange(notsus1) died                          # death with no distinct body event
  player orange(notsus1) revived
  player red(notsus2) voted skip                       # or "voted <color(name)>"
  player blue(notsus7) said "just resetting imposter cool downs"   # chat (Nim repr of the text)
  score player blue(notsus7) +1 (for completing task)  # reasons below
```

`Phase` names are `sim.nim:GamePhase`
(`Lobby|Playing|Voting|VoteResult|GameOver|RoleReveal|GameInfo|MeetingCall`).
Score **reasons** are the only five `expand_replay.nim:printScoreLine` emits —
`killing` (+`KillReward`=10), `completing task` (+`TaskReward`=1), `winning`
(+`WinReward`=100), `failing to vote or skip` (`VoteTimeoutPenalty`=-10),
`standing still` (`StuckPenalty`=-1) — decomposed from each player's `reward`
delta (`expand_replay.nim:printScoreChanges`; amounts are `sim.nim` consts).
Re-check the vocabulary: `grep -n 'result = "' tools/expand_replay.nim`.

### Output: `--format jsonl` (machine-readable) + `--snapshot-every`

`--format jsonl` emits one JSON object per line, schema `{ts, player, key, value}`
(`tools/expand_replay.nim:standardRow`, `eventRow`). Event `key`s are
`tools/expand_replay.nim:key` — `player_joined`, `entered_room`, `left_room`,
`phase`, `vote_called_body`, `vote_called_button`, `kill`, `body`, `died`,
`revived`, `started_task`, `completed_task`, `vote_cast`, `chat`, `score`.

Add `--snapshot-every N` to also emit **sampled state** rows (only meaningful with
`jsonl`; `expandReplayTimeline(..., snapshotEvery)`): `episode_metadata`,
`map_geometry`, `player_manifest` (after roles/tasks assigned, so `role` +
`assigned_tasks` are real), per-tick `player_state` / `body_state`, and
`*_visible_interval` rows (`player_visible_interval` / `body_visible_interval`,
derived from each living player's rendered view —
`expand_replay.nim:visibleObservations`). The stream ends with a `trace_complete`
row (`complete:false` + `trace_warning` on hash failure). Re-check:
`grep -n 'standardRow(0, -1\|_visible_interval\|trace_complete' tools/expand_replay.nim`.

### Programmatic use

`tools/expand_replay.nim:expandReplayTimeline(data: ReplayData, snapshotEvery = 0):
ReplayTimeline` returns a typed `ReplayTimeline` (`events: seq[ReplayEvent]`,
`traceRows: seq[JsonNode]`, `tickCount`, `hashFailed`, `failTick`). Each
`ReplayEvent` (`tools/expand_replay.nim:ReplayEvent`) carries `tick`, `kind`
(`ReplayEventKind`), `actorSlot`/`actorLabel`, `secondarySlot`/`secondaryLabel`,
`room`, `task`, `whileDead`, `phase`, `voteSkip`, `scoreAmount`/`scoreReason`,
`chatText`. The CLI text/JSONL is just a render of these — writing a small custom
extractor against this is encouraged over regex-parsing the text.

---

## C. A player's own logs (subjective)

Each player slot writes its **own stderr** to `logs/policy_agent_{N}.log` — that one
policy's subjective view. Unlike the objective timeline, a log:

- is **subjective** — only what that policy could see/infer, for its slot only;
- is **version-independent** — it's the policy's own recorded output, so it reads
  regardless of game build (no `expand_replay` hash-match needed). For
  hosted/league episodes this makes logs your **primary** data source;
- has a **policy-specific format** — there is no shared schema. crewborg writes
  structured JSON traces; the Nim players write plain-text stderr.

**Find the slot you care about** from `episode.json` (**not** `results.json`, which
has no slot map), filtering by name **and version** — one league episode can contain
several versions of the same policy, all logging the same shape:

```bash
ep=/tmp/eps/<episode_dir>
# league episodes:
jq -r '.policy_results[] | select(.policy.name=="<name>" and .policy.version==<N>) | .position' "$ep/episode.json"
# experience-request episodes:
jq -r '.participants[] | select(.policy_name=="<name>" and .version==<N>) | .position' "$ep/episode.json"
# -> slot N; log is "$ep/logs/policy_agent_N.log"
```

Occasionally a slot's "log" is a Kubernetes collector error rather than the
policy's output — `grep '^{'` (for JSON logs) skips those. **Hosted logs are
capped (~10k lines)** and may be missing the **start** of the game — don't assume
tick 0 is present.

**Per-policy formats:**

- **crewborg** — per-tick **JSON-lines** trace (perception, suspicion beliefs, mode
  decisions, commands). Full format, field reference, and `jq` recipes:
  [`../../crewborg/docs/trace-logs.md`](../../crewborg/docs/trace-logs.md). crewborg
  defaults to `jsonl@artifact`: the trace is zipped to a per-slot **player artifact**
  (`artifacts/policy_artifact_{N}.zip` → `telemetry.jsonl` + `manifest.json`), which
  is **not** subject to the hosted log-line cap — prefer the artifact over
  `logs/policy_agent_{N}.log` when both exist.
- **the Nim players** (e.g. notsus) log **plain-text stderr**
  (human-readable, no structured schema); read directly.
- **A new player** — whatever it emits to stderr; document its format alongside the
  player if it's worth querying programmatically.

---

## D. Replay vs logs — which to reach for

- **`expand_replay` / the replay = objective.** Every player, true roles, the actual
  kills/bodies/votes/tasks/chat. Use it to locate the moment that matters and to
  know ground truth a policy couldn't see. Build a **version-matched** binary
  ([§B](#b-the-replay-as-events-expand_replay-objective-ground-truth)); it
  `hash failed`s only when the binary ≠ the recording build.
- **A policy's log = subjective and version-independent** — always reads regardless
  of game build. For hosted/league episodes it is your **primary** source.
- **For a hosted episode you can't expand:** the log alone carries a lot (a rich
  logger like crewborg records its per-tick view of who/what was around and the
  phase/death/vote lifecycle); use the **visual viewer**
  ([§A](#a-the-visual-replay-for-humans)) for objective ground truth.
- **Align by tick.** Find the event (timeline or log lifecycle), then read the
  policy's log at that tick (for crewborg, its `decision_snapshot` / nearest
  `suspicion_snapshot` — see [`../../crewborg/docs/trace-logs.md`](../../crewborg/docs/trace-logs.md)).

---

## E. The `.bitreplay` format (verified)

The Crewrift `.bitreplay` is the generic **bitworld replay codec**
(`bitworld/replays.nim`) parameterized by Crewrift's `replays.nim:CrewriftReplaySpec`:
magic `"CREWRIFT"` (`CrewriftReplayMagic`), format version `3`
(`CrewriftReplayFormatVersion = 3'u16`), game name/version
(`sim.nim:GameName="crewrift"` / `GameVersion="1"`), joins carrying name+slot+token
(`rjkNameSlotToken`), chat allowed, compression allowed, `hashOrder: rhoStop`.
Re-check: `grep -n "CrewriftReplaySpec\|CrewriftReplayFormatVersion\|CrewriftReplayMagic" src/crewrift/replays.nim`.

- **Header** (`bitworld/replays.nim:openReplayWriter` / `parseReplayBytes`): magic,
  format version (u16), game name, game version, a u64 **millisecond** timestamp,
  then the **config JSON** — so a replay carries its own game config (read back as
  `ReplayData.configJson`, applied via `replays.nim:replayGameConfig`).
- **Body:** typed records (`bitworld/replays.nim` consts) —
  `ReplayTickHashRecord` `0x01`, `ReplayInputRecord` `0x02`, `ReplayJoinRecord`
  `0x03`, `ReplayLeaveRecord` `0x04`, `ReplayChatRecord` `0x05`,
  `ReplayDebugSpriteRecord` `0x06`. Inputs are 8-bit key masks, per player per tick
  (`replays.nim:applyReplayEvents`, `decodeInputMask`).
- **Playback re-runs the sim** (`replays.nim:stepReplay`): apply the tick's
  joins/leaves/chats/inputs, `sim.step`, then `checkReplayHash` validates
  `gameHash()` against that tick's `TickHash` (mismatch echoes + continues, or
  raises under `mismatchQuit`). Seek keyframes are built every
  `ReplayKeyframeTicks = 100` ticks (`replays.nim:buildReplayKeyframes`). Default
  playback `looping = true` (`replays.nim:initReplayPlayer`) at speeds
  `PlaybackSpeeds = [1, 2, 3, 4, 8, 16]`.

The server refuses to **save and load together**:
`server.nim:runServerLoop` raises `"Cannot save and load a replay together"` when
both paths are set. One replay per process — `server.nim` keeps a single
`appState.replayLoaded` switch. Re-check:
`grep -n "Cannot save and load\|replayLoaded" src/crewrift/server.nim`.

Test files showing the format in use: `tests/replays/notsus.bitreplay` (fixture),
`tests/test_replay.nim` (round-trips the codec), `tests/manual_replay.nim` (runs
bots and saves a replay), `tests/test_replay_controls.nim` (play/pause/speed/loop/scrub).

---

## F. Verifying playback (when viewing, not via the CLI)

Health and "the map rendered" don't prove a replay loaded. The `/replay` stream
carries a text sprite `"replay tick <N>"` (`global.nim:addReplayTimingControls`,
the `ReplayTickSpriteId` sprite; the value is `controlTick`). Confirm `<N>`
advances ~24/sec (resets to 0 each loop). Re-check:
`grep -n '"replay tick "' src/crewrift/global.nim`.

```python
# uv run python -   (needs websockets)
import asyncio, re, time, websockets
pat = re.compile(rb"replay tick (\d+)")
async def main():
    async with websockets.connect("ws://127.0.0.1:52100/replay", max_size=None) as ws:
        t0 = time.time()
        while time.time() - t0 < 9:
            msg = await asyncio.wait_for(ws.recv(), timeout=3)
            for m in pat.finditer(bytes(msg)):
                print(round(time.time() - t0, 1), int(m.group(1)))
asyncio.run(main())
```

Advancing `N` = genuine playback; no `replay tick` label = a live game, not a replay.

---

## Gotchas

- **One replay per process** — `server.nim` keeps a single `appState.replayLoaded`.
- **Use `/client/replay` (singular)** for the local viewer page.
- **Platform warning** (`linux/amd64` on `arm64`) is harmless; runs under emulation.
- **Don't set `COGAME_SAVE_REPLAY_URI` + `COGAME_LOAD_REPLAY_URI` together** — the
  server refuses to start (`server.nim:runServerLoop`).
- **`expand_replay` against a mismatched build** prints `hash failed` and emits
  almost nothing — that's a version mismatch, not a corrupt replay
  ([§B](#b-the-replay-as-events-expand_replay-objective-ground-truth)).

---

## Toolkit tools (name + when; how-to lives in each tool's own docs)

These live in the lab/optimizer toolkit, not in this game repo. Use this index to
pick the right one; **defer the how-to to each tool's own documentation.**

- **`tools/build_expand_replay.sh`** — builds a version-matched,
  host-native `expand_replay` binary (tarball fetch, no credentials). Use whenever
  you need to expand a replay and don't already have a binary built at the
  recording ref ([§B](#b-the-replay-as-events-expand_replay-objective-ground-truth)).
- **`coworld-episode-artifacts` skill** — downloads an episode's replay + per-slot
  logs + metadata, handling the league/experience-request disjoint-population
  discovery. Use first, to get the files this doc reads.
- **`crewrift-report` skill** — turns a *set* of episodes (an experience request, a
  policy's recent league games, a tournament batch) into a dense
  strengths/weaknesses report. Use when surveying many games, not one.
- **`crewrift-event-warehouse`** — batch-ingests expanded timelines into a queryable
  store for cross-episode analysis. Use when you need aggregate stats across many
  replays rather than reading one.

---

## See also

- [`./crewrift-gameplay.md`](./crewrift-gameplay.md) — what the events *mean* as
  gameplay (roles, phases, scoring); read this to interpret a timeline or log.
- [`./crewrift-protocol.md`](./crewrift-protocol.md) — what a player must *do* over the wire.
- [`./coworld-platform.md`](./coworld-platform.md) — the hosting platform
  (episodes, leagues, artifacts) this doc's downloads come from.
- [`./README.md`](./README.md) — reference-doc index.
- [`../best_practices.md`](../best_practices.md) — crewborg working conventions.
- [`../../crewborg/docs/trace-logs.md`](../../crewborg/docs/trace-logs.md) —
  crewborg's per-slot JSON trace format + `jq` recipes (the [§C](#c-a-players-own-logs-subjective) "crewborg" pointer).
</content>
</invoke>
