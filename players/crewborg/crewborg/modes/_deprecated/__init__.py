"""DEPRECATED imposter-mode logic — COLD STORAGE, DO NOT USE.

This package holds the *previous* Pretend and Search implementations, retired
2026-06-24 when the imposter seeking/positioning approach was redesigned (see
``design.md`` → "Imposter seeking/positioning — NEW APPROACH").

Why retired: the old logic sought crew via the occupancy-density estimator, which
**diffuses** every unseen crew's position over a growing reachability disc, sums
them, and walks to the **densest cell/room**. That drifts to the central hub
(diffusion centroid) and, by definition, heads toward *crowds* — the worst place to
find a crew member ALONE. Measured effect (event-warehouse vs Aaron/Andre):
crewborg was near a crew member ~half as often as the top imposters and got
isolation-with-crew half as often, despite best-in-field kill execution.

These files are kept ONLY for reference (to see what was tried and why it was
replaced). They are NOT imported anywhere and MUST NOT be used or revived without a
deliberate decision. The live modes are ``crewborg.modes.pretend`` /
``...search``, rebuilt on the group-follow → peel-off principle.
"""
