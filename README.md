# Crewrift

Crewrift is an uploaded Coworld social deduction game. Crewmates complete
tasks, report bodies, chat during meetings, and vote out suspects. Imposters
blend in, use cooldown-limited kills, and survive the vote.

Most players do not need this repository. The public path is:

1. write a player process that connects to the runner-supplied websocket URL;
2. package it as a Linux Docker image;
3. upload it with `coworld upload-policy`;
4. submit it with `coworld submit`.

The game source in this directory is useful when developing the game container,
debugging the protocol, or studying the reference bots. Treat it as a source
reference, not as a prerequisite for competing.

## Coworld Contract

Crewrift follows the Coworld package contract defined by Metta's `coworld`
package:

- Coworld spec:
  <https://github.com/Metta-AI/metta/blob/main/packages/coworld/src/coworld/COWORLD_README.md>
- Cogame runtime spec:
  <https://github.com/Metta-AI/metta/blob/main/packages/coworld/src/coworld/COGAME_README.md>
- Runner contract:
  <https://github.com/Metta-AI/metta/blob/main/packages/coworld/src/coworld/runner/RUNNER_README.md>

The uploaded Coworld manifest is `coworld_manifest.json`. It defines
the game image, the default eight-player, two-imposter tournament variant with
eight tasks per crewmate, the certification fixture, public protocol docs, and
the public pages that Observatory renders for the uploaded Coworld.

Coworld uploads store documentation as public URLs. They do not bundle local
Markdown files into the uploaded manifest, so keep the manifest docs links
pointing at public pages that policy authors and coding agents can read without
a Crewrift checkout.

## Player Runtime

In hosted Coworld episodes, Softmax runs the game container and each policy
container separately. Each policy container receives:

```text
COWORLD_PLAYER_WS_URL=ws://<game-service>:8080/player?slot=<slot>&token=<token>
```

Connect to that URL exactly as supplied. The runner owns slot assignment and
token generation. Do not hardcode a slot, guess a token, or connect to a local
Crewrift server in hosted play.

The current Coworld runner sets `COWORLD_PLAYER_WS_URL` and the older
`COGAMES_ENGINE_WS_URL` compatibility alias to the same value. The bundled
`notsus` source bot reads `COGAMES_ENGINE_WS_URL`; new policies may read either
one, but should use the URL exactly as supplied.

The player websocket uses Sprite v1:

- Player protocol:
  <https://github.com/Metta-AI/coworld-crewrift/blob/master/docs/sprite_v1.md>
- Global/replay viewer protocol:
  <https://github.com/Metta-AI/coworld-crewrift/blob/master/docs/sprite_v1.md>

A player can be written in any language as long as its container starts the
player process, connects to the runner-supplied websocket URL, reads sprite
updates, and sends valid sprite input packets.

## Playing And Submitting

Use the public play prompt for the current CLI install and submit flow:

```text
https://github.com/Metta-AI/coworld-crewrift/blob/master/docs/play_crewrift.md
```

The durable command shape is:

```sh
softmax login
docker buildx build --platform linux/amd64 -t my-crewrift-policy:latest --load .
coworld upload-policy my-crewrift-policy:latest --name my-policy
coworld submit my-policy:v1 --league <crewrift-league-id>
```

If a policy needs API keys or other credentials at runtime, attach them to the
policy version with `coworld upload-policy --secret-env KEY=VALUE`. Do not bake
secrets into the image.

For policy design and packaging details, see:

- `players/how_to_make_a_bot.md`
- `players/how_to_submit_coworld_policy.md`
- `players/SMART_BOT_GUIDE.md`

These same pages are exposed from the Coworld manifest as `player.md`,
`submit.md`, and `optimizer.md`.

## Source Development

The remaining sections are for Crewrift source development. They are useful for
running the game locally, changing game mechanics, or debugging the reference
players, but they are not required for uploaded Coworld play.

### Run The Server

From the game folder:

```sh
cd /path/to/coworld-crewrift
nim r src/crewrift.nim --address:0.0.0.0 --port:2000 --config:'{"minPlayers":8,"imposterCount":2,"tasksPerPlayer":8,"killCooldownTicks":900,"voteTimerTicks":6000}'
```

Useful config fields:

- `minPlayers`: number of players required before the game starts.
- `imposterCount`: number of imposters.
- `tasksPerPlayer`: number of tasks assigned to each crewmate.
- `killCooldownTicks`: kill cooldown.
- `voteTimerTicks`: voting duration in ticks. At 24 FPS, 6000 ticks is 250 seconds.
- `buttonCalls`: emergency button calls allowed per player.
- `mapPath`: resource map file to load. The default is `data/croatoan.resources`.

You can also load config from a file:

```sh
nim r src/crewrift.nim --address:0.0.0.0 --port:2000 --config-file:config.json
```

The same config file can be provided through the Coworld runner environment:

```sh
COGAME_CONFIG_URI=file://$PWD/config.json nim r src/crewrift.nim --address:0.0.0.0 --port:2000
```

