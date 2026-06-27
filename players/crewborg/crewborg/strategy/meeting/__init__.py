"""Meeting layer: the opt-in chat/vote LLM plus its deterministic fallback (design §10).

This package owns everything crewborg does during the Voting phase — what to say in
meeting chat and who to vote out. It has two stacked paths, and the split is the whole
point of the layer:

  - **LLM path (opt-in, ``CREWBORG_LLM_MEETINGS=1``).** ``llm.py`` builds a client,
    ``context.py`` serializes belief into the prompt, ``prompts.py`` selects the role
    doctrine, ``schema.py`` types + validates the returned decision. Routed by Bedrock
    sidecar or a direct Anthropic key; see ``llm.build_meeting_llm_client_from_env``.
  - **Deterministic fallback (always present).** Pure-Python chat + a legal vote, with
    no model call. ``accusation.py`` writes crewmate accusations, ``imposter.py`` +
    ``chat_read.py`` + ``chat_nlp.py`` drive the imposter's bandwagon.

Two invariants this package exists to uphold (both enforced in ``modes/attend_meeting.py``,
which is the sole consumer — this package only supplies the pieces):

  1. **The deterministic fallback is never bypassed.** The LLM is best-effort. When it
     is disabled, fails to build, times out, errors, or returns an invalid decision, the
     mode falls back to a legal vote — it never hangs or no-ops at the deadline. The
     seams here are built for that: ``build_meeting_llm_client_from_env`` *returns* a
     ``DisabledMeetingClient`` instead of raising, ``validate_meeting_decision`` rejects
     illegal output, and a vote target is always resolvable from ``top_suspect`` or skip.
  2. **Crew and imposter chat use the identical format, so chat is not a role tell.** A
     fabricated imposter accusation (``accusation.fabricate_accusation``) renders through
     the exact same ``"<color> sus: <reason>, <reason>"`` template a real crewmate
     accusation does (``accusation.build_accusation``). An observer cannot tell a real
     accusation from a fabricated one by its shape.

This module is the package facade: it re-exports the names ``modes/attend_meeting.py``
imports. ``chat_nlp`` and ``chat_read`` are imported as submodules there, not re-exported.

Collaborators
-------------
Relies on: the submodules below (``context`` / ``llm`` / ``schema`` and, via submodule
  import, ``accusation`` / ``imposter`` / ``chat_read`` / ``chat_nlp`` / ``prompts``).
Used by: ``modes.attend_meeting`` (the only consumer) and the meeting test suite.

Modifying this file: keep ``__all__`` in sync with the imports above; it is the public
surface ``attend_meeting`` depends on. Adding a meeting helper means re-exporting it here.
"""

from crewborg.strategy.meeting.context import (
    serialize_meeting_context,
    valid_vote_targets,
)
from crewborg.strategy.meeting.llm import (
    DisabledMeetingClient,
    MeetingLLMClient,
    MeetingLLMResult,
    build_meeting_llm_client_from_env,
)
from crewborg.strategy.meeting.schema import (
    CHAT_MAX_CHARS,
    VOTE_SKIP,
    MeetingDecision,
    MeetingDecisionValidationError,
    sanitize_chat,
    validate_meeting_decision,
)

__all__ = [
    "CHAT_MAX_CHARS",
    "VOTE_SKIP",
    "DisabledMeetingClient",
    "MeetingDecision",
    "MeetingDecisionValidationError",
    "MeetingLLMClient",
    "MeetingLLMResult",
    "build_meeting_llm_client_from_env",
    "sanitize_chat",
    "serialize_meeting_context",
    "valid_vote_targets",
    "validate_meeting_decision",
]
