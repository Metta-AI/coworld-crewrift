# Event warehouse — the event catalog (how to read the `events` table)

Every row in the `events` fact table is one event: `(ts, episode_id, slot, policy_version,
policy_name, role, key, value)`. **`key`** is the event type (and the Parquet partition); **`value`**
is a JSON string you read with `json_extract_string(value,'$.field')` (text) or
`json_extract(value,'$.field')::int|double` (numbers). This catalogs every `key` and its `value`
fields, so you can write queries without guessing.

> **Verified** against the extractor source 2026-06-27: raw keys from
> `coworld-crewrift/tools/expand_replay.nim`; reporter metadata from
> `crewrift-event-reporter/.../events.py`; derived keys + thresholds from `.../analysis.py`; the fact
> schema from `crewrift-event-warehouse/.../schema.py`. Re-derive there if a field looks off.

## The two universals

- **`slot` ≥ 0** = the row is attributed to that player; its `policy_name` / `policy_version` / `role`
  are already filled in (the warehouse joined the `episode_players` dimension). **`slot = -1`** = a
  global / episode-level row with NULL identity. **Always `WHERE slot >= 0`** for player aggregates.
- **Embedded slots in `value`** (a victim, a follow target, …) are NOT resolved — they're raw slot
  ints. To get that party's role/policy, **self-join `episode_players` on `(episode_id, <that slot>)`**
  (see the cheat sheet at the bottom).
- Every `value` also carries `schema_version`, `source` (`"replay"`/`"reporter"`/`"derived"`),
  `confidence`, usually `episode_id` and `phase`. Filter on `$.source` when a key has two variants.

## Movement & rooms (per-actor)

| key | meaning | key `value` fields |
|---|---|---|
| `entered_room` | actor entered a room | `room` |
| `left_room` | actor left a room | `room` |
| `player_state` | **sampled position snapshot** (every `snapshot_every` ticks) — backbone of all derived proximity | `x, y, vel_x, vel_y, room, inside_room, alive, connected, active_task, task_progress, assigned_tasks, kill_cooldown, vent_cooldown, button_calls_used, reward, role` |
| `headed_to` | inferred travel toward a room (derived) | `target_kind`(="room"), `target_name`, `origin_room`, `tick_start`, `tick_end` |
| `arrived_at` | the matching arrival (derived) | `target_kind`, `target_name`, `tick_start`, `tick_end` |

## Proximity / following / isolation (derived intervals)

Common interval fields: `tick_start, tick_end, last_observed_tick, duration_ticks, boundary_precision,
ended_by, sample_count, min_distance, median_distance, max_distance, rooms`.

| key | slot | meaning + distinctive fields | embedded slots |
|---|---|---|---|
| `proximity_interval` | **−1 (global)** | a pair stayed within `near_distance` (32px) | `player_a`, `player_b` |
| `isolation_interval` | **−1 (global)** | a proximity interval where the pair was alone ≥80% of samples (`isolated_fraction`) | `player_a`, `player_b` |
| `following_interval` | follower | actor tailed someone: `alignment_ratio`, `lag_ratio` | **`target`** (followed) |
| `chase_interval` | chaser | a following interval where the gap closed ≥8px: `start_distance`, `end_distance` | **`target`** |
| `near_body_interval` | observer | a living non-victim lingered near a corpse | **`victim_slot`** (whose body) |

## Kills / bodies

| key | slot | meaning | embedded slots |
|---|---|---|---|
| `kill` | killer | a murder | **`victim_slot`** (+`victim_label`) |
| `body` | victim (or −1) | a corpse became known | — (`label`, `room`) |
| `died` | actor | died with no body registered that tick (e.g. ejection) | — |
| `revived` | actor | returned to alive | — |
| `body_state` | victim (`victim_slot`) | sampled corpse snapshot | **`killer_slot`** (+`kill_tick`, `x`, `y`, `room`) |

## Meetings / votes / chat

| key | slot | meaning | embedded slots |
|---|---|---|---|
| `vote_called_body` | caller | meeting called by reporting a body | **`body_owner_slot`** (the victim) |
| `vote_called_button` | caller | emergency-button meeting | — |
| `vote_cast` | voter | a vote. **Skip** → `target="skip"` (string, no slot). **Player** → `target_slot` (+`target_label`), no `target` key | **`target_slot`** (when not skip) |
| `chat` | speaker | a voting-phase chat message — the only free text | `text` |
| `chat_suss` | speaker | (from the `suss` subcommand) a chat labelled with who it accuses | `suss_target_slot`, `suss_target_role`, `suss_target_policy`, `is_suss`, `target_is_imposter` |

