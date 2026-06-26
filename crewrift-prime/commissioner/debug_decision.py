#!/usr/bin/env python3
"""Local debug path for the Crewrift Prime skill gate.

Runs the SAME pure decision function the hosted commissioner uses
(``decision.evaluate_entrants``) over sample or saved episode ``game_results``,
WITHOUT the hosted round-runner, and prints the decision records (and the exact
``COMMISSIONER_DECISION {json}`` line the hosted path logs).

Usage
-----
  # built-in synthetic sample (one passing, one failing entrant):
  python debug_decision.py

  # from a JSON file shaped { entrant_id: { variant_id: [game_results, ...] } }:
  python debug_decision.py path/to/results.json

  # or pipe that JSON on stdin:
  cat results.json | python debug_decision.py -

Each game_results dict is the per-slot results_schema the platform delivers in
EpisodeResult.game_results (arrays indexed by seat: vote_players, kills, tasks,
imposter, ...). See README.md for the full contract.
"""

from __future__ import annotations

import json
import sys

from decision import DECISION_LOG_TAG, SKILL_VARIANTS, evaluate_entrants


def _sample() -> dict[str, dict[str, list[dict]]]:
    # 8-seat self-play drills; slot 0 is the forced imposter for vote/hunt.
    passing = {
        "scn_vote_basic": [
            {"imposter": [1, 0, 0, 0, 0, 0, 0, 0], "vote_players": [6, 1, 0, 0, 0, 0, 0, 0]},
            {"imposter": [1, 0, 0, 0, 0, 0, 0, 0], "vote_players": [5, 2, 0, 0, 0, 0, 0, 0]},
        ],
        "scn_hunt_isolated": [
            {"imposter": [1, 0, 0, 0, 0, 0, 0, 0], "kills": [2, 0, 0, 0, 0, 0, 0, 0]},
            {"imposter": [1, 0, 0, 0, 0, 0, 0, 0], "kills": [1, 0, 0, 0, 0, 0, 0, 0]},
        ],
        "scn_task_pressure": [
            {"tasks": [5, 4, 4, 5, 4, 4, 5, 4]},
            {"tasks": [4, 4, 4, 4, 4, 4, 4, 4]},
        ],
    }
    failing_hunt = {
        "scn_vote_basic": [
            {"imposter": [1, 0, 0, 0, 0, 0, 0, 0], "vote_players": [6, 1, 0, 0, 0, 0, 0, 0]},
        ],
        "scn_hunt_isolated": [
            {"imposter": [1, 0, 0, 0, 0, 0, 0, 0], "kills": [0, 0, 0, 0, 0, 0, 0, 0]},
            {"imposter": [1, 0, 0, 0, 0, 0, 0, 0], "kills": [0, 0, 0, 0, 0, 0, 0, 0]},
        ],
        "scn_task_pressure": [
            {"tasks": [5, 5, 5, 5, 5, 5, 5, 5]},
        ],
    }
    return {
        "entrant-PASS-0000-0000-000000000001": passing,
        "entrant-FAILHUNT-0000-000000000002": failing_hunt,
    }


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        raw = sys.stdin.read() if argv[1] == "-" else open(argv[1], encoding="utf-8").read()
        data = json.loads(raw)
    else:
        data = _sample()

    records = evaluate_entrants(data)
    print(f"# skill variants: {', '.join(SKILL_VARIANTS)}")
    for entrant, record in records.items():
        print("=" * 88)
        print(f"entrant: {entrant}")
        print(f"  decision: {record.decision}")
        print(f"  reason:   {record.reason}")
        for v in record.verdicts:
            mark = "PASS" if v.passed else "FAIL"
            print(
                f"    [{mark}] {v.skill:<8} {v.metric_name}={v.metric_value:.4f} "
                f"{v.comparator}{v.threshold:g}  episodes={v.episodes_counted}  raw={v.raw_inputs}"
            )
        # show the exact hosted stdout line so local and hosted match
        print("  hosted log line:")
        print(
            f"    {DECISION_LOG_TAG} "
            + json.dumps({"entrant_policy_version_id": entrant, **record.to_dict()}, sort_keys=True)
        )
    print("=" * 88)
    promoted = sum(1 for r in records.values() if r.passed)
    print(f"summary: {promoted}/{len(records)} promoted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
