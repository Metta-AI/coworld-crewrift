#!/usr/bin/env python3
"""Identify and download full Coworld episode artifacts (replay, results, logs).

This is the lab's general-purpose episode-artifact downloader. Point it at a set
of episodes (by policy, by experience-request, by pool/round/division, or by
explicit id) and it writes one self-contained directory per episode containing
everything the Observatory data API will hand back: the episode record, the
results, the replay (raw + decompressed), and every per-agent stderr trace.

Why this exists / how it differs from `coworld replays|episode-logs|episode-results`:
the `coworld` CLI is the right tool for ad-hoc interactive inspection. This script
exists to (1) discover episodes across *all* of a policy's versions in one pass,
(2) bundle replay + per-agent logs + results + metadata into one directory per
episode, and (3) read raw JSON against the live routes so it survives the
client/server version skew that recurs here (the published `coworld` client
regularly ships behind the server). It is **game-agnostic**: nothing about
Crewrift, crewborg, or any specific game is baked in.

THE KEY IDEA: every episode -- whether a league/tournament episode or an ad-hoc
experience-request episode -- carries a `job_id`, and the job is the universal
artifact handle. All artifacts come from three job routes (verified live
2026-06-08):

    GET /jobs/{job_id}/artifacts/results        -> results.json   (scores/metrics)
    GET /jobs/{job_id}/artifacts/replay          -> replay bytes   (game replay)
    GET /jobs/{job_id}/policy-logs               -> ["policy_agent_0.log", ...]
    GET /jobs/{job_id}/policy-logs/{agent_idx}   -> one agent's stderr trace
    GET /jobs/{job_id}/policy-artifact           -> [slot, ...] with uploaded artifacts
    GET /jobs/{job_id}/policy-artifact/{agent_idx} -> one slot's artifact zip
    GET /jobs/{job_id}/artifacts/error_info      -> error_info.json (only on failure)

Each artifact is best-effort: a missing replay or one missing log is logged and
recorded in the per-episode summary, never aborts the episode or the run.

DISCOVERY MODES (pick exactly one):

    --policy NAME [--version N]   league episodes a policy played, newest first
    --ereq ereq_... [--ereq ...]  explicit experience-request episode rows
    --xreq xreq_...               all child episodes of one experience request
    --pool pool_... | --round round_... | --division div_...
                                  experience-request episodes in a container
    --episode UUID [--episode ..] explicit league episode records by id

Usage (auth comes from `softmax login`; run inside `uv run` so softmax is importable):

    uv run python fetch_artifacts.py --policy crewborg -n 10 --out /tmp/eps
    uv run python fetch_artifacts.py --xreq xreq_abc... --out /tmp/eps
    uv run python fetch_artifacts.py --ereq ereq_abc... --ereq ereq_def... --no-logs
    uv run python fetch_artifacts.py --pool pool_abc... -n 20 --out /tmp/eps

The run is idempotent: an episode directory that already looks complete is
skipped unless --force is given.

ENDPOINT MAP & DRIFT NOTES -- see references/endpoint-map.md next to this script.
The authoritative live route list is always `<base>/openapi.json`; read it when a
route 4xxs, because the server moves faster than the client. `<base>` defaults to
the official gateway derived from your `softmax login`
(`<api-server>/observatory`, today https://softmax.com/api/observatory). Pass
--server to override.
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


def log(msg: str) -> None:
    """Progress to stderr so stdout stays clean for any future piping."""
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Auth + server resolution
# --------------------------------------------------------------------------- #

def _softmax_auth():
    try:
        import softmax.auth as auth
    except ImportError as exc:  # pragma: no cover - environment guard
        sys.exit(f"Could not import softmax.auth ({exc}). Run inside `uv run`.")
    return auth


def default_server() -> str:
    """The official Observatory gateway, tracking wherever the user is logged in.

    `get_api_server()` is the login host the `coworld` CLI talks to (today
    https://softmax.com/api); the Observatory data API lives under its
    `/observatory` path. Deriving it here means this script follows the user's
    `softmax login` rather than hard-coding a host.
    """
    return _softmax_auth().get_api_server().rstrip("/") + "/observatory"


def load_token() -> str:
    """Return the current softmax auth token, or exit with a clear message.

    NB the current softmax.auth API is `load_current_token(server=...)`. Older
    tools call `load_current_cogames_token(api_server=...)`, which has been
    removed -- if you copy auth code from elsewhere and it errors, this is why.
    """
    auth = _softmax_auth()
    token = auth.load_current_token(server=auth.get_api_server())
    if not token:
        sys.exit("Not authenticated. Run: uv run softmax login")
    return token


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #

class Client:
    """Thin authenticated httpx wrapper over the Observatory data API."""

    def __init__(self, server: str, token: str) -> None:
        self._http = httpx.Client(
            base_url=server.rstrip("/"),
            headers={"X-Auth-Token": token},
            timeout=120.0,
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

    def get_bytes_or_none(self, path: str) -> bytes | None:
        """GET bytes; return None (not raise) on a 4xx so one missing artifact
        does not abort the episode."""
        r = self._http.get(path)
        if r.status_code >= 400:
            return None
        return r.content

    def get_text_or_none(self, path: str) -> str | None:
        r = self._http.get(path)
        if r.status_code >= 400:
            return None
        return r.text


# --------------------------------------------------------------------------- #
# Normalized episode reference
# --------------------------------------------------------------------------- #

@dataclass
class EpisodeRef:
    """One episode plus the handles needed to fetch its artifacts.

    `record` is the raw source row (a league episode record or an
    experience-request episode row); it is written verbatim as episode.json.
    `job_id` is the universal artifact handle. `replay_url` is a fallback replay
    source when the job replay artifact is unavailable.
    """

    ref_id: str                    # episode uuid or ereq_... id
    created_at: str
    job_id: str | None
    replay_url: str | None
    label: str                     # short human label (policy/version/status)
    record: dict[str, Any] = field(default_factory=dict)


def _tags(rec: dict[str, Any]) -> dict[str, Any]:
    return rec.get("tags") or {}


def _ref_from_episode_record(rec: dict[str, Any], label: str = "") -> EpisodeRef:
    """Normalize a `/episodes/{id}` league-episode record."""
    tags = _tags(rec)
    return EpisodeRef(
        ref_id=str(rec["id"]),
        created_at=rec.get("created_at") or "",
        job_id=tags.get("job_id"),
        replay_url=rec.get("replay_url"),
        label=label or "episode",
        record=rec,
    )


def _ref_from_ereq_row(row: dict[str, Any]) -> EpisodeRef:
    """Normalize a `/v2/episode-requests` experience-request episode row."""
    names = ",".join(
        sorted({p.get("policy_name", "?") for p in (row.get("participants") or [])})
    )
    status = row.get("status") or "?"
    return EpisodeRef(
        ref_id=str(row["id"]),
        created_at=row.get("created_at") or "",
        job_id=None if row.get("job_id") is None else str(row.get("job_id")),
        replay_url=row.get("replay_url"),
        label=f"{status} [{names}]" if names else status,
        record=row,
    )


# --------------------------------------------------------------------------- #
# Discovery modes
# --------------------------------------------------------------------------- #

def discover_by_policy(client: Client, name: str, version: int | None, want: int) -> list[EpisodeRef]:
    """League episodes a policy played, newest first, across its versions.

    /stats/policy-versions?name_exact=NAME -> version ids
    /episodes?policy_version_id=PV         -> episode records (id, replay_url, tags.job_id)
    """
    rows = client.get_json("/stats/policy-versions", name_exact=name, limit=100)
    if isinstance(rows, dict):
        rows = rows.get("entries", [])
    pvs = [{"id": r["id"], "version": r.get("version")} for r in rows]
    if version is not None:
        pvs = [pv for pv in pvs if pv["version"] == version]
        if not pvs:
            sys.exit(f"No '{name}' policy version {version} found "
                     f"(have: {sorted(r.get('version') for r in rows)}).")
    if not pvs:
        sys.exit(f"No policy versions found for name '{name}'.")
    pvs.sort(key=lambda pv: (pv["version"] or -1), reverse=True)
    log(f"{name} policy versions: " + ", ".join(f"v{pv['version']}" for pv in pvs))

    by_id: dict[str, EpisodeRef] = {}
    for pv in pvs:
        offset, page = 0, 100
        # Episodes interleave across versions in time; fetch a generous window
        # per version then merge-sort by created_at.
        while offset < max(want * 2, want + 20):
            eps = client.get_json("/episodes", policy_version_id=pv["id"], limit=page, offset=offset)
            eps = eps if isinstance(eps, list) else eps.get("entries", [])
            if not eps:
                break
            for rec in eps:
                eid = str(rec["id"])
                if eid not in by_id:
                    by_id[eid] = _ref_from_episode_record(rec, label=f"{name} v{pv['version']}")
            if len(eps) < page:
                break
            offset += page
    ordered = sorted(by_id.values(), key=lambda e: e.created_at, reverse=True)
    return ordered[:want]


def discover_by_ereq(client: Client, ereq_ids: list[str]) -> list[EpisodeRef]:
    """Explicit experience-request episode rows by id."""
    refs: list[EpisodeRef] = []
    for eid in ereq_ids:
        try:
            row = client.get_json(f"/v2/episode-requests/{eid}")
        except httpx.HTTPStatusError as exc:
            log(f"  ! {eid}: {exc}")
            continue
        refs.append(_ref_from_ereq_row(row))
    return refs


def discover_by_xreq(client: Client, xreq_id: str, want: int) -> list[EpisodeRef]:
    """All child episodes of one experience request, newest first."""
    rows = client.get_json(f"/v2/experience-requests/{xreq_id}/episodes")
    rows = rows if isinstance(rows, list) else rows.get("entries", [])
    refs = [_ref_from_ereq_row(r) for r in rows]
    refs.sort(key=lambda e: e.created_at, reverse=True)
    return refs[:want]


def discover_by_container(
    client: Client,
    *,
    pool_id: str | None,
    round_id: str | None,
    division_id: str | None,
    want: int,
) -> list[EpisodeRef]:
    """Experience-request episodes filtered by pool / round / division.

    Only `pool_id`, `round_id`, `division_id` are real server-side filters on
    /v2/episode-requests (coworld_id/job_id/episode_id are silently ignored).
    A bare pool uuid 422s -- it must carry the `pool_` prefix.
    """
    if pool_id and not pool_id.startswith("pool_"):
        pool_id = f"pool_{pool_id}"
    params: dict[str, Any] = {"limit": min(max(want, 1), 1000), "offset": 0}
    if pool_id:
        params["pool_id"] = pool_id
    if round_id:
        params["round_id"] = round_id
    if division_id:
        params["division_id"] = division_id
    page = client.get_json("/v2/episode-requests", **params)
    rows = page.get("entries", []) if isinstance(page, dict) else page
    refs = [_ref_from_ereq_row(r) for r in rows]
    refs.sort(key=lambda e: e.created_at, reverse=True)
    return refs[:want]


def discover_by_episode(client: Client, episode_ids: list[str]) -> list[EpisodeRef]:
    """Explicit league episode records by uuid."""
    refs: list[EpisodeRef] = []
    for eid in episode_ids:
        try:
            rec = client.get_json(f"/episodes/{eid}")
        except httpx.HTTPStatusError as exc:
            log(f"  ! {eid}: {exc}")
            continue
        refs.append(_ref_from_episode_record(rec))
    return refs


# --------------------------------------------------------------------------- #
# Per-episode artifact download (keyed off job_id)
# --------------------------------------------------------------------------- #

def _artifact_slot_index(entry: object) -> int | None:
    """Slot index from a policy-artifact listing entry.

    The route returns filenames like ``policy_artifact_0.zip``; tolerate a bare
    int or numeric string too in case the shape changes.
    """
    if isinstance(entry, int):
        return entry
    if isinstance(entry, str):
        import re
        m = re.search(r"(\d+)", entry)
        if m:
            return int(m.group(1))
    return None


def _write_replay(content: bytes, out_dir: Path) -> None:
    """Write the raw replay blob plus its decompressed form.

    Replays are served zlib-compressed (magic 0x78). We keep the raw blob as
    replay.json.z and write the decompressed bytes as replay.json -- the
    directly-loadable form (it is the game's binary replay, named .json only to
    match what `coworld run-episode` writes and the replay-viewer recipe loads).
    """
    (out_dir / "replay.json.z").write_bytes(content)
    try:
        decompressed = zlib.decompress(content)
    except zlib.error:
        decompressed = content  # already uncompressed (defensive)
    (out_dir / "replay.json").write_bytes(decompressed)


def episode_is_complete(out_dir: Path, want_replay: bool, want_logs: bool) -> bool:
    if not (out_dir / "episode.json").exists():
        return False
    if want_replay and not (out_dir / "replay.json").exists():
        return False
    if want_logs and not (out_dir / "logs").exists():
        return False
    return True


def fetch_episode(
    client: Client,
    ref: EpisodeRef,
    out_dir: Path,
    *,
    want_replay: bool,
    want_results: bool,
    want_logs: bool,
) -> dict[str, Any]:
    """Download everything available for one episode into `out_dir`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "ref_id": ref.ref_id,
        "created_at": ref.created_at,
        "label": ref.label,
        "job_id": ref.job_id,
        "dir": out_dir.name,
        "results": False,
        "replay": False,
        "logs": [],
        "policy_artifacts": [],
        "error_info": False,
        "errors": [],
    }

    # 1. Source record (the episode row / detail), verbatim.
    (out_dir / "episode.json").write_text(json.dumps(ref.record, indent=2))

    job = ref.job_id
    if job is None:
        summary["errors"].append("no job_id on episode -- artifacts unavailable")
        return summary

    # 2. Results (scores / metrics).
    if want_results:
        raw = client.get_text_or_none(f"/jobs/{job}/artifacts/results")
        if raw is not None:
            (out_dir / "results.json").write_text(raw)
            summary["results"] = True
        else:
            summary["errors"].append("results artifact unavailable")

    # 3. Replay (prefer the job artifact; fall back to the episode's replay_url).
    if want_replay:
        content = client.get_bytes_or_none(f"/jobs/{job}/artifacts/replay")
        if content is None and ref.replay_url:
            try:
                r = httpx.get(ref.replay_url, follow_redirects=True, timeout=120.0)
                r.raise_for_status()
                content = r.content
            except httpx.HTTPError as exc:
                summary["errors"].append(f"replay_url fallback: {exc}")
        if content is not None:
            _write_replay(content, out_dir)
            summary["replay"] = True
        else:
            summary["errors"].append("replay unavailable (job artifact + replay_url both failed)")

    # 4. Per-agent policy logs. The job lists its own log files; download each.
    if want_logs:
        names = client.get_text_or_none(f"/jobs/{job}/policy-logs")
        log_names: list[str] = []
        if names is not None:
            try:
                log_names = json.loads(names)
            except json.JSONDecodeError:
                log_names = []
        if log_names:
            logs_dir = out_dir / "logs"
            logs_dir.mkdir(exist_ok=True)
            for idx, fname in enumerate(log_names):
                text = client.get_text_or_none(f"/jobs/{job}/policy-logs/{idx}")
                if text is None:
                    summary["errors"].append(f"policy-log {idx} ({fname}): unavailable")
                    continue
                safe = Path(fname).name or f"policy_agent_{idx}.log"
                (logs_dir / safe).write_text(text)
                summary["logs"].append(safe)
        else:
            summary["errors"].append("no policy logs listed for job")

    # 5. Per-player artifact zips (policy-scoped: only slots we own come back).
    # Players may upload one zip of structured telemetry/debug data per slot
    # (e.g. crewborg's trace zip) — separate from the stderr policy logs.
    # The listing is filenames (`["policy_artifact_0.zip", ...]`), not bare
    # slot ints; the download route is keyed by the slot index parsed from them.
    if want_logs:
        listing = client.get_text_or_none(f"/jobs/{job}/policy-artifact")
        slots: list[int] = []
        if listing is not None:
            try:
                entries = json.loads(listing)
            except json.JSONDecodeError:
                entries = []
                summary["errors"].append(f"unparseable policy-artifact listing: {listing[:80]}")
            for entry in entries:
                idx = _artifact_slot_index(entry)
                if idx is None:
                    summary["errors"].append(f"unrecognized policy-artifact entry: {entry!r}")
                    continue
                slots.append(idx)
        for idx in slots:
            content = client.get_bytes_or_none(f"/jobs/{job}/policy-artifact/{idx}")
            if content is None:
                summary["errors"].append(f"policy-artifact {idx}: unavailable")
                continue
            artifacts_dir = out_dir / "artifacts"
            artifacts_dir.mkdir(exist_ok=True)
            (artifacts_dir / f"policy_artifact_{idx}.zip").write_bytes(content)
            summary["policy_artifacts"].append(idx)

    # 6. Error info (present only when the episode failed).
    err = client.get_text_or_none(f"/jobs/{job}/artifacts/error_info")
    if err is not None:
        (out_dir / "error_info.json").write_text(err)
        summary["error_info"] = True

    return summary


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download full Coworld episode artifacts (replay, results, per-agent logs).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sel = parser.add_argument_group("discovery mode (pick exactly one)")
    sel.add_argument("--policy", help="Policy name; downloads that policy's recent league episodes.")
    sel.add_argument("--version", type=int, default=None,
                     help="With --policy: restrict to this policy version (default: all).")
    sel.add_argument("--ereq", action="append", default=[],
                     help="Experience-request episode id (ereq_...). Repeatable.")
    sel.add_argument("--xreq", help="Experience-request id (xreq_...); downloads all its child episodes.")
    sel.add_argument("--pool", help="Pool id (pool_...).")
    sel.add_argument("--round", dest="round_id", help="Round id (round_...).")
    sel.add_argument("--division", dest="division_id", help="Division id (div_...).")
    sel.add_argument("--episode", action="append", default=[],
                     help="League episode uuid. Repeatable.")

    parser.add_argument("-n", "--num", type=int, default=10,
                        help="Max episodes for policy/xreq/pool/round/division modes.")
    parser.add_argument("-o", "--out", type=Path, default=Path("episode_data"),
                        help="Output directory (one subdir per episode + index.json).")
    parser.add_argument("--server", default=None,
                        help="Observatory API base URL. Default: <api-server>/observatory from `softmax login`.")
    parser.add_argument("--no-replay", action="store_true", help="Skip replay downloads.")
    parser.add_argument("--no-results", action="store_true", help="Skip results downloads.")
    parser.add_argument("--no-logs", action="store_true", help="Skip per-agent policy-log downloads.")
    parser.add_argument("--force", action="store_true",
                        help="Re-download episodes whose directory already looks complete.")
    return parser.parse_args(argv)


