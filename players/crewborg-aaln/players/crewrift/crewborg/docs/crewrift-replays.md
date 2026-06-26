# Watching Crewrift replays locally (the correct way)

**Status:** verified 2026-06-11 against crewrift game manifest **0.1.40** / Nim **2.2.10**
and coworld CLI **0.1.22**. Earlier verification used image `crewrift:0.1.23`.
locally — the obvious path (`coworld replay`) is **broken for the Crewrift image**
and will silently show you a live "waiting for players" game instead of your replay.

Verified by A/B control on identical image + replay file: a container started with
`COGAME_LOAD_REPLAY_URI` logged `game started: players=8, imposters=2` and its
replay-tick advanced ~24/s; a container started with only `COGAME_REPLAY_SERVER=1`
(the CLI's way) never started a game and sat at `waiting for players: 0/8` forever.

---

## TL;DR — the working recipe

A Crewrift `.bitreplay` is **not stored frames** — it is per-tick player input
masks that the game server **re-simulates**. To view one, launch the game image so
that it loads the replay **at startup** via the `COGAME_LOAD_REPLAY_URI` env var
(the only mechanism the game actually honors), then open the singular
`/client/replay` page:

```sh
# 1. You need: a replay file (e.g. run-episode's replay.json) and the GAME image tag.
REPLAY_DIR=/tmp/crewborg-tournament-out          # dir containing replay.json
GAME_IMAGE=$(python3 -c "import json;print(json.load(open('/tmp/crewrift-tournament/coworld_manifest.json'))['game']['runnable']['image'])")

# 2. Launch the game image directly in replay-playback mode.
docker rm -f crewrift-replay >/dev/null 2>&1
docker run -d --name crewrift-replay -p 127.0.0.1:52100:8080 \
  -e COGAME_LOAD_REPLAY_URI=file:///coworld-replay/replay.json \
  -v "$REPLAY_DIR":/coworld-replay:ro \
  "$GAME_IMAGE"

# 3. Open the viewer (SINGULAR /client/replay — see "Two bugs" below).
open http://127.0.0.1:52100/client/replay

# 4. When done:
docker rm -f crewrift-replay
```

Playback is **real time (24 fps)**, runs once start→finish, then **holds the final
frame** (no auto-loop). **Reload the page to rewatch.** The viewer has on-canvas
controls (play/pause, speed up to 8×, loop toggle, scrubber).

> The `uri` in `COGAME_LOAD_REPLAY_URI` is the path **inside the container**
> (`/coworld-replay/replay.json`), i.e. relative to the `-v …:/coworld-replay`
> mount — not a host path.

---

## Why `coworld replay` does NOT work for Crewrift

`coworld replay <manifest> <replay.json>` launches the game container with **only**:

- env `COGAME_REPLAY_SERVER=1`
- a read-only mount `<replay_dir> → /coworld-replay`
- published port `127.0.0.1:<port>:8080`

and then prints a client URL and waits for `/healthz` to return 200. It **never
tells the container which replay to load** — it expects the browser client to pass
`?uri=file:///coworld-replay/replay.json`, which the game's `/replay` websocket
would lazily load. (CLI: `coworld/play.py:145-179`, `coworld/runner/runner.py:169-170,356-375`.)

**That contract is not implemented by the Crewrift game image.** Two independent
facts make the CLI path dead:

1. **Crewrift never reads `COGAME_REPLAY_SERVER`.** The server's `replayServerMode`
   is set *only* at startup from `replayLoaded` (`server.nim:664`), which is true
   only when a replay was loaded at boot. The game's only `getEnv` calls are for AI
   API keys. So the CLI's `COGAME_REPLAY_SERVER=1` is a no-op.
2. **Therefore the `?uri=` path is dead.** The `/replay` websocket only stores
   `pendingReplayUri` when `replayServerModeEnabled()` is already true
   (`server.nim:419-437`), and the main loop only loads a pending uri under the same
   condition (`server.nim:705-720`). With `replayServerMode` false, the uri is
   ignored, the socket still upgrades (101) and just streams the **live** game, and
   the container sits in "waiting for players: 0/8" forever (its tick counter climbs
   indefinitely — that's the "stuck at tick 14000" symptom).

`healthz` returning 200 tells you nothing about whether a replay loaded; the CLI
does not verify it (only the `--verify-replay` episode path does, via a different
container). **Health is not enough.**

### Two bugs, for the record
- **Game image** ignores `COGAME_REPLAY_SERVER` + the client `?uri=` mechanism, so
  the CLI's lazy-load contract can't work. Fix is to start with `COGAME_LOAD_REPLAY_URI`.
- **CLI** prints the viewer URL as `/clients/replay` (plural,
  `runner/runner.py:169-170`), but the served client only maps the **singular**
  `/client/replay` → `/replay` websocket. The plural page connects to a dead ws path
  and shows "disconnected…". Always use **`/client/replay`** (singular). This is
  independent of the load bug above and bites even once the replay is loading.

---

## How replay actually works in Crewrift (source-verified)

Env/arg → config is parsed by `bitworld/runtime.nim` `readRuntimeConfig()`
(pinned commit `0547d604`). The relevant env vars:

| Env var | Effect |
|---|---|
| `COGAME_LOAD_REPLAY_URI` | reads the `.bitreplay` bytes into `RuntimeConfig.replay` and sets `replayMode = true` (`runtime.nim:383-386`). **This is the switch that enables playback.** |
| `COGAME_SAVE_REPLAY_URI` | *write* target where a live game saves its replay. Do **not** set together with load — `"Cannot save and load a replay together"` (`server.nim:637-638`). |
| `COGAME_CONFIG_URI` / `COGAME_RESULTS_URI` | live-game config in / results out (irrelevant for replay; config comes from the replay's embedded JSON). |
| `COGAME_HOST` / `COGAME_PORT` | bind overrides; default `0.0.0.0:8080`. |
| `COGAME_REPLAY_SERVER` | **ignored by Crewrift.** (Set by the CLI; no effect.) |

Equivalent CLI flags exist if you exec the binary directly:
`--load-replay-uri:<uri>` or `--load-replay:<path>` (`runtime.nim:331-340`).

Startup playback flow:
`COGAME_LOAD_REPLAY_URI` → `replayMode=true` → `crewrift.nim:21-28` writes the bytes
to a temp `.bitreplay` and passes it as `loadReplayPath` → `runServerLoop` sets
`replayLoaded=true` and `replayServerMode=true` (`server.nim:639,663-664`) and
builds a `ReplayPlayer` with `playing=true` (`replays.nim:318`). The whole live-game
path (player joins, input collection, `sim.step` from live inputs) is gated behind
`if not replayLoaded`, so it's skipped; instead each loop iteration runs
`replayPlayer.stepReplay(sim)` (`server.nim:971-982`).

### The `.bitreplay` format (`replays.nim`, `sim.nim:9-17`)
- Header: magic `"CREWRIFT"`, format version `3`, game name/version, u64 timestamp,
  then the **config JSON** (so the replay carries its own game config).
- Body: typed records — `TickHash(0x01)`, `Input(0x02)`, `Join(0x03)`, `Leave(0x04)`.
  Inputs are 8-bit key masks per player per tick.
- Playback **re-runs the simulation**: `stepReplay` applies that tick's
  joins/leaves/inputs then calls `sim.step(...)`. Per-tick `gameHash()` values are
  validated against `TickHash` records; a mismatch logs and continues.

**Implication:** during replay you will see the *normal game lifecycle* in the logs
and viewer — `waiting for players: 3/8`, `game starting in 5…`, `game started:
players=8, imposters=2`, gameplay events. These are the **re-simulated** recorded
game (players "join" because the replay feeds Join records), **not** a live game
waiting for real connections. That's expected and is how you tell it's working.

### Pacing & end
- Real time: `TargetFps = 24` (`sim.nim`), main loop frame-limited (`server.nim:1089`),
  default speed 1× (`PlaybackSpeeds=[1,2,3,4,8]`).
- `looping` defaults to **false** (`replays.nim:318`): playback stops at the last
  recorded tick and holds the final frame. Toggle loop in the viewer, or reload to
  restart. There is no env var to default looping on.
- No URI allowlist: `file://` and `http(s)://` replay URIs are read directly
  (`runtime.nim:95-121`); a `file://` uri is only rejected if the file is missing.

---

## Getting the inputs

**A replay file.** `coworld run-episode` writes one to `<output>/replay.json`
automatically. (For a *real* tournament-style game rather than the 1-task/300-tick
certification smoke, patch a manifest copy so `certification.game_config` =
`variants[0].game_config` before `run-episode` — see
[`crewborg-tournament-ops` memory] / the steps in this repo's notes.) You can also
`coworld download crewrift -o DIR` for the latest game package.

**The game image tag.** After `coworld download` / `run-episode`, the manifest's
`game.runnable.image` is already rewritten to the **local** image tag
(`coworld/<coworld_id>/crewrift-<ver>-N:downloaded`). Read it straight from the
manifest (see the recipe). Don't guess the `-N` suffix — a package bundles several
images and only `game.runnable.image` is the server.

---

## Verifying a replay is *actually* playing (not a live game)

Health checks and "the map rendered" are **not** sufficient. Use one of these:

1. **Logs:** `docker logs crewrift-replay` should reach `game started: players=8,
   imposters=2` and gameplay events. A *broken/live* container stays at
   `waiting for players: 0/8, need 8 more` and never starts.
2. **No player containers:** `docker ps | grep -i player` is empty — so any
   "players" in the game can only come from the replay.
3. **Decode the replay-tick label from the stream** (most rigorous). The `/replay`
   stream carries a text sprite named `"replay tick <N>"` (`global.nim:2997-3002`).
   Connect and confirm `<N>` advances ~24/sec:
   ```python
   # uv run python - (needs the players env for `websockets`)
   import asyncio, re, time, websockets
   pat = re.compile(rb"replay tick (\d+)")
   async def main():
       async with websockets.connect("ws://127.0.0.1:52100/replay", max_size=None) as ws:
           t0 = time.time()
           while time.time() - t0 < 9:
               msg = await asyncio.wait_for(ws.recv(), timeout=3)
               for m in pat.finditer(bytes(msg)):
                   print(round(time.time()-t0, 1), int(m.group(1)))
   asyncio.run(main())
   ```
   Advancing `N` (e.g. 105→300 over 9s) = genuine playback. A constant `N` at the
   final tick = the replay finished (restart the container to rewatch). No
   `replay tick` label at all = you're looking at a live game, not a replay.

---

## Gotchas

- **Use `/client/replay` (singular).** The CLI's printed plural URL shows
  "disconnected…".
- **Platform warning** (`linux/amd64` on `arm64`) is harmless; it runs under emulation.
- **One replay per process.** `replayLoaded` is a single exclusive switch; you can't
  turn a live game into a replay (or vice-versa) at runtime.
- **It finishes.** ~N ticks ÷ 24 fps seconds of playback, then the final frame
  sticks. Reload or enable loop to rewatch.
- **Don't set save+load together** — the server refuses to start.

---

## Offline replay analysis (player ↔ opponent correlation)

Replays are input masks re-simulated by the Nim server — there is no Python
`.bitreplay` decoder. Use the tools below.

### 1. Download episodes

```sh
players/crewrift/crewborg/scripts/fetch_episodes.sh -n 10
# or: coworld episodes / coworld replays (coworld ≥0.1.22)
```

Each episode directory contains `replay.json` (binary `.bitreplay`), `episode_request.json`
(participants with slot, policy name/version, scores), and per-slot stderr logs.

### 2. Expand replay to structured events (Nim)

From a local `coworld-crewrift` checkout with Nim **2.2.10** (`nimby use 2.2.10`):

```sh
nim r tools/expand_replay.nim --json /path/to/replay.json
```

This runs the canonical loop:

```nim
let data = loadReplay(path)
var sim = initSimServer(data.replayGameConfig())
var replay = initReplayPlayer(data)
while replay.playing:
  replay.stepReplay(sim)
```

Output is JSON events: kills, votes, tasks, phase changes, chat — same schema as
the `crewrift-eventlog-reporter`.

### 3. Correlate crewborg vs opponents (Python)

```sh
uv run python players/crewrift/crewborg/scripts/replay_analysis.py \\
  episode_data/20260610_abc12345 \\
  --crewrift-root ~/coding/games/coworld-crewrift \\
  --trace-db logs/ereq_.../trace.db
```

The script joins:
- **Roster/scores** from `episode_request.json` (who was in the game, which policy version)
- **Ground-truth events** from `expand_replay.nim --json`
- **Agent behavior** from `trace.db` via `positions.server_tick` (= replay tick)

Set `CREWRIFT_ROOT` if the checkout is not at the default path.

### Join key: `server_tick`

The game sends an invisible sprite label `tick <N>` each frame (object/sprite id 5016).
Crewborg records this in `trace.db` `positions.server_tick`. That counter is identical
to the `.bitreplay` timeline tick — use it to align agent mode/intent with replay events
(kills, votes, meetings).

