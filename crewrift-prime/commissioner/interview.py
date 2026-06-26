"""LLM interview client + scorer for the Crewrift Prime qualification gate.

The interview is a HARD GATE added alongside the existing three-skill gate: a
policy qualifies only if it PASSES the skill gate AND passes the interview. The
interview verifies the candidate can chat/speak and reason about Crewrift voting
strategy.

End-to-end flow (driven by :func:`run_interview`):
  1. GENERATE a Crewrift voting-strategy riddle/question via an LLM
     (:meth:`AnthropicRestClient.generate_question`).
  2. CONNECT to the candidate player's interview websocket SERVER (launched in
     interview mode — see the player's ``coworld/interview_server.py``), send the
     question, and receive the answer (``InterviewTransport`` — injectable; the
     default is a stdlib websocket client).
  3. SCORE the answer 0..1 via an LLM grading call with a Crewrift-voting rubric
     (:meth:`AnthropicRestClient.score_answer`).

Failure classification mirrors ``xp_request_client.py``: any infrastructure
failure (no LLM token, the player server unreachable, a timeout, a malformed
response) raises :class:`InterviewInfraError` so the commissioner HOLDS the
entrant for retry rather than disqualifying it. Only a genuine interview round
(question asked, answer received, answer scored) yields a numeric result that the
pure gate in ``decision.py`` consumes.

Why stdlib-only (no ``anthropic`` SDK, no ``websockets`` lib)
-------------------------------------------------------------
The commissioner image installs only the vendored ``commissioners`` package
(fastapi/pydantic/uvicorn/pyyaml). We deliberately keep this module on the Python
standard library — ``urllib`` for the Anthropic REST API (mirroring
``xp_request_client.py``) and a tiny ``socket``-based RFC6455 client for the
player websocket — so the interview adds ZERO new image dependencies. The LLM
and transport are both injectable so tests run with no network at all.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

# --- config (env-overridable) -------------------------------------------------

# Anthropic REST endpoint + model. The commissioner uses Anthropic directly
# (not Bedrock) for the riddle + grading calls; auth is an Anthropic API key.
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_INTERVIEW_MODEL = os.getenv("CREWRIFT_PRIME_INTERVIEW_MODEL", "claude-haiku-4-5-20251001")
# Where the interviewer LLM key comes from (kept distinct from the player's key).
_INTERVIEW_KEY_ENVS = ("CREWRIFT_PRIME_INTERVIEW_API_KEY", "ANTHROPIC_API_KEY")
# Default port the player interview server listens on (mirror the player default).
DEFAULT_INTERVIEW_PORT = int(os.getenv("CREWRIFT_INTERVIEW_PORT", "8770"))

PROTOCOL_VERSION = "coworld.interview.v1"

_USER_AGENT = "crewrift-prime-commissioner/1.0 (+https://softmax.com)"


def _flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


# Resiliency toggles (LLM-failure handling). Both default ON per the user's
# request: an interviewer LLM hiccup must NOT block an otherwise-good candidate.
# A TRANSPORT/player failure is unaffected by these (it stays an infra hold).
#
# RIDDLE_FALLBACK: if the riddle-GENERATION LLM call fails, fall back to a
# built-in question pool so the interview still proceeds with a real question.
# AUTOPASS_ON_LLM_FAIL: if the answer-SCORING LLM call fails AFTER an answer was
# received from the player, auto-pass (any received answer passes when the
# scorer LLM is unavailable) rather than holding for retry.
INTERVIEW_RIDDLE_FALLBACK = _flag("CREWRIFT_PRIME_INTERVIEW_FALLBACK", True)
INTERVIEW_AUTOPASS_ON_LLM_FAIL = _flag("CREWRIFT_PRIME_INTERVIEW_AUTOPASS_ON_LLM_FAIL", True)

# Sentinel passing score used when the scorer LLM is unavailable but an answer
# was received (auto-pass). Mirrors decision.py's pass-at-or-above-threshold.
AUTOPASS_SCORE = 1.0

# A built-in pool of Crewrift voting-strategy questions used when the riddle
# GENERATION LLM call is unavailable (network error, missing key, bad response,
# timeout — anything :class:`AnthropicRestClient.generate_question` raises). One
# is picked deterministically-but-varied (seeded by policy_version_id, falling
# back to random) so the interview still asks a real, on-topic question. These
# cover suspicion ranking, skip-vs-vote calculus, reading vote patterns,
# defending as the imposter, and using concrete signals (vents/bodies/proximity).
_FALLBACK_QUESTION_POOL: tuple[str, ...] = (
    "In Crewrift, when should a crewmate vote to skip instead of voting out a "
    "suspected player, and how does that calculus change if you are the imposter?",
    "You are a crewmate at a meeting with no body and no hard evidence. How do you "
    "rank suspicion across the other seats, and what would make you commit a vote "
    "rather than skip?",
    "Describe how you read the live vote tally during a meeting: what does a sudden "
    "bandwagon (or a refusal to vote) tell you about who is likely the imposter?",
    "As the imposter, you have just been accused in a meeting. Walk through how you "
    "defend yourself, redirect suspicion, and decide whether to push a counter-vote "
    "or quietly skip.",
    "What concrete signals — vents, fresh bodies, proximity to a kill, broken "
    "alibis — most justify voting a player out in Crewrift, and how do you weigh "
    "them against the risk of ejecting an innocent crewmate?",
    "Early game with two imposters still alive: when is skipping the correct, "
    "information-preserving vote, and when does skipping instead hand the game to "
    "the imposters?",
)

# Backwards-compatible alias (first pool entry) for any external reference.
_FALLBACK_QUESTION = _FALLBACK_QUESTION_POOL[0]


def pick_fallback_question(seed: str | int | None = None) -> str:
    """Pick a fallback question deterministically-but-varied.

    Seeded by ``seed`` (e.g. the policy_version_id) so the SAME candidate gets a
    stable question across retries while different candidates vary; falls back to
    a random choice when no seed is given. No I/O.
    """
    if seed is None:
        import random

        return random.choice(_FALLBACK_QUESTION_POOL)
    digest = 0
    for ch in str(seed):
        digest = (digest * 131 + ord(ch)) & 0xFFFFFFFF
    return _FALLBACK_QUESTION_POOL[digest % len(_FALLBACK_QUESTION_POOL)]

# Grading rubric grounded in Crewrift voting strategy. The scorer LLM returns
# strict JSON {"score": 0..1, "reason": "..."}.
_GRADING_RUBRIC = """You are grading a candidate Crewrift player's interview answer about VOTING \
strategy, to decide if it may compete in the league. Crewrift is an Among Us-style social \
deduction game (8 players, 2 imposters; crewmates win by finishing tasks or voting out all \
imposters; imposters win by reaching parity or surviving the vote; meetings are triggered by \
reporting a body or the emergency button, then players chat and vote or skip).

