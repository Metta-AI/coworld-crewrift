# Trace logs: crewborg's subjective game record

crewborg writes a per-tick **JSON-lines trace** — its own point of view, tick by
tick: what it **perceived**, what it **believed** (suspicion over the roster), the
**mode** it chose, and the **command** it sent. This document is the reference for
that trace's *format* and for reading a finished game from it.

It is the cross-cutting observability reference. For what the recorded values
*mean* in gameplay terms, follow the per-area docs: belief/perception in
[`./perception-and-belief.md`](./perception-and-belief.md), suspicion posteriors in
[`./suspicion.md`](./suspicion.md), the modes in
[`./imposter-play.md`](./imposter-play.md) and
[`./crewmate-play.md`](./crewmate-play.md), meetings in
[`./meetings.md`](./meetings.md), occupancy tracking in
[`./agent-tracking.md`](./agent-tracking.md), routing in
[`./navigation.md`](./navigation.md), and the LLM commander in
[`./commander.md`](./commander.md). The package orientation is
[`../README.md`](../README.md); the settled architecture and the canonical event
catalogue are in [`../design.md`](../design.md).

The trace is **crewborg-specific**. Its game-level events are emitted by
`events.py:CrewborgEventTracer`, wired as the runtime's `on_step_complete` hook in
`__init__.py:build_runtime`. The env-derived filtering lives in `trace.py`; output
destinations and the JSONL envelope are owned by the shared `players.player_sdk`
(`TraceOutputs`), invoked from `coworld/policy_player.py:build_trace_outputs`.

---

## Where the trace lands

Output targeting is the env var `CREWBORG_TRACE_OUTPUTS` (read in
`coworld/policy_player.py`). It is a comma-separated list of `format@destination`
specs parsed by the SDK; crewborg sets two constants around it:

| | Value | Meaning |
|---|---|---|
| Default | `jsonl@artifact` | Traces/metrics stream to a temp file, then are zipped and uploaded to `COWORLD_PLAYER_ARTIFACT_UPLOAD_URL` when the bridge exits — a per-slot **player-artifact zip**, not subject to Observatory's policy-log line cap. |
| Fallback | `jsonl@stderr` | Used when no artifact upload URL is present (the bridge is running outside a Coworld runner). Same JSONL content, written to **stderr** and captured as the per-episode policy log. |

`coworld/policy_player.py:build_trace_outputs` tries the artifact destination first;
the SDK raises `ValueError` when the upload URL is missing, and the bridge catches
that and falls back to `jsonl@stderr` rather than crashing before connect. The
upload happens in `outputs.close()`, which the bridge runs inside a `with` block so
it fires before the container exits.

The records are identical either way — only the destination differs.

## Line format

One JSON object per line. Two record shapes, distinguished by their keys:

```json
{"kind":"trace","tick":3420,"event":"domain.decision_snapshot","name":"domain.decision_snapshot","data":{"mode":"normal","role":"crewmate", ...}}
{"kind":"trace","tick":3442,"event":"domain.phase_change","name":"domain.phase_change","data":{"from":"Playing","to":"Voting"}}
{"kind":"metric","metric_kind":"counter","name":"vote_cast","value":1.0,"tags":{}}
```

- **Trace lines** carry `tick`, `event` (and a redundant `name` with the same
  value), and a `data` payload object.
- **Metric lines** carry `metric_kind` (`counter` / `histogram` / `gauge`), `name`,
  `value`, and `tags`, and have **no** `tick` or `event`.

Event names without a prefix (`perception`, `belief_updated`, `action_intent`,
`act_command`, `snapshot_submitted`, `mode_*`, …) are SDK **framework** events;
game-level events emitted by `events.py` are prefixed **`domain.`**.

**`tick` is the engine's ground-truth tick.** The bridge reads the engine's streamed
`tick`-marker sprite and drives the SDK runtime from it (`coworld/policy_player.py`,
`scene.server_tick()`), so every trace and metric `tick` is the **server** tick —
directly alignable to the objective replay timeline. Only the first few frames,
before the marker arrives, fall back to the local message counter.

## The two workhorse records

Two `domain.*` records carry most of what you want when reading a game.

### `domain.decision_snapshot` — the per-tick audit

