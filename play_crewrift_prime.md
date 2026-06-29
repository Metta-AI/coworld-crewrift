# Play Crewrift Prime fast path

**You are the coding agent.** Your job is to help your human get a real Crewrift
Prime policy and improve it with evidence. You build, upload, evaluate, and improve;
**the human drives submission to the league.** Submitting is the irreversible,
champion-making action — you prepare the candidate and the evidence and surface it, but
the human decides when to submit. Do **not** create a new bot from scratch, and do **not**
submit on your own initiative, unless the human explicitly asks.

The fast path is:

```
choose crewborg, crewborg-aaln, or notsus -> build -> upload with explicit run argv
    -> human submits to enter -> confirm qualification -> run small hosted evals
    -> make one scoped change -> verify over enough completed games
    -> human submits the winner
```

> **Ultimate authority:** the optimizer for whichever policy you adopt — its `AGENTS.md` /
> `README.md` (e.g. [`players/crewborg/AGENTS.md`](./players/crewborg/AGENTS.md),
> [`players/crewborg-aaln/optimizer/AGENTS.md`](./players/crewborg-aaln/optimizer/AGENTS.md)) — is
> the final word on the operating model, **including who submits and when.** Read it as soon as you
> pick a policy; where it and this file disagree, follow it.

Commands below use `coworld` on PATH. If your environment normally runs the CLI
as `uv run coworld`, use that prefix. Do not move into an older `coworld-player`
checkout or rebuild a stale venv just to understand this game.

- Game repo: <https://github.com/Metta-AI/coworld-crewrift>
- League: **Crewrift Prime** - `league_a12f5172-0907-4d04-8bcb-ca02f5360e3a`
- Current Prime package observed 2026-06-26: `crewrift_prime:0.4.9` -
  `cow_5e21fb01-1fdf-4441-9acc-2e0cd66832ed`
- Older docs may mention `crewrift_prime:0.1.0` /
  `cow_fa681858-ae83-4f08-83cd-56fc4ec9d164`; treat those as stale for Prime.

## What not to spend time on

The slow onboarding path took about an hour because the agent:

- read broad remote docs twice before acting;
- switched to an older `coworld-player` checkout and repaired its venv;
- adapted an old policy instead of using this repo's shipped policies;
- reverse-engineered Sprite-v1, coordinate frames, and local episode schemas;
- ran full 10,000-tick local games under Docker emulation and wrote a custom
  mixed-roster harness before submitting anything;
- debugged CLI/server upload drift as if it might be policy behavior.

Avoid that path. This file contains enough game context to start. Use deeper
docs only when this file names them.

## Minimum game facts

Crewrift is an Among-Us-style, 8-seat, hidden-role game:

- **Crewmates** do tasks, report bodies, talk/vote in meetings, and win by
  finishing tasks or ejecting all imposters.
- **Imposters** kill on cooldown, blend in, may use vents, and win by killing
  enough crew.
- Scoring: `+100` win, `+1` completed task, `+10` imposter kill, `-10` missed
  vote/skip, `-1` per stuck-idle interval while tasks remain.
- Prime qualification is event-driven after submission. The commissioner runs a
  self-play XP check and expects meeting participation, at least some imposter
  hunting, and task completion. A policy that never votes/talks does not qualify.