## Tasks (per-actor)

| key | meaning | `value` fields |
|---|---|---|
| `started_task` | began a task | `task` (int id → `map_geometry.tasks[].id`) |
| `completed_task` | finished a task | `task`, `while_dead` (crew can finish as a ghost) |
| `task_attempt` | a started→ended span (derived) | `task`, `outcome`(`completed`/`abandoned`), `reason`, `tick_start`, `tick_end`, `duration_ticks` |

## Visibility (raw, per-actor observer) — only when `snapshot_every > 0`

| key | meaning | `value` fields |
|---|---|---|
| `player_visible_interval` | observer had another player in rendered view | `target_slot` (seen), `target_role`, `room`, `tick_start`, `tick_end`, `duration_ticks`, `visibility_basis`, `ended_by` |
| `body_visible_interval` | observer saw a corpse | `target_kind`="body", `target_slot`, `room`, interval fields |

## Score & lifecycle / metadata (mostly global)

| key | slot | `value` fields |
|---|---|---|
| `score` | actor | `amount` (signed), `reason` (`killing`/`completing task`/`winning`/`failing to vote or skip`/`standing still`) |
| `episode_metadata` | −1 | two variants by `$.source`: `reporter` (`request_id`, `report_uri`, `generated_at`) · `replay` (`config{kill_range, kill_cooldown_ticks, vote_timer_ticks, imposter_count, tasks_per_player, …}`) |
| `map_geometry` | −1 | `map_name, width, height, rooms[], tasks[]{id,name,room,x,y}, vents[], button, home` |
| `player_manifest` | ≥0 | two variants by `$.source`: `reporter` (`player_id`, `display_name`) · `replay` (`label`, `color`, `role`, `assigned_tasks`) |
| `phase` | −1 | `phase` (transition) |
| `trace_warning` | −1 | `message`, `fail_tick` — **the version-skew signal**; many of these ⇒ rebuild the expander |
| `trace_complete` | −1 | `complete` (bool), `tick_count` or `fail_tick` |

## Derived-event definitions & thresholds (`analysis.py:AnalysisConfig`, env-overridable)

| concept | rule | threshold (env) |
|---|---|---|
| **near** (`proximity_interval`) | ≥2 consecutive ticks both alive & `distance ≤ near_distance`, lasting ≥ `min_interval_ticks` | `near_distance`=32 (`CREWRIFT_EVENT_NEAR_DISTANCE`) |
| **isolated** (`isolation_interval`) | a near-run where no 3rd living player is within `group_distance` of either, ≥80% of samples | `group_distance`=44 (`CREWRIFT_EVENT_GROUP_DISTANCE`) |
| **following** (`following_interval`) | within a near-run: `alignment_ratio ≥ 0.6` and `lag_ratio ≥ 0.35` over ≥3 moving samples | — |
| **chase** (`chase_interval`) | a following interval where `start_distance − end_distance ≥ 8` | — |
| **near body** (`near_body_interval`) | living non-victim within `body_distance` of a corpse ≥ `min_interval_ticks` | `body_distance`=36 (`CREWRIFT_EVENT_BODY_DISTANCE`); `min_interval_ticks`=24 |

## Slot-join cheat sheet — embedded slots to resolve

Self-join `episode_players p ON p.episode_id = e.episode_id AND p.slot = <field>::int` to get the
other party's `role`/`policy_name`. The **field name varies by key** — match it exactly:

| key | actor `slot` | embedded slot field(s) |
|---|---|---|
| `kill`, `near_body_interval` | killer / observer | `$.victim_slot` |
| `body_state` | victim | `$.killer_slot` |
| `vote_called_body` | caller | `$.body_owner_slot` |
| `vote_cast` | voter | `$.target_slot` (absent when `$.target='skip'`) |
| `player_visible_interval`, `body_visible_interval` | observer | `$.target_slot` |
| `following_interval`, `chase_interval` | follower/chaser | `$.target` |
| `proximity_interval`, `isolation_interval` | — (global) | `$.player_a`, `$.player_b` |
