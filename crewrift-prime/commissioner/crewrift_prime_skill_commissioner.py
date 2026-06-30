"""Crewrift Prime qualifier commissioner — event-driven, results-JSON gate.

A subclass of the stock config-driven ``RulesetStrategyCommissioner`` that owns an
EVENT-DRIVEN qualification loop end to end. There is NO "Qualifiers" staging
division any more: when a new policy is submitted to the league, the commissioner
itself (1) creates and runs an EXPERIENCE REQUEST (xp request) self-play episode
for it, (2) reads the episode's per-slot RESULTS JSON artifact to derive the skill
metrics, (3) evaluates the strict three-skill gate, and (4) on pass promotes the
policy DIRECTLY into the Competition division (on fail, holds for retry or DQs).

Why a custom image is required
------------------------------
The stock ruleset_strategy commissioner's transition vocabulary
(``TransitionCriteriaConfig``, ``extra="forbid"``) only allows
``completed_episodes_*`` / ``score_*`` and discards every other field of the
per-slot ``results_schema``. To gate on advanced skills we must read the game's
results ourselves -> new image. We go further: we own the xp-request client
(``xp_request_client.py``) so the "submit -> run xp request -> read results JSON
-> promote" loop lives entirely in the commissioner. The qualifier reads the
game's own end-of-episode ``results`` artifact (the seat-indexed
``results_schema`` the platform stores per job at ``/jobs/{job_id}/artifacts/results``)
— the SAME shape the Competition path already consumes from
``EpisodeResult.game_results`` — so no replay download or Nim re-expansion is
needed. The Competition division and its win-count scoring are reused.

The submission seam (no per-submission protocol message exists)
---------------------------------------------------------------
The stock platform<->commissioner protocol is round-driven (round_start /
episode_result / schedule_rounds / league_migration / rank_division /
describe_division). It carries NO "policy submitted" message. The most
appropriate existing seam that (a) sees every membership with its status and
(b) returns ``policy_membership_events`` is ``migrate_league``. We override it so
each ``submitted``/``qualifying`` membership is run through the qualification loop
(:meth:`qualify_submission`) and promoted/held/DQ'd. See README for the platform
note: it must call ``migrate_league`` (or an equivalent submission hook) when a
new policy is submitted, since the stock protocol exposes no per-submission event.

The gate ("one xp-request game and we're in")
---------------------------------------------
Each submitted policy plays ONE 8-seat *self-play* combined game via an xp
request. Self-play fills all 8 seats with the entrant, so the single game
exercises every role and all three signals come from the episode results JSON's
per-slot metrics:

- VOTING  = ``meeting_participation`` — capability/participation, not correctness.
- HUNTING = ``imposter_kills`` — total kills landed by the imposter seat(s).
- TASKS   = ``crew_tasks_mean`` — mean tasks completed across the crew seats.

Pass ALL three -> Competition (competing/champion). Fail any -> hold (status
qualifying) and re-run next time. Crash/infra safety: a completed episode with a
populated results JSON is not a crash; a genuine non-completion DQs; xp-request
infra failures and missing/unfetchable results JSON HOLD-retry (never DQ) — there
is no qualifier division to hold IN, so the hold keeps the membership
``qualifying`` in place.

Competition scoring: 1 point per winning PLAYER (seat), by role (unchanged).

Observability (see decision.py for the pure decision function)
--------------------------------------------------------------
For every entrant we build a ``DecisionRecord`` and log one
``COMMISSIONER_DECISION {json}`` line to stdout plus rich membership-event
evidence, identical to before.

Thresholds are constants (env-overridable) in decision.py.
"""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import UUID

from commissioners.common.protocol import (
    CommissionerRoundReport,
    DivisionLeaderboard as CommissionerDivisionLeaderboard,
    DivisionLeaderboardAxis as CommissionerDivisionLeaderboardAxis,
    DivisionLeaderboardColumn as CommissionerDivisionLeaderboardColumn,
    DivisionLeaderboardEntry as CommissionerDivisionLeaderboardEntry,
    DivisionLeaderboardRow as CommissionerDivisionLeaderboardRow,
    DivisionLeaderboardView as CommissionerDivisionLeaderboardView,
    DivisionRanking as CommissionerDivisionRanking,
    EpisodeFailed as CommissionerProtocolEpisodeFailed,
    EpisodeRequest as CommissionerProtocolEpisodeRequest,
    EpisodeResult as CommissionerProtocolEpisodeResult,
    RankingEntry as CommissionerRankingEntry,
    RoundComplete as CommissionerRoundComplete,
    RoundStart as CommissionerRoundStart,
    ScheduleEpisodes as CommissionerScheduleEpisodes,
)
from commissioners.common.commissioners import register_commissioner
from commissioners.common.models import (
    DivisionCommissionerDescriptionPublic,
    DivisionDescriptionContext,
    DivisionLeaderboardContext,
    DivisionLeaderboardSnapshot,
    LeaderboardRecentRoundPublic,
    LeagueMigrationContext,
    LeagueMigrationResult,
    MembershipSnapshot,
    PolicyMembershipEventChange as ModelsPolicyMembershipEventChange,
    PolicyMembershipEventEvidence as ModelsPolicyMembershipEventEvidence,
)
from commissioners.common.ruleset_strategy.commissioner import RulesetStrategyCommissioner
from commissioners.common.ruleset_strategy.entrants import select_rule
from commissioners.common.ruleset_strategy.round_start import RoundStartView
from commissioners.common.utils import (
    COMPLETED_EPISODE_COUNT_METADATA_KEY,
    RANKED_SCORE_COUNT_METADATA_KEY,
)

from game_results_loader import coerce_results_schema, has_results_schema_arrays
from decision import (
    DECISION_LOG_TAG,
    SKILL_GATE_EVIDENCE_TYPE,
    SKILL_GATE_STAGE_ID,
    build_competition_report,
    count_competition_wins,
    evaluate_combined_game_with_interview,
)
from interview import (
    InterviewInfraError,
    InterviewResult,
    InterviewTransport,
    run_interview,
    transport_from_address,
)
from xp_request_client import XpRequestClient, XpRequestError, XpRequestInfraError, XpRequestRun

NUM_SEATS = 8
COMMISSIONER_KEY = "crewrift_prime_skill"
# Full balanced 8-seat game variant used for Competition rounds (role-mixed,
# imposterCount 2). Falls back to the first variant if absent.
COMPETITION_VARIANT = "default"

