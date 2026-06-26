"""Experience-request (xp request) client for the Crewrift Prime commissioner.

This module owns the network I/O for the event-driven qualification flow. When a
new policy is submitted to the league, the commissioner uses this client to:

  1. CREATE an experience request for the submitted policy
     (``POST /v2/experience-requests``),
  2. POLL it until its child episodes complete (or it fails / times out),
  3. FETCH the child episode rows (``GET .../{xreq}/episodes``), and
  4. DOWNLOAD + zlib-decompress each episode's ``.bitreplay``.

It is stdlib-only (``urllib``) on purpose — the commissioner image installs only
the vendored ``commissioners`` package (fastapi/pydantic/uvicorn/pyyaml) and we do
NOT want to add a heavy HTTP dependency just for a handful of GET/POST calls. The
auth + endpoint shapes are ported from the verified reference client
``players/crewbot3000/scripts/diagnose_experience_request.py`` (do not import from
there; it lives outside the image build context).

Failure semantics mirror the commissioner's existing hold-vs-DQ classification:
any infrastructure / dispatch / control-plane failure (HTTP 4xx/5xx on create,
the run never completing within the poll budget, or no completed episode at all)
is surfaced as an :class:`XpRequestInfraError` so the caller HOLDS the entrant for
retry rather than disqualifying it. Only a genuinely completed run with results
feeds the decision gate.

NO game-rule or decision logic lives here (that stays in ``decision.py``); this is
pure transport.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Default Softmax API base; the Observatory data API hangs under ``<base>/observatory``.
DEFAULT_API_BASE = "https://softmax.com/api"
# A normal UA — Cloudflare in front of softmax.com rejects the default python-urllib
# User-Agent with a 1010 "browser signature" 403.
_USER_AGENT = "crewrift-prime-commissioner/1.0 (+https://softmax.com)"

# Crewrift is a closed-roster 8-seat game; a self-play qualifier fills every seat
# with the candidate policy. The platform's create endpoint asserts
# ``len(roster) == player_count`` and derives ``player_count`` from the roster
# length, so the roster MUST carry exactly this many participants.
DEFAULT_SEAT_COUNT = 8

# Run statuses that mean "stop polling": a terminal outcome.
_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "canceled", "error"})
# Episode statuses that count as "settled" (no longer in flight).
_COMPLETED_EPISODE_STATUSES = frozenset({"completed", "complete", "succeeded", "success"})
_FAILED_EPISODE_STATUSES = frozenset({"failed", "error", "cancelled", "canceled", "timeout", "timed_out"})


class XpRequestError(Exception):
    """Base class for experience-request client failures."""


class XpRequestInfraError(XpRequestError):
    """An infrastructure / dispatch / control-plane failure (HOLD, never DQ).

    Raised for HTTP 4xx/5xx on create, auth failures, the run never reaching a
    terminal state within the poll budget, or a terminal run that produced no
    completed episode. The commissioner classifies these as infra holds (retry
    next round) rather than disqualifying the policy.
    """


class XpRequestAuthError(XpRequestInfraError):
    """No usable Softmax API token (a kind of infra failure: we can't dispatch)."""


# --------------------------------------------------------------------------- auth


def _token_file() -> Path:
    return Path(os.environ.get("SOFTMAX_COGAMES_TOKEN_FILE", Path.home() / ".metta" / "cogames.yaml"))


def _parse_cogames_yaml(text: str) -> tuple[str | None, str | None]:
    """Return (base_url, token) from the simple two-level cogames.yaml.

    Shape::

        user_tokens:
          <api-base-url>: <token>
        login_tokens:
          <api-base-url>: <token>

    Prefer ``user_tokens``. The URL key contains a colon, so we split on the
    indented ``key: value`` boundary (mirrors the reference parser).
    """
    sections: dict[str, list[tuple[str, str]]] = {}
    current: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        section = re.match(r"^([A-Za-z0-9_]+):\s*$", raw)
        if section:
            current = section.group(1)
            sections[current] = []
            continue
        entry = re.match(r"^\s+(\S.*?):\s+(\S.+?)\s*$", raw)
        if entry and current:
            sections[current].append((entry.group(1).strip(), entry.group(2).strip()))
    preferred = (sections.get("user_tokens") or sections.get("login_tokens") or [None])[0]
    if not preferred:
        return None, None
    return preferred[0], preferred[1]


def _observatory_base(api_server: str, server_override: str | None) -> str:
    """Map a login host to the Observatory base, honoring an explicit override."""
    if server_override:
        return server_override.rstrip("/")
    trimmed = (api_server or DEFAULT_API_BASE).rstrip("/")
    if trimmed.endswith("/observatory"):
        return trimmed
    return trimmed + "/observatory"


def resolve_auth(server: str | None = None) -> tuple[str, str]:
    """Resolve (observatory_base, token); raise :class:`XpRequestAuthError` if absent.

    Source order: env ``SOFTMAX_API_TOKEN`` -> ``softmax.auth`` (best-effort) ->
    ``~/.metta/cogames.yaml``.
    """
    env_token = os.environ.get("SOFTMAX_API_TOKEN", "").strip()
    if env_token:
        base = (server or os.environ.get("OBSERVATORY_API_URL") or DEFAULT_API_BASE).rstrip("/")
        return _observatory_base(base, server), env_token

    try:  # softmax.auth, when the package happens to be importable in the image.
        import softmax.auth as auth  # type: ignore

        api_server = auth.get_api_server()
        token = auth.load_current_cogames_token(api_server=api_server)
        if token:
            return _observatory_base(api_server, server), token
    except Exception:  # noqa: BLE001 - package usually absent; fall through.
        pass

    path = _token_file()
    if path.exists():
        try:
            base_url, token = _parse_cogames_yaml(path.read_text())
        except OSError:
            base_url, token = None, None
        if token:
            return _observatory_base(base_url or DEFAULT_API_BASE, server), token
    raise XpRequestAuthError(
        "No Softmax API token found (set SOFTMAX_API_TOKEN or run `softmax login`)."
    )


# ------------------------------------------------------------------------- shapes


@dataclass
class EpisodeRow:
    """One child episode of an experience request (the fields we read)."""

    id: str
    status: str | None
    episode_id: str | None
    replay_url: str | None
    participants: list[dict[str, Any]] = field(default_factory=list)
    scores: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_completed(self) -> bool:
        return (self.status or "").lower() in _COMPLETED_EPISODE_STATUSES

    @property
    def is_failed(self) -> bool:
        return (self.status or "").lower() in _FAILED_EPISODE_STATUSES

    @classmethod
    def from_json(cls, row: dict[str, Any]) -> "EpisodeRow":
        return cls(
            id=str(row.get("id") or ""),
            status=row.get("status"),
            episode_id=row.get("episode_id"),
            replay_url=row.get("replay_url"),
            participants=row.get("participants") or [],
            scores=row.get("scores") or [],
            raw=row,
        )


@dataclass
class XpRequestRun:
    """The created experience request plus its (eventually) completed episodes."""

    xreq_id: str
    status: str | None
    episodes: list[EpisodeRow] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def completed_episodes(self) -> list[EpisodeRow]:
        return [episode for episode in self.episodes if episode.is_completed]


# ------------------------------------------------------------------------- client


class XpRequestClient:
    """Thin authenticated stdlib-urllib client over the Observatory xp-request API.

    Best-effort and defensive: every transport failure that means "the policy
    never got a fair chance to run" is normalized to :class:`XpRequestInfraError`
    so the commissioner holds-for-retry rather than disqualifying.
    """

    def __init__(
        self,
        base: str | None = None,
        token: str | None = None,
        *,
        timeout: float = 90.0,
        server: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if base is None or token is None:
            resolved_base, resolved_token = resolve_auth(server)
            base = base or resolved_base
            token = token or resolved_token
        self._base = base.rstrip("/")
        self._headers = {
            "X-Auth-Token": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }
        self._timeout = timeout
        self._sleep = sleep

    # -- low-level HTTP -----------------------------------------------------

    def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> Any:
        url = self._base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # 4xx/5xx -> infra/dispatch failure.
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise XpRequestInfraError(
                f"{method} {path} -> HTTP {exc.code} (request/dispatch failure): {detail}"
            ) from exc
        except urllib.error.URLError as exc:  # network unreachable / DNS / timeout.
            raise XpRequestInfraError(f"{method} {path} -> request failed: {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise XpRequestInfraError(f"{method} {path} -> non-JSON response") from exc

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("POST", path, body=body)

    # -- experience-request operations -------------------------------------

    def create_experience_request(
        self,
        *,
        division_id: str,
        policy_version_id: str,
        seat_count: int = DEFAULT_SEAT_COUNT,
        num_episodes: int = 1,
        notes: str | None = None,
        execution_backend: str = "k8s",
    ) -> str:
        """Create a self-play qualifier xp request; return its ``xreq_...`` id.

        The candidate ``policy_version_id`` fills every seat of the closed-roster
        crewrift game. The platform's ``POST /v2/experience-requests`` endpoint
        validates against ``V2CreateExperienceRequestRequest`` and derives the
        game's ``player_count`` from ``len(roster)`` (it asserts they match), so we
        emit exactly ``seat_count`` roster participants. Each participant pins the
        candidate via ``player.policy_ref`` (a ``name:vN`` label OR a raw
        ``policy_version`` UUID). ``slot=-1`` (the schema default) round-robins
        through the open seats, which for a full self-play roster simply assigns one
        participant per seat.
        """
        roster = [
            {"player": {"policy_ref": policy_version_id}, "slot": -1}
            for _ in range(seat_count)
        ]
        payload: dict[str, Any] = {
            "target": {"division_id": division_id},
            "roster": roster,
            "num_episodes": num_episodes,
            "notes": notes or "crewrift-prime qualifier",
            "execution_backend": execution_backend,
        }
        created = self._post("/v2/experience-requests", payload)
        if not isinstance(created, dict) or not created.get("id"):
            raise XpRequestInfraError("create experience request: missing id in response")
        return str(created["id"])

    def get_filler_policy_versions(self, league_id: str) -> list[str]:
        """Fetch a league's configured default filler policy_version_id list.

        Calls ``GET /v2/leagues/{league_id}/filler-policies`` (the league-config
        API the platform serves) and returns the ordered ``policy_version_id``
        UUID strings from its ``filler_policy_versions`` payload. Reuses this
        client's authenticated transport, so any HTTP/auth/network failure is
        normalized to :class:`XpRequestInfraError` (the caller falls back rather
        than crashing a round). An absent/empty list returns ``[]``.
        """
        path = f"/v2/leagues/{urllib.parse.quote(str(league_id))}/filler-policies"
        payload = self._get(path)
        if not isinstance(payload, dict):
            raise XpRequestInfraError(f"filler policies for {league_id}: unexpected shape {type(payload)}")
        rows = payload.get("filler_policy_versions") or []
        if not isinstance(rows, list):
            raise XpRequestInfraError(f"filler policies for {league_id}: filler_policy_versions not a list")
        ids: list[str] = []
        for row in rows:
            if isinstance(row, dict) and row.get("policy_version_id"):
                ids.append(str(row["policy_version_id"]))
        return ids

    def get_run_detail(self, xreq_id: str) -> dict[str, Any]:
        detail = self._get(f"/v2/experience-requests/{xreq_id}")
        if not isinstance(detail, dict):
            raise XpRequestInfraError(f"run detail for {xreq_id}: unexpected shape")
        return detail

    def get_episodes(self, xreq_id: str) -> list[EpisodeRow]:
        rows = self._get(f"/v2/experience-requests/{xreq_id}/episodes")
        if not isinstance(rows, list):
            raise XpRequestInfraError(f"episodes for {xreq_id}: unexpected shape {type(rows)}")
        return [EpisodeRow.from_json(row) for row in rows if isinstance(row, dict)]

    def poll_until_complete(
        self,
        xreq_id: str,
        *,
        poll_interval_seconds: float = 10.0,
        max_wait_seconds: float = 1800.0,
    ) -> XpRequestRun:
        """Poll a run until it reaches a terminal status or the budget expires.

        Returns an :class:`XpRequestRun` with the latest episode rows. Raises
        :class:`XpRequestInfraError` if the run never reaches a terminal status
        within ``max_wait_seconds`` (a non-completion infra hold).
        """
        deadline = time.monotonic() + max_wait_seconds
        last_status: str | None = None
        while True:
            detail = self.get_run_detail(xreq_id)
            last_status = str(detail.get("status") or "").lower() or last_status
            if last_status in _TERMINAL_RUN_STATUSES:
                episodes = self.get_episodes(xreq_id)
                return XpRequestRun(xreq_id=xreq_id, status=last_status, episodes=episodes, detail=detail)
            # Some backends omit a run-level status; fall back to episode settledness.
            episodes = self.get_episodes(xreq_id)
            if episodes and all(ep.is_completed or ep.is_failed for ep in episodes):
                return XpRequestRun(xreq_id=xreq_id, status=last_status or "completed", episodes=episodes, detail=detail)
            if time.monotonic() >= deadline:
                raise XpRequestInfraError(
                    f"experience request {xreq_id} did not complete within "
                    f"{max_wait_seconds:g}s (last status: {last_status or 'unknown'})"
                )
            self._sleep(poll_interval_seconds)

    def run_qualifier(
        self,
        *,
        division_id: str,
        policy_version_id: str,
        seat_count: int = DEFAULT_SEAT_COUNT,
        num_episodes: int = 1,
        notes: str | None = None,
        execution_backend: str = "k8s",
        poll_interval_seconds: float = 10.0,
        max_wait_seconds: float = 1800.0,
    ) -> XpRequestRun:
        """Create + poll a single self-play qualifier xp request, end to end."""
        xreq_id = self.create_experience_request(
            division_id=division_id,
            policy_version_id=policy_version_id,
            seat_count=seat_count,
            num_episodes=num_episodes,
            notes=notes,
            execution_backend=execution_backend,
        )
        return self.poll_until_complete(
            xreq_id,
            poll_interval_seconds=poll_interval_seconds,
            max_wait_seconds=max_wait_seconds,
        )

    def download_replay(self, replay_url: str, *, timeout: float | None = None) -> bytes:
        """Download a ``.bitreplay`` and zlib-decompress it (raw bytes on failure).

        The hosted replay payload is zlib-compressed (``*.json.z`` / ``*.bitreplay``
        served compressed). We decompress when possible and return the raw bytes
        otherwise so the caller (the parser) can still attempt to read them.
        """
        if not replay_url:
            raise XpRequestInfraError("download replay: empty replay_url")
        req = urllib.request.Request(replay_url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self._timeout) as response:
                raw = response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise XpRequestInfraError(f"download replay {replay_url}: {exc}") from exc
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return raw
