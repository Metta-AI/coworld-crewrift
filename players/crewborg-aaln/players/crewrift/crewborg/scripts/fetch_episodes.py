#!/usr/bin/env python3
"""Download full episode data for the N most recent episodes crewborg played.

Crewborg runs in the hosted Crewrift league; the Observatory data API records
every episode it took part in. This script discovers those episodes, downloads
everything we can actually get for each one, and writes it to disk so you can
replay, diff, or post-mortem an episode offline.

What "full episode data" means here (each verified against the live API on
2026-06-01):

  * episode.json          -- the `/episodes/{id}` record: replay_url, tags
                             (coworld_id / job_id / pool_id), game_stats, steps.
  * episode_request.json  -- the matching `/v2/experience-request-episodes` row
                             for this episode: every participant (name + slot +
                             policy version), status, scores, game_config.
  * replay.json[.z]       -- the complete replay (the whole game). This is
                             Crewrift's binary `.bitreplay` (per-tick input
                             masks, magic "CREWRIFT..."), NOT JSON, but it is
                             named `replay.json` to match what `coworld
                             run-episode` writes and what the documented
                             `COGAME_LOAD_REPLAY_URI` viewer recipe loads (see
                             docs/crewrift-replays.md). The API serves it
                             zlib-compressed; we keep that raw blob as
                             `replay.json.z` and write the decompressed bytes
                             (the directly-loadable form) as `replay.json`.
  * logs/crewborg_slot{N}_v{V}.log -- crewborg's own per-tick stderr trace for
                             each slot it controlled (the richest behavioural
                             record: perception / belief / mode decisions).

Why a standalone script and not the `coworld` CLI: `coworld` *does* have official
equivalents (`coworld episodes`, `coworld replays --download-dir`, `coworld
episode-logs --download-dir`) -- use them for ad-hoc inspection. This script
exists because it (1) filters straight to crewborg across all its policy
versions, (2) bundles replay + per-slot traces + metadata into one directory per
episode in a single pass, and (3) reads raw JSON against the routes directly, so
it survives coworld client/server drift. That drift is real and recurring: the
client ships behind the server. As of 2026-06-02 the published `coworld` CLI
(0.1.22 as of 2026-06-11; earlier 0.1.13 was broken) against the live server because the server renamed
its episode-request API -- `/v2/episode-requests*` -> `/v2/experience-request*`
(see endpoint map below) -- while the CLI still calls the old paths and 404s. So
`coworld episodes`, `coworld replays`, and `coworld episode-logs` do not work
today; this script (which calls the new routes) is the reliable path. (An earlier
instance of the same drift: coworld 0.1.11's `V2EpisodeRequestRow` required an
`assignments` field the server had dropped, crashing the typed CLI with a
`ValidationError`.) The per-ereq `artifacts/{type}` endpoint is a dead end
(rejects results/replay/config with "Unknown artifact type"); the replay comes
from the episode's `replay_url` and the traces from the per-agent `policy-logs`
endpoint -- both confirmed working. See the module-level NOTES for the endpoint map.

Usage (from anywhere; auth comes from `softmax login`):

    uv run python players/crewrift/crewborg/scripts/fetch_episodes.py -n 10
    uv run python .../fetch_episodes.py -n 5 --version 2 --out /tmp/crewborg_eps
    uv run python .../fetch_episodes.py -n 20 --no-logs   # replays + metadata only

The run is idempotent: an episode whose directory already looks complete is
skipped unless --force is given.

NOTES -- live endpoint map (verified 2026-06-02). Base defaults to the official
gateway `<softmax.auth.get_api_server()>/observatory` (today:
https://softmax.com/api/observatory). The full route map is published at
`<base>/openapi.json` -- read it when a route 404s, the server moves faster than
the client. The Observatory service is also reachable directly at
https://api.observatory.softmax-research.net, where the SAME routes sit at the
host *root* (no `/observatory` segment, e.g. `<host>/v2/experience-request-episodes`);
pass --server to switch. The paths below are written for the gateway base.
  GET /stats/policy-versions?name_exact=crewborg              -> all crewborg policy versions
  GET /episodes?policy_version_id=<pv>&limit&offset           -> episodes that pv played
  GET /episodes/{episode_id}                                  -> single episode detail
  GET /v2/experience-request-episodes?pool_id=pool_<uuid>&limit
                                                             -> ereq-episode rows for a pool
                                                                (maps episode_id -> ereq id +
                                                                participants + scores + status)
  GET /v2/experience-request-episodes/{ereq}/{pv}/policy-logs/{slot}
                                                             -> a participant's stderr trace
  <episode.replay_url>                                        -> zlib-compressed binary .bitreplay
NB the `pool_id` query value needs the `pool_` prefix (a bare uuid 422s). These
routes were renamed from `/v2/episode-requests*` around 2026-06; older notes /
the coworld CLI still reference the old names.
"""