Score the answer from 0.0 to 1.0 on how well it demonstrates real understanding of Crewrift \
voting strategy and the ability to communicate it:
- 1.0: clearly reasons about concrete signals (vents, bodies, proximity to kills, the live \
vote tally), explains when to skip vs. vote, and shows role-aware reasoning (crewmate vs imposter).
- 0.5: partially correct, generic, or only covers one role / a few points.
- 0.0: empty, off-topic, refuses, is not about Crewrift voting, or is a degraded/error placeholder \
(e.g. starts with "INTERVIEW_DEGRADED").

Respond with ONLY a JSON object: {"score": <float 0..1>, "reason": "<one sentence>"}. No markdown."""


# --- errors -------------------------------------------------------------------


class InterviewError(Exception):
    """Base class for interview failures."""


class InterviewInfraError(InterviewError):
    """An infrastructure failure (HOLD, never DQ).

    Raised for a missing interviewer LLM token, an unreachable/timing-out player
    interview server, a malformed transport/LLM response, or a grading call that
    fails. The commissioner classifies these as holds (retry next time), exactly
    like an xp-request/replay infra failure.
    """


# --- result -------------------------------------------------------------------


@dataclass
class InterviewResult:
    """The numeric outcome the pure gate (decision.py) consumes."""

    score: float
    question: str
    answer: str
    degraded: bool
    grader_reason: str = ""
    model: str | None = None
    # Resiliency provenance (LLM-failure handling). ``fallback_question`` is True
    # when the riddle-generation LLM failed and a built-in pool question was used.
    # ``auto_passed`` is True when the scorer LLM failed AFTER an answer was
    # received and the interview was auto-passed (any received answer passes).
    fallback_question: bool = False
    auto_passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "question": self.question,
            "answer": self.answer[:2000],
            "degraded": self.degraded,
            "grader_reason": self.grader_reason,
            "model": self.model,
            "fallback_question": self.fallback_question,
            "auto_passed": self.auto_passed,
        }


# --- transport (injectable) ---------------------------------------------------


class InterviewTransport(Protocol):
    """Send a question to a player's interview server and return the answer."""

    def ask(self, question: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]: ...


