"""Role-specific system prompt assembly for meeting LLM decisions.

The system prompt is two parts joined: a **common contract** (the action menu + hard rules
that constrain every decision, regardless of role) and a **role doctrine** (crewmate vs
imposter strategy). The role doctrine is normally loaded from a markdown file under the
package's ``memory/`` directory so it can be iterated without code changes; if that file is
missing or empty, a built-in ``_FALLBACK_ROLE_PROMPTS`` string is used so a call always has
a usable prompt. Loaded prompts are cached by (role, dir).

Both role prompts encode the same two strategic invariants the deterministic path also obeys:
crewmate defaults to restraint (skip unless concrete evidence), and imposter never accuses
or votes a teammate. Note this only shapes *content/strategy* — the chat **format** is not
role-specific here, consistent with "chat is not a role tell".

Collaborators
-------------
Relies on:
  - ``schema`` — ``VOTE_SKIP`` / ``CHAT_MAX_CHARS`` interpolated into the rules text so the
    prompt's stated limits match what validation enforces.
  - the filesystem — role markdown under ``memory/`` (or ``CREWBORG_LLM_PROMPT_DIR``).
Used by: ``llm.AnthropicMeetingClient.decide`` (one system prompt per call), which passes the
  configured ``prompt_dir`` through.

Modifying this file: keep the stated limits (chars, skip token, "vote is final") in sync with
``schema.validate_meeting_decision`` — the prompt is a promise the validator must keep. The
fallback strings exist so a missing prompt file never breaks a call; don't remove them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from crewborg.strategy.meeting.schema import CHAT_MAX_CHARS, VOTE_SKIP

#: Env var that overrides the directory role prompt files are loaded from (else ``memory/``).
PROMPT_DIR_ENV = "CREWBORG_LLM_PROMPT_DIR"

#: Role key → markdown filename for the editable role doctrine prompts.
_ROLE_FILES = {
    "crewmate": "crewmate.md",
    "imposter": "imposter.md",
}

# Role-independent contract prepended to every system prompt: the action menu + the hard
# constraints (legal vote targets, chat limits, vote finality) every decision must respect.
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
- Prefer useful, game-grounded meeting speech over filler.
"""

# Built-in role doctrine used when the editable markdown file is absent/empty, so a call
# always has a role prompt. Mirrors the deterministic strategy (crewmate restraint; imposter
# never outs a teammate).
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
    """Return the common contract plus the role doctrine selected from context. The role is
    read from ``context["self"]["role"]``; anything other than ``"imposter"`` (including a
    missing role) maps to the crewmate doctrine — the safe default."""

    role = context.get("self", {}).get("role")
    role_key = "imposter" if role == "imposter" else "crewmate"
    return "\n\n".join((_COMMON_PROMPT, _role_prompt(role_key, prompt_dir)))


@lru_cache(maxsize=16)
def _role_prompt(role_key: str, prompt_dir: str | None) -> str:
    """The role doctrine text: the markdown file's contents, or the built-in fallback when
    it's missing/unreadable/empty. Cached by (role, dir) so the file is read at most once per
    key (prompts don't change within a process)."""

    path = _prompt_path(role_key, prompt_dir)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK_ROLE_PROMPTS[role_key]
    return text or _FALLBACK_ROLE_PROMPTS[role_key]


def _prompt_path(role_key: str, prompt_dir: str | None) -> Path:
    """Resolve the role's markdown path under ``prompt_dir`` (if given) or the package's
    sibling ``memory/`` directory."""

    root = Path(prompt_dir) if prompt_dir else Path(__file__).with_name("memory")
    return root / _ROLE_FILES[role_key]