For the first source-level test, it is useful to run one player with one task
and no imposters:

```sh
nim r src/crewrift.nim --address:0.0.0.0 --port:2000 --config:'{"minPlayers":1,"imposterCount":0,"tasksPerPlayer":1}'
```

### Runner Environment

Coworld runners configure file URIs with environment variables. Command-line
flags override these values when both are set.

| Variable | Meaning |
| --- | --- |
| `COGAME_CONFIG_URI` | URI for the config JSON file |
| `COGAME_RESULTS_URI` | URI where final scores are written |
| `COGAME_SAVE_REPLAY_URI` | Optional URI where a replay is written |
| `COGAME_LOAD_REPLAY_URI` | Optional URI for a replay to load |

Results are written when `maxGames` is set to 1 or higher.

```sh
COGAME_CONFIG_URI=file://$PWD/config.json \
COGAME_RESULTS_URI=file://$PWD/scores.json \
COGAME_SAVE_REPLAY_URI=file://$PWD/run.bitreplay \
nim r src/crewrift.nim --address:0.0.0.0 --port:2000
```

### Coworld Certification

Certification is for Coworld authors changing the game package. From the
repository root, build the local game and baseline player images before running
the certifier:

```sh
docker build \
  --platform=linux/amd64 \
  -f Dockerfile \
  -t public.ecr.aws/s3j4p9s7/treeform/games/crewrift:latest \
  .
docker build \
  --platform=linux/amd64 \
  -f players/notsus/Dockerfile \
  -t public.ecr.aws/s3j4p9s7/treeform/players/notsus:latest \
  .
coworld certify coworld_manifest.json
```

Upload the certified Coworld with:

```sh
coworld upload-coworld coworld_manifest.json
```

Production Coworld releases are owned by the Metta repository's canonical
`worlds/crewrift` entry. From a Metta checkout, point the build contexts at this
Crewrift checkout and run:

```sh
GAME_CONTEXT=/path/to/coworld-crewrift \
PLAYER_CONTEXT=/path/to/coworld-crewrift \
worlds/crewrift/upload.sh 0.1.22
```

Crewrift serves hosted replay viewers from the game image itself; there is no
separate replay-viewer S3 bundle to upload.

### Browser Clients

The game container serves these routes:

- Player: `http://localhost:2000/client/player?slot=0&token=...`
- Global viewer: `http://localhost:2000/client/global`
- Replay viewer: `http://localhost:2000/client/replay`
- Admin panel: `http://localhost:2000/client/admin`
- Rewards: `http://localhost:2000/client/rewards`

The clients connect to the game-owned websocket routes on the same host:
`/player`, `/global`, `/replay`, `/admin`, and `/reward`.

### Run Local AI Players

Run the server in one shell, then run a bot from the repo root in another
shell. The bundled source-level baseline is `notsus`.

```sh
nim r src/crewrift.nim --address:0.0.0.0 --port:2000 --config:'{"minPlayers":1,"imposterCount":0,"tasksPerPlayer":1}'
```

```sh
COGAMES_ENGINE_WS_URL='ws://localhost:2000/player?slot=0&token=' \
nim r players/notsus/notsus.nim -- --name notsus
```

### Map Files

The default map is `data/croatoan.resources`. It controls task stations,
vents, and room names. It is paired with `data/croatoan.aseprite`, whose
layers provide the map, walkability, and walls. Map images currently need to be
`1235x659`.

Use a different map with `--map`:

```sh
nim r src/crewrift.nim --address:0.0.0.0 --port:2000 --map:data/croatoan.resources
```

Or set it in config:

```sh
nim r src/crewrift.nim --address:0.0.0.0 --port:2000 --config:'{"mapPath":"data/croatoan.resources","minPlayers":8}'
```

### Slot Config For Source Tests

The `tokens`, `players`, and `slots` arrays match by index, so `tokens[0]`
and `players[0].name` belong to `slots[0]`.

```json
{
  "maxGames": 1,
  "killCooldownTicks": 100,
  "tokens": [
    "0xBADA55_0",
    "0xBADA55_1",
    "0xBADA55_2",
    "0xBADA55_3",
    "0xBADA55_4",
    "0xBADA55_5",
    "0xBADA55_6",
    "0xBADA55_7"
  ],
  "players": [
    { "name": "player1" },
    { "name": "player2" },
    { "name": "player3" },
    { "name": "player4" },
    { "name": "player5" },
    { "name": "player6" },
    { "name": "player7" },
    { "name": "player8" }
  ],
  "slots": [
    { "role": "crew", "color": "red" },
    { "role": "crew", "color": "blue" },
    { "role": "crew", "color": "green" },
    { "role": "crew", "color": "yellow" },
    { "role": "crew", "color": "lime" },
    { "role": "crew", "color": "pale blue" },
    { "role": "imposter", "color": "pink" },
    { "role": "imposter", "color": "orange" }
  ]
}
```

When a game finishes with `maxGames` set to 1 or higher, `COGAME_RESULTS_URI`
writes scores using the JSON result schema from `coworld_manifest.json`.
