"""Tests for the commissioner LLM interview client + scorer (interview.py).

No real network or LLM calls: the transport and the Anthropic client are both
mocked/faked. Covers the orchestration (generate -> ask -> score), infra
classification (empty answer, transport failure), score parsing/clamping, and
the pure interview verdict in decision.py.
"""

from __future__ import annotations

import unittest

from decision import INTERVIEW_MIN_SCORE, interview_verdict
import interview as interview_module
from interview import (
    AnthropicRestClient,
    InterviewInfraError,
    InterviewResult,
    pick_fallback_question,
    run_interview,
    transport_from_address,
)


class _FakeTransport:
    def __init__(self, frame: dict) -> None:
        self._frame = frame
        self.asked: list[str] = []

    def ask(self, question: str, *, context=None) -> dict:
        self.asked.append(question)
        return self._frame


class _FakeLLM:
    model = "fake"

    def __init__(
        self,
        *,
        question: str = "Q?",
        score: float = 0.8,
        reason: str = "ok",
        generate_error: Exception | None = None,
        score_error: Exception | None = None,
    ) -> None:
        self._question = question
        self._score = score
        self._reason = reason
        self._generate_error = generate_error
        self._score_error = score_error
        self.scored: list[tuple[str, str]] = []
        self.generated = 0

    def generate_question(self) -> str:
        self.generated += 1
        if self._generate_error is not None:
            raise self._generate_error
        return self._question

    def score_answer(self, question: str, answer: str) -> tuple[float, str]:
        self.scored.append((question, answer))
        if self._score_error is not None:
            raise self._score_error
        return self._score, self._reason


class RunInterviewTest(unittest.TestCase):
    def test_happy_path_returns_score(self) -> None:
        transport = _FakeTransport({"type": "interview_answer", "answer": "Skip when unsure.", "degraded": False})
        llm = _FakeLLM(question="When to skip?", score=0.77, reason="good")
        result = run_interview(transport, llm=llm)
        self.assertIsInstance(result, InterviewResult)
        self.assertEqual(result.question, "When to skip?")
        self.assertEqual(result.answer, "Skip when unsure.")
        self.assertAlmostEqual(result.score, 0.77)
        self.assertFalse(result.degraded)
        self.assertEqual(transport.asked, ["When to skip?"])
        self.assertEqual(llm.scored[0][1], "Skip when unsure.")

    def test_degraded_answer_is_flagged(self) -> None:
        transport = _FakeTransport(
            {"type": "interview_answer", "answer": "INTERVIEW_DEGRADED: no key", "degraded": True}
        )
        result = run_interview(transport, llm=_FakeLLM(score=0.0))
        self.assertTrue(result.degraded)

    def test_empty_answer_is_infra_error(self) -> None:
        transport = _FakeTransport({"type": "interview_answer", "answer": "", "degraded": False})
        with self.assertRaises(InterviewInfraError):
            run_interview(transport, llm=_FakeLLM())

    def test_transport_failure_propagates_as_infra(self) -> None:
        class _Boom:
            def ask(self, question: str, *, context=None) -> dict:
                raise InterviewInfraError("unreachable")

        with self.assertRaises(InterviewInfraError):
            run_interview(_Boom(), llm=_FakeLLM())


