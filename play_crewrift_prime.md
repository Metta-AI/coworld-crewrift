# Play Crewrift Prime fast path

**You are the coding agent.** Your job is to get your human a real Crewrift
Prime policy quickly, then improve it with evidence. Do **not** create a new bot
from scratch unless the human explicitly asks for that.

The fast path is:

```
use players/crewborg-aaln -> build -> upload with explicit run argv -> submit
    -> confirm qualification -> run small hosted evals -> make one scoped change
    -> verify over enough completed games -> submit the winner
```

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

## Default policy decision

Use **`players/crewborg-aaln/`** unless you have a specific reason not to.

It is the strongest shipped scripted baseline and already handles the hard parts
that cost agents time: Sprite-v1 parsing, world-coordinate localization,
walkability/pathing, role detection, legal actions every tick, meeting/vote
fallbacks, task routing, imposter behavior, artifact logging, Docker packaging,
and a policy-specific optimizer workspace.

Use **`players/notsus/`** only if you intentionally want the Nim reference bot or
a weak baseline to compare against. Do not port old code from another checkout
while `crewborg-aaln` exists.

## First submission, copy-paste path

Run from the repo root.

```sh
export LEAGUE_ID=league_a12f5172-0907-4d04-8bcb-ca02f5360e3a
export POLICY=crewborg-aaln
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

Build the default policy:

```sh
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

Then confirm the submission and qualification state:

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

### If you chose `notsus`

Use this only for the Nim/reference path:

```sh
docker build --platform=linux/amd64 -f players/notsus/Dockerfile -t notsus:prime .
coworld upload-policy notsus:prime --name notsus --run /bin/notsus
coworld submit notsus:vN --league "$LEAGUE_ID" \
  --auto-champion always --no-open-browser
```

## Optional smoke before submitting

Skip long local A/B harnesses during onboarding. If you need a smoke check,
prefer one of these:

- Build-only check: `docker build --platform=linux/amd64 -t crewborg-aaln:prime players/crewborg-aaln`
- Unit tests after edits:
  `cd players/crewborg-aaln && python -m pytest players/crewrift/crewborg/tests/`
- Hosted 1-3 episode XP smoke after upload if you are unsure it starts. Use it
  only to detect crashes, `-100`, missing `run`, or qualification-gate failures.

Do not promote or reject a strategy from a 1-3 episode smoke. Crewrift variance
is too high.

## Start optimizing without rediscovery

For `crewborg-aaln`, read exactly these first:

1. [`players/crewborg-aaln/optimizer/guide/SKILL.md`](./players/crewborg-aaln/optimizer/guide/SKILL.md)
   - policy architecture, build/test/upload commands, runtime flags, and the
   file-to-edit map.
2. [`players/crewborg-aaln/optimizer/CREWBORG_INSIGHTS.md`](./players/crewborg-aaln/optimizer/CREWBORG_INSIGHTS.md)
   - known tournament lessons and eval traps.
3. [`players/crewborg-aaln/optimizer/playbooks/optimize-policy.md`](./players/crewborg-aaln/optimizer/playbooks/optimize-policy.md)
   - the one-loop optimization procedure.

Then run this loop:

```
read live standings/submissions -> inspect recent evals/replays/artifacts
  -> write one falsifiable hypothesis -> make one small edit or flag flip
  -> build/upload candidate -> evaluate in small hosted batches
  -> aggregate by role/seat and penalties -> submit only if it clears the gate
```

## Where to make the first changes

Map the hypothesis to the smallest surface:

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
- `crewborg-aaln` artifacts - `trace.db` + `summary.json`, joined to replay
  events by server tick. Start from the optimizer guide before querying these.

## Done for onboarding

Onboarding is complete when:

1. a shipped policy is uploaded with a non-null `run` attribute;
2. the exact uploaded version is submitted to Crewrift Prime for the correct
   player;
3. the submission/qualification state is confirmed;
4. the next optimization step is recorded as one falsifiable hypothesis or one
   named eval to run.

Do not spend the first hour re-learning the game engine. Submit the working
baseline, then optimize from evidence.
