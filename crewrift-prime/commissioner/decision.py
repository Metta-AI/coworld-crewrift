"""Pure decision logic for the Crewrift Prime skill gate.

NO I/O. This module is the single source of truth for:
  - tunable thresholds (env-overridable constants),
  - how each skill metric is computed from per-slot ``game_results`` arrays,
  - the per-skill verdicts and the strict three-skill AND gate,
  - the human-readable reason string and the structured ``DecisionRecord``.

Both the hosted commissioner (crewrift_prime_skill_commissioner.py) and the
local debug script (debug_decision.py) call ``evaluate_entrant`` /
``evaluate_entrants`` so they emit IDENTICAL decision records.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# --- tunable thresholds (env-overridable) -------------------------------------


def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


# Thresholds are intentionally "easier for now" (lowered 2026-06-24) so a modest
# policy can clear the gate while each drill still measures real skill (none are
# trivially 0). Observed crewborg-aaln drill metrics historically: voting ~0.0,
# hunting ~0.75 kills, tasks ~10.5. All three remain env-overridable, and MUST
# stay in sync with the UI mirror in
# web/softmax.com/src/app/(observatory)/observatory/v2/skillGate.ts.
#
# VOTING is a PARTICIPATION / CAPABILITY ASSURANCE (redesigned 2026-06-24), NOT a
# correctness check. It answers "if the game reaches a meeting, does this policy
# take part — does it actually VOTE/skip (and, when measurable, TALK)?".
#
# Honest meeting-aware gate (2026-06-24): a drill episode only offers a vote if a
# MEETING actually occurs (a player reports a body or presses the emergency
# button). If NO meeting occurs in the whole drill (no votes/skips/timeouts on any
# seat) the entrant was never GIVEN THE CHANCE to vote, so we do NOT penalize it
# (no-opportunity -> pass). When meetings DO occur, the entrant passes by casting
# at least one vote/skip (or speaking); it fails only if it had meetings yet never
# voted/talked. With CREWRIFT_PRIME_MEETING_PARTICIPATION_MIN > 0 the bar tightens
# to "participate in >= that fraction of the meetings"; default 0.0 = "any vote
# passes" (the user's intent: pass if the game players vote).
VOTE_PARTICIPATION_MIN = _f("CREWRIFT_PRIME_MEETING_PARTICIPATION_MIN", 0.0)
# Mean kills landed as the forced imposter (hunting drill).
HUNT_KILLS_MIN = _f("CREWRIFT_PRIME_HUNT_KILLS_MIN", 0.5)
# Mean tasks completed per seat (task-pressure drill).
TASK_TASKS_MIN = _f("CREWRIFT_PRIME_TASK_TASKS_MIN", 1.0)

# Forward-compat: per-slot "talk" signal. NOT emitted by the crewrift game today
# (the results_schema has no chat field). When a future game build adds a per-slot
# integer count under one of these keys, the voting assurance automatically counts
# speaking as participation — no commissioner change needed. Until then, "talk" is
# simply absent and participation is judged on vote actions alone (never fabricated).
CHAT_RESULT_KEYS = ("chat_messages", "spoke", "messages_sent")

# Scenario variant ids (must exist in the coworld manifest variants[]).
VOTE_VARIANT = "scn_vote_basic"
HUNT_VARIANT = "scn_hunt_isolated"
TASK_VARIANT = "scn_task_pressure"
SKILL_VARIANTS = (VOTE_VARIANT, HUNT_VARIANT, TASK_VARIANT)

EPISODES_PER_DRILL = int(os.getenv("CREWRIFT_PRIME_EPISODES_PER_DRILL", "4"))
FORCED_IMPOSTER_SEAT = 0
SKILL_GATE_STAGE_ID = "skill_gate"
# DEPRECATED hold substatus. v4 toggled a held entrant's substatus to this
# non-stage value to avoid a presumed host no-op; that REGRESSED scheduling
# because the platform progresses an entrant through the skill_gate stage by its
# substatus, so a `skill_gate_held` entrant no longer mapped to the skill_gate
# stage -> empty round -> "pool must have at least one primary entry".
#
# We no longer EMIT this value (held entrants keep substatus=skill_gate so they
# stay in the gate and get re-tested; the observability event still persists
# because the commissioner builds it directly, bypassing the vendored
# membership_event_is_noop check, and the backend writes the event row
# unconditionally). The constant is retained ONLY so detection still treats any
# lingering `skill_gate_held` entrant as a skill-gate round.
SKILL_GATE_HELD_SUBSTATUS = "skill_gate_held"

# Greppable stdout tag for hosted log inspection.
DECISION_LOG_TAG = "COMMISSIONER_DECISION"

# Generic, game-agnostic evidence type for a skill-gate promotion decision. The
# Observatory UI keys off this type (not a game-specific string) and renders the
# self-describing metadata (decision/passed/reason/skills[]). A game's commissioner
# is the single source of truth for the gate; the UI knows nothing game-specific.
SKILL_GATE_EVIDENCE_TYPE = "skill_gate"

# Presentation metadata for each skill, attached to every recorded verdict so the
# Observatory can render the gate (and derive its "how qualification works"
# explainer) generically — the commissioner owns these strings, not the web app.
SKILL_PRESENTATION: dict[str, dict[str, str]] = {
    "voting": {
        "label": "Voting",
        "blurb": "Vote when a meeting happens (no penalty if none occurs).",
        "threshold_label": "pass if it votes",
    },
    "hunting": {
        "label": "Hunting",
        "blurb": "Land at least one kill from the imposter seats.",
    },
    "tasks": {
        "label": "Tasks",
        "blurb": "Complete tasks while seated on the crew.",
    },
}

# Commissioner-authored "how qualification works" prose. The Observatory renders
# this verbatim — the web app holds NO game-specific copy. Recorded on every
# decision (in the open evidence metadata) so the explainer reflects exactly how
# THIS commissioner gates and scores. ``flow_steps`` is the Submit → Qualifier →
# Competition spine; ``gate_rule`` is the combiner; ``scoring_blurb`` describes
# the Competition pool. A different game's commissioner authors its own.
SKILL_GATE_EXPLAINER: dict[str, Any] = {
    "summary": (
        "Every new submission lands in the Qualifiers pool. To reach the "
        "Competition pool — where it plays the real leaderboard — a policy must "
        "clear one combined qualifier game each round. Anything held in Qualifiers "
        "is automatically re-tested the next round."
    ),
    "flow_steps": [
        {"title": "Submit", "body": "A new policy version enters the Qualifiers pool (staging)."},
        {
            "title": "Qualifier game",
            "body": (
                "One 8-seat self-play game measures every skill at once. Strict AND: "
                "every skill must pass (a policy that can't start the game is disqualified)."
            ),
        },
        {
            "title": "Competition",
            "body": "Pass every skill and the policy is promoted to the Competition pool.",
        },
    ],
    "gate_rule": "AND \u2014 every skill must pass",
    "skills_note": (
        "All skills are read from the single qualifier game and gate on the live "
        "thresholds shown above. Each entrant's per-skill result and overall verdict "
        "appear in the Qualifier Skill Gate panel on a completed round."
    ),
    "scoring_blurb": (
        "Once promoted, the Competition leaderboard ranks by total winning "
        "players \u2014 one point for each player that won as imposter, plus one "
        "point for each player that won as crew \u2014 accumulated all-time."
    ),
}


def _seat_value(arr: Any, seat: int) -> float:
    if isinstance(arr, list) and 0 <= seat < len(arr) and isinstance(arr[seat], (int, float)):
        return float(arr[seat])
    return 0.0


def _imposter_seat(game_results: dict[str, Any]) -> int:
    """Resolve the imposter seat from results (robust); fall back to forced seat 0."""
    imposter = game_results.get("imposter")
    if isinstance(imposter, list):
        for i, v in enumerate(imposter):
            if isinstance(v, (int, float)) and int(v) == 1:
                return i
    return FORCED_IMPOSTER_SEAT


@dataclass
class SkillVerdict:
    """One skill gate's metric, threshold, pass/fail, and the raw inputs used."""

    skill: str
    variant_id: str
    metric_name: str
    metric_value: float
    threshold: float
    comparator: str  # ">="
    episodes_counted: int
    passed: bool
    raw_inputs: dict[str, Any] = field(default_factory=dict)
    # Optional human phrasing for capability/participation skills (e.g. voting).
    # When set it replaces the generic "<metric> <value><cmp><threshold>" text in
    # reason strings so the verdict reads as an assurance, not a number.
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "skill": self.skill,
            "variant_id": self.variant_id,
            "metric_name": self.metric_name,
            "metric_value": round(self.metric_value, 4),
            "threshold": self.threshold,
            "comparator": self.comparator,
            "episodes_counted": self.episodes_counted,
            "passed": self.passed,
            "raw_inputs": self.raw_inputs,
        }
        if self.detail is not None:
            data["detail"] = self.detail
        # Presentation metadata so the Observatory renders this gate generically
        # (label + one-line blurb + threshold phrasing) without any game-specific
        # knowledge. The commissioner is the single source of truth; the UI derives
        # its "how qualification works" explainer from these recorded fields.
        spec = SKILL_PRESENTATION.get(self.skill)
        if spec is not None:
            data["label"] = spec["label"]
            data["blurb"] = spec["blurb"]
            threshold_label = spec.get("threshold_label")
            if threshold_label is not None:
                data["threshold_label"] = threshold_label
        return data

    def _core_phrase(self) -> str:
        if self.detail is not None:
            return self.detail
        return f"{self.metric_name} {self.metric_value:.2f}{self.comparator}{self.threshold:g}"

    def phrase(self) -> str:
        check = "\u2713" if self.passed else "\u2717"
        return f"{self._core_phrase()} {check}"


