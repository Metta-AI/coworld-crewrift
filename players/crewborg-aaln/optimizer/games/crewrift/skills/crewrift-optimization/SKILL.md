---
name: crewrift-optimization
description: Crewrift-specific grounding for the optimizer loop — scoring constants, role-by-color census, the trace.db artifact schema and server_tick replay join, .bitreplay reconstruction via the Nim simulator, fetch/build/submit commands, CREWBORG_* trace flags, the -100 disconnect taint rule, map/navigation facts, and the replay viewer. Use when optimizing a Crewrift policy (e.g. crewborg) and you need the game's concrete commands, schema, and constants.
---

# Crewrift Optimization (game playbook)

Crewrift is an Among Us–style social-deduction game (crewmates do timed tasks and
vote; imposters kill, vent, and blend in). This playbook is the **game-specific
grounding** for the optimizer loop on a Crewrift policy — the concrete commands,
schema, and constants the top-level methodology skills reference. Run the loop
itself from those skills (routed in `../../../../AGENTS.md`).

Crewrift is a **spatial + temporal map game with momentum-based navigation**, so
`spatial-temporal-analysis` and `map-navigation` are both first-class here.

> The reference policy is **crewborg** (`players/crewrift/crewborg/` in the
> `players` repo). Paths below are in that repo; verify any constant against the
> cited source before relying on it — the game is in active development.

## Game facts & scoring (the objective)

24 FPS (`TargetFps=24`). 8 players / 2 imposters default (imposter count
auto-scales `(players−3)//2`). Phases: `Lobby → RoleReveal → Playing → (Voting →
VoteResult)* → GameOver`. **Inputs only act during `Playing` and `Voting`.**

| Reward | Value | | Constant | Value |
|---|---|---|---|---|
| task complete | +1 | | `KillRange` | 20px (dist²≤400) |
| kill | +10 | | `KillCooldownTicks` | GameInfo-advertised (learned live; old 900 is stale) |
| win (each winner) | +100 | | `VentRange` | 16px (dist²≤256) |
| vote timeout (per non-voter) | −10 | | `ReportRange` | 20px |
| stuck (idle crewmate) | −1 | | `TaskCompleteTicks` | 72 (3s hold A) |
| map size | 1235×659 | | `VoteTimerTicks` | 240 (10s) |
| screen/camera | 128×128 | | `TasksPerPlayer` | 8 |

Win: crew win if all imposters dead **or** all tasks done; imposters win when
alive imposters ≥ alive crewmates. Constants live in the `sim.nim` const block
(`defaultGameConfig`), all config-overridable. The **points asymmetry** of
`eval-variance-design` matters here: +10/kill and +100/win dominate +1/task, so a
"league points" goal weights imposter performance and wins, not task counts.

## Roles by color — the cheap global truth

Crewrift assigns roles from a **time-based seed — there is NO color→role
correlation**, so you must read the actual census per episode. `domain.game_over`
(and the GameOver roster icons) give the **role census by color** for the whole
lobby from *any one* artifact in the episode. Combine with `SLOT_COLORS` (the
slot→color map) to label every slot's role without re-simulating. This is how
per-version win rates are computed cheaply (`roles_by_color` × `positions` ×
`outcome`), re-simulating only when per-event detail is needed.

