# Play Crewrift Prime — choose a policy, then partner with it

**Read this if you are the coding agent.** Your job is to play the **Crewrift
Prime** league well *with* your human. You do that by **adopting one of three
base policies** and then running its **optimizer loop** as an intelligent
partner — diagnosing it, forming hypotheses, editing it, evaluating against the
live league, and promoting what wins.

You must **choose exactly one** of the three policies below before you start.
They are not meant to be combined. Pick the one that best fits the human's goal
and the stack you want to work in, then jump straight into its optimizer.

- League: **Crewrift Prime** — `league_a12f5172-0907-4d04-8bcb-ca02f5360e3a`
- Coworld: `crewrift_prime:0.1.0` — `cow_fa681858-ae83-4f08-83cd-56fc4ec9d164`
- New here? Read [`play.md`](./play.md) and [`README.md`](./README.md) first for
  the rules and scoring.

---

## What "a policy with an optimizer" means

A **policy** is the bot that plays — it connects to the `/player` websocket,
perceives the game, and acts every tick. All three base policies already run a
full, legal, never-crash episode out of the box.

An **optimizer** is the *agent workflow* that improves a policy against the live
league. It is not more bot code; it is the loop **you** run as the human's
partner:

```
setup → understand the policy → run hosted evals → mine variance/opponents
      → form one falsifiable hypothesis → make one scoped edit → re-eval
      → promotion gate → submit to the league → record → repeat
```

The reference optimizer ships in this repo at
[`players/crewborg-aaln/optimizer/`](./players/crewborg-aaln/optimizer/). Its
[`AGENTS.md`](./players/crewborg-aaln/optimizer/AGENTS.md) +
[`playbooks/optimize-policy.md`](./players/crewborg-aaln/optimizer/playbooks/optimize-policy.md)
are the canonical loop, and its `skills/` are self-contained optimizer
methodology (eval sizing, hosted XP evals, opponent mining, promotion gate,
replay/artifact analysis). **Whichever base policy you choose, you drive it with
this same optimizer loop** — the loop is policy-agnostic; only the "where to
edit" map changes per policy.

The two Crewrift non-negotiables the optimizer enforces — because this game will
fool you otherwise:

- **High variance, role-asymmetric, 8 seats.** Never promote or reject on fewer
  than ~40 completed games; always disaggregate by role (imposter vs crewmate)
  and seat.
- **The `−100` lobby taint.** A disconnect/no-show scores the whole lobby `−100`
  (usually infra, not your policy). Exclude tainted episodes, report the rate,
  keep eval batches small and sequential.

---

## The three policies (choose one)

### 1. `crewborg` — the readable scripted baseline

[`players/crewborg/`](./players/crewborg/) · Python · no LLM required

A pure-Python policy on the **Cyborg cognitive stack**: a clean
`perception → world-state → integration → cognition → action` pipeline, one
focused module per stage, emitting first-class structured debug
(`CREWBORG_DEBUG` NDJSON on stderr, captured per-player into artifacts).
Crewmate logic (report → committed task routing, anti-oscillation), imposter
logic (flee body → clean isolated kill → fake task), and a multi-signal vote
policy that never abstains. There is a clean **`llm_vote_hook` seam** if you
later want an LLM in the loop.

- **Advantages:** the easiest to *understand and edit* — every decision is
  scripted and traceable to a stage and a debug line, so a hypothesis maps to a
  single file. Deterministic, no API key, no LLM latency or quota risk, cheapest
  to eval. Best **first choice** for most agents.
- **Where behavior lives:** `cognition.py` (decisions/voting),
  `integration.py` (suspicion/tracking), `action.py` (momentum nav), `nav.py`
  (A\*).

### 2. `crewbot3000` — the scripted baseline with an LLM-meeting seam

[`players/crewbot3000/`](./players/crewbot3000/) · Python · LLM optional, **off by default**

A self-contained fork of the high-ranking `crewborg` (same Cyborg stack:
`perceive → update_belief (+ tracking + event log + suspicion) → mode.decide →
resolve_action`, with crew/imposter modes and Bayesian suspicion), repackaged to
build from its own directory. Its differentiator is a **clean LLM meeting seam**
(`strategy/meeting/llm.py`): the scripted baseline runs with no key, but set
`CREWBOT3000_LLM_MEETINGS=1` + a backend (`ANTHROPIC_API_KEY` or Bedrock) and an
LLM drives meeting chat and voting behind a circuit breaker.

- **Advantages:** you get the same solid scripted floor **plus** a switch to add
  a real LLM partner in meetings — exactly where Crewrift rewards talking (the
  qualifier has a **talk gate**: a policy that never talks in a meeting does not
  qualify). Best choice if the human wants the bot to *reason and chat* in
  meetings, or wants to A/B "scripted vs LLM meetings" as distinct candidates.
- **Where behavior lives:** `strategy/meeting/` (LLM + chat), the imposter/crew
  `modes/`, `strategy/suspicion`, `agent_tracking`.

### 3. `notsus` — the stock reference bot

[`players/notsus/`](./players/notsus/) · Nim · no LLM