_COMPETITION_DIVISION_TYPE = "competition"
# result_metadata score kind tag for Competition win-count rounds.
_COMPETITION_SCORE_KIND = "competition_wins"
# commissioner-state key holding the append-only per-round win history (one entry
# per scored policy per round) so ``_complete_competition_round`` can aggregate the
# full division history and publish the SAME win-rate board ``rank_division``
# computes. Without this, the platform's RoundComplete compat shim fabricates its
# own board from this round's results and that board ping-pongs against the board
# the scheduling tick writes — the leaderboard-flip bug. (The state key string is
# kept for backward compatibility with already-persisted commissioner state.)
_WIN_HISTORY_STATE_KEY = "crewrift_prime_mmr_history"
# Episode-request tag names recording how the closed-roster 8-seat game was topped
# up. ``filler_seats`` is the comma-separated 0-based seat indices that are NOT a
# real, uniquely-seated entrant; ``filler_policy_version_ids`` is the comma-
# separated set of policy_version_ids placed in those seats. Both are read back at
# scoring time so a filler/duplicate seat — and any policy that only ever appears
# as a filler — is EXCLUDED from scoring and never represented as a real entrant.
_FILLER_SEATS_TAG = "filler_seats"
_FILLER_POLICY_IDS_TAG = "filler_policy_version_ids"
# Statuses a freshly submitted (not-yet-qualified) policy carries; these are the
# memberships the event-driven gate runs the xp-request qualification loop for.
_SUBMITTED_STATUSES = ("submitted", "qualifying")
# Status/substatus a held (not-yet-qualified) entrant keeps so qualification is
# retried. There is NO qualifier division to hold IN any more — the membership
# simply stays ``qualifying`` (in whatever division it currently sits) and the
# next submission hook re-runs the loop.
_QUALIFYING_STATUS = "qualifying"
_QUALIFIER_SUBSTATUS = SKILL_GATE_STAGE_ID  # "skill_gate" (stable; re-tested next time)
_INACTIVE_SUBSTATUS = "inactive"
# How many self-play episodes the qualifier xp request runs (env-overridable). One
# game already exercises every role in self-play; more reduces single-game variance.
_QUALIFIER_NUM_EPISODES = max(int(os.getenv("CREWRIFT_PRIME_QUALIFIER_EPISODES", "1")), 1)

# Default "filler" player policies used to TOP UP a Competition game to NUM_SEATS
# when fewer than NUM_SEATS real entrants are competing. The closed-roster 8-seat
# crewrift game cannot dispatch with empty seats, so when e.g. only 3 real policies
# are competing the remaining 5 seats are filled with these standard bots so the
# game can run. Filler bots are seat-fillers ONLY: their wins/results NEVER count
# toward scoring, rankings, or the leaderboard. This is enforced with
# defense-in-depth in ``_complete_competition_round``: each scheduled episode tags
# both the filler SEAT indices (``filler_seats``) and the filler POLICY ids
# (``filler_policy_version_ids``), and scoring excludes a seat if it is a filler
# seat OR holds a filler policy, and never ranks/represents a filler policy as a
# real entrant. The filler policy ids are also surfaced explicitly in the decision
# log and ``round_display`` so they are always LABELED as fillers, never counted.
#
# The filler set is resolved at scheduling time with a clear precedence
# (see :meth:`CrewriftPrimeSkillCommissioner._filler_policy_version_ids`):
#
#   1. ``CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS`` — when SET and non-empty this
#      env var (a comma-separated list of policy_version_id UUIDs, settable on the
#      hosted runnable's ``env`` with no rebuild) is an explicit OVERRIDE/fallback.
#   2. otherwise the league-config API — ``GET /v2/leagues/{league_id}/filler-policies``
#      serves the per-league default fillers an admin configured in the web app.
#   3. otherwise empty — empty seats fall back to cycling real entrants (those
#      duplicate seats are still excluded from scoring; 1 scored seat/real policy).
#
# An API lookup that is unavailable, errors, or returns an empty list degrades
# gracefully to the env var, then to (3) — it never crashes a round.
def _env_filler_policy_version_ids() -> list[UUID]:
    """Parse ``CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS`` into UUIDs (override/fallback)."""
    raw = os.getenv("CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS", "")
    ids: list[UUID] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            ids.append(UUID(token))
        except ValueError:
            # Ignore malformed entries rather than crash scheduling; a misconfigured
            # filler id should never take down a tournament round.
            continue
    return ids


def _coerce_uuids(values: list[str]) -> list[UUID]:
    """Best-effort parse of policy_version_id strings into UUIDs (skip malformed)."""
    ids: list[UUID] = []
    for value in values:
        try:
            ids.append(UUID(str(value)))
        except (ValueError, TypeError):
            continue
    return ids

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
    "Qualifier could not be evaluated (experience-request dispatch or results-JSON "
    "fetch failed — infrastructure, not a policy crash) — holding for retry."
)