def resolve_refs(client: Client, args: argparse.Namespace) -> list[EpisodeRef]:
    """Dispatch to exactly one discovery mode based on the selection args."""
    modes = [
        bool(args.policy),
        bool(args.ereq),
        bool(args.xreq),
        bool(args.pool or args.round_id or args.division_id),
        bool(args.episode),
    ]
    if sum(modes) != 1:
        sys.exit("Pick exactly one discovery mode: --policy | --ereq | --xreq | "
                 "--pool/--round/--division | --episode")

    if args.policy:
        return discover_by_policy(client, args.policy, args.version, args.num)
    if args.ereq:
        return discover_by_ereq(client, args.ereq)
    if args.xreq:
        return discover_by_xreq(client, args.xreq, args.num)
    if args.pool or args.round_id or args.division_id:
        return discover_by_container(
            client, pool_id=args.pool, round_id=args.round_id,
            division_id=args.division_id, want=args.num,
        )
    return discover_by_episode(client, args.episode)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    want_replay = not args.no_replay
    want_results = not args.no_results
    want_logs = not args.no_logs
    server = args.server or default_server()
    args.out.mkdir(parents=True, exist_ok=True)

    with Client(server, load_token()) as client:
        refs = resolve_refs(client, args)
        if not refs:
            log("No episodes found for the given selection.")
            return 1
        log(f"Found {len(refs)} episode(s); downloading to {args.out}")

        summaries: list[dict[str, Any]] = []
        for i, ref in enumerate(refs, 1):
            stamp = ref.created_at.replace(":", "").replace("-", "").replace(".", "")[:15]
            short = ref.ref_id[:16] if ref.ref_id.startswith("ereq_") else ref.ref_id[:8]
            ep_dir = args.out / f"{stamp}_{short}"
            if not args.force and episode_is_complete(ep_dir, want_replay, want_logs):
                log(f"  [{i}/{len(refs)}] {short} {ref.label} — already present, skipping")
                summaries.append({"ref_id": ref.ref_id, "dir": ep_dir.name, "skipped": True})
                continue
            log(f"  [{i}/{len(refs)}] {short} {ref.label} {ref.created_at}")
            s = fetch_episode(
                client, ref, ep_dir,
                want_replay=want_replay, want_results=want_results, want_logs=want_logs,
            )
            for err in s["errors"]:
                log(f"      ! {err}")
            summaries.append(s)

    index = {
        "server": server,
        "selection": {
            k: v for k, v in {
                "policy": args.policy, "version": args.version,
                "ereq": args.ereq or None, "xreq": args.xreq,
                "pool": args.pool, "round": args.round_id, "division": args.division_id,
                "episode": args.episode or None, "num": args.num,
            }.items() if v
        },
        "downloaded": len(summaries),
        "episodes": summaries,
    }
    (args.out / "index.json").write_text(json.dumps(index, indent=2))
    log(f"Done. Wrote {len(summaries)} episode(s) + index.json to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
