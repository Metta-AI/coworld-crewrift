---
name: crewrift-event-warehouse
description: "Use to build and query the Crewrift event warehouse — a policy-indexed DuckDB/Parquet dataset of per-tick gameplay events (kills, follows, proximity, votes, tasks, chat) over many episodes — for deep, mechanistic, cross-episode questions about a policy's behaviour. Triggers: 'how often is crewborg near a crewmate without killing', 'who trails crewborg', 'where does it abandon tasks', 'does it vote real imposters', 'build the warehouse for this round', 'query the events'. This is the deep-dig mainstay after crewrift-survey flags something."
---

# Crewrift Event Warehouse

The deep-dig mainstay. The survey (`crewrift-survey`) tells you *what* happened from the result
JSONs; the warehouse tells you *how* — it replays every episode, extracts per-tick **gameplay
events** (movement, proximity, following, kills, bodies, votes, chat, tasks, visibility), re-keys each
from episode *slot* to **policy / role**, and collates them into a queryable **DuckDB/Parquet** dataset
so you can ask **cross-episode, by-policy, by-role** behavioural questions in SQL.

**The tool is vendored** at [`players/crewborg/tools/event-warehouse/`](../../tools/event-warehouse/)
(two packages: `crewrift-event-warehouse` + its `crewrift-event-reporter`), run via `uv` — no global
install. Its own [`README`](../../tools/event-warehouse/crewrift-event-warehouse/README.md) is the
full reference; this skill is the operator's path.

**Use it when** a survey/A-B/diagnose question needs the actual behaviour: kill conversion, stalking,
being-trailed, task abandonment, vote correctness, suss accuracy. **Don't** use it for a quick
batch overview — that's the fast `crewrift-survey`.

## Build it

`build_warehouse.py` is the one-shot: episodes → `report_request.json` → built warehouse, with the
version-skew check baked in.

```bash
B=players/crewborg/skills/crewrift-event-warehouse/scripts/build_warehouse.py
# episodes already pulled WITH replays (coworld-episode-artifacts — do NOT pass --no-replay):
uv run python "$B" --episodes /tmp/eps --out /tmp/wh --expand-replay /tmp/expand-<commit>
# …or let it fetch first. --episode/--xreq/--round are ALL repeatable and mixable, so you can
# span rounds, span experience requests, and cherry-pick arbitrary episodes into one warehouse:
uv run python "$B" --xreq xreq_A --xreq xreq_B --round round_9 --out /tmp/wh --expand-replay /tmp/expand-<commit>
uv run python "$B" --episode <uuid> --episode ereq_xyz --out /tmp/wh --expand-replay /tmp/expand-<commit>
uv run python "$B" --policy crewborg -n 200 --out /tmp/wh --expand-replay /tmp/expand-<commit>
```

It prints the manifest summary and **flags `trace_warning` episodes** (the #1 failure, below).

### ⚠️ The one hard part — `expand_replay` version coupling

The extractor re-steps the sim and **hash-checks every tick**; on a version mismatch it **aborts
mid-replay**, leaving only metadata + a `trace_warning` — so a skewed build looks like *sparse
output, not an error*. The binary must be built from the **exact crewrift commit the arena ran when
it recorded the replays** — not `master` (which drifts ahead and hash-fails).

Vendoring here is what makes this tractable: **the expander source is in this same repo**
(`tools/expand_replay.nim`). To get the right binary:

1. Find the arena's deployed version: `coworld episodes --round <id> --json` → each episode's
   `coworld_version` (e.g. `crewrift:0.1.54`).
2. Build `expand_replay` from `tools/expand_replay.nim` at that commit (e.g. `0.1.54 ⇒ 42fed21`):
   `nim c -d:release -d:useMalloc --opt:speed --out:/tmp/expand-42fed21 tools/expand_replay.nim`
   (after `nimby --global sync nimby.lock`).
3. Verify: it should exit 0 with `trace_complete:true` on a real replay from that round.
4. Pass it as `--expand-replay /tmp/expand-42fed21`.

**Always check the `trace_warning` count first** — if it's more than a trickle, fix the binary before
trusting any query. See the README's "version coupling" section for the full recipe.

## Query it

Two ways. The dataset is a star schema — an `events` fact table + an `episode_players` dimension:

```bash
# interactive: a local dashboard with a SQL editor + one-click preset queries + a schema sidebar
cd players/crewborg/tools/event-warehouse/crewrift-event-warehouse
uv run crewrift-event-warehouse serve --out /tmp/wh        # -> http://127.0.0.1:8765
# in SQL, {events} / {players} expand to the read_parquet(...) exprs.
```

```python
# programmatic: native DuckDB (no extra install)
import duckdb; con = duckdb.connect()
con.execute("CREATE VIEW events AS SELECT * FROM read_parquet('/tmp/wh/events/**/*.parquet', hive_partitioning=true)")
con.execute("CREATE VIEW episode_players AS SELECT * FROM read_parquet('/tmp/wh/episode_players.parquet')")
```

- **How to read the event table** — every event `key` and its `value` JSON fields, and the
  slot-join rules: **[`references/event-catalog.md`](references/event-catalog.md)**. Read this before
  writing SQL; it's the difference between a correct query and a guess.
- **Recipes for the real questions** — rooms/routes, who-trails-whom, *am I getting trailed*,
  *near-a-crewmate-without-killing*, task abandonment by room, vote correctness, suss-rate, role
  outcomes: **[`references/recipes.md`](references/recipes.md)**.

The two rules that keep queries correct (full detail in the catalog):
- **`slot >= 0`** for any player aggregate — global rows (`slot = -1`: `proximity_interval`,
  `map_geometry`, `phase`, metadata) have NULL identity.
- **Embedded slots** in `value` (a victim, a follow `target`) are raw ints — **self-join
  `episode_players` on `(episode_id, that_slot)`** to get *that* party's role/policy. The field name
  varies per key (`$.victim_slot`, `$.target`, `$.target_slot`, `$.player_a/b`) — match it exactly.

## Chat & LLM-enabled extraction

Chat is the **only free text** the warehouse holds — the `chat` event's `value.text` (voting-phase
messages, keyed by speaker). Pull it raw:

