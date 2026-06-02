# Crewrift - AI Social Deductions Sandbox

![Crewrift](docs/crewrift.png)

Crewrift is a Coworld social deduction game.
Crewmates complete tasks, report bodies, chat during meetings, and vote out suspects.
Imposters blend in, use cooldown-limited kills, and survive the vote.

## Crewrift Rules

Crewrift is a multiplayer social deduction Coworld used for social AI benchmarks. Sandbox to make the AI learn and grow in a confined game environment.

Most players are **crewmates**, among them there are some **imposters**.
**Crewmates** win by completing tasks or by voting out all **imposters**.
**Imposters** win by eliminating enough crew players.

### Starting the Game

Players connect to the game and wait until there are enough players to start the game.
Then the game shows them their assigned role, either a crew or an imposter role.

### Being a Crewmate

The players start out next to the **emergency button**.
Any player can press the **emergency button** to start the vote, but they can only do that once per game.
The crew players need to complete tasks, which requires looking at the task radar view, going there, and navigating around walls.
Once at the task, the crewmate needs to press the A button and stand still until the task progress bar is complete.
If the crewmate moves for any reason, they need to restart the task.

*Strategy:* It is advantageous for the **crewmates** to stick together. This way, if someone kills one of the **crewmates** in a group, all the other **crewmates** will know who that is. It is also advantageous to stick together so that during the voting phase the **crewmates** can vouch for each other.

### Being an Imposter

An imposter has a different role.
First, they should blend in by acting like a crewmate.
The **imposters** have a kill progress bar, it starts out empty and slowly fills.
When the kill progress bar is fully filled, they can kill.
They need to stand next to the victim and press the A button to kill.
**Imposters** also have access to vents, which allow them to move around the map faster and hide from **crewmates**.

*Strategy:* It is advantageous for **imposters** to blend in and do almost everything that the crewmate does. Even maybe "faking" doing tasks, standing still where a task needs to be done. The imposters need to kill quickly because the cooldown timers are long, and if they wait too long, the **crewmates** will complete all the tasks and the **imposters** will lose. When an **imposter** kills someone and there is a body, and then they should run away from that location as far as possible. Either using vents or using normal corridors, they need to be away from the bodies so they are not implicated in the crime.

### Voting

Once someone is killed, a body appears.
Both crewmate and imposter can report the body.
When **imposters** do, it is called "self report".
Once, the body is reported, the voting starts.
Voting can also start if someone hits the **emergency button**.
During the voting phase, players can talk to each other and vote.
They can use the left and right keys to select whoever they want to vote for, or they can choose to skip.
Once a vote is cast, they can't change their vote.

*Strategy:* It is advantageous for **crewmates** to be extremely careful about voting because voting another **crewmate** out will probably lose them the game. They need to be absolutely sure.
While for the **imposters**, it is beneficial to vote all the time, vote against any **crewmate** because any **crewmate** that's eliminated is one less they have to kill and means a much higher chance of winning the game.
It is beneficial for the **imposters** to try to confuse the other **crewmates**, and it is important for the **crewmates** to perform good deductive reasoning to figure out who are the **imposters**.

### Scoring

The game scores players based on their performance.

* Winning the game +100 points.
* Completing a task +1 point.
* Killing a crewmate +10 points.
* Not voting and not skipping votes -10 points.
* Standing still and having tasks to do -1 point every 10 seconds.

Winning gives you the ultimate reward, but you can use the rewards for doing tasks and killing to train your agents.

## Run the game locally.

Run the game entirely locally, without using Docker, following these simple steps.

First, you need to install Nim and sync the lock file. I recommend using Nimby.
See https://github.com/treeform/nimby for more information.

```sh
nimby use 2.2.10
nimby sync -g nimby.lock
```

Then, you need to build and run the game with the repo config file.

```sh
COGAME_HOST=0.0.0.0 \
COGAME_PORT=2000 \
COGAME_CONFIG_URI=file://$PWD/config.json \
nim r src/crewrift.nim
```

Then, let's build the example bot:

```sh
nim c players/notsus/notsus.nim
```

Then, you need to run at least 8 bots in parallel.
The source build writes the binary to `players/notsus/notsus.out`.
The repo config assigns slots 0 through 7 to `player1` through `player8`
with matching `0xBADA55_*` tokens.

```sh
for i in 0 1 2 3 4 5 6 7; do
  token="0xBADA55_$i"
  url="ws://localhost:2000/player?slot=$i&token=$token"
  COWORLD_PLAYER_WS_URL="$url" ./players/notsus/notsus.out &
done
wait
```

Then you can monitor the game with the global viewer at http://localhost:2000/client/global.

You can also just choose to run the game with 7 bots and 1 human player:

Use one configured player URL in the browser.
For example, open `http://localhost:2000/client/player?slot=0&token=0xBADA55_0`.

## Run the game with Docker.

Do not want to install or compile Nim? Use the public Softmax images.
These commands use the repo `config.json` file and do not build any images.
Run them from the repo root.

First, create a local Docker network.

```sh
docker network create crewrift-local || true
```

Then, run the game server.

```sh
docker run --rm -d \
  --name crewrift-server \
  --network crewrift-local \
  -p 2000:2000 \
  -v "$PWD/config.json:/workspace/crewrift/config.json:ro" \
  -e COGAME_HOST=0.0.0.0 \
  -e COGAME_PORT=2000 \
  -e COGAME_CONFIG_URI=file:///workspace/crewrift/config.json \
  public.ecr.aws/s3j4p9s7/treeform/games/crewrift:latest
```

Then, run 8 `notsus` bots in parallel.

```sh
for i in 0 1 2 3 4 5 6 7
do
  token="0xBADA55_$i"
  url="ws://crewrift-server:2000/player?slot=$i&token=$token"
  docker run --rm -d \
    --name "crewrift-bot-$i" \
    --network crewrift-local \
    -e COWORLD_PLAYER_WS_URL="$url" \
    public.ecr.aws/s3j4p9s7/treeform/players/notsus:latest
done
```

Then you can monitor the game with the global viewer at http://localhost:2000/client/global.

To stop the local Docker run:

```sh
docker rm -f crewrift-server 2>/dev/null || true
for i in 0 1 2 3 4 5 6 7
do
  docker rm -f "crewrift-bot-$i" 2>/dev/null || true
done
```
