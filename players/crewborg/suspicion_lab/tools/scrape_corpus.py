#!/usr/bin/env python3
"""Incrementally scrape Crewrift league episodes (replay + results) into the corpus.

Stage A of the suspicion-learning pipeline (../README.md §3).
Lists completed rounds for a division, then pulls every episode of each round —
replay.json, results.json, episode.json; no logs — via the lab's fetch_artifacts.py
(which is idempotent per episode). A per-round ledger (corpus/_rounds_done.json)
makes re-runs cheap: fully-scraped rounds are skipped without re-listing episodes.

Usage (auth from `softmax login`; run from the player root (players/crewborg)):

    uv run python suspicion_lab/tools/scrape_corpus.py            # default: Crewrift/Competition
    uv run python suspicion_lab/tools/scrape_corpus.py --max-rounds 10
    uv run python suspicion_lab/tools/scrape_corpus.py --division div_...

Append-only: never deletes; a round re-runs only if its ledger entry is missing or
recorded incomplete. Run it on a loop/cron to keep the corpus growing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import httpx

# suspicion_lab/tools/ -> players/crewborg (the player root); everything hangs off it.
PLAYER_ROOT = Path(__file__).resolve().parents[2]
FETCH_ARTIFACTS = PLAYER_ROOT / "skills/coworld-episode-artifacts/scripts/fetch_artifacts.py"
DEFAULT_CORPUS = PLAYER_ROOT / "suspicion_lab" / "corpus"
DEFAULT_LEAGUE = "Crewrift"
DEFAULT_DIVISION = "Competition"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def observatory_client() -> httpx.Client:
    import softmax.auth as auth

    api = auth.get_api_server()
    token = auth.load_current_token(server=api)
    if not token:
        sys.exit("Not authenticated. Run: uv run softmax login")
    return httpx.Client(
        base_url=api.rstrip("/") + "/observatory",
        headers={"X-Auth-Token": token},
        timeout=120.0,
        follow_redirects=True,
    )


def get_json(client: httpx.Client, path: str, **params):
    r = client.get(path, params=params or None)
    r.raise_for_status()
    return r.json()


def entries(payload):
    """Unwrap the {entries: [...]} list shape some routes use."""
    return payload.get("entries", payload) if isinstance(payload, dict) else payload


def resolve_division(client: httpx.Client, league_name: str, division_name: str) -> str:
    leagues = [lg for lg in entries(get_json(client, "/v2/leagues")) if lg.get("name") == league_name]
    if not leagues:
        sys.exit(f"League {league_name!r} not found.")
    divisions = entries(get_json(client, "/v2/divisions", league_id=leagues[0]["id"]))
    for d in divisions:
        if d.get("name") == division_name:
            return d["id"]
    sys.exit(f"Division {division_name!r} not found in league {league_name!r}.")


def completed_rounds(client: httpx.Client, division_id: str, limit: int) -> list[dict]:
    rounds = entries(get_json(client, "/v2/rounds", division_id=division_id, status="completed", limit=limit))
    # newest first, as the API returns them; scrape newest first so a partial run
    # still favors fresh games (recency matters for fitting — design §9).
    return rounds


def load_ledger(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def scrape_round(round_id: str, corpus: Path) -> tuple[int, int]:
    """Pull one round's episodes via fetch_artifacts. Returns (ok, failed) counts."""
    cmd = [
        sys.executable, str(FETCH_ARTIFACTS),
        "--round", round_id,
        "-n", "1000",
        "--no-logs",
        "--out", str(corpus),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"  fetch_artifacts failed for {round_id}: {proc.stderr.strip()[-300:]}")
        return 0, 1
    # fetch_artifacts writes index.json with per-episode statuses for the run.
    index_path = corpus / "index.json"
    ok = failed = 0
    if index_path.exists():
        try:
            for ep in json.loads(index_path.read_text()).get("episodes", []):
                # the corpus needs the replay; results matter too but replay is the gate
                if ep.get("errors") or not ep.get("replay"):
                    failed += 1
                else:
                    ok += 1
        except (json.JSONDecodeError, OSError):
            pass
    return ok, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape league episodes into the suspicion corpus.")
    parser.add_argument("--league", default=DEFAULT_LEAGUE)
    parser.add_argument("--division", default=None, help="Division id (div_…); overrides --league/name resolution.")
    parser.add_argument("--division-name", default=DEFAULT_DIVISION)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--max-rounds", type=int, default=50, help="Scrape at most this many rounds this run.")
    parser.add_argument("--list-limit", type=int, default=200, help="How many completed rounds to list.")
    args = parser.parse_args(argv)

    args.corpus.mkdir(parents=True, exist_ok=True)
    ledger_path = args.corpus / "_rounds_done.json"
    ledger = load_ledger(ledger_path)

    with observatory_client() as client:
        division_id = args.division or resolve_division(client, args.league, args.division_name)
        rounds = completed_rounds(client, division_id, args.list_limit)
    log(f"{len(rounds)} completed rounds listed for {division_id}; {len(ledger)} already in ledger.")

    scraped = 0
    for rnd in rounds:
        rid = rnd["id"]
        prior = ledger.get(rid)
        if prior and prior.get("complete"):
            continue
        if scraped >= args.max_rounds:
            break
        log(f"scraping {rid} ({rnd.get('created_at', '')})…")
        ok, failed = scrape_round(rid, args.corpus)
        ledger[rid] = {
            "created_at": rnd.get("created_at"),
            "episodes_ok": ok,
            "episodes_failed": failed,
            # complete = we got through the round with no hard failure; episodes that
            # failed individually (e.g. expired replay) won't be retried — they're
            # recorded so build_dataset can report coverage.
            "complete": ok > 0,
        }
        ledger_path.write_text(json.dumps(ledger, indent=1, sort_keys=True))
        log(f"  done: {ok} ok, {failed} failed")
        scraped += 1

    total = sum(v.get("episodes_ok", 0) for v in ledger.values())
    log(f"Corpus ledger: {len(ledger)} rounds, ~{total} episodes ok.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