@dataclass
class DecisionRecord:
    """Full, inspectable record of one entrant's gate decision."""

    passed: bool
    verdicts: list[SkillVerdict]

    @property
    def decision(self) -> str:
        return "PROMOTED" if self.passed else "HELD_IN_QUALIFIERS"

    @property
    def short_reason(self) -> str:
        if self.passed:
            return "Passed Crewrift Prime three-skill gate (voting, hunting, tasks)"
        failed = [v.skill for v in self.verdicts if not v.passed]
        return f"Held in Qualifiers: failed {', '.join(failed) or 'skill gate'}"

    @property
    def reason(self) -> str:
        """Human-readable reason, e.g.
        'PROMOTED: cast votes in 4/4 meetings \u2713, kills_as_imposter_rate ...'
        or 'HELD IN QUALIFIERS: failed voting (did not vote in meetings (0/4))'.
        """
        if self.passed:
            return "PROMOTED: " + ", ".join(v.phrase() for v in self.verdicts)
        fails = [
            f"{v.skill} ({v.detail})"
            if v.detail is not None
            else f"{v.skill} ({v.metric_name} {v.metric_value:.2f} < {v.threshold:g})"
            for v in self.verdicts
            if not v.passed
        ]
        return "HELD IN QUALIFIERS: failed " + "; ".join(fails)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "passed": self.passed,
            "reason": self.reason,
            "short_reason": self.short_reason,
            "skills": {v.skill: v.to_dict() for v in self.verdicts},
            # Commissioner-authored explainer prose (flow, gate rule, scoring), so
            # the Observatory renders "how qualification works" with no
            # game-specific copy of its own.
            "explainer": SKILL_GATE_EXPLAINER,
        }