# Interview hard gate (2026-06-25): in addition to the three skills, every
# candidate must PASS an out-of-band LLM interview about Crewrift voting strategy
# (see commissioner/interview.py + the player's coworld/interview_server.py). An
# interview INFRA failure (no interviewer LLM key, the player interview server
# unreachable, a timeout) HOLDS for retry, exactly like an xp-request infra
# failure — it never DQs.
#
# PLATFORM SUPPORT REQUIRED (the launch seam): to interview a candidate the
# commissioner must launch (or ask the platform to launch) the candidate's
# container in INTERVIEW MODE (its alternate entrypoint runs the websocket
# SERVER) and obtain its reachable address. The investigation found NO existing
# way for the commissioner to get a container address from the xp-request/k8s
# dispatch machinery. So this is wired behind an injectable provider
# (``_interview_transport_provider``): the DEFAULT reads the address from an env
# var the platform is expected to populate (``CREWRIFT_PRIME_INTERVIEW_ADDR``),
# and tests inject a mock. Until the platform implements the launch+address
# wiring, set ``CREWRIFT_PRIME_INTERVIEW_ADDR`` (e.g. ``host:8770``) to point at a
# running interview-mode container, or disable the gate with
# ``CREWRIFT_PRIME_INTERVIEW_ENABLED=0`` while the platform support lands.
_INTERVIEW_ENABLED = os.getenv("CREWRIFT_PRIME_INTERVIEW_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
_INTERVIEW_ADDR_ENV = "CREWRIFT_PRIME_INTERVIEW_ADDR"
_INTERVIEW_REASON = (
    "Interview could not be conducted (no interviewer LLM key, the candidate's "
    "interview server was unreachable, or it timed out — infrastructure, not a "
    "policy failure) — holding for retry."
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
    """RulesetStrategyCommissioner + an event-driven, replay-evaluated gate.

    When a new policy is submitted, the commissioner runs the qualification loop
    itself (see :meth:`qualify_submission`): create a self-play xp request, poll
    it, read the episode's per-slot results JSON artifact into the per-slot
    metrics, evaluate the strict three-skill gate, and on pass promote the policy
    DIRECTLY into Competition. There is no Qualifiers staging division. The
    Competition division's win-count scheduling/scoring is reused unchanged.
    """

    # Lazily-created xp-request client (network I/O). Injectable for tests.
    _xp_client: XpRequestClient | None = None

    # Injectable interview-transport provider: given a membership, launch/locate
    # the candidate's interview-mode container and return a connected
    # InterviewTransport (or raise InterviewInfraError -> hold). Tests inject a
    # mock; the default uses the platform-populated address env var.
    _interview_transport_provider: Any = None
    # Injectable interviewer LLM (AnthropicRestClient-like). Tests inject a fake
    # so no Anthropic call happens; default builds one from the environment.
    _interview_llm: Any = None

    def _xp_request_client(self) -> XpRequestClient:
        if self._xp_client is None:
            self._xp_client = XpRequestClient()
        return self._xp_client

    def _interview_transport(self, membership: MembershipSnapshot) -> InterviewTransport:
        """Build/locate the interview transport for a candidate (the launch seam).

        Default behavior (no provider injected): read the candidate's interview
        server address from ``CREWRIFT_PRIME_INTERVIEW_ADDR`` (the platform must
        launch the container in interview mode and populate this). Raises
        :class:`InterviewInfraError` when no address is available so the entrant
        HOLDS for retry rather than being disqualified.
        """
        if self._interview_transport_provider is not None:
            return self._interview_transport_provider(membership)
        address = os.getenv(_INTERVIEW_ADDR_ENV, "").strip()
        if not address:
            raise InterviewInfraError(
                f"no interview server address ({_INTERVIEW_ADDR_ENV} unset): the platform "
                "must launch the candidate container in interview mode and provide its address"
            )
        return transport_from_address(address)

    def _filler_policy_version_ids(self, league_id: Any) -> list[UUID]:
        """Resolve the filler policy_version_id set for a round (env > API > empty).

        Precedence (see the module note on ``CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS``):
        the env var, when set and non-empty, is an explicit override/fallback;
        otherwise the per-league default fillers served by the league-config API
        (``GET /v2/leagues/{league_id}/filler-policies``) are used. Any API
        unavailability/error or an empty API list degrades gracefully (we log a
        warning and fall back to env, then to no fillers) — a filler lookup must
        never crash a competition round.
        """
        env_ids = _env_filler_policy_version_ids()
        if env_ids:
            return env_ids
        if league_id is None:
            return []
        try:
            served = self._xp_request_client().get_filler_policy_versions(str(league_id))
        except XpRequestError as exc:
            print(
                "WARNING: crewrift-prime: filler-policy lookup failed for league "
                f"{league_id} ({exc}); falling back to env/no-filler seating.",
                flush=True,
            )
            return []
        api_ids = _coerce_uuids(served)
        if not api_ids:
            return []
        return api_ids

    # ---- division detection ---------------------------------------------------

    def _is_competition_round(self, view: RoundStartView) -> bool:
        """True for the Competition division — scored by winning players (1 pt/player, by role)."""
        return str(getattr(view.current_division, "type", "")) == _COMPETITION_DIVISION_TYPE

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
        # No staging/qualifier rounds exist any more — qualification is event-driven
        # via migrate_league/qualify_submission, not a scheduled self-play round.
        return super().schedule_episodes_for_round_start(round_start)

    def _schedule_competition_round(
        self, round_start: CommissionerRoundStart, view: RoundStartView
    ) -> CommissionerScheduleEpisodes:
        """Schedule full 8-seat Competition games with AT MOST ONE real policy per seat.

        The closed-roster 8-seat crewrift game must dispatch exactly ``NUM_SEATS``
        policies. Each episode seats every real entrant AT MOST ONCE (no real policy
        occupies more than one seat in a game). When fewer than ``NUM_SEATS`` real
        entrants are competing, the remaining seats are TOPPED UP with the standard
        default filler policies (resolved by :meth:`_filler_policy_version_ids`:
        the ``CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS`` env override, else the
        per-league fillers served by ``GET /v2/leagues/{league_id}/filler-policies``).
        If no fillers are configured we fall back to cycling real entrants into the empty
        seats so the game can still run — but those duplicate seats, like all filler
        seats, are recorded in ``filler_seats`` and EXCLUDED from scoring/ranking by
        ``_complete_competition_round`` (filler/duplicate wins never count).

        The real entrants and the seat rotation are shifted per episode so seat/role
        exposure is balanced across the round.
        """
        rule = select_rule(self._config(), view.current_division, view.memberships)
        entries = view.entries(rule)
        if not entries:
            return CommissionerScheduleEpisodes(episodes=[])
        num_episodes = self._competition_num_episodes(view, len(entries))
        variant_id = self._competition_variant_id(round_start)
        entrant_ids = [entry.policy_version_id for entry in entries]
        league_id = getattr(round_start.league, "id", None)
        filler_ids = self._filler_policy_version_ids(league_id)
        episodes = [
            self._competition_episode(
                round_start=round_start,
                episode_index=episode_index,
                entrant_ids=entrant_ids,
                filler_ids=filler_ids,
                variant_id=variant_id,
            )
            for episode_index in range(num_episodes)
        ]
        return CommissionerScheduleEpisodes(episodes=episodes)

    def _competition_episode(
        self,
        *,
        round_start: CommissionerRoundStart,
        episode_index: int,
        entrant_ids: list[UUID],
        filler_ids: list[UUID],
        variant_id: str,
    ) -> CommissionerProtocolEpisodeRequest:
        """Build one Competition episode's seating: real policies once each, then fillers.

        Returns an episode request whose ``policy_version_ids`` is exactly
        ``NUM_SEATS`` long. The 0-based seat indices that are NOT a real, uniquely
        seated entrant (filler or duplicate top-up) are recorded as a comma-separated
        ``filler_seats`` tag, and the distinct CONFIGURED filler policy_version_ids
        used to top up (never real-entrant duplicates) are recorded as a comma-
        separated ``filler_policy_version_ids`` tag. Both are read back by
        ``_complete_competition_round`` so filler seats — and any policy that only
        appears as a filler — are excluded from scoring and never scored as a real
        entrant.
        """
        # Real entrants, each seated at most once, rotated per episode for balance.
        seat_policies: list[UUID] = []
        if entrant_ids:
            offset = episode_index % len(entrant_ids)
            rotated = entrant_ids[offset:] + entrant_ids[:offset]
            seat_policies = rotated[:NUM_SEATS]
        real_seat_count = len(seat_policies)

        # Top up the remaining seats. Prefer configured fillers (whose results are
        # excluded); when none are configured, cycle real entrants so the closed
        # roster can still dispatch (those duplicate seats are excluded too).
        remaining = NUM_SEATS - real_seat_count
        topup_is_filler = bool(filler_ids)
        if remaining > 0:
            topup_pool = filler_ids if topup_is_filler else entrant_ids
            seat_policies.extend(
                topup_pool[(episode_index + index) % len(topup_pool)]
                for index in range(remaining)
            )

        filler_seats = list(range(real_seat_count, NUM_SEATS))
        # Only CONFIGURED filler policies (never duplicated real entrants) are
        # recorded as filler policy ids — a duplicated real entrant is still a real
        # policy that scores at its own legitimate seat.
        filler_policy_ids = (
            {str(seat_policies[seat]) for seat in filler_seats} if topup_is_filler else set()
        )
        return CommissionerProtocolEpisodeRequest(
            request_id=f"competition:{round_start.round_id}:{episode_index}",
            variant_id=variant_id,
            policy_version_ids=seat_policies,
            tags={
                "pool_id": str(round_start.round_id),
                "competition": "1",
                _FILLER_SEATS_TAG: ",".join(str(seat) for seat in filler_seats),
                _FILLER_POLICY_IDS_TAG: ",".join(sorted(filler_policy_ids)),
            },
        )

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
            return self._complete_competition_round(
                round_start, view, episode_results, scheduled_episodes
            )
        # No staging/qualifier rounds exist any more — defer everything else to the
        # stock base. Qualification happens via migrate_league/qualify_submission.
        return super().complete_round_for_round_start(
            round_start, episode_results, scheduled_episodes, failed_episodes
        )

    # ---- event-driven qualification (submission -> xp request -> results JSON) -

    def migrate_league(self, ctx: LeagueMigrationContext) -> LeagueMigrationResult:
        """Submission seam: qualify every submitted/qualifying policy.

        The stock protocol carries no per-submission message, so ``migrate_league``
        — which receives every membership with its status and returns
        ``policy_membership_events`` — is the seam we react on. For each membership
        whose status is ``submitted``/``qualifying`` we run the full
        xp-request -> replay-parse -> evaluate loop and emit a promotion (to
        Competition), a hold (stay qualifying), or a DQ. All other memberships
        defer to the stock migration (legacy division restructure).

        NOTE for the platform: to make qualification fire promptly on each new
        submission, the platform must invoke this migration hook when a policy is
        submitted. There is no other commissioner entrypoint that observes a new
        submission (see module docstring / README).
        """
        result = super().migrate_league(ctx)
        events = list(result.policy_membership_events)
        target_division_id = _competition_division_id_from_snapshots(ctx.divisions)
        for membership in ctx.memberships:
            status = _status_str(membership.status)
            if status not in _SUBMITTED_STATUSES:
                continue
            events.append(self.qualify_submission(membership, target_division_id))
        return LeagueMigrationResult(policy_membership_events=events)

    def qualify_submission(
        self,
        membership: MembershipSnapshot,
        target_division_id: UUID | None,
    ) -> ModelsPolicyMembershipEventChange:
        """Run the qualification loop for ONE submitted policy and return its event.

        Steps: create + poll a self-play xp request for the policy, read its
        per-slot results JSON, evaluate the strict gate, and return the membership
        change — promote to ``target_division_id`` on pass, hold (status qualifying)
        on infra/missing-results failure, DQ on a genuine non-completion. Emits the
        same ``COMMISSIONER_DECISION`` log + evidence as the legacy path. Never
        raises: infra failures become holds.
        """
        policy_version_id = str(membership.policy_version_id)
        division_id = str(membership.division_id)

        try:
            run = self._xp_request_client().run_qualifier(
                division_id=division_id,
                policy_version_id=policy_version_id,
                num_episodes=_QUALIFIER_NUM_EPISODES,
                notes="crewrift-prime qualifier (event-driven)",
            )
        except XpRequestInfraError as exc:
            return self._infra_hold_event(membership, f"experience request failed: {exc}")

        game_results, parse_error = self._game_results_from_run(run)
        if parse_error is not None:
            return self._infra_hold_event(membership, parse_error)
        if game_results is None:
            # Run reached a terminal state but produced no completed/parseable
            # episode -> genuine non-completion (crash DQ).
            return self._crash_dq_event(membership, run)

        # Interview hard gate: run the out-of-band LLM interview. An interview
        # INFRA failure holds for retry (never DQ), exactly like an xp-request
        # infra failure. When the gate is disabled (platform launch not yet
        # wired) the interview is skipped and treated as a pass.
        interview: InterviewResult | None = None
        if _INTERVIEW_ENABLED:
            try:
                interview = self._run_interview(membership)
            except InterviewInfraError as exc:
                return self._infra_hold_event(membership, f"interview failed: {exc}", interview=True)

        if interview is None:
            # Gate disabled -> a neutral pass so qualification still works while
            # the platform launch wiring lands (documented in the README).
            record = evaluate_combined_game_with_interview(
                game_results, 1.0, interview_detail="interview gate disabled (skipped)"
            )
            interview_meta: dict[str, Any] = {"interview_enabled": False}
        else:
            record = evaluate_combined_game_with_interview(
                game_results,
                interview.score,
                interview_degraded=interview.degraded,
                interview_detail=(interview.grader_reason if interview.auto_passed else None),
            )
            interview_meta = {"interview_enabled": True, "interview": interview.to_dict()}

        _emit_decision_log(
            {
                "policy_version_id": policy_version_id,
                "xreq_id": run.xreq_id,
                "single_game": True,
                **interview_meta,
                **record.to_dict(),
            }
        )
        evidence = ModelsPolicyMembershipEventEvidence(
            type=SKILL_GATE_EVIDENCE_TYPE,
            title="Qualifier skill gate (xp-request results JSON + LLM interview)",
            summary=record.reason,
            metadata={"xreq_id": run.xreq_id, **interview_meta, **record.to_dict()},
        )
        if record.passed and target_division_id is not None:
            return ModelsPolicyMembershipEventChange(
                league_policy_membership_id=membership.id,
                from_division_id=membership.division_id,
                to_division_id=target_division_id,
                status="competing",
                substatus="champion",
                reason=record.short_reason,
                notes=record.reason,
                evidence=[evidence],
            )
        # Failed the gate -> hold (stay qualifying, in place) and re-test next time.
        return ModelsPolicyMembershipEventChange(
            league_policy_membership_id=membership.id,
            from_division_id=membership.division_id,
            to_division_id=membership.division_id,
            status=_QUALIFYING_STATUS,
            substatus=_QUALIFIER_SUBSTATUS,
            reason=record.short_reason,
            notes=record.reason,
            evidence=[evidence],
        )

    def _game_results_from_run(
        self, run: XpRequestRun
    ) -> tuple[dict | None, str | None]:
        """Build a game_results dict from the first completed episode's results JSON.

        Reads the game's own end-of-episode ``results`` artifact
        (``GET /jobs/{job_id}/artifacts/results`` via
        :meth:`XpRequestClient.get_episode_results`) — the seat-indexed
        ``results_schema`` payload (``scores``/``win``/``tasks``/``kills``
        /``imposter``/``crew``/``vote_players``/``vote_skip``/``vote_timeout``)
        that :func:`decision.evaluate_combined_game` consumes. NO replay download
        or Nim re-expansion is involved.

        Returns ``(game_results, None)`` on success, ``(None, error)`` when a
        completed episode existed but its results JSON was missing/unfetchable (an
        infra hold), or ``(None, None)`` when no completed episode existed at all
        (a genuine non-completion -> caller DQs).
        """
        completed = run.completed_episodes
        if not completed:
            return None, None
        last_error: str | None = None
        for episode in completed:
            if not episode.job_id:
                last_error = f"completed episode {episode.id} has no job_id"
                continue
            try:
                game_results = self._xp_request_client().get_episode_results(episode.job_id)
            except XpRequestInfraError as exc:
                last_error = f"results fetch failed for {episode.id}: {exc}"
                continue
            coerced = coerce_results_schema(game_results)
            if not has_results_schema_arrays(coerced or {}):
                last_error = (
                    f"completed episode {episode.id} results JSON missing per-slot "
                    f"arrays (keys: {sorted((game_results or {}).keys())})"
                )
                continue
            return coerced, None
        return None, last_error or "no completed episode with parseable results JSON"

    def _run_interview(self, membership: MembershipSnapshot) -> InterviewResult:
        """Conduct the out-of-band LLM interview for a candidate (I/O lives here).

        Locates/launches the candidate's interview server (the launch seam),
        generates a riddle, asks the player, and scores the answer. Resilient to
        interviewer-LLM failures (see :func:`interview.run_interview`): a
        riddle-generation LLM failure falls back to a built-in question pool, and
        a scorer-LLM failure AFTER an answer was received auto-passes (both ON by
        default). Only a TRANSPORT/player failure (unreachable server, timeout, no
        answer) raises :class:`InterviewInfraError` so the caller holds-for-retry.
        The pure verdict combination happens in decision.py.
        """
        transport = self._interview_transport(membership)
        return run_interview(
            transport,
            llm=self._interview_llm,
            seed=str(membership.policy_version_id),
        )

    def _infra_hold_event(
        self, membership: MembershipSnapshot, detail: str, *, interview: bool = False
    ) -> ModelsPolicyMembershipEventChange:
        """An xp-request/results-fetch infra failure -> HOLD qualifying (never DQ)."""
        reason_text = _INTERVIEW_REASON if interview else _INFRA_REASON
        evidence_type = "crewrift_prime_interview_failure" if interview else "crewrift_prime_dispatch_failure"
        evidence_title = "Interview infrastructure failure" if interview else "Qualifier infrastructure failure"
        _emit_decision_log(
            {
                "policy_version_id": str(membership.policy_version_id),
                "decision": "INFRA_HOLD",
                "reason": reason_text,
                "detail": detail[:300],
            }
        )
        return ModelsPolicyMembershipEventChange(
            league_policy_membership_id=membership.id,
            from_division_id=membership.division_id,
            to_division_id=membership.division_id,
            status=_QUALIFYING_STATUS,
            substatus=_QUALIFIER_SUBSTATUS,
            reason="Qualifier could not be evaluated (infrastructure error, not a policy crash)",
            notes=reason_text,
            evidence=[
                ModelsPolicyMembershipEventEvidence(
                    type=evidence_type,
                    title=evidence_title,
                    summary=reason_text,
                    metadata={"classified_as": "infrastructure_failure", "detail": detail[:300]},
                )
            ],
        )

    def _crash_dq_event(
        self, membership: MembershipSnapshot, run: XpRequestRun
    ) -> ModelsPolicyMembershipEventChange:
        """A terminal run with no completed episode -> genuine non-completion DQ."""
        _emit_decision_log(
            {
                "policy_version_id": str(membership.policy_version_id),
                "xreq_id": run.xreq_id,
                "decision": "CRASH_DQ",
                "reason": "Qualifier game did not complete (crash / non-completion)",
            }
        )
        return ModelsPolicyMembershipEventChange(
            league_policy_membership_id=membership.id,
            from_division_id=membership.division_id,
            to_division_id=membership.division_id,
            status="disqualified",
            substatus=_INACTIVE_SUBSTATUS,
            reason="Failed to complete the qualifier game",
            notes="The qualifier experience request produced no completed game (crash / non-completion).",
            evidence=[
                ModelsPolicyMembershipEventEvidence(
                    type="crewrift_prime_qualifier_crash",
                    title="Qualifier game did not complete",
                    summary="No completed game from the qualifier experience request (crash).",
                    metadata={"classified_as": "non_completion", "xreq_id": run.xreq_id},
                )
            ],
        )

    # ---- Competition division: score = winning players (1 pt/player, by role) ---

    def _complete_competition_round(
        self,
        round_start: CommissionerRoundStart,
        view: RoundStartView,
        episode_results: list[CommissionerProtocolEpisodeResult],
        scheduled_episodes: list[CommissionerProtocolEpisodeRequest] | None = None,
    ) -> CommissionerRoundComplete:
        """Score a Competition round by WON EPISODES: 1 point per episode won.

        A player scores one point for each episode in which at least one of its
        (non-filler) seats won, capped at 1 per episode regardless of how many of
        its seats won. The imposter/crew split of the winning seats is surfaced in
        the decision log, result_metadata, and round_display for observability. The
        per-round score (the count of won episodes) feeds the win-rate
        leaderboard (see ``rank_division``).

        FILLER/duplicate seats (the top-up seats this round scheduled to fill a
        closed-roster 8-seat game when fewer than ``NUM_SEATS`` real entrants are
        competing) are EXCLUDED: a policy is only credited for the seat it was
        legitimately assigned as a real entrant, never for a filler/duplicate seat.

        Filler exclusion is defense-in-depth:

        - by SEAT — the scheduled episode's ``filler_seats`` tag marks the top-up
          seat indices, and
        - by POLICY — the ``filler_policy_version_ids`` tag marks policies placed
          purely as fillers; such a policy is dropped from scoring AND never ranked
          or represented as a real entrant, even if it also slipped into a seat the
          tags did not mark.

        The configured filler policy ids this round are surfaced explicitly in the
        decision log, ``round_display["filler_policy_version_ids"]``, and the
        observability notes so they are always labeled as fillers and never counted.
        """
        rule = select_rule(self._config(), view.current_division, view.memberships)
        entries = view.entries(rule)
        filler_seats_by_request = _filler_seats_by_request(scheduled_episodes)
        filler_policy_ids = _all_filler_policy_ids(scheduled_episodes)

        # A policy that only ever appears as a configured filler must never be
        # scored or ranked. Real entrants are taken from the division roster, so a
        # pure filler should not be in ``entries`` at all — but drop it explicitly
        # as belt-and-suspenders so a misconfiguration can never score a filler.
        scored_entries = [
            entry for entry in entries if str(entry.policy_version_id) not in filler_policy_ids
        ]

        # Per episode: coerced game_results, the policy at each seat, and the seat
        # indices that are filler/duplicate top-up (excluded from scoring).
        games: list[tuple[dict, list[str], set[int]]] = []
        for result in episode_results:
            if result.game_results is None:
                continue
            game_results = coerce_results_schema(result.game_results)
            if game_results is None:
                continue
            seat_policies = [str(score.policy_version_id) for score in result.scores]
            filler_seats = set(filler_seats_by_request.get(result.request_id, set()))
            # Defense-in-depth: any seat holding a configured filler policy is a
            # filler seat regardless of what the seat-index tag recorded.
            filler_seats |= {
                i for i, sp in enumerate(seat_policies) if sp in filler_policy_ids
            }
            games.append((game_results, seat_policies, filler_seats))

        records = {}
        completed_counts: dict[str, int] = {}
        for entry in scored_entries:
            pid = str(entry.policy_version_id)
            episodes_with_seats = []
            for game_results, seat_policies, filler_seats in games:
                seats = [
                    i
                    for i, sp in enumerate(seat_policies)
                    if sp == pid and i not in filler_seats
                ]
                if seats:
                    episodes_with_seats.append((game_results, seats))
            records[pid] = count_competition_wins(episodes_with_seats)
            completed_counts[pid] = len(episodes_with_seats)

        ranked = sorted(
            scored_entries,
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
                        "episode_wins": rec.episode_wins,
                        "imposter_wins": rec.imposter_wins,
                        "crew_wins": rec.crew_wins,
                    },
                )
            )
            breakdown.append(
                {
                    "policy_version_id": pid,
                    "player_id": str(entry.player_id) if entry.player_id is not None else None,
                    "wins": rec.wins,
                    "imposter_wins": rec.imposter_wins,
                    "crew_wins": rec.crew_wins,
                    "episodes_counted": completed_counts[pid],
                }
            )
        filler_ids_sorted = sorted(filler_policy_ids)
        if filler_ids_sorted:
            _emit_decision_log(
                {
                    "round_id": str(round_start.round_id),
                    "round_number": round_start.round_number,
                    "division": "Competition",
                    "decision": "FILLER_POLICIES_EXCLUDED",
                    "filler_policy_version_ids": filler_ids_sorted,
                    "note": (
                        "Filler policies seat-fill the closed-roster 8-seat game and are "
                        "NOT counted in scoring, ranking, or the leaderboard."
                    ),
                }
            )
        filler_note = (
            "Filler policies are seat-fillers only and are EXCLUDED from scoring: "
            + ", ".join(f"filler policy {fid}" for fid in filler_ids_sorted)
            if filler_ids_sorted
            else "No filler policies were used this round."
        )
        leaderboards, next_state = self._competition_win_leaderboards(
            incoming_state=round_start.state,
            division_id=view.current_division.id,
            round_id=round_start.round_id,
            rankings=rankings,
        )
        return CommissionerRoundComplete(
            results=[CommissionerDivisionRanking(division_id=view.current_division.id, rankings=rankings)],
            # Publish the win-rate board the platform should persist. Without an
            # explicit leaderboards payload the platform's RoundComplete shim fabricates
            # its own board from ``results`` that overwrites (and ping-pongs against)
            # the board the scheduling tick writes via rank_division.
            leaderboards=leaderboards,
            state=next_state,
            round_display={
                "phases": [{"label": "Competition — win rate (won episodes ÷ played episodes)", "episodes": len(games)}],
                "competition_wins": breakdown,
                # Explicitly mark which policies were fillers so any consumer labels
                # them "filler policy <id>" and never treats them as a real entrant.
                "filler_policy_version_ids": filler_ids_sorted,
            },
            observability=CommissionerRoundReport.model_validate(
                build_competition_report(
                    breakdown,
                    notes=[f"Scored {len(games)} completed game(s) this round.", filler_note],
                )
            ),
        )

    def _competition_win_leaderboards(
        self,
        *,
        incoming_state: Any,
        division_id: UUID,
        round_id: UUID,
        rankings: list[CommissionerRankingEntry],
    ) -> tuple[list[CommissionerDivisionLeaderboard], dict[str, Any]]:
        """Accumulate the division's per-round win history and publish the board.

        We keep an append-only list of ``(round_id, policy_version_id, player_id,
        rank, score, episodes_played)`` in commissioner state and collapse it with
        the SAME ``_win_total_board`` helper ``rank_division`` uses, so both
        platform writers (the scheduling tick's ``rank_division`` and this
        round-complete) emit the SAME all-time WIN-RATE board — the board can't
        flip between schemes.

        Player names are intentionally not stored here (round-start memberships
        don't carry them); the platform's leaderboard read path resolves names live
        from the players table, so the published board's null names render correctly.
        """
        state: dict[str, Any] = dict(incoming_state) if isinstance(incoming_state, dict) else {}
        history: list[dict[str, Any]] = list(state.get(_WIN_HISTORY_STATE_KEY, []))
        round_id_str = str(round_id)
        # Idempotency: a round is appended exactly once. A retried round-complete
        # must not double-count its results into the cumulative total.
        already_recorded = any(row.get("round_id") == round_id_str for row in history)
        if not already_recorded:
            for entry in rankings:
                tainted = (
                    int(entry.result_metadata.get(RANKED_SCORE_COUNT_METADATA_KEY, 1)) <= 0
                )
                history.append(
                    {
                        "round_id": round_id_str,
                        "policy_version_id": str(entry.policy_version_id),
                        "player_id": entry.player_id,
                        "rank": entry.rank,
                        # Tainted/unranked entries are kept so the participant stays
                        # visible, but contribute 0 wins / 0 played episodes and are
                        # excluded from the win-rate numerator/denominator below.
                        "score": 0.0 if tainted else entry.score,
                        # Episodes the player PLAYED this round — the win-rate
                        # denominator. Persisted so the replayed board matches
                        # what rank_division computes from result_metadata.
                        "episodes_played": 0
                        if tainted
                        else int(entry.result_metadata.get(COMPLETED_EPISODE_COUNT_METADATA_KEY, 0)),
                        "tainted": tainted,
                    }
                )
        state[_WIN_HISTORY_STATE_KEY] = history

        # Best per-round win score per player + the player's policy versions, then
        # collapse to the all-time win-rate board with the shared helper. Every
        # player in the history is a participant (tainted rows included) so nobody
        # is dropped; tainted rows just don't feed the win rate.
        round_score: dict[tuple[Any, Any], float] = {}
        round_episodes: dict[tuple[Any, Any], int] = {}
        pvids_by_player: dict[Any, set] = {}
        participants: set = set()
        for row in history:
            player_id = row["player_id"]
            participants.add(player_id)
            pvids_by_player.setdefault(player_id, set()).add(UUID(row["policy_version_id"]))
            if row.get("tainted"):
                continue
            key = (player_id, row["round_id"])
            prior = round_score.get(key)
            score = float(row["score"])
            if prior is None or score > prior:
                round_score[key] = score
                round_episodes[key] = int(row.get("episodes_played", 0))

        snapshots = self._win_total_board(
            round_score,
            name_by_player={},
            pvids_by_player=pvids_by_player,
            recent=lambda _pid: None,
            round_episodes=round_episodes,
            participants=participants,
        )
        entries = [
            CommissionerDivisionLeaderboardEntry(
                player_id=str(snapshot.player_id),
                player_name=snapshot.player_name,
                rank=snapshot.rank,
                score=snapshot.score,
                rounds_played=snapshot.rounds_played,
                policy_version_ids=snapshot.policy_version_ids,
                episode_wins=snapshot.episode_wins,
                episodes_played=snapshot.episodes_played,
            )
            for snapshot in snapshots
            if snapshot.player_id is not None
        ]
        leaderboards = [_win_rate_leaderboard(division_id=division_id, entries=entries)]
        return leaderboards, state

    def describe_division(self, ctx: DivisionDescriptionContext) -> DivisionCommissionerDescriptionPublic:
        """Inherit the base description, but state the REAL Competition scoring
        (win rate), since the stock text describes a mean-score EWMA the
        commissioner no longer uses."""
        description = super().describe_division(ctx)
        if str(getattr(ctx.division, "type", "")) == _COMPETITION_DIVISION_TYPE:
            description.leaderboard_rules = (
                "Players are ranked by their all-time WIN RATE across all rounds — the "
                "fraction of episodes they won (0 to 1). A player's row aggregates their "
                "policy versions' won and played episodes."
            )
            description.scoring_mechanics = (
                "Each Competition round, a player wins at most 1 point per EPISODE they "
                "won (role-agnostic; filler seats never count). The leaderboard score is "
                "the player's WIN RATE = total episodes won / total episodes played, "
                "accumulated all-time and always between 0 and 1. The commissioner "
                "computes the ranking and the platform serves it."
            )
        return description

    def rank_division(self, ctx: DivisionLeaderboardContext) -> list[DivisionLeaderboardSnapshot]:
        """Competition leaderboard = all-time WIN RATE, collapsed to player.

        Each player's score is the fraction of episodes they won across the
        division's completed rounds: total episodes won / total episodes played
        (one point per won episode, capped per episode, fillers excluded — see
        ``_complete_competition_round``). The score is always in ``[0, 1]``.
        Players are ranked by descending win rate; ``rounds_played`` is the number
        of completed rounds the player participated in.

        The board is PLAYER-keyed: a player's policy versions are aggregated into
        one row (their wins summed). Matching/seeding is out of scope here — the
        scheduler round-robins all real entrants — so this method only produces the
        displayed standings.

        Other divisions defer to the stock ewma-blended ranking.
        """
        if str(getattr(ctx.division, "type", "")) != _COMPETITION_DIVISION_TYPE:
            return super().rank_division(ctx)
        if not ctx.completed_rounds or not ctx.round_results:
            return []

        completed_ids = {round_row.id for round_row in ctx.completed_rounds}
        name_by_player: dict[Any, str | None] = {}
        # Every player that appears in a completed round's results — INCLUDING rows
        # gated out below as tainted/unranked. A player who participated but has no
        # surviving ranked round must still be shown (zero win rate), so the board
        # never silently drops an active player (the "6 active players, 5 rows" bug).
        participant_pvids: dict[Any, set] = {}
        # (player_id, round_id) -> best win score for that player in that round, so
        # a multi-episode round contributes one per-round total per player and the
        # win-rate aggregation is over ROUNDS, not raw result rows.
        round_score: dict[tuple[Any, Any], float] = {}
        # Parallel map of episodes the player PLAYED in that round, used as the
        # win-rate denominator (one entry per (player, round), matching the row
        # whose win score we keep).
        round_episodes: dict[tuple[Any, Any], int] = {}
        pvids_by_player: dict[Any, set] = {}
        for result in ctx.round_results:
            if result.round_id not in completed_ids:
                continue
            # Record the participant even for tainted rows so they remain visible.
            name_by_player[result.player_id] = result.player_name
            participant_pvids.setdefault(result.player_id, set()).add(result.policy_version_id)
            # Skip tainted/unranked rows (RANKED_SCORE_COUNT <= 0 marks a -100
            # lobby-taint or a no-real-seat round) for SCORING only.
            if int(result.result_metadata.get(RANKED_SCORE_COUNT_METADATA_KEY, 1)) <= 0:
                continue
            pvids_by_player.setdefault(result.player_id, set()).add(result.policy_version_id)
            key = (result.player_id, result.round_id)
            prior = round_score.get(key)
            if prior is None or result.score > prior:
                round_score[key] = result.score
                round_episodes[key] = int(
                    result.result_metadata.get(COMPLETED_EPISODE_COUNT_METADATA_KEY, 0)
                )

        if not participant_pvids:
            return []

        ranks_by, scores_by = self._mmr_recent_round_lookup(ctx, completed_ids)

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

        # Ensure every participant carries their policy versions, even those with
        # only tainted rounds (so a zero-row player still shows their policies).
        for player_id, pvids in participant_pvids.items():
            pvids_by_player.setdefault(player_id, set()).update(pvids)

        return self._win_total_board(
            round_score,
            name_by_player,
            pvids_by_player,
            recent,
            round_episodes,
            participants=set(participant_pvids),
        )

    def _win_total_board(
        self,
        round_score: dict[tuple[Any, Any], float],
        name_by_player: dict[Any, str | None],
        pvids_by_player: dict[Any, set],
        recent,
        round_episodes: dict[tuple[Any, Any], int] | None = None,
        participants: set | None = None,
    ) -> list[DivisionLeaderboardSnapshot]:
        """Collapse per-(player, round) win scores into the all-time WIN-RATE board.

        Single source of truth for the board shape so ``rank_division`` (full
        history, with recent-round strips) and ``_complete_competition_round``
        (replayed history, ``recent`` returns None) emit IDENTICAL standings —
        which is what keeps both platform writers persisting the SAME board (no
        flip). ``round_score`` maps (player_id, round_id) -> the episodes that
        player WON in the round; ``round_episodes`` maps the same key -> episodes
        the player PLAYED in the round.

        ``participants`` lists every player who took part in a completed round,
        including those whose only rounds were tainted/unranked. Such players are
        shown with a 0 win rate (and 0 rounds played) rather than being dropped, so
        the board never hides an active player.

        RANKING is by the player's all-time WIN RATE — total episodes won divided
        by total episodes played, always in ``[0, 1]`` — descending (highest win
        rate = rank 1), with a stable player-id tiebreak.

        The displayed ``score`` is DISPLAY-ONLY and is the player's ABSOLUTE
        CUMULATIVE SUM of per-round scores across ALL completed rounds, FLOORED AT
        0 (never negative): ``max(0.0, sum of per-round win scores)``. In the
        Competition path the per-round score is the count of episodes the player
        won that round, so the cumulative sum is the player's all-time won-episode
        total. The win RATE is still what orders the board; only the value placed
        in ``score`` differs from the rate. ``rounds_played`` is the number of
        rounds the player appears in.
        """
        round_episodes = round_episodes or {}
        wins_total: dict[Any, float] = {}
        episodes_total: dict[Any, int] = {}
        rounds_played: dict[Any, int] = {}
        # Seed every known participant at zero so a player who took part but earned
        # no ranked round still gets a row (win rate 0, 0 rounds played).
        for player_id in participants or set():
            wins_total.setdefault(player_id, 0.0)
            episodes_total.setdefault(player_id, 0)
            rounds_played.setdefault(player_id, 0)
        for (player_id, round_id), score in round_score.items():
            wins_total[player_id] = wins_total.get(player_id, 0.0) + score
            episodes_total[player_id] = episodes_total.get(player_id, 0) + int(
                round_episodes.get((player_id, round_id), 0)
            )
            rounds_played[player_id] = rounds_played.get(player_id, 0) + 1

        def _win_rate(player_id: Any) -> float:
            played = episodes_total.get(player_id, 0)
            if played <= 0:
                return 0.0
            # Clamp to [0, 1]: wins are capped at 1 per played episode upstream,
            # so this only guards against malformed data.
            return max(0.0, min(1.0, wins_total.get(player_id, 0.0) / played))

        # Rank by descending FULL-PRECISION win rate (the same number shown in the
        # WIN% column), with a single deterministic tiebreak that is IDENTICAL in
        # both publishing paths. ``rank_division`` has player names but the
        # round-complete path (``_competition_win_leaderboards``) intentionally does
        # not store them (the platform resolves names live), so a name-based
        # tiebreak would order tied players differently between the two writers and
        # flip the board. We therefore tiebreak ONLY on the stable player id, which
        # both paths share — keeping the scheduling tick and RoundComplete in lockstep
        # and the rank order exactly descending by the displayed win rate.
        ordered = sorted(
            wins_total,
            key=lambda pid: (-_win_rate(pid), str(pid)),
        )
        snapshots: list[DivisionLeaderboardSnapshot] = []
        for rank, player_id in enumerate(ordered, start=1):
            win_rate = _win_rate(player_id)
            # DISPLAY-ONLY score: absolute cumulative sum of the player's per-round
            # win scores across ALL completed rounds, floored at 0. Ranking above is
            # still by win RATE; this only changes the number shown in the Score
            # column (per-round score is the won-episode count, so this is the
            # player's all-time won-episode total).
            cumulative_score = max(0.0, wins_total.get(player_id, 0.0))
            _emit_decision_log(
                {
                    "division": "Competition",
                    "decision": "WIN_RATE_RANK",
                    "player_id": str(player_id) if player_id is not None else None,
                    "rank": rank,
                    "win_rate": win_rate,
                    "score": cumulative_score,
                    "wins": wins_total.get(player_id, 0.0),
                    "episodes_played": episodes_total.get(player_id, 0),
                    "rounds_played": rounds_played.get(player_id, 0),
                }
            )
            snapshots.append(
                DivisionLeaderboardSnapshot(
                    player_id=player_id,
                    player_name=name_by_player.get(player_id),
                    rank=rank,
                    score=cumulative_score,
                    rounds_played=rounds_played.get(player_id, 0),
                    policy_version_ids=set(pvids_by_player.get(player_id, set())),
                    recent_rounds=recent(player_id),
                    # All-time episode totals across ALL completed rounds (NOT the
                    # recent-rounds strip). These are the Competition Win %
                    # numerator/denominator that the client now reads directly, so
                    # a player who won episodes only outside the last-20 window
                    # still surfaces a nonzero win rate.
                    episode_wins=wins_total.get(player_id, 0.0),
                    episodes_played=episodes_total.get(player_id, 0),
                )
            )
        return snapshots

    def _mmr_recent_round_lookup(
        self, ctx: DivisionLeaderboardContext, completed_ids: set
    ) -> tuple[dict, dict]:
        """Per-(round, player) rank/score for the recent-rounds strip.

        Uses the round's own recorded finishing rank/score (the won-episode
        points) so the recent strip mirrors what happened each round, independent
        of the all-time win-rate ordering.
        """
        best: dict[tuple, Any] = {}
        for result in ctx.round_results:
            if result.round_id not in completed_ids:
                continue
            key = (result.round_id, result.player_id)
            current = best.get(key)
            if current is None or (result.score, -result.rank) > (current.score, -current.rank):
                best[key] = result
        ranks_by = {key: r.rank for key, r in best.items()}
        scores_by = {key: r.score for key, r in best.items()}
        return ranks_by, scores_by


