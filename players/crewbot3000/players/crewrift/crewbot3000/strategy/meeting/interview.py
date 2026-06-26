"""Interview-mode LLM client: answer Crewrift voting-strategy questions.

This is the *answer generator* for the out-of-band commissioner interview (see
``coworld/interview_server.py``). The league commissioner connects to the
player's interview websocket server, sends a Crewrift voting-strategy
riddle/question, and the player answers it through its meeting LLM. A policy
must PASS the interview (the commissioner scores the answer) IN ADDITION to the
existing skill gate to qualify.

It deliberately reuses the *exact same* Anthropic plumbing the in-game meeting
LLM uses — :class:`AnthropicMeetingClient`'s ``_anthropic_client`` (direct
Anthropic or AWS Bedrock), :func:`read_meeting_params_from_env` for
model/auth/timeout config, and the voting-strategy knowledge baked into
``prompts.CREWMATE_STRATEGY``/``IMPOSTER_STRATEGY``. The only new thing is a
plain free-text system prompt (the meeting prompt demands strict JSON; an
interview answer is prose).

If the LLM is disabled or unavailable (no backend configured, or the call
raises) the client returns a clear *degraded* answer string rather than
crashing, so the commissioner can score it as a fail. The server never goes
down because the model is missing.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from players.crewrift.crewbot3000.strategy.meeting.llm import (
    AnthropicMeetingClient,
    MeetingLLMConfig,
    read_meeting_params_from_env,
)
from players.crewrift.crewbot3000.strategy.meeting.prompts import (
    CREWMATE_STRATEGY,
    IMPOSTER_STRATEGY,
)

# Larger than a terse meeting reply: an interview answer is a paragraph of prose.
DEFAULT_INTERVIEW_MAX_TOKENS = 600
# Interviews are not on the 24 Hz game clock, so we can afford a real budget.
DEFAULT_INTERVIEW_TIMEOUT_SECONDS = 30.0

# A voting-strategy-grounded system prompt. We expose the SAME knowledge the
# in-game meeting strategy uses (so the interview measures the policy's actual
# reasoning, not a separate canned essay), but ask for free-text prose instead of
# the strict meeting JSON contract.
INTERVIEW_SYSTEM_PROMPT = f"""You are a Crewrift player being interviewed by the league \
commissioner before you are allowed to compete. Crewrift is an Among Us-style social \
deduction game: 8 players, 2 imposters by default; crewmates win by finishing all tasks \
or voting out every imposter; imposters win by reaching parity or surviving the vote. \
Meetings are triggered by reporting a body or pressing the emergency button, after which \
players chat and then vote (or skip); ties and timeouts eject no one.

Answer the interviewer's question about Crewrift VOTING strategy clearly and concretely, \
in plain prose (no JSON, no markdown headers). Demonstrate that you understand both sides:

As a crewmate:
{CREWMATE_STRATEGY}

As an imposter:
{IMPOSTER_STRATEGY}

Be specific and game-grounded. A strong answer names concrete signals (vents, bodies, \
proximity to kills, the live vote tally), explains WHEN to skip vs. vote, and reasons about \
how voting differs by role. Keep it focused — a few tight sentences beat a vague essay."""

# Stable message shown to the commissioner when we cannot actually reason. The
# commissioner scores this as a fail (it carries no Crewrift content), but the
# server stays up and the protocol completes cleanly.
DEGRADED_ANSWER_PREFIX = "INTERVIEW_DEGRADED:"


@dataclass(frozen=True)
class InterviewAnswer:
    """One interview answer plus whether it came from a live LLM call."""

    answer: str
    degraded: bool
    model: str | None = None


class InterviewLLMClient:
    """Generate interview answers via the same Anthropic backend as meetings.

    Construct via :meth:`from_env` to honor the existing meeting env flags
    (``CREWBOT3000_LLM_MEETINGS`` / Bedrock flags / ``ANTHROPIC_API_KEY`` /
    ``CREWBOT3000_LLM_MODEL`` ...). When no backend is configured the client is
    ``enabled = False`` and :meth:`answer` returns a degraded answer.
    """

    def __init__(
        self,
        config: MeetingLLMConfig | None,
        *,
        enabled: bool,
        disabled_reason: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.enabled = enabled
        self.disabled_reason = disabled_reason
        self._config = config
        # Reuse the meeting client purely for its Anthropic/Bedrock connection
        # bootstrap; we drive ``messages.create`` ourselves with a prose prompt.
        self._meeting = (
            AnthropicMeetingClient(config, client=client) if (enabled and config is not None) else None
        )

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, client: Any | None = None
    ) -> "InterviewLLMClient":
        env = os.environ if env is None else env
        params = read_meeting_params_from_env(env)
        if not params.use_llm and client is None:
            return cls(
                None,
                enabled=False,
                disabled_reason=(
                    "interview LLM disabled: set CREWBOT3000_LLM_MEETINGS=1 with "
                    "ANTHROPIC_API_KEY (or a Bedrock flag + AWS creds)"
                ),
            )
        config = MeetingLLMConfig(
            model=params.model,
            max_tokens=_env_int(env, "CREWRIFT_INTERVIEW_MAX_TOKENS", DEFAULT_INTERVIEW_MAX_TOKENS),
            temperature=params.temperature,
            timeout_seconds=_env_float(
                env, "CREWRIFT_INTERVIEW_TIMEOUT_SECONDS", DEFAULT_INTERVIEW_TIMEOUT_SECONDS
            ),
            trace_raw=params.trace_raw,
            use_bedrock=params.use_bedrock,
        )
        return cls(config, enabled=True, client=client)

    def answer(self, question: str, context: dict[str, Any] | None = None) -> InterviewAnswer:
        """Answer one interview question; never raises (degrades on failure)."""
        if not self.enabled or self._meeting is None or self._config is None:
            return InterviewAnswer(
                answer=f"{DEGRADED_ANSWER_PREFIX} {self.disabled_reason or 'LLM unavailable'}",
                degraded=True,
            )
        user_payload: dict[str, Any] = {"question": question}
        if context:
            user_payload["context"] = context
        user_content = json.dumps(user_payload, sort_keys=True, separators=(",", ":"))
        try:
            response = self._meeting._anthropic_client().messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
                system=INTERVIEW_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            text = _response_text(response).strip()
        except Exception as exc:  # noqa: BLE001 - degrade, never crash the server.
            return InterviewAnswer(
                answer=f"{DEGRADED_ANSWER_PREFIX} LLM call failed: {exc}",
                degraded=True,
                model=self._config.model,
            )
        if not text:
            return InterviewAnswer(
                answer=f"{DEGRADED_ANSWER_PREFIX} LLM returned an empty answer",
                degraded=True,
                model=self._config.model,
            )
        return InterviewAnswer(answer=text, degraded=False, model=self._config.model)


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, default))
    except (TypeError, ValueError):
        return default
