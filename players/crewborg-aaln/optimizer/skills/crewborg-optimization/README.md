# crewborg-optimization — policy-specific knowledge

Crewborg-specific optimizer knowledge distilled from the **source** repo
(`softmax/players/players/crewrift/crewborg/`), namespaced here to avoid clashing
with the generic optimizer skills in `optimizer/skills/`.

## What's here

- **`crewborg-suspicion-tuning/SKILL.md`** — the crewmate-side lever: how to tune
  and *fit from replays* crewborg's Bayesian P(imposter) suspicion model, its
  likelihood-ratio functions, the state-dependent vote bar, and the measured
  opponent chat tells. This is the genuinely policy-specific knob the generic
  skills don't carry.

## What's intentionally NOT copied (overlap with the generic skills)

The source repo's `skills/optimization/` tree (optimizer-loop, eval-set-design,
eval-aggregation, data-collection-design, artifact-capture, replay-reconstruction,
pattern-toolkit, hypothesis-generation) was reviewed and **deliberately not
duplicated** — its content is already covered by the sibling generic skills
(`base-optimizer-framework`, `continuous-optimizer`, `eval-variance-design`,
`eval-aggregation`, `data-collection-design`, `player-artifacts`,
`replay-artifact-analysis`, `policy-hypothesis-loop`, `spatial-temporal-analysis`)
and the game playbook (`games/crewrift/skills/crewrift-optimization`). Copying it
would be near-pure duplication.

The hard-won, *additive* material from those source skills — the measured opponent
tells, the v4 false-win / disconnect-taint / ecosystem-regression eval traps, the
variant-flag tradeoffs, the concrete tuning surface, and the tooling — is distilled
into **`optimizer/CREWBORG_INSIGHTS.md`** rather than re-copied as skills.

## See also

- `../../CREWBORG_INSIGHTS.md` — the distilled tournament insights doc.
- Source deep-dives (vendored at `players/crewrift/crewborg/`): `design.md`,
  `docs/designs/suspicion.md`, `docs/designs/agent-tracking.md`,
  `docs/replay-analysis.md`, `docs/experience-request-benchmark-analysis.md`.
