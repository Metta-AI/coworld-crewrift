# Play — start here (Crewrift Prime)

You are (or are helping) a coding agent that will play the **Crewrift Prime**
Coworld league. This page is the 60-second orientation; the full how-to is in
**[`play_crewrift_prime.md`](./play_crewrift_prime.md)**.

- **Game repo:** <https://github.com/Metta-AI/coworld-crewrift>
- **Players (2 ready-to-deploy policies):** <https://github.com/Metta-AI/coworld-crewrift/tree/master/players>
- League: **Crewrift Prime** — `league_a12f5172-0907-4d04-8bcb-ca02f5360e3a`
- Coworld: `crewrift_prime:0.1.0` — `cow_fa681858-ae83-4f08-83cd-56fc4ec9d164`

## The game

**Crewrift** is an Among-Us-style social-deduction Coworld. Eight players per
episode: **crewmates** do tasks, report bodies, talk in meetings, and vote out
suspects; **imposters** kill on a cooldown, vent, blend in, and survive the vote.
Crewmates win by finishing tasks or voting out every imposter; imposters win by
killing enough crew. Scoring: **+100** win, **+1** task, **+10** kill, **−10**
not voting, **−1 / 10s** idle with tasks left. Full rules:
[`README.md` → Crewrift Rules](https://github.com/Metta-AI/coworld-crewrift#crewrift-rules).

**Crewrift Prime** is the seeded league build of that game, with config-only
skill drills (`scn_hunt_isolated`, `scn_vote_basic`, `scn_task_pressure`) and an
event-driven qualifier with a meeting **talk gate** (a policy that never talks in
a meeting does not qualify). See
[`crewrift-prime/commissioner/README.md`](./crewrift-prime/commissioner/README.md).

## You don't write a bot from scratch — you adopt one of 2 default policies

The episode ships **two working default policies** in
[`players/`](https://github.com/Metta-AI/coworld-crewrift/tree/master/players).
**Each one already runs a full, legal, never-crash episode and can be uploaded
and submitted to the league as-is — pick one and deploy it in minutes.** Then you
improve it.

| Policy | Stack | LLM | Pick it when you want… |
|---|---|---|---|
| **`crewborg-aaln`** | Python (Cyborg stack) | none (clean vote-hook seam) | The strongest scripted baseline **with a full optimizer workspace** bolted on — the fastest path to a competitive, improvable policy. **Default choice.** |
| **`notsus`** | Nim (reference bot) | none | To work in Nim close to the engine, or a deliberately simple/weak baseline and the canonical "compare against this" opponent. |

Both speak the same **Sprite v1** protocol and plug into the same `/player`
websocket, so the build/run/upload/eval machinery is identical no matter which
you choose.

## Next

Open **[`play_crewrift_prime.md`](./play_crewrift_prime.md)** — it gives the
direct per-policy descriptions, the "deploy it right now" commands, and the
guide to optimizing with the repo's tools (`tools/expand_replay.nim`), the
event-log **reporter**, the **grader**, and the `crewborg-aaln/optimizer/`
workspace.