def _win_rate_leaderboard(
    *,
    division_id: UUID,
    entries: list[CommissionerDivisionLeaderboardEntry],
) -> CommissionerDivisionLeaderboard:
    """Build the published Competition board.

    Mirrors the vendored ``division_leaderboard_from_entries`` shape (so the
    platform persists it verbatim instead of re-synthesizing it). The board is
    RANKED by all-time win rate (see ``_win_total_board``), but the ``score``
    column now carries the DISPLAY-ONLY absolute cumulative sum of each player's
    per-round scores (floored at 0), not the win rate — so the column is labeled
    "Score" accordingly. The win-rate metric the UI shows is derived separately
    from each row's recent-round strip.
    """
    view = CommissionerDivisionLeaderboardView(
        key="score",
        title="Score",
        axis_values={"metric": "score", "timeframe": "legacy"},
        columns=[
            CommissionerDivisionLeaderboardColumn(key="rank", label="Rank", value_type="integer", sort="asc"),
            CommissionerDivisionLeaderboardColumn(key="score", label="Score", value_type="number", sort="desc"),
            CommissionerDivisionLeaderboardColumn(
                key="rounds_played", label="Rounds Played", value_type="integer"
            ),
            # All-time episode totals (Competition Win % = episode_wins /
            # episodes_played). Carried as columns/values so the platform persists
            # them verbatim into division.leaderboard_config and the backend can
            # surface them on LeaderboardEntryPublic for the client to compute an
            # all-rounds (not last-20) win rate.
            CommissionerDivisionLeaderboardColumn(
                key="episode_wins", label="Episode Wins", value_type="number"
            ),
            CommissionerDivisionLeaderboardColumn(
                key="episodes_played", label="Episodes Played", value_type="integer"
            ),
        ],
        rows=[
            CommissionerDivisionLeaderboardRow(
                subject_type="player",
                subject_id=entry.player_id,
                subject_name=entry.player_name,
                values={
                    "rank": entry.rank,
                    "score": entry.score,
                    "rounds_played": entry.rounds_played,
                    "episode_wins": entry.episode_wins,
                    "episodes_played": entry.episodes_played,
                },
                policy_version_ids=entry.policy_version_ids,
                recent_rounds=entry.recent_rounds,
            )
            for entry in entries
        ],
    )
    return CommissionerDivisionLeaderboard(
        division_id=division_id,
        default_view_key="score",
        axes=[
            CommissionerDivisionLeaderboardAxis(key="metric", label="Metric"),
            CommissionerDivisionLeaderboardAxis(key="timeframe", label="Timeframe"),
        ],
        views=[view],
    )