class RunInterviewResiliencyTest(unittest.TestCase):
    """LLM-failure resiliency: riddle fallback, scorer auto-pass, transport unchanged."""

    _ANSWER = {"type": "interview_answer", "answer": "Vote out vent evidence; skip when unsure.", "degraded": False}

    def _set_toggles(self, *, fallback: bool, autopass: bool):
        saved = (interview_module.INTERVIEW_RIDDLE_FALLBACK, interview_module.INTERVIEW_AUTOPASS_ON_LLM_FAIL)
        interview_module.INTERVIEW_RIDDLE_FALLBACK = fallback
        interview_module.INTERVIEW_AUTOPASS_ON_LLM_FAIL = autopass
        return saved

    def _restore_toggles(self, saved) -> None:
        interview_module.INTERVIEW_RIDDLE_FALLBACK, interview_module.INTERVIEW_AUTOPASS_ON_LLM_FAIL = saved

    # (a) riddle-gen LLM fails -> a fallback question is used and the answer is
    # still scored normally (NOT an infra hold, NOT an auto-pass).
    def test_riddle_gen_failure_uses_fallback_and_scores_normally(self) -> None:
        transport = _FakeTransport(self._ANSWER)
        llm = _FakeLLM(generate_error=InterviewInfraError("no key for generation"), score=0.72, reason="solid")
        result = run_interview(transport, llm=llm, seed="policy-123")
        self.assertTrue(result.fallback_question)
        self.assertFalse(result.auto_passed)
        self.assertAlmostEqual(result.score, 0.72)
        self.assertEqual(result.question, pick_fallback_question("policy-123"))
        self.assertIn(result.question, interview_module._FALLBACK_QUESTION_POOL)
        # The fallback question was actually asked and scored.
        self.assertEqual(transport.asked, [result.question])
        self.assertEqual(llm.scored[0][0], result.question)

    def test_fallback_question_is_deterministic_per_seed(self) -> None:
        self.assertEqual(pick_fallback_question("abc"), pick_fallback_question("abc"))
        self.assertIn(pick_fallback_question(42), interview_module._FALLBACK_QUESTION_POOL)

    # (b) scorer LLM fails AFTER an answer was received -> AUTO-PASS.
    def test_scorer_failure_after_answer_auto_passes(self) -> None:
        transport = _FakeTransport(self._ANSWER)
        llm = _FakeLLM(question="When to skip?", score_error=InterviewInfraError("grader 500"))
        result = run_interview(transport, llm=llm)
        self.assertTrue(result.auto_passed)
        self.assertFalse(result.degraded)
        self.assertEqual(result.score, interview_module.AUTOPASS_SCORE)
        self.assertIn("auto-passed", result.grader_reason)
        # And the pure verdict treats the auto-pass score as a PASS.
        self.assertTrue(interview_verdict(result.score, degraded=result.degraded).passed)

    # (c) transport / no answer -> still an infra hold (UNCHANGED), never auto-pass.
    def test_no_answer_is_infra_not_autopass(self) -> None:
        transport = _FakeTransport({"type": "interview_answer", "answer": "", "degraded": False})
        with self.assertRaises(InterviewInfraError):
            run_interview(transport, llm=_FakeLLM(score_error=InterviewInfraError("grader down")))

    def test_transport_connect_failure_is_infra_not_autopass(self) -> None:
        class _Boom:
            def ask(self, question: str, *, context=None) -> dict:
                raise InterviewInfraError("interview connect failed")

        with self.assertRaises(InterviewInfraError):
            run_interview(_Boom(), llm=_FakeLLM(score_error=InterviewInfraError("grader down")))

    # A degraded answer is excluded from auto-pass: when the scorer LLM fails on a
    # degraded answer it keeps infra-hold semantics (player-side failure).
    def test_degraded_answer_with_scorer_failure_is_infra(self) -> None:
        transport = _FakeTransport(
            {"type": "interview_answer", "answer": "INTERVIEW_DEGRADED: no key", "degraded": True}
        )
        with self.assertRaises(InterviewInfraError):
            run_interview(transport, llm=_FakeLLM(score_error=InterviewInfraError("grader down")))

    # No interviewer LLM key at all (from_env raises) -> with resiliency on, the
    # interview still proceeds (fallback question) and auto-passes a received answer.
    def test_missing_llm_key_falls_back_and_auto_passes(self) -> None:
        import os

        saved_env = {k: os.environ.pop(k, None) for k in ("CREWRIFT_PRIME_INTERVIEW_API_KEY", "ANTHROPIC_API_KEY")}
        try:
            transport = _FakeTransport(self._ANSWER)
            result = run_interview(transport, llm=None, seed="p")
            self.assertTrue(result.fallback_question)
            self.assertTrue(result.auto_passed)
            self.assertTrue(interview_verdict(result.score).passed)
        finally:
            for k, v in saved_env.items():
                if v is not None:
                    os.environ[k] = v

    # (d) toggles OFF -> old behavior: riddle-gen failure and scorer failure both
    # propagate as infra holds.
    def test_toggles_off_restore_old_infra_behavior(self) -> None:
        saved = self._set_toggles(fallback=False, autopass=False)
        try:
            # riddle-gen failure now propagates (no fallback).
            with self.assertRaises(InterviewInfraError):
                run_interview(_FakeTransport(self._ANSWER), llm=_FakeLLM(generate_error=InterviewInfraError("x")))
            # scorer failure now propagates (no auto-pass).
            with self.assertRaises(InterviewInfraError):
                run_interview(_FakeTransport(self._ANSWER), llm=_FakeLLM(score_error=InterviewInfraError("y")))
        finally:
            self._restore_toggles(saved)


