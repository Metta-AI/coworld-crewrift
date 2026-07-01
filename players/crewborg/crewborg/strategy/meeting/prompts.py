"""Role-specific system prompt loading for meeting LLM decisions."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from crewborg.strategy.meeting.schema import CHAT_MAX_CHARS, VOTE_SKIP

PROMPT_DIR_ENV = "CREWBORG_LLM_PROMPT_DIR"

_ROLE_FILES = {
    "crewmate": "crewmate.md",
    "imposter": "imposter.md",
}

_COMMON_PROMPT = f"""You are controlling one Crewrift player during an active meeting.
Choose exactly one JSON object matching the schema. Do not include markdown.

Actions:
- send_chat: send one concise printable-ASCII chat message now.
- set_tentative_vote: update the vote target but do not submit yet.
- submit_vote: submit the vote immediately.
- wait: do nothing this tick.

Rules:
- Use only vote_target values from constraints.valid_vote_targets or "{VOTE_SKIP}".
- Keep chat_text printable ASCII and at most {CHAT_MAX_CHARS} characters.
- A submitted vote is final; tentative votes are auto-submitted near the deadline.

Participation — be an ACTIVE voice; meetings are won by talking:
- In MOST meetings, send_chat at least once: share a concrete thing you observed, ask a specific
  player to account for where they were, react to someone's claim, or push the group toward a read.
  Staying silent forfeits all of your influence on the vote.
- Chatting is SEPARATE from voting. Speak up to drive the deduction even when you are NOT ready to
  commit a vote — vote restraint does not mean staying quiet.
- Prefer specific, game-grounded speech (names, rooms, ticks, who-was-where) over filler — but an
  honest partial read beats silence.
"""

_FALLBACK_ROLE_PROMPTS = {
    "crewmate": (
        "Crewmate doctrine: default to skip unless the context shows concrete, citable "
        "evidence. Do not invent evidence. If state.fallback_vote is skip, treat that as "
        "the deterministic restraint signal and usually wait or skip."
    ),
    "imposter": (
        "Imposter doctrine: never accuse or vote a teammate. Deflect onto a plausible "
        "non-teammate only when it helps survival or a mis-ejection; otherwise wait or skip."
    ),
}


def system_prompt_for_context(context: dict[str, Any], *, prompt_dir: str | None = None) -> str:
    """Return the common contract plus the role doctrine selected from context."""

    role = context.get("self", {}).get("role")
    role_key = "imposter" if role == "imposter" else "crewmate"
    return "\n\n".join((_COMMON_PROMPT, _role_prompt(role_key, prompt_dir)))


@lru_cache(maxsize=16)
def _role_prompt(role_key: str, prompt_dir: str | None) -> str:
    path = _prompt_path(role_key, prompt_dir)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK_ROLE_PROMPTS[role_key]
    return text or _FALLBACK_ROLE_PROMPTS[role_key]


def _prompt_path(role_key: str, prompt_dir: str | None) -> Path:
    root = Path(prompt_dir) if prompt_dir else Path(__file__).with_name("memory")
    return root / _ROLE_FILES[role_key]
