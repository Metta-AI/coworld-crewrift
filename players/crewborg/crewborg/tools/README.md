# crewborg tools — path-prediction analysis

Replay-driven tooling for the **path-prediction module**
(`crewborg.strategy.path_prediction`), which projects where a tracked
crewmate is heading — a probability distribution over candidate nav routes — so the
imposter's seeking logic can *follow a crewmate to their next room* once they leave
crewborg's view. These tools let you **see and score** that module on real replays
without running a live game.

There are three pieces:

| File | What it is |
| --- | --- |
| `replay_frames.py` | Loads one episode from a built **event warehouse** into per-tick ground-truth positions + crewborg's visibility windows + map geometry. Shared by the two tools below. |
| `path_prediction_ui.py` (+ `.html`) | A **live browser UI**: scrub/play a replay, pick an agent, watch its predicted routes (weighted by probability) sharpen as it moves and persist when it leaves view. |
| `path_prediction_eval.py` | **Offline scoring**: at every visible→obscured transition, compare the predicted destination to where the crewmate actually went. Emits a match-rate + CSV **and** per-instance overlay images. |

## Prerequisite: a built event warehouse (version-matched)

Both tools read a **crewrift-event-warehouse** dataset (per-tick `player_state`,
`player_visible_interval`, `map_geometry`). Build one from league rounds or from our
XP-request episodes — see `~/coding/role_repos/reporter_lab/crewrift-event-warehouse`.

