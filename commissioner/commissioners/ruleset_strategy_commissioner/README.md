# Ruleset Strategy Commissioner

Configurable Coworld commissioner whose behavior is packaged in the container image.

This is the shared reusable ruleset commissioner in `Metta-AI/coworld-tools`. Change configs here only when the behavior
should remain shared. If a Coworld needs its own commissioner behavior, add a game-local commissioner/config in
`Metta-AI/coworld-<slug>` and update that Coworld manifest's `commissioner[].source_url` to the game repo. The usual
path is to copy this shared implementation or one of the `packages/coworld` commissioner templates into
`coworld-<slug>/commissioner/`, then customize the game-local copy.

The runnable does not read `league.commissioner_config` for behavior. That field is a platform wire artifact and may
contain legacy data while Coworlds roll over to container commissioners. Configs are authored in the readable
shape below, copied into the image, and selected by the image's `RULESET_STRATEGY_CONFIG_NAME` or
`RULESET_STRATEGY_CONFIG_PATH` environment variables.

The shared Dockerfile bundles configs from `configs/` and defaults to `configs/default.yaml`. Build the default baseline
image with:

```bash
commissioners/ruleset_strategy_commissioner/build.sh
```

For a downstream Coworld-specific image, copy the implementation into the game repo and make it a service in that
Coworld's compose file so `coworld build` builds and pins the game-local source. The Coworld manifest should point at
the game repo, not this shared source tree.

Key config areas:

- `defaults`: scheduling, seating, minimum entrants, and underfilled-seat behavior shared by divisions.
- `divisions`: logical tournament divisions, each with a real Coworld division match and entrant selector.
- `stages`: named substeps inside a division, commonly used for multi-stage qualifiers.
- `game_config`: optional division or stage game-config overrides emitted on scheduled episode requests.
- `policy_membership_events`: ordered criteria-based transitions produced when round results are complete.
  `on_round_complete` is still accepted as an alias, and `on_episode_complete` is still accepted as a legacy alias.
- `scoring`: optional round-score and leaderboard aggregation settings.
- `dispatch_throttle`: optional WebSocket episode release throttling for games that should not receive the full round
  schedule at once.

The current Coworld commissioner protocol only sends memberships from the active league. `fill_seats: fill_from_divisions`
can fill from other divisions in the same league when matching memberships are included in `round_start`. Filling from
another league or tournament requires the platform to include those memberships in `round_start`.

When a round is underfilled, top-up seats (cross-division fillers or duplicated entrants) are tagged on the scheduled
episode as `filler_seats` — a comma-separated list of the filler seat positions. The Softmax Observatory reads this tag
to exclude those "zombie" seats from scoring, rankings, and the matchup-fairness grid, so a topped-up game never counts
a filler policy as a real entrant. A full roster emits no `filler_seats` tag.

`policy_membership_events` entries are evaluated in order after a round completes. The first matching transition is
applied, and the emitted `policy_membership_event` includes evidence with the selected transition id, declared criteria,
observed values, and target metadata. Put these entries on the division or on a stage to make policy membership events
division-specific. `on_round_complete` and `on_episode_complete` are still accepted aliases for existing configs.

`game_config` may be set on a division, or on a stage to override the division for that stage. It is merged over the
incoming variant's game config and included on every scheduled episode request for that division or stage. `num_agents`
from the selected game config controls the number of policy slots in the scheduled episodes.

Example ruleset configs live in `configs/`:

- `default.yaml`: parity config for the default round-robin commissioner.
- `cogs_vs_clips.yaml`: parity config for Cogs vs Clips rolling-window scheduling.
- `four_score.yaml`: Four Score config with crash-check qualifiers and four repeated 8-agent teams per episode.
- `among_them.yaml`: replacement-style Among Them config with staged qualifiers and no Dirt league.
- `cue_n_woo.yaml`: Cue n Woo config with leaderboard-neighbor scheduling and throttled episode dispatch.
- `proxywar.yaml`: ProxyWar config with rolling-window 2-player/4-player scheduling, duplicate filling for short pools, crash-check qualifiers, and throttled episode dispatch.
- `agricogla.yaml`: Agricogla config with `shuffled_window` scheduling (per-round-permuted entry order so every champion eventually meets every other across rounds, instead of the fixed band `baseline_window`/`rolling_window` produce) and per-episode win-rate scoring.

`shuffled_window` seating is the round-robin-over-rounds option: `baseline_window` and `rolling_window` seat a fixed-width window of consecutive entries in a seed order that is stable across rounds, so two entries co-occur only when within `num_agents - 1` of each other and distant entries never share an episode. `shuffled_window` permutes the entry order each time a round is scheduled (seeded from the wall clock, so a re-scheduled round never reuses its previous order) so the band precesses and full pairwise coverage accrues across rounds while per-entrant appearances stay balanced.

## Among Them Style Staged Qualifier

Stage 1 is a self-play crash check. If a policy completes that round, it remains in the qualifier division and moves to
the `score_gate` substatus. If it does not complete, it is disqualified. Stage 2 applies the `score > 0` gate and sends
passing policies to the competition division.

```yaml
scoring:
  round_score: mean
  leaderboard:
    type: ewma
    half_life_hours: 2

defaults:
  seating: rolling_window
  fill_seats: duplicate
  min_entries_to_start: 8
  stage:
    label: Round
    episodes: 100
    min_episodes_per_entrant: 100

divisions:
  qualifiers:
    match:
      name: Qualifiers
      type: staging
    entrants: qualifying
    min_entries_to_start: 1
    stages:
      - id: crash_check
        schedule:
          label: Crash check
          self_play: true
          attempts: 2
          min_episodes_per_entrant: 2
        on_round_complete:
          - id: failed_crash_check
            criteria:
              completed_episodes_lte: 0
            actions:
              - type: update_membership
                status: disqualified
                substatus: inactive
          - id: passed_crash_check
            criteria: otherwise
            actions:
              - type: update_membership
                status: qualifying
                substatus: score_gate

      - id: score_gate
        schedule:
          label: Score gate
          episodes: 2
        on_round_complete:
          - id: passed_score_gate
            criteria:
              score_gt: 0
            actions:
              - type: update_membership
                division: competition
                status: competing
                substatus: champion
          - id: failed_score_gate
            criteria: otherwise
            actions:
              - type: update_membership
                status: disqualified
                substatus: inactive

  competition:
    match:
      type: competition
    entrants: champions
```

The `champions` entrant shortcut selects memberships where `is_champion` is
true. Non-champion `substatus` values are still available for staged qualifier
flows, but champion scheduling does not rely on `substatus: champion`.
