"""The ordinary role prompt remains game-owned under the native transport."""

from __future__ import annotations

from crewborg.strategy.meeting.prompts import system_prompt_for_context


def test_prompt_loader_uses_files_and_missing_file_fallback(tmp_path) -> None:
    (tmp_path / "crewmate.md").write_text("CREWMATE FILE", encoding="utf-8")

    crewmate = system_prompt_for_context(
        {"self": {"role": "crewmate"}}, prompt_dir=str(tmp_path)
    )
    imposter = system_prompt_for_context(
        {"self": {"role": "imposter"}}, prompt_dir=str(tmp_path)
    )

    assert "CREWMATE FILE" in crewmate
    assert "Imposter doctrine" in imposter
