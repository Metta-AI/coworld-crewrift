---
name: policy-hypothesis-loop
description: Generate and test policy improvement hypotheses from eval evidence. Use when deciding what code or strategy change to make after analyzing replays and opponent behavior.
---

# Policy Hypothesis Loop

Make one clear change at a time unless running a deliberate candidate campaign.

## Hypothesis Format

Each hypothesis must include:

- observation,
- causal guess,
- proposed policy change,
- expected metric movement,
- overfit risk,
- validation eval set,
- rollback condition.

Example:

```text
Observation: Crewmate mean is −8 driven by vote-timeout penalties in ~20% of meetings.
Causal guess: the cursor-confirm path misses the deadline when an LLM meeting call is slow.
Change: auto-submit the deterministic top-suspect vote at ≤72 ticks regardless of LLM state.
Expected movement: vote-timeout penalties → ~0; crewmate mean +8.
Risk: voting too early on weak evidence ejects crew.
Validation: 40 games vs broad field + 40 targeted; per-role + penalty decomposition.
Rollback: crewmate win rate or imposter win rate drops.
```

## Candidate Types

- Rule/classifier change.
- Prompt/proposal strategy change.
- Fallback path improvement.
- LLM usage reduction or routing.
- Artifact/logging instrumentation.
- Strategy diversification.
- Opponent-aware activation gate.

## Data-First Process

1. Start from a measurable failure or opportunity.
2. Define what additional data would validate or falsify it.
3. Add artifact fields if the data is missing.
4. Implement the smallest change that tests the hypothesis.
5. Run matched evals with variance.
6. Record verdict as confirmed, rejected, weak evidence, or needs data.

## Candidate Campaigns

When testing several variants:

- Keep variants independent and named.
- Run the same opponent/seat/episode distribution for each.
- Compare against the same champion baseline.
- Promote only the candidate that clears broad guardrails.

## Common Hypotheses In Crewrift

- A missed/late vote (−10) is a cursor/confirm-path bug, not strategy — fix the
  fallback vote first.
- A consistently negative crewmate mean is a structural penalty (missed votes or
  stuck-idle), not variance — decompose penalties before tuning strategy.
- The state-dependent vote bar is mis-set for the endgame (skip loses to the next
  kill).
- A suspicion likelihood-ratio weight is mis-fit for the current field — re-fit
  from replays (`crewborg-optimization/crewborg-suspicion-tuning`).
- An imposter flag flip (BE_DUMB / blend) changes kill tempo and ecosystem
  coupling; predict the second-order effect, not just the local one.
- Nav/pathing wedges late game cost stuck penalties and missed kills (one knob:
  clearance radius / replan interval — `map-navigation`).

## Record Keeping

Before changing policy, record the hypothesis. After eval, record:

- exact candidate ref/version,
- eval request ids,
- metric deltas,
- representative examples,
- verdict,
- what should not be tried again.
