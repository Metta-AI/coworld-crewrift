# Event warehouse — query recipes

A starter library for the questions this warehouse exists to answer. Field names are exact (see
[`event-catalog.md`](event-catalog.md)). Run them in the `serve` dashboard (write `{events}` /
`{players}` — they expand to the `read_parquet(...)` exprs), or against DuckDB directly with the
views below. **Guard player aggregates with `WHERE slot >= 0`**; the global rows have NULL identity.

```python
import duckdb
con = duckdb.connect()
con.execute("CREATE VIEW events AS SELECT * FROM read_parquet('warehouse/events/**/*.parquet', hive_partitioning=true)")
con.execute("CREATE VIEW episode_players AS SELECT * FROM read_parquet('warehouse/episode_players.parquet')")
```

## 0. Sanity first — what's in here, and is it real?

```sql
-- which events exist + how many (and confirm the build wasn't version-skewed)
SELECT key, count(*) AS n FROM events GROUP BY key ORDER BY n DESC;
-- ⚠️ version-skew check: episodes whose replay hash-failed produce only metadata/warning rows
SELECT count(DISTINCT episode_id) AS skewed_episodes FROM events WHERE key='trace_warning';
```
If `skewed_episodes` is more than a trickle, the `expand_replay` binary doesn't match the replays —
the deeper events (kills, following, votes) are missing. Rebuild it (see the SKILL.md), don't trust
the numbers.

## 1. Rooms & routes (movement)

```sql
-- most-visited rooms per policy
SELECT policy_name, json_extract_string(value,'$.room') AS room, count(*) AS visits
FROM events WHERE key='entered_room' AND slot>=0 AND policy_name IS NOT NULL
GROUP BY 1,2 ORDER BY policy_name, visits DESC;

-- most common routes (room -> room)
SELECT json_extract_string(value,'$.origin_room') AS from_room,
       json_extract_string(value,'$.target_name') AS to_room, count(*) AS trips
FROM events WHERE key='headed_to' AND json_extract_string(value,'$.target_kind')='room'
GROUP BY 1,2 ORDER BY trips DESC;
```

## 2. Following / trailing (the `following_interval` family)

```sql
-- who tails whom, with the FOLLOWED player's role (the canonical preset)
SELECT f.policy_name AS follower, tgt.policy_name AS followed, tgt.role AS followed_role,
       count(*) AS follows,
       round(avg(json_extract(f.value,'$.alignment_ratio')::double),3) AS mean_alignment
FROM events f
JOIN episode_players tgt ON tgt.episode_id=f.episode_id
                        AND tgt.slot=json_extract(f.value,'$.target')::int
WHERE f.key='following_interval' AND f.slot>=0
GROUP BY 1,2,3 ORDER BY follows DESC;
```

```sql
-- "am I getting TRAILED?" — crewborg as the TARGET of someone's follow, by who's following + their role
SELECT me.policy_name AS me, me.role AS my_role,
       fol.role AS follower_role, fol.policy_name AS follower,
       count(*) AS times_trailed
FROM events f
JOIN episode_players me  ON me.episode_id=f.episode_id  AND me.slot=json_extract(f.value,'$.target')::int
JOIN episode_players fol ON fol.episode_id=f.episode_id AND fol.slot=f.slot
WHERE f.key='following_interval' AND me.policy_name LIKE 'crewborg%'
GROUP BY 1,2,3,4 ORDER BY times_trailed DESC;
```

## 3. Hunting: near a crewmate but NO kill (imposter kill-conversion)

