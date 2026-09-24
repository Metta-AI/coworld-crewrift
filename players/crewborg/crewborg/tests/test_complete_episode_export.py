"""The exporter labels only model decisions joined to replayed game effects."""

import json
import stat
import subprocess

import pytest

from crewborg.tools import export_complete_episode


def test_export_joins_typed_vote_and_native_chat_to_one_replay_effect_each(
    tmp_path, monkeypatch
) -> None:
    rows = [
        {
            "ts": 0,
            "player": -1,
            "key": "episode_metadata",
            "value": {"hash_checking": True, "config": {"seed": 7}},
        },
        {
            "ts": 0,
            "player": 0,
            "key": "player_manifest",
            "value": {"color": "red", "role": "crew"},
        },
        {"ts": 1, "player": 0, "key": "vote_called_button", "value": {}},
        {"ts": 4, "player": 0, "key": "chat", "value": {"text": "Hi crew"}},
        {"ts": 10, "player": 0, "key": "vote_cast", "value": {"target": "skip"}},
        {
            "ts": 20,
            "player": -1,
            "key": "trace_complete",
            "value": {"complete": True, "outcome": "crew"},
        },
    ]
    monkeypatch.setattr(
        export_complete_episode.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, "".join(json.dumps(row) + "\n" for row in rows)
        ),
    )
    trace = [
        {
            "kind": "trace",
            "tick": 3,
            "event": "domain.meeting_llm_decision",
            "data": {
                "inference_mode": "native_language",
                "model": "haiku",
                "provider_request": {
                    "system": "Speak plainly",
                    "user": '{"meeting":1}',
                },
                "provider_response": '{"action":"send_chat","chat_text":"Hi crew"}',
                "decision": {
                    "action": "send_chat",
                    "chat_text": "Hi crew",
                    "vote_target": None,
                },
                "provider_decision": {
                    "action": "send_chat",
                    "chat_text": "Hi crew",
                    "vote_target": None,
                },
            },
        },
        {
            "kind": "trace",
            "tick": 3,
            "event": "domain.meeting_chat_selected",
            "data": {"text": "Hi crew"},
        },
        {
            "kind": "trace",
            "tick": 5,
            "event": "domain.meeting_llm_decision",
            "data": {
                "inference_mode": "typed_choice",
                "model": "jev-latest",
                "provider_request": {
                    "state": {"meeting": 1},
                    "questions": {"vote": {"criteria": {"skip": "Skip"}}},
                },
                "provider_response": '{"answers":{"vote":{"choice":"skip"}}}',
                "decision": {
                    "action": "set_tentative_vote",
                    "chat_text": None,
                    "vote_target": "skip",
                },
                "provider_decision": {
                    "action": "set_tentative_vote",
                    "chat_text": None,
                    "vote_target": "skip",
                },
            },
        },
        {
            "kind": "trace",
            "tick": 5,
            "event": "domain.meeting_tentative_vote",
            "data": {"target": "skip"},
        },
        {
            "kind": "trace",
            "tick": 9,
            "event": "domain.meeting_vote_selected",
            "data": {"target": "skip"},
        },
    ]
    trace_path = tmp_path / "telemetry.jsonl"
    trace_path.write_text("".join(json.dumps(row) + "\n" for row in trace))
    replay_path = tmp_path / "game.bitreplay"
    replay_path.write_bytes(b"fixture")
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps({"win": [True], "scores": [1], "crew": [1], "imposter": [0]})
    )
    output = tmp_path / "complete.jsonl"

    episode = export_complete_episode.export_episode(
        replay=replay_path,
        expander=tmp_path / "expander",
        trace=trace_path,
        results=results_path,
        output=output,
        episode_id="ereq-test",
        seat=0,
        source_revision="a" * 40,
        game_version="0.1.67",
    )

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert episode["episode"]["seed_family"] == "crewrift:7"
    assert [row["action_status"] for row in episode["decisions"]] == [
        "accepted",
        "accepted",
    ]
    assert episode["decisions"][0]["prompt"][0] == {
        "role": "system",
        "content": "Speak plainly",
    }
    assert (
        episode["decisions"][0]["attempts"][0]["response"]
        == trace[0]["data"]["provider_response"]
    )
    assert (
        episode["decisions"][1]["attempts"][0]["provider_request"]
        == trace[2]["data"]["provider_request"]
    )
    assert [row["executed_action"]["kind"] for row in episode["decisions"]] == [
        "chat",
        "vote_cast",
    ]

    rows[4]["value"] = {"target_slot": 0}
    mismatch = export_complete_episode.export_episode(
        replay=replay_path,
        expander=tmp_path / "expander",
        trace=trace_path,
        results=results_path,
        output=tmp_path / "mismatch.jsonl",
        episode_id="ereq-mismatch",
        seat=0,
        source_revision="a" * 40,
        game_version="0.1.67",
    )
    assert [row["action_status"] for row in mismatch["decisions"]] == [
        "accepted",
        "rejected",
    ]

    rows[4]["value"] = {"target": "skip"}
    trace[2]["data"]["provider_decision"]["vote_target"] = None
    trace_path.write_text("".join(json.dumps(row) + "\n" for row in trace))
    fallback = export_complete_episode.export_episode(
        replay=replay_path,
        expander=tmp_path / "expander",
        trace=trace_path,
        results=results_path,
        output=tmp_path / "fallback.jsonl",
        episode_id="ereq-fallback",
        seat=0,
        source_revision="a" * 40,
        game_version="0.1.67",
    )
    assert [row["action_status"] for row in fallback["decisions"]] == [
        "accepted",
        "rejected",
    ]

    results_path.write_text(
        json.dumps({"win": [True], "scores": [0], "crew": [1], "imposter": [0]})
    )
    with pytest.raises(ValueError, match="official results disagree"):
        export_complete_episode.export_episode(
            replay=replay_path,
            expander=tmp_path / "expander",
            trace=trace_path,
            results=results_path,
            output=tmp_path / "bad-result.jsonl",
            episode_id="ereq-bad-result",
            seat=0,
            source_revision="a" * 40,
            game_version="0.1.67",
        )