class ScoreParsingTest(unittest.TestCase):
    def _client(self) -> AnthropicRestClient:
        return AnthropicRestClient(api_key="x", model="m")

    def test_parses_and_clamps_score(self) -> None:
        client = self._client()
        client._messages = lambda **_k: '{"score": 1.4, "reason": "great"}'  # type: ignore[method-assign]
        score, reason = client.score_answer("q", "a")
        self.assertEqual(score, 1.0)
        self.assertEqual(reason, "great")

    def test_non_json_grade_is_infra(self) -> None:
        client = self._client()
        client._messages = lambda **_k: "I cannot grade this"  # type: ignore[method-assign]
        with self.assertRaises(InterviewInfraError):
            client.score_answer("q", "a")

    def test_missing_key_from_env_is_infra(self) -> None:
        import os

        saved = {k: os.environ.pop(k, None) for k in ("CREWRIFT_PRIME_INTERVIEW_API_KEY", "ANTHROPIC_API_KEY")}
        try:
            with self.assertRaises(InterviewInfraError):
                AnthropicRestClient.from_env()
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class TransportFactoryTest(unittest.TestCase):
    def test_host_port(self) -> None:
        t = transport_from_address("host.local:9999")
        self.assertEqual((t.host, t.port), ("host.local", 9999))

    def test_ws_url(self) -> None:
        t = transport_from_address("ws://1.2.3.4:8770/")
        self.assertEqual((t.host, t.port, t.path), ("1.2.3.4", 8770, "/"))

    def test_bare_host_default_port(self) -> None:
        t = transport_from_address("host.local")
        self.assertEqual(t.port, 8770)


class InterviewVerdictTest(unittest.TestCase):
    def test_pass_at_or_above_threshold(self) -> None:
        v = interview_verdict(INTERVIEW_MIN_SCORE)
        self.assertTrue(v.passed)
        self.assertEqual(v.skill, "interview")

    def test_below_threshold_fails(self) -> None:
        self.assertFalse(interview_verdict(INTERVIEW_MIN_SCORE - 0.01).passed)

    def test_none_fails(self) -> None:
        self.assertFalse(interview_verdict(None).passed)

    def test_degraded_never_passes(self) -> None:
        self.assertFalse(interview_verdict(1.0, degraded=True).passed)


class StdlibWebsocketTransportTest(unittest.TestCase):
    """The commissioner's stdlib RFC6455 client talks to a real websocket server.

    Stands up an in-process ``websockets`` server speaking the
    ``coworld.interview.v1`` contract (interview_ready -> answer) and asserts the
    dependency-free :class:`WebsocketInterviewTransport` completes a round trip.
    """

    def test_round_trip_against_real_server(self) -> None:
        import asyncio
        import json
        import threading

        from websockets.asyncio.server import serve

        received: list[str] = []

        async def handler(ws):
            await ws.send(json.dumps({"type": "interview_ready", "protocol": "coworld.interview.v1", "llm_enabled": True}))
            async for raw in ws:
                msg = json.loads(raw)
                received.append(msg.get("question", ""))
                await ws.send(json.dumps({"type": "interview_answer", "answer": "Skip when unsure.", "degraded": False}))
                return

        result: dict = {}

        def run_server_and_client():
            async def amain():
                async with serve(handler, "127.0.0.1", 0) as server:
                    port = server.sockets[0].getsockname()[1]
                    transport = transport_from_address(f"127.0.0.1:{port}", timeout=5.0)
                    frame = await asyncio.to_thread(transport.ask, "When to skip?")
                    result.update(frame)

            asyncio.run(amain())

        thread = threading.Thread(target=run_server_and_client)
        thread.start()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "server/client thread hung")
        self.assertEqual(result.get("type"), "interview_answer")
        self.assertEqual(result.get("answer"), "Skip when unsure.")
        self.assertEqual(received, ["When to skip?"])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
