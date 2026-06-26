# crewborg-aaln

Aaron Landy's **Crewrift league** policy — the deterministic dumb baseline with
convict-capable vote policy, emergency-button usage, faster task routing, and
imposter anti-camping (v2 upload; vote-deadline fix landed in v3). Source synced
from the [`softmax/players`](https://github.com/Metta-AI/players) monorepo
(`players/crewrift/crewborg`).

## Observatory

| Field | Value |
|-------|-------|
| League | [Crewrift](https://softmax.com/observatory/v2#tab=leagues&detail=league:league_605ff338-0a2e-4e62-aeda-559df9a9198f) |
| Policy | `crewborg-aaln:v2` |
| Policy version ID | `804c2e83-4daa-4cf0-b538-700b5f542c8f` |
| Policy ID | `3154885a-3285-4888-8757-92cb053a078d` |
| Container image | `img_3f2026ab-199f-4528-b03f-5db0f1fe6561` (kind: `docker-img`) |
| Player | Aaron (`ply_630a768f-d623-44b2-80fa-36968d6fa75a`) |
| Uploaded | 2026-06-11T07:41:07Z |

Submitted policy container images are private to Observatory runtime and are not
downloadable via the API. This directory holds the equivalent source tree.

## Runtime flags (v2)

The league upload bakes in:

- `CREWBORG_POLICY_VARIANT=dumb` — scripted baseline (no external LLM required)
- `BE_DUMB=1` — same dumb baseline seam
- `CREWBORG_LLM_MEETINGS=0` — deterministic vote policy (no LLM latency in meetings)
- `CREWBORG_DICK_MODE=0` — no timed dick-mode button spam
- No `CREWBORG_HUNTER` — hunter/stakeout fork is [`sussybuster-aaln`](../sussybuster-aaln/)

Observatory `attributes.run` was not recorded on v2; later versions use
`python -m players.crewrift.crewborg.coworld.policy_player` (see `coplayer_manifest.json`).

## Run locally

```sh
pip install -r requirements.txt
COWORLD_PLAYER_WS_URL='ws://127.0.0.1:8080/player?slot=0&token=' \
  python -m players.crewrift.crewborg.coworld.policy_player
```

## Build

```sh
docker build -t crewborg-aaln:v2 players/crewborg-aaln
```

## Tests

```sh
cd players/crewborg-aaln
python -m pytest players/crewrift/crewborg/tests/
```
