"""Role-specific system prompt loading for gameplay commander decisions."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_DIR_ENV = "CREWBORG_LLM_PROMPT_DIR"

_ROLE_FILES = {
    "crewmate": "crewmate.md",
    "imposter": "imposter.md",
}

_COMMON_PROMPT = """You are the GAMEPLAY COMMANDER for one Crewrift player.

This agent has TWO separate LLM roles. Do not confuse them:
- The MEETING CHATTER speaks and votes during meetings (the Voting phase). That is a
  DIFFERENT role, handled by other code -- it is NOT you. You never produce chat or votes.
- The GAMEPLAY COMMANDER (YOU) runs during live play between meetings. You set sticky,
  high-level priorities that bias the player's movement and targeting.

Choose exactly one JSON object matching the schema. Do not include markdown.

You do not choose modes or low-level controls. The deterministic player already
handles meetings, body reports, task execution, navigation, cooldowns, and kill
safety. Your job is to set sticky priorities that bias which valid room, task,
or player those deterministic modes prefer.

Rules:
- Use only room names from context.legal_rooms.
- Use only player colors from context.legal_players.
- Leave a field null when you do not have a strong preference.
- Bias, do not force: never ask for impossible rooms, dead players, teammates, or stale targets.
- Keep reason short and grounded in the supplied context.
"""

_FALLBACK_ROLE_PROMPTS = {
    "crewmate": (
        "Crewmate doctrine: prefer useful task progress without walking into isolated danger. "
        "Set target_room or target_task only when it improves tasking; use posture stick or "
        "isolate only when the visible crew distribution makes that preference meaningful."
    ),
    "imposter": (
        "Imposter doctrine: create kill opportunities by steering toward plausible victims "
        "and away from teammate-claimed space. DANGER fields require a strong danger_reason: "
        "allow_witnessed_kill risks being seen; skip_evade risks staying at the body."
    ),
}


def system_prompt_for_role(role: str | None, *, prompt_dir: str | None = None) -> str:
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
