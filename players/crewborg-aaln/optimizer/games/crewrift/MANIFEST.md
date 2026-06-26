# Crewrift Skills

Crewrift-specific optimizer knowledge. Crewrift is an Among Us–style
social-deduction game with a map, momentum-based navigation, line of sight, and
phase/meeting timing — so the spatial/temporal and navigation specialty skills in
top-level `skills/` apply directly.

## Skills

- `skills/crewrift-optimization/SKILL.md` — scoring constants, role-by-color census, the `trace.db` artifact schema + `server_tick` replay join, `.bitreplay` reconstruction via the Nim simulator, fetch/build/submit commands, `CREWBORG_*` trace flags, the −100 disconnect taint rule, map/navigation facts, and the replay viewer.
- `skills/crewrift-eval-design/SKILL.md` — Crewrift-specific eval design: the opponent field, seat/role census, episode counts per tier, and the −100 disconnect taint rule applied to Crewrift evals.

## Specialty skills that apply to Crewrift

Crewrift's traits map onto these top-level skills (load alongside the playbook):

- spatial + temporal map → `skills/spatial-temporal-analysis/SKILL.md`
- momentum-based movement on a map → `skills/map-navigation/SKILL.md`
