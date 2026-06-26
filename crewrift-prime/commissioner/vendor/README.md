# commissioners

Commissioner implementations for **coworlds** - containers and tooling that orchestrate tournament rounds, schedule episodes, carry round state, and return scoring or graduation decisions to the platform.

> **Status:** canonical Coworld role repo. The config-driven commissioner is the active runnable container with `/healthz` and `/round` endpoints. The coworld `commissioner` role already has a protocol in metta; see [`docs/COMMISSIONER_DESIGN.md`](docs/COMMISSIONER_DESIGN.md) for pointers and repo conventions.

## What is a coworld commissioner?

A **coworld** is a Softmax v2 tournament unit: one game container + one or more player containers + a `coworld_manifest.json`. A **commissioner** is an optional role declared in the manifest under `commissioner: [...]` that participates in tournament round orchestration.

The canonical protocol lives in the metta repo at `packages/coworld/src/coworld/commissioner/protocol.py`. This repo is for commissioner implementations and scaffolding, not for redefining that protocol.

Coworld background: [`docs/COWORLD_REFERENCE.md`](docs/COWORLD_REFERENCE.md). Commissioner protocol notes: [`docs/COMMISSIONER_DESIGN.md`](docs/COMMISSIONER_DESIGN.md).

## Repository layout

```text
commissioners/
|-- README.md
|-- pyproject.toml
|-- docs/
|   |-- COWORLD_REFERENCE.md
|   `-- COMMISSIONER_DESIGN.md
`-- commissioners/
    |-- common/
    |   `-- ruleset_strategy/
    |-- templates/
    |   `-- commissioner_template/
    |-- ruleset_strategy_commissioner/
    |-- paint_arena/
    |   `-- paint_arena_commissioner/
    `-- default/
        `-- manual_commissioner.py
```

Runnable commissioners share `commissioners/Dockerfile` and select behavior with the `COMMISSIONER_KEY` build arg.
The active config-driven runnable selects one of its bundled YAML configs with `RULESET_STRATEGY_CONFIG_NAME`.
Leaf runnable directories keep thin entrypoints and build scripts:

| File | Purpose |
| --- | --- |
| `<commissioner_name>.py` | Compatibility entrypoint that serves a registered commissioner key. |
| `build.sh` | Builds the commissioner's Docker image through the shared Dockerfile. |
| `README.md` | Commissioner-specific docs: scheduling policy, state shape, local test command, and dependencies. |

## Status of each commissioner

| Commissioner | Coworld | Status |
| --- | --- | --- |
| `templates/commissioner_template` | (template) | Scaffold only - no implementation |
| `ruleset_strategy_commissioner` with `default.yaml` | Any | Active default runnable commissioner published as `ghcr.io/metta-ai/commissioners-default` |
| `ruleset_strategy_commissioner` with `proxywar.yaml` | ProxyWar | Active config for 2-player and 4-player rolling-window rounds |
| `paint_arena/paint_arena_commissioner` | PaintArena | Scaffold only - no implementation |

## Related metta repo locations

- `~/coding/metta/packages/coworld/` - coworld package: manifest schema, runner, certifier, and role types.
- `~/coding/metta/packages/coworld/src/coworld/types.py` - source of truth for the `commissioner` manifest section.
- `~/coding/metta/packages/coworld/src/coworld/commissioner/protocol.py` - canonical commissioner protocol.
- `~/coding/metta/docs/specs/0043-user-container-management.md` - shared runnable shape behind game, player, reporter, commissioner, diagnoser, and optimizer roles.
- `~/coding/metta/packages/coworld/src/coworld/examples/paintarena/` - simplest reference coworld.

## Conventions for new commissioners

- Keep reusable commissioner implementations under `commissioners/common/`.
- Keep one leaf directory per runnable image or entrypoint.
- Keep game-specific code under that game's directory.
- Treat `packages/coworld/src/coworld/commissioner/protocol.py` as canonical. If the protocol needs to change, change it in metta first.
- Keep game/runtime package code in its owning repo unless the file is genuinely commissioner source.