def _array_sum(value: Any) -> float:
    if isinstance(value, list):
        return sum(float(x) for x in value if isinstance(x, (int, float)))
    return 0.0


def _chat_sum(game_results: dict[str, Any]) -> float | None:
    """Per-episode talk count from any known chat key; None when not emitted."""
    for key in CHAT_RESULT_KEYS:
        if key in game_results:
            return _array_sum(game_results.get(key))
    return None


def _episode_had_meeting(gr: dict[str, Any]) -> bool:
    """A vote phase occurred in this episode iff any seat voted, skipped, or timed out."""
    return (
        _array_sum(gr.get("vote_players")) + _array_sum(gr.get("vote_skip")) + _array_sum(gr.get("vote_timeout"))
    ) > 0


def _voting_verdict(episodes: list[dict[str, Any]]) -> SkillVerdict:
    """Meeting-aware participation assurance: does the policy vote when a meeting happens?

    Self-play, so all seats are the entrant. Per episode the entrant
    "participated" if it cast a deliberate vote action — voted for a player
    (``vote_players``) or explicitly skipped (``vote_skip``) — or, when the game
    emits a chat signal, spoke (``chat_messages``/``spoke``). A meeting "occurred"
    iff there was any vote-phase activity (votes/skips/timeouts) on any seat.

    Pass rule (honest about opportunity):
      - No meeting occurred in the whole drill  -> PASS (no vote opportunity;
        the drill never reached a meeting, so we don't penalize the policy).
      - Meetings occurred                       -> PASS if the entrant participated
        in >= max(1, ceil(MIN * meetings)) of them (default MIN=0.0 => any one
        vote passes); FAIL only if it had meetings but never voted/talked.
    """
    from math import ceil

    votes_per_episode: list[float] = []
    skips_per_episode: list[float] = []
    timeouts_per_episode: list[float] = []
    chat_per_episode: list[float | None] = []
    participated_episodes = 0
    meetings = 0
    talk_signal_available = False

    for gr in episodes:
        vote_actions = _array_sum(gr.get("vote_players")) + _array_sum(gr.get("vote_skip"))
        chat = _chat_sum(gr)
        if chat is not None:
            talk_signal_available = True
        votes_per_episode.append(_array_sum(gr.get("vote_players")))
        skips_per_episode.append(_array_sum(gr.get("vote_skip")))
        timeouts_per_episode.append(_array_sum(gr.get("vote_timeout")))
        chat_per_episode.append(chat)
        if _episode_had_meeting(gr):
            meetings += 1
        voted = vote_actions > 0
        talked = chat is not None and chat > 0
        if voted or talked:
            participated_episodes += 1

    total = len(episodes)
    capability = "vote or talk" if talk_signal_available else "vote"

    if total == 0:
        passed = False
        rate = 0.0
        detail = "no drill episodes were scored"
    elif meetings == 0:
        # The drill never reached a meeting -> no vote opportunity. Not a policy
        # failure; do not block promotion on a drill that produced no vote.
        passed = True
        rate = 1.0
        detail = f"no meeting occurred in the {total} drill episodes (no vote opportunity)"
    else:
        rate = participated_episodes / meetings
        required = max(1, ceil(VOTE_PARTICIPATION_MIN * meetings)) if VOTE_PARTICIPATION_MIN > 0 else 1
        passed = participated_episodes >= required
        if passed:
            spoke = " and spoke" if talk_signal_available else ""
            detail = f"cast votes{spoke} in {participated_episodes}/{meetings} meetings"
        else:
            detail = f"did not {capability} in any of the {meetings} meetings reached"

    return SkillVerdict(
        skill="voting",
        variant_id=VOTE_VARIANT,
        metric_name="meeting_participation",
        metric_value=rate,
        threshold=VOTE_PARTICIPATION_MIN,
        comparator=">=",
        episodes_counted=total,
        passed=passed,
        raw_inputs={
            "meetings_occurred": meetings,
            "participated_episodes": participated_episodes,
            "votes_for_players_per_episode": votes_per_episode,
            "vote_skips_per_episode": skips_per_episode,
            "vote_timeouts_per_episode": timeouts_per_episode,
            "chat_messages_per_episode": chat_per_episode,
            "talk_signal_available": talk_signal_available,
        },
        detail=detail,
    )


