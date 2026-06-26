"""Pure OpenSkill (Plackett-Luce) MMR ranking for the Crewrift Prime league.

NO I/O. This module is the single source of truth for turning a Competition
division's completed-round history into a per-policy-version Bayesian skill
rating, faithful to upstream PR Metta-AI/metta#16527 (which added the same rater
to the platform read path for divisions whose ranking is NOT overridden).

Crewrift Prime *overrides* ``rank_division`` for its Competition board, so the
platform's MMR never reaches it; this module re-implements the PR's algorithm so
the commissioner can rank its Competition leaderboard by OpenSkill instead of the
old opponent-blind cumulative win count.

Algorithm (mirrors the PR):
  - Rate **policy versions** (not players); displayed MMR = conservative ordinal
    ``mu - 3*sigma``.
  - Replay completed rounds **oldest-first**; each round is ONE Plackett-Luce
    match, the participating policy versions fed by their finishing ``rank``
    (lower is better, ties allowed). Rounds with < 2 policies carry no
    comparative signal and are skipped.
  - **Best-result-per-round dedup:** keep each policy's best result per round
    (highest score, ties broken by lower rank) so a multi-episode round
    contributes one finishing position per policy.
  - **Player-prior init:** a brand-new policy version from a player who already
    has a rated (out-of-placement) policy starts at that player's best ``mu``
    (with the wide default sigma), so its first ranks aren't insane; placement
    then tightens sigma.
  - **Placement gate:** a policy stays "in placement" (no numeric rank) until it
    has played ``MMR_PLACEMENT_MIN_GAMES`` rated games.
  - **W/L:** wins are first-place (rank == 1) finishes; everything else a loss.

The caller (the commissioner's ``rank_division``) collapses these per-policy
ratings down to the player-keyed leaderboard the Crewrift Prime board requires.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


def _i(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# A policy must complete at least this many rated games before it earns a numeric
# rank. Until then it is "in placement": rated (its games still move others'
# ratings and we learn its skill) but unranked, so a single lucky win can't
# rocket a brand-new policy to the top. Env-overridable (no rebuild needed).
MMR_PLACEMENT_MIN_GAMES = _i("CREWRIFT_PRIME_MMR_PLACEMENT_MIN_GAMES", 5)

# The conservative-ordinal multiplier: displayed MMR = mu - ORDINAL_SIGMA_MULT * sigma.
ORDINAL_SIGMA_MULT = 3.0


@dataclass
class RatedRoundResult:
    """One policy version's finishing position in one completed round.

    A minimal, I/O-free view of the commissioner's
    ``LeaderboardRoundResultSnapshot`` carrying only what the rater needs.
    """

    round_id: UUID
    policy_version_id: UUID
    player_id: Any | None
    rank: int
    score: float


@dataclass
class PolicyMmr:
    """Final per-policy-version rating after replaying a division's history."""

    policy_version_id: UUID
    player_id: Any | None
    mu: float
    sigma: float
    mmr: float
    wins: int
    losses: int
    games_played: int
    in_placement: bool
    # 1-based rank among out-of-placement policies (None while in placement).
    rank: int | None = None


@dataclass
class _MutablePolicy:
    """Mutable per-policy rating state accumulated during the replay."""

    policy_version_id: UUID
    player_id: Any | None
    rating: Any
    wins: int = 0
    losses: int = 0
    games_played: int = 0

    @property
    def in_placement(self) -> bool:
        return self.games_played < MMR_PLACEMENT_MIN_GAMES


@dataclass
class MmrRanking:
    """The full result of ranking a division by MMR.

    ``by_policy`` is every rated policy version (placement and ranked alike),
    ordered by descending MMR. ``placement_min_games`` echoes the gate used so
    callers/observability can report "in placement (k/N)".
    """

    by_policy: list[PolicyMmr] = field(default_factory=list)
    placement_min_games: int = MMR_PLACEMENT_MIN_GAMES


