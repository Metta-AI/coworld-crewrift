"""Typed LLM meeting decision schema and validation helpers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MeetingAction = Literal["send_chat", "set_tentative_vote", "submit_vote", "wait"]

SCHEMA_VERSION = 1
VOTE_SKIP = "skip"
# Crewrift accepts up to 320 printable ASCII chars. Keep the model's utterances
# shorter than the hard cap so chat remains readable in the small meeting UI.
CHAT_MAX_CHARS = 160


class MeetingDecisionValidationError(ValueError):
    """Raised when an LLM decision cannot be safely applied."""


class MeetingDecision(BaseModel):
    """One fast-path meeting decision produced by the LLM."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    action: MeetingAction
    chat_text: str | None = None
    # ``skip`` means skip vote. Any other value must be a live player color that is
    # legal to vote for in the current meeting context.
    vote_target: str | None = None
    reason: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


def sanitize_chat(text: str | None, *, max_chars: int = CHAT_MAX_CHARS) -> str:
    """Return printable-ASCII chat text accepted by Crewrift's chat cleaner."""

    if not text:
        return ""
    printable = "".join(ch for ch in text if " " <= ch <= "~")
    return printable.strip()[:max_chars].strip()


def normalize_vote_target(target: str | None) -> str | None:
    if target is None:
        return None
    target = target.strip().lower()
    return target or None


def validate_meeting_decision(
    decision: MeetingDecision,
    *,
    alive_vote_targets: set[str],
    current_tentative: str | None = None,
    fallback_vote: str | None = None,
) -> MeetingDecision:
    """Sanitize and validate a decision against the current legal meeting state.

    ``alive_vote_targets`` excludes dead players and self. ``fallback_vote`` is
    usually the deterministic suspicion vote (or ``skip``) and is used when the LLM
    requests an early submit without naming a target.
    """

    chat_text = sanitize_chat(decision.chat_text)
    vote_target = normalize_vote_target(decision.vote_target)

    if vote_target is None and decision.action == "submit_vote":
        vote_target = normalize_vote_target(current_tentative) or normalize_vote_target(fallback_vote) or VOTE_SKIP

    if vote_target is not None:
        _validate_vote_target(vote_target, alive_vote_targets)

    if decision.action == "set_tentative_vote" and vote_target is None:
        raise MeetingDecisionValidationError("set_tentative_vote requires vote_target")
    if decision.action == "send_chat" and not chat_text:
        raise MeetingDecisionValidationError("send_chat requires non-empty printable chat_text")

    return decision.model_copy(update={"chat_text": chat_text or None, "vote_target": vote_target})


def _validate_vote_target(target: str, alive_vote_targets: set[str]) -> None:
    if target == VOTE_SKIP:
        return
    if target not in alive_vote_targets:
        legal = ", ".join(sorted(alive_vote_targets | {VOTE_SKIP}))
        raise MeetingDecisionValidationError(f"illegal vote_target {target!r}; legal targets: {legal}")