⚠️ **expand_replay version coupling** (the #1 silent failure): the warehouse's
`CREWRIFT_EXPAND_REPLAY` helper must match the **exact crewrift commit** the arena ran,
or every replay hash-fails → sparse events (no chat/kills/bodies/intervals; check
`manifest.json` `trace_warning` counts first). **Coworld package version ≠ git tag** —
the versions (0.1.58, 0.3.9, …) are coworld package versions; the real source is a
commit. Find it from the deployed coworld's manifest: `coworld download <cow_id>` then
read `game.runnable.source_url` (`.../coworld-crewrift/tree/<commit>`). ⚠️ **PRIME is a FORK and its version moves fast** (`crewrift_prime`: 0.3.9 → 0.4.2 →
0.4.3 within days). Its `source_url` commit is often **non-public** (the fork's
commits aren't in the public `coworld-crewrift`), so you can't always `git checkout`
it. When the `source_url` SHA isn't in the repo, **find the matching commit
empirically**: build expanders from a few public-master candidates and test which one
gives `trace_complete:true` (no hash-fail) on the deploy's replays — *including a
button game* (button-meeting re-sim is the thing that diverges on a wrong commit). The
manifest calls Prime "config-only variants", so the sim usually matches a recent
public-master commit even though the SHA differs. The deploys (2026-06-25):
- **`crewrift_prime:0.4.3`** (current PRIME, where we compete; `source_url` `a3d1547`
  is NON-PUBLIC) ⇒ sim matches **master-tip `26ee08c`**, helper **`/tmp/expand-043`**
  (verified 63/63 `trace_complete`, button games expand cleanly with rounds
  continuing). **Use this for the v44 / current Prime episodes.**
- `crewrift_prime:0.3.9` (older Prime) ⇒ commit `20e3be4`, `/tmp/expand-prime039` —
  but it HASH-FAILS on every button game (re-sim diverges at the button vote); a
  later commit fixed that, so 0.4.3/master expand buttons fine.
- `crewrift:0.1.58` (regular league) ⇒ tag `0.1.59` (`1cbd4de`), `/tmp/expand-0159`.
- (older) `crewrift:0.1.54` ⇒ `42fed21`, `/tmp/expand-42fed21`.
To build: `git -C ~/coding/coworlds/coworld-crewrift fetch && git checkout <commit>;
nimby --global sync nimby.lock; nim c -d:release -d:useMalloc --opt:speed
--out:/tmp/expand-<ver> tools/expand_replay.nim`, then VERIFY `trace_complete:true` on
a real replay (and a button game) before trusting it. Find a replay's deploy via
`coworld_name` + `coworld_version` in `episode.json` (NOT the `source_url` SHA — it may
be non-public). Reusable 0.1.54 warehouses from earlier:
`/tmp/xp_imp_warehouse` (450 XP imposter episodes), `/tmp/crewrift_warehouse` (2 league rounds).

To build an event warehouse from `fetch_artifacts`-downloaded dirs, use the
**`crewrift-event-warehouse`** skill (`scripts/build_warehouse.py` ingests episode dirs / IDs
directly — the old `make_wh_input.py` adapter is retired).

## `path_prediction_ui.py` — live prediction viewer

```sh
# list episodes in a warehouse:
uv run --with duckdb python crewborg/tools/path_prediction_ui.py \
  --warehouse /tmp/xp_imp_warehouse
# serve one episode (then open http://localhost:8810):
uv run --with duckdb python crewborg/tools/path_prediction_ui.py \
  --warehouse /tmp/xp_imp_warehouse --episode ereq_104afabe-6cc8-4be0-b763-4e2d1f3ed613
```

In the page: **agent dropdown** picks the crewmate to predict; **blue routes** are
candidate destinations with opacity/width ∝ probability; the **◇** marks the top
route's predicted position; the **VISIBLE/occluded** tag shows whether crewborg can
see the target this tick (the module is fed *only* visible sightings, so watch the
prediction coast when it flips to occluded). **Gold ring** = crewborg (slot 0),
**white ring** = the selected agent. Scrub the timeline or press **▶ play**.

Notes: predictions are computed server-side per agent on first selection (then
cached); the first dropdown change for a new agent takes ~a second. The nav graph is
crewborg's *real* baked croatoan mask (falls back to a room-rect-union only if the
bake can't load).

## `path_prediction_eval.py` — accuracy at visible→obscured transitions

The moment a crewmate leaves view is when prediction matters. For each such
transition this captures the prediction *at onset*, then uses ground truth to score
it **two** ways:

- **Next-room match** — did the predicted top destination's room equal the **first
  new room the crewmate actually entered** (`inside_room` flips in a room ≠ the onset
  room)? This is the fair "follow them to their next room" target — far better than
  scoring the room they're in 1–2 rooms later.
- **Path reward (−1..+1)** — a **decaying, hallway-weighted** agreement between the
  predicted and actual paths, aligned by arc-length from the shared onset point.
  Getting the *early corridor* right is rewarded heavily; far-out divergence (the
  exact final room) is forgiven. **This is the headline metric** — it measures
  whether we'd chase down the right hallway, which is what actually matters. A
  positive median means we ride the right corridor more often than not, even when the
  room-name match is low (≈38% of "room misses" are actually right-hallway).

```sh
uv run --with matplotlib --with duckdb python \
  crewborg/tools/path_prediction_eval.py \
  --warehouse /tmp/xp_imp_warehouse --episodes 20 --images 40 --out /tmp/pred_eval
# then open /tmp/pred_eval/report.html
```

Knobs: `--episode <id>` (single) or `--episodes N` (deterministic sweep, so reruns
compare cleanly); `--min-occlusion 24` (ignore blinks); `--horizon 240` (window the
next-room must be entered within); `--images N` (sampled overlay PNGs, biased toward
room-changers). Outputs to `--out`: **`report.html`** (self-contained — write-up,
result cards, calibration table, embedded images, full instance table),
`instances.csv` (one row per occlusion), and `images/` (overlay per instance:
**orange** = actual path, **blue** = predicted weighted routes, white dot = onset,
◇/★ = predicted/actual destination). stdout also prints match rate **by confidence
bucket** — calibration should stay monotonic. (Baseline 2026-06-24, 6 eps: next-room
41% / path reward +0.26 / 79% in the top confidence bucket.)

## Tuning the module

Knobs live at the top of `strategy/path_prediction.py` (each documented there):
`HEADING_WINDOW_TICKS`, `ALIGN_GAIN`, `EVIDENCE_DECAY`, `LOOKAHEAD_PX`, `REFRESH_DIST`,
`REACQUIRE_DIST`, `CREW_SPEED_PX`. The path-reward shape (`PATH_DECAY_LEN`,
`PATH_ERR_SCALE`, …) lives in the eval tool — it defines the *metric*, not the
predictor. Loop: change one knob → re-run the eval → watch the **path reward** and
**calibration** in `report.html` → eyeball a few miss images for *how* it fails.
Unit tests pin the qualitative behavior: `tests/test_path_prediction.py`.
