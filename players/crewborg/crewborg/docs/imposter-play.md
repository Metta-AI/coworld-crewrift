# Imposter play

How the imposter converts a game into kills — the subsystem that spans the
per-tick mode selector, the four imposter modes, and the shared victim/witness
logic. This is the narrative tying those files together; for the structural spec
see [`../design.md`](../design.md) §7.2 and §10, and for orientation see
[`../README.md`](../README.md). Per-file behavior lives in each module's
docstring; this page is the cross-cutting view.

## The core thesis

The imposter's job is **conversion, not stealth**: the win is decided by getting
the *second* kill, not by killing perfectly unseen. Two imposters killing twice
is parity-breaking; an imposter that lands one careful kill and then dithers does
not win games. Everything below is shaped around opening and converting kill
windows, and the kill gate (see
[The kill/strike gate](#the-killstrike-gate)) deliberately relaxes — and after
the first kill, abandons — the witness requirement so the second kill actually
fires.

A standing rule follows from this: **imposters never report bodies.**
Self-reporting our own kill opens a meeting that resets the kill cooldown and
kills the snowball. The selector never routes an imposter to Report Body
(`modes/report_body.py`); after a kill the imposter leaves the scene (Evade)
instead.

## Pipeline at a glance

```
                 per tick
  Belief ──► RuleBasedStrategy._select_imposter  (the priority order)
                   │
                   ├─ Voting ──────────────► attend_meeting   (see ./meetings.md)
                   ├─ just killed ─────────► evade            (post-kill re-approach)
                   ├─ ready + visible victim► hunt             (commit ▸ intercept ▸ strike)
                   ├─ nearly ready + crew ──► recon            (pre-ready beeline)
                   └─ otherwise ───────────► search           (always-on seeking FSM)
                                                  │
                          each mode returns a symbolic Intent (navigate_to / kill / idle)
                                                  │
                                          action.py resolves it to a command
```

The selector only *chooses* a mode; the mode object turns belief into a symbolic
`Intent`; `action.py` turns the `Intent` into the actual movement/kill command.
The mode files never move the agent and never press buttons.

## The per-tick selector priority order

`strategy/rule_based.py:RuleBasedStrategy._select` dispatches by phase and role.
A live imposter in the `Playing` phase goes to
`strategy/rule_based.py:_select_imposter`, which applies this order (highest
priority first):

| # | Condition | Mode | Gate function |
|---|-----------|------|---------------|
| 1 | `phase == "Voting"` | `attend_meeting` | (handled in `_select`, before role dispatch) |
| 2 | killed within `EVADE_TICKS` ago | `evade` | `_recent_self_kill(belief)` |
| 3 | kill ready **and** a victim visible | `hunt` | `belief.self_kill_ready and has_visible_victim(belief)` |
| 4 | within `recon_window()` of ready **and** a crewmate has been seen | `recon` | `ticks_until_kill_ready(belief) <= recon_window() and most_recent_victim(belief) is not None` |
| 5 | otherwise (the literal `else`) | `search` | — no gate of its own — |

Order is the design contract (§10): Evade outranks Hunt so we never instantly
re-hunt over our own fresh body; Hunt outranks Recon because a ready kill on a
visible victim beats pre-positioning; Recon outranks Search because a tick-windowed
beeline to crew beats undirected seeking. Search is the always-on fallback that
fires whenever nothing above it does.

The selector re-evaluates every tick — there are no reflex transitions or sticky
mode state in the selector itself (the modes hold their own FSM state). The
meeting branch (Voting) is decided in `_select` before role dispatch, so it
applies to imposter and crewmate alike.

### Selector levers

| Lever | Source | Effect |
|-------|--------|--------|
| `EVADE_TICKS` | env `CREWBORG_EVADE_TICKS`, default `72` (~3s at 24 Hz) | Length of the post-kill Evade window (`_recent_self_kill`). |
| `CREWBORG_BE_DUMB` / `BE_DUMB` | env truthy | Replaces the imposter `Playing` order with only Hunt (ready + visible victim) / Search — skips Evade, Recon, Report Body. An isolation experiment for "always prepare to kill". |
| `skip_evade` | LLM commander (`strategy/commander/bias.py:commander_of`) | When the commander signals danger, suppresses Evade so we don't loiter near a body; logged to `belief.commander_danger_events`. See [`./commander.md`](./commander.md). |

The commander is optional and gated; when absent (`commander_of` returns `None`)
the deterministic order above is the whole story.

## Reconstructing the kill cooldown

The HUD exposes only a binary ready/cooldown bit — no countdown — so the
"how soon can I kill" used by gates 3–4 is reconstructed in
`strategy/opportunity.py`:

- `ticks_until_kill_ready(belief)` returns `0` when `self_kill_ready`; otherwise
  `kill_cooldown_start_tick + duration - last_tick`, where `duration` is the
  learned `kill_cooldown_estimate` or, before anything is measured,
  `DEFAULT_KILL_COOLDOWN_TICKS` (500 — Crewrift Prime 0.3.9, the target league;
  regular Crewrift uses 800). With no cooldown start observed yet it assumes a
  full cooldown remains, so a no-information imposter does not falsely enter the
  Recon window.
- The cooldown start and learned duration are maintained in `types.py` belief
  folding: `last_kill_tick`, `kill_ready_since_tick`, `kill_cooldown_start_tick`,
  and `kill_cooldown_estimate` are updated as `self_kill_ready` transitions are
  observed. `last_kill_tick` is set the tick `self_kill_ready` flips True→False
  (our kill), and the cooldown duration is learned the first time a cooldown runs
  back to ready.

`kill_urgency_ticks(belief)` is `last_tick - kill_ready_since_tick` (0 when not
ready) — how long we have been *able* to kill without doing so. It drives the
witness relaxation below.

## Search — the always-on seeking FSM

`modes/search.py:SearchMode`. Search's job is to keep us *near crew* so a kill
window opens. It does not kill; when the kill is ready and a victim is visible
the selector flips to Hunt. The measured imposter gap that motivates Search is
being near crew about half as often as the strongest imposters.

A small FSM, with all state on the instance:

```
  PICK_ROOM ─► GO_TO_ROOM ─► WATCH ─► FOLLOW(c) ─┐
      ▲            │            │          │      │
      │   (room    │  (room     │ (crew    │ (c   │
      │   empty)   │  empty)    │ leaves)  │ settles in
      └────────────┴────────────┴──────────┘  a room) ─► WATCH
                                             (c lost) ─► PICK_ROOM
```

| State | Behavior |
|-------|----------|
| `pick_room` | Choose a nearby reachable task room to sweep — a random one of the nearest `NEARBY_ROOMS` (4) rooms that contain a task station (somewhere to blend), excluding the current and just-left rooms and the spawn room. A fixed-seed RNG (`0xC0FFEE`) makes picks deterministic/replayable. Head to the room **center** (go fully inside), not a door/task spot. |
| `go_to_room` | Navigate to the center; if a crewmate is seen leaving any watchable room, switch to FOLLOW immediately; on arrival, WATCH if crew are present else back to PICK_ROOM. |
| `watch` | Hold the in-room **vantage** with line-of-sight to the most crew, recomputed as they move (so crew don't walk out of view). Leaver → FOLLOW; no watched crew remain → PICK_ROOM. |
| `follow(c)` | Chase the committed leaver `c` to its next room. When visible, `navigate_to` its live position and feed `strategy/path_prediction.py:PathPredictor`; when occluded, `navigate_to` the predictor's top predicted hallway position. Give up when the target is gone/dead/now a teammate, the lost-ticks budget expires, or the predictor runs out. |

Search never follows the teammate imposter (`belief.teammate_colors`). The path
predictor is fed only what we actually see (the target's position when visible,
`None` otherwise) — the same signal it is scored on offline.

### Vantage selection

`_refresh_vantage` / `_best_vantage` pick, over a coarse `VANTAGE_STEP` (40 px)
grid of reachable in-room points, the point with clear line-of-sight
(`nav._segment_clear` over `belief.nav.walkability`) to the most watchable crew
within `VANTAGE_RANGE` (360 px). It is throttled (`VANTAGE_REFRESH_TICKS` = 18)
and uses hysteresis (`VANTAGE_SWITCH_MARGIN` = 1) so it only moves when a new
vantage sees at least one more crewmate, avoiding jitter between equal vantages.

| Search constant | Value | Meaning |
|-----------------|-------|---------|
| `NEARBY_ROOMS` | 4 | How many nearest task rooms are candidates to sweep. |
| `ARRIVE_RADIUS_SQ` | `24²` | Arrival tolerance at a goto point/vantage. |
| `FOLLOW_LOST_TICKS` | 120 | Drop an unseen follow after this long with no live prediction. |
| `COMMANDER_FOLLOW_LOST_TICKS` | 240 | Extended follow persistence for a commander-hard-named target. |
| `WATCH_RECENT_TICKS` | 36 | A crewmate is "still watchable" from a vantage if seen within this window. |
| `VANTAGE_RANGE` | 360 px | Line-of-sight range for vantage scoring. |
| `VANTAGE_STEP` | 40 px | Grid step for candidate vantage points. |
| `VANTAGE_REFRESH_TICKS` | 18 | Max vantage recompute frequency. |
| `VANTAGE_SWITCH_MARGIN` | 1 | Extra crew a new vantage must see before we move to it. |

An optional commander can bias which room to sweep (`hunt_room`), avoid
(`avoid_room`), or which player to prefer following (`target_player`), and at
`strength == "hard"` force a hunt room and grant the longer follow persistence —
see [`./commander.md`](./commander.md). Movement/pathing mechanics are in
[`./navigation.md`](./navigation.md); path-destination prediction is its own
subsystem in `strategy/path_prediction.py`.

## Recon — the pre-ready beeline

`modes/recon.py:ReconMode`. The only *tick-windowed* pre-position. When the
selector sees the kill within `recon_window()` ticks of ready (gate 4), it routes
here instead of Search. Recon does exactly one thing: **beeline to the
most-recently-seen crewmate** (`strategy/opportunity.py:most_recent_victim` —
live position when visible, last-known otherwise) so that the instant the
cooldown clears, a victim is in view and Hunt fires immediately.

Recon is stateless: the target (commander `target_player` override, else
`most_recent_victim`) is re-chosen each tick. Unlike Hunt it does not require the
target to be currently visible or reachable — it is only pre-positioning, not
striking. It `idle`s only when we have no self position or no crewmate has been
seen at all.

| Recon constant | Source | Value | Meaning |
|----------------|--------|-------|---------|
| `RECON_WINDOW_TICKS` | env `CREWBORG_RECON_WINDOW` via `recon_window()` | 100 | Ticks before ready at which Recon activates. Deliberately short — a long window risks over-extension that gets the imposter caught. |

## Hunt — commit, intercept, strike

`modes/hunt.py:HuntMode`. Selected only when the kill is ready and a non-teammate
crewmate is visible. Hunt owns the kill-ready close and strike; Search/Recon own
acquisition and pre-positioning. The flow each tick:

1. **Commit to a victim.** `_resolve_victim` keeps the currently committed victim
   (`_victim_color`, the only Hunt state) while it stays visible, alive, and a
   non-teammate; otherwise it commits to a new one via
   `strategy/opportunity.py:select_victim` (or a commander-forced visible,
   reachable target). Sticking with one target avoids re-picking every frame.
2. **Intercept, don't tail-chase.** Hunt navigates to the victim's *predicted
   intercept* point, not its live position. `strategy/trajectory.py:predict`
   extrapolates the victim's last-known position along its velocity (estimated
   from its two most recent sightings) by `lead_ticks(self_xy, victim_xy)` ticks.
   A tail-chase at equal speed never closes, so before leading, kills only ever
   landed on stopped crewmates. A stationary or unreliable target predicts to its
   current position (a no-op lead).
3. **Strike or lie in wait.** When in range (`action.py:KILL_RANGE_SQ` = 400, a
   20 px radius) and `self_kill_ready` and the strike is allowed, emit a `kill`.
   Otherwise close on the intercept; when already in range but the kill is
   witnessed, shadow ("lie in wait") rather than blow the kill.

### Trajectory (intercept lead) constants

| Constant | Value | Meaning |
|----------|-------|---------|
| `VELOCITY_MAX_DT` | 4 | Only trust a velocity from two sightings at most this many ticks apart (a wider gap means the player was off-screen between them). |
| `AGENT_SPEED_PX` | 3.0 | Assumed travel speed, used to estimate how long it takes us to close. |
| `MAX_LEAD_TICKS` | 24 | Cap on the lead so a stale/noisy velocity can't fling the aim point across the map. |

## The kill/strike gate

The strike condition in `modes/hunt.py:HuntMode.decide` is:

```
in_range  AND  self_kill_ready  AND  (unwitnessed  OR  already_killed  OR  danger_witness_allowed)
```

`in_range` is `dist2(self_xy, victim_xy) <= KILL_RANGE_SQ`. The interesting part
is the third clause — when a witnessed kill is allowed anyway.

### `unwitnessed` and the isolation radius

`strategy/opportunity.py:unwitnessed(belief, target)` answers "would killing this
target now go unseen, at the current urgency?" A kill is unwitnessed when **no
live non-teammate crewmate** (other than the victim) is both:

- within an isolation radius of the target, and
- has been seen recently enough to credibly still be watching.

The victim itself and fellow imposters are never witnesses; dead crewmates can't
witness. At zero urgency the radius is `BASE_ISOLATION_RADIUS` (48 world px) and
the recency window is `WITNESS_WINDOW_TICKS` (72 ticks).

### Urgency relaxation to zero

The witness bar is not fixed. The longer the imposter has been *able* to kill
without doing so, the more both the radius and the window shrink, so a cautious
imposter that never finds a perfectly clean opening still escalates rather than
stalling forever:

```
frac      = min(1.0, kill_urgency_ticks / URGENCY_FULL_TICKS)
radius_sq = (BASE_ISOLATION_RADIUS * (1 - frac))²
window    = int(WITNESS_WINDOW_TICKS * (1 - frac))
```

At `URGENCY_FULL_TICKS` (240 ticks, ~10s at 24 Hz) of being kill-ready without
killing, `frac == 1.0`, the radius and window both reach zero, and *every* victim
counts as unwitnessed — the imposter will strike regardless of nearby crew.

| Witness constant | Value | Meaning |
|------------------|-------|---------|
| `BASE_ISOLATION_RADIUS` | 48 px | Clearance required around the target at zero urgency. |
| `WITNESS_WINDOW_TICKS` | 72 | Max age of a sighting that still counts a crewmate as a potential witness, at zero urgency. |
| `URGENCY_FULL_TICKS` | 240 | Kill-ready-without-killing ticks at which the witness bar reaches zero. |

### The first-kill witness drop

The decisive override: **after the first kill, the witness requirement is dropped
entirely.** `already_killed = belief.last_kill_tick is not None`, and when true,
the strike fires on any in-range, kill-ready victim regardless of witnesses (the
`unwitnessed` test is bypassed). This is the mechanical expression of the
[core thesis](#the-core-thesis): banking the second kill is the imposter's job,
and at the second ready we are usually already close to crew, so conversion beats
stealth. The reason string distinguishes the two strikes
("striking the 2nd+ kill (witnesses ignored)" vs "striking isolated victim").

`danger_witness_allowed` is the third path: an optional commander danger mode
(`allow_witnessed_kill`) explicitly permits a witnessed kill, emitting a
`commander_danger` trace event when it fires. See
[`./commander.md`](./commander.md).

## Victim selection

`strategy/opportunity.py:select_victim` picks who to commit to: among live
non-teammate crewmates visible *this tick*, filtered to those we can actually
route to (`nav.plan_route` when a nav graph exists), it returns the **most
isolated, tie-broken by nearest to us**:

```
max(candidates, key=lambda t: (_isolation(t, belief), -dist2(self_xy, t)))
```

`_isolation(t)` is the squared distance to `t`'s nearest *other* live
non-teammate — a higher value means a more isolated straggler, the easiest target
to finish off unseen. Ties (and equal isolation) break toward the nearest
candidate so we commit to the one we can reach soonest.

### The teammate-claim heuristic

A soft deconfliction signal so two imposters don't pile onto the same victim.
`_claimed_by_teammate` treats a target as claimed when a recently-seen living
fellow imposter is both *closer* to it than we are and within
`TEAMMATE_CLAIM_RADIUS` (80 px). `select_victim` prefers unclaimed candidates
when any exist, but falls back to the full candidate set if every visible victim
is claimed — it never returns nothing solely because of a claim.

| Victim-selection constant | Value | Meaning |
|---------------------------|-------|---------|
| `TEAMMATE_CLAIM_RADIUS` | 80 px | A teammate closer than us and within this radius "claims" the victim. |
| `TRACK_WINDOW_TICKS` | 120 | Recency for "trackable" (`has_trackable_victim`) and for teammate-claim sightings. |

`has_visible_victim` / `visible_victims` (live non-teammates seen this very tick)
gate Hunt; `most_recent_victim` (the most-recently-seen live non-teammate, by
`last_seen_tick`) is the Recon and cold-start Evade target.

## Evade — post-kill re-approach

`modes/evade.py:EvadeMode`. Selected for `EVADE_TICKS` after our own kill. Despite
the name, **Evade does not flee** — it heads *toward* where the crew most likely
are, so a victim cluster is already nearby when the post-kill window hands back to
Search/Recon/Hunt. This is the inverse of fleeing: blind flight fed the post-kill
drift (lose crew contact, no victim in sight at the next ready), so Evade
re-approaches instead.

Evade is paired with Hunt's first-kill witness drop: re-approaching a *crowd*
would be a poor place to land an *unwitnessed* kill, but once witnesses no longer
veto the second kill, the crowd is target-rich exactly when we need it. The two
are designed to be evaluated together.

Target preference each tick (stateless, re-derived):

1. the densest expected-crew **room** (`agent_tracking.best_pretend_room_target`,
   teammate-pressure-adjusted so two imposters don't pile on the same room);
2. else the hottest occupancy **cell** (`agent_tracking.best_seek_point`);
3. else (cold start, before occupancy has mass) the most-recently-seen crewmate
   (`most_recent_victim`);
4. else `idle`.

When our own position is unknown the room/cell steps are skipped and Evade goes
straight to the last-seen crewmate. The occupancy/densest-crew readouts are the
agent-tracking subsystem — see [`./agent-tracking.md`](./agent-tracking.md).

## Where imposter play touches the rest

| Concern | Lives in | Doc |
|---------|----------|-----|
| Meeting deflect / bandwagon / vote behavior | `modes/attend_meeting.py` | [`./meetings.md`](./meetings.md) |
| Occupancy grid, densest-crew / hottest-cell readouts | `agent_tracking.py`, `strategy/occupancy.py` | [`./agent-tracking.md`](./agent-tracking.md) |
| Movement, routing, line-of-sight, nav graph | `nav.py`, `navbake.py` | [`./navigation.md`](./navigation.md) |
| How belief (roster, sightings, kill bits) is built | `types.py`, `perception/`, `events.py` | [`./perception-and-belief.md`](./perception-and-belief.md) |
| Per-player sighting trail used for tracking | `agent_tracking.py` | [`./agent-tracking.md`](./agent-tracking.md) |
| The suspicion model (crewmate side) | `strategy/suspicion.py` | [`./suspicion.md`](./suspicion.md) |
| The optional LLM commander levers | `strategy/commander/` | [`./commander.md`](./commander.md) |
| Crewmate counterpart of this play | `modes/normal.py`, `modes/accuse.py`, `modes/report_body.py` | [`./crewmate-play.md`](./crewmate-play.md) |
| Trace events for replay/debug | `trace.py` | [`./trace-logs.md`](./trace-logs.md) |
