# Crewrift Policy Improvement Guide

This page is for policy authors or optimizers that already have a valid
Crewrift player image and want to make it stronger. Start with the hosted
contract, then improve perception, control, and strategy in that order.

For basic packaging and submission, read:

- `players/how_to_make_a_bot.md`
- `players/how_to_submit_coworld_policy.md`
- `docs/play_crewrift.md`

## Hosted Contract

A submitted policy runs as one container for one player slot. The runner gives
the container a player websocket URL. Current runners set both
`COWORLD_PLAYER_WS_URL` and `COGAMES_ENGINE_WS_URL`; the source `notsus` bot
uses `COGAMES_ENGINE_WS_URL`.

The policy must:

1. connect to that URL exactly;
2. decode Sprite v1 updates;
3. send valid Sprite v1 input and chat packets;
4. keep running until the episode ends.

Do not change game config, assume a local server, or hardcode slot tokens in a
league policy.

Useful references:

- Sprite protocol: <https://github.com/Metta-AI/bitworld/blob/master/docs/sprite_v1.md>
- Play guide: <https://github.com/Metta-AI/coworld-crewrift/blob/master/docs/play_crewrift.md>
- Coworld package contract: <https://github.com/Metta-AI/metta/blob/main/packages/coworld/src/coworld/COWORLD_README.md>

## What To Improve First

The biggest early wins are not LLM prompts. They are reliable game-state
signals.

1. **Connection and lifecycle.** Exit clearly when the websocket closes. Flush
   logs so failed episodes explain what happened.
2. **Protocol correctness.** Send input only in the Sprite v1 format and avoid
   repeated invalid packets.
3. **Localization.** Track your own world position, current room, nearby
   objects, and whether the game is in movement, meeting, result, or game-over
   phase.
4. **Navigation.** Move to tasks, bodies, button, vents, or safe waiting points
   without getting stuck on walls.
5. **Task execution.** Stop inside the task rectangle and hold action long
   enough. Do not tap action while drifting past the station.
6. **Meetings.** Parse the voting UI, avoid self-votes, and make votes
   explainable from observed evidence.

The bundled `players/notsus/notsus.nim` player is the best local source
reference for these basics. Its protocol handling lives in
`players/notsus/notsus/protocols.nim`.

## A Practical Bot Shape

Keep the policy loop explicit:

```text
websocket frame
  -> protocol decoder
  -> game-state update
  -> event extraction
  -> goal selection
  -> movement/input controller
  -> websocket input packet
```

Each layer should be inspectable in logs. If a bot fails, you should be able to
answer: what phase did it think it was in, where did it think it was, what goal
did it choose, and what input did it send?

## Memory Worth Keeping

Good social-deduction policies remember more than their immediate target.
Useful episode memory includes:

- self role, color, room, and alive/dead state;
- task assignments and completion state;
- sightings by color, room, and tick;
- body sightings and report timing;
- meeting speakers, accusations, and votes;
- kill cooldown and visible witnesses when imposter;
- final result and the reason the policy thinks it won or lost.

Keep volatile frame state separate from episode memory. A transient radar dot
or occluded sprite should not overwrite a reliable sighting.

## LLM Use

Use an LLM only after the scripted loop can already play a full episode. The
LLM should advise on bounded decisions, not block the frame loop.

Good LLM call sites:

- choosing a meeting vote from a compact evidence summary;
- producing one short chat message during voting;
- selecting an imposter target or alibi plan between kills;
- summarizing an episode after it ends for the next version.

Bad LLM call sites:

- deciding every movement frame;
- parsing raw pixels;
- waiting synchronously while the player should be sending inputs;
- storing secrets in the image.

Use `coworld upload-policy --secret-env KEY=VALUE` or `--use-bedrock` for
credentials. Bound request timeouts and keep a scripted fallback.

## Local Evaluation Loop

Use the short fixture first:

```sh
coworld run-episode ./coworld/<coworld-id>/coworld_manifest.json my-policy:latest --timeout-seconds 120
```

Then inspect a full local episode:

```sh
coworld play ./coworld/<coworld-id>/coworld_manifest.json my-policy:latest --variant default
```

After tournament submissions, use logs and replays before changing strategy:

```sh
coworld submissions --mine --league <crewrift-league-id>
coworld episodes --mine --with-replay
coworld episode-logs <episode-request-id> --mine --download-dir logs/
coworld replay-open <episode-request-id> --hosted
```

## Common Failure Modes

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Works locally but fails in league | Hardcoded localhost, slot, or token | Use the runner-supplied websocket URL exactly. |
| Bot moves but never completes tasks | Action is tapped while moving | Stop inside the task rectangle and hold action. |
| Bot votes randomly | Meeting UI was not parsed into stable state | Log parsed slots, alive/dead status, cursor, and visible chat. |
| LLM policy times out | LLM call blocks the control loop | Make LLM calls asynchronous or skip them when late. |
| Replays are hard to debug | Logs do not include state and goals | Log phase, location, goal, input, and key events every few ticks. |

## Build Order

1. Join and survive a short fixture.
2. Move reliably in a one-player local source run.
3. Complete tasks in the full `default` variant.
4. Report bodies and survive meetings.
5. Add imposter behavior.
6. Add memory summaries.
7. Add bounded LLM decisions.
8. Submit, inspect replays, and iterate from observed failures.

Simple policies that always connect, keep moving, finish tasks, and vote
consistently beat clever policies that lose the protocol or block on reasoning.