def _dedup_best_per_round(results: list[RatedRoundResult]) -> dict[UUID, list[RatedRoundResult]]:
    """Keep each policy's best result per round, grouped by round_id.

    Best = highest score, ties broken by lower rank — matching the PR and the
    existing cumulative-win ``rank_division`` dedup, so a multi-episode round
    contributes exactly one finishing position per policy.
    """
    best: dict[tuple[UUID, UUID], RatedRoundResult] = {}
    for result in results:
        key = (result.policy_version_id, result.round_id)
        current = best.get(key)
        if current is None or (result.score, -result.rank) > (current.score, -current.rank):
            best[key] = result
    by_round: dict[UUID, list[RatedRoundResult]] = {}
    for result in best.values():
        by_round.setdefault(result.round_id, []).append(result)
    return by_round


def rank_by_mmr(
    *,
    completed_round_ids_oldest_first: list[UUID],
    round_results: list[RatedRoundResult],
) -> MmrRanking:
    """Rank a division's policy versions by an OpenSkill (Plackett-Luce) rating.

    ``completed_round_ids_oldest_first`` is the division's completed rounds in
    chronological order (the rater needs causal order so a new policy inherits
    its player's skill as it stood when it debuted). ``round_results`` is every
    per-policy round result (any order; deduped to best-per-round here).
    """
    # Import lazily so the pure module imports even where openskill isn't installed
    # (e.g. lint/type passes); the commissioner image always has it (see Dockerfile).
    from openskill.models import PlackettLuce

    if not round_results or not completed_round_ids_oldest_first:
        return MmrRanking(by_policy=[])

    by_round = _dedup_best_per_round(round_results)

    model = PlackettLuce()
    policies: dict[UUID, _MutablePolicy] = {}
    # A player's best mu among their out-of-placement policies, used to prime the
    # player's next policy version. Falls back to the model default otherwise.
    player_prior_mu: dict[Any, float] = {}

    for round_id in completed_round_ids_oldest_first:
        round_round_results = by_round.get(round_id)
        if not round_round_results or len(round_round_results) < 2:
            # A single-policy "match" carries no comparative signal.
            continue

        for result in round_round_results:
            if result.policy_version_id in policies:
                continue
            prior_mu = (
                player_prior_mu.get(result.player_id, model.mu)
                if result.player_id is not None
                else model.mu
            )
            rating = model.rating(mu=prior_mu, sigma=model.sigma, name=str(result.policy_version_id))
            policies[result.policy_version_id] = _MutablePolicy(
                policy_version_id=result.policy_version_id,
                player_id=result.player_id,
                rating=rating,
            )

        ordered = sorted(round_round_results, key=lambda r: r.rank)
        rated = model.rate(
            [[policies[r.policy_version_id].rating] for r in ordered],
            ranks=[r.rank for r in ordered],
        )
        for result, team in zip(ordered, rated, strict=True):
            policy = policies[result.policy_version_id]
            policy.rating = team[0]
            policy.games_played += 1
            if result.rank == 1:
                policy.wins += 1
            else:
                policy.losses += 1
            if (not policy.in_placement) and policy.player_id is not None:
                best = player_prior_mu.get(policy.player_id)
                if best is None or policy.rating.mu > best:
                    player_prior_mu[policy.player_id] = policy.rating.mu

    snapshots = [
        PolicyMmr(
            policy_version_id=policy.policy_version_id,
            player_id=policy.player_id,
            mu=policy.rating.mu,
            sigma=policy.rating.sigma,
            mmr=policy.rating.mu - ORDINAL_SIGMA_MULT * policy.rating.sigma,
            wins=policy.wins,
            losses=policy.losses,
            games_played=policy.games_played,
            in_placement=policy.in_placement,
        )
        for policy in policies.values()
    ]
    # Order by descending MMR; placement policies sort after ranked ones at equal
    # MMR via the in_placement tiebreak, then a stable id tiebreak.
    snapshots.sort(key=lambda s: (-s.mmr, s.in_placement, str(s.policy_version_id)))

    next_rank = 1
    for snap in snapshots:
        if not snap.in_placement:
            snap.rank = next_rank
            next_rank += 1
    return MmrRanking(by_policy=snapshots)
