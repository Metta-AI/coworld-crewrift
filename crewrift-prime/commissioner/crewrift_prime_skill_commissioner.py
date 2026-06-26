"""Crewrift Prime qualifier commissioner — event-driven, replay-evaluated gate.

A subclass of the stock config-driven ``RulesetStrategyCommissioner`` that owns an
EVENT-DRIVEN qualification loop end to end. There is NO "Qualifiers" staging
division any more: when a new policy is submitted to the league, the commissioner
itself (1) creates and runs an EXPERIENCE REQUEST (xp request) self-play episode
for it, (2) downloads and parses the resulting ``.bitreplay`` to derive the skill
metrics, (3) evaluates the strict three-skill gate, and (4) on pass promotes the
policy DIRECTLY into the Competition division (on fail, holds for retry or DQs).

Why a custom image is required
------------------------------
The stock ruleset_strategy commissioner's transition vocabulary
(``TransitionCriteriaConfig``, ``extra="forbid"``) only allows
``completed_episodes_*`` / ``score_*`` and discards every other field of the
per-slot ``results_schema``. To gate on advanced skills we must read the game's
results ourselves -> new image. We go further: we own the xp-request client
(``xp_request_client.py``) and the replay parser (``replay_parser.py``) so the
"submit -> run xp request -> evaluate replay -> promote" loop lives entirely in
the commissioner. The Competition division and its win-count scoring are reused.

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
exercises every role and all three signals come from the parsed replay's per-slot
metrics:

- VOTING  = ``meeting_participation`` — capability/participation, not correctness.
- HUNTING = ``imposter_kills`` — total kills landed by the imposter seat(s).
- TASKS   = ``crew_tasks_mean`` — mean tasks completed across the crew seats.

Pass ALL three -> Competition (competing/champion). Fail any -> hold (status
qualifying) and re-run next time. Crash/infra safety: a completed, parseable
replay with results is not a crash; a genuine non-completion DQs; xp-request infra
failures and replay-parse failures HOLD-retry (never DQ) — there is no qualifier
division to hold IN, so the hold keeps the membership ``qualifying`` in place.

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

from game_results_loader import coerce_results_schema
from mmr import MMR_PLACEMENT_MIN_GAMES, RatedRoundResult, rank_by_mmr
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
from replay_parser import ReplayParseError, parse_replay_metrics
from xp_request_client import XpRequestClient, XpRequestError, XpRequestInfraError, XpRequestRun

NUM_SEATS = 8
COMMISSIONER_KEY = "crewrift_prime_skill"
# Full balanced 8-seat game variant used for Competition rounds (role-mixed,
# imposterCount 2). Falls back to the first variant if absent.
COMPETITION_VARIANT = "default"

_COMPETITION_DIVISION_TYPE = "competition"
# result_metadata score kind tag for Competition win-count rounds.
_COMPETITION_SCORE_KIND = "competition_wins"
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
# toward scoring, rankings, or the leaderboard (see ``_complete_competition_round``,
# which scores by the REAL entrants' own seats and never attributes a filler seat).
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
    "Qualifier could not be evaluated (experience-request dispatch or replay "
    "expansion failed — infrastructure, not a policy crash) — holding for retry."
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
    it, download + parse the resulting ``.bitreplay`` into the per-slot metrics,
    evaluate the strict three-skill gate, and on pass promote the policy DIRECTLY
    into Competition. There is no Qualifiers staging division. The Competition
    division's win-count scheduling/scoring is reused unchanged.
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
        ``filler_seats`` tag so ``_complete_competition_round`` can exclude them.
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
        if remaining > 0:
            topup_pool = filler_ids if filler_ids else entrant_ids
            seat_policies.extend(
                topup_pool[(episode_index + index) % len(topup_pool)]
                for index in range(remaining)
            )

        filler_seats = list(range(real_seat_count, NUM_SEATS))
        return CommissionerProtocolEpisodeRequest(
            request_id=f"competition:{round_start.round_id}:{episode_index}",
            variant_id=variant_id,
            policy_version_ids=seat_policies,
            tags={
                "pool_id": str(round_start.round_id),
                "competition": "1",
                "filler_seats": ",".join(str(seat) for seat in filler_seats),
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

    # ---- event-driven qualification (submission -> xp request -> replay) -------

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

        Steps: create + poll a self-play xp request for the policy, download +
        parse its ``.bitreplay`` into per-slot metrics, evaluate the strict gate,
        and return the membership change — promote to ``target_division_id`` on
        pass, hold (status qualifying) on infra/parse failure, DQ on a genuine
        non-completion. Emits the same ``COMMISSIONER_DECISION`` log + evidence as
        the legacy path. Never raises: infra failures become holds.
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
            title="Qualifier skill gate (xp-request replay + LLM interview)",
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
        """Parse the first completed episode's replay into a game_results dict.

        Returns ``(game_results, None)`` on success, ``(None, error)`` when a
        completed episode existed but its replay could not be parsed (an infra
        hold), or ``(None, None)`` when no completed episode existed at all (a
        genuine non-completion -> caller DQs).
        """
        completed = run.completed_episodes
        if not completed:
            return None, None
        last_error: str | None = None
        for episode in completed:
            if not episode.replay_url:
                last_error = f"completed episode {episode.id} has no replay_url"
                continue
            try:
                replay_bytes = self._xp_request_client().download_replay(episode.replay_url)
                game_results = parse_replay_metrics(replay_bytes, num_seats=NUM_SEATS)
            except (XpRequestInfraError, ReplayParseError) as exc:
                last_error = f"replay parse failed for {episode.id}: {exc}"
                continue
            return coerce_results_schema(game_results), None
        return None, last_error or "no parseable completed episode replay"

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
        """An xp-request/replay infra failure -> HOLD qualifying (never DQ)."""
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
        """Score a Competition round by WINNING PLAYERS: 1 point per winning seat.

        The score is ``imposter_wins + crew_wins`` — one point for each player
        (seat) the entrant occupies that won as imposter, plus one for each that
        won as crew. The imposter/crew split is surfaced in the decision log,
        result_metadata, and round_display. The per-round score and finishing
        rank feed the OpenSkill MMR leaderboard (see ``rank_division``).

        FILLER/duplicate seats (the top-up seats this round scheduled to fill a
        closed-roster 8-seat game when fewer than ``NUM_SEATS`` real entrants are
        competing) are EXCLUDED: a policy is only credited for the seat it was
        legitimately assigned as a real entrant, never for a filler/duplicate seat.
        Filler seats are read from the scheduled episode's ``filler_seats`` tag.
        """
        rule = select_rule(self._config(), view.current_division, view.memberships)
        entries = view.entries(rule)
        filler_seats_by_request = _filler_seats_by_request(scheduled_episodes)

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
            filler_seats = filler_seats_by_request.get(result.request_id, set())
            games.append((game_results, seat_policies, filler_seats))

        records = {}
        completed_counts: dict[str, int] = {}
        for entry in entries:
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
                    "player_id": str(entry.player_id) if entry.player_id is not None else None,
                    "wins": rec.wins,
                    "imposter_wins": rec.imposter_wins,
                    "crew_wins": rec.crew_wins,
                    "episodes_counted": completed_counts[pid],
                }
            )
        return CommissionerRoundComplete(
            results=[CommissionerDivisionRanking(division_id=view.current_division.id, rankings=rankings)],
            round_display={
                "phases": [{"label": "Competition — winning players (1 pt/player, by role)", "episodes": len(games)}],
                "competition_wins": breakdown,
            },
            observability=CommissionerRoundReport.model_validate(
                build_competition_report(
                    breakdown,
                    notes=[f"Scored {len(games)} completed game(s) this round."],
                )
            ),
        )

    def describe_division(self, ctx: DivisionDescriptionContext) -> DivisionCommissionerDescriptionPublic:
        """Inherit the base description, but state the REAL Competition scoring
        (OpenSkill MMR), since the stock text describes a mean-score EWMA the
        commissioner no longer uses."""
        description = super().describe_division(ctx)
        if str(getattr(ctx.division, "type", "")) == _COMPETITION_DIVISION_TYPE:
            description.leaderboard_rules = (
                "Players are ranked by skill rating (MMR), not raw score. A player's row is "
                "their best policy version's rating."
            )
            description.scoring_mechanics = (
                "Each completed Competition round is one OpenSkill (Plackett\u2013Luce) match, with "
                "entrants ordered by winning players (one point per seat that won as imposter or "
                "crew). A policy's MMR is the conservative ordinal mu \u2212 3\u03c3 of its rating; a newly "
                "promoted policy is rated but unranked (\u201cin placement\u201d) until it has played a few "
                "rated rounds, so a single lucky win can't rocket it to the top. The commissioner "
                "computes the ranking and the platform serves it."
            )
        return description

    def rank_division(self, ctx: DivisionLeaderboardContext) -> list[DivisionLeaderboardSnapshot]:
        """Competition leaderboard = per-policy OpenSkill MMR, collapsed to player.

        Each completed Competition round is replayed (oldest-first) as ONE
        Plackett-Luce match, the entrants fed by the finishing rank the round
        already computed from winning-player points (see
        ``_complete_competition_round``). That yields a conservative-ordinal
        ``mu - 3*sigma`` MMR per policy version, a 5-game placement gate, and a
        player-prior init for new versions (faithful to PR Metta-AI/metta#16527).

        The Crewrift Prime board is PLAYER-keyed, so each player's row is their
        BEST out-of-placement policy version (falling back to their best
        in-placement policy when none has cleared placement). A player ranks only
        once all their policies are still in placement -> the player is shown "in
        placement" with no numeric rank, exactly like the per-policy gate.

        Other divisions defer to the stock ewma-blended ranking.
        """
        if str(getattr(ctx.division, "type", "")) != _COMPETITION_DIVISION_TYPE:
            return super().rank_division(ctx)
        if not ctx.completed_rounds or not ctx.round_results:
            return []

        completed_ids = {round_row.id for round_row in ctx.completed_rounds}
        # ``completed_rounds`` is newest-first (platform contract); the rater needs
        # oldest-first so each rating update only depends on earlier rounds.
        oldest_first = [
            round_row.id
            for round_row in sorted(
                ctx.completed_rounds,
                key=lambda r: (r.completed_at or r.created_at, r.round_number),
            )
        ]
        # Map policy_version_id -> player_id/name and skip tainted/unranked rows
        # (RANKED_SCORE_COUNT <= 0 marks a -100 lobby-taint or no-real-seat round).
        player_by_policy: dict[Any, Any] = {}
        name_by_player: dict[Any, str | None] = {}
        rated_results: list[RatedRoundResult] = []
        for result in ctx.round_results:
            if result.round_id not in completed_ids:
                continue
            if int(result.result_metadata.get(RANKED_SCORE_COUNT_METADATA_KEY, 1)) <= 0:
                continue
            player_by_policy[result.policy_version_id] = result.player_id
            name_by_player[result.player_id] = result.player_name
            rated_results.append(
                RatedRoundResult(
                    round_id=result.round_id,
                    policy_version_id=result.policy_version_id,
                    player_id=result.player_id,
                    rank=result.rank,
                    score=result.score,
                )
            )

        ranking = rank_by_mmr(
            completed_round_ids_oldest_first=oldest_first,
            round_results=rated_results,
        )
        if not ranking.by_policy:
            return []

        # Collapse policy versions -> player: a player's row is their best
        # out-of-placement policy; if none cleared placement, their best
        # in-placement policy (so brand-new players still appear, unranked).
        best_by_player: dict[Any, Any] = {}
        agg_by_player: dict[Any, dict] = {}
        for policy in ranking.by_policy:
            player_id = policy.player_id
            agg = agg_by_player.setdefault(
                player_id,
                {
                    "wins": 0,
                    "losses": 0,
                    "games_played": 0,
                    "pvids": set(),
                    "name": name_by_player.get(player_id),
                },
            )
            agg["wins"] += policy.wins
            agg["losses"] += policy.losses
            agg["games_played"] += policy.games_played
            agg["pvids"].add(policy.policy_version_id)

            current = best_by_player.get(player_id)
            # Prefer an out-of-placement policy; among same placement state, prefer
            # higher MMR. ``rank_by_mmr`` already sorted by (-mmr, in_placement),
            # so the first policy seen per (player, placement-state) is its best.
            if current is None:
                best_by_player[player_id] = policy
            elif current.in_placement and not policy.in_placement:
                best_by_player[player_id] = policy
            elif current.in_placement == policy.in_placement and policy.mmr > current.mmr:
                best_by_player[player_id] = policy

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

        # Player MMR = their representative policy's MMR. Out-of-placement players
        # (the representative policy cleared placement) sort first by descending
        # MMR; in-placement players sort after, unranked.
        def sort_key(item):
            player_id, policy = item
            return (policy.in_placement, -policy.mmr, name_by_player.get(player_id) or "", str(player_id))

        ordered = sorted(best_by_player.items(), key=sort_key)
        snapshots: list[DivisionLeaderboardSnapshot] = []
        next_rank = 1
        for player_id, policy in ordered:
            agg = agg_by_player[player_id]
            ranked = not policy.in_placement
            display_rank = next_rank if ranked else None
            if ranked:
                next_rank += 1
            _emit_decision_log(
                {
                    "division": "Competition",
                    "decision": "MMR_RANK",
                    "player_id": str(player_id) if player_id is not None else None,
                    "representative_policy_version_id": str(policy.policy_version_id),
                    "rank": display_rank,
                    "mmr": round(policy.mmr, 4),
                    "mu": round(policy.mu, 4),
                    "sigma": round(policy.sigma, 4),
                    "wins": agg["wins"],
                    "losses": agg["losses"],
                    "games_played": agg["games_played"],
                    "in_placement": not ranked,
                    "placement_min_games": MMR_PLACEMENT_MIN_GAMES,
                }
            )
            snapshots.append(
                DivisionLeaderboardSnapshot(
                    player_id=player_id,
                    player_name=agg["name"],
                    # DivisionLeaderboardSnapshot.rank is non-optional; in-placement
                    # players sort last and are tagged in recent_rounds/score. Use 0
                    # as the sentinel "unranked" rank (UI reads in_placement via the
                    # MMR score + games; rank 0 never collides with 1-based ranks).
                    rank=display_rank if display_rank is not None else 0,
                    score=policy.mmr,
                    rounds_played=agg["games_played"],
                    policy_version_ids=agg["pvids"],
                    recent_rounds=recent(player_id),
                )
            )
        return snapshots

    def _mmr_recent_round_lookup(
        self, ctx: DivisionLeaderboardContext, completed_ids: set
    ) -> tuple[dict, dict]:
        """Per-(round, player) rank/score for the recent-rounds strip.

        Uses the round's own recorded finishing rank/score (the winning-player
        points) so the recent strip mirrors what happened each round, independent
        of the cumulative MMR ordering.
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
        raw = episode.tags.get("filler_seats", "")
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


register_commissioner(COMMISSIONER_KEY, CrewriftPrimeSkillCommissioner)
