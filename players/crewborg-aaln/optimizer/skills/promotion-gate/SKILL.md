---
name: promotion-gate
description: Decide whether a candidate policy is safe to promote or submit. Use after evals to compare champion vs candidate with variance, target coverage, broad guardrails, and rollback criteria.
---

# Promotion Gate

The gate protects the leaderboard objective. It is not enough for a candidate to
look better in one target eval.

## Required Evidence

- Candidate and champion policy refs/version ids.
- Same opponent distribution or matched eval design.
- Enough completed episodes for variance.
- Mean score and standard error.
- Seat-conditioned results.
- Role-conditioned row metrics.
- Failure counts.
- Broad guardrail eval against top-N or random league policies.

## Promote Only If

- Candidate mean beats champion by the configured margin.
- Standard error is low enough that the result is not just noise.
- No important opponent has a severe regression.
- Broad guardrail mean does not regress.
- Candidate does not depend on unavailable services unless fallback works.
- Syntax/build/upload checks pass if submission is included.
- Artifacts and hypothesis verdict are persisted.

## Reject If

- Candidate only beats one target and loses broad expected score.
- Result is within variance.
- Failure rate increases.
- It wins by exploiting a known artifact that no longer appears in current leaderboard play.
- It requires secrets, auth, or runtime services that are not available in hosted execution.
- The change is hard to rollback or not attributable to one hypothesis.

## Weak Evidence

If evidence is weak:

- do not promote,
- run more episodes or a better-matched eval,
- record why the evidence was weak,
- avoid changing multiple variables before the next test.

## Decision Record

Write:

```text
Decision: promote | reject | weak_evidence
Champion: <ref>
Candidate: <ref>
Targets: <opponent refs>
Episodes: <completed>/<requested>
Mean delta: <candidate - champion>
Variance: <stderr or confidence interval>
Broad guardrail: pass | fail
Reason: <short explanation>
Rollback: <policy ref or command>
```

## Submission Rule

Submit to a league only after promotion passes. If the runtime separates
promotion from hosted submission, treat submission as a second gate with auth,
active player, image, and league membership checks.
