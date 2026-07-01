"""Hunt mode: kill-ready pursuit of a visible victim (imposter; design §7.2).

Selected only when the kill is ready and a non-teammate crewmate is visible.
Search owns the pre-cooldown lead window and target acquisition; Hunt owns the
actual kill-ready close/strike behavior. Hunt commits to a visible victim, leads
its motion so it closes range on a moving target, and strikes when the victim is
in range and the kill would go **unwitnessed**:

- pick the most-isolated reachable visible crewmate
  (``strategy.opportunity.select_victim``)
  and stick with it until it's killed or lost;
- navigate to its **predicted intercept** point (``strategy.trajectory``) — leading a
  moving target instead of tail-chasing its live position at equal speed;
- when within KillRange and unwitnessed → ``kill``; if a witness is near, keep
  shadowing (lie in wait) rather than blowing the kill. The urgency bar relaxes
  the witness requirement over time, so a perpetually-shadowed kill still
  eventually fires. **After our first kill the witness requirement is dropped
  entirely** (``last_kill_tick`` set ⇒ strike regardless of witnesses): banking
  the second kill is the imposter's core job and conversion beats stealth there.

Victim selection also has a local teammate-claim heuristic: if a recently seen
fellow imposter is already closer to a target, prefer another victim when one
exists.
"""

from __future__ import annotations

from crewborg.action import KILL_RANGE_SQ
from crewborg.modes import imposter_common as ic
from crewborg.nav import plan_route
from crewborg.strategy.commander.bias import commander_of
from crewborg.strategy.opportunity import select_victim, unwitnessed, visible_victims
from crewborg.strategy.trajectory import lead_ticks, predict
from crewborg.types import ActionState, Belief, Intent, PlayerRecord
from players.player_sdk import EmptyModeParams, Mode, ModeParams


class HuntMode(Mode[Belief, ActionState, Intent]):
    name = "hunt"
    params_type = EmptyModeParams

    def __init__(self, params: ModeParams | None = None) -> None:
        super().__init__(params)
        self._victim_color: str | None = None  # the crewmate we have committed to hunting

    def decide(self, belief: Belief, action_state: ActionState) -> Intent:
        del action_state
        self_xy = ic.self_xy(belief)
        if self_xy is None:
            return Intent(kind="idle", reason="no self position")

        victim = self._resolve_victim(belief)
        if victim is None:
            return Intent(kind="idle", reason="no victim to hunt")  # selector normally flips to Search/Pretend

        victim_xy = (victim.world_x, victim.world_y)
        in_range = ic.dist2(self_xy, victim_xy) <= KILL_RANGE_SQ

        # Strike when kill-ready and in range. The kill fires if it goes UNWITNESSED (the
        # normal case), OR we've already banked a kill — after our first kill the witness
        # requirement is dropped, since getting the SECOND kill is the imposter's core job
        # (2 imposters × 2 = parity, and at the 2nd ready we're usually already close to
        # crew, so conversion beats stealth; James 2026-06-26) — OR the commander's danger
        # mode explicitly allows a witnessed kill.
        cmd = commander_of(belief)
        already_killed = belief.last_kill_tick is not None
        kill_is_unwitnessed = unwitnessed(belief, victim)
        danger_witness_allowed = cmd is not None and cmd.allow_witnessed_kill
        if in_range and belief.self_kill_ready and (kill_is_unwitnessed or already_killed or danger_witness_allowed):
            if not kill_is_unwitnessed and danger_witness_allowed:
                self.emit.event(
                    "commander_danger",
                    {
                        "lever": "allow_witnessed_kill",
                        "danger_reason": cmd.danger_reason,
                        "target_color": victim.color,
                    },
                )
            reason = (
                "striking the 2nd+ kill (witnesses ignored)"
                if already_killed and not kill_is_unwitnessed
                else "striking isolated victim"
            )
            return Intent(kind="kill", target_color=victim.color, reason=reason)

        # Otherwise close on the predicted intercept (lead a moving target) and shadow.
        # When already in range we lie in wait if a witness is near. The urgency
        # bar relaxes the witness test over time.
        intercept = predict(victim, lead_ticks(self_xy, victim_xy))
        if in_range:
            reason = "lying in wait (witness)"
        else:
            reason = "stalking the victim"
        return Intent(kind="navigate_to", point=intercept, reason=reason)

    def _resolve_victim(self, belief: Belief) -> PlayerRecord | None:
        """Keep the committed victim while visible; otherwise commit to a new visible one."""

        current = belief.roster.get(self._victim_color) if self._victim_color is not None else None
        if (
            current is not None
            and current.color not in belief.teammate_colors
            and current.life_status != "dead"
            and current.last_seen_tick == belief.last_tick
        ):
            return current
        victim = self._commander_victim(belief) or select_victim(belief)
        self._victim_color = victim.color if victim is not None else None
        return victim

    def _commander_victim(self, belief: Belief) -> PlayerRecord | None:
        cmd = commander_of(belief)
        if cmd is None or cmd.target_player is None:
            return None
        victim = next((candidate for candidate in visible_victims(belief) if candidate.color == cmd.target_player), None)
        if victim is None or belief.nav is None:
            return victim
        self_xy = ic.self_xy(belief)
        if self_xy is None or not plan_route(belief.nav, self_xy, (victim.world_x, victim.world_y)):
            return None
        return victim
