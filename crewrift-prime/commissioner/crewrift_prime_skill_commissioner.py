"""Crewrift Prime qualifier commissioner — ONE game measures everything.

A thin subclass of the stock config-driven ``RulesetStrategyCommissioner`` that
replaces the score-only Qualifiers gate with a SINGLE-GAME three-skill gate, plus
first-class decision observability (per-skill scores, verdicts, reason strings)
emitted identically on the hosted path and the local debug path.

Why a custom image is required
------------------------------
The stock ruleset_strategy commissioner's transition vocabulary
(``TransitionCriteriaConfig``, ``extra="forbid"``) only allows
``completed_episodes_*`` / ``score_*`` and discards every other field of the
per-slot ``results_schema`` delivered in ``EpisodeResult.game_results``. To gate
on advanced skills we must read ``game_results`` ourselves -> new image. The
Competition division and ranking are reused from the stock base.

The gate ("one game and we're in")
----------------------------------
Each Qualifiers entrant plays exactly ONE 8-seat *self-play* combined game
(``scn_qualifier``). Self-play fills all 8 seats with the entrant, so the single
game exercises every role and all three signals come from its per-slot results:

- VOTING  = ``meeting_participation`` — capability/participation, not correctness.
  Pass if the entrant casts a vote/skip (or speaks) when a meeting occurs; no
  penalty if no meeting occurred; fail only if a meeting happened yet it never
  voted. (Meeting-aware; see decision.py.)
- HUNTING = ``imposter_kills`` — total kills landed by the imposter seat(s) in the
  game.
- TASKS   = ``crew_tasks_mean`` — mean tasks completed across the crew seats.

Pass ALL three -> Competition (competing/champion). Fail any -> stay Qualifiers
and re-run the single game next round. Crash safety is folded in: a completed
game with results is not a crash; a genuine non-completion DQs; infra/dispatch
failures hold-retry (never DQ). Single game => single-game variance; thresholds
are deliberately low/easy and env-overridable.

Competition scoring: 1 point per winning PLAYER (seat), by role — one point for
each seat the entrant occupies that won as imposter, plus one for each that won
as crew (score = imposter_wins + crew_wins, summed per winning seat).

Observability (see decision.py for the pure decision function)
--------------------------------------------------------------
For every entrant we build a ``DecisionRecord`` and:
  - log one ``COMMISSIONER_DECISION {json}`` line to stdout (hosted log tab),
  - set the membership event ``reason`` (short) + ``notes`` (full reason string)
    + ``evidence[].metadata`` (full record) -> visible in Observatory UI/API,
  - persist the records under ``state["crewrift_prime_skill"]`` (<=10MB blob)
    keyed by round, so decisions are auditable across rounds.

Thresholds are constants (env-overridable) in decision.py.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from commissioners.common.protocol import (
    DivisionRanking as CommissionerDivisionRanking,
    EpisodeFailed as CommissionerProtocolEpisodeFailed,
    EpisodeRequest as CommissionerProtocolEpisodeRequest,
    EpisodeResult as CommissionerProtocolEpisodeResult,
    PolicyMembershipEventChange,
    PolicyMembershipEventEvidence,
    RankingEntry as CommissionerRankingEntry,
    RoundComplete as CommissionerRoundComplete,
    RoundStart as CommissionerRoundStart,
    ScheduleEpisodes as CommissionerScheduleEpisodes,
)
from commissioners.common.commissioners import register_commissioner
from commissioners.common.models import (
    DivisionLeaderboardContext,
    DivisionLeaderboardSnapshot,
    LeaderboardRecentRoundPublic,
)
from commissioners.common.ruleset_strategy.commissioner import RulesetStrategyCommissioner
from commissioners.common.ruleset_strategy.entrants import select_rule
from commissioners.common.ruleset_strategy.round_start import RoundStartView
from commissioners.common.utils import (
    COMPLETED_EPISODE_COUNT_METADATA_KEY,
    RANKED_SCORE_COUNT_METADATA_KEY,
)

from game_results_loader import coerce_results_schema
from decision import (
    DECISION_LOG_TAG,
    QUALIFIER_VARIANT,
    SKILL_GATE_EVIDENCE_TYPE,
    SKILL_GATE_STAGE_ID,
    DecisionRecord,
    count_competition_wins,
    evaluate_combined_game,
)
from commissioners.common.ruleset_strategy.membership_events import protocol_policy_membership_event

NUM_SEATS = 8
COMMISSIONER_KEY = "crewrift_prime_skill"
# Full balanced 8-seat game variant used for Competition rounds (role-mixed,
# imposterCount 2). Falls back to the first variant if absent.
COMPETITION_VARIANT = "default"
_STATE_KEY = "crewrift_prime_skill"
_MAX_STATE_ROUNDS = 50  # keep the audit trail bounded well under the 10MB state cap

# Staging (Qualifiers) division type — the entrant runs the single combined
# qualifier game here; the Competition division ranks by win count.
_STAGING_DIVISION_TYPE = "staging"
_COMPETITION_DIVISION_TYPE = "competition"
# result_metadata score kind tag for Competition win-count rounds.
_COMPETITION_SCORE_KIND = "competition_wins"
# Substatus a held (not-yet-qualified) entrant keeps so the platform keeps
# re-running the single qualifier game until it passes.
_QUALIFIER_SUBSTATUS = SKILL_GATE_STAGE_ID  # "skill_gate" (stable; re-tested each round)
_INACTIVE_SUBSTATUS = "inactive"

# Substrings that mark a round failure as INFRASTRUCTURE / DISPATCH (the job was
# never created or was rejected by the control plane) rather than a genuine
# policy crash (a job ran and the container failed/timed out). A dispatch
# failure must NOT disqualify the policy as "Failed crash test".
_DISPATCH_FAILURE_MARKERS = (
    "/jobs/batch",
    "jobs/batch",
    "bad request",
    "unprocessable",
    "service unavailable",
    "too many requests",
    "gateway time-out",
    "bad gateway",
)
# HTTP status codes that indicate the control plane rejected or could not create
# the job (the policy never got a chance to run).
_DISPATCH_FAILURE_HTTP_CODES = (
    "400",
    "401",
    "402",
    "403",
    "404",
    "405",
    "409",
    "413",
    "422",
    "429",
    "500",
    "502",
    "503",
    "504",
)
_INFRA_REASON = (
    "Crash-check episodes could not be dispatched (infrastructure/control-plane "
    "failure, not a policy crash) — holding for retry."
)


def _emit_decision_log(payload: dict) -> None:
    """Write one greppable COMMISSIONER_DECISION line to stdout (hosted log tab)."""
    line = f"{DECISION_LOG_TAG} {json.dumps(payload, sort_keys=True)}"
    print(line, flush=True)


def _looks_like_dispatch_failure(error: str | None) -> bool:
    """True when an episode failure string is an infra/dispatch error (job never ran)."""
    if not error:
        return False
    text = error.lower()
    if any(marker in text for marker in _DISPATCH_FAILURE_MARKERS):
        return True
    # An HTTP status code attached to a request/dispatch context (vs. a code that
    # merely appears in a policy's own crash traceback).
    if ("request" in text or "http" in text or "/jobs" in text or "batch" in text or "status" in text) and any(
        code in text for code in _DISPATCH_FAILURE_HTTP_CODES
    ):
        return True
    return False


def _round_is_dispatch_failure(
    failed_episodes: list[CommissionerProtocolEpisodeFailed] | None,
    episode_results: list[CommissionerProtocolEpisodeResult],
) -> tuple[bool, list[str]]:
    """Classify a crash-check round as an infra/dispatch failure.

    Returns (is_dispatch_failure, error_samples). Only an infra failure when NO
    episode completed AND at least one failure looks like a dispatch error. A
    genuine policy crash (job ran, container failed) is NOT treated as infra.
    """
    if episode_results:
        return False, []
    failures = failed_episodes or []
    if not failures:
        return False, []
    samples: list[str] = []
    dispatch = False
    for failed in failures:
        error = getattr(failed, "error", None)
        if _looks_like_dispatch_failure(error):
            dispatch = True
            sample = (error or "")[:200]
            if sample and sample not in samples and len(samples) < 3:
                samples.append(sample)
    return dispatch, samples


class CrewriftPrimeSkillCommissioner(RulesetStrategyCommissioner):
    """RulesetStrategyCommissioner + a single-game, three-skill Qualifiers gate.

    "One game and we're in": a Qualifiers entrant plays exactly ONE 8-seat
    self-play qualifier game (``scn_qualifier``). Because self-play fills every
    seat with the entrant, that one game exercises both roles, so all three
    signals are read from its per-slot results: voting (meeting participation),
    hunting (kills by the imposter seats), tasks (mean crew tasks). Crash safety
    is folded in — a completed game with results is, by definition, not crashed;
    only a genuine non-completion disqualifies, and infra/dispatch failures
    hold-retry rather than DQ. The Competition division defers to the stock base.
    """

    # ---- division detection ---------------------------------------------------

    def _is_qualifier_round(self, view: RoundStartView) -> bool:
        """True for the staging (Qualifiers) division — where the single gate runs."""
        return str(getattr(view.current_division, "type", "")) == _STAGING_DIVISION_TYPE

    def _is_competition_round(self, view: RoundStartView) -> bool:
        """True for the Competition division — scored by winning players (1 pt/player, by role)."""
        return str(getattr(view.current_division, "type", "")) == _COMPETITION_DIVISION_TYPE

    def _qualifier_variant_id(self, round_start: CommissionerRoundStart) -> str:
        """The combined qualifier variant, falling back to the first variant."""
        available = {variant.id for variant in round_start.variants}
        if QUALIFIER_VARIANT in available:
            return QUALIFIER_VARIANT
        return round_start.variants[0].id if round_start.variants else "default"

    def _competition_variant_id(self, round_start: CommissionerRoundStart) -> str:
        """The full balanced game for Competition, falling back to the first variant."""
        available = {variant.id for variant in round_start.variants}
        if COMPETITION_VARIANT in available:
            return COMPETITION_VARIANT
        return round_start.variants[0].id if round_start.variants else "default"

    # ---- scheduling -----------------------------------------------------------

    def schedule_episodes_for_round_start(
        self, round_start: CommissionerRoundStart
    ) -> CommissionerScheduleEpisodes:
        config = self._config()
        view = RoundStartView(round_start, config)
        if self._is_competition_round(view):
            return self._schedule_competition_round(round_start, view)
        if not self._is_qualifier_round(view):
            return super().schedule_episodes_for_round_start(round_start)

        # Qualifiers: exactly ONE 8-seat self-play combined game per entrant.
        rule = select_rule(config, view.current_division, view.memberships)
        entries = view.entries(rule)
        variant_id = self._qualifier_variant_id(round_start)
        episodes = [
            CommissionerProtocolEpisodeRequest(
                request_id=f"qualifier:{entry.policy_version_id}",
                variant_id=variant_id,
                policy_version_ids=[entry.policy_version_id] * NUM_SEATS,
                tags={
                    "pool_id": str(round_start.round_id),
                    "entrant": str(entry.policy_version_id),
                    "qualifier": "1",
                },
            )
            for entry in entries
        ]
        return CommissionerScheduleEpisodes(episodes=episodes)

    def _schedule_competition_round(
        self, round_start: CommissionerRoundStart, view: RoundStartView
    ) -> CommissionerScheduleEpisodes:
        """Schedule full 8-seat Competition games (the stock path would emit
        only ``len(entries)`` seats — 3 — which the 8-player closed-roster
        crewrift game can never dispatch, leaving every episode ``pending`` and
        the round scoring 0 wins; this mirrors the qualifier 8-seat fix).

        The N entrants are round-robin assigned across all ``NUM_SEATS`` seats of
        each game so every entrant occupies a subset of seats and the per-seat
        ``result.scores`` read by ``_complete_competition_round`` attributes wins
        correctly. The seat rotation is shifted per episode so seat/role exposure
        is balanced across the round.
        """
        rule = select_rule(self._config(), view.current_division, view.memberships)
        entries = view.entries(rule)
        if not entries:
            return CommissionerScheduleEpisodes(episodes=[])
        num_episodes = self._competition_num_episodes(view, len(entries))
        variant_id = self._competition_variant_id(round_start)
        entrant_ids = [entry.policy_version_id for entry in entries]
        episodes = [
            CommissionerProtocolEpisodeRequest(
                request_id=f"competition:{round_start.round_id}:{episode_index}",
                variant_id=variant_id,
                policy_version_ids=[
                    entrant_ids[(episode_index + seat) % len(entrant_ids)]
                    for seat in range(NUM_SEATS)
                ],
                tags={
                    "pool_id": str(round_start.round_id),
                    "competition": "1",
                },
            )
            for episode_index in range(num_episodes)
        ]
        return CommissionerScheduleEpisodes(episodes=episodes)

    def _competition_num_episodes(self, view: RoundStartView, num_entries: int) -> int:
        """Episodes for a Competition round from the round_config stage (default
        falls back to the configured per-entrant episode count, floored at 1)."""
        round_config = view.round_config
        stages = round_config.get("stages")
        if isinstance(stages, list) and stages and isinstance(stages[0], dict):
            stage = stages[0]
            for key in ("num_episodes", "min_episodes_per_entrant"):
                value = stage.get(key)
                if isinstance(value, int) and value > 0:
                    return value
        return max(num_entries, 1)

    def complete_round_for_round_start(
        self,
        round_start: CommissionerRoundStart,
        episode_results: list[CommissionerProtocolEpisodeResult],
        scheduled_episodes: list[CommissionerProtocolEpisodeRequest] | None = None,
        failed_episodes: list[CommissionerProtocolEpisodeFailed] | None = None,
    ) -> CommissionerRoundComplete:
        config = self._config()
        view = RoundStartView(round_start, config)
        if self._is_competition_round(view):
            return self._complete_competition_round(round_start, view, episode_results)
        if not self._is_qualifier_round(view):
            return super().complete_round_for_round_start(
                round_start, episode_results, scheduled_episodes, failed_episodes
            )

        # Produce a normal RoundComplete (rankings/state), then REPLACE its
        # membership events with our single-game qualifier decisions.
        complete = super().complete_round_for_round_start(
            round_start, episode_results, scheduled_episodes, failed_episodes
        )

        # entrant order + request_id -> entrant
        entrant_order: list[str] = []
        seen: set[str] = set()
        request_to_entrant: dict[str, str] = {}
        for episode in scheduled_episodes or []:
            entrant_raw = episode.tags.get("entrant") or (
                str(episode.policy_version_ids[0]) if episode.policy_version_ids else None
            )
            if entrant_raw is None:
                continue
            request_to_entrant[episode.request_id] = str(entrant_raw)
            if entrant_raw not in seen:
                seen.add(entrant_raw)
                entrant_order.append(str(entrant_raw))

        # entrant -> the single completed game's results (first completed episode).
        game_by_entrant: dict[str, dict | None] = {entrant: None for entrant in entrant_order}
        for result in episode_results:
            entrant = request_to_entrant.get(result.request_id)
            if entrant is None or result.game_results is None:
                continue
            if game_by_entrant.get(entrant) is not None:
                continue
            game_by_entrant[entrant] = coerce_results_schema(result.game_results)

        is_dispatch_failure, error_samples = _round_is_dispatch_failure(failed_episodes, episode_results)
        target_division_id = _competition_division_id(round_start)
        memberships_by_policy = {str(m.policy_version_id): m for m in round_start.memberships}
        events: list[PolicyMembershipEventChange] = []
        records: dict[str, DecisionRecord] = {}

        for entrant in entrant_order:
            membership = memberships_by_policy.get(entrant)
            if membership is None:
                continue
            game_results = game_by_entrant.get(entrant)

            if game_results is None:
                # No completed qualifier game for this entrant.
                if is_dispatch_failure:
                    # Infra/dispatch failure -> hold & retry (NOT a policy crash).
                    _emit_decision_log(
                        {
                            "round_id": str(round_start.round_id),
                            "round_number": round_start.round_number,
                            "entrant_policy_version_id": entrant,
                            "decision": "DISPATCH_FAILURE_HOLD",
                            "reason": _INFRA_REASON,
                            "failure_error_samples": error_samples,
                        }
                    )
                    events.append(
                        PolicyMembershipEventChange(
                            league_policy_membership_id=membership.id,
                            from_division_id=membership.division_id,
                            to_division_id=membership.division_id,
                            status="qualifying",
                            substatus=_QUALIFIER_SUBSTATUS,
                            reason="Qualifier game dispatch failed (infrastructure error, not a policy crash)",
                            notes=_INFRA_REASON,
                            evidence=[
                                PolicyMembershipEventEvidence(
                                    type="crewrift_prime_dispatch_failure",
                                    title="Qualifier dispatch failure",
                                    summary=_INFRA_REASON,
                                    metadata={
                                        "classified_as": "infrastructure_dispatch_failure",
                                        "failure_error_samples": error_samples,
                                    },
                                )
                            ],
                        )
                    )
                else:
                    # Genuine non-completion -> crash DQ (this folds in crash safety).
                    _emit_decision_log(
                        {
                            "round_id": str(round_start.round_id),
                            "round_number": round_start.round_number,
                            "entrant_policy_version_id": entrant,
                            "decision": "CRASH_DQ",
                            "reason": "Qualifier game did not complete (crash)",
                        }
                    )
                    events.append(
                        PolicyMembershipEventChange(
                            league_policy_membership_id=membership.id,
                            from_division_id=membership.division_id,
                            to_division_id=membership.division_id,
                            status="disqualified",
                            substatus=_INACTIVE_SUBSTATUS,
                            reason="Failed to complete the qualifier game",
                            notes="The single qualifier game produced no results (crash / non-completion).",
                            evidence=[
                                PolicyMembershipEventEvidence(
                                    type="crewrift_prime_qualifier_crash",
                                    title="Qualifier game did not complete",
                                    summary="No results from the single qualifier game (crash).",
                                    metadata={"classified_as": "non_completion"},
                                )
                            ],
                        )
                    )
                continue

            # Completed game -> evaluate all three skills from this ONE game.
            record = evaluate_combined_game(game_results)
            records[entrant] = record
            _emit_decision_log(
                {
                    "round_id": str(round_start.round_id),
                    "round_number": round_start.round_number,
                    "entrant_policy_version_id": entrant,
                    "single_game": True,
                    **record.to_dict(),
                }
            )
            evidence = PolicyMembershipEventEvidence(
                type=SKILL_GATE_EVIDENCE_TYPE,
                title="Qualifier skill gate (single game)",
                summary=record.reason,
                metadata=record.to_dict(),
            )
            if record.passed and target_division_id is not None:
                events.append(
                    PolicyMembershipEventChange(
                        league_policy_membership_id=membership.id,
                        from_division_id=membership.division_id,
                        to_division_id=target_division_id,
                        status="competing",
                        substatus="champion",
                        reason=record.short_reason,
                        notes=record.reason,
                        evidence=[evidence],
                    )
                )
            else:
                # Hold & re-test next round. Built directly (bypasses the vendored
                # no-op check); the backend persists the event row regardless.
                events.append(
                    PolicyMembershipEventChange(
                        league_policy_membership_id=membership.id,
                        from_division_id=membership.division_id,
                        to_division_id=membership.division_id,
                        status="qualifying",
                        substatus=_QUALIFIER_SUBSTATUS,
                        reason=record.short_reason,
                        notes=record.reason,
                        evidence=[evidence],
                    )
                )

        complete.policy_membership_events = [
            protocol_policy_membership_event(event) for event in events
        ]
        complete.state = self._with_decision_state(
            complete.state,
            round_id=str(round_start.round_id),
            round_number=round_start.round_number,
            records=records,
        )
        return complete

    # ---- Competition division: score = winning players (1 pt/player, by role) ---

    def _complete_competition_round(
        self,
        round_start: CommissionerRoundStart,
        view: RoundStartView,
        episode_results: list[CommissionerProtocolEpisodeResult],
    ) -> CommissionerRoundComplete:
        """Score a Competition round by WINNING PLAYERS: 1 point per winning seat.

        The score is ``imposter_wins + crew_wins`` — one point for each player
        (seat) the entrant occupies that won as imposter, plus one for each that
        won as crew. The imposter/crew split is surfaced in the decision log,
        result_metadata, and round_display. The cumulative leaderboard sums these
        per-round point totals (see ``rank_division``).
        """
        rule = select_rule(self._config(), view.current_division, view.memberships)
        entries = view.entries(rule)

        # Per episode: coerced game_results + the policy at each seat.
        games: list[tuple[dict, list[str]]] = []
        for result in episode_results:
            if result.game_results is None:
                continue
            game_results = coerce_results_schema(result.game_results)
            if game_results is None:
                continue
            seat_policies = [str(score.policy_version_id) for score in result.scores]
            games.append((game_results, seat_policies))

        records = {}
        completed_counts: dict[str, int] = {}
        for entry in entries:
            pid = str(entry.policy_version_id)
            episodes_with_seats = []
            for game_results, seat_policies in games:
                seats = [i for i, sp in enumerate(seat_policies) if sp == pid]
                if seats:
                    episodes_with_seats.append((game_results, seats))
            records[pid] = count_competition_wins(episodes_with_seats)
            completed_counts[pid] = len(episodes_with_seats)

        ranked = sorted(
            entries,
            key=lambda e: (-records[str(e.policy_version_id)].score, e.seed_order, str(e.policy_version_id)),
        )
        rankings = []
        breakdown = []
        for rank, entry in enumerate(ranked, start=1):
            pid = str(entry.policy_version_id)
            rec = records[pid]
            _emit_decision_log(
                {
                    "round_id": str(round_start.round_id),
                    "round_number": round_start.round_number,
                    "division": "Competition",
                    "entrant_policy_version_id": pid,
                    "decision": "COMPETITION_WINS",
                    **rec.to_dict(),
                }
            )
            rankings.append(
                CommissionerRankingEntry(
                    policy_version_id=entry.policy_version_id,
                    player_id=str(entry.player_id) if entry.player_id is not None else None,
                    rank=rank,
                    score=rec.score,
                    result_metadata={
                        "seed_order": entry.seed_order,
                        COMPLETED_EPISODE_COUNT_METADATA_KEY: completed_counts[pid],
                        RANKED_SCORE_COUNT_METADATA_KEY: max(rec.episodes_counted, 1),
                        "score_kind": _COMPETITION_SCORE_KIND,
                        "wins": rec.wins,
                        "imposter_wins": rec.imposter_wins,
                        "crew_wins": rec.crew_wins,
                    },
                )
            )
            breakdown.append(
                {
                    "policy_version_id": pid,
                    "wins": rec.wins,
                    "imposter_wins": rec.imposter_wins,
                    "crew_wins": rec.crew_wins,
                }
            )
        return CommissionerRoundComplete(
            results=[CommissionerDivisionRanking(division_id=view.current_division.id, rankings=rankings)],
            round_display={
                "phases": [{"label": "Competition — winning players (1 pt/player, by role)", "episodes": len(games)}],
                "competition_wins": breakdown,
            },
        )

    def rank_division(self, ctx: DivisionLeaderboardContext) -> list[DivisionLeaderboardSnapshot]:
        """Competition leaderboard = CUMULATIVE wins (sum of per-round win counts).

        Other divisions defer to the stock ewma-blended ranking.
        """
        if str(getattr(ctx.division, "type", "")) != _COMPETITION_DIVISION_TYPE:
            return super().rank_division(ctx)
        if not ctx.completed_rounds or not ctx.round_results:
            return []
        completed_ids = {round_row.id for round_row in ctx.completed_rounds}
        best: dict[tuple, Any] = {}
        for result in ctx.round_results:
            if result.round_id not in completed_ids:
                continue
            if int(result.result_metadata.get(RANKED_SCORE_COUNT_METADATA_KEY, 1)) <= 0:
                continue
            key = (result.player_id, result.round_id)
            current = best.get(key)
            if current is None or (result.score, -result.rank) > (current.score, -current.rank):
                best[key] = result

        totals: dict[Any, dict] = {}
        for result in best.values():
            agg = totals.setdefault(
                result.player_id,
                {"wins": 0.0, "imposter_wins": 0, "crew_wins": 0, "pvids": set(), "name": result.player_name, "rounds": 0},
            )
            agg["wins"] += result.score
            agg["imposter_wins"] += int(result.result_metadata.get("imposter_wins", 0))
            agg["crew_wins"] += int(result.result_metadata.get("crew_wins", 0))
            agg["pvids"].add(result.policy_version_id)
            agg["rounds"] += 1

        ranks_by = {(r.round_id, r.player_id): r.rank for r in best.values()}
        scores_by = {(r.round_id, r.player_id): r.score for r in best.values()}

        def recent(player_id):
            if not ctx.recent_rounds:
                return None
            return [
                LeaderboardRecentRoundPublic(
                    id=round_row.public_id,
                    round_number=round_row.round_number,
                    status=round_row.status,
                    rank=ranks_by.get((round_row.id, player_id)),
                    score=scores_by.get((round_row.id, player_id)),
                    started_at=round_row.started_at,
                    completed_at=round_row.completed_at,
                )
                for round_row in ctx.recent_rounds
            ]

        ordered = sorted(totals.items(), key=lambda kv: (-kv[1]["wins"], kv[1]["name"] or "", str(kv[0])))
        return [
            DivisionLeaderboardSnapshot(
                player_id=player_id,
                player_name=agg["name"],
                rank=rank,
                score=agg["wins"],
                rounds_played=agg["rounds"],
                policy_version_ids=agg["pvids"],
                recent_rounds=recent(player_id),
            )
            for rank, (player_id, agg) in enumerate(ordered, start=1)
        ]

    def _with_decision_state(
        self,
        base_state,
        *,
        round_id: str,
        round_number: int,
        records: dict[str, DecisionRecord],
    ):
        state = dict(base_state) if isinstance(base_state, dict) else {}
        audit = state.get(_STATE_KEY)
        rounds = list(audit.get("rounds", [])) if isinstance(audit, dict) else []
        rounds.append(
            {
                "round_id": round_id,
                "round_number": round_number,
                "decisions": {entrant: record.to_dict() for entrant, record in records.items()},
            }
        )
        # bound the audit trail so state stays well under the 10MB cap
        rounds = rounds[-_MAX_STATE_ROUNDS:]
        state[_STATE_KEY] = {"rounds": rounds}
        return state


def _competition_division_id(round_start: CommissionerRoundStart) -> UUID | None:
    competition = [d for d in round_start.divisions if d.type == "competition"]
    if not competition:
        return None
    return min(competition, key=lambda d: (d.level, d.name, str(d.id))).id


register_commissioner(COMMISSIONER_KEY, CrewriftPrimeSkillCommissioner)
