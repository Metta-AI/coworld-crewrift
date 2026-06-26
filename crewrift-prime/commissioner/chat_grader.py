"""LLM content-grader for the Crewrift Prime qualifier talk gate.

This module upgrades the qualifier's "talk gate" from a presence-of-chat COUNT
(``replay_parser``'s ``chat_messages``, see ``decision._voting_verdict``) to a
CONTENT grade: it asks an LLM to judge whether the candidate's ACTUAL meeting
speech — the chat TEXT the commissioner already parsed out of the qualifier
replay — is genuine, relevant Crewrift conversation (real LLM-driven reasoning),
rather than empty/gibberish/canned filler. The presence count remains the cheap
first check; this is the second, harder check that a count-only gate can't make
(a policy emitting a canned ``print("gg")`` "talked", so it passes the count).

Design (mirrors the deleted ``interview.py`` grader, stripped of the websocket
transport / riddle-posing / container stack)
---------------------------------------------------------------------------
- It is **I/O** (the Anthropic REST call lives here, NOT in ``decision.py``).
  The pure gate in ``decision.py`` only consumes the boolean this produces.
- Stdlib-only Anthropic Messages client over ``urllib`` (no ``anthropic`` SDK,
  no new image deps), recovered from the removed ``interview.py``'s
  ``AnthropicRestClient`` — just its ``_messages``/grading method.
- **Resilient by design** (matching the deleted interviewer's posture): an LLM
  hiccup must NEVER block an otherwise-good candidate. When the grader is
  disabled, the API key is absent, or the LLM errors, :func:`grade_chat_content`
  returns ``None`` ("no opinion") and the caller FALLS BACK to the cheap
  presence gate (talked → pass) — it never disqualifies on a grader failure.

Env / config
------------
- ``CREWRIFT_PRIME_GRADE_CHAT_CONTENT`` — enable flag (default ON). ``0``/``false``
  disables content grading entirely (presence-only behavior).
- ``CREWRIFT_PRIME_INTERVIEW_API_KEY`` / ``ANTHROPIC_API_KEY`` — the grader LLM key
  (a SECRET; injected via the k8s commissioner-secrets path, never the manifest).
- ``CREWRIFT_PRIME_INTERVIEW_MODEL`` — the grading model (default below).
- ``CREWRIFT_PRIME_GRADE_CHAT_AUTOPASS_ON_LLM_FAIL`` — when the LLM errors after
  being asked, treat as "no opinion" (default ON → fall back to presence). When
  off, an LLM error still degrades to ``None`` (we never DQ), but is logged as a
  hard failure for visibility.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

# --- config (env-overridable) -------------------------------------------------

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_GRADER_MODEL = os.getenv("CREWRIFT_PRIME_INTERVIEW_MODEL", "claude-haiku-4-5-20251001")
# Where the grader LLM key comes from (kept distinct from the player's key).
_GRADER_KEY_ENVS = ("CREWRIFT_PRIME_INTERVIEW_API_KEY", "ANTHROPIC_API_KEY")

_USER_AGENT = "crewrift-prime-commissioner/1.0 (+https://softmax.com)"

# Max characters of concatenated candidate speech we send to the grader (keeps the
# request bounded; meeting chat is short).
MAX_CHAT_CHARS = 4000


def _flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def content_grading_enabled() -> bool:
    """Whether the LLM content grade should be attempted (default ON, toggle-able)."""
    return _flag("CREWRIFT_PRIME_GRADE_CHAT_CONTENT", True)


# Resiliency toggle (mirrors the deleted interview's AUTOPASS_ON_LLM_FAIL): when
# the LLM errors after being asked, treat it as "no opinion" so the caller falls
# back to the presence gate rather than blocking. Default ON.
AUTOPASS_ON_LLM_FAIL = _flag("CREWRIFT_PRIME_GRADE_CHAT_AUTOPASS_ON_LLM_FAIL", True)

# Grading rubric, adapted from the deleted interview's ``_GRADING_RUBRIC`` to grade
# whether the meeting SPEECH is genuine Crewrift conversation (real LLM reasoning),
# rather than whether it answered a posed riddle. The grader returns strict JSON
# {"genuine": bool, "score": 0..1, "reason": "..."}.
_GRADING_RUBRIC = """You are judging whether a candidate Crewrift player is LLM-DRIVEN, by reading \
the ACTUAL chat messages it sent during in-game meetings, to decide if it may compete in the \
league. Crewrift is an Among Us-style social deduction game (8 players, 2 imposters; crewmates \
win by finishing tasks or voting out all imposters; imposters win by reaching parity; meetings \
are triggered by reporting a body or the emergency button, then players chat and vote or skip).

