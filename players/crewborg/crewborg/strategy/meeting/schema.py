"""Typed LLM meeting decision schema, plus the sanitize/validate gate every decision passes.

This is the contract the meeting LLM must answer in and the safety net around it. The
model returns one ``MeetingDecision`` (action + optional chat/vote); ``sanitize_chat``
and ``validate_meeting_decision`` then force it into something legal before the mode acts
on it. Validation is *the* place an out-of-spec or illegal model answer is caught and
turned into a fallback (the caller treats a raised error as "use the deterministic vote"),
so it is a load-bearing part of the "deterministic fallback is never bypassed" invariant.

Collaborators
-------------
Relies on: ``pydantic`` for the typed/validated decision model. No project imports — this
  is the leaf the rest of the meeting layer builds on.
Used by:
  - ``llm.AnthropicMeetingClient.decide`` parses the model's JSON into ``MeetingDecision``.
  - ``context.serialize_meeting_context`` advertises ``CHAT_MAX_CHARS`` / ``VOTE_SKIP`` /
    ``SCHEMA_VERSION`` to the prompt.
  - ``prompts`` interpolates ``VOTE_SKIP`` / ``CHAT_MAX_CHARS`` into the rules text.
  - ``modes.attend_meeting`` calls ``validate_meeting_decision`` before applying a decision
    and ``sanitize_chat`` is reused for deterministic chat.

Modifying this file: ``validate_meeting_decision`` is a trust boundary over model output —
keep it total (raise ``MeetingDecisionValidationError`` rather than return something
illegal) so the caller can always fall back. Bumping the wire shape means bumping
``SCHEMA_VERSION`` and the ``Literal[1]`` pin on ``MeetingDecision.schema_version`` together.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: The four things the agent can do on a meeting tick (see ``prompts._COMMON_PROMPT``).
MeetingAction = Literal["send_chat", "set_tentative_vote", "submit_vote", "wait"]

#: Wire-format version of the decision contract; pinned on ``MeetingDecision`` and echoed
#: into the serialized context. Bump when the schema changes incompatibly.
SCHEMA_VERSION = 1
#: Sentinel vote target meaning "skip / eject no one" — distinct from any player color.
VOTE_SKIP = "skip"
# Crewrift accepts up to 320 printable ASCII chars. Keep the model's utterances
# shorter than the hard cap so chat remains readable in the small meeting UI.
CHAT_MAX_CHARS = 160


class MeetingDecisionValidationError(ValueError):
    """Raised when an LLM decision cannot be safely applied."""


class MeetingDecision(BaseModel):
    """One meeting-tick decision produced by the LLM (and the same type the deterministic
    tests construct). ``extra="forbid"`` rejects unknown keys, so a drifting model response
    fails validation rather than silently dropping fields.

    Attributes:
      - ``action``: which of the four ``MeetingAction``s to take this tick.
      - ``chat_text``: message to send for ``send_chat`` (``None`` otherwise); sanitized to
        printable ASCII ≤ ``CHAT_MAX_CHARS`` by ``validate_meeting_decision``.
      - ``vote_target``: a player color, ``VOTE_SKIP``, or ``None``; only meaningful for the
        vote actions and validated against the live legal set.
      - ``reason``: short free-text rationale, surfaced in traces (not acted on).
      - ``confidence``: optional 0.0–1.0 self-report (``Field`` bounds enforce the range).
    """

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
    """Lower-case + strip a vote target so model casing/whitespace matches roster colors
    and ``VOTE_SKIP``; ``None`` (or empty after stripping) stays ``None`` (no target)."""

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
    """Sanitize and validate a decision against the current legal meeting state, returning
    a **new** ``MeetingDecision`` with cleaned ``chat_text``/``vote_target`` (the input is
    not mutated). Raises ``MeetingDecisionValidationError`` on anything illegal — the caller
    treats that as "fall back to the deterministic vote".

    ``alive_vote_targets`` is the legal target set (``context.valid_vote_targets``): live
    players excluding self; ``VOTE_SKIP`` is always additionally legal. ``current_tentative``
    is the vote staged so far, ``fallback_vote`` the deterministic suspicion vote (or
    ``skip``). For a ``submit_vote`` with no explicit target, the vote resolves to the
    tentative, else the fallback, else ``VOTE_SKIP`` — a submit always yields a legal ballot.
    ``set_tentative_vote`` must name a target; ``send_chat`` must carry non-empty printable
    text. ``submit_vote``/``wait`` may carry no chat or target.
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
    """Raise unless ``target`` is ``VOTE_SKIP`` or a currently-legal live color. The error
    message lists the legal set so the fallback trace shows what the model should have used."""

    if target == VOTE_SKIP:
        return
    if target not in alive_vote_targets:
        legal = ", ".join(sorted(alive_vote_targets | {VOTE_SKIP}))
        raise MeetingDecisionValidationError(f"illegal vote_target {target!r}; legal targets: {legal}")