```sql
SELECT episode_id, ts, policy_name, role, json_extract_string(value,'$.text') AS said
FROM events WHERE key='chat' AND slot>=0 ORDER BY episode_id, ts;
```

Raw text is hard to aggregate, so the warehouse can be **LLM-enabled to interpret chat** and emit new
*structured* events. The shipped example is **`suss`** — "who is each message accusing?":

```bash
cd players/crewborg/tools/event-warehouse/crewrift-event-warehouse
uv run crewrift-event-warehouse suss --out /tmp/wh        # needs AWS creds + Bedrock (Haiku)
```

It labels each chat with its **suss target** (Bedrock Haiku), resolves that to the target's
slot/role/policy per episode, and writes a native `events/key=chat_suss` partition — which is what
makes **suss-rate** ("when crewborg accuses someone, is it really an imposter?") queryable
([recipes.md #6](references/recipes.md)). It's idempotent and **cached** (distinct texts only).

### Adding your own LLM chat extraction (the `suss` pattern)

`suss.py` is the **template** for any "interpret chat → new event" extension — sentiment,
claims/alibis, vouching, defenses, naming a room. Copy its four steps:

1. **Distinct texts** — `SELECT DISTINCT json_extract_string(value,'$.text')` over the `chat`
   partition. Chat is heavily templated, so thousands of events collapse to ~hundreds of strings.
2. **Classify once, cache** — batch the distinct texts through Bedrock (temperature 0, JSON-array
   out) and cache to a `<key>_cache.json`, so re-runs are cheap (`--refresh` to redo).
3. **Resolve to identity (no LLM)** — if your label names a player, map color→slot from
   `player_joined` labels (`red(Name)`), then slot→role/policy via `episode_players`. A pure join.
4. **Write a partition** — keyed by the **speaker** (`slot`), your labels in `value`, to
   `events/key=<your_key>/…parquet` using `EVENTS_SCHEMA`. It's then queryable like any other event.

Code to copy: [`crewrift_event_warehouse/suss.py`](../../tools/event-warehouse/crewrift-event-warehouse/crewrift_event_warehouse/suss.py)
(`distinct_texts` → `classify_texts` → `episode_color_maps`/`slot_identity` → `build_suss_partition`).

## Extend it (objective events)

New behavioural questions are usually a new query, not new code. For new *objective* (non-chat)
events — a new proximity/visibility/movement pattern — the extractor is the place: raw events in
`tools/expand_replay.nim`, derived events in the reporter's `analysis.py`.

## See also

- **`crewrift-survey`** — the fast batch pass that flags *what* to deep-dive here.
- **`coworld-episode-artifacts`** — pulls the episodes (with replays) the warehouse builds from.
- [`crewrift-replays.md`](../../docs/reference/crewrift-replays.md) — the single-episode `expand_replay`
  path + the same version-coupling caveat.