```sql
-- as imposter, how many isolation windows with a crew victim did NOT convert to a kill?
-- (isolation_interval is global: player_a/player_b; pair one imposter actor with a crew target)
WITH iso AS (
  SELECT i.episode_id,
         json_extract(i.value,'$.player_a')::int AS a, json_extract(i.value,'$.player_b')::int AS b,
         i.ts AS start_ts, json_extract(i.value,'$.tick_end')::bigint AS end_ts
  FROM events i WHERE i.key='isolation_interval'
), pairs AS (   -- orient each isolation as (imposter, crew_target)
  SELECT iso.episode_id, pa.policy_name AS imposter, pb.slot AS victim_slot, iso.start_ts, iso.end_ts
  FROM iso
  JOIN episode_players pa ON pa.episode_id=iso.episode_id AND pa.slot=iso.a AND pa.role='imposter'
  JOIN episode_players pb ON pb.episode_id=iso.episode_id AND pb.slot=iso.b AND pb.role='crew'
)
SELECT imposter,
       count(*) AS isolations_with_crew,
       count(*) FILTER (WHERE NOT EXISTS (
         SELECT 1 FROM events k WHERE k.key='kill' AND k.episode_id=pairs.episode_id
           AND json_extract(k.value,'$.victim_slot')::int = pairs.victim_slot
           AND k.ts BETWEEN pairs.start_ts AND pairs.end_ts + 60)) AS no_kill,
       round(100.0*count(*) FILTER (WHERE NOT EXISTS (
         SELECT 1 FROM events k WHERE k.key='kill' AND k.episode_id=pairs.episode_id
           AND json_extract(k.value,'$.victim_slot')::int = pairs.victim_slot
           AND k.ts BETWEEN pairs.start_ts AND pairs.end_ts + 60))/count(*),1) AS pct_unconverted
FROM pairs GROUP BY imposter ORDER BY isolations_with_crew DESC;
```
*(The `player_a`/`player_b` orientation is arbitrary, so also run it with `pa.slot=iso.b … pb.slot=iso.a` and union, or do both orderings — see the catalog note.)*

```sql
-- kill LATENCY: ticks from kill-ready (kill_cooldown hits 0) to the next kill — how fast it converts.
WITH ready AS (   -- first tick each imposter-life becomes kill-ready
  SELECT episode_id, slot, policy_name, min(ts) AS ready_ts
  FROM events WHERE key='player_state' AND role='imposter'
    AND json_extract(value,'$.kill_cooldown')::double = 0 AND json_extract_string(value,'$.alive')='true'
  GROUP BY 1,2,3)
SELECT r.policy_name, count(*) AS kills,
       round(median(k.ts - r.ready_ts),0) AS median_ticks_ready_to_kill
FROM ready r JOIN events k ON k.key='kill' AND k.episode_id=r.episode_id AND k.slot=r.slot AND k.ts>=r.ready_ts
GROUP BY 1 ORDER BY kills DESC;

-- visibility AT READY: when kill-ready, did the imposter have a crew victim in view?
SELECT ps.policy_name,
       round(100.0*count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM events v WHERE v.key='player_visible_interval' AND v.episode_id=ps.episode_id
           AND v.slot=ps.slot AND json_extract_string(v.value,'$.target_role')='crew'
           AND ps.ts BETWEEN v.ts AND json_extract(v.value,'$.tick_end')::bigint))/count(*),1) AS pct_ready_with_crew_in_view
FROM events ps WHERE ps.key='player_state' AND ps.role='imposter'
  AND json_extract(ps.value,'$.kill_cooldown')::double=0 AND json_extract_string(ps.value,'$.alive')='true'
GROUP BY 1;
```
*(These two recipes replace the old standalone `kill_latency.py` / `visibility_at_ready.py` scripts — the warehouse does them in SQL.)*

## 4. Tasks — where I complete vs abandon

```sql
-- task-attempt outcomes per policy (crew), joined to the task's room via map_geometry
WITH task_room AS (
  SELECT episode_id, json_extract(t.value,'$.id')::int AS task_id, json_extract_string(t.value,'$.room') AS room
  FROM events m, UNNEST(json_extract(m.value,'$.tasks')) AS t(value)
  WHERE m.key='map_geometry'
)
SELECT a.policy_name, tr.room,
       count(*) FILTER (WHERE json_extract_string(a.value,'$.outcome')='completed') AS completed,
       count(*) FILTER (WHERE json_extract_string(a.value,'$.outcome')='abandoned') AS abandoned
FROM events a
LEFT JOIN task_room tr ON tr.episode_id=a.episode_id AND tr.task_id=json_extract(a.value,'$.task')::int
WHERE a.key='task_attempt' AND a.role='crew' AND a.policy_name LIKE 'crewborg%'
GROUP BY 1,2 ORDER BY abandoned DESC;
```