The reference baseline the engine ships, written in Nim. It parses the Sprite
protocol, handles every game screen (join/role/play/vote/result), does tasks,
can be an imposter, and navigates with A\* + a momentum controller. The README
is candid that it is intentionally "very stupid" — the point is to watch its
replays, see the dumb things it does, and improve from there.

- **Advantages:** the canonical "compare against this" bot; fast and dependency
  light; the right pick if you want to work **in Nim**, close to the engine, or
  you want a deliberately weak floor to beat. You can run the public `notsus`
  image as a fixed opponent in evals regardless of which policy you optimize.
- **Where behavior lives:** `players/notsus/notsus.nim` (single source); use the
  visual debugger (`-d:notsusGui`) to watch it.

> The fourth directory, [`players/crewborg-aaln/`](./players/crewborg-aaln/), is
> **not** one of the three choices — it is Aaron's personal `crewborg` fork and
> the home of the reference **optimizer** you will borrow. Read its optimizer,
> don't submit it as your policy.

### Quick chooser

- Want the simplest, most legible Python bot to iterate on fast → **`crewborg`**.
- Want an LLM reasoning/talking in meetings (and to clear the talk gate with
  smarts) → **`crewbot3000`**.
- Want to work in Nim / close to the engine, or want the canonical weak opponent
  → **`notsus`**.

---

## Adopt your policy and start partnering — right now

Once you have chosen, do these in order. The goal is for you to be a working
partner on *this* policy within a few minutes, with zero prior chat history
needed.

**1. Adopt the policy.** Set `POLICY=<crewborg|crewbot3000|notsus>` in your head
and treat `players/$POLICY/` as your working tree. Read its `README.md`
end-to-end — that is your map of the architecture and the files that control each
behavior.

**2. Run it locally so you've seen it play.** From the repo root, start the game
and run the bots (full instructions in [`README.md`](./README.md#run-the-game-locally));
then watch `http://localhost:2000/client/global`.

```sh
# Python policies (crewborg / crewbot3000): install, then run the module from
# the policy's coplayer_manifest.json "run" (it differs per policy):
pip install -r players/$POLICY/requirements.txt
#   crewborg:     python -m crewborg.main
#   crewbot3000:  python -m players.crewrift.crewbot3000.coworld.policy_player
COWORLD_PLAYER_WS_URL='ws://127.0.0.1:2000/player?slot=0&token=' python -m <run-module>
```

```sh
# notsus (Nim):
nim c players/notsus/notsus.nim
COWORLD_PLAYER_WS_URL='ws://127.0.0.1:2000/player?slot=0&token=' ./players/notsus/notsus.out
```

**3. Read the optimizer and make it yours.** Open
[`players/crewborg-aaln/optimizer/AGENTS.md`](./players/crewborg-aaln/optimizer/AGENTS.md)
and [`playbooks/optimize-policy.md`](./players/crewborg-aaln/optimizer/playbooks/optimize-policy.md).
The guide and IDs there are written for `crewborg-aaln`; **re-point them at your
chosen policy and at Crewrift Prime**: substitute your `players/$POLICY/` source
tree and the Crewrift Prime league/coworld IDs above for the `crewborg-aaln` /
base-Crewrift IDs.

**4. Collect live context — never trust memory.** Select the human's player and
read the live league before touching anything:

```sh
coworld status                       # confirm SOFTMAX_USER_API_TOKEN + active player
coworld player use <player_id>
coworld leagues league_a12f5172-0907-4d04-8bcb-ca02f5360e3a --json
coworld memberships --league league_a12f5172-0907-4d04-8bcb-ca02f5360e3a --active-only --json
coworld submissions --league league_a12f5172-0907-4d04-8bcb-ca02f5360e3a --mine --json
```

**5. Run the loop.** Diagnose with a small hosted XP eval, **inspect logs/stderr
FIRST** (a traceback or missing `run` attribute is a bug to fix before any
strategy), reconstruct behavior by joining replays × artifacts on `server_tick`,
write **one falsifiable hypothesis**, make **one scoped edit** in the file the
policy's README points to, re-eval against the champion + a broad guardrail at
the ~40-game floor, pass the promotion gate, then:

```sh
coworld upload-policy <image> --name $POLICY --run <argv0> --run <argv1>   # one --run per token
coworld submit $POLICY:vN --league league_a12f5172-0907-4d04-8bcb-ca02f5360e3a --auto-champion always --no-open-browser
```

Verify the new version has a non-null `run` attribute (a missing `run` is the
most common silent `−100` failure), confirm the active player and champion state,
record the run, and repeat.

**6. Use the replay expander to find the next improvement.** It is usually the
fastest way to see *why* the bot acted badly:

```sh
nim r tools/expand_replay.nim <replay.bitreplay>                 # human-readable timeline
nim r tools/expand_replay.nim --format jsonl <replay.bitreplay>  # one JSON event per line
```

That is the whole partnership: you adopt one base policy, learn how it plays,
and run its optimizer loop with your human — observe, evaluate, hypothesize,
edit, verify, promote — until it climbs the Crewrift Prime leaderboard.
