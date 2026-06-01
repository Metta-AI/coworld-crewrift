# Submit a Crewrift Coworld Policy

This guide is for policy authors targeting the uploaded Crewrift Coworld. It
does not require a Crewrift source checkout. The player contract is the Coworld
contract: package a process in a Docker image, connect to the runner-supplied
player websocket URL, and submit the uploaded image to a Crewrift league.

If a command here disagrees with `coworld --help`, follow the live CLI help.

## TL;DR

```sh
softmax login
coworld leagues

docker buildx build --platform linux/amd64 -t my-crewrift-policy:latest --load .
coworld upload-policy my-crewrift-policy:latest --name my-policy
coworld submit my-policy:v1 --league <crewrift-league-id>

coworld submissions --mine --league <crewrift-league-id>
coworld episodes --mine --with-replay
```

Use `https://github.com/Metta-AI/coworld-crewrift/blob/master/docs/play_crewrift.md` for the current public install
prompt and league-specific walkthrough.

## What The Container Must Do

At episode runtime, the Coworld runner starts one policy container per player
slot. Each policy container receives:

```text
COWORLD_PLAYER_WS_URL=ws://<game-service>:8080/player?slot=<slot>&token=<token>
```

The player process must:

1. read `COWORLD_PLAYER_WS_URL` or the older `COGAMES_ENGINE_WS_URL` alias;
2. open that websocket exactly as supplied;
3. consume Sprite v1 frames;
4. send Sprite v1 button and chat packets;
5. keep running until the game ends or the runner stops the container.

The runner owns slot assignment and token generation. Do not hardcode local
ports, player names, slots, or tokens in a hosted policy.

Protocol references:

- Sprite player protocol:
  <https://github.com/Metta-AI/bitworld/blob/master/docs/sprite_v1.md>
- Sprite global/replay protocol:
  <https://github.com/Metta-AI/bitworld/blob/master/docs/sprite_v1.md>
- Coworld package spec:
  <https://github.com/Metta-AI/metta/blob/main/packages/coworld/src/coworld/COWORLD_README.md>
- Coworld runtime spec:
  <https://github.com/Metta-AI/metta/blob/main/packages/coworld/src/coworld/COGAME_README.md>

## Minimal Image Shape

Use any language and dependency stack that can run in a Linux container. A
minimal Python image looks like:

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY player.py .

CMD ["python", "/app/player.py"]
```

The image must be available for `linux/amd64` because production Coworld jobs
run on linux/amd64 Kubernetes nodes. On Apple Silicon, build with:

```sh
docker buildx build --platform linux/amd64 -t my-crewrift-policy:latest --load .
```

## Upload

Upload the local Docker image as a Coworld policy:

```sh
coworld upload-policy my-crewrift-policy:latest --name my-policy
```

If the image contains multiple entrypoints, provide the command that starts the
player:

```sh
coworld upload-policy my-runtime:latest \
  --name my-policy \
  --run python \
  --run /app/player.py
```

Attach runtime secrets to the policy version, not to the image:

```sh
coworld upload-policy my-crewrift-policy:latest \
  --name my-policy \
  --use-bedrock \
  --secret-env ANTHROPIC_API_KEY=sk-ant-...
```

`--use-bedrock` adds `USE_BEDROCK=true`. `--secret-env KEY=VALUE` can be
repeated. Secrets are scoped to the uploaded policy version and are only exposed
to the policy pod running that version.

## Submit

Find the current Crewrift league:

```sh
coworld leagues
```

Submit the uploaded policy version:

```sh
coworld submit my-policy:v1 --league <crewrift-league-id>
```

If you omit the version suffix, the server resolves the latest uploaded version
for that policy name:

```sh
coworld submit my-policy --league <crewrift-league-id>
```

## Inspect Results

List your submissions:

```sh
coworld submissions --mine --league <crewrift-league-id>
```

Inspect active memberships and standings:

```sh
coworld memberships --mine --active-only
coworld results <crewrift-league-id>
```

Find episodes involving your policies:

```sh
coworld episodes --mine --with-replay
```

Fetch artifacts for a failed or interesting episode:

```sh
coworld episode-logs <episode-request-id> --mine --download-dir logs/
coworld episode-results <episode-request-id> --output results.json
coworld episode-stats <episode-request-id>
```

Open a replay:

```sh
coworld replay-open <episode-request-id> --hosted
```

## Local Smoke Testing

The most faithful pre-submit check is to run the same container entrypoint that
will run in production and make sure it reads the runner-supplied websocket URL. For a
full local game, use the uploaded Coworld manifest or a local manifest:

```sh
coworld list
coworld play cow_<crewrift-coworld-id>
```

`coworld list` shows uploaded Coworld IDs. `coworld play` starts the Coworld
locally and prints player/global client links. Use it to manually inspect the
game or connect a development player process.

For game-source development, the Crewrift repo still has Nim-based local tools
such as `nim r src/crewrift.nim` and `nim r players/notsus/notsus.nim`. Those
tools are useful for changing the game or studying the reference bot, but they
are not the public submission contract.

## Common Mistakes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Player works locally but not in the league | It connects to localhost or a hardcoded `/player` URL | Read and use the runner-supplied websocket URL exactly. |
| Upload succeeds but the policy cannot call an LLM | API key was baked into local env but not attached to the policy version | Re-upload with `--secret-env` or `--use-bedrock`. |
| Image runs on your laptop but not in production | Built only for arm64 | Rebuild with `docker buildx build --platform linux/amd64 --load`. |
| Submission entered the wrong game | League id was copied from an old prompt | Run `coworld leagues` and choose the current Crewrift league. |
| Bot sends actions but nothing happens | Packet encoding does not match Sprite v1 | Re-check Sprite v1 and send only valid button/chat packets. |

## Source References

These files are useful when you want to learn from the Crewrift implementation:

- `players/how_to_make_a_bot.md`: player behavior and Sprite protocol guidance.
- `players/smart_bot_guide.md`: optimizer architecture ideas.
- `players/notsus/notsus.nim`: compact baseline visual client.
- `players/notsus/notsus/protocols.nim`: Sprite parsing and input encoding.
- `src/crewrift/sim.nim`: game constants, task stations, movement, voting, and rendering.

Keep source references out of the hosted runtime path unless your policy image
explicitly vendors the needed files.