def _competition_division_id(round_start: CommissionerRoundStart) -> UUID | None:
    competition = [d for d in round_start.divisions if d.type == "competition"]
    if not competition:
        return None
    return min(competition, key=lambda d: (d.level, d.name, str(d.id))).id


def _competition_division_id_from_snapshots(divisions: list[Any]) -> UUID | None:
    """Lowest-level Competition division id from migration DivisionSnapshots."""
    competition = [d for d in divisions if str(getattr(d, "type", "")) == _COMPETITION_DIVISION_TYPE]
    if not competition:
        return None
    return min(competition, key=lambda d: (d.level, d.name, str(d.id))).id


def _status_str(status: Any) -> str:
    """Normalize a membership status (enum or str) to its lowercase value."""
    value = status.value if hasattr(status, "value") else str(status)
    return value.lower()


def _filler_seats_by_request(
    scheduled_episodes: list[CommissionerProtocolEpisodeRequest] | None,
) -> dict[str, set[int]]:
    """Map each scheduled episode's ``request_id`` to its filler/duplicate seat set.

    The ``filler_seats`` tag is the comma-separated 0-based seat indices that were
    topped up with default fillers (or duplicated real entrants) to fill a
    closed-roster 8-seat game. Those seats are excluded from scoring so a filler
    bot — or a duplicated real policy — never earns points for a seat it only
    occupied to make the game dispatchable.
    """
    mapping: dict[str, set[int]] = {}
    for episode in scheduled_episodes or []:
        raw = episode.tags.get(_FILLER_SEATS_TAG, "")
        seats: set[int] = set()
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                seats.add(int(token))
            except ValueError:
                continue
        if seats:
            mapping[episode.request_id] = seats
    return mapping


