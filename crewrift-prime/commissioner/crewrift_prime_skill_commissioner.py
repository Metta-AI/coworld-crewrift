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
``results_schema`` served at ``/v2/episode-requests/{ereq_id}/artifacts/results``,
with the team-member-gated ``/jobs/{job_id}/artifacts/results`` as fallback)
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

Competition scoring: role-weighted points per WON EPISODE — 3 points for an
imposter win, 1 point for a crew win (each episode scores at most once).

Observability (see decision.py for the pure decision function)
--------------------------------------------------------------
For every entrant we build a ``DecisionRecord`` and log one
``COMMISSIONER_DECISION {json}`` line to stdout plus rich membership-event
evidence, identical to before.

Thresholds are constants (env-overridable) in decision.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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
    CommissionerChangelogEntry,
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
    PolicyPoolEntry,
    RoundSpec,
    ScheduleContext,
    V2RoundConfig,
)
from commissioners.common.ruleset_strategy.commissioner import RulesetStrategyCommissioner
from commissioners.common.ruleset_strategy.entrants import division_entries, select_rule
from commissioners.common.ruleset_strategy.round_start import RoundStartView
from commissioners.common.utils import (
    COMPLETED_EPISODE_COUNT_METADATA_KEY,
    RANKED_SCORE_COUNT_METADATA_KEY,
    _current_schedule_slot,
)

