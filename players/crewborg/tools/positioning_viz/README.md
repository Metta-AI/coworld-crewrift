# Kill-ready positioning viewer

Look at *where* an imposter is, spatially, the moment its kill cooldown comes off — to see
why crewborg converts ready→kill so much slower than Aaron/Andre. For each **kill-ready
event** (an imposter's `kill_cooldown` hitting 0 while Playing) it draws, on the real map:
the focal imposter's path for the past *P* ticks → the ready moment → forward to the kill (or
the meeting that interrupted), plus every other player's recent path. Crewmates highlighted;
the nearest-crewmate distance at "ready" is the lever.

**Meeting-aware** (this is the whole point): meeting/voting ticks are dropped from the paths
(no teleport-to-Bridge jump), and the ready→kill window ends at the next meeting — because a
meeting resets the cooldown, so a kill *after* a meeting doesn't convert that ready moment.
See `../../best_practices.md` ("Meeting/voting ticks are NOT idle time").

## Data: a per-tick warehouse
The viewer needs per-tick positions, so build the warehouse with `--snapshot-every 1`:

```sh
CREWRIFT_EXPAND_REPLAY=/tmp/expand-043 \
  uv run crewrift-event-warehouse build \
  --input <report_request.json> --out /tmp/v50_pertick --workers 8 --snapshot-every 1
```

(`expand-043` = master sim `26ee08c`, valid for crewrift_prime 0.4.3–0.4.7.)

## Web viewer (interactive)
```sh
uv run --with duckdb --with flask --with pandas python server.py /tmp/v50_pertick [more_warehouses...] [--port 8809]
# open http://localhost:8809
```
Replay dropdown · event scroll-list (color-coded by hunt speed, ★ = us) · Past/Future sliders ·
"future = until next kill" · top stat strip (ready tick, hunting ticks, outcome, nearest crew @ready).

## PNG renderer (for headless/agent use)
Renders the same picture to a file you can open or `Read`:
```sh
# one event, found by policy
python render_event.py /tmp/v50_pertick --find crewborg --nth 0 --past 150 --future 50 -o /tmp/e.png
# a montage of N events for a policy
python render_event.py /tmp/v50_pertick --montage aaron --count 12 -o /tmp/grid.png
```

## Files
- `extract_positions.py` — pulls a replay's per-tick tracks, kills (located at the killer's
  position, since kill events carry no coords), map, and meeting-aware kill-ready events.
- `server.py` — Flask app (no-store headers; serves one or more warehouses).
- `index.html` — canvas viewer.
- `render_event.py` — matplotlib → PNG (single event or montage).
