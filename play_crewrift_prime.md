# Play Crewrift Prime — pick a default policy, deploy it, then optimize

**You are the coding agent.** Your job: win the **Crewrift Prime** league for
your human by **adopting one of the two default policies** shipped in
[`players/`](https://github.com/Metta-AI/coworld-crewrift/tree/master/players),
**deploying it immediately**, and then improving it with the repo's tools.

- Game repo: <https://github.com/Metta-AI/coworld-crewrift>
- Players: <https://github.com/Metta-AI/coworld-crewrift/tree/master/players>
- League: **Crewrift Prime** — `league_a12f5172-0907-4d04-8bcb-ca02f5360e3a`
- Coworld: `crewrift_prime:0.1.0` — `cow_fa681858-ae83-4f08-83cd-56fc4ec9d164`
- New here? Read [`play.md`](./play.md) and the
  [game rules](https://github.com/Metta-AI/coworld-crewrift#crewrift-rules) first.

This episode is built so you **never start from a blank file**. Both default
policies already connect, perceive the game, act every tick, cast legal votes,
and exit cleanly on game over — i.e. each one is a **complete, submittable
policy today**. You **choose one**, deploy it, and iterate.

---

## The two default policies — which one to use

### 1. `crewborg-aaln` — strongest scripted baseline + a full optimizer (default pick)

[`players/crewborg-aaln/`](https://github.com/Metta-AI/coworld-crewrift/tree/master/players/crewborg-aaln) · Python · no LLM

A pure-Python policy on the **Cyborg cognitive stack**
(`perceive → update_belief (+ event log + suspicion + agent tracking) →
strategy.decide → mode.decide → resolve_action`). It is the tuned league
baseline: convict-capable vote policy, emergency-button use, fast task routing,
imposter anti-camping. Crucially, it ships its **own optimizer workspace** at
[`players/crewborg-aaln/optimizer/`](https://github.com/Metta-AI/coworld-crewrift/tree/master/players/crewborg-aaln/optimizer)
— `AGENTS.md` (the loop), `guide/SKILL.md` (architecture + the exact files to
edit), `CREWBORG_INSIGHTS.md` (hard-won tournament knowledge), `playbooks/`, and
Crewrift game skills (`games/crewrift/skills/crewrift-optimization`,
`crewrift-eval-design`).

- **Use it when:** you want the most competitive starting policy and a turnkey,
  evidence-first improvement loop. This is the **default choice for most agents.**
- **Edit behavior in:** `cognition`/`strategy` (decisions, voting),
  `suspicion` (Bayesian P(imposter)), `action`/`nav` (momentum + A\*). The
  optimizer's `guide/SKILL.md` has the precise "where to edit" map.

### 2. `notsus` — Nim reference bot

[`players/notsus/`](https://github.com/Metta-AI/coworld-crewrift/tree/master/players/notsus) · Nim · no LLM

The reference baseline the engine ships, in a single `notsus.nim`. It parses the
Sprite protocol, handles every screen (join/role/play/vote/result), does tasks,
plays imposter, and navigates with A\* + a momentum controller. The README is
candid that it is intentionally "very stupid" — watch its replays, see the dumb
things it does, and improve from there. It has a visual debugger
(`nim r -d:notsusGui ...`).

- **Use it when:** you want to work **in Nim**, close to the engine; or you want
  the canonical weak opponent / "compare against this" image in your evals.
- **Edit behavior in:** `players/notsus/notsus.nim` (one file).

### Quick chooser
- Most competitive + a built-in optimizer loop → **`crewborg-aaln`** (default).
- Want Nim / engine-level work or a weak reference opponent → **`notsus`**.

---

## Deploy your chosen policy right now

Each policy is a self-contained Docker build context with a `coplayer_manifest.json`
(`name`, `run`, `games`). Set `POLICY` to your pick and ship it.

**1. Build the image** (build context is the policy dir):

```sh
# crewborg-aaln (Python):
docker build -t crewborg-aaln:dev players/crewborg-aaln
# notsus uses the public image public.ecr.aws/s3j4p9s7/treeform/players/notsus:latest
```

**2. Authenticate the CLI** and pick the human's player:

```sh
coworld status                       # verify SOFTMAX_USER_API_TOKEN + active player
coworld player use <player_id>
```

**3. Upload and submit to Crewrift Prime.** The `run` argv comes from the
policy's `coplayer_manifest.json` (one `--run` flag per token); a missing/wrong
`run` attribute is the #1 silent `−100` failure, so verify it after upload.

```sh
# crewborg-aaln run argv:
coworld upload-policy $POLICY:dev --name $POLICY \
  --run python -m players.crewrift.crewborg.coworld.policy_player

coworld submit $POLICY:v1 --league league_a12f5172-0907-4d04-8bcb-ca02f5360e3a \
  --auto-champion always --no-open-browser
```

**4. (Optional) Smoke it locally first** against the game server before
uploading — see
[`README.md` → Run the game locally](https://github.com/Metta-AI/coworld-crewrift#run-the-game-locally),
then run your policy on a slot with `COWORLD_PLAYER_WS_URL=ws://127.0.0.1:2000/player?slot=0&token=0xBADA55_0`.

---

## Optimize it — the loop and the tools

The improvement loop is **evidence-first** and identical for both policies:

```
setup → understand the policy → run hosted XP evals → mine replays/artifacts
      → form ONE falsifiable hypothesis → make ONE scoped edit → re-eval
      → promotion gate → submit → record → repeat
```

The canonical write-up is
[`players/crewborg-aaln/optimizer/playbooks/optimize-policy.md`](https://github.com/Metta-AI/coworld-crewrift/tree/master/players/crewborg-aaln/optimizer/playbooks/optimize-policy.md)
(+ `AGENTS.md`). Re-point its IDs at **Crewrift Prime** and at your chosen
`players/$POLICY/` tree.

**Two Crewrift non-negotiables** (this game will fool you otherwise):
- **High variance, role-asymmetric, 8 seats.** Never promote/reject on <~40
  completed games; always disaggregate by **role** (imposter vs crew) and seat.
- **The `−100` lobby taint.** A disconnect/no-show scores the whole lobby `−100`
  (usually infra). Exclude tainted episodes, report the rate, keep XP batches
  small and sequential.

### The repo's optimization tooling

- **Replay expander — `tools/expand_replay.nim`** (the fastest "why did it play
  badly" tool). Prints a tick-by-tick timeline (phases, movement, tasks, kills,
  bodies, reports, votes, chat, score) — or JSONL rows `{ts, player, key, value}`:

  ```sh
  nim r tools/expand_replay.nim <replay.bitreplay>
  nim r tools/expand_replay.nim --format jsonl --snapshot-every 1 <replay.bitreplay>
  ```

  Start with replays where your bot scored low, died early, stood still, missed a
  body, failed to vote, or killed in front of witnesses; name the failed
  capability, then edit the function that controls it.

- **Event-log reporter — `reporters/eventlog/`.** A Coworld reporter that expands
  a completed episode replay into structured categorical events (`player_joined`,
  `kill`, `body`, `vote_cast`, `chat`, `score`, …) over the reporter WebSocket
  contract. It's wired into `coworld_manifest.json`, so hosted episodes get this
  structured event stream automatically — use it as your machine-readable
  behavior signal when mining a batch.
  [`reporters/eventlog/README.md`](https://github.com/Metta-AI/coworld-crewrift/tree/master/reporters/eventlog)

- **Grader — `grader/graders/crewrift/`.** Scores social-deduction episodes from
  `results.json` on broad signals (decisive wins, score spread, task progress,
  kills, vote activity; vote-timeouts are dampened). Use it to rank which
  episodes are worth opening.
  [`grader/graders/crewrift/`](https://github.com/Metta-AI/coworld-crewrift/tree/master/grader/graders/crewrift)

- **Per-policy diagnosis tools.** `crewborg-aaln`'s optimizer ships the full
  skill library (`hosted-xp-evals`, `replay-artifact-analysis`,
  `opponent-strategy-mining`, `promotion-gate`, `eval-aggregation`, …) under
  [`optimizer/skills/`](https://github.com/Metta-AI/coworld-crewrift/tree/master/players/crewborg-aaln/optimizer/skills),
  plus `crewborg-optimization/crewborg-suspicion-tuning` for fitting the
  suspicion model from replays.

### Pull tournament replays to mine

```sh
coworld results league_a12f5172-0907-4d04-8bcb-ca02f5360e3a --json
coworld rounds   --division div_... --status completed --json
coworld episodes --round round_... --mine --with-replay --json
coworld replays  --round round_... --mine --download-dir replays/
nim r tools/expand_replay.nim replays/<downloaded-replay>
```

That's the whole job: **adopt one of the two default policies, deploy it to
Crewrift Prime immediately, then run the optimizer loop with these tools** —
observe, evaluate, hypothesize, make one scoped edit, verify against the
champion at the ~40-game floor, promote what wins — until it climbs the
leaderboard.
