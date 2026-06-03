# Crewrift Reporters

A **reporter** is a Coworld supporting runnable that turns one finished episode
into a human- and machine-readable report — narrative summaries, highlights,
statistics, event logs. Reporters are post-episode and on-demand: they are
**not** run by the episode runner. An episode produces its artifacts whether or
not any reporter ever runs against them.

See the upstream contract in the `coworld` package:

- Role: `packages/coworld/src/coworld/docs/roles/REPORTER.md`
- Input bundle: `packages/coworld/src/coworld/docs/artifacts/EPISODE_BUNDLE.md`
- Replay artifact: `packages/coworld/src/coworld/docs/artifacts/REPLAY.md`
- Output report: `packages/coworld/src/coworld/docs/artifacts/REPORT.md`

This directory holds Crewrift's own reporter(s). It mirrors the layout of
`players/`: each reporter lives in its own folder with an entry-point module and
a same-named submodule folder for its parts.

## scribe

`scribe` is the Crewrift reporter. It is being built bottom-up: first unbundle
the episode and decode its replay, then re-simulate that replay through the real
Crewrift simulator to recover a tick-aligned event timeline.

```
reporters/scribe/
  scribe.nim            entry point (provisional; see "Interface" below)
  scribe/
    bundle.nim          open an episode-bundle zip, parse manifest.json,
                        extract artifact entries
    report.nim          core: bundle -> decoded EpisodeReport (config + replay)
    driver.nim          replay re-simulation driver with hash validation
    identity.nim        stable player identity table
    events.nim          timeline event model
    probes.nim          sim-backed attribution probes
    detect.nim          per-tick event detection
    timeline.nim        extraction orchestration and text rendering
  README.md
```

### What it does today

1. Reads the episode-bundle zip into memory (`bundle.nim`).
2. Parses the bundle's root `manifest.json` and resolves the `replay` entry
   through the manifest (rather than hard-coding a path).
3. Decodes the replay bytes with Crewrift's own codec
   (`src/crewrift/replays.nim`) into a `ReplayData` — joins, leaves, inputs,
   and tick hashes — plus the `GameConfig` the episode ran with, recovered from
   the replay header.
4. Re-simulates the decoded replay tick by tick with `initSimServer` and
   `step`, validating every recorded `gameHash`.
5. Emits a human-readable event timeline with stable player identities for game
   start, playing start, kills, task completions, meetings, votes, ejections,
   voting chat messages, stuck penalties, vents, game over, and replay leaves.

The replay format is **game-owned**, so the reporter reuses the game's codec
instead of reimplementing the byte layout. That is the only way to stay correct
as the format evolves; the codec validates the `CREWRIFT` magic and format
version on the way in.

The event extractor also reuses game logic rather than duplicating rules. Most
events are state diffs between the pre-step and post-step sim snapshots; body
report attribution probes a cloned post-step `SimServer` with the exported
`tryReport` proc and the real pre-step body limit. Kill attribution uses the
simulator's persisted reward-account kill counters and appended body order.

### What is deliberately not built yet

- **Report artifact contents.** The timeline is printed to stdout for now. The
  real Coworld report zip contents — narrative, highlights, statistics, and
  machine-readable event logs — are the next design step.
- **The interface.** The Coworld contract hands a reporter an episode bundle via
  `COGAME_EPISODE_BUNDLE_URI` and expects a report zip at `COGAME_REPORT_URI`.
  That wiring is intentionally stubbed. For now `scribe.nim` takes a bundle zip
  path on the command line so the core can be exercised end to end. When the
  real interface lands, only `scribe.nim` should need to change — `bundle.nim`
  and `report.nim` operate on bytes and decoded state, not URIs.

## Build and run

Dependencies are resolved exactly like the game and the bots — install Nim and
sync the lock file with [nimby](https://github.com/treeform/nimby), then build
from the repo root:

```sh
nimby use 2.2.10
nimby sync -g nimby.lock

nim c reporters/scribe/scribe.nim
reporters/scribe/scribe.out path/to/episode-bundle.zip
```

Run the focused reporter tests with:

```sh
nim c -r reporters/scribe/test_timeline.nim
```
