#!/usr/bin/env python3
"""Submit-&-monitor helpers for the Coworld policy lifecycle.

This script assumes the version is **already uploaded** (the `build & upload` skill does
that). It covers the API-mechanical parts of the *submit -> qualify -> champion* path that
the `coworld` CLI doesn't, with the emphasis on the one question that actually matters:

    **did the submitted policy QUALIFY?**

Qualification is a commissioner-issued state transition on the league-policy-membership,
`qualifying -> competing` (or `-> disqualified`). It is NOT a counter the backend exposes;
the verdict lives in `LeaguePolicyMembership.status`, and the running progress lives in the
`policy-membership-events` evidence. See `references/cli.md` for the full model.

Command:

    monitor   --name NAME [--watch]       qualification-centered status: submission ->
                                          membership status -> qualifying progress ->
                                          standings. `--watch` polls until the verdict is
                                          terminal (competing / disqualified) — run it as a
                                          BACKGROUND process and keep working.

Two footguns this monitor is built to avoid (verified in metta source):
  * A failed *round* is NOT a disqualified *policy* — infra faults (5xx/OOM/dead pod) abort
    rounds without changing membership status. The only trustworthy DQ signal is
    `membership.status == disqualified`. So we key off status, never off round failures.
  * A disqualified membership drops out of `active_only` / leaderboard queries — so we poll
    WITHOUT `active_only`, or a DQ would silently vanish instead of being reported.

To pick a version to submit, list uploads with the `build-and-upload` skill's `versions.py`.

Usage (auth from `softmax login`; run inside `uv run`):

    uv run python policy_lifecycle.py monitor  --name crewborg
    uv run python policy_lifecycle.py monitor  --name crewborg --watch   # background it

Routes (Observatory gateway): /stats/policy-versions, /v2/league-submissions,
/v2/league-policy-memberships, /v2/policy-membership-events, /v2/divisions/{id}/leaderboard.
The API drifts — read `<base>/openapi.json` if a route 4xxs.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

# Membership status meanings (PolicyMembershipStatus in metta models.py).
TERMINAL_STATUSES = {"competing", "disqualified"}  # qualification verdict is settled
SUBSTATUS_HINT = {
    "crash": "container crashed/failed episodes — pull the qualifier episodes' logs "
    "(the usual cause is TIMEOUTS / LLM latency; a fast/no-LLM player qualifies clean)",
    "inactive": "evicted (player-per-user limit, default 2) or retired — NOT a quality "
    "failure; a newer champion of yours can evict an older membership",
}


def client() -> httpx.Client:
    try:
        import softmax.auth as auth
    except ImportError as exc:  # pragma: no cover
        sys.exit(f"Could not import softmax.auth ({exc}). Run inside `uv run`.")
    api = auth.get_api_server()
    tok = auth.load_current_token(server=api)
    if not tok:
        sys.exit("Not authenticated. Run: uv run softmax login")
    return httpx.Client(base_url=api.rstrip("/") + "/observatory",
                        headers={"X-Auth-Token": tok}, timeout=60.0, follow_redirects=True)


def get(c: httpx.Client, path: str, **params: Any) -> Any:
    r = c.get(path, params=params or None)
    r.raise_for_status()
    return r.json()


def rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("entries") or payload.get("memberships") or payload.get("submissions") or []
    return []


def policy_name_of(obj: dict[str, Any]) -> str | None:
    pv = obj.get("policy_version") or {}
    return (pv.get("policy") or {}).get("name") or pv.get("policy_name") or obj.get("policy_name")


def qualifying_progress(c: httpx.Client, lpm_id: str) -> str | None:
    """Latest 'completed/scheduled episodes (score)' from the membership's event evidence."""
    try:
        evs = rows(get(c, "/v2/policy-membership-events", league_policy_membership_id=lpm_id, limit=50))
    except httpx.HTTPStatusError:
        return None
    for ev in reversed(evs):  # newest last in practice; scan back for one carrying observed counts
        for e in (ev.get("evidence") or []):
            obs = ((e.get("metadata") or {}).get("observed")) or {}
            if "completed_episodes" in obs or "scheduled_episodes" in obs:
                done, sched = obs.get("completed_episodes"), obs.get("scheduled_episodes")
                score = obs.get("score")
                bits = [f"{done}/{sched} qualifying episodes" if sched else f"{done} episodes"]
                if score is not None:
                    bits.append(f"score {score}")
                return ", ".join(bits)
    return None


def verdict(status: str | None, substatus: str | None, is_champion: bool) -> str:
    if status == "competing":
        return "✅ QUALIFIED — now competing" + ("  👑 CHAMPION" if is_champion else "")
    if status == "disqualified":
        hint = SUBSTATUS_HINT.get(substatus or "", "read the qualifier episodes' logs for the cause")
        return f"❌ DISQUALIFIED (substatus={substatus or '-'}) — {hint}"
    if status == "qualifying":
        return "⏳ QUALIFYING — running qualifier episodes; not yet qualified"
    if status == "submitted":
        return "… submitted — placed, awaiting the first qualifier round"
    return f"… status={status}"