def _hunting_verdict(episodes: list[dict[str, Any]]) -> SkillVerdict:
    kills_per_episode: list[float] = []
    seats_used: list[int] = []
    for gr in episodes:
        seat = _imposter_seat(gr)
        seats_used.append(seat)
        kills_per_episode.append(_seat_value(gr.get("kills"), seat))
    rate = (sum(kills_per_episode) / len(kills_per_episode)) if kills_per_episode else 0.0
    return SkillVerdict(
        skill="hunting",
        variant_id=HUNT_VARIANT,
        metric_name="kills_as_imposter_rate",
        metric_value=rate,
        threshold=HUNT_KILLS_MIN,
        comparator=">=",
        episodes_counted=len(episodes),
        passed=bool(kills_per_episode) and rate >= HUNT_KILLS_MIN,
        raw_inputs={
            "imposter_seat_kills_per_episode": kills_per_episode,
            "known_imposter_seats": seats_used,
        },
    )


def _task_verdict(episodes: list[dict[str, Any]]) -> SkillVerdict:
    mean_tasks_per_episode: list[float] = []
    for gr in episodes:
        tasks = gr.get("tasks")
        if isinstance(tasks, list) and tasks:
            vals = [float(x) for x in tasks if isinstance(x, (int, float))]
            if vals:
                mean_tasks_per_episode.append(sum(vals) / len(vals))
    rate = (sum(mean_tasks_per_episode) / len(mean_tasks_per_episode)) if mean_tasks_per_episode else 0.0
    return SkillVerdict(
        skill="tasks",
        variant_id=TASK_VARIANT,
        metric_name="tasks_completed",
        metric_value=rate,
        threshold=TASK_TASKS_MIN,
        comparator=">=",
        episodes_counted=len(mean_tasks_per_episode),
        passed=bool(mean_tasks_per_episode) and rate >= TASK_TASKS_MIN,
        raw_inputs={"mean_tasks_per_episode": mean_tasks_per_episode},
    )