from __future__ import annotations

import argparse
import json
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

POLICY_NAME = "crewborg"


def log(msg: str) -> None:
    """Progress to stderr so stdout stays clean for any future piping."""
    print(msg, file=sys.stderr, flush=True)


def _softmax_auth():
    try:
        import softmax.auth as auth
    except ImportError as exc:  # pragma: no cover - environment guard
        sys.exit(f"Could not import softmax.auth ({exc}). Run inside `uv run`.")
    return auth


def default_server() -> str:
    """The official Observatory gateway, tracking wherever the user is logged in.

    `get_api_server()` is the login host the `coworld` CLI talks to (today
    https://softmax.com/api); the Observatory API lives under its `/observatory`
    path. Deriving it here means this script follows the user's `softmax login`
    rather than hard-coding a host.
    """
    return _softmax_auth().get_api_server().rstrip("/") + "/observatory"


def load_token() -> str:
    """Return the current softmax auth token, or exit with a clear message."""
    auth = _softmax_auth()
    token = auth.load_current_cogames_token(api_server=auth.get_api_server())
    if not token:
        sys.exit("Not authenticated. Run: uv run softmax login")
    return token


@dataclass
class EpisodeRecord:
    """One crewborg episode plus the handles needed to fetch its data."""

    episode_id: str
    created_at: str
    policy_version_id: str
    version: int | None
    replay_url: str | None
    job_id: str | None
    pool_id: str | None          # bare uuid as it appears in episode tags
    coworld_id: str | None
    detail: dict[str, Any] = field(default_factory=dict)


