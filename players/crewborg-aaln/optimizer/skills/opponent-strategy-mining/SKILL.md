---
name: opponent-strategy-mining
description: Infer opponent policy strategies from eval results, replays, prompts, answers, artifacts, and leaderboard changes. Use to detect copied strategies, attractors, seat conditioning, and strategy shifts.
---

# Opponent Strategy Mining

Infer behavior from evidence; do not assume intent from one episode.

## Evidence To Collect

- Recent leaderboard ranks and scores.
- Active policy refs and policy version ids.
- Pairwise eval summaries against our champion/candidate.
- Replays with the per-event detail (kills, votes, chat, tasks).
- Our artifacts showing what we saw and how we responded.
- Recent episodes where opponent won unusually hard or changed behavior.

## Profile Schema

For each opponent, maintain:

- policy ref and version id,
- first seen / last seen,
- dominant behaviors (per role where the game has roles),
- seat-conditioned behavior and score by seat,
- role-conditioned strength (e.g. imposter win rate vs crewmate win rate),
- LLM/prompt-dependency or fallback-exploit indicators,
- vulnerabilities and countermeasures,
- confidence level.

## Strategy Classes

Generic classes that recur across games:

- **Fallback exploiter** — wins when our LLM call fails or times out.
- **Tempo / timing exploit** — exploits a predictable cadence (cooldown,
  meeting, turn).
- **Seat/role conditioning** — behaves differently by seat or role.
- **Deterministic tie** — forces equal scores.

**Crewrift-specific (social deduction):** chat-steering / framing (bare-sus
disinfo), vote-bloc coordination, kill-tempo aggression, vent/blend evasion. The
*measured* opponent tells for the current field (e.g. the plain-sus tell vs
truecrew) live in `CREWBORG_INSIGHTS.md` §2 — read and re-measure those rather
than re-deriving here.

## Inference Rules

- Require repeated observations before labeling a strategy.
- Prefer row-level evidence over final score.
- Treat policy version changes as new hypotheses until proven equivalent.
- Distinguish "hardcoded counter" from "general classifier":
  - hardcoded counters are acceptable as fallback cases only,
  - general classifiers should parse features like repeated token dominance,
    answer phrases, and decoy structure.

## Output

For each opponent, produce:

```text
Strategy: <class>
Evidence: <rows/episodes/metrics>
Exploit: <what causes us to lose points>
Counter: <general change to test>
Overfit risk: <how this could hurt broad leaderboard performance>
Next eval: <episodes and opponent set>
```

## Anti-Overfit Rule

Never optimize solely to beat one opponent unless that opponent dominates the
leaderboard objective. A counter must either improve broad expected score or be
gated behind a classifier that only activates when the opponent pattern is
present.