@dataclass
class WebsocketInterviewTransport:
    """Default transport: a stdlib ``socket`` RFC6455 client (no deps).

    Connects to ``ws://<host>:<port>/`` (the player interview server), waits for
    the ``interview_ready`` frame, sends one ``interview_question``, and returns
    the parsed ``interview_answer`` frame. All socket/protocol failures become
    :class:`InterviewInfraError` (a hold).
    """

    host: str
    port: int = DEFAULT_INTERVIEW_PORT
    path: str = "/"
    timeout: float = 30.0

    def ask(self, question: str, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        conn = _WebsocketConnection.connect(self.host, self.port, self.path, self.timeout)
        try:
            ready = conn.recv_json()
            if ready.get("type") != "interview_ready":
                raise InterviewInfraError(f"player server did not greet with interview_ready: {ready}")
            payload: dict[str, Any] = {"type": "interview_question", "question": question}
            if context:
                payload["context"] = context
            conn.send_json(payload)
            answer = conn.recv_json()
            # Skip any stray error frames until we get the answer (or give up).
            attempts = 0
            while answer.get("type") != "interview_answer" and attempts < 3:
                attempts += 1
                answer = conn.recv_json()
            if answer.get("type") != "interview_answer":
                raise InterviewInfraError(f"player server returned no interview_answer: {answer}")
            return answer
        finally:
            conn.close()


# --- LLM client (Anthropic REST over urllib) ----------------------------------


@dataclass
class AnthropicRestClient:
    """Minimal Anthropic Messages client over stdlib ``urllib`` (no SDK)."""

    api_key: str
    model: str = DEFAULT_INTERVIEW_MODEL
    timeout: float = 30.0
    max_tokens: int = 512

    @classmethod
    def from_env(cls) -> "AnthropicRestClient":
        key = ""
        for name in _INTERVIEW_KEY_ENVS:
            key = (os.environ.get(name) or "").strip()
            if key:
                break
        if not key:
            raise InterviewInfraError(
                "no interviewer LLM key (set CREWRIFT_PRIME_INTERVIEW_API_KEY or ANTHROPIC_API_KEY)"
            )
        return cls(api_key=key, model=DEFAULT_INTERVIEW_MODEL)

    def _messages(self, *, system: str, user: str, max_tokens: int) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            ANTHROPIC_API_URL,
            data=body,
            method="POST",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
                "accept": "application/json",
                "user-agent": _USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise InterviewInfraError(f"Anthropic HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise InterviewInfraError(f"Anthropic request failed: {exc}") from exc
        return _anthropic_text(raw)

    def generate_question(self) -> str:
        system = (
            "You write a single short interview question (a riddle is welcome) that probes whether "
            "a candidate understands Crewrift VOTING strategy. Crewrift is an Among Us-style social "
            "deduction game. Output ONLY the question text, one or two sentences, no preamble, no markdown."
        )
        text = self._messages(system=system, user="Write one Crewrift voting-strategy interview question.", max_tokens=200)
        text = text.strip()
        return text or _FALLBACK_QUESTION

    def score_answer(self, question: str, answer: str) -> tuple[float, str]:
        user = json.dumps({"question": question, "answer": answer}, separators=(",", ":"))
        text = self._messages(system=_GRADING_RUBRIC, user=user, max_tokens=200)
        return _parse_score(text)


# --- orchestration ------------------------------------------------------------


def run_interview(
    transport: InterviewTransport,
    *,
    llm: AnthropicRestClient | None = None,
    context: dict[str, Any] | None = None,
    seed: str | int | None = None,
) -> InterviewResult:
    """Generate a riddle, ask the player, score the answer. Resilient to LLM failures.

    ``transport`` is required (the caller knows how to reach the player server —
    see :class:`WebsocketInterviewTransport` and the launch seam in
    ``crewrift_prime_skill_commissioner.py``). ``llm`` defaults to an
    env-configured :class:`AnthropicRestClient`. ``seed`` (e.g. the
    policy_version_id) makes any fallback-question pick deterministic per
    candidate.

    Failure classification (LLM vs transport are handled separately):

    - **Riddle-generation LLM fails** (and ``CREWRIFT_PRIME_INTERVIEW_FALLBACK``
      is on, the default): fall back to a built-in pool question and PROCEED —
      the answer is still scored normally. Not an infra hold.
    - **Transport / player fails** (can't connect, timeout, no/empty/malformed
      answer): raises :class:`InterviewInfraError` — UNCHANGED infra-hold
      semantics. The auto-pass below NEVER applies here (no answer was received).
    - **Scoring LLM fails** AFTER a (non-degraded) answer was received (and
      ``CREWRIFT_PRIME_INTERVIEW_AUTOPASS_ON_LLM_FAIL`` is on, the default):
      AUTO-PASS — return a passing result (score ``AUTOPASS_SCORE``) rather than
      holding for retry. "Auto-pass any answer received when the scorer LLM
      fails." A DEGRADED answer is excluded (it keeps its player-side-failure
      semantics and never auto-passes).

    Building the LLM client itself can fail (no API key). With either resiliency
    toggle on, that no-key failure ALSO degrades gracefully (fallback question +
    auto-pass) rather than holding, so a missing interviewer key never blocks a
    candidate that successfully answered.
    """
    # Acquire the interviewer LLM. A missing key (from_env raising) is treated as
    # an LLM failure: with resiliency on we proceed with no LLM (fallback +
    # auto-pass); otherwise we preserve the old infra-hold behavior.
    interviewer = llm
    llm_unavailable = False
    if interviewer is None:
        try:
            interviewer = AnthropicRestClient.from_env()
        except InterviewInfraError:
            if not (INTERVIEW_RIDDLE_FALLBACK or INTERVIEW_AUTOPASS_ON_LLM_FAIL):
                raise
            interviewer = None
            llm_unavailable = True

    # 1) GENERATE the riddle (LLM). On LLM failure -> fallback question pool.
    fallback_question = False
    question = ""
    if interviewer is not None:
        try:
            question = interviewer.generate_question()
        except InterviewInfraError:
            if not INTERVIEW_RIDDLE_FALLBACK:
                raise
            question = ""
    if not question:
        fallback_question = True
        question = pick_fallback_question(seed)

    # 2) ASK the player (TRANSPORT). Failures here KEEP infra-hold semantics: any
    # InterviewInfraError (connect/timeout/closed) propagates unchanged.
    answer_frame = transport.ask(question, context=context)
    answer = answer_frame.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        # No answer genuinely received -> still an infra hold (NOT an auto-pass).
        raise InterviewInfraError("player interview answer was empty or malformed")
    degraded = bool(answer_frame.get("degraded"))
    model = answer_frame.get("model") if isinstance(answer_frame.get("model"), str) else (
        interviewer.model if interviewer is not None else None
    )

    # 3) SCORE the answer (LLM). An answer WAS received; if the scorer LLM fails
    # (or no LLM is available), AUTO-PASS rather than holding for retry. A
    # DEGRADED answer is excluded from auto-pass: it is a player-side failure
    # ("answer received but the player could not reason"), so it keeps its
    # existing semantics (scored-and-fails when scorable; infra-hold when the
    # scorer LLM is unavailable) and never auto-passes.
    if interviewer is None or llm_unavailable:
        if degraded:
            raise InterviewInfraError(
                "scorer LLM unavailable and player answer was degraded (no auto-pass)"
            )
        return _autopass_result(question, answer, degraded, model, fallback_question)
    try:
        score, reason = interviewer.score_answer(question, answer)
    except InterviewInfraError as exc:
        if not INTERVIEW_AUTOPASS_ON_LLM_FAIL or degraded:
            raise
        return _autopass_result(
            question, answer, degraded, model, fallback_question, scorer_error=str(exc)
        )
    return InterviewResult(
        score=score,
        question=question,
        answer=answer,
        degraded=degraded,
        grader_reason=reason,
        model=model,
        fallback_question=fallback_question,
    )


def _autopass_result(
    question: str,
    answer: str,
    degraded: bool,
    model: str | None,
    fallback_question: bool,
    *,
    scorer_error: str | None = None,
) -> InterviewResult:
    """Build a passing result for the scorer-LLM-unavailable auto-pass case."""
    detail = "interview auto-passed: scorer LLM unavailable"
    if scorer_error:
        detail = f"{detail} ({scorer_error[:160]})"
    return InterviewResult(
        score=AUTOPASS_SCORE,
        question=question,
        answer=answer,
        # Auto-pass means the answer PASSES; never carry the degraded flag through
        # (a degraded flag would block the pure verdict). The answer was received.
        degraded=False,
        grader_reason=detail,
        model=model,
        fallback_question=fallback_question,
        auto_passed=True,
    )


# --- helpers ------------------------------------------------------------------


def _anthropic_text(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InterviewInfraError("Anthropic returned non-JSON") from exc
    content = data.get("content")
    if not isinstance(content, list):
        raise InterviewInfraError(f"Anthropic response missing content: {str(data)[:200]}")
    parts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"]
    return "".join(parts)


def _parse_score(text: str) -> tuple[float, str]:
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last < first:
        raise InterviewInfraError(f"grader did not return JSON: {text[:200]!r}")
    try:
        data = json.loads(text[first : last + 1])
    except json.JSONDecodeError as exc:
        raise InterviewInfraError(f"grader JSON invalid: {text[:200]!r}") from exc
    raw_score = data.get("score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError) as exc:
        raise InterviewInfraError(f"grader score not numeric: {raw_score!r}") from exc
    score = max(0.0, min(1.0, score))
    reason = data.get("reason") if isinstance(data.get("reason"), str) else ""
    return score, reason


# --- stdlib RFC6455 websocket client (minimal, text frames only) --------------


class _WebsocketConnection:
    """A tiny synchronous websocket client (text frames) over a raw socket.

    Implements just enough of RFC6455 for the interview Q&A: an HTTP upgrade
    handshake, masked client text frames, and unmasked server text-frame reads
    (handling close/ping control frames). Not a general-purpose client.
    """

    def __init__(self, sock: socket.socket, buffer: bytes, timeout: float) -> None:
        self._sock = sock
        self._buf = bytearray(buffer)
        self._timeout = timeout

    @classmethod
    def connect(cls, host: str, port: int, path: str, timeout: float) -> "_WebsocketConnection":
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise InterviewInfraError(f"interview connect to {host}:{port} failed: {exc}") from exc
        sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"User-Agent: {_USER_AGENT}\r\n"
            "\r\n"
        )
        try:
            sock.sendall(handshake.encode("ascii"))
            buffer = b""
            while b"\r\n\r\n" not in buffer:
                chunk = sock.recv(4096)
                if not chunk:
                    raise InterviewInfraError("interview handshake: server closed during upgrade")
                buffer += chunk
                if len(buffer) > 65536:
                    raise InterviewInfraError("interview handshake: response too large")
        except OSError as exc:
            sock.close()
            raise InterviewInfraError(f"interview handshake failed: {exc}") from exc
        header, _, rest = buffer.partition(b"\r\n\r\n")
        if b"101" not in header.split(b"\r\n", 1)[0]:
            sock.close()
            raise InterviewInfraError(f"interview handshake not 101: {header[:120]!r}")
        return cls(sock, rest, timeout)

    def send_json(self, obj: dict[str, Any]) -> None:
        payload = json.dumps(obj).encode("utf-8")
        frame = self._build_text_frame(payload)
        try:
            self._sock.sendall(frame)
        except OSError as exc:
            raise InterviewInfraError(f"interview send failed: {exc}") from exc

    def recv_json(self) -> dict[str, Any]:
        text = self._recv_text_frame()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InterviewInfraError(f"interview recv: non-JSON frame {text[:120]!r}") from exc
        if not isinstance(obj, dict):
            raise InterviewInfraError("interview recv: frame was not a JSON object")
        return obj

    def close(self) -> None:
        try:
            # Best-effort close frame (opcode 0x8), then drop the socket.
            self._sock.sendall(b"\x88\x80" + os.urandom(4))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    @staticmethod
    def _build_text_frame(payload: bytes) -> bytes:
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        length = len(payload)
        header = bytearray([0x81])  # FIN + text opcode.
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        return bytes(header) + mask + masked

    def _recv_text_frame(self) -> str:
        while True:
            opcode, payload = self._read_frame()
            if opcode == 0x1:  # text
                return payload.decode("utf-8", errors="replace")
            if opcode == 0x8:  # close
                raise InterviewInfraError("interview server closed the connection")
            if opcode == 0x9:  # ping -> pong
                self._send_pong(payload)
                continue
            if opcode == 0xA:  # pong -> ignore
                continue
            # Binary or continuation we don't expect; ignore and keep reading.

    def _send_pong(self, payload: bytes) -> None:
        try:
            self._sock.sendall(self._build_control_frame(0xA, payload))
        except OSError:
            pass

    @staticmethod
    def _build_control_frame(opcode: int, payload: bytes) -> bytes:
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + masked

    def _read_frame(self) -> tuple[int, bytes]:
        b0 = self._read_exact(1)[0]
        opcode = b0 & 0x0F
        b1 = self._read_exact(1)[0]
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        data = self._read_exact(length)
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return opcode, data

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout as exc:
                raise InterviewInfraError("interview read timed out") from exc
            except OSError as exc:
                raise InterviewInfraError(f"interview read failed: {exc}") from exc
            if not chunk:
                raise InterviewInfraError("interview read: server closed mid-frame")
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out


def transport_from_address(address: str, *, timeout: float = 30.0) -> WebsocketInterviewTransport:
    """Build a websocket transport from a ``ws://host:port/path`` or ``host:port``."""
    if "://" in address:
        parsed = urlparse(address)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or DEFAULT_INTERVIEW_PORT
        path = parsed.path or "/"
    else:
        host, _, port_s = address.partition(":")
        host = host or "127.0.0.1"
        port = int(port_s) if port_s else DEFAULT_INTERVIEW_PORT
        path = "/"
    return WebsocketInterviewTransport(host=host, port=port, path=path, timeout=timeout)