class Client:
    """Thin authenticated httpx wrapper over the Observatory data API."""

    def __init__(self, server: str, token: str) -> None:
        self._http = httpx.Client(
            base_url=server.rstrip("/"),
            headers={"X-Auth-Token": token},
            timeout=60.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(self, path: str, **params: Any) -> Any:
        r = self._http.get(path, params=params or None)
        r.raise_for_status()
        return r.json()

    def get_bytes(self, path: str) -> bytes:
        r = self._http.get(path)
        r.raise_for_status()
        return r.content

    def get_text_or_none(self, path: str) -> str | None:
        """GET text; return None (not raise) on a 4xx so one missing log
        doesn't abort the episode."""
        r = self._http.get(path)
        if r.status_code >= 400:
            return None
        return r.text


def discover_policy_versions(client: Client, version: int | None) -> list[dict[str, Any]]:
    """All crewborg policy versions (id + version), newest first.

    Pass `version` to keep only that one. Auto-discovers future versions, so a
    newly uploaded crewborg:v3 needs no code change.
    """
    rows = client.get_json("/stats/policy-versions", name_exact=POLICY_NAME, limit=100)
    if isinstance(rows, dict):  # tolerate {"entries": [...]} shape
        rows = rows.get("entries", [])
    pvs = [{"id": r["id"], "version": r.get("version")} for r in rows]
    if version is not None:
        pvs = [pv for pv in pvs if pv["version"] == version]
        if not pvs:
            sys.exit(f"No crewborg policy version {version} found (have: "
                     f"{sorted(r.get('version') for r in rows)}).")
    pvs.sort(key=lambda pv: (pv["version"] or -1), reverse=True)
    return pvs


def discover_episodes(client: Client, pvs: list[dict[str, Any]], want: int) -> list[EpisodeRecord]:
    """Most-recent `want` episodes across the given policy versions, deduped.

    Paginates `/episodes` per version until it has enough or the source is
    exhausted, then merges, dedupes by episode id, and trims to `want`.
    """
    by_id: dict[str, EpisodeRecord] = {}
    for pv in pvs:
        offset, page = 0, 100
        # Grab a generous window per version; episodes interleave across
        # versions in time, so we fetch >= want from each then merge-sort.
        while offset < max(want * 2, want + 20):
            rows = client.get_json(
                "/episodes", policy_version_id=pv["id"], limit=page, offset=offset
            )
            if not isinstance(rows, list) or not rows:
                break
            for ep in rows:
                eid = ep["id"]
                if eid in by_id:
                    continue
                tags = ep.get("tags") or {}
                by_id[eid] = EpisodeRecord(
                    episode_id=eid,
                    created_at=ep.get("created_at") or "",
                    policy_version_id=pv["id"],
                    version=pv["version"],
                    replay_url=ep.get("replay_url"),
                    job_id=tags.get("job_id"),
                    pool_id=tags.get("pool_id"),
                    coworld_id=tags.get("coworld_id"),
                    detail=ep,
                )
            if len(rows) < page:
                break
            offset += page
    ordered = sorted(by_id.values(), key=lambda e: e.created_at, reverse=True)
    return ordered[:want]


class PoolEreqIndex:
    """Lazily maps episode_id -> episode-request row, one pool fetch at a time.

    Many episodes share a pool (a league round), so we fetch each pool's ereq
    list once and cache the episode_id -> ereq lookup it yields.
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self._cache: dict[str, dict[str, dict[str, Any]]] = {}

    def ereq_for(self, episode: EpisodeRecord) -> dict[str, Any] | None:
        if not episode.pool_id:
            return None
        pool_key = f"pool_{episode.pool_id}"
        mapping = self._cache.get(pool_key)
        if mapping is None:
            mapping = self._load_pool(pool_key)
            self._cache[pool_key] = mapping
        return mapping.get(episode.episode_id)

    def _load_pool(self, pool_key: str) -> dict[str, dict[str, Any]]:
        mapping: dict[str, dict[str, Any]] = {}
        offset, page = 0, 200
        while True:
            try:
                rows = self._client.get_json(
                    "/v2/experience-request-episodes", pool_id=pool_key, limit=page, offset=offset
                )
            except httpx.HTTPStatusError as exc:
                log(f"    ! could not list ereqs for {pool_key}: {exc}")
                break
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                eid = row.get("episode_id")
                if eid:
                    mapping[str(eid)] = row
            if len(rows) < page:
                break
            offset += page
        return mapping


def crewborg_slots(ereq: dict[str, Any]) -> list[dict[str, Any]]:
    """Participant entries in this ereq that are crewborg (one per slot it held)."""
    return [
        p for p in (ereq.get("participants") or [])
        if p.get("policy_name") == POLICY_NAME
    ]


def episode_is_complete(out_dir: Path, want_replay: bool, want_logs: bool) -> bool:
    """True if a prior run already wrote this episode's expected files."""
    if not (out_dir / "episode.json").exists():
        return False
    if want_replay and not (out_dir / "replay.json").exists():
        return False
    if want_logs and not (out_dir / "logs").exists():
        return False
    return True


def fetch_episode(
    client: Client,
    ereq_index: PoolEreqIndex,
    episode: EpisodeRecord,
    out_dir: Path,
    *,
    want_replay: bool,
    want_logs: bool,
) -> dict[str, Any]:
    """Download everything available for one episode into `out_dir`.

    Each piece is best-effort: a failure to fetch the replay or one slot's log
    is logged and recorded in the returned summary, but does not abort the
    episode or the run.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "episode_id": episode.episode_id,
        "created_at": episode.created_at,
        "version": episode.version,
        "job_id": episode.job_id,
        "dir": out_dir.name,
        "replay": False,
        "episode_request": False,
        "logs": [],
        "errors": [],
    }

    # 1. Episode detail (fall back to the listing record if the detail call fails).
    try:
        detail = client.get_json(f"/episodes/{episode.episode_id}")
    except httpx.HTTPStatusError as exc:
        detail = episode.detail
        summary["errors"].append(f"episode detail: {exc}")
    (out_dir / "episode.json").write_text(json.dumps(detail, indent=2))

    # 2. Episode-request row (participants, scores, status, game_config).
    ereq = ereq_index.ereq_for(episode)
    if ereq is not None:
        (out_dir / "episode_request.json").write_text(json.dumps(ereq, indent=2))
        summary["episode_request"] = True
        summary["status"] = ereq.get("status")
        summary["participants"] = [
            {"slot": p.get("position"), "name": p.get("policy_name")}
            for p in (ereq.get("participants") or [])
        ]
    else:
        summary["errors"].append("episode-request row not found in pool")

    # 3. Replay (whole game). The API serves the binary `.bitreplay` zlib-
    # compressed; keep that raw blob and also write the decompressed bytes as
    # `replay.json` -- the form COGAME_LOAD_REPLAY_URI loads (it is binary, not
    # JSON, despite the name; see the module docstring).
    if want_replay:
        if episode.replay_url:
            try:
                raw = httpx.get(episode.replay_url, follow_redirects=True, timeout=120.0)
                raw.raise_for_status()
                (out_dir / "replay.json.z").write_bytes(raw.content)
                try:
                    decompressed = zlib.decompress(raw.content)
                except zlib.error:
                    # Already uncompressed (defensive; the API serves .z today).
                    decompressed = raw.content
                (out_dir / "replay.json").write_bytes(decompressed)
                summary["replay"] = True
            except (httpx.HTTPError, OSError) as exc:
                summary["errors"].append(f"replay: {exc}")
        else:
            summary["errors"].append("no replay_url on episode")

    # 4. crewborg's per-slot policy logs (needs the ereq for slot + pv).
    if want_logs and ereq is not None:
        slots = crewborg_slots(ereq)
        if slots:
            logs_dir = out_dir / "logs"
            logs_dir.mkdir(exist_ok=True)
            for p in slots:
                slot, pv = p.get("position"), p.get("policy_version_id")
                ver = p.get("version")
                text = client.get_text_or_none(
                    f"/v2/experience-request-episodes/{ereq['id']}/{pv}/policy-logs/{slot}"
                )
                if text is None:
                    summary["errors"].append(f"policy-log slot {slot}: unavailable")
                    continue
                name = f"crewborg_slot{slot}_v{ver}.log"
                (logs_dir / name).write_text(text)
                summary["logs"].append(name)
        else:
            summary["errors"].append("no crewborg participant in this episode")
    elif want_logs and ereq is None:
        summary["errors"].append("cannot fetch logs without episode-request row")

    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download full episode data for the N most recent crewborg episodes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-n", "--num", type=int, default=10,
                        help="Number of most-recent episodes to download.")
    default_out = Path(__file__).resolve().parent.parent / "episode_data"
    parser.add_argument("-o", "--out", type=Path, default=default_out,
                        help="Output directory (one subdir per episode + index.json).")
    parser.add_argument("--version", type=int, default=None,
                        help="Only this crewborg policy version (e.g. 2). Default: all.")
    parser.add_argument("--server", default=None,
                        help="Observatory API base URL. Default: the official gateway "
                             "derived from your `softmax login` (<api-server>/observatory).")
    parser.add_argument("--no-replay", action="store_true", help="Skip replay downloads.")
    parser.add_argument("--no-logs", action="store_true", help="Skip crewborg policy-log downloads.")
    parser.add_argument("--force", action="store_true",
                        help="Re-download episodes whose directory already looks complete.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    want_replay = not args.no_replay
    want_logs = not args.no_logs
    server = args.server or default_server()
    args.out.mkdir(parents=True, exist_ok=True)

    with Client(server, load_token()) as client:
        pvs = discover_policy_versions(client, args.version)
        log("crewborg policy versions: " + ", ".join(f"v{pv['version']}" for pv in pvs))
        episodes = discover_episodes(client, pvs, args.num)
        if not episodes:
            log("No crewborg episodes found.")
            return 1
        log(f"Found {len(episodes)} episode(s); downloading to {args.out}")

        ereq_index = PoolEreqIndex(client)
        summaries: list[dict[str, Any]] = []
        for i, ep in enumerate(episodes, 1):
            # Sortable, human-scannable dir name: <timestamp>_<short id>.
            stamp = ep.created_at.replace(":", "").replace("-", "").replace(".", "")[:15]
            ep_dir = args.out / f"{stamp}_{ep.episode_id[:8]}"
            if not args.force and episode_is_complete(ep_dir, want_replay, want_logs):
                log(f"  [{i}/{len(episodes)}] {ep.episode_id[:8]} v{ep.version} — already present, skipping")
                summaries.append({"episode_id": ep.episode_id, "dir": ep_dir.name, "skipped": True})
                continue
            log(f"  [{i}/{len(episodes)}] {ep.episode_id[:8]} v{ep.version} {ep.created_at}")
            s = fetch_episode(
                client, ereq_index, ep, ep_dir,
                want_replay=want_replay, want_logs=want_logs,
            )
            for err in s["errors"]:
                log(f"      ! {err}")
            summaries.append(s)

    index = {
        "server": server,
        "policy": POLICY_NAME,
        "requested": args.num,
        "downloaded": len(summaries),
        "episodes": summaries,
    }
    (args.out / "index.json").write_text(json.dumps(index, indent=2))
    log(f"Done. Wrote {len(summaries)} episode(s) + index.json to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