from game_results_loader import coerce_results_schema, has_results_schema_arrays
from decision import (
    CREW_WIN_POINTS,
    DECISION_LOG_TAG,
    EXCLUDE_VOID_GAMES,
    IMPOSTER_WIN_POINTS,
    SKILL_GATE_EVIDENCE_TYPE,
    SKILL_GATE_STAGE_ID,
    build_competition_report,
    count_competition_wins,
    episode_is_void,
    evaluate_combined_game_with_interview,
    is_roleless_game,
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

# --- Role-parallel divisions (2026-07-08) ------------------------------------
# Alongside the mixed-role Competition division ("Both"), two role-pinned
# competition divisions grade a policy purely as imposter or purely as crew. All
# three draw entrants from the SAME Competition pool (a policy qualifies once).
#
# The role divisions are matched by their division NAME (the migration creates a
# distinct DivisionSnapshot per YAML entry, all with type "competition"), so the
# subclass routes scheduling/scoring/description by name rather than type alone.
_COMPETITION_DIVISION_NAME = "Competition"
_IMPOSTERS_DIVISION_NAME = "Imposters"
_CREW_DIVISION_NAME = "Crew"
# How the game's role enum labels the two seat roles (see coworld_manifest.json
# game_config.slots[].role and the results-schema imposter/crew arrays).
_ROLE_IMPOSTER = "imposter"
_ROLE_CREW = "crew"
# The default game has NUM_IMPOSTER_SEATS imposters and the rest crew per episode
# (coworld_manifest.json default variant `imposterCount: 2`). The role divisions
# pin exactly this split so the game dispatches identically to Competition.
NUM_IMPOSTER_SEATS = 2
NUM_CREW_SEATS = NUM_SEATS - NUM_IMPOSTER_SEATS
# Episode-request tag marking a role-pinned division round (observability only).
_ROLE_LEAGUE_TAG = "role_league"

# --- Champions (advanced) division (2026-07-08) ------------------------------
# The Champions division is the ADVANCED mixed game: identical seating/scoring to
# the Competition ("Both") division, but only PROVEN policies are seated. A policy
# earns a Champions seat by its recent Competition FORM: its mean role-weighted
# round score across the platform's recent Competition results (``RoundStart.
# recent_results``, which carry each policy's per-round role-weighted ``score``)
# must be >= CHAMPION_MIN_MEAN_SCORE over at least CHAMPION_MIN_ROUNDS scored
# rounds. Both are env-overridable on the hosted runnable with no rebuild. When
# FEWER than 2 policies qualify, the Champions round has nothing to grade (a
# 1-entrant mixed game is meaningless) and dispatches no episodes.
#
# NOTE ON THE METRIC: RoundStart.recent_results exposes only the per-(round,
# policy) role-weighted ``score`` (not episodes played), so the gate uses MEAN
# ROUND SCORE — the same role-weighted points the Standings board accumulates —
# rather than raw win rate. A policy that consistently scores in Competition is,
# by construction, one that consistently wins.
def _f_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw is not None and raw.strip() else default
    except ValueError:
        return default


CHAMPION_MIN_MEAN_SCORE = _f_env("CREWRIFT_PRIME_CHAMPION_MIN_MEAN_SCORE", 3.0)
CHAMPION_MIN_ROUNDS = int(_f_env("CREWRIFT_PRIME_CHAMPION_MIN_ROUNDS", 2))
# Episode-request tag marking a Champions (advanced) division round + the mean
# Competition round score the seated entrant carried in (observability only).
_CHAMPIONS_LEAGUE_TAG = "champions_league"
_CHAMPION_MEAN_SCORE_TAG = "champion_mean_score"

# --- Standings recency window (2026-07-02) -----------------------------------
# The main Competition "Standings" board can optionally grade players on RECENT
# merit only: when enabled, only rounds whose gameplay completed within the last
# ``STANDINGS_WINDOW_HOURS`` hours count toward the standings win rate / cumulative
# score.
#
# DISABLED BY DEFAULT (2026-07-02). A 6-hour window was briefly the default, but it
# collapsed the standings to a SINGLE round whenever the league's other recent
# rounds fell outside the 6h window (a burst of rounds followed by any pause) — so
# the board showed win% = wins / one-round's-episodes (e.g. 5/7 = 71%) instead of
# aggregating every recent round. To avoid silently blanking the multi-round board
# again, the window now DEFAULTS TO 0 (all-time). Opt in explicitly by setting
# ``CREWRIFT_PRIME_STANDINGS_WINDOW_HOURS`` to a positive number of hours on the
# hosted runnable's ``env`` (no rebuild); ``0``/negative/unset/unparseable keeps the
# all-time board.
#
# The window applies ONLY to the Competition Standings the commissioner owns
# (``rank_division`` scheduling-tick board + the ``_complete_competition_round``
# round-complete board — both publish the SAME board via ``_win_total_board``).
# The other Observatory tabs (Rich table / Distribution / Spread / Live) are
# rendered by the separate web app and are NOT affected by this constant.
def _standings_window_hours() -> float:
    raw = os.getenv("CREWRIFT_PRIME_STANDINGS_WINDOW_HOURS")
    if raw is None or not raw.strip():
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


STANDINGS_WINDOW_HOURS = _standings_window_hours()

# --- Commissioner changelog ---------------------------------------------------
# The Prime commissioner is a black box to the platform: operators and players
# can only learn HOW it works and WHAT changed if it tells them. It publishes
# this list (newest first) on every division description; the Observatory renders
# it verbatim in the League Overview. Add a new entry at the TOP whenever the
# commissioner's observable behavior (scoring, scheduling, matchmaking,
# eligibility, filler handling, void-game handling, ...) changes. Keep entries
# player-legible: describe the behavior change, not the code.
PRIME_COMMISSIONER_CHANGELOG: list[CommissionerChangelogEntry] = [
    CommissionerChangelogEntry(
        date="2026-07-13",
        category="scheduling",
        title="New Crew and Imposter divisions + weekly reset",
        detail=(
            "We are introducing 2 new divisions which will separately rank player "
            "performance for the respective role of Crew and Imposters. For example "
            "the new Crew division will only place policies in the crew seats, "
            "imposters will be randomly chosen from a pool of filler policies. "
            "Likewise the Imposter division will only place policies in the imposter "
            "seats, with crewmates being added from a pool of filler policies. The "
            "primary Competition will still be available. At ~1pm today, rounds and "
            "scores for all divisions will reset for a fresh week."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-08",
        category="scheduling",
        title="New Imposters and Crew leagues",
        detail=(
            "Alongside the mixed Competition league, two role-pinned leagues now "
            "grade every policy purely as imposter or purely as crew. The Imposters "
            "league seats the real entrants on the two imposter seats and fills the "
            "crew seats with default filler policies; the Crew league seats the real "
            "entrants on the crew seats and fills the imposter seats with fillers. "
            "All three leagues share the same entrant pool — a policy qualifies once "
            "into Competition and is automatically graded in all three — and each has "
            "its own win-rate standings. Filler seats never count."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-08",
        category="matchmaking",
        title="Fair episode appearances each round",
        detail=(
            "Each Competition round now shuffles the entrant seating order (deterministically "
            "per round, so replays match) before rotating who sits each episode. Previously "
            "entrants near the middle of the join-order list appeared in roughly 50% more "
            "episodes than those at the ends within a round, which gave a skill-independent "
            "scoring advantage; the shuffle equalizes per-entrant appearances within every round."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-06",
        category="overview",
        title="League update through Jul 6",
        detail=(
            "Game episode scores are now role-weighted (3 imposter / 1 crew) and crew "
            "time-limit wins score correctly. Competition uses 36-episode rounds with "
            "role-weighted round scores, enforced $10/pod/episode LLM caps, fair "
            "matchmaking (all entrants seated, anti-collusion seating, one policy per "
            "player), void/filler exclusion, and all-time win-rate standings with true "
            "WIN % on the board. Standings Score is the cumulative sum of role-weighted "
            "round points; gap-era win history is backfilled from platform recent_results. "
            "See crewrift-prime/CHANGELOG.md for the full list."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-06",
        category="eligibility",
        title='Champion status now shows as "active"',
        detail=(
            "A policy that passes the gate and joins Competition now shows the "
            'standard "active" status instead of "champion", matching the '
            "platform's champion flag that drives the leaderboard. Nothing about "
            "scoring, seating, or standings changes."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-05",
        category="scoring",
        title="Imposter wins now score 3 points",
        detail=(
            "Round scores are role-weighted: an episode won as imposter scores 3 "
            "points and an episode won as crew scores 1 point (each episode still "
            "scores at most once). The Standings win rate is unchanged — it keeps "
            "counting a won episode once regardless of role."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-05",
        category="scoring",
        title="Crew time-limit wins now score 1 point",
        detail=(
            "When crewmates win by outlasting imposters until the tick limit, the "
            "game now awards a crew win (1 point in episode results). Previously "
            "these endings were treated as draws and every seat scored 0 — which "
            "was wrong because most crew wins in production end exactly this way."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-05",
        category="scheduling",
        title="Rounds grew to 36 episodes",
        detail=(
            "Each Competition round now schedules 36 episodes (up from 12), so a "
            "single round samples each player across more games and both roles."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-05",
        category="eligibility",
        title="$10 LLM spend cap per pod per episode",
        detail=(
            "Each player pod may spend at most $10 (estimated) on LLM calls per "
            "episode. The cap is enforced by the platform's Bedrock sidecar: once "
            "a pod reaches the limit, further model calls fail with a standard "
            "Bedrock throttling error for the rest of the episode, so players "
            "should handle throttling gracefully."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-03",
        category="matchmaking",
        title="Every competing entrant is seated each round",
        detail=(
            "Competition rounds now seat every competing entrant each round instead of "
            "only the current champion, so all active policies play and are scored each "
            "round rather than waiting on the sidelines."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-02",
        category="matchmaking",
        title="Anti-collusion seating",
        detail=(
            "A player is seated at most once per episode even when they have multiple "
            "policy versions submitted, and the same policy is never placed at two "
            "seats in one game. This closes a loophole where one player could control "
            "multiple seats and collude with themselves."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-02",
        category="eligibility",
        title="One policy per player in Competition",
        detail=(
            "Each player may have at most one active policy in the Competition "
            "division. When a newer policy qualifies, older versions from the same "
            "player are retired automatically."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-02",
        category="scoring",
        title="Standings show all rounds again",
        detail=(
            "Competition standings once more aggregate every completed round (all-time "
            "win rate) instead of only the last few hours. The short recency window was "
            "collapsing the board to a single round whenever the other recent rounds fell "
            "outside the window, so it is now off by default."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-07-02",
        category="scoring",
        title="Void games no longer count",
        detail=(
            "Disconnected or void games in which every player policy scored 0 are "
            "excluded from wins and episodes played, so an infrastructure failure can "
            "no longer drag down a player's win rate."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-06-30",
        category="scoring",
        title="Competition board shows true win rate",
        detail=(
            "Standings rank by win rate (episodes won / episodes played), not MMR. "
            "The board publishes explicit per-player WIN %, all-time win and played "
            "totals, and a cumulative role-weighted round-score column."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-06-28",
        category="matchmaking",
        title="Filler seats excluded from scoring",
        detail=(
            "When the closed 8-seat roster is topped up with filler policies, those "
            "seats — and any policy that only ever appears as filler — are excluded "
            "from scoring and never represented as a real entrant."
        ),
    ),
    CommissionerChangelogEntry(
        date="2026-06-24",
        category="eligibility",
        title="Lowered skill-gate qualification thresholds",
        detail=(
            "Submission qualification thresholds were lowered so more submitted "
            "policies clear the skill gate and reach the Competition division."
        ),
    ),
]

# ISO-8601 timestamp recorded on each per-round win-history row so the
# round-complete publishing path can apply the SAME recency window as
# ``rank_division`` (which reads the round snapshots' real timestamps). Rows
# persisted before this field existed lack it and are treated as in-window
# (never silently dropped) for backward compatibility.
_WIN_HISTORY_RECORDED_AT_KEY = "recorded_at"


# commissioner-state key holding the append-only per-round win history (one entry
# per scored policy per round) so ``_complete_competition_round`` can aggregate the
# full division history and publish the SAME win-rate board ``rank_division``
# computes. Without this, the platform's RoundComplete compat shim fabricates its
# own board from this round's results and that board ping-pongs against the board
# the scheduling tick writes — the leaderboard-flip bug. (The state key string is
# kept for backward compatibility with already-persisted commissioner state.)
_WIN_HISTORY_STATE_KEY = "crewrift_prime_mmr_history"
# Per-(round_id, player_id) role-weighted point totals persisted across round
# completions. Win-history rows recorded before PR #125 stored only episode-win
# counts in ``score``; this map is seeded from platform ``recent_results`` (which
# carry the authoritative role-weighted round score) plus live rankings so the
# round-complete board matches ``rank_division`` even for gap-era history rows.
_WIN_ROUND_POINTS_STATE_KEY = "crewrift_prime_round_points"
# Episode-request tag names recording how the closed-roster 8-seat game was topped
# up. ``filler_seats`` is the comma-separated 0-based seat indices that are NOT a
# real, uniquely-seated entrant; ``filler_policy_version_ids`` is the comma-
# separated set of policy_version_ids placed in those seats. Both are read back at
# scoring time so a filler/duplicate seat — and any policy that only ever appears
# as a filler — is EXCLUDED from scoring and never represented as a real entrant.
_FILLER_SEATS_TAG = "filler_seats"
_FILLER_POLICY_IDS_TAG = "filler_policy_version_ids"
# Max USD a single PLAYER POD may spend per episode (LLM/sidecar usage etc.).
# Enforced the way the platform (`Metta-AI/metta`) does it: the league's
# ``episode_player_pod_llm_spend_limit_usd`` setting (``leagues.settings``) is
# read at episode dispatch and injected into each player pod's Bedrock sidecar
# (``BEDROCK_SIDECAR_SPEND_LIMIT_USD``), which meters estimated token spend and
# rejects further LLM calls with a Bedrock ``ThrottlingException`` once the cap
# is hit. The commissioner keeps that league setting in sync with this value
# (see :meth:`CrewriftPrimeSkillCommissioner._sync_league_spend_limit`) and also
# stamps every scheduled episode with the ``max_spend_per_pod_usd`` tag for
# observability; env-overridable without a rebuild.
MAX_SPEND_PER_POD_USD = float(os.getenv("CREWRIFT_PRIME_MAX_SPEND_PER_POD_USD", "10"))
_MAX_SPEND_TAG = "max_spend_per_pod_usd"
# The platform LeagueSettings field carrying the enforced per-episode
# per-player-pod LLM spend ceiling (see Metta-AI/metta
# app_backend/v2/league_settings.py); plumbed into the pod's Bedrock sidecar
# at dispatch by the platform's job dispatcher.
_LEAGUE_SPEND_LIMIT_SETTING = "episode_player_pod_llm_spend_limit_usd"
# Statuses a freshly submitted (not-yet-qualified) policy carries; these are the
# memberships the event-driven gate runs the xp-request qualification loop for.
_SUBMITTED_STATUSES = ("submitted", "qualifying")
# Status/substatus a held (not-yet-qualified) entrant keeps so qualification is
# retried. There is NO qualifier division to hold IN any more — the membership
# simply stays ``qualifying`` (in whatever division it currently sits) and the
# next submission hook re-runs the loop.
_QUALIFYING_STATUS = "qualifying"
_QUALIFIER_SUBSTATUS = SKILL_GATE_STAGE_ID  # "skill_gate" (stable; re-tested next time)
# Substatus stamped on a policy when it PASSES the gate and is promoted to
# Competition. Uses the platform-native ``active`` value (the Observatory marks an
# ``is_champion`` competitor "active"; see app_backend models
# POLICY_MEMBERSHIP_SUBSTATUS_ACTIVE) rather than a bespoke "champion" string, so the
# commissioner's substatus agrees with the platform's is_champion leaderboard flag
# instead of drifting from it. Substatus is display-only — nothing reads its value.
_ACTIVE_SUBSTATUS = "active"
_INACTIVE_SUBSTATUS = "inactive"
# Substatus for a Competition membership retired because the SAME player promoted
# a newer policy (one-policy-per-player rule). Distinct from ``inactive`` so the
# Observatory can tell "replaced by the player's newer submission" from a real DQ.
_SUPERSEDED_SUBSTATUS = "superseded"
_ONE_POLICY_EVIDENCE_TYPE = "crewrift_prime_one_policy_per_player"
# ONE POLICY PER PLAYER (tournament rule): a player may field at most ONE active
# policy in the Competition division at any time. Enforced in ``migrate_league``
# (the only seam that sees every membership and can emit membership events):
# when a player's newer policy qualifies, their older competing policy is retired
# (superseded), and any pre-existing duplicates are swept the same way. ON by
# default; set CREWRIFT_PRIME_ONE_POLICY_PER_PLAYER=0 (or false/no/off) to disable.
_ONE_POLICY_PER_PLAYER = os.getenv(
    "CREWRIFT_PRIME_ONE_POLICY_PER_PLAYER", "1"
).strip().lower() not in ("0", "false", "no", "off")
# How many self-play episodes the qualifier xp request runs (env-overridable). One
# game already exercises every role in self-play; more reduces single-game variance.
_QUALIFIER_NUM_EPISODES = max(int(os.getenv("CREWRIFT_PRIME_QUALIFIER_EPISODES", "1")), 1)

# Max submitted/qualifying memberships to qualify in a SINGLE migrate_league pass.
#
# `qualify_submission` BLOCKS on a real self-play qualifier game (~250s
# create->complete) plus an LLM interview, all run SERIALLY. The platform's
# qualify pass wraps the whole `migrate_league` call in a single request timeout
# (`_QUALIFY_PASS_REQUEST_TIMEOUT_SECONDS`, ~2100s in metta). If more than a
# handful of policies are pending, qualifying them ALL in one pass exceeds that
# timeout: the pass raises TimeoutError, ZERO events are applied, the backlog
# never drains, and — because the qualify pass and round scheduling contend for
# the same per-league commissioner container — round scheduling is starved too
# (no new rounds get created for ANY division).
#
# So we bound each pass to at most this many memberships (oldest-id first for a
# stable, fair order); the remainder are picked up on subsequent passes. Each
# pass then completes well within the timeout and the backlog drains
# incrementally. Default 6 (6 * ~250s per-game wall ~= 1500s, comfortably under
# the platform's ~2100s qualify-pass timeout with margin for container startup
# and the WS round-trip). Env-overridable without a rebuild; keep the product
# (`_MAX_QUALIFY_PER_PASS` * per-game wall) safely under that timeout.
_MAX_QUALIFY_PER_PASS = max(int(os.getenv("CREWRIFT_PRIME_MAX_QUALIFY_PER_PASS", "6")), 1)

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

    def _entrant_display_names(self, division_id: Any) -> dict[str, dict[str, str | None]]:
        """Best-effort {policy_version_id: {player_name, policy_label}} for a division.

        Joins through the platform's membership API (see
        :meth:`XpRequestClient.get_entrant_display_names`) so the observability
        HTML can lead with a human handle instead of a raw UUID. This is
        DISPLAY-ONLY: scoring never reads it, so ANY failure (auth, network, a
        test double without the method) degrades to an empty map and the report
        falls back to id-based labels — a name lookup must never fail a round.

        Only an ALREADY-CREATED client is used (the qualifier/filler paths create
        it on any live deployment long before a Competition round completes);
        we never construct one here, so an offline/unit-test commissioner does
        no network I/O for a display nicety.
        """
        client = self._xp_client
        if client is None:
            return {}
        try:
            lookup = getattr(client, "get_entrant_display_names", None)
            if lookup is None:
                return {}
            names = lookup(str(division_id))
            return names if isinstance(names, dict) else {}
        except Exception as exc:  # noqa: BLE001 - observability only, never raise.
            print(
                "WARNING: crewrift-prime: entrant display-name lookup failed for "
                f"division {division_id} ({exc}); rendering id-based labels.",
                flush=True,
            )
            return {}

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

    # Sync the league-settings spend limit at most once per commissioner process;
    # the setting is persistent platform state, so one successful write suffices.
    _spend_limit_synced: bool = False

    def _sync_league_spend_limit(self, league_id: Any) -> None:
        """Ensure the platform enforces the per-pod per-episode LLM spend cap.

        This is the SAME mechanism the platform (`Metta-AI/metta`) uses for
        Bedrock spend caps: the league's
        ``episode_player_pod_llm_spend_limit_usd`` setting (``leagues.settings``,
        ``POST /v2/leagues/{league_id}/settings``) is resolved by the job
        dispatcher at episode dispatch and injected into each player pod's
        Bedrock sidecar as ``BEDROCK_SIDECAR_SPEND_LIMIT_USD``; the sidecar
        meters estimated token spend and rejects further LLM calls with a
        standard Bedrock ``ThrottlingException`` once the cap is reached.

        The commissioner writes ``MAX_SPEND_PER_POD_USD`` into that setting
        (read-merge-write so other team-configured settings — episodes per
        round, round interval — are never clobbered) the first time it schedules
        a Competition round. Best-effort: any API/auth/network failure logs a
        warning and is retried on the next round — a settings sync must never
        crash scheduling. The ``max_spend_per_pod_usd`` episode tag remains as
        observability metadata alongside the enforced setting.
        """
        if self._spend_limit_synced or league_id is None:
            return
        try:
            client = self._xp_request_client()
            get_settings = getattr(client, "get_league_settings", None)
            update_settings = getattr(client, "update_league_settings", None)
            if get_settings is None or update_settings is None:
                return  # injected test double without the settings API
            settings = dict(get_settings(str(league_id)))
            current = settings.get(_LEAGUE_SPEND_LIMIT_SETTING)
            if current is not None and float(current) == MAX_SPEND_PER_POD_USD:
                self._spend_limit_synced = True
                return
            settings[_LEAGUE_SPEND_LIMIT_SETTING] = MAX_SPEND_PER_POD_USD
            update_settings(str(league_id), settings)
            self._spend_limit_synced = True
            print(
                "crewrift-prime: set league "
                f"{league_id} {_LEAGUE_SPEND_LIMIT_SETTING}="
                f"{MAX_SPEND_PER_POD_USD:g} (enforced per player pod per episode "
                "by the Bedrock sidecar).",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort; never crash scheduling.
            print(
                "WARNING: crewrift-prime: league spend-limit sync failed for league "
                f"{league_id} ({exc}); the platform keeps its current setting — "
                "will retry next round.",
                flush=True,
            )

    # ---- division detection ---------------------------------------------------

    def _division_kind(self, division: Any) -> str | None:
        """Classify a competition division as ``both`` / ``imposters`` / ``crew``.

        All three role-parallel divisions carry ``type == competition``; they are
        distinguished by their division NAME (from the YAML ``divisions:`` block).
        Returns ``None`` for any non-competition division (defers to the stock base).
        """
        if str(getattr(division, "type", "")) != _COMPETITION_DIVISION_TYPE:
            return None
        name = str(getattr(division, "name", "") or "")
        if name == _IMPOSTERS_DIVISION_NAME:
            return "imposters"
        if name == _CREW_DIVISION_NAME:
            return "crew"
        return "both"

    def _is_competition_round(self, view: RoundStartView) -> bool:
        """True for any of the three competition divisions (Both / Imposters / Crew)."""
        return self._division_kind(view.current_division) is not None

    def _is_role_division(self, division: Any) -> bool:
        """True for the role-pinned Imposters/Crew divisions (not the mixed Both)."""
        return self._division_kind(division) in ("imposters", "crew")

    def _competition_variant_id(self, round_start: CommissionerRoundStart) -> str:
        """The full balanced game for Competition, falling back to the first variant."""
        available = {variant.id for variant in round_start.variants}
        if COMPETITION_VARIANT in available:
            return COMPETITION_VARIANT
        return round_start.variants[0].id if round_start.variants else "default"

    def _role_slots_game_config(self, round_start: CommissionerRoundStart) -> dict[str, Any]:
        """Full ``slots`` override pinning ``NUM_IMPOSTER_SEATS`` imposters first.

        The game merges an episode's ``game_config`` over the variant's, so we must
        supply the FULL ``slots`` array (seat order fixed): the first
        ``NUM_IMPOSTER_SEATS`` seats are imposters and the rest crew. We preserve
        the variant's per-seat colors/tokens where present so the role override does
        not clobber the game's cosmetic slot config.
        """
        base_slots: list[dict[str, Any]] = []
        variant = next(
            (v for v in round_start.variants if v.id == self._competition_variant_id(round_start)),
            None,
        )
        if variant is not None:
            variant_slots = variant.game_config.get("slots")
            if isinstance(variant_slots, list):
                base_slots = [dict(slot) if isinstance(slot, dict) else {} for slot in variant_slots]
        slots: list[dict[str, Any]] = []
        for seat in range(NUM_SEATS):
            slot = dict(base_slots[seat]) if seat < len(base_slots) else {}
            slot["role"] = _ROLE_IMPOSTER if seat < NUM_IMPOSTER_SEATS else _ROLE_CREW
            slots.append(slot)
        return {"slots": slots}

    # ---- scheduling -----------------------------------------------------------

    def schedule_rounds(self, ctx: ScheduleContext) -> list[RoundSpec]:
        """Schedule rounds for all three competition divisions.

        The stock scheduler counts a division's entrants from its OWN memberships,
        so the role-pinned Imposters/Crew divisions — which carry no memberships of
        their own (a policy qualifies once into Competition and is graded in all
        three; the league uniqueness constraint allows only one live membership per
        policy) — would never get a round. We therefore:

        - schedule the mixed Competition division and any other divisions via the
          stock scheduler (unchanged), and
        - additionally schedule the role divisions here, sourcing their entrant list
          from the SHARED Competition pool. A role round is skipped only when there
          are no Competition entrants or a round is already pending/running for that
          division in the current schedule slot (same cadence guard as the stock
          scheduler).

        Platform contract: the round runner must accept these rounds even though the
        role division has no memberships, by resolving ``entrant_policy_version_ids``
        against live league memberships (see metta
        ``_prepare_container_commissioner_round`` / ``_resolve_container_round_memberships``).
        Without that fallback every Imposters/Crew round fails immediately with
        ``has no active memberships for container commissioner``.
        """
        specs = super().schedule_rounds(ctx)
        config = self._config()
        current_slot = _current_schedule_slot(datetime.now(UTC), config)

        competition_division = next(
            (d for d in ctx.divisions if self._division_kind(d) == "both"), None
        )
        if competition_division is None:
            return specs
        competition_rule = select_rule(
            config, competition_division, ctx.active_memberships
        )
        competition_entrants = division_entries(
            competition_division, ctx.active_memberships, competition_rule
        )
        if not competition_entrants:
            return specs

        for division in ctx.divisions:
            if not self._is_role_division(division):
                continue
            division_rounds = [r for r in ctx.recent_rounds if r.division_id == division.id]
            if any(r.status in ("pending", "claimed", "running") for r in division_rounds):
                continue
            latest_round = max(division_rounds, key=lambda r: r.created_at, default=None)
            if latest_round is not None and latest_round.created_at >= current_slot:
                continue
            rule = select_rule(config, division, ctx.active_memberships)
            stages = rule.stages if rule is not None and rule.stages is not None else config.stages
            specs.append(
                RoundSpec(
                    division_id=division.id,
                    round_config=V2RoundConfig(
                        stages=stages,
                        entrant_policy_version_ids=[
                            entry.policy_version_id for entry in competition_entrants
                        ],
                    ),
                    execution_backend=config.default_execution_backend,
                    notes=f"auto-scheduled by {type(self).__name__}:role:{division.name}",
                )
            )
        return specs

    def schedule_episodes_for_round_start(
        self, round_start: CommissionerRoundStart
    ) -> CommissionerScheduleEpisodes:
        config = self._config()
        view = RoundStartView(round_start, config)
        kind = self._division_kind(view.current_division)
        if kind == "both":
            return self._schedule_competition_round(round_start, view)
        if kind == "imposters":
            return self._schedule_role_round(round_start, view, role="imposters")
        if kind == "crew":
            return self._schedule_role_round(round_start, view, role="crew")
        # No staging/qualifier rounds exist any more — qualification is event-driven
        # via migrate_league/qualify_submission, not a scheduled self-play round.
        return super().schedule_episodes_for_round_start(round_start)

    def _schedule_competition_round(
        self, round_start: CommissionerRoundStart, view: RoundStartView
    ) -> CommissionerScheduleEpisodes:
        """Schedule full 8-seat Competition games with AT MOST ONE seat per PLAYER.

        The closed-roster 8-seat crewrift game must dispatch exactly ``NUM_SEATS``
        policies. Each episode seats every competing PLAYER AT MOST ONCE — no player
        occupies two seats in a game, even if they submitted multiple policy
        versions (only one of a player's versions is ever seated per episode). When
        fewer than ``NUM_SEATS`` distinct players are competing, the remaining seats
        are TOPPED UP with the standard default filler policies (resolved by
        :meth:`_filler_policy_version_ids`: the
        ``CREWRIFT_PRIME_FILLER_POLICY_VERSION_IDS`` env override, else the
        per-league fillers served by ``GET /v2/leagues/{league_id}/filler-policies``).
        If no fillers are configured we fall back to cycling real entrants into the
        empty seats so the game can still run — but NEVER seating a policy whose
        PLAYER already holds a seat in that same episode (so one player can't control
        two seats and collude with itself). Only when there are genuinely more seats
        than distinct players (and no fillers) is a seat unavoidably duplicated, and
        then it EXACTLY copies an already-seated policy rather than a player's other
        version. Those duplicate top-up seats, like all filler seats, are recorded in
        ``filler_seats`` and EXCLUDED from scoring/ranking by
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
        # Fairness: PERMUTE the entrant order once per round before the per-episode
        # seat rotation below. `_competition_episode` seats a sliding window of the
        # first NUM_SEATS distinct players, shifted by `episode_index`; with a FIXED
        # entrant order that window is a rolling band over a stable seed order, so
        # entrants near the MIDDLE of the list appear in ~50% more of a round's
        # episodes than those at the ends (a bell curve over seed_order, which tracks
        # join order). Appearances drive round score, so a stable order hands a
        # standing, skill-independent advantage to whoever sits mid-list. Shuffling
        # per round (seeded from round_id so it is deterministic and replayable, and
        # differs every round so no entrant is permanently advantaged) precesses the
        # band and equalizes per-entrant appearances across rounds — the
        # `shuffled_window` principle applied to this closed-roster scheduler.
        _seed = int.from_bytes(
            hashlib.sha256(str(round_start.round_id).encode()).digest()[:8], "big"
        )
        random.Random(_seed).shuffle(entrant_ids)
        # Map each real entrant policy to its PLAYER so a single player can never
        # occupy two seats in one episode — even across two DIFFERENT policy
        # versions they submitted. Fillers (and any policy with no known player)
        # have no player id and are only deduped by policy. When two entrant
        # policies share a player id they collapse to one seat-eligible player.
        player_by_policy = {
            entry.policy_version_id: (
                str(entry.player_id) if entry.player_id is not None else None
            )
            for entry in entries
        }
        league_id = getattr(round_start.league, "id", None)
        # Keep the platform-enforced per-pod per-episode LLM spend cap in sync
        # (league setting -> Bedrock sidecar; best-effort, never blocks a round).
        self._sync_league_spend_limit(league_id)
        filler_ids = self._filler_policy_version_ids(league_id)
        episodes = [
            self._competition_episode(
                round_start=round_start,
                episode_index=episode_index,
                entrant_ids=entrant_ids,
                player_by_policy=player_by_policy,
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
        player_by_policy: dict[UUID, str | None],
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

        No single PLAYER ever occupies two seats in the same episode: real seats are
        deduped by player (so two policy versions submitted by the same player never
        share a game), and the top-up never seats a policy whose player is already
        present. This prevents a player from controlling multiple seats and colluding
        with itself.
        """
        # Dedup identity for a policy: its PLAYER if known, else the policy id. Two
        # policies from the same player collapse to one identity so they can't share
        # a game. Fillers/unknown-player policies dedup by their own policy id.
        def seat_key(policy: UUID) -> str:
            player = player_by_policy.get(policy)
            return f"player:{player}" if player is not None else f"policy:{policy}"

        # Real entrants, at most one seat PER PLAYER, rotated per episode for balance.
        seat_policies: list[UUID] = []
        seated_keys: set[str] = set()
        if entrant_ids:
            offset = episode_index % len(entrant_ids)
            rotated = entrant_ids[offset:] + entrant_ids[:offset]
            for policy in rotated:
                if len(seat_policies) >= NUM_SEATS:
                    break
                key = seat_key(policy)
                if key in seated_keys:
                    continue
                seat_policies.append(policy)
                seated_keys.add(key)
        real_seat_count = len(seat_policies)

        # Top up the remaining seats. Prefer configured fillers (whose results are
        # excluded). When none are configured, cycle real entrants so the closed
        # roster can still dispatch (those duplicate seats are excluded from
        # scoring) — but NEVER seat a policy whose PLAYER is already in THIS episode,
        # so a single player can't hold two seats and collude with itself. A policy
        # is only reused for a top-up seat once every OTHER distinct player has
        # already been placed (i.e. the pool is genuinely exhausted, e.g. a single
        # entrant with no fillers).
        remaining = NUM_SEATS - real_seat_count
        topup_is_filler = bool(filler_ids)
        if remaining > 0:
            topup_pool = filler_ids if topup_is_filler else entrant_ids
            seat_policies.extend(
                _distinct_topup(
                    topup_pool,
                    already_seated=seat_policies,
                    count=remaining,
                    rotation=episode_index,
                    key=seat_key,
                )
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
                # Per-episode spend cap for each player pod (USD), enforced platform-side.
                _MAX_SPEND_TAG: f"{MAX_SPEND_PER_POD_USD:g}",
            },
        )

    # ---- role-pinned divisions (Imposters / Crew) -----------------------------

    def _competition_entries(self, view: RoundStartView) -> list[PolicyPoolEntry]:
        """Entrants for a ROLE division, sourced from the COMPETITION pool.

        The Imposters and Crew divisions do not carry their own memberships — a
        policy qualifies once into Competition and is graded in all three
        divisions. So a role round's entrant pool is the Competition division's
        competing memberships, not the (empty) role-division memberships. We locate
        the Competition division snapshot in this round and reuse the shared
        ``division_entries`` selection against it.
        """
        competition_division = next(
            (
                division
                for division in view.divisions
                if self._division_kind(division) == "both"
            ),
            None,
        )
        if competition_division is None:
            return []
        rule = select_rule(self._config(), competition_division, view.memberships)
        entries = division_entries(competition_division, view.memberships, rule)
        # Honor the round_config's frozen entrant list (set by ``schedule_rounds``
        # from the Competition pool at round-creation time) so the seating and the
        # scoring agree on exactly which policies competed this round, even if the
        # Competition roster changed between round creation and execution.
        configured_order = view.round_config.get("entrant_policy_version_ids")
        if isinstance(configured_order, list):
            order = {
                UUID(str(policy_id)): index
                for index, policy_id in enumerate(configured_order)
            }
            entries = [entry for entry in entries if entry.policy_version_id in order]
            entries.sort(key=lambda entry: order[entry.policy_version_id])
        for index, entry in enumerate(entries):
            entry.pool_id = view.round_start.round_id
            entry.seed_order = index
        return entries

    def _schedule_role_round(
        self, round_start: CommissionerRoundStart, view: RoundStartView, *, role: str
    ) -> CommissionerScheduleEpisodes:
        """Schedule role-pinned 8-seat games for the Imposters or Crew division.

        The real entrants (drawn from the SAME Competition pool as the mixed
        Competition division) are seated ONLY on the seats of the division's target
        role, and the OTHER role's seats are filled with default filler policies:

        - ``imposters``: real entrants occupy the ``NUM_IMPOSTER_SEATS`` imposter
          seats (0..NUM_IMPOSTER_SEATS-1); the ``NUM_CREW_SEATS`` crew seats are
          fillers.
        - ``crew``: real entrants occupy the ``NUM_CREW_SEATS`` crew seats
          (NUM_IMPOSTER_SEATS..NUM_SEATS-1); the ``NUM_IMPOSTER_SEATS`` imposter
          seats are fillers.

        Roles are pinned via the episode ``game_config`` (a full ``slots`` array —
        first ``NUM_IMPOSTER_SEATS`` imposter, rest crew) so the game assigns the
        intended role to every seat rather than the default random split. As in
        Competition, each PLAYER occupies at most one real seat per episode, the
        entrant order is shuffled per round and rotated per episode for fair
        appearances, and filler seats are tagged and excluded from scoring.

        When no fillers are configured, the opposing-role seats fall back to
        duplicating already-seated real entrants (still excluded from scoring) so
        the closed roster can dispatch.
        """
        entries = self._competition_entries(view)
        if not entries:
            return CommissionerScheduleEpisodes(episodes=[])
        real_role = _ROLE_IMPOSTER if role == "imposters" else _ROLE_CREW
        real_seat_count = NUM_IMPOSTER_SEATS if role == "imposters" else NUM_CREW_SEATS
        num_episodes = self._competition_num_episodes(view, len(entries))
        variant_id = self._competition_variant_id(round_start)
        game_config = self._role_slots_game_config(round_start)
        entrant_ids = [entry.policy_version_id for entry in entries]
        # Same per-round shuffle as Competition (deterministic from round_id), so
        # role rounds also equalize per-entrant appearances rather than favoring the
        # middle of the join-order list.
        _seed = int.from_bytes(
            hashlib.sha256(f"{role}:{round_start.round_id}".encode()).digest()[:8], "big"
        )
        random.Random(_seed).shuffle(entrant_ids)
        player_by_policy = {
            entry.policy_version_id: (
                str(entry.player_id) if entry.player_id is not None else None
            )
            for entry in entries
        }
        league_id = getattr(round_start.league, "id", None)
        self._sync_league_spend_limit(league_id)
        filler_ids = self._filler_policy_version_ids(league_id)
        episodes = [
            self._role_episode(
                round_start=round_start,
                episode_index=episode_index,
                role=role,
                real_role=real_role,
                real_seat_count=real_seat_count,
                entrant_ids=entrant_ids,
                player_by_policy=player_by_policy,
                filler_ids=filler_ids,
                variant_id=variant_id,
                game_config=game_config,
            )
            for episode_index in range(num_episodes)
        ]
        return CommissionerScheduleEpisodes(episodes=episodes)

    def _role_episode(
        self,
        *,
        round_start: CommissionerRoundStart,
        episode_index: int,
        role: str,
        real_role: str,
        real_seat_count: int,
        entrant_ids: list[UUID],
        player_by_policy: dict[UUID, str | None],
        filler_ids: list[UUID],
        variant_id: str,
        game_config: dict[str, Any],
    ) -> CommissionerProtocolEpisodeRequest:
        """Build one role-division episode: real entrants on target-role seats.

        Seats 0..NUM_IMPOSTER_SEATS-1 are the imposter seats and the rest are crew
        seats (matching ``game_config`` slot roles). The target role's seats are
        filled first with up to ``real_seat_count`` distinct-player real entrants
        (rotated per episode), then the OTHER role's seats — and any target-role
        seats left unfilled because there were fewer than ``real_seat_count`` real
        players — are topped up with fillers (or duplicated reals when no fillers
        are configured). Every non-real seat is recorded in ``filler_seats`` and, if
        a configured filler, in ``filler_policy_version_ids`` so scoring excludes it.
        """

        def seat_key(policy: UUID) -> str:
            player = player_by_policy.get(policy)
            return f"player:{player}" if player is not None else f"policy:{policy}"

        # Real seats of the target role: at most one per player, rotated per episode.
        real_policies: list[UUID] = []
        seated_keys: set[str] = set()
        if entrant_ids:
            offset = episode_index % len(entrant_ids)
            rotated = entrant_ids[offset:] + entrant_ids[:offset]
            for policy in rotated:
                if len(real_policies) >= real_seat_count:
                    break
                key = seat_key(policy)
                if key in seated_keys:
                    continue
                real_policies.append(policy)
                seated_keys.add(key)

        # Assign the target-role seat indices to the real policies. Imposter seats
        # are 0..NUM_IMPOSTER_SEATS-1; crew seats are the rest.
        if real_role == _ROLE_IMPOSTER:
            target_seats = list(range(0, NUM_IMPOSTER_SEATS))
        else:
            target_seats = list(range(NUM_IMPOSTER_SEATS, NUM_SEATS))

        seat_policies: list[UUID | None] = [None] * NUM_SEATS
        real_seats: list[int] = []
        for policy, seat in zip(real_policies, target_seats):
            seat_policies[seat] = policy
            real_seats.append(seat)

        # Every seat that is not a legitimately-seated real entrant (the opposing
        # role's seats, plus any target-role seats left empty when fewer than
        # real_seat_count players are competing) is a filler seat.
        empty_seats = [seat for seat in range(NUM_SEATS) if seat not in real_seats]
        topup_is_filler = bool(filler_ids)
        already_seated = [p for p in seat_policies if p is not None]
        topup_pool = filler_ids if topup_is_filler else entrant_ids
        topups = _distinct_topup(
            topup_pool,
            already_seated=already_seated,
            count=len(empty_seats),
            rotation=episode_index,
            key=seat_key,
        )
        for seat, policy in zip(empty_seats, topups):
            seat_policies[seat] = policy

        # Defensive: if the pool was empty and _distinct_topup returned nothing,
        # duplicate an already-seated policy so the closed roster still dispatches.
        for seat in range(NUM_SEATS):
            if seat_policies[seat] is None:
                seat_policies[seat] = already_seated[seat % len(already_seated)]

        resolved = [p for p in seat_policies if p is not None]
        filler_seats = sorted(empty_seats)
        filler_policy_ids = (
            {str(seat_policies[seat]) for seat in filler_seats} if topup_is_filler else set()
        )
        return CommissionerProtocolEpisodeRequest(
            request_id=f"{role}:{round_start.round_id}:{episode_index}",
            variant_id=variant_id,
            policy_version_ids=resolved,
            game_config=game_config,
            tags={
                "pool_id": str(round_start.round_id),
                "competition": "1",
                _ROLE_LEAGUE_TAG: role,
                _FILLER_SEATS_TAG: ",".join(str(seat) for seat in filler_seats),
                _FILLER_POLICY_IDS_TAG: ",".join(sorted(filler_policy_ids)),
                _MAX_SPEND_TAG: f"{MAX_SPEND_PER_POD_USD:g}",
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

        BOUNDED PER PASS: qualification blocks on a real self-play game + interview
        per membership, run serially, and the platform wraps the whole call in one
        request timeout. We therefore qualify at most ``_MAX_QUALIFY_PER_PASS``
        memberships per pass (never-attempted first, then id), draining any larger
        backlog over successive passes so a pass never times out (which would apply
        zero events and starve round scheduling).

        NOTE for the platform: to make qualification fire promptly on each new
        submission, the platform must invoke this migration hook when a policy is
        submitted. There is no other commissioner entrypoint that observes a new
        submission (see module docstring / README).

        TOURNAMENT RULE — one policy per player: after qualification events are
        drafted, :func:`_enforce_one_policy_per_player` retires (supersedes) any
        OLDER active Competition membership held by a player whose newer policy
        just promoted, and sweeps pre-existing duplicates, so a player never
        fields two policies in the Competition division at once. Env-disable
        with ``CREWRIFT_PRIME_ONE_POLICY_PER_PLAYER=0``.
        """
        result = super().migrate_league(ctx)
        events = list(result.policy_membership_events)
        target_division_id = _competition_division_id_from_snapshots(ctx.divisions)
        # Qualify at most `_MAX_QUALIFY_PER_PASS` memberships per pass. Each
        # `qualify_submission` blocks on a ~250s self-play game + LLM interview run
        # serially, and the platform wraps this whole call in one request timeout;
        # qualifying an unbounded backlog in a single pass would time out, apply
        # ZERO events (so the backlog never drains), and starve round scheduling
        # (which shares the per-league commissioner container). We take a stable
        # slice per pass and let later passes handle the rest.
        #
        # Order never-attempted memberships (substatus is None) FIRST: a policy that
        # keeps holding (e.g. a re-tested skill_gate) carries a substatus, so without
        # this a backlog of low-id holds would fill the whole slice every pass and a
        # fresh submission would never be evaluated at all (stuck qualifying with a
        # null substatus indefinitely). The `id` tiebreak keeps each pass's slice
        # stable. The snapshot carries no timestamp, so `substatus is None` is the
        # only "never attempted" signal available.
        pending = sorted(
            (m for m in ctx.memberships if _status_str(m.status) in _SUBMITTED_STATUSES),
            key=lambda m: (m.substatus is not None, str(m.id)),
        )
        for membership in pending[:_MAX_QUALIFY_PER_PASS]:
            events.append(self.qualify_submission(membership, target_division_id))
        if _ONE_POLICY_PER_PLAYER:
            events = _enforce_one_policy_per_player(ctx, events, target_division_id)
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

        run, game_results, terminal = self._run_qualifier_game(
            membership, division_id, policy_version_id, notes="crewrift-prime qualifier (event-driven)"
        )
        if terminal is not None:
            return terminal

        # Degenerate game guard: a parseable results JSON in which NO seat reached
        # role assignment (both the imposter and crew per-slot arrays are entirely
        # zero) never played a real match — the self-play game ended before roles
        # were assigned. In self-play (one policy on every seat) a broken/degenerate
        # policy can cause that BY ITSELF, so — unlike a multi-policy game — it is
        # NOT reliable evidence of an infra fault. Confirm with one more game before
        # deciding: a rare transient produces a real match on the retry (score it);
        # a policy that reliably cannot reach a real match is a non-completion (DQ),
        # not an infra hold that retries forever. A genuine infra/crash on the retry
        # still holds/DQs on its own terms.
        if is_roleless_game(game_results):
            run, game_results, terminal = self._run_qualifier_game(
                membership, division_id, policy_version_id,
                notes="crewrift-prime qualifier (roleless confirmation)",
            )
            if terminal is not None:
                return terminal
            if is_roleless_game(game_results):
                return self._roleless_dq_event(membership, run)

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
                substatus=_ACTIVE_SUBSTATUS,
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

        Reads the game's own end-of-episode ``results`` artifact via
        :meth:`XpRequestClient.get_episode_results` — the episode-request route
        (``GET /v2/episode-requests/{ereq_id}/artifacts/results``) first, with the
        team-member-gated job route (``GET /jobs/{job_id}/artifacts/results``) as a
        fallback — the seat-indexed ``results_schema`` payload
        (``scores``/``win``/``tasks``/``kills``/``imposter``/``crew``
        /``vote_players``/``vote_skip``/``vote_timeout``) that
        :func:`decision.evaluate_combined_game` consumes. NO replay download
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
            if not episode.job_id and not episode.id:
                last_error = f"completed episode {episode.id} has no job_id or episode-request id"
                continue
            try:
                game_results = self._xp_request_client().get_episode_results(
                    episode.job_id or "", episode_request_id=episode.id or None
                )
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

    def _run_qualifier_game(
        self,
        membership: MembershipSnapshot,
        division_id: str,
        policy_version_id: str,
        *,
        notes: str,
    ) -> tuple[XpRequestRun | None, dict | None, ModelsPolicyMembershipEventChange | None]:
        """Run ONE qualifier game and classify the platform-side outcome.

        Returns ``(run, game_results, terminal_event)``:

        - ``terminal_event`` set -> an infra hold (xp-request create/poll/results
          fetch failed) or a crash DQ (no completed/parseable episode); the caller
          returns it directly.
        - otherwise ``game_results`` is a parseable game. It MAY be roleless — the
          caller decides whether to confirm-and-DQ or score it.
        """
        try:
            run = self._xp_request_client().run_qualifier(
                division_id=division_id,
                policy_version_id=policy_version_id,
                num_episodes=_QUALIFIER_NUM_EPISODES,
                notes=notes,
            )
        except XpRequestInfraError as exc:
            return None, None, self._infra_hold_event(membership, f"experience request failed: {exc}")

        game_results, parse_error = self._game_results_from_run(run)
        if parse_error is not None:
            return run, None, self._infra_hold_event(membership, parse_error)
        if game_results is None:
            # Terminal run, no completed/parseable episode -> genuine non-completion.
            return run, None, self._crash_dq_event(membership, run)
        return run, game_results, None

    def _roleless_dq_event(
        self, membership: MembershipSnapshot, run: XpRequestRun
    ) -> ModelsPolicyMembershipEventChange:
        """Repeated roleless self-play games -> the policy cannot reach a real match.

        Classified as a non-completion DQ (not an infra hold that retries forever).
        A policy wrongly caught here can simply be resubmitted.
        """
        _emit_decision_log(
            {
                "policy_version_id": str(membership.policy_version_id),
                "xreq_id": run.xreq_id,
                "decision": "ROLELESS_DQ",
                "reason": "Qualifier games assigned no roles on repeated attempts (no real match)",
            }
        )
        return ModelsPolicyMembershipEventChange(
            league_policy_membership_id=membership.id,
            from_division_id=membership.division_id,
            to_division_id=membership.division_id,
            status="disqualified",
            substatus=_INACTIVE_SUBSTATUS,
            reason="Qualifier never reached a real match",
            notes=(
                "The self-play qualifier assigned no imposter/crew roles on repeated attempts "
                "(the match ended before roles were assigned). Disqualified rather than held "
                "for retry; resubmit to try again."
            ),
            evidence=[
                ModelsPolicyMembershipEventEvidence(
                    type="crewrift_prime_qualifier_roleless",
                    title="Qualifier never reached a real match",
                    summary="Repeated self-play qualifier games assigned no imposter/crew roles.",
                    metadata={"classified_as": "non_completion", "xreq_id": run.xreq_id},
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

    # ---- Competition division: score = role-weighted won episodes (3 imposter / 1 crew) ---

    def _complete_competition_round(
        self,
        round_start: CommissionerRoundStart,
        view: RoundStartView,
        episode_results: list[CommissionerProtocolEpisodeResult],
        scheduled_episodes: list[CommissionerProtocolEpisodeRequest] | None = None,
    ) -> CommissionerRoundComplete:
        """Score a Competition round by WON EPISODES, role-weighted: 3 points per
        episode won as imposter, 1 point per episode won as crew.

        A player scores points for each episode in which at least one of its
        (non-filler) seats won — each episode is scored at most once regardless of
        how many of its seats won: 3 points if any winning seat was an imposter,
        else 1 point for a crew win. The imposter/crew split of the winning seats
        is surfaced in the decision log, result_metadata, and round_display for
        observability. The per-round win count (won episodes, role-agnostic) feeds
        the win-rate leaderboard (see ``rank_division``).

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

        VOID/DISCONNECTED games are also excluded: an episode in which every real
        (non-filler) player seat scored 0 (nobody won) is treated as a disconnected
        game and dropped from BOTH the win-rate numerator (wins) and denominator
        (episodes played), so broken games never drag down players' win %. This is
        on by default (``EXCLUDE_VOID_GAMES``; disable with
        ``CREWRIFT_PRIME_COUNT_VOID_GAMES=1``) and, because it reduces the shared
        ``episodes_played`` metadata that both publishing paths consume, it applies
        identically to ``rank_division`` and this round-complete board.
        """
        # Entrants: for the mixed Competition division the roster IS this division's
        # memberships; for the role-pinned Imposters/Crew divisions the roster is the
        # SHARED Competition pool (a policy qualifies once and is graded in all three
        # divisions), so source entrants from Competition there.
        division_label = str(getattr(view.current_division, "name", "") or "Competition")
        if self._is_role_division(view.current_division):
            entries = self._competition_entries(view)
        else:
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

        # Best-effort display names (player_name / policy_label) for the report's
        # HTML view, keyed by policy_version_id. Round-start memberships carry only
        # ids, so we join through the platform's membership API; observability
        # never fails a round, so a lookup failure just falls back to id labels.
        display_names = self._entrant_display_names(view.current_division.id)

        # Per episode: coerced game_results, the policy at each seat, and the seat
        # indices that are filler/duplicate top-up (excluded from scoring).
        #
        # VOID/DISCONNECTED games (see ``episode_is_void``) are dropped here so they
        # count toward NEITHER the win-rate numerator (wins) NOR its denominator
        # (episodes played): a disconnected episode in which every real player seat
        # scored 0 was no real contest and must not drag down players' win %. This
        # single filter feeds every downstream count (``episodes_with_seats`` and
        # ``completed_counts``, which becomes ``episodes_played``), so BOTH the
        # ``rank_division`` and ``_complete_competition_round`` publishing paths see
        # the same reduced episode set and stay in lockstep. Filler seats never
        # count, so an episode won only by fillers is still void for real players.
        games: list[tuple[dict, list[str], set[int]]] = []
        void_games = 0
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
            if EXCLUDE_VOID_GAMES:
                player_seats = [
                    i for i in range(len(seat_policies)) if i not in filler_seats
                ]
                if episode_is_void(game_results, player_seats):
                    void_games += 1
                    continue
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
                    "division": division_label,
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
                        "points": rec.points,
                        "episode_wins": rec.episode_wins,
                        "imposter_wins": rec.imposter_wins,
                        "crew_wins": rec.crew_wins,
                        "imposter_episode_wins": rec.imposter_episode_wins,
                        "crew_episode_wins": rec.crew_episode_wins,
                    },
                )
            )
            breakdown.append(
                {
                    "policy_version_id": pid,
                    "player_id": str(entry.player_id) if entry.player_id is not None else None,
                    # Human display names for the observability HTML (never used
                    # for scoring); None when the membership lookup had no name.
                    "player_name": display_names.get(pid, {}).get("player_name"),
                    "policy_label": display_names.get(pid, {}).get("policy_label"),
                    "wins": rec.wins,
                    "points": rec.points,
                    "imposter_wins": rec.imposter_wins,
                    "crew_wins": rec.crew_wins,
                    "imposter_episode_wins": rec.imposter_episode_wins,
                    "crew_episode_wins": rec.crew_episode_wins,
                    "episodes_counted": completed_counts[pid],
                }
            )
        filler_ids_sorted = sorted(filler_policy_ids)
        if filler_ids_sorted:
            _emit_decision_log(
                {
                    "round_id": str(round_start.round_id),
                    "round_number": round_start.round_number,
                    "division": division_label,
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
        if EXCLUDE_VOID_GAMES and void_games:
            _emit_decision_log(
                {
                    "round_id": str(round_start.round_id),
                    "round_number": round_start.round_number,
                    "division": division_label,
                    "decision": "VOID_GAMES_EXCLUDED",
                    "void_games": void_games,
                    "note": (
                        "Disconnected/void games in which every real player seat scored 0 "
                        "were EXCLUDED from both the win-rate numerator (wins) and denominator "
                        "(episodes played)."
                    ),
                }
            )
        void_note = (
            f"Excluded {void_games} void/disconnected game(s) (all player policies scored 0) "
            "from both wins and episodes played."
            if EXCLUDE_VOID_GAMES and void_games
            else None
        )
        round_notes = [f"Scored {len(games)} completed game(s) this round.", filler_note]
        if void_note:
            round_notes.append(void_note)
        leaderboards, next_state = self._competition_win_leaderboards(
            incoming_state=round_start.state,
            division_id=view.current_division.id,
            round_id=round_start.round_id,
            rankings=rankings,
            round_start=round_start,
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
                    notes=round_notes,
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
        round_start: CommissionerRoundStart | None = None,
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
        # Best-effort division label for the observability log line (matches the
        # role-parallel Competition / Imposters / Crew boards).
        division_label = "Competition"
        if round_start is not None:
            match = next(
                (d for d in round_start.divisions if d.id == division_id), None
            )
            if match is not None:
                division_label = str(getattr(match, "name", "") or "Competition")
        round_id_str = str(round_id)
        # Idempotency: a round is appended exactly once. A retried round-complete
        # must not double-count its results into the cumulative total.
        already_recorded = any(row.get("round_id") == round_id_str for row in history)
        if not already_recorded:
            recorded_at = _now_utc().isoformat()
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
                        #
                        # ``entry.score`` is the role-weighted point total; win-rate
                        # uses role-agnostic episode wins from metadata. Both are
                        # persisted so the replayed board matches ``rank_division``.
                        "episode_wins": 0.0
                        if tainted
                        else float(entry.result_metadata.get("episode_wins", entry.score)),
                        "points": 0.0
                        if tainted
                        else float(entry.result_metadata.get("points", entry.score)),
                        "imposter_episode_wins": 0
                        if tainted
                        else int(entry.result_metadata.get("imposter_episode_wins", 0)),
                        "crew_episode_wins": 0
                        if tainted
                        else int(entry.result_metadata.get("crew_episode_wins", 0)),
                        # Legacy field: episode-win count (pre-role-weighting history
                        # stored only this key).
                        "score": 0.0
                        if tainted
                        else float(entry.result_metadata.get("episode_wins", entry.score)),
                        # Episodes the player PLAYED this round — the win-rate
                        # denominator. Persisted so the replayed board matches
                        # what rank_division computes from result_metadata.
                        "episodes_played": 0
                        if tainted
                        else int(entry.result_metadata.get(COMPLETED_EPISODE_COUNT_METADATA_KEY, 0)),
                        "tainted": tainted,
                        # Wall-clock time this round was scored, so the standings
                        # recency window (see rank_division) can be applied here too
                        # and both publishing paths keep the SAME windowed board.
                        _WIN_HISTORY_RECORDED_AT_KEY: recorded_at,
                    }
                )
        state[_WIN_HISTORY_STATE_KEY] = history

        policy_to_player = _player_id_by_policy(round_start.memberships if round_start else [])
        points_map = _sync_round_points_state(
            state,
            history=history,
            rankings=rankings,
            round_id_str=round_id_str,
            recent_results=list(round_start.recent_results) if round_start else [],
            policy_to_player=policy_to_player,
            division_id=division_id,
        )

        # STANDINGS RECENCY WINDOW: consider only history rows whose round was
        # scored within the last STANDINGS_WINDOW_HOURS hours, mirroring the
        # per-round timestamp filter ``rank_division`` applies to ``completed_rounds``
        # — so the scheduling-tick board and this round-complete board stay identical
        # (no flip) while both grade on recent merit only. Rows persisted before the
        # timestamp field existed lack it and are treated as in-window (kept).
        cutoff = _standings_window_cutoff()
        windowed_history = [row for row in history if _history_row_in_window(row, cutoff)]

        # Best per-round wins/points per player, then collapse with the shared helper.
        # Every player in the windowed history is a participant (tainted rows
        # included) so nobody in-window is dropped; tainted rows don't feed metrics.
        round_wins: dict[tuple[Any, Any], float] = {}
        round_points: dict[tuple[Any, Any], float] = {}
        round_episodes: dict[tuple[Any, Any], int] = {}
        pvids_by_player: dict[Any, set] = {}
        participants: set = set()
        for row in windowed_history:
            player_id = row["player_id"]
            participants.add(player_id)
            pvids_by_player.setdefault(player_id, set()).add(UUID(row["policy_version_id"]))
            if row.get("tainted"):
                continue
            key = (player_id, row["round_id"])
            wins = _history_episode_wins(row)
            prior = round_wins.get(key)
            if prior is None or wins > prior:
                round_wins[key] = wins
                round_points[key] = _history_points(row, points_map)
                round_episodes[key] = int(row.get("episodes_played", 0))

        snapshots = self._win_total_board(
            round_wins,
            round_points,
            name_by_player={},
            pvids_by_player=pvids_by_player,
            recent=lambda _pid: None,
            round_episodes=round_episodes,
            participants=participants,
            division_label=division_label,
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
        leaderboards = [
            _win_rate_leaderboard(
                division_id=division_id,
                entries=entries,
                win_metrics=_win_metrics_by_player(round_wins, round_episodes, participants),
            )
        ]
        return leaderboards, state

    def describe_division(self, ctx: DivisionDescriptionContext) -> DivisionCommissionerDescriptionPublic:
        """Inherit the base description, but state the REAL scoring (win rate) for
        each of the three competition divisions (Both / Imposters / Crew), since the
        stock text describes a mean-score EWMA the commissioner no longer uses."""
        description = super().describe_division(ctx)
        kind = self._division_kind(ctx.division)
        if kind is not None:
            window = (
                f"the last {STANDINGS_WINDOW_HOURS:g} hours of gameplay"
                if STANDINGS_WINDOW_HOURS > 0
                else "all rounds"
            )
            if kind == "imposters":
                role_intro = (
                    "This is the IMPOSTER league: it grades every Competition policy "
                    "purely on how well it plays as an IMPOSTER. Each round seats the "
                    "real entrants on the imposter seats and fills the crew seats with "
                    "default filler policies (fillers never count)."
                )
            elif kind == "crew":
                role_intro = (
                    "This is the CREW league: it grades every Competition policy purely "
                    "on how well it plays as CREW. Each round seats the real entrants on "
                    "the crew seats and fills the imposter seats with default filler "
                    "policies (fillers never count)."
                )
            else:
                role_intro = (
                    "This is the mixed Competition league: each round runs the full "
                    "8-seat game with the game's natural random role assignment (2 "
                    "imposters / 6 crew per episode), so a policy is graded across both "
                    "roles."
                )
            description.leaderboard_rules = (
                f"{role_intro} Players are ranked by their WIN RATE over {window} — the "
                "fraction of episodes they won (0 to 1). A player's row aggregates their "
                "policy versions' won and played episodes. Only recent gameplay counts, "
                "so players are graded on their current form rather than stale results."
            )
            description.scoring_mechanics = (
                "Round scores are ROLE-WEIGHTED: 3 points per episode won as imposter, "
                "1 point per episode won as crew (each episode scores at most once; "
                "filler seats never count). Standings are RANKED by WIN RATE "
                f"= episodes won / episodes played over {window}. The displayed Score "
                "column is the cumulative sum of each player's role-weighted round "
                "points. Void/disconnected games in which every player policy scored 0 "
                "are not counted toward wins or episodes played. The Imposters and Crew "
                "leagues share the same entrant pool as Competition (a policy qualifies "
                "once and is graded in all three). The commissioner computes the ranking "
                "and the platform serves it."
            )
        # Publish the commissioner changelog on every division so operators and
        # players can see how the commissioner works and what functionality changed.
        description.changelog = list(PRIME_COMMISSIONER_CHANGELOG)
        return description

    def rank_division(self, ctx: DivisionLeaderboardContext) -> list[DivisionLeaderboardSnapshot]:
        """Competition leaderboard = all-time (or windowed) WIN RATE, collapsed to player.

        Players are RANKED by descending win rate (episodes won / episodes played;
        a won episode counts once regardless of role, fillers excluded). The
        displayed ``score`` is the cumulative sum of role-weighted round points
        (3/imposter win, 1/crew win). ``rounds_played`` is the number of counted
        completed rounds the player participated in.

        By DEFAULT every completed round counts (all-time board). An OPTIONAL recency
        window (``CREWRIFT_PRIME_STANDINGS_WINDOW_HOURS`` > 0) restricts the count to
        rounds whose gameplay finished within the last N hours, so players can be
        graded on current form. The window is OFF by default because a short window
        collapsed the board to a single round whenever the other recent rounds fell
        outside it; if enabled and it would drop every round, ``rank_division`` falls
        back to the most-recent completed round rather than an empty board.

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

        # STANDINGS RECENCY WINDOW: when enabled (STANDINGS_WINDOW_HOURS > 0), only
        # rounds whose gameplay completed within the last STANDINGS_WINDOW_HOURS hours
        # count toward the standings. DEFAULT is 0 (disabled -> all-time board);
        # filtering the completed-round id set here is the single, least-invasive seam
        # — every score below is gated on ``result.round_id in completed_ids``.
        #
        # Defense-in-depth against the single-round board: if the window would drop
        # EVERY completed round (e.g. a burst of rounds then a pause longer than the
        # window), do NOT return an empty board. An empty ``rank_division`` result
        # lets the platform's RoundComplete compat shim fabricate its own board from
        # just the latest round's results, which both flips the leaderboard and shows
        # a misleading one-round win rate. Instead fall back to the most-recent
        # completed round so the standings still reflect real gameplay.
        completed_ids = _rounds_within_standings_window(
            ctx.completed_rounds, _standings_window_cutoff()
        )
        if not completed_ids:
            completed_ids = _most_recent_completed_round_ids(ctx.completed_rounds)
        if not completed_ids:
            return []
        name_by_player: dict[Any, str | None] = {}
        # Every player that appears in a completed round's results — INCLUDING rows
        # gated out below as tainted/unranked. A player who participated but has no
        # surviving ranked round must still be shown (zero win rate), so the board
        # never silently drops an active player (the "6 active players, 5 rows" bug).
        participant_pvids: dict[Any, set] = {}
        # (player_id, round_id) -> best per-round wins and role-weighted points.
        round_wins: dict[tuple[Any, Any], float] = {}
        round_points: dict[tuple[Any, Any], float] = {}
        # Episodes the player PLAYED that round (win-rate denominator).
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
            wins = _round_episode_wins_from_result(result)
            points = _round_points_from_result(result)
            prior = round_wins.get(key)
            if prior is None or wins > prior:
                round_wins[key] = wins
                round_points[key] = points
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
            round_wins,
            round_points,
            name_by_player,
            pvids_by_player,
            recent,
            round_episodes,
            participants=set(participant_pvids),
            division_label=str(getattr(ctx.division, "name", "") or "Competition"),
        )

    def _win_total_board(
        self,
        round_wins: dict[tuple[Any, Any], float],
        round_points: dict[tuple[Any, Any], float],
        name_by_player: dict[Any, str | None],
        pvids_by_player: dict[Any, set],
        recent,
        round_episodes: dict[tuple[Any, Any], int] | None = None,
        participants: set | None = None,
        division_label: str = "Competition",
    ) -> list[DivisionLeaderboardSnapshot]:
        """Collapse per-(player, round) metrics into the all-time WIN-RATE board.

        Single source of truth for the board shape so ``rank_division`` (full
        history, with recent-round strips) and ``_complete_competition_round``
        (replayed history, ``recent`` returns None) emit IDENTICAL standings —
        which is what keeps both platform writers persisting the SAME board (no
        flip). ``round_wins`` maps (player_id, round_id) -> episodes WON;
        ``round_points`` maps the same key -> role-weighted POINTS (3/imposter,
        1/crew); ``round_episodes`` maps the same key -> episodes PLAYED.

        ``participants`` lists every player who took part in a completed round,
        including those whose only rounds were tainted/unranked. Such players are
        shown with a 0 win rate (and 0 rounds played) rather than being dropped, so
        the board never hides an active player.

        RANKING is by the player's all-time WIN RATE — total episodes won divided
        by total episodes played, always in ``[0, 1]`` — descending (highest win
        rate = rank 1), with a stable player-id tiebreak.

        The displayed ``score`` is DISPLAY-ONLY and is the player's ABSOLUTE
        CUMULATIVE SUM of per-round role-weighted POINTS across ALL completed
        rounds, FLOORED AT 0 (never negative): ``max(0.0, sum of per-round
        points)``. The win RATE orders the board; ``score`` mirrors the per-round
        Rankings point total aggregated over time. ``rounds_played`` is the number
        of rounds the player appears in.
        """
        round_episodes = round_episodes or {}
        wins_total, episodes_total, rounds_played = _aggregate_win_metrics(
            round_wins, round_episodes, participants
        )
        points_total = _aggregate_cumulative_points(round_points, participants)

        def _win_rate(player_id: Any) -> float:
            return _clamped_win_rate(
                wins_total.get(player_id, 0.0), episodes_total.get(player_id, 0)
            )

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
            # DISPLAY-ONLY score: cumulative role-weighted points, floored at 0.
            cumulative_score = max(0.0, points_total.get(player_id, 0.0))
            _emit_decision_log(
                {
                    "division": division_label,
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


def _now_utc() -> datetime:
    """Wall-clock now (UTC). Isolated so tests can reason about the window edge."""
    return datetime.now(UTC)


def _standings_window_cutoff(now: datetime | None = None) -> datetime | None:
    """The earliest gameplay time that still counts toward the Standings.

    Returns ``now - STANDINGS_WINDOW_HOURS`` (UTC), or ``None`` when the window is
    disabled (``STANDINGS_WINDOW_HOURS <= 0``) — meaning the all-time board.
    """
    if STANDINGS_WINDOW_HOURS <= 0:
        return None
    return (now or _now_utc()) - timedelta(hours=STANDINGS_WINDOW_HOURS)


def _as_utc(value: datetime | None) -> datetime | None:
    """Coerce a (possibly naive) datetime to timezone-aware UTC; pass through None."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _round_effective_time(round_row: Any) -> datetime | None:
    """The time a round's gameplay happened: completed_at, else started_at, else created_at."""
    for attr in ("completed_at", "started_at", "created_at"):
        ts = _as_utc(getattr(round_row, attr, None))
        if ts is not None:
            return ts
    return None


def _history_row_in_window(row: dict[str, Any], cutoff: datetime | None) -> bool:
    """Whether a persisted win-history row falls within the standings recency window.

    With the window disabled (``cutoff is None``) every row is kept. A row that
    predates the recorded-at field (legacy state) is KEPT rather than dropped, so
    upgrading the commissioner never silently blanks an in-flight board.
    """
    if cutoff is None:
        return True
    raw = row.get(_WIN_HISTORY_RECORDED_AT_KEY)
    if not raw:
        return True
    try:
        recorded = _as_utc(datetime.fromisoformat(str(raw)))
    except ValueError:
        return True
    return recorded is None or recorded >= cutoff


def _rounds_within_standings_window(
    completed_rounds: list[Any], cutoff: datetime | None
) -> set:
    """Ids of completed rounds whose gameplay falls within the recency window.

    With the window disabled (``cutoff is None``) every completed round is kept
    (all-time board). A round with no resolvable timestamp is KEPT (we never drop
    a real round just because its timestamps are missing).
    """
    ids = set()
    for round_row in completed_rounds:
        if cutoff is None:
            ids.add(round_row.id)
            continue
        ts = _round_effective_time(round_row)
        if ts is None or ts >= cutoff:
            ids.add(round_row.id)
    return ids


def _most_recent_completed_round_ids(completed_rounds: list[Any]) -> set:
    """Id(s) of the single most-recent completed round (by effective time).

    Fallback for ``rank_division`` when the recency window would otherwise drop
    every completed round: rather than emit an EMPTY board (which lets the platform
    fabricate a misleading one-round board that also flips against the round-complete
    writer), keep the latest real round so the standings reflect actual gameplay.
    Rounds with no resolvable timestamp sort last (kept only if nothing else has a
    timestamp), so a real timestamped round always wins.
    """
    if not completed_rounds:
        return set()
    epoch = datetime.min.replace(tzinfo=UTC)
    latest = max(
        completed_rounds, key=lambda r: _round_effective_time(r) or epoch
    )
    return {latest.id}


def _clamped_win_rate(wins: float, episodes_played: int) -> float:
    """True per-player WIN % = episodes won / episodes played, clamped to [0, 1].

    This is the PER-PLAYER rate the UI must show, NOT a normalized share of total
    wins across players. Each player's value is independent of every other
    player's, so the column does NOT sum to 1.0 (100%) across the board. Wins are
    capped at 1 per played episode upstream, so the clamp only guards against
    malformed data.
    """
    if episodes_played <= 0:
        return 0.0
    return max(0.0, min(1.0, wins / episodes_played))


def _round_points_map_key(round_id: Any, player_id: Any) -> str:
    return f"{round_id}:{player_id}"


def _points_from_ranking_entry(entry: CommissionerRankingEntry) -> float:
    return float(entry.result_metadata.get("points", entry.score))


def _player_id_by_policy(memberships: list[Any]) -> dict[str, str]:
    return {
        str(membership.policy_version_id): str(membership.player_id)
        for membership in memberships
        if getattr(membership, "player_id", None) is not None
    }


def _recompute_points_from_role_wins(row: dict[str, Any]) -> float | None:
    """Reconstruct role-weighted points when imposter/crew episode counts are stored."""
    if "imposter_episode_wins" not in row and "crew_episode_wins" not in row:
        return None
    return float(
        IMPOSTER_WIN_POINTS * int(row.get("imposter_episode_wins", 0))
        + CREW_WIN_POINTS * int(row.get("crew_episode_wins", 0))
    )


def _sync_round_points_state(
    state: dict[str, Any],
    *,
    history: list[dict[str, Any]],
    rankings: list[CommissionerRankingEntry],
    round_id_str: str,
    recent_results: list[Any],
    policy_to_player: dict[str, str],
    division_id: UUID,
) -> dict[str, float]:
    """Maintain the authoritative per-(round, player) role-weighted points map.

    Gap-era win-history rows stored only episode-win counts in ``score``; the
    platform's ``recent_results`` on ``RoundStart`` carry the role-weighted round
    scores the DB already has, so we seed/backfill from those before replaying
    history for the round-complete board.
    """
    points_map: dict[str, float] = {
        str(key): float(value)
        for key, value in dict(state.get(_WIN_ROUND_POINTS_STATE_KEY, {})).items()
    }

    def _set_points(round_id: Any, player_id: Any, points: float) -> None:
        if player_id is None:
            return
        points_map[_round_points_map_key(round_id, player_id)] = float(points)

    for row in history:
        if row.get("tainted"):
            continue
        player_id = row.get("player_id")
        round_id = row.get("round_id")
        if player_id is None or round_id is None:
            continue
        if "points" in row:
            _set_points(round_id, player_id, float(row["points"]))
            continue
        recomputed = _recompute_points_from_role_wins(row)
        if recomputed is not None:
            _set_points(round_id, player_id, recomputed)
            row["points"] = recomputed

    for recent in recent_results:
        if getattr(recent, "division_id", None) != division_id:
            continue
        player_id = policy_to_player.get(str(recent.policy_version_id))
        if player_id is None:
            continue
        _set_points(recent.round_id, player_id, float(recent.score))

    for entry in rankings:
        if entry.player_id is None:
            continue
        if int(entry.result_metadata.get(RANKED_SCORE_COUNT_METADATA_KEY, 1)) <= 0:
            continue
        points = _points_from_ranking_entry(entry)
        _set_points(round_id_str, entry.player_id, points)
        for row in history:
            if str(row.get("round_id")) != round_id_str:
                continue
            if str(row.get("player_id")) != str(entry.player_id):
                continue
            row["points"] = points
            if "imposter_episode_wins" not in row:
                row["imposter_episode_wins"] = int(
                    entry.result_metadata.get("imposter_episode_wins", 0)
                )
            if "crew_episode_wins" not in row:
                row["crew_episode_wins"] = int(entry.result_metadata.get("crew_episode_wins", 0))

    for row in history:
        if row.get("tainted"):
            continue
        player_id = row.get("player_id")
        round_id = row.get("round_id")
        if player_id is None or round_id is None or "points" in row:
            continue
        map_key = _round_points_map_key(round_id, player_id)
        if map_key in points_map:
            row["points"] = points_map[map_key]

    state[_WIN_ROUND_POINTS_STATE_KEY] = points_map
    return points_map


def _round_episode_wins_from_result(result: Any) -> float:
    """Role-agnostic episode win count from a round-result row."""
    return float(result.result_metadata.get("episode_wins", result.score))


def _round_points_from_result(result: Any) -> float:
    """Role-weighted round points from a round-result row."""
    return float(result.result_metadata.get("points", result.score))


def _history_episode_wins(row: dict[str, Any]) -> float:
    """Episode wins from a persisted win-history row (legacy rows used ``score``)."""
    return float(row.get("episode_wins", row.get("score", 0)))


def _history_points(row: dict[str, Any], points_map: dict[str, float] | None = None) -> float:
    """Role-weighted points from a persisted win-history row."""
    if "points" in row:
        return float(row["points"])
    points_map = points_map or {}
    map_key = _round_points_map_key(row.get("round_id"), row.get("player_id"))
    if map_key in points_map:
        return float(points_map[map_key])
    recomputed = _recompute_points_from_role_wins(row)
    if recomputed is not None:
        return recomputed
    # Pre-role-weighted legacy: only ``score`` was stored and wins == points.
    if "episode_wins" not in row:
        return float(row.get("score", 0))
    # Role-weighted gap row (``score`` holds wins, not points) with no map entry.
    return 0.0


def _aggregate_win_metrics(
    round_wins: dict[tuple[Any, Any], float],
    round_episodes: dict[tuple[Any, Any], int],
    participants: set | None,
) -> tuple[dict[Any, float], dict[Any, int], dict[Any, int]]:
    """Collapse per-(player, round) wins/played into all-time per-player totals.

    Returns ``(wins_total, episodes_total, rounds_played)`` keyed by player id.
    Every known participant is seeded at zero so a player who took part but earned
    no ranked round still appears (win rate 0, 0 rounds played).
    """
    wins_total: dict[Any, float] = {}
    episodes_total: dict[Any, int] = {}
    rounds_played: dict[Any, int] = {}
    for player_id in participants or set():
        wins_total.setdefault(player_id, 0.0)
        episodes_total.setdefault(player_id, 0)
        rounds_played.setdefault(player_id, 0)
    for (player_id, round_id), wins in round_wins.items():
        wins_total[player_id] = wins_total.get(player_id, 0.0) + wins
        episodes_total[player_id] = episodes_total.get(player_id, 0) + int(
            round_episodes.get((player_id, round_id), 0)
        )
        rounds_played[player_id] = rounds_played.get(player_id, 0) + 1
    return wins_total, episodes_total, rounds_played


def _aggregate_cumulative_points(
    round_points: dict[tuple[Any, Any], float],
    participants: set | None,
) -> dict[Any, float]:
    """Sum per-(player, round) role-weighted points into all-time per-player totals."""
    points_total: dict[Any, float] = {}
    for player_id in participants or set():
        points_total.setdefault(player_id, 0.0)
    for (player_id, _round_id), points in round_points.items():
        points_total[player_id] = points_total.get(player_id, 0.0) + points
    return points_total


def _win_metrics_by_player(
    round_wins: dict[tuple[Any, Any], float],
    round_episodes: dict[tuple[Any, Any], int],
    participants: set | None,
) -> dict[str, tuple[float, int, float]]:
    """``str(player_id) -> (wins, episodes_played, win_rate)`` for the published board.

    Uses the SAME aggregation as ``_win_total_board`` so the WIN %/wins/played the
    UI renders are exactly the values the board is ranked by.
    """
    wins_total, episodes_total, _ = _aggregate_win_metrics(
        round_wins, round_episodes, participants
    )
    return {
        str(player_id): (
            wins_total.get(player_id, 0.0),
            episodes_total.get(player_id, 0),
            _clamped_win_rate(wins_total.get(player_id, 0.0), episodes_total.get(player_id, 0)),
        )
        for player_id in wins_total
    }


def _win_rate_leaderboard(
    *,
    division_id: UUID,
    entries: list[CommissionerDivisionLeaderboardEntry],
    win_metrics: dict[str, tuple[float, int, float]] | None = None,
) -> CommissionerDivisionLeaderboard:
    """Build the published Competition board.

    Mirrors the vendored ``division_leaderboard_from_entries`` shape (so the
    platform persists it verbatim instead of re-synthesizing it). The board is
    RANKED by all-time win rate (see ``_win_total_board``), but the ``score``
    column carries the DISPLAY-ONLY absolute cumulative sum of each player's
    per-round role-weighted points (floored at 0), not the win rate — so the
    column is labeled "Score" accordingly.

    ``win_metrics`` maps ``player_id -> (wins, episodes_played, win_rate)`` and is
    surfaced as explicit ``win_rate``/``wins``/``episodes_played`` columns so the
    Observatory UI can render the TRUE PER-PLAYER WIN % (``episodes_won /
    episodes_played``, clamped to ``[0, 1]``) directly from the payload. The UI
    MUST NOT derive WIN % from ``score`` (e.g. ``score / sum(score)`` across
    players) — that produces a normalized SHARE that sums to 100% and is wrong.
    Each player's ``win_rate`` here is independent and the column does NOT sum to
    1.0 across the board.
    """
    win_metrics = win_metrics or {}

    def _metrics(player_id: str) -> tuple[float, int, float]:
        return win_metrics.get(player_id, (0.0, 0, 0.0))

    view = CommissionerDivisionLeaderboardView(
        key="score",
        title="Score",
        # Standings headline is Win % (the board is RANKED by win rate). The platform renders the
        # `primary_column` value under its label + value_type, so a `percent` win_rate column shows
        # e.g. "20%" instead of the raw cumulative `score`.
        primary_column="win_rate",
        axis_values={"metric": "score", "timeframe": "legacy"},
        columns=[
            CommissionerDivisionLeaderboardColumn(key="rank", label="Rank", value_type="integer", sort="asc"),
            # True per-player WIN % = episodes_won / episodes_played, clamped to
            # [0, 1]. NOT a share of total wins (which would sum to 100%); the UI
            # renders this column verbatim as the WIN % rate. `percent` tells the UI
            # to format the [0, 1] fraction as a whole-number percentage (0.2 -> "20%").
            CommissionerDivisionLeaderboardColumn(
                key="win_rate", label="Win %", value_type="percent", sort="desc"
            ),
            CommissionerDivisionLeaderboardColumn(key="score", label="Score", value_type="number", sort="desc"),
            CommissionerDivisionLeaderboardColumn(key="wins", label="Wins", value_type="number"),
            CommissionerDivisionLeaderboardColumn(
                key="episodes_played", label="Episodes Played", value_type="integer"
            ),
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
                    # True per-player WIN % (PR side): episodes_won / episodes_played,
                    # clamped to [0, 1]; the Observatory UI renders this verbatim.
                    "win_rate": _metrics(entry.player_id)[2],
                    "score": entry.score,
                    "wins": _metrics(entry.player_id)[0],
                    "rounds_played": entry.rounds_played,
                    # All-time episode totals (master side). ``episode_wins`` /
                    # ``episodes_played`` are the Competition Win % numerator /
                    # denominator carried verbatim into division.leaderboard_config
                    # so the backend can compute an all-rounds (not last-20) rate.
                    # ``episode_wins`` equals ``wins`` (both are wins_total) and
                    # ``episodes_played`` matches ``_metrics(...)[1]``; populated
                    # once from the entry so both code paths stay in lockstep.
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


def _enforce_one_policy_per_player(
    ctx: LeagueMigrationContext,
    events: list[ModelsPolicyMembershipEventChange],
    competition_division_id: UUID | None,
) -> list[ModelsPolicyMembershipEventChange]:
    """Tournament rule: a player fields at most ONE active Competition policy.

    Pure function over the migration snapshot + the events drafted so far.
    Projects which memberships would be actively ``competing`` in the Competition
    division AFTER ``events`` apply, groups them by ``player_id``, and for any
    player holding more than one seat APPENDS a retire event (status
    ``disqualified``, substatus ``superseded``, ``end_time=now``) for every
    membership except the one being kept. Appending (never rewriting) keeps the
    qualification events' audit trail intact — a policy that passed the gate
    still shows its promotion, followed by its supersession.

    Which one is kept: a membership promoted IN THIS migration outranks any
    pre-existing membership (the newer submission replaces the old); among
    equals, the one latest in ``ctx.memberships`` wins (the platform lists
    memberships in creation order, so this is "keep the newest"). Memberships
    with no ``player_id`` are left alone — the rule attributes seats to players,
    and an unattributed seat cannot be safely retired.
    """
    if competition_division_id is None:
        return events

    # Index the drafted events by membership id (last event wins, matching how
    # the platform applies them sequentially).
    drafted: dict[UUID, ModelsPolicyMembershipEventChange] = {}
    for event in events:
        drafted[event.league_policy_membership_id] = event

    promoted_ids: set[UUID] = set()
    active: list[MembershipSnapshot] = []
    for membership in ctx.memberships:
        event = drafted.get(membership.id)
        if event is not None:
            # Project the membership's post-event state from the drafted event.
            if (
                event.to_division_id == competition_division_id
                and _status_str(event.status) == "competing"
            ):
                was_already_competing = (
                    membership.division_id == competition_division_id
                    and _status_str(membership.status) == "competing"
                )
                if not was_already_competing:
                    promoted_ids.add(membership.id)
                active.append(membership)
            continue
        if (
            membership.division_id == competition_division_id
            and _status_str(membership.status) == "competing"
        ):
            active.append(membership)

    by_player: dict[str, list[MembershipSnapshot]] = {}
    for membership in active:
        player_id = membership.player_id
        if player_id is None:
            continue
        by_player.setdefault(str(player_id), []).append(membership)

    out = list(events)
    order = {m.id: i for i, m in enumerate(ctx.memberships)}
    for player_id, seats in by_player.items():
        if len(seats) <= 1:
            continue
        # Newest submission wins: freshly promoted first, then latest snapshot order.
        keeper = max(seats, key=lambda m: (m.id in promoted_ids, order.get(m.id, -1)))
        for membership in seats:
            if membership.id == keeper.id:
                continue
            _emit_decision_log(
                {
                    "decision": "ONE_POLICY_PER_PLAYER_SUPERSEDE",
                    "player_id": player_id,
                    "superseded_policy_version_id": str(membership.policy_version_id),
                    "kept_policy_version_id": str(keeper.policy_version_id),
                    "reason": "player may field only one Competition policy at a time",
                }
            )
            out.append(
                ModelsPolicyMembershipEventChange(
                    league_policy_membership_id=membership.id,
                    from_division_id=competition_division_id,
                    to_division_id=None,
                    status="disqualified",
                    substatus=_SUPERSEDED_SUBSTATUS,
                    reason="Superseded by the player's newer policy (one policy per player)",
                    end_time=_now_utc(),
                    notes=(
                        "Tournament rule: a player may field at most one active policy in "
                        f"the Competition division. Policy {membership.policy_version_id} was "
                        f"retired in favor of {keeper.policy_version_id} (same player "
                        f"{player_id}). This is NOT a skill disqualification."
                    ),
                    evidence=[
                        ModelsPolicyMembershipEventEvidence(
                            type=_ONE_POLICY_EVIDENCE_TYPE,
                            title="One policy per player (Competition)",
                            summary=(
                                "Retired: the same player promoted a newer policy into the "
                                "Competition division."
                            ),
                            metadata={
                                "player_id": player_id,
                                "superseded_policy_version_id": str(membership.policy_version_id),
                                "kept_policy_version_id": str(keeper.policy_version_id),
                                "kept_league_policy_membership_id": str(keeper.id),
                            },
                        )
                    ],
                )
            )
    return out


def _distinct_topup(
    pool: list[UUID],
    *,
    already_seated: list[UUID],
    count: int,
    rotation: int,
    key: Callable[[UUID], str] | None = None,
) -> list[UUID]:
    """Pick ``count`` top-up policies from ``pool``, avoiding duplicates in a seat.

    Returns policies drawn from ``pool`` for the empty seats, preferring policies
    whose dedup identity is NOT already present in ``already_seated`` (nor already
    chosen for this top-up) so no identity occupies two seats in the same episode.
    ``key`` maps a policy to its dedup identity — by default its own id, but
    scheduling passes a PLAYER-aware key so two policies from the same player (and
    a player already holding a real seat) count as the same identity and can't
    share a game. Self-collusion (one player controlling multiple seats) is thus
    impossible whenever the pool holds enough distinct identities.

    When the pool is exhausted of distinct identities (e.g. more seats than
    distinct players and no configured fillers), the closed roster still has to
    dispatch, so remaining seats are filled by EXACTLY DUPLICATING already-seated
    policies (cycling for balance) rather than introducing a seated player's OTHER
    policy version — so a player is never represented by two DIFFERENT policies in
    one game, and any unavoidable duplicate seat is a pure copy that scoring
    excludes. ``rotation`` shifts the starting point per episode for balance.
    """
    if not pool:
        return []
    identity = key if key is not None else (lambda policy: f"policy:{policy}")
    seated_keys = {identity(policy) for policy in already_seated}
    chosen: list[UUID] = []
    size = len(pool)
    reuse_index = 0
    for index in range(count):
        # Prefer a policy whose identity is not already present in this episode.
        picked: UUID | None = None
        for step in range(size):
            candidate = pool[(rotation + index + step) % size]
            candidate_key = identity(candidate)
            if candidate_key not in seated_keys:
                picked = candidate
                seated_keys.add(candidate_key)
                break
        if picked is None:
            # Pool exhausted of distinct identities: the roster must still fill, so
            # duplicate an ALREADY-PLACED policy exactly (never a seated player's
            # OTHER version), cycling for balance. Prefer reusing top-up policies
            # already chosen for this episode (e.g. keep cycling the filler bots);
            # otherwise duplicate an already-seated real policy. Either way the
            # duplicate seats are tagged filler and excluded from scoring.
            reuse_pool = chosen or already_seated or pool
            picked = reuse_pool[(rotation + reuse_index) % len(reuse_pool)]
            reuse_index += 1
        chosen.append(picked)
    return chosen


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
