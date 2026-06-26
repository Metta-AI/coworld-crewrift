"""Tests for the content-graded talk gate (chat_grader + decision seam).

No real network/LLM: a fake grader is injected (or env toggles flip behavior),
so these exercise the gate semantics without any Anthropic call.
"""

from __future__ import annotations

import os
import unittest
from dataclasses import dataclass

import chat_grader
from chat_grader import (
    ChatGraderError,
    ChatGradeResult,
    concat_candidate_chat,
    grade_chat_content,
)
from decision import evaluate_combined_game


def _combined_game(*, talks: bool, texts: list[list[str]] | None = None) -> dict:
    # One 8-seat self-play game that passes hunting+tasks; voting depends on talk.
    game = {
        "imposter": [1, 1, 0, 0, 0, 0, 0, 0],
        "crew": [0, 0, 1, 1, 1, 1, 1, 1],
        "kills": [2, 0, 0, 0, 0, 0, 0, 0],
        "tasks": [0, 0, 4, 4, 4, 4, 4, 4],
        "vote_players": [0, 1, 2, 0, 0, 0, 0, 0],
        "vote_skip": [3, 0, 0, 0, 0, 0, 0, 0],
        "vote_timeout": [0, 0, 0, 0, 0, 0, 0, 0],
        "win": [True, True, False, False, False, False, False, False],
        "scores": [100, 100, 0, 0, 0, 0, 0, 0],
    }
    game["chat_messages"] = [1, 1, 2, 0, 0, 0, 0, 0] if talks else [0, 0, 0, 0, 0, 0, 0, 0]
    if texts is not None:
        game["chat_texts"] = texts
    return game


@dataclass
class _FakeGrader:
    """A grader stub: returns a fixed verdict, or raises to simulate an LLM error."""

    genuine: bool = True
    score: float = 0.9
    raise_error: bool = False

    def grade(self, chat_text: str) -> ChatGradeResult:
        if self.raise_error:
            raise ChatGraderError("simulated LLM failure")
        reason = "genuine social-deduction reasoning" if self.genuine else "canned/gibberish"
        return ChatGradeResult(passed=self.genuine, score=self.score, reason=reason, model="fake")


class ConcatCandidateChatTest(unittest.TestCase):
    def test_self_play_concatenates_all_seats(self) -> None:
        texts = [["sus red", "where blue"], [], ["i was in elec"], [], [], [], [], []]
        gr = {"chat_texts": texts}
        out = concat_candidate_chat(gr)  # self-play -> all seats
        self.assertIn("sus red", out)
        self.assertIn("where blue", out)
        self.assertIn("i was in elec", out)

    def test_missing_chat_texts_is_empty(self) -> None:
        self.assertEqual(concat_candidate_chat({}), "")

    def test_selected_seats_only(self) -> None:
        texts = [["a"], ["b"], ["c"]]
        self.assertEqual(concat_candidate_chat({"chat_texts": texts}, seats=[1]), "b")


class GradeChatContentResiliencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            k: os.environ.get(k)
            for k in ("CREWRIFT_PRIME_GRADE_CHAT_CONTENT",)
        }
        os.environ["CREWRIFT_PRIME_GRADE_CHAT_CONTENT"] = "1"

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_genuine_chat_passes(self) -> None:
        result = grade_chat_content("sus red, saw him vent", grader=_FakeGrader(genuine=True))
        assert result is not None
        self.assertTrue(result.passed)

    def test_gibberish_chat_fails(self) -> None:
        result = grade_chat_content("asdf asdf asdf", grader=_FakeGrader(genuine=False, score=0.1))
        assert result is not None
        self.assertFalse(result.passed)

    def test_empty_text_degrades_to_none(self) -> None:
        # No text to grade -> None (caller falls back to presence gate).
        self.assertIsNone(grade_chat_content("   ", grader=_FakeGrader(genuine=True)))

    def test_llm_error_degrades_to_none(self) -> None:
        # An LLM error NEVER DQs: returns None so the caller uses presence-only.
        self.assertIsNone(grade_chat_content("sus red", grader=_FakeGrader(raise_error=True)))

    def test_disabled_flag_degrades_to_none(self) -> None:
        os.environ["CREWRIFT_PRIME_GRADE_CHAT_CONTENT"] = "0"
        self.assertIsNone(grade_chat_content("sus red", grader=_FakeGrader(genuine=True)))

    def test_missing_key_degrades_to_none(self) -> None:
        # With no injected grader and no API key in env, from_env raises -> None.
        saved = {k: os.environ.pop(k, None) for k in chat_grader._GRADER_KEY_ENVS}
        try:
            self.assertIsNone(grade_chat_content("sus red"))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class ContentGatedVotingTest(unittest.TestCase):
    """The decision seam: chat_content_passed True/False/None over the gate."""

    def test_meeting_genuine_chat_qualifies(self) -> None:
        gr = _combined_game(talks=True)
        record = evaluate_combined_game(gr, chat_content_passed=True)
        skills = {v.skill: v for v in record.verdicts}
        self.assertTrue(skills["voting"].passed)
        self.assertTrue(record.passed)
        self.assertTrue(skills["voting"].raw_inputs["chat_content_graded"])

    def test_meeting_gibberish_chat_does_not_qualify(self) -> None:
        # Meeting + chat present, but content graded not-genuine -> voting FAILS.
        gr = _combined_game(talks=True)
        record = evaluate_combined_game(
            gr, chat_content_passed=False, chat_content_detail="canned/gibberish"
        )
        skills = {v.skill: v for v in record.verdicts}
        self.assertFalse(skills["voting"].passed)
        self.assertTrue(skills["hunting"].passed)
        self.assertTrue(skills["tasks"].passed)
        self.assertFalse(record.passed)
        self.assertIn("canned/gibberish", skills["voting"].detail)

    def test_meeting_no_chat_fails_unchanged(self) -> None:
        # No chat at all -> presence hard gate fails regardless of content grade.
        gr = _combined_game(talks=False)
        record = evaluate_combined_game(gr, chat_content_passed=True)
        skills = {v.skill: v for v in record.verdicts}
        self.assertFalse(skills["voting"].passed)
        self.assertIn("never talked", skills["voting"].detail)

    def test_grader_unavailable_falls_back_to_presence(self) -> None:
        # chat_content_passed=None (grader down/disabled) -> presence gate: talked
        # -> pass (no DQ), exactly the prior behavior.
        gr = _combined_game(talks=True)
        record = evaluate_combined_game(gr, chat_content_passed=None)
        skills = {v.skill: v for v in record.verdicts}
        self.assertTrue(skills["voting"].passed)
        self.assertFalse(skills["voting"].raw_inputs["chat_content_graded"])
        self.assertTrue(record.passed)

    def test_grader_disabled_presence_only(self) -> None:
        # Same as unavailable: None -> presence-only. A talked-but-gibberish policy
        # would pass here (content gate inert), which is the documented degraded mode.
        gr = _combined_game(talks=True)
        record = evaluate_combined_game(gr, chat_content_passed=None)
        self.assertTrue({v.skill: v for v in record.verdicts}["voting"].passed)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