def evaluate_entrant(results_by_variant: dict[str, list[dict[str, Any]]]) -> DecisionRecord:
    """Pure gate decision for one entrant.

    ``results_by_variant`` maps each skill variant id to the list of per-episode
    ``game_results`` dicts that entrant produced for that drill. Missing/empty
    drills fail their skill (no wrong promotions). Strict AND across all three.
    """
    verdicts = [
        _voting_verdict(results_by_variant.get(VOTE_VARIANT, [])),
        _hunting_verdict(results_by_variant.get(HUNT_VARIANT, [])),
        _task_verdict(results_by_variant.get(TASK_VARIANT, [])),
    ]
    return DecisionRecord(passed=all(v.passed for v in verdicts), verdicts=verdicts)


def evaluate_entrants(
    results_by_entrant: dict[str, dict[str, list[dict[str, Any]]]],
) -> dict[str, DecisionRecord]:
    return {entrant: evaluate_entrant(by_variant) for entrant, by_variant in results_by_entrant.items()}


# ============================================================================
# Single combined qualifier game (2026-06-24): "one game and we're in".
#
# Because the qualifier is 8-seat SELF-PLAY, ONE normal game already exercises
# every role — the entrant plays the imposter(s) AND the crew — so all three
# signals are read from that single game's per-slot results_schema:
#   - voting  = meeting participation (same meeting-aware rule as the drill),
#   - hunting = total kills by the imposter seats in the game,
#   - tasks   = mean tasks completed across the crew seats in the game.
# Single game => single-game variance; thresholds are deliberately low/easy.
# ============================================================================

QUALIFIER_VARIANT = "scn_qualifier"


def _imposter_seats(game_results: dict[str, Any]) -> list[int]:
    imposter = game_results.get("imposter")
    if isinstance(imposter, list):
        return [i for i, v in enumerate(imposter) if isinstance(v, (int, float)) and int(v) == 1]
    return []


def _crew_seats(game_results: dict[str, Any]) -> list[int]:
    crew = game_results.get("crew")
    if isinstance(crew, list):
        return [i for i, v in enumerate(crew) if isinstance(v, (int, float)) and int(v) == 1]
    return []


def _hunting_combined_verdict(game_results: dict[str, Any]) -> SkillVerdict:
    """Hunting from ONE game: total kills landed by the imposter seat(s)."""
    seats = _imposter_seats(game_results)
    kills = game_results.get("kills")
    per_seat = [_seat_value(kills, i) for i in seats]
    total = sum(per_seat)
    passed = bool(seats) and total >= HUNT_KILLS_MIN
    detail = (
        f"imposter seats landed {total:g} kill(s)"
        if seats
        else "no imposter seat in this game"
    )
    return SkillVerdict(
        skill="hunting",
        variant_id=QUALIFIER_VARIANT,
        metric_name="imposter_kills",
        metric_value=total,
        threshold=HUNT_KILLS_MIN,
        comparator=">=",
        episodes_counted=1,
        passed=passed,
        raw_inputs={"imposter_seats": seats, "kills_per_imposter_seat": per_seat},
        detail=detail,
    )


def _task_combined_verdict(game_results: dict[str, Any]) -> SkillVerdict:
    """Tasks from ONE game: mean tasks completed across the crew seats."""
    seats = _crew_seats(game_results)
    tasks = game_results.get("tasks")
    per_seat = [_seat_value(tasks, i) for i in seats]
    mean = (sum(per_seat) / len(per_seat)) if per_seat else 0.0
    passed = bool(per_seat) and mean >= TASK_TASKS_MIN
    detail = (
        f"crew completed {mean:.2f} tasks/seat on average"
        if seats
        else "no crew seat in this game"
    )
    return SkillVerdict(
        skill="tasks",
        variant_id=QUALIFIER_VARIANT,
        metric_name="crew_tasks_mean",
        metric_value=mean,
        threshold=TASK_TASKS_MIN,
        comparator=">=",
        episodes_counted=1,
        passed=passed,
        raw_inputs={"crew_seats": seats, "tasks_per_crew_seat": per_seat},
        detail=detail,
    )


