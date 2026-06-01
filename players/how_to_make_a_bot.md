# How To Make A Crewrift Coworld Player

This guide explains how to write a player for the uploaded Crewrift Coworld.
The hosted contract is simple: your Docker image starts a player process, the
Coworld runner sets a player websocket URL, and your process connects to that
websocket to play one assigned slot. Current runners set both
`COWORLD_PLAYER_WS_URL` and the older `COGAMES_ENGINE_WS_URL` alias.

You do not need a Crewrift source checkout to compete. The source
implementation is still useful because the `notsus` player shows how to parse
Sprite v1 updates, keep local state, move through the map, complete tasks, and
vote during meetings.

## Coworld Runtime

In Coworld episodes, Softmax starts the game container and one policy container
per player slot. Your player receives:

```text
COWORLD_PLAYER_WS_URL
```

Connect to that URL exactly as supplied. It already includes the `/player`
endpoint, slot, and token for your player pod. The runner owns slot assignment
and token generation. Existing Crewrift source bots read the compatibility
`COGAMES_ENGINE_WS_URL` name; hosted runs populate both names with the same
value.

Your player image can be written in any language. It only needs to:

1. open the runner-supplied websocket;
2. read Sprite v1 updates from the server;
3. keep enough local state to understand the game screen;
4. send valid Sprite v1 input packets back to the same websocket;
5. keep running until the game ends or the runner stops the container.

Protocol references:

- Player protocol: <https://github.com/Metta-AI/bitworld/blob/master/docs/sprite_v1.md>
- Global viewer protocol: <https://github.com/Metta-AI/bitworld/blob/master/docs/sprite_v1.md>
- Play guide: <https://github.com/Metta-AI/coworld-crewrift/blob/master/docs/play_crewrift.md>
- Coworld spec: <https://github.com/Metta-AI/metta/blob/main/packages/coworld/src/coworld/COWORLD_README.md>

## Container Shape

A hosted player should fail loudly if neither runner websocket environment
variable is present. That usually means it is being run outside the Coworld
runner.

Minimal Dockerfile shape:

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY player.py .

CMD ["python", "/app/player.py"]
```

Build for the platform used by Coworld jobs:

```sh
docker buildx build --platform linux/amd64 -t my-crewrift-policy:latest --load .
coworld upload-policy my-crewrift-policy:latest --name my-policy
```

The process inside the image should not assume local files from this repository
unless the image explicitly copies them. If you want to use map geometry,
sprites, or the reference implementation, vendor the exact files into the
image.

## Source References

- `players/notsus/notsus.nim`: Reference player.
- `players/notsus/notsus/protocols.nim`: Sprite protocol parsing and input.
- `src/crewrift/sim.nim`: Game constants, task state, roles, movement, voting,
  and results.
- `src/crewrift/common/protocol.nim`: Shared game protocol helpers.
- Sprite v1: <https://github.com/Metta-AI/bitworld/blob/master/docs/sprite_v1.md>

## Local Source Run

Start a local server:

```sh
nim r src/crewrift.nim --address:0.0.0.0 --port:2000 --config:'{"minPlayers":1,"imposterCount":0,"tasksPerPlayer":1}'
```

Run the reference bot from another shell:

```sh
COGAMES_ENGINE_WS_URL='ws://localhost:2000/player?slot=0&token=' \
nim r players/notsus/notsus.nim -- --name notsus
```

For GUI debugging:

```sh
COGAMES_ENGINE_WS_URL='ws://localhost:2000/player?slot=0&token=' \
nim r -d:notsusGui players/notsus/notsus.nim -- --gui --name notsus-debug
```

## Player Loop

A useful player architecture is a simple pipeline:

1. Connect to the websocket.
2. Decode Sprite v1 messages.
3. Update local object, label, layer, and viewport state.
4. Track the player, nearby tasks, visible bodies, voting screens, and phase
   changes.
5. Choose a goal.
6. Send input only when the desired input changes.
7. Repeat until the game ends.

Keep this pipeline explicit. It is much easier to debug than a set of hidden
callbacks.

## Common Mistakes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Works locally but not in league | The player connects to localhost or a hardcoded URL | Use the runner-supplied websocket URL exactly. |
| Upload succeeds but the policy cannot call an LLM | API key was not attached to the policy version | Re-upload with `--secret-env` or `--use-bedrock`. |
| Image runs on a laptop but not in production | Built only for arm64 | Rebuild with `docker buildx build --platform linux/amd64 --load`. |
| Actions do nothing | Input packet encoding is invalid | Re-check Sprite v1 and compare with `players/notsus/notsus/protocols.nim`. |

## Next Steps

Start by making a player that joins, moves, and sends a small amount of valid
input. Then add task selection, meeting handling, voting, and strategy. The
`notsus` player is intentionally useful as a readable reference for that path.
