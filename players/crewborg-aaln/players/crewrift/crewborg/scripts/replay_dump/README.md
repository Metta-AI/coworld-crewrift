# replay_dump — full multi-agent ground truth from `.bitreplay`

A Crewrift `.bitreplay` stores per-tick input masks that the Nim simulator
re-runs deterministically. `tools/expand_replay.nim` (in the game checkout)
turns one into an *event* timeline; **`replay_dump.nim` here additionally
emits every player's position each Playing tick**, body-drop coordinates
with the killer, roster/role lines, and map geometry — everything needed for
player↔opponent correlation analysis (approach tracks, proximity, isolation,
vote matrices joined to true roles).

The trace↔replay join key is `server_tick`: crewborg records it per tick in
the artifact `positions` table and it equals the replay's `sim.tickCount`
exactly (verified 1000/1000 position matches on a hosted episode).

## Requirements

- A `coworld-crewrift` checkout (`CREWRIFT_ROOT`), Nim 2.2.10
  (`nimby use 2.2.10 && nimby sync -g nimby.lock`, then
  `export PATH="$HOME/.nimby/nim/bin:$PATH"`). See
  `players/crewrift/crewborg/docs/replay-analysis.md` for the one-time setup.
- The exporter imports `../src/crewrift/{replays,sim}` and
  `./expand_replay`, so it compiles **from inside `$CREWRIFT_ROOT/tools/`**;
  the runner script copies it there (one untracked file).

## Run

```sh
# smoke test on the bundled fixture (expected: desyncs at tick 712, by design)
cp replay_dump.nim "$CREWRIFT_ROOT/tools/"
(cd "$CREWRIFT_ROOT" && nim c -d:release -o:/tmp/replay_dump tools/replay_dump.nim)
/tmp/replay_dump "$CREWRIFT_ROOT/tests/replays/notsus.bitreplay" /tmp/notsus.ndjson

# expand a whole eval directory (ep??_*/replay.bitreplay -> replay_data/*.ndjson)
CREWRIFT_ROOT=... scripts/replay_dump/run_replay_dump.sh <eval_episode_dir>
```

Fresh production replays validate cleanly (99/100 of the 2026-06-11 top-ranked
eval; the one desync still yields a usable partial dump — `replay_dump` uses
`mismatchQuit` semantics and records the fail tick in the `end` line).

## Output format (NDJSON, one JSON object per line, keyed by `k`)

| `k` | contents |
|-----|----------|
| `meta` | config subset (`killCooldownTicks`, …) + map geometry (rooms/tasks/vents/button) |
| `roster` | per player: index, `slot` (joinOrder == ereq participant `position`), `name` (participant `player_name`), color, role, alive, assigned tasks — re-emitted on joins and phase changes; the last one has authoritative end-of-game roles |
| `phase` | phase transition at tick `t` |
| `t` | one Playing tick: `"p": [[slot, x, y, alive], …]` for all players |
| `body` | new body: `victim`/`killer` slots + world `x`/`y` at the kill |
| `e` | `expand_replay` event rows (`kill`, `vote_cast`, `vote_called_*`, `entered_room`, `completed_task`, `chat`, `score`, …) |
| `end` | tick count, `hash_failed`/`fail_tick`, winner, per-player final state |

Slot ↔ policy mapping: replay join `slot` equals the ereq `participants[i].position`,
and the replay join name equals `player_name` — map each slot to its policy via
the episode's `ereq.json`.

## Downstream pipeline (worked example)

`players/crewrift/crewborg/episode_data/eval_2026-06-11_topranked/replay_analysis/`
contains the full pipeline this tool was built for, runnable in order:

1. `fetch_replays.py` — download each episode's `replay_url` (public S3,
   zlib) → `ep??_*/replay.bitreplay`
2. `run_replay_dump.sh` (this dir) — expand to `replay_data/*.ndjson`
3. `build_cache.py` — NDJSON → numpy caches + `episodes.json`
4. `analyze.py` — correlation stats (death forensics, imposter models,
   proximity/isolation, vote matrices, crew routes) → `stats.txt`/`stats.json`
5. `make_figures.py` — map-grounded PNGs (needs `uv run --with matplotlib`)

Findings from the first run: `eval_2026-06-11_topranked/REPLAY_FINDINGS.md`.
