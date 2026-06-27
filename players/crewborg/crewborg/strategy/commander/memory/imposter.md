Imposter gameplay doctrine:

- Use `hunt_room` to spread pressure into a room where a kill opportunity is likely.
- Use `target_player` only for a legal, live, non-teammate player from the context.
- Use `avoid_room` when a teammate appears to own that space or the room is too exposed.
- Prefer priorities that create a victim in view when the cooldown becomes ready.
- Leave fields null when the deterministic search, recon, and hunt rules already have enough information.

DANGER fields - only set with a strong, stated reason in `danger_reason`:

- `allow_witnessed_kill`: strike even if a crewmate may witness it. Usually loses the game. Set only when the math favors it, such as the last kill to win or when stealth no longer matters.
- `skip_evade`: do not flee or vent after a kill. Set only to immediately chain a second kill on an isolated victim.
