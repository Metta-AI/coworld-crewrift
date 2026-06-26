---
name: replay-reconstruction
description: "Use to reconstruct ground-truth game info from a Crewrift .bitreplay and match it to crewborg's player behavioral data so a hypothesis can be validated. Trigger on 'reconstruct the game from the replay', 'join trace to replay', 'expand the replay', 'who actually killed whom', or 'match player data to ground truth'."
---

# Replay Reconstruction & Join

## What this gives you

The player artifact is crewborg's **point of view** (what it believed it saw).
The replay is **ground truth** (what actually happened: real roles, real kills,
real votes). The loop needs both, joined on a shared clock, so a behavioral
pattern in the artifact can be checked against what was objectively true.

Specifically, this skill reconstructs game info from a replay **in the shape that
matches the player behavioral data structure** — the `positions`/`traces` tables
keyed by `server_tick` — so the join is mechanical and the hypothesis is testable.

**Announce at start:** "Reconstructing ground truth from the `.bitreplay` and
joining it to crewborg's artifact on `server_tick`, so I can check the behavioral
pattern against what actually happened."

## Critical fact: a `.bitreplay` is not frames

A Crewrift `.bitreplay` is **per-tick input masks** that the Nim simulator
**re-runs** — not stored frames. There is **no Python decoder**. Reconstruction
shells out to the game checkout:

```
data = loadReplay(path); sim = initSimServer(data.replayGameConfig())
replay = initReplayPlayer(data)
while replay.playing: replay.stepReplay(sim)
```

`tools/expand_replay.nim --json` wraps that loop and emits structured ground-truth
events (kills with victim slot, votes with target, task completions, phase
changes), each carrying a `ts` = the server tick.

## One-time setup

Need: `coworld` CLI ≥ 0.1.22, Nim 2.2.10, and a local `coworld-crewrift`
checkout. (Baseline verified 2026-06-11: Nim 2.2.10, coworld 0.1.22, manifest
0.1.40, replay format 3.)

```sh
cd ~/experiments/softmax/coworld-crewrift
nimby use 2.2.10 && nimby sync -g nimby.lock
export PATH="$HOME/.nimby/nim/bin:$PATH"
export CREWRIFT_ROOT=~/experiments/softmax/coworld-crewrift
nim r tests/test_replay.nim     # smoke: 3 OKs
```

## Reconstruct + join (the script does both)

```sh
# ground truth only:
uv run python players/crewrift/crewborg/scripts/replay_analysis.py \
  episode_data/20260610_abc12345

# + join crewborg's own per-tick mode/intent to the replay events:
uv run python players/crewrift/crewborg/scripts/replay_analysis.py \
  episode_data/20260610_abc12345 \
  --trace-db logs/ereq_.../trace.db --slot 2 -o report.json
```

The report carries `slot_stats` (per-slot kills/tasks/votes from ground truth),
the `crewborg_opponent_correlation` matrix (for each crewborg slot, who else was
in the game and how they did), and — with `--trace-db` — `trace_joins`: replay
events aligned to crewborg's `mode`/`intent_kind`/`phase` at that tick.

## The join key (match the data structures)

| Source | Clock field | Note |
|---|---|---|
| Replay events (`expand_replay`) | `ts` | server tick of the event |
| Artifact `positions` | `server_tick` | from the `tick <N>` sprite marker |

They are the **same counter**, so the join is `positions.server_tick == event.ts`.
This is *the* reason the `positions` table records `server_tick` — it makes
crewborg's per-tick belief/mode line up against authoritative outcomes. Older
artifacts predate the `positions` table; the join is simply skipped (the script
handles this) and you fall back to ground truth alone.

## Roles by color — the cheap global truth

`domain.game_over` (and the GameOver roster icons) give the **role census by
color** for the whole lobby from *any one* artifact in the episode. Combined with
`SLOT_COLORS` (the slot→color map) you can label every slot's role without
re-simulating — `eval_2026-06-11_v3_vs_v8/analyze.py` uses exactly this
(`roles_by_color` × `positions` × `outcome`) to compute per-version win rates
cheaply, only re-simulating when it needs per-event detail.

## Watching a replay visually (eyeball ground truth)

Do **not** use `coworld replay` (broken for Crewrift). Launch the game image in
playback mode:

```sh
docker run -d --name crewrift-replay -p 127.0.0.1:52100:8080 \
  -e COGAME_LOAD_REPLAY_URI=file:///coworld-replay/replay.json \
  -v "$EPISODE_DIR":/coworld-replay:ro "$GAME_IMAGE"
open http://127.0.0.1:52100/client/replay
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Set CREWRIFT_ROOT ...` | export `CREWRIFT_ROOT` or pass `--crewrift-root` |
| `expand_replay failed` / Nim missing | `export PATH="$HOME/.nimby/nim/bin:$PATH"` after `nimby use 2.2.10` |
| `hash_failed: true` on bundled `notsus.bitreplay` | expected fixture divergence; fresh production replays validate |
| `positions` table missing | older artifact — re-fetch a recent episode; join skipped |

## Integration

- **Consumes:** `artifact-capture` (the `trace.db` / `positions` to join).
- **Feeds:** `eval-aggregation` (joined ground-truth + behavior records),
  `pattern-toolkit` (ground-truth-labeled behavior).
- **Grounded in:** `scripts/replay_analysis.py`, `docs/replay-analysis.md`,
  `docs/crewrift-replays.md`, `AGENTS.md` (the `tick <N>` marker, `.bitreplay`
  semantics).
