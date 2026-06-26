# Replay analysis — quick start

Correlate crewborg's behavior against its opponents by reconstructing a finished
game from its `.bitreplay`. This guide is the shortest path from "I have an
episode" to "I have a player↔opponent correlation report."

> A Crewrift `.bitreplay` is **not** stored frames — it is per-tick input masks
> that the Nim simulator **re-runs**. There is no Python decoder, so analysis
> shells out to `coworld-crewrift/tools/expand_replay.nim`.

## One-time setup

You need three things: the `coworld` CLI (≥0.1.22), Nim 2.2.10, and a local
`coworld-crewrift` checkout.

```sh
# 1. coworld CLI (pinned in the `coworld` extra)
cd ~/experiments/softmax/players
uv sync --extra test --extra coworld
uv run coworld episodes --limit 1        # smoke test: should list real episodes

# 2. Nim 2.2.10 (the version the replay tools build against)
cd ~/experiments/softmax/coworld-crewrift
nimby use 2.2.10 && nimby sync -g nimby.lock
export PATH="$HOME/.nimby/nim/bin:$PATH"
nim r tests/test_replay.nim              # smoke test: 3 OKs

# 3. Tell the analysis script where the checkout is
export CREWRIFT_ROOT=~/experiments/softmax/coworld-crewrift
```

## The workflow

### 1. Download episodes

```sh
players/crewrift/crewborg/scripts/fetch_episodes.sh -n 10
```

Writes `episode_data/<timestamp>_<id8>/` per episode, each with:

| File | What |
|------|------|
| `replay.json` | the binary `.bitreplay` (per-tick input masks) |
| `episode_request.json` | roster: slot → policy name/version + scores |
| `logs/crewborg_slot{N}_v{V}.log` | crewborg's stderr trace per slot |

### 2. Analyze one episode

```sh
uv run python players/crewrift/crewborg/scripts/replay_analysis.py \
  episode_data/20260610_abc12345
```

The script re-simulates the replay (`expand_replay.nim --json`), joins the events
to the roster, and prints a JSON report: per-slot kills/tasks/votes plus a
`crewborg_opponent_correlation` matrix (for each crewborg slot, who else was in
the game and how they did).

### 3. (Optional) join crewborg's own decisions

If you have the artifact `trace.db` for that episode, pass it to align crewborg's
per-tick mode/intent with replay events:

```sh
uv run python players/crewrift/crewborg/scripts/replay_analysis.py \
  episode_data/20260610_abc12345 \
  --trace-db logs/ereq_.../trace.db \
  --slot 2 \
  -o report.json
```

The join key is `server_tick`: the game emits an invisible `tick <N>` sprite each
frame, crewborg records it in `positions.server_tick`, and that counter is
identical to the replay timeline. (Older `trace.db` artifacts predate the
`positions` table — the join is simply skipped if it's absent.)

## Watch a replay visually

To eyeball ground truth instead of reading JSON, launch the game image in
playback mode (do **not** use `coworld replay` — it's broken for Crewrift):

```sh
docker run -d --name crewrift-replay -p 127.0.0.1:52100:8080 \
  -e COGAME_LOAD_REPLAY_URI=file:///coworld-replay/replay.json \
  -v "$EPISODE_DIR":/coworld-replay:ro "$GAME_IMAGE"
open http://127.0.0.1:52100/client/replay   # singular /client/replay
```

Full source-verified recipe: [`crewrift-replays.md`](./crewrift-replays.md).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Set CREWRIFT_ROOT ...` | Export `CREWRIFT_ROOT` or pass `--crewrift-root <path>` |
| `expand_replay failed` / Nim not found | `export PATH="$HOME/.nimby/nim/bin:$PATH"` after `nimby use 2.2.10` |
| `hash_failed: true` on the bundled `notsus.bitreplay` | Expected — that fixture diverges at tick 712 (legacy meeting timing). Fresh production replays validate cleanly. |
| `coworld episodes` 404s | You're on coworld <0.1.22; `uv sync --extra coworld` |
| `positions` table missing | Older artifact; re-fetch a recent episode |

## Version baseline (verified 2026-06-11)

| Component | Version |
|-----------|---------|
| Nim | 2.2.10 |
| coworld CLI | 0.1.22 |
| crewrift manifest | 0.1.40 |
| replay format | 3 |