def _filler_policy_ids_by_request(
    scheduled_episodes: list[CommissionerProtocolEpisodeRequest] | None,
) -> dict[str, set[str]]:
    """Map each scheduled episode's ``request_id`` to its CONFIGURED filler policy ids.

    The ``filler_policy_version_ids`` tag lists the distinct policy_version_ids that
    were placed purely as fillers (never duplicated real entrants). A policy that
    only ever appears as a filler must NEVER be scored or represented as a real
    entrant; this map lets scoring drop it defensively even if a seat-index slipped.
    """
    mapping: dict[str, set[str]] = {}
    for episode in scheduled_episodes or []:
        raw = episode.tags.get(_FILLER_POLICY_IDS_TAG, "")
        ids = {token.strip() for token in raw.split(",") if token.strip()}
        if ids:
            mapping[episode.request_id] = ids
    return mapping


def _all_filler_policy_ids(
    scheduled_episodes: list[CommissionerProtocolEpisodeRequest] | None,
) -> set[str]:
    """The union of every CONFIGURED filler policy id across the round's episodes.

    Used to (1) drop a pure-filler policy from scoring/ranking everywhere and (2)
    surface, in observability, which policies were fillers this round so they are
    explicitly labeled as fillers and never mistaken for a real entrant.
    """
    ids: set[str] = set()
    for seat_ids in _filler_policy_ids_by_request(scheduled_episodes).values():
        ids |= seat_ids
    return ids


register_commissioner(COMMISSIONER_KEY, CrewriftPrimeSkillCommissioner)
