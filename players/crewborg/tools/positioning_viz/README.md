# Kill-ready positioning viewer

Look at *where* an imposter is, spatially, the moment its kill cooldown comes off — to study
how a policy converts ready→kill (e.g. why crewborg may hunt longer before killing). For each
**kill-ready event** (an imposter's `kill_cooldown` hitting 0 while Playing) it draws, on the
real map: the focal imposter's path for the past *P* ticks → the ready moment → forward to the
kill (or the meeting that interrupted), plus every other player's recent path. The configured
"us" policy is highlighted; the nearest-crewmate distance at "ready" is the lever.

**Meeting-aware** (this is the whole point): meeting/voting ticks are dropped from the paths
(no teleport-to-Bridge jump), and the ready→kill window ends at the next meeting — because a
meeting resets the cooldown, so a kill *after* a meeting doesn't convert that ready moment.
See [`../../docs/best_practices.md`](../../docs/best_practices.md) ("Meeting ticks are NOT idle time").

## Data: a per-tick event warehouse

The viewer needs per-tick `player_state` position snapshots, which is the warehouse default
(`--snapshot-every 1`). Build one with a **version-matched** `expand_replay` binary (the binary
must match the game commit that recorded the replays — `CREWRIFT_REF` in
[`../build/versions.env`](../build/versions.env); see
[`../../docs/reference/crewrift-replays.md`](../../docs/reference/crewrift-replays.md) §B and the
`crewrift-event-warehouse` skill for the coupling recipe):

```sh
# from the repo root — fetches episodes (with replays) and builds the warehouse:
B=players/crewborg/skills/crewrift-event-warehouse/scripts/build_warehouse.py
uv run python "$B" --xreq <xreq_id> --out /tmp/wh --expand-replay /tmp/expand-<commit>
```

The event `value` fields the extractor reads (`player_state`, `kill`, `map_geometry`) are
catalogued in
[`../../skills/crewrift-event-warehouse/references/event-catalog.md`](../../skills/crewrift-event-warehouse/references/event-catalog.md).

## Web viewer (interactive)

```sh
# from this directory:
uv run --with duckdb --with flask --with pandas python server.py /tmp/wh [more_warehouses...] [--port 8809]
# open http://localhost:8809
```

Replay dropdown · event scroll-list (colour-coded by hunt speed, ★ = us) · Past/Future sliders ·
"future = until next kill" · top stat strip (ready tick, hunting ticks, outcome, nearest crew @ready).
Arrow keys step events. `--us-policy <name>` changes which policy is starred (default `crewborg`).

## PNG renderer (for headless / agent use)

Renders the same picture to a file you can open or `Read`:

```sh
# one event, found by policy across all episodes
python render_event.py /tmp/wh --find crewborg --nth 0 --past 150 --future 50 -o /tmp/e.png
# a montage of N events for a policy
python render_event.py /tmp/wh --montage crewborg --count 12 -o /tmp/grid.png
# one event of a specific episode
python render_event.py /tmp/wh --episode <episode_id> --event 0 -o /tmp/e.png
```

## Files

- `extract_positions.py` — the data layer: pulls a replay's per-tick tracks, kills (located at
  the killer's position, since `kill` events carry no coords), map, and the meeting-aware
  kill-ready events. Owns the data shape; both viewers import it.
- `server.py` — Flask app (no-store headers; serves one or more warehouses) + 2 JSON endpoints.
- `index.html` — canvas viewer (the browser drawing layer).
- `render_event.py` — matplotlib → PNG (single event or montage; the headless drawing layer).
```
extract_positions.py  ──►  server.py + index.html   (interactive)
        └─────────────►  render_event.py            (PNG)
```