You do not need to reread the full rules before adopting the default policy.
Read [`README.md`](./README.md#crewrift-rules) only when changing game strategy.

## Choose the starting policy

The three shipped policies are all viable bases. Pick one deliberately, then get it into the
league early — build, upload, and let the human submit — before sinking time into deeper research.

### `crewborg` - shared lineage, human-driven, with an LLM gameplay commander

Use **`players/crewborg/`** for the full crewborg Python cognitive stack (perception → belief →
suspicion → strategy → modes → action) plus a complete in-folder optimization toolkit. It
**shares a lineage with `crewborg-aaln`**, so both carry that cognitive stack, an in-meeting
chat/vote LLM, a fitted suspicion model, an optimizer workspace, and structured tracing. What's
particular to **this** copy:

- **An LLM gameplay commander** (`crewborg/strategy/commander/`) — a *background-thread* LLM that
  biases the deterministic modes' priorities from a slower outer loop **without ever stalling
  per-tick play**; off by default (byte-identical deterministic play until enabled). This Playing-phase
  steering layer is specific to crewborg (it's alongside the shared in-meeting chat/vote LLM).
- **A human-in-the-loop optimizer** — an 11-skill toolkit (survey → diagnose → experiment →
  matched A/B; build/upload/submit; a queryable DuckDB/Parquet **event warehouse**) plus a
  `suspicion_lab/` refit pipeline, built around a **human driving the ideation, experimentation,
  and analysis** while the agent builds observability and holds the correctness gate. Start at
  [`players/crewborg/README.md`](./players/crewborg/README.md) →
  [`players/crewborg/AGENTS.md`](./players/crewborg/AGENTS.md).

Tradeoff: like `crewborg-aaln`, it's a large Python stack — more surface than `notsus` when you
want to read every line.

### `crewborg-aaln` - stronger league baseline, richer optimizer

Use **`players/crewborg-aaln/`** when you want the fastest path to a competitive
league policy. It already handles Sprite-v1 parsing, world-coordinate
localization, walkability/pathing, role detection, legal actions every tick,
meeting/vote fallbacks, task routing, imposter behavior, artifact logging,
Docker packaging, and a policy-specific optimizer workspace.

Tradeoff: it is a larger Python cognitive stack with more historical strategy
knobs. That is powerful once you are optimizing, but it is not the smallest base
when you want to understand every line.

### `notsus` - credible mechanics-first base, smaller surface

Use **`players/notsus/`** when you want a simpler, engine-close starting point
with strengths `crewborg-aaln` does not have:

- **Single Nim policy close to the game code.** Fewer framework layers, easier
  to reason about end-to-end behavior and protocol details.
- **Strong mechanics baseline.** It parses Sprite v1, uses the walkability map,
  navigates with A*/momentum control, completes tasks, handles meetings/voting,
  and can play imposter.
- **Visual debugger.** `nim r -d:notsusGui players/notsus/notsus.nim` shows the
  viewport, walkability mask, position, visible objects, goal, A* path, selected
  step, input mask, velocity, and stuck state. That makes movement/task bugs much
  faster to inspect than in a headless-only stack.
- **Good substrate for mechanics work.** If your hypothesis is about navigation,
  task routing, kill positioning, task radar interpretation, or low-level
  protocol handling, `notsus` may be the cleaner base even if its current
  strategy is deliberately simple.
- **Simple hosted run contract.** The run argv is just `/bin/notsus`, and the
  public image/build path is straightforward.

Tradeoff: it has less optimizer infrastructure and less accumulated social/vote
strategy than `crewborg-aaln`, so you will add more of that yourself.

Do not port old code from another checkout while these two bases exist.

## First submission, copy-paste paths

These are the build → upload → submit commands. **The `coworld submit` step is the human's
call** — run it at the human's direction, not on your own; build and upload are yours. Run from
the repo root.

```sh
export LEAGUE_ID=league_a12f5172-0907-4d04-8bcb-ca02f5360e3a
```

Check auth and the active player before mutating anything:

```sh
coworld status
coworld player list --json
coworld leagues "$LEAGUE_ID" --json
```

If the active player is not your human's player, select it:

```sh
coworld player use <player_id>
```

### Submit `crewborg`

```sh
export POLICY=crewborg
docker build --platform=linux/amd64 \
  -f players/crewborg/crewborg/coworld/Dockerfile -t "$POLICY:prime" players/crewborg
coworld upload-policy "$POLICY:prime" --name "$POLICY" \
  --run python \
  --run -m \
  --run crewborg.coworld.policy_player
coworld submit "$POLICY:vN" --league "$LEAGUE_ID" \
  --auto-champion always --no-open-browser
```

Run argv from
[`players/crewborg/coplayer_manifest.json`](./players/crewborg/coplayer_manifest.json); each
token gets its own `--run`. Or use crewborg's in-folder **`build-and-upload`** skill (build +
upload) and **`coworld-policy-lifecycle`** (the gated submit + qualification monitoring).

### Submit `crewborg-aaln`

```sh
export POLICY=crewborg-aaln
docker build --platform=linux/amd64 -t "$POLICY:prime" "players/$POLICY"
```

Upload it with the exact run argv from
[`players/crewborg-aaln/coplayer_manifest.json`](./players/crewborg-aaln/coplayer_manifest.json).
Each argv token gets its own `--run` flag; this prevents the common silent
hosted `-100` start failure.

```sh
coworld upload-policy "$POLICY:prime" --name "$POLICY" \
  --run python \
  --run -m \
  --run players.crewrift.crewborg.coworld.policy_player
```

The upload output will give a new version such as `crewborg-aaln:v7`. Submit
that exact version:

```sh
coworld submit "$POLICY:vN" --league "$LEAGUE_ID" \
  --auto-champion always --no-open-browser
```

### Submit `notsus`

This is the mechanics-first alternative, not just a throwaway baseline:

```sh
export POLICY=notsus
docker build --platform=linux/amd64 -f players/notsus/Dockerfile -t notsus:prime .
coworld upload-policy "$POLICY:prime" --name "$POLICY" --run /bin/notsus
coworld submit "$POLICY:vN" --league "$LEAGUE_ID" \
  --auto-champion always --no-open-browser
```

After either path, confirm the submission and qualification state:

```sh
coworld submissions --league "$LEAGUE_ID" --policy "$POLICY:vN" --json
coworld memberships --league "$LEAGUE_ID" --policy "$POLICY:vN" --json
```

If `upload-policy` fails before pushing with an ECR/pydantic
`authorization_token` or registry-shape error, treat it as Coworld CLI/server
drift, not a policy problem. First try a newer `coworld` CLI if available. If the
current CLI is still broken, follow
[`players/crewborg-aaln/optimizer/skills/coworld-operations/SKILL.md`](./players/crewborg-aaln/optimizer/skills/coworld-operations/SKILL.md)
for the policy-version upload contract and record the workaround; do not rewrite
the player.

## Optional smoke before submitting

Skip long local A/B harnesses during onboarding. If you need a smoke check,
prefer one of these:

- Build-only check for `crewborg`:
  `docker build --platform=linux/amd64 -f players/crewborg/crewborg/coworld/Dockerfile -t crewborg:prime players/crewborg`
- `crewborg` unit tests after edits:
  `cd players/crewborg && PYTHONPATH="$PWD" python -m pytest crewborg/tests/`
  (or its `coworld-local-run` skill for a Gate-1 connect→play→exit smoke)
- Build-only check for `crewborg-aaln`:
  `docker build --platform=linux/amd64 -t crewborg-aaln:prime players/crewborg-aaln`
- Build-only check for `notsus`:
  `docker build --platform=linux/amd64 -f players/notsus/Dockerfile -t notsus:prime .`
- `crewborg-aaln` unit tests after edits:
  `cd players/crewborg-aaln && python -m pytest players/crewrift/crewborg/tests/`
- `notsus` fast source check after edits:
  `nim c players/notsus/notsus.nim`
- Hosted 1-3 episode XP smoke after upload if you are unsure it starts. Use it
  only to detect crashes, `-100`, missing `run`, or qualification-gate failures.

Do not promote or reject a strategy from a 1-3 episode smoke. Crewrift variance
is too high.

## Start optimizing without rediscovery

For `crewborg`, read exactly these first:

1. [`players/crewborg/README.md`](./players/crewborg/README.md) and
   [`players/crewborg/AGENTS.md`](./players/crewborg/AGENTS.md) — orientation, the
   evaluate→diagnose→experiment→improve loop, and the full skills/tools catalog.
2. [`players/crewborg/docs/best_practices.md`](./players/crewborg/docs/best_practices.md) —
   the measurement/diagnosis/hypothesis disciplines (decompose by role, ops vs behavior, no
   causal claim without the falsifying query).
3. [`players/crewborg/crewborg/design.md`](./players/crewborg/crewborg/design.md) — the
   cognitive-stack architecture and where each behavior lives.

For `crewborg-aaln`, read exactly these first:

1. [`players/crewborg-aaln/optimizer/guide/SKILL.md`](./players/crewborg-aaln/optimizer/guide/SKILL.md)
   - policy architecture, build/test/upload commands, runtime flags, and the
   file-to-edit map.
2. [`players/crewborg-aaln/optimizer/CREWBORG_INSIGHTS.md`](./players/crewborg-aaln/optimizer/CREWBORG_INSIGHTS.md)
   - known tournament lessons and eval traps.
3. [`players/crewborg-aaln/optimizer/playbooks/optimize-policy.md`](./players/crewborg-aaln/optimizer/playbooks/optimize-policy.md)
   - the one-loop optimization procedure.

For `notsus`, read exactly these first:

1. [`players/notsus/README.md`](./players/notsus/README.md)
   - the bot's mechanics, debugger, navigation model, and strategy notes.
2. [`players/notsus/notsus.nim`](./players/notsus/notsus.nim)
   - the single-file policy surface.
3. [`players/notsus/tools/run.nim`](./players/notsus/tools/run.nim)
   - useful automation patterns for build/upload/eval when you stay on the Nim
   path.

Then run this loop:

```
read live standings/submissions -> inspect recent evals/replays/artifacts
  -> write one falsifiable hypothesis -> make one small edit or flag flip
  -> build/upload candidate -> evaluate in small hosted batches
  -> aggregate by role/seat and penalties -> human submits the winner if it clears the gate
```

## Where to make the first changes

Map the hypothesis to the smallest surface:

For `crewborg`:

| Goal | Start here |
|---|---|
| Orient + run the optimization loop | `players/crewborg/README.md` + `AGENTS.md` (the full skills/tools catalog) |
| Flip a behavior via env flags | `players/crewborg/crewborg/coworld/Dockerfile` / the README env-var table (`CREWBORG_LLM_MEETINGS`, `CREWBORG_LLM_COMMANDER`, tracing levels) |
| Improve meeting votes / chat | `players/crewborg/crewborg/strategy/meeting/` |
| Improve suspicion | `…/strategy/suspicion.py` + `social_evidence.py` (refit via `players/crewborg/suspicion_lab/`) |
| Change role/phase mode selection | `…/strategy/rule_based.py` |
| Change a stance directly | `…/modes/` (`hunt.py`, `evade.py`, `report_body.py`, …) |
| Improve imposter hunting | `…/modes/hunt.py`, `search.py`, `…/strategy/opportunity.py`, `trajectory.py` |
| Improve pathing / stuck recovery | `…/nav.py`, `action.py` |
| Steer play with the LLM commander | `…/strategy/commander/` |
| Find WHERE to improve (evidence-first) | the `crewrift-survey` → `crewrift-diagnose` → `crewrift-experiment` skills under `players/crewborg/skills/` |

For `crewborg-aaln`:

| Goal | Start here |
|---|---|
| Flip a known behavior variant | `players/crewborg-aaln/Dockerfile` `ENV` flags (`BE_DUMB`, `CREWBORG_LLM_MEETINGS`, `CREWBORG_DICK_MODE`) |
| Improve meeting votes | `players/crewborg-aaln/players/crewrift/crewborg/strategy/meeting/vote_policy.py` |
| Improve suspicion/flee/report choices | `players/crewborg-aaln/players/crewrift/crewborg/strategy/suspicion.py` and `strategy/rule_based.py` |
| Change role/phase mode selection | `players/crewborg-aaln/players/crewrift/crewborg/strategy/rule_based.py` |
| Change a stance directly | `players/crewborg-aaln/players/crewrift/crewborg/modes/` |
| Improve pathing, stuck recovery, task arrival | `players/crewborg-aaln/players/crewrift/crewborg/nav.py` and `action.py` |
| Improve imposter hunting | `strategy/opportunity.py`, `strategy/trajectory.py`, `modes/hunt.py`, `modes/search.py` |
| Add evidence before deciding | `events.py`, `trace.py`, `artifact.py` |

Keep one hypothesis per candidate. Broad rewrites make the eval uninterpretable.

For `notsus`:

| Goal | Start here |
|---|---|
| Inspect movement/task behavior visually | `nim r -d:notsusGui players/notsus/notsus.nim` |
| Change navigation, task routing, momentum, role handling, vote UI, or imposter behavior | `players/notsus/notsus.nim` |
| Keep the hosted package simple | `players/notsus/Dockerfile` and `players/notsus/coplayer_manifest.json` |
| Automate repeatable build/upload/eval work | `players/notsus/tools/run.nim` |

`notsus` is most attractive when the next improvement is low-level and
observable: reaching tasks faster, avoiding oscillation, selecting cleaner kill
positions, interpreting task radar better, or using the visual debugger to close
the loop on a movement bug.

## Eval rules that prevent wasted time

- Crewrift is high-variance, 8-seat, and role-asymmetric. A real candidate or
  guardrail verdict needs **40-80 completed games**, not one lucky lobby.
- Run XP requests in small sequential batches, especially for LLM policies.
  Whole-lobby `-100` sweeps are usually infrastructure contention; retry them
  and report the taint rate.
- Always break results down by role, seat, win rate, vote-timeout penalties,
  stuck-idle penalties, and `-100` failures. A flat mean hides the bug.
- Inspect hosted stdout/stderr before strategy analysis. Tracebacks, malformed
  actions, provider timeouts, and missing `run` are runtime bugs.
- Do not count a policy as worse because the whole episode was tainted. Do count
  only-our-slot `-100` as our policy/upload/runtime failure until logs prove
  otherwise.

The concrete XP request shapes and artifact commands live in
[`players/crewborg-aaln/optimizer/skills/coworld-operations/SKILL.md`](./players/crewborg-aaln/optimizer/skills/coworld-operations/SKILL.md)
and the Crewrift sample-size rules live in
[`players/crewborg-aaln/optimizer/games/crewrift/skills/crewrift-eval-design/SKILL.md`](./players/crewborg-aaln/optimizer/games/crewrift/skills/crewrift-eval-design/SKILL.md).

## Replay and artifact tools

Use these only after you have a specific failure to inspect:

- `tools/expand_replay.nim` - turns a replay into tick/event rows.
  ```sh
  nim r tools/expand_replay.nim <replay.bitreplay>
  nim r tools/expand_replay.nim --format jsonl --snapshot-every 1 <replay.bitreplay>
  ```
- `reporters/eventlog/` - hosted reporter stream with events such as
  `player_joined`, `kill`, `body`, `vote_cast`, `chat`, and `score`.
- `grader/graders/crewrift/` - ranks episodes worth opening by score, task,
  kill, and vote signals.
- `crewborg` analysis - its in-folder skills are the primary path and are batch-first
  (start wide, then drill): `crewrift-survey` (fast per-policy/role overview + win heat map),
  a `crewrift-event-warehouse` (queryable DuckDB/Parquet event store re-keyed by policy/role),
  then `crewrift-diagnose`/`crewrift-experiment`. See `players/crewborg/AGENTS.md`.
- `crewborg-aaln` artifacts - `trace.db` + `summary.json`, joined to replay
  events by server tick. Start from the optimizer guide before querying these.

## Done for onboarding

Onboarding is complete when:

1. a shipped policy is uploaded with a non-null `run` attribute;
2. the human has submitted the exact uploaded version to Crewrift Prime for the
   correct player (submission is the human's call — you prepare and surface it);
3. the submission/qualification state is confirmed;
4. the next optimization step is recorded as one falsifiable hypothesis or one
   named eval to run.

Do not spend the first hour re-learning the game engine. Get the working baseline
ready to submit and surface it to the human, then optimize from evidence.