def evaluate_combined_game(game_results: dict[str, Any] | None) -> DecisionRecord:
    """Strict-AND three-skill decision computed from ONE self-play game.

    ``game_results`` is the per-slot results_schema of the single qualifier game
    (or None when the game produced no results — every skill then fails and the
    caller decides crash-DQ vs infra-hold). Voting reuses the meeting-aware rule
    over the single game; hunting/tasks read the imposter/crew seats of that game.
    """
    if game_results is None:
        no_data = lambda skill, metric, variant: SkillVerdict(  # noqa: E731
            skill=skill,
            variant_id=variant,
            metric_name=metric,
            metric_value=0.0,
            threshold=0.0,
            comparator=">=",
            episodes_counted=0,
            passed=False,
            raw_inputs={},
            detail="no completed qualifier game",
        )
        return DecisionRecord(
            passed=False,
            verdicts=[
                no_data("voting", "meeting_participation", VOTE_VARIANT),
                no_data("hunting", "imposter_kills", QUALIFIER_VARIANT),
                no_data("tasks", "crew_tasks_mean", QUALIFIER_VARIANT),
            ],
        )
    verdicts = [
        _voting_verdict([game_results]),
        _hunting_combined_verdict(game_results),
        _task_combined_verdict(game_results),
    ]
    return DecisionRecord(passed=all(v.passed for v in verdicts), verdicts=verdicts)


# ============================================================================
# Competition division scoring (2026-06-25): "1 point per winning PLAYER, by role".
#
# In the Competition division the score counts EVERY winning seat the entrant
# occupies: 1 point for each player (seat) that won as imposter, plus 1 point for
# each player (seat) that won as crew. The score is the sum of both
# (``imposter_wins + crew_wins``). A seat scores if its per-slot ``win`` boolean
# is True; the role of that winning seat (imposter vs crew) comes from the
# per-slot ``imposter``/``crew`` arrays. Unlike a per-episode tally, an entrant
# that occupies multiple winning seats in one game scores once PER winning seat.
# The cumulative leaderboard sums these per-round point totals.
# ============================================================================


@dataclass
class CompetitionWinRecord:
    """One entrant's winning-player points across a Competition round.

    ``imposter_wins``/``crew_wins`` count individual winning SEATS (players), and
    the score is their sum (1 point per winning player, by role).
    """

    imposter_wins: int
    crew_wins: int
    episodes_counted: int

    @property
    def wins(self) -> int:
        """Total winning players (seats) across both roles = the score."""
        return self.imposter_wins + self.crew_wins

    @property
    def score(self) -> float:
        return float(self.wins)

    @property
    def reason(self) -> str:
        return (
            f"{self.wins} winning player(s) in {self.episodes_counted} game(s) "
            f"({self.imposter_wins} as imposter, {self.crew_wins} as crew)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "wins": self.wins,
            "imposter_wins": self.imposter_wins,
            "crew_wins": self.crew_wins,
            "episodes_counted": self.episodes_counted,
            "reason": self.reason,
        }


def _seat_flag(arr: Any, seat: int) -> bool:
    return isinstance(arr, list) and 0 <= seat < len(arr) and bool(arr[seat])


def count_competition_wins(
    episodes_with_seats: list[tuple[dict[str, Any], list[int]]],
) -> CompetitionWinRecord:
    """Winning-player points for an entrant: 1 per winning seat, split by role.

    ``episodes_with_seats`` pairs each episode's ``game_results`` with the seat
    indices that belong to the entrant in that episode (in Competition seating an
    entrant occupies a subset of seats; in self-play it occupies all 8). EACH of
    the entrant's seats that has ``win`` True scores one point: an imposter point
    if that winning seat is an imposter seat, a crew point if it is a crew seat.
    An entrant occupying several winning seats in one game scores once per seat.
    """
    imposter_wins = crew_wins = 0
    for game_results, seats in episodes_with_seats:
        win = game_results.get("win")
        imposter = game_results.get("imposter")
        crew = game_results.get("crew")
        for seat in seats:
            if not _seat_flag(win, seat):
                continue
            if _seat_flag(imposter, seat):
                imposter_wins += 1
            if _seat_flag(crew, seat):
                crew_wins += 1
    return CompetitionWinRecord(
        imposter_wins=imposter_wins,
        crew_wins=crew_wins,
        episodes_counted=len(episodes_with_seats),
    )