def focal(c: httpx.Client, name: str) -> tuple[list[dict], list[dict]]:
    """This policy's submissions and memberships (memberships WITHOUT active_only, to catch DQ)."""
    subs = [s for s in rows(get(c, "/v2/league-submissions", mine=True, limit=200))
            if policy_name_of(s) == name]
    mems = [m for m in rows(get(c, "/v2/league-policy-memberships", mine=True, limit=1000))
            if policy_name_of(m) == name]
    return subs, mems


def render(c: httpx.Client, name: str) -> tuple[str, bool]:
    """Print current state; return (focal_status, is_terminal) for the watch loop."""
    subs, mems = focal(c, name)
    print(f"=== {name} — submissions ===")
    if not subs:
        print("  (none — has it been submitted to a league yet?)")
    for s in subs[:10]:
        st = s.get("status")
        line = f"  {s.get('id')}  status={st}  v{(s.get('policy_version') or {}).get('version')}"
        if st in ("rejected", "withdrawn") and s.get("notes"):
            line += f"  ⚠️ notes: {s.get('notes')}"   # e.g. 'league has no submission division' (rare; league misconfig)
        if s.get("league_policy_membership_id"):
            line += f"  membership={s.get('league_policy_membership_id')}"
        print(line)

    print(f"\n=== {name} — memberships (did it QUALIFY?) ===")
    if not mems:
        print("  (no memberships yet — submission still processing, or it was rejected above)")
    focal_status, terminal = None, False
    # newest membership first
    mems.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    for i, m in enumerate(mems[:6]):
        status, substatus = m.get("status"), m.get("substatus")
        champ = bool(m.get("is_champion"))
        div = m.get("division") or {}
        lpm = m.get("id")
        print(f"  membership {lpm}  div={div.get('name') or m.get('division_id')}({div.get('type') or '?'})")
        print(f"      {verdict(status, substatus, champ)}")
        if status == "qualifying":
            prog = qualifying_progress(c, lpm)
            if prog:
                print(f"      progress: {prog}")
        if status == "competing":
            div_id = div.get("id") or m.get("division_id")
            pid = (m.get("player") or {}).get("id") or m.get("player_id")
            if div_id:
                try:
                    board = rows(get(c, f"/v2/divisions/{div_id}/leaderboard", include_recent_rounds=5))
                    hit = next((e for e in board if e.get("player_id") == pid), None)
                    if hit:
                        print(f"      standings: rank {hit.get('rank')}  score {hit.get('score')}  "
                              f"rounds {hit.get('rounds_played')}")
                except httpx.HTTPStatusError as exc:
                    print(f"      ! leaderboard: {exc}")
        if i == 0:  # the latest membership drives the watch verdict
            focal_status = status
            terminal = status in TERMINAL_STATUSES
    # a rejected submission with no membership is also terminal
    if not mems and subs and all(s.get("status") in ("rejected", "withdrawn") for s in subs):
        terminal = True
    return focal_status or "(none)", terminal


def cmd_monitor(c: httpx.Client, args: argparse.Namespace) -> int:
    if not args.watch:
        render(c, args.name)
        return 0
    deadline = time.monotonic() + args.max_minutes * 60
    last = None
    print(f"[watch] polling every {args.poll}s until {args.name} qualifies or is disqualified "
          f"(max {args.max_minutes} min). Background this and keep working.\n")
    while True:
        status, terminal = render(c, args.name)
        if status != last:
            print(f"\n[watch] >>> status now: {status}\n")
            last = status
        if terminal:
            print(f"[watch] DONE — terminal verdict: {status}")
            return 0
        if time.monotonic() > deadline:
            print(f"[watch] timed out after {args.max_minutes} min at status={status} (not terminal). "
                  f"Re-run to keep watching.")
            return 0
        time.sleep(args.poll)
        print("-" * 60)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Submit & monitor: list versions / watch qualification.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pm = sub.add_parser("monitor", help="Qualification-centered status; --watch to poll until terminal.")
    pm.add_argument("--name", required=True)
    pm.add_argument("--watch", action="store_true", help="Poll until qualified/disqualified (background it).")
    pm.add_argument("--poll", type=int, default=60, help="Seconds between polls in --watch (default 60).")
    pm.add_argument("--max-minutes", type=int, default=120, help="Stop watching after this long (default 120).")
    pm.set_defaults(func=cmd_monitor)
    args = ap.parse_args(argv)
    with client() as c:
        return args.func(c, args)


if __name__ == "__main__":
    raise SystemExit(main())
