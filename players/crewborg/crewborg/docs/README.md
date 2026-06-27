# crewborg docs — how the agent works

The **cross-cutting references**: how each subsystem works end-to-end, across the files no
single docstring can cover. For orientation start with the [package README](../README.md);
for the structural spec (file/type/tuning reference) see [`../design.md`](../design.md);
come here for the mechanism deep-dives. Every doc is **descriptive** — how the agent *is*,
in present tense.

## By subsystem

| Doc | What it covers |
|---|---|
| [perception-and-belief.md](./perception-and-belief.md) | Sprite-v1 scene → percepts → the belief world-model; the per-tick fold; camera-relative→world coordinates (no computer vision) |
| [navigation.md](./navigation.md) | the static map, the offline-baked A* nav graph over the walkability mask, vent-teleport routing, and momentum/predictive-stop control |
| [imposter-play.md](./imposter-play.md) | the kill-conversion pipeline: the imposter selector → Search/Recon/Hunt/Evade, the witness/isolation gate, victim selection + trajectory lead |
| [crewmate-play.md](./crewmate-play.md) | the detective loop: tasks → suspicion → Accuse/vote, the emergency-button mechanic, and vote-restraint |
| [suspicion.md](./suspicion.md) | the Bayesian P(imposter) model — the prior, the per-event log-LR evidence, the fitted weights, and how they're learned |
| [agent-tracking.md](./agent-tracking.md) | the probabilistic occupancy/location belief (reachability discs, the expected-crew grid) that feeds imposter seeking |
| [meetings.md](./meetings.md) | the meeting subsystem: the always-present deterministic vote + the opt-in LLM chat/vote path, chat reading, and imposter deflection |
| [commander.md](./commander.md) | the opt-in, gated-off LLM gameplay commander that writes priorities the modes read to bias execution |
| [trace-logs.md](./trace-logs.md) | the `domain.*` JSONL trace format, the trace controls, and how to read a finished game |

## By goal — where do I start?

- **"How does it play?"** → [imposter-play.md](./imposter-play.md) (imposter) / [crewmate-play.md](./crewmate-play.md) (crewmate).
- **"How does it see and move?"** → [perception-and-belief.md](./perception-and-belief.md) / [navigation.md](./navigation.md).
- **"How does it decide who's the imposter?"** → [suspicion.md](./suspicion.md) (+ [agent-tracking.md](./agent-tracking.md) for where players are).
- **"How do meetings / the LLM work?"** → [meetings.md](./meetings.md) (+ [commander.md](./commander.md) for the gameplay commander).
- **"How do I debug a game?"** → [trace-logs.md](./trace-logs.md).

The Sprite-v1 **wire protocol** and the game's label vocabulary live in the player-directory
top-level `AGENTS.md` (the consolidated game/SDK reference), not here — these docs describe how
crewborg *uses* the protocol, not its byte layout.
