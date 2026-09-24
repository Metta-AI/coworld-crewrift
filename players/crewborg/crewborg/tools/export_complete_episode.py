"""Export a private Crewborg episode after a version-matched replay re-simulation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from bisect import bisect_right
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


def export_episode(
    *,
    replay: Path,
    expander: Path,
    trace: Path,
    results: Path,
    output: Path,
    episode_id: str,
    seat: int,
    source_revision: str,
    game_version: str,
) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
        raise ValueError("source revision must be a pinned lowercase Git SHA")
    if not episode_id or not game_version or seat < 0:
        raise ValueError("episode ID, game version, and seat are required")
    expanded = subprocess.run(
        [str(expander), "--format", "jsonl", str(replay)],
        check=True,
        capture_output=True,
        text=True,
    )
    replay_rows = [json.loads(line) for line in expanded.stdout.splitlines()]
    metadata = [row for row in replay_rows if row["key"] == "episode_metadata"]
    completed = [row for row in replay_rows if row["key"] == "trace_complete"]
    if len(metadata) != 1 or len(completed) != 1:
        raise ValueError("replay needs one metadata row and one completion proof")
    proof = completed[0]["value"]
    if proof["complete"] is not True or proof["outcome"] not in {
        "crew",
        "imposter",
        "draw",
    }:
        raise ValueError("replay is incomplete or has no authoritative outcome")
    if metadata[0]["value"]["hash_checking"] is not True:
        raise ValueError("replay was not hash checked")
    seed = metadata[0]["value"]["config"]["seed"]
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("replay seed is invalid")
    manifest_rows = [row for row in replay_rows if row["key"] == "player_manifest"]
    manifests = {row["player"]: row["value"] for row in manifest_rows}
    if seat not in manifests or len(manifests) != len(manifest_rows):
        raise ValueError("replay has no unique manifest for the selected seat")
    colors = {slot: manifest["color"] for slot, manifest in manifests.items()}
    result = json.loads(results.read_text())
    for slot, manifest in manifests.items():
        manifest_role = manifest["role"]
        won = manifest_role == proof["outcome"]
        score = (3 if manifest_role == "imposter" else 1) if won else 0
        if (
            result["win"][slot] is not won
            or result["scores"][slot] != score
            or result["imposter"][slot] != int(manifest_role == "imposter")
            or result["crew"][slot] != int(manifest_role == "crew")
        ):
            raise ValueError("official results disagree with the replayed seat outcome")
    role = manifests[seat]["role"]
    expected_win = role == proof["outcome"]
    expected_score = result["scores"][seat]

    starts = sorted(
        row["ts"]
        for row in replay_rows
        if row["key"] in {"vote_called_body", "vote_called_button"}
    )

    def meeting_at(tick: int) -> int:
        return bisect_right(starts, tick) - 1

    trace_rows = []
    for line in trace.read_text().splitlines():
        row = json.loads(line)
        if row["kind"] == "trace":
            if row["tick"] > completed[0]["ts"]:
                raise ValueError("player trace extends beyond replay completion")
            trace_rows.append(row)
        elif row["kind"] != "metric":
            raise ValueError("unknown player telemetry line")
    calls = [row for row in trace_rows if row["event"] == "domain.meeting_llm_decision"]
    if not calls:
        raise ValueError("player trace has no meeting model decisions")
    for row in calls:
        data = row["data"]
        if (
            data["inference_mode"] not in {"typed_choice", "native_language"}
            or data["provider_request"] is None
            or data["provider_response"] is None
            or meeting_at(row["tick"]) < 0
        ):
            raise ValueError(
                "meeting model decision lacks private provider evidence or replay meeting"
            )
        if data["inference_mode"] == "typed_choice":
            choice = json.loads(data["provider_response"])["answers"]["vote"]["choice"]
            if (
                choice != data["decision"]["vote_target"]
                or choice
                not in data["provider_request"]["questions"]["vote"]["criteria"]
            ):
                raise ValueError(
                    "typed provider choice differs from the selected legal vote"
                )

    applied: dict[int, tuple[str, dict]] = {}
    for effect in replay_rows:
        if effect["player"] != seat or effect["key"] not in {"vote_cast", "chat"}:
            continue
        meeting = meeting_at(effect["ts"])
        if meeting < 0:
            raise ValueError("game effect has no replay meeting")
        if effect["key"] == "vote_cast":
            target = (
                effect["value"]["target"]
                if "target" in effect["value"]
                else colors[effect["value"]["target_slot"]]
            )
            selections = [
                row
                for row in trace_rows
                if row["event"] == "domain.meeting_vote_selected"
                and meeting_at(row["tick"]) == meeting
                and row["tick"] <= effect["ts"]
                and row["data"]["target"] == target
            ]
            relevant = [
                (index, row)
                for index, row in enumerate(calls)
                if meeting_at(row["tick"]) == meeting
                and row["tick"] <= (selections[-1]["tick"] if selections else -1)
                and row["data"]["decision"]["action"]
                in {"set_tentative_vote", "submit_vote"}
            ]
            if (
                not selections
                or not relevant
                or relevant[-1][1]["data"]["decision"]["vote_target"] != target
                or relevant[-1][1]["data"]["provider_decision"]["vote_target"] != target
            ):
                continue
            index, choice = relevant[-1]
            staged = any(
                row["event"] == "domain.meeting_tentative_vote"
                and row["data"]["target"] == target
                and choice["tick"] <= row["tick"] <= selections[-1]["tick"]
                for row in trace_rows
            )
            if not staged:
                continue
        else:
            text = effect["value"]["text"]
            selections = [
                row
                for row in trace_rows
                if row["event"] == "domain.meeting_chat_selected"
                and meeting_at(row["tick"]) == meeting
                and row["tick"] <= effect["ts"]
                and row["data"]["text"] == text
            ]
            relevant = [
                (index, row)
                for index, row in enumerate(calls)
                if meeting_at(row["tick"]) == meeting
                and row["tick"] <= (selections[-1]["tick"] if selections else -1)
                and row["data"]["decision"]["action"] == "send_chat"
            ]
            if (
                not selections
                or not relevant
                or relevant[-1][1]["data"]["decision"]["chat_text"] != text
                or relevant[-1][1]["data"]["provider_decision"]["chat_text"] != text
            ):
                continue
            index, _ = relevant[-1]
        if index in applied:
            raise ValueError("one model decision matched multiple game effects")
        applied[index] = (effect["key"], effect)

    decisions = []
    for index, row in enumerate(calls):
        data = row["data"]
        decision = data["decision"]
        typed = data["inference_mode"] == "typed_choice"
        request = data["provider_request"]
        response = data["provider_response"]
        prompt = (
            request
            if typed
            else [
                {"role": "system", "content": request["system"]},
                {"role": "user", "content": request["user"]},
            ]
        )
        decision_id = f"{episode_id}:seat:{seat}:decision:{index}"
        attempt_id = f"{decision_id}:model"
        effect = applied.get(index)
        executed = (
            None
            if effect is None
            else {
                "kind": effect[0],
                "tick": effect[1]["ts"],
                "value": effect[1]["value"],
            }
        )
        decisions.append(
            {
                "schema_version": "1",
                "event_type": "decision",
                "event_id": str(uuid5(NAMESPACE_URL, decision_id)),
                "episode_id": episode_id,
                "decision_id": decision_id,
                "decision_index": index,
                "game": "coworld-crewrift",
                "game_version": game_version,
                "source_revision": source_revision,
                "seat": str(seat),
                "visibility": "private",
                "observation": request["state"]
                if typed
                else json.loads(request["user"]),
                "prompt": prompt,
                "attempts": [
                    {
                        "attempt_id": attempt_id,
                        "policy": data["model"],
                        "origin": "model",
                        "inference_mode": data["inference_mode"],
                        "provider_request": request,
                        "provider_response": response,
                        "response": json.loads(response) if typed else response,
                        "parsed_action": decision,
                        "accepted": effect is not None,
                    }
                ],
                "selected_attempt_id": attempt_id if effect is not None else None,
                "executed_action": executed,
                "action_status": "accepted" if effect is not None else "rejected",
                "terminal": False,
            }
        )

    complete = {
        "schema_version": "1",
        "episode": {
            "schema_version": "1",
            "event_type": "episode",
            "event_id": str(uuid5(NAMESPACE_URL, episode_id + ":episode")),
            "episode_id": episode_id,
            "seed_family": f"crewrift:{seed}",
            "game": "coworld-crewrift",
            "game_version": game_version,
            "source_revision": source_revision,
            "status": "completed",
            "outcome": {
                "team": proof["outcome"],
                "role": role,
                "win": expected_win,
                "score": expected_score,
                "replay_hash_verified": True,
                "replay_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
            },
            "participant_outcomes": result,
        },
        "decisions": decisions,
    }
    with os.fdopen(
        os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as stream:
        stream.write(
            json.dumps(complete, separators=(",", ":"), ensure_ascii=False) + "\n"
        )
    return complete


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("replay", "expander", "trace", "results", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--seat", required=True, type=int)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--game-version", required=True)
    args = parser.parse_args()
    episode = export_episode(
        replay=args.replay,
        expander=args.expander,
        trace=args.trace,
        results=args.results,
        output=args.output,
        episode_id=args.episode_id,
        seat=args.seat,
        source_revision=args.source_revision,
        game_version=args.game_version,
    )
    print(
        json.dumps(
            {
                "episode_id": episode["episode"]["episode_id"],
                "decisions": len(episode["decisions"]),
                "accepted": sum(
                    row["action_status"] == "accepted" for row in episode["decisions"]
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