## 5. Votes — did crew vote a real imposter?

```sql
-- vote correctness: of crewborg's non-skip votes, how often was the target actually an imposter?
SELECT v.policy_name,
       count(*) AS player_votes,
       round(100.0*count(*) FILTER (WHERE tgt.role='imposter')/count(*),1) AS pct_hit_imposter
FROM events v
JOIN episode_players tgt ON tgt.episode_id=v.episode_id AND tgt.slot=json_extract(v.value,'$.target_slot')::int
WHERE v.key='vote_cast' AND v.role='crew' AND json_extract_string(v.value,'$.target') IS DISTINCT FROM 'skip'
  AND v.policy_name LIKE 'crewborg%'
GROUP BY 1 ORDER BY player_votes DESC;
```

## 6. Chat — raw text, and LLM-labelled suss-rate

```sql
-- read what was actually said (chat is the only free text; keyed by speaker)
SELECT episode_id, ts, policy_name, role, json_extract_string(value,'$.text') AS said
FROM events WHERE key='chat' AND slot>=0 ORDER BY episode_id, ts;

-- how chatty is each policy, by role?
SELECT policy_name, role, count(*) AS msgs, count(DISTINCT episode_id) AS eps,
       round(count(*)::double/count(DISTINCT episode_id),2) AS msgs_per_game
FROM events WHERE key='chat' AND slot>=0 GROUP BY 1,2 ORDER BY msgs DESC;

-- crew ENGAGEMENT scoreboard: REAL talk + spoke% (exclude the crew abstain non-message
-- 'no read, skipping'). Pair with suss accuracy (above) + vote accuracy (#5) for the full picture.
SELECT policy_name,
       round(count(*) FILTER (WHERE txt <> 'no read, skipping')::double
             / count(DISTINCT episode_id), 2) AS real_msgs_per_game,
       round(100.0*count(DISTINCT episode_id) FILTER (WHERE txt <> 'no read, skipping')
             / count(DISTINCT episode_id), 0) AS spoke_pct
FROM (SELECT episode_id, policy_name, json_extract_string(value,'$.text') AS txt
      FROM events WHERE key='chat' AND role='crew' AND slot>=0)
GROUP BY 1 ORDER BY real_msgs_per_game DESC;
```
*(This crew-engagement scoreboard + per-policy role outcomes [#7] is what the old standalone
`prime_summary.py` did — it's folded here; no separate tool.)*

Raw text doesn't aggregate — to ask *who* a message accuses, LLM-enable the warehouse with the
`suss` subcommand (`crewrift-event-warehouse suss --out <wh>`), which writes a `chat_suss` partition.
The same pattern extends to any chat interpretation (claims, defenses, vouching) — see the SKILL.md
"Adding your own LLM chat extraction".

```sql
-- suss-rate: when crewborg (crew) accuses someone in chat, how often is the target really an imposter?
SELECT policy_name,
       count(*) FILTER (WHERE json_extract_string(value,'$.is_suss')='true') AS susses,
       round(100.0*count(*) FILTER (WHERE json_extract_string(value,'$.target_is_imposter')='true')
         / NULLIF(count(*) FILTER (WHERE json_extract_string(value,'$.is_suss')='true'),0),1) AS suss_accuracy_pct
FROM events WHERE key='chat_suss' AND role='crew' GROUP BY 1;
```

## 7. Outcomes — the dimension table directly

```sql
-- role win rates + kill rate straight from episode_players (no event parsing)
SELECT policy_name, role, count(*) AS games,
       round(100.0*avg(win::int),1) AS win_pct,
       round(avg(kills),2) AS kills_per_game, round(avg(tasks),2) AS tasks_per_game
FROM episode_players WHERE policy_name IS NOT NULL GROUP BY 1,2 ORDER BY 1,2;
```

---

**Writing your own:** start from the catalog — pick the `key`, read its `value` fields, and remember
embedded slots (a victim, a follow target) need a self-join to `episode_players` to get *that*
party's role/policy. The interval events (`*_interval`) carry `tick_start`/`tick_end` so you can
window-join them against point events (`kill`, `vote_cast`) for "did X lead to Y" questions.