This is the Crewrift instance of `eval-aggregation` Step 3's "attribute slot →
role" — win = `(role==imposter and outcome==imps_win)` or `(role==crewmate and
outcome==crew_wins)`.

## Artifacts: the trace.db schema (`replay-artifact-analysis` grounding)

All logging lives in the **artifact**, not stderr (hosted logs are capped/reset;
artifacts persist, are pre-structured + indexed, and self-describing via an
embedded `README.md`). One `.zip` per slot = `trace.db` + `summary.json` +
`README.md` (code: `artifact.py`, `SqliteEpisodeRecorder`).

`trace.db` — three indexed tables:

| Table | One row per | Key columns |
|---|---|---|
| `traces` | trace event | `tick`, `event` (e.g. `domain.vote_cast`), `data` (JSON) |
| `metrics` | metric sample | `name`, `kind` (counter/histogram/gauge), `value`, `tags` |
| `positions` | tick (24 Hz) | `tick`, **`server_tick`** (replay join key), `self_x/y`, `room_id`, `mode`, `intent_kind`, `held_mask`, `phase`, `visible` (JSON LoS players) |

Domain events are `domain.*`; framework events are unprefixed. `summary.json`
holds row counts, `first/last_tick`, `event_counts`, and an `episode` block (slot,
role, color, outcome) — and is **always echoed to stderr** as a greppable block,
so the data survives even with no binary upload channel.

Read pattern (stdlib `sqlite3`, join the tables — no reformatting):

```python
import json, sqlite3, zipfile, tempfile
from pathlib import Path
def read_artifact(zip_path):
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extract("trace.db", td); summary = json.loads(zf.read("summary.json"))
        db = sqlite3.connect(str(Path(td) / "trace.db"))
        evs = lambda n: [(t, json.loads(d)) for t, d in db.execute(
            "select tick, data from traces where event=? order by tick", (n,))]
        out = {"summary": summary, "kills": evs("domain.kill_landed"),
               "votes": evs("domain.meeting_vote_selected"),
               "game_over": evs("domain.game_over"),
               "positions": db.execute(
                 "select server_tick, self_x, self_y, mode, intent_kind, phase, visible "
                 "from positions where server_tick is not null order by server_tick").fetchall()}
        db.close(); return out
```

Reference: `episode_data/eval_2026-06-11_v3_vs_v8/analyze.py`.

## Trace levels (what `data-collection-design` turns on here)

Raise the level *before* the episode (more rows = more to mine, bounded by
`MAX_ROWS_PER_TABLE`); match the level across baseline and variant arms:

- `CREWBORG_METRICS=1` — counters/gauges without full debug.
- `CREWBORG_TRACE=debug` — full framework stream + metrics + `decision_snapshot`
  + per-tick `suspicion_tick` / `kill_state` / `occupancy_snapshot`.
- `CREWBORG_TRACE=viewer` — adds `viewer_*` frames for the replay viewer.
- Targeted: `CREWBORG_TRACE_GROUPS=voting,occupancy`,
  `CREWBORG_TRACE_INCLUDE=meeting_*,vote_cast`.

Binary artifact upload uses `COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` (presigned PUT,
or `file://` locally), ≤200MB/slot; otherwise the stderr `summary.json` block is
the fallback.

## Eval commands (`eval-variance-design` Step 4 grounding)

```sh
# build + upload + submit (RECORD the pinned policy-version id for each arm)
players/crewrift/crewborg/build.sh
coworld upload-policy crewborg-aaln:latest --name crewborg-aaln   # [+ --use-bedrock]
coworld submit crewborg-aaln:<tag> --league <crewrift-league-id>

# pull the episodes just played (replays + per-slot traces + roster)
players/crewrift/crewborg/scripts/fetch_episodes.sh -n 100        # or fetch_episodes.py
```

`fetch_episodes` writes `episode_data/<timestamp>_<id8>/` per episode with
`episode.json`, `episode_request.json` (roster: slot → policy/version + scores),
`replay.json` (the `.bitreplay`), and `logs/crewborg_slot{N}_v{V}.log`. Seats are
seed-assigned, so for head-to-head put multiple slots of each arm in one lobby
with rotating seats (the `v3_vs_v8` shape). Local iteration: `coworld
run-episode` (see crewborg AGENTS.md workarounds: re-download manifest + delete
`slots.items.properties.name`, pass `--run .../coworld/entrypoint.sh`).

## Replay reconstruction (`.bitreplay` + the server_tick join)

A Crewrift `.bitreplay` is **per-tick input masks the Nim simulator re-runs**, not
stored frames — there is **no Python decoder**. Reconstruction shells out to a
local game checkout via `tools/expand_replay.nim --json`, which emits ground-truth
events (kills w/ victim slot, votes w/ target, task completions, phase changes),
each carrying `ts` = the server tick.