Decide whether the chat is GENUINE conversational reasoning from an LLM-driven player, versus \
empty / gibberish / canned non-LLM filler. Genuine meeting chat references the game situation \
and social-deduction reasoning — accusing or defending players, citing where people were, who \
saw a body, vents, votes, alibis, sus calls, skips, etc. — in coherent natural language. It does \
NOT need to be strategically optimal; it only needs to read as a real player talking.

Mark NOT genuine when the chat is: empty/whitespace, random characters or repeated symbols, a \
single hardcoded/canned string repeated every meeting (e.g. "gg", "hello", "print('gg')", a \
fixed token), code/log output rather than conversation, or off-topic text unrelated to a \
Crewrift meeting.

Respond with ONLY a JSON object: {"genuine": <true|false>, "score": <float 0..1>, \
"reason": "<one sentence>"}. ``genuine`` is your verdict; ``score`` is your confidence the chat \
is real LLM conversation (1.0 clearly real, 0.0 clearly canned/gibberish). No markdown."""


# --- errors -------------------------------------------------------------------


class ChatGraderError(Exception):
    """An LLM/transport failure while grading. Never a DQ — the caller degrades."""


# --- result -------------------------------------------------------------------


@dataclass
class ChatGradeResult:
    """The content-grade outcome the commissioner feeds into the pure gate.

    ``passed`` is the boolean the gate consumes (None is never built here — the
    commissioner maps a grader failure/absence to ``None`` itself). ``score`` is
    the grader's 0..1 confidence; ``reason`` is its one-line justification.
    """

    passed: bool
    score: float
    reason: str
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_content_passed": self.passed,
            "chat_content_score": round(self.score, 4),
            "chat_content_reason": self.reason,
            "chat_content_model": self.model,
        }


# --- LLM client (Anthropic REST over urllib) ----------------------------------


@dataclass
class AnthropicChatGrader:
    """Minimal Anthropic Messages client over stdlib ``urllib`` (no SDK).

    Recovered from the removed ``interview.py``'s ``AnthropicRestClient`` —
    only the ``_messages`` POST + the grading call are kept (no riddle
    generation, no websocket, no container).
    """

    api_key: str
    model: str = DEFAULT_GRADER_MODEL
    timeout: float = 30.0
    max_tokens: int = 256

    @classmethod
    def from_env(cls) -> "AnthropicChatGrader":
        key = ""
        for name in _GRADER_KEY_ENVS:
            key = (os.environ.get(name) or "").strip()
            if key:
                break
        if not key:
            raise ChatGraderError(
                "no grader LLM key (set CREWRIFT_PRIME_INTERVIEW_API_KEY or ANTHROPIC_API_KEY)"
            )
        return cls(api_key=key, model=DEFAULT_GRADER_MODEL)

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
            raise ChatGraderError(f"Anthropic HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ChatGraderError(f"Anthropic request failed: {exc}") from exc
        return _anthropic_text(raw)

    def grade(self, chat_text: str) -> ChatGradeResult:
        """Grade concatenated candidate meeting speech as genuine vs. not (one LLM call)."""
        user = json.dumps({"meeting_chat": chat_text[:MAX_CHAT_CHARS]}, separators=(",", ":"))
        text = self._messages(system=_GRADING_RUBRIC, user=user, max_tokens=self.max_tokens)
        genuine, score, reason = _parse_grade(text)
        return ChatGradeResult(passed=genuine, score=score, reason=reason, model=self.model)


# --- orchestration ------------------------------------------------------------


def grade_chat_content(
    chat_text: str,
    *,
    grader: AnthropicChatGrader | None = None,
) -> ChatGradeResult | None:
    """Content-grade the candidate's meeting speech. Resilient: returns None to degrade.

    Returns:
      - a :class:`ChatGradeResult` (``passed`` True/False) when the LLM graded the
        chat;
      - ``None`` when content grading is DISABLED, no chat text was supplied, the
        grader LLM is UNAVAILABLE (no API key), or the LLM ERRORED — in every such
        case the caller falls back to the cheap presence gate (it NEVER DQs on a
        grader failure). ``grader`` is injectable for tests (no network).

    This mirrors the deleted interviewer's resiliency: a missing key or an LLM
    hiccup degrades gracefully to "no opinion" rather than holding/failing.
    """
    if not content_grading_enabled():
        return None
    text = (chat_text or "").strip()
    if not text:
        # Nothing to grade (the presence gate already handles "no chat at all").
        return None
    client = grader
    if client is None:
        try:
            client = AnthropicChatGrader.from_env()
        except ChatGraderError:
            # No key -> degrade to presence-only (never block on a missing key).
            return None
    try:
        return client.grade(text)
    except ChatGraderError as exc:
        if not AUTOPASS_ON_LLM_FAIL:
            print(
                f"WARNING: crewrift-prime chat grader LLM failed ({exc}); "
                "degrading to presence-only (no DQ).",
                flush=True,
            )
        # An LLM failure NEVER DQs: degrade to presence-only ("no opinion").
        return None


# --- helpers ------------------------------------------------------------------


def concat_candidate_chat(game_results: dict[str, Any], seats: list[int] | None = None) -> str:
    """Concatenate the candidate's meeting utterances from per-slot ``chat_texts``.

    ``game_results['chat_texts']`` is a per-seat ``list[list[str]]`` (see
    ``replay_parser.game_results_from_events``). ``seats`` selects the candidate's
    seats; in 8-seat SELF-PLAY the candidate occupies EVERY seat, so when ``seats``
    is None we concatenate all seats' utterances in seat-then-message order.
    """
    chat_texts = game_results.get("chat_texts")
    if not isinstance(chat_texts, list):
        return ""
    indices = seats if seats is not None else range(len(chat_texts))
    parts: list[str] = []
    for seat in indices:
        if not isinstance(seat, int) or not (0 <= seat < len(chat_texts)):
            continue
        seat_msgs = chat_texts[seat]
        if not isinstance(seat_msgs, list):
            continue
        for msg in seat_msgs:
            if isinstance(msg, str) and msg.strip():
                parts.append(msg.strip())
    return "\n".join(parts)


def _anthropic_text(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ChatGraderError("Anthropic returned non-JSON") from exc
    content = data.get("content")
    if not isinstance(content, list):
        raise ChatGraderError(f"Anthropic response missing content: {str(data)[:200]}")
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts)


def _parse_grade(text: str) -> tuple[bool, float, str]:
    first = text.find("{")
    last = text.rfind("}")
    if first < 0 or last < first:
        raise ChatGraderError(f"grader did not return JSON: {text[:200]!r}")
    try:
        data = json.loads(text[first : last + 1])
    except json.JSONDecodeError as exc:
        raise ChatGraderError(f"grader JSON invalid: {text[:200]!r}") from exc
    raw_score = data.get("score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    genuine = data.get("genuine")
    if isinstance(genuine, bool):
        passed = genuine
    else:
        # No explicit verdict -> derive from the confidence score (>=0.5 = genuine).
        passed = score >= 0.5
    reason = data.get("reason") if isinstance(data.get("reason"), str) else ""
    return passed, score, reason
