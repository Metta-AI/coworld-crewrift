---
name: hypothesis-generation
description: "Use to turn a crewborg behavior pattern into a falsifiable, pre-registered improvement hypothesis with a predicted metric move, for the optimizer loop. Trigger on 'generate a hypothesis', 'why is this happening', 'what change should I try', or 'turn this pattern into an experiment'."
---

# Hypothesis Generation

## Purpose

Convert a **pattern candidate** (from `pattern-toolkit`) into a single
falsifiable hypothesis that drives the next policy change and the next eval. The
loop is **hypothesis-first**: you need a pre-game hypothesis, then you modify the
policy to collect data matching it. A hypothesis without a predicted, measurable
move is just an opinion.

**Announce at start:** "Framing this pattern as a falsifiable hypothesis with a
mechanism, a predicted metric move on the goal, and the data needed to validate
it."

## A hypothesis must have all five parts

1. **Pattern (observed).** The fact from the data, with its version/role split
   and uncertainty. ("v8 imposter kills/ep 1.1 vs v3 1.6 on the same field, n=41.")
2. **Mechanism (causal read).** *Why* you think it happens, in terms of the
   policy code — a specific mode/strategy/param (`design.md` §7/§10/§12).
   ("Deadline-held plurality-join vote forfeits free-parity ejections" →
   `vote_policy.py`.)
3. **Change (the intervention).** The smallest policy edit that would fix the
   mechanism, ideally **gated behind an env flag / variant** so it A/Bs cleanly.
4. **Prediction (falsifiable, on the goal metric).** The directional + rough
   magnitude move you expect, stated *before* the eval. ("Crewmate win rate +5–8pp
   vs the league field; imposter win rate unchanged.") Include the **null** — what
   "no effect" looks like — and a **regression tripwire** (what must NOT drop).
5. **Validation data.** Exactly what must be logged/measured to confirm or kill
   it (hands to `data-collection-design`). If the current artifacts can't
   distinguish the hypothesis from the null, the hypothesis isn't testable yet.

## Generating good candidates

- **Separate behavioral vs objective claims.** "Higher task coverage" (behavioral)
  is not "higher win rate" (objective). Say which you're predicting; a behavioral
  win that doesn't move the objective is a (useful) negative result.
- **Tie to a knob.** crewborg's tunable surface is explicit
  (`design.md` §12: thresholds, lead/evade ticks, isolation bars, vote bars; env
  flags `BE_DUMB`, `DICK_MODE`, `LLM_MEETINGS`). Prefer hypotheses that map to one
  knob — they're cheap to test and cheap to revert.
- **Beware ecosystem interactions.** A change can help in isolation and regress in
  the field through a tempo/ecosystem coupling (the v4 kill-tempo regression).
  Predict the *second-order* effect, not just the local one.
- **Mind the gate flags.** Some params are inert in the shipped variant (e.g.
  `SEARCH_LEAD_TICKS`/`EVADE_TICKS` are inert under `BE_DUMB=1`). A hypothesis
  about an inert param will show no effect for the wrong reason — check the
  shipped variant first.

## Pre-register (so you can't move the goalposts)

Write the five parts down *before* changing code or running the eval — in the
findings doc or a hypothesis stub. The prediction and the regression tripwire are
the contract `eval-aggregation` checks against. Use `tr.research-partner`'s shape:
`Question → Result(predicted) → Interpretation(mechanism) → Caveats(confounds) →
Next Experiment(the change)`.

## Worked instance

`docs/designs/suspicion.md` §6 (offline LR fitting) is a concrete hypothesis loop:
each evidence weight is a pre-registered claim ("venting raises P(imposter) by
LR≈8"), validated by re-fitting from replays, with every change logged in a
provenance table. Mirror that discipline for behavioral hypotheses.

## Output

A pre-registered hypothesis block (the five parts) + the regression tripwire. Hand
the **change** to `data-collection-design` (instrument first) and the
**prediction** to `eval-aggregation` (the bar to test against).

## Integration

- **Consumes:** `pattern-toolkit`.
- **Feeds:** `data-collection-design`, then `eval-aggregation`.
- **Pairs with:** `tr.research-partner`.
- **Grounded in:** `design.md` §7/§10/§12, `docs/designs/suspicion.md` §6,
  `episode_data/FINDINGS_v4.md`.