```sh
# one-time: coworld ≥0.1.22, Nim 2.2.10, local coworld-crewrift checkout
cd <coworld-crewrift> && nimby use 2.2.10 && nimby sync -g nimby.lock
export PATH="$HOME/.nimby/nim/bin:$PATH" CREWRIFT_ROOT=<coworld-crewrift>

# reconstruct (+ join crewborg's per-tick mode/intent to replay events)
uv run python players/crewrift/crewborg/scripts/replay_analysis.py \
  episode_data/<ts>_<id8> --trace-db logs/ereq_.../trace.db --slot 2 -o report.json
```

The join key: replay event `ts` == artifact `positions.server_tick` (both are the
same counter; `server_tick` comes from the game's invisible `tick <N>` sprite
marker). This is *the* reason `positions` records `server_tick`. Older artifacts
predate the `positions` table — the join is skipped, fall back to ground truth.
The report carries `slot_stats`, `crewborg_opponent_correlation`, and (with
`--trace-db`) `trace_joins`.

## Taint rule (`eval-aggregation` Step 2 grounding)

A disconnect / no-show scores the **whole lobby −100** (usually a cold-node
image-pull timeout, not gameplay). Exclude any episode with a slot at −100 or an
artifact showing 0 ticks, **and report the disconnect rate** (historical
background ≈5–12%; a spike is a deploy bug, not a policy result). Outcomes to keep
are `imps_win` / `crew_wins`. Behavioral corroboration metrics: kills/ep,
tasks/ep, vote accuracy.

## Watching a replay (eyeball ground truth)

`coworld replay` is **broken for Crewrift**. Launch the game image in playback:

```sh
docker run -d --name crewrift-replay -p 127.0.0.1:52100:8080 \
  -e COGAME_LOAD_REPLAY_URI=file:///coworld-replay/replay.json \
  -v "$EPISODE_DIR":/coworld-replay:ro "$GAME_IMAGE"
# open http://127.0.0.1:52100/client/replay
```

## Map & navigation facts (`map-navigation` + `spatial-temporal-analysis` grounding)

- Only map is **croatoan**; rooms/vents/tasks are baked from
  `data/croatoan.resources` (server-side, not streamed); the emergency button is
  *derived* (28×34 rect on the bridge center). Decoded walkability validates the
  bake — a mismatch means a different map (fail loud).
- The nav graph (`nav.py`, once/episode) is built over the walkability alpha:
  coarsened to **8px cells** for A* speed but validated at pixel resolution;
  reachability is a pixel flood from spawn; a **clearance radius** (eroded mask)
  keeps routes off walls; **destination anchors** are precomputed per
  task/vent/button; **vent teleport edges** join same-group vent anchors and are
  **imposter-only**.
- Movement is **momentum-based** (`Accel=76`, friction `144/256`, `MaxSpeed=704`,
  `MotionScale=256`), owned by the action layer with a bang-bang controller +
  predictive stop; the route re-roots at the live position every
  `REPLAN_INTERVAL`.
- For `spatial-temporal-analysis`: bin `self_x/self_y` into the 1235×659 map;
  `positions.visible` is the per-tick line-of-sight set; align kills/votes to the
  position track on `server_tick` for encounter geometry and time-gradient
  trajectories.

## Experiment flags

`CREWBORG_BE_DUMB` (aggressive imposter), `CREWBORG_DICK_MODE` (one-shot crewmate
button rush), `CREWBORG_LLM_MEETINGS` — the cheapest A/B surface. The v2 baked-in
values, flip mechanics, and per-flag tradeoffs are in `guide/SKILL.md` and
`CREWBORG_INSIGHTS.md` §4. Match the flag set across eval arms.

## Grounding sources (crewborg repo)

`design.md` (§6 map/nav, §9 action layer, §11 artifacts), `AGENTS.md` (constants,
Sprite v1, `tick <N>` marker), `artifact.py`, `scripts/fetch_episodes.py`,
`scripts/replay_analysis.py`, `episode_data/eval_2026-06-11_v3_vs_v8/analyze.py`,
`episode_data/FINDINGS_v4.md`.
