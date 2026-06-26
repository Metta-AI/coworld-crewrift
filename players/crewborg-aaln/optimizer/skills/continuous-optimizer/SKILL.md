---
name: continuous-optimizer
description: Run an autonomous policy optimization loop: monitor leaderboard changes, select opponents, launch evals, analyze artifacts, create hypotheses, test candidates, and promote only verified improvements. Use for continuous or scheduled optimization of game policies.
---

# Continuous Optimizer

Operate the loop as evidence-first automation:

```text
observe leaderboard -> select targets -> run evals -> inspect stdout/stderr
logs -> analyze replays/artifacts -> generate hypotheses -> make one scoped
change -> verify -> promote or reject -> record memory -> repeat
```

## Sandbox Lifecycle

Each optimizer agent owns one persistent sandbox workspace and one Coworld
player. Scheduled runs should treat the sandbox like a durable snapshot:

- Start every tick by reading `optimizer_loop_lifecycle` status.
- `active`: run one evidence-based optimization loop, then record the loop
  verdict.
- `frozen`: a significant improvement has already been verified; write a
  heartbeat/status note and do not mutate source, request XP, upload, submit, or
  promote.
- `restart`/retrigger: resume from the latest frozen snapshot, reload current
  leaderboard/player/policy state, and move back to `active`.
- After a verified significant improvement, record it with
  `optimizer_loop_lifecycle` and freeze the sandbox until the next trigger.

## Loop

1. **Check safety**: verify budget, episode caps, auth, active player, and build/submit permissions before spending episodes or changing policy.
2. **Observe**: read current leaderboard, active memberships, submissions, and the current champion policy version.
3. **Select targets**:
   - Always include the top policies above or near us.
   - Include recent risers even if their score is lower.
   - Include a random/top-N broad set before promotion to catch overfitting.
4. **Evaluate**: use `hosted-xp-evals` for pairwise and broad evals.
5. **Log triage**: inspect hosted stdout/stderr for this optimizer player's
   slot before strategy analysis. Tracebacks, malformed actions, provider
   failures without fallback, and crashes become the next root-cause fix.
6. **Analyze**: use `replay-artifact-analysis` and `opponent-strategy-mining`.
7. **Hypothesize**: use `policy-hypothesis-loop`; create one candidate per hypothesis unless doing a deliberate small campaign.
8. **Verify**: rerun matched evals against the targeted opponent and broad guardrails.
9. **Promote**: use `promotion-gate`; never promote on weak evidence or unresolved player log crashes.
10. **Persist**: save the request id, episode ids, artifact paths, stdout/stderr log findings, summaries, hypotheses, verdict, and policy version lineage.

## Target Selection Heuristics

- Optimize for leaderboard score, not ego wins.
- If worse policies award more expected points, include them in guardrails.
- If a top policy changes version or behavior, re-evaluate immediately.
- If the leaderboard is stable, run smaller scheduled probes and reserve large evals for candidate gates.

## Cadence

- Short probe: 8-16 episodes against new or changed policies.
- Diagnostic eval: 24-40 episodes against one target.
- Candidate gate: 40+ episodes per important opponent plus a broad top-N/random set.
- Avoid maximum boundary request sizes if the backend has recently failed there; split into batches.

## Stop Conditions

Stop and ask for human input or reduce autonomy when:

- Budget or episode cap fails.
- Auth/player context is ambiguous.
- Candidate requires a risky broad refactor.
- Eval result is tied within variance.
- Candidate beats one target but broad guardrails regress.

## Memory To Record

Every loop should write:

- What changed in the leaderboard.
- Which policies were evaluated and why.
- Eval request ids and episode counts.
- Mean score, stderr, role metrics, and notable pattern summaries.
- Hosted stdout/stderr findings and whether artifacts were emitted/joinable.
- Hypothesis, policy change, expected effect, and verdict.
- Whether the result is a playbook, anti-pattern, or needs more evidence.