One record per tick (under debug, or when narrowly targeted; see
[Controls](#trace-controls)). It ties perception, belief gates, and action together
for that tick. Built by `events.py:_decision_snapshot_payload`. Top-level keys:

| Key | Contents |
|---|---|
| `phase`, `role` | Game phase; `crewmate` / `imposter` / `dead` (`null` at the terminal `GameOver` tick). |
| `mode` | The active mode name. |
| `intent` | `{kind, point, target_color, target_id, task_index, reason}` — the mode's chosen intent. |
| `command` | `{held_mask, buttons, chat}` — the wire command; `buttons` is the decoded mask (`up`/`down`/`left`/`right`/`a`/`b`), `chat` is a bool. |
| `self` | `{x, y}` — crewborg's world position. |
| `visible_players[]` | `{color, xy, life_status, suspicion, believed_imposter, confirmed_imposter}`. `suspicion` is P(imposter) ∈ [0,1] as a **crewmate**, and `null` as the **imposter** (it scores suspicion only as a crewmate). |
| `visible_bodies[]` | `{id, color, xy}`. |
| `threats[]` | Every **believed or confirmed** imposter, visible or not: `{color, p, believed, confirmed, visible, life_status, last_seen_tick, age_ticks, xy, dist, dist_sq, tailing_self}`. |
| `task` | Present only for a `complete_task` intent: `{task_index, valid, visible, completed, active_progress_pct, rect, inside, goal, anchor, dist}`. |
| `accuse` | Present only for a `call_meeting` intent: `{active, target_color, target_p, target_visible, target_last_seen_tick, button_xy, button_dist, button_dist_sq}`. |
| `nav` | `{route_goal, route_cursor, route_len, next_waypoint}`. |
| `voting` | Present only during `Voting`: `{cursor_slot, cursor_on_skip, candidates, vote_confirmed}` — a cursor that never advances under repeated `down` presses is the vote-timeout signature. |

`CREWBORG_TRACE_DECISION_FIELDS` narrows the payload to a chosen subset of these
top-level keys (`schema_version` is always kept).

### `domain.suspicion_snapshot` — why a meeting voted

One record at the start of every meeting (both live roles), emitted by
`events.py:_observe_meeting_suspicion`. It is the single record that explains a
vote after the fact. Keys:

| Key | Contents |
|---|---|
| `role` | A crewmate's genuine belief, or an imposter's deflection view over non-teammates. |
| `prior` | The base P(imposter) prior at meeting time. |
| `ranking[]` | `{color, p, confirmed, events[]}`, sorted by descending posterior `p`. `events[]` is each suspect's compact log (`{kind, dur, target, region, min_dist}`). |
| `confirmed[]` | Colors witnessed killing/venting. |
| `believed[]` | Colors over the flee bar. |
| `would_vote`, `would_vote_p` | The suspect `top_suspect` returns against the vote bar, and its posterior. |
| `vote_bar` | The `VOTE_PROBABILITY` threshold the meeting compares against. |

**Training capture** (`CREWBORG_TRACE_SUSPICION_FEATURES=1`, off by default): each `ranking[]` entry
additionally carries `features` — the exact `_fitted_features` vector the model scores — plus
`seen_ticks`, and each `events[]` entry carries `end_tick`. These are the raw inputs needed to refit
and parity-check the suspicion model on crewborg's *runtime* features (closing the train→serve gap;
see [`./suspicion.md`](./suspicion.md) §8).

What the posteriors *mean* and how `top_suspect` / the vote bar work is
[`./suspicion.md`](./suspicion.md); the meeting flow itself is
[`./meetings.md`](./meetings.md).

## Event families

`events.py:CrewborgEventTracer` emits the families below. The **lean default**
stream keeps the durable game events plus low-volume framework boundary events; the
heavy/per-tick families (marked) are emitted only under `CREWBORG_TRACE=debug` /
`viewer` or when narrowly targeted. `trace.py:lean_trace_filter` and
`NOISY_DOMAIN_EVENTS` define that partition.

### Always-on game events

| Event | Key fields |
|---|---|
| `domain.phase_change` | `from`, `to` |
| `domain.role_resolved` | `role` |
| `domain.body_sighted` | `body_id`, `color`, `world_x`, `world_y` |
| `domain.task_started` | `task_index` |
| `domain.task_completed` | `task_index`, `crew_tasks_remaining` |
| `domain.chat_sent` | `text` |
| `domain.chat_received` | `meeting_id`, `speaker_color`, `text`, `chat_tick` |
| `domain.vote_cast` | `{}` (the tick the ballot is confirmed) |

### Imposter-only action/kill events

| Event | Key fields |
|---|---|
| `domain.kill_attempted` | `target_id` |
| `domain.kill_landed` | `world_x`, `world_y` |
| `domain.report_attempted` | `body_id` |
| `domain.vent_attempted` | `{}` |
| `domain.kill_ready_changed` | `ready`, `ready_since_tick`, `last_kill_tick`, `urgency_ticks`, `has_trackable_victim`, `mode` (fires on each cooldown↔ready edge) |

### Knowledge layer (the reasoning behind the actions)

| Event | Key fields |
|---|---|
| `domain.player_event` | `color`, `kind`, `start_tick`, `target_color`, `region_index`, `min_dist` — one per newly opened observation interval (room/task/vent/near_body/tailing_self/…) |
| `domain.player_died` | `color`, `source`, `death_tick`, `body_xy` |
| `domain.imposter_confirmed` | `color`, `p` (a color entered the witnessed set) |
| `domain.believed_changed` | `added`, `removed`, `believed` (the over-the-bar set moved) |
| `domain.suspicion_snapshot` | see [above](#domainsuspicion_snapshot--why-a-meeting-voted) — one per meeting |

### Occupancy tracking

| Event | Key fields |
|---|---|
| `domain.occupancy_substrate` | `anchors`, `polylines`, `grid_cells`, `cell_size` (once, when the grid is built) |
| `domain.occupancy_reacquired` | `color`, `predicted_cell`, `actual_cell`, `predicted_point`, `actual_point`, `top_probability`, `distance_error`, `disc_radius` (a lost player re-seen) |
| `domain.occupancy_seek_target` | `cell`, `point`, `expected`, `tracked`, `support_cells` (imposter only — where it is hunting) |

What occupancy means is [`./agent-tracking.md`](./agent-tracking.md).

### Meeting decision and LLM events

The deterministic meeting path commits one `domain.meeting_decision`
(`modes/attend_meeting.py:_trace_meeting_decision`) — the headline meeting
diagnostic:

| Key | Contents |
|---|---|
| `role` | `crewmate` / `imposter` |
| `path` | crewmate: `accuse` / `vote_no_chat` / `silent_skip`; imposter: `proactive` / `bandwagon` / `skip` |
| `target` | The accused/voted color (or `null`) |
| `fabricated` | True when an imposter's bandwagon evidence is fabricated |
| `top_suspect` | The current leading suspect |
| `votes` | (imposter) vote tally against each color — the heat that drove it |
| `chat_accusers` | (imposter) per-color count of chat accusers |
| `nlp` | (imposter) the chat-NLP state (`ready`/`loading`/`disabled`/`failed`) |

When the LLM meeting layer is enabled, `modes/attend_meeting.py` also emits
`domain.meeting_context_serialized` (`trigger` + the full serialized dossier —
**large**), `domain.meeting_llm_decision` (`trigger`, `model`, `latency_ms`,
`usage`, `decision`), `domain.meeting_llm_debug` (raw request/response),
`domain.meeting_tentative_vote`, and `domain.meeting_llm_fallback` (`reason` +
detail) on each fallback to the deterministic path. Both paths emit
`domain.meeting_chat_selected` (`text`, `reason`) and `domain.meeting_vote_selected`
(`target`, `reason`) as the chat/vote actually goes out. The meeting machinery is
[`./meetings.md`](./meetings.md).

### Framework boundary events

The SDK emits these directly. The low-volume ones (`mode_entered`, `mode_exited`,
`mode_completed`, `mode_stalled`, `directive_rejected`, `strategy_inferences`) are in
the lean default; the per-tick ones (`perception`, `belief_updated`,
`action_intent`, `act_command`, `snapshot_submitted`, `strategy_evaluated`) are not.
The `action_intent` / `act_command` payloads are `repr()` strings — use
`decision_snapshot.data.intent` / `.command` for the structured form.

### Debug / viewer only

`domain.decision_snapshot` (above), `domain.suspicion_tick` (the entire live
P(imposter) vector every tick), `domain.kill_state` (imposter per-tick kill
context), `domain.occupancy_snapshot`, and the browser-viewer bootstraps
`domain.viewer_map` / `domain.viewer_occupancy_grid` / per-tick
`domain.viewer_frame`. Commander telemetry (`domain.commander_*`,
`domain.commander_applied`, `domain.commander_danger`) is also gated here; see
[`./commander.md`](./commander.md).

### Identity: colors, not slots

Players are **colors** almost everywhere. The slot↔color map appears only inside
`domain.meeting_context_serialized` (`voting.candidates[].slot`). crewborg's own
color is in that context's `self.color`; its own slot is the artifact/log filename.

## Trace controls

All read once at construction from the environment.

| Env var | Effect |
|---|---|
| `CREWBORG_TRACE` | Global verbosity. Empty = lean default. `debug` adds `decision_snapshot`, `suspicion_tick`, `kill_state`, `occupancy_snapshot`, commander, and viewer frames, and enables metrics. `viewer` adds the viewer-frame family. |
| `CREWBORG_TRACE_GROUPS` | Comma/space list of family names to admit *without* full debug volume. Names: `action`, `belief`, `chat`, `commander`, `debug`, `decision`, `framework`, `kill`, `knowledge`, `llm`, `meeting`, `mode`, `occupancy`, `state`, `suspicion`, `task`, `viewer`, `voting`, `all`, and the synthetic `lean`. Defined in `trace.py:TRACE_GROUP_PATTERNS`. |
| `CREWBORG_TRACE_INCLUDE` | Extra event-name globs to admit. A bare token also matches its `domain.`-prefixed form (`kill_landed` matches `domain.kill_landed`). |
| `CREWBORG_TRACE_SUSPICION_FEATURES` | Off by default. When set (`1`/`true`), `suspicion_snapshot` additionally emits, per suspect, the exact runtime feature vector (`features`) + raw inputs (`seen_ticks`, per-event `end_tick`) for refitting the suspicion model on runtime features (the train→serve-gap rework). |
| `CREWBORG_TRACE_EXCLUDE` | Event-name globs to suppress. **Exclude always wins** over any admit. |
| `CREWBORG_TRACE_DECISION_FIELDS` | Whitelist of `decision_snapshot` top-level fields to keep (e.g. `mode,intent,command`). |
| `CREWBORG_METRICS` | `1`/`true`/`yes`/`on` enables metric emission independently of `CREWBORG_TRACE` (metrics are also on whenever `CREWBORG_TRACE=debug`). |

Targeting precedence (`trace.py:TraceConfig.allows`): if any group or include glob is
set, admit events matching them; otherwise admit everything at `debug`/`viewer`, or
the lean default set. An exclude-glob match suppresses regardless. The same config
gates `events.py`'s optional/heavy families via `targets_event`, so
`CREWBORG_TRACE_GROUPS=decision` turns on `decision_snapshot` without full debug.

## How to read a game

Prefilter with `grep '^{'` — the final line of a stderr log is plain text
(`game over…`), not JSON, and the filter also drops any collector-error lines so
`jq` never chokes. Set `f=logs/policy_agent_7.log` (or the extracted
`telemetry.jsonl` from the artifact zip).

```bash
# 1. Event histogram — run this FIRST. It tells you which events are present,
#    so an empty result from a later query means "didn't happen," not "wrong file."
grep '^{' "$f" | jq -r 'select(.kind=="trace")|.event' | sort | uniq -c | sort -rn

# 2. Phase timeline — when meetings and rounds happened
grep '^{' "$f" | jq -c 'select(.event=="domain.phase_change")|{tick,from:.data.from,to:.data.to}'

# 3. The kills, votes, and deaths
grep '^{' "$f" | jq -c 'select((.event//"")|test("kill_|vote_cast|player_died"))|{tick,event,data}'

# 4. The suspicion snapshot per meeting — top suspects + whether it would vote
grep '^{' "$f" | jq -c 'select(.event=="domain.suspicion_snapshot")
  | {tick, would_vote:.data.would_vote, top:[.data.ranking[]|{c:.color,p:.p}][:3]}'

# 5. The meeting decision — which path and target it committed to
grep '^{' "$f" | jq -c 'select(.event=="domain.meeting_decision")|{tick,data}'

# 6. Why a mode/action was chosen at a given tick (needs decision_snapshot)
grep '^{' "$f" | jq -c 'select(.tick==3430 and .event=="domain.decision_snapshot")|.data'
```

To trace one decision end to end: find the kill or meeting in the histogram, read
the `suspicion_snapshot` and `meeting_decision` around it to see who crewborg
suspected and which path it took, then — if `decision_snapshot` is present — read the
per-tick `intent` / `command` to see exactly what it did and the `reason` string the
mode attached.

Metric lines have no `.event`, so always `select(.kind=="trace")` or guard
`(.event//"")` before `test()`. An empty `select` result is normal (that event
didn't occur) — confirm against the histogram from step 1.

## Reading caveats

- **Role-gated content.** A crewmate game has no `kill_*` events; an imposter game
  has no `suspicion_snapshot` and `null` `visible_players[].suspicion`. `role` reads
  `dead` for the stretch after crewborg dies.
- **stderr logs are capped** (~10k lines) and may be missing the **start** of the
  game — don't assume tick 0 is present. The artifact-zip `telemetry.jsonl` is not
  capped.
- **One episode can carry multiple crewborg versions**, all logging this same JSON
  shape. Identify crewborg's slot by name **and** version from the episode metadata,
  not by eyeballing which logs are JSON.
- **`meeting_context_serialized` is large** (the full LLM dossier). Exclude it when
  scanning meeting events unless you specifically want it.
</content>
</invoke>
